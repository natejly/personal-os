"""Hybrid memory retrieval: BM25, embedding similarity and graph-seed rankings fused with RRF, gated by relevance.

A row is returned only if it matched (a lexical hit, a cosine at or above the floor, or a graph seed); recency
only orders matched rows. No match, no rows: the always-on standing preferences live in the profile instead.

Embeddings are optional and come from the shared embed.Embedder (the same route and back-off the document
retriever uses). Vectors live in `memory_vectors`, stored like retrieval.py stores chunk vectors: float32,
L2-normalised, so cosine is a dot product. With no embedding route every public call still works and ranks
on the lexical and graph signals alone; nothing here raises into a chat turn.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any

import numpy as np

from . import memory_limits as ml
from .db import Database
from . import graph_recall
from .embed import Embedder, pack, rrf, unpack
from .repos import Graph, Memories, _scope_clause, live_mem

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


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


class MemoryIndex:
    def __init__(self, db: Database, memories: Memories, graph: Graph, embedder: Embedder | None = None):
        self.db, self.memories, self.graph = db, memories, graph
        self.embedder = embedder or Embedder()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.recall: Any = None  # GraphRecall, set by app: node vectors for graph-seeded ranking
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
                f"""SELECT m.id, m.content, v.content_hash, v.model FROM memories m
                   LEFT JOIN memory_vectors v ON v.memory_id=m.id WHERE {live_mem('m')}""").fetchall()
        return [r for r in rows if r["model"] != model or r["content_hash"] != _hash(r["content"])]

    def pending_count(self, model: str) -> int:
        return len(self._stale(model))

    async def index(self, settings: dict[str, Any], memory_ids: list[str] | None = None, limit: int = ml.INDEX_BATCH) -> int:
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

    async def query_vec(self, settings: dict[str, Any], query: str, timeout: float = ml.QUERY_TIMEOUT) -> np.ndarray | None:
        """The query's embedding, or None (off, route down, too slow, or an empty query)."""
        if not query.strip() or not self.enabled(settings) or not self.embedder.available(settings):
            return None
        try:
            vecs = await asyncio.wait_for(self.embedder.embed(settings, [query[:ml.QUERY_CHARS]]), timeout)
        except Exception:  # noqa: BLE001 - timeouts included
            return None
        return vecs[0] if vecs else None

    async def near_duplicate(self, settings: dict[str, Any], project_id: str | None, text: str) -> dict[str, Any] | None:
        """The live, unpinned memory in scope whose meaning is closest to `text`, if its cosine reaches
        NEAR_DUP_SIMILARITY; else None (also with embeddings off, down or slow). Never raises.
        ponytail: rows with no vector yet are not compared, so a fact saved before the backfill ran can be duplicated once."""
        try:
            qv = await self.query_vec(settings, text)
            model = self.embedder.model(settings) if qv is not None else ""
            if qv is None or not model:
                return None
            where, args = _scope_clause(project_id, include_global=False)  # a project save never rewrites a personal row
            with self.db.tx() as c:
                rows = c.execute(
                    f"""SELECT v.memory_id, v.vec, v.dim FROM memory_vectors v JOIN memories m ON m.id=v.memory_id
                        WHERE v.model=? AND m.pinned=0 AND {live_mem('m')} AND {where.replace('project_id', 'm.project_id')} LIMIT ?""",
                    (model, *args, ml.VECTOR_CAP)).fetchall()
            best = max(((float(unpack(r["vec"]) @ qv), r["memory_id"]) for r in rows if r["dim"] == qv.shape[0]), default=None)
            return self.memories.get(best[1]) if best and best[0] >= ml.NEAR_DUP_SIMILARITY else None
        except Exception:  # noqa: BLE001 - dedupe is best effort; the caller just creates the row
            log.exception("memory near-duplicate check failed")
            return None

    # ---------- rankers ----------
    def _cosine(self, c: Any, where: str, args: list[Any], qvec: np.ndarray | None, model: str) -> list[str]:
        """Rows at or above the similarity floor, best first."""
        if qvec is None or not model:
            return []
        rows = c.execute(
            f"""SELECT v.memory_id, v.vec, v.dim FROM memory_vectors v JOIN memories m ON m.id=v.memory_id
                WHERE v.model=? AND {live_mem('m')} AND {where.replace('project_id', 'm.project_id')} LIMIT ?""",
            (model, *args, ml.VECTOR_CAP)).fetchall()
        scored = [(float(unpack(r["vec"]) @ qvec), r["memory_id"]) for r in rows if r["dim"] == qvec.shape[0]]
        scored = [s for s in scored if s[0] >= ml.MEMORY_MIN_SIMILARITY]
        scored.sort(key=lambda s: -s[0])
        return [mid for _, mid in scored[:ml.RANK_DEPTH]]

    def _graph_seeded(self, c: Any, where: str, args: list[Any], project_id: str | None, query: str,
                      qvec: np.ndarray | None = None, model: str = "") -> list[str]:
        """Memories that name an entity the query seeds. The self node and value nodes are left out: every
        "User prefers ..." memory would otherwise match "User"."""
        vec_hits = self.recall.similar(project_id, qvec, model) if self.recall is not None and qvec is not None else None
        labels = {nm.lower() for n in graph_recall.subgraph(self.graph, project_id, query, vec_hits)["nodes"]
                  if not (graph_recall.is_self(n) or graph_recall.is_literal(n)) for nm in graph_recall.names(n) if len(nm) > 2}
        if not labels:
            return []
        # Whole words, as graph_recall.mentions matches: "Sam" must not seed every memory that says "same".
        named = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(lb) for lb in sorted(labels, key=len, reverse=True)) + r")(?!\w)")
        rows = c.execute(f"SELECT id, content FROM memories WHERE {where} AND {live_mem()} ORDER BY updated_at DESC", args).fetchall()
        return [r["id"] for r in rows if named.search(r["content"].lower())][:ml.RANK_DEPTH]

    def search(self, project_id: str | None, query: str, query_vec: np.ndarray | None = None, limit: int = 20,
               settings: dict[str, Any] | None = None, include_global: bool = True) -> list[dict[str, Any]]:
        """Memories that matched the query (lexical hit, cosine >= MEMORY_MIN_SIMILARITY, or graph seed), ranked by
        RRF over those rankers plus recency among the matches. No match returns []. Never raises."""
        try:
            where, args = _scope_clause(project_id, include_global)
            model = self.embedder.model(settings) if settings is not None else ""
            lexical = [m["id"] for m in self.memories.matching(project_id, query, ml.RANK_DEPTH, include_global)]
            with self.db.tx() as c:
                cos = self._cosine(c, where, args, query_vec, model)
                graph = self._graph_seeded(c, where, args, project_id, query, query_vec, model)
                matched = list(dict.fromkeys([*lexical, *cos, *graph]))
                if not matched:
                    return []
                recent = [r["id"] for r in c.execute(
                    f"SELECT id FROM memories WHERE id IN ({','.join('?' * len(matched))}) ORDER BY updated_at DESC", matched)]
            ids = [i for i, _ in rrf([lexical, cos, graph, recent], weights=[ml.W_BM25, ml.W_COSINE, ml.W_GRAPH, ml.W_RECENT])][:limit]
        except Exception:  # noqa: BLE001 - retrieval must never break a reply
            log.exception("memory search failed")
            return []
        rows = {m["id"]: m for m in (self.memories.get(i) for i in ids) if m}
        return [rows[i] for i in ids if i in rows]

    def candidates(self, project_id: str | None, query: str, query_vec: np.ndarray | None, settings: dict[str, Any],
                   limit: int = ml.CANDIDATES, top: int = ml.CANDIDATES_TOP) -> list[dict[str, Any]]:
        """The extractor's reconciliation set: the top hybrid hits first, then pinned/recent rows up to `limit`."""
        out: dict[str, dict[str, Any]] = {}
        for m in self.search(project_id, query, query_vec, limit=top, settings=settings):
            out[m["id"]] = m
        for m in self.memories.for_context(project_id, query, limit=limit):
            if len(out) >= limit:
                break
            out.setdefault(m["id"], m)
        return list(out.values())
