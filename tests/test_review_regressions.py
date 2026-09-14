"""Behavioral regressions for the blocking broker review. No real credentials."""

import asyncio
import json
import time
from collections.abc import Mapping
from email.utils import formatdate

import httpx
import pytest

from venue_broker.app import create_app
from venue_broker.broker import Broker, BrokerResponse, HttpxFetcher
from venue_broker.config import BucketConfig, Settings, kalshi_token_cost


class AdmissionClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += max(delay, 1e-9)
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "margin,override",
    [(0.9, None), (1.0, None), (1.0, BucketConfig(100, 100, "requests"))],
)
@pytest.mark.asyncio
async def test_polymarket_worst_rolling_second_never_exceeds_twenty(
    margin, override, monkeypatch
) -> None:
    clock = AdmissionClock()
    admissions = []

    async def fetcher(url, headers):
        admissions.append(clock.now)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(
        Settings(rate_margin=margin, polymarket_bucket_override=override),
        fetcher=fetcher,
    )
    bucket = broker._buckets["pmus"]
    monkeypatch.setattr(bucket, "_clock", clock.monotonic)
    monkeypatch.setattr(bucket, "_sleep", clock.sleep)
    monkeypatch.setattr(bucket, "_updated_at", 0)
    try:
        for index in range(120):
            if index == 60:
                clock.now += 2.3  # Refill completely; exercise another cold burst.
            await broker.get("pmus", f"/unique-{index}", b"", {}, f"bot-{index % 4}")
        worst = max(
            sum(start <= stamp < start + 1 for stamp in admissions)
            for start in admissions
        )
        print(f"margin={margin:g}, override={override}: worst rolling second={worst}")
        assert worst <= 20, f"worst rolling second admitted {worst} requests"
        assert len(admissions) == 120
        assert clock.now < 10  # The ceiling must still allow useful throughput.
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_concurrent_requests_and_retries_obey_rolling_ceiling():
    admissions = []
    attempts = {}

    async def fetcher(url, headers):
        admissions.append(time.monotonic())
        attempts[url] = attempts.get(url, 0) + 1
        return BrokerResponse(429 if attempts[url] == 1 else 200, b"result", {})

    async def retry_sleep(delay):
        await asyncio.sleep(0)

    broker = Broker(Settings(), fetcher=fetcher, retry_sleeper=retry_sleep)
    try:
        responses = await asyncio.wait_for(
            asyncio.gather(
                *(
                    broker.get("pmus", f"/burst-{index}", b"", {}, f"bot-{index % 4}")
                    for index in range(30)
                )
            ),
            6,
        )
        assert all(response.status_code == 200 for response in responses)
        assert len(admissions) == 60
        worst = max(
            sum(start <= stamp < start + 1 for stamp in admissions)
            for start in admissions
        )
        assert worst <= 20, f"real-clock rolling second admitted {worst} upstream calls"
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_kalshi_advanced_retains_two_seconds_of_burst(monkeypatch) -> None:
    clock = AdmissionClock()
    admissions = []

    async def fetcher(url, headers):
        admissions.append(clock.now)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(rate_margin=1, kalshi_tier="advanced"), fetcher=fetcher)
    bucket = broker._buckets["kalshi"]
    monkeypatch.setattr(bucket, "_clock", clock.monotonic)
    monkeypatch.setattr(bucket, "_sleep", clock.sleep)
    monkeypatch.setattr(bucket, "_updated_at", 0)
    try:
        for index in range(61):
            await broker.get("kalshi", f"/markets/{index}", b"", {}, "weather")
        assert admissions[:60] == [0] * 60
        assert admissions[60] == pytest.approx(10 / 300)
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "auth_name",
    ["Authorization", "X-PM-Access-Key", "KALSHI-ACCESS-KEY", "Cookie"],
)
@pytest.mark.asyncio
async def test_concurrent_same_caller_different_credentials_are_isolated(
    auth_name, caplog
) -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def fetcher(url: str, headers: Mapping[str, str]) -> BrokerResponse:
        account = headers[auth_name.lower()]
        calls.append(account)
        started.set()
        await release.wait()
        return BrokerResponse(
            200, b"account-one" if account.endswith("one") else b"account-two", {}
        )

    broker = Broker(Settings(), fetcher=fetcher)
    first = asyncio.create_task(
        broker.get(
            "pmus",
            "/v1/portfolio/positions",
            b"",
            {auth_name: "fake-secret-one"},
            "weather",
        )
    )
    await started.wait()
    second = asyncio.create_task(
        broker.get(
            "pmus",
            "/v1/portfolio/positions",
            b"",
            {auth_name: "fake-secret-two"},
            "weather",
        )
    )
    # A full event-loop turn lets the second caller join the in-flight lookup.
    await asyncio.sleep(0)
    keys = repr(list(broker._inflight))
    release.set()
    try:
        one, two = await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert one.body == b"account-one"
        assert two.body == b"account-two"
        assert len(calls) == 2
        exposed = keys + repr(broker._cache) + json.dumps(await broker.metrics())
        assert "fake-secret" not in exposed + caplog.text
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "path,host",
    [
        ("/v1/portfolio/positions", "api.polymarket.us"),
        ("/v1/account/balances", "api.polymarket.us"),
        ("/v1/orders/open", "api.polymarket.us"),
        ("/v1/markets/example/book", "gateway.polymarket.us"),
        ("/v1/events", "gateway.polymarket.us"),
    ],
)
@pytest.mark.asyncio
async def test_polymarket_routes_by_documented_surface(path, host) -> None:
    calls = []

    async def fetcher(url, headers):
        calls.append((url, headers))
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    auth = {
        "x-pm-access-key": "fake-key",
        "x-pm-timestamp": "123",
        "x-pm-signature": "fake-signature",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(broker)),
            base_url="http://broker",
        ) as client:
            response = await client.get("/pmus" + path + "?limit=1", headers=auth)
        assert response.status_code == 200
        assert calls[0][0] == f"https://{host}{path}?limit=1"
        assert all(calls[0][1][key] == value for key, value in auth.items())
    finally:
        await broker.close()


