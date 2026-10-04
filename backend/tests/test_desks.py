"""Desks: the two locks, the one status writer, recovery, and the rail label's debounce.

Four of these tests exist because of a race, not because of a feature. `claim_run` and
`claim_output` are the start and promotion locks, so both are driven from two real threads and the
assertion is that exactly one caller wins. `set_status` is the only writer of `desks.status` and it
must write the column and the timeline row in ONE transaction, so that test makes the event insert
fail with a trigger and asserts the status rolled back with it. `recover()` is what the user sees
after a crash, so it must leave a needs_you event behind for every desk it interrupts.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_desks.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="desktest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.cowork import (  # noqa: E402
    AUTONOMY, DESK_CONTINUE, DESK_HINT, DESK_RESUME, HEADLINE_FLUSH_S, LIVE, NEEDS_YOU,
    OUTPUT_KINDS, RECOVER_FROM, STATUSES, DeskRuntime, Desks,
)
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

DATA_DIR = Path(os.environ["PERSONAL_OS_DATA_DIR"])
db = Database(DATA_DIR)
workspace = Workspace(DATA_DIR)
convos = Conversations(db)
desks = Desks(db, workspace)
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def fresh(**kw: Any) -> dict[str, Any]:
    conv = convos.create(None, "desk", "test-model")
    return desks.create(conversation_id=conv["id"], brief=kw.pop("brief", "Write the brief"), **kw)


def kinds(desk_id: str) -> list[str]:
    return [e["kind"] for e in desks.events(desk_id)]


# ---- shape ----
def test_constants() -> None:
    check(set(NEEDS_YOU) <= set(STATUSES) and set(LIVE) <= set(STATUSES), "NEEDS_YOU and LIVE are statuses")
    check(not set(NEEDS_YOU) & {"draft", "working", "done", "failed", "stopped", "paused", "planning"},
          "NEEDS_YOU holds only states that are genuinely waiting on the user")
    check(LIVE == ("planning", "working", "needs_approval"), "LIVE drives live_count()")
    check(set(RECOVER_FROM) == set(LIVE) | {"awaiting_plan"},
          "recover() also sweeps awaiting_plan: its card was held open by a run that died with the process")
    check(not set(RECOVER_FROM) & {"paused", "blocked", "draft", "review"},
          "...and nothing the user parked on purpose")
    check(AUTONOMY == ("plan", "ask", "propose") and len(OUTPUT_KINDS) == 4, "the enums match the spec")
    for frag in (DESK_HINT, DESK_CONTINUE, DESK_RESUME):
        check(frag and frag == frag.strip(), "the prompt fragments are non-empty and unpadded")
    check("desk_deliver" in DESK_HINT and "desk_ask" in DESK_HINT and "desk_done" in DESK_HINT,
          "DESK_HINT names the three tools that end a turn honestly")


def test_create() -> None:
    d = fresh(brief="Summarise the Q3 numbers\nand send me a note")
    check(d["status"] == "draft" and d["turn"] == 0 and d["cost"] == 0, "a new desk is a draft that has done nothing")
    check(d["title"] == "Summarise the Q3 numbers", "the title comes from the brief's first line")
    check(d["workspace"] == f"cowork/{d['id']}", "the stored workspace path is relative, not absolute")
    check((DATA_DIR / "cowork" / d["id"] / "outputs").is_dir(), "create() made the workspace on disk")
    check(d["budget"] == {} and d["archived"] is False and d["live"] is False, "the view decodes JSON and booleans")
    check(d["unseen"] == 0, "the draft event does not need the user")
    check(kinds(d["id"]) == ["status"], "creating a desk leaves one timeline row")
    check(desks.by_conversation(d["conversation_id"])["id"] == d["id"], "by_conversation finds it")
    check(desks.get(d["id"])["outputs"] == [], "get() carries outputs by default")

    named = fresh(title="  Taxes  ")
    check(named["title"] == "Taxes", "an explicit title is stripped and wins")
    try:
        fresh(autonomy="yolo")
    except ValueError:
        check(True, "an unknown autonomy is refused rather than stored")
    else:
        raise AssertionError("an unknown autonomy was accepted")


def test_update_and_list() -> None:
    d = fresh(brief="budget desk", budget={"maxTurns": 3})
    check(d["budget"] == {"maxTurns": 3}, "the budget round-trips")
    upd = desks.update(d["id"], {"budget": {"maxTurns": 5}})
    check(upd["budget"] == {"maxTurns": 5}, "budget merges rather than replaces")
    upd = desks.update(d["id"], {"title": "Renamed", "status": "done", "cost": 99})
    check(upd["title"] == "Renamed", "a whitelisted field is written")
    check(upd["status"] == "draft" and upd["cost"] == 0, "status and cost are not patchable from update()")
    check(desks.update("nope", {"title": "x"}) is None, "patching an absent desk is None, not a crash")

    check(any(x["id"] == d["id"] for x in desks.list()), "an unarchived desk is listed")
    desks.update(d["id"], {"archived": True})
    check(not any(x["id"] == d["id"] for x in desks.list()), "an archived desk is not")
    check(any(x["id"] == d["id"] for x in desks.list(archived=True)), "…until archived=True")
    check(all(x["status"] == "draft" for x in desks.list(status="draft")), "the status filter is exact")


# ---- the status writer ----
def test_set_status_writes_column_and_event() -> None:
    d = fresh()
    out = desks.set_status(d["id"], "awaiting_plan", reason="plan", plan_id="p1", run_id="r1",
                           headline="waiting on your plan")
    check(out["status"] == "awaiting_plan" and out["plan_id"] == "p1" and out["run_id"] == "r1",
          "the columns are written")
    ev = desks.events(d["id"])[-1]
    check(ev["kind"] == "plan" and ev["needs_you"] is True, "a NEEDS_YOU status appends a needs_you event")
    check(ev["data"]["status"] == "awaiting_plan" and ev["data"]["reason"] == "plan", "the event carries the reason")
    check(desks.get(d["id"])["unseen"] == 1, "the unseen count on the desk view follows it")

    done = desks.set_status(d["id"], "done")
    check(done["ended_at"] is not None and done["run_id"] is None,
          "a terminal status stamps ended_at and drops the run that is no longer driving it")
    check(desks.set_status(d["id"], "working", event=False) is not None, "event=False still writes the column")
    check(kinds(d["id"]).count("status") == 2, "…and appends nothing")
    try:
        desks.set_status(d["id"], "almost_done")
    except ValueError:
        check(True, "an unknown status is refused: the state machine is closed")
    else:
        raise AssertionError("an unknown status was written")
    check(desks.set_status("nope", "done") is None, "setting the status of an absent desk is None")


def test_set_status_is_one_transaction() -> None:
    """The invariant: a failing event insert takes the status column back with it."""
    d = fresh()
    desks.set_status(d["id"], "working")
    with db.tx() as c:
        c.execute("CREATE TRIGGER t_boom BEFORE INSERT ON desk_events WHEN NEW.body LIKE '%boom%'"
                  " BEGIN SELECT RAISE(ABORT, 'boom'); END")
    try:
        try:
            desks.set_status(d["id"], "failed", reason="boom")
        except sqlite3.DatabaseError:
            check(True, "the event insert failed as the test intended")
        else:
            raise AssertionError("the trigger did not fire")
        check(desks.get(d["id"])["status"] == "working",
              "the status column rolled back with the event: both writes are one transaction")
        check(not any("boom" in e["body"] for e in desks.events(d["id"])), "and no half-written event survived")
    finally:
        with db.tx() as c:
            c.execute("DROP TRIGGER t_boom")


# ---- the locks ----
def _race(fn: Any, n: int = 2) -> list[Any]:
    """Run `fn` on n threads that start together, so the lock is tested, not the scheduler."""
    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n
    def go(i: int) -> None:
        barrier.wait()
        try:
            results[i] = fn()
        except BaseException as e:  # noqa: BLE001 - the failure itself is the result under test
            results[i] = e
    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return results


def test_claim_run_is_a_lock() -> None:
    d = fresh()
    got = _race(lambda: desks.claim_run(d["id"], ("draft",)))
    check(all(not isinstance(r, BaseException) for r in got), f"neither claim errored: {got}")
    won = [r for r in got if r]
    check(len(won) == 1, f"exactly one of two concurrent claims wins, got {len(won)}")
    check(won[0]["status"] == "planning", "a desk with no plan claims into planning")
    check(won[0]["run_id"] is None, "the claim clears run_id; the runner sets it with update_run")
    check(kinds(d["id"]).count("status") == 2, "the winning claim appended exactly one timeline row")
    check(desks.claim_run(d["id"], ("draft",)) is None, "a third claim from the wrong status is refused")


def test_claim_run_targets_working_once_a_plan_exists() -> None:
    d = fresh()
    desks.set_status(d["id"], "blocked", reason="question", plan_id="plan-1")
    out = desks.claim_run(d["id"], ("blocked", "paused", "interrupted", "review"))
    check(out and out["status"] == "working", "a desk that already has a plan resumes into working")
    check(out["status_reason"] == "" and out["last_error"] is None, "the claim clears the old reason and error")
    check(desks.claim_run(d["id"], ()) is None, "an empty from_statuses claims nothing")


def test_claim_output_is_a_lock() -> None:
    d = fresh()
    o = desks.declare_output(d["id"], "outputs/brief.md", "Brief", "", "a" * 64, 120, "run-1")
    got = _race(lambda: desks.claim_output(o["id"]))
    check(all(not isinstance(r, BaseException) for r in got), f"neither claim errored: {got}")
    check(len([r for r in got if r]) == 1, "a double-clicked Accept claims once")
    check(desks.output(o["id"])["status"] == "accepted", "the claimed row is accepted")
    check(desks.claim_output(o["id"]) is None, "an accepted row cannot be claimed again")


# ---- outputs ----
def test_outputs_lifecycle() -> None:
    d = fresh()
    o = desks.declare_output(d["id"], "outputs/a.md", "A", "the first draft", "s" * 64, 10, "run-1")
    check(o["status"] == "proposed" and o["verified"] is False, "a delivered file is proposed, not promoted")
    again = desks.declare_output(d["id"], "outputs/a.md", "A2", "rewritten", "t" * 64, 20, "run-2")
    check(again["id"] == o["id"], "re-writing a file updates one row")
    check(again["title"] == "A2" and again["sha256"] == "t" * 64, "…with the new bytes")
    check(len(desks.outputs(d["id"])) == 1, "and does not add a second")
    check(kinds(d["id"]).count("output") == 2, "each delivery is a timeline row")

    desks.claim_output(again["id"])
    bad = desks.finish_output(again["id"], kind="doc", ref="doc-1", verified=False)
    check(bad["status"] == "promote_failed" and bad["verified"] is False,
          "an unverified read-back is promote_failed, never a tick")
    check(desks.claim_output(again["id"]) is not None, "a failed promotion can be retried")
    ok = desks.finish_output(again["id"], kind="doc", ref="doc-1", verified=True)
    check(ok["status"] == "promoted" and ok["promoted_id"] == "doc-1" and ok["verified"] is True, "and then succeed")
    check(kinds(d["id"])[-1] == "promoted", "a verified promotion is a timeline row")
    check(desks.claim_output(again["id"]) is None, "a promoted row is final")
    check(desks.reject_output(again["id"]) is None, "a promoted row cannot be rejected afterwards")
    try:
        desks.finish_output(again["id"], kind="telepathy", ref=None, verified=True)
    except ValueError:
        check(True, "an unknown destination is refused")
    else:
        raise AssertionError("an unknown destination was stored")

    other = desks.declare_output(d["id"], "outputs/b.md", "B", "", "u" * 64, 3, None)
    check(desks.reject_output(other["id"])["status"] == "rejected", "a proposed row can be rejected")
    check(desks.finish_output("nope", kind="doc", ref=None, verified=True) is None, "an absent output is None")


# ---- settle ----
def test_settle() -> None:
    d = fresh()
    desks.claim_run(d["id"], ("draft",))
    desks.set_status(d["id"], "working")
    check(desks.settle(d["id"], partial="rounds", stopped=False, error=None, chain=True)["status"] == "working",
          "a turn the supervisor will chain leaves the desk working")
    check(desks.settle(d["id"], partial="rounds", stopped=False, error=None)["status"] == "review",
          "a budget-window stop with no chain lands in review")
    check(desks.get(d["id"])["status_reason"] == "budget", "…with the reason the UI shows")

    d2 = fresh()
    desks.set_status(d2["id"], "working")
    out = desks.settle(d2["id"], partial="blocked", stopped=False, error=None)
    check(out["status"] == "blocked" and out["status_reason"] == "approval",
          "a parked approval settles to blocked, not to an error")

    d3 = fresh()
    desks.set_status(d3["id"], "working")
    out = desks.settle(d3["id"], partial=None, stopped=False, error="boom")
    check(out["status"] == "failed" and out["last_error"] == "boom", "an error settles to failed and keeps the text")

    d4 = fresh()
    desks.set_status(d4["id"], "blocked", reason="question", question="Which account?")
    out = desks.settle(d4["id"], partial=None, stopped=False, error=None)
    check(out["status"] == "blocked" and out["question"] == "Which account?",
          "a desk that already decided its own end (desk_ask) is left alone")
    check(desks.settle(d4["id"], partial=None, stopped=True, error=None)["status"] == "stopped",
          "…but a stop outranks it")

    d5 = fresh()
    desks.set_status(d5["id"], "working")
    desks.declare_output(d5["id"], "outputs/c.md", "C", "", "v" * 64, 4, None)
    check(desks.settle(d5["id"], partial=None, stopped=False, error=None)["status"] == "review",
          "a turn that just stops with undecided outputs goes to review, never silently to done")
    check(desks.settle("nope", partial=None, stopped=False, error=None) == {}, "settling an absent desk is empty")


# ---- counts, recovery, events ----
def test_live_count_and_charge() -> None:
    before = desks.live_count()
    d = fresh()
    desks.set_status(d["id"], "working")
    check(desks.live_count() == before + 1, "a working desk is live")
    out = desks.charge(d["id"], 0.25, 1)
    check(out["cost"] == 0.25 and out["turn"] == 1, "charge accumulates cost and turns")
    desks.charge(d["id"], 0.25)
    check(desks.get(d["id"])["cost"] == 0.5, "…and adds rather than replaces")
    desks.set_status(d["id"], "done")
    check(desks.live_count() == before, "a finished desk is not")


def test_recover() -> None:
    live = []
    for status in LIVE:
        d = fresh()
        desks.set_status(d["id"], status, run_id="r-1")
        live.append(d["id"])
    # awaiting_plan is a needs-you state, not a LIVE one, but the run holding its plan card is
    # in-process: a restart kills it, and the desk is in neither RESUME_FROM nor MESSAGE_FROM, so a
    # boot that left it alone would strand it forever.
    carded = fresh()
    desks.set_status(carded["id"], "awaiting_plan", plan_id="p-1", run_id="r-1")
    live.append(carded["id"])
    quiet = fresh()
    desks.set_status(quiet["id"], "paused")

    n = desks.recover()
    check(n >= len(live), f"recover() reports what it interrupted, got {n}")
    for did in live:
        row = desks.get(did)
        check(row["status"] == "interrupted", "every desk whose run died is interrupted at boot")
        check(row["status_reason"] == "restart" and row["run_id"] is None, "…with no run still claimed")
        ev = desks.events(did)[-1]
        check(ev["kind"] == "interrupted" and ev["needs_you"] is True,
              "…and a needs_you event, so a desk that died while the app was closed is still visible")
    check(desks.get(quiet["id"])["status"] == "paused", "a desk that was not live is untouched")
    check(desks.live_count() == 0, "nothing is live after recovery")
    check(desks.recover() == 0, "recovery is idempotent: no desk is auto-resumed")


def test_events_and_inbox() -> None:
    d = fresh()
    ev = desks.event(d["id"], "note", "Found three candidates.", run_id="r-9", count=3)
    check(ev["kind"] == "note" and ev["data"] == {"count": 3} and ev["needs_you"] is False,
          "**data lands in the event's data blob")
    asked = desks.event(d["id"], "question", "Which account?", needs_you=True)
    inbox = desks.inbox()
    check(any(x["id"] == asked["id"] for x in inbox), "an unseen needs_you event is in the inbox")
    check(not any(x["id"] == ev["id"] for x in inbox), "a plain note is not")
    check(all("desk_title" in x for x in inbox), "the inbox row names its desk without a second query")

    desks.mark_seen(asked["id"])
    check(not any(x["id"] == asked["id"] for x in desks.inbox()), "marking it seen clears it")
    check(desks.get(d["id"])["unseen"] == 0, "and the desk's unseen badge with it")
    times = [e["created_at"] for e in desks.events(d["id"])]
    check(times == sorted(times), "events read oldest first, like a timeline")
    check(len(desks.events(d["id"], limit=1)) == 1, "the limit takes the newest")


def test_delete() -> None:
    d = fresh()
    desks.event(d["id"], "note", "x")
    desks.declare_output(d["id"], "outputs/d.md", "D", "", "w" * 64, 1, None)
    desks.delete(d["id"])
    check(desks.get(d["id"]) is None, "the desk is gone")
    with db.tx() as c:
        check(c.execute("SELECT COUNT(*) FROM desk_events WHERE desk_id=?", (d["id"],)).fetchone()[0] == 0,
              "its events cascade")
        check(c.execute("SELECT COUNT(*) FROM desk_outputs WHERE desk_id=?", (d["id"],)).fetchone()[0] == 0,
              "its outputs cascade")
    check((DATA_DIR / "cowork" / d["id"]).is_dir(), "deleting the row leaves the workspace on disk (§6.5)")


# ---- the rail label ----
class _CountingDesks:
    """A Desks that counts headline writes, because 'at most once a second' is the assertion."""

    def __init__(self, inner: Desks) -> None:
        self.inner, self.writes = inner, 0

    def set_headline(self, id: str, headline: str, live_only: bool = False) -> None:
        self.writes += 1
        self.inner.set_headline(id, headline, live_only)

    def get(self, id: str, with_outputs: bool = True) -> dict[str, Any] | None:
        return self.inner.get(id, with_outputs)


def test_runtime_debounce() -> None:
    d = fresh()
    desks.set_status(d["id"], "working")
    spy = _CountingDesks(desks)
    clock = [0.0]
    rt = DeskRuntime(spy, d["id"], clock=lambda: clock[0])  # type: ignore[arg-type]

    for i in range(400):
        clock[0] += 0.01
        check_none = rt.observe("delta", {"id": "m1", "text": "word "})
        if check_none is not None:
            raise AssertionError("a delta published a desk_status")
    check(spy.writes == 0, "400 deltas wrote nothing: the headline is never computed per token")

    clock[0] = 10.0
    first = rt.observe("tool_call", {"name": "desk_write_file", "arguments": {"path": "outputs/brief.md"}})
    check(first is not None and first["headline"] == "writing outputs/brief.md",
          "a tool call names what the rail shows")
    check(spy.writes == 1, "the first interesting event flushes immediately")

    clock[0] = 10.3
    check(rt.observe("tool_result", {"name": "desk_write_file"}) is None, "a second write inside the window is held")
    clock[0] = 10.6
    check(rt.observe("tool_call", {"name": "web_search", "arguments": {"query": "q"}}) is None, "…and so is a third")
    check(spy.writes == 1, "at most one row write per second, whatever the run publishes")

    clock[0] = 11.1
    out = rt.observe("tool_call", {"name": "web_search", "arguments": {"query": "q"}})
    check(out is not None and out["headline"] == "searching the web", "after the window the latest label lands")
    check(spy.writes == 2, "exactly two writes for seven events")

    clock[0] = 11.2
    out = rt.observe("plan_card", {"message_id": "m", "call_id": "c", "plan": {}})
    check(out is not None and out["headline"] == "waiting on your plan",
          "a status change flushes immediately, inside the window")
    check(rt.observe("plan_card", {"message_id": "m", "call_id": "c", "plan": {}}) is None,
          "…but an unchanged label publishes nothing")
    check(desks.get(d["id"])["headline"] == "waiting on your plan", "the row holds the last flushed label")
    check(HEADLINE_FLUSH_S == 1.0, "the window is the documented one")

    desks.set_status(d["id"], "review", headline="")
    clock[0] = 20.0
    rt.observe("tool_result", {"name": "desk_done"})
    check(desks.get(d["id"])["headline"] == "", "a tool_result after the desk settled does not relabel it 'thinking'")


TESTS = [test_constants, test_create, test_update_and_list, test_set_status_writes_column_and_event,
         test_set_status_is_one_transaction, test_claim_run_is_a_lock,
         test_claim_run_targets_working_once_a_plan_exists, test_claim_output_is_a_lock,
         test_outputs_lifecycle, test_settle, test_live_count_and_charge, test_recover,
         test_events_and_inbox, test_delete, test_runtime_debounce]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
