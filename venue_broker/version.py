"""Resolve immutable build identity once, before the broker starts."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

COMMIT_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


def resolve_commit_sha() -> str:
    explicit = os.getenv("VENUE_BROKER_COMMIT_SHA")
    if explicit is not None:
        return explicit.lower() if COMMIT_SHA.fullmatch(explicit) else "unknown"

    repository = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    candidate = result.stdout.strip()
    return candidate.lower() if COMMIT_SHA.fullmatch(candidate) else "unknown"
