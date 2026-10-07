"""CRUD for projects, conversations, memories, knowledge graph, documents."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import blobs
from .db import Database, new_id, now, row_to_dict
from .migrations import sync_memories_fts
from .memory_limits import LEXICAL_HITS


ALL = "__all__"  # sentinel: every scope (used by the library views)


MEMORY_MODES = ("shared", "isolated")
# An isolated project ("this project only") never sees personal rows. Every scoped read goes through
# _scope_clause, so the check lives in its SQL rather than in each caller.
_ISOLATED_SQL = "EXISTS (SELECT 1 FROM projects WHERE id=? AND memory_mode='isolated')"


def _scope_clause(project_id: str | None, include_global: bool = True) -> tuple[str, list[Any]]:
    """Items visible in a scope: the project's own items plus (optionally) global ones, unless the project is isolated."""
    if project_id == ALL:
        return "1=1", []
    if project_id is None:
        return "project_id IS NULL", []
    if include_global:
        return f"(project_id = ? OR (project_id IS NULL AND NOT {_ISOLATED_SQL}))", [project_id, project_id]
    return "project_id = ?", [project_id]


def live_mem(alias: str = "") -> str:
    """SQL for a live memory: not superseded, not deleted, not past its expiry. `alias` is the table alias of
    `memories` in the query ("" for none). `now` is computed in SQL so no bind arguments are needed."""
    p = f"{alias}." if alias else ""
    return (f"({p}invalid_at IS NULL AND {p}deleted_at IS NULL AND "
            f"({p}expires_at IS NULL OR {p}expires_at > (julianday('now') - 2440587.5) * 86400.0))")


def is_isolated(db: Database, project_id: str | None) -> bool:
    """Whether a chat in this project must keep personal memory, docs, skills and voice out (and write none)."""
    if not project_id or project_id == ALL:
        return False
    with db.tx() as c:
        return bool(c.execute(f"SELECT {_ISOLATED_SQL}", (project_id,)).fetchone()[0])


def fts_query(text: str, max_terms: int = 12, prefix: bool = False) -> str:
    """Turn free text into a forgiving FTS5 OR-query.

    With prefix=True each term also matches longer words that start with it, so
    a search box filters as you type ("lite" finds "LiteLLM").
    """
    seen: list[str] = []
    for raw in re.findall(r"\w[\w'-]*", text):
        raw = raw.strip("'-_")
        # Short words are mostly noise ("of", "an"), except an acronym (AI), a code with a digit (Q3) or any
        # non-Latin word (café, Zürich, 会議), which the tokenizer indexes like any other.
        if not (len(raw) >= 3 or (len(raw) == 2 and (raw.isupper() or any(ch.isdigit() for ch in raw))) or not raw.isascii()):
            continue
        t = raw.lower()
        if t not in seen:
            seen.append(t)
            if len(seen) >= max_terms:  # the rest is dropped anyway; a pasted 150 KB message made this list quadratic
                break
    star = "*" if prefix else ""
    return " OR ".join(f'"{t}"{star}' for t in seen)


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")


