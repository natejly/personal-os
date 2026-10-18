"""ExtensionHost: owns the hook bus, the loaded extensions and the app's start/stop (design doc section 3.3)."""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import itertools
import logging
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Iterable, Iterator, get_args

from fastapi import Depends, HTTPException

from .. import backups, migrations
from ..context import Block, Contribution, RetrievalSources, Slot
from ..runtime.defaults import core_providers
from ..runtime.slots import SLOTS, Provider, Runtime
from .catalog import HOOKS
from .channels import ChannelAdapter, Channels
from .defaults import DEFAULT_KEYS, Defaults
from .directives import Directive, Directives
from .disposables import Disposable, Scope
from .hooks import HookBus
from .manifest import Manifest, load_manifest
from .prechecks import Precheck, Prechecks
from .routes import ExtensionRoutes
from .triggers import TriggerAdapter, Triggers

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
LIFECYCLE_HOOKS = frozenset({"app.start", "app.stop", "ext.changed"})  # fired whether or not the extension is enabled: its handlers start and stop its own work


@dataclass
class RecapSection:
    """One extension's slice of the recap: `card` adds keys to GET /dashboard, `facts` to what the recap model reads."""
    id: str
    card: Callable[[], dict[str, Any]]
    facts: Callable[[], dict[str, Any]] | None = None


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
    publish: Callable[[str, Any], None]  # an app-topic event, from any thread
    widget_kinds: Callable[[], tuple[str, ...]] = lambda: ()  # the canvas widget kinds the loaded extensions declare
    owe_wake: Callable[[str, str, dict[str, Any]], None] = lambda *_: None  # (conversation id, text, wake record): the chat is owed a hidden turn
    record_job_run: Callable[[str, dict[str, Any], str, str | None], None] = lambda *_: None  # (run id, fire record, status, error): a job run that is not a chat turn, for the history, inbox and notifications
    preset_keys: Callable[[str], dict[str, Any]] | None = None  # a permission preset id -> the permission keys it sets (Presets.keys), for defaults.permissionPreset
    chat_settings: Callable[[str | None], dict[str, Any]] | None = None  # (conversation id) -> the settings with that chat's and its project's permission overrides; the global ones for None

    def __post_init__(self) -> None:
        if self.chat_settings is None:
            self.chat_settings = lambda _conversation_id: self.settings()


_generation = itertools.count(1)


def _import_extension(root: Path, scope: ExtensionScope) -> ModuleType:
    """Import `<root>/extension.py` into a package namespace of its own, so a reload never meets the previous code's
    modules. The namespace is dropped when the scope is disposed."""
    name = f"grain_ext_{scope.manifest.id.replace('-', '_')}_{next(_generation)}"
    spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
    spec.submodule_search_locations = [str(root)]
    sys.modules[name] = importlib.util.module_from_spec(spec)

    def drop() -> None:
        for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[module]
    scope.disposables.add(Disposable(drop))
    importlib.invalidate_caches()
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no stale .pyc on a same-second edit, no litter in the folder
    try:
        return importlib.import_module(f"{name}.extension")
    finally:
        sys.dont_write_bytecode = keep


class Services:
    """The services an extension declared under `requires`."""

    def __init__(self, declared: tuple[str, ...], permissions: tuple[str, ...], lookups: dict[str, Callable[[], Any]]) -> None:
        self._declared, self._permissions, self._lookups = declared, permissions, lookups

    def get(self, name: str) -> Any:
        if name not in self._declared:
            raise KeyError(f"service {name!r} is not declared under requires.services")
        if f"service:{name}" not in self._permissions:
            raise KeyError(f"service {name!r} needs the permission 'service:{name}'")
        return self._lookups[name]()


