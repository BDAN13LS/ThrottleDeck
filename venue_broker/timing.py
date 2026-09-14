"""Safe numeric request diagnostics; never retain HTTP trace payloads or headers."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

TIMING_HEADER = "X-Venue-Broker-Timing"


def empty_timing(delivery: str = "upstream") -> dict[str, Any]:
    return {
        "version": 1,
        "delivery": delivery,
        "upstream_attempts": 0,
        "upstream_429s": 0,
        "upstream_http_ms": 0.0,
        "queue_wait_ms": 0.0,
        "retry_sleep_ms": 0.0,
        "coalesced_wait_ms": 0.0,
        "attempts": [],
    }


def elapsed_ms(clock: Callable[[], float], start: float) -> float:
    return round(max(0.0, clock() - start) * 1000, 3)


def edge_cache(headers: Mapping[str, str]) -> str | None:
    value = next(
        (v.upper() for k, v in headers.items() if k.lower() == "cf-cache-status"), ""
    )
    return (
        value
        if value
        in {
            "HIT",
            "MISS",
            "DYNAMIC",
            "BYPASS",
            "EXPIRED",
            "REVALIDATED",
            "STALE",
            "UPDATING",
        }
        else None
    )


def endpoint_family(path: str) -> str:
    # Fixed route families avoid query parameters, account IDs and arbitrary paths.
    if path.startswith("/v1/markets/") and path.rsplit("/", 1)[-1] in {
        "book",
        "bbo",
        "settlement",
    }:
        return "/v1/markets/{slug}/" + path.rsplit("/", 1)[-1]
    if path in {"/v1/events", "/v1/markets", "/exchange/status"}:
        return path
    return "other_get"


class HttpTrace:
    """Measure HTTP phases without inspecting trace info (which carries secrets).

    pre_send_ms includes pool acquisition, scheduling, TCP and TLS. It is an
    upper bound on pool wait, not a measurement of pool wait alone. HTTP header
    receive time includes network and server time; server-only time is unknown.
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self.clock = clock
        self.start = clock()
        self.starts: dict[str, float] = {}
        self.values: dict[str, float | None] = {
            "pre_send_ms": None,
            "connect_tcp_ms": 0.0,
            "tls_ms": 0.0,
            "response_headers_ms": None,
            "response_body_ms": None,
        }

    async def __call__(self, event: str, info: Mapping[str, Any]) -> None:
        phase, state = event.rsplit(".", 1)
        name = phase.rsplit(".", 1)[-1]
        field = {
            "connect_tcp": "connect_tcp_ms",
            "start_tls": "tls_ms",
            "receive_response_headers": "response_headers_ms",
            "receive_response_body": "response_body_ms",
        }.get(name)
        if name == "send_request_headers" and state == "started":
            if self.values["pre_send_ms"] is None:
                self.values["pre_send_ms"] = elapsed_ms(self.clock, self.start)
        if field is None:
            return
        if state == "started":
            self.starts[phase] = self.clock()
        elif state in {"complete", "failed"} and phase in self.starts:
            duration = elapsed_ms(self.clock, self.starts.pop(phase))
            self.values[field] = round((self.values[field] or 0.0) + duration, 3)
