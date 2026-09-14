from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from governor.security import SessionManager, ensure_control_key

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def test_control_key_is_32_persistent_random_bytes(tmp_path) -> None:
    target = tmp_path / "control.key"
    first = ensure_control_key(target)
    second = ensure_control_key(target)
    assert len(first) == 32
    assert first == second == target.read_bytes()


def test_launch_nonce_is_one_use_and_creates_server_side_session() -> None:
    manager = SessionManager(now=lambda: NOW)
    nonce = manager.issue_launch_nonce()
    session = manager.consume_launch_nonce(nonce)
    assert manager.authenticate(session.session_id) == session
    assert manager.validate_csrf(session.session_id, session.csrf_token)
    with pytest.raises(ValueError, match="nonce"):
        manager.consume_launch_nonce(nonce)


def test_expired_nonce_and_session_are_rejected() -> None:
    clock = [NOW]
    manager = SessionManager(now=lambda: clock[0])
    nonce = manager.issue_launch_nonce()
    clock[0] += timedelta(minutes=6)
    with pytest.raises(ValueError, match="nonce"):
        manager.consume_launch_nonce(nonce)

    clock[0] = NOW
    session = manager.consume_launch_nonce(manager.issue_launch_nonce())
    clock[0] += timedelta(hours=9)
    assert manager.authenticate(session.session_id) is None
