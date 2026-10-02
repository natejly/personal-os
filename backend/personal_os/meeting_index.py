"""Semantic retrieval over meetings: the summary, notes and transcript are chunked and embedded with the
shared embed.Embedder (same route, back-off and vector format as memory_index.py), and meeting search fuses
those chunk rankings with the keyword hits by reciprocal rank.

Embedding sends meeting text to the embedding provider, so it is opt-in (`meetingEmbeddings`, off by
default). Off, no route, or a failed call all leave the plain FTS search untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import numpy as np

from .chunker import split_text
from .db import Database, new_id
from .embed import Embedder, pack, rrf, unpack
from .meetings import Meetings

log = logging.getLogger("grain.meeting_index")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_vectors (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meeting_vec ON meeting_vectors(meeting_id, idx);
"""
INDEX_BATCH = 5      # meetings embedded per index() call
VECTOR_CAP = 20000   # chunk rows scanned by the brute-force cosine ranker
QUERY_TIMEOUT = 2.0
MIN_SIM = 0.25


def _source(m: Any) -> str:
    parts = [m["title"], m["summary"], m["enhanced"] or m["notes"], m["transcript"]]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _hash(src: str) -> str:
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


class MeetingIndex:
    def __init__(self, db: Database, meetings: Meetings, embedder: Embedder | None = None):
        self.db, self.meetings = db, meetings
        self.embedder = embedder or Embedder()
        self._tasks: set[asyncio.Task[Any]] = set()
        with db.tx() as c:
            c.executescript(SCHEMA)

    @staticmethod
    def enabled(settings: dict[str, Any]) -> bool:
        return (bool(settings.get("meetingEmbeddings")) and bool(settings.get("embeddingModel"))
                and settings.get("retrievalMode", "hybrid") != "bm25")

    def _stale(self, model: str, ids: list[str] | None) -> list[Any]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT m.id, m.title, m.summary, m.notes, m.enhanced, m.transcript, "
                "  (SELECT content_hash || model FROM meeting_vectors v WHERE v.meeting_id=m.id LIMIT 1) AS have "
                "FROM meetings m WHERE m.status='ready'").fetchall()
        out = []
        for r in rows:
            if ids is not None and r["id"] not in ids:
                continue
            src = _source(r)
            if src and r["have"] != _hash(src) + model:
                out.append(r)
        return out

    async def index(self, settings: dict[str, Any], meeting_ids: list[str] | None = None,
                    limit: int = INDEX_BATCH) -> int:
        """Embed finished meetings whose vectors are missing or out of date. Idempotent. Returns how many."""
        if not self.enabled(settings) or not self.embedder.available(settings):
            return 0
        model = self.embedder.model(settings)
        done = 0
        for r in self._stale(model, meeting_ids)[:limit]:
            src = _source(r)
            chunks = split_text(src)
            vecs = await self.embedder.embed(settings, chunks)
            if vecs is None or len(vecs) != len(chunks):
                break
            h = _hash(src)
            with self.db.tx() as c:
                c.execute("DELETE FROM meeting_vectors WHERE meeting_id=?", (r["id"],))
                for i, (t, v) in enumerate(zip(chunks, vecs)):
                    c.execute("INSERT INTO meeting_vectors(id,meeting_id,idx,text,model,dim,vec,content_hash) "
                              "VALUES(?,?,?,?,?,?,?,?)", (new_id(), r["id"], i, t, model, int(v.shape[0]), pack(v), h))
            done += 1
        return done

    def schedule(self, settings: dict[str, Any], meeting_ids: list[str] | None = None) -> None:
        """Background (re)index; never blocks or raises."""
        try:
            if not self.enabled(settings) or not self.embedder.available(settings):
                return
            if not self._stale(self.embedder.model(settings), meeting_ids):
                return
            task = asyncio.get_running_loop().create_task(self.index(settings, meeting_ids))
        except RuntimeError:
            return  # no running loop (sync caller)
        except Exception:  # noqa: BLE001
            log.exception("meeting index scheduling failed")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _query_vec(self, settings: dict[str, Any], query: str) -> np.ndarray | None:
        try:
            vecs = await asyncio.wait_for(self.embedder.embed(settings, [query[:2000]]), QUERY_TIMEOUT)
        except Exception:  # noqa: BLE001 - timeouts included
            return None
        return vecs[0] if vecs else None

    def _semantic(self, qvec: np.ndarray, model: str, project_id: str | None, depth: int) -> list[tuple[str, str]]:
        """Best chunk per meeting, ranked: [(meeting_id, chunk_text)]."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT v.meeting_id, v.text, v.vec, m.project_id FROM meeting_vectors v "
                f"JOIN meetings m ON m.id=v.meeting_id WHERE v.model=? AND {self.meetings._visible(c, 'm')} LIMIT ?",
                (model, VECTOR_CAP)).fetchall()
        best: dict[str, tuple[float, str]] = {}
        for r in rows:
            if project_id not in (None, "__all__") and r["project_id"] != project_id:
                continue
            v = unpack(r["vec"])
            s = float(v @ qvec) if v.shape == qvec.shape else 0.0
            if s >= MIN_SIM and s > best.get(r["meeting_id"], (-1.0, ""))[0]:
                best[r["meeting_id"]] = (s, r["text"])
        top = sorted(best.items(), key=lambda kv: -kv[1][0])[:depth]
        return [(mid, t) for mid, (_, t) in top]

    async def search(self, settings: dict[str, Any], query: str, project_id: str | None = "__all__",
                     limit: int = 10) -> list[dict[str, Any]]:
        """Keyword hits fused with by-meaning chunk hits. Plain FTS when embeddings are off or unavailable."""
        lex = self.meetings.search(query, project_id, limit)
        if not self.enabled(settings) or not query.strip():
            return lex
        self.schedule(settings)  # lazily embed meetings that have no vectors yet
        qvec = await self._query_vec(settings, query)
        if qvec is None:
            return lex
        sem = self._semantic(qvec, self.embedder.model(settings), project_id, max(1, limit) * 3)
        if not sem:
            return lex
        by_id = {h["meeting_id"]: h for h in lex}
        chunk = dict(sem)
        fused = rrf([[h["meeting_id"] for h in lex], [mid for mid, _ in sem]])
        out = []
        for mid, score in fused:
            if mid in by_id:
                h = dict(by_id[mid])
            else:
                m = self.meetings.get(mid, include_hidden=False)
                if not m:
                    continue
                h = {"meeting_id": mid, "title": m["title"], "status": m["status"], "started_at": m["started_at"],
                     "doc_id": m["doc_id"], "snippet": chunk[mid][:300].strip(), "field": "semantic"}
            h["score"] = score
            out.append(h)
            if len(out) >= max(1, limit):
                break
        return out
