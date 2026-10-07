"""Hybrid memory retrieval: RRF fusion, optional embeddings, indexing, extractor candidates, fallback."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import learn  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import EmbedError, Embedder, rrf  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Documents, Graph, Memories  # noqa: E402

# Bag-of-words into 509 dims; synonyms share a bucket so 'boss' lands next to 'manager'.
SYN = {"boss": "manager", "supervisor": "manager"}
CFG = {"embeddingModel": "fake-embed", "hybridRetrieval": True}


def _vec(text: str) -> list[float]:
    v = [0.0] * 509
    for w in text.lower().replace("'", " ").replace("?", " ").split():
        w = SYN.get(w, w)
        v[sum(map(ord, w)) % 509] += 1.0
    return v


class Fake:
    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0
        self.fail = False

    async def __call__(self, settings: Any, texts: list[str], model: Any) -> list[list[float]]:
        self.calls += 1
        self.texts += len(texts)
        if self.fail:
            raise EmbedError("down")
        return [_vec(t) for t in texts]


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories, graph = Memories(db), Graph(db)
        fake = Fake()
        idx = MemoryIndex(db, memories, graph, Embedder(fake))
        yield db, memories, graph, idx, fake


def test_rrf_ordering_and_ties() -> None:
    out = rrf([["a", "b", "c"], ["b", "a"]])
    assert [i for i, _ in out][:2] == ["a", "b"]  # tie on score keeps first-seen order
    assert [i for i, _ in rrf([["a", "b"], ["c", "b"]])][0] == "b"  # agreement beats a single first place
    assert rrf([]) == []


def test_paraphrase_found_with_embeddings_and_fallback_without(env) -> None:
    db, memories, graph, idx, fake = env
    target = memories.create(None, "Priya is the user's manager", kind="fact", source="auto")
    for i in range(6):
        memories.create(None, f"Unrelated note number {i} about gardening", kind="note")
    q = "who is my boss"
    # BM25 alone cannot see it.
    assert target["id"] not in [m["id"] for m in memories.list(None, q)]
    assert asyncio.run(idx.index(CFG)) == 7
    qv = asyncio.run(idx.query_vec(CFG, q))
    assert qv is not None
    assert idx.search(None, q, qv, limit=3, settings=CFG)[0]["id"] == target["id"]
    # Embeddings off or failing: the same call degrades to lexical + graph only (nothing matches here), no raise.
    assert asyncio.run(idx.query_vec({"embeddingModel": ""}, q)) is None
    assert asyncio.run(idx.query_vec({**CFG, "hybridRetrieval": False}, q)) is None
    assert idx.search(None, q, None, limit=3, settings=CFG) == []  # no recency fill without a match
    fake.fail = True
    idx.embedder.reset()
    assert asyncio.run(idx.query_vec(CFG, q)) is None
    assert [m["id"] for m in idx.search(None, "manager", None, limit=3, settings=CFG)] == [target["id"]]


def test_index_idempotent_reembeds_and_cascades(env) -> None:
    db, memories, graph, idx, fake = env
    a = memories.create(None, "The user likes espresso", kind="preference")
    assert asyncio.run(idx.index(CFG)) == 1
    calls = fake.calls
    assert asyncio.run(idx.index(CFG)) == 0 and fake.calls == calls
    memories.update(a["id"], {"content": "The user likes green tea"})
    assert asyncio.run(idx.index(CFG)) == 1
    memories.delete(a["id"])
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) n FROM memory_vectors").fetchone()["n"] == 0


def test_invalid_rows_are_not_indexed_or_returned(env) -> None:
    db, memories, graph, idx, fake = env
    a = memories.create(None, "User lives in Austin", kind="fact")
    b = memories.supersede(a["id"], "User lives in Denver")
    assert asyncio.run(idx.index(CFG)) == 1
    qv = asyncio.run(idx.query_vec(CFG, "where does the user live"))
    assert [m["id"] for m in idx.search(None, "live", qv, settings=CFG)] == [b["id"]]


def test_trashed_rows_neither_ranked_nor_reembedded(env) -> None:
    db, memories, graph, idx, fake = env
    gone = memories.create(None, "User visited Austin in Austin", kind="fact")
    live = memories.create(None, "User lives in Austin now", kind="fact")
    with db.tx() as c:
        c.execute("UPDATE memories SET deleted_at=1 WHERE id=?", (gone["id"],))
    assert [m["id"] for m in idx.search(None, "Austin", limit=1)] == [live["id"]]
    assert idx.pending_count("fake-embed") == 1


def test_model_mismatch_ignored_then_reindexed(env) -> None:
    db, memories, graph, idx, fake = env
    m = memories.create(None, "Priya is the user's manager", kind="fact")
    asyncio.run(idx.index(CFG))
    other = {**CFG, "embeddingModel": "other-model"}
    with db.tx() as c:
        assert idx._cosine(c, "project_id IS NULL", [], _np(_vec("manager")), "other-model") == []
    assert idx.pending_count("other-model") == 1
    assert asyncio.run(idx.index(other)) == 1
    with db.tx() as c:
        assert c.execute("SELECT model FROM memory_vectors WHERE memory_id=?", (m["id"],)).fetchone()["model"] == "other-model"


def _np(v: list[float]):
    from personal_os.embed import normalize
    return normalize(v)


def test_learn_candidates_include_old_semantic_match(env, monkeypatch) -> None:
    db, memories, graph, idx, fake = env
    old = memories.create(None, "Priya is the user's manager", kind="fact", source="auto")
    for i in range(70):
        memories.create(None, f"Recent filler note {i}", kind="note")
    asyncio.run(idx.index(CFG, limit=500))
    captured: list[Any] = []

    async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **kw: Any) -> str:
        captured.extend(messages)
        return json.dumps({})

    monkeypatch.setattr(learn.llm, "complete", fake_complete)

    def run(index: Any) -> str:
        captured.clear()
        asyncio.run(learn.learn_from_exchange(
            settings=CFG, memories=memories, graph=graph, project_id=None,
            user_text="boss", assistant_text="ok", model="m", index=index))
        return captured[-1]["content"]

    assert "Priya is the user's manager" not in run(None)  # 60 most recent only: the old row is missed
    assert "Priya is the user's manager" in run(idx)
    assert old["id"]


def test_learn_indexes_new_memories(env, monkeypatch) -> None:
    db, memories, graph, idx, fake = env

    async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **kw: Any) -> str:
        return json.dumps({"memories": [{"content": "User drinks oat milk lattes", "kind": "preference"}]})

    monkeypatch.setattr(learn.llm, "complete", fake_complete)
    asyncio.run(learn.learn_from_exchange(settings=CFG, memories=memories, graph=graph, project_id=None,
                                          user_text="hi there", assistant_text="ok", model="m", index=idx))
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) n FROM memory_vectors").fetchone()["n"] == 1


def test_context_unchanged_without_memory_hits(env) -> None:
    db, memories, graph, idx, fake = env
    memories.create(None, "User prefers dark mode", kind="fact")
    kw = dict(memories=memories, graph=graph, documents=Documents(db), project=None, project_id=None,
              query="dark", settings={}, conv_settings={}, global_system_prompt="sys")
    base, used = build_context(**kw)
    same, _ = build_context(**kw, memory_hits=None)
    assert base == same and used["memories"]
    hits = [memories.create(None, "Priya is the user's manager", kind="fact")]
    swapped, used2 = build_context(**kw, memory_hits=hits)
    assert "Priya" in swapped and "dark mode" not in swapped and len(used2["memories"]) == 1
    assert fake.calls == 0  # building context never embeds
