import asyncio
import time
from collections.abc import Mapping

import pytest

from venue_broker.broker import Broker, BrokerResponse
from venue_broker.config import BucketConfig, CallerPolicy, Settings

TEST_RATE = 50.0
FAIRNESS_BOUND_SECONDS = 0.5
FLOOD_SIZE = 100


class RecordingFetcher:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def __call__(self, url: str, headers: Mapping[str, str]) -> BrokerResponse:
        self.paths.append(url.rsplit("/", maxsplit=1)[-1])
        return BrokerResponse(200, b"ok", {})


def fairness_settings() -> Settings:
    return Settings(
        rate_margin=1.0,
        polymarket_bucket_override=BucketConfig(
            TEST_RATE, 1.0, "requests"
        ),
        kalshi_bucket_override=BucketConfig(1000.0, 1000.0, "tokens"),
        caller_policy=CallerPolicy(
            default_class="standard",
            weights={"trading": 8, "standard": 2, "bulk": 1},
            callers={"collector": "trading", "execution": "bulk"},
        ),
    )


async def wait_for_queue_depth(
    broker: Broker, caller: str, minimum: int
) -> None:
    for _ in range(100):
        metrics = await broker.metrics()
        caller_values = metrics["venues"]["pmus"]["callers"].get(caller, {})
        if caller_values.get("queue_depth", 0) >= minimum:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{caller} never reached queue depth {minimum}")


async def stop_tasks(tasks: list[asyncio.Task[BrokerResponse]], broker: Broker) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await broker.close()


@pytest.mark.asyncio
async def test_bulk_flood_cannot_starve_trading_caller() -> None:
    fetcher = RecordingFetcher()
    broker = Broker(fairness_settings(), fetcher=fetcher)
    await broker.get("pmus", "/prime", b"", {}, "execution")
    flood = [
        asyncio.create_task(
            broker.get("pmus", f"/bulk-{index}", b"", {}, "execution")
        )
        for index in range(FLOOD_SIZE)
    ]
    await wait_for_queue_depth(broker, "execution", FLOOD_SIZE - 2)

    started = time.monotonic()
    trading = asyncio.create_task(
        broker.get("pmus", "/trading", b"", {}, "collector")
    )
    response = await asyncio.wait_for(trading, FAIRNESS_BOUND_SECONDS)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < FAIRNESS_BOUND_SECONDS
    assert fetcher.paths.index("trading") <= 2
    metrics = await broker.metrics()
    caller = metrics["venues"]["pmus"]["callers"]["collector"]
    assert caller["class"] == "trading"
    assert caller["queue_admissions"] == 1
    assert caller["queue_wait_seconds_max"] > 0
    await stop_tasks(flood, broker)


@pytest.mark.asyncio
async def test_sustained_trading_load_cannot_starve_bulk_caller() -> None:
    fetcher = RecordingFetcher()
    broker = Broker(fairness_settings(), fetcher=fetcher)
    await broker.get("pmus", "/prime", b"", {}, "collector")
    flood = [
        asyncio.create_task(
            broker.get("pmus", f"/trading-{index}", b"", {}, "collector")
        )
        for index in range(FLOOD_SIZE)
    ]
    await wait_for_queue_depth(broker, "collector", FLOOD_SIZE - 2)

    started = time.monotonic()
    bulk = asyncio.create_task(
        broker.get("pmus", "/bulk", b"", {}, "execution")
    )
    response = await asyncio.wait_for(bulk, FAIRNESS_BOUND_SECONDS)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < FAIRNESS_BOUND_SECONDS
    assert fetcher.paths.index("bulk") <= 10
    metrics = await broker.metrics()
    caller = metrics["venues"]["pmus"]["callers"]["execution"]
    assert caller["class"] == "bulk"
    assert caller["queue_admissions"] == 1
    assert caller["queue_wait_seconds_max"] > 0
    await stop_tasks(flood, broker)
