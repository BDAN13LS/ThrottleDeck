import asyncio

import pytest

from venue_broker.limiter import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_burst_above_budget_is_spread_instead_of_dropped() -> None:
    clock = FakeClock()
    bucket = TokenBucket(
        refill_rate=2.0,
        capacity=2.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    admitted_at: list[float] = []

    async def request() -> None:
        await bucket.acquire()
        admitted_at.append(clock.now)

    await asyncio.gather(*(request() for _ in range(5)))

    assert len(admitted_at) == 5
    assert max(admitted_at) - min(admitted_at) == pytest.approx(1.5)
    assert clock.sleeps == pytest.approx([0.5, 0.5, 0.5])


@pytest.mark.asyncio
async def test_bucket_charges_variable_token_costs() -> None:
    clock = FakeClock()
    bucket = TokenBucket(
        refill_rate=100.0,
        capacity=100.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    await bucket.acquire(50.0)
    await bucket.acquire(60.0)

    assert clock.sleeps == pytest.approx([0.1])
    assert await bucket.available() == pytest.approx(0.0)
