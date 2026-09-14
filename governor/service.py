"""Authenticated, loopback-only Governor control service."""

from __future__ import annotations

import asyncio
import html
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator

from governor.collector import GovernorCollector
from governor.config import GOVERNOR_HOST, GOVERNOR_PORT
from governor.direct_egress import run_direct_egress_audit
from governor.models import InvalidControlSchema, RevisionConflict
from governor.registry import APP_REGISTRY, MONEY_CONTROL
from governor.security import (
    CONTROL_KEY_HEADER,
    CSRF_HEADER,
    SESSION_COOKIE,
    BrowserSession,
    SessionManager,
)
from governor.store import ControlStore
from governor.viewmodel import build_snapshot

EXPECTED_HOST = f"{GOVERNOR_HOST}:{GOVERNOR_PORT}"
EXPECTED_ORIGIN = f"http://{EXPECTED_HOST}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_is_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must not be blank")
        return cleaned


class GlobalStopBody(ControlBody):
    confirmation: Literal["STOP ALL"]


class TradeUnlockBody(ControlBody):
    confirmation: str = Field(min_length=3, max_length=80)

    @field_validator("confirmation")
    @classmethod
    def confirmation_matches_config(cls, value: str) -> str:
        if value != MONEY_CONTROL.confirmation_phrase:
            raise ValueError("confirmation does not match the configured phrase")
        return value


