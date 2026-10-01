"""Action plans: the single-use bind, first-wins decisions, and decide_call's ordering.

The invariants under test are the ones a bug makes silent rather than loud. `claim` must be
single-use and digest-exact, or an approved step becomes a standing grant. `decide` must be
first-wins, or two windows approve the same plan twice. And `decide_call` must compute the taint
verdict BEFORE it claims, or a step is burnt on a call the user is then shown a card for and denies —
a step that can never be reclaimed.

This file deliberately does not import personal_os.app or personal_os.tools: the rules live in
plans.py as pure functions precisely so they can be asserted without the chat loop, and a local
`Spec` stub stands in for ToolSpec (decide_call only ever reads .danger and .taints).

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_plans.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="planstest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import plans as plansmod  # noqa: E402
from personal_os.db import Database, new_id  # noqa: E402
from personal_os.plans import (  # noqa: E402
    MAX_STEPS, PLAN_BLOCKED, PLAN_TOOL, PROPOSE_ONLY, STEP_REJECTED, ActionPlans, autoplan,
    decide_call, normalize_plan, parse_plan_edits, taint_expected,
)
from personal_os.runlog import args_digest  # noqa: E402

passed = 0

CONV = "conv-1"


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


class Spec:
    """The two fields decide_call and normalize_plan read off a ToolSpec."""

    def __init__(self, danger: str = "safe", taints: bool = False) -> None:
        self.danger, self.taints = danger, taints


SPECS: dict[str, Any] = {
    PLAN_TOOL: Spec("plan"),
    "desk_ask": Spec("plan"),
    "doc_read": Spec("safe"),
    "web_search": Spec("network", taints=True),
    "doc_create": Spec("writes"),
    "run_python": Spec("executes"),
    "gmail_send": Spec("external"),
}
MODES = {n: ("ask" if s.danger == "external" else "on") for n, s in SPECS.items()}


def gate(name: str, mode: str, ctx: dict[str, Any]) -> str:
    """Toolbox.gate (tools.py:352-357), copied so this test does not import tools.py."""
    spec = SPECS.get(name)
    if spec and spec.danger == "external" and mode == "on" and ctx.get("tainted"):
        return "ask"
    return mode


def fresh() -> ActionPlans:
    return ActionPlans(Database(tempfile.mkdtemp(prefix="plans-")))


SEND = {"to": "dana@example.com", "subject": "Hi", "body": "x"}


def proposal(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"title": "A plan", "intent": "do the thing", "steps": list(steps)}


def opened(ap: ActionPlans, *steps: dict[str, Any], conversation_id: str = CONV,
           desk_id: str | None = None, tainted: bool = False) -> dict[str, Any]:
    norm, err = normalize_plan(proposal(*steps), MODES, SPECS)
    assert err is None, err
    return ap.open(conversation_id=conversation_id, desk_id=desk_id, run_id="run-1", message_id="msg-1",
                   call_id=new_id(), title="A plan", intent="do the thing", steps=norm, tainted=tainted)


# ---- normalize_plan ----
def test_normalize_plan_computes_digest_danger_and_taint() -> None:
    """The card is built from this: the digest the claim will match, and the danger it will show."""
    steps, err = normalize_plan(proposal(
        {"title": "Look it up", "tool": "web_search", "arguments": {"query": "acme"}, "why": "numbers"},
        {"title": "Think", "why": "judgement"},
        {"title": "Send it", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
    ), MODES, SPECS)
    check(err is None, "a plan of available, enabled tools normalizes")
    check([s["idx"] for s in steps] == [1, 2, 3], "steps are numbered from 1, as the card labels them")
    check(steps[0]["args_digest"] == args_digest({"query": "acme"}), "the digest is runlog's, not a local one")
    check(steps[1]["tool"] == "" and steps[1]["args_digest"] == "", "a reasoning step has no tool and no digest")
    check(steps[1]["arguments"] == {}, "a missing arguments object normalizes to {}")
    check(steps[2]["danger"] == "external", "ToolSpec.danger is snapshotted onto the step")
    check(steps[0]["taints"] is True and steps[2]["taints"] is False, "taints is read off the spec")


def test_normalize_plan_rejects_unknown_unavailable_and_off() -> None:
    """Validated against the unfiltered mode map, so no card promises what cannot execute."""
    _, err = normalize_plan(proposal({"title": "x", "tool": "nope", "arguments": {}}), MODES, SPECS)
    check(err and "unknown tool 'nope'" in err["error"], "an unknown tool is refused before the card")

    _, err = normalize_plan(proposal({"title": "x", "tool": "gmail_send", "arguments": SEND}), MODES, SPECS,
                            available=lambda n: n != "gmail_send")
    check(err and "not available" in err["error"], "a tool whose integration is disconnected is refused")

    off = {**MODES, "doc_create": "off"}
    _, err = normalize_plan(proposal({"title": "x", "tool": "doc_create", "arguments": {"title": "t"}}), off, SPECS)
    check(err and "turned off" in err["error"], "a tool the user turned off is refused")

    _, err = normalize_plan({"title": "t", "steps": []}, MODES, SPECS)
    check(err and "non-empty" in err["error"], "an empty plan is refused: answer instead of proposing nothing")

    _, err = normalize_plan(proposal({"title": "x", "tool": "doc_create", "arguments": "nope"}), MODES, SPECS)
    check(err and "must be an object" in err["error"], "arguments must be an object")

    steps, err = normalize_plan({"title": "t", "steps": [{"title": f"s{i}"} for i in range(MAX_STEPS + 5)]},
                                MODES, SPECS)
    check(err is None and len(steps) == MAX_STEPS, f"a long plan is capped at {MAX_STEPS} steps")


def test_parse_plan_edits_shapes() -> None:
    """The edit payload is user input off the wire; a bad shape must raise, never half-apply."""
    check(parse_plan_edits(None) == {}, "no steps means no edits")
    check(parse_plan_edits([{"idx": 2, "arguments": {"a": 1}}, {"idx": 4, "drop": True}])
          == {2: {"a": 1}, 4: None}, "arguments edit an index, drop clears it")
    for bad, why in (("nope", "a string is not a list of edits"),
                     ([{"arguments": {}}], "an edit without an idx"),
                     ([{"idx": "2", "arguments": {}}], "a string idx"),
                     ([{"idx": 1}], "neither arguments nor drop"),
                     ([{"idx": 1, "arguments": []}], "arguments that are not an object")):
        try:
            parse_plan_edits(bad)
            check(False, f"{why} must raise")
        except ValueError:
            check(True, f"{why} raises ValueError")


# ---- the repo ----
def test_open_supersede_active_and_latest() -> None:
    """One live plan per conversation, and `active` is what turns plan mode back off."""
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    check(ap.get(p["plan_id"])["status"] == "pending", "a new plan is pending")
    check(ap.active(CONV) is None, "a pending plan is not active: planning still applies")
    check(ap.by_call(p["call_id"])["plan_id"] == p["plan_id"], "a plan is findable by its approval call")
    ap.decide(p["plan_id"], "approve")
    check(ap.active(CONV)["plan_id"] == p["plan_id"], "an approved plan with unconsumed steps is active")

    ap.supersede(CONV)
    p2 = opened(ap, {"title": "Again", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    check(ap.get(p["plan_id"])["status"] == "superseded", "opening a plan supersedes the live one")
    check(ap.latest(CONV)["plan_id"] == p2["plan_id"], "latest is the newest plan, decided or not")
    check(ap.active(CONV) is None, "a superseded plan is no longer active")


def test_a_superseded_plans_steps_are_not_claimable() -> None:
    """The variant the user said no to must not be answered by the plan it replaced.

    Approve P1 [gmail_send SEND]; the agent proposes P2 with the same call; the user rejects P2.
    P1 is 'superseded' by then, but its step was left 'approved' — so the call still claimed it and
    ran with no card, which is the user's rejection granted through the earlier plan."""
    ap = fresh()
    p1 = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p1["plan_id"], "approve")
    ap.supersede(CONV)
    p2 = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "again"})
    ap.decide(p2["plan_id"], "reject", decided_by="user")

    check(ap.get(p1["plan_id"])["status"] == "superseded", "P1 is superseded, as supersede() says")
    check(ap.claim(p1["plan_id"], "gmail_send", SEND, "call-a") is None,
          "a superseded plan's step grants nothing: claim() is scoped to a live plan")
    check(ap.get(p1["plan_id"])["steps"][0]["status"] == "dropped",
          "and the step itself was demoted with the plan, not left reading as live")
    check(ap.active(CONV) is None, "nothing in this conversation is active any more")


