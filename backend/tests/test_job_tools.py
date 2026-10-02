"""Per-job tool allowlist and the read-only dry run. Offline: the model is a scripted stub, Google is a recorder.

Run: backend/.venv/bin/python -m pytest backend/tests/test_job_tools.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="jobtools-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import job_tools, llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import SEED_JOBS, Jobs, Scheduler  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
jobs = appmod.jobs
T0 = 1772409600.0
ROUNDS: list[Any] = []
SENT: list[str] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN_TOOLS.append([t["function"]["name"] for t in tools or []])
    SEEN_PROMPTS.append(next((m["content"] for m in reversed(messages) if m["role"] == "user"), ""))
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


SEEN_TOOLS: list[list[str]] = []
SEEN_PROMPTS: list[str] = []


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
    SEEN_TOOLS.clear()
    SEEN_PROMPTS.clear()
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET enabled=0, next_due_at=NULL")

    def rec(*a: Any, **k: Any) -> dict[str, Any]:
        SENT.append("sent")
        return {"id": "x", "ok": True}

    for name in ("gmail_send", "gmail_draft", "calendar_create"):
        setattr(appmod.google, name, rec)
    yield


def call(name: str, args: dict[str, Any], cid: str = "call_0") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def make_job(**kw: Any) -> dict[str, Any]:
    return jobs.create(f"tools {time.time()}", "0 * * * *", "report", timezone="UTC", enabled=True, at=T0, **kw)


def fire_scheduled(job: dict[str, Any]) -> dict[str, Any]:
    s = Scheduler(jobs, appmod._launch_job, clock=lambda: T0 + 3600)  # noqa: SLF001

    async def go() -> str:
        fired = await s.tick()
        run = next(r for r in appmod.bus._runs.values() if r.run_id == fired[0]["run_id"])  # noqa: SLF001
        with contextlib.suppress(Exception):
            await run.task  # type: ignore[misc]
        return fired[0]["run_id"]

    return store.get(asyncio.run(go())) or {}


def wait_ended(run_id: str) -> dict[str, Any]:
    for _ in range(200):
        row = store.get(run_id) or {}
        if row.get("ended_at") is not None:
            return row
        time.sleep(0.05)
    raise AssertionError("run never ended")


def results(run_id: str) -> list[dict[str, Any]]:
    return [d for _, e, d in store.events(run_id) if e == "tool_result"]


def conv_settings(run: dict[str, Any]) -> dict[str, Any]:
    return appmod.convos.get(run["conversation_id"], with_messages=False)["settings"]


# ---------------- the map ----------------
def test_tool_modes_inherit_and_narrow() -> None:
    tools = appmod._all_tool_infos()  # noqa: SLF001
    assert job_tools.tool_modes(None, tools) == {}
    modes = job_tools.tool_modes(["calendar_events"], tools)
    assert "calendar_events" not in modes and modes["gmail_send"] == "off" and set(modes.values()) == {"off"}
    sched = [t["name"] for t in tools if t["danger"] == "schedules"]
    assert sched, "the toolbox has a schedules-tier tool"
    assert all(job_tools.tool_modes(sched, tools)[n] == "off" for n in sched)  # listing it does not allow it
    ro = job_tools.read_only_names(tools)
    assert "calendar_events" in ro and "gmail_send" not in ro and "todo_add" not in ro and "desk_done" not in ro


def test_api_validates_names() -> None:
    r = client.post("/jobs", json={"name": "bad", "prompt": "p", "cron": "0 * * * *", "allowed_tools": ["calendar_events", "nope_tool"]})
    assert r.status_code == 400 and "nope_tool" in r.text
    sched = next(t["name"] for t in appmod._all_tool_infos() if t["danger"] == "schedules")  # noqa: SLF001
    r = client.post("/jobs", json={"name": "bad", "prompt": "p", "cron": "0 * * * *", "allowed_tools": [sched]})
    assert r.status_code == 400 and sched in r.text
    ok = client.post("/jobs", json={"name": "ok", "prompt": "p", "cron": "0 * * * *", "allowed_tools": ["calendar_events"]})
    assert ok.status_code == 200 and ok.json()["allowed_tools"] == ["calendar_events"]
    assert client.patch(f"/jobs/{ok.json()['id']}", json={"allowed_tools": ["zzz"]}).status_code == 400
    # PATCH null resets to inherit
    back = client.patch(f"/jobs/{ok.json()['id']}", json={"allowed_tools": None})
    assert back.status_code == 200 and back.json()["allowed_tools"] is None
    assert client.get("/jobs").json() and all("allowed_tools" in j for j in client.get("/jobs").json())
    client.delete(f"/jobs/{ok.json()['id']}")


# ---------------- a run is provably confined ----------------
def test_a_job_without_an_allowlist_is_unchanged() -> None:
    job = make_job()
    run = fire_scheduled(job)
    assert run["status"] == "done"
    assert not conv_settings(run).get("tools"), "no override map: the job inherits every tool mode as before"


def test_a_run_cannot_call_outside_its_allowlist() -> None:
    ROUNDS.append({"tool_calls": [call("todo_list", {}, "a"), call("gmail_send", {"to": "x@y.z", "subject": "s", "body": "b"}, "b"),
                                  call("current_time", {}, "c")]})
    ROUNDS.append(["done"])
    job = make_job(allowed_tools=["current_time"])
    run = fire_scheduled(job)
    assert run["status"] == "done"
    assert SEEN_TOOLS[0] == ["current_time"], "the model is only offered the allowlisted tool"
    res = results(run["run_id"])
    by_err = [bool(r["error"]) for r in res]
    assert by_err == [True, True, False]  # two refused, the allowlisted one executed
    assert appmod.proposals.list("pending", run_id=run["run_id"]) == [] and SENT == []


def test_the_allowlist_cannot_widen_a_tool_the_user_turned_off() -> None:
    client.put("/settings", json={"tools": {"current_time": "off"}})
    try:
        ROUNDS.append({"tool_calls": [call("current_time", {})]})
        ROUNDS.append(["done"])
        job = make_job(allowed_tools=["current_time"])
        run = fire_scheduled(job)
        assert SEEN_TOOLS[0] == []
        assert results(run["run_id"])[0]["error"]
    finally:
        client.put("/settings", json={"tools": {}})


# ---------------- dry run ----------------
def test_dry_run_is_read_only_and_invisible() -> None:
    job = make_job()
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": "x"}, "a"), call("gmail_send", {"to": "x@y.z", "subject": "s", "body": "b"}, "b"),
                                  call("current_time", {}, "c")]})
    ROUNDS.append(["Here is what I would do."])
    r = client.post(f"/jobs/{job['id']}/dry_run")
    assert r.status_code == 200 and r.json()["ok"]
    run = wait_ended(r.json()["run_id"])
    assert run["input"]["dry_run"] is True and run["status"] == "done"
    assert "todo_add" not in SEEN_TOOLS[0] and "gmail_send" not in SEEN_TOOLS[0] and "current_time" in SEEN_TOOLS[0]
    assert SEEN_PROMPTS[0].startswith("This is a preview.") and "report" in SEEN_PROMPTS[0]
    assert [bool(x["error"]) for x in results(run["run_id"])] == [True, True, False]
    assert appmod.proposals.list(None, run_id=run["run_id"]) == [] and SENT == []
    assert run["run_id"] not in [a["run_id"] for a in client.get("/inbox").json()["while_you_were_away"]]
    assert run["run_id"] in [a["run_id"] for a in client.get("/inbox?include_dry=1").json()["while_you_were_away"]]
    assert not appmod.job_policy._tasks, "a preview is never watched, so it can never count as a failure"  # noqa: SLF001
    assert jobs.get(job["id"])["consecutive_failures"] == 0
    assert client.post("/jobs/nope/dry_run").status_code == 404


def test_dry_run_respects_the_jobs_own_allowlist_and_is_not_blocked_by_a_live_run() -> None:
    job = make_job(allowed_tools=["current_time"])
    store.create("live-real", None, "job", {"job_id": job["id"]})
    try:
        ROUNDS.append(["ok"])
        run = wait_ended(client.post(f"/jobs/{job['id']}/dry_run").json()["run_id"])
        assert SEEN_TOOLS[0] == ["current_time"]
        assert run["status"] == "done"
    finally:
        store.update("live-real", status="done", ended_at=time.time())


# ---------------- storage ----------------
def test_migration_idempotent_and_old_rows_decode_to_none() -> None:
    d = Path(tempfile.mkdtemp(prefix="oldjobs2-"))
    c = sqlite3.connect(d / "personal-os.db")
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, cron TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'UTC', "
              "enabled INTEGER NOT NULL DEFAULT 0, prompt TEXT NOT NULL DEFAULT '', project_id TEXT, last_fired_at REAL, "
              "last_due_at REAL, last_run_id TEXT, last_error TEXT, next_due_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    c.execute("INSERT INTO jobs(id,name,cron,enabled,created_at,updated_at) VALUES('a','old','0 * * * *',1,1,1)")
    c.commit()
    c.close()
    Database(d)
    row = Jobs(Database(d)).get("a")
    assert row["allowed_tools"] is None and row["enabled"] is True


def test_seeded_jobs_get_sensible_allowlists_that_validate() -> None:
    known = {t["name"] for t in appmod._all_tool_infos()}  # noqa: SLF001
    for s in SEED_JOBS:
        assert s["allowed_tools"] and set(s["allowed_tools"]) <= known, s["name"]
    j = Jobs(Database(tempfile.mkdtemp(prefix="seed-")))
    j.seed()
    assert all(x["allowed_tools"] for x in j.list())
