"""The hook bus (design doc section 5): named points where an extension may observe, add, transform or narrow.

Four call shapes, chosen by the caller:
- emit:    concurrent, results ignored, a failing handler never reaches the caller
- collect: concurrent, results merged in priority order
- pipe:    sequential, each handler sees the previous value
- gate:    sequential, first opinion wins; a handler can only Deny or Ask, never allow

A handler takes one argument (the payload, or the current value for pipe) and may be sync (it then runs in a worker
thread) or async. Returning None means "no opinion". Code that is not a coroutine (a repository method, a usage
callback that runs on any thread) fires through emit_sync / pipe_sync instead. Handlers that raise or time out are logged and skipped, except
in a gate, where silence never means yes: the call turns into an approval question (or a denial, if the subscriber
asked for that), never an allow.

Every hook is cataloged in catalog.HOOKS with its kind, payload and return type. A call whose method does not match the
hook's kind is logged once; under GRAIN_HOOKS_STRICT=1 (the tests) it raises, and so does a payload or a handler's return
that does not match the catalog.
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Literal

from . import hookschema
from .disposables import Disposable

if TYPE_CHECKING:
    from .catalog import HookSpec

log = logging.getLogger(__name__)

DEFAULT_PRIORITY = 50
_CTX_KEYS = ("run_id", "conversation_id", "project_id", "agent_id", "desk_id")


def hook_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
    """The stable subset of a loop's tool context that a handler may read."""
    return {**{k: ctx.get(k) for k in _CTX_KEYS}, "tainted": bool(ctx.get("tainted"))}
MUTE_AFTER = 5  # consecutive emit failures before a handler is muted for the session
STATS_WINDOW = 200  # latencies kept per handler for the dev panel's p50 and p95
ERROR_WINDOW = 10  # failures kept per handler
TIMEOUT_S = {"emit": 2.0, "collect": 1.0, "pipe": 1.0, "gate": 2.0}
STRICT = os.environ.get("GRAIN_HOOKS_STRICT") == "1"
CALL_KIND = {"emit": "emit", "emit_sync": "emit", "fire": "emit", "collect": "collect", "pipe": "pipe", "pipe_sync": "pipe", "gate": "gate"}


class HookContractError(ValueError):
    """A hook was called by the wrong method, or a payload or a handler's return does not match the catalog (strict mode only for the last two)."""


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class Ask:
    reason: str


@dataclass(frozen=True)
class Cancel:
    """What a pipe handler returns to drop the value altogether (a message that must not be sent); the chain stops there."""


@dataclass(eq=False)
class Subscription:
    hook: str
    fn: Callable[[Any], Any]
    owner: str
    order: int  # position in load order: the tie-break after priority
    priority: int
    timeout_s: float | None
    on_failure: Literal["ask", "deny"]
    seq: int
    failures: int = field(default=0)
    muted: bool = field(default=False)
    calls: int = field(default=0)
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=STATS_WINDOW))  # seconds
    errors: deque[str] = field(default_factory=lambda: deque(maxlen=ERROR_WINDOW))

    def record(self, started: float, error: BaseException | None = None) -> None:
        self.calls += 1
        self.latencies.append(time.perf_counter() - started)
        if error is not None:
            self.errors.append(f"{type(error).__name__}: {error}" if str(error) else type(error).__name__)

    @property
    def sort_key(self) -> tuple[int, int, int]:
        return (self.priority, self.order, self.seq)

    async def call(self, arg: Any) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(arg)
        out = await asyncio.to_thread(self.fn, arg)
        return await out if inspect.isawaitable(out) else out  # a partial or callable object wrapping an async def


