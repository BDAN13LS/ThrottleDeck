from pathlib import Path

import pytest

from governor.registry import load_app_config
from venue_broker.config import (
    BROKER_HOST,
    CACHE_TTL_RULES,
    DEFAULT_CALLER_POLICY_PATH,
    DEFAULT_CONTROL_POLICY_PATH,
    KALSHI_TIERS,
    POLYMARKET_PUBLIC_REQUESTS_PER_SECOND,
    Settings,
    cache_ttl,
    kalshi_token_cost,
    load_caller_policy,
)


def test_documented_limits_are_visible_in_config() -> None:
    assert POLYMARKET_PUBLIC_REQUESTS_PER_SECOND == 20.0
    assert KALSHI_TIERS["basic"].read_refill_rate == 200.0
    assert KALSHI_TIERS["basic"].read_capacity == 400.0


def test_service_host_is_loopback_only() -> None:
    assert BROKER_HOST == "127.0.0.1"


def test_control_policy_path_is_fixed_under_local_app_data() -> None:
    assert DEFAULT_CONTROL_POLICY_PATH.name == "broker-policy.json"
    assert DEFAULT_CONTROL_POLICY_PATH.parent.name == "ThrottleDeck"


def test_margin_scales_refill_and_burst_capacity() -> None:
    settings = Settings(rate_margin=0.9, kalshi_tier="basic")

    assert settings.polymarket_bucket.refill_rate == pytest.approx(18.0)
    assert settings.polymarket_bucket.capacity == pytest.approx(18.0)
    assert settings.kalshi_bucket.refill_rate == pytest.approx(180.0)
    assert settings.kalshi_bucket.capacity == pytest.approx(360.0)


def test_kalshi_costs_use_tokens_not_request_counts() -> None:
    assert kalshi_token_cost("/markets/ABC/orderbook", b"") == 10.0
    assert kalshi_token_cost("/cfbenchmarks/v1/value", b"") == 50.0
    assert kalshi_token_cost("/margin/balance", b"") == 5.0
    assert (
        kalshi_token_cost("/margin/balance", b"compute_available_balance=true") == 50.0
    )


def test_cache_ttls_are_explicit_and_default_to_no_cache() -> None:
    assert CACHE_TTL_RULES
    assert cache_ttl("pmus", "/v1/sports/teams") >= 60.0
    assert 0 < cache_ttl("pmus", "/v1/markets/game/book") <= 1.0
    assert 0 < cache_ttl("kalshi", "/markets/GAME/orderbook") <= 1.0
    assert cache_ttl("pmus", "/v1/account/balances") == 0.0
    assert cache_ttl("kalshi", "/unknown/new-endpoint") == 0.0


def test_root_policy_configures_classes_and_unknown_caller_default() -> None:
    policy = load_caller_policy(DEFAULT_CALLER_POLICY_PATH)

    assert policy.weights == {"trading": 8, "standard": 2, "bulk": 1}
    assert policy.class_for("collector") == "trading"
    assert policy.class_for("execution") == "bulk"
    assert policy.class_for("research") == "standard"
    assert policy.class_for("new-bot") == "standard"
    assert policy.project_for("collector") == "Signal Collector"
    assert policy.project_for("execution-lag") == "Execution Bot"
    assert policy.project_for("research") == "Market Research"


def test_shipped_dashboard_and_broker_registries_name_the_same_callers() -> None:
    root = Path(__file__).resolve().parents[1]
    policy = load_caller_policy(root / "caller-policy.toml")
    apps = load_app_config(root / "governor" / "default-apps.toml")

    dashboard_callers = {
        caller for app in apps.apps.values() for caller in app.callers
    }

    assert dashboard_callers == set(policy.callers)


def test_public_launcher_does_not_pin_account_specific_rates() -> None:
    launcher = (
        Path(__file__).resolve().parents[1] / "scripts" / "start-broker-hidden.ps1"
    ).read_text(encoding="utf-8")

    assert "$env:VENUE_BROKER_KALSHI_TIER" not in launcher
    assert "$env:VENUE_BROKER_KALSHI_PERPS_READ_RATE" not in launcher


def test_changing_policy_file_is_the_only_step_to_promote_a_caller(tmp_path) -> None:
    policy_path = tmp_path / "caller-policy.toml"
    policy_path.write_text(
        """
[defaults]
class = "standard"

[classes.trading]
weight = 8
[classes.standard]
weight = 2

[callers.execution]
class = "trading"
project = "Example App"
""".strip(),
        encoding="utf-8",
    )

    policy = load_caller_policy(policy_path)

    assert policy.class_for("execution") == "trading"
    assert policy.project_for("execution") == "Example App"


@pytest.mark.parametrize(
    "contents, message",
    [
        (
            """
[defaults]
class = "missing"
[classes.standard]
weight = 2
""",
            "default class",
        ),
        (
            """
[defaults]
class = "standard"
[classes.standard]
weight = 0
""",
            "positive integer",
        ),
        (
            """
[defaults]
class = "standard"
[classes.standard]
weight = 2
[callers.bot]
class = "missing"
project = "Bot"
""",
            "unknown class",
        ),
        (
            """
[defaults]
class = "standard"
[classes.standard]
weight = 2
[callers.bot]
class = "standard"
""",
            "project",
        ),
    ],
)
def test_invalid_caller_policy_fails_loudly(
    tmp_path, contents: str, message: str
) -> None:
    policy_path = tmp_path / "invalid.toml"
    policy_path.write_text(contents.strip(), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_caller_policy(policy_path)


def test_the_rolling_ceiling_respects_the_safety_margin() -> None:
    """The margin has to apply to the hard cap, not only the refill rate.

    The venue counts 20 requests per second per IP. Capping our own rolling
    window at exactly 20 means running permanently at the boundary: our second
    and the venue's are not the same second, so a few milliseconds of offset
    turns our compliant 20 into their 21 and we take a 429 while believing we
    were inside the limit. The margin exists for exactly that gap, and was
    applied to the refill rate while the ceiling kept the raw number.
    """
    from venue_broker.config import POLYMARKET_PUBLIC_REQUESTS_PER_SECOND, Settings

    settings = Settings(rate_margin=0.9)

    assert settings.polymarket_rolling_limit == 18
    assert settings.polymarket_rolling_limit < POLYMARKET_PUBLIC_REQUESTS_PER_SECOND


def test_a_full_margin_allows_the_documented_ceiling() -> None:
    """Someone who deliberately sets the margin to 1.0 gets the raw limit."""
    from venue_broker.config import Settings

    assert Settings(rate_margin=1.0).polymarket_rolling_limit == 20
