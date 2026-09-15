# Working in ThrottleDeck Broker

ThrottleDeck Broker is the single loopback-only owner of shared Polymarket US and Kalshi
read budgets on one machine. Several applications can share one public egress IP,
so independent limiters can each look healthy while collectively breaching a venue
limit. Call the local broker instead of probing a venue to discover its shape;
start with [the project README](README.md) and official venue documentation.

## Adopt the broker

Copy [broker_client.py](broker_client.py) into the calling repository, install
`httpx`, and use:

```python
from broker_client import get

response = get("/v1/events", venue="pmus", caller="my-bot")
data = response.raise_for_status().json()
```

Use `venue="kalshi"` for a path relative to Kalshi's `/trade-api/v2` base.
Supply authentication headers from the caller when an authenticated GET is
needed. The client deliberately raises `BrokerUnavailableError` instead of
falling back to a direct venue request.

## Configuration and operation

Readable rate, token-cost, cache, tier, and environment-variable policy lives in
[venue_broker/config.py](venue_broker/config.py). The service binds to hardcoded
`127.0.0.1`; its default port is `8777`, configurable with
`VENUE_BROKER_PORT`. Install and run it with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m venue_broker
```

For unattended Windows operation, use the existing hidden launcher and task
installer under [scripts/](scripts/). Do not invent a second scheduled instance.

## ThrottleDeck control window

Governor is a separate loopback-only control service on `127.0.0.1:8778`; it is
not a second broker and must never start, stop, or supervise ThrottleDeck Broker. Its
React source is in `ui/`, while the committed production bundle is in
`governor/static/`. Rebuild the bundle after every UI change.

The Edge app launches at 940 by 700 pixels in a cool near-black iOS-style theme
and remains resizable. Its approximately 920 by 650 usable content area must fit
without a page scrollbar; application rows and Control activity use compact
compositions there. Application request counts and sparklines measure brokered
REST only. Preserve null request-series points across missing/reset counters and
never infer direct or WebSocket liveness from them. Preserve the safety hierarchy, exact control meanings,
non-optimistic mutations, and the two-consecutive-miss polling warning. Browser
tests also run at 940 by 700 and 720 by 480; no tested viewport may scroll
horizontally.

Application IDs, display names, caller groups, coverage notes, money badges, and
the protected application come from `%LOCALAPPDATA%\ThrottleDeck\apps.toml`. The
checked-in `governor/default-apps.toml`, UI fixtures, documentation, and screenshots
must stay synthetic and operator-neutral. Config changes take effect after both
ThrottleDeck and ThrottleDeck Broker restart; never put credentials in this file.
Keep its caller labels synchronized with `caller-policy.toml`. Every broker caller
entry also requires a synthetic `project` value for `/callers` and generated
adoption status.

```powershell
npm --prefix ui test
npm --prefix ui run typecheck
npm --prefix ui run build
npm --prefix ui run test:e2e
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Tests replace the upstream fetch function and do not call either venue.

## Rules that must remain true

- Bind only to loopback. Do not make the broker reachable from the LAN or internet.
- Exactly one broker process owns the machine's venue budgets. Callers never run independent fallback limiters or bypass it when it is unavailable.
- Never store credentials, private keys, API keys, or signed headers in this repository. Authentication remains caller-owned and is only forwarded in memory.
- Keep the broker read-only. It accepts `GET`; adding order submission or fund movement is outside its purpose and requires an explicit, specific request.
- Read the cited official venue page before inferring an API shape or making a live discovery request.
- Do not log request headers. They may contain caller-owned authentication material.
