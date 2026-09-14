"""Atomic publication of the minimal ThrottleDeck Broker access policy."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from governor.models import ControlSnapshot
from governor.registry import APP_REGISTRY


def broker_policy_payload(snapshot: ControlSnapshot) -> dict[str, object]:
    return {
        "schemaVersion": snapshot.schema_version,
        "revision": snapshot.revision,
        "global": snapshot.state("broker:global").value,
        "apps": {
            app_id: snapshot.state(f"broker:app:{app_id}").value
            for app_id in sorted(APP_REGISTRY)
        },
    }


def publish_broker_policy(snapshot: ControlSnapshot, target: Path) -> None:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            broker_policy_payload(snapshot), sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
