"""Request timing must expose retry sleep without inventing venue service time."""

import asyncio
import json
import logging

import httpx
import pytest

from venue_broker.app import create_app
from venue_broker.broker import Broker, BrokerResponse
from venue_broker.config import Settings


class Clock:
    seconds = 0.0

    def __call__(self):
        return self.seconds

    async def sleep(self, delay):
        self.seconds += delay


def timing(response):
    return json.loads(httpx.Headers(response.headers)["x-venue-broker-timing"])


@pytest.mark.asyncio
async def test_retry_timing_separates_http_queue_and_actual_sleep(caplog):
    clock = Clock()
    calls = []

    async def fetch(url, headers):
        calls.append(url)
        clock.seconds += 0.05
        if len(calls) == 1:
            return BrokerResponse(
                429,
                b'{"message":"Too Many Requests"}',
                {
                    "Retry-After": "8",
                    "CF-Cache-Status": "MISS",
                    "X-Venue-Broker-Timing": '{"upstream_attempts":999}',
                    "Set-Cookie": "private-response-cookie",
                },
            )
        return BrokerResponse(200, b'{"ok":true}', {"CF-Cache-Status": "HIT"})

    broker = Broker(Settings(), fetcher=fetch, clock=clock, retry_sleeper=clock.sleep)

    async def admission(caller, cost):
        clock.seconds += 0.025
        return 0.025

    broker._limiters["pmus"].acquire = admission
    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(broker)),
            base_url="http://broker",
        ) as client:
            response = await client.get(
                "/pmus/v1/markets/sample/bbo",
                headers={
                    "X-Broker-Caller": "collector",
                    "Authorization": "private-request-key",
                },
            )
    data = timing(response)
    assert response.status_code == 200
    assert len(calls) == data["upstream_attempts"] == 2
    assert data["upstream_429s"] == 1
    assert data["upstream_http_ms"] == pytest.approx(100)
    assert data["queue_wait_ms"] == pytest.approx(50)
    assert data["retry_sleep_ms"] == pytest.approx(8000)
    assert data["total_ms"] == pytest.approx(8150)
    assert [a["status"] for a in data["attempts"]] == [429, 200]
    assert [a["edge_cache"] for a in data["attempts"]] == ["MISS", "HIT"]
    assert data["attempts"][0]["retry_after_ms"] == 8000
    assert data["attempts"][0]["response_kind"] == "too_many_requests"
    evidence = json.dumps(data) + caplog.text + json.dumps(await broker.metrics())
    assert "private-request-key" not in evidence
    assert "private-response-cookie" not in evidence
    assert "SOMETHING OUTSIDE" not in caplog.text
    await broker.close()


@pytest.mark.asyncio
async def test_documented_stopgap_never_sleeps_or_retries():
    clock = Clock()
    calls = []

    async def fetch(url, headers):
        calls.append(url)
        clock.seconds += 0.05
        return BrokerResponse(
            429, b'{"message":"Global Rate Limit Exceeded"}', {"Retry-After": "8"}
        )

    broker = Broker(Settings(), fetcher=fetch, clock=clock, retry_sleeper=clock.sleep)
    response = await broker.get("pmus", "/v1/markets/sample/bbo", b"", {}, "probe")
    assert len(calls) == 1
    assert clock.seconds == pytest.approx(0.05)
    data = timing(response)
    assert data["upstream_429s"] == 1  # All HTTP 429s within this operation.
    assert data["retry_sleep_ms"] == 0
    assert data["attempts"][0]["response_kind"] == "latency_stopgap"
    assert (await broker.metrics())["venues"]["pmus"]["upstream_429s"] == 0
    await broker.close()


