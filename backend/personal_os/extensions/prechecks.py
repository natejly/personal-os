"""Prechecks (design doc 3.4): an extension's refusal of a tool call before any approval card is shown.

An extension registers `fn(name, args, ctx) -> str | None` with `api.precheck(fn)`. A text is the refusal; there is no
way to approve. A function that raises is logged and counts as no refusal: the extension's `tool.pre` gate repeats the
same check below the approval wait, and that gate fails toward asking.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .disposables import Disposable

log = logging.getLogger(__name__)

Precheck = Callable[[str, dict[str, Any], dict[str, Any]], str | None]


class Prechecks:
    def __init__(self) -> None:
        self._checks: list[tuple[Precheck, Callable[[], bool]]] = []

    def register(self, fn: Precheck, enabled: Callable[[], bool]) -> Disposable:
        entry = (fn, enabled)
        self._checks.append(entry)
        return Disposable(lambda: self._checks.remove(entry))

    def refusal(self, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> str | None:
        """The first refusal of an enabled extension's precheck, or None."""
        for fn, enabled in list(self._checks):
            if not enabled():
                continue
            try:
                if text := fn(name, args, ctx):
                    return text
            except Exception:  # noqa: BLE001 - see the module docstring
                log.warning("precheck %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return None
