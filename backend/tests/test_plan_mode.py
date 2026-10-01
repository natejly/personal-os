"""Planning mode through the real app: the three guards, the card, and the argument bind.

The one failure this file exists to catch is a silent one. Planning mode is worth nothing if a
withheld tool can still be reached — so every guard is asserted on its own, not just the three
together: the schema set the model is offered (§4.2), the decision function at the call site
(§4.3), and `Toolbox.call`'s own refusal, checked by calling it directly with `plan_phase` set
(§4.4). The bind is asserted the same way: approving step 1 approves sha256(canon(args)) for that
one call, so the same call twice, one changed character, and a step the user edited are each a
separate assertion.

`llm.stream_chat` is replaced by a scripted generator whose signature tracks the real one, the way
test_runs.py does it. Each scripted turn is one round: some text, then the tool calls the model
"made". The schema list each round was offered is recorded, because that list IS guard 1.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_plan_mode.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="planmodetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm, plans as plansmod, tools  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, aplans, toolbox  # noqa: E402
from personal_os.plans import MUTATING, PLAN_SAFE_DANGER, PLAN_TOOL  # noqa: E402
from personal_os.runlog import args_digest, canon  # noqa: E402
from test_runlog import CANON_FIXTURES  # noqa: E402  - the one fixture set planDigest.test.ts also pins

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0

# One scripted round per entry. {"text": str} answers in prose; {"calls": [{name, arguments}]} is a
# round of tool calls. `tools` collects the schema list each round was offered — guard 1 itself.
SCRIPT: dict[str, Any] = {"turns": [], "default": {"text": "All done."}, "tools": [], "names": [],
                          "sent": []}


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto") -> Any:
    SCRIPT["tools"].append([t["function"]["name"] for t in (tools or [])])
    # The shape of the message list every round is sent, so the assistant/tool pairing can be
    # asserted. A real provider rejects a system message wedged between a tool_calls turn and its
    # replies; a stub would happily stream on, so this is the only place that can catch it.
    SCRIPT["sent"].append([{"role": m.get("role"), "calls": len(m.get("tool_calls") or [])}
                           for m in messages])
    turn = dict(SCRIPT["turns"].pop(0)) if SCRIPT["turns"] else dict(SCRIPT["default"])
    # The closing round of a budget or breaker stop is sent with tool_choice "none"; a stub that
    # kept calling tools there would loop forever instead of ending the reply.
    calls = [] if tool_choice == "none" else list(turn.get("calls") or [])
    text = turn.get("text") or ("" if calls else "All done.")
    if text:
        yield {"type": "delta", "text": text}
    yield {"type": "end", "finish_reason": "tool_calls" if calls else "stop",
           "tool_calls": [{"id": f"call_{i}", "name": c["name"], "arguments": json.dumps(c.get("arguments") or {})}
                          for i, c in enumerate(calls)],
           "usage": None}


# app.py calls stream_chat with keywords, so a parameter added there must land on the stub too or
# every run dies before its first delta.
_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted_stream).parameters)
assert not _missing, f"_scripted_stream is missing {sorted(_missing)} from llm.stream_chat"

llm.stream_chat = _scripted_stream


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def script(*turns: dict[str, Any]) -> None:
    SCRIPT["turns"] = [dict(t) for t in turns]
    SCRIPT["tools"] = []
    SCRIPT["sent"] = []


def interleavings() -> list[str]:
    """Every place a non-`tool` message was sent between an assistant tool_calls turn and the
    replies to its calls, across every round recorded since the last script()."""
    bad: list[str] = []
    for r, seq in enumerate(SCRIPT["sent"]):
        for i, m in enumerate(seq):
            if m["role"] != "assistant" or not m["calls"]:
                continue
            after = [x["role"] for x in seq[i + 1:i + 1 + m["calls"]]]
            if after != ["tool"] * m["calls"]:
                bad.append(f"round {r}: {m['calls']} tool_calls at {i} answered by {after}")
    return bad


def plan_conv(mode: str = "always") -> str:
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"planMode": mode}})
    return cid


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {label}")


def live(conv_id: str) -> dict[str, Any]:
    return next((r for r in j("GET", "/runs") if r["conversation_id"] == conv_id), {})


def drain(conv_id: str) -> None:
    wait_until(lambda: not live(conv_id), f"the run on {conv_id} to end")


def card(conv_id: str, tool: str | None = None) -> dict[str, Any]:
    """The pending approval this conversation is blocked on. Deciding it is the test's job."""
    found: dict[str, Any] = {}

    def ready() -> bool:
        for row in j("GET", "/approvals"):
            if row["conversation_id"] == conv_id and (tool is None or row["tool"] == tool):
                found.update(row)
                return True
        return False

    wait_until(ready, f"an approval card for {tool or 'any tool'} on {conv_id}")
    return found


