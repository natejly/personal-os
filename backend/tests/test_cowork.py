"""Desks end to end, through the real app: parallelism, the park, the chain, review and promotion.

A desk is one conversation plus one workspace plus one approved plan, so most of what is asserted
here is that nothing new was invented to make that work: three desks are three ordinary runs, a
park is a row that outlives its run, a chained turn is a fresh bounded run announced before the old
one closes, and a promotion is only a promotion once the promoted copy has been read back.

`llm.stream_chat` is replaced by a scripted generator whose signature tracks the real one, the way
test_runs.py does it. One scripted entry is one round. The closing tool-free round (`tool_choice`
"none") deliberately does NOT consume the script, so a budget stop cannot silently eat the next
turn's first round.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_cowork.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="coworktest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

# Every test here drives background run tasks through the app. They outlive the request that started
# them, so the whole module needs ONE portal: pytest's per-request TestClient opens and closes one
# each time and cancels every run in flight. `python backend/tests/test_cowork.py` is the harness.
if "pytest" in sys.modules:  # pragma: no cover - collection guard, not behaviour
    import pytest

    pytest.skip("needs one portal for the whole module; run it directly", allow_module_level=True)

from personal_os import llm  # noqa: E402
from personal_os.app import (AUTH_TOKEN, _desk_tasks, _missed_wake, _should_chain, app,  # noqa: E402
                             bus, db, plans, desks, docs, run_store, toolbox, workspace)
from personal_os.app import events as topic  # noqa: E402
from personal_os.plans import PLAN_SAFE_DANGER  # noqa: E402
from personal_os.cowork import LIVE, NEEDS_YOU  # noqa: E402
from personal_os.plans import PLAN_TOOL  # noqa: E402
from personal_os.runs import Run  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0

REPORT = "# The report\n\nOne finding, written down.\n"

SCRIPT: dict[str, Any] = {"turns": [], "default": {"text": "All done."}, "delay": 0.0, "tools": [], "systems": [],
                          "messages": []}


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                           cancel: asyncio.Event | None = None) -> Any:
    SCRIPT["tools"].append([t["function"]["name"] for t in (tools or [])])
    SCRIPT["messages"].append([dict(m) for m in messages])
    SCRIPT["systems"].append("\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system"))
    if tool_choice == "none":
        # The closing round of a budget, park or breaker stop. It never calls a tool and never
        # consumes a scripted turn: the next turn's first round is still waiting for its entry.
        yield {"type": "delta", "text": "Wrapping up."}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}
        return
    turn = dict(SCRIPT["turns"].pop(0)) if SCRIPT["turns"] else dict(SCRIPT["default"])
    calls = list(turn.get("calls") or [])
    for word in (turn.get("text") or ("" if calls else "All done.")).split(" "):
        if SCRIPT["delay"]:
            await asyncio.sleep(SCRIPT["delay"])
        if word:
            yield {"type": "delta", "text": word + " "}
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


def script(*turns: dict[str, Any], delay: float = 0.0) -> None:
    SCRIPT["turns"] = [dict(t) for t in turns]
    SCRIPT["delay"] = delay
    SCRIPT["tools"] = []
    SCRIPT["systems"] = []
    SCRIPT["messages"] = []


def settings_patch(**patch: Any) -> None:
    j("PUT", "/settings", patch)


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {label}")


def runs_of(desk_id: str, status: str = "") -> list[dict[str, Any]]:
    return j("GET", f"/runs?desk_id={desk_id}" + (f"&status={status}" if status else ""))


def quiet(desk_id: str) -> None:
    wait_until(lambda: not runs_of(desk_id), f"every run of desk {desk_id} to end")


def desk(desk_id: str) -> dict[str, Any]:
    return j("GET", f"/cowork/desks/{desk_id}")


def card(desk_id: str, tool: str | None = None) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def ready() -> bool:
        for row in j("GET", f"/approvals?desk_id={desk_id}"):
            if tool is None or row["tool"] == tool:
                found.update(row)
                return True
        return False

    wait_until(ready, f"an approval card for {tool or 'any tool'} on desk {desk_id}")
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


def tape(conv_id: str, run_id: str) -> list[tuple[str, Any]]:
    """One finished run's events, replayed from the tape rather than the live bus."""
    return events(client.get(f"/conversations/{conv_id}/stream?since=0&run_id={run_id}").text)


def propose(title: str, *steps: dict[str, Any]) -> dict[str, Any]:
    return {"name": PLAN_TOOL, "arguments": {"title": title, "intent": title,
                                             "steps": [{"why": "the brief asks for it", **s} for s in steps]}}


def step(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"title": tool, "tool": tool, "arguments": arguments}


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def make_desk(brief: str, start: bool = True, **body: Any) -> dict[str, Any]:
    return j("POST", "/cowork/desks", {"brief": brief, "start": start, **body})


WRITE = call("desk_write_file", path="outputs/report.md", content=REPORT)
DELIVER = call("desk_deliver", path="outputs/report.md", title="The report", summary="One finding.")


def delivering_desk(brief: str) -> dict[str, Any]:
    """A desk that plans, writes one output, delivers it and finishes — the whole happy path."""
    script({"calls": [propose("Write it up", step("desk_write_file", WRITE["arguments"]),
                              step("desk_deliver", DELIVER["arguments"]))]},
           {"calls": [WRITE]},
           {"calls": [DELIVER]},
           {"calls": [call("desk_done", summary="Written and delivered.")]},
           {"text": "Finished."})
    made = make_desk(brief)
    did = made["desk"]["id"]
    j("POST", f"/approvals/{card(did, PLAN_TOOL)['call_id']}", {"decision": "allow"})
    quiet(did)
    return desk(did)


