"""Developer-mode model duels: each side runs its whole turn in a hidden copy of the chat, a call that would change the
outside world is held, and only the picked side is persisted. Files and the tool gate: test_full_duel.py.

Also the votes route, the Elo math and migration 38. Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_model_duel.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="duel-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import duel, llm, migrations  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
appmod.db.set_settings({"hiddenViews": []})  # a fresh install has Lists off, and its routes answer 404
CALLS: list[str] = []  # the model of every stream_chat call, in order


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    CALLS.append(model)
    after_tool = any(m.get("role") == "tool" for m in messages)
    if model == "duel-a" and not after_tool:
        yield {"type": "delta", "text": "A will add it"}
        yield {"type": "end", "finish_reason": "tool_calls", "usage": {"prompt_tokens": 5, "completion_tokens": 7},
               "tool_calls": [{"id": "call_a", "name": "todo_add", "arguments": json.dumps({"title": "from-a"})}]}
    elif model == "duel-b" and not after_tool:
        await asyncio.sleep(0.05)
        yield {"type": "delta", "text": "B answer" if CALLS.count("duel-b") == 1 else "B again"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    else:
        yield {"type": "delta", "text": "A done"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "devTools": True, "permissionMode": "allow_all"})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _clean():  # type: ignore[no-untyped-def]
    CALLS.clear()
    client.delete("/model-votes")
    yield


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def start(content: str, ab: str | None = "duel-b") -> tuple[str, str]:
    cid = client.post("/conversations", json={}).json()["id"]
    body: dict[str, Any] = {"content": content, "model": "duel-a"}
    if ab:
        body["ab_model"] = ab
    rid = client.post(f"/conversations/{cid}/chat", json=body).json()["run_id"]
    return cid, rid


def todos_from_a() -> list[Any]:
    return [t for t in client.get("/todos").json() if "from-a" in json.dumps(t)]


def assistant_rows(cid: str) -> list[dict[str, Any]]:
    return [m for m in client.get(f"/conversations/{cid}").json()["messages"] if m["role"] == "assistant"]


def wait_done(cid: str) -> None:
    wait_until(lambda: not appmod.bus.answering(cid), f"the run to finish; cards: {client.get('/approvals').json()}")


def test_duel_runs_no_tool_and_picked_b_is_the_only_message() -> None:
    cid, _ = start("add a todo")
    mid = wait_until(lambda: next(iter(appmod._ab_waits), None), "the duel to wait for a pick")
    assert sorted(CALLS) == ["duel-a", "duel-a", "duel-b"]  # both sides ran their whole turn: A asked for a tool, then answered
    assert todos_from_a() == []  # A asked for todo_add; the duel held it, not ran it
    assert [m["content"] for m in assistant_rows(cid)] == [""]  # nothing committed yet
    assert client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "b"}).status_code == 200
    wait_done(cid)
    rows = assistant_rows(cid)
    assert [m["content"] for m in rows] == ["B answer"] and rows[0]["model"] == "duel-b"
    assert todos_from_a() == [] and CALLS.count("duel-a") == 2  # A's call never ran, and A was not asked again
    assert not [c for c in client.get("/conversations").json() if c["id"] != cid]  # the hidden side copies are gone
    v = client.get("/model-votes").json()
    assert v["votes"] == 1 and {m["model"]: m["wins"] for m in v["models"]} == {"duel-b": 1, "duel-a": 0}


def test_regenerate_restreams_one_side_then_the_pick_uses_it() -> None:
    cid, _ = start("add a todo")
    mid = wait_until(lambda: next(iter(appmod._ab_waits), None), "the duel to wait for a pick")
    assert client.post(f"/conversations/{cid}/ab/regenerate", json={"message_id": mid, "side": "x"}).status_code == 400
    assert client.post(f"/conversations/{cid}/ab/regenerate", json={"message_id": mid, "side": "b"}).status_code == 200
    wait_until(lambda: CALLS.count("duel-b") == 2 and mid in appmod._ab_waits and not appmod._ab_waits[mid].done(), "the duel to wait again")
    assert sorted(CALLS) == ["duel-a", "duel-a", "duel-b", "duel-b"]  # only B ran again; A was not asked
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "b"})
    wait_done(cid)
    assert [m["content"] for m in assistant_rows(cid)] == ["B again"] and todos_from_a() == []


def test_picking_a_runs_its_tool_call_once() -> None:
    cid, _ = start("add a todo")
    mid = wait_until(lambda: next(iter(appmod._ab_waits), None), "the duel to wait for a pick")
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "tie"})
    wait_done(cid)
    assert len(todos_from_a()) == 1  # the loop ran A's held call, once, after the pick
    assert CALLS.count("duel-a") == 3 and CALLS.count("duel-b") == 1  # A's two side rounds, then one follow-up round on the real chat
    assert assistant_rows(cid)[-1]["content"].endswith("A done")
    assert client.get("/model-votes").json()["models"][0]["ties"] == 1


def test_no_duel_without_dev_tools_or_ab_model() -> None:
    cid, _ = start("hello", ab=None)
    wait_done(cid)
    assert "duel-b" not in CALLS and not appmod._ab_waits
    client.put("/settings", json={"devTools": False})
    try:
        cid, _ = start("add a todo")
        wait_done(cid)  # no duel to wait on: the run goes straight through A
        assert "duel-b" not in CALLS
    finally:
        client.put("/settings", json={"devTools": True})


def test_pick_with_no_duel_is_404_and_bad_winner_400() -> None:
    assert client.post("/conversations/x/ab/pick", json={"message_id": "nope", "winner": "a"}).status_code == 404
    assert client.post("/conversations/x/ab/pick", json={"message_id": "nope", "winner": "z"}).status_code == 400


def test_elo_and_standings() -> None:
    def v(a: str, b: str, w: str, ams: int = 100, bms: int = 300) -> dict[str, Any]:
        return {"model_a": a, "model_b": b, "winner": w, "a_ms": ams, "b_ms": bms}
    assert duel.elo([v("x", "y", "a")]) == {"x": 1016.0, "y": 984.0}  # K=32, equal ratings: the winner takes 16
    assert duel.elo([v("x", "y", "tie")]) == {"x": 1000.0, "y": 1000.0}
    assert duel.elo([v("x", "y", "bad")]) == {}
    rows = {r["model"]: r for r in duel.standings([v("x", "y", "a"), v("y", "x", "tie"), v("x", "y", "b")])}
    assert (rows["x"]["wins"], rows["x"]["losses"], rows["x"]["ties"]) == (1, 1, 1)
    assert rows["x"]["win_rate"] == 0.5 and rows["x"]["mean_ms"] == 167 and rows["y"]["mean_ms"] == 233


def test_votes_route_round_trip_and_reset() -> None:
    for w in ("a", "a", "b"):
        appmod.model_votes.add(conversation_id="c", model_a="m1", model_b="m2", winner=w, prompt="p" * 500, a_ms=10, b_ms=30, a_tokens=1, b_tokens=2)
    body = client.get("/model-votes").json()
    m1 = next(m for m in body["models"] if m["model"] == "m1")
    assert body["votes"] == 3 and (m1["wins"], m1["losses"]) == (2, 1) and m1["elo"] > 1000 and m1["mean_ms"] == 10
    assert len(appmod.model_votes.all()[0]["prompt_excerpt"]) == duel.EXCERPT_CHARS
    client.delete("/model-votes")
    assert client.get("/model-votes").json() == {"votes": 0, "models": []}


def test_migration_38_creates_model_votes() -> None:
    assert dict((v, n) for v, n, _ in migrations.MIGRATIONS)[38] == "model_votes"
    c = sqlite3.connect(":memory:")
    migrations._model_votes(c)  # the step alone; every other test applies the whole chain through Database
    c.execute("INSERT INTO model_votes(created_at, model_a, model_b, winner) VALUES (1, 'a', 'b', 'tie')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO model_votes(created_at, model_a, model_b, winner) VALUES (1, 'a', 'b', 'nope')")


def _desk() -> tuple[str, str]:
    """A fresh asleep desk on its own chat, the way a new autonomous chat makes one."""
    cid = client.post("/conversations", json={}).json()["id"]
    appmod.convos.update(cid, {"model": "duel-a"})
    out = client.post("/cowork/desks", json={"conversation_id": cid, "brief": "do it", "autonomy": "ask", "start": False}).json()
    return out["desk"]["id"], cid


def test_desk_message_with_ab_model_duels_then_the_desk_continues_after_the_pick() -> None:
    did, cid = _desk()
    before = len(todos_from_a())
    assert client.post(f"/cowork/desks/{did}/message", json={"content": "add a todo", "ab_model": "duel-b"}).status_code == 200
    mid = wait_until(lambda: next(iter(appmod._ab_waits), None), "the desk's duel to wait for a pick")
    assert sorted(CALLS) == ["duel-a", "duel-a", "duel-b"] and len(todos_from_a()) == before  # the desk's own state stays put while the sides work
    assert appmod.bus.answering(cid).status == "awaiting_approval"  # parked like a pending card: no idle clock may cut it
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "tie"})
    wait_until(lambda: not appmod.bus.answering(cid), "the desk turn to finish")
    assert len(todos_from_a()) == before + 1  # the loop went on with A's call, autonomously
    assert client.get("/model-votes").json()["votes"] == 1


def test_internal_desk_turns_never_duel() -> None:
    did, cid = _desk()
    body = appmod.ChatIn(content="go on", kind="continue", ab_model="duel-b")
    async def _start() -> None:
        appmod.bus.start(cid, lambda r: appmod._run_desk(r, did, body), kind="desk", desk_id=did, turn=0, input={"content": "go on"})

    asyncio.run_coroutine_threadsafe(_start(), appmod._loop).result(5)
    wait_until(lambda: not appmod.bus.answering(cid), "the hidden turn to finish")
    assert "duel-b" not in CALLS and not appmod._ab_waits
