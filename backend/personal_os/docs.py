"""Docs: long-form markdown notes with a revision history and reviewable assistant edits.

Distinct from two neighbours that sound similar:
  * `documents` — files the user uploads, chunked for retrieval. Read-only knowledge.
  * `notes` — canvas mode's sticky notes: a body, a colour, no history.

A doc is something the user writes. Every change lands as a revision, so the editor can show a diff
and walk backwards. Assistant edits never touch `docs.content`: they land as a *pending* revision the
user accepts or rejects, which is what makes an LLM safe to point at prose someone cares about.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal, like todos and notes
  title TEXT NOT NULL DEFAULT 'Untitled',
  content TEXT NOT NULL DEFAULT '',
  folder TEXT NOT NULL DEFAULT '',
  starred INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_updated ON docs(updated_at DESC);

CREATE TABLE IF NOT EXISTS doc_revisions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  before TEXT NOT NULL DEFAULT '',
  after TEXT NOT NULL DEFAULT '',
  title_before TEXT,
  title_after TEXT,
  summary TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'assistant'
  tool TEXT,                                 -- tool that proposed it, when author='assistant'
  status TEXT NOT NULL DEFAULT 'applied',    -- 'applied' | 'pending' | 'rejected'
  created_at REAL NOT NULL,
  resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_rev_doc ON doc_revisions(doc_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rev_pending ON doc_revisions(status, created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, content, doc_id UNINDEXED, tokenize='porter unicode61'
);
"""

# A burst of keystrokes is one edit, not forty. Consecutive user revisions inside this window are
# folded into the newest one, so the history reads as sessions rather than as a keylogger.
COALESCE_SECONDS = 180.0


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def diff_stat(before: str, after: str) -> dict[str, int]:
    """Lines added/removed between two versions — cheap enough to compute on every list row."""
    added = removed = 0
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return {"added": added, "removed": removed}


def unified_diff(before: str, after: str, context: int = 3) -> str:
    return "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                             fromfile="before", tofile="after", n=context)
    )


