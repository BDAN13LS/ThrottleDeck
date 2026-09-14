"""Core request broker with rate limiting, single-flight, and safe caching.

Authentication headers are forwarded in memory and never logged or persisted.
Cache and single-flight keys use a process-local keyed fingerprint of the complete
authentication header set. Caller labels are scheduling policy, not identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import secrets
import time
import uuid
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import DefaultCookiePolicy
from typing import Any
from urllib.parse import quote, unquote

import httpx

from venue_broker.config import (
    Settings,
    cache_ttl,
    is_kalshi_perps_path,
    kalshi_token_cost,
)
from venue_broker.control_policy import ControlPolicyReader
from venue_broker.fairness import FairLimiterClosed, WeightedFairLimiter
from venue_broker.limiter import TokenBucket
from venue_broker.timing import (
    TIMING_HEADER,
    HttpTrace,
    edge_cache,
    elapsed_ms,
    empty_timing,
    endpoint_family,
)

# A venue may ask for a wait longer than a caller's request is worth. For a
# trading caller a slow answer is worse than an error, because it cannot decide
# to give up on something it does not know is stuck.
MAX_RETRY_DELAY_SECONDS = 10.0

# Characters that may stand unencoded in a query string: the sub-delimiters and
# the few generic delimiters a query is allowed to contain, plus % so an
# already-encoded query is not encoded twice.
_QUERY_SAFE = "!$&'()*+,-./:;=?@_~%[]"


def capped_retry_delay(delay: float) -> float:
    """The wait to actually take, whatever the venue or the backoff asked for."""
    return min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)


POLYMARKET_BASE_URL = "https://gateway.polymarket.us"
POLYMARKET_AUTH_BASE_URL = "https://api.polymarket.us"
KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
AUTH_HEADER_NAMES = {
    "authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "x-pm-access-key",
    "x-pm-timestamp",
    "x-pm-signature",
    "kalshi-access-key",
    "kalshi-access-signature",
    "kalshi-access-timestamp",
}
log = logging.getLogger("venue_broker.broker")

METRIC_NAMES = (
    "requests_served",
    "suspended_refusals",
    "cache_hits",
    "coalesced_requests",
    "upstream_requests",
    "upstream_429s",
    # The documented order latency stopgap is excluded from upstream_429s.
    "upstream_latency_stopgaps",
    # Admissions sampled at response receipt, not token balance or venue load.
    # This does not prove bypass traffic, the venue's window, or its limit key.
    "upstream_429s_with_headroom",
    "upstream_timeouts",
    "upstream_connection_failures",
    "broker_failures",
    "caller_disconnects",
)


@dataclass(frozen=True, slots=True)
class BrokerResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]
    timing: Mapping[str, Any] | None = None

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    response: BrokerResponse
    expires_at: float


Fetcher = Callable[[str, Mapping[str, str]], Awaitable[BrokerResponse]]


class _RejectUpstreamCookies(DefaultCookiePolicy):
    """Caller credentials must never become shared HTTP-client state."""

    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False


class HttpxFetcher:
    def __init__(self, timeout: float) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._client.cookies.jar.set_policy(_RejectUpstreamCookies())

    async def __call__(self, url: str, headers: Mapping[str, str]) -> BrokerResponse:
        trace = HttpTrace()
        response = await self._client.get(
            url, headers=headers, extensions={"trace": trace}
        )
        return BrokerResponse(
            response.status_code, response.content, response.headers, trace.values
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class Broker:
    def __init__(
        self,
        settings: Settings,
        *,
        fetcher: Fetcher | None = None,
        clock: Callable[[], float] = time.monotonic,
        retry_sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        policy_reader: ControlPolicyReader | None = None,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._policy_reader = policy_reader
        self._fingerprint_key = secrets.token_bytes(32)
        self._clock = clock
        self._wall_clock = wall_clock
        self._instance_id = str(uuid.uuid4())
        self._started_at = wall_clock()
        self._retry_sleep = retry_sleeper
        self._owned_fetcher = (
            HttpxFetcher(settings.request_timeout_seconds) if fetcher is None else None
        )
        self._fetcher = fetcher or self._owned_fetcher
        pm_config = settings.polymarket_bucket
        kalshi_config = settings.kalshi_bucket
        self._buckets = {
            "pmus": TokenBucket(
                pm_config.refill_rate,
                pm_config.capacity,
                rolling_limit=settings.polymarket_rolling_limit,
            ),
            "kalshi": TokenBucket(kalshi_config.refill_rate, kalshi_config.capacity),
        }
        self._limiters = {
            venue: WeightedFairLimiter(bucket, settings.caller_policy, clock=clock)
            for venue, bucket in self._buckets.items()
        }
        self._bucket_configs = {"pmus": pm_config, "kalshi": kalshi_config}
        perps_config = settings.kalshi_perps_bucket
        self._perps_limiter = (
            WeightedFairLimiter(
                TokenBucket(perps_config.refill_rate, perps_config.capacity),
                settings.caller_policy,
                clock=clock,
            )
            if perps_config is not None
            else None
        )
        self._cache: OrderedDict[tuple[str, str, bytes, str, str], CacheEntry] = (
            OrderedDict()
        )
        self._inflight: dict[
            tuple[str, str, bytes, str, str], asyncio.Task[BrokerResponse]
        ] = {}
        self._coordination_lock = asyncio.Lock()
        self._metrics: dict[str, Counter[str]] = {
            "pmus": Counter(),
            "kalshi": Counter(),
        }
        self._caller_metrics: dict[str, dict[str, Counter[str]]] = {
            "pmus": defaultdict(Counter),
            "kalshi": defaultdict(Counter),
        }
        self._kalshi_prediction_metrics: dict[str, Counter[str]] = defaultdict(Counter)
        self._kalshi_perps_metrics: dict[str, Counter[str]] = defaultdict(Counter)
        self._configured_callers = frozenset(settings.caller_policy.callers)
        self._caller_last_seen: dict[str, dict[str, dict[str, str]]] = {
            "pmus": defaultdict(dict),
            "kalshi": defaultdict(dict),
        }
        self._caller_wait_total: dict[str, Counter[str]] = {
            "pmus": Counter(),
            "kalshi": Counter(),
        }
        self._caller_wait_max: dict[str, Counter[str]] = {
            "pmus": Counter(),
            "kalshi": Counter(),
        }
        self._caller_admissions: dict[str, Counter[str]] = {
            "pmus": Counter(),
            "kalshi": Counter(),
        }
        self._closing = False

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        await asyncio.gather(*(limiter.close() for limiter in self._limiters.values()))
        if self._perps_limiter is not None:
            await self._perps_limiter.close()
        if self._owned_fetcher is not None:
            await self._owned_fetcher.aclose()

    def _metric_caller(self, caller: str) -> str:
        normalized = caller.lower()
        if normalized == "unknown" or normalized in self._configured_callers:
            return normalized
        return "other"

    def _increment(
        self,
        venue: str,
        caller: str,
        metric: str,
        *,
        perps: bool = False,
    ) -> None:
        metric_caller = self._metric_caller(caller)
        self._metrics[venue][metric] += 1
        self._caller_metrics[venue][metric_caller][metric] += 1
        if venue == "kalshi":
            lane = (
                self._kalshi_perps_metrics if perps else self._kalshi_prediction_metrics
            )
            lane[metric_caller][metric] += 1
        field = {
            "requests_served": "last_request_at",
            "upstream_requests": "last_upstream_at",
            "broker_failures": "last_error_at",
        }.get(metric)
        if field:
            self._caller_last_seen[venue][metric_caller][field] = (
                self._wall_clock().isoformat()
            )

    def caller_disconnected(
        self, venue: str, caller: str, *, perps: bool = False
    ) -> None:
        self._increment(venue, caller, "caller_disconnects", perps=perps)

    def _record_queue_wait(self, venue: str, caller: str, wait: float) -> None:
        metric_caller = self._metric_caller(caller)
        self._caller_wait_total[venue][metric_caller] += wait
        self._caller_wait_max[venue][metric_caller] = max(
            self._caller_wait_max[venue][metric_caller], wait
        )
        self._caller_admissions[venue][metric_caller] += 1

    async def get(
        self,
        venue: str,
        path: str,
        query: bytes,
        headers: Mapping[str, str],
        caller: str,
    ) -> BrokerResponse:
        started = self._clock()
        response = await self._get(venue, path, query, headers, caller)
        timing = dict(response.timing or empty_timing())
        timing["total_ms"] = elapsed_ms(self._clock, started)
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() != TIMING_HEADER.lower()
        }
        response_headers[TIMING_HEADER] = json.dumps(timing, separators=(",", ":"))
        if timing["upstream_429s"] or timing["total_ms"] >= 1000:
            log.warning(
                "request_timing venue=%s endpoint=%s caller=%s status=%s timing=%s",
                venue,
                endpoint_family(path),
                caller,
                response.status_code,
                response_headers[TIMING_HEADER],
            )
        return replace(response, headers=response_headers, timing=timing)

    async def _get(
        self,
        venue: str,
        path: str,
        query: bytes,
        headers: Mapping[str, str],
        caller: str,
    ) -> BrokerResponse:
        if venue not in self._buckets:
            raise ValueError(f"unknown venue: {venue}")
        if not path.startswith("/"):
            raise ValueError("upstream path must start with /")

        perps = venue == "kalshi" and is_kalshi_perps_path(path)
        if self._policy_reader is not None:
            decision = self._policy_reader.decision(caller)
            if decision.state == "unavailable":
                return broker_error(
                    503,
                    "control_state_unavailable",
                    "Governor broker-access state is unavailable.",
                )
            if decision.state == "stopped":
                self._increment(venue, caller, "suspended_refusals", perps=perps)
                return broker_error(
                    423,
                    "broker_access_stopped",
                    f"Brokered REST access is stopped for {decision.scope}.",
                )

        self._increment(venue, caller, "requests_served", perps=perps)
        if self._closing:
            self._increment(venue, caller, "broker_failures", perps=perps)
            return broker_error(503, "broker_closing", "The venue broker is closing.")
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        authenticated = any(name in normalized_headers for name in AUTH_HEADER_NAMES)
        cache_scope = (
            self._credential_scope(normalized_headers) if authenticated else "public"
        )
        can_share = not authenticated or caller != "unknown"
        accept = normalized_headers.get("accept", "")
        key = (venue, path, query, cache_scope, accept)
        ttl = cache_ttl(venue, path) if can_share else 0.0

        if ttl > 0:
            entry = self._cache.get(key)
            if entry is not None and entry.expires_at > self._clock():
                self._cache.move_to_end(key)
                self._increment(venue, caller, "cache_hits", perps=perps)
                return replace(entry.response, timing=empty_timing("cache"))
            if entry is not None:
                self._cache.pop(key, None)

        if not can_share:
            return await self._fetch_with_retry(
                venue, path, query, normalized_headers, caller
            )

        async with self._coordination_lock:
            task = self._inflight.get(key)
            coalesced = task is not None
            if task is None:
                task = asyncio.create_task(
                    self._shared_fetch(
                        key,
                        venue,
                        path,
                        query,
                        normalized_headers,
                        caller,
                        ttl,
                    )
                )
                self._inflight[key] = task
            else:
                self._increment(venue, caller, "coalesced_requests", perps=perps)

        if coalesced:
            started = self._clock()
            response = await asyncio.shield(task)
            timing = dict(response.timing or empty_timing())
            timing["delivery"] = "coalesced"
            timing["coalesced_wait_ms"] = elapsed_ms(self._clock, started)
            return replace(response, timing=timing)
        return await asyncio.shield(task)

    def _credential_scope(self, headers: Mapping[str, str]) -> str:
        # Include signatures and timestamps too: a key ID alone is not proof
        # that the request holds the credential. Canonical JSON avoids collisions
        # between header boundaries. A random HMAC key prevents offline guessing
        # from a cache-key dump, even for low-entropy credentials such as cookies.
        material = json.dumps(
            sorted(
                (name, headers[name]) for name in AUTH_HEADER_NAMES if name in headers
            ),
            separators=(",", ":"),
        ).encode()
        return (
            "credential:"
            + hmac.new(self._fingerprint_key, material, hashlib.sha256).hexdigest()
        )

    async def _shared_fetch(
        self,
        key: tuple[str, str, bytes, str, str],
        venue: str,
        path: str,
        query: bytes,
        headers: Mapping[str, str],
        caller: str,
        ttl: float,
    ) -> BrokerResponse:
        try:
            response = await self._fetch_with_retry(venue, path, query, headers, caller)
            if ttl > 0 and 200 <= response.status_code < 300:
                self._store_cache(key, CacheEntry(response, self._clock() + ttl))
            return response
        finally:
            task = asyncio.current_task()
            async with self._coordination_lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)

    def _store_cache(
        self,
        key: tuple[str, str, bytes, str, str],
        entry: CacheEntry,
    ) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > self.settings.max_cache_entries:
            self._cache.popitem(last=False)

    async def _fetch_with_retry(
        self,
        venue: str,
        path: str,
        query: bytes,
        headers: Mapping[str, str],
        caller: str,
    ) -> BrokerResponse:
        timing = empty_timing()
        response = await self._fetch_attempts(
            venue, path, query, headers, caller, timing
        )
        return replace(response, timing=timing)

    async def _fetch_attempts(
        self,
        venue: str,
        path: str,
        query: bytes,
        headers: Mapping[str, str],
        caller: str,
        timing: dict[str, Any],
    ) -> BrokerResponse:
        url = upstream_url(venue, path, query)
        limiter = self._limiters[venue]
        config = self._bucket_configs[venue]
        perps = venue == "kalshi" and is_kalshi_perps_path(path)
        if perps:
            if self._perps_limiter is None:
                self._increment(venue, caller, "broker_failures", perps=perps)
                return broker_error(
                    503,
                    "perps_budget_unconfigured",
                    "Configure VENUE_BROKER_KALSHI_PERPS_READ_RATE from the account's "
                    "perps limits before making perps reads.",
                )
            limiter = self._perps_limiter
            config = self.settings.kalshi_perps_bucket
            assert config is not None
        try:
            cost = kalshi_token_cost(path, query) if venue == "kalshi" else 1.0
        except ValueError:
            self._increment(venue, caller, "broker_failures", perps=perps)
            return broker_error(
                400, "invalid_request_cost", "Invalid read batch parameters."
            )
        if cost > config.capacity:
            self._increment(venue, caller, "broker_failures", perps=perps)
            return broker_error(
                400,
                "request_cost_exceeds_capacity",
                f"Request costs {cost:g} tokens; bucket capacity is "
                f"{config.capacity:g}. Split the read batch.",
            )

        for attempt in range(self.settings.max_retries + 1):
            queue_start = self._clock()
            try:
                wait = await limiter.acquire(caller, cost)
            except FairLimiterClosed:
                self._increment(venue, caller, "broker_failures", perps=perps)
                return broker_error(
                    503, "broker_closing", "The venue broker is closing."
                )
            finally:
                timing["queue_wait_ms"] += elapsed_ms(self._clock, queue_start)
            self._record_queue_wait(venue, caller, wait)
            if self._closing:
                self._increment(venue, caller, "broker_failures", perps=perps)
                return broker_error(
                    503, "broker_closing", "The venue broker is closing."
                )
            self._increment(venue, caller, "upstream_requests", perps=perps)
            detail: dict[str, Any] = {
                "attempt": attempt + 1,
                "started_unix_ms": round(time.time() * 1000),
                "admitted_at_start": limiter.admissions_in_window(),
                "status": None,
                "error": None,
                "edge_cache": None,
                "retry_after_ms": None,
                "retry_sleep_ms": 0.0,
            }
            timing["attempts"].append(detail)
            timing["upstream_attempts"] += 1
            http_start = self._clock()
            try:
                raw_response = await self._fetcher(url, headers)
            except (httpx.TimeoutException, TimeoutError) as error:
                detail["error"] = type(error).__name__
                self._increment(venue, caller, "upstream_timeouts", perps=perps)
                self._increment(venue, caller, "broker_failures", perps=perps)
                return broker_error(
                    504,
                    "upstream_timeout",
                    "The upstream venue did not respond before the timeout.",
                )
            except (httpx.RequestError, OSError) as error:
                detail["error"] = type(error).__name__
                metric = "upstream_connection_failures"
                self._increment(venue, caller, metric, perps=perps)
                self._increment(venue, caller, "broker_failures", perps=perps)
                code = (
                    "broker_closing" if self._closing else "upstream_connection_failed"
                )
                status = 503 if self._closing else 502
                message = (
                    "The venue broker is closing."
                    if self._closing
                    else "The upstream venue connection failed."
                )
                return broker_error(status, code, message)
            finally:
                detail["http_ms"] = elapsed_ms(self._clock, http_start)
                detail["admitted_at_response"] = limiter.admissions_in_window()
                timing["upstream_http_ms"] += detail["http_ms"]
            detail.update(raw_response.timing or {})
            detail["status"] = raw_response.status_code
            detail["edge_cache"] = edge_cache(raw_response.headers)
            retry_after = parse_retry_after(raw_response.headers)
            detail["retry_after_ms"] = (
                round(retry_after * 1000, 3) if retry_after is not None else None
            )
            response = venue_response(raw_response)
            if response.status_code != 429:
                return response

            timing["upstream_429s"] += 1
            if is_polymarket_latency_stopgap(venue, response):
                detail["response_kind"] = "latency_stopgap"
                self._increment(
                    venue, caller, "upstream_latency_stopgaps", perps=perps
                )
                return response
            detail["response_kind"] = (
                "too_many_requests"
                if b"too many requests" in response.body.lower()
                else "other_429"
            )
            self._increment(venue, caller, "upstream_429s", perps=perps)
            # Preserve the historical counter's response-time sampling semantics.
            admitted = limiter.admissions_in_window()
            ceiling = (
                self.settings.polymarket_rolling_limit if venue == "pmus" else None
            )
            headroom = (
                admitted is not None and ceiling is not None and admitted < ceiling
            )
            if headroom:
                self._increment(
                    venue, caller, "upstream_429s_with_headroom", perps=perps
                )
            log.warning(
                "venue %s threw HTTP 429 for %s (caller %s) while this broker "
                "had admitted %s of its %s in the trailing second; "
                "local headroom does not identify the venue's refusal cause",
                venue,
                endpoint_family(path),
                caller,
                "?" if admitted is None else admitted,
                "?" if ceiling is None else ceiling,
            )
            if attempt >= self.settings.max_retries:
                return response

            delay = (
                retry_after
                if retry_after is not None
                else self.settings.retry_base_seconds * (2**attempt)
            )
            sleep_start = self._clock()
            await self._retry_sleep(capped_retry_delay(delay))
            detail["retry_sleep_ms"] = elapsed_ms(self._clock, sleep_start)
            timing["retry_sleep_ms"] += detail["retry_sleep_ms"]

        raise AssertionError("retry loop did not return")

    async def metrics(self) -> dict[str, Any]:
        venues: dict[str, Any] = {}
        for venue, limiter in self._limiters.items():
            config = self._bucket_configs[venue]
            queues = await limiter.snapshot()
            perps_values = None
            if venue == "kalshi" and self._perps_limiter is not None:
                perps_queues = await self._perps_limiter.snapshot()
                perps_config = self.settings.kalshi_perps_bucket
                assert perps_config is not None
                perps_values = {
                    "tokens_available": round(await self._perps_limiter.available(), 3),
                    "capacity": perps_config.capacity,
                    "refill_rate_per_second": perps_config.refill_rate,
                    "unit": perps_config.unit,
                    "queues": {
                        "callers": dict(self._bounded_callers(perps_queues["callers"])),
                        "classes": perps_queues["classes"],
                    },
                }
                # Caller counts describe all Kalshi work, including perps.
                queues = {
                    group: Counter(queues[group]) + Counter(perps_queues[group])
                    for group in ("callers", "classes")
                }
            values: dict[str, Any] = {
                "tokens_available": round(await limiter.available(), 3),
                "capacity": config.capacity,
                "refill_rate_per_second": config.refill_rate,
                "unit": config.unit,
            }
            values.update({name: self._metrics[venue][name] for name in METRIC_NAMES})
            if venue == "pmus":
                values["rolling_second_limit"] = self.settings.polymarket_rolling_limit
            if venue == "kalshi":
                values["perps"] = perps_values or {"status": "unconfigured"}
            bounded_queues = self._bounded_callers(queues["callers"])
            callers = set(self._configured_callers) | {"other", "unknown"}
            caller_values: dict[str, Any] = {}
            for caller in sorted(callers):
                counts = self._caller_metrics[venue][caller]
                admissions = self._caller_admissions[venue][caller]
                total_wait = self._caller_wait_total[venue][caller]
                metrics = {name: counts[name] for name in METRIC_NAMES}
                metrics.update(
                    {
                        "class": self.settings.caller_policy.class_for(caller),
                        "queue_depth": bounded_queues[caller],
                        "queue_admissions": admissions,
                        "queue_wait_seconds_total": round(total_wait, 6),
                        "queue_wait_seconds_max": round(
                            self._caller_wait_max[venue][caller], 6
                        ),
                        "queue_wait_seconds_average": round(
                            total_wait / admissions if admissions else 0.0, 6
                        ),
                    }
                )
                metrics.update(
                    {
                        name: self._caller_last_seen[venue][caller].get(name)
                        for name in (
                            "last_request_at",
                            "last_upstream_at",
                            "last_error_at",
                        )
                    }
                )
                caller_values[caller] = metrics
            values["callers"] = caller_values
            if venue == "kalshi":
                values["prediction_callers"] = {
                    caller: {
                        name: self._kalshi_prediction_metrics[caller][name]
                        for name in METRIC_NAMES
                    }
                    for caller in sorted(callers)
                }
                values["perps_callers"] = {
                    caller: {
                        name: self._kalshi_perps_metrics[caller][name]
                        for name in METRIC_NAMES
                    }
                    for caller in sorted(callers)
                }
            values["classes"] = {
                name: {
                    "weight": weight,
                    "queue_depth": queues["classes"][name],
                }
                for name, weight in self.settings.caller_policy.weights.items()
            }
            venues[venue] = values
        return {
            "schema_version": 1,
            "instance_id": self._instance_id,
            "started_at": self._started_at.isoformat(),
            "sampled_at": self._wall_clock().isoformat(),
            "kalshi_tier": self.settings.kalshi_tier,
            "status": "closing" if self._closing else "ok",
            "venues": venues,
        }

    def _bounded_callers(self, values: Mapping[str, int]) -> Counter[str]:
        bounded: Counter[str] = Counter()
        for caller, value in values.items():
            bounded[self._metric_caller(caller)] += value
        return bounded

    def health(self) -> tuple[int, dict[str, str]]:
        if self._closing:
            return 503, {"status": "closing"}
        return 200, {"status": "ok"}


def upstream_url(venue: str, path: str, query: bytes) -> str:
    base = POLYMARKET_BASE_URL if venue == "pmus" else KALSHI_BASE_URL
    # Retail account, order and portfolio reads use the authenticated API.
    # Public market data stays on the gateway even if a caller supplies auth.
    if venue == "pmus" and unquote(path).split("/")[1:3] in (
        ["v1", "account"],
        ["v1", "orders"],
        ["v1", "portfolio"],
    ):
        base = POLYMARKET_AUTH_BASE_URL
    url = f"{base}{path}"
    if query:
        # The caller's bytes, percent-encoded rather than decoded as ASCII.
        # A query is not required to be ASCII, and decoding one raised before
        # the request was ever attempted — a broker fault for something the
        # venue would have handled.
        url = f"{url}?{quote(query, safe=_QUERY_SAFE)}"
    return url


def is_polymarket_latency_stopgap(venue: str, response: BrokerResponse) -> bool:
    return venue == "pmus" and b"global rate limit exceeded" in response.body.lower()


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    value = next(
        (
            header_value
            for key, header_value in headers.items()
            if key.lower() == "retry-after"
        ),
        None,
    )
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            parsed = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, parsed) if math.isfinite(parsed) else None


def venue_response(response: BrokerResponse) -> BrokerResponse:
    headers = {
        key: value
        for key, value in response.headers.items()
        if not key.lower().startswith("x-venue-broker-")
    }
    headers["X-Venue-Broker-Source"] = "venue"
    return BrokerResponse(response.status_code, response.body, headers)


def broker_error(status_code: int, code: str, message: str) -> BrokerResponse:
    body = json.dumps(
        {"error": {"code": code, "message": message}},
        separators=(",", ":"),
    ).encode()
    return BrokerResponse(
        status_code,
        body,
        {
            "content-type": "application/json",
            "X-Venue-Broker-Source": "broker",
        },
    )
