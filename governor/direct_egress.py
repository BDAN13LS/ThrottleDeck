"""Manual, secret-safe inspection for venue HTTPS connections on Windows."""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

_VENUE_HOSTS = {
    "polymarket-us": ("gateway.polymarket.us", "api.polymarket.us"),
    "kalshi": ("external-api.kalshi.com",),
}


def _resolved_venue_addresses() -> dict[str, set[str]]:
    resolved: dict[str, set[str]] = {}
    for venue, hosts in _VENUE_HOSTS.items():
        addresses: set[str] = set()
        for host in hosts:
            try:
                addresses.update(
                    str(item[4][0]) for item in socket.getaddrinfo(host, 443)
                )
            except OSError:
                continue
        if addresses:
            resolved[venue] = addresses
    return resolved


def classify_connections(
    rows: Sequence[Mapping[str, Any]],
    venue_addresses: Mapping[str, set[str]],
    *,
    sampled_for_ms: int,
) -> dict[str, Any]:
    """Return only process identity and counts; command lines are never accepted."""
    findings: list[dict[str, Any]] = []
    for row in rows:
        if row.get("is_broker") is True:
            continue
        address = str(row.get("remote_address") or "")
        venue = next(
            (
                name
                for name, addresses in venue_addresses.items()
                if address in addresses
            ),
            None,
        )
        if venue is None:
            continue
        try:
            pid = int(row.get("pid"))
            observations = max(1, int(row.get("observations") or 1))
        except (TypeError, ValueError):
            continue
        process_name = str(row.get("process_name") or "unknown")[:120]
        findings.append(
            {
                "venue": venue,
                "process_name": process_name,
                "pid": pid,
                "observations": observations,
            }
        )
    findings.sort(key=lambda item: (item["venue"], item["process_name"], item["pid"]))
    return {
        "schemaVersion": 1,
        "status": "findings" if findings else "clear",
        "sampledForMs": sampled_for_ms,
        "findings": findings,
        "caveat": (
            "No finding does not prove there was no bypass; short or already-closed "
            "connections can be missed."
        ),
    }


def _powershell_rows(sampled_for_ms: int, broker_port: int) -> list[dict[str, Any]]:
    script = rf"""
$ErrorActionPreference = 'Stop'
$until = (Get-Date).AddMilliseconds({sampled_for_ms})
$seen = @{{}}
do {{
  $brokerPids = @((Get-NetTCPConnection `
    -State Listen -LocalPort {broker_port} `
    -ErrorAction SilentlyContinue).OwningProcess)
  $connections = @(Get-NetTCPConnection `
    -State Established -RemotePort 443 -ErrorAction SilentlyContinue)
  foreach ($connection in $connections) {{
    $key = "$($connection.OwningProcess)|$($connection.RemoteAddress)"
    if (-not $seen.ContainsKey($key)) {{
      $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
      $seen[$key] = [PSCustomObject]@{{
        remote_address = [string]$connection.RemoteAddress
        process_name = if ($process) {{
          [string]$process.ProcessName
        }} else {{
          'unknown'
        }}
        pid = [int]$connection.OwningProcess
        observations = 1
        is_broker = $brokerPids -contains $connection.OwningProcess
      }}
    }} else {{
      $seen[$key].observations += 1
    }}
  }}
  Start-Sleep -Milliseconds 250
}} while ((Get-Date) -lt $until)
@($seen.Values) | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=(sampled_for_ms / 1000) + 8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = completed.stdout.strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [item for item in parsed if isinstance(item, dict)]


def run_direct_egress_audit(*, sampled_for_ms: int = 2_000) -> dict[str, Any]:
    """Sample established port-443 connections only after an operator asks."""
    if platform.system() != "Windows":
        return {
            "schemaVersion": 1,
            "status": "unavailable",
            "sampledForMs": 0,
            "findings": [],
            "caveat": "The traffic audit is available on Windows only.",
        }
    addresses = _resolved_venue_addresses()
    if not addresses:
        return {
            "schemaVersion": 1,
            "status": "unavailable",
            "sampledForMs": 0,
            "findings": [],
            "caveat": "Venue addresses could not be resolved for this audit.",
        }
    try:
        broker_port = int(os.getenv("VENUE_BROKER_PORT", "8777"))
        rows = _powershell_rows(sampled_for_ms, broker_port)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return {
            "schemaVersion": 1,
            "status": "unavailable",
            "sampledForMs": 0,
            "findings": [],
            "caveat": "Windows could not complete the connection sample.",
        }
    return classify_connections(rows, addresses, sampled_for_ms=sampled_for_ms)
