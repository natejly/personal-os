"""Agent loops for the tests. Each imports only the loop API, as a registered loop must, so the registry accepts them.

`Spy` keeps what it was handed; `Hostile` tries, one at a time, everything a loop is supposed not to be able to do and records how
each attempt ended in `SEEN["attacks"]`; `Greedy` makes more model calls than the turn allows; `Idle` does nothing.
"""
from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, AsyncIterator

from personal_os.runtime.loop_api import LimitReached, LoopEvent, LoopHarness, TurnInput

SEEN: dict[str, Any] = {}
CORE_TYPES = {"Toolbox", "Database", "HookBus", "RunStore", "Run", "Runtime", "ExtensionHost", "ToolStep", "TurnState"}


def call(name: str, cid: str = "c1", **args: Any) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


async def model_text(h: LoopHarness, messages: list[dict[str, Any]], tools: bool = True) -> dict[str, Any]:
    end: dict[str, Any] = {}
    async for ev in h.model.stream(messages, tools=tools):
        if ev["type"] == "end":
            end = ev
    return end


class Idle:
    id = "test/idle"
    bounds = MappingProxyType({"round": 1})

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        yield "delta", {"text": "idle"}


class Spy:
    """Asks for its context, one model call and one tool call, and keeps the harness for the test to look at."""
    id = "test/spy"
    bounds = MappingProxyType({"round": 2})

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        SEEN["harness"], SEEN["turn"] = h, turn
        messages = h.context.build(["a block for the tail"])
        SEEN["context"] = [dict(m) for m in messages]
        end = await model_text(h, messages)
        SEEN["end"] = end
        if end["tool_calls"]:
            SEEN["tool_messages"] = await h.tools.run(end["tool_calls"])
        yield "delta", {"text": "spy"}


class Greedy:
    """Calls the model until the core stops it."""
    id = "test/greedy"
    bounds = MappingProxyType({"round": 1})

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        messages = h.context.build([])
        try:
            while True:
                await model_text(h, messages, tools=False)
        except LimitReached as e:
            SEEN["greedy"] = (str(e), h.limits.model_calls)
        yield "delta", {"text": "stopped"}


class Hostile:
    id = "test/hostile"
    bounds = MappingProxyType({"round": 2})

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        attacks: dict[str, Any] = SEEN.setdefault("attacks", {})

        def attempt(name: str, fn: Any) -> None:
            try:
                attacks[name] = ("got", fn())
            except BaseException as e:  # noqa: BLE001 - how each attempt ended is what the test reads
                attacks[name] = ("refused", type(e).__name__)

        attempt("globals_hold_a_core_object", lambda: any(type(v).__name__ in CORE_TYPES for v in globals().values()))
        attempt("tools_dict", lambda: h.tools.__dict__)
        attempt("tools_vars", lambda: vars(h.tools))
        attempt("rebind_tools", lambda: setattr(h.tools, "run", None))
        attempt("rebind_bounds", lambda: setattr(h.limits, "_bounds", {"round": 10**6}))
        attempt("round_bound_argument", lambda: h.limits.next_round("round", bound=99))
        attempt("round_unknown_scope", lambda: h.limits.next_round("other"))
        attempt("public_surface", lambda: {n: sorted(a for a in dir(getattr(h, n)) if not a.startswith("_")) for n in
                                           ("context", "model", "tools", "events", "hooks", "limits")})
        attempt("event_outside_the_vocabulary", lambda: h.events.emit(("done", {})))
        attempt("tool_event_forged", lambda: h.events.emit(("tool_result", {})))
        messages = h.context.build(["text for the tail"])
        attacks["prefix"] = messages[0]["content"]
        attacks["tail_last"] = messages[-1]["content"]
        try:
            await h.hooks.emit("tool.pre", {})
        except ValueError as e:
            attacks["other_hook"] = ("refused", type(e).__name__)
        for n in range(3):
            try:
                h.limits.next_round("round")
            except LimitReached:
                attacks["round_bound"] = ("refused", "LimitReached", n)
                break
        end = await model_text(h, messages)
        attacks["offered_end"] = end["finish_reason"]
        if end["tool_calls"]:
            attacks["tool_messages"] = await h.tools.run(end["tool_calls"])
        yield "delta", {"text": "hostile"}
