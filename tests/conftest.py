"""Make registry tests independent of any user's local app names."""

from __future__ import annotations

import os
from pathlib import Path

os.environ["THROTTLEDECK_APPS_CONFIG"] = str(
    Path(__file__).with_name("fixtures") / "apps.toml"
)
