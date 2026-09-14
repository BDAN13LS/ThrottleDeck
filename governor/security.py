"""Per-install authentication and one-use local browser sessions."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

CONTROL_KEY_HEADER = "x-governor-control-key"
SESSION_COOKIE = "governor_session"
CSRF_HEADER = "x-csrf-token"
NONCE_LIFETIME = timedelta(minutes=5)
SESSION_LIFETIME = timedelta(hours=8)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _restrict_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    username = os.environ.get("USERNAME")
    if not username:
        raise RuntimeError("cannot determine current Windows user")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:(F)",
        ],
        check=False,
        capture_output=True,
        creationflags=flags,
    )
    if result.returncode != 0:
        raise RuntimeError("could not restrict Governor control-key permissions")


def ensure_control_key(path: Path) -> bytes:
    """Read or atomically create the fixed 32-byte per-install secret."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            _restrict_windows_acl(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    if len(key) != 32:
        raise RuntimeError("Governor control key has an invalid length")
    _restrict_windows_acl(path)
    return key


@dataclass(frozen=True, slots=True)
class BrowserSession:
    session_id: str
    csrf_token: str
    expires_at: datetime


class SessionManager:
    def __init__(self, *, now: Callable[[], datetime] = _utc_now) -> None:
        self._now = now
        self._nonces: dict[str, datetime] = {}
        self._sessions: dict[str, BrowserSession] = {}

    def _prune(self) -> None:
        now = self._now()
        self._nonces = {
            nonce: expires for nonce, expires in self._nonces.items() if expires > now
        }
        self._sessions = {
            key: session
            for key, session in self._sessions.items()
            if session.expires_at > now
        }

    def issue_launch_nonce(self) -> str:
        self._prune()
        nonce = secrets.token_urlsafe(32)
        self._nonces[nonce] = self._now() + NONCE_LIFETIME
        return nonce

    def consume_launch_nonce(self, nonce: str) -> BrowserSession:
        self._prune()
        expires = self._nonces.pop(nonce, None)
        if expires is None or expires <= self._now():
            raise ValueError("invalid or expired launch nonce")
        session = BrowserSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self._now() + SESSION_LIFETIME,
        )
        self._sessions[session.session_id] = session
        return session

    def authenticate(self, session_id: str | None) -> BrowserSession | None:
        self._prune()
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def validate_csrf(self, session_id: str | None, token: str | None) -> bool:
        session = self.authenticate(session_id)
        return bool(
            session and token and secrets.compare_digest(session.csrf_token, token)
        )
