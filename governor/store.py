"""SQLite-backed Governor state, audit, and bounded metric history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from governor.config import (
    MAX_STORAGE_BYTES,
    RAW_RETENTION_HOURS,
    ROLLUP_RETENTION_DAYS,
)
from governor.models import (
    CONTROL_SCHEMA_VERSION,
    BrokerSample,
    ControlAction,
    ControlEvent,
    ControlRecord,
    ControlSnapshot,
    ControlState,
    InvalidControlSchema,
    MaintenanceResult,
    RevisionConflict,
)
from governor.publisher import publish_broker_policy
from governor.registry import CONTROL_SCOPES

_TABLES = {
    "control_state",
    "control_events",
    "metric_samples",
    "metric_rollups",
    "maintenance_state",
    "runtime_events",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise InvalidControlSchema("control timestamps must include a timezone")
    return parsed


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class ControlStore:
    def __init__(
        self,
        path: Path,
        *,
        policy_target: Path | None = None,
        now: Callable[[], datetime] = _utc_now,
        max_storage_bytes: int = MAX_STORAGE_BYTES,
    ) -> None:
        self.path = path.resolve()
        self.policy_target = (
            policy_target.resolve()
            if policy_target is not None
            else self.path.with_name("broker-policy.json")
        )
        self._now = now
        self._max_storage_bytes = max_storage_bytes

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        policy_target: Path | None = None,
        now: Callable[[], datetime] = _utc_now,
        max_storage_bytes: int = MAX_STORAGE_BYTES,
    ) -> ControlStore:
        store = cls(
            path,
            policy_target=policy_target,
            now=now,
            max_storage_bytes=max_storage_bytes,
        )
        store.path.parent.mkdir(parents=True, exist_ok=True)
        with store._connect() as connection:
            names = store._table_names(connection)
            if not names:
                store._initialize(connection)
            else:
                store._add_configured_scopes(connection)
                store._validate(connection)
        publish_broker_policy(store.snapshot(), store.policy_target)
        return store

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        policy_target: Path | None = None,
        now: Callable[[], datetime] = _utc_now,
        max_storage_bytes: int = MAX_STORAGE_BYTES,
    ) -> ControlStore:
        store = cls(
            path,
            policy_target=policy_target,
            now=now,
            max_storage_bytes=max_storage_bytes,
        )
        if not store.path.is_file():
            raise InvalidControlSchema("Governor database is missing")
        try:
            with store._connect() as connection:
                store._validate(connection)
        except sqlite3.DatabaseError as error:
            raise InvalidControlSchema("Governor database is unreadable") from error
        return store

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.executescript(
            """
            CREATE TABLE maintenance_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                schema_version INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                collection_paused INTEGER NOT NULL CHECK (collection_paused IN (0, 1)),
                last_maintenance_at TEXT
            );
            CREATE TABLE control_state (
                scope TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('allowed', 'stopped')),
                revision INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE TABLE control_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('stop', 'resume')),
                state TEXT NOT NULL CHECK (state IN ('allowed', 'stopped')),
                revision INTEGER NOT NULL UNIQUE,
                occurred_at TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            CREATE TABLE metric_samples (
                sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                stale INTEGER NOT NULL CHECK (stale IN (0, 1)),
                restart INTEGER NOT NULL CHECK (restart IN (0, 1)),
                error TEXT
            );
            CREATE INDEX metric_samples_time ON metric_samples(sampled_at);
            CREATE TABLE metric_rollups (
                minute_start TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                first_payload_json TEXT NOT NULL,
                last_payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (minute_start, instance_id)
            );
            CREATE TABLE runtime_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
            """
        )
        now = self._now().isoformat()
        connection.execute(
            "INSERT INTO maintenance_state VALUES (1, ?, 0, 0, NULL)",
            (CONTROL_SCHEMA_VERSION,),
        )
        for scope in sorted(CONTROL_SCOPES):
            connection.execute(
                "INSERT INTO control_state VALUES (?, ?, 0, ?, ?)",
                (scope, "allowed", now, "install default"),
            )
        connection.commit()

    def _add_configured_scopes(self, connection: sqlite3.Connection) -> None:
        """Add newly configured apps without rewriting existing safety state."""
        existing = {
            str(row[0])
            for row in connection.execute("SELECT scope FROM control_state")
        }
        missing = CONTROL_SCOPES - existing
        if not missing:
            return
        row = connection.execute(
            "SELECT revision FROM maintenance_state WHERE id=1"
        ).fetchone()
        if row is None:
            raise InvalidControlSchema("Governor maintenance state is missing")
        revision = int(row[0])
        now = self._now().isoformat()
        for scope in sorted(missing):
            connection.execute(
                "INSERT INTO control_state VALUES (?, ?, ?, ?, ?)",
                (scope, "allowed", revision, now, "app config default"),
            )
        connection.commit()

    def _validate(self, connection: sqlite3.Connection) -> None:
        names = self._table_names(connection)
        if not _TABLES.issubset(names):
            raise InvalidControlSchema("Governor database schema is incomplete")
        row = connection.execute(
            "SELECT schema_version, revision, collection_paused "
            "FROM maintenance_state WHERE id=1"
        ).fetchone()
        if row is None or row["schema_version"] != CONTROL_SCHEMA_VERSION:
            raise InvalidControlSchema(
                "Governor database schema version is unsupported"
            )
        if row["revision"] < 0 or row["collection_paused"] not in (0, 1):
            raise InvalidControlSchema("Governor maintenance state is invalid")
        states = connection.execute(
            "SELECT scope, state, revision, changed_at FROM control_state"
        ).fetchall()
        stored_scopes = {str(item["scope"]) for item in states}
        if not CONTROL_SCOPES.issubset(stored_scopes):
            raise InvalidControlSchema("Governor control scopes are invalid")
        if any(
            scope not in CONTROL_SCOPES and not scope.startswith("broker:app:")
            for scope in stored_scopes
        ):
            raise InvalidControlSchema("Governor control scopes are invalid")
        for item in states:
            if item["state"] not in {"allowed", "stopped"}:
                raise InvalidControlSchema("Governor control state is invalid")
            if not 0 <= item["revision"] <= row["revision"]:
                raise InvalidControlSchema("Governor control revision is invalid")
            _parse_time(str(item["changed_at"]))

    def snapshot(self) -> ControlSnapshot:
        try:
            with self._connect() as connection:
                self._validate(connection)
                revision = int(
                    connection.execute(
                        "SELECT revision FROM maintenance_state WHERE id=1"
                    ).fetchone()[0]
                )
                records = {
                    str(row["scope"]): ControlRecord(
                        scope=str(row["scope"]),
                        state=ControlState(str(row["state"])),
                        revision=int(row["revision"]),
                        changed_at=_parse_time(str(row["changed_at"])),
                        reason=str(row["reason"]),
                    )
                    for row in connection.execute(
                        "SELECT scope, state, revision, changed_at, reason "
                        "FROM control_state"
                    )
                }
        except sqlite3.DatabaseError as error:
            raise InvalidControlSchema("Governor database is unreadable") from error
        return ControlSnapshot(revision=revision, records=records)

    def apply(
        self,
        scope: str,
        action: str,
        expected_revision: int,
        reason: str,
    ) -> ControlRecord:
        if scope not in CONTROL_SCOPES:
            raise ValueError(f"unknown control scope: {scope}")
        try:
            parsed_action = ControlAction(action)
        except ValueError as error:
            raise ValueError(f"unknown control action: {action}") from error
        cleaned_reason = reason.strip()
        if not cleaned_reason or len(cleaned_reason) > 500:
            raise ValueError("reason must contain 1 through 500 characters")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate(connection)
            current_revision = int(
                connection.execute(
                    "SELECT revision FROM maintenance_state WHERE id=1"
                ).fetchone()[0]
            )
            if expected_revision != current_revision:
                connection.rollback()
                raise RevisionConflict(self.snapshot())
            row = connection.execute(
                "SELECT state, revision, changed_at, reason FROM control_state "
                "WHERE scope=?",
                (scope,),
            ).fetchone()
            assert row is not None
            desired = parsed_action.state
            if row["state"] == desired.value:
                connection.rollback()
                return ControlRecord(
                    scope,
                    ControlState(str(row["state"])),
                    int(row["revision"]),
                    _parse_time(str(row["changed_at"])),
                    str(row["reason"]),
                )
            revision = current_revision + 1
            changed_at = self._now()
            connection.execute(
                "UPDATE control_state "
                "SET state=?, revision=?, changed_at=?, reason=? WHERE scope=?",
                (
                    desired.value,
                    revision,
                    changed_at.isoformat(),
                    cleaned_reason,
                    scope,
                ),
            )
            connection.execute(
                "UPDATE maintenance_state SET revision=? WHERE id=1", (revision,)
            )
            connection.execute(
                "INSERT INTO control_events "
                "(scope, action, state, revision, occurred_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scope,
                    parsed_action.value,
                    desired.value,
                    revision,
                    changed_at.isoformat(),
                    cleaned_reason,
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        record = ControlRecord(scope, desired, revision, changed_at, cleaned_reason)
        try:
            publish_broker_policy(self.snapshot(), self.policy_target)
        except Exception:
            # The durable state is already committed.  Leaving an older
            # all-allowed policy in place would make that failure unsafe, so
            # invalidate the published view and force the ThrottleDeck Broker
            # to fail closed.
            self.policy_target.unlink(missing_ok=True)
            raise
        return record

    def events(self, limit: int = 100) -> tuple[ControlEvent, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("event limit must be from 1 through 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id, scope, action, state, revision, occurred_at, reason "
                "FROM control_events ORDER BY event_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            ControlEvent(
                event_id=int(row["event_id"]),
                scope=str(row["scope"]),
                action=ControlAction(str(row["action"])),
                state=ControlState(str(row["state"])),
                revision=int(row["revision"]),
                occurred_at=_parse_time(str(row["occurred_at"])),
                reason=str(row["reason"]),
            )
            for row in rows
        )

    def record_sample(self, sample: BrokerSample) -> None:
        if sample.sampled_at.tzinfo is None:
            raise ValueError("sample time must include a timezone")
        with self._connect() as connection:
            paused = bool(
                connection.execute(
                    "SELECT collection_paused FROM maintenance_state WHERE id=1"
                ).fetchone()[0]
            )
            if paused:
                return
            payload = {
                "metrics": sample.metrics,
                "deltas": sample.deltas,
            }
            connection.execute(
                "INSERT INTO metric_samples "
                "(sampled_at, instance_id, status, payload_json, stale, restart, "
                "error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sample.sampled_at.isoformat(),
                    sample.instance_id or "unknown",
                    sample.status,
                    _json(payload),
                    int(sample.stale),
                    int(sample.restart),
                    sample.error,
                ),
            )
            connection.commit()

    def latest_sample(self) -> BrokerSample | None:
        samples = self.recent_samples(limit=1)
        return samples[-1] if samples else None

    @staticmethod
    def _sample_from_row(row: sqlite3.Row) -> BrokerSample:
        payload = json.loads(str(row["payload_json"]))
        return BrokerSample(
            sampled_at=_parse_time(str(row["sampled_at"])),
            instance_id=(
                None if row["instance_id"] == "unknown" else str(row["instance_id"])
            ),
            status=str(row["status"]),
            metrics=payload.get("metrics", {}),
            stale=bool(row["stale"]),
            restart=bool(row["restart"]),
            error=str(row["error"]) if row["error"] is not None else None,
            deltas=payload.get("deltas"),
        )

    def recent_samples(self, limit: int = 60) -> tuple[BrokerSample, ...]:
        """Return up to ``limit`` raw samples in oldest-to-newest order."""
        if not 1 <= limit <= 1000:
            raise ValueError("sample limit must be from 1 through 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sampled_at, instance_id, status, payload_json, stale, restart, "
                "error FROM metric_samples ORDER BY sample_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._sample_from_row(row) for row in reversed(rows))

    def sample_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
            )

    def rollup_count(self) -> int:
        with self._connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM metric_rollups").fetchone()[0]
            )

    def collection_paused(self) -> bool:
        with self._connect() as connection:
            return bool(
                connection.execute(
                    "SELECT collection_paused FROM maintenance_state WHERE id=1"
                ).fetchone()[0]
            )

    def maintain(self) -> MaintenanceResult:
        now = self._now()
        raw_cutoff = (now - timedelta(hours=RAW_RETENTION_HOURS)).isoformat()
        rollup_cutoff = (now - timedelta(days=ROLLUP_RETENTION_DAYS)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            was_paused = bool(
                connection.execute(
                    "SELECT collection_paused FROM maintenance_state WHERE id=1"
                ).fetchone()[0]
            )
            old_rows = connection.execute(
                "SELECT sample_id, sampled_at, instance_id, status, payload_json "
                "FROM metric_samples WHERE sampled_at < ? ORDER BY sampled_at",
                (raw_cutoff,),
            ).fetchall()
            for row in old_rows:
                sampled = _parse_time(str(row["sampled_at"]))
                minute = sampled.replace(second=0, microsecond=0).isoformat()
                connection.execute(
                    """
                    INSERT INTO metric_rollups
                        (minute_start, instance_id, sample_count,
                         first_payload_json, last_payload_json, status)
                    VALUES (?, ?, 1, ?, ?, ?)
                    ON CONFLICT(minute_start, instance_id) DO UPDATE SET
                        sample_count = metric_rollups.sample_count + 1,
                        last_payload_json = excluded.last_payload_json,
                        status = excluded.status
                    """,
                    (
                        minute,
                        str(row["instance_id"]),
                        str(row["payload_json"]),
                        str(row["payload_json"]),
                        str(row["status"]),
                    ),
                )
            if old_rows:
                connection.executemany(
                    "DELETE FROM metric_samples WHERE sample_id=?",
                    ((int(row["sample_id"]),) for row in old_rows),
                )
            deleted_rollups = connection.execute(
                "DELETE FROM metric_rollups WHERE minute_start < ?", (rollup_cutoff,)
            ).rowcount
            connection.execute(
                "UPDATE maintenance_state SET last_maintenance_at=? WHERE id=1",
                (now.isoformat(),),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if was_paused and old_rows:
                # A normal DELETE leaves SQLite pages allocated. A full reclaim
                # is reserved for guard recovery, never the steady-state path.
                connection.execute("VACUUM")
                # VACUUM itself writes through the WAL in WAL mode. Truncate it
                # again before measuring so recovery is based on durable size,
                # not the temporary compaction journal.
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        total = sum(
            path.stat().st_size
            for path in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
            if path.exists()
        )
        paused = total > self._max_storage_bytes
        with self._connect() as connection:
            connection.execute(
                "UPDATE maintenance_state SET collection_paused=? WHERE id=1",
                (int(paused),),
            )
            connection.commit()
        return MaintenanceResult(
            rolled_up=len(old_rows),
            deleted_rollups=max(0, deleted_rollups),
            total_bytes=total,
            collection_paused=paused,
        )
