"""Prompt directives (design doc 3.4): a built-in extension's unwrapped paragraph in the stable prompt prefix.

`api.directive(fn, version=)` registers `fn(cfg) -> str`. The text is a pure function of the settings it reads, so the
prefix stays byte-identical between turns; `version` is part of the prefix hash and is bumped when the text changes.
"""
from __future__ import annotations

from typing import Any, Callable

from .disposables import Disposable

Directive = Callable[[dict[str, Any]], str]


class Directives:
    def __init__(self) -> None:
        self._items: list[tuple[str, Directive, int, Callable[[], bool]]] = []

    def register(self, owner: str, fn: Directive, version: int, enabled: Callable[[], bool]) -> Disposable:
        entry = (owner, fn, version, enabled)
        self._items.append(entry)
        return Disposable(lambda: self._items.remove(entry))

    def texts(self, cfg: dict[str, Any]) -> list[tuple[str, int, str]]:
        """(owner, version, text) of each enabled directive that has text under these settings, in registration order."""
        out = [(owner, version, fn(cfg)) for owner, fn, version, enabled in self._items if enabled()]
        return [item for item in out if item[2]]

    def text(self, cfg: dict[str, Any]) -> str:
        return "\n\n".join(text for _, _, text in self.texts(cfg))

    def versions(self, cfg: dict[str, Any]) -> list[str]:
        """`<owner>@<version>` of each directive with text: what the prefix hash records beside the text."""
        return [f"{owner}@{version}" for owner, version, _ in self.texts(cfg)]