@pytest.mark.parametrize("venue", ["pmus", "kalshi"])
@pytest.mark.parametrize("route", ["/metrics", "/health"])
@pytest.mark.asyncio
async def test_monitoring_responds_while_admission_is_blocked(
    venue, route, monkeypatch
) -> None:
    sleeping = asyncio.Event()

    async def sleep(delay):
        sleeping.set()
        await asyncio.Event().wait()

    async def fetcher(url, headers):
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    bucket = broker._buckets[venue]
    monkeypatch.setattr(bucket, "_tokens", 0)
    monkeypatch.setattr(bucket, "_sleep", sleep)
    queued = asyncio.create_task(broker.get(venue, "/queued", b"", {}, "weather"))
    await asyncio.wait_for(sleeping.wait(), 1)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(broker)),
            base_url="http://broker",
        ) as client:
            response = await asyncio.wait_for(client.get(route), 0.2)
        assert response.status_code == 200
        assert not queued.done()
        if route == "/metrics":
            values = response.json()["venues"][venue]
            assert values["callers"]["weather"]["queue_depth"] == 1
            assert values["upstream_requests"] == 0
    finally:
        await broker.close()
        await asyncio.wait_for(queued, 1)


@pytest.mark.asyncio
async def test_cancelled_leader_does_not_create_duplicate_upstream() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        started.set()
        await release.wait()
        return BrokerResponse(200, b"original", {})

    broker = Broker(Settings(), fetcher=fetcher)
    first = asyncio.create_task(broker.get("pmus", "/uncached", b"", {}, "weather"))
    await started.wait()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    follower = asyncio.create_task(broker.get("pmus", "/uncached", b"", {}, "other"))
    await asyncio.sleep(0)
    release.set()
    try:
        assert (await asyncio.wait_for(follower, 1)).body == b"original"
        assert len(calls) == 1, (
            "leader cancellation caused a duplicate upstream request"
        )
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_price_history_is_cached_for_thirty_seconds() -> None:
    clock = AdmissionClock()
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, str(len(calls)).encode(), {})

    broker = Broker(Settings(), fetcher=fetcher, clock=clock.monotonic)
    try:
        first = await broker.get("pmus", "/v1/price-history", b"symbol=A", {}, "one")
        clock.now = 29.999
        cached = await broker.get("pmus", "/v1/price-history", b"symbol=A", {}, "two")
        assert cached.body == first.body
        assert len(calls) == 1
        other = await broker.get("pmus", "/v1/price-history", b"symbol=B", {}, "two")
        assert other.body != first.body
        clock.now = 30
        fresh = await broker.get("pmus", "/v1/price-history", b"symbol=A", {}, "two")
        assert fresh.body != first.body
        assert len(calls) == 3
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        (formatdate(2_000_000_005, usegmt=True), 5.0),
        (formatdate(1_999_999_999, usegmt=True), 0.0),
        ("2.5", 2.5),
        ("garbage", 1.0),
        ("nan", 1.0),
        ("inf", 1.0),
    ],
)
@pytest.mark.asyncio
async def test_retry_after_controls_real_retry_delay(
    retry_after, expected, monkeypatch
):
    monkeypatch.setattr("venue_broker.broker.time.time", lambda: 2_000_000_000)
    sleeps = []
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return (
            BrokerResponse(429, b"limited", {"Retry-After": retry_after})
            if len(calls) == 1
            else BrokerResponse(200, b"ok", {})
        )

    async def sleep(delay):
        sleeps.append(delay)

    broker = Broker(Settings(), fetcher=fetcher, retry_sleeper=sleep)
    try:
        response = await broker.get("pmus", "/uncached", b"", {}, "weather")
        assert response.status_code == 200
        assert sleeps == [expected]
        assert len(calls) == 2
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_percent_encoded_non_ascii_path_reaches_upstream_unchanged() -> None:
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    path = "/pmus/v1/markets/caf%C3%A9/book"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(broker)),
            base_url="http://broker",
        ) as client:
            response = await client.get(path + "?label=%C3%A9")
        assert response.status_code == 200
        assert calls == [
            "https://gateway.polymarket.us/v1/markets/caf%C3%A9/book?label=%C3%A9"
        ]
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "path,cost",
    [
        ("/portfolio/orders/queue_positions", 10),
        ("/portfolio/orders/real-order-id", 2),
    ],
)
@pytest.mark.asyncio
async def test_literal_queue_positions_uses_full_default_cost(path, cost, monkeypatch):
    async def fetcher(url, headers):
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(rate_margin=1), fetcher=fetcher)
    bucket = broker._buckets["kalshi"]
    monkeypatch.setattr(bucket, "_clock", lambda: 0)
    monkeypatch.setattr(bucket, "_updated_at", 0)
    try:
        assert (await broker.get("kalshi", path, b"", {}, "weather")).status_code == 200
        assert await bucket.available() == 400 - cost
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "path,query,expected",
    [
        ("/markets/orderbooks", b"tickers=A&tickers=B&tickers=C", 30),
        ("/markets/orderbooks", b"tickers=A%2CB&tickers=C", 30),
        ("/markets/candlesticks", b"market_tickers=A%2CB%2CC&start_ts=1", 30),
    ],
)
def test_batch_cost_counts_every_item(path, query, expected):
    assert kalshi_token_cost(path, query) == expected