@pytest.mark.asyncio
async def test_cache_hit_does_not_replay_the_original_work():
    clock = Clock()

    async def fetch(url, headers):
        clock.seconds += 0.05
        return BrokerResponse(200, b"{}", {})

    broker = Broker(Settings(), fetcher=fetch, clock=clock)
    first = await broker.get("pmus", "/v1/markets/sample/bbo", b"", {}, "one")
    second = await broker.get("pmus", "/v1/markets/sample/bbo", b"", {}, "two")
    assert timing(first)["upstream_attempts"] == 1
    data = timing(second)
    assert data["delivery"] == "cache"
    assert data["upstream_attempts"] == 0
    assert data["upstream_http_ms"] == 0
    assert data["attempts"] == []
    await broker.close()


@pytest.mark.asyncio
async def test_http_trace_is_wired_to_real_http_calls():
    from venue_broker.broker import HttpxFetcher

    async def serve(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(0.03)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    fetcher = HttpxFetcher(2)
    try:
        port = server.sockets[0].getsockname()[1]
        response = await fetcher(f"http://127.0.0.1:{port}/bbo", {})
        assert response.status_code == 200
        assert response.timing["response_headers_ms"] >= 20
        assert response.timing["connect_tcp_ms"] >= 0
        assert response.timing["pre_send_ms"] is not None
        assert response.timing["response_body_ms"] is not None
    finally:
        await fetcher.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_headroom_at_response_does_not_reconstruct_admission(caplog):
    clock = Clock()

    async def fetch(url, headers):
        clock.seconds += 5
        return BrokerResponse(429, b"unknown refusal", {"CF-Cache-Status": "secret"})

    broker = Broker(Settings(max_retries=0), fetcher=fetch, clock=clock)
    broker._limiters["pmus"].admissions_in_window = lambda: (
        18 if clock.seconds == 0 else 0
    )
    with caplog.at_level(logging.WARNING):
        response = await broker.get("pmus", "/v1/events", b"", {}, "probe")
    detail = timing(response)["attempts"][0]
    assert detail["admitted_at_start"] == 18
    assert detail["admitted_at_response"] == 0
    assert detail["edge_cache"] is None
    assert detail["response_kind"] == "other_429"
    assert "SOMETHING OUTSIDE" not in caplog.text
    await broker.close()


@pytest.mark.asyncio
async def test_coalesced_wait_is_separate_from_shared_work():
    clock = Clock()
    started, release = asyncio.Event(), asyncio.Event()

    async def fetch(url, headers):
        started.set()
        await release.wait()
        clock.seconds += 0.1
        return BrokerResponse(200, b"{}", {})

    broker = Broker(Settings(), fetcher=fetch, clock=clock)
    leader = asyncio.create_task(broker.get("pmus", "/v1/events", b"", {}, "one"))
    await started.wait()
    clock.seconds += 0.05
    follower = asyncio.create_task(broker.get("pmus", "/v1/events", b"", {}, "two"))
    await asyncio.sleep(0)
    release.set()
    a, b = await asyncio.gather(leader, follower)
    assert timing(a)["delivery"] == "upstream"
    data = timing(b)
    assert data["delivery"] == "coalesced"
    assert data["coalesced_wait_ms"] == pytest.approx(100)
    assert data["total_ms"] == pytest.approx(100)
    assert data["upstream_http_ms"] == pytest.approx(150)
    assert data["upstream_attempts"] == 1
    await broker.close()


@pytest.mark.asyncio
async def test_timeout_keeps_attempt_duration_and_error_kind():
    clock = Clock()

    async def fetch(url, headers):
        clock.seconds += 3
        raise httpx.ReadTimeout("must-not-log-private-material")

    broker = Broker(Settings(), fetcher=fetch, clock=clock)
    response = await broker.get("pmus", "/v1/events", b"", {}, "probe")
    assert response.status_code == 504
    data = timing(response)
    assert data["upstream_attempts"] == 1
    assert data["upstream_http_ms"] == pytest.approx(3000)
    assert data["attempts"][0]["error"] == "ReadTimeout"
    assert "must-not-log" not in json.dumps(data)
    await broker.close()
