"""Jobs: an OS wake for the next slot, one boot retry of a killed run, and a directory trigger.

Offline: fake clock, fake wake sink (never the OS), scripted model, Google is a recorder.

Run: backend/.venv/bin/python -m pytest backend/tests/test_jobs_wake_retry_dir.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="jobswrd-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import Jobs, Scheduler  # noqa: E402
from personal_os.jobs_policy import JobPolicy  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
jobs = appmod.jobs
proposals = appmod.proposals
HOUR = 3600.0
T0 = 1772409600.0
ROUNDS: list[Any] = []
SENT: list[str] = []


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto",  # type: ignore[no-untyped-def]
                    fast=False, cancel=None):
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
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET enabled=0, next_due_at=NULL")
    for row in appmod.outbox.list():
        if row["status"] == "holding":
            appmod.outbox.cancel(row["id"])

    def send(*a: Any, **kw: Any) -> dict[str, Any]:
        SENT.append("gmail_send")
        return {"id": "m1", "ok": True, "sent": "m1", "verified": True,
                "verification": {"status": verify.VERIFIED, "what": "gmail_send", "attempts": 1, "compared": ["id"]}}
    appmod.google.gmail_send = send
    yield
    ROUNDS.clear()


class FakeWake:
    def __init__(self) -> None:
        self.booked: list[float] = []
        self.calls: list[tuple[str, float]] = []

    def schedule(self, at: float) -> None:
        self.calls.append(("schedule", at))
        self.booked.append(at)

    def cancel(self, at: float) -> None:
        self.calls.append(("cancel", at))
        self.booked.remove(at)


def call(name: str, args: dict[str, Any], cid: str = "c0") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def tick(at: float, wake: Any = None) -> list[dict[str, Any]]:
    # One scheduler per wake sink, as in the app: it is what remembers which wake it booked.
    s = getattr(wake, "sched", None) or Scheduler(jobs, appmod._launch_job, wake=wake)  # noqa: SLF001
    s.clock = lambda: at
    if wake is not None:
        wake.sched = s

    async def go() -> list[dict[str, Any]]:
        fired = await s.tick()
        for f in fired:
            run = next((r for r in appmod.bus._runs.values() if r.run_id == f["run_id"]), None)  # noqa: SLF001
            if run is not None and run.task is not None:
                with contextlib.suppress(Exception):
                    await run.task
        return fired

    return asyncio.run(go())


def job_runs(job_id: str) -> list[dict[str, Any]]:
    return [r for r in store.of_kind("job", limit=500) if (r["input"] or {}).get("job_id") == job_id]


def two_proposal_rounds() -> dict[str, Any]:
    args = {"name": f"Follow up {time.time()}", "prompt": "Check again.", "in_minutes": 90}
    ROUNDS.append({"tool_calls": [call("gmail_send", {"to": "a@example.com", "subject": "S", "body": "B"}, "a"),
                                  call("schedule_task", args, "b")]})
    ROUNDS.append(["proposed both"])
    return args


def check_two_proposals(run_id: str, args: dict[str, Any]) -> None:
    assert SENT == [] and not any(x["name"] == args["name"] for x in jobs.list()), "neither tool body ran"
    mine = proposals.list("pending", run_id=run_id)
    assert sorted(p["tool"] for p in mine) == ["gmail_send", "schedule_task"]
    sched = next(p for p in mine if p["tool"] == "schedule_task")
    assert client.post(f"/proposals/{sched['id']}/accept").status_code == 200
    assert sum(x["name"] == args["name"] for x in jobs.list()) == 1, "accept ran it once"
    assert client.post(f"/proposals/{sched['id']}/accept").status_code == 409
    assert sum(x["name"] == args["name"] for x in jobs.list()) == 1, "a second accept did not"


@pytest.fixture()
def home_dir():  # type: ignore[no-untyped-def]
    d = tempfile.mkdtemp(prefix="grain-watch-", dir=os.path.expanduser("~"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------- wake ----------------
def test_one_wake_for_the_next_slot_cleared_on_disable_and_never_doubled() -> None:
    w = FakeWake()
    s = Scheduler(jobs, appmod._launch_job, clock=lambda: T0, wake=w)  # noqa: SLF001
    job = jobs.create("wake", "", "p", kind="once", run_at=T0 + 120, enabled=True, at=T0, timezone="UTC")
    s.sync_wake()
    assert w.booked == [jobs.get(job["id"])["next_due_at"]] == [T0 + 120]
    s.sync_wake()
    s.sync_wake()
    assert w.calls == [("schedule", T0 + 120)], "re-arming the same instant adds no second wake"
    jobs.update(job["id"], {"enabled": False}, at=T0)
    s.sync_wake()
    assert w.booked == [] and w.calls[-1] == ("cancel", T0 + 120)


def test_tick_past_the_slot_launches_once_and_its_calls_are_proposals() -> None:
    args = two_proposal_rounds()
    job = jobs.create("once", "", "do it", kind="once", run_at=T0 + 120, enabled=True, at=T0, timezone="UTC")
    w = FakeWake()
    assert tick(T0 + 60, w) == []
    assert len(tick(T0 + 130, w)) == 1
    assert tick(T0 + 140, w) == [], "a second tick does not launch again"
    runs = job_runs(job["id"])
    assert len(runs) == 1
    check_two_proposals(runs[0]["run_id"], args)
    assert w.booked == [], "a spent one-off leaves no wake behind"


# ---------------- boot retry ----------------
class Rig:
    def __init__(self) -> None:
        self.db = Database(tempfile.mkdtemp(prefix="wrd-"))
        self.jobs = Jobs(self.db)
        self.store = RunStore(self.db)
        self.launched: list[dict[str, Any]] = []
        self.policy = JobPolicy(self.jobs, self.store, self.launch, clock=lambda: T0, settings=dict)

    async def launch(self, job: dict[str, Any], fire: dict[str, Any]) -> str:
        rid = f"new{len(self.launched)}"
        self.launched.append(fire)
        self.store.create(rid, None, "job", {**fire})
        return rid

    def stored(self, job: dict[str, Any], rid: str, status: str, attempt: int | None) -> None:
        inp = {"job_id": job["id"], "job": job["name"], "due_at": T0}
        if attempt is not None:
            inp["attempt"] = attempt
        self.store.create(rid, None, "job", inp)
        self.store.update(rid, status=status, ended_at=T0, error="Interrupted")

    def boot(self) -> list[str]:
        async def go() -> list[str]:
            out = await self.policy.boot_retry()
            for t in list(self.policy._tasks):  # noqa: SLF001
                t.cancel()
            return out
        return asyncio.run(go())


def test_boot_retries_an_interrupted_run_once_and_never_a_stopped_or_spent_one() -> None:
    r = Rig()
    job = r.jobs.create("j", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0, max_retries=1)
    r.stored(job, "old", "interrupted", 0)
    assert len(r.boot()) == 1
    assert r.launched[0]["attempt"] == 2 and r.launched[0]["retry_of"] == "old"
    assert r.boot() == [], "a second boot sees the retry as the latest run, so it does not retry again"

    spent = r.jobs.create("spent", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0, max_retries=1)
    r.stored(spent, "s1", "interrupted", 1)
    stopped = r.jobs.create("stopped", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0, max_retries=1)
    r.stored(stopped, "u1", "done", 0)
    n = len(r.launched)
    assert r.boot() == [] and len(r.launched) == n


def test_the_boot_retry_is_proposal_only() -> None:
    args = two_proposal_rounds()
    job = jobs.create("retry me", "0 * * * *", "do it", timezone="UTC", enabled=True, at=T0, max_retries=1)
    store.create("dead-run", None, "job", {"job_id": job["id"], "job": job["name"], "kind": "cron", "cron": job["cron"],
                                           "timezone": "UTC", "due_at": T0, "fired_at": T0, "late_seconds": 0.0,
                                           "missed_slots": 0, "late": False})
    store.update("dead-run", status="interrupted", ended_at=T0, error="Interrupted")

    async def go() -> list[str]:
        ids = await appmod.job_policy.boot_retry()
        for rid in ids:
            run = next(r for r in appmod.bus._runs.values() if r.run_id == rid)  # noqa: SLF001
            await run.task
        await appmod.job_policy.drain()
        return ids

    ids = asyncio.run(go())
    assert len(ids) == 1
    row = store.get(ids[0])
    assert row["kind"] == "job" and row["input"]["retry_of"] == "dead-run" and row["input"]["attempt"] == 2
    check_two_proposals(ids[0], args)


# ---------------- directory trigger ----------------
def watch_job(d: str, cron: str = "", **kw: Any) -> dict[str, Any]:
    return jobs.create(f"watch {time.time()}", cron, "new files", kind="watch", watch_dir=d, timezone="UTC",
                       enabled=True, at=T0, **kw)


def test_directory_job_is_idle_until_a_file_appears_then_one_launch_per_burst(home_dir: str) -> None:
    job = watch_job(home_dir)
    assert tick(T0 + 1) == [] and tick(T0 + 2) == [], "no files, no launch"
    (Path(home_dir) / "a.txt").write_text("1")
    fired = tick(T0 + 3)
    assert len(fired) == 1 and fired[0]["trigger"] == "dir" and fired[0]["collapsed"] == 1
    assert job_runs(job["id"])[0]["kind"] == "job"
    assert tick(T0 + 4) == [], "the same file does not fire twice"
    for i in range(5):
        (Path(home_dir) / f"b{i}.txt").write_text("x")
    fired = tick(T0 + 5)
    assert len(fired) == 1 and fired[0]["collapsed"] > 1, "five files collapse into one launch"
    assert len(job_runs(job["id"])) == 2


def test_directory_run_proposes_its_external_calls(home_dir: str) -> None:
    args = two_proposal_rounds()
    job = watch_job(home_dir)
    (Path(home_dir) / "x.txt").write_text("1")
    assert len(tick(T0 + 1)) == 1
    check_two_proposals(job_runs(job["id"])[0]["run_id"], args)


def test_the_allowlist_still_cannot_enable_an_off_tool(home_dir: str) -> None:
    client.put("/settings", json={"tools": {"current_time": "off"}})
    try:
        ROUNDS.append({"tool_calls": [call("current_time", {})]})
        ROUNDS.append(["done"])
        job = watch_job(home_dir, allowed_tools=["current_time"])
        (Path(home_dir) / "x.txt").write_text("1")
        assert len(tick(T0 + 1)) == 1
        res = [d for _, e, d in store.events(job_runs(job["id"])[0]["run_id"]) if e == "tool_result"]
        assert res and res[0]["error"]
    finally:
        client.put("/settings", json={"tools": {}})


def test_clock_and_directory_on_one_job_launch_once_per_tick(home_dir: str) -> None:
    job = watch_job(home_dir, cron="0 * * * *")
    (Path(home_dir) / "x.txt").write_text("1")
    fired = tick(T0 + HOUR + 10)
    assert len(fired) == 1 and fired[0]["trigger"] == "clock+dir"
    assert tick(T0 + HOUR + 20) == []
    assert len(job_runs(job["id"])) == 1


def test_a_directory_outside_home_or_hidden_is_refused() -> None:
    for bad in ("/etc", os.path.join(os.path.expanduser("~"), ".ssh")):
        r = client.post("/jobs", json={"name": "w", "prompt": "p", "kind": "watch", "watch_dir": bad})
        assert r.status_code == 400, bad


def test_nudge_cuts_the_nap_short() -> None:
    s = Scheduler(jobs, appmod._launch_job)  # noqa: SLF001 - real asyncio nap, no injected sleep

    async def go() -> float:
        start = time.monotonic()
        nap = asyncio.create_task(s._nap(60.0))  # noqa: SLF001
        await asyncio.sleep(0.05)
        s.nudge()
        await asyncio.wait_for(nap, 2.0)
        return time.monotonic() - start

    assert asyncio.run(go()) < 2.0
    assert not s._poke.is_set(), "the poke is spent by the nap it ended"  # noqa: SLF001


def test_a_refused_power_wake_marks_it_unavailable_once() -> None:
    from personal_os.jobs import PowerWake

    calls: list[list[str]] = []

    class Done:
        returncode = 1

    def runner(argv: list[str], **kw: Any) -> Done:
        calls.append(argv)
        return Done()

    w = PowerWake(runner)
    s = Scheduler(jobs, appmod._launch_job, clock=lambda: T0, wake=w)  # noqa: SLF001
    assert s.wake_unavailable is False
    w.schedule(T0 + 60)
    assert w.unavailable and s.wake_unavailable and len(calls) == 1
    w.schedule(T0 + 120)
    w.cancel(T0 + 60)
    assert len(calls) == 1, "nothing is asked again after the first refusal"

    def missing(argv: list[str], **kw: Any) -> Done:
        raise FileNotFoundError("no such tool")

    w2 = PowerWake(missing)
    w2.schedule(T0)
    assert w2.unavailable


def test_wake_route_nudges_and_inbox_reports_wake_status() -> None:
    poked: list[bool] = []
    real = appmod.scheduler.nudge
    appmod.scheduler.nudge = lambda: poked.append(True)  # type: ignore[method-assign]
    try:
        assert client.post("/jobs/wake").json() == {"ok": True}
    finally:
        appmod.scheduler.nudge = real  # type: ignore[method-assign]
    assert poked == [True]
    assert "wake_unavailable" in client.get("/inbox").json()["scheduler"]
