from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from governor.models import BrokerSample, InvalidControlSchema, RevisionConflict
from governor.publisher import publish_broker_policy
from governor.registry import APP_REGISTRY, app_for_caller, load_app_config
from governor.store import ControlStore

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )


def test_registry_uses_explicit_caller_groups_without_prefix_guessing() -> None:
    assert APP_REGISTRY["collector"].callers == (
        "collector",
        "collector-scanner",
        "collector-us-trades",
    )
    assert APP_REGISTRY["execution"].money_mode == "real"
    assert APP_REGISTRY["research"].callers == ("research",)
    assert app_for_caller("execution-cockpit") == "execution"
    assert app_for_caller("execution-made-up") == "unassigned"


def test_per_user_registry_loads_names_callers_and_money_control(tmp_path) -> None:
    path = tmp_path / "apps.toml"
    path.write_text(
        """
[[apps]]
id = "alpha"
name = "My Collector"
callers = ["alpha-one", "alpha-two"]
money_mode = "real"
coverage_note = "REST only."

[money_control]
app_id = "alpha"
confirmation_phrase = "  UNLOCK ALPHA  "
""".strip(),
        encoding="utf-8",
    )

    loaded = load_app_config(path)

    assert loaded.apps["alpha"].display_name == "My Collector"
    assert loaded.apps["alpha"].callers == ("alpha-one", "alpha-two")
    assert loaded.money_control.confirmation_phrase == "UNLOCK ALPHA"


