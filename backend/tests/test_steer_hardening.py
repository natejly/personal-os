"""Steer ordering and teardown in the chat loop.

A steer before the first token swaps the untouched row, a steer in the context is not sent twice, a steer during an
approval wait declines the card with the user's words, breakers start clean after a fold, nothing awaits between the
last steer check and `done`, and a call that was executing when the task was cut is recorded as interrupted.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_steer_hardening.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="steerhard-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction, llm  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []
SEEN: list[list[dict[str, Any]]] = []
RAN: list[dict[str, Any]] = []
STATE: dict[str, Any] = {"hang_started": False}
NAMES = ("t_echo", "t_ask", "t_hang")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    """A step is a list of text chunks, a dict (tool calls), the string "wait" (hold until a steer or Stop, then end
    as a cancelled read does), or a callable that sees the messages and returns one of those."""
    SEEN.append(copy.deepcopy(messages))
    step = ROUNDS.pop(0) if ROUNDS else ["done"]
    if callable(step):
        step = step(messages)
    if step == "wait":
        assert cancel is not None
        await cancel.wait()
        yield {"type": "end", "finish_reason": "cancelled", "tool_calls": [], "usage": None}
        return
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _echo(ctx: dict[str, Any], text: str) -> dict[str, Any]:
    RAN.append({"text": text})
    return {"ok": True, "text": text}


async def _hang(ctx: dict[str, Any]) -> dict[str, Any]:
    STATE["hang_started"] = True
    await asyncio.Event().wait()
    return {"ok": True}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    obj = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    appmod.toolbox.specs["t_echo"] = ToolSpec("t_echo", "t", obj, _echo, "test", "safe")
    appmod.toolbox.specs["t_ask"] = ToolSpec("t_ask", "t", obj, _echo, "test", "safe")
    appmod.toolbox.specs["t_hang"] = ToolSpec("t_hang", "t", {"type": "object", "properties": {}}, _hang, "test", "safe")
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real
    for n in NAMES:
        appmod.toolbox.specs.pop(n, None)


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    SEEN.clear()
    RAN.clear()
    STATE["hang_started"] = False
    yield
    ROUNDS.clear()


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def start(modes: dict[str, str] | None = None, content: str = "go") -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    if modes:
        j("PATCH", f"/conversations/{cid}", {"settings": {"tools": modes}})
    return cid, j("POST", f"/conversations/{cid}/chat", {"content": content})["run_id"]


def finished(rid: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "the run to finish")


def tape(rid: str) -> list[tuple[str, Any]]:
    return [(e, d) for _, e, d in store.events(rid)]


def call(name: str, arguments: str, cid: str = "c1") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": arguments}


def users(messages: list[dict[str, Any]]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


def test_steer_before_the_first_token_swaps_the_untouched_row() -> None:
    ROUNDS.extend(["wait", ["answered"]])
    cid, rid = start()
    wait_until(lambda: len(SEEN) == 1, "the first request")
    j("POST", f"/conversations/{cid}/steer", {"content": "actually, something else"})
    finished(rid)
    ev = tape(rid)
    names = [e for e, _ in ev if e in ("assistant_message", "user_message", "removed_message", "delta", "done")]
    assert names == ["user_message", "assistant_message", "user_message", "removed_message", "assistant_message", "delta", "done"], names
    old = [d for e, d in ev if e == "assistant_message"][0]["id"]
    new = [d for e, d in ev if e == "assistant_message"][1]["id"]
    assert old != new
    assert [d for e, d in ev if e == "removed_message"][0]["id"] == old
    assert [d for e, d in ev if e == "done"][-1]["id"] == new
    assert users(SEEN[1]).count("actually, something else") == 1, "the steer is sent once"
    rows = j("GET", f"/conversations/{cid}")["messages"]
    assert [m["role"] for m in rows] == ["user", "user", "assistant"] and rows[-1]["id"] == new and rows[-1]["content"] == "answered"


def test_a_steer_already_in_the_context_is_not_sent_twice() -> None:
    real = compaction.prepare_history

    async def late(*a: Any, **kw: Any) -> Any:
        # The steer lands while the context is being assembled: persisted and queued before the history is read.
        cid = a[4]
        run = appmod.bus.get(cid)
        um = appmod.convos.add_message(cid, "user", "late steer")
        run.publish("user_message", um)
        run.steers.append(um)
        history, info = await real(*a, **kw)
        info.setdefault("row_ids", [m["id"] for m in appmod.convos.get(cid)["messages"]])
        return history, info

    compaction.prepare_history = late
    try:
        ROUNDS.append(["ok"])
        cid, rid = start()
        finished(rid)
    finally:
        compaction.prepare_history = real
    assert users(SEEN[0]).count("late steer") == 1, users(SEEN[0])
    assert not any(e == "removed_message" for e, _ in tape(rid)), "a steer older than the row leaves the row alone"


def test_a_steer_during_an_approval_wait_declines_the_card_with_the_users_words() -> None:
    ROUNDS.extend([{"tool_calls": [call("t_ask", '{"text": "x"}')]}, ["on it"]])
    cid, rid = start({"t_ask": "ask"})
    row = wait_until(lambda: store.approvals(run_id=rid), "the approval card")[0]
    j("POST", f"/conversations/{cid}/steer", {"content": "no, use the other folder"})
    finished(rid)
    after = store.approval(row["call_id"])
    assert after["status"] == "denied" and after["decided_by"] == "steer" and after["note"] == "no, use the other folder"
    assert RAN == [], "the declined call never ran"
    res = [m for m in SEEN[1] if m["role"] == "tool"][0]
    assert "declined by the user, who said: no, use the other folder" in json.loads(res["content"])["error"]
    assert users(SEEN[1])[-1] == "no, use the other folder", "the next segment answers the steer"
    ev = tape(rid)
    assert [d["segment"] for e, d in ev if e == "done"] == [True, False]
    assert any(e == "tool_result" and not d.get("pending") for e, d in ev)


def test_breakers_start_clean_after_a_fold() -> None:
    bad = '{"text": "x'
    steered: list[bool] = []

    def inject(messages: list[dict[str, Any]]) -> dict[str, Any]:
        run = appmod.bus.get(cid_box[0])
        um = appmod.convos.add_message(cid_box[0], "user", "new direction")
        run.publish("user_message", um)
        run.steers.append(um)
        steered.append(True)
        # still blocked at this point: three failures in a row disabled the tool for the reply
        return {"tool_calls": [call("t_echo", '{"text": "blocked"}', "c4")]}

    ROUNDS.extend([{"tool_calls": [call("t_echo", bad, f"c{i}")]} for i in range(3)])
    ROUNDS.extend([inject, {"tool_calls": [call("t_echo", '{"text": "after the fold"}', "c5")]}, ["fin"]])
    cid_box = [""]
    cid = j("POST", "/conversations", {})["id"]
    cid_box[0] = cid
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"t_echo": "on"}}})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]
    finished(rid)
    assert steered
    assert RAN == [{"text": "after the fold"}], RAN


def test_a_steer_after_the_final_done_gets_a_409_even_while_teardown_runs() -> None:
    real = appmod.toolbox.shell.kill_conversation
    gate = {"entered": False}

    async def slow(conv_id: str) -> Any:
        gate["entered"] = True
        await asyncio.sleep(1.0)
        return await real(conv_id)

    appmod.toolbox.shell.kill_conversation = slow
    try:
        ROUNDS.append(["quick"])
        cid, rid = start()
        wait_until(lambda: any(e == "done" for e, _ in tape(rid)), "done on the tape")
        j("POST", f"/conversations/{cid}/steer", {"content": "too late"}, expect=409)
        finished(rid)
    finally:
        appmod.toolbox.shell.kill_conversation = real
    assert gate["entered"], "teardown still ran, after the done"


def test_a_call_cut_by_shutdown_is_saved_as_interrupted() -> None:
    ROUNDS.append({"tool_calls": [call("t_hang", "{}")]})
    cid, rid = start({"t_hang": "on"})
    wait_until(lambda: STATE["hang_started"], "the tool to start")
    run = appmod.bus.get(cid)
    run.task.get_loop().call_soon_threadsafe(run.task.cancel)
    wait_until(lambda: (m := j("GET", f"/conversations/{cid}")["messages"][-1]) and m["role"] == "assistant" and m.get("outcome") == "interrupted" and m,
               "the interrupted reply")
    msg = j("GET", f"/conversations/{cid}")["messages"][-1]
    ev = [t for t in msg["tool_events"] if t["name"] == "t_hang"]
    assert len(ev) == 1 and ev[0]["interrupted"] is True and ev[0]["pending"] is False
    assert "may or may not have completed" in ev[0]["error"]
