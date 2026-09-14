import asyncio
from collections.abc import Mapping

import httpx
import pytest

from venue_broker.app import create_app, parse_route
from venue_broker.broker import Broker, BrokerResponse
from venue_broker.config import BucketConfig, Settings


class RecordingFetcher:
    def __init__(self) -> None:
        self.url = ""
        self.headers: dict[str, str] = {}

    async def __call__(self, url: str, headers: Mapping[str, str]) -> BrokerResponse:
        self.url = url
        self.headers = dict(headers)
        return BrokerResponse(
            200,
            b'{"proxied":true}',
            {"content-type": "application/json"},
        )


@pytest.mark.asyncio
async def test_app_proxies_path_query_and_auth_but_not_caller_header() -> None:
    fetcher = RecordingFetcher()
    settings = Settings(
        rate_margin=1.0,
        polymarket_bucket_override=BucketConfig(100.0, 100.0, "requests"),
        kalshi_bucket_override=BucketConfig(1000.0, 1000.0, "tokens"),
    )
    app = create_app(Broker(settings, fetcher=fetcher))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://broker"
    ) as client:
        response = await client.get(
            "/kalshi/markets",
            params={"status": "open"},
            headers={
                "X-Broker-Caller": "weather",
                "KALSHI-ACCESS-KEY": "not-a-real-key",
            },
        )

    assert response.json() == {"proxied": True}
    assert fetcher.url == (
        "https://external-api.kalshi.com/trade-api/v2/markets?status=open"
    )
    assert fetcher.headers["kalshi-access-key"] == "not-a-real-key"
    assert "x-broker-caller" not in fetcher.headers


@pytest.mark.asyncio
async def test_metrics_route_is_json() -> None:
    settings = Settings(rate_margin=1.0)
    app = create_app(Broker(settings, fetcher=RecordingFetcher()))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://broker"
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert set(response.json()["venues"]) == {"pmus", "kalshi"}


@pytest.mark.asyncio
async def test_health_is_cheap_and_does_not_call_upstream() -> None:
    fetcher = RecordingFetcher()
    broker = Broker(Settings(rate_margin=1.0), fetcher=fetcher)
    app = create_app(broker)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://broker"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fetcher.url == ""


@pytest.mark.asyncio
async def test_non_get_requests_are_rejected() -> None:
    settings = Settings(rate_margin=1.0)
    app = create_app(Broker(settings, fetcher=RecordingFetcher()))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://broker"
    ) as client:
        response = await client.post("/pmus/v1/orders")

    assert response.status_code == 405
    assert response.headers["X-Venue-Broker-Source"] == "broker"
    assert response.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.asyncio
async def test_every_existing_broker_surface_remains_get_only() -> None:
    app = create_app(Broker(Settings(rate_margin=1.0), fetcher=RecordingFetcher()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://broker"
    ) as client:
        responses = [
            await client.post(path)
            for path in ("/metrics", "/health", "/pmus/v1/events")
        ]
    assert [response.status_code for response in responses] == [405, 405, 405]
    assert all(
        response.json()["error"]["code"] == "method_not_allowed"
        for response in responses
    )


@pytest.mark.asyncio
async def test_caller_disconnect_cancels_its_waiter_without_sending_response() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetcher(url: str, headers: Mapping[str, str]) -> BrokerResponse:
        started.set()
        await release.wait()
        return BrokerResponse(200, b"ok", {})

    broker = Broker(Settings(rate_margin=1.0), fetcher=fetcher)
    app = create_app(broker)
    incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": b"", "more_body": False})
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return await incoming.get()

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/pmus/v1/events",
        "raw_path": b"/pmus/v1/events",
        "query_string": b"",
        "headers": [(b"x-broker-caller", b"collector")],
    }
    request = asyncio.create_task(app(scope, receive, send))
    await started.wait()
    await incoming.put({"type": "http.disconnect"})
    await asyncio.wait_for(request, 0.2)

    assert sent == []
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["callers"]["other"]["caller_disconnects"] == 1
    release.set()
    await broker.close()


def test_a_route_with_dot_segments_is_refused() -> None:
    """Policy is chosen by path, so a path that lies about itself breaks it.

    The upstream host is fixed, so traversal cannot reach another server. What
    it can do is worse for a broker: the cache TTL, the Kalshi token cost and
    the choice between gateway and api.polymarket.us are ALL decided from the
    path. A request that presents a cheap public-looking path and resolves
    upstream to something else is metered wrong and cached wrong, and a caller
    with a malformed path gets that silently rather than an error.
    """
    for raw in (
        b"/pmus/../v1/events",
        b"/kalshi/../../portfolio/balance",
        b"/pmus/a/../../b",
        b"/pmus/%2e%2e/v1/events",
        b"/kalshi/%2E%2E/portfolio/balance",
    ):
        assert parse_route(raw) is None, raw


def test_ordinary_routes_still_parse() -> None:
    """The guard must not reject a path that merely contains a dot."""
    assert parse_route(b"/pmus/v1/events") == ("pmus", "/v1/events")
    assert parse_route(b"/kalshi/portfolio/balance") == (
        "kalshi",
        "/portfolio/balance",
    )
    assert parse_route(b"/pmus/v1/price-history") == ("pmus", "/v1/price-history")
    assert parse_route(b"/pmus/v1/a.b/c") == ("pmus", "/v1/a.b/c")
