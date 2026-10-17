"""Clamps for numbers that arrive from a client or a model."""
from __future__ import annotations

from typing import Any


def clamp_limit(limit: int, hi: int = 500) -> int:
    """A list route's LIMIT. SQLite reads a negative one as 'no limit', so it is bounded here, not trusted."""
    return max(1, min(int(limit), hi))


def clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    """`value` as an int within [lo, hi]; `default` when it is missing or not a number."""
    try:
        return max(lo, min(int(value if value is not None else default), hi))
    except (TypeError, ValueError):
        return default
