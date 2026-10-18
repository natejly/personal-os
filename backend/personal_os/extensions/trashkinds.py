"""Trash kinds an extension owns (design doc 3.4): `api.trash_handler(kind, handler)`. The extension soft-deletes its own
rows; the trash lists them beside the app's, restores or erases one through the handler, and sweeps those older than the
retention period. A kind is named `<extension id>:<name>`, so it can neither shadow the app's kinds nor another extension's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .disposables import Disposable


@dataclass(frozen=True)
class TrashHandler:
    label: str  # the group's heading
    items: Callable[[], list[dict[str, Any]]]  # {id, title, deleted_at} of every row in the trash
    restore: Callable[[str], bool]
    purge: Callable[[str], bool]


class TrashKinds:
    def __init__(self) -> None:
        self._items: dict[str, tuple[TrashHandler, Callable[[], bool]]] = {}

    def register(self, kind: str, handler: TrashHandler, enabled: Callable[[], bool]) -> Disposable:
        if kind in self._items:
            raise ValueError(f"trash kind {kind!r} is already registered")
        self._items[kind] = (handler, enabled)
        return Disposable(lambda: self._items.pop(kind, None))

    def get(self, kind: str) -> TrashHandler | None:
        """The handler of a kind, unless its extension is off: a disabled extension's trash is left alone, not shown."""
        item = self._items.get(kind)
        return item[0] if item and item[1]() else None

    def kinds(self) -> list[str]:
        return [k for k in self._items if self.get(k)]
