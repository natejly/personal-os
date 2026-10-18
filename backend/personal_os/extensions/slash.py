"""Slash commands an extension registers (design doc 3.4): `api.command(name, fn)` for a name its manifest lists under
contributes.commands. Typing `/name args` in a chat runs `fn(args)` and lays the text it returns under the turn, the way
the app's own `/research` and `/schedule` lay theirs; the stored message keeps what was typed. Nothing else runs.
"""
from __future__ import annotations

import logging
from typing import Callable

from .disposables import Disposable

log = logging.getLogger(__name__)

CommandFn = Callable[[str], str | None]


class SlashCommands:
    def __init__(self) -> None:
        self._items: dict[str, tuple[str, CommandFn, str, Callable[[], bool]]] = {}  # name -> (owner, fn, hint, enabled)

    def register(self, owner: str, name: str, fn: CommandFn, hint: str, enabled: Callable[[], bool]) -> Disposable:
        if name in self._items:
            raise ValueError(f"the slash command /{name} is already registered by {self._items[name][0]!r}")
        self._items[name] = (owner, fn, hint, enabled)
        return Disposable(lambda: self._items.pop(name, None))

    def expand(self, name: str, args: str) -> str | None:
        """What the command of that name lays under the turn, or None when there is none, it is off, or it has nothing to add."""
        item = self._items.get(name)
        if item is None or not item[3]():
            return None
        try:
            return item[1](args) or None
        except Exception:  # noqa: BLE001 - a broken command leaves the turn as typed
            log.warning("slash command /%s from %s failed", name, item[0], exc_info=True)
            return None

    def listing(self) -> list[dict[str, str]]:
        """The enabled commands, for the composer's menu."""
        return [{"name": name, "hint": hint, "owner": owner} for name, (owner, _, hint, enabled) in sorted(self._items.items()) if enabled()]