def cjk_like(col: str, text: str) -> tuple[str, list[str]]:
    """A LIKE clause matching any CJK run of `text` inside `col`, or ("", []) when there is none. The tokenizer
    indexes a whole unspaced run as one token, so FTS never finds a word inside it.
    ponytail: LIKE scan over the scope; add a trigram FTS table if the corpus makes the scan slow."""
    runs = list(dict.fromkeys(_CJK.findall(text)))[:6]
    if not runs:
        return "", []
    return "(" + " OR ".join(f"{col} LIKE ?" for _ in runs) + ")", [f"%{r}%" for r in runs]


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

    def create(self, name: str, description: str = "", system_prompt: str = "", color: str = "#d97757",
               memory_mode: str = "shared") -> dict[str, Any]:
        sid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO projects(id,name,description,system_prompt,color,memory_mode,created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, name, description, system_prompt, color, memory_mode if memory_mode in MEMORY_MODES else "shared", now()),
            )
        return self.get(sid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {k: v for k, v in patch.items() if k in {"name", "description", "system_prompt", "color", "tools", "memory_mode"} and v is not None}
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
            soft = ("conversations", "memories", "documents", "docs")  # the tables that can sit in the trash
            q = lambda t: c.execute(f"SELECT COUNT(*) FROM {t} WHERE {where}" + (" AND deleted_at IS NULL" if t in soft else ""), args).fetchone()[0]  # noqa: E731
            has_docs = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()
            # documents = uploaded files; docs = what the user writes in Files (created by docs.py, absent in bare test DBs)
            return {"conversations": q("conversations"), "memories": q("memories"), "nodes": q("kg_nodes"), "documents": q("documents"),
                    "docs": q("docs") if has_docs else 0}


# ---------------- Conversations ----------------
# New chats start at low. The stored value "default" is a separate choice: it omits
# reasoning_effort, which on Kimi K3 means the model's own max. See llm.effort_param.
DEFAULT_EFFORT = "low"
DEFAULT_CONV_SETTINGS = {"effort": DEFAULT_EFFORT, "fast": False, "useMemory": True, "useGraph": True, "useDocuments": True,
                         "useStyle": True, "draftMode": False, "autoLearn": True, "useTools": True, "tools": {},
                         "responseStyle": "default", "responseStyleText": ""}
# Style banking needs no flag of its own: it is gated on the chat's autoLearn.


# Per attached file, how much extracted text goes into the user turn; the rest is reachable through read_document.
ATTACH_INLINE_CHARS = 40_000


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

    def search(self, q: str, limit: int = 20, per_conv: int = 3, project_id: str | None = ALL,
               exclude_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Conversations whose messages match `q`, best first, each with up to `per_conv` excerpts. Matched
        words are wrapped in \\x02 / \\x03. FTS (AND of the words, prefix on the last) for ASCII queries;
        a LIKE scan otherwise, because the tokenizer does not segment CJK. Trashed chats, superseded
        replies, desk and job transcripts are left out, as `list` leaves them out.

        A `project_id` other than ALL is the agent's recall (search_memory include_chats): that project's chats
        plus personal ones (None = personal only), minus `exclude_ids` and chats with memory off. Filtered in
        SQL, so the 300-row cap cannot be spent on another project's matches."""
        tokens = re.findall(r"\w+", q)
        if not tokens:
            return []
        scope, sargs = "", []
        if project_id != ALL:
            where, sargs = _scope_clause(project_id, include_global=True)
            scope = " AND " + where.replace("project_id", "c.project_id") + " AND COALESCE(json_extract(c.settings,'$.useMemory'),1) != 0"
            ex = [str(i) for i in exclude_ids or []]
            if ex:
                scope += f" AND c.id NOT IN ({','.join('?' * len(ex))})"
                sargs = [*sargs, *ex]
        base = ("FROM {src} JOIN conversations c ON c.id = m.conversation_id "
                "WHERE {cond} AND c.deleted_at IS NULL AND m.superseded_at IS NULL AND m.kind IS NULL "
                "AND COALESCE(json_extract(c.settings,'$.deskId'),'')='' AND COALESCE(json_extract(c.settings,'$.job_id'),'')='' "
                + scope)
        cols = ("m.id, m.conversation_id, m.role, m.created_at, c.title, c.project_id, c.updated_at, "
                "COALESCE(json_extract(c.settings,'$.tainted'),0) AS tainted")
        rows: list[Any] = []
        fts_ok = q.isascii() and any(len(t) >= 2 for t in tokens)
        with self.db.tx() as c:
            if fts_ok:
                match = " ".join(f'"{t}"' for t in tokens) + "*"
                try:
                    rows = c.execute(
                        f"SELECT {cols}, snippet(messages_fts,0,char(2),char(3),' … ',12) AS snip, bm25(messages_fts) AS score "
                        + base.format(src="messages_fts f JOIN messages m ON m.rowid = f.rowid", cond="messages_fts MATCH ?")
                        + " ORDER BY score LIMIT 300", (match, *sargs)).fetchall()
                except Exception:
                    rows = []
                    fts_ok = False
            if not fts_ok:
                esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = c.execute(
                    f"SELECT {cols}, m.content AS snip, 0 AS score "
                    + base.format(src="messages m", cond="m.content LIKE ? ESCAPE '\\'")
                    + " ORDER BY m.created_at DESC LIMIT 300", (f"%{esc}%", *sargs)).fetchall()
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
                                                    "updated_at": r["updated_at"], "tainted": bool(r["tainted"]), "hits": 0, "snippets": []}
            item["hits"] += 1
            if len(item["snippets"]) < per_conv:
                item["snippets"].append({"message_id": r["id"], "role": r["role"], "created_at": r["created_at"], "text": snip})
        return list(out.values())

    def _hydrate(self, r: Any) -> dict[str, Any]:
        d = row_to_dict(r, ("settings",)) or {}
        d["settings"] = {**DEFAULT_CONV_SETTINGS, **(d.get("settings") or {})}
        if d["settings"].get("learn") is False:  # stays in history and search; only what it teaches is off
            d["settings"]["autoLearn"] = False
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
                d["messages"] = [row_to_dict(m, ("context_used", "tool_events", "trace", "attachments", "followups")) for m in rows]
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

    def add_message(self, conv_id: str, role: str, content: str, model: str | None = None, *, variant_of: str | None = None,
                    attachments: list[dict[str, Any]] | None = None, kind: str | None = None) -> dict[str, Any]:
        mid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO messages(id,conversation_id,role,content,model,created_at,variant_of,attachments,kind) VALUES(?,?,?,?,?,?,?,?,?)",
                (mid, conv_id, role, content, model, t, variant_of, json.dumps(attachments) if attachments else None, kind),
            )
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, conv_id))
            # A row opened into an existing regenerate group announces its siblings, so the switcher shows on
            # the live event rather than after a reload.
            variants = self._variant_groups(c, conv_id).get(variant_of) if variant_of else None
        return {"id": mid, "conversation_id": conv_id, "role": role, "content": content, "model": model, "created_at": t, "error": None, "context_used": None, "tool_events": None, "trace": None, "reasoning": None,
                "outcome": None, "error_kind": None, "variant_of": variant_of, "variants": variants, "attachments": attachments or None, "kind": kind}

    def for_model(self, row: dict[str, Any]) -> str:
        """A message dict's content as the model reads it, attachments inlined (see model_content)."""
        atts = row.get("attachments")
        if not atts or row.get("role") == "assistant":
            return row.get("content") or ""
        with self.db.tx() as c:
            return self.model_content(c, row.get("content") or "", json.dumps(atts))

    def model_content(self, c: Any, content: str, attachments_json: str | None) -> str:
        """What the model reads for a user row: the text as typed, then each attached file's extracted text
        under a cap. Past the cap the model is told how to read the rest, so a long file still works."""
        if not attachments_json:
            return content
        try:
            atts = json.loads(attachments_json)
        except ValueError:
            return content
        parts = [content] if content else []
        for a in atts if isinstance(atts, list) else []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            row = c.execute("SELECT name, text FROM documents WHERE id=? AND deleted_at IS NULL", (a["id"],)).fetchone()
            name = str((row["name"] if row else None) or a.get("name") or "file")
            text = (row["text"] if row else "") or ""
            if len(text) > ATTACH_INLINE_CHARS:
                text = (text[:ATTACH_INLINE_CHARS] + f"\n[... {len(text) - ATTACH_INLINE_CHARS} more characters; "
                        f"read them with read_document id={a['id']} and an offset]")
            parts.append(f'<attached_file name="{name}" id="{a["id"]}">\n{text or "(no readable text)"}\n</attached_file>')
        return "\n\n".join(parts)

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
        row = row_to_dict(c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone(), ("context_used", "tool_events", "trace", "attachments", "followups")) or {}
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
                       outcome: str | None = None, error_kind: str | None = None, attachments: list[dict[str, Any]] | None = None) -> None:
        if context_used and context_used.get("chunks"):
            # Every saved reply goes through here: check its [n] against the full excerpts (adding quote/support in
            # place, so the 'done' event carries them too), then save a trimmed copy. The live refs keep their full
            # text, so a second finish on the same ledger (a parked card, a steer's next segment) checks the same.
            from .context import cite_check, cite_slim
            cite_check(content, context_used["chunks"])
            context_used = cite_slim(context_used)
        with self.db.tx() as c:
            c.execute(
                "UPDATE messages SET content=?, error=?, context_used=?, tool_events=?, trace=?, reasoning=?, "
                "outcome=COALESCE(?, outcome), error_kind=COALESCE(?, error_kind), attachments=COALESCE(?, attachments) WHERE id=?",
                (content, error, json.dumps(context_used) if context_used else None, json.dumps(tool_events) if tool_events else None,
                 json.dumps(trace) if trace else None, reasoning or None, outcome, error_kind, json.dumps(attachments) if attachments else None, mid),
            )

    def set_followups(self, mid: str, items: list[str]) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE messages SET followups=? WHERE id=?", (json.dumps(items), mid))

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

    # Settings a branch carries over. The rest stays behind on purpose: standing tool grants (the `tools` map's
    # "on" entries), skipPermissions, titles, and the desk/job markers.
    FORK_KEYS = (*[k for k in DEFAULT_CONV_SETTINGS if k != "tools"], "useSkills", "tainted", "taint_sources")

    def fork(self, conv_id: str, upto_mid: str) -> dict[str, Any]:
        """A new chat holding the live transcript up to and including `upto_mid`; the source is untouched. Only rows a
        reader sees are copied (superseded rows and inactive variants are not), with fresh ids and no variant links.
        Raises KeyError for a message not in this chat, ValueError for a superseded one."""
        cid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute("BEGIN IMMEDIATE")  # one snapshot: a reply finishing mid-copy must not split the prefix
            src = c.execute("SELECT * FROM conversations WHERE id=? AND deleted_at IS NULL", (conv_id,)).fetchone()
            tgt = c.execute("SELECT superseded_at FROM messages WHERE id=? AND conversation_id=?", (upto_mid, conv_id)).fetchone()
            if not src or not tgt:
                raise KeyError(upto_mid)
            if tgt["superseded_at"] is not None:
                raise ValueError(upto_mid)
            rows = c.execute("SELECT * FROM messages WHERE conversation_id=? AND superseded_at IS NULL ORDER BY created_at, rowid",
                             (conv_id,)).fetchall()
            rows = rows[:[r["id"] for r in rows].index(upto_mid) + 1]
            ss = self._hydrate(src)["settings"]
            st = {k: ss[k] for k in self.FORK_KEYS if k in ss}
            st["tools"] = {k: v for k, v in (ss.get("tools") or {}).items() if v != "on"}
            st.update(titleSource="auto", titleTurns=sum(r["role"] == "user" for r in rows), forkedFrom=conv_id)
            c.execute(
                "INSERT INTO conversations(id,project_id,title,model,settings,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (cid, src["project_id"], f"{src['title']} (branch)", src["model"], json.dumps(st), t, t))
            # Original timestamps keep the order (and the inserts keep rowid ties in order); later turns sort after.
            c.executemany(
                "INSERT INTO messages(id,conversation_id,role,content,model,error,context_used,tool_events,created_at,outcome,error_kind,kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(new_id(), cid, r["role"], r["content"], r["model"], r["error"], r["context_used"], r["tool_events"],
                  r["created_at"], r["outcome"], r["error_kind"], r["kind"]) for r in rows])
        return self.get(cid)  # type: ignore[return-value]

    def history(self, conv_id: str) -> list[dict[str, str]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT role, content, attachments FROM messages WHERE conversation_id=?\n"
                "AND (content != '' OR (role = 'user' AND attachments IS NOT NULL))\n"  # an assistant row's attachments are files it sent, not text to read
                "AND superseded_at IS NULL\n"
                "ORDER BY created_at, rowid",
                (conv_id,),
            ).fetchall()
            return [{"role": r["role"], "content": self.model_content(c, r["content"], r["attachments"] if r["role"] == "user" else None)} for r in rows]

    def history_rows(self, conv_id: str) -> list[dict[str, Any]]:
        """history() with the ids and timestamps compaction needs to say where a summary ends, and the tool events
        of an assistant row (a reply that only ran tools has no prose but still happened)."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id, role, content, created_at, tool_events, attachments FROM messages WHERE conversation_id=?\n"
                "AND (content != '' OR (role = 'user' AND attachments IS NOT NULL) OR (role = 'assistant' AND tool_events IS NOT NULL))\n"
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
                atts = d.pop("attachments")
                d["content"] = self.model_content(c, d["content"], atts if d["role"] == "user" else None)
                out.append(d)
        return out


# ---------------- Memories ----------------
class Memories:
    def __init__(self, db: Database):
        self.db = db

    def list(self, project_id: str | None, q: str = "", include_global: bool = True, include_invalid: bool = False) -> list[dict[str, Any]]:
        where, args = _scope_clause(project_id, include_global)
        # Superseded, retracted and expired rows are history: out of every default listing.
        with self.db.tx() as c:
            if q.strip():
                fq = fts_query(q, prefix=True)
                if not fq:
                    return []
                rows = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.memory_id
                        WHERE memories_fts MATCH ? AND {where.replace('project_id', 'm.project_id')} AND {'m.deleted_at IS NULL' if include_invalid else live_mem('m')}
                        ORDER BY bm25(memories_fts) LIMIT 100""",
                    (fq, *args),
                ).fetchall()
            else:
                rows = c.execute(f"SELECT * FROM memories WHERE {where} AND {'deleted_at IS NULL' if include_invalid else live_mem()} ORDER BY pinned DESC, updated_at DESC", args).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM memories WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())

    def create(self, project_id: str | None, content: str, kind: str = "fact", source: str = "user", pinned: bool = False,
               provenance: dict[str, Any] | None = None, expires_at: float | None = None) -> dict[str, Any]:
        content = content.strip()
        prov = provenance or {}
        with self.db.tx() as c:
            # The duplicate check and the insert must be one write: two creates of the same content at once (a
            # double-click) each saw no row and both inserted. IMMEDIATE takes the write lock before the SELECT.
            c.execute("BEGIN IMMEDIATE")
            dup = c.execute(
                f"SELECT id FROM memories WHERE {live_mem()} AND lower(content)=lower(?) AND {'project_id IS NULL' if project_id is None else 'project_id=?'}",
                (content,) if project_id is None else (content, project_id),
            ).fetchone()
            if dup:
                return self.get(dup["id"])  # type: ignore[return-value]
            mid = new_id()
            t = now()
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at,valid_from,source_conversation_id,source_message_id,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, project_id, content, kind, source, int(pinned), t, t, t,
                 prov.get("conversation_id"), prov.get("message_id"), expires_at),
            )
            c.execute("INSERT INTO memories_fts(content, memory_id) VALUES(?,?)", (content, mid))
        return self.get(mid)  # type: ignore[return-value]

    # ---- non-destructive changes: the old row stays as history, only `invalid_at` says it no longer holds ----
    def supersede(self, old_id: str, new_content: str, kind: str | None = None, source: str = "auto",
                  provenance: dict[str, Any] | None = None, keep_pinned: bool = False,
                  expires_at: float | None = None) -> dict[str, Any] | None:
        """Replace a memory with a new version, keeping the old one as history. Returns the new row.
        The new row expires at `expires_at` when given, else when the old one did.

        A pinned memory is user-curated: it is rewritten in place and stays valid, never archived, unless
        `keep_pinned` (the user's own edit), which versions it like any other row and pins the new one.
        """
        old = self.get(old_id)
        new_content = new_content.strip()
        if not old or old["invalid_at"] is not None or not new_content:
            return None
        if old["pinned"] and not keep_pinned:
            return self.update(old_id, {"content": new_content, **({"kind": kind} if kind else {}),
                                        **({"expires_at": expires_at} if expires_at is not None else {})})
        prov = provenance or {}
        mid, t = new_id(), now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at,valid_from,source_conversation_id,source_message_id,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, old["project_id"], new_content, kind or old["kind"], source, int(bool(old["pinned"])), t, t, t,
                 prov.get("conversation_id"), prov.get("message_id"), expires_at if expires_at is not None else old.get("expires_at")),
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
        t = now()
        # An expired row is history too: restoring it makes it live again, with no end date. A future expiry stays.
        if m.get("expires_at") is not None and m["expires_at"] <= t:
            m = self.update(id, {"expires_at": None}) or m
        if m["invalid_at"] is None:
            return m
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
                sync_memories_fts(c, [id])  # re-adds it only if live: editing a trashed or superseded row must not make it searchable
            if "kind" in patch and patch["kind"]:
                c.execute("UPDATE memories SET kind=? WHERE id=?", (patch["kind"], id))
            if "pinned" in patch and patch["pinned"] is not None:
                c.execute("UPDATE memories SET pinned=? WHERE id=?", (int(bool(patch["pinned"])), id))
            if "project_id" in patch:
                c.execute("UPDATE memories SET project_id=? WHERE id=?", (patch["project_id"], id))
            if "expires_at" in patch:  # None clears it
                c.execute("UPDATE memories SET expires_at=? WHERE id=?", (patch["expires_at"], id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM memories WHERE id=?", (id,))
            c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))

    def pinned(self, project_id: str | None) -> list[dict[str, Any]]:
        """Every live pinned memory in scope: a pin is always in context, whatever the query."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM memories WHERE {where} AND pinned=1 AND {live_mem()} ORDER BY updated_at DESC", args).fetchall()
        return [d for d in (row_to_dict(r) for r in rows) if d]

    def matching(self, project_id: str | None, query: str, limit: int = LEXICAL_HITS, include_global: bool = True) -> list[dict[str, Any]]:
        """Live memories that lexically match the query: FTS hits by bm25, then CJK substring hits (the tokenizer
        indexes an unspaced run as one token), deduped. Nothing else: no pins, no recency."""
        where, args = _scope_clause(project_id, include_global)
        with self.db.tx() as c:
            hits: list[Any] = []
            fq = fts_query(query)
            if fq:
                hits = c.execute(
                    f"""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.memory_id
                        WHERE memories_fts MATCH ? AND {where.replace('project_id', 'm.project_id')} AND {live_mem('m')} ORDER BY bm25(memories_fts) LIMIT ?""",
                    (fq, *args, limit),
                ).fetchall()
            like, largs = cjk_like("content", query)
            if like:
                hits += c.execute(f"SELECT * FROM memories WHERE {like} AND {where} AND {live_mem()} "
                                  "ORDER BY updated_at DESC LIMIT ?", (*largs, *args, limit)).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in hits:
            d = row_to_dict(r)
            if d:
                out.setdefault(d["id"], d)
        return list(out.values())[:limit]

    def profile(self, project_id: str | None) -> list[dict[str, Any]]:
        """The always-on standing preferences: live pinned rows and preference/instruction rows in scope, pins first
        then newest first. Fully ordered (id last) so the prompt prefix is byte-identical turn to turn."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM memories WHERE {where} AND {live_mem()} AND (pinned=1 OR kind IN ('preference','instruction')) "
                             "ORDER BY pinned DESC, COALESCE(valid_from, updated_at) DESC, id", args).fetchall()
        return [d for d in (row_to_dict(r) for r in rows) if d]

    def for_context(self, project_id: str | None, query: str, limit: int = 40) -> list[dict[str, Any]]:
        """Pinned + recent memories, plus FTS hits for the query, deduped."""
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            base = c.execute(f"SELECT * FROM memories WHERE {where} AND {live_mem()} ORDER BY pinned DESC, updated_at DESC LIMIT ?", (*args, limit)).fetchall()
        out: dict[str, dict[str, Any]] = {d["id"]: d for d in self.matching(project_id, query)}
        for r in base:
            d = row_to_dict(r)
            if d:
                out.setdefault(d["id"], d)
        # Pins lead (stable sort keeps hit order behind them) so neither the limit nor a window-share trim drops one.
        return sorted(out.values(), key=lambda d: not d.get("pinned"))[:limit]


