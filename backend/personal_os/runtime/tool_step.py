"""The chat loop's tool step (design doc section 10.10.1): everything that happens between the model asking for tools and the
tool messages going back to it. For each call of a round, in order: the invalid-call check, the plan and propose blocks, the core
guards, permission rules, the floor, the safety reviewer, the approval card and its wait, the idempotency journal, `dispatch_tool`,
result rendering, taint, the breakers (repeat, error, doom loop, denial streak, stuck) and the events the UI reads.

The default loop and every other loop reach tools only through here, so no loop can run a tool without the cards and refusals the
chat gives. The app's singletons (toolbox, plans, run store, ...) are read from the app module when a turn runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from .. import approval_log, autoreview, guards, limits, permissions, permrules, tools
from .. import shell as shell_tool
from .. import workers as workers_mod
from ..context import cite_slim
from ..dispatch import Contact, dispatch_tool
from ..extensions.hooks import hook_ctx
from ..mcp_client import MCP_DANGER
from ..plans import PLAN_BLOCKED, PLAN_TOOL, normalize_plan, plan_voided_by_taint, taint_expected
from ..runs import CoreRefused, Run, args_digest
from ..stuck import STUCK_NUDGE, STUCK_STOP, WAITING_TOOLS, StuckDetector, detector_for
from ..subagents import UNATTENDED_KINDS, parallel_safe
from ..thinking_summary import ThinkingSummary
from ..tools import ASK_LOCKED_DANGER, summarize_result
from ..trace import Tracer
from .sources import is_source_tool

if TYPE_CHECKING:
    from ..app import RunMeter

log = logging.getLogger("personal_os")


@dataclass
class TurnState:
    """What the tool step reads and changes beside the round it is handed: the turn's fixed context and the running state the loop
    and the step share."""
    cfg: dict[str, Any]
    conv: dict[str, Any]
    conv_id: str
    body: Any  # the ChatIn that started the turn
    run: Run | None
    stop: asyncio.Event
    steers: list[dict[str, Any]] | None
    tool_ctx: dict[str, Any]
    modes: dict[str, str]
    mcp_modes: dict[str, str]
    mcp_defer: bool
    explicit_modes: dict[str, str]
    loaded: set[str]
    perm_rules: Any
    pmode: str
    meter: RunMeter = field(init=False)  # started with the first round
    desk_id: str | None
    autonomy: str
    in_side: bool
    messages: list[dict[str, Any]]
    used: dict[str, Any]
    tsum: ThinkingSummary
    propose_without_run: bool
    planning: bool
    active_plan: dict[str, Any] | None
    plan_seen: bool  # whether this chat has any plan at all: one query per reply keeps the plan lookups off the hot path
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    denials: permrules.DenialStreak = field(default_factory=permrules.DenialStreak)  # refused calls in a row; at three the next result says to stop varying them
    detector: StuckDetector = field(init=False)  # loop shapes loopRepeatLimit cannot see; thresholds from stuckSensitivity (__post_init__)
    fence_nonce: str = field(default_factory=lambda: secrets.token_hex(8))  # per run: untrusted results are fenced with an id the page cannot guess
    blocked: set[str] = field(default_factory=set)
    tool_errors: dict[str, int] = field(default_factory=dict)
    warm_tasks: list[asyncio.Task] = field(default_factory=list)  # read-only calls started ahead of their turn in the round; cancelled at the end
    delegation_forced: bool = False  # set once the reply's own tool use has to turn into delegating
    work_ms: int = 0  # tool time (not approval waits) of this reply's own work calls, for limits.DELEGATION_AFTER_SECONDS
    last_sig: str | None = None
    repeats: int = 0
    partial: str | None = None  # how the reply was cut short ("loop" once a breaker has stopped tool use)
    stop_text: str | None = None
    stuck_hits: int = 0
    parked: str | None = None  # the call this reply let go of rather than keep waiting on
    inflight: dict[str, Any] | None = None  # the call whose tool is executing, for a cancelled run to record
    awaiting: dict[str, Any] | None = None  # the tool event of a call blocked on approval, for a cancelled run to keep
    ended: bool = False  # the step ended the reply itself (a parked card): the loop stops

    def __post_init__(self) -> None:
        self.detector = detector_for(self.cfg.get("stuckSensitivity"))


@dataclass
class Round:
    """The reply segment and round one `ToolStep.run` works in. A steer opens a new segment between rounds, so these are handed in per round."""
    am: dict[str, Any]
    buf: list[str]
    tool_events: list[dict[str, Any]]
    tracer: Tracer
    n: int
    span: dict[str, Any]
    ok_calls: list[str] = field(default_factory=list)  # names of this round's calls that came back without an error


class ToolStep:
    def __init__(self, st: TurnState, *, front: Callable[[], bool], load_groups: Callable[[Any], None], log_mode: Callable[..., None],
                 review_call: Callable[..., Awaitable[dict[str, Any]]], schemas: Callable[..., list[dict[str, Any]]]) -> None:
        """The closures are the turn's: they read what only the loop's setup knows (the desk, the persona, the model)."""
        from .. import app  # the singletons the gate chain reads; imported when a turn starts, as the app imports this module
        self.st = st
        self.core = app
        self.front, self.load_groups, self.log_mode, self.review_call, self.schemas = front, load_groups, log_mode, review_call, schemas
        self.accounted: set[str] = set()  # the call ids that went to the journal through here, for the check that nothing went around it

    def prepare(self, calls: list[dict[str, Any]], *, text: str, reasoning: str | None, cut: bool) -> dict[str, Any]:
        """Normalise the round's calls in place and return the assistant message that carries them. `cut` says the output limit
        ended the round, so its last call may be truncated."""
        st, core = self.st, self.core
        known_names = set(core.toolbox.specs) | set(st.modes) | set(st.mcp_modes)
        advertised = [s["function"]["name"] for s in st.tool_schemas]
        for i, c in enumerate(calls):
            core._normalise_call(c, known_names, advertised, cut and i == len(calls) - 1)
        turn = {"role": "assistant", "content": text or None,
                "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": core._replay_args(c["arguments"])}} for c in calls]}
        if reasoning:  # interleaved thinking: the next round reads this round's reasoning back
            turn["reasoning_content"] = reasoning
        return turn

    def _warm_segment(self, calls: list[dict[str, Any]], start: int, warm: dict[str, asyncio.Task],
                      warm_ctx: dict[str, dict[str, Any]], rnd: Round) -> int:
        """Start the maximal run of read-only calls beginning at calls[start] together (at most parallelReads at a
        time) and return its length. A call joins only when it would run without a card: on, not forced, no rule
        asking or refusing it, not a writer, a spawn or session-holding tool, and no plan in play. The first call
        that does not qualify ends the run, so a write is a barrier and later reads wait for it. A reply that is
        tainted by an earlier call of the run is simulated, so a call that would then ask is not started."""
        st, core = self.st, self.core
        width = limits.slots(st.cfg, "parallelReads")
        # A call starts before the loop settles it, so a permission gate could not stop a warmed read.
        if width < 2 or core.toolbox.bus.active("tool.permission") or st.planning or st.active_plan is not None or st.plan_seen or core.proposal_only(st.run) or st.delegation_forced:
            return 1
        sim = dict(st.tool_ctx)
        seg: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for c in calls[start:]:
            name, args = c["name"], c["_args"] or {}
            spec, raw = core.toolbox.specs.get(name), st.modes.get(name, "off")
            if c["_invalid"] or name in st.blocked or not parallel_safe(spec, name, raw):
                break
            if core._gate(name, raw, sim, args) != "on" or core.toolbox.fs_needs_ask(name, args, sim):
                break
            proots, pcwd = shell_tool.perm_where(core.toolbox, sim)
            pcwd = pcwd if name == "shell_run" else None
            perm = permrules.resolve(name, args, "on", False, rules=st.perm_rules, conv=st.conv_id, roots=proots, cwd=pcwd,
                                     doom=st.detector is not None and st.detector.repeat_count(name, args) >= limits.loop_setting(st.cfg, "loopDoomLimit") - 1)
            if perm.mode != "on" or perm.refusal or perm.kind == "doom_loop":
                break
            # A call Auto mode would hand the reviewer must not have gone out before the review.
            if autoreview.route(st.pmode, mode="on", danger=spec.danger, explicit_on=st.explicit_modes.get(name) == "on",
                                covered=bool(perm.rule)) != "run":
                break
            seg.append((c, args))
            if spec.taints:
                sim["tainted"] = True
        if len(seg) < 2:
            return 1
        sem = asyncio.Semaphore(width)

        async def run_one(name: str, args: dict[str, Any], ctx: dict[str, Any], uid: str) -> Any:
            async with sem:
                # Stop skips reads still queued for a slot; reads already running side by side are not interrupted.
                if st.stop.is_set():
                    return {"error": "Stopped by the user before this call ran; it was not executed."}
                self.accounted.add(uid)
                return await core._call_tool(st.run, rnd.n, name, args, ctx, uid)

        for c, args in seg:
            key = tools.call_key(c["name"], args)
            if key not in warm:  # an identical call in the round runs once and shares the result
                # Taint set by a read must not reach calls gated before it; its sources merge back when it is consumed.
                warm_ctx[key] = {**st.tool_ctx, "taint_sources": []}
                warm[key] = asyncio.ensure_future(run_one(c["name"], args, warm_ctx[key], f"{rnd.am['id']}:{c['id']}"))
                st.warm_tasks.append(warm[key])
        return len(seg)

    async def run(self, calls: list[dict[str, Any]], rnd: Round) -> AsyncIterator[tuple[str, Any]]:
        """Gate, run and report each call of a round in order, yielding the events the UI reads and appending one tool message per call to
        the turn's messages. The assistant message that asked for them is already there."""
        st, core = self.st, self.core
        # Read-only agent_spawn calls of this round start together; never in plan mode.
        core.subagent_mgr.prestart(calls, st.tool_ctx, start=not st.planning and not st.stop.is_set())
        if rnd.buf and rnd.buf[-1] and not rnd.buf[-1].endswith("\n"):
            rnd.buf.append("\n")
            yield "delta", {"id": rnd.am["id"], "text": "\n"}
        # Consecutive read-only calls start together (see _warm_segment); a write, an ask or any other call
        # is a barrier. The loop below still gates, journals and reports every call in order and only
        # collects the warmed result instead of calling the tool.
        warm: dict[str, asyncio.Task] = {}
        warm_ctx: dict[str, dict[str, Any]] = {}
        warmed_upto = 0
        for ci, c in enumerate(calls):
            if ci >= warmed_upto and not st.stop.is_set():
                warm.clear()  # a result is only shared inside its own run of reads, never across a barrier
                warmed_upto = ci + max(1, self._warm_segment(calls, ci, warm, warm_ctx, rnd))
            if st.stop.is_set():
                # Stop means the rest of this round's calls do not run either. The provider still needs a tool
                # message per call, and nothing was journaled as started, so nothing replays.
                st.messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps({"error": "Stopped by the user before this call ran; it was not executed."})})
                continue
            args = c["_args"] or {}
            sig = tools.call_key(c["name"], args)
            st.repeats = st.repeats + 1 if sig == st.last_sig and c["name"] not in WAITING_TOOLS else 1
            st.last_sig = sig
            if st.repeats >= limits.loop_setting(st.cfg, "loopRepeatLimit"):  # before the approval gate: denying the same call forever is still a loop
                st.partial = "loop"
            if st.partial == "loop":  # every pending call still needs a tool message, executed or not
                st.messages.append({"role": "tool", "tool_call_id": c["id"], "content": st.stop_text or core.LOOP_STOP.format(name=c["name"], n=limits.loop_setting(st.cfg, "loopRepeatLimit"))})
                continue
            # A call that cannot run as sent (broken JSON, an unknown name, a cut-off call) is answered here with
            # what was wrong. It never reaches the gate, a rule, a plan claim, an approval card or the tool:
            # nothing the user could approve would ever work. Arguments the signature cannot take are refused
            # below instead, once the gate has spoken: a plan-mode or turned-off refusal comes first, as it did.
            invalid = c["_invalid"]
            if c["_problem"]:
                bad = core._invalid_call_error(c, core.toolbox.specs.get(c["name"]))
                shown = (c["_asked"] or c["name"])[:80] if invalid == "name" else c["name"]
                uid = f"{rnd.am['id']}:{c['id']}"
                if c["name"] in st.blocked:
                    bad = tools.denied(c["name"], f"failing {limits.loop_setting(st.cfg, 'loopToolErrorLimit')} times in a row and disabled for the rest of this reply")
                yield "tool_call", {"message_id": rnd.am["id"], "id": uid, "name": shown, "arguments": {},
                                    "needs_approval": False, "forced": False, "proposal": None, "permission": None, "plan": None}
                tspan = rnd.tracer.start("tool", shown, {"round": rnd.n, "arguments": {}, "invalid": invalid}, parent=rnd.span)
                yield "span", {"message_id": rnd.am["id"], "span": tspan}
                preview, err = summarize_result(bad), bad.get("error")
                st.tool_errors[c["name"]] = st.tool_errors.get(c["name"], 0) + 1
                if st.tool_errors[c["name"]] >= limits.loop_setting(st.cfg, "loopToolErrorLimit"):
                    st.blocked.add(c["name"])
                rnd.tracer.end(tspan, {"result_chars": len(preview), "invalid": invalid}, error=err)
                event = {"id": uid, "name": shown, "arguments": {}, "result_preview": preview, "duration_ms": 0,
                         "error": err, "images": None, "undo": None, "approval": None, "plan": None, "forced": False,
                         "tainted": False, "blocked": None, "breaker": st.partial, "proposal": None,
                         "invalid": invalid}
                rnd.tool_events.append(event)
                yield "tool_result", {"message_id": rnd.am["id"], **event}
                yield "span", {"message_id": rnd.am["id"], "span": tspan}
                st.messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": core.tool_results.for_model(st.conv_id, rnd.am["id"], c["name"], bad, untrusted=False, inline_chars=limits.loop_setting(st.cfg, "toolResultInlineChars"))})
                continue
            raw_mode = st.modes.get(c["name"], "off")
            first_use = False  # a deferred tool called by name before any search: loaded now, run now, schema returned on an error
            if raw_mode != "off" and c["name"] in st.tool_ctx["deferred"]:
                if core.proposal_only(st.run) or st.active_plan is not None or st.plan_seen or st.body.resume_of:
                    # A plan step, a resumed run or a scheduled run names a tool it already settled on:
                    # load its group rather than send it searching. Its gate below is unchanged.
                    self.load_groups([c["name"]])
                    st.tool_schemas = self.schemas()
                else:
                    st.loaded.add(c["name"])
                    first_use = True
                    st.tool_schemas = self.schemas()
            elif (raw_mode == "off" and st.mcp_defer and not st.planning and core.mcp_is(c["name"]) and c["name"] in st.mcp_modes
                  and c["name"] not in st.modes):
                st.loaded.add(c["name"])
                st.modes[c["name"]] = raw_mode = st.mcp_modes[c["name"]]
                first_use = True
                st.tool_schemas = self.schemas()
            spec = core.toolbox.specs.get(c["name"])
            # Connector tools are not in toolbox.specs but are external by construction; "safe" here would
            # let them through plan mode, propose-only desks and the unexpected-taint rule.
            danger = spec.danger if spec else (MCP_DANGER if core.mcp_is(c["name"]) else "safe")
            mode = core._gate(c["name"], raw_mode, st.tool_ctx, args)
            # What auto mode may never review away: a card forced by taint, a voided plan, the doom loop or a desk that
            # asks as it goes (hard_forced), against an alwaysAsk / force_ask card it may lift on a confident allow (soft).
            hard_forced = mode != raw_mode
            # A credential store read or write, or a write once the reply read untrusted content, asks.
            fs_ask = mode != "off" and core.toolbox.fs_needs_ask(c["name"], args, st.tool_ctx)
            if fs_ask and mode == "on":
                mode = "ask"
            lockable = bool(spec and (core.toolbox.ask_locked(spec) or core.toolbox.forces_ask(c["name"], args, st.tool_ctx)))
            hard_forced = hard_forced or core.toolbox.forces_card(c["name"], args, st.tool_ctx) or (
                lockable and core.toolbox.tainted_for(c["name"], args, st.tool_ctx))
            desk_cleared = False
            # untrusted content in this reply upgraded on -> ask; so does a call that may never run unasked
            # (shell_run outside its sandbox, or able to reach out in a tainted reply), which no standing grant can then buy off
            forced = mode != raw_mode or (mode == "ask" and (fs_ask or core.toolbox.forces_ask(c["name"], args, st.tool_ctx)))
            # A sandboxed shell_run inside this desk's own workspace needs no card when the tool is still on its default
            # `ask` (shell.auto_ok). Everything below (plan mode, desk autonomy, permission rules, doom-loop) can still ask.
            if (c["name"] == "shell_run" and mode == "ask" and raw_mode == "ask" and not forced and st.desk_id
                    and shell_tool.auto_ok(args, st.tool_ctx, st.cfg, [core.workspace.desk_root(st.desk_id)])):
                mode, desk_cleared = "on", True
            blocked_reason: str | None = None
            # ---- plan mode, in priority order. Each rule can only ever make a call ask or stop;
            # none of them can turn a card off, so this is a narrowing of the gate above.
            if c["name"] != PLAN_TOOL and raw_mode != "off":
                # Nothing consequential runs while a plan is being drafted. `forced` rides out
                # on the tool_call event so the UI can say "blocked while planning" rather than
                # "turned off". A propose-only desk never performs an external action: it is filed as a proposal
                # below (`proposing`), or refused when there is no run to file it under.
                blocked_reason = guards.plan_refusal(danger, planning=st.planning, propose_without_run=st.propose_without_run)
                if blocked_reason:
                    mode, forced = "off", True
                elif (st.autonomy != "propose" or danger != "external") and plan_voided_by_taint(
                        danger, bool(st.tool_ctx["tainted"]), taint_expected(st.active_plan, st.tool_ctx["taint_sources"])):
                    # Taint the approved plan did not predict voids the pre-approval: ask, and
                    # consume nothing. A web fetch is included: its address can carry what was read.
                    # Checked before any claim, because a step burnt on a call the
                    # user then denies can never be reclaimed.
                    mode, forced, hard_forced = "ask", True, True
            # Argument-pattern rules, session grants and the doom-loop card (permrules.py). A deny refuses; a
            # forced approval (taint, plan mode) is never downgraded; MCP tools keep their schema-bound grants.
            # An alwaysAsk tool tops out at ask (Toolbox.effective), so gate() does not turn an 'on' into a forced
            # card for it; and an external tool the user set to ask is 'ask' before the gate too. A tainted run
            # forces either here, so the card buys no grant or allow rule.
            # That alone does not stop an approved plan step from standing in for the card: taint the plan did
            # not expect already forced above, so this is taint the user saw on the plan card (taint_only).
            taint_only = not forced and mode == "ask" and danger in ASK_LOCKED_DANGER and core.toolbox.tainted_for(c["name"], args, st.tool_ctx)
            forced = forced or taint_only
            hard_forced = hard_forced or taint_only
            perm = permrules.Resolution(mode, forced)
            pre_mode = mode  # what the call would have done before rules and session grants (approval_log below)
            if c["name"] != PLAN_TOOL and mode != "off" and not core.mcp_is(c["name"]):
                # A shell command is judged from where it will start, and the desk's own folder is not Grain's data.
                # A file tool's relative path starts at the desk (proots[0]), as the tool resolves it.
                proots, pcwd = shell_tool.perm_where(core.toolbox, st.tool_ctx)
                pcwd = pcwd if c["name"] == "shell_run" else None
                perm = permrules.resolve(
                    c["name"], args, mode, forced,
                    rules=st.perm_rules, conv=st.conv_id, roots=proots, cwd=pcwd,
                    doom=st.detector is not None and st.detector.repeat_count(c["name"], args) >= limits.loop_setting(st.cfg, "loopDoomLimit") - 1)
                mode = perm.mode
                if perm.kind == "doom_loop":
                    forced, taint_only, hard_forced = True, False, True
            elif core.mcp_is(c["name"]) and mode != "off":
                # A global deny rule can name an MCP slug or server; it refuses over any grant and the grant row is untouched.
                perm.refusal = permrules.mcp_denied(c["name"], st.perm_rules)
            # A background run never waits on an approval: there is nobody at the keyboard, and the call is not
            # going to happen either way. It becomes a proposal in _call_tool and the run carries on.
            # MCP tools are external by construction but are not in Toolbox.specs, so proposes()
            # cannot see them. A scheduled run must still record them instead of calling them.
            # A 'propose' desk files its external calls the same way, without a job's hint.
            proposing = mode != "off" and st.run is not None and (
                (core.proposal_only(st.run) and (core.toolbox.proposes(c["name"]) or core.mcp_is(c["name"])))
                or (st.autonomy == "propose" and danger == "external"))
            if proposing:
                mode, forced = "on", False
            # The provider's call id is only unique within one request -- llm.stream_chat falls back
            # to "call_<idx>" when the provider omits one -- so two conversations streaming at once
            # both produce "call_0". Key anything cross-conversation by the message id too, or one
            # chat's approval resolves another chat's call. The model still sees c["id"].
            uid = f"{rnd.am['id']}:{c['id']}"
            plan: dict[str, Any] | None = None     # this call's own proposed plan, when it is propose_plan
            claimed: dict[str, Any] | None = None  # the approved plan step this call consumed instead of asking
            pre: Any = None                        # a result settled before the gate: nothing to approve
            if c["name"] != PLAN_TOOL and mode != "off" and (bad := core.toolbox.precheck(c["name"], args, st.tool_ctx)) is not None:
                # Arguments the tool's signature cannot take: nothing the user could approve would ever run, so
                # no card opens and no plan step is spent on it. Not the plan tool: its placeholder function is
                # narrower than its schema, and normalize_plan below is its check.
                pre, invalid = bad, "schema"
            if c["name"] == PLAN_TOOL and mode != "off":
                # A plan is nothing but its card, so it asks whatever the mode says, and no standing grant
                # below can turn that off. It is not a taint upgrade either, so it is not `forced`.
                mode, forced = "ask", False
                # A plan the user cannot act on never reaches them. A tool missing from this reply's schemas
                # (an integration that is not connected) is as unusable as one that is off.
                # Judged against every tool this reply could use once the plan is approved, not
                # against the reduced planning-phase set: a plan whose steps are exactly the tools
                # being withheld while it is drafted is the normal case, not an unusable plan.
                offered = {s["function"]["name"] for s in self.schemas(withheld=True)}
                args, pre = normalize_plan(args, {n: (m if n in offered else "off") for n, m in st.modes.items()},
                                           specs={**{n: sp.danger for n, sp in core.toolbox.specs.items()},
                                                  **{n: MCP_DANGER for n in st.modes if core.mcp_is(n)}},
                                           taints={n for n in st.modes if core.toolbox.taints(n) or core.mcp_is(n)})
                if pre is None:
                    # A confident plan goes ahead unanswered after the countdown; the wait loop below enforces it.
                    # Not in manual mode, and not after untrusted content, which may have shaped the plan.
                    auto_s = float(st.cfg.get("planAutoApproveSeconds") or 0)
                    if auto_s > 0 and args["confidence"] == "high" and st.pmode != "manual" and not st.tool_ctx["tainted"]:
                        args["auto_at"] = time.time() + auto_s  # on the args too, so the inline card can count down
                    plan = core.plans.open(uid, args, run_id=st.run.run_id if st.run else None, conversation_id=st.conv_id,
                                      message_id=rnd.am["id"], tainted=bool(st.tool_ctx["tainted"]),
                                      desk_id=st.run.desk_id if st.run else None)
                    st.plan_seen = True
                    # Its own event, because the card needs the whole plan with its steps and
                    # their digests; the tool_call event carries only the raw arguments.
                    yield "plan_card", {"message_id": rnd.am["id"], "call_id": uid, "plan": plan}
            elif (st.plan_seen or st.active_plan) and c["name"] != PLAN_TOOL and pre is None:
                # Digest binding: an approved step whose arguments hash to the same thing stands in for the
                # modal, exactly once. A forced approval never consults a plan -- untrusted content in this
                # reply must always reach the user -- and a claim only matches inside its own run.
                # In a chat a plan exists to stand in for the modal, so it is only consulted when
                # there would have been one. A desk's plan is also its record of progress - what
                # `_should_chain` reads - so an approved step is claimed there even for a call that
                # was going to run anyway. A forced approval never consults a plan either way, except the
                # external/schedules taint lock when that taint is one the plan card already showed.
                if (not forced or taint_only) and (mode == "ask" or (st.active_plan is not None and mode == "on")):
                    claimed = core.plans.claim(st.run.run_id if st.run else None, c["name"], args, uid,
                                          desk_id=st.run.desk_id if st.run else None, card_locked=lockable, inherit=st.body.resume_of)
                    # Progress, for a supervisor deciding whether another turn is worth it: a
                    # turn that consumed no approved step did not advance the plan.
                    if claimed is not None and st.run is not None:
                        st.run.steps_consumed += 1
                if claimed is None and core.plans.rejected(st.conv_id, c["name"], args):
                    pre = tools.denied(c["name"], "part of a plan you rejected")
            if blocked_reason and pre is None:
                pre = tools.denied(c["name"], blocked_reason)
            if perm.refusal and pre is None:
                pre = tools.denied(c["name"], perm.refusal)
            if pre is None and raw_mode != "off" and (lane := workers_mod.lane_refusal(c["name"], self.front(), st.delegation_forced)):
                pre = lane  # agent_spawn in a delegating chat, the hand-off tools elsewhere, anything but delegating once forced
            unattended = False
            reviewer_denied = False
            lifted_all = False
            review = None
            is_background = st.run is not None and st.run.kind in UNATTENDED_KINDS
            # Permission mode (autoreview.route). Manual leaves the gate above exactly as it settled; auto sends what is not
            # known safe to the reviewer; allow_all lifts every card except the floor (a destructive shell command, a settings change)
            # and the user's own questions. In manual the floor alone is routed: destructiveDefaults blocks or asks. Refusals, proposals
            # and approved plan steps are already settled, so they never get here.
            floor_hint = "items" if core.mcp_is(c["name"]) and (core._mcp_event(c["name"]) or {}).get("destructive") else None
            floor = core.toolbox.floor_action(c["name"], args, st.tool_ctx, kind=floor_hint)
            if (st.pmode != "manual" or floor) and claimed is None and pre is None and not proposing and mode != "off":
                explicit = st.explicit_modes.get(c["name"]) if not core.mcp_is(c["name"]) else ("on" if raw_mode == "on" else None)
                locked_spec = bool(spec and core.toolbox.ask_locked(spec))
                hints = core._mcp_event(c["name"]) or {}
                # A connector call forced by untrusted content stays a card (allow-all runs it: autoreview.route).
                mcp_tainted = bool(hints) and hard_forced and bool(st.tool_ctx["tainted"])
                rt = autoreview.connector_route(
                    st.pmode, mode=mode, raw_mode=raw_mode, hints=hints, hard_forced=hard_forced, tainted=bool(st.tool_ctx["tainted"]),
                    covered=desk_cleared or (pre_mode == "ask" and perm.mode == "on") or bool(perm.rule and perm.mode == "on"),
                    question=c["name"] in permrules.STILL_ASK, floor=floor) if core.mcp_is(c["name"]) else autoreview.route(
                    # handing work to a worker needs no review of its own: each call the worker makes is reviewed in its turn
                    st.pmode, mode=mode, danger="safe" if c["name"] in workers_mod.FRONT_TOOLS else danger, explicit_on=explicit == "on",
                    # an alwaysAsk tool's stored "ask" is only the cap on "on", not a choice, so it is not an explicit ask
                    explicit_ask=(explicit == "ask" and not locked_spec) or (mode == "ask" and perm.kind in ("rule", "external_directory")),
                    covered=desk_cleared or (pre_mode == "ask" and perm.mode == "on") or bool(perm.rule and perm.mode == "on"),
                    # a connector that calls its own tool destructive is reviewed strictly (a confident, untainted allow)
                    hard_forced=hard_forced, soft_forced=(lockable or bool(hints.get("destructive"))) and not hard_forced,
                    # a sensitive-path read/write, a tainted write or a runaway repeat stays a card in auto (allow-all runs it)
                    fenced=bool(fs_ask) or perm.kind in ("external_directory", "doom_loop") or mcp_tainted,
                    question=c["name"] in permrules.STILL_ASK or c["name"] == PLAN_TOOL,
                    floor=floor)
                if rt == "block":
                    pre = tools.denied(c["name"], guards.floor_text(core.toolbox, st.tool_ctx, c["name"], args, floor_hint))
                    self.log_mode(c["name"], args, uid, "deny", "destructive", "blocked by destructiveDefaults")
                elif rt == "run":
                    if mode == "ask":
                        mode, forced = "on", False
                    lifted_all = st.pmode == "allow_all" and danger != "safe"
                elif rt == "card" and floor:
                    mode, forced, hard_forced = "ask", True, True  # a connector tool the floor cards (a native one is already forced)
                elif rt in ("review", "review_strict"):
                    review = await self.review_call(c["name"], args, danger, raw_mode)
                    outcome = autoreview.apply(rt, review["verdict"], review["confidence"], core.toolbox.tainted_for(c["name"], args, st.tool_ctx))
                    if outcome == "run":
                        mode, forced = "on", False
                    elif outcome == "deny":
                        reviewer_denied = True
                        pre = tools.denied(c["name"], f"refused by the safety reviewer: {review['reason']}. "
                                                      "Do not retry the same call; change approach or ask the user.")
                        self.log_mode(c["name"], args, uid, "deny", "auto-review", "mode: auto", review)
                    else:
                        mode, forced = "ask", forced or rt == "review_strict"
                        self.log_mode(c["name"], args, uid, "review-ask", "auto-review", "mode: auto", review)
            if pre is None and mode != "off" and not proposing:
                # The core has settled on run or card: its guards refuse what they refuse, then an extension may only narrow.
                why, narrowed = await guards.settle(core.toolbox, st.tool_ctx, c["name"], args, danger=danger, mode=mode,
                                                    kind=floor_hint)
                if why:
                    pre = tools.denied(c["name"], why)
                elif narrowed and mode == "on" and claimed is None:  # an approved plan step is the user's answer to this very call
                    mode, forced = "ask", True
            # A background run never waits on a card. Manual mode keeps unattendedApprovals; in the other modes a card that
            # survives routing becomes a proposal when the call can be one, else a recorded refusal.
            if (mode == "ask" and claimed is None and pre is None and not proposing and st.run is not None and is_background
                    and (st.pmode != "manual" or permissions.get(st.cfg, "unattendedApprovals") == "deny")):
                if st.pmode != "manual" and (core.toolbox.proposes(c["name"]) or core.mcp_is(c["name"])):
                    proposing, mode, forced = True, "on", False
                else:
                    # Nobody is there to answer: refuse with a recorded reason rather than park a card for later.
                    unattended = True
                    why = ("no one is available to approve it and unattendedApprovals is set to deny" if st.pmode == "manual"
                           else "it needs an approval and no one is available to give it in a background run")
                    pre = tools.denied(c["name"], f"refused: {why}")
                    if st.run.store is not None:
                        with contextlib.suppress(CoreRefused):  # refused outright: the refusal above stands, no row to record
                            st.run.store.open_approval(uid, st.run.run_id, c["name"], args, conversation_id=st.conv_id, message_id=rnd.am["id"],
                                                    forced=forced, desk_id=st.run.desk_id, danger=danger)
                            st.run.store.decide(uid, "deny", by="unattended", note=why)
            if core.proposal_only(st.run) and mode == "ask" and claimed is None and pre is None:
                # A background run has nobody to answer a card, and only external calls can become proposals.
                # Opening one here would park the run forever (and every later fire behind it).
                pre = tools.denied(c["name"], "not available in a background run: it needs an approval and nobody is watching")
            asks = mode == "ask" and claimed is None and pre is None
            if asks and st.desk_id and not st.in_side and c["name"] != PLAN_TOOL and core.run_store.claim_parked(st.desk_id, c["name"], args, uid):
                # The user already said yes to exactly this call on a card an earlier turn let go
                # of (see parked_report). Spent here, once; any other arguments still ask.
                asks = False
            # History: a call that runs without a card because something already stood behind it — an approved plan
            # step, an allow rule or session grant, or the review gate's look (approval_log.py). Card answers log in decide().
            if not asks and pre is None and not proposing and mode != "off":
                granted_by = (("conversation" if permrules.SESSION.covers(st.conv_id, perm.keys) else "rule")
                              if pre_mode == "ask" and perm.mode == "on" else None)
                if claimed is not None or review or granted_by or lifted_all:
                    approval_log.record(core.db, tool=c["name"], args=args, conversation_id=st.conv_id, call_id=uid,
                                        run_id=st.run.run_id if st.run else None, desk_id=st.desk_id or None, agent=st.run.kind if st.run else None,
                                        decision="plan" if claimed else ("always" if granted_by else "auto"),
                                        scope="plan" if claimed else (granted_by or ("auto-review" if review else "allow-all")),
                                        rule=perm.rule if granted_by == "rule" else None,
                                        note=None if claimed or granted_by else ("mode: auto" if review else "allowed (allow-all mode)"),
                                        review=None if claimed or granted_by else review)
            yield "tool_call", {"message_id": rnd.am["id"], "id": uid, "name": c["name"], "arguments": args,
                                "needs_approval": asks, "forced": forced, "proposal": proposing or None,
                                "permission": core._permission_card(perm, danger, c["name"], args) if asks else None,
                                "review": review, "mcp": core._mcp_event(c["name"]),
                                "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None}
            tspan = rnd.tracer.start("tool", c["name"], {"round": rnd.n, "arguments": core._short(args), "mode": mode, "forced": forced,
                                                     "plan_step": f"{claimed['plan_id']}#{claimed['idx']}" if claimed else None,
                                                     **({"repaired": True} if c["_repaired"] else {}),
                                                     **({"invalid": invalid} if invalid else {})},
                             parent=rnd.span)
            yield "span", {"message_id": rnd.am["id"], "span": tspan}
            t0 = time.time()
            decision = "allow"
            deny_note: str | None = None
            edit_info: dict[str, Any] = {}
            interrupted = False  # Stop cut this call short (see _await_tool)
            if asks:
                # Pause the reply until the user approves or denies this call (POST /approvals/{call_id}).
                # The approval is a row, and it waits as long as it takes: there is no auto-deny.
                approval_t0 = time.time()
                fut: asyncio.Future = asyncio.get_event_loop().create_future()
                core._approvals[uid] = fut
                store = st.run.store if st.run is not None else None
                mine = False  # whether the row on file is the one this wait opened (a reused id returns the old row)
                if store is not None:
                    try:
                        opened = store.open_approval(uid, st.run.run_id, c["name"], args, conversation_id=st.conv_id, message_id=rnd.am["id"],
                                                     forced=forced, desk_id=st.run.desk_id, danger=danger, review=review)
                    except CoreRefused as refused:  # settle cleared it, so only a context mismatch gets here: a refusal, not a card
                        opened = None
                        fut.set_result("deny")
                        core._approval_notes[uid] = str(refused)
                    mine = bool(opened and opened.get("status") == "pending" and opened.get("run_id") == st.run.run_id
                                and opened.get("tool") == c["name"] and opened.get("args_digest") == args_digest(args))
                    if not mine:
                        log.warning("approval %s was already on file for another call; only a live answer will count", uid)
                    st.run.budget = st.meter.snapshot()
                    st.run.set_status("awaiting_approval")
                st.awaiting = {"id": uid, "name": c["name"], "arguments": args, "result_preview": "", "duration_ms": 0,
                            "error": None, "pending": True, "needs_approval": True, "forced": forced}
                if st.desk_id and not st.in_side:  # a duel side's card is its own: the real desk's status stays as it is
                    # The desk leaves the rail's "working" label and says what it is waiting for.
                    # These are the *live* waiting states: a run is still holding the card open.
                    # desk_ask's card IS the question, so the desk carries it from the moment the
                    # card opens: the rail and the banner show it, and the banner's answer can
                    # settle this card (message_desk) as well as the card's own box can.
                    q = str(args.get("question") or "").strip() if c["name"] in core.QUESTION_TOOLS else None
                    core.desks.set_status(st.desk_id, "awaiting_plan" if plan is not None else "needs_approval",
                                     run_id=st.run.run_id if st.run else None, question=q)
                # A desk's card may outlive the run that raised it: nobody is at the keyboard, and
                # holding a run open for hours to wait is how a desk ends up pinned to a dead task.
                # 0 means never park, which is what an ordinary chat always does.
                park_after = float(st.cfg.get("parkAfterSeconds") or 0) if st.desk_id and not st.in_side else 0.0
                waited = 0.0
                try:
                    while not fut.done():
                        if st.stop.is_set():
                            fut.set_result("deny")
                            if store is not None:
                                store.decide(uid, "deny", by="stop")
                            if plan is not None:  # a stop means "stop", not "never": this no does not block later
                                core.plans.decide(uid, "deny", by="stop", note="You stopped the reply before answering this plan.")
                            break
                        if st.steers and not st.desk_id and c["name"] in core.QUESTION_TOOLS:
                            # The user wrote while a question waited: that message is the answer. It is taken off
                            # the steers so the loop top does not hand it to the model a second time as a new turn.
                            answer = st.steers.pop()
                            note = str(answer["content"]).strip()[:500]
                            fut.set_result("allow")
                            core._approval_notes[uid] = note
                            if store is not None:
                                store.decide(uid, "allow", by="steer", note=note)
                            # The /steer route already stored it as a user row. The answer lives on the card and the
                            # approval row, so that row goes: kept, the next turn's history replays it as an unanswered turn.
                            with core.db.tx() as tx:
                                tx.execute("DELETE FROM messages WHERE id=?", (answer["id"],))
                            yield "removed_message", {"id": answer["id"]}
                            break
                        if st.steers and not st.desk_id:
                            # The user wrote instead of answering the card: that is a no, with their words as the
                            # reason. `by="steer"` keeps a plan re-proposable (only a user's own no blocks it), and
                            # the loop top folds the message in once this call has its result.
                            note = str(st.steers[-1]["content"]).strip()[:500]
                            fut.set_result("deny")
                            core._approval_notes[uid] = note
                            if store is not None:
                                store.decide(uid, "deny", by="steer", note=note)
                            if plan is not None:
                                core.plans.decide(uid, "deny", by="steer", note=note)
                            break

                        # Park, never auto-deny, and never in front of somebody who is looking at
                        # the card: with a viewer attached the run waits as long as it takes.
                        if (park_after > 0 and waited >= park_after and store is not None
                                and st.run is not None and st.run.watchers == 0):
                            store.park(uid)
                            st.parked = uid
                            break
                        auto_at = core.plans.auto_at(uid) if plan is not None else None
                        if auto_at is not None and time.time() >= auto_at:
                            # The countdown ran out with the card untouched: go ahead, recorded as automatic.
                            fut.set_result("allow")
                            if store is not None:
                                store.decide(uid, "allow", by="auto", note="confident plan went ahead after the countdown")
                            core.plans.decide(uid, "allow", by="auto")
                            break
                        try:
                            # Short, so an approval-blocked run notices a stop or a shutdown promptly.
                            await asyncio.wait_for(asyncio.shield(fut), timeout=min(2, max(0.05, auto_at - time.time())) if auto_at else 2)
                        except asyncio.TimeoutError:
                            waited = time.time() - approval_t0
                            row = store.approval(uid) if store is not None else None
                            if mine and row and row["status"] != "pending" and not fut.done():  # decided on the row alone
                                fut.set_result(row["decision"])
                    decision = fut.result() if fut.done() else "deny"
                finally:
                    core._approvals.pop(uid, None)
                deny_note = core._approval_notes.pop(uid, None)
                pending_card, st.awaiting = st.awaiting, None
                if not st.parked:
                    # On the tape, so a window that re-attaches while the approved call is still running (or
                    # another window) sees the card answered rather than open for a second approval.
                    yield "tool_decision", {"message_id": rnd.am["id"], "id": uid, "decision": decision}
                if decision != "deny" and mine and store is not None and (arow := store.approval(uid)) and arow.get("edited_args"):
                    # The user rewrote this call on its card. approval_edits validated it in the route and the row's
                    # digest was re-bound to it; from here on the edited arguments are THE call: they run, are
                    # journaled, verified and queued (outbox) in place of the model's. Never read from the model's call.
                    original_args, args = args, arow["edited_args"]
                    edit_info = {"original_arguments": original_args, "edited_arguments": args, "edited_by": "user"}
                    # The dispatch re-runs the core guards and tool.pre on the edited call, but not tool.permission.
                    why, _ = await guards.extension_gate(core.toolbox.bus, c["name"], args, danger=danger, mode=mode,
                                                         tainted=bool(st.tool_ctx.get("tainted")))
                    if why:
                        pre = tools.denied(c["name"], why)
                if st.parked:
                    # The reply ends here, still owing this call an answer. The row stays pending
                    # and decidable; the desk moves from a live waiting state to `blocked`, which
                    # is the one that means "no run is coming back for this on its own".
                    if st.desk_id:
                        core.desks.set_status(st.desk_id, "blocked",
                                         reason="plan" if plan is not None else
                                         ("question" if c["name"] in core.QUESTION_TOOLS else "approval"),
                                         run_id=None)
                    # The reply ends here, but what it streamed so far is the user's to keep: finish the row
                    # (the card stays on it as pending) and record what this turn spent, as the normal end does.
                    kept = rnd.tool_events + ([pending_card] if pending_card else [])
                    core.convos.finish_message(rnd.am["id"], "".join(rnd.buf).strip(), None, st.used, kept, rnd.tracer.spans,
                                          st.tsum.stored(), attachments=st.tool_ctx.get("reply_attachments"))
                    core.convos.touch(st.conv_id)
                    if st.run is not None:
                        st.run.partial, st.run.cost, st.run.rounds = st.partial, st.meter.cost, st.meter.rounds
                    yield "parked", {"message_id": rnd.am["id"], "call_id": uid, "name": c["name"]}
                    # Persist the reply with its card still pending, so the transcript keeps a
                    # card the user can answer after a reload; then end like any other reply,
                    # so a watching window stops showing it as streaming.
                    card = {"id": uid, "name": c["name"], "arguments": args, "result_preview": "", "duration_ms": 0,
                            "error": None, "pending": True, "needs_approval": True, "forced": forced, "parked": True}
                    rnd.tool_events.append(card)
                    rnd.tracer.end(tspan, {"parked": True})
                    text = "".join(rnd.buf).strip()
                    reasoning = st.tsum.stored()
                    core.convos.finish_message(rnd.am["id"], text, None, st.used, rnd.tool_events, rnd.tracer.spans, reasoning,
                                          attachments=st.tool_ctx.get("reply_attachments"))
                    core.convos.touch(st.conv_id)
                    if st.run is not None:
                        st.run.partial, st.run.cost, st.run.rounds = None, st.meter.cost, st.meter.rounds
                    yield "done", {"id": rnd.am["id"], "error": None, "context_used": cite_slim(st.used), "tool_events": rnd.tool_events,
                                   "trace": rnd.tracer.spans, "stopped": False, "partial": None, "segment": False,
                                   "tainted": st.tool_ctx["tainted"], "taint_sources": st.tool_ctx["taint_sources"],
                                   "reasoning": reasoning, "outcome": None, "error_kind": None, "parked": uid,
                                   "attachments": st.tool_ctx.get("reply_attachments") or None}
                    st.ended = True
                    return
                if st.run is not None:
                    st.run.set_status("running")
                if st.desk_id and not st.in_side:
                    # Carrying the plan id onto the desk is what makes the plan outlive this turn:
                    # `_should_chain` reads `plans.remaining(desk["plan_id"])`, and claim_run reads
                    # it to tell a desk that has a plan from one that still needs to draft one.
                    approved_plan = core.plans.by_call(uid) if plan is not None else None
                    core.desks.set_status(st.desk_id, "working", run_id=st.run.run_id if st.run else None,
                                     plan_id=(approved_plan or {}).get("plan_id")
                                     if (approved_plan or {}).get("status") == "approved" else None,
                                     question="" if c["name"] in core.QUESTION_TOOLS else None)
                st.meter.paused += time.time() - approval_t0  # approval waits are the user's time, not the run's
                t0 = time.time()  # don't count waiting time as tool time
                if decision in ("allow", "allow_host") and forced:
                    core._approve_url(st.tool_ctx, args, decision == "allow_host")
                if decision == "allow_host":
                    decision = "allow"
                if decision == "always_session":
                    # Scoped to this chat and to exactly what the card named; a forced card is answered once.
                    if not forced:
                        permrules.SESSION.add(st.conv_id, perm.keys)
                    decision = "allow"
                elif decision == "always_rule":
                    decision = "allow"  # POST /approvals already saved the rules, or refused to for a forced card
                granted = decision in ("always_chat", "always_global")
                # A tainted reply cannot buy a standing grant, and neither can a plan card: 'always' on
                # propose_plan would leave the plan with no approval at all.
                # An external write, or booking unattended work, is never granted whole-tool: only a patterned rule
                # (always_rule) can stand. Toolbox.effective would cap such a grant back to ask anyway.
                standing = granted and not forced and c["name"] != PLAN_TOOL and not is_source_tool(c["name"]) and (
                    spec is None or not core.toolbox.ask_locked(spec))  # a connector tool (no spec) keeps its schema-bound grant; a source's tools always ask
                if granted and not standing:
                    decision = "allow"  # one-shot
                elif decision == "always_chat":
                    # MCP grants live in mcp_grants, not in the settings tool maps: a grant there is
                    # bound to the schema it approved, so a server that rewrites the tool re-asks.
                    if core.mcp_is(c["name"]):
                        core.mcp_store.set_grant(c["name"], "on", "chat", st.conv_id)
                    else:
                        # Re-read: the approval may have waited for hours, and a tool switched off in the meantime
                        # must stay off. Merge into the current map, never the one read at the start of the reply.
                        fresh = (core.convos.get(st.conv_id) or st.conv)["settings"].get("tools") or {}
                        core.convos.update(st.conv_id, {"settings": {"tools": {**fresh, c["name"]: "on"}}})
                        st.conv["settings"].setdefault("tools", {})[c["name"]] = "on"
                    st.modes[c["name"]] = "on"
                    decision = "allow"
                elif decision == "always_global":
                    if core.mcp_is(c["name"]):
                        core.mcp_store.set_grant(c["name"], "on", "global")
                    else:
                        # Re-read for the same reason: the Settings page may have changed the map during the wait.
                        permissions.update(core.db, lambda cur: {"tools": {**(cur["tools"] or {}), c["name"]: "on"}})
                    st.modes[c["name"]] = "on"
                    decision = "allow"
                if standing:
                    # the grant changed modes; keep the schemas in step
                    st.tool_schemas = self.schemas()
                if plan is not None:
                    # The decision was recorded on the plan by whoever answered it (the route, or the stop
                    # above), including any step the user edited: re-read it rather than trust `args`.
                    plan = core.plans.by_call(uid) or plan
                    if st.desk_id and plan.get("status") == "approved":
                        # A plan approved in this very reply is the desk's active plan from here
                        # on: `active_plan` was read before it existed, and the calls that follow
                        # are exactly the ones it authorises. Drafting is over, so the block on
                        # consequential calls lifts with it - otherwise the plan the user just
                        # approved could never be carried out.
                        st.active_plan, st.planning = plan, False
                        st.tool_ctx["planning"] = False
                        # Drafting is over: the tools held back above are offered again.
                        st.tool_schemas = self.schemas()
                    # Published on the run's own stream, so a window that was watching the card
                    # sees the answer even when it was given somewhere else.
                    yield "plan_decision", {"message_id": rnd.am["id"], "call_id": uid, "plan": plan}
            was_tainted, was_blocked = st.tool_ctx["tainted"], c["name"] in st.blocked
            sources_before = len(st.tool_ctx["taint_sources"])  # grows only if this call brought untrusted text
            ran = False  # only a call that really executed says anything about being stuck
            mcp_ran = False  # the connector was actually contacted: whatever it said, error or not, is third-party text
            if was_blocked:
                result: Any = tools.denied(c["name"], f"failing {limits.loop_setting(st.cfg, 'loopToolErrorLimit')} times in a row and disabled for the rest of this reply")
            elif pre is not None:
                # An unusable plan, a step of a plan the user rejected, or a plan-mode refusal.
                # Ahead of the `off` branch: plan mode turns the mode off to stop the call, and
                # the model must hear "put it in a plan", not "turned off, do not retry".
                result = pre
            elif mode == "off":
                result = tools.denied(c["name"], "not loaded; call mcp_tool_search first"
                                      if st.mcp_defer and core.mcp_is(c["name"]) and c["name"] in st.mcp_modes
                                      else "turned off for this chat")
            elif plan is not None:
                result = core.plans.model_result(plan)  # the decision, and the arguments the user actually authorised
            elif decision != "allow":
                # A note typed with the denial goes back as the result and the reply carries on.
                result = (tools.tool_error(f"{c['name']} was declined by the user, who said: {deny_note}",
                                           alternative="follow what the user said, or ask them what they would like instead")
                          if deny_note else tools.denied(c["name"], tools.DISCARDED if c["name"] == "gmail_send" else "just declined by the user"))
            elif asks and c["name"] in core.QUESTION_TOOLS and (answer := ((core.run_store.approval(uid) or {}).get("note")
                                                                   or deny_note or "").strip()):
                # The card was answered while this reply was still holding it, so the answer goes
                # straight back as the result and the turn carries on. Running the tool body here
                # would block the desk on a question the user has just answered.
                result = tools.answered(answer, args.get("options"))
            elif proposing and st.run is not None:
                # A job or a 'propose' desk: the call (built-in or connector) is a proposals row, and nothing is contacted.
                result, ran = core._propose(st.run, c["name"], args, uid, st.tool_ctx), True
            elif core.mcp_is(c["name"]):
                st.inflight = {"id": uid, "name": c["name"], "arguments": args}
                contact = Contact(functools.partial(core._connector_call, c["name"], args, hook_ctx(st.tool_ctx)))
                result, interrupted = await core._await_tool(dispatch_tool(core.toolbox, st.tool_ctx, c["name"], args, call_id=uid,
                                                                      connector=contact), st.stop,
                                                        grace=core.STOP_GRACE_SECONDS if danger in core.IDEMPOTENT_DANGER else 0.0)
                ran = not interrupted
                mcp_ran = contact.reached and ran
            elif not asks and (wkey := tools.call_key(c["name"], args)) in warm:
                # Started with its neighbours; an identical call in the round shares this one run.
                result = await warm[wkey]
                if isinstance(result, dict):
                    result = dict(result)
                if warm_ctx[wkey].get("tainted"):
                    st.tool_ctx["tainted"] = True
                st.tool_ctx["taint_sources"].extend(warm_ctx[wkey]["taint_sources"])
                ran = True
            else:
                st.tool_ctx["fs_outside_ok"] = fs_ask  # the user approved this credential-store access or write
                st.inflight = {"id": uid, "name": c["name"], "arguments": args}
                self.accounted.add(uid)
                try:
                    result, interrupted = await core._await_tool(
                        core._call_tool(st.run, rnd.n, c["name"], args, st.tool_ctx, uid), st.stop,
                        grace=core.STOP_GRACE_SECONDS if danger in core.IDEMPOTENT_DANGER else 0.0)
                finally:
                    st.tool_ctx["fs_outside_ok"] = False
                ran = not interrupted
            if interrupted:
                # The journal row stays `started`, so a later identical call reports unknown_outcome rather than repeating it.
                result = tools.tool_error("Stopped by the user before it finished; whether it took effect is unknown."
                                          if danger in core.IDEMPOTENT_DANGER else "Stopped by the user.")
            if first_use and isinstance(result, dict) and not was_blocked:
                # The model never saw this schema: the first call of a deferred tool gets it (the tool is loaded from the next round on).
                fn_ = (st.tool_ctx["mcp_schemas"].get(c["name"]) or {}).get("function") or (spec.schema()["function"] if spec else {})
                result["schema"] = {"description": str(fn_.get("description") or "")[:400], "parameters": fn_.get("parameters")}
            hint = st.denials.note()
            if hint and isinstance(result, dict):
                result["permission_note"] = hint
            st.denials.record(bool(perm.refusal) or unattended or reviewer_denied or (asks and decision != "allow"))
            ms = int((time.time() - t0) * 1000)
            # images (e.g. matplotlib figures from run_python) go to the UI, not to the model
            images = result.pop("images", None) if isinstance(result, dict) else None
            # likewise the side panel's content (the `show` tool): the model keeps only the one-line receipt
            show = result.pop("show", None) if isinstance(result, dict) else None
            research = result.pop("research", None) if isinstance(result, dict) else None  # deep_research's trail, UI only
            dashboard = result.pop("dashboard", None) if isinstance(result, dict) else None  # dashboard_open: the UI renders it inline
            preview = summarize_result(result)
            err = result.get("error") if isinstance(result, dict) else None
            if c["name"] not in workers_mod.FORCED_ALLOW:
                st.work_ms += ms
            if not err:
                rnd.ok_calls.append(c["name"])
            # consecutive argument errors and crashes only: a not-found or empty answer neither counts nor resets
            if not err:
                st.tool_errors[c["name"]] = 0
            elif spec is None or tools.counts_toward_block(result):  # a connector tool's error text is not ours to classify
                st.tool_errors[c["name"]] = st.tool_errors.get(c["name"], 0) + 1
            if ran and not err and st.run is not None:
                st.run.tool_ok += 1  # a desk turn's progress, beside steps_consumed (_chain_kind)
            if st.tool_errors.get(c["name"], 0) >= limits.loop_setting(st.cfg, "loopToolErrorLimit"):
                st.blocked.add(c["name"])
            # An MCP result is third-party text by definition, so it taints like a web fetch does.
            # A connector's error text is as untrusted as its output, so it taints either way.
            tainted = decision == "allow" and (mcp_ran or (not err and core.toolbox.taints(c["name"])))
            if tainted:
                st.tool_ctx["tainted"] = True  # Toolbox.call sets this for built-ins; _mcp_call cannot reach ctx
                if not was_tainted:
                    yield "taint", {"message_id": rnd.am["id"], "source": c["name"]}
                st.tool_ctx["taint_sources"].append(c["name"])
            event = {"id": uid, "name": c["name"], "arguments": args, "result_preview": preview, "duration_ms": ms,
                     "error": err, "images": images or None, "show": show or None, "research": research or None, "dashboard": dashboard or None, **edit_info,
                     "undo": result.get("undo") if isinstance(result, dict) and isinstance(result.get("undo"), dict) else None,
                     "approval": (("plan" if claimed else decision) if mode == "ask" and not invalid else None),
                     "plan": {"plan_id": claimed["plan_id"], "idx": claimed["idx"], "title": claimed["title"]} if claimed else None,
                     "forced": forced, "tainted": tainted, "blocked": c["name"] if was_blocked else None, "breaker": st.partial,
                     **({"interrupted": True, "pending": False} if interrupted else {}),
                     **({"repaired": True} if c["_repaired"] else {}),
                     **({"invalid": invalid} if invalid else {}),
                     "blocked_by": "plan_mode" if blocked_reason == PLAN_BLOCKED else None,
                     "proposal": (result.get("proposal_id") if proposing and isinstance(result, dict) else None),
                     **({"review": review} if review else {})}
            stuck = None
            if st.detector is not None:
                if ran or (c["name"] in core.QUESTION_TOOLS and isinstance(result, dict) and result.get("status") == "answered"):
                    st.detector.observe(c["name"], args, result)  # an answered question is the user's input, not a refused call
                else:
                    st.detector.skip(c["name"])  # a streak of calls that never ran ends the run like any other stuck shape
                stuck = st.detector.check()
                if stuck and st.stuck_hits == 0:
                    st.stuck_hits = 1
                    st.detector.reset()  # the model gets a fresh run at it; the same shape again ends tool use
                    event["breaker"] = "stuck_nudge"
                elif stuck:
                    st.stuck_hits += 1
                    st.partial, st.stop_text = "loop", STUCK_STOP.format(detail=stuck.detail)
                    event["breaker"] = "stuck"
            rnd.tracer.end(tspan, {"result_chars": len(preview), "images": len(images or []), **({"stuck": stuck.pattern} if stuck else {})}, error=err)
            if claimed is not None:
                # The step was spent at the gate; this records whether the call it authorised
                # actually worked, so the plan can be read after the fact.
                core.plans.finish(uid, not err, err)
            rnd.tool_events.append(event)
            st.inflight = None
            yield "tool_result", {"message_id": rnd.am["id"], **event}
            yield "span", {"message_id": rnd.am["id"], "span": tspan}
            for_model = {**result, "images_shown_to_user": [i["name"] for i in images]} if images and isinstance(result, dict) else result
            if edit_info and isinstance(for_model, dict):
                # The model proposed one thing and the user ran another: say so, or it reports the wrong call as done.
                for_model = {**for_model, "note": "The user edited these arguments before approving; this ran with their version: "
                             + json.dumps(args, default=str)[:1500]}
            # Small results go in whole; a big one is stored and replaced by a handle the model can
            # page with read_tool_result, so nothing is silently truncated away. See working.py.
            # Anything saved while this run is tainted can carry that text, including a script
            # that prints it. Paging the handle later has to taint again, even after the banner is cleared.
            brought_untrusted = bool(st.tool_ctx.get("tainted"))
            # Fenced per call, not per run: a safe read after a web read is not wrapped, or the fence means nothing.
            fence = st.fence_nonce if len(st.tool_ctx["taint_sources"]) > sources_before else None
            content, rid = core.tool_results.render(st.conv_id, rnd.am["id"], c["name"], for_model, untrusted=brought_untrusted, fence=fence,
                                                  inline_chars=limits.loop_setting(st.cfg, "toolResultInlineChars"))
            if rid:
                event["result_id"] = rid  # persisted with the tool event, so later turns can name the handle
            if stuck and event["breaker"] == "stuck_nudge":
                content = f"{content}\n\n[stuck_notice] {STUCK_NUDGE.format(detail=stuck.detail)}"
            st.messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
            # Persisted with the event (after the SSE copy went out) so the next turn replays exactly this text: no image bytes.
            event["call_id"], event["for_model"] = c["id"], content
            if c["name"] == "mcp_tool_search":
                # A search loads schemas: bring their modes in (grants and ask are already resolved
                # in mcp_modes) and offer them from the next round.
                for _slug in st.tool_ctx["mcp_loaded"]:
                    if _slug in st.mcp_modes:
                        st.modes.setdefault(_slug, st.mcp_modes[_slug])
                st.tool_schemas = self.schemas()
            elif c["name"] == "tool_search" or (c["name"] == "skill_view" and st.tool_ctx["deferred"] and ran):
                if c["name"] == "skill_view":
                    # A skill's steps name the tools it uses: load their groups along with it.
                    self.load_groups(set(re.findall(r"[a-z][a-z0-9_]+", json.dumps(result, default=str))) & st.tool_ctx["deferred"])
                st.tool_schemas = self.schemas()
            if st.tool_ctx.pop("plan_changed", None):
                yield "plan", {"conversation_id": st.conv_id, "steps": (core.work_plans.get(st.conv_id) or {}).get("steps") or []}
        # Warmed reads nobody consumed (Stop, a loop break) are cancelled and reaped now, not at run end.
        pending = [t for t in st.warm_tasks if not t.done()]
        for t in pending:
            t.cancel()
        await asyncio.gather(*st.warm_tasks, return_exceptions=True)
        st.warm_tasks.clear()
