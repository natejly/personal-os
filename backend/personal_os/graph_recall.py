"""Knowledge-graph recall: which entities a message is about, and the live facts around them.

Seeds come from two signals: a label or alias named in the text as a whole word, and (when embeddings are up)
node vectors close to the message. The block the model sees is 1-hop live edges ranked by seed score, extractor
confidence and recency. Node vectors live in `kg_node_vectors`, stored like memory_index stores memory vectors.
Nothing here raises into a chat turn; with no embedding route it still works on mentions alone.

Must not import context.py (context imports this module).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any

import numpy as np

from . import redact
from .db import Database, row_to_dict
from .embed import Embedder, pack, unpack
from .memory_limits import (
    GRAPH_CONTEXT_MAX_EDGES, GRAPH_CONTEXT_MAX_SEEDS, GRAPH_MATCH_SIMILARITY, GRAPH_MIN_MENTION_CHARS,
    GRAPH_INDEX_BATCH, GRAPH_NAME_CHARS, GRAPH_NODE_VECTOR_CAP, GRAPH_QUALIFIER_CHARS, GRAPH_RECENCY_HALF_LIFE_DAYS, GRAPH_RELATION_CHARS,
    GRAPH_RESOLVE_SIMILARITY,
)
from .repos import Graph, _scope_clause

log = logging.getLogger("grain.graph_recall")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_node_vectors (
  node_id TEXT PRIMARY KEY REFERENCES kg_nodes(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL,
  text_hash TEXT NOT NULL,
  updated_at REAL NOT NULL
);
"""

DAY = 86400.0


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def _props(node: dict[str, Any]) -> dict[str, Any]:
    p = node.get("properties")
    return p if isinstance(p, dict) else {}


def is_self(node: dict[str, Any]) -> bool:
    return bool(_props(node).get("self"))


def is_literal(node: dict[str, Any]) -> bool:
    return bool(_props(node).get("literal"))


def _skip(node: dict[str, Any]) -> bool:
    """Never a seed and never embedded: "User" matches every first-person message, a status value matches noise."""
    return is_self(node) or is_literal(node)


def aliases(node: dict[str, Any]) -> list[str]:
    a = _props(node).get("aliases")
    return [str(x).strip() for x in a if str(x).strip()] if isinstance(a, list) else []


def names(node: dict[str, Any]) -> list[str]:
    """The label and every alias."""
    return [str(node.get("label") or "").strip(), *aliases(node)]


def node_text(node: dict[str, Any]) -> str:
    """What a node is embedded as: "Samantha Ortiz (Sam, Sammy)"."""
    al = aliases(node)
    return f"{str(node.get('label') or '').strip()} ({', '.join(al)})" if al else str(node.get("label") or "").strip()


def _clean(text: Any, limit: int) -> str:
    return " ".join(redact.scrub_command_output(str(text)).split())[:limit]


# ---------- seeding ----------
def mentions(nodes: list[dict[str, Any]], text: str) -> dict[str, float]:
    """node_id -> 1.0 for nodes whose label or an alias appears in `text` as a whole word or phrase."""
    low = text.lower()
    out: dict[str, float] = {}
    for n in nodes:
        if _skip(n):
            continue
        for nm in names(n):
            if len(nm) >= GRAPH_MIN_MENTION_CHARS and re.search(rf"(?<!\w){re.escape(nm.lower())}(?!\w)", low):
                out[n["id"]] = 1.0
                break
    return out


def _age_days(edge: dict[str, Any], at: float) -> float:
    return max(0.0, (at - float(edge.get("valid_at") or edge.get("created_at") or at)) / DAY)


def subgraph(graph: Graph, project_id: str | None, text: str, vec_hits: dict[str, float] | None = None) -> dict[str, Any]:
    """Seeds (mentions + vector hits), their live 1-hop edges ranked, and the nodes those touch. Empty when nothing seeds."""
    empty: dict[str, Any] = {"seeds": [], "nodes": [], "edges": [], "scores": {}}
    g = graph.get(project_id)
    by_id = {n["id"]: n for n in g["nodes"]}
    scores = mentions(g["nodes"], text)
    for nid, s in (vec_hits or {}).items():
        if nid in by_id and not _skip(by_id[nid]):
            scores[nid] = max(scores.get(nid, 0.0), s)
    seeds = sorted(scores, key=lambda i: -scores[i])[:GRAPH_CONTEXT_MAX_SEEDS]
    if not seeds:
        return empty
    seed_score = {i: scores[i] for i in seeds}
    at = time.time()
    ranked: list[tuple[float, dict[str, Any]]] = []
    for e in g["edges"]:
        s = max(seed_score.get(e["source_id"], 0.0), seed_score.get(e["target_id"], 0.0))
        if not s or e["source_id"] not in by_id or e["target_id"] not in by_id:
            continue
        conf = e.get("confidence")
        w = s * (1.0 if conf is None else float(conf)) * 0.5 ** (_age_days(e, at) / GRAPH_RECENCY_HALF_LIFE_DAYS)
        ranked.append((w, e))
    ranked.sort(key=lambda r: -r[0])
    edges = [e for _, e in ranked[:GRAPH_CONTEXT_MAX_EDGES]]
    order = list(seeds)
    for e in edges:
        for i in (e["source_id"], e["target_id"]):
            if i not in seed_score and i not in order:
                order.append(i)
    return {"seeds": seeds, "nodes": [by_id[i] for i in order], "edges": edges, "scores": seed_score}


