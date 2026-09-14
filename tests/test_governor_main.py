"""Boundary tests for the Governor Edge app launcher."""

from __future__ import annotations

from typing import Any

from governor import __main__ as governor_main


def test_edge_app_opens_at_the_compact_governor_window_size(monkeypatch) -> None:
    launched: dict[str, Any] = {}
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

    monkeypatch.setattr(governor_main.shutil, "which", lambda _name: edge)

    def record_launch(arguments, **options) -> None:
        launched["arguments"] = arguments
        launched["options"] = options

    monkeypatch.setattr(governor_main.subprocess, "Popen", record_launch)

    governor_main._open_edge("http://127.0.0.1:8778/launch?nonce=one-use")

    assert launched["arguments"] == [
        edge,
        "--app=http://127.0.0.1:8778/launch?nonce=one-use",
        "--window-size=940,700",
    ]
    assert launched["options"]["close_fds"] is True
