"""The hook bus (design doc section 5): named points where an extension may observe, add, transform or narrow.

Four call shapes, chosen by the caller:
- emit:    concurrent, results ignored, a failing handler never reaches the caller
- collect: concurrent, results merged in priority order
- pipe:    sequential, each handler sees the previous value
- gate:    sequential, first opinion wins; a handler can only Deny or Ask, never allow

A handler takes one argument (the payload, or the current value for pipe) and may be sync (it then runs in a worker
thread) or async. Returning None means "no opinion". Handlers that raise or time out are logged and skipped, except
in a gate, where silence never means yes: the call turns into an approval question (or a denial, if the subscriber
asked for that), never an allow.
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .disposables import Disposable

log = logging.getLogger(__name__)

DEFAULT_PRIORITY = 50
MUTE_AFTER = 5  # consecutive emit failures before a handler is muted for the session
TIMEOUT_S = {"emit": 2.0, "collect": 1.0, "pipe": 1.0, "gate": 2.0}

HOOKS = frozenset({
    "app.start", "app.stop", "settings.changed", "chat.created", "chat.deleted", "project.created", "project.deleted",
    "ext.changed",
    "turn.accept", "turn.start", "input.attachment",
    "context.contribute",
    "model.select", "model.request", "model.response",
    "tools.offer", "tool.permission", "tool.pre", "tool.post", "tool.result", "tool.taint", "tool.error",
    "reply.complete", "turn.settled", "run.after", "subagent.start", "subagent.stop", "worker.report",
    "memory.before_write", "memory.written", "memory.deleted", "compaction.before", "compaction.after",
    "approval.requested", "approval.resolved",
    "job.fired", "job.done", "schedule.tick",
    "channel.inbound", "channel.outbound", "file.written", "review.accepted", "notify",
})


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class Ask:
    reason: str


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
    def _known(hook: str) -> None:
        if hook not in HOOKS:
            raise ValueError(f"unknown hook {hook!r}")

    async def _invoke(self, sub: Subscription, arg: Any, kind: str) -> Any:
        timeout = TIMEOUT_S[kind] if sub.timeout_s is None else sub.timeout_s
        return await asyncio.wait_for(sub.call(arg), timeout)

    async def _try(self, sub: Subscription, arg: Any, kind: str) -> tuple[bool, Any]:
        try:
            return True, await self._invoke(sub, arg, kind)
        except Exception:  # noqa: BLE001 - a handler is third-party to the code that fires the hook
            log.warning("hook %s from %s failed", sub.hook, sub.owner, exc_info=True)
            return False, None

    async def emit(self, hook: str, payload: Any = None) -> None:
        self._known(hook)
        subs = self._subs.get(hook)
        if not subs:
            return
        await asyncio.gather(*(self._emit_one(s, payload) for s in subs if not s.muted))

    async def _emit_one(self, sub: Subscription, payload: Any) -> None:
        ok, _ = await self._try(sub, payload, "emit")
        sub.failures = 0 if ok else sub.failures + 1
        if sub.failures >= MUTE_AFTER:
            sub.muted = True
            log.error("hook %s from %s muted after %d consecutive failures", sub.hook, sub.owner, sub.failures)

    async def collect(self, hook: str, payload: Any = None) -> list[Any]:
        self._known(hook)
        subs = self._subs.get(hook)
        if not subs:
            return []
        done = await asyncio.gather(*(self._try(s, payload, "collect") for s in subs))
        return [r for ok, r in done if ok and r is not None]

    async def pipe(self, hook: str, value: Any) -> Any:
        self._known(hook)
        for sub in self._subs.get(hook, ()):
            ok, out = await self._try(sub, value, "pipe")
            if ok and out is not None:
                value = out
        return value

    async def gate(self, hook: str, payload: Any = None) -> Deny | Ask | None:
        """None means no handler objected, not that the call is allowed: the permission stack still decides."""
        self._known(hook)
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