@pytest.mark.parametrize(
    "query", [b"", b"tickers=", b"tickers=A,,B", b"&".join([b"tickers=A"] * 101)]
)
@pytest.mark.asyncio
async def test_invalid_batches_are_rejected_without_upstream(query):
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    try:
        response = await broker.get(
            "kalshi", "/markets/orderbooks", query, {}, "weather"
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request_cost"
        assert calls == []
    finally:
        await broker.close()


@pytest.mark.parametrize("count,status", [(3, 200), (4, 400)])
@pytest.mark.asyncio
async def test_entire_batch_is_charged_or_rejected_before_admission(
    count, status, monkeypatch
):
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(
        Settings(kalshi_bucket_override=BucketConfig(10, 30, "tokens")), fetcher=fetcher
    )
    bucket = broker._buckets["kalshi"]
    monkeypatch.setattr(bucket, "_clock", lambda: 0)
    monkeypatch.setattr(bucket, "_updated_at", 0)
    query = "&".join(f"tickers=T{index}" for index in range(count)).encode()
    try:
        response = await asyncio.wait_for(
            broker.get("kalshi", "/markets/orderbooks", query, {}, "weather"), 0.2
        )
        assert response.status_code == status
        if status == 200:
            assert len(calls) == 1
            assert await bucket.available() == 0
        else:
            assert calls == []
            assert response.headers["X-Venue-Broker-Source"] == "broker"
            assert response.json()["error"]["code"] == "request_cost_exceeds_capacity"
            assert await bucket.available() == 30
            metrics = await broker.metrics()
            assert (
                metrics["venues"]["kalshi"]["callers"]["weather"]["queue_admissions"]
                == 0
            )
    finally:
        await broker.close()


@pytest.mark.parametrize("method", ["POST", "DELETE", "PUT", "PATCH"])
@pytest.mark.asyncio
async def test_write_batches_never_reach_upstream(method):
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(broker)),
            base_url="http://broker",
        ) as client:
            response = await client.request(
                method,
                "/kalshi/portfolio/orders/batched",
                json={"orders": [{"ticker": "fake"}]},
            )
        assert response.status_code == 405
        assert response.headers["X-Venue-Broker-Source"] == "broker"
        assert calls == []
    finally:
        await broker.close()


