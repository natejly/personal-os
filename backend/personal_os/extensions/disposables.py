"""Disposables: every registration an extension makes returns one, and the extension's scope keeps them all."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


class Disposable:
    """Undoes one registration. Disposing twice is harmless."""

    def __init__(self, undo: Callable[[], None]) -> None:
        self._undo: Callable[[], None] | None = undo

    def dispose(self) -> None:
        undo, self._undo = self._undo, None
        if undo:
            undo()


class Scope:
    """What one extension registered. An extension that forgets to unregister cannot leak: disposing the scope
    undoes everything, newest first, and one failing undo does not stop the rest."""

    def __init__(self) -> None:
        self._items: list[Disposable] = []

    def add(self, d: Disposable) -> Disposable:
        self._items.append(d)
        return d

    def dispose(self) -> None:
        items, self._items = self._items, []
        for d in reversed(items):
            try:
                d.dispose()
            except Exception:  # noqa: BLE001 - the remaining registrations must still be undone
                log.warning("disposing a registration failed", exc_info=True)
