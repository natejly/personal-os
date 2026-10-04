"""Stop reaches into the tool phase: a running call is raced against Stop, the rest of the round is skipped.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_stop_tools.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="stoptools-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
appmod.db.set_settings({"toolDeferAbove": 0})  # these tests drive their own tools; deferral is test_tool_search.py
from personal_os import llm  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []
STATE: dict[str, Any] = {"counter": 0, "cancelled": False, "calls": []}
NAMES = ("t_hang_safe", "t_hang_write", "t_bump_write", "t_slow_write")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    STATE["calls"].append(1)
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _hang(ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        STATE["cancelled"] = True
        raise
    return {"ok": True}


async def _bump(ctx: dict[str, Any]) -> dict[str, Any]:
    STATE["counter"] += 1
    return {"ok": True}


async def _slow(ctx: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.5)
    return {"ok": True, "slow": True}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    obj = {"type": "object", "properties": {}}
    for name, fn, danger in (("t_hang_safe", _hang, "safe"), ("t_hang_write", _hang, "writes"),
                             ("t_bump_write", _bump, "writes"), ("t_slow_write", _slow, "writes")):
        appmod.toolbox.specs[name] = ToolSpec(name, name, obj, fn, "test", danger)
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
    STATE.update(counter=0, cancelled=False, calls=[])
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


def tape(run_id: str) -> list[tuple[int, str, Any]]:
    return store.events(run_id)


def call(name: str, cid: str) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps({})}


def start(tools: list[str]) -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {t: "on" for t in tools}}})
    return cid, j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]


def drain(run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(run_id)) and r["status"] not in ("running", "awaiting_approval") and r,
                      f"run {run_id} to finish", timeout)


def results(run_id: str) -> dict[str, dict[str, Any]]:
    return {d["name"]: d for _, e, d in tape(run_id) if e == "tool_result"}


def wait_called(run_id: str) -> None:
    wait_until(lambda: any(e == "tool_call" for _, e, _ in tape(run_id)), "the tool_call event")
    time.sleep(0.1)  # let the call get into its await


def test_stop_during_a_read_tool_interrupts_it_and_skips_the_rest() -> None:
    ROUNDS.append({"tool_calls": [call("t_hang_safe", "a"), call("t_bump_write", "b")]})
    cid, rid = start(["t_hang_safe", "t_bump_write"])
    wait_called(rid)
    t0 = time.time()
    j("POST", f"/conversations/{cid}/stop")
    row = drain(rid, 6)
    assert time.time() - t0 < 4, "Stop took effect inside the tool, not after it"
    assert row["status"] == "done"
    res = results(rid)
    assert res["t_hang_safe"]["interrupted"] is True and res["t_hang_safe"]["error"], res
    assert "t_bump_write" not in res and STATE["counter"] == 0, "later calls never ran"
    assert not any(e == "tool_call" and d["name"] == "t_bump_write" for _, e, d in tape(rid)), "no card opened for a skipped call"
    assert STATE["cancelled"] is True, "the tool saw its cancellation"
    done = [d for _, e, d in tape(rid) if e == "done"][-1]
    assert done["stopped"] is True
    assert len(STATE["calls"]) == 1, "no final round after a Stop"
    msg = j("GET", f"/conversations/{cid}")["messages"][-1]
    assert all(not t.get("pending") for t in msg["tool_events"]) and msg["tool_events"][0]["interrupted"] is True
    j("POST", f"/conversations/{cid}/chat", {"content": "again"})  # not 409: the run is over


def test_a_write_cut_off_by_stop_leaves_its_journal_row_started() -> None:
    ROUNDS.append({"tool_calls": [call("t_hang_write", "a")]})
    cid, rid = start(["t_hang_write"])
    wait_called(rid)
    t0 = time.time()
    j("POST", f"/conversations/{cid}/stop")
    drain(rid, 10)
    took = time.time() - t0
    assert appmod.STOP_GRACE_SECONDS - 0.5 <= took < 7, took
    r = results(rid)["t_hang_write"]
    assert r["interrupted"] is True and "unknown" in r["error"]
    rows = [x for x in store.executed(rid) if x["tool"] == "t_hang_write"]
    assert rows and all(x["status"] == "started" for x in rows), rows


def test_a_write_finishing_inside_the_grace_reports_its_real_result() -> None:
    ROUNDS.append({"tool_calls": [call("t_slow_write", "a")]})
    cid, rid = start(["t_slow_write"])
    wait_called(rid)
    j("POST", f"/conversations/{cid}/stop")
    drain(rid)
    r = results(rid)["t_slow_write"]
    assert not r.get("error") and not r.get("interrupted"), r
    rows = [x for x in store.executed(rid) if x["tool"] == "t_slow_write"]
    assert rows and rows[0]["status"] == "done", rows


def test_await_tool_helper_without_a_run() -> None:
    async def go() -> list[Any]:
        stop = asyncio.Event()
        ran: list[int] = []

        async def quick() -> int:
            ran.append(1)
            return 7

        first = await appmod._await_tool(quick(), stop, grace=0)
        stop.set()
        second = await appmod._await_tool(quick(), stop, grace=0)  # already stopped: never started
        t0 = time.time()
        third = await appmod._await_tool(quick(), stop, grace=3)  # a write is not started after Stop either, and no grace is spent
        return [first, second, third, len(ran), time.time() - t0]

    first, second, third, n, took = asyncio.run(go())
    assert first == (7, False) and second == (None, True) and third == (None, True) and n == 1
    assert took < 0.5, took


def test_cancelling_the_run_cancels_the_tool_and_runs_its_cleanup() -> None:
    """Shutdown path: the helper forwards the cancel to the call and waits for its cleanup before re-raising."""
    async def go() -> bool:
        seen = {"c": False}

        async def body() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                seen["c"] = True
                raise

        outer = asyncio.ensure_future(appmod._await_tool(body(), asyncio.Event(), grace=0))
        await asyncio.sleep(0.05)
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
        return seen["c"]

    assert asyncio.run(go()) is True
