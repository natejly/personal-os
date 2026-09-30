"""Native todo list, optionally mirrored to Google Tasks (see gtasks.py).

Sync bookkeeping lives here so every writer behaves the same:
- `external_id` links a todo to its Google Task; `remote_updated` is the remote `updated`
  stamp at last sync; `synced_at` mirrors `updated_at` at last sync, so
  `updated_at > synced_at` means "locally edited since".
- Deleting a synced todo leaves a tombstone so the sync can delete the remote task too.
- `on_change` (set by the app) is fired after any user-visible mutation; the sync services
  use it to schedule a near-immediate sync. Sync's own writes pass notify=False.

The Google Calendar mirror (todocal.py) reuses the same shape one level over:
`calendar_event_id`/`calendar_id` point at the mirrored event and `calendar_sig` records the
todo fields as last mirrored, so a pass can tell what actually changed. Deleting a todo that
had an event leaves an event tombstone so the mirror can delete the event too.
"""
from __future__ import annotations

from typing import Any, Callable

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
  calendar_event_id TEXT,
  calendar_link TEXT,
  calendar_id TEXT,
  calendar_sig TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_todos_open ON todos(done, due);

CREATE TABLE IF NOT EXISTS todo_tombstones (
  external_id TEXT PRIMARY KEY,
  deleted_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS todo_event_tombstones (
  event_id TEXT PRIMARY KEY,
  calendar_id TEXT,
  deleted_at REAL NOT NULL
);
"""


class Todos:
    def __init__(self, db: Database):
        self.db = db
        self.on_change: Callable[[], None] | None = None
        with db.tx() as c:
            c.executescript(SCHEMA)
            # Columns arrived after the first release; CREATE TABLE IF NOT EXISTS won't add them.
            have = {r["name"] for r in c.execute("PRAGMA table_info(todos)").fetchall()}
            for col, ddl in {"calendar_event_id": "TEXT", "calendar_link": "TEXT", "synced_at": "REAL",
                             "remote_updated": "TEXT", "calendar_id": "TEXT", "calendar_sig": "TEXT"}.items():
                if col not in have:
                    c.execute(f"ALTER TABLE todos ADD COLUMN {col} {ddl}")

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a sync hiccup must never break a todo write
                pass

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

    def create(self, title: str, project_id: str | None = None, notes: str = "", due: str | None = None, priority: int = 2, source: str = "local", external_id: str | None = None, notify: bool = True) -> dict[str, Any]:
        tid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO todos(id,project_id,title,notes,due,priority,done,source,external_id,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?,?)",
                (tid, project_id, title.strip(), notes, due or None, int(priority), source, external_id, t, t),
            )
        if notify:
            self._changed()
        return self.get(tid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any], notify: bool = True) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"title", "notes", "due", "priority", "done", "project_id", "calendar_event_id", "calendar_link", "calendar_id"}}
        if not fields:
            return self.get(id)
        if "done" in fields:
            fields["done"] = int(bool(fields["done"]))
            fields["completed_at"] = now() if fields["done"] else None
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE todos SET {sets} WHERE id=?", (*fields.values(), id))
        if notify:
            self._changed()
        return self.get(id)

    def delete(self, id: str, notify: bool = True, tombstone: bool = True) -> None:
        t = self.get(id)
        with self.db.tx() as c:
            if tombstone and t and t.get("external_id"):
                c.execute("INSERT OR REPLACE INTO todo_tombstones(external_id, deleted_at) VALUES(?,?)", (t["external_id"], now()))
            if tombstone and t and t.get("calendar_event_id"):
                c.execute("INSERT OR REPLACE INTO todo_event_tombstones(event_id, calendar_id, deleted_at) VALUES(?,?,?)",
                          (t["calendar_event_id"], t.get("calendar_id"), now()))
            c.execute("DELETE FROM todos WHERE id=?", (id,))
        if notify:
            self._changed()

    # ---- sync support ----
    def all_for_sync(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute("SELECT * FROM todos").fetchall()]  # type: ignore[misc]

    def set_sync_state(self, id: str, external_id: str | None, remote_updated: str | None, synced_at: float | None) -> None:
        """Record where a todo stands against its Google Task; never bumps updated_at."""
        with self.db.tx() as c:
            c.execute("UPDATE todos SET external_id=?, remote_updated=?, synced_at=? WHERE id=?",
                      (external_id, remote_updated, synced_at, id))

    def tombstones(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute("SELECT * FROM todo_tombstones").fetchall()]  # type: ignore[misc]

    def clear_tombstone(self, external_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM todo_tombstones WHERE external_id=?", (external_id,))

    # ---- calendar mirror support ----
    def set_calendar_state(self, id: str, event_id: str | None, link: str | None, calendar_id: str | None, sig: str | None) -> None:
        """Record where a todo stands against its mirrored calendar event; never bumps updated_at."""
        with self.db.tx() as c:
            c.execute("UPDATE todos SET calendar_event_id=?, calendar_link=?, calendar_id=?, calendar_sig=? WHERE id=?",
                      (event_id, link, calendar_id, sig, id))

    def event_tombstones(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute("SELECT * FROM todo_event_tombstones").fetchall()]  # type: ignore[misc]

    def clear_event_tombstone(self, event_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM todo_event_tombstones WHERE event_id=?", (event_id,))

    def stats(self) -> dict[str, int]:
        with self.db.tx() as c:
            open_ = c.execute("SELECT COUNT(*) FROM todos WHERE done=0").fetchone()[0]
            overdue = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND due IS NOT NULL AND due < date('now')").fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND due = date('now')").fetchone()[0]
        return {"open": open_, "overdue": overdue, "today": today}
