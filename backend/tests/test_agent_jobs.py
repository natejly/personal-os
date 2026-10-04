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
from personal_os import verify  # noqa: E402
from personal_os.jobs import (  # noqa: E402
    LATE_GRACE_S, SEED_JOBS, Jobs, Scheduler, next_fire, parse_when, prev_fire, slots_between, spent, valid_cron,
    valid_schedule, valid_tz,
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
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
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
    # Nothing may be left holding in the outbox between tests, or one test's mail goes out under another.
    for row in appmod.outbox.list():
        if row["status"] == "holding":
            appmod.outbox.cancel(row["id"])
    for name in ("gmail_send", "gmail_draft", "calendar_create"):
        setattr(appmod.google, name, _recorder(name))
    yield
    ROUNDS.clear()


def _recorder(name: str):  # type: ignore[no-untyped-def]
    def fn(*args: Any, **kw: Any) -> dict[str, Any]:
        SENT.append((name, {"args": args, "kw": kw}))
        # A real write comes back with its read-back verdict attached (verify.py); an unproven one is
        # an error, not a result, so a stub that omits it would fail every write for the wrong reason.
        return {"id": f"{name}-{len(SENT)}", "ok": True, "sent": f"{name}-{len(SENT)}",
                "verified": True,
                "verification": {"status": verify.VERIFIED, "what": name, "attempts": 1, "compared": ["id"]}}
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


def test_an_mcp_tool_in_a_job_run_is_proposed_and_never_called() -> None:
    """Connectors are external but not in the built-in toolbox, so a job used to call them for real."""
    called: list[str] = []

    async def boom(slug: str, args: dict[str, Any]) -> dict[str, Any]:
        called.append(slug)
        return {"ok": True, "wrote": args}

    def tooling(project_id: str | None, conversation_id: str | None) -> tuple[dict[str, str], list[dict[str, Any]]]:
        return {"mcp__files__write": "on"}, []

    real_tooling, real_call = appmod._mcp_tooling, appmod._mcp_call
    appmod._mcp_tooling, appmod._mcp_call = tooling, boom
    try:
        args = {"path": "notes.md", "content": "hello"}
        ROUNDS.append({"tool_calls": [call("mcp__files__write", args)]})
        ROUNDS.append(["I left that for you to approve."])
        job = make_job("connector", "0 * * * *", "save the notes", at=T0)
        tick(T0 + HOUR)
    finally:
        appmod._mcp_tooling, appmod._mcp_call = real_tooling, real_call

    assert called == [], "the connector was contacted"
    run = job_runs(job["id"])[0]
    assert run["status"] == "done"
    mine = proposals.list("pending", run_id=run["run_id"])
    assert len(mine) == 1 and mine[0]["tool"] == "mcp__files__write" and mine[0]["args"] == args
    assert store.approvals(None, run_id=run["run_id"]) == []

    appmod._mcp_call = boom
    try:
        res = j("POST", f"/proposals/{mine[0]['id']}/accept")
    finally:
        appmod._mcp_call = real_call
    assert called == ["mcp__files__write"]
    assert res["ok"] is True and res["proposal"]["status"] == "accepted"


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


def _flush_outbox() -> None:
    """Accepting a send queues it behind the undo window (outbox.py); this is the user cutting that short."""
    for row in appmod.outbox.list():
        if row["status"] == "holding":  # list() also carries recently resolved rows, which cannot be sent again
            j("POST", f"/outbox/gmail/{row['id']}/send-now")


def test_accepting_a_proposal_executes_it_once_and_re_accepting_does_not_double_send() -> None:
    args = {"to": "a@example.com", "subject": "Hello", "body": "Body."}
    p = _one_proposal("gmail_send", args)
    res = j("POST", f"/proposals/{p['id']}/accept")
    assert res["ok"] is True and res["replayed"] is False
    assert SENT == [], "accepting queues the mail; the undo window is what sends it"
    _flush_outbox()
    assert [n for n, _ in SENT] == ["gmail_send"], "accepting is what puts it on its way"
    assert SENT[0][1]["args"][:3] == ("a@example.com", "Hello", "Body.")
    row = proposals.get(p["id"])
    assert row["status"] == "accepted" and row["decided_at"] and row["error"] is None
    assert row["result"]["queued"] and row["result"]["status"] == "holding", \
        "the model is told it is held, never that it is sent"

    j("POST", f"/proposals/{p['id']}/accept", expect=409)
    j("POST", f"/proposals/{p['id']}/reject", expect=409)
    assert len(SENT) == 1, "a second accept never reaches the tool"
    assert [x["id"] for x in proposals.list("pending")] == [x["id"] for x in proposals.list("pending") if x["id"] != p["id"]]
    j("POST", "/proposals/nope/accept", expect=404)


def test_an_accept_that_fails_before_writing_goes_back_to_pending() -> None:
    p = _one_proposal("calendar_create", {"summary": "Standup", "start": "2026-10-01T09:00"})

    def expired(*args: Any, **kw: Any) -> Any:
        raise RuntimeError("token expired")

    appmod.google.calendar_create = expired
    res = j("POST", f"/proposals/{p['id']}/accept")
    assert res["ok"] is False and res["proposal"]["status"] == "pending", "nothing was written, so it can be accepted again"
    assert any(x["id"] == p["id"] for x in proposals.list("pending"))
    appmod.google.calendar_create = _recorder("calendar_create")
    res = j("POST", f"/proposals/{p['id']}/accept")
    assert res["ok"] is True and res["proposal"]["status"] == "accepted"
    assert [n for n, _ in SENT] == ["calendar_create"]


def test_a_proposal_can_be_edited_before_it_is_accepted_or_simply_rejected() -> None:
    p = _one_proposal("gmail_send", {"to": "a@example.com", "subject": "Draft", "body": "Too blunt."})
    edited = {"to": "a@example.com", "subject": "Draft", "body": "Warmer, and shorter."}
    res = j("POST", f"/proposals/{p['id']}/accept", {"args": edited})
    assert res["ok"] is True
    _flush_outbox()
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
    ROUNDS.append([""])  # ...even after the one nudge an empty round gets; it did tool work, so this is not an error
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


def test_inbox_runs_carry_a_read_state_the_user_can_clear() -> None:
    j("POST", "/inbox/seen_all")  # earlier tests' runs are in the same window
    job = make_job("readstate", "0 * * * *", at=T0)
    tick(T0 + HOUR)
    rid = job_runs(job["id"])[0]["run_id"]

    box = j("GET", "/inbox")
    entry = next(e for e in box["while_you_were_away"] if e["run_id"] == rid)
    assert entry["seen"] is False and box["counts"]["unseen_runs"] == 1

    assert j("POST", f"/inbox/runs/{rid}/seen")["ok"] is True
    box = j("GET", "/inbox")
    assert next(e for e in box["while_you_were_away"] if e["run_id"] == rid)["seen"] is True
    assert box["counts"]["unseen_runs"] == 0
    j("POST", f"/inbox/runs/{rid}/seen")  # twice is fine

    j("POST", "/inbox/runs/no-such-run/seen", expect=404)
    chat = next((r for r in store.list(None, limit=500) if r["kind"] != "job"), None)
    if chat:
        j("POST", f"/inbox/runs/{chat['run_id']}/seen", expect=404)

    tick(T0 + 2 * HOUR)
    tick(T0 + 3 * HOUR)
    assert j("GET", "/inbox")["counts"]["unseen_runs"] == 2
    assert j("POST", "/inbox/seen_all")["marked"] >= 3
    box = j("GET", "/inbox")
    assert box["counts"]["unseen_runs"] == 0 and all(e["seen"] for e in box["while_you_were_away"])


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


# ---------------- one-off tasks ----------------
def once_job(name: str, run_at: float, prompt: str = "report", *, at: float, enabled: bool = True) -> dict[str, Any]:
    return jobs.create(f"{name} {time.time()}", "", prompt, kind="once", run_at=run_at, timezone="UTC",
                       enabled=enabled, at=at)


def tool(name: str, args: dict[str, Any], *, proposal_only: bool = False) -> Any:
    ctx = {"project_id": None, "conversation_id": None, "settings": appmod.settings(),
           "tainted": False, "taint_sources": [], "allowed_urls": set(), "proposal_only": proposal_only}
    return asyncio.run(appmod.toolbox.call(name, args, ctx))


def iso_in(seconds: float) -> str:
    """A local ISO-8601 stamp that far from the real clock. The HTTP and tool layers refuse backdated times, so
    these tests cannot use T0 for them the way the scheduler tests do."""
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + seconds))


