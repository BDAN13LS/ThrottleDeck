import asyncio
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from venue_broker.broker import Broker, BrokerResponse
from venue_broker.config import BucketConfig, CallerPolicy, Settings
from venue_broker.control_policy import ControlPolicyReader


class FakeFetcher:
    def __init__(self, responses: list[BrokerResponse] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.responses = responses or [
            BrokerResponse(200, b'{"ok":true}', {"content-type": "application/json"})
        ]
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def __call__(self, url: str, headers: Mapping[str, str]) -> BrokerResponse:
        self.calls.append((url, dict(headers)))
        self.started.set()
        if self.block:
            await self.release.wait()
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def broker_settings() -> Settings:
    return Settings(
        rate_margin=1.0,
        polymarket_bucket_override=BucketConfig(100.0, 100.0, "requests"),
        kalshi_bucket_override=BucketConfig(1000.0, 1000.0, "tokens"),
        retry_base_seconds=0.01,
        caller_policy=CallerPolicy(
            "standard",
            {"standard": 1},
            {"execution": "standard", "weather": "standard"},
        ),
    )


def write_control_policy(path, *, global_state="allowed", execution="allowed"):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "revision": 1,
                "global": global_state,
                "apps": {
                    "collector": "allowed",
                    "research": "allowed",
                    "execution": execution,
                },
            }
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


@pytest.mark.asyncio
async def test_two_concurrent_identical_requests_make_one_upstream_call() -> None:
    fetcher = FakeFetcher()
    fetcher.block = True
    broker = Broker(broker_settings(), fetcher=fetcher)

    first = asyncio.create_task(
        broker.get("pmus", "/v1/markets/game/book", b"depth=10", {}, "execution")
    )
    await fetcher.started.wait()
    second = asyncio.create_task(
        broker.get("pmus", "/v1/markets/game/book", b"depth=10", {}, "weather")
    )
    await asyncio.sleep(0)
    fetcher.release.set()

    first_response, second_response = await asyncio.gather(first, second)

    assert first_response.body == second_response.body
    assert len(fetcher.calls) == 1
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["coalesced_requests"] == 1
    assert metrics["venues"]["pmus"]["callers"]["weather"]["coalesced_requests"] == 1


@pytest.mark.asyncio
async def test_fifty_cold_cache_callers_make_one_upstream_request() -> None:
    fetcher = FakeFetcher()
    fetcher.block = True
    broker = Broker(broker_settings(), fetcher=fetcher)

    requests = [
        asyncio.create_task(
            broker.get(
                "pmus",
                "/v1/markets/game/book",
                b"depth=10",
                {},
                f"caller-{index}",
            )
        )
        for index in range(50)
    ]
    await fetcher.started.wait()
    await asyncio.sleep(0.02)

    assert len(fetcher.calls) == 1
    fetcher.release.set()
    responses = await asyncio.gather(*requests)

    assert {response.body for response in responses} == {b'{"ok":true}'}
    assert len(fetcher.calls) == 1
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["coalesced_requests"] == 49


@pytest.mark.asyncio
async def test_cache_serves_repeated_request_without_upstream_call() -> None:
    fetcher = FakeFetcher()
    broker = Broker(broker_settings(), fetcher=fetcher)

    first = await broker.get("pmus", "/v1/sports/teams", b"league=nfl", {}, "execution")
    second = await broker.get("pmus", "/v1/sports/teams", b"league=nfl", {}, "weather")

    assert first.body == second.body
    assert len(fetcher.calls) == 1
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["cache_hits"] == 1
    assert metrics["venues"]["pmus"]["callers"]["weather"]["cache_hits"] == 1


@pytest.mark.asyncio
async def test_cache_entry_expires_at_its_path_ttl() -> None:
    clock = ManualClock()
    fetcher = FakeFetcher()
    broker = Broker(broker_settings(), fetcher=fetcher, clock=clock.monotonic)

    await broker.get("pmus", "/v1/sports/teams", b"", {}, "execution")
    clock.now = 299.9
    await broker.get("pmus", "/v1/sports/teams", b"", {}, "execution")
    clock.now = 300.0
    await broker.get("pmus", "/v1/sports/teams", b"", {}, "execution")

    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_cache_has_a_bounded_number_of_entries() -> None:
    fetcher = FakeFetcher()
    settings = broker_settings()
    settings = Settings(
        rate_margin=settings.rate_margin,
        polymarket_bucket_override=settings.polymarket_bucket_override,
        kalshi_bucket_override=settings.kalshi_bucket_override,
        max_cache_entries=1,
    )
    broker = Broker(settings, fetcher=fetcher)

    await broker.get("pmus", "/v1/sports/teams", b"league=nfl", {}, "execution")
    await broker.get("pmus", "/v1/sports/teams", b"league=mlb", {}, "execution")
    await broker.get("pmus", "/v1/sports/teams", b"league=nfl", {}, "execution")

    assert len(fetcher.calls) == 3


