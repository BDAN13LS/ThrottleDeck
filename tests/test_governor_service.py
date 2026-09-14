from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx
import pytest

from governor.security import CONTROL_KEY_HEADER, SessionManager
from governor.service import create_app
from governor.store import ControlStore

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)
HOST = "127.0.0.1:8778"
ORIGIN = "http://127.0.0.1:8778"


@pytest.fixture
async def service(tmp_path):
    store = ControlStore.create(
        tmp_path / "governor.sqlite3",
        policy_target=tmp_path / "broker-policy.json",
        now=lambda: NOW,
    )
    sessions = SessionManager(now=lambda: NOW)
    key = b"k" * 32
    app = create_app(
        store,
        control_key=key,
        sessions=sessions,
        order_lock_path=tmp_path / "execution-order.lock",
        now=lambda: NOW,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        yield client, store, sessions, key


async def signed_in(service):
    client, _, _, key = service
    launched = await client.post(
        "/internal/v1/launch",
        headers={"host": HOST, CONTROL_KEY_HEADER: key.hex()},
    )
    nonce = launched.json()["nonce"]
    redirect = await client.get(
        f"/launch?nonce={nonce}", headers={"host": HOST}, follow_redirects=False
    )
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/"
    assert "nonce" not in redirect.headers["location"]
    assert "HttpOnly" in redirect.headers["set-cookie"]
    assert "SameSite=strict" in redirect.headers["set-cookie"]
    csrf = redirect.headers["x-governor-csrf"]
    return {"origin": ORIGIN, "host": HOST, "x-csrf-token": csrf}


@pytest.mark.asyncio
async def test_launch_nonce_is_one_use_and_snapshot_accept_path(service) -> None:
    client, _, _, key = service
    response = await client.post(
        "/internal/v1/launch",
        headers={"host": HOST, CONTROL_KEY_HEADER: key.hex()},
    )
    nonce = response.json()["nonce"]
    first = await client.get(
        f"/launch?nonce={nonce}", headers={"host": HOST}, follow_redirects=False
    )
    second = await client.get(f"/launch?nonce={nonce}", headers={"host": HOST})
    assert first.status_code == 303
    assert second.status_code == 403
    snapshot = await client.get("/api/v1/snapshot", headers={"host": HOST})
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["schemaVersion"] == 1
    assert payload["generatedAt"] == NOW.isoformat()
    assert set(payload) == {
        "schemaVersion",
        "revision",
        "generatedAt",
        "broker",
        "brokerAccess",
        "tradeLock",
        "budgets",
        "apps",
        "activity",
    }
    assert payload["brokerAccess"]["global"]["scope"] == "broker:global"
    assert payload["tradeLock"]["state"] == "allowed"
    assert [app["app_id"] for app in payload["apps"]] == [
        "collector",
        "execution",
        "research",
    ]
    assert [budget["id"] for budget in payload["budgets"]] == [
        "pmus",
        "kalshi.predictions",
        "kalshi.perps",
    ]


@pytest.mark.asyncio
async def test_health_and_static_dashboard_delivery(service) -> None:
    client, _, _, _ = service
    assert (await client.get("/health", headers={"host": HOST})).json() == {
        "status": "ok"
    }
    headers = await signed_in(service)
    page = await client.get("/", headers={"host": HOST})
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert '<meta name="governor-csrf" content="' in page.text
    assert "ThrottleDeck - API Control" in page.text
    stylesheet = re.search(r'href="(?P<path>/assets/[^"]+\.css)"', page.text)
    assert stylesheet is not None
    asset = await client.get(stylesheet.group("path"), headers={"host": HOST})
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/css")
    assert headers["x-csrf-token"] in page.text


@pytest.mark.asyncio
async def test_host_origin_csrf_and_cors_are_strict(service) -> None:
    client, _, _, _ = service
    headers = await signed_in(service)
    body = {
        "expected_revision": 0,
        "reason": "operator",
        "confirmation": "STOP ALL",
    }
    bad_host = await client.post(
        "/api/v1/broker-access/global/stop",
        headers=headers | {"host": "localhost:8778"},
        json=body,
    )
    bad_origin = await client.post(
        "/api/v1/broker-access/global/stop",
        headers=headers | {"origin": "https://attacker.example"},
        json=body,
    )
    bad_csrf = await client.post(
        "/api/v1/broker-access/global/stop",
        headers=headers | {"x-csrf-token": "wrong"},
        json=body,
    )
    assert (bad_host.status_code, bad_origin.status_code, bad_csrf.status_code) == (
        403,
        403,
        403,
    )
    assert "access-control-allow-origin" not in bad_origin.headers


@pytest.mark.asyncio
async def test_controls_require_exact_payload_and_current_revision(service) -> None:
    client, _, _, _ = service
    headers = await signed_in(service)
    stopped = await client.post(
        "/api/v1/broker-access/apps/execution/stop",
        headers=headers,
        json={"expected_revision": 0, "reason": "operator"},
    )
    stale = await client.post(
        "/api/v1/broker-access/apps/collector/stop",
        headers=headers,
        json={"expected_revision": 0, "reason": "old window"},
    )
    invalid = await client.post(
        "/api/v1/broker-access/apps/made-up/stop",
        headers=headers,
        json={"expected_revision": 1, "reason": "operator"},
    )
    extra = await client.post(
        "/api/v1/broker-access/global/stop",
        headers=headers,
        json={
            "expected_revision": 1,
            "reason": "operator",
            "confirmation": "STOP ALL",
            "extra": True,
        },
    )
    assert stopped.status_code == 200
    assert stopped.json()["snapshot"]["revision"] == 1
    assert stopped.json()["snapshot"]["brokerAccess"]["apps"]["execution"][
        "state"
    ] == "stopped"
    assert stale.status_code == 409
    assert stale.json()["error"] == "revision_conflict"
    assert stale.json()["snapshot"]["revision"] == 1
    assert invalid.status_code == 404
    assert extra.status_code == 422


@pytest.mark.asyncio
async def test_trade_lock_confirmation_and_exact_permit_payload(service) -> None:
    client, store, _, key = service
    headers = await signed_in(service)
    bad = await client.post(
        "/api/v1/trade-lock/execution/unlock",
        headers=headers,
        json={"expected_revision": 0, "reason": "operator", "confirmation": "yes"},
    )
    assert bad.status_code == 422
    store.apply("trade:new-orders:execution", "stop", 0, "test setup")
    unlocked = await client.post(
        "/api/v1/trade-lock/execution/unlock",
        headers=headers,
        json={
            "expected_revision": 1,
            "reason": "operator",
            "confirmation": "UNLOCK NEW ORDERS",
        },
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["snapshot"]["tradeLock"]["state"] == "allowed"
    permit = await client.get(
        "/internal/v1/trade-permit/execution",
        headers={"host": HOST, CONTROL_KEY_HEADER: key.hex()},
    )
    assert permit.status_code == 200
    assert permit.json() == {
        "schemaVersion": 1,
        "appId": "execution",
        "newOrders": "allowed",
        "revision": 2,
        "observedAt": NOW.isoformat(),
    }
    assert json.loads(store.policy_target.read_text())["revision"] == 2


@pytest.mark.asyncio
async def test_trade_permit_is_fail_closed_for_lock_and_invalid_database(
    service,
) -> None:
    client, store, _, key = service
    request_headers = {"host": HOST, CONTROL_KEY_HEADER: key.hex()}
    store.apply("trade:new-orders:execution", "stop", 0, "test lock")
    locked = await client.get(
        "/internal/v1/trade-permit/execution", headers=request_headers
    )
    assert locked.status_code == 423
    with store._connect() as connection:
        connection.execute("UPDATE maintenance_state SET schema_version=99 WHERE id=1")
        connection.commit()
    unavailable = await client.get(
        "/internal/v1/trade-permit/execution", headers=request_headers
    )
    assert unavailable.status_code == 503
