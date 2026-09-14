import asyncio
from collections.abc import Mapping

import httpx
import pytest

from venue_broker.broker import Broker, BrokerResponse
from venue_broker.config import BucketConfig, Settings


def settings(*, refill_rate: float = 100.0, capacity: float = 100.0) -> Settings:
    return Settings(
        rate_margin=1.0,
        polymarket_bucket_override=BucketConfig(
            refill_rate, capacity, "requests"
        ),
        kalshi_bucket_override=BucketConfig(1000.0, 1000.0, "tokens"),
        max_retries=0,
    )


class RaisingFetcher:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __call__(self, url: str, headers: Mapping[str, str]) -> BrokerResponse:
        raise self.error


@pytest.mark.asyncio
async def test_upstream_timeout_is_a_structured_broker_504() -> None:
    request = httpx.Request("GET", "https://venue.example/slow")
    broker = Broker(
        settings(),
        fetcher=RaisingFetcher(httpx.ReadTimeout("too slow", request=request)),
    )

    response = await broker.get("pmus", "/slow", b"", {}, "collector")

    assert response.status_code == 504
    assert response.headers["X-Venue-Broker-Source"] == "broker"
    assert response.json()["error"]["code"] == "upstream_timeout"


@pytest.mark.asyncio
async def test_upstream_connection_failure_is_a_structured_broker_502() -> None:
    request = httpx.Request("GET", "https://venue.example/unreachable")
    broker = Broker(
        settings(),
        fetcher=RaisingFetcher(
            httpx.ConnectError("connection refused", request=request)
        ),
    )

    response = await broker.get("pmus", "/unreachable", b"", {}, "collector")

    assert response.status_code == 502
    assert response.headers["X-Venue-Broker-Source"] == "broker"
    assert response.json()["error"]["code"] == "upstream_connection_failed"


@pytest.mark.asyncio
async def test_venue_5xx_keeps_exact_status_and_body_and_is_labeled_venue() -> None:
    expected = BrokerResponse(
        503,
        b'{"venue":"maintenance"}',
        {"content-type": "application/json", "x-venue-request-id": "abc"},
    )

    async def fetcher(url: str, headers: Mapping[str, str]) -> BrokerResponse:
        return expected

    broker = Broker(settings(), fetcher=fetcher)
    response = await broker.get("kalshi", "/exchange/status", b"", {}, "execution")

    assert response.status_code == expected.status_code
    assert response.body == expected.body
    assert response.headers["x-venue-request-id"] == "abc"
    assert response.headers["X-Venue-Broker-Source"] == "venue"


@pytest.mark.asyncio
async def test_closing_broker_releases_queued_callers_with_503() -> None:
    async def fetcher(url: str, headers: Mapping[str, str]) -> BrokerResponse:
        return BrokerResponse(200, b"ok", {})

    broker = Broker(settings(refill_rate=0.0001, capacity=1.0), fetcher=fetcher)
    await broker.get("pmus", "/first", b"", {}, "execution")
    queued = asyncio.create_task(
        broker.get("pmus", "/queued", b"", {}, "collector")
    )
    await asyncio.sleep(0.02)

    await broker.close()
    response = await asyncio.wait_for(queued, 0.2)

    assert response.status_code == 503
    assert response.headers["X-Venue-Broker-Source"] == "broker"
    assert response.json()["error"]["code"] == "broker_closing"
