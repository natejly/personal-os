"""CRUD for projects, conversations, memories, knowledge graph, documents."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
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


def fts_query(text: str, max_terms: int = 12, prefix: bool = False) -> str:
    """Turn free text into a forgiving FTS5 OR-query.

    With prefix=True each term also matches longer words that start with it, so
    a search box filters as you type ("lite" finds "LiteLLM").
    """
    terms = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_'-]{2,}", text)
    seen: list[str] = []
    for t in terms:
        t = t.lower().strip("'-")
        if t and t not in seen:
            seen.append(t)
    seen = seen[:max_terms]
    star = "*" if prefix else ""
    return " OR ".join(f'"{t}"{star}' for t in seen)


# ---------------- Projects ----------------
class Projects:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY created_at").fetchall()
        return [row_to_dict(r, ("tools",)) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM projects WHERE id=? AND deleted_at IS NULL", (id,)).fetchone(), ("tools",))

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

    def delete(self, id: str) -> list[str]:
        """Delete a project. The FK cascade drops its documents/memories rows but not their FTS rows
        or the uploaded files, so those are cleared here (files after the commit). Returns the paths."""
        with self.db.tx() as c:
            paths = [r["path"] for r in c.execute("SELECT path FROM documents WHERE project_id=?", (id,)).fetchall() if r["path"]]
            c.execute("DELETE FROM chunks_fts WHERE document_id IN (SELECT id FROM documents WHERE project_id=?)", (id,))
            c.execute("DELETE FROM memories_fts WHERE memory_id IN (SELECT id FROM memories WHERE project_id=?)", (id,))
            c.execute("DELETE FROM projects WHERE id=?", (id,))
        for p in paths:
            try:
                Path(p).unlink()
            except OSError:
                pass
        return paths

    def stats(self, project_id: str | None) -> dict[str, int]:
        where, args = _scope_clause(project_id, include_global=False)
        with self.db.tx() as c:
            soft = ("conversations", "memories", "documents")  # the tables that can sit in the trash
            q = lambda t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {where}" + (" AND deleted_at IS NULL" if t in soft else ""), args).fetchone()[0]  # noqa: E731
            return {"conversations": q("conversations"), "memories": q("memories"), "nodes": q("kg_nodes"), "documents": q("documents")}


# ---------------- Conversations ----------------
# useActivity/useMeetings are listed even though context.py reads them with a `.get(..., True)`
# fallback: without them the toggles never appear in a stored conversation's settings.
# New chats start at low. The stored value "default" is a separate choice: it omits
# reasoning_effort, which on Kimi K3 means the model's own max. See llm.effort_param.
DEFAULT_EFFORT = "low"
DEFAULT_CONV_SETTINGS = {"effort": DEFAULT_EFFORT, "fast": False, "useMemory": True, "useGraph": True, "useDocuments": True, "useActivity": True,
                         "useStyle": True, "draftMode": False, "useMeetings": True, "autoLearn": True, "useTools": True, "tools": {}}


class Conversations:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None, include_jobs: bool = False,
             include_desks: bool = False, archived: bool = False) -> list[dict[str, Any]]:
        """A scheduled job's transcript is a conversation too, but it is indexed by the Agent Inbox, not the
        sidebar: one daily job would otherwise bury the user's own chats within a month. A desk's
        transcript is hidden on the same grounds and always — Cowork is its index, and a desk that
        chains a dozen turns would otherwise own the whole of Recent. An archived chat is hidden from the list unless `archived` asks for exactly
        those. Order stays recency: pinned rows are partitioned by the renderer, because the home and
        recap callers slice this list and the sidebar groups it by date."""
        where, args = _scope_clause(project_id, include_global=False)
        arch = "IS NOT NULL" if archived else "IS NULL"
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM conversations WHERE {where} AND deleted_at IS NULL AND archived_at {arch} ORDER BY updated_at DESC", args).fetchall()
        out = [self._hydrate(r) for r in rows]
        if not include_desks:
            out = [c for c in out if not c["settings"].get("deskId")]
        return out if include_jobs else [c for c in out if not c["settings"].get("job_id")]

    def search(self, q: str, limit: int = 20, per_conv: int = 3) -> list[dict[str, Any]]:
        """Conversations whose messages match `q`, best first, each with up to `per_conv` excerpts. Matched
        words are wrapped in \\x02 / \\x03. FTS (AND of the words, prefix on the last) for ASCII queries;
        a LIKE scan otherwise, because the tokenizer does not segment CJK. Trashed chats, superseded
        replies, desk and job transcripts are left out, as `list` leaves them out."""
        tokens = re.findall(r"\w+", q)
        if not tokens:
            return []
        base = ("FROM {src} JOIN conversations c ON c.id = m.conversation_id "
                "WHERE {cond} AND c.deleted_at IS NULL AND m.superseded_at IS NULL "
                "AND COALESCE(json_extract(c.settings,'$.deskId'),'')='' AND COALESCE(json_extract(c.settings,'$.job_id'),'')=''")
        cols = "m.id, m.conversation_id, m.role, m.created_at, c.title, c.project_id, c.updated_at"
        rows: list[Any] = []
        fts_ok = q.isascii() and any(len(t) >= 2 for t in tokens)
        with self.db.tx() as c:
            if fts_ok:
                match = " ".join(f'"{t}"' for t in tokens) + "*"
                try:
                    rows = c.execute(
                        f"SELECT {cols}, snippet(messages_fts,0,char(2),char(3),' … ',12) AS snip, bm25(messages_fts) AS score "
                        + base.format(src="messages_fts f JOIN messages m ON m.rowid = f.rowid", cond="messages_fts MATCH ?")
                        + " ORDER BY score LIMIT 300", (match,)).fetchall()
                except Exception:
                    rows = []
                    fts_ok = False
            if not fts_ok:
                esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = c.execute(
                    f"SELECT {cols}, m.content AS snip, 0 AS score "
                    + base.format(src="messages m", cond="m.content LIKE ? ESCAPE '\\'")
                    + " ORDER BY m.created_at DESC LIMIT 300", (f"%{esc}%",)).fetchall()
        # Located by a case-insensitive regex, not by lowercasing: lower() can change a string's length
        # (a dotted capital I becomes two characters) and shift every offset.
        needle = re.compile(re.escape(q.strip()), re.IGNORECASE)
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            snip = r["snip"]
            if not fts_ok:
                m = needle.search(snip)
                if m is None:  # LIKE's own folding found it where Python's does not: head of the row, unmarked
                    snip = snip[:120] + (" …" if len(snip) > 120 else "")
                else:
                    i, e = m.start(), m.end()
                    a, b = max(0, i - 60), min(len(snip), e + 60)
                    snip = ("… " if a else "") + snip[a:i] + "\x02" + snip[i:e] + "\x03" + snip[e:b] + (" …" if b < len(snip) else "")
            item = out.get(r["conversation_id"])
            if item is None:
                if len(out) >= limit:
                    continue
                item = out[r["conversation_id"]] = {"id": r["conversation_id"], "title": r["title"], "project_id": r["project_id"],
                                                    "updated_at": r["updated_at"], "hits": 0, "snippets": []}
            item["hits"] += 1
            if len(item["snippets"]) < per_conv:
                item["snippets"].append({"message_id": r["id"], "role": r["role"], "created_at": r["created_at"], "text": snip})
        return list(out.values())

    def _hydrate(self, r: Any) -> dict[str, Any]:
        d = row_to_dict(r, ("settings",)) or {}
        d["settings"] = {**DEFAULT_CONV_SETTINGS, **(d.get("settings") or {})}
        return d

    def get(self, id: str, with_messages: bool = True) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM conversations WHERE id=? AND deleted_at IS NULL", (id,)).fetchone()
            if not r:
                return None
            d = self._hydrate(r)
            if with_messages:
                rows = c.execute(
                    "SELECT * FROM messages WHERE conversation_id=?\n"
                    "AND superseded_at IS NULL\n"
                    "ORDER BY created_at, rowid", (id,)).fetchall()
                d["messages"] = [row_to_dict(m, ("context_used", "tool_events", "trace")) for m in rows]
                self._attach_variants(c, id, d["messages"])
        return d

    @staticmethod
    def _variant_groups(c: Any, conv_id: str) -> dict[str, list[str]]:
        """Regenerate groups of a conversation: root id -> member ids in creation order (groups of one omitted)."""
        rows = c.execute(
            "SELECT id, variant_of FROM messages WHERE conversation_id=? AND (variant_of IS NOT NULL OR superseded_at IS NOT NULL "
            "OR id IN (SELECT variant_of FROM messages WHERE conversation_id=? AND variant_of IS NOT NULL)) "
            "ORDER BY created_at, rowid", (conv_id, conv_id)).fetchall()
        groups: dict[str, list[str]] = {}
        for r in rows:
            groups.setdefault(r["variant_of"] or r["id"], []).append(r["id"])
        return {k: v for k, v in groups.items() if len(v) > 1}

    def _attach_variants(self, c: Any, conv_id: str, messages: list[dict[str, Any]]) -> None:
        groups = self._variant_groups(c, conv_id)
        if not groups:
            return
        for m in messages:
            root = m.get("variant_of") or m["id"]
            if root in groups and m["id"] in groups[root]:
                m["variants"] = groups[root]

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
                # The read-merge-write must hold the write lock from the read on, or two writers
                # (a settings PATCH and the run's own taint mark) each merge into a stale copy.
                if not c.in_transaction:
                    c.execute("BEGIN IMMEDIATE")
                cur = c.execute("SELECT settings FROM conversations WHERE id=?", (id,)).fetchone()
                merged = {**json.loads(cur["settings"] if cur else "{}"), **patch["settings"]}
                c.execute("UPDATE conversations SET settings=? WHERE id=?", (json.dumps(merged), id))
            # None of these touch updated_at: filing a chat is not activity in it.
            if "pinned" in patch:
                c.execute("UPDATE conversations SET pinned_at=? WHERE id=?", (now() if patch["pinned"] else None, id))
            if "archived" in patch:
                # Archiving drops the pin, so an unarchived chat comes back as an ordinary one.
                if patch["archived"]:
                    c.execute("UPDATE conversations SET archived_at=?, pinned_at=NULL WHERE id=?", (now(), id))
                else:
                    c.execute("UPDATE conversations SET archived_at=NULL WHERE id=?", (id,))
            if "project_id" in patch:
                c.execute("UPDATE conversations SET project_id=? WHERE id=?", (patch["project_id"], id))
        return self.get(id, with_messages=False)

    def touch(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), id))

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM conversations WHERE id=?", (id,))

    def add_message(self, conv_id: str, role: str, content: str, model: str | None = None, *, variant_of: str | None = None) -> dict[str, Any]:
        mid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO messages(id,conversation_id,role,content,model,created_at,variant_of) VALUES(?,?,?,?,?,?,?)",
                (mid, conv_id, role, content, model, t, variant_of),
            )
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, conv_id))
            # A row opened into an existing regenerate group announces its siblings, so the switcher shows on
            # the live event rather than after a reload.
            variants = self._variant_groups(c, conv_id).get(variant_of) if variant_of else None
        return {"id": mid, "conversation_id": conv_id, "role": role, "content": content, "model": model, "created_at": t, "error": None, "context_used": None, "tool_events": None, "trace": None, "reasoning": None,
                "outcome": None, "error_kind": None, "variant_of": variant_of, "variants": variants}

    MAX_VARIANTS = 5

    def begin_variant(self, old_id: str, model: str | None = None) -> dict[str, Any]:
        """Regenerate without losing the answer: hide `old_id` and open an empty sibling in one transaction, so a
        group always has exactly one active row. Keeps the newest MAX_VARIANTS superseded rows."""
        mid = new_id()
        t = now()
        with self.db.tx() as c:
            old = c.execute("SELECT * FROM messages WHERE id=?", (old_id,)).fetchone()
            if not old:
                raise KeyError(old_id)
            root = old["variant_of"] or old["id"]
            c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (t, old_id))
            c.execute(
                "INSERT INTO messages(id,conversation_id,role,content,model,created_at,variant_of) VALUES(?,?,?,?,?,?,?)",
                (mid, old["conversation_id"], "assistant", "", model, t, root),
            )
            # Newest by when it was hidden, not by when it was written: the answer the user was just looking at
            # (an old variant they switched back to) is the one a failed replacement must be able to restore.
            stale = c.execute(
                "SELECT id FROM messages WHERE (id=? OR variant_of=?) AND superseded_at IS NOT NULL "
                "ORDER BY superseded_at DESC, created_at DESC, rowid DESC",
                (root, root)).fetchall()[self.MAX_VARIANTS:]
            for r in stale:
                c.execute("DELETE FROM messages WHERE id=?", (r["id"],))
            if root in {r["id"] for r in stale}:
                # The root row was pruned: re-key the survivors so `id = root OR variant_of = root` still holds.
                keep = c.execute("SELECT id FROM messages WHERE variant_of=? ORDER BY created_at, rowid LIMIT 1", (root,)).fetchone()
                c.execute("UPDATE messages SET variant_of=? WHERE variant_of=?", (keep["id"], root))
                c.execute("UPDATE messages SET variant_of=NULL WHERE id=?", (keep["id"],))
                root = keep["id"]
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, old["conversation_id"]))
            variants = self._variant_groups(c, old["conversation_id"]).get(root)
        return {"id": mid, "conversation_id": old["conversation_id"], "role": "assistant", "content": "", "model": model, "created_at": t, "error": None,
                "context_used": None, "tool_events": None, "trace": None, "reasoning": None, "outcome": None, "error_kind": None, "variant_of": root,
                "variants": variants}

    def _hydrated_row(self, c: Any, conv_id: str, mid: str) -> dict[str, Any]:
        row = row_to_dict(c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone(), ("context_used", "tool_events", "trace")) or {}
        self._attach_variants(c, conv_id, [row])
        return row

    def restore_if_empty(self, conv_id: str) -> dict[str, Any] | None:
        """A regenerate that produced nothing: drop the empty replacement and bring the newest superseded sibling back."""
        with self.db.tx() as c:
            last = c.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND superseded_at IS NULL ORDER BY created_at DESC, rowid DESC LIMIT 1", (conv_id,)).fetchone()
            if not last or last["role"] != "assistant" or not last["variant_of"] or last["content"] or last["tool_events"]:
                return None
            root = last["variant_of"]
            prev = c.execute(
                "SELECT id FROM messages WHERE (id=? OR variant_of=?) AND superseded_at IS NOT NULL ORDER BY superseded_at DESC, created_at DESC, rowid DESC LIMIT 1",
                (root, root)).fetchone()
            if not prev:
                return None
            c.execute("DELETE FROM messages WHERE id=?", (last["id"],))
            c.execute("UPDATE messages SET superseded_at=NULL WHERE id=?", (prev["id"],))
            return self._hydrated_row(c, conv_id, prev["id"])

    def activate_message(self, conv_id: str, mid: str) -> bool:
        """Switch the trailing answer to another variant of its group. Only the last turn can switch: later turns
        were built on the answer they follow."""
        with self.db.tx() as c:
            tgt = c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=?", (mid, conv_id)).fetchone()
            if not tgt or tgt["superseded_at"] is None:
                return False
            root = tgt["variant_of"] or tgt["id"]
            last = c.execute(
                "SELECT id FROM messages WHERE conversation_id=? AND superseded_at IS NULL ORDER BY created_at DESC, rowid DESC LIMIT 1", (conv_id,)).fetchone()
            active = c.execute(
                "SELECT id FROM messages WHERE (id=? OR variant_of=?) AND superseded_at IS NULL", (root, root)).fetchone()
            if not last or not active or active["id"] != last["id"]:
                return False
            c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (now(), active["id"]))
            c.execute("UPDATE messages SET superseded_at=NULL WHERE id=?", (mid,))
            return True

    def finish_message(self, mid: str, content: str, error: str | None, context_used: dict[str, Any] | None, tool_events: list[dict[str, Any]] | None = None,
                       trace: list[dict[str, Any]] | None = None, reasoning: str | None = None, *,
                       outcome: str | None = None, error_kind: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE messages SET content=?, error=?, context_used=?, tool_events=?, trace=?, reasoning=?, "
                "outcome=COALESCE(?, outcome), error_kind=COALESCE(?, error_kind) WHERE id=?",
                (content, error, json.dumps(context_used) if context_used else None, json.dumps(tool_events) if tool_events else None,
                 json.dumps(trace) if trace else None, reasoning or None, outcome, error_kind, mid),
            )

    def set_trace(self, mid: str, trace: list[dict[str, Any]]) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE messages SET trace=? WHERE id=?", (json.dumps(trace), mid))

    def delete_message(self, mid: str, conv_id: str | None = None) -> bool:
        """Delete a message and, when it belongs to a regenerate group, every variant of it."""
        with self.db.tx() as c:
            row = c.execute("SELECT id, variant_of, conversation_id FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or (conv_id is not None and row["conversation_id"] != conv_id):
                return False
            root = row["variant_of"] or row["id"]
            return c.execute("DELETE FROM messages WHERE id=? OR variant_of=?", (root, root)).rowcount > 0

    def supersede_from(self, conv_id: str, mid: str) -> list[dict[str, Any]]:
        """Hide `mid` (a live user row) and every live row after it, with one stamp, in one transaction.

        Nothing is deleted: the rows stay on disk, out of every transcript reader. Returns the hidden rows as
        {id, created_at, role, tool_events} in transcript order; [] when `mid` is not a live user row here."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM messages WHERE conversation_id=? AND superseded_at IS NULL ORDER BY created_at, rowid",
                             (conv_id,)).fetchall()
            ids = [r["id"] for r in rows]
            if mid not in ids or rows[ids.index(mid)]["role"] != "user":
                return []
            cut = rows[ids.index(mid):]
            t = now()
            c.execute(f"UPDATE messages SET superseded_at=? WHERE id IN ({','.join('?' * len(cut))})", (t, *[r["id"] for r in cut]))
            hidden = [row_to_dict(r, ("tool_events",)) or {} for r in cut]
        return [{"id": r["id"], "created_at": r["created_at"], "role": r["role"], "tool_events": r.get("tool_events") or []} for r in hidden]

    def history(self, conv_id: str) -> list[dict[str, str]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? AND content != ''\n"
                "AND superseded_at IS NULL\n"
                "ORDER BY created_at, rowid",
                (conv_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def history_rows(self, conv_id: str) -> list[dict[str, Any]]:
        """history() with the ids and timestamps compaction needs to say where a summary ends, and the tool events
        of an assistant row (a reply that only ran tools has no prose but still happened)."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id, role, content, created_at, tool_events FROM messages WHERE conversation_id=?\n"
                "AND (content != '' OR (role = 'assistant' AND tool_events IS NOT NULL))\n"
                "AND superseded_at IS NULL\n"
                "ORDER BY created_at, rowid",
                (conv_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                ev = json.loads(d["tool_events"]) if d["tool_events"] else None
            except ValueError:
                ev = None
            d["tool_events"] = ev if isinstance(ev, list) else None
            out.append(d)
        return out


# ---------------- Memories ----------------
class Memories:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None, q: str = "", include_global: bool = True, include_invalid: bool = False) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global)
        # Superseded / retracted rows (invalid_at set) are history: out of every default listing.
        live = "" if include_invalid else " AND invalid_at IS NULL"
        with self.db.tx() as c:
            if q.strip():
                fq = fts_query(q, prefix=True)
                if not fq:
                    return []
                rows = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.memory_id
                        WHERE memories_fts MATCH ? AND m.deleted_at IS NULL AND {where.replace('project_id', 'm.project_id')}{live.replace('invalid_at', 'm.invalid_at')}
                        ORDER BY bm25(memories_fts) LIMIT 100""",
                    (fq, *args),
                ).fetchall()
            else:
                rows = c.execute(f"SELECT * FROM memories WHERE {where} AND deleted_at IS NULL{live} ORDER BY pinned DESC, updated_at DESC", args).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())

    def create(self, project_id: str | None, content: str, kind: str = "fact", source: str = "user", pinned: bool = False,
               provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        content = content.strip()
        prov = provenance or {}
        with self.db.tx() as c:
            dup = c.execute(
                f"SELECT id FROM memories WHERE invalid_at IS NULL AND deleted_at IS NULL AND lower(content)=lower(?) AND {'project_id IS NULL' if project_id is None else 'project_id=?'}",
                (content,) if project_id is None else (content, project_id),
            ).fetchone()
            if dup:
                return self.get(dup["id"])  # type: ignore[return-value]
            mid = new_id()
            t = now()
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at,valid_from,source_conversation_id,source_message_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (mid, project_id, content, kind, source, int(pinned), t, t, t,
                 prov.get("conversation_id"), prov.get("message_id")),
            )
            c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (content, mid))
        return self.get(mid)  # type: ignore[return-value]

    # ---- non-destructive changes: the old row stays as history, only `invalid_at` says it no longer holds ----
    def supersede(self, old_id: str, new_content: str, kind: str | None = None, source: str = "auto",
                  provenance: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Replace a memory with a new version, keeping the old one as history. Returns the new row.

        A pinned memory is user-curated: it is rewritten in place and stays valid, never archived.
        """
        old = self.get(old_id)
        new_content = new_content.strip()
        if not old or old["invalid_at"] is not None or not new_content:
            return None
        if old["pinned"]:
            return self.update(old_id, {"content": new_content, **({"kind": kind} if kind else {})})
        prov = provenance or {}
        mid, t = new_id(), now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at,valid_from,source_conversation_id,source_message_id) "
                "VALUES(?,?,?,?,?,0,?,?,?,?,?)",
                (mid, old["project_id"], new_content, kind or old["kind"], source, t, t, t,
                 prov.get("conversation_id"), prov.get("message_id")),
            )
            c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (new_content, mid))
            c.execute("UPDATE memories SET invalid_at=?, superseded_by=? WHERE id=?", (t, mid, old_id))
            c.execute("DELETE FROM memories_fts WHERE memory_id=?", (old_id,))
        return self.get(mid)

    def invalidate(self, id: str) -> dict[str, Any] | None:
        """Soft forget: the row leaves context and search but stays in history. Pinned rows are refused (None)."""
        m = self.get(id)
        if not m or m["pinned"] or m["invalid_at"] is not None:
            return None
        with self.db.tx() as c:
            c.execute("UPDATE memories SET invalid_at=? WHERE id=?", (now(), id))
            c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))
        return self.get(id)

    def restore(self, id: str) -> dict[str, Any] | None:
        """Bring a superseded or forgotten row back. If its replacement is still live, that one steps aside."""
        m = self.get(id)
        if not m:
            return None
        if m["invalid_at"] is None:
            return m
        t = now()
        with self.db.tx() as c:
            nxt = m["superseded_by"]
            if nxt:
                c.execute("UPDATE memories SET invalid_at=? WHERE id=? AND invalid_at IS NULL", (t, nxt))
                c.execute("DELETE FROM memories_fts WHERE memory_id=?", (nxt,))
            c.execute("UPDATE memories SET invalid_at=NULL, superseded_by=NULL, updated_at=? WHERE id=?", (t, id))
            c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))
            c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (m["content"], id))
        return self.get(id)

    def history(self, id: str) -> list[dict[str, Any]]:
        """The supersession chain through `id`, oldest first."""
        with self.db.tx() as c:
            first = row_to_dict(c.execute("SELECT * FROM memories WHERE id=?", (id,)).fetchone())
            if not first:
                return []
            chain, seen = [first], {id}
            while True:  # predecessors
                r = c.execute("SELECT * FROM memories WHERE superseded_by=?", (chain[0]["id"],)).fetchone()
                if not r or r["id"] in seen:
                    break
                seen.add(r["id"])
                chain.insert(0, row_to_dict(r))  # type: ignore[arg-type]
            while chain[-1]["superseded_by"] and chain[-1]["superseded_by"] not in seen:  # successors
                r = c.execute("SELECT * FROM memories WHERE id=?", (chain[-1]["superseded_by"],)).fetchone()
                if not r:
                    break
                seen.add(r["id"])
                chain.append(row_to_dict(r))  # type: ignore[arg-type]
        return chain

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if "content" in patch and patch["content"] is not None:
                if not str(patch["content"]).strip():
                    raise ValueError("Memory content cannot be empty")
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

    def pinned(self, project_id: str | None) -> list[dict[str, Any]]:
        """Every live pinned memory in scope: a pin is always in context, whatever the query."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM memories WHERE {where} AND pinned=1 AND invalid_at IS NULL AND deleted_at IS NULL ORDER BY updated_at DESC", args).fetchall()
        return [d for d in (row_to_dict(r) for r in rows) if d]

    def for_context(self, project_id: str | None, query: str, limit: int = 40) -> list[dict[str, Any]]:
        """Pinned + recent memories, plus FTS hits for the query, deduped."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            base = c.execute(f"SELECT * FROM memories WHERE {where} AND invalid_at IS NULL AND deleted_at IS NULL ORDER BY pinned DESC, updated_at DESC LIMIT ?", (*args, limit)).fetchall()
            hits: list[Any] = []
            fq = fts_query(query)
            if fq:
                hits = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.memory_id
                        WHERE memories_fts MATCH ? AND m.deleted_at IS NULL AND {where.replace('project_id', 'm.project_id')} AND m.invalid_at IS NULL ORDER BY bm25(memories_fts) LIMIT 15""",
                    (fq, *args),
                ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in [*hits, *base]:
            d = row_to_dict(r)
            if d:
                out.setdefault(d["id"], d)
        # Pins lead (stable sort keeps hit order behind them) so neither the limit nor a budget trim drops one.
        return sorted(out.values(), key=lambda d: not d.get("pinned"))[:limit]


