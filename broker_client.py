"""Tiny synchronous client for the local venue broker.

Copy this file into another repository or put ThrottleDeck Broker on ``PYTHONPATH``.
The broker never silently falls back to a venue: an unavailable local service is
an operational error because direct fallback would recreate the shared-IP breach.
Authentication headers remain owned by the caller and are forwarded by the broker.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

DEFAULT_BROKER_URL = "http://127.0.0.1:8777"
DEFAULT_CLIENT_TIMEOUT_SECONDS = 35.0
VALID_VENUES = {"pmus", "kalshi"}


class BrokerUnavailableError(ConnectionError):
    """The required local broker could not be reached."""


def get(
    path: str,
    *,
    venue: str,
    caller: str = "unknown",
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_CLIENT_TIMEOUT_SECONDS,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """GET one venue path through the broker and return the httpx response."""
    if venue not in VALID_VENUES:
        raise ValueError(f"venue must be one of: {', '.join(sorted(VALID_VENUES))}")
    if not path.startswith("/"):
        raise ValueError("path must start with /")

    broker_url = (base_url or os.getenv("VENUE_BROKER_URL", DEFAULT_BROKER_URL)).rstrip(
        "/"
    )
    request_headers = dict(headers or {})
    request_headers["X-Broker-Caller"] = caller
    url = f"{broker_url}/{venue}{path}"
    owns_client = client is None
    active_client = client or httpx.Client()

    try:
        return active_client.get(
            url,
            headers=request_headers,
            params=params,
            timeout=timeout,
        )
    except httpx.TransportError as error:
        raise BrokerUnavailableError(
            f"The venue broker at {broker_url} is unavailable or its connection "
            "was interrupted. Start it with `python -m venue_broker`; direct "
            "venue fallback and transparent replay are disabled."
        ) from error
    finally:
        if owns_client:
            active_client.close()
