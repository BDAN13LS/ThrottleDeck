"""Render an adoption snapshot from the broker's enforced live registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

BEGIN_MARKER = "<!-- BEGIN GENERATED ADOPTION STATUS -->"
END_MARKER = "<!-- END GENERATED ADOPTION STATUS -->"


def _served(metrics: dict[str, Any], venue: str, caller: str) -> int:
    try:
        return int(metrics["venues"][venue]["callers"][caller]["requests_served"])
    except (KeyError, TypeError, ValueError):
        return 0


def _status(count: int, *, warning: bool = False) -> str:
    if count > 0:
        return f"{'⚠️' if warning else '✅'} observed ({count:,})"
    return "◻ not observed"


def render_status_table(metrics: dict[str, Any], registry: dict[str, Any]) -> str:
    projects: dict[str, list[str]] = {}
    for record in registry.get("callers", []):
        if not isinstance(record, dict):
            continue
        caller = record.get("name")
        project = record.get("project")
        if isinstance(caller, str) and isinstance(project, str):
            projects.setdefault(project, []).append(caller)

    lines = [
        "| Project | Polymarket US | Kalshi | Evidence since broker start |",
        "|---|---:|---:|---|",
        (
            "| **ThrottleDeck Broker** | owns the budget | owns the budget | "
            f"instance `{metrics.get('instance_id', 'unknown')}` |"
        ),
    ]
    for project in sorted(projects):
        callers = sorted(projects[project])
        pmus_counts = {caller: _served(metrics, "pmus", caller) for caller in callers}
        kalshi_counts = {
            caller: _served(metrics, "kalshi", caller) for caller in callers
        }
        pmus_total = sum(pmus_counts.values())
        kalshi_total = sum(kalshi_counts.values())
        evidence = [
            f"{venue} `{caller}`: {count:,}"
            for venue, counts in (("pmus", pmus_counts), ("kalshi", kalshi_counts))
            for caller, count in counts.items()
            if count
        ]
        evidence_text = "; ".join(evidence) or "configured; no served traffic yet"
        lines.append(
            f"| **{project}** | {_status(pmus_total)} | {_status(kalshi_total)} | "
            f"{evidence_text} |"
        )

    other_pmus = _served(metrics, "pmus", "other")
    other_kalshi = _served(metrics, "kalshi", "other")
    if other_pmus or other_kalshi:
        lines.append(
            "| **Unconfigured caller names** | "
            f"{_status(other_pmus, warning=True)} | "
            f"{_status(other_kalshi, warning=True)} | "
            "aggregated as `other`; add each caller to `caller-policy.toml` |"
        )
    return "\n".join(lines)


def replace_status_table(document: str, table: str) -> str:
    if document.count(BEGIN_MARKER) != 1 or document.count(END_MARKER) != 1:
        raise ValueError("adoption document must contain one generated status block")
    before, remainder = document.split(BEGIN_MARKER, 1)
    _old, after = remainder.split(END_MARKER, 1)
    return f"{before}{BEGIN_MARKER}\n{table.rstrip()}\n{END_MARKER}{after}"


def _read_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh only ADOPTION-STATUS.md's generated status table."
    )
    parser.add_argument("--broker", default="http://127.0.0.1:8777")
    parser.add_argument(
        "--document",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "docs" / "ADOPTION-STATUS.md",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    metrics = _read_json(f"{args.broker.rstrip('/')}/metrics")
    callers = _read_json(f"{args.broker.rstrip('/')}/callers")
    current = args.document.read_text(encoding="utf-8")
    updated = replace_status_table(current, render_status_table(metrics, callers))
    if args.check:
        return 0 if current == updated else 1
    args.document.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
