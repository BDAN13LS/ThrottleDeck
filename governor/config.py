"""Fixed loopback and per-user runtime configuration for Governor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

GOVERNOR_HOST = "127.0.0.1"
GOVERNOR_PORT = 8778
BROKER_ORIGIN = "http://127.0.0.1:8777"
RAW_RETENTION_HOURS = 24
ROLLUP_RETENTION_DAYS = 90
MAX_STORAGE_BYTES = 250 * 1024 * 1024


def governor_root() -> Path:
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / "ThrottleDeck"
    return Path.home() / "AppData" / "Local" / "ThrottleDeck"


@dataclass(frozen=True, slots=True)
class GovernorPaths:
    root: Path

    @classmethod
    def default(cls) -> GovernorPaths:
        return cls(governor_root())

    @property
    def database(self) -> Path:
        return self.root / "governor.sqlite3"

    @property
    def broker_policy(self) -> Path:
        return self.root / "broker-policy.json"

    @property
    def control_key(self) -> Path:
        return self.root / "control.key"

    @property
    def apps(self) -> Path:
        return self.root / "apps.toml"

    @property
    def execution_order_lock(self) -> Path:
        return self.root / "execution-order.lock"