@dataclass
class ExtensionScope:
    """One loaded extension: its manifest, and everything it registered, undone together when it stops."""
    manifest: Manifest
    index: int
    _bus: HookBus
    retrieval: RetrievalSources
    channels: Channels
    triggers: Triggers
    prechecks: Prechecks
    directives: Directives
    runtime: Runtime
    module: Module | None = None  # set for an adapted Module
    root: Path | None = None  # the extension's folder, where data.migrations is looked up
    _ctx: ExtensionContext | None = None  # set for an extension loaded from its folder
    dev: bool = False  # loaded from a folder outside the package (`load_folder`), so it can be reloaded
    disposables: Scope = field(default_factory=Scope)
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    routers: list[APIRouter] = field(default_factory=list)
    recap_sections: list[RecapSection] = field(default_factory=list)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def log(self) -> logging.Logger:
        return logging.getLogger(f"{__package__}.{self.manifest.id}")

    @property
    def bus(self) -> HookBus:
        """The raw bus, for built-in code. A folder extension subscribes through `on`, which checks its manifest."""
        if self.dev:
            raise PermissionError(f"extension {self.manifest.id!r} subscribes through scope.on, not the bus")
        return self._bus

    @property
    def db(self) -> Database:
        assert self._ctx is not None
        if self.dev:
            self._require("db:own")
        return self._ctx.db

    @property
    def app(self) -> ExtensionContext:
        """The app's context, for built-in code. A folder extension reaches a service through `services`."""
        assert self._ctx is not None
        if self.dev:
            raise PermissionError(f"extension {self.manifest.id!r} reaches the app through scope.services, not scope.app")
        return self._ctx

    @property
    def defaults(self) -> Defaults:
        assert self._ctx is not None
        return Defaults(self._ctx.db, self._ctx.preset_keys)

    @property
    def services(self) -> Services:
        assert self._ctx is not None
        return Services(self.manifest.requires_services, self.manifest.permissions, self._ctx.services)

    def enabled(self) -> bool:
        if self.module:
            return self.module.enabled()
        if self.manifest.kind == "profile":
            return self.defaults.applied(self.manifest.id)
        if self.manifest.enabled == "always" and not self.dev:  # only a built-in decides it cannot be turned off
            return True
        if self._ctx is None:
            return self.manifest.enabled is not False
        return self.manifest.id not in self._ctx.hidden_views()

    @property
    def toggleable(self) -> bool:
        """Does `enabled` follow this extension's id in `hiddenViews`? Not for a module (its view decides), a profile (it is applied
        or not) or a built-in that is always on."""
        return self.module is None and self._ctx is not None and self.manifest.kind == "extension" and (self.manifest.enabled != "always" or self.dev)

    def migration_steps(self) -> list[tuple[int, str, migrations.Step]]:
        folder = self.manifest.data.get("migrations")
        return migrations.sql_steps(self.root / folder) if folder and self.root else []

    def _require(self, permission: str) -> None:
        if permission not in self.manifest.permissions:
            raise ValueError(f"extension {self.manifest.id!r} did not declare permission {permission!r}")

    def _while_enabled(self, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        """`fn` as the bus should call it: a disabled extension has no opinion. Sync stays sync, so the bus runs it where it ran."""
        if inspect.iscoroutinefunction(fn):
            async def guarded_async(arg: Any) -> Any:
                if self.enabled():
                    return await fn(arg)
            return guarded_async

        def guarded(arg: Any) -> Any:
            if self.enabled():
                return fn(arg)
        return guarded

    def require_enabled(self) -> None:
        """A dependency of the extension's routes: while it is disabled they answer 404."""
        if not self.enabled():
            raise HTTPException(404, "Not Found")

    def on(self, hook: str, fn: Callable[[Any], Any], *, priority: int | None = None, timeout_ms: int | None = None) -> Disposable:
        """Subscribe to a hook the manifest declares (the manifest check has already seen to its permissions); priority, timeout
        and failure policy default to its entry. Only the lifecycle hooks reach `fn` while the extension is disabled."""
        if hook not in self.manifest.hooks:
            raise ValueError(f"extension {self.manifest.id!r} did not declare hook {hook!r}")
        declared = self.manifest.hooks[hook]
        sub = self._bus.subscribe(
            hook, fn if hook in LIFECYCLE_HOOKS else self._while_enabled(fn), owner=self.manifest.id, order=self.index,
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

    def recap_section(self, section_id: str, *, card: Callable[[], dict[str, Any]],
                      facts: Callable[[], dict[str, Any]] | None = None) -> Disposable:
        """Fill the manifest's `recap.section` slot of that id while the extension is enabled; both functions must be cheap."""
        if section_id not in {s["id"] for s in self.manifest.contributes.get("slots", {}).get("recap.section", ())}:
            raise ValueError(f"extension {self.manifest.id!r} did not declare recap section {section_id!r}")
        section = RecapSection(section_id, card, facts)
        self.recap_sections.append(section)
        return self.disposables.add(Disposable(lambda: self.recap_sections.remove(section)))

    def emit_ui(self, name: str, data: Any) -> None:
        """Send the open windows an `ext:<id>:<name>` event; the extension's frontend half receives it."""
        self._ctx.publish(f"ext:{self.manifest.id}:{name}", data)

    def provide(self, service: str, impl: Any, *, id: str | None = None, label: str | None = None) -> Disposable:
        """Offer a service the manifest declares under provides.services, to the app and to extensions that require it. A dotted
        name is a runtime slot (`sandbox.backend`): `id` and `label` must match an entry of the manifest's contributes.runtime, the
        slot's permission must be declared, and only the user's settings can select the provider."""
        if "." in service:
            return self._provide_slot(service, impl, id, label)
        if service not in self.manifest.provides_services:
            raise ValueError(f"extension {self.manifest.id!r} did not declare service {service!r} under provides")
        services = self._ctx.services
        if service in services:
            raise ValueError(f"service {service!r} is already provided")
        services[service] = lambda: impl
        return self.disposables.add(Disposable(lambda: services.pop(service, None)))

    def _provide_slot(self, slot: str, impl: Any, provider_id: str | None, label: str | None) -> Disposable:
        if slot not in SLOTS:
            raise ValueError(f"{slot!r} is not a runtime slot")
        declared = next((e for e in self.manifest.contributes.get("runtime", ()) if e["slot"] == slot and e["id"] == provider_id), None)
        if declared is None:
            raise ValueError(f"extension {self.manifest.id!r} did not declare provider {provider_id!r} for {slot} under contributes.runtime")
        if label != declared["label"]:
            raise ValueError(f"provider {provider_id!r} is labelled {label!r}, its manifest entry says {declared['label']!r}")
        return self.disposables.add(self.runtime.attach(slot, f"{self.manifest.id}/{provider_id}", impl))

    def channel(self, adapter: ChannelAdapter) -> Disposable:
        """Offer a channel adapter: the app routes its chat, run changes and shutdown through it while the extension runs."""
        return self.disposables.add(self.channels.register(adapter, self.enabled))

    def trigger(self, kind: str, adapter: TriggerAdapter) -> Disposable:
        """Offer a scheduled-job kind: while the extension is enabled, the scheduler asks the adapter for a look when a job of
        that kind is due. Built-in only: the adapter is handed the scheduler and writes text into the run's prompt."""
        if self.dev:
            raise PermissionError(f"extension {self.manifest.id!r} cannot offer a trigger kind: only a built-in extension may")
        return self.disposables.add(self.triggers.register(kind, adapter, self.enabled))

    def precheck(self, fn: Precheck) -> Disposable:
        """Refuse a tool call before any approval card: `fn(name, args, ctx)` returns the refusal text or None. It cannot
        approve, and it is silent while the extension is disabled. Needs `hooks:gate`, like the `tool.pre` gate it mirrors."""
        self._require("hooks:gate")
        return self.disposables.add(self.prechecks.register(fn, self.enabled))

    def directive(self, fn: Directive, *, version: int) -> Disposable:
        """Add an unwrapped paragraph to the stable prompt prefix: `fn(settings)` returns the text (empty for none). Built-in
        only: the text is not marked as an extension's, so it is the app's own voice. Bump `version` when the text changes."""
        if self.dev:
            raise PermissionError(f"extension {self.manifest.id!r} cannot add a prompt directive: only a built-in extension may")
        return self.disposables.add(self.directives.register(self.manifest.id, fn, version, self.enabled))

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
        self._require("context:volatile" if slot == "volatile" else "prompt:inject")

        async def contribute(payload: dict[str, Any]) -> Block | None:
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
        await self._bus.emit("app.start", {}, owner=self.manifest.id)

    async def stop(self) -> None:
        try:
            try:
                await asyncio.wait_for(self._bus.emit("app.stop", {}, owner=self.manifest.id), STOP_BUDGET_S)
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
        self.channels = Channels()
        self.triggers = Triggers()
        self.prechecks = Prechecks()
        self.directives = Directives()
        self.runtime = Runtime(self.bus, core_providers())
        self.routes = ExtensionRoutes(self)  # mounted at /ext by the app: where folder-loaded extensions' routers are served
        self.extensions: dict[str, ExtensionScope] = {}  # in start order
        self.failed: dict[Path, str] = {}  # folders `load_dev` could not load, with the reason
        self._failed_reloads: set[Path] = set()  # folders whose reload failed: the user already chose their on/off state
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
        self._activate(scope, importlib.import_module(f"{__package__}.{ext_id}.extension"))
        return scope

    def load_folder(self, root: Path, ctx: ExtensionContext) -> ExtensionScope:
        """Load an extension from any folder holding extension.json and extension.py, in a fresh module namespace. If its
        manifest or `activate` is wrong, nothing stays registered."""
        manifest = load_manifest(root / "extension.json")
        if "service:chat" in manifest.permissions:
            raise ValueError("only a built-in extension may use the chat service: it can answer an approval")
        if builtin_only := sorted(h for h in manifest.hooks if HOOKS[h].subscribers == "builtin"):
            raise ValueError(f"only a built-in extension may subscribe to {', '.join(builtin_only)}")
        scope = self._add(manifest, root=root, ctx=ctx, dev=True)
        if manifest.kind == "profile":
            return scope  # a profile is data: nothing to import or activate
        try:
            self._activate(scope, _import_extension(root, scope))
        except BaseException:
            scope.disposables.dispose()
            del self.extensions[scope.manifest.id], self._enabled[scope.manifest.id]
            raise
        return scope

    async def load_dev(self, root: Path, ctx: ExtensionContext, *, first_load: bool = True) -> ExtensionScope:
        """Load and start the extension in `root` (the dev panel and the dev server). A folder extension starts disabled on
        a first load, not on a reload: the user turns it on. A failure is kept in `failed` and raised."""
        if loaded := next((s for s in self.extensions.values() if s.root == root), None):
            raise ValueError(f"{root} is already loaded as {loaded.manifest.id!r}")
        scope: ExtensionScope | None = None
        hide = False
        first_load = first_load and root not in self._failed_reloads
        try:
            scope = self.load_folder(root, ctx)
            if hide := first_load and scope.toggleable and scope.manifest.id not in ctx.hidden_views():
                ctx.set_settings({"hiddenViews": [*ctx.hidden_views(), scope.manifest.id]})
                self.refresh_enabled()
            self.migrate(ctx.db)
            await scope.start()
        except Exception as e:
            self.failed[root] = f"{type(e).__name__}: {e}"
            if scope:
                await self.unload(scope.manifest.id)
                if hide:  # a load that failed leaves nothing, not even the id in hiddenViews
                    ctx.set_settings({"hiddenViews": [v for v in ctx.hidden_views() if v != scope.manifest.id]})
            raise
        self.failed.pop(root, None)
        self._failed_reloads.discard(root)
        return scope

    async def unload(self, ext_id: str) -> None:
        """Stop an extension and forget it."""
        await self.extensions[ext_id].stop()
        del self.extensions[ext_id], self._enabled[ext_id]

    async def reload(self, ext_id: str) -> ExtensionScope:
        """Stop a folder-loaded extension and load it again from its folder: new code, same data."""
        old = self.extensions[ext_id]
        if not old.dev or old.root is None or old._ctx is None:
            raise ValueError(f"extension {ext_id!r} is not loaded from a folder, so it cannot be reloaded")
        await self.unload(ext_id)
        try:
            return await self.load_dev(old.root, old._ctx, first_load=False)
        except Exception:
            self._failed_reloads.add(old.root)
            raise

    @staticmethod
    def _activate(scope: ExtensionScope, module: ModuleType) -> None:
        module.activate(scope)
        declared = {t["name"]: (t.get("danger"), t.get("group")) for t in scope.manifest.contributes.get("tools", ())}
        registered = {name: (spec.danger, spec.group) for name, spec in scope.tools.items()}
        if registered != declared:
            raise ValueError(f"extension {scope.manifest.id!r} registered tools {registered}, its manifest declares {declared}")

    def recap_cards(self) -> dict[str, Any]:
        """The dashboard keys contributed by the enabled extensions' recap sections."""
        return {k: v for s in self._enabled_sections() for k, v in s.card().items()}

    def recap_facts(self) -> dict[str, Any]:
        """The recap facts contributed by the enabled extensions' recap sections."""
        return {k: v for s in self._enabled_sections() if s.facts for k, v in s.facts().items()}

    def _enabled_sections(self) -> Iterator[RecapSection]:
        return (sec for scope in self.extensions.values() if scope.enabled() for sec in scope.recap_sections)

    def _add(self, manifest: Manifest, module: Module | None = None, root: Path | None = None,
             ctx: ExtensionContext | None = None, dev: bool = False) -> ExtensionScope:
        if manifest.id in self.extensions:
            raise ValueError(f"duplicate extension id {manifest.id!r}")
        scope = ExtensionScope(manifest, len(self.extensions), self.bus, self.retrieval, self.channels, self.triggers, self.prechecks,
                               self.directives, self.runtime, module, root, ctx, dev)
        try:
            for entry in manifest.contributes.get("runtime", ()):  # listed while the extension is idle; selectable once it provides code
                scope.disposables.add(self.runtime.declare(Provider(
                    f"{manifest.id}/{entry['id']}", entry["slot"], entry["label"], "user" if dev else "built-in", manifest.id,
                    entry.get("meta", {}), enabled=scope.enabled)))
        except (ValueError, PermissionError):
            scope.disposables.dispose()
            raise
        self.extensions[manifest.id] = scope
        self._enabled[manifest.id] = scope.enabled()
        return scope

    def mount(self, app: FastAPI, scope: ExtensionScope) -> None:
        """Include a built-in extension's routers at their own prefixes; they answer 404 while the extension is disabled."""
        for router in scope.routers:
            app.include_router(router, dependencies=[Depends(scope.require_enabled)])

    def widget_kinds(self) -> tuple[str, ...]:
        """The canvas widget kinds the extensions declare, enabled or not: a turned-off extension keeps its windows."""
        return tuple(i["id"] for s in self.extensions.values() for i in s.manifest.contributes.get("slots", {}).get("canvas.widget", ()))

    def fire(self, hook: str, payload: Any = None) -> None:
        self.bus.fire(hook, payload)

    def refresh_enabled(self) -> None:
        """Fire ext.changed for each extension whose enabled state differs from the last one reported. An extension's
        contributes.defaults are applied when it turns on and reverted when it turns off (a profile does this itself).
        Those writes can flip an extension already visited, so it goes round again until nothing changes."""
        for _ in self.extensions:
            if not self._refresh_pass():
                return

    def _refresh_pass(self) -> bool:
        changed = False
        for ext_id, scope in self.extensions.items():
            if (now := scope.enabled()) != self._enabled[ext_id]:
                changed, self._enabled[ext_id] = True, now
                if scope._ctx and scope.manifest.kind == "extension":
                    db = scope._ctx.db
                    before = db.get_settings()
                    if now:
                        scope.defaults.apply(scope.manifest)
                    else:
                        scope.defaults.revert(ext_id)
                    self._settings_changed(before, db.get_settings())
                self.bus.fire("ext.changed", {"id": ext_id, "enabled": now})
        return changed

    def _settings_changed(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Fire settings.changed for the settings a defaults write touched."""
        keys = [k for k in DEFAULT_KEYS if before.get(k) != after.get(k)]
        if keys:
            self.bus.fire("settings.changed", {"keys": keys, "old": {k: before.get(k) for k in keys}, "new": {k: after.get(k) for k in keys}})

    # ---- data ----
    def migrate(self, db: Database) -> None:
        """Apply every extension's pending migrations, after a premigrate backup when there are any (not on a database this start created)."""
        fresh, db.fresh = db.fresh, False
        steps = {s.manifest.id: s.migration_steps() for s in self.extensions.values()}
        if not any(steps.values()):
            return  # nothing declares migrations: no connection to open
        with contextlib.closing(db.connect()) as c:
            todo = {ext_id: p for ext_id, st in steps.items() if (p := migrations.pending_extension(c, ext_id, st))}
            if not todo:
                return
            if not fresh:
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
        """The value as it may be stored for a declared extension setting, or ValueError with the reason. None unsets it."""
        kind = self.setting_type(key)
        if kind is None:
            raise ValueError(f"{key} is not a declared setting")
        ok = {"string": isinstance(value, str), "secret": isinstance(value, str),
              "boolean": isinstance(value, bool), "integer": isinstance(value, int) and not isinstance(value, bool),
              "number": isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)}[kind]
        if value is not None and not ok:
            raise ValueError(f"{key} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")
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