def create_app(
    store: ControlStore,
    *,
    control_key: bytes,
    sessions: SessionManager | None = None,
    order_lock_path: Path,
    collector: GovernorCollector | None = None,
    now: Callable[[], datetime] = _utc_now,
    static_dir: Path | None = None,
    direct_egress_audit: Callable[[], dict] = run_direct_egress_audit,
) -> FastAPI:
    session_manager = sessions or SessionManager(now=now)
    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(collector.run(stop_event)) if collector else None
        try:
            yield
        finally:
            stop_event.set()
            if task:
                await task

    app = FastAPI(lifespan=lifespan)
    assets_root = (static_dir or Path(__file__).with_name("static")).resolve()

    @app.middleware("http")
    async def exact_host(request: Request, call_next):
        if request.headers.get("host") != EXPECTED_HOST:
            return JSONResponse({"error": "forbidden_host"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def require_control_key(request: Request) -> None:
        supplied = request.headers.get(CONTROL_KEY_HEADER)
        if supplied is None or not secrets.compare_digest(supplied, control_key.hex()):
            raise HTTPException(status_code=403, detail="invalid_control_key")

    def require_session(request: Request) -> BrowserSession:
        session_id = request.cookies.get(SESSION_COOKIE)
        session = session_manager.authenticate(session_id)
        if session is None:
            raise HTTPException(status_code=401, detail="session_required")
        return session

    def require_mutation_auth(request: Request) -> BrowserSession:
        session = require_session(request)
        if request.headers.get("origin") != EXPECTED_ORIGIN:
            raise HTTPException(status_code=403, detail="invalid_origin")
        if not session_manager.validate_csrf(
            session.session_id, request.headers.get(CSRF_HEADER)
        ):
            raise HTTPException(status_code=403, detail="invalid_csrf")
        return session

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/")
    async def index(request: Request) -> HTMLResponse:
        session = require_session(request)
        try:
            page = (assets_root / "index.html").read_text(encoding="utf-8")
        except OSError as error:
            raise HTTPException(
                status_code=503, detail="dashboard_unavailable"
            ) from error
        csrf = html.escape(session.csrf_token, quote=True)
        page = page.replace(
            "</head>", f'<meta name="governor-csrf" content="{csrf}" /></head>', 1
        )
        return HTMLResponse(page)

    @app.get("/assets/{asset_path:path}")
    async def asset(asset_path: str, request: Request) -> FileResponse:
        require_session(request)
        asset_root = (assets_root / "assets").resolve()
        candidate = (asset_root / asset_path).resolve()
        if asset_root not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="asset_not_found")
        return FileResponse(candidate)

    @app.post(
        "/internal/v1/launch",
        dependencies=[Depends(require_control_key)],
    )
    async def create_launch() -> dict:
        return {"nonce": session_manager.issue_launch_nonce()}

    @app.get("/launch")
    async def consume_launch(nonce: str) -> RedirectResponse:
        try:
            session = session_manager.consume_launch_nonce(nonce)
        except ValueError as error:
            raise HTTPException(status_code=403, detail="invalid_launch") from error
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session.session_id,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=8 * 60 * 60,
        )
        response.headers["X-Governor-CSRF"] = session.csrf_token
        return response

    @app.get("/api/v1/snapshot", dependencies=[Depends(require_session)])
    async def snapshot() -> dict:
        try:
            return build_snapshot(store, now())
        except InvalidControlSchema as error:
            raise HTTPException(
                status_code=503, detail="control_state_unavailable"
            ) from error

    @app.post(
        "/api/v1/diagnostics/direct-egress",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def direct_egress() -> dict:
        return await asyncio.to_thread(direct_egress_audit)

    def apply_control(scope: str, action: str, body: ControlBody) -> dict:
        try:
            store.apply(scope, action, body.expected_revision, body.reason)
        except RevisionConflict:
            return JSONResponse(
                {
                    "error": "revision_conflict",
                    "snapshot": build_snapshot(store, now()),
                },
                status_code=409,
            )
        except InvalidControlSchema as error:
            raise HTTPException(
                status_code=503, detail="control_state_unavailable"
            ) from error
        return {"snapshot": build_snapshot(store, now())}

    @app.post(
        "/api/v1/broker-access/global/stop",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def stop_global(
        body: GlobalStopBody,
    ) -> dict:
        return apply_control("broker:global", "stop", body)

    @app.post(
        "/api/v1/broker-access/global/resume",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def resume_global(
        body: ControlBody,
    ) -> dict:
        return apply_control("broker:global", "resume", body)

    def app_scope(app_id: str) -> str:
        if app_id not in APP_REGISTRY:
            raise HTTPException(status_code=404, detail="unknown_app")
        return f"broker:app:{app_id}"

    @app.post(
        "/api/v1/broker-access/apps/{app_id}/stop",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def stop_app(
        app_id: str,
        body: ControlBody,
    ) -> dict:
        return apply_control(app_scope(app_id), "stop", body)

    @app.post(
        "/api/v1/broker-access/apps/{app_id}/resume",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def resume_app(
        app_id: str,
        body: ControlBody,
    ) -> dict:
        return apply_control(app_scope(app_id), "resume", body)

    def apply_trade(action: str, body: ControlBody) -> dict:
        lock = FileLock(str(order_lock_path), timeout=5)
        with lock:
            return apply_control("trade:new-orders:execution", action, body)

    @app.post(
        "/api/v1/trade-lock/execution/stop",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def stop_trade(
        body: ControlBody,
    ) -> dict:
        return await asyncio.to_thread(apply_trade, "stop", body)

    @app.post(
        "/api/v1/trade-lock/execution/unlock",
        dependencies=[Depends(require_mutation_auth)],
    )
    async def unlock_trade(
        body: TradeUnlockBody,
    ) -> dict:
        return await asyncio.to_thread(apply_trade, "resume", body)

    @app.get(
        "/internal/v1/trade-permit/execution",
        dependencies=[Depends(require_control_key)],
    )
    async def trade_permit() -> dict:
        try:
            snapshot = store.snapshot()
        except InvalidControlSchema as error:
            raise HTTPException(
                status_code=503, detail="control_state_unavailable"
            ) from error
        if snapshot.state("trade:new-orders:execution") != "allowed":
            raise HTTPException(status_code=423, detail="new_orders_locked")
        return {
            "schemaVersion": 1,
            "appId": "execution",
            "newOrders": "allowed",
            "revision": snapshot.revision,
            "observedAt": now().isoformat(),
        }

    return app
