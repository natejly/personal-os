"""The tool-path seam (design doc 5.0 and 5.5): every tool call, whichever loop makes it, passes through here.

dispatch_tool wraps Toolbox.call, or a connector call a loop hands it. The core guards (guards.py) run first, so no
extension hook sees a call the core refuses; then it fires the tool hooks around the call:
tool.pre (a gate), then tool.result (a pipe over the whole result, so every channel a loop later splits out of it is
covered), then tool.error, tool.post and tool.taint (emits). Core redaction runs on whatever the pipe returns.
Callers that journal a call put the seam inside the journal, so a replayed result is the one the hooks produced.
"""
from __future__ import annotations

import copy
import time
from typing import Any, Awaitable, Callable

from . import guards, redact
from .extensions.hooks import Ask, Deny, HookBus, hook_ctx
from .tools import Toolbox, scrub_strings, denied, summarize_result, tool_error

def _refused(name: str, verdict: Deny | Ask) -> dict[str, Any]:
    # The seam sits below each loop's approval wait, so it cannot open a card: an Ask stops the call and tells the
    # model to ask the user, which is the closest thing to asking that never lets the call through.
    if isinstance(verdict, Deny):
        return denied(name, guards.blocked_by_extension(verdict))
    return tool_error(f"{name} needs the user's approval before it runs ({verdict.reason}). It was not run.",
                      alternative="ask the user whether to go ahead")


async def _observe(bus: HookBus, name: str, args: dict[str, Any], result: Any, error: str | None, started: float, call_id: str,
                   run_id: str | None) -> None:
    if error and bus.active("tool.error"):
        await bus.emit("tool.error", {"name": name, "call_id": call_id, "run_id": run_id, "error": redact.scrub_command_output(error)})
    if bus.active("tool.post"):
        await bus.emit("tool.post", {"name": name, "call_id": call_id, "run_id": run_id, "args": scrub_strings(args),
                                     "result_preview": redact.scrub_command_output(summarize_result(result)),
                                     "error": redact.scrub_command_output(error) if error else None,
                                     "duration_ms": int((time.monotonic() - started) * 1000)})


class Contact:
    """A connector call that remembers whether it was made. tool.pre can refuse a call before it reaches the connector,
    and only a call that did reach it brings back third-party text, so a loop taints on `reached`, not on having tried."""

    def __init__(self, call: Callable[[], Awaitable[Any]]) -> None:
        self._call = call
        self.reached = False

    async def __call__(self) -> Any:
        self.reached = True
        return await self._call()


async def dispatch_tool(toolbox: Toolbox, ctx: dict[str, Any], name: str, args: dict[str, Any], *, call_id: str,
                        connector: Callable[[], Awaitable[Any]] | None = None) -> Any:
    """Run one tool call under the hooks. `connector` is the call to make for an MCP tool (Toolbox.call otherwise);
    its text taints the run, which the calling loop records itself."""
    bus = toolbox.bus
    if refusal := guards.run_core_guards(toolbox, ctx, name, args):
        return refusal.result(name)
    if bus.active("tool.pre") and (verdict := await bus.gate(
            "tool.pre", {"name": name, "args": copy.deepcopy(args), "call_id": call_id, "ctx": hook_ctx(ctx)})):
        return _refused(name, verdict)
    sources_before = len(ctx.get("taint_sources") or ())
    started = time.monotonic()
    try:
        result = await (connector() if connector is not None else toolbox.call(name, args, ctx))
    except Exception as e:
        await _observe(bus, name, args, None, f"{type(e).__name__}: {e}", started, call_id, ctx.get("run_id"))
        raise
    if bus.active("tool.result"):
        piped = await bus.pipe("tool.result", {"name": name, "call_id": call_id, "run_id": ctx.get("run_id"), "result": copy.deepcopy(result),
                                               "tainted": connector is not None or bool(ctx.get("tainted"))})
        if isinstance(piped, dict) and "result" in piped:
            result = piped["result"]
    error = result.get("error") if isinstance(result, dict) else None
    await _observe(bus, name, args, result, str(error) if error else None, started, call_id, ctx.get("run_id"))
    if bus.active("tool.taint"):
        sources = [name] if connector is not None else list(ctx.get("taint_sources") or ())[sources_before:]
        for source in sources:
            await bus.emit("tool.taint", {"name": name, "source": source})
    return result
