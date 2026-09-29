"""Sticky notes: the only content type canvas mode adds."""
from __future__ import annotations

from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal scope, like todos and boards
  body TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT 'yellow',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
"""


class Notes:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def list(self, project_id: str | None = "__all__", q: str = "") -> list[dict[str, Any]]:
        where, args = [], []
        if project_id != "__all__":
            if project_id is None:
                where.append("project_id IS NULL")
            else:
                where.append("project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("body LIKE ?")
            args.append(f"%{q}%")
        sql = "SELECT * FROM notes" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC"
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM notes WHERE id=?", (id,)).fetchone())

    def create(self, body: str = "", color: str = "yellow", project_id: str | None = None) -> dict[str, Any]:
        nid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute("INSERT INTO notes(id,project_id,body,color,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (nid, project_id, body, color or "yellow", t, t))
        return self.get(nid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"body", "color", "project_id"}}
        if not fields:
            return self.get(id)
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM notes WHERE id=?", (id,))
