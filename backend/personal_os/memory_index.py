"""Hybrid memory retrieval: BM25, embedding similarity, recency/pin and graph-seed rankings fused with RRF.

Embeddings are optional and come from the shared embed.Embedder (the same route and back-off the document
retriever uses). Vectors live in `memory_vectors`, stored like retrieval.py stores chunk vectors: float32,
L2-normalised, so cosine is a dot product. With no embedding route every public call still works and ranks
on the lexical, recency and graph signals alone; nothing here raises into a chat turn.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

import numpy as np

from .db import Database
from .embed import Embedder, pack, rrf, unpack
from .repos import Graph, Memories, _scope_clause, fts_query

log = logging.getLogger("grain.memory_index")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_vectors (
  memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  content_hash TEXT NOT NULL,
  updated_at REAL NOT NULL
);
"""

INDEX_BATCH = 200       # memories embedded per index() call
VECTOR_CAP = 5000       # rows scanned by the brute-force cosine ranker
RANK_DEPTH = 50         # how deep each ranker reads
QUERY_TIMEOUT = 2.0     # seconds a chat turn waits for the query embedding


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


class MemoryIndex:
    def __init__(self, db: Database, memories: Memories, graph: Graph, embedder: Embedder | None = None):
        self.db, self.memories, self.graph = db, memories, graph
        self.embedder = embedder or Embedder()
        self._tasks: set[asyncio.Task[Any]] = set()
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------- settings ----------
    @staticmethod
    def enabled(settings: dict[str, Any]) -> bool:
        """Off with no embedding model, with hybridRetrieval false, or when retrievalMode forces keywords."""
        return (bool(settings.get("embeddingModel")) and bool(settings.get("hybridRetrieval", True))
                and settings.get("retrievalMode", "hybrid") != "bm25")

    # ---------- indexing ----------
    def _stale(self, model: str) -> list[Any]:
        with self.db.tx() as c:
            rows = c.execute(
                """SELECT m.id, m.content, v.content_hash, v.model FROM memories m
                   LEFT JOIN memory_vectors v ON v.memory_id=m.id WHERE m.invalid_at IS NULL""").fetchall()
        return [r for r in rows if r["model"] != model or r["content_hash"] != _hash(r["content"])]

    def pending_count(self, model: str) -> int:
        return len(self._stale(model))

    async def index(self, settings: dict[str, Any], memory_ids: list[str] | None = None, limit: int = INDEX_BATCH) -> int:
        """Embed live memories whose vector is missing, stale or from another model. Idempotent. Returns how many."""
        if not self.enabled(settings) or not self.embedder.available(settings):
            return 0
        model = self.embedder.model(settings)
        want = set(memory_ids) if memory_ids is not None else None
        todo = [r for r in self._stale(model) if want is None or r["id"] in want][:limit]
        if not todo:
            return 0
        vecs = await self.embedder.embed(settings, [r["content"] for r in todo])
        if vecs is None or len(vecs) != len(todo):
            return 0
        t = time.time()
        with self.db.tx() as c:
            for r, v in zip(todo, vecs):
                c.execute(
                    "INSERT OR REPLACE INTO memory_vectors(memory_id,model,dim,vec,content_hash,updated_at) VALUES(?,?,?,?,?,?)",
                    (r["id"], model, int(v.shape[0]), pack(v), _hash(r["content"]), t))
        return len(todo)

    def schedule(self, settings: dict[str, Any]) -> None:
        """Backfill in the background when something is waiting; never blocks or raises."""
        try:
            if not self.enabled(settings) or not self.embedder.available(settings):
                return
            if not self.pending_count(self.embedder.model(settings)):
                return
            task = asyncio.get_running_loop().create_task(self.index(settings))
        except RuntimeError:
            return  # no running loop (sync caller)
        except Exception:  # noqa: BLE001
            log.exception("memory index scheduling failed")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def query_vec(self, settings: dict[str, Any], query: str, timeout: float = QUERY_TIMEOUT) -> np.ndarray | None:
        """The query's embedding, or None (off, route down, too slow, or an empty query)."""
        if not query.strip() or not self.enabled(settings) or not self.embedder.available(settings):
            return None
        try:
            vecs = await asyncio.wait_for(self.embedder.embed(settings, [query[:2000]]), timeout)
        except Exception:  # noqa: BLE001 - timeouts included
            return None
        return vecs[0] if vecs else None

    # ---------- rankers ----------
    def _bm25(self, c: Any, where: str, args: list[Any], query: str) -> list[str]:
        fq = fts_query(query)
        if not fq:
            return []
        rows = c.execute(
            f"""SELECT m.id FROM memories_fts f JOIN memories m ON m.id=f.memory_id
                WHERE memories_fts MATCH ? AND {where.replace('project_id', 'm.project_id')} AND m.invalid_at IS NULL
                ORDER BY bm25(memories_fts) LIMIT ?""", (fq, *args, RANK_DEPTH)).fetchall()
        return [r["id"] for r in rows]

    def _cosine(self, c: Any, where: str, args: list[Any], qvec: np.ndarray | None, model: str) -> list[str]:
        if qvec is None or not model:
            return []
        rows = c.execute(
            f"""SELECT v.memory_id, v.vec, v.dim FROM memory_vectors v JOIN memories m ON m.id=v.memory_id
                WHERE v.model=? AND m.invalid_at IS NULL AND {where.replace('project_id', 'm.project_id')} LIMIT ?""",
            (model, *args, VECTOR_CAP)).fetchall()
        scored = [(float(unpack(r["vec"]) @ qvec), r["memory_id"]) for r in rows if r["dim"] == qvec.shape[0]]
        scored.sort(key=lambda s: -s[0])
        return [mid for _, mid in scored[:RANK_DEPTH]]

    def _recent(self, c: Any, where: str, args: list[Any]) -> list[str]:
        rows = c.execute(f"SELECT id FROM memories WHERE {where} AND invalid_at IS NULL ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                         (*args, RANK_DEPTH)).fetchall()
        return [r["id"] for r in rows]

    def _graph_seeded(self, c: Any, where: str, args: list[Any], project_id: str | None, query: str) -> list[str]:
        labels = [n["label"].lower() for n in self.graph.neighborhood(project_id, query)["nodes"] if len(n["label"]) > 2]
        if not labels:
            return []
        rows = c.execute(f"SELECT id, content FROM memories WHERE {where} AND invalid_at IS NULL ORDER BY updated_at DESC", args).fetchall()
        return [r["id"] for r in rows if any(lb in r["content"].lower() for lb in labels)][:RANK_DEPTH]

    def search(self, project_id: str | None, query: str, query_vec: np.ndarray | None = None, limit: int = 20,
               settings: dict[str, Any] | None = None, include_global: bool = True) -> list[dict[str, Any]]:
        """Memories ranked by RRF over BM25, cosine, recency/pin and graph-seed rankings. Never raises."""
        try:
            where, args = _scope_clause(project_id, include_global)
            model = self.embedder.model(settings) if settings is not None else ""
            with self.db.tx() as c:
                lists = [self._bm25(c, where, args, query),
                         self._cosine(c, where, args, query_vec, model),
                         self._recent(c, where, args),
                         self._graph_seeded(c, where, args, project_id, query)]
            # Recency is a tiebreaker-weight signal: it must not outvote an actual match.
            ids = [i for i, _ in rrf(lists, weights=[1.0, 1.0, 0.5, 0.7])][:limit]
        except Exception:  # noqa: BLE001 - retrieval must never break a reply
            log.exception("memory search failed")
            return []
        rows = {m["id"]: m for m in (self.memories.get(i) for i in ids) if m}
        return [rows[i] for i in ids if i in rows]

    def candidates(self, project_id: str | None, query: str, query_vec: np.ndarray | None, settings: dict[str, Any],
                   limit: int = 40, top: int = 20) -> list[dict[str, Any]]:
        """The extractor's reconciliation set: the top hybrid hits first, then pinned/recent rows up to `limit`."""
        out: dict[str, dict[str, Any]] = {}
        for m in self.search(project_id, query, query_vec, limit=top, settings=settings):
            out[m["id"]] = m
        for m in self.memories.for_context(project_id, query, limit=limit):
            if len(out) >= limit:
                break
            out.setdefault(m["id"], m)
        return list(out.values())