# ---------------- Knowledge graph ----------------
class Graph:
    def __init__(self, db: Database):
        self.db = db

    def get(self, project_id: str | None, include_global: bool = True, include_invalid: bool = False) -> dict[str, list[dict[str, Any]]]:
        where, args = _scope_clause(project_id, include_global)
        live = "" if include_invalid else " AND invalid_at IS NULL"
        with self.db.tx() as c:
            nodes = c.execute(f"SELECT * FROM kg_nodes WHERE {where} ORDER BY created_at", args).fetchall()
            edges = c.execute(f"SELECT * FROM kg_edges WHERE {where}{live} ORDER BY created_at", args).fetchall()
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

    def upsert_edge(self, project_id: str | None, source_id: str, target_id: str, relation: str, properties: dict[str, Any] | None = None,
                    valid_at: float | None = None, source_message_id: str | None = None, fact: str = "") -> dict[str, Any]:
        relation = relation.strip()
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?)",
                (source_id, target_id, relation),
            ).fetchone()
            if r:
                if r["invalid_at"] is not None:  # re-asserted: revive the old row rather than duplicate it
                    c.execute("UPDATE kg_edges SET invalid_at=NULL, superseded_by=NULL, valid_at=?, source_message_id=COALESCE(?, source_message_id) WHERE id=?",
                              (valid_at or now(), source_message_id, r["id"]))
                    r = c.execute("SELECT * FROM kg_edges WHERE id=?", (r["id"],)).fetchone()
                return row_to_dict(r, ("properties",))  # type: ignore[return-value]
            eid = new_id()
            t = now()
            c.execute(
                "INSERT INTO kg_edges(id,project_id,source_id,target_id,relation,properties,created_at,valid_at,source_message_id,fact) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (eid, project_id, source_id, target_id, relation, json.dumps(properties or {}), t, valid_at or t, source_message_id, fact),
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

    def invalidate_edge(self, id: str, at: float | None = None, superseded_by: str | None = None) -> dict[str, Any] | None:
        """The relation stopped holding: keep the row as history instead of deleting it."""
        with self.db.tx() as c:
            c.execute("UPDATE kg_edges SET invalid_at=?, superseded_by=? WHERE id=? AND invalid_at IS NULL", (at or now(), superseded_by, id))
            r = c.execute("SELECT * FROM kg_edges WHERE id=?", (id,)).fetchone()
        return row_to_dict(r, ("properties",))

    def find_edge(self, source_id: str, target_id: str, relation: str, live_only: bool = True) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?)" + (" AND invalid_at IS NULL" if live_only else ""),
                (source_id, target_id, relation.strip()),
            ).fetchone()
        return row_to_dict(r, ("properties",))

    def delete_edge(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM kg_edges WHERE id=?", (id,))

    def neighborhood(self, project_id: str | None, query: str, max_nodes: int = 30) -> dict[str, list[dict[str, Any]]]:
        """Nodes whose label appears in the query as whole words (or vice-versa), plus 1-hop neighbours."""
        g = self.get(project_id)
        q = query.lower()
        words = set(re.findall(r"[a-z0-9][a-z0-9'-]{2,}", q))
        seeds = [
            n for n in g["nodes"]
            if re.search(rf"(?<!\w){re.escape(n['label'].lower())}(?!\w)", q) or (len(n["label"]) > 3 and any(w == n["label"].lower() or w in n["label"].lower().split() for w in words))
        ]
        if not seeds:
            return {"nodes": [], "edges": []}
        seed_ids = {n["id"] for n in seeds}
        edges = [e for e in g["edges"] if e["source_id"] in seed_ids or e["target_id"] in seed_ids]
        keep = set(seed_ids)
        for e in edges:
            keep.add(e["source_id"])
            keep.add(e["target_id"])
        # Seeds first, so the cap trims neighbours rather than the entities the query named.
        nodes = (seeds + [n for n in g["nodes"] if n["id"] in keep and n["id"] not in seed_ids])[:max_nodes]
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
        # Called after a document's chunks are stored: (document_id, [(chunk_id, text)]). The app wires
        # it to background embedding, so an upload never waits on (or fails because of) the model.
        self.on_chunks: Any = None

    def list(self, project_id: str | None, include_global: bool = True) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global)
        with self.db.tx() as c:
            rows = c.execute(
                f"SELECT id, project_id, name, mime, size, chunk_count, created_at, pinned, substr(text,1,300) AS preview FROM documents WHERE {where} AND deleted_at IS NULL ORDER BY created_at DESC",
                args,
            ).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM documents WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())

    def set_pinned(self, id: str, pinned: bool) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE documents SET pinned=? WHERE id=? AND deleted_at IS NULL", (int(pinned), id))
        return self.get(id)

    def pinned(self, project_id: str | None) -> list[dict[str, Any]]:
        """Pinned documents (full text) visible in a scope: the project's own plus global ones."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM documents WHERE pinned=1 AND {where} AND deleted_at IS NULL ORDER BY created_at", args).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    @staticmethod
    def _build_chunks(name: str, text: str, blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Structure-aware chunks; plain paragraph packing if the chunker cannot cope."""
        try:
            from .chunker import chunk_blocks
            from .extract_text import markdown_blocks

            out = chunk_blocks(blocks if blocks else markdown_blocks(text), title=name)
            if out:
                return [{"text": ch.text, "heading": ch.heading_str, "page": ch.page, "ctx": ch.ctx} for ch in out]
        except Exception:  # noqa: BLE001 - a chunker bug must not lose an upload
            pass
        return [{"text": ch, "heading": "", "page": None, "ctx": ch} for ch in chunk_text(text)]

    def _store_chunks(self, c: Any, did: str, chunks: list[dict[str, Any]]) -> list[tuple[str, str]]:
        stored: list[tuple[str, str]] = []
        for i, ch in enumerate(chunks):
            cid = new_id()
            c.execute("INSERT INTO chunks(id,document_id,idx,text,heading,page) VALUES(?,?,?,?,?,?)",
                      (cid, did, i, ch["text"], ch["heading"], ch["page"]))
            # FTS indexes the contextualised string (title + heading path + text); chunks.text stays the clean excerpt.
            c.execute("INSERT INTO chunks_fts(text, chunk_id, document_id) VALUES(?,?,?)", (ch["ctx"], cid, did))
            stored.append((cid, ch["text"]))
        return stored

    def create(self, project_id: str | None, name: str, mime: str, size: int, path: str, text: str,
               blocks: list[dict[str, Any]] | None = None, content_hash: str | None = None) -> dict[str, Any]:
        did = new_id()
        chunks = self._build_chunks(name, text, blocks)
        digest = content_hash if content_hash is not None else hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO documents(id,project_id,name,mime,size,path,text,chunk_count,created_at,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (did, project_id, name, mime, size, path, text, len(chunks), now(), digest),
            )
            stored = self._store_chunks(c, did, chunks)
        if self.on_chunks and stored:
            try:
                self.on_chunks(did, stored)
            except Exception:  # noqa: BLE001 - indexing is best effort
                pass
        return self.get(did)  # type: ignore[return-value]

    def find_by_hash(self, project_id: str | None, content_hash: str) -> dict[str, Any] | None:
        """An existing document with these exact bytes in exactly this scope (None = personal)."""
        if not content_hash:
            return None
        where, args = ("project_id IS NULL", []) if project_id is None else ("project_id=?", [project_id])
        with self.db.tx() as c:
            return row_to_dict(c.execute(f"SELECT * FROM documents WHERE content_hash=? AND deleted_at IS NULL AND {where} LIMIT 1", (content_hash, *args)).fetchone())

    def reindex(self, id: str | None = None) -> int:
        """Re-chunk from the stored upload (or, without a file, the stored text read as markdown).
        Keeps document ids; embeddings cascade away with the old chunks and are re-made by the next backfill."""
        from .extract_text import extract_structured

        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM documents" + (" WHERE id=?" if id else ""), (id,) if id else ()).fetchall()
        total = 0
        for d in rows:
            blocks = None
            digest = d["content_hash"]
            p = Path(d["path"]) if d["path"] else None
            if p is not None and p.is_file():
                try:
                    data = p.read_bytes()
                    blocks = extract_structured(d["name"], data, d["mime"])
                    digest = digest or hashlib.sha256(data).hexdigest()
                except Exception:  # noqa: BLE001 - fall back to the stored text
                    blocks = None
            chunks = self._build_chunks(d["name"], d["text"], blocks)
            with self.db.tx() as c:
                c.execute("DELETE FROM chunks_fts WHERE document_id=?", (d["id"],))
                c.execute("DELETE FROM chunks WHERE document_id=?", (d["id"],))
                stored = self._store_chunks(c, d["id"], chunks)
                c.execute("UPDATE documents SET chunk_count=?, content_hash=? WHERE id=?", (len(chunks), digest, d["id"]))
            total += len(chunks)
            if self.on_chunks and stored:
                try:
                    self.on_chunks(d["id"], stored)
                except Exception:  # noqa: BLE001
                    pass
        return total

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
                f"""SELECT f.chunk_id, f.document_id, d.name, ch.idx, ch.text, ch.heading, ch.page, bm25(chunks_fts) AS score
                    FROM chunks_fts f JOIN documents d ON d.id=f.document_id JOIN chunks ch ON ch.id=f.chunk_id
                    WHERE chunks_fts MATCH ? AND d.deleted_at IS NULL AND {where.replace('project_id', 'd.project_id')}
                    ORDER BY score LIMIT ?""",
                (fq, *args, limit),
            ).fetchall()
        return [dict(r) for r in rows]