def events(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        event, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event and data:
            out.append((event, json.loads(data)))
    return out


def replay(conv_id: str) -> list[tuple[str, Any]]:
    """Every event of the finished run, from the ring. Safe to GET unthreaded: a run that has ended
    replays and closes rather than following live."""
    return events(client.get(f"/conversations/{conv_id}/stream?since=0").text)


def results(evs: list[tuple[str, Any]], name: str) -> list[dict[str, Any]]:
    return [d for e, d in evs if e == "tool_result" and d["name"] == name]


def offered(round_index: int) -> set[str]:
    return set(SCRIPT["tools"][round_index])


def propose(title: str, *steps: dict[str, Any]) -> dict[str, Any]:
    return {"name": PLAN_TOOL, "arguments": {"title": title, "intent": title,
                                             "steps": [{"why": "because the task needs it", **s} for s in steps]}}


def step(tool: str, arguments: dict[str, Any], title: str = "") -> dict[str, Any]:
    return {"title": title or tool, "tool": tool, "arguments": arguments}


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def titles() -> set[str]:
    return {t["title"] for t in j("GET", "/todos")}


class as_danger:
    """Temporarily relabel a tool's danger. Every `external` tool in the tree is a Google tool, and
    normalize_plan refuses a tool whose integration is not connected — so the only honest way to
    exercise §4.6's external/taint rule against the real app is to relabel one that is."""

    def __init__(self, name: str, danger: str) -> None:
        self.spec = toolbox.specs[name]
        self.danger, self.was = danger, self.spec.danger

    def __enter__(self) -> None:
        self.spec.danger = self.danger

    def __exit__(self, *exc: Any) -> None:
        self.spec.danger = self.was


def test_planning_withholds_every_mutating_tool() -> None:
    """Guard 1: the model is never offered what it must not call (§4.2)."""
    cid = plan_conv()
    script({"text": "This needs no actions at all."})
    j("POST", f"/conversations/{cid}/chat", {"content": "hello"})
    drain(cid)

    shown = offered(0)
    check(PLAN_TOOL in shown, "propose_plan is the one tool planning mode adds")
    check(not [n for n in shown if toolbox.specs[n].danger in MUTATING],
          f"no writes/executes/external tool is offered while planning, got {sorted(n for n in shown if toolbox.specs[n].danger in MUTATING)}")
    check("web_search" in shown and "fetch_url" in shown,
          "network survives the filter: a plan whose arguments were guessed is worth very little (§4.2)")
    check({"current_time", "search_memory"} <= shown, "safe tools are untouched")
    check(not [n for n in shown if toolbox.specs[n].group == "desk"], "a chat is offered no desk tool")
    check(all(toolbox.specs[n].danger in PLAN_SAFE_DANGER for n in shown),
          "PLAN_SAFE_DANGER is exactly what gets through")

    plain = j("POST", "/conversations", {})["id"]
    script({"text": "ok"})
    j("POST", f"/conversations/{plain}/chat", {"content": "hello"})
    drain(plain)
    check("todo_add" in offered(0), "with planMode off the same chat is offered the write tool")


def test_a_mutating_call_while_planning_is_denied_by_both_gates() -> None:
    """Guards 2 and 3, each asserted alone: either one on its own must stop the call."""
    cid = plan_conv()
    script({"calls": [call("todo_add", title="sneaked past the schema filter")]}, {"text": "I could not."})
    j("POST", f"/conversations/{cid}/chat", {"content": "add a todo"})
    drain(cid)

    hit = results(replay(cid), "todo_add")
    check(len(hit) == 1, f"the call was answered exactly once, got {len(hit)}")
    check(hit[0]["blocked_by"] == "plan_mode", f"the UI can say 'blocked while planning', got {hit[0]['blocked_by']}")
    check(hit[0]["forced"] is True, "rule 3 both denies and forces, which is what plan_mode means")
    check("not available while planning" in (hit[0]["error"] or ""),
          f"the model is told to put it in a plan step, got {hit[0]['error']!r}")
    # tools.denied() completes "<name> is <reason>.", so PLAN_BLOCKED carries no subject of its
    # own, and this one refusal drops "Do not retry it": re-proposing the call IS the instruction.
    check((hit[0]["error"] or "").count("todo_add is") == 1,
          f"one subject, not two, got {hit[0]['error']!r}")
    check(".." not in (hit[0]["error"] or ""), f"and one full stop, got {hit[0]['error']!r}")
    check("Do not retry" not in (hit[0]["error"] or ""),
          f"no instruction that contradicts re-proposing, got {hit[0]['error']!r}")
    check("Do not retry" in (tools.denied("todo_add", "turned off for this chat")["error"]),
          "a refusal that is simply final still says so")
    check("sneaked past the schema filter" not in titles(), "and nothing was written")

    # Guard 3 alone: Toolbox.call never invokes gate(), so its clause is reached independently.
    direct = asyncio.run(toolbox.call("todo_add", {"title": "straight through the toolbox"},
                                      {"project_id": None, "conversation_id": cid, "plan_phase": True}))
    check("planning mode is on and no plan has been approved" in (direct.get("error") or ""),
          f"Toolbox.call refuses before spec.fn is awaited, got {direct}")
    check("straight through the toolbox" not in titles(), "the second gate wrote nothing either")
    check(toolbox.gate("todo_add", "on", {"plan_phase": True}) == "off",
          "gate's planning clause resolves to off, not to a card the user could wave through")
    check(toolbox.gate("web_search", "on", {"plan_phase": True}) == "on", "and leaves a network tool alone")


def test_auto_arms_planning_on_the_first_mutating_call() -> None:
    """§4.7's 'auto', which is the whole difference between it and 'always': the reply starts with
    the full tool set and the first mutating attempt is what flips planning on."""
    cid = plan_conv("auto")
    script({"calls": [call("todo_add", title="auto should stop this")]}, {"text": "Let me plan instead."})
    j("POST", f"/conversations/{cid}/chat", {"content": "add a todo"})
    drain(cid)

    check("todo_add" in offered(0), "auto does not filter the schema set up front")
    check(PLAN_TOOL in offered(0), "but propose_plan is there to reach for")
    hit = results(replay(cid), "todo_add")
    check(hit[0]["blocked_by"] == "plan_mode", f"the attempt arms planning instead of running, got {hit[0]}")
    check("auto should stop this" not in titles(), "and writes nothing")
    check("todo_add" not in offered(1), "the schema set is recomputed for the rest of the reply")


def test_propose_plan_always_asks() -> None:
    """Rule 1: a plan card can never resolve to `on` through any settings layer."""
    j("PUT", "/settings", {"tools": {PLAN_TOOL: "on"}})
    try:
        cid = plan_conv()
        args = {"title": "renew the passport"}
        script({"calls": [propose("Two errands", step("todo_add", args))]}, {"text": "Done."})
        j("POST", f"/conversations/{cid}/chat", {"content": "sort my errands"})
        row = card(cid, PLAN_TOOL)
        check(row["danger"] == "plan" and row["status"] == "pending", f"the plan is a pending card, got {row['status']}")
        check(row["plan_id"], "the approval row carries the plan it is deciding")
        pending = j("GET", f"/conversations/{cid}/plan")
        check(pending["status"] == "pending" and len(pending["steps"]) == 1,
              "a reloaded window can still find the card through the conversation")
        check(pending["steps"][0]["args_digest"] == args_digest(args),
              "the step binds sha256(canon(args)), not the prose")
        j("POST", f"/cowork/plans/{row['plan_id']}", {"decision": "approve"})
        drain(cid)
        evs = replay(cid)
        check([d for e, d in evs if e == "plan"], "the card streamed inline as a `plan` event")
        check([d for e, d in evs if e == "plan_decision" and d["decision"] == "approve"],
              "and the decision streamed back")
    finally:
        j("PUT", "/settings", {"tools": {}})


def test_approval_flips_the_schema_set_and_the_planned_call_runs() -> None:
    cid = plan_conv()
    args = {"title": "book the flights", "priority": 1}
    script({"calls": [propose("Trip", step("todo_add", args))]},
           {"calls": [call("todo_add", **args)]},
           {"text": "Booked."})
    j("POST", f"/conversations/{cid}/chat", {"content": "plan the trip"})
    row = card(cid, PLAN_TOOL)
    j("POST", f"/cowork/plans/{row['plan_id']}", {"decision": "approve"})
    drain(cid)

    check("todo_add" not in offered(0), "withheld while the plan was being drafted")
    check("todo_add" in offered(1), "and back in the schema set the round after approval (§4.2)")
    hit = results(replay(cid), "todo_add")
    check(len(hit) == 1 and hit[0]["error"] is None, f"the planned call ran, got {hit}")
    check(hit[0]["approval"] is None, "with no card: approving the plan approved these exact arguments")
    check(hit[0]["plan_step"], "and the call is recorded against the step it consumed")
    check(hit[0]["off_plan"] is False, "a claimed call is not off-plan")
    check("book the flights" in titles(), "the todo really exists")
    done = aplans.get(row["plan_id"])
    check(done["steps"][0]["status"] == "done", f"the step is spent, got {done['steps'][0]['status']}")


def test_the_same_call_twice_and_one_changed_character_both_ask() -> None:
    """Single-use and digest-exact, end to end: approving step 1 approves one call, once."""
    cid = plan_conv()
    args = {"title": "send the invoice"}
    again = {"title": "send the invoicE"}        # one changed character
    script({"calls": [propose("Billing", step("todo_add", args))]},
           {"calls": [call("todo_add", **args)]},
           {"calls": [call("todo_add", **args)]},
           {"calls": [call("todo_add", **again)]},
           {"text": "Stopping."})
    j("POST", f"/conversations/{cid}/chat", {"content": "bill them"})
    j("POST", f"/cowork/plans/{card(cid, PLAN_TOOL)['plan_id']}", {"decision": "approve"})
    second = card(cid, "todo_add")
    check(second["args"] == args, "the second identical call is the one asking")
    j("POST", f"/approvals/{second['call_id']}", {"decision": "deny"})
    third = card(cid, "todo_add")
    check(third["args"] == again, "and then the one-character variant asks on its own")
    j("POST", f"/approvals/{third['call_id']}", {"decision": "deny"})
    drain(cid)

    hit = results(replay(cid), "todo_add")
    check(len(hit) == 3, f"three attempts, three answers, got {len(hit)}")
    check(hit[0]["approval"] is None and hit[0]["plan_step"], "the first consumed the step with no card")
    check(hit[1]["off_plan"] is True and hit[1]["plan_step"] is None, "the second is off-plan and consumed nothing")
    check(hit[2]["off_plan"] is True and hit[2]["plan_step"] is None, "so is the changed one")
    check(titles() & {args["title"], again["title"]} == {args["title"]},
          f"exactly the approved call happened, got {sorted(titles() & {args['title'], again['title']})}")
    check(args_digest(args) != args_digest(again), "and the digests really do differ by that character")


def test_an_edited_step_binds_to_the_users_arguments() -> None:
    cid = plan_conv()
    mine = {"title": "mail the agent"}
    theirs = {"title": "mail the landlord"}      # what the user changed it to
    script({"calls": [propose("Housing", step("todo_add", mine))]},
           {"calls": [call("todo_add", **mine)]},
           {"calls": [call("todo_add", **theirs)]},
           {"text": "Done."})
    j("POST", f"/conversations/{cid}/chat", {"content": "sort the lease"})
    row = card(cid, PLAN_TOOL)
    out = j("POST", f"/cowork/plans/{row['plan_id']}",
            {"decision": "edit", "steps": [{"idx": 1, "arguments": theirs}], "note": "the landlord, not the agent"})
    check(out["ok"] is True, "the edit decided the plan")
    edited = out["plan"]["steps"][0]
    check(edited["edited"] is True and edited["arguments"] == theirs, "the step now carries the user's arguments")
    check(edited["args_digest"] == args_digest(theirs), "and its digest was recomputed from them")

    rejected = card(cid, "todo_add")
    check(rejected["args"] == mine, "the agent's own arguments no longer match the step")
    j("POST", f"/approvals/{rejected['call_id']}", {"decision": "deny"})
    drain(cid)
    hit = results(replay(cid), "todo_add")
    check(hit[0]["off_plan"] is True, "the agent's version is off-plan")
    check(hit[1]["plan_step"] and hit[1]["approval"] is None, "the user's version claims the step with no card")
    check(theirs["title"] in titles() and mine["title"] not in titles(),
          "the agent is bound to the user's arguments, not its own")


def test_a_rejected_plan_stops_the_reply_and_blocks_that_exact_step() -> None:
    cid = plan_conv()
    args = {"title": "delete the backups"}
    script({"calls": [propose("Cleanup", step("todo_add", args))]}, {"text": "Understood, I stopped."})
    j("POST", f"/conversations/{cid}/chat", {"content": "clean up"})
    row = card(cid, PLAN_TOOL)
    j("POST", f"/cowork/plans/{row['plan_id']}", {"decision": "reject", "note": "not the backups"})
    drain(cid)

    told = results(replay(cid), PLAN_TOOL)[0]["result_preview"]
    check("rejected" in told, f"the model is told the plan was rejected, got {told[:120]!r}")
    check("do not do the work anyway" in told, "with an explicit instruction to stop rather than improvise")
    check(aplans.rejected(cid, "todo_add", args) is True, "that exact step is blocked for this conversation")
    check(aplans.rejected(cid, "todo_add", {"title": "something else"}) is False,
          "a different call is not blocked: the block is digest-exact")
    check("todo_add" not in offered(1), "and planning stays on, so the next round still cannot write")
    check(aplans.active(cid) is None, "a rejected plan is not an approved one")


def test_expected_taint_survives_approval_and_unexpected_taint_does_not() -> None:
    """§4.6, the only place a pre-approval survives a taint — and only the taint the card named."""
    with as_danger("todo_add", "external"):
        reach = {"title": "email the summary"}
        unreachable = {"title": "email the board"}   # distinct, so the last check names one call only

        expected = plan_conv()
        script({"calls": [propose("Research then send",
                                  step("search_documents", {"query": "quarterly"}),
                                  step("todo_add", reach))]},
               {"calls": [call("search_documents", query="quarterly")]},
               {"calls": [call("todo_add", **reach)]},
               {"text": "Sent."})
        j("POST", f"/conversations/{expected}/chat", {"content": "research then send"})
        row = card(expected, PLAN_TOOL)
        plan = j("GET", f"/cowork/plans/{row['plan_id']}")
        check(plan["expected_taint"] == ["search_documents"],
              f"the card names the taint it will incur, got {plan['expected_taint']}")
        j("POST", f"/cowork/plans/{row['plan_id']}", {"decision": "approve"})
        drain(expected)
        hit = results(replay(expected), "todo_add")
        check(len(hit) == 1 and hit[0]["approval"] is None and hit[0]["plan_step"],
              f"predicted taint does not void the claim, got {hit}")

        surprise = plan_conv()
        script({"calls": [propose("Just send", step("todo_add", unreachable))]},
               {"calls": [call("search_documents", query="off plan")]},
               {"calls": [call("todo_add", **unreachable)]},
               {"text": "Blocked."})
        j("POST", f"/conversations/{surprise}/chat", {"content": "just send it"})
        pid = card(surprise, PLAN_TOOL)["plan_id"]
        check(aplans.get(pid)["expected_taint"] == [], "this plan predicted no taint at all")
        j("POST", f"/cowork/plans/{pid}", {"decision": "approve"})
        forced = card(surprise, "todo_add")
        j("POST", f"/approvals/{forced['call_id']}", {"decision": "deny"})
        drain(surprise)
        hit = results(replay(surprise), "todo_add")
        check(len(hit) == 1 and hit[0]["plan_step"] is None,
              "an off-plan fetch forces the card and consumes no step (§4.3 rule 5)")
        check(aplans.get(pid)["steps"][0]["status"] == "approved",
              "the step the user denied is still there to claim, not burnt")
        check(unreachable["title"] not in titles(), "and nothing was sent")


def test_always_chat_on_a_plan_card_buys_no_standing_grant() -> None:
    cid = plan_conv()
    script({"calls": [propose("Errand", step("todo_add", {"title": "collect the parcel"}))]}, {"text": "Done."})
    j("POST", f"/conversations/{cid}/chat", {"content": "run my errand"})
    row = card(cid, PLAN_TOOL)
    j("POST", f"/approvals/{row['call_id']}", {"decision": "always_chat"})
    drain(cid)

    check(aplans.get(row["plan_id"])["status"] == "approved", "the plan itself is approved")
    conv = j("GET", f"/conversations/{cid}")
    check((conv["settings"].get("tools") or {}).get(PLAN_TOOL) is None,
          f"no chat-level grant was written, got {conv['settings'].get('tools')}")
    check((j("GET", "/settings").get("tools") or {}).get(PLAN_TOOL) is None,
          "and no global one either: a plan card is never a permission card")


def test_a_rejected_replacement_is_not_answered_by_the_plan_it_replaced() -> None:
    """The user's "no" must not be granted through the earlier variant.

    Approve P1 [todo_add A]; the agent proposes P2 with the same call; the user rejects P2. P1 is
    superseded by then, but its step was left 'approved' — so the call claimed it and ran with no
    card. The second half is in-memory: _chat_stream read `plan` once and only reassigned it on an
    approval, so even with claim() fixed the rejected round still held a live-looking snapshot."""
    cid = plan_conv()
    args = {"title": "wipe the archive"}
    script({"calls": [propose("First go", step("todo_add", args))]},
           {"calls": [propose("Same thing again", step("todo_add", args))]},
           {"calls": [call("todo_add", **args)]},
           {"text": "Understood, I stopped."})
    j("POST", f"/conversations/{cid}/chat", {"content": "clear it out"})
    j("POST", f"/cowork/plans/{card(cid, PLAN_TOOL)['plan_id']}", {"decision": "approve"})
    second = card(cid, PLAN_TOOL)
    j("POST", f"/cowork/plans/{second['plan_id']}", {"decision": "reject", "note": "no, leave it"})
    drain(cid)

    hit = results(replay(cid), "todo_add")
    check(len(hit) == 1 and hit[0]["plan_step"] is None,
          f"no step was claimed: a superseded plan is not a standing grant, got {hit}")
    check(hit[0]["off_plan"] is False,
          "and no stale plan was left in memory either — with none live, nothing is 'off plan'")
    check("rejected this exact step" in (hit[0]["error"] or ""),
          f"so the call reaches rule 8 and the rejection answers it, got {hit[0]['error']!r}")
    check(args["title"] not in titles(), "nothing was written")
    check(aplans.active(cid) is None, "and the conversation has no live plan at all")


def test_no_system_message_splits_a_tool_calls_turn_from_its_replies() -> None:
    """A hint raised while answering a call is stashed and flushed after the loop. Appended where
    it is raised, it lands between the assistant tool_calls turn and the `tool` messages answering
    it — a sequence providers reject or mis-handle, and a scripted stub never notices."""
    cid = plan_conv()
    args = {"title": "pay the invoice"}
    script({"calls": [propose("Billing", step("todo_add", args))]},
           {"calls": [call("todo_add", **args)]},
           {"text": "Paid."})
    j("POST", f"/conversations/{cid}/chat", {"content": "pay it"})
    j("POST", f"/cowork/plans/{card(cid, PLAN_TOOL)['plan_id']}", {"decision": "approve"})
    drain(cid)
    bad = interleavings()
    check(not bad, f"approving a plan appends its hint after the tool reply, got {bad}")
    check(any(m["role"] == "system" for seq in SCRIPT["sent"] for m in seq[1:]),
          "the hint really was sent — the check above is not passing on an empty list")

    auto = plan_conv("auto")
    script({"calls": [call("todo_add", title="auto arms here")]}, {"text": "Let me plan."})
    j("POST", f"/conversations/{auto}/chat", {"content": "add a todo"})
    drain(auto)
    bad = interleavings()
    check(not bad, f"arming 'auto' mid-loop does the same, got {bad}")


def test_auto_runs_the_plan_it_made_you_write() -> None:
    """'auto' arms planning on the first mutating call — and then has to stop arming it. After
    approval `planning` is False while planMode is still 'auto', so re-arming denies the very call
    the user approved and nothing in 'auto' could ever execute."""
    cid = plan_conv("auto")
    args = {"title": "renew the insurance"}
    script({"calls": [call("todo_add", **args)]},
           {"calls": [propose("Insurance", step("todo_add", args))]},
           {"calls": [call("todo_add", **args)]},
           {"text": "Renewed."})
    j("POST", f"/conversations/{cid}/chat", {"content": "renew it"})
    j("POST", f"/cowork/plans/{card(cid, PLAN_TOOL)['plan_id']}", {"decision": "approve"})
    drain(cid)

    hit = results(replay(cid), "todo_add")
    check(len(hit) == 2, f"two attempts: the one that armed planning and the approved one, got {len(hit)}")
    check(hit[0]["blocked_by"] == "plan_mode", "the first armed planning instead of running")
    check(hit[1]["blocked_by"] is None and hit[1]["error"] is None,
          f"the approved call is not re-blocked by planning, got {hit[1]}")
    check(hit[1]["plan_step"] and hit[1]["approval"] is None, "it claimed its step, with no card")
    check(args["title"] in titles(), "and the work the user approved actually happened")


def test_the_canonical_string_is_the_one_planDigest_ts_must_produce() -> None:
    """The single most dangerous failure mode in the design: if these two drift by one byte,
    claim() silently never matches and every approved step re-prompts (§4.8)."""
    for args, want in CANON_FIXTURES:
        check(canon(args) == want, f"canon({args!r}) == {want!r}, got {canon(args)!r}")
    check(canon(None) == "{}", "missing arguments canonicalise to the empty object")
    check(args_digest({"a": 1, "b": 2}) == args_digest({"b": 2, "a": 1}), "key order cannot change the digest")
    check(len({args_digest(a) for a, _ in CANON_FIXTURES}) == len({w for _, w in CANON_FIXTURES}),
          "distinct canonical strings stay distinct once hashed")


def test_source_pins_the_single_schemas_closure_and_both_gate_clauses() -> None:
    """Three source-text guards, because each of these is a hole that reopens silently."""
    appsrc = (Path(__file__).resolve().parents[1] / "personal_os" / "app.py").read_text()
    toolsrc = (Path(__file__).resolve().parents[1] / "personal_os" / "tools.py").read_text()

    check(appsrc.count("def _schemas()") == 1, "one closure, not two")
    check(appsrc.count("toolbox.schemas(") == 1,
          "every tool_schemas assignment goes through _schemas(): a second raw call lets a granted "
          "write tool reappear mid-plan")
    check(appsrc.count("tool_schemas = _schemas()") >= 2,
          "and the grant path recomputes through the same closure")
    check('m[PLAN_TOOL] = "ask"' in appsrc, "propose_plan is forced into the planning schema set")

    clause = re.findall(r'ctx\.get\("plan_phase"\) and spec\.danger not in PLAN_SAFE_DANGER', toolsrc)
    check(len(clause) == 2, f"the planning clause is in BOTH gate() and call(), got {len(clause)}")
    check(toolsrc.count('ctx.get("proposal_only")') == 2, "so is the propose-only clause")
    check(toolsrc.index("if ctx.get(\"plan_phase\")") < toolsrc.index("out = await spec.fn("),
          "and call()'s clause sits above the await, so nothing runs first")
    check(plansmod.PLAN_SAFE_DANGER == ("safe", "network", "plan"),
          f"PLAN_SAFE_DANGER is what §4.2 says it is, got {plansmod.PLAN_SAFE_DANGER}")


TESTS = [test_planning_withholds_every_mutating_tool,
         test_a_mutating_call_while_planning_is_denied_by_both_gates,
         test_auto_arms_planning_on_the_first_mutating_call,
         test_propose_plan_always_asks,
         test_approval_flips_the_schema_set_and_the_planned_call_runs,
         test_the_same_call_twice_and_one_changed_character_both_ask,
         test_an_edited_step_binds_to_the_users_arguments,
         test_a_rejected_plan_stops_the_reply_and_blocks_that_exact_step,
         test_a_rejected_replacement_is_not_answered_by_the_plan_it_replaced,
         test_no_system_message_splits_a_tool_calls_turn_from_its_replies,
         test_auto_runs_the_plan_it_made_you_write,
         test_expected_taint_survives_approval_and_unexpected_taint_does_not,
         test_always_chat_on_a_plan_card_buys_no_standing_grant,
         test_the_canonical_string_is_the_one_planDigest_ts_must_produce,
         test_source_pins_the_single_schemas_closure_and_both_gate_clauses]


def _loose_ends() -> Iterator[str]:
    """A test that failed before it answered its card leaves a run waiting forever: parking and the
    chat timeout are both switched off here. Clear them so the client can close."""
    for row in client.get("/approvals").json():
        client.post(f"/approvals/{row['call_id']}", json={"decision": "deny"})
        yield f"denied a leftover {row['tool']} card"
    for run in client.get("/runs").json():
        client.post(f"/conversations/{run['conversation_id']}/stop")
        yield f"stopped a leftover run on {run['conversation_id']}"


if __name__ == "__main__":
    failures = 0
    # One portal, so every request shares the event loop the run tasks live on. Parking and the chat
    # approval timeout are off: every card in this file is decided by the test that opened it, and a
    # timer firing first would make that a race.
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "planMode": "off",
                                      "parkAfterSeconds": 0, "approvalWaitSeconds": 0})
        for t in TESTS:
            try:
                t()
                print(f"ok   {t.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {t.__name__}: {e}")
            for note in _loose_ends():
                print(f"     cleanup: {note}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
