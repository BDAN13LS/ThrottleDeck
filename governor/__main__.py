"""Governor service and one-use browser launcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import uvicorn

from governor.collector import GovernorCollector
from governor.config import GOVERNOR_HOST, GOVERNOR_PORT, GovernorPaths
from governor.security import CONTROL_KEY_HEADER, ensure_control_key
from governor.service import create_app
from governor.store import ControlStore


def _request_launch_nonce(key: bytes) -> str:
    request = urllib.request.Request(
        f"http://{GOVERNOR_HOST}:{GOVERNOR_PORT}/internal/v1/launch",
        method="POST",
        headers={CONTROL_KEY_HEADER: key.hex()},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.loads(response.read())
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise RuntimeError("Governor returned an invalid launch response")
    return nonce


def _open_edge(url: str) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    candidates = [
        shutil.which("msedge.exe"),
        str(
            Path(os.environ.get("PROGRAMFILES(X86)", ""))
            / "Microsoft"
            / "Edge"
            / "Application"
            / "msedge.exe"
        ),
        str(
            Path(os.environ.get("PROGRAMFILES", ""))
            / "Microsoft"
            / "Edge"
            / "Application"
            / "msedge.exe"
        ),
    ]
    executable = next(
        (item for item in candidates if item and Path(item).is_file()), None
    )
    if executable is None:
        raise RuntimeError("Microsoft Edge is not installed in a standard location")
    subprocess.Popen(
        [executable, f"--app={url}", "--window-size=940,700"],
        creationflags=flags,
        close_fds=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="governor")
    parser.add_argument("--open", action="store_true")
    arguments = parser.parse_args()
    paths = GovernorPaths.default()
    key = ensure_control_key(paths.control_key)
    if arguments.open:
        nonce = _request_launch_nonce(key)
        _open_edge(f"http://{GOVERNOR_HOST}:{GOVERNOR_PORT}/launch?nonce={nonce}")
        return
    store = ControlStore.create(
        paths.database,
        policy_target=paths.broker_policy,
    )
    collector = GovernorCollector(store)
    app = create_app(
        store,
        control_key=key,
        order_lock_path=paths.execution_order_lock,
        collector=collector,
    )
    uvicorn.run(app, host=GOVERNOR_HOST, port=GOVERNOR_PORT, log_level="warning")


if __name__ == "__main__":
    main()
