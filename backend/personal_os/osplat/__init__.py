"""Operating-system layer. One implementation is picked once from sys.platform; everything else asks it.

    from . import osplat
    osplat.current().desktop.open_path(path)      # a platform object
    if osplat.has("screenshot"): ...              # a capability
    osplat.capabilities()                         # {name: bool}, served by GET /capabilities

This package is the only place that reads sys.platform (tests/test_platform.py enforces it). To add a platform, write one
module that exposes `build() -> Platform` and add it to `_BUILDERS`.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator

from .base import Platform, Unsupported  # noqa: F401

_current: Platform | None = None


def _build(name: str) -> Platform:
    if name == "darwin":
        from . import darwin as mod
    elif name == "win32":
        from . import win32 as mod
    else:  # linux and every other unix
        from . import linux as mod
    plat = mod.build()
    return plat


def current() -> Platform:
    global _current
    if _current is None:
        _current = _build(sys.platform)
    return _current


def name() -> str:
    return current().name


def capabilities() -> dict[str, bool]:
    return current().capabilities()


def has(capability: str) -> bool:
    return bool(capabilities().get(capability, False))


@contextmanager
def use(plat: Platform | str) -> Iterator[Platform]:
    """Tests: run with another platform object ("darwin", "linux", "win32" or a Platform) and restore afterwards."""
    global _current
    saved = _current
    _current = _build(plat) if isinstance(plat, str) else plat
    try:
        yield _current
    finally:
        _current = saved