def test_parse_when_reads_an_iso_instant_and_refuses_anything_vague() -> None:
    NOON_UTC = 1790866800.0  # 2026-10-01 15:00:00 UTC
    assert parse_when("2026-10-01T15:00", "UTC") == NOON_UTC
    assert parse_when("2026-10-01 15:00", "UTC") == NOON_UTC, "a space instead of a T is what people type"
    assert parse_when("2026-10-01T15:00:30", "UTC") == NOON_UTC + 30
    # No offset means "in this timezone", so the same wall clock in two zones is two instants.
    assert parse_when("2026-10-01T15:00", "Europe/Berlin") == NOON_UTC - 2 * HOUR
    assert parse_when("2026-10-01T15:00+02:00", "UTC") == NOON_UTC - 2 * HOUR, "an explicit offset wins"
    assert parse_when("2026-10-01", "UTC") is None, "a bare date is not a time of day"
    assert parse_when("next Tuesday", "UTC") is None and parse_when("", "UTC") is None
    assert valid_schedule("once", None, NOON_UTC) and not valid_schedule("once", "0 * * * *", None)
    assert valid_schedule("cron", "0 * * * *", None) and not valid_schedule("cron", "nope", None)


def test_a_one_off_fires_at_its_instant_and_then_switches_itself_off() -> None:
    ROUNDS.append(["done that"])
    job = once_job("one-off", T0 + HOUR, at=T0)
    assert job["kind"] == "once" and job["cron"] == "" and job["next_due_at"] == T0 + HOUR

    assert tick(T0 + HOUR - 60) == [], "not due yet"
    fired = tick(T0 + HOUR)
    assert len(fired) == 1 and fired[0]["due_at"] == T0 + HOUR and fired[0]["missed_slots"] == 0
    assert fired[0]["late"] is False and fired[0]["kind"] == "once"

    row = jobs.get(job["id"])
    assert row["enabled"] is False, "a one-off retires instead of sitting enabled with nothing to wait for"
    assert row["next_due_at"] is None and row["last_due_at"] == T0 + HOUR and row["last_fired_at"] == T0 + HOUR
    assert spent(row) is True

    assert tick(T0 + 2 * HOUR) == [] and tick(T0 + 40 * HOUR) == [], "once means once"
    runs = job_runs(job["id"])
    assert len(runs) == 1 and runs[0]["kind"] == "job" and runs[0]["input"]["kind"] == "once"

    entry = next(e for e in j("GET", "/inbox")["while_you_were_away"] if e["job_id"] == job["id"])
    assert entry["kind"] == "once" and entry["late"] is False


