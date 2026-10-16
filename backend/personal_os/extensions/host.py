"""ExtensionHost: owns the hook bus, the loaded extensions and the app's start/stop (design doc section 3.3)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from .. import backups, migrations
from .disposables import Disposable, Scope
from .hooks import HookBus
from .manifest import Manifest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..db import Database
    from ..modules import Module

log = logging.getLogger(__name__)

Handler = Callable[[], Awaitable[None]]

SETTING_PREFIX = "ext."  # a setting `ext.<id>.<field>` belongs to the extension that declares <field>
STOP_BUDGET_S = 5.0  # what one extension's app.stop handlers get before they are cancelled


@dataclass
class ExtensionScope:
    """One loaded extension: its manifest, and everything it registered, undone together when it stops."""
    manifest: Manifest
    index: int
    bus: HookBus
    module: Module | None = None  # set for an adapted Module
    root: Path | None = None  # the extension's folder, where data.migrations is looked up
    disposables: Scope = field(default_factory=Scope)

    def enabled(self) -> bool:
        return self.module.enabled() if self.module else self.manifest.enabled is not False

    def migration_steps(self) -> list[tuple[int, str, migrations.Step]]:
        folder = self.manifest.data.get("migrations")
        return migrations.sql_steps(self.root / folder) if folder and self.root else []

    def on(self, hook: str, fn: Callable[[Any], Any], *, priority: int | None = None, timeout_ms: int | None = None) -> Disposable:
        """Subscribe to a hook the manifest declares; priority, timeout and failure policy default to its entry."""
        if hook not in self.manifest.hooks:
            raise ValueError(f"extension {self.manifest.id!r} did not declare hook {hook!r}")
        declared = self.manifest.hooks[hook]
        sub = self.bus.subscribe(
            hook, fn, owner=self.manifest.id, order=self.index,
            priority=declared.get("priority") if priority is None else priority,
            timeout_ms=declared.get("timeoutMs") if timeout_ms is None else timeout_ms,
            on_failure=declared.get("onFailure", "ask"))
        return self.disposables.add(sub)

    async def start(self) -> None:
        if self.module:
            await self.module.start()
        await self.bus.emit("app.start", {}, owner=self.manifest.id)

    async def stop(self) -> None:
        try:
            try:
                await asyncio.wait_for(self.bus.emit("app.stop", None, owner=self.manifest.id), STOP_BUDGET_S)
            except asyncio.TimeoutError:
                log.warning("extension %s: app.stop handlers cancelled after %.0f s", self.manifest.id, STOP_BUDGET_S)
            if self.module:
                await self.module.stop()
        finally:
            self.disposables.dispose()


class ExtensionHost:
    def __init__(self) -> None:
        self.bus = HookBus()
        self.extensions: dict[str, ExtensionScope] = {}  # in start order
        self._startup: list[Handler] = []
        self._shutdown: list[Handler] = []
        self._enabled: dict[str, bool] = {}  # what ext.changed last reported, per extension
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fired: set[asyncio.Task[None]] = set()

    # ---- the app's start and stop, in registration order ----
    def on_start(self, fn: Handler) -> Handler:
        self._startup.append(fn)
        return fn

    def on_stop(self, fn: Handler) -> Handler:
        self._shutdown.append(fn)
        return fn

    @contextlib.asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        self._loop = asyncio.get_running_loop()
        for fn in self._startup:
            await fn()
        try:
            yield
        finally:
            for fn in self._shutdown:
                try:
                    await fn()
                except Exception:  # noqa: BLE001 - the remaining handlers (outbox, backups, ...) still have to run
                    log.exception("shutdown handler %s failed", fn.__name__)
            for task in self._fired:
                task.cancel()

    # ---- extensions ----
    def add_module(self, module: Module) -> ExtensionScope:
        """Adapt a Module: it becomes an extension whose start and stop are the module's."""
        manifest = Manifest.from_dict({"id": module.key, "name": module.label, "version": "1.0.0", "apiVersion": 1})
        return self._add(manifest, module)

    def _add(self, manifest: Manifest, module: Module | None = None, root: Path | None = None) -> ExtensionScope:
        if manifest.id in self.extensions:
            raise ValueError(f"duplicate extension id {manifest.id!r}")
        scope = ExtensionScope(manifest, len(self.extensions), self.bus, module, root)
        self.extensions[manifest.id] = scope
        self._enabled[manifest.id] = scope.enabled()
        return scope

    def fire(self, hook: str, payload: Any = None) -> None:
        """Emit from any thread without waiting for the handlers: a sync route runs in the threadpool, and a slow
        handler must not hold up its response. Before the loop is running (and after it has closed) nothing fires."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._spawn, hook, payload)

    def _spawn(self, hook: str, payload: Any) -> None:
        task = asyncio.get_running_loop().create_task(self.bus.emit(hook, payload))
        self._fired.add(task)  # the loop keeps only a weak reference to a task
        task.add_done_callback(self._fired.discard)

    def refresh_enabled(self) -> None:
        """Fire ext.changed for each extension whose enabled state differs from the last one reported."""
        for ext_id, scope in self.extensions.items():
            if (now := scope.enabled()) != self._enabled[ext_id]:
                self._enabled[ext_id] = now
                self.fire("ext.changed", {"id": ext_id, "enabled": now})

    # ---- data ----
    def migrate(self, db: Database) -> None:
        """Apply every extension's pending migrations, after a premigrate backup when there are any."""
        steps = {s.manifest.id: s.migration_steps() for s in self.extensions.values()}
        if not any(steps.values()):
            return  # nothing declares migrations: no connection to open
        with contextlib.closing(db.connect()) as c:
            todo = {ext_id: p for ext_id, st in steps.items() if (p := migrations.pending_extension(c, ext_id, st))}
            if not todo:
                return
            backups.create(db.data_dir, "premigrate")
            for ext_id, pending in todo.items():
                migrations.run_extension(c, ext_id, pending)

    def data_tables(self) -> list[str]:
        """The tables extensions own, for the readable export (a backup copies the whole database file)."""
        return [t for s in self.extensions.values() for t in s.manifest.data.get("tables", ())]

    # ---- settings: `ext.<id>.<field>`, typed by the manifest ----
    def setting_type(self, key: str) -> str | None:
        """The declared type of an extension setting key, or None when no loaded extension declares it."""
        if not key.startswith(SETTING_PREFIX):
            return None
        ext_id, _, name = key.removeprefix(SETTING_PREFIX).partition(".")
        spec = self.extensions[ext_id].manifest.settings.get(name) if ext_id in self.extensions else None
        return spec["type"] if spec else None

    def check_setting(self, key: str, value: Any) -> Any:
        """The value as it may be stored for a declared extension setting, or ValueError with the reason."""
        kind = self.setting_type(key)
        if kind is None:
            raise ValueError(f"{key} is not a declared setting")
        ok = {"string": isinstance(value, str), "secret": value is None or isinstance(value, str),
              "boolean": isinstance(value, bool), "integer": isinstance(value, int) and not isinstance(value, bool),
              "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)}[kind]
        if not ok:
            raise ValueError(f"{key} must be a {kind}")
        return value

    def secret_setting_keys(self) -> list[str]:
        return [f"{SETTING_PREFIX}{s.manifest.id}.{name}" for s in self.extensions.values()
                for name, spec in s.manifest.settings.items() if spec["type"] == "secret"]

    async def start(self) -> None:
        for scope in self.extensions.values():
            await scope.start()

    async def stop(self) -> None:
        """Stop in reverse start order; one extension failing to stop never keeps the others from stopping."""
        for scope in reversed(list(self.extensions.values())):
            try:
                await scope.stop()
            except Exception:  # noqa: BLE001
                log.exception("extension %s failed to stop", scope.manifest.id)
