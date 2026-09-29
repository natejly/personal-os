"""Native todo list (optionally mirrored from Google Tasks later)."""
from __future__ import annotations

from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  due TEXT,
  priority INTEGER NOT NULL DEFAULT 2,
  done INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'local',
  external_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_todos_open ON todos(done, due);
"""


class Todos:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def list(self, project_id: str | None = "__all__", include_done: bool = False, q: str = "") -> list[dict[str, Any]]:
        where, args = [], []
        if project_id != "__all__":
            if project_id is None:
                where.append("project_id IS NULL")
            else:
                where.append("project_id = ?")
                args.append(project_id)
        if not include_done:
            where.append("done = 0")
        if q.strip():
            where.append("(title LIKE ? OR notes LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = "SELECT * FROM todos" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY done, CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, priority, created_at DESC"
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM todos WHERE id=?", (id,)).fetchone())

    def create(self, title: str, project_id: str | None = None, notes: str = "", due: str | None = None, priority: int = 2, source: str = "local", external_id: str | None = None) -> dict[str, Any]:
        tid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO todos(id,project_id,title,notes,due,priority,done,source,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?,?)",
                (tid, project_id, title.strip(), notes, due or None, int(priority), source, external_id, t, t),
            )
        return self.get(tid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"title", "notes", "due", "priority", "done", "project_id"}}
        if not fields:
            return self.get(id)
        if "done" in fields:
            fields["done"] = int(bool(fields["done"]))
            fields["completed_at"] = now() if fields["done"] else None
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE todos SET {sets} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM todos WHERE id=?", (id,))

    def stats(self) -> dict[str, int]:
        with self.db.tx() as c:
            open_ = c.execute("SELECT COUNT(*) FROM todos WHERE done=0").fetchone()[0]
            overdue = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND due IS NOT NULL AND due < date('now')").fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND due = date('now')").fetchone()[0]
        return {"open": open_, "overdue": overdue, "today": today}