def test_a_one_off_missed_while_the_app_was_closed_still_runs_and_is_told_it_is_late() -> None:
    ROUNDS.append(["sorry, late"])
    job = once_job("missed one-off", T0 + HOUR, "check the invoice", at=T0)
    fired = tick(T0 + 9 * HOUR)  # the lid was shut over its instant
    assert len(fired) == 1
    assert fired[0]["due_at"] == T0 + HOUR, "it runs for the instant it was set to, not for now"
    assert fired[0]["late"] is True and fired[0]["late_seconds"] == 8 * HOUR
    assert fired[0]["missed_slots"] == 0, "a one-off has no other slots to skip"

    run = job_runs(job["id"])[0]
    convo = j("GET", f"/conversations/{run['conversation_id']}")
    first = next(m for m in convo["messages"] if m["role"] == "user")
    assert "only starting now" in first["content"] and "check the invoice" in first["content"]
    assert "earlier run" not in first["content"], "nothing was skipped, so it must not claim any were"
    assert jobs.get(job["id"])["next_due_at"] is None


def test_a_spent_one_off_is_never_re_armed_and_is_rescheduled_by_moving_its_time() -> None:
    ROUNDS.append(["first"])
    job = once_job("spent", T0 + HOUR, at=T0)
    tick(T0 + HOUR)
    assert spent(jobs.get(job["id"]))

    back_on = jobs.update(job["id"], {"enabled": True}, at=T0 + 2 * HOUR)
    assert back_on["next_due_at"] is None, "there is no instant left to wait for"
    assert jobs.arm(T0 + 2 * HOUR) >= 0 and jobs.get(job["id"])["next_due_at"] is None, "arm() does not revive it"
    assert tick(T0 + 3 * HOUR) == [], "switching a fired task on does not quietly run it a second time"
    assert "already ran" in j("PATCH", f"/jobs/{job['id']}", {"enabled": True}, expect=400)["detail"]

    ROUNDS.append(["again, on purpose"])
    moved = jobs.update(job["id"], {"run_at": T0 + 10 * HOUR, "enabled": True}, at=T0 + 4 * HOUR)
    assert moved["next_due_at"] == T0 + 10 * HOUR and spent(moved) is False
    assert len(tick(T0 + 10 * HOUR)) == 1, "a new time is a new task"
    assert len(job_runs(job["id"])) == 2


