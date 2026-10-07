"""Autonomous chats (desks at 'ask' autonomy) are front-agent turns: they delegate to background workers.

Offline, one scripted llm.stream_chat, permission mode manual. The model tells its seats apart: a worker's system
prompt says "background worker for Grain"; a wake turn's last user message carries the system's hand-over; anything
else is the front agent. Modelled on test_orchestration.py.

Run: backend/.venv/bin/python -m pytest backend/tests/test_autonomous_workers.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="autowork-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os import workers as W  # noqa: E402
from personal_os.commands import mention_note  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
mgr = appmod.workers_mgr
WAKE_MARK = "A background worker has finished"

FRONT: list[Any] = []
WAKE: list[Any] = []
WORKER: dict[str, list[Any]] = {}
GATES: dict[str, threading.Event] = {}
SEEN: list[dict[str, Any]] = []


def call(cid: str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args or {})}


def brief_of(messages: list[dict[str, Any]]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                cancel: asyncio.Event | None = None) -> Any:
    sys_text = str(messages[0].get("content") or "") if messages and messages[0]["role"] == "system" else ""
    names = [t["function"]["name"] for t in (tools or [])]
    mk = None
    if "background worker for Grain" in sys_text:
        seat, brief = "worker", brief_of(messages)
        mk = next((k for k in {*WORKER, *GATES} if k in brief), None)
        queue = WORKER.get(mk or "", [])
        step = queue.pop(0) if queue else {"text": f"report {mk or ''}".strip()}
    elif WAKE_MARK in brief_of(messages):
        seat = "wake"
        step = WAKE.pop(0) if WAKE else {"text": "Your work is finished."}
    else:
        seat = "front"
        step = FRONT.pop(0) if FRONT else {"text": "ok"}
    SEEN.append({"seat": seat, "marker": mk, "tools": names, "messages": [dict(m) for m in messages], "system": sys_text})
    if seat == "worker" and mk and not any(m["role"] == "assistant" for m in messages):
        gate = GATES.get(mk)
        while gate is not None and not gate.is_set():
            await asyncio.sleep(0.01)
    if step.get("delay"):
        await asyncio.sleep(step["delay"])
    calls = [] if tool_choice == "none" else step.get("calls", [])
    if step.get("text"):
        yield {"type": "delta", "text": step["text"]}
    yield {"type": "end", "finish_reason": "tool_calls" if calls else "stop", "tool_calls": calls, "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "toolDeferAbove": 0, "autoTitle": False,
                                      "followUps": False, "learnStyle": False})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    for q in (FRONT, WAKE, SEEN):
        q.clear()
    WORKER.clear()
    GATES.clear()
    monkeypatch.setattr(llm, "stream_chat", _fake)
    monkeypatch.setattr(mgr, "memory", lambda: None)
    appmod.db.set_settings({"permissionMode": "manual", "workspaceRoots": [], "delegationForce": True, "delegationAfterRounds": 2,
                            "workerMaxConcurrent": 4, "telegramPushWorkerResults": False, "permissionRules": {"allow": [], "ask": [], "deny": []}})
    yield
    for g in GATES.values():
        g.set()
    on_loop(_end_all)


async def _end_all() -> None:
    for c in list(mgr.sub.children.values()):
        if c.detached and not c.finished.is_set():
            await mgr.stop(c.conversation_id, c.id, by="tool")
    for c in list(mgr.sub.children.values()):
        if c.detached:
            await asyncio.wait_for(c.finished.wait(), 5)
    mgr.queue.clear()
    if mgr._recheck is not None:
        mgr._recheck.cancel()
    mgr.on_end = lambda cid: appmod._wake_conversation(cid)


# ---------------- helpers ----------------
def on_loop(fn: Callable[[], Any]) -> Any:
    return client.portal.call(fn)  # type: ignore[union-attr]


def wait(pred: Callable[[], Any], what: str, timeout: float = 10.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def make_desk(autonomy: str = "ask", message: str = "please do the thing") -> tuple[str, str]:
    """A chat turned into a desk the way the composer does it, then its first message. -> (conversation id, desk id)"""
    cid = appmod.convos.create(None, "Autonomous", "test-model")["id"]
    r = client.post("/cowork/desks", json={"conversation_id": cid, "autonomy": autonomy, "brief": message, "start": False})
    assert r.status_code == 200, r.text
    did = r.json()["desk"]["id"]
    r = client.post(f"/cowork/desks/{did}/message", json={"content": message})
    assert r.status_code == 200, r.text
    return cid, did


def desk_row(did: str) -> dict[str, Any]:
    return client.get(f"/cowork/desks/{did}").json()


def idle(cid: str) -> bool:
    run = appmod.bus.get(cid)
    return not (run and run.live) and not mgr.pending_wakes(cid) and not any(w["status"] in ("queued", "running", "awaiting_approval") for w in mgr.list(cid))


def settle(cid: str) -> list[dict[str, Any]]:
    wait(lambda: idle(cid), "the chat to settle")
    time.sleep(0.05)
    wait(lambda: idle(cid), "the chat to stay settled")
    return client.get(f"/conversations/{cid}").json()["messages"]


def desk_runs(did: str) -> list[dict[str, Any]]:
    return [r for r in store.list(desk_id=did, statuses=None) if r["kind"] == "desk"]  # a worker of the desk shares its desk_id


def seat(name: str) -> list[dict[str, Any]]:
    return [s for s in SEEN if s["seat"] == name]


def delegate(goal: str, **kw: Any) -> dict[str, Any]:
    return {"text": "On it.", "calls": [call("d1", "delegate", {"goal": goal, "context": "ctx", "done_criteria": "done", "report_format": "short", **kw})]}


# ---------------- a. the tools and prompt of an ask desk ----------------
def test_an_ask_desk_is_offered_delegate_and_the_front_hint() -> None:
    cid, did = make_desk("ask")
    settle(cid)
    first = seat("front")[0]
    assert "delegate" in first["tools"] and "agent_spawn" not in first["tools"]
    assert {"message_worker", "check_worker", "stop_worker", "resume_worker"} <= set(first["tools"])
    assert "## How to work" in first["system"], "the front-agent stance"
    assert "desk" in first["system"].lower() and "desk_done" in first["tools"], "and the desk's own hint and tools"


@pytest.mark.parametrize("autonomy", ["plan", "propose"])
def test_plan_and_propose_desks_have_no_hand_off_tools(autonomy: str) -> None:
    cid, did = make_desk(autonomy)
    wait(lambda: seat("front"), "the desk's first round")
    first = seat("front")[0]
    assert not set(W.FRONT_TOOLS) & set(first["tools"]) and "## How to work" not in first["system"]
    if autonomy == "propose":  # a plan desk withholds agent_spawn while it drafts its plan
        assert "agent_spawn" in first["tools"]
    client.post(f"/cowork/desks/{did}/pause")


# ---------------- b. answer, worker, wake ----------------
def test_an_ask_desk_answers_hands_off_and_is_woken_through_the_desk() -> None:
    GATES["Find the thing"] = threading.Event()
    WORKER["Find the thing"] = [{"text": "FOUND: 42"}]
    FRONT[:] = [delegate("Find the thing"), {"text": "Started."}]
    WAKE[:] = [{"text": "The answer is 42."}]
    cid, did = make_desk("ask")
    d = wait(lambda: (x := desk_row(did))["status"] == "done" and x, "the desk to settle done")
    assert d["status_reason"] == "answered"
    wait(lambda: not appmod.bus.live(cid), "the first run to end")
    assert len(desk_runs(did)) == 1, "one desk turn: no nudge follows the answer"
    assert not any("ended your reply without calling" in str(m.get("content")) for s in SEEN for m in s["messages"])
    assert len(mgr.list(cid)) == 1
    GATES["Find the thing"].set()
    msgs = settle(cid)
    runs = desk_runs(did)
    assert len(runs) == 2 and {r["kind"] for r in runs} == {"desk"}, "the wake turn is a second desk run"
    assert any(r["input"].get("wake") for r in runs)
    kinds = [(m["role"], m.get("kind")) for m in msgs]
    assert kinds == [("user", None), ("assistant", None), ("user", "wake"), ("assistant", None)], kinds
    assert msgs[3]["content"] == "The answer is 42." and "FOUND: 42" in msgs[2]["content"]
    assert len(seat("wake")) == 1 and "delegate" in seat("wake")[0]["tools"]
    wait(lambda: desk_row(did)["status"] == "done", "the desk to be done again")


# ---------------- c. forced delegation ----------------
def test_forced_delegation_narrows_an_ask_desk_after_the_threshold() -> None:
    appmod.db.set_settings({"delegationAfterRounds": 1})
    search = lambda i: call(f"c{i}", "search_memory", {"query": f"distinct {i}"})  # noqa: E731
    FRONT[:] = [{"text": "Looking.", "calls": [search(1)]},
                {"text": "", "calls": [search(2), call("d1", "delegate", {"goal": "Big job"})]},
                {"text": "Handed off."}]
    cid, did = make_desk("ask")
    settle(cid)
    front = seat("front")
    assert len(front) == 3
    assert {"search_memory", "delegate"} <= set(front[0]["tools"])
    narrowed = set(front[1]["tools"])
    assert "delegate" in narrowed and "search_memory" not in narrowed and narrowed <= W.FORCED_ALLOW, narrowed
    assert {"desk_done", "desk_ask"} & narrowed, "a desk can still finish or ask"
    refused = [m for m in front[2]["messages"] if m["role"] == "tool" and "not available in this reply any more" in str(m["content"])]
    assert len(refused) == 1


# ---------------- d. delegate agent=<name> ----------------
def approved_agent(name: str, body: str) -> dict[str, Any]:
    row = appmod.agent_defs.save(f"---\nname: {name}\ndescription: Does {name} work\n---\n{body}")
    appmod.agent_defs.approve(row["id"])
    return row


def test_delegate_agent_runs_the_worker_as_that_library_agent() -> None:
    row = approved_agent("tidyup", "Tidy everything in three bullets.")
    try:
        WORKER["Tidy job"] = [{"text": "tidied"}]
        FRONT[:] = [delegate("Tidy job", agent="tidyup"), {"text": "Started."}]
        cid, did = make_desk("ask")
        settle(cid)
        w = mgr.list(cid)
        assert len(w) == 1
        run = store.get(w[0]["id"])
        assert run["kind"] == "worker" and run["input"]["role"] == "tidyup"
        assert "Tidy everything in three bullets." in seat("worker")[0]["system"]
    finally:
        appmod.agent_defs.delete(row["id"])


def test_delegate_with_an_unknown_agent_is_a_tool_error_and_starts_nothing() -> None:
    row = approved_agent("tidyup2", "Tidy.")
    try:
        FRONT[:] = [delegate("Never runs", agent="nope"), {"text": "Sorry."}]
        cid, did = make_desk("ask")
        settle(cid)
        assert mgr.list(cid) == [] and not seat("worker")
        result = next(m for m in seat("front")[1]["messages"] if m["role"] == "tool")
        out = json.loads(result["content"])
        assert out["field"] == "role" and "tidyup2" in out["expected"] and "researcher" in out["expected"], out
    finally:
        appmod.agent_defs.delete(row["id"])


# ---------------- e. a worker holds the desk's file tools, not its control tools ----------------
def test_a_worker_is_offered_the_desk_file_tools_but_not_the_control_tools() -> None:
    desk_tools = {n for n, s in appmod.toolbox.specs.items() if s.group == "desk"}
    assert desk_tools, "the desk tools exist"
    FRONT[:] = [delegate("Plain job"), {"text": "Started."}]
    cid, did = make_desk("ask")
    settle(cid)
    assert desk_tools & set(seat("front")[0]["tools"]), "the front agent of a desk has them"
    wtools = set(seat("worker")[0]["tools"])
    files = {"desk_write_file", "desk_read_file", "desk_list_files", "desk_trash_file"}
    control = {"desk_done", "desk_deliver", "desk_ask", "desk_start"}
    assert files <= wtools and not control & wtools
    assert not control & set(store.get(mgr.list(cid)[0]["id"])["input"]["tools"])


# ---------------- e2. a desk turn that only hands work on is quiet ----------------
def test_an_ask_desk_turn_that_only_delegated_leaves_no_reply_row() -> None:
    WORKER["Quiet desk job"] = [{"text": "FOUND: 7"}]
    FRONT[:] = [{**delegate("Quiet desk job"), "text": ""}, {"text": "NO_REPLY"}]
    WAKE[:] = [{"text": "The answer is 7."}]
    cid, did = make_desk("ask")
    msgs = settle(cid)
    assert [m["content"] for m in msgs if m["role"] == "assistant"] == ["The answer is 7."], "only the worker's result is said"


# ---------------- f. hints ----------------
def test_agents_hint_and_mention_note_name_the_right_tool() -> None:
    row = approved_agent("scribe", "Write well.")
    try:
        both = {"delegate": "on", "agent_spawn": "on"}
        assert "delegate agent=<name>" in appmod._agents_hint(both, front=True) and "scribe" in appmod._agents_hint(both, front=True)
        assert appmod._agents_hint({"agent_spawn": "on"}, front=True) == "", "front needs delegate on"
        assert "agent_spawn role=<name>" in appmod._agents_hint(both) and "delegate agent" not in appmod._agents_hint(both)
        assert appmod._agents_hint({"delegate": "on"}) == "", "the spawn lane needs agent_spawn on"
    finally:
        appmod.agent_defs.delete(row["id"])
    assert "delegate agent=x" in mention_note("@x", ["x"], front=True)
    assert "agent_spawn role=x" in mention_note("@x", ["x"])
