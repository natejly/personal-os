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

import json
from datetime import date
import datetime as dt
from typing import Any, Callable

from . import todo_rules
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



def clean_due(due: Any) -> str | None:
    """A due date as YYYY-MM-DD, or None for no date.

    Both syncs build API bodies from this string, so anything else ("tomorrow", a bad
    month) used to be stored as given and then fail every pass. An ISO datetime keeps
    its date part. Raises ValueError for anything that is not a date.
    """
    if due is None or not str(due).strip():
        return None
    text = str(due).strip()
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise ValueError(f"due must be a date as YYYY-MM-DD, got {text[:40]!r}") from None


class Todos:
    def __init__(self, db: Database):
        self.db = db
        self.on_change: Callable[[], None] | None = None
        with db.tx() as c:
            c.executescript(SCHEMA)
            # Columns arrived after the first release; CREATE TABLE IF NOT EXISTS won't add them.
            have = {r["name"] for r in c.execute("PRAGMA table_info(todos)").fetchall()}
            for col, ddl in {"calendar_event_id": "TEXT", "calendar_link": "TEXT", "synced_at": "REAL",
                             "remote_updated": "TEXT", "calendar_id": "TEXT", "calendar_sig": "TEXT",
                             "repeat": "TEXT", "estimate_min": "INTEGER",
                             "deleted_at": "REAL", "deleted_with": "TEXT"}.items():  # the last two: trash.py
                if col not in have:
                    c.execute(f"ALTER TABLE todos ADD COLUMN {col} {ddl}")

    @staticmethod
    def _out(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """`repeat` is stored as JSON text and handed out as a dict (or None)."""
        if row and isinstance(row.get("repeat"), str):
            try:
                row["repeat"] = json.loads(row["repeat"])
            except ValueError:
                row["repeat"] = None
        return row

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a sync hiccup must never break a todo write
                pass

    def list(self, project_id: str | None = "__all__", include_done: bool = False, q: str = "") -> list[dict[str, Any]]:
        where, args = ["deleted_at IS NULL"], []
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
            return [self._out(row_to_dict(r)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._out(row_to_dict(c.execute("SELECT * FROM todos WHERE id=? AND deleted_at IS NULL", (id,)).fetchone()))

    def create(self, title: str, project_id: str | None = None, notes: str = "", due: str | None = None, priority: int = 2, source: str = "local", external_id: str | None = None, notify: bool = True, repeat: dict[str, Any] | None = None, estimate_min: int | None = None) -> dict[str, Any]:
        rep = todo_rules.parse_repeat(repeat)
        tid = new_id()
        t = now()
        due = clean_due(due)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO todos(id,project_id,title,notes,due,priority,done,source,external_id,created_at,updated_at,repeat,estimate_min) VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?)",
                (tid, project_id, title.strip(), notes, due or None, int(priority), source, external_id, t, t, json.dumps(rep) if rep else None, int(estimate_min) if estimate_min else None),
            )
        if notify:
            self._changed()
        return self.get(tid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any], notify: bool = True, today: date | None = None) -> dict[str, Any] | None:
        """Apply a patch. Completing an open repeating todo spawns its next instance (_spawn_next); Tasks-sync
        completions come through here too, so they recur as well. The completed row has its repeat cleared,
        so reopening and completing it again never spawns a second copy."""
        fields = {k: v for k, v in patch.items() if k in {"title", "notes", "due", "priority", "done", "project_id", "calendar_event_id", "calendar_link", "calendar_id", "repeat", "estimate_min"}}
        if not fields:
            return self.get(id)
        if "due" in fields:
            fields["due"] = clean_due(fields["due"])
        if "repeat" in fields:
            rep = todo_rules.parse_repeat(fields["repeat"])
            fields["repeat"] = json.dumps(rep) if rep else None
        if "estimate_min" in fields:
            fields["estimate_min"] = int(fields["estimate_min"]) if fields["estimate_min"] else None
        before = self.get(id)
        completing = bool(before and fields.get("done") and not before["done"] and before.get("repeat"))
        if "done" in fields:
            fields["done"] = int(bool(fields["done"]))
            fields["completed_at"] = now() if fields["done"] else None
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE todos SET {sets} WHERE id=?", (*fields.values(), id))
            if completing:
                c.execute("UPDATE todos SET repeat=NULL WHERE id=?", (id,))
        if completing and before:
            self._spawn_next(before, today or date.today())
        if notify:
            self._changed()
        return self.get(id)

    def _spawn_next(self, row: dict[str, Any], completed_on: date) -> dict[str, Any]:
        """Next instance of a just-completed repeating todo. source='local' so Tasks sync makes it a fresh
        remote task instead of treating it as the completed one."""
        due = date.fromisoformat(row["due"][:10]) if row.get("due") else None
        nxt = todo_rules.next_due(due, row["repeat"], completed_on)
        return self.create(row["title"], row.get("project_id"), row.get("notes") or "", nxt.isoformat(), row["priority"],
                           source="local", notify=False, repeat=row["repeat"], estimate_min=row.get("estimate_min"))

    @staticmethod
    def _tombstone(c: Any, t: dict[str, Any]) -> None:
        if t.get("external_id"):
            c.execute("INSERT OR REPLACE INTO todo_tombstones(external_id, deleted_at) VALUES(?,?)", (t["external_id"], now()))
        if t.get("calendar_event_id"):
            c.execute("INSERT OR REPLACE INTO todo_event_tombstones(event_id, calendar_id, deleted_at) VALUES(?,?,?)",
                      (t["calendar_event_id"], t.get("calendar_id"), now()))

    def delete(self, id: str, notify: bool = True, tombstone: bool = True) -> None:
        """Erase a todo for good. The user-facing delete is `trash`; this is the purge and the sync's own."""
        with self.db.tx() as c:
            t = row_to_dict(c.execute("SELECT * FROM todos WHERE id=?", (id,)).fetchone())
            # A trashed todo already left its tombstones (see trash), so only a live one needs them here.
            if tombstone and t and not t.get("deleted_at"):
                self._tombstone(c, t)
            c.execute("DELETE FROM todos WHERE id=?", (id,))
        if notify:
            self._changed()

    def trash(self, id: str, deleted_with: str | None = None, notify: bool = True) -> bool:
        """Soft delete. The remote Google task and mirrored calendar event are still deleted the way a hard
        delete would (tombstones), and the todo forgets its links, so a restore comes back as a new task
        instead of pointing at one that no longer exists."""
        with self.db.tx() as c:
            t = row_to_dict(c.execute("SELECT * FROM todos WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())
            if not t:
                return False
            self._tombstone(c, t)
            c.execute("UPDATE todos SET deleted_at=?, deleted_with=?, external_id=NULL, remote_updated=NULL, synced_at=NULL,"
                      " calendar_event_id=NULL, calendar_link=NULL, calendar_id=NULL, calendar_sig=NULL WHERE id=?",
                      (now(), deleted_with, id))
        if notify:
            self._changed()
        return True

    def restore(self, id: str, notify: bool = True) -> bool:
        with self.db.tx() as c:
            cur = c.execute("UPDATE todos SET deleted_at=NULL, deleted_with=NULL, updated_at=? WHERE id=? AND deleted_at IS NOT NULL",
                            (now(), id))
        if notify and cur.rowcount:
            self._changed()
        return bool(cur.rowcount)

    # ---- sync support ----
    def all_for_sync(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute("SELECT * FROM todos WHERE deleted_at IS NULL").fetchall()]  # type: ignore[misc]

    def set_sync_state(self, id: str, external_id: str | None, remote_updated: str | None, synced_at: float | None) -> bool:
        """Record where a todo stands against its Google Task; never bumps updated_at.

        False when the todo no longer exists (deleted while the sync was talking to Google).
        """
        with self.db.tx() as c:
            cur = c.execute("UPDATE todos SET external_id=?, remote_updated=?, synced_at=? WHERE id=?",
                            (external_id, remote_updated, synced_at, id))
            return cur.rowcount > 0

    def forget_sync_links(self) -> int:
        """Unlink every todo from its Google Task without deleting either side.

        Used when the sync target changes (another task list or another account): the old
        ids mean nothing there, and a linked todo whose task is missing is deleted locally.
        """
        with self.db.tx() as c:
            cur = c.execute("UPDATE todos SET external_id=NULL, remote_updated=NULL, synced_at=NULL WHERE external_id IS NOT NULL")
            c.execute("DELETE FROM todo_tombstones")
            return cur.rowcount

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
            open_ = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL").fetchone()[0]
            overdue = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL AND due IS NOT NULL AND due < date('now')").fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL AND due = date('now')").fetchone()[0]
        return {"open": open_, "overdue": overdue, "today": today}
