"""Local-only ThrottleDeck Broker observation and restart-aware history derivation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from governor.config import BROKER_ORIGIN
from governor.models import BrokerSample
from governor.store import ControlStore

STALE_AFTER = timedelta(seconds=15)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("sampled_at is missing")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("sampled_at must have a timezone")
    return timestamp


def _counter_delta(current: Any, previous: Any) -> Any:
    if isinstance(current, Mapping) and isinstance(previous, Mapping):
        return {
            key: _counter_delta(value, previous.get(key))
            for key, value in current.items()
        }
    if (
        isinstance(current, int | float)
        and not isinstance(current, bool)
        and isinstance(previous, int | float)
        and not isinstance(previous, bool)
    ):
        return current - previous if current >= previous else None
    return None


class GovernorCollector:
    def __init__(
        self,
        store: ControlStore,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._client = client or httpx.AsyncClient(
            base_url=BROKER_ORIGIN,
            timeout=2.0,
        )
        self._owns_client = client is None
        self._now = now
        latest = store.latest_sample()
        self._last_good = latest if latest and latest.metrics else None
        self._consecutive_failures = 0

    @staticmethod
    def _validate_metrics(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("metrics response is not an object")
        if payload.get("schema_version") != 1:
            raise ValueError("metrics schema is unsupported")
        instance = payload.get("instance_id")
        if not isinstance(instance, str) or not instance:
            raise ValueError("metrics instance_id is invalid")
        _parse_timestamp(payload.get("sampled_at"))
        return payload

    async def sample_once(self) -> BrokerSample:
        observed_at = self._now()
        try:
            health = await self._client.get("/health")
            health.raise_for_status()
            if health.json().get("status") != "ok":
                raise ValueError("broker health is not ok")
            response = await self._client.get("/metrics")
            response.raise_for_status()
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            self._consecutive_failures += 1
            within_grace = self._last_good is not None and (
                self._consecutive_failures == 1
            )
            last_good_metrics = self._last_good.metrics if self._last_good else {}
            last_good_source_time = (
                _parse_timestamp(last_good_metrics.get("sampled_at"))
                if self._last_good
                else observed_at
            )
            sample = BrokerSample(
                sampled_at=observed_at,
                instance_id=(self._last_good.instance_id if self._last_good else None),
                status="ok" if within_grace else "unavailable",
                metrics=last_good_metrics,
                stale=(
                    observed_at - last_good_source_time > STALE_AFTER
                    if within_grace
                    else True
                ),
                error="broker_poll_missed" if within_grace else "broker_unavailable",
            )
        else:
            self._consecutive_failures = 0
            try:
                metrics = self._validate_metrics(response.json())
            except (ValueError, TypeError):
                sample = BrokerSample(
                    sampled_at=observed_at,
                    instance_id=(
                        self._last_good.instance_id if self._last_good else None
                    ),
                    status="incompatible",
                    metrics=(self._last_good.metrics if self._last_good else {}),
                    stale=True,
                    error="broker_metrics_incompatible",
                )
            else:
                instance_id = str(metrics["instance_id"])
                source_time = _parse_timestamp(metrics["sampled_at"])
                restart = bool(
                    self._last_good and self._last_good.instance_id != instance_id
                )
                deltas = None
                if self._last_good and not restart:
                    deltas = _counter_delta(metrics, self._last_good.metrics)
                sample = BrokerSample(
                    sampled_at=observed_at,
                    instance_id=instance_id,
                    status="ok",
                    metrics=metrics,
                    stale=observed_at - source_time > STALE_AFTER,
                    restart=restart,
                    deltas=deltas,
                )
                self._last_good = sample
        self._store.record_sample(sample)
        return sample

    async def run(self, stop: asyncio.Event, interval: float = 5.0) -> None:
        cycles = 0
        try:
            while not stop.is_set():
                await self.sample_once()
                cycles += 1
                if cycles % 12 == 0:
                    self._store.maintain()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass
        finally:
            if self._owns_client:
                await self._client.aclose()