@pytest.mark.parametrize(
    "path",
    [
        "/margin/balance",
        "/margin/markets/A/orderbook",
        "/account/limits/perps",
        "/%6dargin/balance",
    ],
)
@pytest.mark.asyncio
async def test_perps_without_known_budget_is_refused_without_spending_predictions(path):
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(), fetcher=fetcher)
    try:
        response = await broker.get("kalshi", path, b"", {}, "weather")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "perps_budget_unconfigured"
        assert calls == []
        metrics = await broker.metrics()
        assert (
            metrics["venues"]["kalshi"]["callers"]["weather"]["queue_admissions"] == 0
        )
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_perps_and_prediction_budgets_are_independent(monkeypatch):
    monkeypatch.setenv("VENUE_BROKER_KALSHI_PERPS_READ_RATE", "50")
    monkeypatch.setenv("VENUE_BROKER_RATE_MARGIN", "1")
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings.from_env(), fetcher=fetcher)
    prediction = broker._buckets["kalshi"]
    monkeypatch.setattr(prediction, "_clock", lambda: 0)
    monkeypatch.setattr(prediction, "_updated_at", 0)
    monkeypatch.setattr(prediction, "_tokens", 0)
    # The perps bucket needs the same frozen clock. Left on wall time its
    # refill depends on how long the request happened to take, and the
    # assertion below became a range wide enough to pass by luck on a quiet
    # machine and fail on a busy one.
    perps_bucket = broker._perps_limiter._bucket
    monkeypatch.setattr(perps_bucket, "_clock", lambda: 0)
    monkeypatch.setattr(perps_bucket, "_updated_at", 0)
    try:
        response = await asyncio.wait_for(
            broker.get("kalshi", "/margin/balance", b"", {}, "weather"), 0.2
        )
        assert response.status_code == 200
        assert await prediction.available() == 0
        metrics = await broker.metrics()
        perps = metrics["venues"]["kalshi"]["perps"]
        assert perps["capacity"] == 50
        assert perps["refill_rate_per_second"] == 50
        assert perps["tokens_available"] == 45
        assert calls == ["https://external-api.kalshi.com/trade-api/v2/margin/balance"]
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_http_client_does_not_retain_or_replay_upstream_cookies(monkeypatch):
    received = []

    def upstream(request):
        received.append(request.headers.get("cookie"))
        return httpx.Response(
            200, headers={"Set-Cookie": "session=fake-private; Path=/"}
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(upstream), **kwargs),
    )
    fetcher = HttpxFetcher(1)
    try:
        await fetcher("https://venue.example/account", {"cookie": "caller=fake-one"})
        await fetcher("https://venue.example/account", {})
        assert received == ["caller=fake-one", None]
        assert list(fetcher._client.cookies.jar) == []
    finally:
        await fetcher.aclose()


@pytest.mark.asyncio
async def test_matching_credentials_share_but_fingerprints_change_between_brokers():
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def fetcher(url, headers):
        calls.append(url)
        started.set()
        await release.wait()
        return BrokerResponse(200, b"account", {})

    brokers = [Broker(Settings(), fetcher=fetcher) for _ in range(2)]
    scopes = []
    auth = {"authorization": "fake-credential"}
    try:
        for broker in brokers:
            started.clear()
            release.clear()
            first = asyncio.create_task(
                broker.get("pmus", "/v1/sports/teams", b"", auth, "one")
            )
            await started.wait()
            second = asyncio.create_task(
                broker.get("pmus", "/v1/sports/teams", b"", auth, "two")
            )
            await asyncio.sleep(0)
            scopes.append(next(iter(broker._inflight))[3])
            release.set()
            await asyncio.gather(first, second)
            cached = await broker.get("pmus", "/v1/sports/teams", b"", auth, "three")
            assert cached.body == b"account"
        assert len(calls) == 2
        assert scopes[0] != scopes[1]
        assert all("fake-credential" not in scope for scope in scopes)
    finally:
        for broker in brokers:
            await broker.close()
