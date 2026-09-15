"""Readable policy for upstream limits, token costs, and cache lifetimes.

Primary sources:

* Polymarket US public traffic: 20 requests/second/IP in
  ``https://docs.polymarket.us/api-reference/rate-limits``.
* Kalshi token mechanics and every public tier: Basic read refill 200
  tokens/second with a 400-token capacity in
  ``https://docs.kalshi.com/getting_started/rate_limits``.
* Kalshi's default endpoint cost is 10 tokens. The CF Benchmarks passthrough
  costs 50 tokens, explaining the documented Basic-tier rate of 4 requests/second;
  see ``https://docs.kalshi.com/cfbenchmarks/rest-passthrough``.

The tier below is a table of what the venue documents, not a claim about this
account. ``KALSHI_TIERS`` defaults to ``basic`` so a checkout with no
environment set cannot over-request on an account that was never upgraded.
``/metrics`` reports the tier in force, and that is the only runtime statement
worth trusting.

The broker handles GET reads only. Kalshi's separate Write buckets therefore do
not belong in the runtime limiter, but the tier table below records them so the
source numbers remain reviewable next to the code that uses them.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import parse_qs, unquote

POLYMARKET_PUBLIC_REQUESTS_PER_SECOND = 20.0
DEFAULT_RATE_MARGIN = 0.90
DEFAULT_PORT = 8777
DEFAULT_MAX_CACHE_ENTRIES = 4096
BROKER_HOST = "127.0.0.1"
DEFAULT_CALLER_POLICY_PATH = (
    Path(__file__).resolve().parent.parent / "caller-policy.toml"
)
DEFAULT_CONTROL_POLICY_PATH = (
    Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "ThrottleDeck"
    / "broker-policy.json"
)
POLICY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


@dataclass(frozen=True, slots=True)
class CallerPolicy:
    """Weighted caller classes loaded once when the broker starts."""

    default_class: str
    weights: Mapping[str, int]
    callers: Mapping[str, str]
    projects: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        object.__setattr__(self, "callers", MappingProxyType(dict(self.callers)))
        object.__setattr__(self, "projects", MappingProxyType(dict(self.projects)))

    def class_for(self, caller: str) -> str:
        return self.callers.get(caller.lower(), self.default_class)

    def project_for(self, caller: str) -> str | None:
        return self.projects.get(caller.lower())

    def registry(self) -> list[dict[str, str | int]]:
        return [
            {
                "name": caller,
                "project": self.projects.get(caller, "Unassigned"),
                "class": class_name,
                "weight": self.weights[class_name],
            }
            for caller, class_name in sorted(self.callers.items())
        ]


def load_caller_policy(path: str | os.PathLike[str]) -> CallerPolicy:
    """Load and strictly validate the startup-only caller policy TOML."""
    policy_path = Path(path)
    try:
        with policy_path.open("rb") as file:
            raw = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load caller policy {policy_path}: {error}") from error

    _reject_unknown_keys(raw, {"defaults", "classes", "callers"}, "top level")
    defaults = _required_table(raw, "defaults")
    classes = _required_table(raw, "classes")
    callers = raw.get("callers", {})
    if not isinstance(callers, dict):
        raise ValueError("caller policy [callers] must be a table")
    _reject_unknown_keys(defaults, {"class"}, "[defaults]")

    default_class = defaults.get("class")
    if not isinstance(default_class, str) or not POLICY_NAME.fullmatch(default_class):
        raise ValueError("caller policy default class must be a valid class name")
    default_class = default_class.lower()

    weights: dict[str, int] = {}
    for raw_name, values in classes.items():
        name = _normalized_policy_name(raw_name, "class")
        if name in weights:
            raise ValueError(f"duplicate caller class after normalization: {name}")
        if not isinstance(values, dict):
            raise ValueError(f"caller class {name!r} must be a table")
        _reject_unknown_keys(values, {"weight"}, f"[classes.{raw_name}]")
        weight = values.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise ValueError(f"caller class {name!r} weight must be a positive integer")
        weights[name] = weight

    if not weights:
        raise ValueError("caller policy must define at least one class")
    if default_class not in weights:
        raise ValueError(
            f"caller policy default class {default_class!r} is not defined"
        )

    caller_classes: dict[str, str] = {}
    caller_projects: dict[str, str] = {}
    for raw_name, values in callers.items():
        name = _normalized_policy_name(raw_name, "caller")
        if name in caller_classes:
            raise ValueError(f"duplicate caller after normalization: {name}")
        if not isinstance(values, dict):
            raise ValueError(f"caller {name!r} must be a table")
        _reject_unknown_keys(values, {"class", "project"}, f"[callers.{raw_name}]")
        class_name = values.get("class")
        if not isinstance(class_name, str):
            raise ValueError(f"caller {name!r} class must be a string")
        class_name = class_name.lower()
        if class_name not in weights:
            raise ValueError(f"caller {name!r} references unknown class {class_name!r}")
        project = values.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError(f"caller {name!r} project must be a non-empty string")
        if len(project) > 80 or "\n" in project or "\r" in project or "|" in project:
            raise ValueError(f"caller {name!r} project is invalid")
        caller_classes[name] = class_name
        caller_projects[name] = project.strip()

    return CallerPolicy(default_class, weights, caller_classes, caller_projects)


def _required_table(raw: Mapping[str, object], name: str) -> dict[str, object]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"caller policy [{name}] must be a table")
    return value


def _reject_unknown_keys(
    values: Mapping[str, object], allowed: set[str], location: str
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unknown caller policy key in {location}: {unknown[0]}")


def _normalized_policy_name(value: object, kind: str) -> str:
    if not isinstance(value, str) or not POLICY_NAME.fullmatch(value):
        raise ValueError(f"caller policy {kind} name must match {POLICY_NAME.pattern}")
    return value.lower()


def default_caller_policy() -> CallerPolicy:
    """Return the built-in test/programmatic default used outside startup."""
    # ``weather`` is retained as a bounded compatibility row for existing
    # programmatic callers. Production startup replaces this with the explicit
    # caller-policy.toml registry.
    return CallerPolicy(
        "standard",
        {"standard": 1},
        {"weather": "standard"},
        {"weather": "Unassigned"},
    )


@dataclass(frozen=True, slots=True)
class KalshiTier:
    read_refill_rate: float
    read_capacity: float
    write_refill_rate: float
    write_capacity: float


# Values are tokens, not request counts. Basic and Advanced read buckets have
# two seconds of capacity; higher read tiers have one. Write capacity is one
# second for Basic and two seconds above Basic.
KALSHI_TIERS: dict[str, KalshiTier] = {
    "basic": KalshiTier(200.0, 400.0, 100.0, 100.0),
    "advanced": KalshiTier(300.0, 600.0, 300.0, 600.0),
    "expert": KalshiTier(600.0, 600.0, 600.0, 1200.0),
    "premier": KalshiTier(1000.0, 1000.0, 1000.0, 2000.0),
    "paragon": KalshiTier(2000.0, 2000.0, 2000.0, 4000.0),
    "prime": KalshiTier(4000.0, 4000.0, 4000.0, 8000.0),
    "prestige": KalshiTier(10000.0, 10000.0, 8000.0, 16000.0),
}


@dataclass(frozen=True, slots=True)
class BucketConfig:
    refill_rate: float
    capacity: float
    unit: str


@dataclass(frozen=True, slots=True)
class CacheTTLRule:
    venue: str
    path_pattern: str
    seconds: float
    reason: str


# First match wins. Live books and quotes get only a 250 ms micro-cache. Static
# directories get five minutes. Anything not named here gets no cache at all.
CACHE_TTL_RULES: tuple[CacheTTLRule, ...] = (
    CacheTTLRule(
        "pmus",
        r"/v1/price-history",
        30.0,
        "Price history documentation asks for at least 30 seconds of caching.",
    ),
    CacheTTLRule(
        "pmus",
        r"/v1/markets/[^/]+/(?:book|bbo)",
        0.25,
        "Live price data; only collapse near-simultaneous polling.",
    ),
    CacheTTLRule(
        "pmus",
        r"/v1/sports(?:/.*)?",
        300.0,
        "Sports, league, team, and player reference directories.",
    ),
    CacheTTLRule(
        "pmus",
        r"/v1/tags(?:/.*)?",
        300.0,
        "Tag reference data changes infrequently.",
    ),
    CacheTTLRule(
        "pmus",
        r"/v1/referencedata/(?:symbols|instruments)(?:/.*)?",
        300.0,
        "Instrument and symbol reference data is explicitly cacheable.",
    ),
    CacheTTLRule(
        "kalshi",
        r"/markets/(?:orderbooks|[^/]+/orderbook)",
        0.25,
        "Live order books; only collapse near-simultaneous polling.",
    ),
    CacheTTLRule(
        "kalshi",
        r"/series(?:/.*)?",
        300.0,
        "Series are reference metadata.",
    ),
    CacheTTLRule(
        "kalshi",
        r"/exchange/status",
        2.0,
        "Exchange state changes rarely, but stale closures should clear quickly.",
    ),
)


# Only documented GET exceptions to Kalshi's 10-token default are listed.
# The authoritative live list is GET /account/endpoint_costs; these rules come
# from official endpoint pages and are intentionally easy to update.
KALSHI_GET_TOKEN_COST_RULES: tuple[tuple[str, float, str], ...] = (
    (
        r"/portfolio/orders/queue_positions",
        10.0,
        "https://docs.kalshi.com/api-reference/orders/get-queue-positions-for-orders",
    ),
    (
        r"/cfbenchmarks(?:/.*)?",
        50.0,
        "https://docs.kalshi.com/cfbenchmarks/rest-passthrough",
    ),
    (
        r"/portfolio/orders/[^/]+",
        2.0,
        "https://docs.kalshi.com/api-reference/orders/get-order",
    ),
    (
        r"/margin/balance",
        5.0,
        "https://docs.kalshi.com/margin-rest/portfolio/get-balance",
    ),
    (
        r"/communications/quotes/[^/]+",
        2.0,
        "https://docs.kalshi.com/api-reference/communications/get-quote",
    ),
    (
        r"/communications/rfqs/[^/]+/quotes/[^/]+",
        2.0,
        "https://docs.kalshi.com/api-reference/communications/get-rfq-quote",
    ),
)
KALSHI_DEFAULT_TOKEN_COST = 10.0


def cache_ttl(venue: str, path: str) -> float:
    """Return an explicit TTL, defaulting to zero for every unknown path."""
    for rule in CACHE_TTL_RULES:
        if rule.venue == venue and re.fullmatch(rule.path_pattern, path):
            return rule.seconds
    return 0.0


def kalshi_token_cost(path: str, query: bytes) -> float:
    """Return the documented GET cost, including query-dependent work."""
    path = unquote(path).rstrip("/")
    if path in ("/markets/orderbooks", "/markets/candlesticks"):
        parameters = parse_qs(query.decode("ascii"), keep_blank_values=True)
        parameter = "tickers" if path == "/markets/orderbooks" else "market_tickers"
        # Count every supplied item, including duplicates. Orderbooks documents
        # repeated query keys; candlesticks documents comma-separated tickers.
        # Counting both forms conservatively avoids undercharging mixed input.
        items = [
            item for value in parameters.get(parameter, []) for item in value.split(",")
        ]
        if not 1 <= len(items) <= 100 or any(
            not item or len(item) > 200 for item in items
        ):
            raise ValueError(
                "A read batch must contain between 1 and 100 valid tickers."
            )
        return len(items) * KALSHI_DEFAULT_TOKEN_COST
    if path == "/margin/balance":
        parameters = parse_qs(query.decode("ascii"), keep_blank_values=True)
        compute_balance = parameters.get("compute_available_balance", [])
        if any(value.lower() == "true" for value in compute_balance):
            return 50.0
    for pattern, cost, _source in KALSHI_GET_TOKEN_COST_RULES:
        if re.fullmatch(pattern, path):
            return cost
    return KALSHI_DEFAULT_TOKEN_COST


def is_kalshi_perps_path(path: str) -> bool:
    path = unquote(path).rstrip("/")
    return (
        path == "/margin"
        or path.startswith("/margin/")
        or path == "/portfolio/margin"
        or path.startswith("/portfolio/margin/")
        or path == "/account/limits/perps"
    )


@dataclass(frozen=True, slots=True)
class Settings:
    rate_margin: float = DEFAULT_RATE_MARGIN
    kalshi_tier: str = "basic"
    port: int = DEFAULT_PORT
    max_retries: int = 2
    retry_base_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    max_cache_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    polymarket_bucket_override: BucketConfig | None = None
    kalshi_bucket_override: BucketConfig | None = None
    kalshi_perps_read_rate: float | None = None
    caller_policy: CallerPolicy = field(default_factory=default_caller_policy)
    control_policy_path: Path = DEFAULT_CONTROL_POLICY_PATH

    def __post_init__(self) -> None:
        if not 0 < self.rate_margin <= 1:
            raise ValueError("rate_margin must be greater than 0 and at most 1")
        if self.kalshi_tier not in KALSHI_TIERS:
            raise ValueError(f"unknown Kalshi tier: {self.kalshi_tier}")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        if self.kalshi_perps_read_rate is not None and (
            not math.isfinite(self.kalshi_perps_read_rate)
            or self.kalshi_perps_read_rate <= 0
        ):
            raise ValueError("kalshi_perps_read_rate must be finite and positive")

    @property
    def polymarket_rolling_limit(self) -> int:
        """The most requests allowed in any rolling second, margin included.

        The venue counts 20 per second per IP. Capping at exactly 20 runs
        permanently at the boundary, and our second is not the venue's second:
        a few milliseconds of offset turns a compliant 20 into their 21. The
        margin that protects the refill rate has to protect this too.
        """
        return max(1, int(POLYMARKET_PUBLIC_REQUESTS_PER_SECOND * self.rate_margin))

    @property
    def polymarket_bucket(self) -> BucketConfig:
        if self.polymarket_bucket_override is not None:
            return self.polymarket_bucket_override
        safe_rate = POLYMARKET_PUBLIC_REQUESTS_PER_SECOND * self.rate_margin
        return BucketConfig(safe_rate, safe_rate, "requests")

    @property
    def kalshi_bucket(self) -> BucketConfig:
        if self.kalshi_bucket_override is not None:
            return self.kalshi_bucket_override
        tier = KALSHI_TIERS[self.kalshi_tier]
        return BucketConfig(
            tier.read_refill_rate * self.rate_margin,
            tier.read_capacity * self.rate_margin,
            "tokens",
        )

    @property
    def kalshi_perps_bucket(self) -> BucketConfig | None:
        # Perps has a separate account tier. Do not infer its rate from the
        # prediction tier. Its documented Read capacity is one second of budget.
        if self.kalshi_perps_read_rate is None:
            return None
        safe_rate = self.kalshi_perps_read_rate * self.rate_margin
        return BucketConfig(safe_rate, safe_rate, "tokens")

    @classmethod
    def from_env(cls) -> Settings:
        policy_path = os.getenv(
            "VENUE_BROKER_CALLER_POLICY", str(DEFAULT_CALLER_POLICY_PATH)
        )
        return cls(
            kalshi_perps_read_rate=(
                float(os.environ["VENUE_BROKER_KALSHI_PERPS_READ_RATE"])
                if "VENUE_BROKER_KALSHI_PERPS_READ_RATE" in os.environ
                else None
            ),
            rate_margin=float(
                os.getenv("VENUE_BROKER_RATE_MARGIN", str(DEFAULT_RATE_MARGIN))
            ),
            kalshi_tier=os.getenv("VENUE_BROKER_KALSHI_TIER", "basic").lower(),
            port=int(os.getenv("VENUE_BROKER_PORT", str(DEFAULT_PORT))),
            max_retries=int(os.getenv("VENUE_BROKER_MAX_RETRIES", "2")),
            retry_base_seconds=float(os.getenv("VENUE_BROKER_RETRY_BASE_SECONDS", "1")),
            request_timeout_seconds=float(
                os.getenv("VENUE_BROKER_REQUEST_TIMEOUT_SECONDS", "30")
            ),
            max_cache_entries=int(
                os.getenv(
                    "VENUE_BROKER_MAX_CACHE_ENTRIES",
                    str(DEFAULT_MAX_CACHE_ENTRIES),
                )
            ),
            caller_policy=load_caller_policy(policy_path),
            control_policy_path=DEFAULT_CONTROL_POLICY_PATH,
        )
