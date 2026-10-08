"""Worker lifecycle (workers.py, subagents.py): what a worker owns, why it ended, liveness, and resuming without repeats.

Offline, against a scripted llm.stream_chat; no shell process is started (shell.py's own tests cover kill_run on real
processes). The claims:
  - a worker's tool ctx run_id is its own run id, so the jobs it starts are keyed to it, and they are killed when it ends;
  - every interrupted worker row says why (stopped by whom, backend shutdown);
  - a resumed worker replays a write its predecessor already made instead of making it again;
  - a working worker bumps its run row's updated_at, and one with no progress says so in its status line, while one
    waiting on a job that prints does not;
  - jobs a worker left running when the last backend died are killed at startup; other orphans are left listed.

Run: backend/.venv/bin/python -m pytest -q backend/tests/test_worker_lifecycle.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="wlife-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import limits, llm, shell  # noqa: E402
from personal_os import workers as W  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

mgr = appmod.workers_mgr
sub = appmod.subagent_mgr
store = appmod.run_store
HOLD = {"on": False}


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                  effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    while HOLD["on"] and not (cancel is not None and cancel.is_set()):
        await asyncio.sleep(0.02)
    yield {"type": "delta", "text": "report: done"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(llm, "stream_chat", _stream)
    monkeypatch.setattr(mgr, "memory", lambda: None)
    monkeypatch.setattr(mgr, "on_end", None)
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "workerMaxConcurrent": 4})
    HOLD["on"] = False
    yield
    HOLD["on"] = False


def parent(conv: str) -> dict[str, Any]:
    return {"project_id": None, "conversation_id": conv, "settings": appmod.settings(), "modes": {}, "model": "test-model",
            "tainted": False, "taint_sources": [], "agent_run_id": "run_parent", "run_id": "run_parent"}


def new_conv() -> str:
    return appmod.convos.create(None, "Worker life", "test-model")["id"]


async def until(pred: Any, what: str, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def test_a_worker_owns_its_jobs_and_says_who_stopped_it(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def kill_run(run_id: str) -> int:
        killed.append(run_id)
        return 1
    monkeypatch.setattr(appmod.toolbox.shell, "kill_run", kill_run)

    async def go() -> None:
        HOLD["on"] = True
        conv = new_conv()
        wid = mgr.start(parent(conv), {"goal": "hold"})["worker_id"]
        ch = sub.children[wid]
        assert ch.ctx["run_id"] == wid and ch.ctx["agent_run_id"] == wid  # its jobs are keyed to it, not to the reply
        await until(lambda: ch.state == "running" and ch.task_obj is not None, "the worker to start")
        await mgr.stop(conv, wid, by="ui")
        await until(ch.finished.is_set, "the worker to end")
        row = store.get(wid)
        assert killed == [wid]
        assert row["status"] == "interrupted" and row["error"] == "Stopped by the user."
        assert mgr.info(row)["status"] == "stopped"
    asyncio.run(go())


def test_shutdown_and_watchdog_ends_carry_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        HOLD["on"] = True
        conv = new_conv()
        a = sub.children[mgr.start(parent(conv), {"goal": "a"})["worker_id"]]
        b = sub.children[mgr.start(parent(conv), {"goal": "b"})["worker_id"]]
        await until(lambda: a.task_obj is not None and b.task_obj is not None, "both to start")
        sub.halt(a, "shutdown")
        sub.stop_tree(b.id, "stale")
        await until(lambda: a.finished.is_set() and b.finished.is_set(), "both to end")
        assert "backend shut down" in store.get(a.id)["error"]
        assert "hang watchdog" in store.get(b.id)["error"]
        assert store.get(a.id)["status"] == store.get(b.id)["status"] == "interrupted"
    asyncio.run(go())


def test_a_resumed_worker_replays_a_write_instead_of_repeating_it(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[dict[str, Any]] = []

    async def write(ctx: dict[str, Any], x: int = 0) -> Any:
        ran.append({"x": x})
        return {"ok": True, "x": x}
    spec = ToolSpec("fake_write", "test", _obj({"x": {"type": "integer"}}, []), write, "files", "writes")
    monkeypatch.setitem(appmod.toolbox.specs, "fake_write", spec)

    async def go() -> None:
        conv = new_conv()
        first = sub.children[mgr.start(parent(conv), {"goal": "write once"})["worker_id"]]
        await until(first.finished.is_set, "the first worker to end")
        res, replayed = await store.call_once(first.id, 1, "fake_write", {"x": 1}, lambda: write({}, 1))
        assert not replayed and len(ran) == 1
        got = mgr.resume(parent(conv), first.id, "carry on")
        nxt = sub.children[got["worker_id"]]
        assert nxt.resume_of == first.id
        again = await sub._call(nxt, "fake_write", {"x": 1}, f"{nxt.id}:c1", spec)
        assert again.get("replayed") and again["x"] == 1 and len(ran) == 1  # the write is not made twice
        fresh = await sub._call(nxt, "fake_write", {"x": 2}, f"{nxt.id}:c2", spec)
        assert not fresh.get("replayed") and len(ran) == 2                  # a new write still runs
        await until(nxt.finished.is_set, "the resumed worker to end")
    asyncio.run(go())


def test_a_resumed_history_does_not_claim_an_unanswered_call_never_ran() -> None:
    from personal_os.subagents import _close_calls
    msgs = _close_calls([{"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]}])
    assert "may or may not have taken effect" in msgs[-1]["content"] and "not run" not in msgs[-1]["content"]


def test_heartbeat_and_stall_note(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> None:
        HOLD["on"] = True
        conv = new_conv()
        wid = mgr.start(parent(conv), {"goal": "slow"})["worker_id"]
        ch = sub.children[wid]
        await until(lambda: ch.task_obj is not None, "the worker to start")
        store._exec("UPDATE agent_runs SET updated_at=1 WHERE run_id=?", (wid,))
        ch.beat_at = 0.0
        sub._beat(ch)
        assert store.get(wid)["updated_at"] > time.time() - 5  # progress bumped the row
        before = store.get(wid)["updated_at"]
        sub._beat(ch)
        assert store.get(wid)["updated_at"] == before          # throttled
        # no progress for longer than the note threshold: said in the status line, and nothing stops it
        ch.last_activity = time.monotonic() - limits.WORKER_STALL_NOTE_SECONDS - 60
        info = mgr.info(store.get(wid))
        assert info["idle_s"] >= limits.WORKER_STALL_NOTE_SECONDS and "no progress for" in info["now"]
        sub._beat(ch)
        assert ch.stalled and not ch.finished.is_set()
        # waiting on a job of its own that is printing is progress
        job = shell.Job("jfake", "opencode run", "/tmp", conv, wid, True, False, 1000)
        job.out_at = time.time()
        appmod.toolbox.shell.jobs[job.id] = job
        try:
            assert mgr.info(store.get(wid))["idle_s"] < 5
            sub._beat(ch)
            assert not ch.stalled
        finally:
            appmod.toolbox.shell.jobs.pop(job.id, None)
        await mgr.stop(conv, wid, by="tool")
    asyncio.run(go())


def test_orphaned_worker_jobs_are_reaped_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    async def kill(job: Any) -> str:
        killed.append(job.id)
        job.status = "killed"
        return "killed"
    monkeypatch.setattr(appmod.toolbox.shell, "kill", kill)

    async def go() -> None:
        conv = new_conv()
        w = sub.children[mgr.start(parent(conv), {"goal": "gone"})["worker_id"]]
        await until(w.finished.is_set, "the worker to end")
        store.create("run_chatx", conv, "chat", {})
        store.update("run_chatx", status="done", ended_at=time.time())
        jobs = appmod.toolbox.shell.jobs
        for jid, rid in (("jw", w.id), ("jc", "run_chatx"), ("jn", None)):
            j = shell.Job(jid, "sleep", "/tmp", conv, rid, True, False, 1000)
            j.status = "orphaned"
            jobs[jid] = j
        try:
            assert await mgr.reap_orphan_jobs() == 1
            assert killed == ["jw"] and jobs["jc"].status == jobs["jn"].status == "orphaned"
        finally:
            for jid in ("jw", "jc", "jn"):
                jobs.pop(jid, None)
    asyncio.run(go())


def test_briefs_ask_for_locations_isolation_and_a_structured_report() -> None:
    brief = W.render_brief({"goal": "Fix the bug"})
    assert "## Report format" in brief and "verification" in brief
    assert "Short table" in W.render_brief({"goal": "x", "report_format": "Short table"})
    assert "git worktree" in W.WORKER_PROMPT and "shell_poll with wait_s" in W.WORKER_PROMPT
    assert "verify" in appmod.toolbox.specs["delegate"].description and "absolute paths" in W.FRONT_AGENT_HINT
