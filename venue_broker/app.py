"""Small ASGI surface for the loopback-only broker."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import quote, unquote

from venue_broker.broker import Broker, BrokerResponse, broker_error
from venue_broker.config import is_kalshi_perps_path

CALLER_HEADER = "x-broker-caller"
VALID_CALLER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
REQUEST_HEADERS_TO_REMOVE = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    CALLER_HEADER,
}
RESPONSE_HEADERS_TO_REMOVE = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class BrokerApp:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return

        method = scope["method"]
        if method != "GET":
            await send_response(
                send,
                broker_error(405, "method_not_allowed", "Only GET is supported."),
            )
            return

        raw_path = scope.get("raw_path")
        if raw_path is None:
            raw_path = quote(scope["path"], safe="/").encode("ascii")
        if raw_path == b"/metrics":
            body = json.dumps(await self._broker.metrics(), sort_keys=True).encode()
            await send_response(
                send,
                BrokerResponse(200, body, {"content-type": "application/json"}),
            )
            return
        if raw_path == b"/health":
            status, payload = self._broker.health()
            body = json.dumps(payload, sort_keys=True).encode()
            headers = {"content-type": "application/json"}
            if status >= 400:
                headers["X-Venue-Broker-Source"] = "broker"
            await send_response(send, BrokerResponse(status, body, headers))
            return

        route = parse_route(raw_path)
        if route is None:
            await send_response(
                send,
                broker_error(404, "route_not_found", "The broker route was not found."),
            )
            return
        venue, upstream_path = route
        headers = decode_headers(scope.get("headers", []))
        caller = normalize_caller(headers.get(CALLER_HEADER))
        forwarded = {
            key: value
            for key, value in headers.items()
            if key not in REQUEST_HEADERS_TO_REMOVE
        }

        response_task = asyncio.create_task(
            self._broker.get(
                venue,
                upstream_path,
                scope.get("query_string", b""),
                forwarded,
                caller,
            )
        )
        disconnect_task = asyncio.create_task(wait_for_disconnect(receive))
        done, _pending = await asyncio.wait(
            {response_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if disconnect_task in done:
            response_task.cancel()
            await asyncio.gather(response_task, return_exceptions=True)
            self._broker.caller_disconnected(
                venue,
                caller,
                perps=(venue == "kalshi" and is_kalshi_perps_path(upstream_path)),
            )
            return
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        response = await response_task
        await send_response(send, response)

    async def _lifespan(self, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self._broker.close()
                await send({"type": "lifespan.shutdown.complete"})
                return


def create_app(broker: Broker) -> BrokerApp:
    return BrokerApp(broker)


def parse_route(raw_path: bytes) -> tuple[str, str] | None:
    """The venue and upstream path, or None when the route cannot be trusted.

    Dot segments are refused, decoded form included. The upstream host is fixed
    so traversal cannot reach another server, but every policy decision this
    broker makes is taken from the path: the cache TTL, the Kalshi token cost,
    and whether a Polymarket read goes to the gateway or the authenticated api
    host. A path that resolves upstream to something other than what it
    presents is therefore metered and cached as the wrong thing, silently.
    """
    for prefix, venue in ((b"/pmus", "pmus"), (b"/kalshi", "kalshi")):
        if raw_path == prefix or raw_path.startswith(prefix + b"/"):
            remainder = raw_path[len(prefix) :] or b"/"
            try:
                decoded = remainder.decode("ascii")
            except UnicodeDecodeError:
                return None
            if any(part in (".", "..") for part in unquote(decoded).split("/")):
                return None
            return venue, decoded
    return None


def decode_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in raw_headers
    }


def normalize_caller(value: str | None) -> str:
    if value is not None and VALID_CALLER.fullmatch(value):
        return value.lower()
    return "unknown"


async def wait_for_disconnect(receive: Any) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def send_response(send: Any, response: BrokerResponse) -> None:
    headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in response.headers.items()
        if key.lower() not in RESPONSE_HEADERS_TO_REMOVE
    ]
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": response.body})
