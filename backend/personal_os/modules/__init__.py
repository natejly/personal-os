"""Feature modules: one unit per feature that owns its routes, tables, agent tools, background loops
and Today-screen payload, so app.py and tools.py iterate a list instead of naming each feature.

A module is constructed once, with a ModuleContext, after the app's shared services exist. app.py then:
- includes `router()` (if any) on the FastAPI app,
- passes the list to Toolbox, which calls `register_tools(box)` where the feature's tools used to be
  registered, so tool order is unchanged,
- awaits `start()` on startup and `stop()` on shutdown,
- merges `today()` into GET /dashboard.

Tables stay where they are declared today (a module-level SCHEMA run by the store class), so a module
adds no migration step. `tool_available` lets a module veto one of its own tools at schema time, the
way Toolbox.available() special-cases google/sandbox/mac today; returning None means "no opinion".

This is the pilot contract (docs/module-manifest.md). Built-in only: nothing is loaded at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from fastapi import APIRouter

    from ..db import Database
    from ..tools import Toolbox


@dataclass
class ModuleContext:
    """What a module may use from the app. Everything else it needs, it constructs itself."""
    db: Database
    settings: Callable[[], dict[str, Any]]
    set_settings: Callable[[dict[str, Any]], None]
    google: Any
    # app.sid / app.wsid: normalise a project query param for a reader / a writer (the writer 404s on
    # a stale project id and 400s on 'all').
    sid: Callable[[str | None], Any]
    wsid: Callable[[str | None], str | None]


class Module:
    key: str = ""
    label: str = ""

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx

    def router(self) -> APIRouter | None:
        return None

    def register_tools(self, box: Toolbox) -> None:
        """Add ToolSpecs to box.specs. Called from Toolbox.__init__."""

    def tool_available(self, name: str) -> bool | None:
        return None

    async def start(self) -> None:
        """Start background loops. Must return promptly; spawn tasks, do not await them."""

    async def stop(self) -> None:
        """Cancel and await whatever start() spawned. Must not raise."""

    def today(self) -> dict[str, Any]:
        """Keys merged into the GET /dashboard payload. Must be cheap and must not call Google."""
        return {}


def build_modules(ctx: ModuleContext) -> list[Module]:
    """The built-in modules, in registration order."""
    from .mailwatch import MailWatchModule
    from .todos import TodosModule

    return [TodosModule(ctx), MailWatchModule(ctx)]


M = TypeVar("M", bound=Module)


def get(modules: list[Module], key: str, cls: type[M]) -> M:
    """The module with this key, as its concrete class, for the app code that still reads its internals."""
    for m in modules:
        if m.key == key:
            if not isinstance(m, cls):
                raise TypeError(f"module {key!r} is {type(m).__name__}, not {cls.__name__}")
            return m
    raise KeyError(key)
