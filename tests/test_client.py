import httpx
import pytest

import broker_client


def test_client_timeout_leaves_room_for_broker_timeout_response() -> None:
    assert broker_client.DEFAULT_CLIENT_TIMEOUT_SECONDS > 30.0


def test_client_builds_broker_url_and_caller_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = broker_client.get(
            "/v1/events",
            venue="pmus",
            caller="execution",
            params={"active": "true"},
            client=client,
        )

    assert response.json() == {"ok": True}
    assert str(seen[0].url) == "http://127.0.0.1:8777/pmus/v1/events?active=true"
    assert seen[0].headers["x-broker-caller"] == "execution"


def test_client_fails_loudly_when_broker_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(broker_client.BrokerUnavailableError) as error:
            broker_client.get("/markets", venue="kalshi", client=client)

    message = str(error.value)
    assert "venue broker" in message.lower()
    assert "python -m venue_broker" in message


def test_client_maps_mid_response_restart_to_broker_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection dropped during response", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(broker_client.BrokerUnavailableError):
            broker_client.get("/markets", venue="kalshi", client=client)