def test_editing_a_job_over_the_api_re_arms_it_and_a_spent_one_off_runs_again_at_a_new_time() -> None:
    made = j("POST", "/jobs", {"name": "edit me", "cron": "0 9 * * *", "prompt": "old", "timezone": "UTC", "enabled": True})
    edited = j("PATCH", f"/jobs/{made['id']}", {"prompt": "new", "cron": "30 7 * * 1-5", "timezone": "America/New_York",
                                                 "max_retries": 3, "name": "edited"})
    assert (edited["prompt"], edited["name"], edited["max_retries"]) == ("new", "edited", 3)
    assert edited["next_due_at"] == next_fire("30 7 * * 1-5", "America/New_York", edited["updated_at"])
    assert edited["next_due_at"] != made["next_due_at"]
    assert j("DELETE", f"/jobs/{made['id']}") == {"ok": True}

    ROUNDS.append(["first"])
    once = once_job("spent api", T0 + HOUR, at=T0)
    tick(T0 + HOUR)
    assert "already ran" in j("PATCH", f"/jobs/{once['id']}", {"enabled": True}, expect=400)["detail"]
    later = time.time() + 2 * HOUR
    again = j("PATCH", f"/jobs/{once['id']}", {"run_at": later, "enabled": True})
    assert again["enabled"] is True and again["next_due_at"] == later and spent(again) is False


