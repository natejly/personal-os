"""ExtensionHost: owns the hook bus, the loaded extensions and the app's start/stop (design doc section 3.3)."""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Iterable, get_args

from .. import backups, migrations
from ..context import Block, Contribution, RetrievalSources, Slot
from .defaults import Defaults
from .disposables import Disposable, Scope
from .hooks import HookBus
from .manifest import Manifest, load_manifest

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from ..db import Database
    from ..modules import Module
    from ..tools import ToolSpec

log = logging.getLogger(__name__)

Handler = Callable[[], Awaitable[None]]

SETTING_PREFIX = "ext."  # a setting `ext.<id>.<field>` belongs to the extension that declares <field>
STOP_BUDGET_S = 5.0  # what one extension's app.stop handlers get before they are cancelled
STABLE_CONTEXT_TIMEOUT_MS = 100  # a stable block reads cached state: it is built in the turn path, before the first token


@dataclass
class ExtensionContext:
    """What the app lends to the extensions it loads."""
    db: Database
    hidden_views: Callable[[], Iterable[str]]  # the views the user turned off: an extension in this list is disabled
    services: dict[str, Callable[[], Any]]  # looked up late, because some are built after the extensions; `provide` adds to it
    settings: Callable[[], dict[str, Any]]
    set_settings: Callable[[dict[str, Any]], None]
    # app.sid / app.wsid: normalise a project query param for a reader / a writer (the writer 404s on
    # a stale project id and 400s on 'all').
    sid: Callable[[str | None], Any]
    wsid: Callable[[str | None], str | None]


class Services:
    """The services an extension declared under `requires`."""

    def __init__(self, declared: tuple[str, ...], lookups: dict[str, Callable[[], Any]]) -> None:
        self._declared, self._lookups = declared, lookups

    def get(self, name: str) -> Any:
        if name not in self._declared:
            raise KeyError(f"service {name!r} is not declared under requires.services")
        return self._lookups[name]()


@dataclass
class ExtensionScope:
    """One loaded extension: its manifest, and everything it registered, undone together when it stops."""
    manifest: Manifest
    index: int
    bus: HookBus
    retrieval: RetrievalSources
    module: Module | None = None  # set for an adapted Module
    root: Path | None = None  # the extension's folder, where data.migrations is looked up
    ctx: ExtensionContext | None = None  # set for an extension loaded from its folder
    disposables: Scope = field(default_factory=Scope)
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    routers: list[APIRouter] = field(default_factory=list)
    today_fns: list[Callable[[], dict[str, Any]]] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def log(self) -> logging.Logger:
        return logging.getLogger(f"{__package__}.{self.manifest.id}")

    @property
    def db(self) -> Database:
        assert self.ctx is not None
        return self.ctx.db

    @property
    def app(self) -> ExtensionContext:
        assert self.ctx is not None
        return self.ctx

    @property
    def defaults(self) -> Defaults:
        return Defaults(self.db)

    @property
    def services(self) -> Services:
        assert self.ctx is not None
        return Services(self.manifest.requires_services, self.ctx.services)

    def enabled(self) -> bool:
        if self.module:
            return self.module.enabled()
        if self.manifest.kind == "profile":
            return self.defaults.applied(self.manifest.id)
        if self.manifest.enabled == "always":
            return True
        if self.ctx is None:
            return self.manifest.enabled is not False
        return self.manifest.id not in self.ctx.hidden_views()

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

    def tool(self, spec: ToolSpec) -> Disposable:
        """Offer a tool the manifest declares under contributes.tools; the Toolbox registers it after the modules' tools."""
        self.tools[spec.name] = spec
        return self.disposables.add(Disposable(lambda: self.tools.pop(spec.name, None)))

    def router(self, router: APIRouter) -> Disposable:
        """Add routes. The app includes the router once, when the extension is loaded."""
        self.routers.append(router)
        return self.disposables.add(Disposable(lambda: self.routers.remove(router)))

    def today(self, fn: Callable[[], dict[str, Any]]) -> Disposable:
        """Add keys to the GET /dashboard payload while the extension is enabled; `fn` must be cheap."""
        self.today_fns.append(fn)
        return self.disposables.add(Disposable(lambda: self.today_fns.remove(fn)))

    def provide(self, service: str, impl: Any) -> Disposable:
        """Offer a service the manifest declares under provides.services, to the app and to extensions that require it."""
        if service not in self.manifest.provides_services:
            raise ValueError(f"extension {self.manifest.id!r} did not declare service {service!r} under provides")
        services = self.app.services
        if service in services:
            raise ValueError(f"service {service!r} is already provided")
        services[service] = lambda: impl
        return self.disposables.add(Disposable(lambda: services.pop(service, None)))

    def spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """Run a background task that is cancelled and awaited when the extension stops."""
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def context(self, fn: Callable[[dict[str, Any]], Any], *, slot: Slot, priority: int | None = None,
                share: float | None = None) -> Disposable:
        """Add text to the prompt. `fn` takes {conversation_id, project_id} and returns a Contribution (or None for
        nothing), sync or async; it runs under the manifest's context.contribute hook entry (a stable block gets 100 ms).
        `share` is a fraction of the context window, in (0, 1]."""
        if slot not in get_args(Slot):
            raise ValueError(f"unknown context slot {slot!r}")
        if share is not None and not 0 < share <= 1:
            raise ValueError(f"context share must be in (0, 1], got {share!r}")

        async def contribute(payload: dict[str, Any]) -> Block | None:
            if not self.enabled():
                return None
            out = await asyncio.to_thread(fn, payload)  # an async fn only builds its coroutine there, whatever kind of callable it is
            if inspect.isawaitable(out):
                out = await out
            if isinstance(out, Contribution):
                return Block(out.text, slot, self.manifest.id, share, out.version)
            if out is not None:
                log.warning("extension %s: context handler returned %s, not a Contribution", self.manifest.id, type(out).__name__)
            return None
        return self.on("context.contribute", contribute, priority=priority,
                       timeout_ms=STABLE_CONTEXT_TIMEOUT_MS if slot == "stable" else None)

    def retrieval_source(self, name: str, fn: Callable[[dict[str, Any]], Awaitable[Any]]) -> Disposable:
        """Add a place a turn's retrieval looks; `fn` takes {query, vec, project_id, conv_settings} and returns a list of {id, text, ref}."""
        async def search(payload: dict[str, Any]) -> Any:
            return await fn(payload) if self.enabled() else None
        return self.disposables.add(self.retrieval.register(name, search, owner=self.manifest.id))

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
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.disposables.dispose()


