"""Non-destructive memory: supersession, soft forget, provenance, edge validity, migration."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import learn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402


@pytest.fixture()
def stores():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        yield db, Memories(db), Graph(db)


def _fts_ids(db: Database) -> set[str]:
    with db.tx() as c:
        return {r["memory_id"] for r in c.execute("SELECT memory_id FROM memories_fts")}


def _learn(memories: Memories, graph: Graph, reply: dict[str, Any], monkeypatch: Any, captured: list | None = None, **kw: Any) -> dict[str, Any]:
    async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **kw: Any) -> str:
        if captured is not None:
            captured.extend(messages)
        return json.dumps(reply)

    monkeypatch.setattr(learn.llm, "complete", fake_complete)
    return asyncio.run(learn.learn_from_exchange(
        settings={}, memories=memories, graph=graph, project_id=None,
        user_text="hi", assistant_text="hello", model="m", **kw,
    ))


def test_supersede_keeps_history_and_hides_old_row(stores) -> None:
    db, memories, _ = stores
    old = memories.create(None, "User prefers dark mode", kind="preference", source="auto")
    new = memories.supersede(old["id"], "User prefers light mode")
    assert new and new["id"] != old["id"] and new["kind"] == "preference"
    o = memories.get(old["id"])
    assert o["invalid_at"] is not None and o["superseded_by"] == new["id"]
    assert [m["id"] for m in memories.list(None)] == [new["id"]]
    assert [m["id"] for m in memories.for_context(None, "dark mode")] == [new["id"]]
    assert memories.list(None, q="dark") == []
    assert old["id"] not in _fts_ids(db)
    assert {m["id"] for m in memories.list(None, include_invalid=True)} == {old["id"], new["id"]}
    third = memories.supersede(new["id"], "User prefers auto theme")
    assert [m["id"] for m in memories.history(new["id"])] == [old["id"], new["id"], third["id"]]
    assert [m["id"] for m in memories.history(third["id"])] == [old["id"], new["id"], third["id"]]


def test_restore_revives_and_reindexes(stores) -> None:
    db, memories, _ = stores
    old = memories.create(None, "User prefers dark mode", kind="preference")
    new = memories.supersede(old["id"], "User prefers light mode")
    back = memories.restore(old["id"])
    assert back["invalid_at"] is None and back["superseded_by"] is None
    assert old["id"] in _fts_ids(db) and new["id"] not in _fts_ids(db)
    assert [m["id"] for m in memories.list(None, q="dark")] == [old["id"]]
    assert memories.get(new["id"])["invalid_at"] is not None  # the replacement steps aside


def test_learn_updates_supersede_and_forget_invalidates(stores, monkeypatch) -> None:
    db, memories, graph = stores
    a = memories.create(None, "User prefers dark mode", kind="preference", source="auto")
    b = memories.create(None, "User is learning French", kind="goal")
    pinned = memories.create(None, "User is vegetarian", kind="fact", pinned=True)
    tags = {m["id"]: f"M{i + 1}" for i, m in enumerate(memories.for_context(None, "hi", limit=60))}
    out = _learn(memories, graph, {
        "updates": [{"id": tags[a["id"]], "content": "User prefers light mode"}],
        "forget": [tags[b["id"]], tags[pinned["id"]]],
    }, monkeypatch)
    assert out["superseded"][0]["old_id"] == a["id"]
    assert memories.get(a["id"]) is not None and memories.get(a["id"])["invalid_at"] is not None
    assert memories.get(b["id"])["invalid_at"] is not None  # soft, not deleted
    assert [x["id"] for x in out["invalidated"]] == [b["id"]]
    assert memories.get(pinned["id"])["invalid_at"] is None
    assert sorted(m["content"] for m in memories.list(None)) == ["User is vegetarian", "User prefers light mode"]


def test_provenance_on_learned_memories(stores, monkeypatch) -> None:
    _, memories, graph = stores
    out = _learn(memories, graph, {"memories": [{"content": "User runs marathons", "kind": "fact"}]}, monkeypatch,
                 conversation_id="c1", message_id="m1")
    m = out["memories"][0]
    assert m["source_conversation_id"] == "c1" and m["source_message_id"] == "m1" and m["valid_from"]


def test_edge_ended_and_replaced_then_revived(stores, monkeypatch) -> None:
    _, memories, graph = stores
    nate, acme = graph.upsert_node(None, "Nate", "person"), graph.upsert_node(None, "Acme", "organization")
    graph.upsert_edge(None, nate["id"], acme["id"], "works at")
    out = _learn(memories, graph, {
        "entities": [{"label": "Beta", "type": "organization"}, {"label": "Nate", "type": "person"}],
        "relations": [{"source": "Nate", "target": "Beta", "relation": "works at"}],
        "ended": [{"source": "Nate", "target": "Acme", "relation": "works at"}],
    }, monkeypatch, message_id="m2")
    assert [e["target_id"] for e in out["ended"]] == [acme["id"]]
    sub = graph.neighborhood(None, "Nate")
    labels = {n["id"]: n["label"] for n in sub["nodes"]}
    assert [labels[e["target_id"]] for e in sub["edges"]] == ["Beta"]
    assert sub["edges"][0]["source_message_id"] == "m2"
    assert len(graph.get(None)["edges"]) == 1 and len(graph.get(None, include_invalid=True)["edges"]) == 2
    # re-asserting revives the old row instead of adding a duplicate
    e = graph.upsert_edge(None, nate["id"], acme["id"], "works at")
    assert e["invalid_at"] is None
    assert len(graph.get(None, include_invalid=True)["edges"]) == 2 and len(graph.get(None)["edges"]) == 2


def test_replaces_invalidates_old_edge(stores, monkeypatch) -> None:
    _, memories, graph = stores
    nate, acme = graph.upsert_node(None, "Nate"), graph.upsert_node(None, "Acme")
    old = graph.upsert_edge(None, nate["id"], acme["id"], "lives near")
    _learn(memories, graph, {"relations": [{"source": "Nate", "target": "Beta", "relation": "lives near", "replaces": "Nate|lives near|Acme"}]}, monkeypatch)
    row = next(e for e in graph.get(None, include_invalid=True)["edges"] if e["id"] == old["id"])
    assert row["invalid_at"] is not None and row["superseded_by"]


def test_migration_adds_columns_idempotently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        Database(tmp)
        path = Path(tmp) / "personal-os.db"
        c = sqlite3.connect(path)
        # rebuild the tables the way the first release had them
        c.executescript("""
            DROP INDEX IF EXISTS idx_mem_valid;
            ALTER TABLE memories DROP COLUMN valid_from; ALTER TABLE memories DROP COLUMN invalid_at;
            ALTER TABLE memories DROP COLUMN superseded_by; ALTER TABLE memories DROP COLUMN source_conversation_id;
            ALTER TABLE memories DROP COLUMN source_message_id;
            ALTER TABLE kg_edges DROP COLUMN valid_at; ALTER TABLE kg_edges DROP COLUMN invalid_at;
            ALTER TABLE kg_edges DROP COLUMN superseded_by; ALTER TABLE kg_edges DROP COLUMN source_message_id;
            ALTER TABLE kg_edges DROP COLUMN fact;
        """)
        t = time.time()
        c.execute("INSERT INTO memories(id,content,kind,source,pinned,created_at,updated_at) VALUES('old1','User likes tea','fact','user',0,?,?)", (t, t))
        c.commit()
        c.close()
        db = Database(tmp)
        Database(tmp)  # second open is a no-op
        m = Memories(db)
        row = m.get("old1")
        assert row["invalid_at"] is None and row["valid_from"] is None
        assert [x["id"] for x in m.list(None)] == ["old1"]
        with db.tx() as cc:
            cols = {r["name"] for r in cc.execute("PRAGMA table_info(kg_edges)")}
        assert {"valid_at", "invalid_at", "superseded_by", "source_message_id", "fact"} <= cols


def test_prompt_carries_todays_date(stores, monkeypatch) -> None:
    _, memories, graph = stores
    cap: list = []
    _learn(memories, graph, {}, monkeypatch, captured=cap)
    assert time.strftime("%Y-%m-%d") in cap[0]["content"] and "absolute" in cap[0]["content"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
