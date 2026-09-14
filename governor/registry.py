"""Per-user application-to-caller registry; caller prefixes are never inferred."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from governor.config import GovernorPaths
from governor.models import AppDefinition, MoneyMode

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True, slots=True)
class MoneyControlDefinition:
    app_id: str
    confirmation_phrase: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    apps: Mapping[str, AppDefinition]
    money_control: MoneyControlDefinition


def _default_config_path() -> Path:
    return Path(__file__).with_name("default-apps.toml")


def configured_path() -> Path:
    override = os.getenv("THROTTLEDECK_APPS_CONFIG")
    if override:
        return Path(override).expanduser()
    per_user = GovernorPaths.default().apps
    return per_user if per_user.is_file() else _default_config_path()


def load_app_config(path: Path) -> AppConfig:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(f"Governor app config is unreadable: {path}") from error

    rows = payload.get("apps")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "Governor app config must contain at least one [[apps]] entry"
        )

    apps: dict[str, AppDefinition] = {}
    claimed_callers: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("each [[apps]] entry must be a table")
        app_id = row.get("id")
        name = row.get("name")
        callers = row.get("callers")
        mode = row.get("money_mode", "paper")
        note = row.get("coverage_note", "")
        if not isinstance(app_id, str) or not _IDENTIFIER.fullmatch(app_id):
            raise RuntimeError(
                "app ids must use 1-64 letters, numbers, dots, dashes, or underscores"
            )
        if app_id in apps:
            raise RuntimeError(f"duplicate app id: {app_id}")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise RuntimeError(f"app {app_id} must have a name of 1-80 characters")
        if not isinstance(callers, list) or not callers:
            raise RuntimeError(f"app {app_id} must have at least one caller")
        parsed_callers: list[str] = []
        for caller in callers:
            if not isinstance(caller, str) or not _IDENTIFIER.fullmatch(caller):
                raise RuntimeError(f"app {app_id} has an invalid caller")
            normalized = caller.lower()
            if normalized in claimed_callers:
                raise RuntimeError(f"caller is assigned more than once: {normalized}")
            claimed_callers.add(normalized)
            parsed_callers.append(normalized)
        try:
            money_mode = MoneyMode(mode)
        except ValueError as error:
            raise RuntimeError(
                f"app {app_id} money_mode must be paper or real"
            ) from error
        if not isinstance(note, str) or len(note) > 500:
            raise RuntimeError(
                f"app {app_id} coverage_note must be text up to 500 characters"
            )
        apps[app_id] = AppDefinition(
            app_id=app_id,
            display_name=name.strip(),
            callers=tuple(parsed_callers),
            money_mode=money_mode,
            coverage_note=note.strip(),
        )

    money = payload.get("money_control")
    if not isinstance(money, dict):
        raise RuntimeError("Governor app config must contain [money_control]")
    money_app_id = money.get("app_id")
    phrase = money.get("confirmation_phrase")
    if not isinstance(money_app_id, str) or money_app_id not in apps:
        raise RuntimeError("money_control.app_id must name a configured app")
    if apps[money_app_id].money_mode is not MoneyMode.REAL:
        raise RuntimeError("money_control.app_id must name a real-money app")
    if not isinstance(phrase, str) or not 3 <= len(phrase.strip()) <= 80:
        raise RuntimeError(
            "money_control.confirmation_phrase must contain 3-80 characters"
        )

    return AppConfig(
        apps=MappingProxyType(apps),
        money_control=MoneyControlDefinition(money_app_id, phrase.strip()),
    )


APP_CONFIG = load_app_config(configured_path())
APP_REGISTRY = APP_CONFIG.apps
MONEY_CONTROL = APP_CONFIG.money_control
CONTROL_SCOPES = frozenset(
    {"broker:global", "trade:new-orders:execution"}
    | {f"broker:app:{app_id}" for app_id in APP_REGISTRY}
)
_CALLER_TO_APP = MappingProxyType(
    {caller: app.app_id for app in APP_REGISTRY.values() for caller in app.callers}
)


def app_for_caller(caller: str) -> str:
    return _CALLER_TO_APP.get(caller.lower(), "unassigned")
