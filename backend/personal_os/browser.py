"""The agent's own interactive browser: browser_* tools over the desktop app's bridge."""
from __future__ import annotations

from typing import Any


def register(tb: Any) -> None:
    """Register this module's tools on the Toolbox."""
