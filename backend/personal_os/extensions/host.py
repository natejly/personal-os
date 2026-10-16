"""ExtensionHost: owns the hook bus, the loaded extensions and the app's start/stop (design doc section 3.3)."""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from .disposables import Disposable, Scope
from .hooks import HookBus
from .manifest import Manifest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..modules import Module

log = logging.getLogger(__name__)

Handler = Callable[[], Awaitable[None]]


@dataclass
class ExtensionScope:
    """One loaded extension: its manifest, and everything it registered, undone together when it stops."""
    manifest: Manifest
    index: int
    bus: HookBus
    module: Module | None = None  # set for an adapted Module
    disposables: Scope = field(default_factory=Scope)

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

    async def stop(self) -> None:
        try:
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

    # ---- the app's start and stop, in registration order ----
    def on_start(self, fn: Handler) -> Handler:
        self._startup.append(fn)
        return fn

    def on_stop(self, fn: Handler) -> Handler:
        self._shutdown.append(fn)
        return fn

    @contextlib.asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
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

    # ---- extensions ----
    def add_module(self, module: Module) -> ExtensionScope:
        """Adapt a Module: it becomes an extension whose start and stop are the module's."""
        manifest = Manifest.from_dict({"id": module.key, "name": module.label, "version": "1.0.0", "apiVersion": 1})
        return self._add(manifest, module)

    def _add(self, manifest: Manifest, module: Module | None = None) -> ExtensionScope:
        if manifest.id in self.extensions:
            raise ValueError(f"duplicate extension id {manifest.id!r}")
        scope = ExtensionScope(manifest, len(self.extensions), self.bus, module)
        self.extensions[manifest.id] = scope
        return scope

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
