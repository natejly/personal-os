"""ExtensionHost: owns the hook bus, the loaded extensions and the app's start/stop (design doc section 3.3)."""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Collection, Iterable, Literal

from .disposables import Disposable, Scope
from .hooks import HookBus
from .manifest import Manifest, ManifestError, load_manifest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..modules import Module

log = logging.getLogger(__name__)

Tier = Literal["builtin", "user", "thirdparty"]
TIER_RANK: dict[str, int] = {"builtin": 0, "user": 1, "thirdparty": 2}
Handler = Callable[[], Awaitable[None]]


@dataclass
class ExtensionScope:
    """One loaded extension: its manifest, and everything it registered, undone together when it stops."""
    manifest: Manifest
    tier: Tier
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
            hook, fn, owner=self.manifest.id, order=(TIER_RANK[self.tier], self.index),
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
    def __init__(self, services: Iterable[str] = ()) -> None:
        """`services` are the names core itself provides, which an extension may list under requires.services."""
        self.bus = HookBus()
        self.extensions: dict[str, ExtensionScope] = {}  # in start order
        self.invalid: dict[str, str] = {}  # folder or id -> why it was not loaded
        self._services = frozenset(services)
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
        return self._add(manifest, "builtin", module)

    def load_dir(self, root: Path, tier: Tier, live_tools: Collection[str] = ()) -> None:
        """Read every `<root>/<id>/extension.json`, validate the set as a whole, and register the valid ones in start order.
        An invalid extension is listed in `invalid` with the reason and never partly loaded."""
        found: list[Manifest] = []
        for path in sorted(root.glob("*/extension.json")):
            try:
                found.append(load_manifest(path))
            except ManifestError as e:
                self.invalid[path.parent.name] = str(e)
        for m in self._resolve(found, live_tools):
            self._add(m, tier)

    def _add(self, manifest: Manifest, tier: Tier, module: Module | None = None) -> ExtensionScope:
        if manifest.id in self.extensions:
            raise ValueError(f"duplicate extension id {manifest.id!r}")
        scope = ExtensionScope(manifest, tier, len(self.extensions), self.bus, module)
        self.extensions[manifest.id] = scope
        return scope

    def _resolve(self, found: list[Manifest], live_tools: Collection[str]) -> list[Manifest]:
        """The manifests that can load, topologically sorted by `requires` (manifest order within a level)."""
        valid: dict[str, Manifest] = {}
        taken = set(live_tools).union(*(s.manifest.tool_names for s in self.extensions.values()))
        for m in found:
            if m.id in valid or m.id in self.extensions:
                self.invalid[m.id] = "id already in use"
            elif clash := sorted(set(m.tool_names) & taken) or sorted({n for n in m.tool_names if m.tool_names.count(n) > 1}):
                self.invalid[m.id] = f"tool name(s) already in use: {', '.join(clash)}"
            else:
                valid[m.id] = m
                taken.update(m.tool_names)
        while bad := {i: why for i, m in valid.items() if (why := self._unmet(m, valid))}:
            for i, why in bad.items():
                self.invalid[i] = why
                del valid[i]
        ordered: list[Manifest] = []
        placed = set(self.extensions)
        while len(ordered) < len(valid):
            ready = [m for m in valid.values() if m not in ordered and self._needs(m, valid) <= placed]
            if not ready:
                for m in valid.values():
                    if m not in ordered:
                        self.invalid[m.id] = "requires cycle"
                break
            ordered.extend(ready)
            placed.update(m.id for m in ready)
        return ordered

    def _provided(self, valid: dict[str, Manifest]) -> set[str]:
        return set(self._services).union(*(m.provides_services for m in valid.values()))

    def _unmet(self, m: Manifest, valid: dict[str, Manifest]) -> str:
        if missing := [e for e in m.requires_extensions if e not in valid and e not in self.extensions]:
            return f"requires extension(s) that are not loaded: {', '.join(missing)}"
        if missing := [s for s in m.requires_services if s not in self._provided(valid)]:
            return f"requires service(s) nobody provides: {', '.join(missing)}"
        return ""

    def _needs(self, m: Manifest, valid: dict[str, Manifest]) -> set[str]:
        """Ids that must start before m: the extensions it names and whoever provides a service it names."""
        providers = {s: i for i, v in valid.items() for s in v.provides_services}
        return set(m.requires_extensions) | {providers[s] for s in m.requires_services if s in providers}

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
