"""A fair asynchronous token bucket that waits instead of dropping bursts."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable


class TokenBucket:
    def __init__(
        self,
        refill_rate: float,
        capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rolling_limit: int | None = None,
    ) -> None:
        if refill_rate <= 0 or capacity <= 0:
            raise ValueError("refill rate and capacity must be positive")
        self.refill_rate = refill_rate
        self.capacity = capacity
        self._clock = clock
        self._sleep = sleeper
        self._tokens = capacity
        self._updated_at = clock()
        self._lock = asyncio.Lock()
        self._rolling_limit = rolling_limit
        self._admissions: deque[float] = deque()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._updated_at = now

    async def acquire(self, cost: float = 1.0) -> None:
        if cost <= 0:
            raise ValueError("token cost must be positive")
        if cost > self.capacity:
            raise ValueError(
                f"token cost {cost:g} exceeds bucket capacity {self.capacity:g}"
            )

        # Keeping the lock while waiting gives queued callers FIFO admission.
        async with self._lock:
            while True:
                self._refill()
                now = self._clock()
                # Keep the boundary conservatively closed. The next sleep goes
                # strictly past it, avoiding floating-point early admissions.
                while self._admissions and now > self._admissions[0] + 1.0:
                    self._admissions.popleft()
                window_full = (
                    self._rolling_limit is not None
                    and len(self._admissions) >= self._rolling_limit
                )
                if self._tokens >= cost and not window_full:
                    self._tokens -= cost
                    if self._rolling_limit is not None:
                        self._admissions.append(now)
                    return
                delay = max(0.0, (cost - self._tokens) / self.refill_rate)
                if window_full:
                    delay = max(delay, self._admissions[0] + 1.0 - now)
                await self._sleep(math.nextafter(now + delay, math.inf) - now)

    def admissions_in_window(self) -> int | None:
        """How many admissions fall inside the trailing second, right now.

        ``None`` when this bucket has no rolling limit (Kalshi meters tokens,
        not requests, so a per-second admission count means nothing there).

        This is the broker's half of an asymmetry nothing else on the machine
        has: it knows exactly what IT put on the wire in the last second, and
        it sees the venue's answer. A genuine 429 arriving while this number
        sat below the rolling limit is positive evidence that something OUTSIDE
        this queue pushed the IP past the venue's ceiling.

        Non-mutating and free of awaits, like ``available()``, so reading it on
        the 429 path cannot queue behind admission sleep or disturb the window
        it is reporting.
        """
        if self._rolling_limit is None:
            return None
        now = self._clock()
        return sum(1 for at in self._admissions if now <= at + 1.0)

    async def available(self) -> float:
        # All state is owned by one event loop. This projection contains no
        # await or mutation, so monitoring never queues behind admission sleep.
        elapsed = max(0.0, self._clock() - self._updated_at)
        return min(self.capacity, self._tokens + elapsed * self.refill_rate)