def test_per_user_registry_rejects_a_caller_assigned_twice(tmp_path) -> None:
    path = tmp_path / "apps.toml"
    path.write_text(
        """
[[apps]]
id = "one"
name = "One"
callers = ["shared"]
money_mode = "real"

[[apps]]
id = "two"
name = "Two"
callers = ["shared"]
money_mode = "paper"

[money_control]
app_id = "one"
confirmation_phrase = "UNLOCK ONE"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="assigned more than once"):
        load_app_config(path)


def test_install_state_allows_broker_reads_and_execution_trading(store) -> None:
    snap = store.snapshot()
    assert snap.state("broker:global") == "allowed"
    assert snap.state("broker:app:execution") == "allowed"
    assert snap.state("trade:new-orders:execution") == "allowed"
    assert snap.revision == 0


def test_state_change_and_audit_event_commit_together(store) -> None:
    before = store.snapshot()
    changed = store.apply("broker:app:execution", "stop", before.revision, "operator")
    assert changed.state == "stopped"
    assert changed.revision == 1
    event = store.events(limit=1)[0]
    assert event.revision == changed.revision
    assert event.scope == changed.scope
    assert event.reason == "operator"


def test_stale_revision_is_rejected_and_idempotent_stop_does_not_churn(store) -> None:
    changed = store.apply("broker:global", "stop", 0, "operator")
    with pytest.raises(RevisionConflict) as caught:
        store.apply("broker:app:execution", "stop", 0, "stale window")
    assert caught.value.current.revision == changed.revision

    again = store.apply("broker:global", "stop", changed.revision, "operator")
    assert again == changed
    assert len(store.events()) == 1


def test_unknown_scope_is_rejected(store) -> None:
    with pytest.raises(ValueError, match="scope"):
        store.apply("broker:app:made-up", "stop", 0, "operator")


def test_state_and_policy_survive_reopen(tmp_path) -> None:
    database = tmp_path / "governor.sqlite3"
    policy = tmp_path / "broker-policy.json"
    store = ControlStore.create(database, policy_target=policy, now=lambda: NOW)
    store.apply("broker:app:collector", "stop", 0, "operator")

    reopened = ControlStore.open(database, policy_target=policy, now=lambda: NOW)
    assert reopened.snapshot().state("broker:app:collector") == "stopped"
    published = json.loads(policy.read_text(encoding="utf-8"))
    assert published == {
        "schemaVersion": 1,
        "revision": 1,
        "global": "allowed",
        "apps": {
            "collector": "stopped",
            "research": "allowed",
            "execution": "allowed",
        },
    }


def test_create_adds_a_new_configured_app_without_resetting_existing_state(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "governor.sqlite3"
    policy = tmp_path / "broker-policy.json"
    original = ControlStore.create(database, policy_target=policy, now=lambda: NOW)
    original.apply("broker:app:collector", "stop", 0, "operator")
    expanded = frozenset(
        {
            "broker:global",
            "broker:app:collector",
            "broker:app:execution",
            "broker:app:research",
            "broker:app:new-client",
            "trade:new-orders:execution",
        }
    )
    monkeypatch.setattr("governor.store.CONTROL_SCOPES", expanded)

    reopened = ControlStore.create(database, policy_target=policy, now=lambda: NOW)

    assert reopened.snapshot().state("broker:app:collector") == "stopped"
    assert reopened.snapshot().state("broker:app:new-client") == "allowed"


def test_publisher_contains_only_broker_policy_fields(store, tmp_path) -> None:
    target = tmp_path / "published.json"
    publish_broker_policy(store.snapshot(), target)
    text = target.read_text(encoding="utf-8")
    assert set(json.loads(text)) == {"schemaVersion", "revision", "global", "apps"}
    assert "operator" not in text
    assert "trade:new-orders" not in text


def test_corrupt_schema_is_rejected(tmp_path) -> None:
    database = tmp_path / "bad.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE maintenance_state "
            "(id INTEGER PRIMARY KEY, schema_version INTEGER, revision INTEGER, "
            "collection_paused INTEGER)"
        )
        connection.execute("INSERT INTO maintenance_state VALUES (1, 99, 0, 0)")
    with pytest.raises(InvalidControlSchema):
        ControlStore.open(database)


def test_concurrent_stores_enforce_one_revision_winner(tmp_path) -> None:
    database = tmp_path / "governor.sqlite3"
    one = ControlStore.create(database, now=lambda: NOW)
    two = ControlStore.open(database, now=lambda: NOW)
    one.apply("broker:app:collector", "stop", 0, "one")
    with pytest.raises(RevisionConflict):
        two.apply("broker:app:execution", "stop", 0, "two")


def test_maintenance_rolls_raw_samples_and_expires_old_rollups(tmp_path) -> None:
    database = tmp_path / "governor.sqlite3"
    store = ControlStore.create(database, now=lambda: NOW)
    old = BrokerSample(
        sampled_at=NOW - timedelta(hours=25),
        instance_id="instance-one",
        status="ok",
        metrics={"requests": 1},
    )
    current = BrokerSample(
        sampled_at=NOW,
        instance_id="instance-one",
        status="ok",
        metrics={"requests": 2},
    )
    store.record_sample(old)
    store.record_sample(current)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO metric_rollups "
            "(minute_start, instance_id, sample_count, first_payload_json, "
            "last_payload_json, status) VALUES (?, ?, 1, '{}', '{}', 'ok')",
            ((NOW - timedelta(days=91)).isoformat(), "ancient"),
        )
    result = store.maintain()
    assert result.rolled_up == 1
    assert result.deleted_rollups == 1
    assert store.sample_count() == 1
    assert store.rollup_count() == 1


def test_storage_guard_pauses_samples_but_not_control_writes(tmp_path) -> None:
    database = tmp_path / "governor.sqlite3"
    store = ControlStore.create(
        database,
        now=lambda: NOW,
        max_storage_bytes=1,
    )
    result = store.maintain()
    assert result.collection_paused is True
    store.record_sample(BrokerSample(NOW, "instance", "ok", {"requests": 1}))
    assert store.sample_count() == 0
    assert store.apply("broker:global", "stop", 0, "operator").state == "stopped"


def test_publish_failure_invalidates_old_allowed_policy(tmp_path, monkeypatch) -> None:
    database = tmp_path / "governor.sqlite3"
    policy = tmp_path / "broker-policy.json"
    store = ControlStore.create(database, policy_target=policy, now=lambda: NOW)

    def fail_publish(*_args, **_kwargs):
        raise OSError("disk write failed")

    monkeypatch.setattr("governor.store.publish_broker_policy", fail_publish)
    with pytest.raises(OSError, match="disk write failed"):
        store.apply("broker:global", "stop", 0, "operator")

    assert not policy.exists()
    assert store.snapshot().state("broker:global") == "stopped"
