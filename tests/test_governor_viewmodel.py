from __future__ import annotations

from datetime import UTC, datetime, timedelta

from governor.models import BrokerSample
from governor.store import ControlStore
from governor.viewmodel import build_snapshot

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def metrics(*, execution: int, perps: int, other: int, headroom: int) -> dict:
    def caller(requests: int) -> dict:
        return {
            "requests_served": requests,
            "broker_failures": 0,
            "upstream_429s": 0,
            "upstream_429s_with_headroom": 0,
            "queue_depth": 0,
            "queue_wait_seconds_max": 0.002,
            "last_request_at": NOW.isoformat(),
        }

    return {
        "schema_version": 1,
        "instance_id": "broker-one",
        "started_at": (NOW - timedelta(hours=2)).isoformat(),
        "sampled_at": NOW.isoformat(),
        "kalshi_tier": "basic",
        "status": "ok",
        "venues": {
            "pmus": {
                "tokens_available": 16,
                "capacity": 18,
                "refill_rate_per_second": 3,
                "unit": "requests",
                "upstream_429s_with_headroom": headroom,
                "callers": {
                    "execution": caller(execution),
                    "other": caller(other),
                    "unknown": caller(0),
                },
            },
            "kalshi": {
                "tokens_available": 500,
                "capacity": 540,
                "refill_rate_per_second": 270,
                "unit": "tokens",
                "callers": {
                    "research": caller(perps),
                    "other": caller(0),
                    "unknown": caller(0),
                },
                "prediction_callers": {"research": caller(0)},
                "perps_callers": {"research": caller(perps)},
                "perps": {
                    "tokens_available": 340,
                    "capacity": 360,
                    "refill_rate_per_second": 180,
                    "unit": "tokens",
                    "queues": {"callers": {"research": 0}},
                },
            },
        },
    }


def test_dashboard_snapshot_derives_real_windows_and_budget_lanes(tmp_path) -> None:
    store = ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )
    store.record_sample(
        BrokerSample(
            NOW - timedelta(seconds=60),
            "broker-one",
            "ok",
            metrics(execution=5, perps=2, other=0, headroom=0),
        )
    )
    store.record_sample(
        BrokerSample(
            NOW,
            "broker-one",
            "ok",
            metrics(execution=15, perps=9, other=2, headroom=1),
        )
    )

    snapshot = build_snapshot(store, NOW)

    assert snapshot["broker"]["state"] == "healthy"
    assert snapshot["broker"]["sample_age_ms"] == 0
    assert [budget["available_now"] for budget in snapshot["budgets"]] == [
        16,
        500,
        340,
    ]
    assert snapshot["budgets"][2]["configured"] is True
    rows = {row["app_id"]: row for row in snapshot["apps"]}
    assert rows["execution"]["recent_requests"] == 10
    assert rows["execution"]["venue_split"] == [
        {
            "venue": "polymarket-us",
            "count": 10,
            "upstream_429s": 0,
            "headroom_429s": 0,
            "queue_wait_ms": 2.0,
        },
    ]
    assert rows["research"]["recent_requests"] == 7
    assert rows["execution"]["recent_window_seconds"] == 60
    assert rows["execution"]["money_mode"] == "real-money"
    assert rows["unassigned"]["recent_requests"] == 2
    assert snapshot["tradeLock"]["app_name"] == "Execution Bot"
    assert snapshot["tradeLock"]["confirmation_phrase"] == "UNLOCK NEW ORDERS"
    assert any(
        event["kind"] == "possible-direct-caller"
        for event in snapshot["activity"]
    )


def test_app_request_series_uses_adjacent_same_instance_samples(tmp_path) -> None:
    store = ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )

    def sample(
        *, execution_pmus: int, execution_kalshi: int, other: int, unknown: int
    ) -> dict:
        values = metrics(
            execution=execution_pmus,
            perps=0,
            other=other,
            headroom=0,
        )
        kalshi_callers = values["venues"]["kalshi"]["callers"]
        kalshi_callers["execution"] = {
            **kalshi_callers["research"],
            "requests_served": execution_kalshi,
        }
        kalshi_callers["unknown"] = {
            **kalshi_callers["unknown"],
            "requests_served": unknown,
        }
        return values

    store.record_sample(
        BrokerSample(
            NOW - timedelta(seconds=10),
            "broker-one",
            "ok",
            sample(execution_pmus=5, execution_kalshi=7, other=2, unknown=3),
        )
    )
    store.record_sample(
        BrokerSample(
            NOW - timedelta(seconds=5),
            "broker-one",
            "ok",
            sample(execution_pmus=8, execution_kalshi=11, other=5, unknown=4),
        )
    )
    store.record_sample(
        BrokerSample(
            NOW,
            "broker-one",
            "ok",
            sample(execution_pmus=1, execution_kalshi=1, other=6, unknown=1),
        )
    )

    rows = {row["app_id"]: row for row in build_snapshot(store, NOW)["apps"]}

    assert rows["execution"]["request_series"] == [7, None]
    assert rows["unassigned"]["request_series"] == [4, None]


def test_broker_restart_does_not_create_negative_recent_traffic(tmp_path) -> None:
    store = ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )
    store.record_sample(
        BrokerSample(
            NOW - timedelta(seconds=5),
            "old",
            "ok",
            metrics(execution=100, perps=100, other=0, headroom=0),
        )
    )
    store.record_sample(
        BrokerSample(
            NOW,
            "new",
            "ok",
            metrics(execution=1, perps=1, other=0, headroom=0),
            restart=True,
        )
    )

    snapshot = build_snapshot(store, NOW)

    rows = {row["app_id"]: row for row in snapshot["apps"]}
    assert rows["execution"]["recent_requests"] is None
    assert rows["execution"]["request_series"] == []
    assert any(event["kind"] == "broker-restarted" for event in snapshot["activity"])


def test_old_broker_version_is_degraded_not_reported_offline(tmp_path) -> None:
    store = ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )
    store.record_sample(
        BrokerSample(
            NOW,
            None,
            "incompatible",
            {},
            stale=True,
            error="broker_metrics_incompatible",
        )
    )

    snapshot = build_snapshot(store, NOW)

    assert snapshot["broker"]["state"] == "degraded"
    assert any(
        event["kind"] == "broker-update-required"
        for event in snapshot["activity"]
    )
