"""CRUD for projects, conversations, memories, knowledge graph, documents."""
from __future__ import annotations

import json
import re
from typing import Any

from .db import Database, new_id, now, row_to_dict


ALL = "__all__"  # sentinel: every scope (used by the library views)


def _scope_clause(project_id: str | None, include_global: bool = True) -> tuple[str, list[Any]]:
    """Items visible in a scope: the project's own items plus (optionally) global ones."""
    if project_id == ALL:
        return "1=1", []
    if project_id is None:
        return "project_id IS NULL", []
    if include_global:
        return "(project_id = ? OR project_id IS NULL)", [project_id]
    return "project_id = ?", [project_id]


def fts_query(text: str, max_terms: int = 12) -> str:
    """Turn free text into a forgiving FTS5 OR-query."""
    terms = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_'-]{2,}", text)
    seen: list[str] = []
    for t in terms:
        t = t.lower().strip("'-")
        if t and t not in seen:
            seen.append(t)
    seen = seen[:max_terms]
    return " OR ".join(f'"{t}"' for t in seen)


# ---------------- Projects ----------------
class Projects:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [row_to_dict(r, ("tools",)) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM projects WHERE id=?", (id,)).fetchone(), ("tools",))

    def create(self, name: str, description: str = "", system_prompt: str = "", color: str = "#d97757") -> dict[str, Any]:
        sid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO projects(id,name,description,system_prompt,color,created_at) VALUES(?,?,?,?,?,?)",
                (sid, name, description, system_prompt, color, now()),
            )
        return self.get(sid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {k: v for k, v in patch.items() if k in {"name", "description", "system_prompt", "color", "tools"} and v is not None}
        if "tools" in allowed:
            allowed["tools"] = json.dumps(allowed["tools"])
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            with self.db.tx() as c:
                c.execute(f"UPDATE projects SET {sets} WHERE id=?", (*allowed.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM projects WHERE id=?", (id,))

    def stats(self, project_id: str | None) -> dict[str, int]:
        where, args = _scope_clause(project_id, include_global=False)
        with self.db.tx() as c:
            q = lambda t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {where}", args).fetchone()[0]  # noqa: E731
            return {"conversations": q("conversations"), "memories": q("memories"), "nodes": q("kg_nodes"), "documents": q("documents")}


# ---------------- Conversations ----------------
DEFAULT_CONV_SETTINGS = {"useMemory": True, "useGraph": True, "useDocuments": True, "autoLearn": True, "useTools": True, "tools": {}}


class Conversations:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global=False)
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM conversations WHERE {where} ORDER BY updated_at DESC", args).fetchall()
        return [self._hydrate(r) for r in rows]

    def _hydrate(self, r: Any) -> dict[str, Any]:
        d = row_to_dict(r, ("settings",)) or {}
        d["settings"] = {**DEFAULT_CONV_SETTINGS, **(d.get("settings") or {})}
        return d

    def get(self, id: str, with_messages: bool = True) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM conversations WHERE id=?", (id,)).fetchone()
            if not r:
                return None
            d = self._hydrate(r)
            if with_messages:
                rows = c.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at, rowid", (id,)).fetchall()
                d["messages"] = [row_to_dict(m, ("context_used", "tool_events", "trace")) for m in rows]
        return d

    def create(self, project_id: str | None, title: str, model: str) -> dict[str, Any]:
        cid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO conversations(id,project_id,title,model,settings,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (cid, project_id, title, model, "{}", t, t),
            )
        return self.get(cid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if "title" in patch and patch["title"]:
                c.execute("UPDATE conversations SET title=? WHERE id=?", (patch["title"], id))
            if "model" in patch and patch["model"]:
                c.execute("UPDATE conversations SET model=? WHERE id=?", (patch["model"], id))
            if "settings" in patch and isinstance(patch["settings"], dict):
                cur = c.execute("SELECT settings FROM conversations WHERE id=?", (id,)).fetchone()
                merged = {**json.loads(cur["settings"] if cur else "{}"), **patch["settings"]}
                c.execute("UPDATE conversations SET settings=? WHERE id=?", (json.dumps(merged), id))
        return self.get(id, with_messages=False)

    def touch(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), id))

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM conversations WHERE id=?", (id,))

    def add_message(self, conv_id: str, role: str, content: str, model: str | None = None) -> dict[str, Any]:
        mid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO messages(id,conversation_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
                (mid, conv_id, role, content, model, t),
            )
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, conv_id))
        return {"id": mid, "conversation_id": conv_id, "role": role, "content": content, "model": model, "created_at": t, "error": None, "context_used": None, "tool_events": None, "trace": None}

    def finish_message(self, mid: str, content: str, error: str | None, context_used: dict[str, Any] | None, tool_events: list[dict[str, Any]] | None = None,
                       trace: list[dict[str, Any]] | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE messages SET content=?, error=?, context_used=?, tool_events=?, trace=? WHERE id=?",
                (content, error, json.dumps(context_used) if context_used else None, json.dumps(tool_events) if tool_events else None,
                 json.dumps(trace) if trace else None, mid),
            )

    def set_trace(self, mid: str, trace: list[dict[str, Any]]) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE messages SET trace=? WHERE id=?", (json.dumps(trace), mid))

    def delete_message(self, mid: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM messages WHERE id=?", (mid,))

    def history(self, conv_id: str) -> list[dict[str, str]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? AND content != '' ORDER BY created_at, rowid",
                (conv_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------------- Memories ----------------
class Memories:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None, q: str = "", include_global: bool = True) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global)
        with self.db.tx() as c:
            if q.strip():
                fq = fts_query(q)
                if not fq:
                    return []
                rows = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.memory_id
                        WHERE memories_fts MATCH ? AND {where.replace('project_id', 'm.project_id')}
                        ORDER BY bm25(memories_fts) LIMIT 100""",
                    (fq, *args),
                ).fetchall()
            else:
                rows = c.execute(f"SELECT * FROM memories WHERE {where} ORDER BY pinned DESC, updated_at DESC", args).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM memories WHERE id=?", (id,)).fetchone())

    def create(self, project_id: str | None, content: str, kind: str = "fact", source: str = "user", pinned: bool = False) -> dict[str, Any]:
        content = content.strip()
        with self.db.tx() as c:
            dup = c.execute(
                f"SELECT id FROM memories WHERE lower(content)=lower(?) AND {'project_id IS NULL' if project_id is None else 'project_id=?'}",
                (content,) if project_id is None else (content, project_id),
            ).fetchone()
            if dup:
                return self.get(dup["id"])  # type: ignore[return-value]
            mid = new_id()
            t = now()
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (mid, project_id, content, kind, source, int(pinned), t, t),
            )
            c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (content, mid))
        return self.get(mid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if "content" in patch and patch["content"] is not None:
                c.execute("UPDATE memories SET content=?, updated_at=? WHERE id=?", (patch["content"].strip(), now(), id))
                c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))
                c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (patch["content"].strip(), id))
            if "kind" in patch and patch["kind"]:
                c.execute("UPDATE memories SET kind=? WHERE id=?", (patch["kind"], id))
            if "pinned" in patch and patch["pinned"] is not None:
                c.execute("UPDATE memories SET pinned=? WHERE id=?", (int(bool(patch["pinned"])), id))
            if "project_id" in patch:
                c.execute("UPDATE memories SET project_id=? WHERE id=?", (patch["project_id"], id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM memories WHERE id=?", (id,))
            c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))

    def for_context(self, project_id: str | None, query: str, limit: int = 40) -> list[dict[str, Any]]:
        """Pinned + recent memories, plus FTS hits for the query, deduped."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            base = c.execute(f"SELECT * FROM memories WHERE {where} ORDER BY pinned DESC, updated_at DESC LIMIT ?", (*args, limit)).fetchall()
            hits: list[Any] = []
            fq = fts_query(query)
            if fq:
                hits = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.memory_id
                        WHERE memories_fts MATCH ? AND {where.replace('project_id', 'm.project_id')} ORDER BY bm25(memories_fts) LIMIT 15""",
                    (fq, *args),
                ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in [*hits, *base]:
            d = row_to_dict(r)
            if d:
                out.setdefault(d["id"], d)
        return list(out.values())[:limit]


# ---------------- Knowledge graph ----------------
class Graph:
    def __init__(self, db: Database):
        self.db = db

    def get(self, project_id: str | None, include_global: bool = True) -> dict[str, list[dict[str, Any]]]:
        where, args = _scope_clause(project_id, include_global)
        with self.db.tx() as c:
            nodes = c.execute(f"SELECT * FROM kg_nodes WHERE {where} ORDER BY created_at", args).fetchall()
            edges = c.execute(f"SELECT * FROM kg_edges WHERE {where} ORDER BY created_at", args).fetchall()
        return {
            "nodes": [row_to_dict(n, ("properties",)) for n in nodes],  # type: ignore[misc]
            "edges": [row_to_dict(e, ("properties",)) for e in edges],  # type: ignore[misc]
        }

    def get_node(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM kg_nodes WHERE id=?", (id,)).fetchone(), ("properties",))

    def find_node(self, project_id: str | None, label: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM kg_nodes WHERE IFNULL(project_id,'')=? AND lower(label)=lower(?)",
                (project_id or "", label.strip()),
            ).fetchone()
        return row_to_dict(r, ("properties",))

    def upsert_node(self, project_id: str | None, label: str, type: str = "entity", properties: dict[str, Any] | None = None) -> dict[str, Any]:
        label = label.strip()
        existing = self.find_node(project_id, label)
        if existing:
            if properties:
                merged = {**existing["properties"], **properties}
                with self.db.tx() as c:
                    c.execute("UPDATE kg_nodes SET properties=?, updated_at=? WHERE id=?", (json.dumps(merged), now(), existing["id"]))
                existing["properties"] = merged
            return existing
        nid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO kg_nodes(id,project_id,label,type,properties,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (nid, project_id, label, type or "entity", json.dumps(properties or {}), t, t),
            )
        return self.get_node(nid)  # type: ignore[return-value]

    def update_node(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if patch.get("label"):
                c.execute("UPDATE kg_nodes SET label=?, updated_at=? WHERE id=?", (patch["label"].strip(), now(), id))
            if patch.get("type"):
                c.execute("UPDATE kg_nodes SET type=?, updated_at=? WHERE id=?", (patch["type"], now(), id))
            if isinstance(patch.get("properties"), dict):
                c.execute("UPDATE kg_nodes SET properties=?, updated_at=? WHERE id=?", (json.dumps(patch["properties"]), now(), id))
        return self.get_node(id)

    def delete_node(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM kg_nodes WHERE id=?", (id,))

    def upsert_edge(self, project_id: str | None, source_id: str, target_id: str, relation: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        relation = relation.strip()
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?)",
                (source_id, target_id, relation),
            ).fetchone()
            if r:
                return row_to_dict(r, ("properties",))  # type: ignore[return-value]
            eid = new_id()
            c.execute(
                "INSERT INTO kg_edges(id,project_id,source_id,target_id,relation,properties,created_at) VALUES(?,?,?,?,?,?,?)",
                (eid, project_id, source_id, target_id, relation, json.dumps(properties or {}), now()),
            )
            r = c.execute("SELECT * FROM kg_edges WHERE id=?", (eid,)).fetchone()
        return row_to_dict(r, ("properties",))  # type: ignore[return-value]

    def update_edge(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if patch.get("relation"):
                c.execute("UPDATE kg_edges SET relation=? WHERE id=?", (patch["relation"].strip(), id))
            if isinstance(patch.get("properties"), dict):
                c.execute("UPDATE kg_edges SET properties=? WHERE id=?", (json.dumps(patch["properties"]), id))
            r = c.execute("SELECT * FROM kg_edges WHERE id=?", (id,)).fetchone()
        return row_to_dict(r, ("properties",))

    def delete_edge(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM kg_edges WHERE id=?", (id,))

    def neighborhood(self, project_id: str | None, query: str, max_nodes: int = 30) -> dict[str, list[dict[str, Any]]]:
        """Nodes whose label appears in the query (or vice-versa), plus 1-hop neighbours."""
        g = self.get(project_id)
        q = query.lower()
        words = set(re.findall(r"[a-z0-9][a-z0-9'-]{2,}", q))
        seeds = [
            n for n in g["nodes"]
            if n["label"].lower() in q or (len(n["label"]) > 3 and any(w == n["label"].lower() or w in n["label"].lower().split() for w in words))
        ]
        if not seeds:
            return {"nodes": [], "edges": []}
        seed_ids = {n["id"] for n in seeds}
        edges = [e for e in g["edges"] if e["source_id"] in seed_ids or e["target_id"] in seed_ids]
        keep = set(seed_ids)
        for e in edges:
            keep.add(e["source_id"])
            keep.add(e["target_id"])
        nodes = [n for n in g["nodes"] if n["id"] in keep][:max_nodes]
        kept_ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["source_id"] in kept_ids and e["target_id"] in kept_ids]
        return {"nodes": nodes, "edges": edges}


# ---------------- Documents ----------------
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= CHUNK_SIZE:
            cur = f"{cur}\n\n{p}" if cur else p
            continue
        if cur:
            chunks.append(cur)
        while len(p) > CHUNK_SIZE:
            chunks.append(p[:CHUNK_SIZE])
            p = p[CHUNK_SIZE - CHUNK_OVERLAP:]
        cur = p
    if cur:
        chunks.append(cur)
    return chunks


class Documents:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None, include_global: bool = True) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global)
        with self.db.tx() as c:
            rows = c.execute(
                f"SELECT id, project_id, name, mime, size, chunk_count, created_at, substr(text,1,300) AS preview FROM documents WHERE {where} ORDER BY created_at DESC",
                args,
            ).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM documents WHERE id=?", (id,)).fetchone())

    def create(self, project_id: str | None, name: str, mime: str, size: int, path: str, text: str) -> dict[str, Any]:
        did = new_id()
        chunks = chunk_text(text)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO documents(id,project_id,name,mime,size,path,text,chunk_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (did, project_id, name, mime, size, path, text, len(chunks), now()),
            )
            for i, ch in enumerate(chunks):
                cid = new_id()
                c.execute("INSERT INTO chunks(id,document_id,idx,text) VALUES(?,?,?,?)", (cid, did, i, ch))
                c.execute("INSERT INTO chunks_fts(text, chunk_id, document_id) VALUES(?,?,?)", (ch, cid, did))
        return self.get(did)  # type: ignore[return-value]

    def delete(self, id: str) -> str | None:
        with self.db.tx() as c:
            r = c.execute("SELECT path FROM documents WHERE id=?", (id,)).fetchone()
            c.execute("DELETE FROM chunks_fts WHERE document_id=?", (id,))
            c.execute("DELETE FROM documents WHERE id=?", (id,))
        return r["path"] if r else None

    def search(self, project_id: str | None, query: str, limit: int = 6) -> list[dict[str, Any]]:
        fq = fts_query(query)
        if not fq:
            return []
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT f.chunk_id, f.document_id, d.name, ch.idx, ch.text, bm25(chunks_fts) AS score
                    FROM chunks_fts f JOIN documents d ON d.id=f.document_id JOIN chunks ch ON ch.id=f.chunk_id
                    WHERE chunks_fts MATCH ? AND {where.replace('project_id', 'd.project_id')}
                    ORDER BY score LIMIT ?""",
                (fq, *args, limit),
            ).fetchall()
        return [dict(r) for r in rows]