@pytest.mark.asyncio
async def test_unknown_paths_are_not_cached() -> None:
    fetcher = FakeFetcher()
    broker = Broker(broker_settings(), fetcher=fetcher)

    await broker.get("kalshi", "/unknown/new-endpoint", b"", {}, "weather")
    await broker.get("kalshi", "/unknown/new-endpoint", b"", {}, "weather")

    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_authenticated_cache_uses_credentials_without_keying_secrets() -> None:
    fetcher = FakeFetcher(
        [
            BrokerResponse(200, b"first", {}),
            BrokerResponse(200, b"second", {}),
            BrokerResponse(200, b"third", {}),
        ]
    )
    broker = Broker(broker_settings(), fetcher=fetcher)

    await broker.get(
        "pmus",
        "/v1/sports/teams",
        b"",
        {"authorization": "Bearer secret-one"},
        "execution",
    )
    same_caller = await broker.get(
        "pmus",
        "/v1/sports/teams",
        b"",
        {"authorization": "Bearer rotated-secret"},
        "execution",
    )
    other_caller = await broker.get(
        "pmus",
        "/v1/sports/teams",
        b"",
        {"authorization": "Bearer secret-two"},
        "weather",
    )

    assert same_caller.body == b"second"
    assert other_caller.body == b"third"
    assert len(fetcher.calls) == 3
    assert fetcher.calls[0][1]["authorization"] == "Bearer secret-one"
    assert fetcher.calls[1][1]["authorization"] == "Bearer rotated-secret"
    assert fetcher.calls[2][1]["authorization"] == "Bearer secret-two"
    assert "secret" not in repr(broker._cache)