def test_the_schedule_preview_lists_the_next_fires_in_the_zone_and_writes_nothing() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    before = len(j("GET", "/jobs"))
    r = j("GET", "/jobs/preview?cron=30%207%20*%20*%201-5&timezone=America/New_York&n=5")
    assert r["ok"] is True and len(r["next"]) == 5 and r["next"] == sorted(set(r["next"]))
    for ts in r["next"]:
        d = datetime.fromtimestamp(ts, ZoneInfo("America/New_York"))
        assert (d.hour, d.minute) == (7, 30) and d.weekday() < 5, d
    assert r["next"][0] > time.time()
    # Across the fall-back day an ambiguous wall time fires once, not twice.
    fall_back = datetime(2026, 11, 1, 0, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    t, fires = fall_back, []
    for _ in range(3):
        t = next_fire("30 1 * * *", "America/New_York", t)
        fires.append(t)
    assert len({datetime.fromtimestamp(x, ZoneInfo("America/New_York")).date() for x in fires}) == 3
    assert j("GET", "/jobs/preview?cron=*%20*%20*%20*%20*&n=500")["next"].__len__() == 10, "n is capped"

    bad = j("GET", "/jobs/preview?cron=every%20morning")
    assert bad["ok"] is False and "not a cron expression" in bad["error"] and bad["next"] == []
    assert j("GET", "/jobs/preview?cron=0%209%20*%20*%20*&timezone=Mars/Olympus")["ok"] is False
    assert len(j("GET", "/jobs")) == before


def test_a_one_off_with_no_time_at_all_is_disarmed_rather_than_spun_on() -> None:
    job = once_job("broken", T0 + HOUR, at=T0)
    with appmod.db.tx() as c:  # a row hand-edited into nonsense
        c.execute("UPDATE jobs SET run_at=NULL, next_due_at=? WHERE id=?", (T0, job["id"]))
    assert tick(T0 + HOUR) == []
    row = jobs.get(job["id"])
    assert row["next_due_at"] is None and row["enabled"] is False and "no time to run at" in (row["last_error"] or "")


def test_the_one_off_api_round_trips_and_refuses_a_time_that_has_passed() -> None:
    soon = time.time() + 3 * HOUR
    made = j("POST", "/jobs", {"name": "API one-off", "kind": "once", "run_at": soon, "prompt": "do it once"})
    assert made["kind"] == "once" and made["cron"] == "" and made["run_at"] == soon
    assert made["enabled"] is False and made["next_due_at"] is None
    on = j("PATCH", f"/jobs/{made['id']}", {"enabled": True})
    assert on["next_due_at"] == soon, "a one-off is armed to its instant, not to the next cron slot"

    j("POST", "/jobs", {"name": "no time", "kind": "once", "prompt": "x"}, expect=400)
    j("POST", "/jobs", {"name": "yesterday", "kind": "once", "run_at": time.time() - HOUR, "prompt": "x"}, expect=400)
    j("POST", "/jobs", {"name": "no cron", "kind": "cron", "prompt": "x"}, expect=400)
    j("POST", "/jobs", {"name": "nonsense kind", "kind": "sometimes", "run_at": soon, "prompt": "x"}, expect=400)
    j("PATCH", f"/jobs/{made['id']}", {"run_at": time.time() - HOUR}, expect=400)
    # Switching kind without supplying the other half of the schedule is a 400, not a crash while arming.
    assert "needs a cron" in j("PATCH", f"/jobs/{made['id']}", {"kind": "cron"}, expect=400)["detail"]
    cron_job = j("POST", "/jobs", {"name": "repeating", "cron": "0 9 * * *", "prompt": "x"})
    assert "needs run_at" in j("PATCH", f"/jobs/{cron_job['id']}", {"kind": "once"}, expect=400)["detail"]
    swapped = j("PATCH", f"/jobs/{cron_job['id']}", {"kind": "once", "run_at": soon, "enabled": True})
    assert swapped["kind"] == "once" and swapped["cron"] == "" and swapped["next_due_at"] == soon
    assert j("DELETE", f"/jobs/{cron_job['id']}") == {"ok": True}
    assert j("DELETE", f"/jobs/{made['id']}") == {"ok": True}


# ---------------- the assistant schedules its own follow-up work ----------------
def test_schedule_task_books_a_one_off_and_a_repeating_job() -> None:
    out = tool("schedule_task", {"name": "Chase the invoice", "prompt": "Check whether Acme replied.",
                                 "when": iso_in(3 * HOUR)})
    assert out.get("scheduled") is True and out["repeats"] is False and out["enabled"] is True
    row = jobs.get(out["id"])
    assert row["kind"] == "once" and row["prompt"] == "Check whether Acme replied."
    assert abs(row["run_at"] - (time.time() + 3 * HOUR)) < 120

    rel = tool("schedule_task", {"name": "Check the build", "prompt": "Did the deploy finish?", "in_minutes": 45})
    assert abs(jobs.get(rel["id"])["run_at"] - (time.time() + 45 * 60)) < 5

    rep = tool("schedule_task", {"name": "Weekly review", "prompt": "Write my weekly review.", "cron": "0 17 * * 5"})
    assert rep["repeats"] is True and rep["schedule"] == "0 17 * * 5"
    assert jobs.get(rep["id"])["kind"] == "cron" and rep["next_run"]

    for bad, field in (({"name": "x", "prompt": "y", "when": "next Tuesday"}, "when"),
                       ({"name": "x", "prompt": "y", "when": "2026-10-01"}, "when"),
                       ({"name": "x", "prompt": "y", "when": "2020-01-01T09:00"}, "when"),
                       ({"name": "x", "prompt": "y", "cron": "every morning"}, "cron"),
                       ({"name": " ", "prompt": "y", "when": iso_in(HOUR)}, "name"),
                       ({"name": "x", "prompt": " ", "when": iso_in(HOUR)}, "prompt")):
        out = tool("schedule_task", bad)
        assert "error" in out and out.get("field") == field, (bad, out)
    both = tool("schedule_task", {"name": "x", "prompt": "y", "when": iso_in(HOUR), "cron": "0 9 * * *"})
    assert "error" in both and "one schedule" in both["error"]


def test_listing_and_cancelling_scheduled_tasks() -> None:
    mine = tool("schedule_task", {"name": "Cancel me", "prompt": "nothing", "when": iso_in(5 * HOUR)})
    listed = tool("scheduled_tasks", {})
    assert [t["id"] for t in listed["tasks"]] == [mine["id"]], "only what is switched on"
    assert listed["count"] == 1 and listed["tasks"][0]["next_run"]

    off = tool("cancel_scheduled_task", {"id": mine["id"]})
    assert off["cancelled"] is True and off["enabled"] is False
    assert jobs.get(mine["id"])["next_due_at"] is None, "cancelling disarms it"
    assert tool("scheduled_tasks", {})["tasks"] == []
    assert any(t["id"] == mine["id"] for t in tool("scheduled_tasks", {"include_off": True})["tasks"])

    again = tool("cancel_scheduled_task", {"id": mine["id"]})
    assert again["cancelled"] is False and "already switched off" in again["note"]
    assert "error" in tool("cancel_scheduled_task", {"id": "nope"})
    assert jobs.get(mine["id"]) is not None, "cancelling keeps the row; only the user deletes it"


def test_a_scheduled_run_cannot_schedule_more_work_and_proposes_instead() -> None:
    """The anti-loop rule: a job that could create jobs is an agent that keeps itself alive."""
    refused = tool("schedule_task", {"name": "loop", "prompt": "do it again", "in_minutes": 5}, proposal_only=True)
    assert "error" in refused and "loop" in refused["error"]
    assert not any(x["name"] == "loop" for x in jobs.list()), "nothing was booked"
    assert "error" in tool("cancel_scheduled_task", {"id": "whatever"}, proposal_only=True)
    assert "error" not in tool("scheduled_tasks", {}, proposal_only=True), "reading what is scheduled is fine"

    # End to end: the call from a real job run becomes a proposal, and accepting it is what schedules the task.
    args = {"name": f"Follow up {time.time()}", "prompt": "Check whether they replied.", "in_minutes": 90}
    ROUNDS.append({"tool_calls": [call("schedule_task", args)]})
    ROUNDS.append(["I proposed a follow-up."])
    job = make_job("scheduler", "0 * * * *", "chase this up later", at=T0)
    tick(T0 + HOUR)
    run = job_runs(job["id"])[0]
    assert run["status"] == "done"
    assert not any(x["name"] == args["name"] for x in jobs.list()), "a background run books nothing by itself"

    pending = [p for p in proposals.list("pending", run_id=run["run_id"]) if p["tool"] == "schedule_task"]
    assert len(pending) == 1
    res = j("POST", f"/proposals/{pending[0]['id']}/accept")
    assert res["ok"] is True
    booked = next(x for x in jobs.list() if x["name"] == args["name"])
    assert booked["kind"] == "once" and booked["enabled"] is True and booked["next_due_at"] is not None
    j("POST", f"/proposals/{pending[0]['id']}/accept", expect=409)


# ---------------- per-job model and budget ----------------
async def _listing(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": "cheap-model"}, {"id": "embedder", "mode": "embedding"}]


def _run_by_hand(job_id: str) -> dict[str, Any]:
    ROUNDS.append(["done"])
    return wait_done(j("POST", f"/jobs/{job_id}/run")["run_id"])


def test_a_job_runs_on_its_own_model_and_an_unknown_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "list_models", _listing)
    base = {"name": "Triage", "cron": "0 6 * * *", "prompt": "triage", "timezone": "UTC"}
    j("POST", "/jobs", {**base, "model": "no-such-model"}, expect=422)
    j("POST", "/jobs", {**base, "model": "embedder"}, expect=422)
    made = j("POST", "/jobs", {**base, "model": "cheap-model"})
    assert made["model"] == "cheap-model"
    row = _run_by_hand(made["id"])
    assert j("GET", f"/conversations/{row['conversation_id']}")["model"] == "cheap-model"

    j("PATCH", f"/jobs/{made['id']}", {"model": "nope"}, expect=422)
    assert j("PATCH", f"/jobs/{made['id']}", {"model": None})["model"] is None, "null resets to the default"
    row = _run_by_hand(made["id"])
    assert j("GET", f"/conversations/{row['conversation_id']}")["model"] == (appmod.settings().get("defaultModel") or "")


def test_a_job_budget_only_tightens_the_job_caps() -> None:
    base = {"name": "Cheap", "cron": "0 6 * * *", "prompt": "x", "timezone": "UTC"}
    j("POST", "/jobs", {**base, "budget": {"maxRunCost": 5}}, expect=422)  # above JOB_BUDGET
    j("POST", "/jobs", {**base, "budget": {"maxToolRounds": 3}}, expect=422)  # the round cap is not the job's to set
    j("POST", "/jobs", {**base, "budget": {"maxRunCost": 0}}, expect=422)  # 0 would mean unlimited
    j("POST", "/jobs", {**base, "budget": {"maxRunSeconds": "60"}}, expect=422)
    made = j("POST", "/jobs", {**base, "budget": {"maxRunCost": 0.05, "maxRunSeconds": 60}})
    assert made["budget"] == {"maxRunCost": 0.05, "maxRunSeconds": 60}
    b = _run_by_hand(made["id"])["budget"]
    assert b["max_cost"] == 0.05 and b["max_seconds"] == 60
    assert b["max_tokens"] <= appmod.JOB_BUDGET["maxRunTokens"]
    j("PATCH", f"/jobs/{made['id']}", {"budget": {"maxRunCost": 1}}, expect=422)
    assert j("PATCH", f"/jobs/{made['id']}", {"budget": None})["budget"] is None

    # A row written behind the API's back still cannot loosen the job caps: the runner clamps again.
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET budget=? WHERE id=?",
                  (json.dumps({"maxRunCost": 50, "maxRunTokens": 0, "maxToolRounds": 99}), made["id"]))
    b = _run_by_hand(made["id"])["budget"]
    assert b["max_cost"] == appmod.JOB_BUDGET["maxRunCost"] and b["max_rounds"] <= appmod.JOB_BUDGET["maxToolRounds"]
    assert 0 < b["max_tokens"] <= appmod.JOB_BUDGET["maxRunTokens"]


