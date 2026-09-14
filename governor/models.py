"""Immutable Governor state and history contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

CONTROL_SCHEMA_VERSION = 1


class ControlState(StrEnum):
    ALLOWED = "allowed"
    STOPPED = "stopped"


class ControlAction(StrEnum):
    STOP = "stop"
    RESUME = "resume"

    @property
    def state(self) -> ControlState:
        return (
            ControlState.STOPPED if self is ControlAction.STOP else ControlState.ALLOWED
        )


class MoneyMode(StrEnum):
    PAPER = "paper"
    REAL = "real"


@dataclass(frozen=True, slots=True)
class AppDefinition:
    app_id: str
    display_name: str
    callers: tuple[str, ...]
    money_mode: MoneyMode
    coverage_note: str


@dataclass(frozen=True, slots=True)
class ControlRecord:
    scope: str
    state: ControlState
    revision: int
    changed_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class ControlEvent:
    event_id: int
    scope: str
    action: ControlAction
    state: ControlState
    revision: int
    occurred_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    revision: int
    records: Mapping[str, ControlRecord]
    schema_version: int = CONTROL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))

    def state(self, scope: str) -> ControlState:
        if scope not in self.records:
            raise ValueError(f"unknown control scope: {scope}")
        return self.records[scope].state


@dataclass(frozen=True, slots=True)
class BrokerSample:
    sampled_at: datetime
    instance_id: str | None
    status: str
    metrics: Mapping[str, Any]
    stale: bool = False
    restart: bool = False
    error: str | None = None
    deltas: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    rolled_up: int
    deleted_rollups: int
    total_bytes: int
    collection_paused: bool


class InvalidControlSchema(RuntimeError):
    """Durable control state cannot be trusted."""


class RevisionConflict(RuntimeError):
    def __init__(self, current: ControlSnapshot) -> None:
        super().__init__(f"expected current revision {current.revision}")
        self.current = current