@pytest.mark.asyncio
async def test_unidentified_authenticated_requests_are_never_cached() -> None:
    fetcher = FakeFetcher()
    broker = Broker(broker_settings(), fetcher=fetcher)
    headers = {"kalshi-access-key": "not-a-real-key"}

    await broker.get("kalshi", "/markets", b"", headers, "unknown")
    await broker.get("kalshi", "/markets", b"", headers, "unknown")

    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_global_rate_limit_message_does_not_trigger_backoff() -> None:
    response = BrokerResponse(429, b'{"message":"Global Rate Limit Exceeded"}', {})
    fetcher = FakeFetcher([response])
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    broker = Broker(broker_settings(), fetcher=fetcher, retry_sleeper=record_sleep)

    actual = await broker.get("pmus", "/v1/example", b"", {}, "weather")

    assert actual.status_code == 429
    assert len(fetcher.calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_real_429_is_retried_and_counted() -> None:
    fetcher = FakeFetcher(
        [
            BrokerResponse(429, b'{"message":"Too Many Requests"}', {}),
            BrokerResponse(200, b"ok", {}),
        ]
    )
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    broker = Broker(broker_settings(), fetcher=fetcher, retry_sleeper=record_sleep)

    actual = await broker.get("pmus", "/v1/example", b"", {}, "weather")

    assert actual.status_code == 200
    assert len(fetcher.calls) == 2
    assert sleeps == [0.01]
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["upstream_429s"] == 1
    assert metrics["venues"]["pmus"]["callers"]["weather"]["upstream_429s"] == 1


@pytest.mark.asyncio
async def test_one_budget_is_shared_across_different_callers() -> None:
    """The property the whole service exists for.

    Every bot on this machine already rate-limits itself correctly and they
    still breach the venue's ceiling together, because the limit is per IP and
    no process can see another. Measured on 2026-09-13: Execution Bot at 16 rps,
    Signal Collector's scanner at 8 and its us_trades stream at 8, up to 32 against
    a ceiling of 20.

    So a budget that is per-caller here would reproduce exactly the bug this
    replaces, while looking like a fix. Two callers must draw down one bucket.
    """
    fetcher = FakeFetcher()
    settings = Settings(
        rate_margin=1.0,
        # Two requests of room, refilling so slowly that nothing is returned
        # within the test's lifetime.
        polymarket_bucket_override=BucketConfig(0.0001, 2.0, "requests"),
        kalshi_bucket_override=BucketConfig(1000.0, 1000.0, "tokens"),
        retry_base_seconds=0.01,
    )
    broker = Broker(settings, fetcher=fetcher, clock=ManualClock().monotonic)

    await broker.get("pmus", "/a", b"", {}, "execution")
    await broker.get("pmus", "/b", b"", {}, "collector")

    # The budget is now spent. A third request from a third caller must not
    # find a fresh allowance of its own.
    third = asyncio.create_task(broker.get("pmus", "/c", b"", {}, "some-other-bot"))
    await asyncio.sleep(0.05)

    assert not third.done(), (
        "a third caller was served after two others spent the whole budget; "
        "the bucket is per-caller, which is the bug this service replaces"
    )
    third.cancel()
    await asyncio.gather(third, return_exceptions=True)
    await broker.close()


def test_a_retry_delay_is_capped() -> None:
    """A venue can ask for a wait longer than a caller's request is worth.

    Retry-After is taken from the venue verbatim and exponential backoff is
    unbounded, so a header of 86400 parks a request for a day. For a trading
    caller a slow answer is worse than an error: it cannot decide to give up on
    something it does not know is stuck.
    """
    from venue_broker.broker import MAX_RETRY_DELAY_SECONDS, capped_retry_delay

    assert capped_retry_delay(86400.0) == MAX_RETRY_DELAY_SECONDS
    assert capped_retry_delay(0.5) == 0.5
    assert capped_retry_delay(MAX_RETRY_DELAY_SECONDS + 1) == MAX_RETRY_DELAY_SECONDS
    assert MAX_RETRY_DELAY_SECONDS <= 30


def test_a_non_ascii_query_does_not_crash_the_request() -> None:
    """Query bytes arrive as the caller sent them, not as ASCII.

    Decoding them as ASCII raises before the request is ever made, so a search
    term with an accent in it fails as a broker fault rather than reaching the
    venue.
    """
    from venue_broker.broker import upstream_url

    url = upstream_url("pmus", "/v1/events", "q=café".encode())

    assert url.startswith("https://gateway.polymarket.us/v1/events?")
    assert "caf" in url


@pytest.mark.asyncio
async def test_a_latency_stopgap_is_counted_apart_from_a_real_rate_limit() -> None:
    """The venue sends a 429 that its own docs say is NOT a rate limit.

    `Global Rate Limit Exceeded` is documented as a latency stopgap: a request
    queued but not processed within five seconds, returned to protect the caller
    from a stale fill. The broker already knows not to back off for it.

    But it was counted in `upstream_429s` before that check, so the number
    conflated "the venue throttled us" with "the venue was slow". That matters
    now: the Signal Collector session is building a bypass detector on this counter —
    a 429 arriving while the broker held itself at 18 is proof something else
    pushed the IP over 20. Built on a conflated count, that detector accuses a
    bystander every time the venue is merely slow.
    """
    stopgap = BrokerResponse(
        429,
        b'{"error":"Global Rate Limit Exceeded"}',
        {"content-type": "application/json"},
    )
    fetcher = FakeFetcher(responses=[stopgap])
    broker = Broker(broker_settings(), fetcher=fetcher, clock=ManualClock().monotonic)

    await broker.get("pmus", "/v1/events", b"", {}, "execution")
    metrics = await broker.metrics()
    pmus = metrics["venues"]["pmus"]

    assert pmus["upstream_latency_stopgaps"] == 1
    assert pmus["upstream_429s"] == 0, (
        "a latency stopgap was counted as a rate limit; a bypass detector built "
        "on this number would accuse a caller that did nothing"
    )
    await broker.close()


@pytest.mark.asyncio
async def test_a_real_rate_limit_is_still_counted() -> None:
    fetcher = FakeFetcher(
        responses=[BrokerResponse(429, b'{"error":"too many requests"}', {})]
    )
    broker = Broker(broker_settings(), fetcher=fetcher, clock=ManualClock().monotonic)

    await broker.get("pmus", "/v1/events", b"", {}, "execution")
    pmus = (await broker.metrics())["venues"]["pmus"]

    assert pmus["upstream_429s"] >= 1
    assert pmus["upstream_latency_stopgaps"] == 0
    await broker.close()


@pytest.mark.asyncio
async def test_a_429_with_headroom_is_flagged_as_an_outside_caller() -> None:
    """THE ASYMMETRY. The broker knows what it put on the wire and sees the
    venue's answer; nothing else on the machine holds both halves.

    Its ceiling is 18 against the venue's 20, deliberately. So traffic this
    broker admitted cannot by itself earn a 429 — and one arriving while the
    trailing second held far fewer than 18 is positive evidence that something
    outside this queue pushed the IP over.

    It does NOT identify the culprit. A process that never speaks to the broker
    is invisible to it. It proves one exists, and gives the second it happened.
    """
    fetcher = FakeFetcher(
        responses=[BrokerResponse(429, b'{"error":"too many requests"}', {})]
    )
    broker = Broker(broker_settings(), fetcher=fetcher, clock=ManualClock().monotonic)

    await broker.get("pmus", "/v1/events", b"", {}, "execution")
    pmus = (await broker.metrics())["venues"]["pmus"]

    assert pmus["upstream_429s"] >= 1
    assert pmus["upstream_429s_with_headroom"] >= 1, (
        "a throttle arrived while this broker had admitted one request in the "
        "trailing second and it was not flagged as foreign pressure"
    )
    await broker.close()


@pytest.mark.asyncio
async def test_a_latency_stopgap_never_accuses_anyone() -> None:
    """The bystander case the counter split exists for. The venue being SLOW
    must not register as another process stealing the budget."""
    stopgap = BrokerResponse(
        429,
        b'{"error":"Global Rate Limit Exceeded"}',
        {"content-type": "application/json"},
    )
    fetcher = FakeFetcher(responses=[stopgap])
    broker = Broker(broker_settings(), fetcher=fetcher, clock=ManualClock().monotonic)

    await broker.get("pmus", "/v1/events", b"", {}, "execution")
    pmus = (await broker.metrics())["venues"]["pmus"]

    assert pmus["upstream_latency_stopgaps"] == 1
    assert pmus["upstream_429s_with_headroom"] == 0, (
        "the venue was merely slow and the detector accused a bystander"
    )
    await broker.close()


@pytest.mark.asyncio
async def test_the_refusal_is_logged_with_when_what_and_how_much_room(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A counter cannot say when, for what, or for whom. That blindness is what
    made 311 refusals on the Signal Collector side undiagnosable this morning."""
    import logging

    fetcher = FakeFetcher(
        responses=[BrokerResponse(429, b'{"error":"too many requests"}', {})]
    )
    broker = Broker(broker_settings(), fetcher=fetcher, clock=ManualClock().monotonic)

    with caplog.at_level(logging.WARNING, logger="venue_broker.broker"):
        await broker.get("pmus", "/v1/events", b"", {}, "execution")

    messages = [r.getMessage() for r in caplog.records]
    assert any("429" in m for m in messages), messages
    joined = " ".join(messages)
    assert "/v1/events" in joined, "the refused path is not in the log"
    assert "execution" in joined, "the caller is not in the log"
    assert "trailing second" in joined, "how much room we had is not in the log"
    await broker.close()


def test_the_admission_window_reader_does_not_disturb_the_window() -> None:
    """It is read on the 429 path, so it must be a projection like
    ``available()`` — no awaits, no mutation. If reading the number changed it,
    the evidence would be altered by the act of collecting it."""
    from venue_broker.limiter import TokenBucket

    clock = ManualClock()
    bucket = TokenBucket(18, 18, clock=clock.monotonic, rolling_limit=18)
    before = bucket.admissions_in_window()
    for _ in range(5):
        assert bucket.admissions_in_window() == before
    assert before == 0


def test_a_bucket_without_a_rolling_limit_reports_unknown_not_zero() -> None:
    """Kalshi meters TOKENS, not requests, so a per-second admission count is
    meaningless there. Zero would read as "we sent nothing", which is a claim;
    None is the truth."""
    from venue_broker.limiter import TokenBucket

    clock = ManualClock()
    assert TokenBucket(270, 540, clock=clock.monotonic).admissions_in_window() is None


@pytest.mark.asyncio
async def test_stopped_policy_precedes_cache_inflight_limiter_and_fetcher(
    tmp_path,
) -> None:
    policy_path = tmp_path / "broker-policy.json"
    write_control_policy(policy_path)
    fetcher = FakeFetcher()
    fetcher.block = True
    clock = ManualClock()
    broker = Broker(
        broker_settings(),
        fetcher=fetcher,
        policy_reader=ControlPolicyReader(policy_path),
        clock=clock.monotonic,
    )
    first = asyncio.create_task(
        broker.get("pmus", "/v1/markets/game/book", b"", {}, "execution")
    )
    await fetcher.started.wait()
    before_admissions = broker._buckets["pmus"].admissions_in_window()
    write_control_policy(policy_path, execution="stopped")

    denied = await asyncio.wait_for(
        broker.get("pmus", "/v1/markets/game/book", b"", {}, "execution"),
        timeout=0.2,
    )

    assert denied.status_code == 423
    assert denied.json()["error"]["code"] == "broker_access_stopped"
    assert denied.headers["X-Venue-Broker-Source"] == "broker"
    assert len(fetcher.calls) == 1
    assert broker._buckets["pmus"].admissions_in_window() == before_admissions
    metrics = await broker.metrics()
    assert metrics["venues"]["pmus"]["requests_served"] == 1
    assert metrics["venues"]["pmus"]["coalesced_requests"] == 0
    assert metrics["venues"]["pmus"]["suspended_refusals"] == 1
    fetcher.release.set()
    await first
    await broker.close()


@pytest.mark.asyncio
async def test_global_stop_applies_to_unknown_caller(tmp_path) -> None:
    policy_path = tmp_path / "broker-policy.json"
    write_control_policy(policy_path, global_state="stopped")
    fetcher = FakeFetcher()
    broker = Broker(
        broker_settings(),
        fetcher=fetcher,
        policy_reader=ControlPolicyReader(policy_path),
    )

    response = await broker.get("kalshi", "/markets", b"", {}, "unknown")

    assert response.status_code == 423
    assert fetcher.calls == []
    await broker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "truncated", "future"])
async def test_invalid_policy_fails_closed(tmp_path, damage) -> None:
    policy_path = tmp_path / "broker-policy.json"
    if damage == "truncated":
        policy_path.write_text("{", encoding="utf-8")
    elif damage == "future":
        write_control_policy(policy_path)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        payload["schemaVersion"] = 2
        policy_path.write_text(json.dumps(payload), encoding="utf-8")
    broker = Broker(
        broker_settings(),
        fetcher=FakeFetcher(),
        policy_reader=ControlPolicyReader(policy_path),
    )

    response = await broker.get("pmus", "/v1/events", b"", {}, "execution")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "control_state_unavailable"
    await broker.close()


@pytest.mark.asyncio
async def test_metrics_have_process_identity_bounded_callers_and_kalshi_lanes() -> None:
    fixed = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)
    policy = CallerPolicy("standard", {"standard": 1}, {"collector": "standard"})
    settings = Settings(
        rate_margin=1.0,
        kalshi_perps_read_rate=100,
        caller_policy=policy,
    )
    broker = Broker(settings, fetcher=FakeFetcher(), wall_clock=lambda: fixed)
    await broker.get("kalshi", "/markets", b"", {}, "collector")
    await broker.get("kalshi", "/margin/balance", b"", {}, "random-one")
    await broker.get("kalshi", "/markets", b"", {}, "random-two")
    await broker.get("kalshi", "/markets", b"", {}, "unknown")

    metrics = await broker.metrics()

    assert metrics["schema_version"] == 1
    assert metrics["instance_id"]
    assert metrics["started_at"] == fixed.isoformat()
    assert metrics["sampled_at"] == fixed.isoformat()
    assert metrics["kalshi_tier"] == "basic"
    callers = metrics["venues"]["kalshi"]["callers"]
    assert set(callers) == {"collector", "other", "unknown"}
    assert callers["other"]["requests_served"] == 2
    assert callers["collector"]["last_request_at"] == fixed.isoformat()
    assert (
        metrics["venues"]["kalshi"]["prediction_callers"]["other"]["requests_served"]
        == 1
    )
    assert metrics["venues"]["kalshi"]["perps_callers"]["other"]["requests_served"] == 1
    await broker.close()