def test_a_desk_is_a_conversation_the_chat_list_hides() -> None:
    made = make_desk("Compare the two vendors", start=False)
    did, cid = made["desk"]["id"], made["conversation_id"]
    conv = j("GET", f"/conversations/{cid}")
    check(conv["settings"]["deskId"] == did, "the conversation carries the desk it belongs to")
    check(conv["settings"]["planMode"] == "always", "and a desk always plans its first run (§4.1)")
    check(made["desk"]["status"] == "draft" and "run_id" not in made, "start=false creates nothing to watch")
    check(made["desk"]["workspace"] == f"cowork/{did}", f"the row stores a relative root, got {made['desk']['workspace']}")

    listed = {c["id"] for c in j("GET", "/conversations")}
    check(cid not in listed, "a desk's conversation is not in Recent chats")
    check(cid in {c["id"] for c in j("GET", "/conversations?include_desks=true")},
          "?include_desks=true is how it is still reachable (§3.9)")
    check(j("GET", "/cowork/desks/nope", expect=404)["detail"] == "No such desk", "an unknown desk 404s")


def test_ask_as_it_goes_cards_each_change_instead_of_planning_first() -> None:
    """'Ask as it goes' is a mode the UI offers, so it has to be a different thing from 'Plan
    first': nothing withheld up front, no plan card, and one card per change on its way out."""
    script({"calls": [WRITE]},
           {"calls": [call("desk_done", summary="Written.")]},
           {"text": "Done."})
    made = make_desk("Write the note", autonomy="ask")
    did, cid = made["desk"]["id"], made["conversation_id"]
    check(j("GET", f"/conversations/{cid}")["settings"]["planMode"] == "off",
          "an 'ask' desk does not start its reply in planning")
    row = card(did, "desk_write_file")
    check(row["tool"] == "desk_write_file", f"the change itself is the card, got {row['tool']}")
    check(bool(row["forced"]),
          "forced, so 'Always allow' cannot quietly turn the mode the user chose back off")
    j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
    quiet(did)

    tapes = tape(cid, made["run_id"])
    check(not [d for e, d in tapes if e == "plan"], "no plan was ever proposed")
    hit = [d for e, d in tapes if e == "tool_result" and d["name"] == "desk_write_file"]
    check(len(hit) == 1 and hit[0]["error"] is None, f"the approved write ran, got {hit}")
    check(hit[0]["blocked_by"] is None, "it was never blocked by planning, the way 'plan' autonomy would")
    check(hit[0]["approval"] == "allow", "and it ran because the user said so, call by call")
    check(j("GET", f"/cowork/desks/{did}/files")["files"], "the file really exists")


def test_a_desk_is_told_it_is_a_desk_and_why_a_plan_comes_first() -> None:
    """The desk's own instructions (outputs/ + desk_deliver, desk_ask, desk_done) reached no model at
    all, so a planning desk saw no write or deliver tool and no reason for their absence, and
    answered in chat with a report it had not saved."""
    script({"calls": [propose("Write it up", step("desk_write_file", WRITE["arguments"]))]}, {"text": "Waiting."})
    planning = make_desk("Write the note")["desk"]["id"]
    j("POST", f"/approvals/{card(planning, PLAN_TOOL)['call_id']}", {"decision": "deny"})
    quiet(planning)
    first = SCRIPT["systems"][0]
    check("## This is a cowork desk" in first and "desk_deliver" in first, "a desk's first round carries the desk hint")
    check("## Planning first" in first and "propose_plan" in first, "and, while planning, why writes are not offered yet")

    script({"calls": [call("desk_done", summary="Nothing to do.")]}, {"text": "Done."})
    asking = make_desk("Write the note", autonomy="ask")["desk"]["id"]
    quiet(asking)
    check("## This is a cowork desk" in SCRIPT["systems"][0], "an 'ask' desk carries the desk hint too")
    check("## Planning first" not in SCRIPT["systems"][0], "but is not told to plan")
    script({"text": "Hi."})
    cid = j("POST", "/conversations", {"title": "plain chat"})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": "hello"})
    wait_until(lambda: bool(SCRIPT["systems"]), "the plain chat's first round")
    check("## This is a cowork desk" not in SCRIPT["systems"][0], "an ordinary chat is never told it is a desk")


def test_seen_clears_the_desks_needs_you_badge() -> None:
    script({"calls": [call("desk_ask", question="Which vendor did you mean?")]}, {"text": "Waiting."})
    did = make_desk("Compare the vendors", autonomy="ask")["desk"]["id"]
    q = card(did, "desk_ask")
    j("POST", f"/approvals/{q['call_id']}", {"decision": "allow", "note": "the second one"})
    quiet(did)

    before = desk(did)
    check(before["unseen"] > 0, f"the desk is in Needs you, got unseen={before['unseen']}")
    after = j("POST", f"/cowork/desks/{did}/seen")
    check(after["unseen"] == 0, f"POST /seen clears the badge, got unseen={after['unseen']}")
    check(after["id"] == did and "events" in after and "outputs" in after,
          "and answers with the same shape as GET /cowork/desks/{id}")
    check(not [e for e in after["events"] if e["needs_you"] and not e["seen"]],
          "every unseen needs_you event of this desk is marked, not just the newest")
    check(not [e for e in j("GET", "/cowork/inbox") if e["desk_id"] == did],
          "so it leaves the cross-desk inbox too")


def test_three_desks_run_at_once() -> None:
    script(delay=0.05)
    made = [make_desk(f"Desk {i}") for i in range(3)]
    ids = [m["desk"]["id"] for m in made]
    check(len({m["run_id"] for m in made}) == 3, "three desks, three distinct run ids")
    live = {r["run_id"]: r for r in j("GET", "/runs") if r["desk_id"] in ids}
    check(len(live) == 3, f"all three are listed as running at once, got {len(live)}")
    check(all(r["kind"] == "desk" and r["live"] is True for r in live.values()), "each is a live desk run")
    check(len({r["conversation_id"] for r in live.values()}) == 3,
          "one conversation each, which is why no bus surgery was needed")
    for did in ids:
        j("POST", f"/cowork/desks/{did}/stop")
    for did in ids:
        quiet(did)
    landed = {d: desk(d)["status"] for d in ids}
    check(set(landed.values()) == {"stopped"}, f"and all three stop independently, got {landed}")


