"""Weighted fair admission without splitting the venue's physical budget.

Every shared upstream budget has exactly one token bucket, because its limit is
shared. Requests wait in caller-class queues before touching that bucket. Smooth
weighted round-robin chooses the next class, and round-robin chooses a caller
inside the class. Thus an early bulk flood cannot occupy the token bucket's FIFO
lock, while every class with a positive configured weight continues to receive
turns. Weights are relative admission turns, not reserved capacity: 8:2:1 gives
equal-cost trading traffic about four times standard throughput and eight times
bulk throughput while all three remain backlogged.

The scheduler deliberately does not encode project names. A caller's class is
resolved entirely by the startup-loaded TOML policy, so promotion is a config
edit and restart rather than a code change.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from venue_broker.config import CallerPolicy


class TokenSource(Protocol):
    async def acquire(self, cost: float = 1.0) -> None: ...

    async def available(self) -> float: ...

    def admissions_in_window(self) -> int | None: ...


class FairLimiterClosed(RuntimeError):
    """Admission stopped because the broker is closing."""


@dataclass(slots=True, eq=False)
class _Waiter:
    caller: str
    class_name: str
    cost: float
    enqueued_at: float


class WeightedFairLimiter:
    """Serialize token admission in weighted class order."""

    def __init__(
        self,
        bucket: TokenSource,
        policy: CallerPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bucket = bucket
        self._policy = policy
        self._clock = clock
        self._condition = asyncio.Condition()
        self._queues: dict[str, dict[str, deque[_Waiter]]] = {
            name: {} for name in policy.weights
        }
        self._callers: dict[str, deque[str]] = {
            name: deque() for name in policy.weights
        }
        self._scores = {name: 0 for name in policy.weights}
        self._serving: _Waiter | None = None
        self._closed = False
        self._closed_event = asyncio.Event()
        self._caller_depth: Counter[str] = Counter()
        self._class_depth: Counter[str] = Counter()

    def admissions_in_window(self) -> int | None:
        """The bucket's trailing-second admission count, or None.

        A passthrough so the 429 path can ask "how much room did we have?"
        without reaching past this wrapper into the bucket.
        """
        reader = getattr(self._bucket, "admissions_in_window", None)
        return reader() if callable(reader) else None

    async def acquire(self, caller: str, cost: float = 1.0) -> float:
        """Wait for a fair turn and tokens, returning queue wait seconds."""
        class_name = self._policy.class_for(caller)
        waiter = _Waiter(caller, class_name, cost, self._clock())
        selected = False

        async with self._condition:
            if self._closed:
                raise FairLimiterClosed
            self._enqueue(waiter)
            self._select_if_idle()
            try:
                while self._serving is not waiter:
                    if self._closed:
                        raise FairLimiterClosed
                    await self._condition.wait()
                if self._closed:
                    raise FairLimiterClosed
                selected = True
            except BaseException:
                self._abandon(waiter)
                self._select_if_idle()
                self._condition.notify_all()
                raise

        bucket_task = asyncio.create_task(self._bucket.acquire(cost))
        close_task = asyncio.create_task(self._closed_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {bucket_task, close_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if close_task in done:
                raise FairLimiterClosed
            await bucket_task
            return max(0.0, self._clock() - waiter.enqueued_at)
        finally:
            for task in (bucket_task, close_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(bucket_task, close_task, return_exceptions=True)
            if selected:
                await self._finish(waiter)

    async def close(self) -> None:
        async with self._condition:
            if self._closed:
                return
            self._closed = True
            self._closed_event.set()
            self._condition.notify_all()

    async def available(self) -> float:
        return await self._bucket.available()

    async def snapshot(self) -> dict[str, Mapping[str, int]]:
        async with self._condition:
            return {
                "callers": dict(self._caller_depth),
                "classes": {
                    name: self._class_depth[name] for name in self._policy.weights
                },
            }

    def _enqueue(self, waiter: _Waiter) -> None:
        queues = self._queues[waiter.class_name]
        queue = queues.get(waiter.caller)
        if queue is None:
            queue = deque()
            queues[waiter.caller] = queue
            self._callers[waiter.class_name].append(waiter.caller)
        queue.append(waiter)
        self._caller_depth[waiter.caller] += 1
        self._class_depth[waiter.class_name] += 1

    def _select_if_idle(self) -> None:
        if self._serving is not None or self._closed:
            return
        active = [name for name, callers in self._callers.items() if callers]
        if not active:
            return
        for name in self._scores:
            if name in active:
                self._scores[name] += self._policy.weights[name]
            else:
                self._scores[name] = 0
        chosen = max(active, key=lambda name: self._scores[name])
        self._scores[chosen] -= sum(self._policy.weights[name] for name in active)

        caller = self._callers[chosen].popleft()
        queue = self._queues[chosen][caller]
        self._serving = queue.popleft()
        if queue:
            self._callers[chosen].append(caller)
        else:
            del self._queues[chosen][caller]

    def _abandon(self, waiter: _Waiter) -> None:
        if self._serving is waiter:
            self._serving = None
        else:
            queue = self._queues[waiter.class_name].get(waiter.caller)
            if queue is not None:
                try:
                    queue.remove(waiter)
                except ValueError:
                    pass
                if not queue:
                    del self._queues[waiter.class_name][waiter.caller]
                    try:
                        self._callers[waiter.class_name].remove(waiter.caller)
                    except ValueError:
                        pass
        self._decrement_depth(waiter)

    async def _finish(self, waiter: _Waiter) -> None:
        async with self._condition:
            if self._serving is waiter:
                self._serving = None
            self._decrement_depth(waiter)
            self._select_if_idle()
            self._condition.notify_all()

    def _decrement_depth(self, waiter: _Waiter) -> None:
        self._caller_depth[waiter.caller] -= 1
        if self._caller_depth[waiter.caller] <= 0:
            del self._caller_depth[waiter.caller]
        self._class_depth[waiter.class_name] -= 1
