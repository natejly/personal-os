"""Graph backfill: user messages only, idempotent, scoped, cancellable, retrying after an error."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import graph_backfill, graph_learn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.graph_backfill import BackfillRunning, GraphBackfill  # noqa: E402
from personal_os.repos import ALL, Conversations, Graph, Projects  # noqa: E402

REPLY = {"triples": [{"subject": "User", "predicate": "uses", "object": "Docker", "confidence": 0.9}]}


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(graph_backfill, "GRAPH_BACKFILL_DELAY_SECONDS", 0)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        yield db, Conversations(db), Graph(db)


def _fake(monkeypatch: Any, seen: list[list[dict[str, Any]]], fail: bool = False, hang: bool = False) -> None:
    async def complete(settings: Any, model: str, messages: Any, kind: str = "learn", **kw: Any) -> str:
        seen.append(messages)
        if hang:
            await asyncio.sleep(30)
        if fail:
            raise RuntimeError("model down")
        return json.dumps(REPLY)

    monkeypatch.setattr(graph_learn.llm, "complete", complete)


def _run(bf: GraphBackfill, **kw: Any) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        bf.start({}, "m", **kw)
        await bf.join()
        return bf.status()

    return asyncio.run(go())


def test_reads_user_messages_only_and_stamps_them(env, monkeypatch) -> None:
    db, convos, graph = env
    conv = convos.create(None, "t", "m")
    u = convos.add_message(conv["id"], "user", "I use Docker daily")
    convos.add_message(conv["id"], "assistant", "SECRET-ASSISTANT-WORDS about Kubernetes")
    seen: list[Any] = []
    _fake(monkeypatch, seen)
    st = _run(GraphBackfill(db, graph))
    assert st["done"] == 1 and st["total"] == 1 and st["errors"] == 0 and not st["running"]
    assert len(seen) == 1 and "SECRET-ASSISTANT-WORDS" not in json.dumps(seen) and "Assistant replied" not in seen[0][1]["content"]
    edge = graph.get(None)["edges"][0]
    assert edge["source_message_id"] == u["id"] and edge["valid_at"] == u["created_at"]


def test_second_run_processes_nothing(env, monkeypatch) -> None:
    db, convos, graph = env
    conv = convos.create(None, "t", "m")
    convos.add_message(conv["id"], "user", "I use Docker daily")
    seen: list[Any] = []
    _fake(monkeypatch, seen)
    bf = GraphBackfill(db, graph)
    _run(bf)
    again = _run(bf)
    assert again["total"] == 0 and again["done"] == 0 and len(seen) == 1
    assert GraphBackfill(db, graph).pending(None) == 0  # the marks are stored, not in memory


def test_ineligible_chats_are_skipped(env, monkeypatch) -> None:
    db, convos, graph = env
    private = convos.create(None, "p", "m", private=True)
    off = convos.create(None, "o", "m")
    convos.update(off["id"], {"settings": {"autoLearn": False}})
    muted = convos.create(None, "m", "m")
    convos.update(muted["id"], {"settings": {"learn": False}})  # the "don't learn from this chat" toggle
    gone = convos.create(None, "g", "m")
    tainted = convos.create(None, "t", "m")
    convos.update(tainted["id"], {"settings": {"tainted": True}})
    for c in (private, off, gone, tainted, muted):
        convos.add_message(c["id"], "user", "I use Docker daily")
    with db.tx() as c:
        c.execute("UPDATE conversations SET deleted_at=1 WHERE id=?", (gone["id"],))
    convos.add_message(convos.create(None, "ok", "m")["id"], "user", "   ")  # blank text is not a message to mine
    assert GraphBackfill(db, graph).pending(None) == 0


def test_scope_filter(env, monkeypatch) -> None:
    db, convos, graph = env
    pid = Projects(db).create("Work")["id"]
    convos.add_message(convos.create(None, "a", "m")["id"], "user", "I use Docker daily")
    convos.add_message(convos.create(pid, "b", "m")["id"], "user", "I use Rust daily")
    bf = GraphBackfill(db, graph)
    assert (bf.pending(None), bf.pending(pid), bf.pending(ALL)) == (1, 1, 2)
    seen: list[Any] = []
    _fake(monkeypatch, seen)
    st = _run(bf, project_id=pid)
    assert st["done"] == 1 and st["project_id"] == pid and "Rust" in json.dumps(seen) and "Docker" not in json.dumps(seen)
    assert bf.pending(None) == 1 and bf.pending(ALL) == 1  # the personal message is still waiting
    assert _run(bf, project_id=ALL)["done"] == 1


def test_limit_caps_the_run(env, monkeypatch) -> None:
    db, convos, graph = env
    conv = convos.create(None, "t", "m")
    for i in range(3):
        convos.add_message(conv["id"], "user", f"I use Docker {i}")
    _fake(monkeypatch, [])
    st = _run(GraphBackfill(db, graph), limit=2)
    assert st["total"] == 2 and st["done"] == 2 and GraphBackfill(db, graph).pending(None) == 1


def test_cancel_stops_the_run_and_leaves_messages_pending(env, monkeypatch) -> None:
    db, convos, graph = env
    conv = convos.create(None, "t", "m")
    for i in range(3):
        convos.add_message(conv["id"], "user", f"I use Docker {i}")
    _fake(monkeypatch, [], hang=True)
    bf = GraphBackfill(db, graph)

    async def go() -> None:
        bf.start({}, "m")
        await asyncio.sleep(0.05)
        with pytest.raises(BackfillRunning):
            bf.start({}, "m")
        assert bf.cancel() is True
        await bf.join()

    asyncio.run(go())
    st = bf.status()
    assert st["cancelled"] is True and not st["running"] and st["finished_at"] and st["done"] == 0
    assert bf.pending(None) == 3 and bf.cancel() is False


def test_an_error_leaves_the_message_unmarked_and_is_counted(env, monkeypatch) -> None:
    db, convos, graph = env
    conv = convos.create(None, "t", "m")
    convos.add_message(conv["id"], "user", "I use Docker daily")
    _fake(monkeypatch, [], fail=True)
    bf = GraphBackfill(db, graph)
    st = _run(bf)
    assert st["errors"] == 1 and st["done"] == 0 and bf.pending(None) == 1
    _fake(monkeypatch, [])  # the retry succeeds
    st = _run(bf)
    assert st["done"] == 1 and st["errors"] == 0 and bf.pending(None) == 0