class HookBus:
    def __init__(self) -> None:
        self._subs: dict[str, tuple[Subscription, ...]] = {}
        self._seq = itertools.count()
        self.loop: asyncio.AbstractEventLoop | None = None  # where `start` runs coroutines; set while the app runs
        self._pending: set[asyncio.Task[None]] = set()
        self._misused: set[tuple[str, str]] = set()  # (hook, method) pairs already logged

    def subscribe(self, hook: str, fn: Callable[[Any], Any], *, owner: str, order: int,
                  priority: int | None = None, timeout_ms: int | None = None,
                  on_failure: Literal["ask", "deny"] = "ask") -> Disposable:
        self._known(hook)
        sub = Subscription(hook, fn, owner, order, DEFAULT_PRIORITY if priority is None else priority,
                           None if timeout_ms is None else timeout_ms / 1000, on_failure, next(self._seq))
        self._subs[hook] = tuple(sorted((*self._subs.get(hook, ()), sub), key=lambda s: s.sort_key))
        return Disposable(lambda: self._remove(sub))

    def _remove(self, sub: Subscription) -> None:
        left = tuple(s for s in self._subs.get(sub.hook, ()) if s is not sub)
        if left:
            self._subs[sub.hook] = left
        else:
            self._subs.pop(sub.hook, None)

    @staticmethod
    def _known(hook: str) -> HookSpec:
        from .catalog import HOOKS  # catalog imports this module, so it cannot be imported at the top
        if hook not in HOOKS:
            raise ValueError(f"unknown hook {hook!r}")
        return HOOKS[hook]

    def _enter(self, hook: str, method: str, payload: Any) -> None:
        """What every call does first: the hook must exist and be called the way its kind says; strict mode checks the payload."""
        spec = self._known(hook)
        if spec.kind != CALL_KIND[method]:
            message = f"{hook} is a {spec.kind} hook, called with {method}"
            if STRICT:
                raise HookContractError(message)
            if (hook, method) not in self._misused:
                self._misused.add((hook, method))
                log.error(message)
        if STRICT and (reason := hookschema.problem(spec.payload, spec.payload_of(payload) if spec.payload_of else payload)):
            raise HookContractError(f"payload of {hook}: {reason}")

    def _returned(self, hook: str, out: Any) -> None:
        """Strict mode: what a pipe, collect or gate handler returned must be what the catalog says (None is no opinion)."""
        spec = self._known(hook)
        if STRICT and out is not None and (reason := hookschema.problem(spec.returns, out)):
            raise HookContractError(f"return of a {hook} handler: {reason}")

    @staticmethod
    def _timeout(sub: Subscription, kind: str) -> float | None:
        """Core handlers were never time-limited; only an explicit budget bounds one."""
        if sub.timeout_s is not None:
            return sub.timeout_s
        return None if sub.owner == "core" else TIMEOUT_S[kind]

    async def _invoke(self, sub: Subscription, arg: Any, kind: str) -> Any:
        return await asyncio.wait_for(sub.call(arg), self._timeout(sub, kind))

    async def _try(self, sub: Subscription, arg: Any, kind: str) -> tuple[bool, Any]:
        started = time.perf_counter()
        try:
            out = await self._invoke(sub, arg, kind)
        except Exception as e:  # noqa: BLE001 - a handler is third-party to the code that fires the hook
            log.warning("hook %s from %s failed", sub.hook, sub.owner, exc_info=True)
            sub.record(started, e)
            return False, None
        sub.record(started)
        if kind != "emit":
            self._returned(sub.hook, out)
        return True, out

    def subscriptions(self, owner: str) -> list[Subscription]:
        """Everything one extension is subscribed with, for the dev panel."""
        return [s for subs in self._subs.values() for s in subs if s.owner == owner]

    def active(self, hook: str) -> bool:
        """Whether anything is subscribed, so a hot path can skip building a payload nobody will read."""
        self._known(hook)
        return hook in self._subs

    async def emit(self, hook: str, payload: Any = None, *, owner: str | None = None,
                   public: Callable[[Any], Any] | None = None) -> None:
        """`owner` limits the call to one extension's handlers, for the hooks the host fires one extension at a time.
        `public` builds what every handler but a core one receives in place of `payload`, once and only if there is one."""
        self._enter(hook, "emit", payload)
        subs = self._subs.get(hook)
        if not subs:
            return
        targets = [s for s in subs if not s.muted and owner in (None, s.owner)]
        shown = public(payload) if public is not None and any(s.owner != "core" for s in targets) else payload
        await asyncio.gather(*(self._emit_one(s, payload if s.owner == "core" else shown) for s in targets))

    async def _emit_one(self, sub: Subscription, payload: Any) -> None:
        ok, _ = await self._try(sub, payload, "emit")
        self._settle(sub, ok)

    @staticmethod
    def _settle(sub: Subscription, ok: bool) -> None:
        sub.failures = 0 if ok else sub.failures + 1
        if sub.failures >= MUTE_AFTER and sub.owner != "core":  # built-in work is retried every time, never switched off
            sub.muted = True
            log.error("hook %s from %s muted after %d consecutive failures", sub.hook, sub.owner, sub.failures)

    def _inline(self, sub: Subscription, arg: Any) -> tuple[bool, Any]:
        """Call a handler on the caller's thread. One that answers with an awaitable cannot be waited for here."""
        started = time.perf_counter()
        try:
            out = sub.fn(arg)
        except Exception as e:  # noqa: BLE001 - a handler is third-party to the code that fires the hook
            log.warning("hook %s from %s failed", sub.hook, sub.owner, exc_info=True)
            sub.record(started, e)
            return False, None
        if inspect.isawaitable(out):
            if inspect.iscoroutine(out):
                out.close()
            log.warning("hook %s from %s is async and cannot run inline from synchronous code; skipped", sub.hook, sub.owner)
            sub.record(started, RuntimeError("async handler cannot run inline from synchronous code"))
            return False, None
        sub.record(started)
        return True, out

    def emit_sync(self, hook: str, payload: Any = None) -> None:
        """emit for synchronous code on any thread. A sync handler has finished before this returns, in order and without
        a timeout, so it must be quick. An async one is started on `loop` and not waited for (dropped when there is none)."""
        self._enter(hook, "emit_sync", payload)
        for sub in self._subs.get(hook, ()):
            if sub.muted:
                continue
            if inspect.iscoroutinefunction(sub.fn):
                self.start(self._emit_one(sub, payload))
            else:
                self._settle(sub, self._inline(sub, payload)[0])

    def start(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a coroutine on `loop` from any thread without waiting for it. Before the loop is set (and after it is
        closed) nothing runs."""
        loop = self.loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        loop.call_soon_threadsafe(self._track, coro)

    def _track(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._pending.add(task)  # the loop keeps only a weak reference to a task
        task.add_done_callback(self._pending.discard)

    def fire(self, hook: str, payload: Any = None) -> None:
        """emit from any thread without waiting for the handlers: a sync route runs in the threadpool, and a slow
        handler must not hold up its response."""
        self._enter(hook, "fire", payload)
        self.start(self.emit(hook, payload))

    def close(self) -> None:
        """Cancel what `start` began and stop starting more."""
        self.loop = None
        for task in self._pending:
            task.cancel()

    async def collect(self, hook: str, payload: Any = None, *, skipped: list[str] | None = None) -> list[Any]:
        """`skipped` gains the owner of every handler that failed or timed out, for a caller that reports what was dropped."""
        self._enter(hook, "collect", payload)
        subs = self._subs.get(hook)
        if not subs:
            return []
        done = await asyncio.gather(*(self._try(s, payload, "collect") for s in subs))
        if skipped is not None:
            skipped.extend(s.owner for s, (ok, _) in zip(subs, done) if not ok)
        return [r for ok, r in done if ok and r is not None]

    async def pipe(self, hook: str, value: Any) -> Any:
        """Handlers run one after another, each on the previous result. A handler may return Cancel, which stops the chain
        and is returned as it is."""
        self._enter(hook, "pipe", value)
        for sub in self._subs.get(hook, ()):
            ok, out = await self._try(sub, value, "pipe")
            if ok and isinstance(out, Cancel):
                return out
            if ok and out is not None:
                value = out
        return value

    def pipe_sync(self, hook: str, value: Any) -> Any:
        """pipe for synchronous code: handlers run inline and must be sync (an async one is skipped). A handler may return
        Deny, which stops the chain and is returned as it is."""
        self._enter(hook, "pipe_sync", value)
        for sub in self._subs.get(hook, ()):
            if sub.muted:
                continue
            ok, out = self._inline(sub, value)
            self._settle(sub, ok)
            if ok:
                self._returned(hook, out)
            if isinstance(out, Deny):
                return out
            if ok and out is not None:
                value = out
        return value

    async def gate(self, hook: str, payload: Any = None) -> Deny | Ask | None:
        """None means no handler objected, not that the call is allowed: the permission stack still decides."""
        self._enter(hook, "gate", payload)
        for sub in self._subs.get(hook, ()):
            ok, out = await self._try(sub, payload, "gate")
            if ok and out is None:
                continue
            if ok and isinstance(out, (Deny, Ask)):
                return out
            log.warning("gate %s from %s %s", hook, sub.owner, "returned something other than Deny or Ask" if ok else "failed")
            reason = f"hook {hook} from {sub.owner} did not answer"
            return Deny(reason) if sub.on_failure == "deny" else Ask(reason)
        return None
