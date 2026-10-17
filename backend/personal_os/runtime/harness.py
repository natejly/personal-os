"""The objects a non-default agent loop is handed (design doc section 10.10.2), and the pump that carries its events to the reply.

Each object holds only the closure the core gave it, in a private slot: no public attribute leads to the toolbox, the tool
context, the database, the hook bus, the run store, the settings store or the model client, and the only method on `Tools` runs a
call through the whole gate chain. The core builds them around the turn it already set up (`app._chat_stream`).
"""
from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence

from ..extensions.catalog import LoopPlanPayload, LoopVerifyPayload
from ..extensions.hooks import HookBus
from ..tools import scrub_strings
from .loop_api import AgentLoop, LimitReached, LoopEvent, LoopHarness, Messages, ToolCall, ToolMessage, TurnEnded, TurnInput

LOOP_HOOKS = {"loop.plan": LoopPlanPayload, "loop.verify": LoopVerifyPayload}  # the only hooks a loop may fire


class LoopViolation(Exception):
    """A loop ran a tool without going through the tool step."""
    kind = "loop_violation"


class ModelCalls:
    """The turn's model calls, counted by the object that makes them. The loop can read the count and cannot change it."""

    def __init__(self, ceiling: int) -> None:
        self.ceiling, self.made = ceiling, 0

    def spend(self) -> None:
        if self.made >= self.ceiling:
            raise LimitReached(f"this turn reached its {self.ceiling} model calls")
        self.made += 1


class _Sealed:
    """A harness object: filled in once by the core and read-only after, so a loop cannot swap the closure behind it. Python hides
    nothing, so this is not the security boundary; the import fence, the built-in tier and the journal check are."""
    __slots__ = ()

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is read-only")

    def _fill(self, **values: Any) -> None:
        for name, value in values.items():
            object.__setattr__(self, name, value)


class ContextBuilder(_Sealed):
    __slots__ = ("_build",)

    def __init__(self, build: Callable[[Sequence[str]], Messages]) -> None:
        self._fill(_build=build)

    def build(self, extra_tail: Sequence[str] = ()) -> Messages:
        return self._build(tuple(extra_tail))


class ModelStream(_Sealed):
    __slots__ = ("_stream",)

    def __init__(self, stream: Callable[[Messages, bool], AsyncIterator[dict[str, Any]]]) -> None:
        self._fill(_stream=stream)

    def stream(self, messages: Messages, *, tools: bool) -> AsyncIterator[dict[str, Any]]:
        return self._stream(messages, tools)


class Tools(_Sealed):
    __slots__ = ("_run",)

    def __init__(self, run: Callable[[list[ToolCall]], Awaitable[list[ToolMessage]]]) -> None:
        self._fill(_run=run)

    async def run(self, calls: Sequence[ToolCall]) -> list[ToolMessage]:
        return await self._run(list(calls))


class EventSink(_Sealed):
    __slots__ = ("_emit",)

    def __init__(self, emit: Callable[[LoopEvent], None]) -> None:
        self._fill(_emit=emit)

    def emit(self, event: LoopEvent) -> None:
        self._emit(event)


class LoopHooks(_Sealed):
    __slots__ = ("_bus", "_ids")

    def __init__(self, bus: HookBus, run_id: str | None, conversation_id: str) -> None:
        self._fill(_bus=bus, _ids={"run_id": run_id, "conversation_id": conversation_id})

    async def emit(self, hook: str, payload: Mapping[str, Any]) -> None:
        if hook not in LOOP_HOOKS or set(payload) != set(LOOP_HOOKS[hook].__annotations__):
            raise ValueError(f"a loop fires {' or '.join(LOOP_HOOKS)}, each with the keys of its payload")
        full = scrub_strings({**payload, **self._ids})  # the ids are the core's, whatever the loop wrote
        if hook == "loop.plan":  # the catalog scan reads literal hook ids, so each is spelled out
            await self._bus.emit("loop.plan", full)  # hook: loop.plan
        else:
            await self._bus.emit("loop.verify", full)  # hook: loop.verify


class TurnLimits(_Sealed):
    """The core's clocks and counters for the turn. The round bounds are the loop's declaration, read once when the turn starts."""
    __slots__ = ("_bounds", "_rounds", "_calls", "_stopped", "_steers")

    def __init__(self, bounds: Mapping[str, int], calls: ModelCalls, stopped: Callable[[], bool],
                 steers: Callable[[], Awaitable[list[str]]]) -> None:
        self._fill(_bounds=MappingProxyType(dict(bounds)), _rounds=dict.fromkeys(bounds, 0), _calls=calls, _stopped=stopped, _steers=steers)

    @property
    def stopped(self) -> bool:
        return self._stopped()

    def next_round(self, scope: str) -> int:
        if scope not in self._bounds:
            raise KeyError(f"{scope!r} is not a round scope this loop declared")
        if self._rounds[scope] >= self._bounds[scope]:
            raise LimitReached(f"{scope} rounds are spent ({self._bounds[scope]})")
        self._rounds[scope] += 1
        return self._rounds[scope]

    def restart(self, scope: str) -> None:
        self._rounds[scope] = 0

    async def take_steers(self) -> list[str]:
        return await self._steers()


class LoopRun:
    """One non-default loop's turn. The loop's events and the events the tool and model objects publish meet in one queue, in order,
    so a card reaches the UI while the loop is still waiting on its answer."""

    def __init__(self, loop: AgentLoop, translate: Callable[[LoopEvent], tuple[str, Any]]) -> None:
        self._loop, self._translate = loop, translate
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def publish(self, item: tuple[str, Any]) -> None:
        """An event the core objects already shaped for the reply."""
        self._queue.put_nowait(("event", item))

    def emit(self, event: LoopEvent) -> None:
        """An event the loop emitted: checked and shaped by the core first."""
        self.publish(self._translate(event))

    async def events(self, turn: TurnInput, harness: LoopHarness) -> AsyncIterator[tuple[str, Any]]:
        async def feed() -> None:
            try:
                async for event in self._loop.run(turn, harness):
                    self.emit(event)
            except TurnEnded:
                pass
            except asyncio.CancelledError:
                raise
            except BaseException as e:  # noqa: BLE001 - re-raised in the reply's own task below
                self._queue.put_nowait(("error", e))
            self._queue.put_nowait(("end", None))

        task = asyncio.ensure_future(feed())
        try:
            while (kind := (item := await self._queue.get())[0]) != "end":
                if kind == "error":
                    raise item[1]
                yield item[1]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
