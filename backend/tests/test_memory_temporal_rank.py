"""A date phrase in the query is a ranking signal for memory search: it brings on-topic rows from the window in,
nudges rows by closeness to it, and changes nothing when there is no phrase. Fixed clock, fake embedder."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import temporal_query  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import Embedder  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402
from test_memory_index import CFG, Fake  # noqa: E402

NOW = datetime.fromisoformat("2026-10-07T12:00:00-04:00")  # "last month" = September 2026
RERANK = {"provider": "fireworks", "memoryRerank": True}


def at(iso: str) -> float:
    return datetime.fromisoformat(iso + "T12:00:00-04:00").timestamp()


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories = Memories(db)
        yield db, memories, MemoryIndex(db, memories, Graph(db), Embedder(Fake()))


def add(db: Database, memories: Memories, text: str, day: str) -> str:
    m = memories.create(None, text, kind="note")
    with db.tx() as c:
        c.execute("UPDATE memories SET created_at=?, updated_at=?, valid_from=? WHERE id=?", (at(day), at(day), at(day), m["id"]))
    return m["id"]


def search(idx: MemoryIndex, q: str, **kw: Any) -> list[str]:
    qv = asyncio.run(idx.query_vec(CFG, q))
    return [m["id"] for m in idx.search(None, q, qv, settings=CFG, now=NOW, **kw)]


def test_window_ranks_in_window_row_above_newer_out_of_window_row(env) -> None:
    db, memories, idx = env
    old = add(db, memories, "Decided to use Postgres for the billing service", "2026-09-15")
    new = add(db, memories, "Billing service decided: SQLite, the billing service decided it is final", "2026-10-05")  # the better text match, and newer
    asyncio.run(idx.index(CFG))
    assert search(idx, "billing service decided")[0] == new  # newer and the better match without a date phrase
    assert search(idx, "billing service decided last month")[:2] == [old, new]


def test_unrelated_in_window_row_is_not_brought_in(env) -> None:
    db, memories, idx = env
    add(db, memories, "Decided to use Postgres for the billing service", "2026-09-15")
    cactus = add(db, memories, "Water the cactus on Sundays", "2026-09-10")
    asyncio.run(idx.index(CFG))
    assert cactus not in search(idx, "billing service decided last month")


def test_no_window_leaves_search_untouched(env, monkeypatch) -> None:
    db, memories, idx = env
    for i, day in enumerate(["2026-08-01", "2026-09-15", "2026-10-05"]):
        add(db, memories, f"Billing note {i} about invoices", day)
    asyncio.run(idx.index(CFG))
    first = search(idx, "billing invoices")

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("temporal code ran without a date phrase")

    monkeypatch.setattr(MemoryIndex, "_temporal", boom)
    monkeypatch.setattr(MemoryIndex, "_proximity", boom)
    assert search(idx, "billing invoices") == first
    qv = asyncio.run(idx.query_vec(CFG, "billing invoices"))
    other = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    assert [m["id"] for m in idx.search(None, "billing invoices", qv, settings=CFG, now=other)] == first


def test_no_embeddings_window_only_lifts_lexical_hits(env) -> None:
    db, memories, idx = env
    hit = add(db, memories, "Decided to use Postgres for the billing service", "2026-09-15")
    cactus = add(db, memories, "Water the cactus on Sundays", "2026-09-10")
    got = [m["id"] for m in idx.search(None, "billing last month", None, settings=CFG, now=NOW)]
    assert hit in got and cactus not in got


def test_rerank_scores_get_the_same_window_nudge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories = Memories(db)
        idx = MemoryIndex(db, memories, Graph(db), Embedder())
        far = [add(db, memories, f"Tomato note {i} about gardening", "2026-06-01") for i in range(3)]
        inside = add(db, memories, "Tomato note 9 about gardening", "2026-09-10")

        async def stub(settings: Any, model: str, query: str, docs: list[str], *, timeout: float, top_n: int) -> Any:
            scores = [(i, 0.9 if "note 9" not in d else 0.8) for i, d in enumerate(docs)]  # the model prefers the old rows
            return sorted(scores, key=lambda s: -s[1])

        idx.rerank_fn = stub
        plain = asyncio.run(idx.search_reranked(None, "tomato gardening", settings=RERANK, now=NOW))
        dated = asyncio.run(idx.search_reranked(None, "tomato gardening last month", settings=RERANK, now=NOW))
        assert plain[-1]["id"] == inside  # lowest model score, no window: the reranker order stands
        assert dated[0]["id"] == inside and dated[0]["rerank_score"] == 0.8  # window nudge beats a 0.1 gap; raw score kept
        assert {m["id"] for m in dated} == {*far, inside}


def test_graph_list_ranks_seed_memories_above_neighbour_memories(env) -> None:
    db, memories, idx = env
    seed = add(db, memories, "Priya joined the platform team", "2026-08-01")
    neighbour = add(db, memories, "Initech moved offices again", "2026-10-05")  # newer, but only names the neighbour
    a = idx.graph.upsert_node(None, "Priya", "person", {})
    b = idx.graph.upsert_node(None, "Initech", "org", {})
    idx.graph.upsert_edge(None, a["id"], b["id"], "works_at", valid_at=at("2026-08-01"))
    with db.tx() as c:
        assert idx._graph_seeded(c, "project_id IS NULL", [], None, "what is Priya up to") == [seed, neighbour]