BUILTIN_ROOT = Path(__file__).parent


def builtin_manifest(ext_id: str) -> Manifest:
    return load_manifest(BUILTIN_ROOT / ext_id / "extension.json")


class ExtensionHost:
    def __init__(self) -> None:
        self.bus = HookBus()
        self.retrieval = RetrievalSources()
        self.extensions: dict[str, ExtensionScope] = {}  # in start order
        self._startup: list[Handler] = []
        self._shutdown: list[Handler] = []
        self._enabled: dict[str, bool] = {}  # what ext.changed last reported, per extension

    # ---- the app's start and stop, in registration order ----
    def on_start(self, fn: Handler) -> Handler:
        self._startup.append(fn)
        return fn

    def on_stop(self, fn: Handler) -> Handler:
        self._shutdown.append(fn)
        return fn

    @contextlib.asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        self.bus.loop = asyncio.get_running_loop()
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
            self.bus.close()

    # ---- extensions ----
    def add_module(self, module: Module) -> ExtensionScope:
        """Adapt a Module: it becomes an extension whose start and stop are the module's."""
        manifest = Manifest.from_dict({"id": module.key, "name": module.label, "version": "1.0.0", "apiVersion": 1})
        return self._add(manifest, module)

    def load_builtin(self, ext_id: str, ctx: ExtensionContext) -> ExtensionScope:
        """Load the built-in extension in this package's `<ext_id>/` folder: read its manifest, then run its `activate(scope)`."""
        scope = self._add(builtin_manifest(ext_id), root=BUILTIN_ROOT / ext_id, ctx=ctx)
        if scope.manifest.kind == "profile":
            return scope  # a profile is data: nothing to import or activate
        importlib.import_module(f"{__package__}.{ext_id}.extension").activate(scope)
        declared = {t["name"]: (t.get("danger"), t.get("group")) for t in scope.manifest.contributes.get("tools", ())}
        registered = {name: (spec.danger, spec.group) for name, spec in scope.tools.items()}
        if registered != declared:
            raise ValueError(f"extension {ext_id!r} registered tools {registered}, its manifest declares {declared}")
        return scope

    def today(self) -> dict[str, Any]:
        """The dashboard keys contributed by the enabled extensions."""
        return {k: v for s in self.extensions.values() if s.enabled() for fn in s.today_fns for k, v in fn().items()}

    def _add(self, manifest: Manifest, module: Module | None = None, root: Path | None = None,
             ctx: ExtensionContext | None = None) -> ExtensionScope:
        if manifest.id in self.extensions:
            raise ValueError(f"duplicate extension id {manifest.id!r}")
        scope = ExtensionScope(manifest, len(self.extensions), self.bus, self.retrieval, module, root, ctx)
        self.extensions[manifest.id] = scope
        self._enabled[manifest.id] = scope.enabled()
        return scope

    def fire(self, hook: str, payload: Any = None) -> None:
        self.bus.fire(hook, payload)

    def refresh_enabled(self) -> None:
        """Fire ext.changed for each extension whose enabled state differs from the last one reported. An extension's
        contributes.defaults are applied when it turns on and reverted when it turns off (a profile does this itself)."""
        for ext_id, scope in self.extensions.items():
            if (now := scope.enabled()) != self._enabled[ext_id]:
                self._enabled[ext_id] = now
                if scope.ctx and scope.manifest.kind == "extension":
                    if now:
                        scope.defaults.apply(scope.manifest)
                    else:
                        scope.defaults.revert(ext_id)
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
