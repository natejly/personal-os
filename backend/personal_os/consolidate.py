"""Memory consolidation: the "dream pass", reviewable and approve-only.

Memory rots. Near-duplicates pile up, "next month" stays "next month" forever, and the graph holds
"Postgres" and "PostgreSQL" as two things. This module finds those, asks the model to *propose* a
tidier version, and stores the proposal. Nothing is ever applied here by itself: `propose` only
writes `memory_proposals` rows, and `apply` is called only from a user route. Pinned memories are
never candidates. The model sees memory and graph text only and answers with short ids (C1, N1, ...)
that are mapped back server-side, so it cannot name a row it was not shown.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from itertools import combinations
from typing import Any

from . import llm, redact
from .db import Database, new_id, now, row_to_dict
from .learn import _parse_json
from .repos import ALL, Graph, Memories

log = logging.getLogger("personal_os")


def _line(text: Any, limit: int = 2000) -> str:
    """One line. A memory or a label sits in the proposal prompt, so a newline cannot open a new group."""
    return " ".join(str(text or "").replace("\r", " ").split())[:limit]


def _shown(text: Any, limit: int = 2000) -> str:
    """One line, with credentials removed. The stored memory stays as written."""
    return _line(redact.scrub_command_output(str(text or "")), limit)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_proposals (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                       -- merge_memories | rewrite_memory | merge_entities
  payload TEXT NOT NULL,                    -- JSON: ids, proposed text/label, a snapshot for the stale check
  rationale TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | applied | dismissed | stale
  created_at REAL NOT NULL,
  decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mem_proposals_status ON memory_proposals(status, created_at DESC);
"""

KINDS = ("merge_memories", "rewrite_memory", "merge_entities")
MAX_CANDIDATES = 20
BATCH = 10
JACCARD = 0.6
ROT_AGE_DAYS = 30
ROT_RE = re.compile(r"\b(tomorrow|tonight|yesterday|next (?:week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                    r"this (?:week|month|year|weekend|friday|monday)|upcoming|soon|currently|right now|these days)\b", re.I)
TITLES = {"dr", "mr", "mrs", "ms", "prof", "the"}

PROMPT = """You tidy a personal memory store. You are shown groups of candidates; each item has a short id like [C1] (memory) or [N1] (graph entity).
Return ONLY a JSON object:
{"proposals": [{"kind": "merge_memories|rewrite_memory|merge_entities", "ids": ["C1","C2"], "text": "merged or rewritten memory", "label": "canonical entity name", "rationale": "one short sentence"}]}

Rules:
- merge_memories: only when the items state the SAME fact. Write one memory in third person keeping the most specific wording. Never combine different facts and never invent anything.
- rewrite_memory: one memory ("ids" has one id) that uses a relative time (tomorrow, next month, currently...). Rewrite it with an absolute date ONLY when one can be derived from the date the memory was saved (shown as saved=YYYY-MM-DD); otherwise propose nothing.
- merge_entities: exactly two [N] ids that are the same real-world thing. "label" is the best name for it.
- Ids in one proposal must come from the same group. Propose nothing when unsure; an empty list is a good answer.
"""


