"""Fail-closed reader for Governor's atomically published broker policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from governor.registry import APP_REGISTRY, app_for_caller

POLICY_SCHEMA_VERSION = 1
_STATES = {"allowed", "stopped"}
_TOP_LEVEL_KEYS = {"schemaVersion", "revision", "global", "apps"}


@dataclass(frozen=True, slots=True)
class AccessDecision:
    state: Literal["allowed", "stopped", "unavailable"]
    scope: str
    app_id: str
    revision: int | None

    @property
    def allowed(self) -> bool:
        return self.state == "allowed"


class ControlPolicyReader:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._metadata: tuple[int, int, int, int] | None = None
        self._policy: dict[str, Any] | None = None

    @staticmethod
    def _validate(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
            raise ValueError("broker control policy keys are invalid")
        if payload["schemaVersion"] != POLICY_SCHEMA_VERSION:
            raise ValueError("broker control policy version is unsupported")
        revision = payload["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("broker control policy revision is invalid")
        if payload["global"] not in _STATES:
            raise ValueError("broker global control state is invalid")
        apps = payload["apps"]
        if not isinstance(apps, dict) or set(apps) != set(APP_REGISTRY):
            raise ValueError("broker application controls are invalid")
        if any(state not in _STATES for state in apps.values()):
            raise ValueError("broker application control state is invalid")
        return payload

    def _read(self) -> dict[str, Any]:
        stat = self.path.stat()
        metadata = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
        if metadata == self._metadata and self._policy is not None:
            return self._policy
        payload = self._validate(json.loads(self.path.read_text(encoding="utf-8")))
        self._metadata = metadata
        self._policy = payload
        return payload

    def validate(self) -> None:
        self._read()

    def decision(self, caller: str) -> AccessDecision:
        app_id = app_for_caller(caller)
        try:
            policy = self._read()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return AccessDecision("unavailable", "broker:global", app_id, None)
        revision = int(policy["revision"])
        if policy["global"] == "stopped":
            return AccessDecision("stopped", "broker:global", app_id, revision)
        if app_id != "unassigned" and policy["apps"][app_id] == "stopped":
            return AccessDecision("stopped", f"broker:app:{app_id}", app_id, revision)
        return AccessDecision("allowed", "broker:global", app_id, revision)
