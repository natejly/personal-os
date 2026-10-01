"""Hybrid retrieval over document chunks: BM25 and embedding KNN fused with reciprocal rank fusion.

Embeddings are optional. With no embedding model, a failing route or retrievalMode='bm25', search
returns exactly what Documents.search returns. Vectors live in chunk_embeddings as unit-length
float32 blobs, so the KNN is one numpy matrix-vector product (fine to ~100k chunks, no extension).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from .db import Database
from .embed import Embedder, pack, rrf, unpack  # noqa: F401 - rrf re-exported for callers
from .repos import ALL, Documents, _scope_clause

log = logging.getLogger("grain.retrieval")

RRF_K = 60


class Retriever:
    def __init__(self, db: Database, documents: Documents, embedder: Embedder | None = None, docs: Any = None):
        self.db, self.documents = db, documents
        self.embedder = embedder or Embedder()
        self.docs = docs  # the user's own Docs (docs-3); None = uploads only
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---------- indexing ----------
    def _pending(self, c: Any, model: str, document_id: str | None = None, limit: int = 256) -> list[Any]:
        where, args = ("AND ch.document_id=?", [document_id]) if document_id else ("", [])
        return c.execute(
            f"""SELECT ch.id, ch.document_id, ch.text FROM chunks ch
                LEFT JOIN chunk_embeddings e ON e.chunk_id=ch.id AND e.model=?
                WHERE e.chunk_id IS NULL {where} LIMIT ?""", (model, *args, limit)).fetchall()

    async def embed_pending(self, settings: dict[str, Any], document_id: str | None = None, limit: int = 256) -> dict[str, Any]:
        """Embed chunks that have no vector for the current model. Idempotent; returns {embedded, remaining}."""
        model = self.embedder.model(settings)
        embedded, error = 0, None
        while model:
            with self.db.tx() as c:
                rows = self._pending(c, model, document_id, limit)
            if not rows:
                break
            vecs = await self.embedder.embed(settings, [r["text"] for r in rows])
            if vecs is None:
                error = "embeddings unavailable"
                break
            with self.db.tx() as c:
                for r, v in zip(rows, vecs):
                    # A chunk deleted while the request was in flight must not leave an orphan row.
                    if c.execute("SELECT 1 FROM chunks WHERE id=?", (r["id"],)).fetchone():
                        c.execute("INSERT OR REPLACE INTO chunk_embeddings(chunk_id,document_id,model,dim,vec) VALUES(?,?,?,?,?)",
                                  (r["id"], r["document_id"], model, int(v.shape[0]), pack(v)))
                        embedded += 1
            if len(rows) < limit:
                break
        with self.db.tx() as c:
            remaining = len(self._pending(c, model, document_id, 100000)) if model else 0
        out: dict[str, Any] = {"embedded": embedded, "remaining": remaining}
        if error:
            out["error"] = error
        return out

    def schedule(self, settings_fn: Any, document_id: str | None = None) -> None:
        """Fire-and-forget background embedding. Never raises and never blocks the caller."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if settings_fn().get("retrievalMode", "hybrid") != "hybrid" or not self.embedder.available(settings_fn()):
            return

        async def run() -> None:
            try:
                await self.embed_pending(settings_fn(), document_id)
            except Exception:  # noqa: BLE001 - rows simply stay unembedded
                log.exception("background embedding failed")

        t = loop.create_task(run())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def status(self, settings: dict[str, Any]) -> dict[str, Any]:
        model = self.embedder.model(settings)
        with self.db.tx() as c:
            chunks = c.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            embedded = c.execute("SELECT COUNT(*) AS n FROM chunk_embeddings WHERE model=?", (model,)).fetchone()["n"] if model else 0
        return {"chunks": chunks, "embedded": embedded, "model": model, "mode": settings.get("retrievalMode", "hybrid")}

    # ---------- search ----------
    def _vector_rank(self, project_id: str | None, q: np.ndarray, model: str, candidates: int) -> list[tuple[str, float]]:
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT e.chunk_id, e.vec, e.dim FROM chunk_embeddings e JOIN documents d ON d.id=e.document_id
                    WHERE e.model=? AND {where.replace('project_id', 'd.project_id')}""", (model, *args)).fetchall()
        rows = [r for r in rows if r["dim"] == q.shape[0]]
        if not rows:
            return []
        mat = np.stack([unpack(r["vec"]) for r in rows])
        sims = mat @ q
        order = np.argsort(-sims)[:candidates]
        return [(rows[i]["chunk_id"], float(sims[i])) for i in order]

    def _chunks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT ch.id AS chunk_id, ch.document_id, d.name, ch.idx, ch.text FROM chunks ch
                    JOIN documents d ON d.id=ch.document_id WHERE ch.id IN ({','.join('?' * len(ids))})""", ids).fetchall()
        return {r["chunk_id"]: dict(r) for r in rows}

    async def search(self, project_id: str | None, query: str, settings: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
        cand = max(limit, int(settings.get("retrievalCandidates") or 20))
        bm25 = self.documents.search(project_id, query, limit=cand * 2)
        hybrid = settings.get("retrievalMode", "hybrid") == "hybrid" and self.embedder.available(settings) and bool(query.strip())
        qv = await self.embedder.embed(settings, [query]) if hybrid else None
        by_id = {h["chunk_id"]: {**h, "sources": ["bm25"]} for h in bm25}
        bm25_ids = [h["chunk_id"] for h in bm25]
        vec_ids: list[str] = []
        sims: dict[str, float] = {}
        if qv:
            ranked = self._vector_rank(project_id, qv[0], self.embedder.model(settings), cand)
            sims = dict(ranked)
            vec_ids = [cid for cid, _ in ranked]
            for cid, h in self._chunks([i for i in vec_ids if i not in by_id]).items():
                by_id[cid] = {**h, "sources": []}
            for cid in vec_ids:
                if cid in by_id:
                    by_id[cid]["sources"].append("vector")
        if not qv:
            fused = [(cid, -float(by_id[cid].get("score") or 0.0)) for cid in bm25_ids]
            floor_applies = False
        else:
            fused = rrf([bm25_ids, vec_ids], [1.0, 1.0], k=RRF_K)
            floor_applies = True
        floor = float(settings.get("retrievalMinSimilarity") or 0.0)
        cap = int(settings.get("retrievalPerDocCap") or 0)
        per_doc: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for cid, score in fused:
            h = by_id.get(cid)
            if h is None:
                continue
            # Exact keyword hits survive a weak cosine; only vector-only hits are held to the floor.
            if floor_applies and "bm25" not in h["sources"] and sims.get(cid, 0.0) < floor:
                continue
            if cap and per_doc.get(h["document_id"], 0) >= cap:
                continue
            per_doc[h["document_id"]] = per_doc.get(h["document_id"], 0) + 1
            out.append({**h, "score": score})
            if len(out) >= limit:
                break
        return out