def test_a_plan_whose_only_live_step_calls_nothing_is_not_active() -> None:
    """A tool-less reasoning step is promoted to 'approved' and can never be claimed, so counting
    it would leave the plan live for ever and gate every unplanned call behind it."""
    ap = fresh()
    p = opened(ap,
               {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Decide what to do next", "why": "no tool: this is the judgement"})
    ap.decide(p["plan_id"], "approve")
    check(ap.active(CONV)["plan_id"] == p["plan_id"], "while a real step is unspent the plan is active")
    ap.claim(p["plan_id"], "gmail_send", SEND, "call-a")
    ap.finish("call-a", True)
    steps = {s["idx"]: s for s in ap.get(p["plan_id"])["steps"]}
    check(steps[2]["status"] == "approved", "the reasoning step is still sitting there, as decide() left it")
    check(ap.active(CONV) is None, "but it is not something claim() could spend, so the plan is done")


def test_for_desk_ignores_superseded() -> None:
    ap = fresh()
    first = opened(ap, {"title": "a", "tool": "doc_create", "arguments": {"title": "a"}}, desk_id="desk-1")
    ap.supersede(CONV)
    second = opened(ap, {"title": "b", "tool": "doc_create", "arguments": {"title": "b"}}, desk_id="desk-1")
    check(ap.for_desk("desk-1")["plan_id"] == second["plan_id"], "a desk shows its live plan")
    check(first["plan_id"] != second["plan_id"], "the superseded one is a different row, still on disk")


def test_decide_is_first_wins() -> None:
    """Two windows, one plan: the row's rowcount is the lock, not a process-memory flag."""
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    first = ap.decide(p["plan_id"], "approve", note="go")
    second = ap.decide(p["plan_id"], "reject", note="actually no")
    check(first and first["status"] == "approved", "the first decision lands")
    check(second is None, "the second decision is refused, not applied")
    check(ap.get(p["plan_id"])["status"] == "approved", "the plan keeps the first decision")
    check(ap.decide(p["plan_id"], "shred") is None, "an unknown decision is refused")


def test_decide_edit_recomputes_the_digest_and_drops() -> None:
    """An edited step binds to the user's arguments; a dropped step is never claimable."""
    ap = fresh()
    p = opened(ap,
               {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Write", "tool": "doc_create", "arguments": {"title": "Draft"}, "why": "keep"},
               {"title": "Read", "tool": "doc_read", "arguments": {"doc": "d1"}, "why": "context"})
    mine = {**SEND, "body": "corrected"}
    out = ap.decide(p["plan_id"], "edit", steps=parse_plan_edits(
        [{"idx": 1, "arguments": mine}, {"idx": 2, "drop": True}]), note="fixed the body")
    assert out is not None
    by_idx = {s["idx"]: s for s in out["steps"]}
    check(by_idx[1]["arguments"] == mine, "the step carries the user's arguments")
    check(by_idx[1]["args_digest"] == args_digest(mine), "the digest is recomputed from them")
    check(by_idx[1]["edited"] is True, "the edited step is flagged")
    check(by_idx[2]["status"] == "dropped", "an explicitly dropped step is dropped")
    check(by_idx[3]["status"] == "approved" and by_idx[3]["edited"] is False,
          "a step the edit did not mention stays approved and unedited")
    check(ap.claim(p["plan_id"], "gmail_send", SEND, "c1") is None,
          "the agent's original arguments no longer claim anything")
    check(ap.claim(p["plan_id"], "doc_create", {"title": "Draft"}, "c2") is None, "a dropped step cannot be claimed")
    check(ap.claim(p["plan_id"], "gmail_send", mine, "c3") is not None, "the user's arguments claim the step")


def test_decide_reject_marks_every_step() -> None:
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    out = ap.decide(p["plan_id"], "reject", note="no")
    assert out is not None
    check(out["status"] == "rejected", "the plan is rejected")
    check(out["steps"][0]["status"] == "rejected", "its steps are rejected with it")
    check(ap.claim(p["plan_id"], "gmail_send", SEND, "c1") is None, "a rejected step is not claimable")
    check(ap.model_result(out)["status"] == "rejected", "the model is told it was rejected")
    check("Stop" in ap.model_result(out)["instruction"], "and told to stop rather than improvise around it")


def test_claim_is_single_use_and_digest_exact() -> None:
    """The whole bet: one approved step is one call with one set of arguments, once."""
    ap = fresh()
    p = opened(ap,
               {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Send again", "tool": "gmail_send", "arguments": SEND, "why": "twice on purpose"})
    ap.decide(p["plan_id"], "approve")
    first = ap.claim(p["plan_id"], "gmail_send", SEND, "call-a")
    check(first and first["idx"] == 1, "the first claim takes the lowest unconsumed matching step")
    second = ap.claim(p["plan_id"], "gmail_send", SEND, "call-b")
    check(second and second["idx"] == 2, "a duplicated step is a second grant, deliberately")
    check(ap.claim(p["plan_id"], "gmail_send", SEND, "call-c") is None, "a third identical call claims nothing")

    ap2 = fresh()
    q = opened(ap2, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap2.decide(q["plan_id"], "approve")
    changed = {**SEND, "body": "X"}
    check(ap2.claim(q["plan_id"], "gmail_send", changed, "call-a") is None,
          "one changed character claims nothing")
    check(ap2.claim(q["plan_id"], "doc_create", SEND, "call-a") is None, "a different tool claims nothing")
    check(ap2.claim(q["plan_id"], "gmail_send", SEND, "call-a") is not None, "the exact call still claims")


def test_claim_requires_an_approved_plan() -> None:
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    check(ap.claim(p["plan_id"], "gmail_send", SEND, "call-a") is None, "a pending plan grants nothing")


def test_finish_moves_a_consumed_step() -> None:
    ap = fresh()
    p = opened(ap,
               {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Write", "tool": "doc_create", "arguments": {"title": "t"}, "why": "keep"})
    ap.decide(p["plan_id"], "approve")
    ap.claim(p["plan_id"], "gmail_send", SEND, "call-a")
    ap.claim(p["plan_id"], "doc_create", {"title": "t"}, "call-b")
    ap.finish("call-a", True)
    ap.finish("call-b", False, "disk full")
    by_idx = {s["idx"]: s for s in ap.get(p["plan_id"])["steps"]}
    check(by_idx[1]["status"] == "done" and by_idx[1]["result_error"] is None, "a successful call lands as done")
    check(by_idx[2]["status"] == "failed" and by_idx[2]["result_error"] == "disk full", "a failure keeps its reason")
    check(ap.remaining(p["plan_id"]) == [], "nothing is left to do")


def test_rejected_blocklist_honours_only_the_user() -> None:
    """A Stop-induced rejection must not poison the rest of the conversation."""
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "reject", decided_by="stop")
    check(ap.rejected(CONV, "gmail_send", SEND) is False, "a stop-rejected step is not blocklisted")

    ap.supersede(CONV)
    q = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(q["plan_id"], "reject", decided_by="user")
    check(ap.rejected(CONV, "gmail_send", SEND) is True, "a user-rejected step is blocklisted")
    check(ap.rejected(CONV, "gmail_send", {**SEND, "body": "X"}) is False, "only those exact arguments")
    check(ap.rejected("other-conv", "gmail_send", SEND) is False, "and only in that conversation")


def test_block_is_a_checklist_with_the_approved_arguments() -> None:
    """Without this the plan is invisible from turn two: history replays prose only."""
    ap = fresh()
    p = opened(ap,
               {"title": "Look it up", "tool": "web_search", "arguments": {"query": "acme"}, "why": "numbers"},
               {"title": "Send it", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Tidy up", "tool": "doc_create", "arguments": {"title": "t"}, "why": "keep"})
    check(ap.block(p["plan_id"]) is None, "a pending plan has no checklist: nothing is approved yet")
    ap.decide(p["plan_id"], "approve", steps=parse_plan_edits([{"idx": 3, "drop": True}]))
    ap.claim(p["plan_id"], "web_search", {"query": "acme"}, "call-a")
    ap.finish("call-a", True)
    ap.claim(p["plan_id"], "gmail_send", SEND, "call-b")
    block = ap.block(p["plan_id"])
    assert block is not None
    check("[x] 1. Look it up" in block, "a finished step is ticked")
    check("[>] 2. Send it" in block, "a claimed step is in flight")
    check("Tidy up" not in block, "a dropped step is not offered back to the model")

    ap2 = fresh()
    q = opened(ap2, {"title": "Send it", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap2.decide(q["plan_id"], "approve")
    block2 = ap2.block(q["plan_id"])
    assert block2 is not None
    check("[ ] 1. Send it" in block2, "an unspent step is unticked")
    check('"body":"x"' in block2, "and carries its exact approved arguments, so the digest survives a new turn")


def test_model_result_reports_the_edit() -> None:
    ap = fresh()
    p = opened(ap,
               {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"},
               {"title": "Write", "tool": "doc_create", "arguments": {"title": "t"}, "why": "keep"})
    mine = {**SEND, "body": "corrected"}
    out = ap.decide(p["plan_id"], "edit", steps=parse_plan_edits(
        [{"idx": 1, "arguments": mine}, {"idx": 2, "drop": True}]))
    assert out is not None
    res = ap.model_result(out)
    check(res["status"] == "approved" and res["edited"] == [1], "the model is told which step the user changed")
    check(res["dropped"] == [2], "and which it dropped")
    check([s["arguments"] for s in res["steps"]] == [mine], "it is handed the arguments it is bound to")


# ---- expected taint ----
def test_taint_expected() -> None:
    """Approving a plan that reads the web IS approving the send that follows it (§4.6)."""
    plan = {"expected_taint": ["web_search"],
            "steps": [{"tool": "fetch_url", "status": "done"}, {"tool": "gmail_send", "status": "approved"}]}
    check(taint_expected(plan, []) is True, "no taint is trivially expected")
    check(taint_expected(None, ["web_search"]) is False, "without a plan nothing was predicted")
    check(taint_expected(plan, ["web_search"]) is True, "a tool the card named is expected")
    check(taint_expected(plan, ["fetch_url"]) is True, "so is a tool that actually claimed a step")
    check(taint_expected(plan, ["web_search", "read_document"]) is False, "one off-plan source is enough to void it")


def test_decide_drops_the_expected_taint_of_a_dropped_step() -> None:
    ap = fresh()
    p = opened(ap,
               {"title": "Look it up", "tool": "web_search", "arguments": {"query": "acme"}, "why": "numbers"},
               {"title": "Send it", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    check(p["expected_taint"] == ["web_search"], "the proposal predicts the read it contains")
    out = ap.decide(p["plan_id"], "edit", steps=parse_plan_edits([{"idx": 1, "drop": True}]))
    assert out is not None
    check(out["expected_taint"] == [], "dropping the read also drops the exemption it bought")


# ---- decide_call, rule by rule ----
def ctx_of(**kw: Any) -> dict[str, Any]:
    return {"conversation_id": CONV, "tainted": False, "taint_sources": [], "call_id": "uid-1", **kw}


def policy(ap: ActionPlans, name: str, args: dict[str, Any], *, planning: bool = False,
           plan: dict[str, Any] | None = None, autonomy: str = "plan",
           ctx: dict[str, Any] | None = None, raw_mode: str | None = None) -> Any:
    return decide_call(name, args, raw_mode=raw_mode or MODES.get(name, "off"), specs=SPECS,
                       ctx=ctx or ctx_of(), planning=planning, plan=plan, autonomy=autonomy,
                       gate=gate, plans=ap)


def test_rule_1_the_plan_tool_is_always_a_card() -> None:
    ap = fresh()
    pol = policy(ap, PLAN_TOOL, {"title": "t"}, raw_mode="on")
    check(pol.mode == "ask" and pol.is_plan, "propose_plan asks even when settings say on")
    check(pol.forced is False, "and it is not 'forced', so it can never buy a standing grant")


def test_rule_2_desk_ask_is_a_question_card() -> None:
    check(policy(fresh(), "desk_ask", {"question": "which one?"}, raw_mode="on").mode == "ask",
          "desk_ask always asks")


def test_rule_3_planning_blocks_consequential_tools() -> None:
    ap = fresh()
    for name in ("doc_create", "run_python", "gmail_send"):
        pol = policy(ap, name, {}, planning=True)
        # A reason fragment: tools.denied() supplies the subject, so a `{name}` here would double it.
        check(pol.deny == PLAN_BLOCKED, f"{name} is blocked while planning, with the wording")
        check(not PLAN_BLOCKED.startswith(name) and not PLAN_BLOCKED.endswith("."),
              "and the fragment carries neither a subject nor a full stop of its own")
        check(pol.forced is True, f"{name}'s block rides out as forced, so the UI says 'blocked while planning'")
    for name in ("doc_read", "web_search"):
        check(policy(ap, name, {}, planning=True).deny is None, f"{name} stays callable while planning")


def test_rule_4_a_propose_only_desk_cannot_act_outside() -> None:
    ap = fresh()
    check(policy(ap, "gmail_send", SEND, autonomy="propose").deny == PROPOSE_ONLY,
          "a propose-only desk may not send")
    check(policy(ap, "doc_create", {"title": "t"}, autonomy="propose").deny is None,
          "it may still write in the app")


def test_rule_5_unexpected_taint_asks_and_consumes_nothing() -> None:
    """The ordering that matters most: a step burnt on a denied call can never be reclaimed."""
    ap = fresh()
    p = opened(ap,
               {"title": "Look it up", "tool": "web_search", "arguments": {"query": "acme"}, "why": "numbers"},
               {"title": "Send it", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "approve")
    plan = ap.active(CONV)
    dirty = ctx_of(tainted=True, taint_sources=["read_document"])
    pol = decide_call("gmail_send", SEND, raw_mode="on", specs=SPECS, ctx=dirty, planning=False,
                      plan=plan, autonomy="plan", gate=gate, plans=ap)
    check(pol.mode == "ask" and pol.forced is True, "an unexpected taint forces the approved send back to a card")
    check(pol.claimed_step is None, "and claims nothing")
    step = [s for s in ap.get(p["plan_id"])["steps"] if s["idx"] == 2][0]
    check(step["status"] == "approved", "the step is still there to be spent once the user says yes")

    clean = ctx_of(tainted=True, taint_sources=["web_search"])
    pol = decide_call("gmail_send", SEND, raw_mode="on", specs=SPECS, ctx=clean, planning=False,
                      plan=plan, autonomy="plan", gate=gate, plans=ap)
    check(pol.mode == "on" and pol.claimed_step, "taint the plan predicted does not void the claim")


def test_rule_6_an_approved_step_runs_without_a_card() -> None:
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "approve")
    plan = ap.active(CONV)
    pol = policy(ap, "gmail_send", SEND, plan=plan)
    check(pol.mode == "on" and pol.forced is False, "the approved call runs, though external defaults to ask")
    check(pol.claimed_step, "and the policy reports which step it spent")
    again = policy(ap, "gmail_send", SEND, plan=plan)
    check(again.mode == "ask" and again.off_plan, "the same call a second time is off plan, and asks")


def test_rule_7_an_off_plan_mutating_call_asks() -> None:
    """A plan is a strong default, not a cage: off plan asks, it is not hard-denied."""
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "approve")
    plan = ap.active(CONV)
    pol = policy(ap, "doc_create", {"title": "unplanned"}, plan=plan)
    check(pol.mode == "ask" and pol.off_plan and pol.deny is None, "an unplanned write asks")
    safe = policy(ap, "doc_read", {"doc": "d1"}, plan=plan)
    check(safe.mode == "on" and safe.off_plan is False, "an unplanned read is not a card")


def test_rule_8_a_rejected_step_stays_rejected() -> None:
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "reject", decided_by="user")
    pol = policy(ap, "gmail_send", SEND)
    check(pol.deny == STEP_REJECTED, "the exact rejected call cannot fall through to doing the work anyway")
    check(policy(ap, "gmail_send", {**SEND, "body": "X"}).deny is None, "a different call is only a card")


def test_rule_9_falls_through_to_todays_gate() -> None:
    ap = fresh()
    check(policy(ap, "doc_read", {"doc": "d1"}).mode == "on", "a safe tool runs, as it does today")
    check(policy(ap, "gmail_send", SEND).mode == "ask", "an external tool asks, as it does today")
    pol = policy(ap, "gmail_send", SEND, raw_mode="on", ctx=ctx_of(tainted=True, taint_sources=["web_search"]))
    check(pol.mode == "ask" and pol.forced, "taint still upgrades an external tool to ask with no plan in play")
    check(policy(ap, "doc_create", {"title": "t"}, raw_mode="off").mode == "off", "an off tool stays off")


def test_a_plan_never_overrides_a_tool_the_user_turned_off() -> None:
    ap = fresh()
    p = opened(ap, {"title": "Send", "tool": "gmail_send", "arguments": SEND, "why": "asked"})
    ap.decide(p["plan_id"], "approve")
    pol = policy(ap, "gmail_send", SEND, plan=ap.active(CONV), raw_mode="off")
    check(pol.mode == "off", "turning the tool off after approving wins: they meant it")
    check([s for s in ap.get(p["plan_id"])["steps"]][0]["status"] == "approved", "and the step is not spent")


def test_autoplan_arms_on_the_first_mutating_call() -> None:
    check(autoplan("doc_create", specs=SPECS, mode_pref="auto", planning=False) is True,
          "a write arms planning in auto mode")
    check(autoplan("doc_read", specs=SPECS, mode_pref="auto", planning=False) is False, "a read does not")
    check(autoplan("doc_create", specs=SPECS, mode_pref="auto", planning=True) is False,
          "it does not re-arm once planning is on")
    check(autoplan("doc_create", specs=SPECS, mode_pref="off", planning=False) is False, "off means off")
    check(autoplan(PLAN_TOOL, specs=SPECS, mode_pref="auto", planning=False) is False,
          "and propose_plan itself never arms it")
    # After an approval `planning` is False again while mode_pref is still 'auto'. Without the
    # plan, the next call re-arms planning and rule 3 denies the very call the user authorised.
    ap = fresh()
    p = opened(ap, {"title": "Write", "tool": "doc_create", "arguments": {"title": "t"}, "why": "keep"})
    ap.decide(p["plan_id"], "approve")
    check(autoplan("doc_create", specs=SPECS, mode_pref="auto", planning=False,
                   plan=ap.active(CONV)) is False,
          "an approved plan suppresses re-arming, or in 'auto' it could never execute")


def test_an_ask_as_it_goes_desk_cards_every_change() -> None:
    """'ask' autonomy is a mode of its own, not a synonym for 'plan': nothing is withheld up
    front, and each mutating call is carded on its way out."""
    ap = fresh()
    for name, args in (("doc_create", {"title": "t"}), ("run_python", {"code": "1"}), ("gmail_send", SEND)):
        pol = policy(ap, name, args, autonomy="ask", raw_mode="on")
        check(pol.mode == "ask" and pol.deny is None, f"{name} is carded, not withheld, for an 'ask' desk")
        check(pol.forced is True, f"{name}'s card cannot buy a standing grant that switches the mode off")
    check(policy(ap, "doc_read", {"doc": "d1"}, autonomy="ask", raw_mode="on").mode == "on",
          "a read is left alone: 'ask' is about changes")
    check(policy(ap, "doc_create", {"title": "t"}, autonomy="ask", raw_mode="off").mode == "off",
          "and a tool the user turned off stays off")

    p = opened(ap, {"title": "Write", "tool": "doc_create", "arguments": {"title": "t"}, "why": "keep"})
    ap.decide(p["plan_id"], "approve")
    pol = policy(ap, "doc_create", {"title": "t"}, autonomy="ask", plan=ap.active(CONV))
    check(pol.mode == "on" and pol.claimed_step,
          "a step this desk did plan still runs on its approval: 8b sits below the bind")


def test_module_constants_are_the_ones_the_other_packages_import() -> None:
    """tools.py and app.py both import these names rather than restating the strings."""
    check(plansmod.PLAN_SAFE_DANGER == ("safe", "network", "plan"), "the planning allowlist is fixed")
    check(plansmod.PLAN_TOOL == "propose_plan", "the plan tool's name is fixed")
    check(plansmod.MAX_STEPS == 12, "a plan is at most twelve steps")
    check("held to" in plansmod.PLAN_TOOL_DESCRIPTION, "the description states the argument contract")
    check(len(plansmod.PLAN_TOOL_EXAMPLES) == 3, "three escalating examples")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
