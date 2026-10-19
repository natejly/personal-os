"""Runtime slots: the core's swappable seams, each with a built-in default and alternatives extensions contribute
(design doc section 10.7).

An extension supplies a provider through `scope.provide("<area>.<name>", impl, id=, label=)`; it cannot select it. A
selection is the user's, stored per chat, project, profile and global, and `Runtime.resolve` is the one function every
consumer asks for the winner (chat over project over profile over global over the default). The invariant envelope
(permission stack, `dispatch_tool`, redaction, the journal, approvals) sits outside every slot, so no provider can
bypass it.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from ..extensions.disposables import Disposable
from ..extensions.hooks import HookBus
from ..osplat.base import DIMENSIONS, weaker
from . import contracts, loop_api, loop_fence, memory_api, protocols

log = logging.getLogger(__name__)

run_id: ContextVar[str | None] = ContextVar("runtime_run_id", default=None)  # the run a call belongs to, for hook payloads
USER_SLOTS = frozenset({"model.router", "model.client", "tools.source"})  # a user extension may not provide the others
GRANTED_SLOTS = frozenset({"agent.loop"})  # a user extension may provide these only when the user granted its runtime:<slot> permission
CLIENT_KINDS = frozenset({"chat", "complete", "embed", "image", "audio"})
_LOCALITY = {"remote": 0, "local": 1}
_STRICTEST: dict[str, Any] = {"model.client": "local", "sandbox.backend": {"workspace-write": dict.fromkeys(DIMENSIONS, "full")}}
ACCEPT_PARTIAL = "accept_partial"  # the global selection's record of what the user accepted a backend not to enforce


@dataclass(frozen=True)
class SlotDef:
    id: str
    protocol: type
    multi: bool          # tools.source only: several providers active at once
    default_id: str
    permission: str
    contract: Callable[[Any], Any]  # a suite any provider must pass (async; raises AssertionError)
    noun: str


SLOTS: dict[str, SlotDef] = {d.id: d for d in (
    SlotDef("model.client", protocols.ModelClient, False, "core/openai-compatible", "runtime:model", contracts.model_client, "model client"),
    SlotDef("model.router", protocols.ModelRouter, False, "core/heuristic", "runtime:router", contracts.model_router, "model router"),
    SlotDef("tools.source", protocols.ToolSource, True, "core/builtin", "runtime:tools", contracts.tool_source, "tool source"),
    SlotDef("sandbox.backend", protocols.SandboxBackend, False, "core/os-default", "runtime:sandbox", contracts.sandbox_backend, "sandbox backend"),
    SlotDef("agent.loop", loop_api.AgentLoop, False, "core/chat-loop", "runtime:loop", contracts.agent_loop, "agent loop"),
    SlotDef("memory.system", memory_api.MemorySystem, False, "core/memory", "runtime:memory", contracts.memory_system, "memory system"),
)}
PERMISSIONS = frozenset(d.permission for d in SLOTS.values())


@dataclass
class Provider:
    id: str                     # "core/<name>" or "<extension id>/<name>"
    slot: str
    label: str
    tier: str = "built-in"
    extension: str | None = None  # None for a core default
    meta: dict[str, Any] = field(default_factory=dict)
    impl: Any = None
    enabled: Callable[[], bool] = lambda: True
    core: bool = False

    @property
    def available(self) -> bool:
        return self.core or (self.impl is not None and self.enabled())


@dataclass(frozen=True)
class Scopes:
    """The stored selections one lookup reads, as `{slot: value}` per layer, and who is asking."""
    chat: dict[str, Any] = field(default_factory=dict)
    project: dict[str, Any] = field(default_factory=dict)
    profiles: tuple[tuple[str, dict[str, Any]], ...] = ()  # (profile id, selections) of the enabled profiles
    global_: dict[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None

    def layers(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """Highest first."""
        yield "chat", self.chat
        yield "project", self.project
        for profile_id, selections in self.profiles:
            yield f"profile:{profile_id}", selections
        yield "global", self.global_


@dataclass(frozen=True)
class Resolution:
    slot: str
    provider: str                       # the winner's id (for tools.source, the default; the sources enabled on top are `extra`)
    source: str                         # chat, project, profile:<id>, global or default
    reason: str
    default: bool                       # nothing but the built-in is in effect
    extra: tuple[str, ...] = ()
    fell_back_from: str | None = None
    refused: bool = False               # the selected provider is gone and the default is less safe: nothing may run

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "provider": self.provider, "extra": list(self.extra), "source": self.source, "reason": self.reason,
                "default": self.default, "fell_back_from": self.fell_back_from, "refused": self.refused}


def at_least_as_private(a: str, b: str) -> bool:
    """Is a model client of locality `a` no less private than one of locality `b`?"""
    return _LOCALITY[a] >= _LOCALITY[b]


def support_of(backend: Any) -> dict[str, Any]:
    """What a sandbox backend enforces; one that cannot say is judged as enforcing nothing."""
    try:
        return backend.support()
    except Exception:  # noqa: BLE001
        log.warning("sandbox backend %s failed to report its support", getattr(backend, "id", backend), exc_info=True)
        return {}


class Refused(Exception):
    """The selected provider is gone and the built-in default is less safe than it was, so nothing may run in its place."""


def _phrase(source: str) -> str:
    if source.startswith("profile:"):
        return f"the profile '{source.removeprefix('profile:')}' selects it"
    return {"chat": "this chat selects it", "project": "this project selects it", "global": "your Settings select it"}[source]


class Runtime:
    def __init__(self, bus: HookBus, core: Iterable[Provider]) -> None:
        self.bus = bus
        self.scopes: Callable[[], Scopes] = Scopes  # the app points this at the chat in the current context
        self.note: Callable[[str, str], None] = lambda title, body: None  # the app points this at the inbox: (title, body) of a selection that is gone
        self._providers: dict[str, dict[str, Provider]] = {slot: {} for slot in SLOTS}
        self._seen: dict[str, Any] = {}  # provider id -> its locality or support when last available: what a fallback is judged against
        self._announced: set[tuple[str, str, bool]] = set()
        self.router_failures: dict[str, int] = {}  # model.router provider id -> failures in a row (router.choose)
        for provider in core:
            if provider.slot == "agent.loop" and provider.impl is not None:
                loop_fence.check(provider.impl)
            self._providers[provider.slot][provider.id] = provider

    # ---- registration ----
    def declare(self, provider: Provider, granted: bool = False) -> Disposable:
        """List a provider an extension's manifest names; it can be selected only once `attach` gives it code."""
        if provider.slot not in SLOTS:
            raise ValueError(f"{provider.slot!r} is not a runtime slot")
        if provider.id in self._providers[provider.slot]:
            raise ValueError(f"{provider.slot} already has a provider {provider.id!r}")
        if provider.tier == "user" and provider.slot not in USER_SLOTS and not (granted and provider.slot in GRANTED_SLOTS):
            raise PermissionError(f"a user extension cannot provide {provider.slot}")
        self._providers[provider.slot][provider.id] = provider
        return Disposable(lambda: self._providers[provider.slot].pop(provider.id, None))

    def attach(self, slot: str, provider_id: str, impl: Any) -> Disposable:
        provider = self._providers.get(slot, {}).get(provider_id)
        if provider is None:
            raise ValueError(f"{provider_id!r} is not declared for {slot}")
        if not isinstance(impl, SLOTS[slot].protocol):
            raise ValueError(f"{provider_id!r} does not implement the {slot} interface")
        if slot == "model.client" and not set(impl.kinds) <= CLIENT_KINDS:
            raise ValueError(f"a model client's kinds are among {', '.join(sorted(CLIENT_KINDS))}")
        if slot == "agent.loop":
            loop_fence.check(impl)
        provider.impl = impl
        self._remember(provider)

        def detach() -> None:
            provider.impl = None
        return Disposable(detach)

    def providers(self, slot: str) -> list[Provider]:
        return list(self._providers[slot].values())

    def provider(self, slot: str, provider_id: str) -> Provider | None:
        return self._providers[slot].get(provider_id)

    def has_alternatives(self, slot: str) -> bool:
        """False when only the core's providers exist: the slot cannot resolve to anything else, so no lookup is needed."""
        return any(not p.core for p in self._providers[slot].values())

    # ---- resolution ----
    def resolve(self, slot: str, scopes: Scopes | None = None) -> Resolution:
        scopes = scopes or self.scopes()
        return self._multi(SLOTS[slot], scopes) if SLOTS[slot].multi else self._single(SLOTS[slot], scopes)

    def non_default(self, scopes: Scopes | None = None) -> list[Resolution]:
        """The slots where something other than the built-in provider is in effect (or was selected and is gone)."""
        return [r for r in self.resolve_all(scopes).values() if not r.default]

    def alternative(self, slot: str) -> Provider | None:
        """The provider of a single slot the current chat has selected when it is not the default, else None. Raises Refused
        when the selection is gone and the default is less safe, which is also the case once the last extension that provided it
        has been removed."""
        scopes = self.scopes()
        if not self.has_alternatives(slot) and not any(layer.get(slot) for _, layer in scopes.layers()):
            return None  # nothing but the core's providers, and nothing selected
        res = self.resolve(slot, scopes)
        if res.refused:
            raise Refused(res.reason)
        return None if res.provider == SLOTS[slot].default_id else self._providers[slot][res.provider]

    def accepted(self, provider_id: str) -> frozenset[str]:
        """The dimensions the user accepted a sandbox backend to enforce only partly (set in Settings, never by a profile or extension)."""
        return frozenset(self.scopes().global_.get(ACCEPT_PARTIAL, {}).get(provider_id, ()))

    def announce(self, slot: str, wanted: str, used: str | None, why: str, refused: bool, scopes: Scopes | None = None) -> None:
        """Tell subscribers a selection was not honoured (runtime.fallback)."""
        scopes = scopes or self.scopes()
        self.bus.emit_sync("runtime.fallback", {"slot": slot, "wanted": wanted, "used": used, "why": why, "refused": refused,
                                                "conversation_id": scopes.conversation_id, "run_id": scopes.run_id})

    def resolve_all(self, scopes: Scopes | None = None) -> dict[str, Resolution]:
        scopes = scopes or self.scopes()
        return {slot: self.resolve(slot, scopes) for slot in SLOTS}

    def _single(self, d: SlotDef, scopes: Scopes) -> Resolution:
        default = self._providers[d.id][d.default_id]
        wanted, source = next(((v, src) for src, layer in scopes.layers() if (v := layer.get(d.id)) is not None), (None, "default"))
        if wanted is None:
            return Resolution(d.id, d.default_id, source, f"Using {default.label} ({d.noun}) by default.", True)
        if wanted == d.default_id:
            return Resolution(d.id, d.default_id, source, f"Using {default.label} ({d.noun}) because {_phrase(source)}.", True)
        chosen = self._providers[d.id].get(wanted) if isinstance(wanted, str) else None
        if chosen is not None and chosen.available:
            self._remember(chosen)
            self._announced = {k for k in self._announced if k[:2] != (d.id, wanted)}
            return Resolution(d.id, chosen.id, source, f"Using {chosen.label} ({d.noun}) because {_phrase(source)}.", False)
        return self._fallback(d, default, str(wanted), scopes)

    def _fallback(self, d: SlotDef, default: Provider, wanted: str, scopes: Scopes) -> Resolution:
        """The selected provider is disabled, removed or unknown: the default stands in only when it is no less safe."""
        refused = not self._no_less_safe(d.id, default, wanted)
        why = (f"Selected '{wanted}' is not available and the default is less safe, so nothing was run." if refused
               else f"Selected '{wanted}' is not available; using the default.")
        if (key := (d.id, wanted, refused)) not in self._announced:
            self._announced.add(key)
            self.announce(d.id, wanted, None if refused else default.id, why, refused, scopes)
            self.note(f"A selected {d.noun} is not available", why)
        return Resolution(d.id, wanted if refused else default.id, "default", why, False, fell_back_from=wanted, refused=refused)

    def _multi(self, d: SlotDef, scopes: Scopes) -> Resolution:
        """tools.source: `{enable, disable}` lists merged from global up to chat; the defaults are never disableable."""
        on: list[str] = []
        source = "default"
        for src, layer in reversed(list(scopes.layers())):
            sel = layer.get(d.id) or {}
            if sel.get("enable") or sel.get("disable"):
                source = src
            on = [i for i in (*on, *sel.get("enable", ())) if i not in sel.get("disable", ())]
        extra = tuple(dict.fromkeys(i for i in on if (p := self._providers[d.id].get(i)) is not None and not p.core and p.available))
        missing = next((i for i in on if (p := self._providers[d.id].get(i)) is None or not (p.core or p.available)), None)
        gone = self._fallback(d, self._providers[d.id][d.default_id], missing, scopes) if missing else None  # said once, as for a single slot
        if not extra:
            return gone or Resolution(d.id, d.default_id, "default", f"Using the built-in tools and connectors ({d.noun}) by default.", True)
        names = ", ".join(self._providers[d.id][i].label for i in extra)
        return Resolution(d.id, d.default_id, source, f"Using {names} ({d.noun}) because {_phrase(source)}.", False, extra=extra)

    # ---- what a provider is, and whether swapping to it loosens anything ----
    def _remember(self, provider: Provider) -> None:
        if provider.impl is None:
            return
        if provider.slot == "model.client":
            self._seen[provider.id] = provider.impl.locality
        elif provider.slot == "sandbox.backend":
            self._seen[provider.id] = support_of(provider.impl)

    def traits(self, slot: str, provider_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """(locality, enforcement) of a provider, from its code while it is there and from the last report otherwise."""
        if (provider := self._providers[slot].get(provider_id)) is not None:
            self._remember(provider)
        seen = self._seen.get(provider_id)
        return (seen, None) if slot == "model.client" else (None, seen) if slot == "sandbox.backend" else (None, None)

    def _no_less_safe(self, slot: str, default: Provider, missing_id: str) -> bool:
        if slot not in ("model.client", "sandbox.backend"):
            return True
        was = self._seen.get(missing_id, _STRICTEST[slot])  # never reported (a restart): judged as the strictest, so only a default as safe stands in
        now = self.traits(slot, default.id)[0 if slot == "model.client" else 1]
        return at_least_as_private(now, was) if slot == "model.client" else not weaker(now or {}, was)

    def loosening(self, slot: str, was: Resolution, wanted: Any) -> list[str]:
        """What choosing `wanted` (a provider id, or for tools.source an `{enable, disable}` object) would loosen compared with
        the resolution in effect: empty when it is equal or stricter. The user sees these lines before confirming."""
        if slot == "tools.source":
            added = [i for i in (wanted or {}).get("enable", ()) if i not in was.extra]
            return [f"{p.label} adds its tools" for i in added if (p := self._providers[slot].get(i)) and not p.core]  # a core source is always on
        new = self._providers[slot].get(wanted) if isinstance(wanted, str) else None
        if new is None or not new.available:
            return []
        (locality, enforcement), (was_locality, was_enforcement) = self.traits(slot, new.id), self.traits(slot, was.provider)
        if slot == "model.client" and locality == "remote" and was_locality == "local":
            return [f"{new.label} is remote and {self._providers[slot][was.provider].label} is local: your prompts would leave this computer"]
        if slot == "sandbox.backend":
            return [f"{new.label} enforces less: {line}" for line in weaker(enforcement or {}, was_enforcement or {})]
        return []

    # ---- tests ----
    async def test(self, slot: str, provider_id: str) -> dict[str, Any]:
        """Run the slot's contract suite against a provider: {ok, detail}. KeyError for an unknown provider, ValueError for a
        built-in one, which has no code behind it."""
        provider = self._providers[slot][provider_id]
        if provider.impl is None:
            raise ValueError("This provider has no code to test.")
        try:
            await SLOTS[slot].contract(provider.impl)
        except Exception as e:  # noqa: BLE001 - any failure of the provider is the answer
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"[:300]}
        return {"ok": True, "detail": "The provider passed the contract suite."}
