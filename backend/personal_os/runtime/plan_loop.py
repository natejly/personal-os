"""Plan, execute, verify: the alternative agent loop (design doc section 10.10.4).

One bounded plan call, each step of the plan worked through with tools, one verification, and a final answer that says what was
not verified when the check failed twice. It reaches tools, context, approvals and the model only through the harness it is
handed, so every call is judged exactly as the chat judges it; the plan is a steering aid shown in the progress panel and approves
nothing. The rounds it may take per scope are declared in `bounds`; the core counts them and caps the turn's model calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, AsyncIterator

from .loop_api import LimitReached, LoopEvent, LoopHarness, Messages, TurnInput

MAX_STEPS = 8
PREVIEW_CHARS = 200
SINGLE_STEP = {"title": "Do the task", "done_when": "the request is answered"}

PLAN_TAIL = ("Before doing anything, plan the work. Reply with JSON only, no tool calls and no other text: "
             '{"steps": [{"title": "...", "done_when": "..."}]} with at most 8 steps. Each has a short title and one sentence '
             "saying how to tell it is done. Nothing in the plan is carried out or approved by being listed.")
STEP_TAIL = ("You are carrying out step {n} of {total} of the plan: {title}\nIt is done when: {done_when}\n"
             "Work on this step only. Call tools as needed; when the step is done, say so in one sentence and call no tool.")
REPAIR_TAIL = "These gaps were found when the work was checked:\n{gaps}\nClose them: call the tools that are needed, then say in one sentence what you did."
VERIFY_TAIL = ('Check the work against the plan. Reply with JSON only: {{"ok": true or false, "gaps": ["..."]}}. '
               "`gaps` lists what is missing, wrong or unproven; it is empty when ok.\n\n{record}")
ANSWER_OK_TAIL = ("The work was checked and nothing is missing. Write the final answer for the user, and say so if any call was "
                  "refused or failed along the way.")
ANSWER_UNVERIFIED_TAIL = ("The work could not be fully verified. Write the final answer for the user. Say plainly what is not verified or not "
                          "done, and do not say the task is complete.\nNot verified:\n{gaps}")


@dataclass
class _Call:
    """What one model call came back with."""
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Work:
    """What the turn has done so far, for the verification and the final answer."""
    steps: list[dict[str, str]] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    notes: list[list[str]] = field(default_factory=list)  # per step: which tools it called and whether each worked; never their output, which is data and would ride in a system message
    said: list[str] = field(default_factory=list)  # per step: what the model said when it finished the step
    gaps: list[str] = field(default_factory=list)  # calls that were refused or failed

    def record(self) -> str:
        lines = []
        for i, step in enumerate(self.steps):
            lines.append(f"{i + 1}. {step['title']} (done when: {step['done_when']}) [{self.status[i]}]")
            lines += [f"   - {note}" for note in self.notes[i]]
            if self.said[i]:
                lines.append(f"   said: {self.said[i]}")
        lines.append("Calls that were refused or failed:" + ("\n" + "\n".join(f"- {g}" for g in self.gaps) if self.gaps else " none"))
        return "\n".join(lines)


def _json_object(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None


def _plan_problem(data: Any) -> str | None:
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not steps:
        return 'it must be {"steps": [...]} with at least one step'
    if len(steps) > MAX_STEPS:
        return f"it has {len(steps)} steps and the most is {MAX_STEPS}"
    for step in steps:
        if not (isinstance(step, dict) and all(isinstance(step.get(k), str) and step[k].strip() for k in ("title", "done_when"))):
            return "each step needs a title and done_when, both text"
    return None


def _verdict(data: Any) -> tuple[bool, list[str]]:
    """(ok, gaps) from the verification reply; one that cannot be read is a failed check, never a pass."""
    if isinstance(data, dict) and isinstance(data.get("ok"), bool) and isinstance(data.get("gaps", []), list):
        gaps = [str(g)[:300] for g in data.get("gaps", []) if str(g).strip()]
        return data["ok"] and not gaps, gaps
    return False, ["the verification reply could not be read"]


def _plan_event(work: _Work) -> LoopEvent:
    return "plan", {"steps": [{"text": s["title"], "note": s["done_when"], "status": st} for s, st in zip(work.steps, work.status)]}


class PlanExecuteVerify:
    id = "core/plan-execute-verify"
    bounds = MappingProxyType({"plan": 2, "execute": 6, "verify": 2})  # a plan call and one repair; rounds per step; verifications

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        work = _Work()
        try:
            async for event in self._turn(turn, h, work):
                yield event
        except LimitReached as e:
            yield "delta", {"text": f"I stopped here: {e}. What was done so far:\n{work.record()}"}

    async def _turn(self, turn: TurnInput, h: LoopHarness, work: _Work) -> AsyncIterator[LoopEvent]:
        yield "status", {"text": "Planning the work"}
        yield "span", {"name": "plan", "open": True, "meta": {}}
        work.steps = await self._plan(h)
        work.status, work.notes, work.said = ["pending"] * len(work.steps), [[] for _ in work.steps], [""] * len(work.steps)
        await h.hooks.emit("loop.plan", {"run_id": turn.run_id, "conversation_id": turn.conversation_id, "steps": [dict(s) for s in work.steps]})
        yield _plan_event(work)
        yield "span", {"name": "plan", "open": False, "meta": {"steps": len(work.steps)}}
        for i in range(len(work.steps)):
            if h.limits.stopped:
                return
            yield "status", {"text": f"Step {i + 1} of {len(work.steps)}: {work.steps[i]['title']}"}
            async for event in self._step(h, work, i, STEP_TAIL.format(n=i + 1, total=len(work.steps), **work.steps[i])):
                yield event
        ok, gaps = False, []
        while not h.limits.stopped:
            attempt = h.limits.next_round("verify")
            yield "status", {"text": "Checking the work"}
            yield "span", {"name": "verify", "open": True, "meta": {"attempt": attempt}}
            ok, gaps = await self._verify(h, work)
            await h.hooks.emit("loop.verify", {"run_id": turn.run_id, "conversation_id": turn.conversation_id, "attempt": attempt, "ok": ok, "gaps": gaps})
            yield "span", {"name": "verify", "open": False, "meta": {"ok": ok, "gaps": len(gaps)}}
            if ok or attempt >= self.bounds["verify"]:
                break
            async for event in self._step(h, work, len(work.steps) - 1, REPAIR_TAIL.format(gaps="\n".join(f"- {g}" for g in gaps)), repair=True):
                yield event
        if h.limits.stopped:
            return
        tail = ANSWER_OK_TAIL if ok else ANSWER_UNVERIFIED_TAIL.format(gaps="\n".join(f"- {g}" for g in gaps) or "- the check did not finish")
        async for event in self._call(h, h.context.build([tail, work.record()]), _Call(), tools=False, forward=True):
            yield event

    async def _plan(self, h: LoopHarness) -> list[dict[str, str]]:
        tail, problem = [PLAN_TAIL], ""
        for _ in range(self.bounds["plan"]):
            h.limits.next_round("plan")
            out = _Call()
            async for _event in self._call(h, h.context.build(tail), out, tools=False):
                pass
            data = _json_object(out.text)
            if not (problem := _plan_problem(data) or ""):
                return [{"title": s["title"].strip(), "done_when": s["done_when"].strip()} for s in data["steps"]]
            tail = [PLAN_TAIL, f"Your last reply was not a usable plan: {problem}. Reply with the JSON only."]
        return [dict(SINGLE_STEP)]

    async def _step(self, h: LoopHarness, work: _Work, index: int, tail: str, repair: bool = False) -> AsyncIterator[LoopEvent]:
        """Work one step for up to the declared rounds; a round that asks for no tool ends it. A repair pass works the gaps the check found
        and leaves the plan's progress alone."""
        if not repair:
            work.status[index] = "in_progress"
            yield _plan_event(work)
        yield "span", {"name": "execute", "open": True, "meta": {"step": index + 1, "repair": repair}}
        h.limits.restart("execute")
        messages = h.context.build([tail])
        while not h.limits.stopped:
            try:
                h.limits.next_round("execute")
            except LimitReached:
                break
            out = _Call()
            async for event in self._call(h, messages, out, tools=True):
                yield event
            if h.limits.stopped:
                return
            if not out.tool_calls:
                work.said[index] = out.text.strip()[:PREVIEW_CHARS]
                break
            names = {c["id"]: c["name"] for c in out.tool_calls}
            for message in await h.tools.run(out.tool_calls):
                content, name = str(message.get("content") or ""), names.get(message.get("tool_call_id"), "a tool")
                failed = bool(_error_of(content))
                work.notes[index].append(f"{name}: {'failed' if failed else 'ran'}")
                if failed:
                    work.gaps.append(f"{name} failed")
            await h.limits.take_steers()
        if not repair:
            work.status[index] = "done"
            yield _plan_event(work)
        yield "span", {"name": "execute", "open": False, "meta": {"step": index + 1}}

    async def _verify(self, h: LoopHarness, work: _Work) -> tuple[bool, list[str]]:
        out = _Call()
        async for _event in self._call(h, h.context.build([VERIFY_TAIL.format(record=work.record())]), out, tools=False):
            pass
        return _verdict(_json_object(out.text))

    async def _call(self, h: LoopHarness, messages: Messages, out: _Call, *, tools: bool, forward: bool = False) -> AsyncIterator[LoopEvent]:
        """One model call. Its text is kept in `out`; with `forward` it also streams to the reader as the answer. A read a steer cut short
        is asked again once the steer is folded in."""
        while True:
            finish = None
            async for event in h.model.stream(messages, tools=tools):
                if event["type"] == "delta" and forward:
                    yield "delta", {"text": event["text"]}
                elif event["type"] == "end":
                    out.text, out.tool_calls, finish = event["text"], list(event["tool_calls"]), event["finish_reason"]
            if finish != "cancelled" or h.limits.stopped:
                return
            await h.limits.take_steers()


def _error_of(content: str) -> str:
    """The error a tool message reports, or ''."""
    try:
        data = json.loads(content)
    except ValueError:
        return ""
    return str(data.get("error") or "") if isinstance(data, dict) else ""
