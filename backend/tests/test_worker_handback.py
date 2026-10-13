"""Worker hand-back (workers.py, subagents.py): the wake lists what a worker did, and a silent worker is nudged, then stopped.

Offline, against a scripted llm.stream_chat that never answers while HOLD is on (and ignores cancel, as a hung model read does).

Run: backend/.venv/bin/python -m pytest -q backend/tests/test_worker_handback.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="whand-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import limits, llm  # noqa: E402
from personal_os import workers as W  # noqa: E402

mgr = appmod.workers_mgr
sub = appmod.subagent_mgr
store = appmod.run_store
HOLD = {"on": True}


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                  effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    while HOLD["on"]:
        await asyncio.sleep(0.02)
    yield {"type": "delta", "text": "report: done"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(llm, "stream_chat", _stream)
    monkeypatch.setattr(mgr, "memory", lambda: None)
    monkeypatch.setattr(mgr, "on_end", None)
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "workerMaxConcurrent": 4})
    HOLD["on"] = True
    yield
    HOLD["on"] = False


def parent(conv: str) -> dict[str, Any]:
    return {"project_id": None, "conversation_id": conv, "settings": appmod.settings(), "modes": {}, "model": "test-model",
            "tainted": False, "taint_sources": [], "agent_run_id": "run_parent", "run_id": "run_parent"}


async def until(pred: Any, what: str, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


async def started() -> tuple[str, Any]:
    conv = appmod.convos.create(None, "Handback", "test-model")["id"]
    wid = mgr.start(parent(conv), {"goal": "hold"})["worker_id"]
    ch = sub.children[wid]
    await until(lambda: ch.task_obj is not None, "the worker to start")
    return conv, ch


def tape(wid: str, calls: list[tuple[str, dict[str, Any], bool]]) -> None:
    seq = 1000
    for i, (name, args, err) in enumerate(calls):
        store.append(wid, seq + 2 * i, "tool_call", {"id": f"c{i}", "name": name, "arguments": args})
        store.append(wid, seq + 2 * i + 1, "tool_result", {"id": f"c{i}", "error": err})


def test_wake_lists_writes_not_reads_and_caps() -> None:
    async def go() -> None:
        _, ch = await started()
        calls = [("read_local_file", {"path": "/a"}, False), ("write_local_file", {"path": "/out.txt"}, False),
                 ("shell_run", {"command": "make"}, True)] + [("write_local_file", {"path": f"/f{i}"}, False) for i in range(12)]
        tape(ch.id, calls)
        acts = mgr.side_effects(ch.id)
        assert [a["tool"] for a in acts[:2]] == ["write_local_file", "shell_run"] and len(acts) == 14
        assert all(a["tool"] != "read_local_file" for a in acts)
        text, _ = W.build_wake([{"id": ch.id, "title": "t", "goal": "g", "status": "done", "text": "report", "actions": acts}])
        assert "Actions taken" in text and "write_local_file /out.txt -> ok" in text and "shell_run make -> error" in text
        assert "read_local_file" not in text and "and 2 more" in text and text.count("\n- ") == W.ACTION_LINES + 1
        none, _ = W.build_wake([{"id": ch.id, "title": "t", "goal": "g", "status": "done", "text": "report", "actions": []}])
        assert "Actions taken" not in none
        await mgr.stop(None, ch.id, by="tool")
    asyncio.run(go())


def test_report_instructions_ask_for_decisions_and_assumptions() -> None:
    assert "Decisions" in W.WORKER_PROMPT and "Assumptions" in W.WORKER_PROMPT


def test_stall_is_nudged_then_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "WORKER_STALL_NOTE_SECONDS", 1)
    monkeypatch.setattr(limits, "WORKER_STALL_NUDGE_SECONDS", 2)
    monkeypatch.setattr(limits, "WORKER_STALL_STOP_SECONDS", 4)

    async def go() -> None:
        conv, ch = await started()
        sub._beat(ch)
        assert not ch.steers and not ch.nudged_at                   # fresh: nothing yet
        ch.last_activity = time.monotonic() - 2.5
        sub._beat(ch)
        assert ch.nudged_at and "No progress for" in ch.steers[-1] and not ch.finished.is_set()
        n = len(ch.steers)
        sub._beat(ch)
        assert len(ch.steers) == n                                   # one nudge only
        ch.last_activity = time.monotonic() - 2.5                    # still silent after it (STOP - NUDGE)
        sub._beat(ch)
        await until(ch.finished.is_set, "the stalled worker to be stopped")
        row = store.get(ch.id)
        assert row["error"].startswith("Stalled:") and mgr.info(row)["status"] == "stopped"
        assert any(r["run_id"] == ch.id for r in mgr.pending_wakes(conv))  # the front agent is still owed the report
    asyncio.run(go())


def test_a_waiting_tool_is_not_stalled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "WORKER_STALL_NOTE_SECONDS", 1)
    monkeypatch.setattr(limits, "WORKER_STALL_NUDGE_SECONDS", 2)
    monkeypatch.setattr(limits, "WORKER_STALL_STOP_SECONDS", 4)

    async def go() -> None:
        _, ch = await started()
        ch.last_activity = time.monotonic() - 60
        ch.tool_since, ch.now = ch.last_activity, "shell_poll j1"
        sub._beat(ch)
        assert not ch.nudged_at and not ch.steers and not ch.stalled and sub.idle_seconds(ch) == 0
        await mgr.stop(None, ch.id, by="tool")
    asyncio.run(go())