def test_a_job_budget_never_loosens_the_users_own_stricter_setting() -> None:
    caps = appmod._job_caps({"maxRunCost": 0.01, "maxRunSeconds": 0}, {"maxRunCost": 0.1, "maxRunSeconds": 0})  # noqa: SLF001
    assert caps["maxRunCost"] == 0.01, "the user's stricter cap wins"
    assert caps["maxRunSeconds"] == appmod.JOB_BUDGET["maxRunSeconds"], "a 0 in the job budget is not 'unlimited'"


# ---------------- proposal hygiene ----------------
def _bare_proposal(job_id: str | None, *, at: float | None = None) -> dict[str, Any]:
    return proposals.create(run_id=None, tool="calendar_create", args={"summary": f"x {time.time()}"}, job_id=job_id, at=at)


def test_a_stale_proposal_expires_on_a_tick_and_cannot_be_accepted() -> None:
    now = time.time()
    old = _bare_proposal("jx", at=now - 8 * 86400)
    fresh = _bare_proposal("jx", at=now - 6 * 86400)
    sched = Scheduler(jobs, appmod._launch_job, clock=lambda: now, policy=appmod.job_policy)  # noqa: SLF001 - default 7 days
    tick(now, sched)
    assert proposals.get(old["id"])["status"] == "expired" and proposals.get(fresh["id"])["status"] == "pending"
    r = client.post(f"/proposals/{old['id']}/accept")
    assert r.status_code == 409 and "expired" in r.text
    assert SENT == [], "an expired proposal never runs"
    assert all(p["id"] != old["id"] for p in j("GET", "/inbox")["needs_you"]["proposals"])
    assert any(p["id"] == old["id"] for p in j("GET", "/proposals?status=expired"))
    proposals.reject(fresh["id"])


