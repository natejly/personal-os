"""The seeded Reconcile job: seeded once, unattended and propose-only, and told about its own earlier proposals.

Run: PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reconcile_job.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="reconciletest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import job_tools, llm, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import RECONCILE_JOB, Jobs, Scheduler, next_fire, valid_cron  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
jobs, proposals, store = appmod.jobs, appmod.proposals, appmod.run_store
T0 = 1_772_409_600.0  # 2026-03-02 00:00:00 UTC, a Monday
ROUNDS: list[Any] = []
SEEN: list[list[dict[str, Any]]] = []
SENT: list[str] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append(messages)
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _recorder(name: str):  # type: ignore[no-untyped-def]
    def fn(*args: Any, **kw: Any) -> dict[str, Any]:
        SENT.append(name)
        return {"id": f"{name}-1", "ok": True, "verified": True,
                "verification": {"status": verify.VERIFIED, "what": name, "attempts": 1, "compared": ["id"]}}
    return fn


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        task = getattr(appmod.app.state, "jobs_task", None)
        if task is not None:
            task.get_loop().call_soon_threadsafe(task.cancel)  # the tests drive tick() with a fake clock
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear(), SEEN.clear(), SENT.clear()
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET enabled=0, next_due_at=NULL")
    appmod.google.calendar_create = _recorder("calendar_create")
    yield


def _reconcile_job() -> dict[str, Any]:
    r = RECONCILE_JOB
    return jobs.create(f"Reconcile {time.time()}", r["cron"], r["prompt"], timezone="UTC", enabled=True, at=T0,
                       allowed_tools=r["allowed_tools"])


def _fire(at: float) -> None:
    s = Scheduler(jobs, appmod._launch_job, clock=lambda: at)  # noqa: SLF001

    async def go() -> None:
        for f in await s.tick():
            run = next((x for x in appmod.bus._runs.values() if x.run_id == f["run_id"]), None)  # noqa: SLF001
            if run is not None and run.task is not None:
                with contextlib.suppress(Exception):
                    await run.task

    asyncio.run(go())


def _runs(job_id: str) -> list[dict[str, Any]]:
    return [r for r in store.of_kind("job", limit=500) if (r["input"] or {}).get("job_id") == job_id]


def _calls(rid: str) -> dict[str, Any]:
    return {"id": rid, "name": "calendar_create",
            "arguments": json.dumps({"summary": "Talk: graph databases", "start": "2026-10-20T16:00:00", "end": "2026-10-20T17:00:00"})}


# ---------------- seeding ----------------
def test_reconcile_is_seeded_once_enabled_twice_a_day_and_never_back_after_a_delete() -> None:
    fresh = Jobs(Database(tempfile.mkdtemp(prefix="seedrec-")))
    assert fresh.seed(at=T0) >= 1
    row = next(j for j in fresh.list() if j["name"] == "Reconcile")
    assert row["enabled"] is True and row["next_due_at"] is not None and valid_cron(row["cron"])
    first = next_fire(row["cron"], "UTC", T0)
    assert (next_fire(row["cron"], "UTC", first) - first) < 12 * 3600 and (first - T0) < 24 * 3600
    assert fresh.seed(at=T0) == 0
    fresh.delete(row["id"])
    assert fresh.seed(at=T0) == 0 and "Reconcile" not in {j["name"] for j in fresh.list()}


def test_a_database_seeded_before_reconcile_existed_gets_it_once() -> None:
    old = Jobs(Database(tempfile.mkdtemp(prefix="seedold-")))
    with old.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('jobsSeeded', 'true')")
    assert old.seed(at=T0) == 1 and old.seed(at=T0) == 0
    assert [j["name"] for j in old.list()] == ["Reconcile"]


def test_the_allowlist_has_no_in_app_write_and_every_write_in_it_is_external() -> None:
    infos = {t["name"]: t for t in appmod._all_tool_infos()}  # noqa: SLF001
    allowed = set(RECONCILE_JOB["allowed_tools"])
    assert allowed <= set(infos), allowed - set(infos)
    writers = {n for n in allowed if infos[n]["danger"] not in ("safe",)}
    assert writers and all(infos[n]["danger"] == "external" for n in writers), writers
    modes = job_tools.tool_modes(RECONCILE_JOB["allowed_tools"], infos.values())
    for n in ("todo_add", "todo_update", "save_memory", "doc_edit", "doc_create", "gmail_send", "schedule_task"):
        assert modes.get(n) == "off", n


# ---------------- propose only ----------------
def test_a_calendar_create_from_reconcile_is_a_pending_proposal_not_a_write() -> None:
    ROUNDS.append({"tool_calls": [_calls("c1")]})
    ROUNDS.append(["Found one event missing from the calendar; proposed it."])
    job = _reconcile_job()
    _fire(T0 + 24 * 3600)
    run = _runs(job["id"])[0]
    assert run["status"] == "done" and SENT == []
    mine = proposals.list("pending", run_id=run["run_id"])
    assert len(mine) == 1 and mine[0]["tool"] == "calendar_create" and mine[0]["job_id"] == job["id"]
    assert mine[0]["editable"] is True, "the user can correct it before accepting"


def test_an_in_app_write_is_not_available_to_reconcile() -> None:
    ROUNDS.append({"tool_calls": [{"id": "t1", "name": "todo_add", "arguments": json.dumps({"title": f"x{time.time()}"})}]})
    ROUNDS.append(["done"])
    job = _reconcile_job()
    _fire(T0 + 24 * 3600)
    run = _runs(job["id"])[0]
    assert proposals.list("pending", run_id=run["run_id"]) == []
    results = [d for _, e, d in store.events(run["run_id"]) if e == "tool_result"]
    assert results and results[0].get("error"), "the call was refused, not run"


# ---------------- earlier proposals reach the next run ----------------
def _user_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content")) for m in messages if m.get("role") == "user")


def test_pending_and_rejected_proposals_are_in_the_next_runs_prompt_but_accepted_ones_are_not() -> None:
    job = _reconcile_job()
    mk = lambda title: proposals.create(run_id=None, tool="calendar_create", job_id=job["id"],  # noqa: E731
                                        args={"summary": title, "start": "2026-10-20T16:00:00"})
    mk("Pending standup")
    proposals.reject(mk("Rejected offsite")["id"])
    proposals.claim(mk("Accepted dinner")["id"])
    other = jobs.create(f"other {time.time()}", "0 * * * *", "x", timezone="UTC", enabled=False, at=T0)
    proposals.create(run_id=None, tool="calendar_create", job_id=other["id"], args={"summary": "Someone else's"})

    _fire(T0 + 24 * 3600)
    assert _runs(job["id"]), "the job ran"
    text = _user_text(SEEN[0])
    assert "[pending] calendar_create" in text and "Pending standup" in text
    assert "[rejected] calendar_create" in text and "Rejected offsite" in text
    assert "Accepted dinner" not in text and "Someone else's" not in text
    assert "Do not propose any of these again" in text


def test_a_job_with_no_earlier_proposals_gets_its_prompt_unchanged() -> None:
    job = _reconcile_job()
    _fire(T0 + 24 * 3600)
    assert "earlier proposals" not in _user_text(SEEN[0]).split(RECONCILE_JOB["prompt"])[-1]
