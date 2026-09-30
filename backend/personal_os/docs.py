"""Authored documents: the editor's docs, with append-only version history.

Not repos.py's Documents (uploaded RAG sources): a doc is text the user or an agent writes in the
app, so every body change is kept as a version and can be diffed or restored, like artifacts.py
does for generated HTML. `preview` and `chars` are derived from the body on the way out, never
stored, so the two can't drift.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from .db import Database, new_id, now, row_to_dict

# Owned here, not by db.py: Database._migrate runs before Docs(db) exists (see artifacts.py).
SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal scope, like notes and todos
  title TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT 'md',        -- FORMATS
  body TEXT NOT NULL DEFAULT '',            -- current text, denormalised so a read is one row
  version INTEGER NOT NULL DEFAULT 0,       -- MAX(doc_versions.version); 0 until the first body write
  agent_conv_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,  -- conversation acting as this doc's editing agent
  drive_file_id TEXT,                       -- Google Doc this was last backed up to
  drive_synced_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_updated ON docs(updated_at DESC);
CREATE TABLE IF NOT EXISTS doc_versions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,                 -- 1-based, derived from docs.version so pruning cannot reuse a number
  body TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'user',      -- who wrote it: user | agent | restore
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dv_version ON doc_versions(doc_id, version);
"""

FORMATS = ("md", "txt", "tex")

MAX_BODY_CHARS = 1_000_000
MAX_VERSIONS = 100  # oldest versions are pruned; numbers still only ever go up
PREVIEW_CHARS = 160


def _meta(row: dict[str, Any] | None, with_body: bool = False) -> dict[str, Any] | None:
    """Row → DocMeta (src/shared/types.ts): derive preview/chars, and keep the body only when asked."""
    if row is None:
        return None
    body = row.pop("body", "")
    row["preview"] = re.sub(r"\s+", " ", body[: PREVIEW_CHARS * 4]).strip()[:PREVIEW_CHARS]
    row["chars"] = len(body)
    if with_body:
        row["body"] = body
    return row


def _check_body(body: str) -> str:
    body = body or ""
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"Document is {len(body)} characters, over the {MAX_BODY_CHARS} limit")
    return body


class Docs:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------- docs ----------
    def list(self, project_id: str | None = "__all__", q: str = "") -> list[dict[str, Any]]:
        where, args = [], []
        if project_id != "__all__":
            if project_id is None:
                where.append("project_id IS NULL")
            else:
                where.append("project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("(title LIKE ? OR body LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = "SELECT * FROM docs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC"
        with self.db.tx() as c:
            return [_meta(row_to_dict(r)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return _meta(row_to_dict(c.execute("SELECT * FROM docs WHERE id=?", (id,)).fetchone()), with_body=True)

    def create(self, title: str = "", body: str = "", format: str = "md",
               project_id: str | None = None, source: str = "user") -> dict[str, Any]:
        """Create a doc. A non-empty body is stored as version 1, so history starts at the beginning."""
        if format not in FORMATS:
            raise ValueError(f"Unknown doc format '{format}'")
        body = _check_body(body)
        did = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO docs(id,project_id,title,format,body,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (did, project_id, (title or "Untitled").strip()[:200] or "Untitled", format, "", 0, t, t),
            )
        if body:
            return self.save_version(did, body, source=source)  # type: ignore[return-value]
        return self.get(did)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Metadata in place; a body change goes through save_version so nothing is overwritten."""
        fields: dict[str, Any] = {}
        if "title" in patch:
            fields["title"] = str(patch["title"] or "Untitled").strip()[:200] or "Untitled"
        if "format" in patch:
            if patch["format"] not in FORMATS:
                raise ValueError(f"Unknown doc format '{patch['format']}'")
            fields["format"] = patch["format"]
        for k in ("project_id", "agent_conv_id"):
            if k in patch:
                fields[k] = patch[k]
        if fields:
            fields["updated_at"] = now()
            sets = ", ".join(f"{k}=?" for k in fields)
            with self.db.tx() as c:
                c.execute(f"UPDATE docs SET {sets} WHERE id=?", (*fields.values(), id))
        if "body" in patch and patch["body"] is not None:
            return self.save_version(id, patch["body"], source=patch.get("source") or "user")
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM docs WHERE id=?", (id,))

    def mark_backed_up(self, id: str, drive_file_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE docs SET drive_file_id=?, drive_synced_at=? WHERE id=?", (drive_file_id, now(), id))
        return self.get(id)

    # ---------- versions ----------
    def save_version(self, id: str, body: str, source: str = "user") -> dict[str, Any] | None:
        """Append a version and point the doc at it. The number comes from docs.version, which never decreases."""
        body = _check_body(body)
        t = now()
        with self.db.tx() as c:
            row = c.execute("SELECT version, body FROM docs WHERE id=?", (id,)).fetchone()
            if not row:
                return None
            if row["body"] == body and int(row["version"]) > 0:
                return self.get(id)  # a no-op save must not burn a version number
            n = int(row["version"]) + 1
            c.execute("INSERT INTO doc_versions(id,doc_id,version,body,source,created_at) VALUES(?,?,?,?,?,?)",
                      (new_id(), id, n, body, source, t))
            c.execute("UPDATE docs SET body=?, version=?, updated_at=? WHERE id=?", (body, n, t, id))
            c.execute("DELETE FROM doc_versions WHERE doc_id=? AND version <= ?", (id, n - MAX_VERSIONS))
        return self.get(id)

    def versions(self, id: str) -> list[dict[str, Any]]:
        """Newest first, without bodies: a history list should not carry every document it describes."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT version,source,created_at,LENGTH(body) AS chars FROM doc_versions WHERE doc_id=? ORDER BY version DESC",
                (id,)).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def version(self, id: str, n: int) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = row_to_dict(c.execute(
                "SELECT version,source,created_at,body FROM doc_versions WHERE doc_id=? AND version=?",
                (id, int(n))).fetchone())
        if r is not None:
            r["chars"] = len(r["body"])
        return r

    def restore(self, id: str, n: int) -> dict[str, Any] | None:
        """Undo, as a step forward: version n's body becomes a new version, so the history stays append-only."""
        old = self.version(id, n)
        if not old:
            return None
        return self.save_version(id, old["body"], source="restore")

    def diff(self, id: str, frm: int, to: int) -> dict[str, Any] | None:
        a, b = self.version(id, frm), self.version(id, to)
        if not a or not b:
            return None
        lines = difflib.unified_diff(
            a["body"].splitlines(keepends=True), b["body"].splitlines(keepends=True),
            fromfile=f"v{frm}", tofile=f"v{to}")
        return {"frm": frm, "to": to, "diff": "".join(lines)}