def test_deleting_a_job_rejects_its_pending_proposals() -> None:
    jb = make_job("doomed", "0 * * * *", at=T0, enabled=False)
    mine, other = _bare_proposal(jb["id"]), _bare_proposal("someone-else")
    assert any(p["id"] == mine["id"] and p["job"] == jb["name"] for p in j("GET", "/inbox")["needs_you"]["proposals"])
    j("DELETE", f"/jobs/{jb['id']}")
    row = proposals.get(mine["id"])
    assert row["status"] == "rejected" and row["error"] == "job deleted"
    assert proposals.get(other["id"])["status"] == "pending"
    proposals.reject(other["id"])


def test_reject_all_only_touches_that_jobs_pending_proposals() -> None:
    a1, a2, b = _bare_proposal("job-a"), _bare_proposal("job-a"), _bare_proposal("job-b")
    done = _bare_proposal("job-a")
    proposals.reject(done["id"], at=1.0)
    assert j("POST", "/proposals/reject_all?job_id=job-a")["rejected"] == 2
    assert proposals.get(a1["id"])["status"] == proposals.get(a2["id"])["status"] == "rejected"
    assert proposals.get(b["id"])["status"] == "pending" and proposals.get(done["id"])["decided_at"] == 1.0
    proposals.reject(b["id"])


def test_accepting_a_send_says_it_is_queued_not_sent() -> None:
    p = _one_proposal("gmail_send", {"to": "a@example.com", "subject": "Hi", "body": "B."})
    res = j("POST", f"/proposals/{p['id']}/accept")
    assert res["ok"] is True and res["queued"] is True and res["sends_in_seconds"] > 0
    assert SENT == [] and any(r["status"] == "holding" for r in appmod.outbox.list())
    q = _one_proposal("calendar_create", {"summary": "Standup", "start": "2026-10-01T09:00"})
    res = j("POST", f"/proposals/{q['id']}/accept")
    assert res["ok"] is True and res["queued"] is False and res["sends_in_seconds"] is None