def _norm_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard_sets(ta: set[str], tb: set[str]) -> float:
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _norm_label(label: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", label.lower()) if t not in TITLES]
    return " ".join(toks)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def alias_like(a: str, b: str) -> bool:
    """Two entity labels that plausibly name the same thing."""
    na, nb = _norm_label(a), _norm_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta:  # "Priya" / "Priya Shah"
        return True
    ca, cb = na.replace(" ", ""), nb.replace(" ", "")
    short, long_ = sorted((ca, cb), key=len)
    if len(short) >= 6 and edit_distance(ca, cb) <= 1:
        return True
    return len(short) >= 5 and long_.startswith(short)  # "Postgres" / "PostgreSQL"


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


class Consolidator:
    def __init__(self, db: Database, memories: Memories, graph: Graph) -> None:
        self.db, self.memories, self.graph = db, memories, graph
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------------- storage ----------------
    def list(self, status: str | None = "pending", project_id: str | None = ALL) -> list[dict[str, Any]]:
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if project_id != ALL:
            where.append("project_id IS ?" if project_id is None else "project_id=?")
            if project_id is not None:
                args.append(project_id)
        sql = "SELECT * FROM memory_proposals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC"
        with self.db.tx() as c:
            return [row_to_dict(r, ("payload",)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM memory_proposals WHERE id=?", (id,)).fetchone(), ("payload",))

    def _set_status(self, id: str, status: str) -> dict[str, Any]:
        with self.db.tx() as c:
            c.execute("UPDATE memory_proposals SET status=?, decided_at=? WHERE id=?", (status, now(), id))
        return self.get(id)  # type: ignore[return-value]

    def dismiss(self, id: str) -> dict[str, Any] | None:
        p = self.get(id)
        if not p or p["status"] != "pending":
            return p
        return self._set_status(id, "dismissed")

    def _known_sets(self) -> set[frozenset[str]]:
        """Id sets that already have a pending or dismissed proposal: not proposed again."""
        with self.db.tx() as c:
            rows = c.execute("SELECT payload FROM memory_proposals WHERE status IN ('pending','dismissed')").fetchall()
        out: set[frozenset[str]] = set()
        for r in rows:
            try:
                out.add(frozenset(json.loads(r["payload"]).get("ids") or []))
            except ValueError:
                pass
        return out

    # ---------------- candidates (deterministic, no model) ----------------
    def memory_candidates(self, project_id: str | None = ALL, t: float | None = None) -> list[dict[str, Any]]:
        """Groups: {'type': 'dup'|'rot', 'items': [memory rows]}."""
        t = t or time.time()
        rows = [m for m in self.memories.list(project_id, include_global=project_id != ALL) if not m["pinned"]]
        out: list[dict[str, Any]] = []
        uf = _UF()
        toks = {m["id"]: _norm_tokens(m["content"]) for m in rows}  # once per memory, not once per pair
        for a, b in combinations(rows, 2):
            if a["project_id"] == b["project_id"] and _jaccard_sets(toks[a["id"]], toks[b["id"]]) >= JACCARD:
                uf.union(a["id"], b["id"])
        groups: dict[str, list[dict[str, Any]]] = {}
        for m in rows:
            if m["id"] in uf.p:
                groups.setdefault(uf.find(m["id"]), []).append(m)
        out += [{"type": "dup", "items": g} for g in groups.values() if len(g) > 1]
        cutoff = t - ROT_AGE_DAYS * 86400
        out += [{"type": "rot", "items": [m]} for m in rows
                if m["source"] == "auto" and m["created_at"] < cutoff and ROT_RE.search(m["content"])]
        return out

    def entity_candidates(self, project_id: str | None = ALL) -> list[dict[str, Any]]:
        g = self.graph.get(project_id, include_global=project_id != ALL)
        degree: dict[str, int] = {}
        for e in g["edges"]:
            for k in (e["source_id"], e["target_id"]):
                degree[k] = degree.get(k, 0) + 1
        out = []
        for a, b in combinations(g["nodes"], 2):
            if a["project_id"] == b["project_id"] and a["type"] == b["type"] and alias_like(a["label"], b["label"]):
                out.append({"type": "entity", "items": [{**a, "degree": degree.get(a["id"], 0)}, {**b, "degree": degree.get(b["id"], 0)}]})
        return out

    def candidates(self, project_id: str | None = ALL) -> list[dict[str, Any]]:
        known = self._known_sets()
        out = [c for c in [*self.memory_candidates(project_id), *self.entity_candidates(project_id)]
               if frozenset(i["id"] for i in c["items"]) not in known]
        return out[:MAX_CANDIDATES]

    # ---------------- propose ----------------
    async def propose(self, settings: dict[str, Any], project_id: str | None, model: str) -> list[dict[str, Any]]:
        """Ask the model about each batch of candidates and store what survives validation as *pending*."""
        # The pair scans are O(n^2): off the event loop so a long memory list never stalls streaming replies.
        cands = await asyncio.to_thread(self.candidates, project_id)
        created: list[dict[str, Any]] = []
        known = self._known_sets()
        for i in range(0, len(cands), BATCH):
            batch = cands[i:i + BATCH]
            alias: dict[str, dict[str, Any]] = {}
            group_of: dict[str, int] = {}
            lines: list[str] = []
            for gi, cand in enumerate(batch, 1):
                lines.append(f"Group {gi} ({cand['type']}):")
                for it in cand["items"]:
                    tag = f"N{len(alias) + 1}" if cand["type"] == "entity" else f"C{len(alias) + 1}"
                    alias[tag] = it
                    group_of[tag] = gi
                    if cand["type"] == "entity":
                        lines.append(f"  [{tag}] {_shown(it.get('label'), 200)} ({_line(it.get('type'), 40)}, {it['degree']} relations)")
                    else:
                        saved = time.strftime("%Y-%m-%d", time.localtime(it["created_at"]))
                        lines.append(f"  [{tag}] saved={saved}: {_shown(it.get('content'))}")
            extraction_model = settings.get("extractionModel") or model
            try:
                raw = await llm.complete(settings, extraction_model, [{"role": "system", "content": PROMPT},
                                                                      {"role": "user", "content": "\n".join(lines)}], "learn")
            except Exception:  # noqa: BLE001 - a dead model means no proposals, never a half-written state
                log.exception("consolidation call failed")
                continue
            for p in (_parse_json(raw).get("proposals") or []):
                made = self._validate(p, alias, group_of, known)
                if made:
                    created.append(self._store(made))
                    known.add(frozenset(made["payload"]["ids"]))
        return created

    def _validate(self, p: Any, alias: dict[str, dict[str, Any]], group_of: dict[str, int], known: set[frozenset[str]]) -> dict[str, Any] | None:
        if not isinstance(p, dict) or p.get("kind") not in KINDS or not isinstance(p.get("ids"), list):
            return None
        tags = [str(x).strip() for x in p["ids"]]
        if not tags or len(set(tags)) != len(tags) or any(t not in alias for t in tags) or len({group_of[t] for t in tags}) != 1:
            return None
        items = [alias[t] for t in tags]
        kind, text = p["kind"], str(p.get("text") or "").strip()
        rationale = str(p.get("rationale") or "").strip()[:300]
        if kind in ("merge_memories", "rewrite_memory"):
            if not all(t.startswith("C") for t in tags) or any(it.get("pinned") for it in items) or len(text) < 6:
                return None
            if (kind == "merge_memories") != (len(items) > 1) or any(it["invalid_at"] is not None for it in items):
                return None
            if kind == "rewrite_memory" and text == items[0]["content"]:
                return None
            snap = {it["id"]: it["content"] for it in items}
            payload: dict[str, Any] = {"ids": [it["id"] for it in items], "text": text, "snapshot": snap}
        else:
            if len(items) != 2 or not all(t.startswith("N") for t in tags):
                return None
            a, b = items
            label = str(p.get("label") or "").strip()
            if label.lower() == b["label"].lower():
                keep, lose = b, a
            elif label.lower() == a["label"].lower():
                keep, lose = a, b
            else:
                keep, lose = sorted(items, key=lambda n: (-n["degree"], n["created_at"]))
            payload = {"ids": [keep["id"], lose["id"]], "keep_id": keep["id"], "lose_id": lose["id"], "label": label or keep["label"],
                       "snapshot": {keep["id"]: keep["label"], lose["id"]: lose["label"]}}
        if frozenset(payload["ids"]) in known:
            return None
        return {"project_id": items[0]["project_id"], "kind": kind, "payload": payload, "rationale": rationale}

    def _store(self, made: dict[str, Any]) -> dict[str, Any]:
        pid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO memory_proposals(id,project_id,kind,payload,rationale,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
                      (pid, made["project_id"], made["kind"], json.dumps(made["payload"]), made["rationale"], now()))
        return self.get(pid)  # type: ignore[return-value]

    # ---------------- apply (user route only) ----------------
    def apply(self, id: str) -> dict[str, Any] | None:
        p = self.get(id)
        if not p or p["status"] != "pending":
            return p
        pl = p["payload"]
        try:
            if p["kind"] == "merge_entities":
                ok = self._apply_entities(pl)
            else:
                ok = self._apply_memories(p["kind"], pl)
        except Exception:  # noqa: BLE001
            log.exception("applying proposal %s failed", id)
            ok = False
        return self._set_status(id, "applied" if ok else "stale")

    def _apply_memories(self, kind: str, pl: dict[str, Any]) -> bool:
        rows = [self.memories.get(i) for i in pl["ids"]]
        # Stale: a row vanished, changed, was pinned or already retired since the proposal was made.
        if any(r is None or r["pinned"] or r["invalid_at"] is not None or r["content"] != pl["snapshot"].get(r["id"]) for r in rows):
            return False
        first = rows[0]
        merged = self.memories.supersede(first["id"], pl["text"], source="auto")
        if not merged:
            return False
        for r in rows[1:]:
            self.memories.invalidate(r["id"])
            with self.db.tx() as c:  # chain the merged-in rows to the result so history shows where they went
                c.execute("UPDATE memories SET superseded_by=? WHERE id=?", (merged["id"], r["id"]))
        return True

    def _apply_entities(self, pl: dict[str, Any]) -> bool:
        keep, lose = self.graph.get_node(pl["keep_id"]), self.graph.get_node(pl["lose_id"])
        if not keep or not lose or keep["label"] != pl["snapshot"].get(keep["id"]) or lose["label"] != pl["snapshot"].get(lose["id"]):
            return False
        with self.db.tx() as c:
            edges = c.execute("SELECT * FROM kg_edges WHERE source_id=? OR target_id=?", (lose["id"], lose["id"])).fetchall()
            for e in edges:
                s = keep["id"] if e["source_id"] == lose["id"] else e["source_id"]
                t = keep["id"] if e["target_id"] == lose["id"] else e["target_id"]
                clash = s != t and c.execute(
                    "SELECT 1 FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?) AND id<>?",
                    (s, t, e["relation"], e["id"])).fetchone()
                if s == t or clash:
                    c.execute("DELETE FROM kg_edges WHERE id=?", (e["id"],))
                else:
                    c.execute("UPDATE kg_edges SET source_id=?, target_id=? WHERE id=?", (s, t, e["id"]))
            props = {**lose["properties"], **keep["properties"]}
            aliases = list(dict.fromkeys([*(props.get("aliases") or []), lose["label"]]))
            props["aliases"] = aliases
            c.execute("DELETE FROM kg_nodes WHERE id=?", (lose["id"],))
            new_label = (pl.get("label") or keep["label"]).strip()
            clash_label = new_label.lower() != keep["label"].lower() and c.execute(
                "SELECT 1 FROM kg_nodes WHERE IFNULL(project_id,'')=? AND lower(label)=lower(?) AND id<>?",
                (keep["project_id"] or "", new_label, keep["id"])).fetchone()
            c.execute("UPDATE kg_nodes SET properties=?, label=?, updated_at=? WHERE id=?",
                      (json.dumps(props), keep["label"] if clash_label else new_label, now(), keep["id"]))
        return True