def edge_line(edge: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    """`- Sam Ortiz —works_at→ Acme (head of procurement; since 2026-09-12)`. Values are scrubbed and one line."""
    src, dst = by_id.get(edge["source_id"], {}), by_id.get(edge["target_id"], {})
    extra = [q for q in (_clean(edge.get("fact") or "", GRAPH_QUALIFIER_CHARS),
                         f"since {time.strftime('%Y-%m-%d', time.localtime(edge['valid_at']))}" if edge.get("valid_at") else "") if q]
    return (f"- {_clean(src.get('label') or '?', GRAPH_NAME_CHARS)} —{_clean(edge.get('relation') or '', GRAPH_RELATION_CHARS)}→ {_clean(dst.get('label') or '?', GRAPH_NAME_CHARS)}"
            + (f" ({'; '.join(extra)})" if extra else ""))


# ---------- node vectors ----------
class GraphRecall:
    def __init__(self, db: Database, graph: Graph, embedder: Embedder | None = None):
        self.db, self.graph = db, graph
        self.embedder = embedder or Embedder()
        self._tasks: set[asyncio.Task[Any]] = set()
        with db.tx() as c:
            c.executescript(SCHEMA)

    def enabled(self, settings: dict[str, Any]) -> bool:
        from .memory_index import MemoryIndex  # deferred: memory_index imports this module
        return MemoryIndex.enabled(settings) and self.embedder.available(settings)

    def _stale(self, model: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("""SELECT n.*, v.text_hash AS _hash, v.model AS _model FROM kg_nodes n
                                LEFT JOIN kg_node_vectors v ON v.node_id=n.id""").fetchall()
        out = []
        for r in rows:
            n = row_to_dict(r, ("properties",)) or {}
            n.pop("_hash", None), n.pop("_model", None)
            if n and not _skip(n) and (r["_model"] != model or r["_hash"] != _hash(node_text(n))):
                out.append(n)
        return out

    async def index(self, settings: dict[str, Any], node_ids: list[str] | None = None, limit: int = GRAPH_INDEX_BATCH) -> int:
        """Embed nodes whose vector is missing, stale or from another model. Idempotent, never raises. Returns how many."""
        try:
            if not self.enabled(settings):
                return 0
            model = self.embedder.model(settings)
            want = set(node_ids) if node_ids is not None else None
            todo = [n for n in self._stale(model) if want is None or n["id"] in want][:limit]
            if not todo:
                return 0
            vecs = await self.embedder.embed(settings, [node_text(n) for n in todo])
            if vecs is None or len(vecs) != len(todo):
                return 0
            t = time.time()
            with self.db.tx() as c:
                for n, v in zip(todo, vecs):
                    c.execute("INSERT OR REPLACE INTO kg_node_vectors(node_id,model,dim,vec,text_hash,updated_at) VALUES(?,?,?,?,?,?)",
                              (n["id"], model, int(v.shape[0]), pack(v), _hash(node_text(n)), t))
            return len(todo)
        except Exception:  # noqa: BLE001 - a failed index only costs recall, not the turn
            log.exception("graph node indexing failed")
            return 0

    def schedule(self, settings: dict[str, Any]) -> None:
        """Backfill in the background when something is waiting; never blocks or raises."""
        try:
            if not self.enabled(settings) or not self._stale(self.embedder.model(settings)):
                return
            task = asyncio.get_running_loop().create_task(self.index(settings))
        except RuntimeError:
            return  # no running loop (sync caller)
        except Exception:  # noqa: BLE001
            log.exception("graph index scheduling failed")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _visible(self, project_id: str | None, model: str) -> list[Any]:
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            return c.execute(
                f"""SELECT n.*, v.vec, v.dim FROM kg_node_vectors v JOIN kg_nodes n ON n.id=v.node_id
                    WHERE v.model=? AND {where.replace('project_id', 'n.project_id')} ORDER BY n.updated_at DESC LIMIT ?""",
                (model, *args, GRAPH_NODE_VECTOR_CAP)).fetchall()

    def similar(self, project_id: str | None, qvec: np.ndarray | None, model: str) -> dict[str, float]:
        """node_id -> cosine for visible nodes at or above GRAPH_MATCH_SIMILARITY."""
        if qvec is None or not model:
            return {}
        try:
            out: dict[str, float] = {}
            for r in self._visible(project_id, model):
                if r["dim"] != qvec.shape[0]:
                    continue
                s = float(unpack(r["vec"]) @ qvec)
                if s >= GRAPH_MATCH_SIMILARITY:
                    out[r["id"]] = s
            return out
        except Exception:  # noqa: BLE001
            log.exception("graph similarity scan failed")
            return {}

    async def nearest(self, settings: dict[str, Any], project_id: str | None, names_: list[str]) -> list[tuple[dict[str, Any], float] | None]:
        """For each name, the best visible entity at or above GRAPH_RESOLVE_SIMILARITY, else None. Never raises."""
        none: list[tuple[dict[str, Any], float] | None] = [None] * len(names_)
        try:
            if not names_ or not self.enabled(settings):
                return none
            vecs = await self.embedder.embed(settings, [n.strip() for n in names_])
            if vecs is None or len(vecs) != len(names_):
                return none
            rows = []
            for r in self._visible(project_id, self.embedder.model(settings)):
                node = row_to_dict(r, ("properties",)) or {}
                node.pop("vec", None), node.pop("dim", None)
                if node and not _skip(node):
                    rows.append((node, unpack(r["vec"])))
            out: list[tuple[dict[str, Any], float] | None] = []
            for q in vecs:
                best: tuple[dict[str, Any], float] | None = None
                for node, v in rows:
                    if v.shape != q.shape:
                        continue
                    s = float(v @ q)
                    if s >= GRAPH_RESOLVE_SIMILARITY and (best is None or s > best[1]):
                        best = (node, s)
                out.append(best)
            return out
        except Exception:  # noqa: BLE001
            log.exception("graph entity resolution failed")
            return none