# ---------------- Knowledge graph ----------------
SELF_LABEL = "User"


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

    def _self_scope(self, project_id: str | None) -> str | None:
        """An isolated project keeps its own user node (nothing is written to personal scope); every other scope shares the global one."""
        return project_id if project_id and project_id != ALL and is_isolated(self.db, project_id) else None

    def find_self_node(self, project_id: str | None = None) -> dict[str, Any] | None:
        """The node flagged properties.self in this scope; its label is free to change. Never creates."""
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM kg_nodes WHERE IFNULL(project_id,'')=? AND json_extract(properties,'$.self')=1 ORDER BY created_at LIMIT 1",
                          (self._self_scope(project_id) or "",)).fetchone()
        return row_to_dict(r, ("properties",))

    def self_node(self, project_id: str | None = None) -> dict[str, Any]:
        """The node standing for the user (facts about them are edges from it), created on first use."""
        n = self.find_self_node(project_id)
        if n:
            return n
        # An unflagged node labelled "User" is only flagged (a hand-made one); a renamed flagged node was found above.
        return self.upsert_node(self._self_scope(project_id), SELF_LABEL, "person", {"self": True})

    def value_node(self, project_id: str | None, value: str, create: bool = True) -> dict[str, Any] | None:
        """The node for a status or deadline value, shared by every edge with that value in the scope. None when an
        entity already has that name (it is never turned into a value) or, with create=False, when there is none."""
        n = self.find_node(project_id, value)
        if n:
            return n if (n.get("properties") or {}).get("literal") else None
        return self.upsert_node(project_id, value, "topic", {"literal": True}) if create else None

    def supersede_siblings(self, project_id: str | None, edge: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """`edge` has a single-valued relation: a new employer, status or home replaces the old one. Live edges from the
        same source with the same relation and another target end as of this edge's date. When one of them is NEWER
        (an old message replayed), it stands and `edge` is the one marked as history instead.
        Returns (the edges ended, whether `edge` itself was ended)."""
        when = edge.get("valid_at") or edge["created_at"]
        at = lambda e: e.get("valid_at") or e["created_at"]  # noqa: E731
        others = [o for o in self.get(project_id)["edges"]
                  if o["id"] != edge["id"] and o["source_id"] == edge["source_id"] and o["target_id"] != edge["target_id"]
                  and o["relation"].lower() == edge["relation"].lower()]
        newer = max((o for o in others if at(o) > when), key=at, default=None)
        if newer:
            self.invalidate_edge(edge["id"], at=at(newer), superseded_by=newer["id"])
            return [], True
        return [o for o in others if self.invalidate_edge(o["id"], at=when, superseded_by=edge["id"])], False

    def upsert_edge(self, project_id: str | None, source_id: str, target_id: str, relation: str, properties: dict[str, Any] | None = None,
                    valid_at: float | None = None, source_message_id: str | None = None, fact: str = "",
                    confidence: float | None = None) -> dict[str, Any]:
        relation = relation.strip()
        # ponytail: one edge per (source, target, relation), so a second related_to label for the same ordered pair
        # replaces the first's qualifier; key the row on the label too if two labels must coexist.
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?)",
                (source_id, target_id, relation),
            ).fetchone()
            if r:
                best = max((x for x in (r["confidence"], confidence) if x is not None), default=None)  # a re-assertion never lowers it
                if best != r["confidence"] or (fact and fact != r["fact"]):  # a newer qualifier ("head of X" -> "COO") replaces the old
                    c.execute("UPDATE kg_edges SET confidence=?, fact=? WHERE id=?", (best, fact or r["fact"], r["id"]))
                    r = c.execute("SELECT * FROM kg_edges WHERE id=?", (r["id"],)).fetchone()
                if r["invalid_at"] is not None:  # re-asserted: revive the old row rather than duplicate it
                    c.execute("UPDATE kg_edges SET invalid_at=NULL, superseded_by=NULL, valid_at=?, source_message_id=COALESCE(?, source_message_id) WHERE id=?",
                              (valid_at or now(), source_message_id, r["id"]))
                    r = c.execute("SELECT * FROM kg_edges WHERE id=?", (r["id"],)).fetchone()
                return row_to_dict(r, ("properties",))  # type: ignore[return-value]
            eid = new_id()
            t = now()
            c.execute(
                "INSERT INTO kg_edges(id,project_id,source_id,target_id,relation,properties,created_at,valid_at,source_message_id,fact,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (eid, project_id, source_id, target_id, relation, json.dumps(properties or {}), t, valid_at or t, source_message_id, fact, confidence),
            )
            r = c.execute("SELECT * FROM kg_edges WHERE id=?", (eid,)).fetchone()
        return row_to_dict(r, ("properties",))  # type: ignore[return-value]

    def update_edge(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if patch.get("relation"):
                c.execute("UPDATE kg_edges SET relation=? WHERE id=?", (patch["relation"].strip(), id))
            if isinstance(patch.get("properties"), dict):
                c.execute("UPDATE kg_edges SET properties=? WHERE id=?", (json.dumps(patch["properties"]), id))
            if isinstance(patch.get("fact"), str):
                c.execute("UPDATE kg_edges SET fact=? WHERE id=?", (patch["fact"], id))
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
            d = row_to_dict(c.execute("SELECT * FROM documents WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())
        if d:
            d["has_original"] = blobs.inside_uploads(self.db.data_dir, d.get("path")) is not None  # read-time, so no column to migrate
        return d

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
        like, largs = cjk_like("ch.text", query)
        if not fq and not like:
            return []
        where, args = _scope_clause(project_id)
        scope = where.replace('project_id', 'd.project_id')
        rows: list[Any] = []
        with self.db.tx() as c:
            if fq:
                rows = c.execute(
                    f"""SELECT f.chunk_id, f.document_id, d.name, ch.idx, ch.text, ch.heading, ch.page, bm25(chunks_fts) AS score
                        FROM chunks_fts f JOIN documents d ON d.id=f.document_id JOIN chunks ch ON ch.id=f.chunk_id
                        WHERE chunks_fts MATCH ? AND d.deleted_at IS NULL AND {scope}
                        ORDER BY score LIMIT ?""",
                    (fq, *args, limit),
                ).fetchall()
            if like and len(rows) < limit:
                rows += c.execute(
                    f"""SELECT ch.id AS chunk_id, ch.document_id, d.name, ch.idx, ch.text, ch.heading, ch.page, 0 AS score
                        FROM chunks ch JOIN documents d ON d.id=ch.document_id
                        WHERE {like} AND d.deleted_at IS NULL AND {scope} LIMIT ?""",
                    (*largs, *args, limit),
                ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:  # FTS rank first, LIKE hits after
            out.setdefault(r["chunk_id"], dict(r))
        return list(out.values())[:limit]
