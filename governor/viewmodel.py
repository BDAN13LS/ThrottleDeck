"""Translate durable controls and broker samples into the dashboard contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import pairwise
from typing import Any

from governor.models import BrokerSample, ControlRecord
from governor.registry import APP_REGISTRY, MONEY_CONTROL
from governor.store import ControlStore

_BUDGETS = (
    ("pmus", "pmus", "rest"),
    ("kalshi.predictions", "kalshi", "predictions"),
    ("kalshi.perps", "kalshi", "perps"),
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _record_json(record: ControlRecord) -> dict[str, Any]:
    return {
        "scope": record.scope,
        "state": record.state.value,
        "revision": record.revision,
        "changed_at": record.changed_at.isoformat(),
        "reason": record.reason,
    }


def _venue(sample: BrokerSample | None, venue: str) -> Mapping[str, Any]:
    if sample is None:
        return {}
    return _mapping(_mapping(sample.metrics.get("venues")).get(venue))


def _budget_values(
    sample: BrokerSample | None, budget_id: str
) -> Mapping[str, Any]:
    if budget_id == "pmus":
        return _venue(sample, "pmus")
    kalshi = _venue(sample, "kalshi")
    if budget_id == "kalshi.perps":
        return _mapping(kalshi.get("perps"))
    return kalshi


def _lane_callers(
    sample: BrokerSample | None, budget_id: str
) -> Mapping[str, Any]:
    if budget_id == "pmus":
        return _mapping(_venue(sample, "pmus").get("callers"))
    kalshi = _venue(sample, "kalshi")
    lane = "perps_callers" if budget_id == "kalshi.perps" else "prediction_callers"
    return _mapping(kalshi.get(lane))


def _caller_total(callers: Mapping[str, Any], metric: str) -> int | float | None:
    values = [
        _number(_mapping(record).get(metric)) for record in callers.values()
    ]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _counter_delta(current: int | float | None, earlier: int | float | None):
    if current is None or earlier is None or current < earlier:
        return None
    return current - earlier


def _series(samples: Sequence[BrokerSample], budget_id: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        available = _number(_budget_values(sample, budget_id).get("tokens_available"))
        if available is not None:
            values.append(float(available))
    if len(values) <= 24:
        return values
    step = max(1, len(values) // 24)
    reduced = values[::step]
    return reduced[-24:]


def _queue_depth(values: Mapping[str, Any]) -> int | float | None:
    callers = _mapping(values.get("callers"))
    depths = [
        _number(_mapping(record).get("queue_depth")) for record in callers.values()
    ]
    known = [value for value in depths if value is not None]
    if known:
        return sum(known)
    queues = _mapping(values.get("queues"))
    raw = _mapping(queues.get("callers"))
    fallback = [_number(value) for value in raw.values()]
    present = [value for value in fallback if value is not None]
    return sum(present) if present else None


def _budget_json(
    budget_id: str,
    venue: str,
    product: str,
    latest: BrokerSample | None,
    samples: Sequence[BrokerSample],
) -> dict[str, Any]:
    values = _budget_values(latest, budget_id)
    configured = not (
        budget_id == "kalshi.perps" and values.get("status") == "unconfigured"
    )
    available = _number(values.get("tokens_available")) if configured else None
    capacity = _number(values.get("capacity")) if configured else None
    refill = _number(values.get("refill_rate_per_second")) if configured else None
    queue = _queue_depth(values) if configured else None
    chart = _series(samples, budget_id) if configured else []
    depletion = None
    if capacity and chart:
        depletion = max(0.0, min(1.0, 1.0 - min(chart) / float(capacity)))

    current_throttles = _caller_total(
        _lane_callers(latest, budget_id), "upstream_429s_with_headroom"
    )
    first = samples[0] if len(samples) > 1 else None
    first_throttles = _caller_total(
        _lane_callers(first, budget_id), "upstream_429s_with_headroom"
    )
    throttle_delta = _counter_delta(current_throttles, first_throttles)

    unavailable = latest is None or latest.status != "ok"
    if unavailable:
        health = "unavailable"
    elif not configured:
        health = "unconfigured"
    elif (
        (capacity and available is not None and available / capacity <= 0.1)
        or (queue or 0) >= 10
    ):
        health = "critical"
    elif (
        (capacity and available is not None and available / capacity <= 0.35)
        or (queue or 0) > 0
        or (throttle_delta or 0) > 0
    ):
        health = "pressure"
    else:
        health = "healthy"

    return {
        "id": budget_id,
        "venue": venue,
        "product": product,
        "configured": configured,
        "health": health,
        "unit": values.get("unit") or ("requests" if venue == "pmus" else "tokens"),
        "available_now": available,
        "capacity": capacity,
        "refill_per_second": refill,
        "queue_depth": queue,
        "recent_pressure": depletion,
        "throttled_with_headroom": current_throttles,
        "throttled_with_headroom_delta": throttle_delta,
        "series": chart,
        "sampled_at": latest.sampled_at.isoformat() if latest else None,
    }


def _metric_for_callers(
    sample: BrokerSample | None,
    venue: str,
    callers: Sequence[str],
    metric: str,
) -> int | float | None:
    rows = _mapping(_venue(sample, venue).get("callers"))
    values = [_number(_mapping(rows.get(caller)).get(metric)) for caller in callers]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _latest_for_callers(
    sample: BrokerSample | None, callers: Sequence[str]
) -> str | None:
    timestamps: list[str] = []
    for venue in ("pmus", "kalshi"):
        rows = _mapping(_venue(sample, venue).get("callers"))
        for caller in callers:
            value = _mapping(rows.get(caller)).get("last_request_at")
            if isinstance(value, str) and value:
                timestamps.append(value)
    return max(timestamps) if timestamps else None


def _request_total_for_callers(
    sample: BrokerSample | None, callers: Sequence[str]
) -> int | float | None:
    totals = [
        _metric_for_callers(sample, venue, callers, "requests_served")
        for venue in ("pmus", "kalshi")
    ]
    known = [total for total in totals if total is not None]
    return sum(known) if known else None


def _request_series(
    samples: Sequence[BrokerSample], callers: Sequence[str]
) -> list[int | None]:
    totals = [_request_total_for_callers(sample, callers) for sample in samples]
    deltas: list[int | None] = []
    for earlier, current in pairwise(totals):
        delta = _counter_delta(current, earlier)
        # Missing counters and resets are unknown, not proof of zero traffic.
        deltas.append(max(0, int(delta)) if delta is not None else None)
    return deltas


def _app_json(
    app_id: str,
    latest: BrokerSample | None,
    first: BrokerSample | None,
    samples: Sequence[BrokerSample],
    window_seconds: int | None,
    access: str,
    *,
    callers: Sequence[str] | None = None,
    unassigned: bool = False,
) -> dict[str, Any]:
    definition = APP_REGISTRY.get(app_id)
    selected = tuple(callers or (definition.callers if definition else ()))
    splits: list[dict[str, Any]] = []
    total_requests: int | float | None = 0 if first is not None else None
    total_errors: int | float | None = 0 if first is not None else None
    max_wait: float | None = None
    for venue, display in (("pmus", "polymarket-us"), ("kalshi", "kalshi")):
        current_requests = _metric_for_callers(
            latest, venue, selected, "requests_served"
        )
        earlier_requests = _metric_for_callers(
            first, venue, selected, "requests_served"
        )
        recent = _counter_delta(current_requests, earlier_requests)
        current_429s = _metric_for_callers(latest, venue, selected, "upstream_429s")
        earlier_429s = _metric_for_callers(first, venue, selected, "upstream_429s")
        recent_429s = _counter_delta(current_429s, earlier_429s)
        current_headroom = _metric_for_callers(
            latest, venue, selected, "upstream_429s_with_headroom"
        )
        earlier_headroom = _metric_for_callers(
            first, venue, selected, "upstream_429s_with_headroom"
        )
        recent_headroom = _counter_delta(current_headroom, earlier_headroom)
        venue_wait: float | None = None
        rows = _mapping(_venue(latest, venue).get("callers"))
        for caller in selected:
            wait = _number(_mapping(rows.get(caller)).get("queue_wait_seconds_max"))
            if wait is not None:
                venue_wait = max(venue_wait or 0.0, float(wait) * 1000)
        if recent is not None:
            splits.append(
                {
                    "venue": display,
                    "count": recent,
                    "upstream_429s": recent_429s,
                    "headroom_429s": recent_headroom,
                    "queue_wait_ms": venue_wait,
                }
            )
            assert total_requests is not None
            total_requests += recent
        current_errors = _metric_for_callers(latest, venue, selected, "broker_failures")
        earlier_errors = _metric_for_callers(first, venue, selected, "broker_failures")
        error_delta = _counter_delta(current_errors, earlier_errors)
        if error_delta is not None:
            assert total_errors is not None
            total_errors += error_delta
        if venue_wait is not None:
            max_wait = max(max_wait or 0.0, venue_wait)

    return {
        "app_id": app_id,
        "name": definition.display_name if definition else "Unassigned callers",
        "money_mode": (
            "real-money"
            if definition and definition.money_mode.value == "real"
            else "paper"
        ),
        "access": access,
        "callers": list(selected) if not unassigned else [],
        "coverage_note": (
            definition.coverage_note
            if definition
            else (
                "Callers the registry does not know. Their reads are still "
                "brokered and still count."
            )
        ),
        "recent_requests": total_requests,
        "request_series": _request_series(samples, selected),
        "recent_window_seconds": window_seconds,
        "queue_wait_ms": max_wait,
        "errors": total_errors,
        "venue_split": splits,
        "last_request_at": _latest_for_callers(latest, selected),
        "unassigned": unassigned,
    }


def _activity(
    store: ControlStore,
    latest: BrokerSample | None,
    first: BrokerSample | None,
    observed_at: datetime,
):
    events: list[dict[str, Any]] = []
    for event in store.events(limit=20):
        if event.scope == "trade:new-orders:execution":
            money_name = APP_REGISTRY[MONEY_CONTROL.app_id].display_name
            subject = f"{money_name} new money orders"
        elif event.scope == "broker:global":
            subject = "All brokered REST access"
        else:
            app_id = event.scope.removeprefix("broker:app:")
            subject = f"{APP_REGISTRY[app_id].display_name} brokered REST access"
        verb = "stopped" if event.state.value == "stopped" else "allowed"
        events.append(
            {
                "at": event.occurred_at.isoformat(),
                "kind": "control-change",
                "severity": "warning" if event.state.value == "stopped" else "info",
                "detail": f"{subject} {verb} by the operator.",
                "app_id": (
                    event.scope.removeprefix("broker:app:")
                    if event.scope.startswith("broker:app:")
                    else (
                        MONEY_CONTROL.app_id
                        if event.scope == "trade:new-orders:execution"
                        else None
                    )
                ),
            }
        )
    if latest and latest.restart:
        events.append(
            {
                "at": latest.sampled_at.isoformat(),
                "kind": "broker-restarted",
                "severity": "info",
                "detail": (
                    "ThrottleDeck Broker restarted; recent counters began a new "
                    "window."
                ),
                "app_id": None,
            }
        )
    current = _number(_venue(latest, "pmus").get("upstream_429s_with_headroom"))
    earlier = _number(_venue(first, "pmus").get("upstream_429s_with_headroom"))
    if current and (first is None or (_counter_delta(current, earlier) or 0) > 0):
        events.append(
            {
                "at": latest.sampled_at.isoformat() if latest else "",
                "kind": "possible-direct-caller",
                "severity": "warning",
                "detail": (
                    "Polymarket throttled this machine while ThrottleDeck Broker still "
                    "had room."
                ),
                "app_id": None,
            }
        )
    if latest and latest.error == "broker_metrics_incompatible":
        events.append(
            {
                "at": latest.sampled_at.isoformat(),
                "kind": "broker-update-required",
                "severity": "warning",
                "detail": (
                    "ThrottleDeck Broker is healthy but needs a restart to publish "
                    "current metrics."
                ),
                "app_id": None,
            }
        )
    elif latest is None or latest.status != "ok":
        events.append(
            {
                "at": (
                    latest.sampled_at.isoformat()
                    if latest
                    else observed_at.isoformat()
                ),
                "kind": "broker-unavailable",
                "severity": "critical",
                "detail": "ThrottleDeck Broker is not responding.",
                "app_id": None,
            }
        )
    return sorted(events, key=lambda event: event["at"], reverse=True)[:20]


def build_snapshot(store: ControlStore, observed_at: datetime) -> dict[str, Any]:
    """Build the complete schema-version-1 dashboard payload."""
    controls = store.snapshot()
    history = list(store.recent_samples(limit=60))
    latest = history[-1] if history else None
    if latest is not None:
        history = [
            sample for sample in history if sample.instance_id == latest.instance_id
        ]
    first = history[0] if len(history) > 1 else None
    window_seconds = (
        max(1, int((latest.sampled_at - first.sampled_at).total_seconds()))
        if latest and first
        else None
    )

    broker_metrics = _mapping(latest.metrics) if latest else {}
    paused = store.collection_paused()
    if latest and latest.error == "broker_metrics_incompatible":
        broker_state = "degraded"
    elif latest is None or latest.status != "ok":
        broker_state = "down"
    else:
        broker_state = "degraded" if latest.stale else "healthy"
    broker = {
        "state": broker_state,
        "instance_id": latest.instance_id if latest else None,
        "started_at": broker_metrics.get("started_at"),
        "sampled_at": latest.sampled_at.isoformat() if latest else None,
        "sample_age_ms": (
            max(0, round((observed_at - latest.sampled_at).total_seconds() * 1000))
            if latest
            else None
        ),
        "tier": broker_metrics.get("kalshi_tier"),
        "collection_paused": paused,
        "pause_reason": (
            "History collection paused at the 250 MB storage guard." if paused else None
        ),
    }

    apps = [
        _app_json(
            app_id,
            latest,
            first,
            history,
            window_seconds,
            controls.records[f"broker:app:{app_id}"].state.value,
        )
        for app_id in APP_REGISTRY
    ]
    unassigned_callers = ("other", "unknown")
    unassigned_total = sum(
        _metric_for_callers(latest, venue, unassigned_callers, "requests_served") or 0
        for venue in ("pmus", "kalshi")
    )
    if unassigned_total:
        apps.append(
            _app_json(
                "unassigned",
                latest,
                first,
                history,
                window_seconds,
                controls.records["broker:global"].state.value,
                callers=unassigned_callers,
                unassigned=True,
            )
        )

    return {
        "schemaVersion": controls.schema_version,
        "revision": controls.revision,
        "generatedAt": observed_at.isoformat(),
        "broker": broker,
        "brokerAccess": {
            "global": _record_json(controls.records["broker:global"]),
            "apps": {
                app_id: _record_json(controls.records[f"broker:app:{app_id}"])
                for app_id in APP_REGISTRY
            },
        },
        "tradeLock": {
            **_record_json(controls.records["trade:new-orders:execution"]),
            "app_id": MONEY_CONTROL.app_id,
            "app_name": APP_REGISTRY[MONEY_CONTROL.app_id].display_name,
            "confirmation_phrase": MONEY_CONTROL.confirmation_phrase,
        },
        "budgets": [
            _budget_json(budget_id, venue, product, latest, history)
            for budget_id, venue, product in _BUDGETS
        ],
        "apps": apps,
        "activity": _activity(store, latest, first, observed_at),
    }
