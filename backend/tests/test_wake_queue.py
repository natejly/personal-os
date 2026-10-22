"""The wake queue (wakes.py) and its drainer (app._drain_wakes): a wake owed while the chat is busy is delivered when it is idle,
one that was owed when the process died is delivered at the next start, the same event queues once, and each kind of wake
reads exactly as its builder writes it.

Offline: one scripted llm.stream_chat. Run: backend/.venv/bin/python -m pytest backend/tests/test_wake_queue.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="wakeq-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, workers as workers_mod  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
q = appmod.wake_q
GATE: list[asyncio.Event] = []


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    if GATE and messages[-1]["role"] == "user" and "hold" in str(messages[-1]["content"]):
        await GATE[0].wait()
    yield {"type": "delta", "text": "noted"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "autoTitle": False,
                                      "followUps": False, "learnStyle": False})
        yield


@pytest.fixture(autouse=True)
def _llm(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    GATE.clear()
    monkeypatch.setattr(llm, "stream_chat", _fake)


def wait(pred: Callable[[], Any], what: str, timeout: float = 10.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def chat(title: str) -> str:
    cid = client.post("/conversations", json={"title": title}).json()["id"]
    appmod.convos.update(cid, {"settings": {"deskId": ""}})  # a plain chat
    return cid


def wakes_of(cid: str) -> list[dict[str, Any]]:
    return [m for m in appmod.convos.get(cid)["messages"] if m.get("kind") == "wake"]


def on_loop(fn: Callable[[], Any]) -> None:
    async def go() -> None:
        r = fn()
        if asyncio.iscoroutine(r):
            await r
    asyncio.run_coroutine_threadsafe(go(), appmod._loop).result(timeout=20)  # type: ignore[arg-type]


WAKE = {"ids": [], "tainted": True, "title": "a coding session", "attachments": []}


def test_the_same_event_queues_once_even_after_it_was_delivered() -> None:
    cid = chat("Dedupe")
    assert q.add(cid, "job", {"id": "j1"}, "job:dedupe-1") is True
    assert q.add(cid, "job", {"id": "j1"}, "job:dedupe-1") is False
    (row,) = q.open(cid, "job")
    q.done([row["id"]])
    assert q.add(cid, "job", {"id": "j1"}, "job:dedupe-1") is False and q.open(cid, "job") == []
    assert q.add(cid, "ext", {"text": "a", "wake": WAKE}) and q.add(cid, "ext", {"text": "a", "wake": WAKE}), "no key: each one queues"
    q.done(r["id"] for r in q.open(cid, "ext"))


def test_a_wake_owed_while_a_reply_is_written_is_delivered_when_it_ends() -> None:
    cid = chat("Busy")
    gate = asyncio.run_coroutine_threadsafe(_make_gate(), appmod._loop).result(timeout=5)  # type: ignore[arg-type]
    GATE.append(gate)
    client.post(f"/conversations/{cid}/chat", json={"content": "hold this reply"})
    wait(lambda: appmod.bus.answering(cid), "the reply to start")
    appmod.owe_wake(cid, "Session s1 settled.", WAKE)
    appmod.owe_wake(cid, "Session s2 settled.", WAKE)
    time.sleep(0.3)
    assert wakes_of(cid) == [] and len(q.open(cid, "ext")) == 2, "nothing starts while the reply is being written"
    appmod._loop.call_soon_threadsafe(gate.set)  # type: ignore[union-attr]
    wait(lambda: wakes_of(cid) and not q.open(cid, "ext"), "the folded wake to run and be delivered")
    assert [m["content"] for m in wakes_of(cid)] == ["Session s1 settled.\n\nSession s2 settled."], "several owed fold into one turn"


async def _make_gate() -> asyncio.Event:
    return asyncio.Event()


def test_a_wake_that_was_live_when_the_process_died_is_delivered_at_the_next_start() -> None:
    cid = chat("Restart")
    q.add(cid, "ext", {"text": "Left over.", "wake": WAKE})
    (row,) = q.open(cid, "ext")
    q.claim([row["id"]])  # its turn was running when the backend stopped
    assert q.pending(cid) == [], "claimed: no second drainer starts it"
    on_loop(appmod._workers_startup)  # what the backend does at startup
    wait(lambda: wakes_of(cid) and not q.open(cid, "ext"), "the leftover wake to be delivered")
    assert wakes_of(cid)[0]["content"] == "Left over."


def test_a_scheduled_tasks_wake_reads_as_a_workers_report() -> None:
    cid = chat("Job")
    report = {"id": "run-q", "label": "Scheduled task", "title": "Digest", "goal": "g", "status": "done", "text": "Digest result", "tainted": False}
    q.add(cid, "job", report, "job:run-q")
    on_loop(lambda: appmod._drain_wakes(cid))
    wait(lambda: wakes_of(cid) and not q.open(cid, "job"), "the task's wake to be delivered")
    assert wakes_of(cid)[0]["content"] == workers_mod.build_wake([report])[0]


def test_a_worker_ending_with_its_report_owed_is_a_queue_row_until_the_wake_ends() -> None:
    cid = chat("Worker")
    rid = "sa_q_" + os.urandom(3).hex()
    appmod.run_store.create(rid, None, "worker", {"task": "t", "role": "general", "conversation_id": cid, "title": "T", "goal": "G", "depth": 1,
                                                  "allow_subworkers": False, "tools": []})
    q.add(cid, "worker", {"run_id": rid}, f"worker:{rid}")
    on_loop(lambda: appmod._drain_wakes(cid))
    time.sleep(0.2)
    assert wakes_of(cid) == [] and appmod.workers_mgr.pending_wakes(cid) == [], "a worker still running is not reported yet"
    appmod.run_store.update(rid, status="done", ended_at=time.time())
    assert [r["run_id"] for r in appmod.workers_mgr.pending_wakes(cid)] == [rid]
    on_loop(lambda: appmod._drain_wakes(cid))
    wait(lambda: wakes_of(cid) and not q.open(cid, "worker"), "the worker's wake to be delivered")
    assert f"Worker {rid} (T): done." in wakes_of(cid)[0]["content"]
