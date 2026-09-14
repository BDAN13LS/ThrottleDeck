from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from governor.collector import GovernorCollector
from governor.store import ControlStore

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def metrics(instance: str, requests: int, *, sampled_at: datetime = NOW) -> dict:
    return {
        "schema_version": 1,
        "instance_id": instance,
        "sampled_at": sampled_at.isoformat(),
        "requests": {"polymarket": requests, "kalshi": 2},
        "callers": {"execution": {"requests": requests}},
        "kalshi_prediction_callers": {"collector": {"requests": 2}},
        "kalshi_perps_callers": {"other": {"requests": 1}},
    }


@pytest.fixture
def store(tmp_path):
    return ControlStore.create(tmp_path / "governor.sqlite3", now=lambda: NOW)


@pytest.mark.asyncio
async def test_same_instance_deltas_and_only_local_observation_paths(store) -> None:
    paths: list[str] = []
    payloads = [metrics("one", 3), metrics("one", 7)]

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=payloads.pop(0))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8777"
    ) as client:
        collector = GovernorCollector(store, client=client, now=lambda: NOW)
        await collector.sample_once()
        sample = await collector.sample_once()

    assert sample.deltas["requests"]["polymarket"] == 4
    assert paths == ["/health", "/metrics", "/health", "/metrics"]


@pytest.mark.asyncio
async def test_restart_and_decreasing_counters_do_not_make_false_deltas(store) -> None:
    payloads = [metrics("one", 10), metrics("two", 2), metrics("two", 1)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=payloads.pop(0))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8777"
    ) as client:
        collector = GovernorCollector(store, client=client, now=lambda: NOW)
        await collector.sample_once()
        restarted = await collector.sample_once()
        decreased = await collector.sample_once()

    assert restarted.restart is True
    assert restarted.deltas is None
    assert decreased.restart is False
    assert decreased.deltas["requests"]["polymarket"] is None


@pytest.mark.asyncio
async def test_stale_source_and_unconfigured_perps_are_preserved(store) -> None:
    payload = metrics("one", 3, sampled_at=NOW - timedelta(seconds=16))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8777"
    ) as client:
        sample = await GovernorCollector(
            store, client=client, now=lambda: NOW
        ).sample_once()

    assert sample.stale is True
    assert sample.metrics["kalshi_perps_callers"]["other"]["requests"] == 1


@pytest.mark.asyncio
async def test_one_missed_poll_keeps_fresh_sample_before_marking_broker_down(
    store,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, json=metrics("one", 3))
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8777"
    ) as client:
        collector = GovernorCollector(store, client=client, now=lambda: NOW)
        good = await collector.sample_once()
        missed_once = await collector.sample_once()
        missed_twice = await collector.sample_once()

    assert missed_once.metrics == good.metrics
    assert missed_once.stale is False
    assert missed_once.status == "ok"
    assert missed_once.error == "broker_poll_missed"
    assert missed_twice.metrics == good.metrics
    assert missed_twice.stale is True
    assert missed_twice.status == "unavailable"
    assert missed_twice.error == "broker_unavailable"


@pytest.mark.asyncio
async def test_old_broker_metrics_are_distinguished_from_an_outage(store) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "ok", "venues": {}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8777"
    ) as client:
        sample = await GovernorCollector(
            store, client=client, now=lambda: NOW
        ).sample_once()

    assert sample.status == "incompatible"
    assert sample.error == "broker_metrics_incompatible"
    assert sample.stale is True
