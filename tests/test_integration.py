import httpx
import pytest

from venue_broker.app import create_app
from venue_broker.broker import Broker
from venue_broker.config import Settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_polymarket_events_contract() -> None:
    broker = Broker(Settings.from_env())
    transport = httpx.ASGITransport(app=create_app(broker))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://broker", timeout=40.0
        ) as client:
            response = await client.get(
                "/pmus/v1/events",
                params={"limit": 1},
                headers={"X-Broker-Caller": "integration-test"},
            )

        assert response.status_code == 200
        assert response.headers["X-Venue-Broker-Source"] == "venue"
        assert isinstance(response.json()["events"], list)
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_live_kalshi_exchange_status_contract() -> None:
    broker = Broker(Settings.from_env())
    transport = httpx.ASGITransport(app=create_app(broker))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://broker", timeout=40.0
        ) as client:
            response = await client.get(
                "/kalshi/exchange/status",
                headers={"X-Broker-Caller": "integration-test"},
            )

        assert response.status_code == 200
        assert response.headers["X-Venue-Broker-Source"] == "venue"
        payload = response.json()
        assert isinstance(payload["exchange_active"], bool)
        assert isinstance(payload["trading_active"], bool)
    finally:
        await broker.close()
