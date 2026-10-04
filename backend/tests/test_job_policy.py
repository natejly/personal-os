"""Job run policy: the overlap guard, retry with persisted attempts, and the failure-streak auto-pause.

Offline: the clock and sleep are injected, the launcher is a stub that books synthetic run rows, and the
test flips those rows to done/error by hand.

Run: backend/.venv/bin/python -m pytest backend/tests/test_job_policy.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="jobpolicy-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import Jobs, Scheduler  # noqa: E402
from personal_os.jobs_policy import JobPolicy  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402

T0 = 1772409600.0
CFG = {"jobRetryBackoffS": 120, "jobFailureStreakLimit": 3}


class Rig:
    """A private DB, a stub launcher that records fires, and a fake clock whose sleep records its arguments."""

    def __init__(self) -> None:
        self.db = Database(tempfile.mkdtemp(prefix="jp-"))
        self.jobs = Jobs(self.db)
        self.store = RunStore(self.db)
        self.now = T0
        self.sleeps: list[float] = []
        self.launched: list[dict[str, Any]] = []
        self.on_sleep: Any = None
        self.policy = self.make_policy()
        self.sched = Scheduler(self.jobs, self.launch, clock=lambda: self.now, sleep=self._sleep, policy=self.policy)

    def make_policy(self) -> JobPolicy:
        return JobPolicy(self.jobs, self.store, self.launch, clock=lambda: self.now, sleep=self._sleep, settings=lambda: CFG)

    async def _sleep(self, s: float) -> None:
        self.sleeps.append(s)
        if self.on_sleep:
            self.on_sleep(s)
        self.now += s
        await asyncio.sleep(0)

    async def launch(self, job: dict[str, Any], fire: dict[str, Any]) -> str:
        rid = f"run{len(self.launched)}"
        self.launched.append(fire)
        self.store.create(rid, None, "job", {**fire})
        return rid

    def finish(self, rid: str, status: str, error: str | None = None) -> None:
        self.store.update(rid, status=status, error=error, ended_at=self.now)

    def job(self, **kw: Any) -> dict[str, Any]:
        return self.jobs.create("j", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0 - 10, **kw)

    async def fire(self, at: float) -> list[dict[str, Any]]:
        self.now = at
        return await self.sched.tick()


def test_overlap_skips_and_consumes_the_slot() -> None:
    r = Rig()
    jb = r.job()

    async def go() -> None:
        f1 = await r.fire(T0 + 3600)
        assert len(f1) == 1 and f1[0]["run_id"] == "run0"
        f2 = await r.fire(T0 + 7200)  # run0 still running
        assert f2[0]["skipped"] == "previous run still running" and f2[0]["run_id"] is None
        assert len(r.launched) == 1
        got = r.jobs.get(jb["id"])
        assert got["last_skip_reason"] and got["last_skip_at"] == T0 + 7200
        assert got["next_due_at"] == T0 + 3 * 3600  # advanced: the slot was consumed
        assert [(k["reason"], k["due_at"], k["at"]) for k in r.jobs.skips(jb["id"])] == \
            [("previous run still running", T0 + 7200, T0 + 7200)], "the skip is kept as history, not only on the job"
        r.finish("run0", "done")
        assert await r.fire(T0 + 7300) == []  # no catch-up after the long run ends
        await r.policy.drain()
        assert len(r.launched) == 1

    asyncio.run(go())


def test_retry_backoff_and_attempt_persist() -> None:
    r = Rig()
    jb = r.job()

    def flip(s: float) -> None:
        # While the watcher polls (small sleeps), the live run ends in error; the 120 s backoff is not a poll.
        if s < 120:
            for i in range(len(r.launched)):
                if r.store.get(f"run{i}")["status"] == "running":
                    r.finish(f"run{i}", "error", "boom")

    r.on_sleep = flip

    async def go() -> None:
        await r.fire(T0 + 3600)
        await r.policy.drain()

    asyncio.run(go())
    assert 120 in r.sleeps
    assert len(r.launched) == 2  # one retry, no more (max_retries=1)
    assert r.launched[1]["attempt"] == 2 and r.launched[1]["retry_of"] == "run0"
    # the attempt rides in the stored run input, so a rebuilt policy sees it
    stored = r.store.get("run1")["input"]
    assert stored["attempt"] == 2 and stored["retry_of"] == "run0"
    got = r.jobs.get(jb["id"])
    assert got["consecutive_failures"] == 1  # one failed FIRE, not one per run

    # a fresh policy settling the stored attempt-2 run must not retry again
    r.policy = r.make_policy()
    before = len(r.launched)
    asyncio.run(r.policy.settle(got, stored, "run1", r.store.get("run1")))
    assert len(r.launched) == before


def test_streak_pauses_and_success_resets() -> None:
    r = Rig()
    jb = r.job(max_retries=0)

    async def end(at: float, status: str, err: str | None = None) -> None:
        await r.fire(at)
        r.finish(f"run{len(r.launched) - 1}", status, err)
        await r.policy.drain()

    async def go() -> None:
        await end(T0 + 3600, "error", "bad key\nsecond line")
        await end(T0 + 7200, "error", "bad key")
        assert r.jobs.get(jb["id"])["consecutive_failures"] == 2
        await end(T0 + 10800, "done")  # a success in between resets
        assert r.jobs.get(jb["id"])["consecutive_failures"] == 0
        for i in range(3):
            await end(T0 + 14400 + i * 3600, "error", "bad key\nsecond line")
        got = r.jobs.get(jb["id"])
        assert got["enabled"] is False and got["next_due_at"] is None
        assert got["paused_reason"].startswith("paused after 3 failed runs: bad key")
        assert "second line" not in got["paused_reason"]
        assert r.jobs.due(T0 + 99999) == []
        # resume: clears reason + streak and re-arms from now, no catch-up
        resumed = r.jobs.update(jb["id"], {"enabled": True}, at=T0 + 50000)
        assert resumed["paused_reason"] is None and resumed["consecutive_failures"] == 0
        assert resumed["enabled"] and resumed["next_due_at"] > T0 + 50000

    asyncio.run(go())


def test_user_stop_is_not_a_failure() -> None:
    r = Rig()
    jb = r.job(max_retries=0)

    async def go() -> None:
        await r.fire(T0 + 3600)
        r.finish("run0", "stopped")
        await r.policy.drain()

    asyncio.run(go())
    got = r.jobs.get(jb["id"])
    assert got["consecutive_failures"] == 0 and got["enabled"] and len(r.launched) == 1


def test_manual_runs_are_not_watched() -> None:
    r = Rig()
    jb = r.job()

    async def go() -> None:
        r.policy.watch_soon(jb, {"manual": True}, "x")
        assert not r.policy._tasks  # noqa: SLF001

    asyncio.run(go())


def test_migration_is_idempotent_on_an_old_schema() -> None:
    d = Path(tempfile.mkdtemp(prefix="oldjobs-"))
    c = sqlite3.connect(d / "personal-os.db")
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, cron TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'UTC', "
              "enabled INTEGER NOT NULL DEFAULT 0, prompt TEXT NOT NULL DEFAULT '', project_id TEXT, last_fired_at REAL, "
              "last_due_at REAL, last_run_id TEXT, last_error TEXT, next_due_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    c.execute("INSERT INTO jobs(id,name,cron,enabled,created_at,updated_at) VALUES('a','old','0 * * * *',1,1,1)")
    c.commit()
    c.close()
    db = Database(d)
    Database(d)  # a second open must not fail
    row = Jobs(db).get("a")
    assert row["enabled"] is True and row["max_retries"] == 1 and row["consecutive_failures"] == 0
    assert row["paused_reason"] is None and row["last_skip_at"] is None


def test_expiry_fires_once_then_pauses_and_reenable_restarts() -> None:
    r = Rig()
    r.policy.settings = lambda: {**CFG, "jobExpireDays": 7}
    jb = r.job(max_retries=0)

    async def go() -> None:
        await r.fire(T0 + 3600)  # first tick stamps the window (and fires that slot)
        assert r.jobs.get(jb["id"])["expires_at"] == T0 + 3600 + 7 * 86400
        r.finish("run0", "done")
        await r.policy.drain()
        last = T0 + 3600 + 7 * 86400 + 60
        assert len(await r.fire(last)) == 1  # one more fire past expiry
        got = r.jobs.get(jb["id"])
        assert not got["enabled"] and got["paused_reason"] == "expired"
        r.finish("run1", "done")
        await r.policy.drain()
        assert await r.fire(last + 7200) == [] and len(r.launched) == 2  # then nothing
        # a real switch restarts the window; re-sending the same state keeps it
        r.jobs.update(jb["id"], {"enabled": True}, last + 10)
        got = r.jobs.get(jb["id"])
        assert got["expires_at"] is None and got["paused_reason"] is None
        await r.fire(last + 3700)
        stamped = r.jobs.get(jb["id"])["expires_at"]
        assert stamped == last + 3700 + 7 * 86400
        r.jobs.update(jb["id"], {"enabled": True}, last + 3800)
        assert r.jobs.get(jb["id"])["expires_at"] == stamped

    asyncio.run(go())


def test_expiry_off_by_default_and_migration_idempotent() -> None:
    r = Rig()
    jb = r.job(max_retries=0)

    async def go() -> None:
        await r.fire(T0 + 3600)
        r.finish("run0", "done")
        await r.policy.drain()
        await r.fire(T0 + 400 * 86400)
        got = r.jobs.get(jb["id"])
        assert got["expires_at"] is None and got["enabled"] and len(r.launched) == 2

    asyncio.run(go())
    with r.db.tx() as c:
        r.db._migrate(c)
        r.db._migrate(c)
    assert r.jobs.get(jb["id"])["name"] == "j"


# ---------------- through the app ----------------
client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    with client:
        task = getattr(appmod.app.state, "jobs_task", None)
        if task is not None:
            task.get_loop().call_soon_threadsafe(task.cancel)
        yield


def test_inbox_exposes_paused_jobs() -> None:
    jb = appmod.jobs.create("paused one", "0 * * * *", "p", timezone="UTC", enabled=True)
    appmod.jobs.pause(jb["id"], "paused after 3 failed runs: nope")
    body = client.get("/inbox").json()
    mine = [p for p in body["needs_you"]["paused_jobs"] if p["id"] == jb["id"]]
    assert mine and mine[0]["reason"].endswith("nope")
    assert body["counts"]["paused_jobs"] >= 1 and body["counts"]["needs_you"] >= 1
    r = client.patch(f"/jobs/{jb['id']}", json={"enabled": True})  # Resume
    assert r.status_code == 200 and r.json()["paused_reason"] is None
    assert all(p["id"] != jb["id"] for p in client.get("/inbox").json()["needs_you"]["paused_jobs"])
    appmod.jobs.delete(jb["id"])


def test_manual_run_with_a_live_run_is_409() -> None:
    jb = appmod.jobs.create("busy", "0 * * * *", "p", timezone="UTC", enabled=False)
    appmod.run_store.create("live-run-1", None, "job", {"job_id": jb["id"], "job": "busy"})
    r = client.post(f"/jobs/{jb['id']}/run")
    assert r.status_code == 409 and "still running" in r.text
    appmod.run_store.update("live-run-1", status="done", ended_at=time.time())
    appmod.jobs.delete(jb["id"])


def test_max_retries_is_editable_and_bounded() -> None:
    jb = client.post("/jobs", json={"name": "r", "prompt": "p", "cron": "0 * * * *", "max_retries": 2}).json()
    assert jb["max_retries"] == 2
    assert client.patch(f"/jobs/{jb['id']}", json={"max_retries": 0}).json()["max_retries"] == 0
    assert client.patch(f"/jobs/{jb['id']}", json={"max_retries": 99}).status_code == 422
    assert "jobFailureStreakLimit" in client.get("/settings").json()
    client.delete(f"/jobs/{jb['id']}")


def test_inbox_shows_expired_pause() -> None:
    jb = appmod.jobs.create("old one", "0 * * * *", "p", timezone="UTC", enabled=True)
    appmod.jobs.pause(jb["id"], "expired")
    mine = [p for p in client.get("/inbox").json()["needs_you"]["paused_jobs"] if p["id"] == jb["id"]]
    assert mine and mine[0]["reason"] == "expired"
    appmod.jobs.delete(jb["id"])


def test_a_failed_one_off_is_retried_and_a_good_one_retires() -> None:
    """A one-off is disabled the moment it fires; its retry budget still holds, and a success stays retired."""
    for outcome, want in (("error", 2), ("done", 1)):
        r = Rig()
        jb = r.jobs.create("once", "", "p", kind="once", run_at=T0 + 60, timezone="UTC", enabled=True, at=T0, max_retries=1)

        def flip(s: float, r: Rig = r, outcome: str = outcome) -> None:
            if s < 120:
                for i in range(len(r.launched)):
                    if r.store.get(f"run{i}")["status"] == "running":
                        r.finish(f"run{i}", outcome, "boom" if outcome == "error" else None)

        r.on_sleep = flip

        async def go(r: Rig = r) -> None:
            await r.fire(T0 + 120)
            await r.policy.drain()
            assert await r.fire(T0 + 7200) == []  # retired: never fires again

        asyncio.run(go())
        assert len(r.launched) == want, (outcome, len(r.launched))
        got = r.jobs.get(jb["id"])
        assert not got["enabled"] and got["next_due_at"] is None

    # boot_retry: an interrupted one-off is relaunched once, too
    r = Rig()
    r.jobs.create("once", "", "p", kind="once", run_at=T0 + 60, timezone="UTC", enabled=True, at=T0, max_retries=1)
    asyncio.run(r.fire(T0 + 120))
    r.finish("run0", "interrupted", "Interrupted")

    async def boot() -> list[str]:
        out = await r.policy.boot_retry()
        for t in list(r.policy._tasks):  # noqa: SLF001
            t.cancel()
        return out

    assert len(asyncio.run(boot())) == 1 and r.launched[1]["retry_of"] == "run0"


def test_inbox_lists_desks_and_every_review_queue() -> None:
    conv = appmod.convos.create(None, "desk", "m")
    desk = appmod.desks.create(conversation_id=conv["id"], brief="b", title="Waiting desk")
    appmod.desks.set_status(desk["id"], "review")
    doc = appmod.docs.create("Note", "one")
    appmod.docs.propose(doc["id"], "two", summary="s")
    body = client.get("/inbox").json()
    rows = [d for d in body["needs_you"]["desks"] if d["desk_id"] == desk["id"]]
    assert len(rows) == 1 and rows[0]["desk_title"] == "Waiting desk", "one row per desk waiting on the user"
    queues = {q["key"]: q["count"] for q in body["needs_you"]["elsewhere"]}
    assert all(n > 0 for n in queues.values()), "an empty queue is left out"
    assert queues.get("doc_edits", 0) >= 1, "a proposed doc edit is counted"
    assert body["counts"]["needs_you"] >= 1 + sum(queues.values())
    appmod.desks.mark_desk_seen(desk["id"])
    assert all(d["desk_id"] != desk["id"] for d in client.get("/inbox").json()["needs_you"]["desks"]), "seen clears it"
    appmod.desks.delete(desk["id"])


def test_skip_history_keeps_the_last_200_per_job() -> None:
    r = Rig()
    a, b = r.job(), r.job()
    for i in range(205):
        r.jobs.record_skip(a["id"], "previous run still running", T0 + i, T0 + i)
    r.jobs.record_skip(b["id"], "previous run still running", T0)
    with r.db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM job_skips WHERE job_id=?", (a["id"],)).fetchone()["n"]
    assert n == 200
    kept = r.jobs.skips(a["id"], 500)
    assert len(kept) == 200 and kept[0]["at"] == T0 + 204 and kept[-1]["at"] == T0 + 5, "the oldest go first"
    assert len(r.jobs.skips(b["id"])) == 1, "another job's history is untouched"
    r.jobs.delete(a["id"])
    with r.db.tx() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM job_skips WHERE job_id=?", (a["id"],)).fetchone()["n"] == 0
