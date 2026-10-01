"""Scheduled jobs, the catch-up rule, proposal-only background runs, and the Agent Inbox.

The clock is injected and fake; nothing sleeps; Google is a recorder, so a test that "sends" mail only
appends to a list.

Run: uv run --project backend --with pytest pytest backend/tests/test_agent_jobs.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="jobstest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.jobs import (  # noqa: E402
    LATE_GRACE_S, SEED_JOBS, Jobs, Scheduler, next_fire, prev_fire, slots_between, valid_cron, valid_tz,
)

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
jobs = appmod.jobs
proposals = appmod.proposals

HOUR = 3600.0
# A fixed, unambiguous instant: 2026-03-02 00:00:00 UTC, a Monday, nowhere near a DST edge.
T0 = 1772409600.0

ROUNDS: list[Any] = []
# Everything a Google write would have done, if it had been allowed to.
SENT: list[tuple[str, dict[str, Any]]] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto") -> Any:
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        # The app's own scheduler runs on the real clock; these tests drive tick() with a fake one, so stop it.
        task = getattr(appmod.app.state, "jobs_task", None)
        if task is not None:
            task.get_loop().call_soon_threadsafe(task.cancel)
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    SENT.clear()
    # Only the job a test creates may be armed, so a tick fires exactly what that test is about.
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET enabled=0, next_due_at=NULL")
    for name in ("gmail_send", "gmail_draft", "calendar_create"):
        setattr(appmod.google, name, _recorder(name))
    yield
    ROUNDS.clear()


def _recorder(name: str):  # type: ignore[no-untyped-def]
    def fn(*args: Any, **kw: Any) -> dict[str, Any]:
        SENT.append((name, {"args": args, "kw": kw}))
        return {"id": f"{name}-{len(SENT)}", "ok": True}
    return fn


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def call(name: str, args: dict[str, Any], cid: str = "call_0") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def tick(at: float, sched: Scheduler | None = None) -> list[dict[str, Any]]:
    """One scheduler pass at a fake instant, with every run it launched driven to completion. Nothing sleeps."""
    s = sched or Scheduler(jobs, appmod._launch_job, clock=lambda: at)  # noqa: SLF001
    s.clock = lambda: at

    async def go() -> list[dict[str, Any]]:
        fired = await s.tick()
        for f in fired:
            run = next((r for r in appmod.bus._runs.values() if r.run_id == f["run_id"]), None)  # noqa: SLF001
            if run is not None and run.task is not None:
                with contextlib.suppress(Exception):
                    await run.task
        return fired

    return asyncio.run(go())


def make_job(name: str, cron: str, prompt: str = "report", *, at: float, enabled: bool = True) -> dict[str, Any]:
    return jobs.create(f"{name} {time.time()}", cron, prompt, timezone="UTC", enabled=enabled, at=at)


def job_runs(job_id: str) -> list[dict[str, Any]]:
    return [r for r in store.of_kind("job", limit=500) if (r["input"] or {}).get("job_id") == job_id]


# ---------------- cron arithmetic ----------------
def test_cron_helpers_read_the_expression_in_its_own_timezone() -> None:
    assert valid_cron("30 7 * * *") and valid_cron("0 17 * * 5")
    assert not valid_cron("every morning") and not valid_cron("")
    assert valid_tz("UTC") and valid_tz("Europe/Berlin") and not valid_tz("Mars/Olympus")
    assert next_fire("0 * * * *", "UTC", T0) == T0 + HOUR, "strictly after, so an exact hit is not returned twice"
    assert prev_fire("0 * * * *", "UTC", T0) == T0, "at-or-before, so an exact hit is returned"
    assert slots_between("0 * * * *", "UTC", T0, T0 + 4 * HOUR) == 5
    assert slots_between("0 * * * *", "UTC", T0, T0) == 1
    assert slots_between("0 * * * *", "UTC", T0 + HOUR, T0) == 0
    # The same wall-clock minute in two zones is two different instants.
    assert next_fire("30 7 * * *", "Europe/Berlin", T0) != next_fire("30 7 * * *", "UTC", T0)


def test_the_shipped_jobs_are_seeded_once_and_disabled() -> None:
    fresh = Jobs(appmod.db)
    assert fresh.seed(at=T0) >= 0
    seeded = {j["name"]: j for j in fresh.list()}
    for s in SEED_JOBS:
        assert s["name"] in seeded, f"{s['name']} is not seeded"
        row = seeded[s["name"]]
        assert valid_cron(row["cron"]) and row["prompt"]
        assert row["enabled"] is False and row["next_due_at"] is None, "an unattended run costs money: the user opts in"
    assert fresh.seed(at=T0) == 0, "seeding again adds nothing"
    assert [s["name"] for s in SEED_JOBS] == ["Morning brief", "Scan unread and draft replies", "Weekly review"]


# ---------------- firing ----------------
def test_a_due_job_fires_once_as_a_job_run() -> None:
    ROUNDS.append(["Here is your brief."])
    job = make_job("due", "0 * * * *", "brief me", at=T0)
    assert job["next_due_at"] == T0 + HOUR

    assert tick(T0 + 30 * 60) == [], "not due yet"
    assert job_runs(job["id"]) == []

    fired = tick(T0 + HOUR + 10)
    assert len(fired) == 1 and fired[0]["job_id"] == job["id"]
    assert fired[0]["due_at"] == T0 + HOUR and fired[0]["late"] is False and fired[0]["missed_slots"] == 0

    runs = job_runs(job["id"])
    assert len(runs) == 1, "one slot, one run"
    run = runs[0]
    assert run["kind"] == "job" and run["status"] == "done" and run["conversation_id"]
    assert run["input"]["job"] == job["name"] and run["input"]["due_at"] == T0 + HOUR
    assert jobs.get(job["id"])["last_run_id"] == run["run_id"]
    assert jobs.get(job["id"])["next_due_at"] == T0 + 2 * HOUR, "re-armed for the following slot"

    msgs = j("GET", f"/conversations/{run['conversation_id']}")["messages"]
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "brief me", "on time: no late notice"
    assert "Here is your brief." in msgs[-1]["content"]
    assert tick(T0 + HOUR + 20) == [], "the same slot does not fire twice"


def test_a_gap_of_many_slots_fires_once_for_the_most_recent_and_is_reported_late() -> None:
    """Asleep from 00:00 to 05:10 with an hourly job: one run, for 05:00, marked late. Not five runs."""
    ROUNDS.append(["Late brief."])
    job = make_job("gap", "0 * * * *", "brief me", at=T0)
    fired = tick(T0 + 5 * HOUR + 600)  # 05:10, having missed 01:00 02:00 03:00 04:00 and 05:00
    assert len(fired) == 1, "exactly one catch-up, not one per missed slot"
    f = fired[0]
    assert f["due_at"] == T0 + 5 * HOUR, "the most recent missed slot, not the oldest"
    assert f["late"] is True and f["late_seconds"] == 600.0 and f["missed_slots"] == 4
    runs = job_runs(job["id"])
    assert len(runs) == 1 and runs[0]["input"]["missed_slots"] == 4 and runs[0]["input"]["late"] is True

    user = j("GET", f"/conversations/{runs[0]['conversation_id']}")["messages"][0]["content"]
    assert user.startswith("[This run was scheduled for"), "the run is told it is late"
    assert "10 min late" in user and "4 earlier runs were skipped" in user
    assert user.endswith("brief me"), "the job's own prompt still follows"
    assert jobs.get(job["id"])["next_due_at"] == T0 + 6 * HOUR
    assert tick(T0 + 5 * HOUR + 700) == [], "the gap is closed, not replayed"


def test_a_fire_inside_the_grace_window_is_not_called_late() -> None:
    ROUNDS.append(["ok"])
    job = make_job("grace", "0 * * * *", at=T0)
    f = tick(T0 + HOUR + LATE_GRACE_S - 5)[0]
    assert f["late"] is False and f["missed_slots"] == 0 and f["late_seconds"] > 0
    assert j("GET", f"/conversations/{job_runs(job['id'])[0]['conversation_id']}")["messages"][0]["content"] == "report"


def test_a_disabled_job_never_fires_and_re_enabling_it_has_nothing_to_catch_up() -> None:
    job = make_job("off", "0 * * * *", at=T0, enabled=False)
    assert job["enabled"] is False and job["next_due_at"] is None
    # Even with a slot armed in the past (it was enabled once, then switched off), it is not due.
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET next_due_at=? WHERE id=?", (T0, job["id"]))
    assert jobs.due(T0 + 10 * HOUR) == [] or all(x["id"] != job["id"] for x in jobs.due(T0 + 10 * HOUR))
    assert tick(T0 + 10 * HOUR) == []
    assert job_runs(job["id"]) == []

    back = jobs.update(job["id"], {"enabled": True}, at=T0 + 10 * HOUR)
    assert back["next_due_at"] == T0 + 11 * HOUR, "re-armed from now: being switched off is not a missed slot"
    assert tick(T0 + 10 * HOUR + 60) == []
    assert job_runs(job["id"]) == []


def test_editing_the_schedule_re_arms_and_an_unreadable_cron_is_disarmed() -> None:
    job = make_job("edit", "0 * * * *", at=T0)
    moved = jobs.update(job["id"], {"cron": "0 0 * * *"}, at=T0 + 100)
    assert moved["next_due_at"] == next_fire("0 0 * * *", "UTC", T0 + 100), "the old slot is not inherited"
    with appmod.db.tx() as c:  # a row hand-edited into nonsense must not spin the scheduler
        c.execute("UPDATE jobs SET cron='not a cron', next_due_at=? WHERE id=?", (T0, job["id"]))
    assert tick(T0 + HOUR) == []
    row = jobs.get(job["id"])
    assert row["next_due_at"] is None and "not a cron" in (row["last_error"] or "")


def test_the_jobs_api_round_trips_and_rejects_nonsense() -> None:
    made = j("POST", "/jobs", {"name": "API job", "cron": "15 6 * * *", "prompt": "do it", "timezone": "UTC"})
    assert made["enabled"] is False and made["cron"] == "15 6 * * *" and made["next_due_at"] is None
    on = j("PATCH", f"/jobs/{made['id']}", {"enabled": True})
    assert on["enabled"] is True and on["next_due_at"] > time.time()
    assert any(x["id"] == made["id"] for x in j("GET", "/jobs"))
    j("POST", "/jobs", {"name": "bad", "cron": "every morning", "prompt": "x"}, expect=400)
    j("PATCH", f"/jobs/{made['id']}", {"timezone": "Mars/Olympus"}, expect=400)
    j("PATCH", "/jobs/nope", {"enabled": True}, expect=404)
    assert j("DELETE", f"/jobs/{made['id']}") == {"ok": True}
    j("DELETE", f"/jobs/{made['id']}", expect=404)


def test_a_manual_run_is_still_a_job_run_and_leaves_the_schedule_alone() -> None:
    ROUNDS.append(["by hand"])
    job = make_job("manual", "0 0 1 1 *", "do it", at=T0, enabled=False)
    res = j("POST", f"/jobs/{job['id']}/run")
    assert res["ok"] and res["run_id"]
    row = wait_done(res["run_id"])
    assert row["kind"] == "job" and row["input"]["manual"] is True and row["input"]["late"] is False
    assert jobs.get(job["id"])["next_due_at"] is None, "firing by hand does not arm a disabled job"
    j("POST", "/jobs/nope/run", expect=404)


def test_the_loop_only_sleeps_and_selects_and_fires_once_when_the_slot_arrives() -> None:
    """No heartbeat: a wake with nothing due launches nothing. The clock and the sleep are both injected."""
    make_job("loop", "0 * * * *", at=T0)
    at = {"t": T0}
    naps: list[float] = []
    launched: list[dict[str, Any]] = []

    async def fake_sleep(s: float) -> None:
        naps.append(s)
        at["t"] += s
        if len(naps) > 90:  # an hour of one-minute wakes, plus a little
            raise asyncio.CancelledError

    async def record(job_row: dict[str, Any], fire: dict[str, Any]) -> str:
        launched.append(fire)
        return "fake-run"

    sched = Scheduler(jobs, record, clock=lambda: at["t"], sleep=fake_sleep)
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(sched.loop())
    assert all(n <= 60.0 for n in naps), "one wake is at most a minute, so a catch-up after a suspend is not hours late"
    assert len(launched) == 1 and launched[0]["due_at"] == T0 + HOUR, "fired once, when the slot came"
    assert launched[0]["late_seconds"] <= 60.0 and launched[0]["late"] is False
    assert sched.fires == 1 and len(naps) > 50, "it slept its way there rather than spinning"


def test_a_job_transcript_is_a_conversation_the_sidebar_does_not_show() -> None:
    ROUNDS.append(["done"])
    job = make_job("hidden", "0 * * * *", at=T0)
    tick(T0 + HOUR)
    cid = job_runs(job["id"])[0]["conversation_id"]
    assert all(c["id"] != cid for c in j("GET", "/conversations")), "a job's own chat does not bury the user's"
    listed = next(c for c in j("GET", "/conversations?include_jobs=true") if c["id"] == cid)
    assert listed["settings"]["job_id"] == job["id"] and listed["settings"]["autoLearn"] is False
    assert j("GET", f"/conversations/{cid}")["id"] == cid, "the inbox's 'open' link still reaches it"
    assert all(c["id"] != cid for c in j("GET", "/dashboard")["recent_conversations"])


def wait_done(run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = store.get(run_id)
        if row and row["status"] not in ("running", "awaiting_approval"):
            return row
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish")


# ---------------- proposal-only ----------------
def test_an_external_write_in_a_job_run_becomes_a_proposal_and_never_reaches_the_api() -> None:
    args = {"to": "mira@example.com", "subject": "Re: invoice", "body": "Looks right to me."}
    ROUNDS.append({"tool_calls": [call("gmail_send", args)]})
    ROUNDS.append(["I drafted a reply for you to approve."])
    job = make_job("mail", "0 * * * *", "scan my unread mail and reply", at=T0)
    tick(T0 + HOUR)
    run = job_runs(job["id"])[0]
    assert run["status"] == "done"
    assert SENT == [], "nothing left the machine"

    mine = proposals.list("pending", run_id=run["run_id"])
    assert len(mine) == 1
    p = mine[0]
    assert p["tool"] == "gmail_send" and p["args"] == args and p["status"] == "pending" and p["edited"] is False
    assert p["job_id"] == job["id"] and p["conversation_id"] == run["conversation_id"] and p["call_id"]

    results = [d for _, e, d in store.events(run["run_id"]) if e == "tool_result"]
    assert len(results) == 1 and results[0]["proposal"] == p["id"] and results[0]["error"] is None
    assert "proposed" in results[0]["result_preview"] and p["id"] in results[0]["result_preview"]
    assert store.executed(run["run_id"]) == [], "a proposal is not a side effect, so it is not journaled as one"
    assert store.approvals(None, run_id=run["run_id"]) == [], "a background run never waits on an approval"


def test_the_toolbox_refuses_an_external_tool_whenever_the_context_says_proposal_only() -> None:
    """The second gate, in the module that owns the tool functions: even a caller that forgot the first one is safe."""
    ctx = {"project_id": None, "conversation_id": None, "settings": appmod.settings(), "proposal_only": True}
    out = asyncio.run(appmod.toolbox.call("gmail_send", {"to": "a@b.c", "subject": "s", "body": "b"}, ctx))
    assert "error" in out and "background run" in out["error"] and SENT == []
    assert asyncio.run(appmod.toolbox.call("calendar_create", {"summary": "x", "start": "2026-10-01T10:00"}, ctx))["error"]
    # An in-app write is not outward-facing and still works in a job.
    ok = asyncio.run(appmod.toolbox.call("todo_add", {"title": f"proposal-only todo {time.time()}"}, ctx))
    assert "error" not in ok and ok["id"]


def test_a_job_run_is_told_it_is_a_job_and_runs_on_a_tighter_budget() -> None:
    ROUNDS.append(["noted"])
    job = make_job("budget", "0 * * * *", at=T0)
    tick(T0 + HOUR)
    run = job_runs(job["id"])[0]
    b = run["budget"]
    cfg = appmod.settings()
    assert b["max_rounds"] == min(cfg["maxToolRounds"], appmod.JOB_BUDGET["maxToolRounds"]) < cfg["maxToolRounds"]
    assert b["max_tokens"] < cfg["maxRunTokens"] and b["max_seconds"] < cfg["maxRunSeconds"]
    assert b["max_cost"] < cfg["maxRunCost"]
    assert appmod._caps({"maxToolRounds": 3}, appmod.JOB_BUDGET)["maxToolRounds"] == 3, "a stricter setting wins"  # noqa: SLF001
    assert appmod._caps({"maxRunSeconds": 0}, appmod.JOB_BUDGET)["maxRunSeconds"] == 240, "0 means unlimited: capped"  # noqa: SLF001
    system = next(d for _, e, d in store.events(run["run_id"]) if e == "assistant_message")["context_used"]["system_prompt"]
    assert "scheduled background run" in system and "recorded as a proposal" in system


# ---------------- accepting a proposal ----------------
def _one_proposal(tool: str, args: dict[str, Any], prompt: str = "do the thing") -> dict[str, Any]:
    ROUNDS.append({"tool_calls": [call(tool, args)]})
    ROUNDS.append(["proposed"])
    job = make_job(f"prop {tool}", "0 * * * *", prompt, at=T0)
    tick(T0 + HOUR)
    return proposals.list("pending", run_id=job_runs(job["id"])[0]["run_id"])[0]


def test_accepting_a_proposal_executes_it_once_and_re_accepting_does_not_double_send() -> None:
    args = {"to": "a@example.com", "subject": "Hello", "body": "Body."}
    p = _one_proposal("gmail_send", args)
    res = j("POST", f"/proposals/{p['id']}/accept")
    assert res["ok"] is True and res["replayed"] is False
    assert [n for n, _ in SENT] == ["gmail_send"], "accepting is what sends it"
    assert SENT[0][1]["args"][:3] == ("a@example.com", "Hello", "Body.")
    row = proposals.get(p["id"])
    assert row["status"] == "accepted" and row["decided_at"] and row["result"]["ok"] is True and row["error"] is None

    j("POST", f"/proposals/{p['id']}/accept", expect=409)
    j("POST", f"/proposals/{p['id']}/reject", expect=409)
    assert len(SENT) == 1, "a second accept never reaches the tool"
    assert [x["id"] for x in proposals.list("pending")] == [x["id"] for x in proposals.list("pending") if x["id"] != p["id"]]
    j("POST", "/proposals/nope/accept", expect=404)


def test_a_proposal_can_be_edited_before_it_is_accepted_or_simply_rejected() -> None:
    p = _one_proposal("gmail_send", {"to": "a@example.com", "subject": "Draft", "body": "Too blunt."})
    edited = {"to": "a@example.com", "subject": "Draft", "body": "Warmer, and shorter."}
    res = j("POST", f"/proposals/{p['id']}/accept", {"args": edited})
    assert res["ok"] is True
    assert SENT[0][1]["args"][2] == "Warmer, and shorter.", "the user's text is what went out, not the model's"
    row = proposals.get(p["id"])
    assert row["edited"] is True and row["args"] == edited

    q = _one_proposal("calendar_create", {"summary": "Coffee", "start": "2026-10-01T10:00"})
    assert j("POST", f"/proposals/{q['id']}/reject")["proposal"]["status"] == "rejected"
    assert [n for n, _ in SENT] == ["gmail_send"], "a rejected proposal never runs"
    assert all(x["id"] != q["id"] for x in proposals.list("pending"))
    assert j("GET", "/proposals?status=rejected")[0]["id"] == q["id"]
    j("GET", "/proposals?status=sideways", expect=400)


# ---------------- the inbox ----------------
def test_the_inbox_is_built_from_journal_rows() -> None:
    args = {"to": "mira@example.com", "subject": "Re: plan", "body": "Yes."}
    ROUNDS.append({"tool_calls": [call("gmail_send", args)]})
    ROUNDS.append([""])  # the run writes no prose at all: the entry must still be complete
    job = make_job("inbox", "0 * * * *", "scan and reply", at=T0)
    tick(T0 + 3 * HOUR + 300)  # late by 5 minutes, two slots skipped
    run = job_runs(job["id"])[0]

    box = j("GET", "/inbox")
    entry = next(e for e in box["while_you_were_away"] if e["run_id"] == run["run_id"])
    assert entry["job"] == job["name"] and entry["job_id"] == job["id"] and entry["status"] == "done"
    assert entry["late"] is True and entry["missed_slots"] == 2 and entry["late_seconds"] == 300.0
    assert entry["due_at"] == T0 + 3 * HOUR and entry["conversation_id"] == run["conversation_id"]
    assert entry["tool_calls"] == 1 and entry["proposals"] == 1 and entry["pending_proposals"] == 1
    assert entry["summary"] == "", "no prose, and the entry is still complete: it is built from rows"
    assert entry["tool_calls"] == store.event_counts(run["run_id"])["tool_result"]

    pid = proposals.list("pending", run_id=run["run_id"])[0]["id"]
    assert any(p["id"] == pid for p in box["needs_you"]["proposals"])
    assert box["counts"]["needs_you"] == len(box["needs_you"]["approvals"]) + len(box["needs_you"]["proposals"])
    assert box["counts"]["late"] >= 1 and box["scheduler"]["next_due_at"] is not None

    # Change one row and only that number moves: the payload is a view over the tables, not a cached story.
    proposals.reject(pid)
    after = j("GET", "/inbox")
    moved = next(e for e in after["while_you_were_away"] if e["run_id"] == run["run_id"])
    assert moved["pending_proposals"] == 0 and moved["proposals"] == 1
    assert all(p["id"] != pid for p in after["needs_you"]["proposals"])
    assert after["counts"]["proposals"] == box["counts"]["proposals"] - 1

    assert j("GET", "/inbox?hours=0")["while_you_were_away"] == [], "the window is a WHERE on started_at"


def test_a_failed_job_shows_up_as_a_failure_and_a_pending_approval_needs_you() -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("the model is down")
        yield  # pragma: no cover - makes this an async generator

    ROUNDS.clear()
    llm.stream_chat = boom
    job = make_job("fails", "0 * * * *", at=T0)
    tick(T0 + HOUR)
    llm.stream_chat = _scripted
    run = job_runs(job["id"])[0]
    assert run["status"] == "error" and "the model is down" in (run["error"] or "")
    entry = next(e for e in j("GET", "/inbox")["while_you_were_away"] if e["run_id"] == run["run_id"])
    assert entry["status"] == "error" and "the model is down" in (entry["error"] or "")
    assert j("GET", "/inbox")["counts"]["failed"] >= 1

    # A chat run's approval still lands in "Needs you", labelled with the run it belongs to.
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"todo_add": "ask"}}})
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": f"inbox approval {time.time()}"})]})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "add a todo"})["run_id"]
    deadline = time.time() + 15
    while time.time() < deadline:
        hit = [a for a in j("GET", "/inbox")["needs_you"]["approvals"] if a["run_id"] == rid]
        if hit:
            break
        time.sleep(0.02)
    assert hit and hit[0]["run_kind"] == "chat" and hit[0]["tool"] == "todo_add" and hit[0]["live"] is True
    j("POST", f"/approvals/{hit[0]['call_id']}", {"decision": "deny"})
    wait_done(rid)
    assert all(a["call_id"] != hit[0]["call_id"] for a in j("GET", "/inbox")["needs_you"]["approvals"])