class Docs:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---- indexing ----
    @staticmethod
    def _reindex(c: Any, doc_id: str, title: str, content: str) -> None:
        c.execute("DELETE FROM docs_fts WHERE doc_id=?", (doc_id,))
        c.execute("INSERT INTO docs_fts(title, content, doc_id) VALUES(?,?,?)", (title, content, doc_id))

    # ---- reads ----
    def list(self, project_id: str | None = "__all__", q: str = "") -> list[dict[str, Any]]:
        """Docs without their bodies: a preview is enough for a list, and bodies get long."""
        where, args = [], []
        if project_id != "__all__":
            if project_id is None:
                where.append("d.project_id IS NULL")
            else:
                where.append("d.project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("(d.title LIKE ? OR d.content LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = (
            "SELECT d.id, d.project_id, d.title, d.folder, d.starred, d.created_at, d.updated_at, d.content, "
            "  length(d.content) AS size, "
            "  (SELECT COUNT(*) FROM doc_revisions r WHERE r.doc_id=d.id AND r.status='pending') AS pending "
            "FROM docs d" + (" WHERE " + " AND ".join(where) if where else "") +
            " ORDER BY d.starred DESC, d.updated_at DESC"
        )
        with self.db.tx() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = d.pop("content") or ""  # the list shows a preview; bodies stay out of the payload
            out.append({**d, "preview": body[:240], "words": word_count(body)})
        return out

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            d = row_to_dict(c.execute("SELECT * FROM docs WHERE id=?", (id,)).fetchone())
            if not d:
                return None
            pending = [row_to_dict(r) for r in c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? AND status='pending' ORDER BY created_at", (id,)).fetchall()]
        d["words"] = word_count(d["content"])
        d["pending"] = [self._rev_view(r, d["content"]) for r in pending]  # type: ignore[arg-type]
        return d

    def find(self, name_or_id: str) -> dict[str, Any] | None:
        """Resolve what a model passed: an id, or a title (exact, then unique prefix/substring)."""
        key = (name_or_id or "").strip()
        if not key:
            return None
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM docs WHERE id=? OR lower(title)=lower(?)", (key, key)).fetchone()
            if not r:
                hits = c.execute("SELECT id FROM docs WHERE title LIKE ? COLLATE NOCASE LIMIT 2", (f"%{key}%",)).fetchall()
                if len(hits) != 1:
                    return None
                r = hits[0]
        return self.get(r["id"])

    def search(self, query: str, project_id: str | None = "__all__", limit: int = 10) -> list[dict[str, Any]]:
        """FTS over titles and bodies, with a snippet around the hit."""
        q = (query or "").strip()
        if not q:
            return []
        match = " OR ".join(f'"{w}"' for w in re.findall(r"\w+", q)) or f'"{q}"'
        with self.db.tx() as c:
            try:
                rows = c.execute(
                    "SELECT f.doc_id, snippet(docs_fts, 1, '', '', ' … ', 24) AS snippet, bm25(docs_fts) AS score "
                    "FROM docs_fts f WHERE docs_fts MATCH ? ORDER BY score LIMIT ?", (match, max(1, limit) * 3)).fetchall()
            except Exception:  # malformed FTS expression — fall back to LIKE
                rows = c.execute(
                    "SELECT id AS doc_id, substr(content,1,200) AS snippet, 0 AS score FROM docs "
                    "WHERE content LIKE ? OR title LIKE ? LIMIT ?", (f"%{q}%", f"%{q}%", max(1, limit) * 3)).fetchall()
            out = []
            for r in rows:
                d = c.execute("SELECT id, title, project_id FROM docs WHERE id=?", (r["doc_id"],)).fetchone()
                if not d:
                    continue
                if project_id != "__all__" and d["project_id"] != project_id:
                    continue
                out.append({"doc_id": d["id"], "title": d["title"], "snippet": (r["snippet"] or "").strip()})
                if len(out) >= limit:
                    break
        return out

    # ---- writes ----
    def create(self, title: str = "Untitled", content: str = "", project_id: str | None = None,
               folder: str = "", author: str = "user") -> dict[str, Any]:
        did = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO docs(id,project_id,title,content,folder,starred,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",
                (did, project_id, (title or "Untitled").strip()[:200], content, folder.strip(), t, t))
            self._reindex(c, did, title, content)
            if content:
                c.execute(
                    "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at,resolved_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'applied',?,?)",
                    (new_id(), did, "", content, None, title, "Created", author, None, t, t))
        return self.get(did)  # type: ignore[return-value]

    def save(self, id: str, content: str | None = None, title: str | None = None, summary: str = "",
             author: str = "user", coalesce: bool = True) -> dict[str, Any] | None:
        """Apply an edit straight to the doc and record it. Used by the editor's autosave."""
        cur = self.get(id)
        if not cur:
            return None
        new_content = cur["content"] if content is None else content
        new_title = cur["title"] if title is None else (title.strip()[:200] or "Untitled")
        if new_content == cur["content"] and new_title == cur["title"]:
            return cur
        t = now()
        with self.db.tx() as c:
            c.execute("UPDATE docs SET content=?, title=?, updated_at=? WHERE id=?", (new_content, new_title, t, id))
            self._reindex(c, id, new_title, new_content)
            last = c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? AND status='applied' ORDER BY created_at DESC LIMIT 1", (id,)).fetchone()
            fold = (coalesce and last is not None and last["author"] == author
                    and t - last["created_at"] < COALESCE_SECONDS and not last["summary"].startswith("Restored"))
            if fold:
                c.execute("UPDATE doc_revisions SET after=?, title_after=?, created_at=?, resolved_at=?, summary=? WHERE id=?",
                          (new_content, new_title, t, t, summary or last["summary"], last["id"]))
            else:
                c.execute(
                    "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at,resolved_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'applied',?,?)",
                    (new_id(), id, cur["content"], new_content, cur["title"], new_title, summary or "Edited", author, None, t, t))
        return self.get(id)

    def update_meta(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Title/folder/star/project moves that are not content edits, so they skip the history."""
        fields = {k: v for k, v in patch.items() if k in {"title", "folder", "starred", "project_id"}}
        if not fields:
            return self.get(id)
        if "title" in fields:
            fields["title"] = (str(fields["title"]).strip()[:200]) or "Untitled"
        if "starred" in fields:
            fields["starred"] = 1 if fields["starred"] else 0
        fields["updated_at"] = now()
        with self.db.tx() as c:
            c.execute(f"UPDATE docs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
            d = c.execute("SELECT title, content FROM docs WHERE id=?", (id,)).fetchone()
            if d:
                self._reindex(c, id, d["title"], d["content"])
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM docs WHERE id=?", (id,))
            c.execute("DELETE FROM docs_fts WHERE doc_id=?", (id,))

    # ---- revisions ----
    def _rev_view(self, r: dict[str, Any], current: str | None = None) -> dict[str, Any]:
        """A revision plus what the UI needs to render it without fetching the bodies twice."""
        base = r["before"] if current is None else current
        return {**r, "stat": diff_stat(r["before"], r["after"]),
                # A pending edit is reviewed against the doc as it stands now, not as it stood when
                # proposed — otherwise the diff lies whenever the user typed in between.
                "stale": current is not None and r["status"] == "pending" and current != r["before"],
                "stat_vs_current": diff_stat(base, r["after"]) if current is not None else None}

    def revisions(self, doc_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = [row_to_dict(r) for r in c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? ORDER BY created_at DESC LIMIT ?", (doc_id, limit)).fetchall()]
        return [self._rev_view(r) for r in rows]  # type: ignore[arg-type]

    def revision(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = row_to_dict(c.execute("SELECT * FROM doc_revisions WHERE id=?", (rev_id,)).fetchone())
        return self._rev_view(r) if r else None  # type: ignore[arg-type]

    def pending_count(self) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM doc_revisions WHERE status='pending'").fetchone()[0])

    def propose(self, doc_id: str, after: str, summary: str = "", tool: str | None = None,
                title_after: str | None = None) -> dict[str, Any] | None:
        """Record an assistant edit for review. The doc itself is untouched until `accept`."""
        cur = self.get(doc_id)
        if cur is None:
            return None
        rid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,'assistant',?,'pending',?)",
                (rid, doc_id, cur["content"], after, cur["title"], title_after, summary or "Assistant edit", tool, now()))
        return self.revision(rid)

    def accept(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM doc_revisions WHERE id=? AND status='pending'", (rev_id,)).fetchone()
            if not r:
                return None
            d = c.execute("SELECT * FROM docs WHERE id=?", (r["doc_id"],)).fetchone()
            if not d:
                return None
            t = now()
            title = r["title_after"] or d["title"]
            # `before` is rewritten to the content actually replaced, so "undo" after a stale accept
            # restores what the user had rather than what the model saw.
            c.execute("UPDATE doc_revisions SET status='applied', before=?, title_before=?, resolved_at=? WHERE id=?",
                      (d["content"], d["title"], t, rev_id))
            c.execute("UPDATE docs SET content=?, title=?, updated_at=? WHERE id=?", (r["after"], title, t, r["doc_id"]))
            self._reindex(c, r["doc_id"], title, r["after"])
            doc_id = r["doc_id"]
        return self.get(doc_id)

    def reject(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT doc_id FROM doc_revisions WHERE id=? AND status='pending'", (rev_id,)).fetchone()
            if not r:
                return None
            c.execute("UPDATE doc_revisions SET status='rejected', resolved_at=? WHERE id=?", (now(), rev_id))
            doc_id = r["doc_id"]
        return self.get(doc_id)

    def restore(self, rev_id: str) -> dict[str, Any] | None:
        """Roll the doc back to how a revision left it — itself recorded as a new revision."""
        r = self.revision(rev_id)
        if not r:
            return None
        return self.save(r["doc_id"], content=r["after"], title=r["title_after"],
                         summary=f"Restored revision from {r['created_at']:.0f}", author="user", coalesce=False)
