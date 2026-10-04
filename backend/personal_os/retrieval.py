"""Hybrid retrieval over document chunks: BM25 and embedding KNN fused with reciprocal rank fusion.

Two stores feed one ranking: uploaded files (`chunks`) and the user's own Docs (`doc_chunks`, see
docs.py). Embeddings are optional. With no embedding model, a failing route or retrievalMode='bm25',
search is BM25 only. Vectors are unit-length float32 blobs, so the KNN is one numpy matrix-vector
product (fine to ~100k chunks, no extension to ship).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from . import llm
from .chunker import contextualize
from .db import Database
from .embed import Embedder, pack, rrf, unpack  # noqa: F401 - rrf re-exported for callers
from .repos import Documents, _scope_clause
from .retrieval_rerank import rerank

log = logging.getLogger("grain.retrieval")

RRF_K = 60
ALL_SOURCES = ("files", "docs")

# Per store: the chunk table, its embeddings table, the parent table, the parent's name column and
# the column on the chunk table pointing at the parent. Table names are constants, never user input.
_STORE = {
    "files": {"chunks": "chunks", "emb": "chunk_embeddings", "parent": "documents", "name": "name", "fk": "document_id", "tag": "file"},
    "docs": {"chunks": "doc_chunks", "emb": "doc_chunk_embeddings", "parent": "docs", "name": "title", "fk": "doc_id", "tag": "doc"},
}


class Retriever:
    # Seconds a doc edit waits before its vectors are made, so autosave bursts become one batch.
    DOC_DEBOUNCE = 2.0

    def __init__(self, db: Database, documents: Documents, embedder: Embedder | None = None, docs: Any = None):
        self.db, self.documents = db, documents
        self.embedder = embedder or Embedder()
        self.docs = docs  # the user's own Docs; None = uploads only
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._docs_pending = False
        # One blurb pass at a time: overlapping passes would select the same blurb='' rows and call the model twice.
        self._ctx_lock = asyncio.Lock()
        self.complete: Any = llm.complete  # tests stub this
        self.rerank_fn: Any = None  # tests stub this; None = retrieval_rerank.rerank

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the server loop so edits made on worker threads can still schedule embedding."""
        self._loop = loop

    # ---------- indexing ----------
    def _pending(self, c: Any, store: str, model: str, parent_id: str | None = None, limit: int = 256) -> list[Any]:
        s = _STORE[store]
        where, args = (f"AND ch.{s['fk']}=?", [parent_id]) if parent_id else ("", [])
        return c.execute(
            f"""SELECT ch.id, ch.{s['fk']} AS parent_id, ch.text, ch.heading, ch.blurb, p.{s['name']} AS name FROM {s['chunks']} ch
                JOIN {s['parent']} p ON p.id=ch.{s['fk']}
                LEFT JOIN {s['emb']} e ON e.chunk_id=ch.id AND e.model=?
                WHERE e.chunk_id IS NULL {where} LIMIT ?""", (model, *args, limit)).fetchall()

    async def _embed_store(self, settings: dict[str, Any], store: str, parent_id: str | None, limit: int) -> tuple[int, str | None]:
        s, model = _STORE[store], self.embedder.model(settings)
        embedded = 0
        while model:
            with self.db.tx() as c:
                rows = self._pending(c, store, model, parent_id, limit)
            if not rows:
                break
            vecs = await self.embedder.embed(settings, [contextualize(r["name"], r["heading"], r["text"], r["blurb"]) for r in rows])
            if vecs is None:
                return embedded, "embeddings unavailable"
            with self.db.tx() as c:
                for r, v in zip(rows, vecs):
                    # A chunk deleted while the request was in flight must not leave an orphan row.
                    if c.execute(f"SELECT 1 FROM {s['chunks']} WHERE id=?", (r["id"],)).fetchone():
                        c.execute(f"INSERT OR REPLACE INTO {s['emb']}(chunk_id,{s['fk']},model,dim,vec) VALUES(?,?,?,?,?)",
                                  (r["id"], r["parent_id"], model, int(v.shape[0]), pack(v)))
                        embedded += 1
            if len(rows) < limit:
                break
        return embedded, None

    async def contextualize_pending(self, settings: dict[str, Any], limit: int = 64,
                                    stores: tuple[str, ...] = ALL_SOURCES, document_id: str | None = None) -> int:
        """Write a model-made blurb for up to `limit` chunks per store that have none, re-index it with the
        chunk and drop the chunk's vector so embed_pending re-makes it. Off unless contextualChunks; a model
        error stops the pass and leaves the chunk as it was. `document_id` narrows to one uploaded file (and skips
        Docs). Returns blurbs written. Never raises."""
        model = settings.get("defaultModel")
        if not settings.get("contextualChunks") or not model:
            return 0
        done = 0
        for store in stores:
            if store == "docs" and (document_id or self.docs is None):
                continue
            s = _STORE[store]
            where, args = (f"AND ch.{s['fk']}=?", [document_id]) if document_id else ("", [])
            with self.db.tx() as c:
                rows = c.execute(
                    f"""SELECT ch.id, ch.{s['fk']} AS parent_id, ch.text, ch.heading, p.{s['name']} AS name FROM {s['chunks']} ch
                        JOIN {s['parent']} p ON p.id=ch.{s['fk']} WHERE ch.blurb='' AND p.deleted_at IS NULL {where}
                        ORDER BY ch.{s['fk']}, ch.idx LIMIT ?""", (*args, limit)).fetchall()
            docs_text: dict[str, str] = {}
            for r in rows:
                try:
                    if r["parent_id"] not in docs_text:
                        with self.db.tx() as c:
                            docs_text[r["parent_id"]] = "\n".join(x["text"] for x in c.execute(
                                f"SELECT text FROM {s['chunks']} WHERE {s['fk']}=? ORDER BY idx", (r["parent_id"],)))[:6000]
                    # Document first, chunk last: the shared prefix is what a prompt cache can reuse.
                    blurb = (await self.complete(settings, str(model), [{"role": "user", "content": (
                        f"<document>\n{docs_text[r['parent_id']]}\n</document>\n<chunk>\n{r['text']}\n</chunk>\n"
                        "Write one or two sentences (under 80 words) that situate this chunk within the document "
                        "to improve search retrieval. Answer with only that context.")}], "contextualize")).strip()
                except Exception:  # noqa: BLE001 - leave the chunk as it was
                    log.warning("chunk contextualisation failed; stopping this pass", exc_info=True)
                    return done
                if not blurb:
                    continue
                with self.db.tx() as c:
                    if not c.execute(f"SELECT 1 FROM {s['chunks']} WHERE id=?", (r["id"],)).fetchone():
                        continue
                    fts = "chunks_fts" if store == "files" else "doc_chunks_fts"
                    c.execute(f"UPDATE {s['chunks']} SET blurb=? WHERE id=?", (blurb, r["id"]))
                    c.execute(f"DELETE FROM {fts} WHERE chunk_id=?", (r["id"],))
                    c.execute(f"INSERT INTO {fts}(text, chunk_id, {s['fk']}) VALUES(?,?,?)",
                              (contextualize(r["name"], r["heading"], r["text"], blurb), r["id"], r["parent_id"]))
                    c.execute(f"DELETE FROM {s['emb']} WHERE chunk_id=?", (r["id"],))
                done += 1
        return done

    async def contextualize_all(self, settings: dict[str, Any], stores: tuple[str, ...] = ALL_SOURCES,
                                document_id: str | None = None) -> int:
        """contextualize_pending until a pass writes nothing, so a large upload is not left part-done. Never raises."""
        total = 0
        async with self._ctx_lock:
            while n := await self.contextualize_pending(settings, stores=stores, document_id=document_id):
                total += n
        return total

    async def embed_pending(self, settings: dict[str, Any], document_id: str | None = None, limit: int = 256,
                            stores: tuple[str, ...] = ALL_SOURCES) -> dict[str, Any]:
        """Embed chunks that have no vector for the current model. Idempotent; returns {embedded, remaining}.
        `document_id` narrows to one uploaded file (and skips Docs)."""
        model = self.embedder.model(settings)
        embedded, error = 0, None
        for store in stores:
            if store == "docs" and (document_id or self.docs is None):
                continue
            n, err = await self._embed_store(settings, store, document_id if store == "files" else None, limit)
            embedded += n
            error = error or err
        remaining = 0
        if model:
            with self.db.tx() as c:
                for store in stores:
                    if store == "docs" and (document_id or self.docs is None):
                        continue
                    remaining += len(self._pending(c, store, model, document_id if store == "files" else None, 100000))
        out: dict[str, Any] = {"embedded": embedded, "remaining": remaining}
        if error:
            out["error"] = error
        return out

    def _embeds(self, settings: dict[str, Any]) -> bool:
        return settings.get("retrievalMode", "hybrid") == "hybrid" and self.embedder.available(settings)

    def _kick(self, coro_fn: Any, settings_fn: Any, contextual: bool = False) -> None:
        s = settings_fn()
        # Blurbs also go into the keyword index, so they are written even with no embedder or in bm25 mode.
        if not self._embeds(s) and not (contextual and s.get("contextualChunks") and s.get("defaultModel")):
            return

        def start(loop: asyncio.AbstractEventLoop) -> None:
            t = loop.create_task(coro_fn())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        try:
            start(asyncio.get_running_loop())
        except RuntimeError:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(start, self._loop)

    def schedule(self, settings_fn: Any, document_id: str | None = None) -> None:
        """Fire-and-forget background blurbs (contextualChunks) and embedding of an uploaded file. Never raises or
        blocks the caller."""
        async def run() -> None:
            try:
                # Before embedding: a blurb drops the chunk's vector, so the other order embeds it twice.
                await self.contextualize_all(settings_fn(), stores=("files",), document_id=document_id)
                if self._embeds(settings_fn()):
                    await self.embed_pending(settings_fn(), document_id, stores=("files",))
            except Exception:  # noqa: BLE001 - rows simply stay unembedded
                log.exception("background embedding failed")

        # Docs are not contextualised here: every edit re-chunks and empties their blurbs, so doing it per edit
        # burst would call the model for every chunk again. Rebuild index (embed-backfill) covers them.
        self._kick(run, settings_fn, contextual=True)

    def schedule_docs(self, settings_fn: Any) -> None:
        """Debounced background embedding of edited Docs: one pending task covers every edit in the window."""
        if self._docs_pending:
            return

        async def run() -> None:
            try:
                await asyncio.sleep(self.DOC_DEBOUNCE)
                self._docs_pending = False
                await self.embed_pending(settings_fn(), stores=("docs",))
            except Exception:  # noqa: BLE001
                log.exception("background doc embedding failed")
            finally:
                self._docs_pending = False

        self._docs_pending = True
        before = len(self._tasks)
        self._kick(run, settings_fn)
        if len(self._tasks) == before:  # not started (no model, bm25 mode, no loop yet)
            self._docs_pending = False

    def status(self, settings: dict[str, Any]) -> dict[str, Any]:
        model = self.embedder.model(settings)
        with self.db.tx() as c:
            chunks = c.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            embedded = c.execute("SELECT COUNT(*) AS n FROM chunk_embeddings WHERE model=?", (model,)).fetchone()["n"] if model else 0
            out = {"chunks": chunks, "embedded": embedded, "model": model, "mode": settings.get("retrievalMode", "hybrid")}
            if self.docs is not None:
                out["doc_chunks"] = c.execute("SELECT COUNT(*) AS n FROM doc_chunks").fetchone()["n"]
                out["doc_embedded"] = c.execute("SELECT COUNT(*) AS n FROM doc_chunk_embeddings WHERE model=?", (model,)).fetchone()["n"] if model else 0
        return out

    # ---------- search ----------
    def _has_vectors(self, stores: list[str], model: str) -> bool:
        """Whether any stored vector could match: with none, embedding the query is a wasted round trip."""
        with self.db.tx() as c:
            return any(c.execute(f"SELECT 1 FROM {_STORE[s]['emb']} WHERE model=? LIMIT 1", (model,)).fetchone()
                       for s in stores)

    def _vector_rank(self, store: str, project_id: str | None, q: np.ndarray, model: str, candidates: int) -> list[tuple[str, float]]:
        s = _STORE[store]
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT e.chunk_id, e.vec, e.dim FROM {s['emb']} e JOIN {s['parent']} p ON p.id=e.{s['fk']}
                    WHERE e.model=? AND p.deleted_at IS NULL AND {where.replace('project_id', 'p.project_id')}""", (model, *args)).fetchall()
        rows = [r for r in rows if r["dim"] == q.shape[0]]
        if not rows:
            return []
        mat = np.stack([unpack(r["vec"]) for r in rows])
        sims = mat @ q
        order = np.argsort(-sims)[:candidates]
        return [(rows[i]["chunk_id"], float(sims[i])) for i in order]

    def _chunks(self, store: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        s = _STORE[store]
        extra = ", ch.page" if store == "files" else ""
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT ch.id AS chunk_id, ch.{s['fk']} AS document_id, p.{s['name']} AS name, ch.idx, ch.text, ch.heading{extra}
                    FROM {s['chunks']} ch JOIN {s['parent']} p ON p.id=ch.{s['fk']}
                    WHERE p.deleted_at IS NULL AND ch.id IN ({','.join('?' * len(ids))})""", ids).fetchall()
        out = {}
        for r in rows:
            h = dict(r)
            h.setdefault("page", None)
            if store == "docs":
                h.update(source="doc", doc_id=h["document_id"], title=h["name"])
            out[r["chunk_id"]] = h
        return out

    async def search(self, project_id: str | None, query: str, settings: dict[str, Any], limit: int = 6,
                     sources: tuple[str, ...] = ("files",)) -> list[dict[str, Any]]:
        """Top `limit` chunks across the requested stores ('files', 'docs'), each tagged `source` ('file'|'doc')
        and `sources` (which rankers found it: 'bm25', 'vector')."""
        stores = [s for s in ALL_SOURCES if s in sources and (s == "files" or self.docs is not None)]
        cand = max(limit, int(settings.get("retrievalCandidates") or 20))
        by_key: dict[str, dict[str, Any]] = {}
        bm25_lists: list[list[str]] = []
        for store in stores:
            raw = (self.documents.search(project_id, query, limit=cand * 2) if store == "files"
                   else self.docs.chunk_search(query, project_id, limit=cand * 2))
            keys = []
            for h in raw:
                k = f"{store}:{h['chunk_id']}"
                by_key[k] = {**h, "source": _STORE[store]["tag"], "sources": ["bm25"]}
                keys.append(k)
            bm25_lists.append(keys)
        hybrid = (settings.get("retrievalMode", "hybrid") == "hybrid" and self.embedder.available(settings)
                  and bool(query.strip()) and self._has_vectors(stores, self.embedder.model(settings)))
        qv = await self.embedder.embed(settings, [query[:2000]]) if hybrid else None  # a long paste would 400 the embed route
        vec_lists: list[list[str]] = []
        sims: dict[str, float] = {}
        if qv:
            for store in stores:
                ranked = self._vector_rank(store, project_id, qv[0], self.embedder.model(settings), cand)
                keys = [f"{store}:{cid}" for cid, _ in ranked]
                for (cid, sim), k in zip(ranked, keys):
                    sims[k] = sim
                for cid, h in self._chunks(store, [c for c, _ in ranked if f"{store}:{c}" not in by_key]).items():
                    by_key[f"{store}:{cid}"] = {**h, "source": _STORE[store]["tag"], "sources": []}
                for k in keys:
                    if k in by_key:
                        by_key[k]["sources"].append("vector")
                vec_lists.append(keys)
            fused = rrf(bm25_lists + vec_lists, [1.0] * (len(bm25_lists) + len(vec_lists)), k=RRF_K)
        else:
            # No vectors: bm25 only. One store keeps its own order; several interleave by rank (rrf).
            fused = rrf(bm25_lists, [1.0] * len(bm25_lists), k=RRF_K) if len(bm25_lists) > 1 else \
                [(k, -float(by_key[k].get("score") or 0.0)) for k in (bm25_lists[0] if bm25_lists else [])]
        if settings.get("retrievalRerank") and settings.get("retrievalRerankModel") and len(fused) > 1:
            fused = await (self.rerank_fn or rerank)(settings, query, fused, by_key)
        floor = float(settings.get("retrievalMinSimilarity") or 0.0)
        cap = int(settings.get("retrievalPerDocCap") or 0)
        per_doc: dict[tuple[str, str], int] = {}
        out: list[dict[str, Any]] = []
        for k, score in fused:
            h = by_key.get(k)
            if h is None:
                continue
            # Exact keyword hits survive a weak cosine; only vector-only hits are held to the floor.
            if qv and "bm25" not in h["sources"] and sims.get(k, 0.0) < floor:
                continue
            dk = (h["source"], h["document_id"])
            if cap and per_doc.get(dk, 0) >= cap:
                continue
            per_doc[dk] = per_doc.get(dk, 0) + 1
            out.append({**h, "score": score})
            if len(out) >= limit:
                break
        return out