def test_the_live_desk_cap_409s() -> None:
    script(delay=0.05)
    busy = make_desk("The only desk allowed")
    settings_patch(deskMaxLive=1)
    try:
        wait_until(lambda: desk(busy["desk"]["id"])["status"] in LIVE, "the first desk to be live")
        over = client.post("/cowork/desks", json={"brief": "One too many", "start": True})
        check(over.status_code == 409, f"creating a fifth live desk 409s, got {over.status_code}")
        check(over.json()["detail"]["max"] == 1, "the 409 says what the cap was")
        queued = make_desk("Queued for later", start=False)
        check(queued["desk"]["status"] == "draft", "but a draft can still be queued: a draft is not live")
        started = client.post(f"/cowork/desks/{queued['desk']['id']}/start")
        check(started.status_code == 409, f"starting it is what 409s, got {started.status_code}")
    finally:
        settings_patch(deskMaxLive=4)
        j("POST", f"/cowork/desks/{busy['desk']['id']}/stop")
        quiet(busy["desk"]["id"])


def test_a_double_start_makes_one_run() -> None:
    script(delay=0.05)
    did = make_desk("Started twice", start=False)["desk"]["id"]
    codes: list[int] = []

    def go() -> None:
        codes.append(client.post(f"/cowork/desks/{did}/start").status_code)

    threads = [threading.Thread(target=go, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    check(sorted(codes) == [200, 409], f"exactly one Start wins the claim, got {codes}")
    check(len(runs_of(did)) == 1, "and exactly one run exists while it is live")
    j("POST", f"/cowork/desks/{did}/stop")
    quiet(did)
    check(len(run_store.list(desk_id=did, statuses=None)) == 1, "no orphaned run was left on the tape")


def _parked_desk() -> tuple[str, dict[str, Any]]:
    """A desk whose plan card nobody watched, left parked. Returns (desk_id, the approval row)."""
    script({"calls": [propose("Needs a decision", step("desk_write_file", WRITE["arguments"]))]},
           {"calls": [WRITE]},
           {"text": "Done."})
    did = make_desk("Park me")["desk"]["id"]
    row = card(did, PLAN_TOOL)
    wait_until(lambda: desk(did)["status"] == "blocked", "the unwatched desk to park")
    quiet(did)
    return did, row


def test_an_unanswered_card_parks_rather_than_auto_denying() -> None:
    """§4.5: the run lets go, the row does not. The card is still decidable tomorrow."""
    settings_patch(parkAfterSeconds=1)
    try:
        did, row = _parked_desk()
        parked = desk(did)
        check(parked["status"] == "blocked" and parked["status_reason"] == "plan",
              f"the desk is blocked on its plan card, got {parked['status']}/{parked['status_reason']}")
        check(parked["status"] in NEEDS_YOU, "which is a Needs you state")
        check(not runs_of(did), "the run ended rather than holding a task open all night")
        check(_desk_tasks.get(did) is None, "and no supervisor task is left alive behind it")
        still = run_store.approval(row["call_id"])
        check(still["status"] == "pending", f"the approval row stays pending, got {still['status']}")
        check(still["decided_by"] == "park", "recording that a timer, not a user, let go of it")
        check(plans.get(row["plan_id"])["status"] == "pending", "and the plan is still undecided")
    finally:
        settings_patch(parkAfterSeconds=0)


def test_deciding_a_parked_card_resumes_the_desk() -> None:
    settings_patch(parkAfterSeconds=1)
    try:
        did, row = _parked_desk()
        settings_patch(parkAfterSeconds=0)
        out = j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
        check(out["ok"] is True and out["resumed"] is True,
              "deciding it resumes the desk through the same resume() the recovery path uses")
        wait_until(lambda: len(run_store.list(desk_id=did, statuses=None)) == 2, "a second run to carry it on")
        quiet(did)
        check(workspace.resolve_in(did, "outputs/report.md").is_file(), "the resumed turn ran the approved step")
        check(plans.get(row["plan_id"])["steps"][0]["status"] == "done", "consuming it exactly once")
        check(desk(did)["plan_id"] == row["plan_id"],
              f"and the desk carries the plan it is executing, got {desk(did)['plan_id']!r} — "
              "claim_run and _should_chain both read desks.plan_id")
    finally:
        settings_patch(parkAfterSeconds=0)


def test_a_wake_that_lost_the_race_with_its_own_run_is_retried() -> None:
    """_launch_desk refuses while a run is live on the conversation, and a run that parked a card
    stays live for _final_round and the auto-learn tail — so an approval decided inside that
    window answered `resumed: False` and the desk sat blocked with nobody coming back for it. The
    supervisor retries it once the run has really ended; this pins the predicate it is gated on.
    (The window itself is sub-millisecond against a scripted stub, so it is not raced here.)"""
    settings_patch(parkAfterSeconds=1)
    try:
        did, row = _parked_desk()
    finally:
        settings_patch(parkAfterSeconds=0)
    check(_missed_wake(did) is None,
          "a card the user has not answered is not a missed wake: park() leaves the row pending")
    run_store.decide(row["call_id"], "allow", by="user")
    plans.decide(row["plan_id"], "approve")
    claimed = _missed_wake(did)
    check(claimed is not None, "once it is decided and nothing is waiting on it, the wake is retried")
    check(claimed["status"] in ("planning", "working"),
          f"and the desk is claimed for another turn, got {claimed['status']}")
    check(_missed_wake(did) is None, "only once: claim_run's rowcount is still the lock")
    desks.set_status(did, "stopped", reason="test")   # claimed without a run; do not leave it LIVE


def test_a_watched_card_never_parks() -> None:
    """Parking in front of somebody reading a twelve-step plan would be a bug, so run.watchers gates it."""
    settings_patch(parkAfterSeconds=1)
    try:
        script({"calls": [propose("Watched", step("desk_write_file", WRITE["arguments"]))]},
               {"calls": [WRITE]},
               {"text": "Done."})
        made = make_desk("Watch me")
        did, cid = made["desk"]["id"], made["conversation_id"]
        row = card(did, PLAN_TOOL)
        body: list[str] = []
        viewer = threading.Thread(target=lambda: body.append(client.get(f"/conversations/{cid}/stream?since=0").text),
                                  daemon=True)
        viewer.start()
        wait_until(lambda: desk(did)["status"] == "awaiting_plan", "the watched desk to be awaiting its plan")
        time.sleep(6)       # six times the park timer, with a subscriber attached the whole way
        check(desk(did)["status"] == "awaiting_plan", f"it did not park, got {desk(did)['status']}")
        check(run_store.approval(row["call_id"])["status"] == "pending", "and the card is untouched")
        check(bool(runs_of(did)), "the run is still there, waiting indefinitely")
        j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
        quiet(did)
        viewer.join(20)
        check(body and "plan_decision" in body[0], "the watcher saw the decision arrive on its own stream")
    finally:
        settings_patch(parkAfterSeconds=0)


def test_a_chained_turn_hands_off_before_it_ends() -> None:
    """Long autonomy is bought by chaining bounded replies, never by raising maxToolRounds."""
    # The completion gate is off here: this script finishes without delivering its files, which the gate would
    # (rightly) refuse, and either the refusal and its nudge or the reviewer it would start adds a third run to a test about two.
    settings_patch(maxToolRounds=2, deskDoneGate=False, deskSelfReview=False)
    try:
        second = call("desk_write_file", path="outputs/second.md", content="# Second\n")
        script({"calls": [propose("Two files", step("desk_write_file", WRITE["arguments"]),
                                  step("desk_write_file", second["arguments"]))]},
               {"calls": [WRITE]},
               {"calls": [second]},                       # round 3: out of rounds, never executed
               {"calls": [second]},                       # turn 2, round 1: the step picked up again
               {"calls": [call("desk_done", summary="Both files written.")]},
               {"text": "Finished."})
        made = make_desk("Write two files")
        did, cid = made["desk"]["id"], made["conversation_id"]
        first_run = made["run_id"]
        j("POST", f"/approvals/{card(did, PLAN_TOOL)['call_id']}", {"decision": "allow"})
        wait_until(lambda: len(run_store.list(desk_id=did, statuses=None)) == 2, "the chained turn to start")
        quiet(did)

        names = [e for e, _ in tape(cid, first_run)]
        check("desk_handoff" in names, f"the first turn announced its successor, got {names[-6:]}")
        check(names.index("desk_handoff") < names.index("done"),
              "and announced it BEFORE done, so the renderer can re-attach without a gap")
        check({"plan_card", "plan_decision", "desk_status"} <= set(names),
              f"the four new ChatEvent names all really leave the bus, got {sorted(set(names))}")
        rows = run_store.list(desk_id=did, statuses=None)
        check(len(rows) == 2, f"two bounded runs, not one long one, got {len(rows)}")
        check({r["turn"] for r in rows} == {0, 1}, f"numbered as consecutive turns, got {[r['turn'] for r in rows]}")
        check(desk(did)["turn"] == 2, f"the desk counted both turns, got {desk(did)['turn']}")
        check(workspace.resolve_in(did, "outputs/second.md").is_file(), "the second step really ran on the next turn")
        spent = plans.get(desk(did)["plan_id"])
        check([s["status"] for s in spent["steps"]] == ["done", "done"], "both approved steps are spent exactly once")
    finally:
        settings_patch(maxToolRounds=25, deskDoneGate=True, deskSelfReview=True)


def test_a_turn_that_consumed_no_step_does_not_chain() -> None:
    settings_patch(maxToolRounds=2)
    try:
        script({"calls": [propose("One file", step("desk_write_file", WRITE["arguments"]))]},
               {"calls": [call("desk_read_file", path="work/missing.txt")]},         # a round spent on nothing the plan asked for, and it errors: no progress
               {"calls": [WRITE]},                        # round 3: out of rounds, never executed
               {"text": "Finished."})
        did = make_desk("Spin the wheels")["desk"]["id"]
        j("POST", f"/approvals/{card(did, PLAN_TOOL)['call_id']}", {"decision": "allow"})
        quiet(did)
        time.sleep(0.5)      # long enough for a chained turn to have appeared, if one were coming

        check(len(run_store.list(desk_id=did, statuses=None)) == 1, "no second turn was started")
        state = desk(did)
        # §9 calls this landing in `failed`; Desks.settle maps a budget-window stop to review/budget
        # whatever the reason, and review is in NEEDS_YOU, so the user sees it either way.
        check(state["status"] == "review" and state["status_reason"] == "budget",
              f"the desk stops and asks the user instead, got {state['status']}/{state['status_reason']}")
        check(_desk_tasks.get(did) is None, "and the supervisor let go")
    finally:
        settings_patch(maxToolRounds=25)


def test_the_desk_budget_caps_the_chain_even_with_turns_left() -> None:
    """_should_chain's five guards, asserted directly: a scripted run never costs real money, so
    the cost axis cannot be reached through the app."""
    did = make_desk("Budgeted", start=False)["desk"]["id"]
    plan = plans.open(f"budget:{did}",
                      {"title": "t", "steps": [{"tool": "desk_write_file", "arguments": WRITE["arguments"], "why": ""}]},
                      run_id=None, conversation_id=j("GET", f"/cowork/desks/{did}")["conversation_id"],
                      message_id="", tainted=False, desk_id=did)
    plans.decide(f"budget:{did}", "allow")
    base = {"status": "working", "turn": 0, "cost": 0.0, "plan_id": plan["plan_id"], "budget": None}
    run = Run("unused")
    run.partial, run.steps_consumed = "rounds", 1
    try:
        check(_should_chain(base, run) is True, "the baseline turn would chain")
        check(_should_chain({**base, "cost": 99.0}, run) is False, "the cost cap stops it with turns left over")
        check(_should_chain({**base, "turn": 99}, run) is False, "so does the turn cap")
        check(_should_chain({**base, "cost": 0.001, "budget": {"maxCost": 0.0001}}, run) is False,
              "a desk's own budget may make the user's settings stricter")
        check(_should_chain({**base, "cost": 0.001}, run) is True,
              "and the same spend is nothing against the default cap")
        run.steps_consumed = 0
        check(_should_chain(base, run) is False, "a turn that consumed no step is not progress")
        run.steps_consumed = 1
        check(_should_chain({**base, "status": "review"}, run) is False, "and a desk that is not working never chains")
    finally:
        run.end()


def test_desk_ask_moves_the_desk_to_needs_you() -> None:
    script({"calls": [call("desk_ask", question="Which vendor should I price against?")]},
           {"text": "Waiting."})
    did = make_desk("Ask me something")["desk"]["id"]
    row = card(did, "desk_ask")
    check(row["danger"] == "plan", "a question is a card the user answers, not a permission card")
    j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
    quiet(did)

    # Approving the card without typing an answer is "seen, no answer": the tool tells the model to go on
    # with its best judgement instead of parking the desk on a question that was just looked at.
    state = desk(did)
    check(state["status_reason"] != "question" and not state["question"],
          f"an approval with no answer does not park the desk on the question, got {state['status']}/{state['status_reason']}")


def test_a_steer_does_not_double_charge_the_turn() -> None:
    """A steered reply closes its current segment with its own `done` and carries on in a fresh
    assistant message. _run_desk read every `done` as end-of-turn, so one turn was charged twice
    and the desk hit its turn budget half a turn early."""
    script({"text": " ".join(["Thinking."] * 40)}, {"text": "Folded in the steer."}, delay=0.02)
    made = make_desk("Steer me")
    did, cid = made["desk"]["id"], made["conversation_id"]
    out = j("POST", f"/cowork/desks/{did}/message", {"content": "also check work/ while you are there"})
    check(out["steered"] is True, "the message steered the live run rather than starting a turn")
    quiet(did)

    dones = [d for e, d in tape(cid, made["run_id"]) if e == "done"]
    check(len(dones) == 2, f"the run emitted a segment `done` and then its real one, got {len(dones)}")
    check([bool(d.get("segment")) for d in dones] == [True, False],
          f"and only the last one claims to end the run, got {[d.get('segment') for d in dones]}")
    check(desk(did)["turn"] == 1, f"so the desk is charged for one turn, got {desk(did)['turn']}")


def test_desk_done_with_an_output_lands_in_review() -> None:
    state = delivering_desk("Write the report")
    planning_round, after_approval = set(SCRIPT["tools"][0]), set(SCRIPT["tools"][1])
    check({"desk_list_files", "desk_read_file", "desk_ask"} <= planning_round,
          "a desk is offered its own workspace tools, which a chat is not (§4.2)")
    check("desk_write_file" not in planning_round, "but writing into it waits for the plan, like any other write")
    check("desk_write_file" in after_approval, "and arrives with the approval")
    check(state["status"] == "review" and state["status_reason"] == "done",
          f"desk_done with something to review lands in review, got {state['status']}/{state['status_reason']}")
    check(len(state["outputs"]) == 1, "one nominated output")
    out = state["outputs"][0]
    check(out["status"] == "proposed" and out["path"] == "outputs/report.md", f"awaiting a decision, got {out}")
    check(out["sha256"] == workspace.sha(state["id"], "outputs/report.md"), "recorded against the bytes on disk")
    files = j("GET", f"/cowork/desks/{state['id']}/files")["files"]
    check(any(f["path"] == "outputs/report.md" and f["state"] == "new" for f in files),
          f"the file the desk wrote shows as new in the review tree, got {files}")
    check(j("GET", f"/cowork/desks/{state['id']}/file?path=outputs/report.md")["text"] == REPORT,
          "and previews as what was written")


def test_accept_is_exactly_once_and_reads_the_promotion_back() -> None:
    state = delivering_desk("Promote the report")
    did, oid = state["id"], state["outputs"][0]["id"]
    body = {"outputs": [{"output_id": oid, "destination": "doc", "title": "The report"}]}
    first = j("POST", f"/cowork/desks/{did}/accept", body)["results"][0]
    check(first["ok"] is True and first["verified"] is True, f"the promotion was read back and matched, got {first}")
    check(docs.get(first["ref"])["content"] == REPORT, "and the doc really holds the file's bytes")

    again = j("POST", f"/cowork/desks/{did}/accept", body)["results"][0]
    check(again["ok"] is False and again["error"] == "already decided",
          f"a second Accept promotes nothing: the claim is the lock, got {again}")
    check(len([d for d in j("GET", "/docs") if d["title"] == "The report"]) == 1, "exactly one doc exists")
    check(desk(did)["status"] == "done", "with nothing left to decide the desk is finished")


def test_a_promotion_that_does_not_read_back_is_promote_failed() -> None:
    state = delivering_desk("Truncate me")
    did, oid = state["id"], state["outputs"][0]["id"]
    real = docs.create

    def truncating(title: str, content: str, project_id: str | None = None) -> Any:
        return real(title, content[:8], project_id)      # a half-written file, the way a real one fails

    docs.create = truncating  # type: ignore[method-assign]
    try:
        out = j("POST", f"/cowork/desks/{did}/accept",
                {"outputs": [{"output_id": oid, "destination": "doc"}]})["results"][0]
    finally:
        docs.create = real  # type: ignore[method-assign]
    check(out["ok"] is False and out["verified"] is False, f"a mismatch is never reported as success, got {out}")
    check("does not match" in (out.get("error") or ""), f"and says what went wrong, got {out.get('error')!r}")
    row = next(o for o in j("GET", f"/cowork/desks/{did}/outputs") if o["id"] == oid)
    check(row["status"] == "promote_failed", f"the row records the failure, got {row['status']}")
    check(desk(did)["status"] == "review", "and the desk stays in review, because nothing was decided")


def test_a_failed_promotion_can_be_retried() -> None:
    """§6.4 says a failed read-back stays retryable, and claim_output clears the claim so Accept
    can take it again — but the work is booked through call_once, whose key is run + step + args.
    With a constant step and constant args every retry replays the cached failure for ever."""
    state = delivering_desk("Retry me")
    did, oid = state["id"], state["outputs"][0]["id"]
    body = {"outputs": [{"output_id": oid, "destination": "doc"}]}
    real = docs.create

    def truncating(title: str, content: str, project_id: str | None = None) -> Any:
        return real(title, content[:8], project_id)

    docs.create = truncating  # type: ignore[method-assign]
    try:
        first = j("POST", f"/cowork/desks/{did}/accept", body)["results"][0]
    finally:
        docs.create = real  # type: ignore[method-assign]
    check(first["ok"] is False, f"the first attempt fails its read-back, got {first}")

    second = j("POST", f"/cowork/desks/{did}/accept", body)["results"][0]
    check(second["ok"] is True and second["verified"] is True,
          f"the retry really runs rather than replaying the taped failure, got {second}")
    check(docs.get(second["ref"])["content"] == REPORT, "and this time the doc holds the whole file")
    row = next(o for o in j("GET", f"/cowork/desks/{did}/outputs") if o["id"] == oid)
    check(row["status"] == "promoted", f"the row ends promoted, got {row['status']}")


def test_download_hands_over_the_file_it_marks_promoted() -> None:
    """`download` is the one destination where nothing enters the app, so the read-back is the
    file itself and the hand-off is a route that serves it. Without one the row was marked
    promoted and verified having given the user nothing at all."""
    state = delivering_desk("Download me")
    did, oid = state["id"], state["outputs"][0]["id"]
    out = j("POST", f"/cowork/desks/{did}/accept",
            {"outputs": [{"output_id": oid, "destination": "download"}]})["results"][0]
    check(out["ok"] is True and out["ref"] == "outputs/report.md",
          f"verified against the bytes that were delivered, got {out}")

    got = client.get(f"/cowork/desks/{did}/download?path={out['ref']}")
    check(got.status_code == 200 and got.text == REPORT, f"and the route serves them, got {got.status_code}")
    check("attachment" in got.headers.get("content-disposition", ""), "as a download, not a page")
    check(got.headers["content-type"].startswith("application/octet-stream"),
          "never rendered: a desk workspace is agent-written")
    check(client.get(f"/cowork/desks/{did}/download?path=outputs/nope.md").status_code == 404,
          "a path that is not there 404s")
    check(client.get(f"/cowork/desks/{did}/download?path=../../etc/passwd").status_code == 400,
          "and it is the same containment chokepoint as every other workspace read")


def test_doc_append_proposes_a_revision_and_leaves_the_doc_alone() -> None:
    state = delivering_desk("Append the report")
    did, oid = state["id"], state["outputs"][0]["id"]
    target = j("POST", "/docs", {"title": "Running notes", "content": "# Running notes\n\nAlready here.\n"})
    before = j("GET", f"/docs/{target['id']}")["content"]

    out = j("POST", f"/cowork/desks/{did}/accept",
            {"outputs": [{"output_id": oid, "destination": "doc_append", "doc_id": target["id"]}]})["results"][0]
    check(out["ok"] is True and out["kind"] == "doc_append", f"the append was accepted, got {out}")
    check(j("GET", f"/docs/{target['id']}")["content"] == before,
          "the document the user wrote is untouched until they accept the revision")
    waiting = [r for r in j("GET", f"/docs/{target['id']}/revisions") if r["status"] == "pending"]
    check(len(waiting) == 1 and waiting[0]["id"] == out["ref"],
          f"exactly one revision is waiting in the existing Docs review UI, got {waiting}")
    check(REPORT.strip() in j("GET", f"/docs/revisions/{out['ref']}")["after"], "and it carries the file's text")


def test_delete_keeps_the_workspace_unless_purge() -> None:
    state = delivering_desk("Delete me")
    did, cid = state["id"], state["conversation_id"]
    root = workspace.desk_root(did)
    check((root / "outputs" / "report.md").is_file(), "the desk wrote a real file on disk")

    j("DELETE", f"/cowork/desks/{did}")
    j("GET", f"/cowork/desks/{did}", expect=404)
    j("GET", f"/conversations/{cid}", expect=404)
    check(cid not in {c["id"] for c in j("GET", "/conversations?include_desks=true")},
          "the transcript goes with the desk: the route deletes it, because the cascade runs the other way")
    check((root / "outputs" / "report.md").is_file(),
          "but the files stay: a deleted desk's work is the one thing the user cannot regenerate (§6.5)")

    purged = delivering_desk("Purge me")
    proot = workspace.desk_root(purged["id"])
    j("DELETE", f"/cowork/desks/{purged['id']}?purge=true")
    check(not proot.exists(), "purge=true is the only thing that removes the directory")


TESTS = [test_a_desk_is_a_conversation_the_chat_list_hides,
         test_a_desk_is_told_it_is_a_desk_and_why_a_plan_comes_first,
         test_three_desks_run_at_once,
         test_the_live_desk_cap_409s,
         test_a_double_start_makes_one_run,
         test_an_unanswered_card_parks_rather_than_auto_denying,
         test_deciding_a_parked_card_resumes_the_desk,
         test_a_wake_that_lost_the_race_with_its_own_run_is_retried,
         test_a_watched_card_never_parks,
         test_a_chained_turn_hands_off_before_it_ends,
         test_a_turn_that_consumed_no_step_does_not_chain,
         test_the_desk_budget_caps_the_chain_even_with_turns_left,
         test_desk_ask_moves_the_desk_to_needs_you,
         test_ask_as_it_goes_cards_each_change_instead_of_planning_first,
         test_seen_clears_the_desks_needs_you_badge,
         test_a_steer_does_not_double_charge_the_turn,
         test_desk_done_with_an_output_lands_in_review,
         test_accept_is_exactly_once_and_reads_the_promotion_back,
         test_a_promotion_that_does_not_read_back_is_promote_failed,
         test_a_failed_promotion_can_be_retried,
         test_download_hands_over_the_file_it_marks_promoted,
         test_doc_append_proposes_a_revision_and_leaves_the_doc_alone,
         test_delete_keeps_the_workspace_unless_purge]


def _system_text(round_messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content") or "") for m in round_messages if m.get("role") == "system")


def _tool_text(round_messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content") or "") for m in round_messages if m.get("role") == "tool")


def test_a_desk_is_told_it_is_a_desk() -> None:
    script({"text": "Looked."})
    did = make_desk("Just look")["desk"]["id"]
    quiet(did)
    check("## This is a cowork desk" in _system_text(SCRIPT["messages"][0]),
          "DESK_HINT reaches the model, so it knows about outputs/, desk_deliver, desk_ask and desk_done")


def test_a_woken_desk_sees_its_approved_plan_and_what_the_user_said() -> None:
    """A parked plan approved later wakes the desk with a fresh turn whose history is text only, so
    the plan and the answer have to be put in front of the model explicitly."""
    settings_patch(parkAfterSeconds=1)
    try:
        did, row = _parked_desk()
        settings_patch(parkAfterSeconds=0)
        cid = desk(did)["conversation_id"]
        with db.tx() as c:
            msg = c.execute("SELECT tool_events FROM messages WHERE conversation_id=? AND role='assistant' "
                            "ORDER BY created_at LIMIT 1", (cid,)).fetchone()
        parked_card = [e for e in json.loads(msg["tool_events"] or "[]") if e["id"] == row["call_id"]]
        check(parked_card and parked_card[0]["pending"], "the parked turn persisted its card, still answerable after a reload")
        first_round = len(SCRIPT["messages"])
        j("POST", f"/approvals/{row['call_id']}", {"decision": "allow", "note": "Keep it short."})
        wait_until(lambda: len(run_store.list(desk_id=did, statuses=None)) == 2, "the woken turn")
        quiet(did)
        woken = _system_text(SCRIPT["messages"][first_round])
        check("## Approved plan: Needs a decision" in woken, "the woken turn is shown the plan it is carrying out")
        check("## While this desk was waiting" in woken and "approved the plan" in woken,
              "and told the user approved it while it was parked")
        check("Keep it short." in woken, "with the note the user gave")
        check(run_store.approval(row["call_id"])["reported_at"], "which is told once, then marked")
        later = _system_text(SCRIPT["messages"][-1])
        check("[x] 1." in later, f"the plan block tracks step status as it runs, got {later[-300:]!r}")
        with db.tx() as c:
            msg = c.execute("SELECT tool_events FROM messages WHERE conversation_id=? AND role='assistant' "
                            "ORDER BY created_at LIMIT 1", (cid,)).fetchone()
        settled = [e for e in json.loads(msg["tool_events"] or "[]") if e["id"] == row["call_id"]][0]
        check(not settled["pending"] and not settled.get("error"), "and the parked card settles as answered, not as an error")
    finally:
        settings_patch(parkAfterSeconds=0)


def test_an_approved_parked_call_runs_on_the_next_turn_without_a_second_card() -> None:
    settings_patch(parkAfterSeconds=1)
    try:
        script({"calls": [WRITE]}, {"calls": [WRITE]}, {"text": "Written."})
        did = make_desk("Ask me first", autonomy="ask")["desk"]["id"]
        row = card(did, "desk_write_file")
        wait_until(lambda: desk(did)["status"] == "blocked", "the ask-as-it-goes card to park")
        quiet(did)
        settings_patch(parkAfterSeconds=0)
        j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
        wait_until(lambda: len(run_store.list(desk_id=did, statuses=None)) == 2, "the woken turn")
        quiet(did)
        check(workspace.resolve_in(did, "outputs/report.md").is_file(), "the repeated call ran")
        check(not [a for a in run_store.approvals(None, desk_id=did) if a["call_id"] != row["call_id"]],
              "without opening a second card")
        check(run_store.approval(row["call_id"])["claimed_by"], "spending the parked grant")
        check(run_store.claim_parked(did, "desk_write_file", WRITE["arguments"], "again") is None,
              "which is single use")
    finally:
        settings_patch(parkAfterSeconds=0)


def test_answering_a_desk_ask_card_carries_on_in_the_same_turn() -> None:
    script({"calls": [call("desk_ask", question="Which vendor?")]}, {"text": "Pricing against Acme."})
    did = make_desk("Ask, then carry on")["desk"]["id"]
    row = card(did, "desk_ask")
    check("Which vendor?" in desk(did)["question"], "the open card puts its question on the desk for the banner")
    j("POST", f"/approvals/{row['call_id']}", {"decision": "allow", "note": "Acme"})
    quiet(did)
    check('"answer": "Acme"' in _tool_text(SCRIPT["messages"][1]), "the answer goes back as the tool result")
    state = desk(did)
    check(state["status_reason"] != "question" and not state["question"],
          f"and the desk does not block on a question already answered, got {state['status']}/{state['status_reason']}")


def test_a_message_answers_an_open_desk_ask_card() -> None:
    script({"calls": [call("desk_ask", question="Which quarter?")]}, {"text": "Using Q3."})
    did = make_desk("Ask through the banner")["desk"]["id"]
    card(did, "desk_ask")
    out = j("POST", f"/cowork/desks/{did}/message", {"content": "Q3"})
    check(out.get("answered") is True and out.get("live") is True, "the banner's answer settles the live card")
    quiet(did)
    check('"answer": "Q3"' in _tool_text(SCRIPT["messages"][1]), "and reaches the model as the answer")


def test_a_pending_plan_shows_in_the_desk() -> None:
    script({"calls": [propose("Show me", step("desk_write_file", WRITE["arguments"]))]}, {"text": "ok"})
    did = make_desk("Plan it")["desk"]["id"]
    row = card(did, PLAN_TOOL)
    plan = desk(did)["plan"]
    check(plan and plan["status"] == "pending" and plan["call_id"] == row["call_id"],
          "a plan waiting on the user is in the desk payload, with the call id the card is decided by")
    j("POST", f"/approvals/{row['call_id']}", {"decision": "deny"})
    quiet(did)


def test_pause_and_stop_refuse_a_finished_desk() -> None:
    state = delivering_desk("Finish, then try to pause")
    check(state["status"] == "review", f"precondition: in review, got {state['status']}")
    j("POST", f"/cowork/desks/{state['id']}/pause", expect=409)
    j("POST", f"/cowork/desks/{state['id']}/stop", expect=409)
    check(desk(state["id"])["status"] == "review", "and the desk is left in review")


def test_desk_changes_reach_the_app_topic() -> None:
    seq = topic.seq
    did = make_desk("Draft only", start=False)["desk"]["id"]
    j("PATCH", f"/cowork/desks/{did}", {"title": "Renamed"})
    wait_until(lambda: any(e == "desk_status" and d.get("id") == did and d.get("title") == "Renamed"
                           for s, e, d in list(topic._ring) if s > seq),
               "a desk_status event for the rename on /events")


def _chat(plan_mode: str) -> str:
    cid = j("POST", "/conversations", {"title": f"plan {plan_mode}"})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"planMode": plan_mode}})
    return cid


def _chat_turn(cid: str, text: str) -> None:
    j("POST", f"/conversations/{cid}/chat", {"content": text})
    wait_until(lambda: not bus.live(cid), "the chat reply to end")


def test_chat_plan_mode_always_offers_only_reading_and_the_plan() -> None:
    script({"text": "ok"})
    _chat_turn(_chat("always"), "add a todo")
    offered = SCRIPT["tools"][0]
    check(PLAN_TOOL in offered, "the plan tool is offered")
    check(all(toolbox.specs[n].danger in PLAN_SAFE_DANGER for n in offered if n in toolbox.specs),
          f"and nothing consequential, got {[n for n in offered if toolbox.specs.get(n) and toolbox.specs[n].danger not in PLAN_SAFE_DANGER]}")
    check("## Plan mode is on" in _system_text(SCRIPT["messages"][0]), "and the model is told why")
    script({"text": "ok"})
    _chat_turn(_chat("off"), "add a todo")
    check("todo_add" in SCRIPT["tools"][0], "with plan mode off the same chat is offered writes")


def test_chat_plan_mode_auto_turns_on_at_the_first_change() -> None:
    script({"calls": [call("todo_add", text="Buy milk")]}, {"text": "I will propose a plan."})
    _chat_turn(_chat("auto"), "add a todo")
    check("todo_add" in SCRIPT["tools"][0], "auto offers everything until something consequential is reached for")
    check("planning" in _tool_text(SCRIPT["messages"][1]), f"the first write is refused with the planning message, got {_tool_text(SCRIPT['messages'][1])[:300]!r} / {len(SCRIPT['messages'])}")
    check("todo_add" not in SCRIPT["tools"][1], "and from the next round only reading and the plan are offered")


def test_a_planning_desk_is_not_offered_desk_done_or_desk_start() -> None:
    script({"calls": [propose("Write it up", step("desk_write_file", WRITE["arguments"]))]}, {"text": "Waiting."})
    did = make_desk("Write the note")["desk"]["id"]
    j("POST", f"/approvals/{card(did, PLAN_TOOL)['call_id']}", {"decision": "deny"})
    quiet(did)
    first = SCRIPT["tools"][0]
    check("desk_done" not in first and "desk_start" not in first, f"neither is offered while planning, got {first}")
    check(PLAN_TOOL in first, "but the plan tool is")
    script({"calls": [call("desk_read_file", path="work/none.txt")]}, {"text": "Done."})
    asking = make_desk("Look around", autonomy="ask")["desk"]["id"]
    quiet(asking)
    check("desk_done" in SCRIPT["tools"][0] and "desk_start" not in SCRIPT["tools"][0],
          "an approved/ask desk is offered desk_done, and still never desk_start")


def test_a_reply_that_just_ends_gets_exactly_one_nudge() -> None:
    script({"text": "I think that is everything."}, {"text": "Still nothing."})
    did = make_desk("Do it", autonomy="ask")["desk"]["id"]
    quiet(did)
    time.sleep(0.3)
    quiet(did)
    firsts = [m for m in SCRIPT["messages"] if m]
    nudges = [m for m in firsts if "ended your reply without calling" in str(m[-1].get("content") or "")]
    check(len(nudges) == 1, f"one nudge turn was started, got {len(nudges)} of {len(firsts)} rounds")
    check(len(run_store.list(desk_id=did, statuses=None)) == 2, "and no third turn")
    check(desk(did)["status"] in ("review", "blocked"), "a nudged turn that also just ends settles")


TESTS += [test_a_planning_desk_is_not_offered_desk_done_or_desk_start,
         test_a_reply_that_just_ends_gets_exactly_one_nudge,
         test_a_desk_is_told_it_is_a_desk,
         test_a_woken_desk_sees_its_approved_plan_and_what_the_user_said,
         test_an_approved_parked_call_runs_on_the_next_turn_without_a_second_card,
         test_answering_a_desk_ask_card_carries_on_in_the_same_turn,
         test_a_message_answers_an_open_desk_ask_card,
         test_a_pending_plan_shows_in_the_desk,
         test_pause_and_stop_refuse_a_finished_desk,
         test_desk_changes_reach_the_app_topic,
         test_chat_plan_mode_always_offers_only_reading_and_the_plan,
         test_chat_plan_mode_auto_turns_on_at_the_first_change]


def _loose_ends() -> Iterator[str]:
    """A test that failed before answering its card leaves a run waiting forever: parking and the
    chat timeout are both off here. Clear them so the next test starts from a quiet backend."""
    for row in client.get("/approvals").json():
        client.post(f"/approvals/{row['call_id']}", json={"decision": "deny"})
        yield f"denied a leftover {row['tool']} card"
    for run in client.get("/runs").json():
        client.post(f"/conversations/{run['conversation_id']}/stop")
        yield f"stopped a leftover run on {run['conversation_id']}"
    for d in client.get("/cowork/desks").json():
        if d["status"] in LIVE:
            client.post(f"/cowork/desks/{d['id']}/stop")
            yield f"stopped desk {d['id']}"


if __name__ == "__main__":
    failures = 0
    # One portal, so every request shares the event loop the run tasks live on. Parking is off
    # except in the test that is about it, and the chat approval timeout is off everywhere: every
    # card here is decided by the test that opened it, and a timer firing first would be a race.
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "planMode": "off",
                                      "parkAfterSeconds": 0, "approvalWaitSeconds": 0, "deskMaxLive": 4})
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
