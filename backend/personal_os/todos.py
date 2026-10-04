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

# Titles that were not typed in this app. Listing them is the same as reading the mail or the meeting.
UNTRUSTED_SOURCES = frozenset({"meeting", "email", "google"})

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

CREATE TABLE IF NOT EXISTS todo_deps (
  todo_id TEXT NOT NULL,      -- this todo waits on ...
  blocks_id TEXT NOT NULL,    -- ... this one (the blocker)
  PRIMARY KEY (todo_id, blocks_id)
);

CREATE TABLE IF NOT EXISTS todo_filters (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  query TEXT NOT NULL         -- JSON: {tag, q, project_id}
);

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


# Board columns are statuses now. A todo's `status` is a free column name, NULL meaning "derive it":
# Done for a finished todo, To do otherwise. Moving to a done-like status completes the todo.
DEFAULT_STATUSES = ["Backlog", "To do", "In progress", "Done"]
_DONE_WORDS = ("done", "complete", "completed", "finished", "shipped")


def is_done_status(status: Any) -> bool:
    return str(status or "").strip().lower() in _DONE_WORDS


def ensure_schema(c: Any) -> None:
    """The todos tables and every post-release column. Used by Todos and by the migration that moves boards in
    (which can run before any Todos exists); statements are run one by one because executescript commits."""
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            c.execute(stmt)
    # Columns arrived after the first release; CREATE TABLE IF NOT EXISTS won't add them.
    have = {r["name"] for r in c.execute("PRAGMA table_info(todos)").fetchall()}
    for col, ddl in {"calendar_event_id": "TEXT", "calendar_link": "TEXT", "synced_at": "REAL",
                     "remote_updated": "TEXT", "calendar_id": "TEXT", "calendar_sig": "TEXT",
                     "repeat": "TEXT", "estimate_min": "INTEGER",
                     "deleted_at": "REAL", "deleted_with": "TEXT",  # the last two: trash.py
                     "parent_id": "TEXT", "tags": "TEXT",  # local-only: Tasks sync never reads these
                     "list_name": "TEXT", "status": "TEXT", "position": "REAL NOT NULL DEFAULT 0"}.items():  # board view
        if col not in have:
            c.execute(f"ALTER TABLE todos ADD COLUMN {col} {ddl}")


def import_boards(c: Any) -> int:
    """Move every kanban card into todos once. A board becomes a list (`list_name`), its column the card's `status`,
    a card in a done-like column a completed todo. The old tables are renamed to legacy_* (card history included),
    which is also what makes the move run only once. Returns how many cards moved."""
    tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "cards" not in tables:
        return 0
    ensure_schema(c)
    rows = c.execute(
        "SELECT cd.*, b.name AS board_name, b.project_id AS board_project, col.name AS col_name "
        "FROM cards cd JOIN boards b ON b.id=cd.board_id JOIN board_columns col ON col.id=cd.column_id").fetchall()
    for r in rows:
        done = is_done_status(r["col_name"])
        try:
            labels = json.loads(r["labels"] or "[]")
        except ValueError:
            labels = []
        c.execute(
            "INSERT INTO todos(id,project_id,title,notes,due,priority,done,source,created_at,updated_at,completed_at,tags,list_name,status,position)"
            " VALUES(?,?,?,?,?,?,?,'board',?,?,?,?,?,?,?)",
            (new_id(), r["board_project"], r["title"], r["description"] or "", r["due"] or None, r["priority"], int(done),
             r["created_at"], r["updated_at"], r["updated_at"] if done else None,
             json.dumps(Todos.clean_tags(labels)), r["board_name"], r["col_name"], r["position"]))
    if "canvas_windows" in tables:  # a board window becomes a todos window in board view, on that board's list
        for w in c.execute("SELECT w.id, b.name FROM canvas_windows w JOIN boards b ON b.id=w.ref_id WHERE w.kind='board'").fetchall():
            c.execute("UPDATE canvas_windows SET kind='todos', ref_id=NULL, config=? WHERE id=?",
                      (json.dumps({"view": "board", "list": w["name"], "includeDone": True}), w["id"]))
        c.execute("DELETE FROM canvas_windows WHERE kind='board'")  # one whose board is gone
    for old, new in (("cards", "legacy_cards"), ("boards", "legacy_boards"), ("board_columns", "legacy_board_columns"),
                     ("card_events", "legacy_card_events")):
        if old in tables:
            c.execute(f"ALTER TABLE {old} RENAME TO {new}")
    return len(rows)


class Todos:
    def __init__(self, db: Database):
        self.db = db
        self.on_change: Callable[[], None] | None = None
        with db.tx() as c:
            ensure_schema(c)

    @staticmethod
    def _out(row: dict[str, Any] | None) -> dict[str, Any] | None:
        """`repeat` is stored as JSON text and handed out as a dict (or None)."""
        if row and isinstance(row.get("repeat"), str):
            try:
                row["repeat"] = json.loads(row["repeat"])
            except ValueError:
                row["repeat"] = None
        if row:
            row["status"] = row.get("status") or ("Done" if row.get("done") else "To do")
            try:
                row["tags"] = json.loads(row["tags"]) if row.get("tags") else []
            except ValueError:
                row["tags"] = []
        return row

    @staticmethod
    def clean_tags(raw: Any) -> list[str]:
        """Tags as a de-duplicated list of lowercase words; accepts a list or a comma separated string."""
        items = raw.split(",") if isinstance(raw, str) else (raw or [])
        out: list[str] = []
        for t in items:
            t = str(t).strip().lstrip("#").lower()[:40]
            if t and t not in out:
                out.append(t)
        return out

    def _attach(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add `depends_on` (open blockers), `blocked_count` and `blocking_count` to rows."""
        with self.db.tx() as c:
            pairs = c.execute("SELECT d.todo_id, d.blocks_id FROM todo_deps d JOIN todos b ON b.id=d.blocks_id"
                              " JOIN todos a ON a.id=d.todo_id WHERE b.done=0 AND b.deleted_at IS NULL AND a.deleted_at IS NULL").fetchall()
        waits: dict[str, list[str]] = {}
        blocking: dict[str, int] = {}
        for a, b in pairs:
            waits.setdefault(a, []).append(b)
            blocking[b] = blocking.get(b, 0) + 1
        for r in rows:
            r["depends_on"] = waits.get(r["id"], [])
            r["blocked_count"] = len(r["depends_on"])
            r["blocking_count"] = blocking.get(r["id"], 0)
        return rows

    def set_deps(self, id: str, blockers: list[str]) -> None:
        """Replace what `id` waits on. Raises ValueError for an unknown todo, itself, or a cycle."""
        blockers = list(dict.fromkeys(blockers))
        with self.db.tx() as c:
            for b in blockers:
                if b == id:
                    raise ValueError("a todo cannot depend on itself")
                if not c.execute("SELECT 1 FROM todos WHERE id=? AND deleted_at IS NULL", (b,)).fetchone():
                    raise ValueError(f"no todo with id {b!r} to depend on")
            # A cycle exists when a blocker already (transitively) waits on `id`.
            edges: dict[str, list[str]] = {}
            for a, b in c.execute("SELECT todo_id, blocks_id FROM todo_deps WHERE todo_id != ?", (id,)).fetchall():
                edges.setdefault(a, []).append(b)
            seen: set[str] = set()
            stack = list(blockers)
            while stack:
                n = stack.pop()
                if n == id:
                    raise ValueError("that dependency would make a cycle")
                if n not in seen:
                    seen.add(n)
                    stack += edges.get(n, [])
            c.execute("DELETE FROM todo_deps WHERE todo_id=?", (id,))
            c.executemany("INSERT INTO todo_deps(todo_id, blocks_id) VALUES(?,?)", [(id, b) for b in blockers])

    def _check_parent(self, id: str, parent_id: str | None) -> None:
        seen: set[str] = set()
        while parent_id:
            if parent_id == id or parent_id in seen:
                raise ValueError("a todo cannot be its own ancestor")
            seen.add(parent_id)
            p = self.get(parent_id)
            if not p:
                raise ValueError(f"no todo with id {parent_id!r} to nest under")
            parent_id = p.get("parent_id")

    @staticmethod
    def nest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reorder so each todo's subtasks follow it. Rows whose parent is not in the list stay top-level."""
        ids = {r["id"] for r in rows}
        kids: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            if r.get("parent_id") in ids:
                kids.setdefault(r["parent_id"], []).append(r)
        out: list[dict[str, Any]] = []

        def walk(r: dict[str, Any]) -> None:
            out.append(r)
            for k in kids.get(r["id"], []):
                walk(k)
        for r in rows:
            if r.get("parent_id") not in ids:
                walk(r)
        return out

    # ---- saved filters ----
    def filters(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [{"id": r["id"], "name": r["name"], **json.loads(r["query"])} for r in c.execute("SELECT * FROM todo_filters ORDER BY name").fetchall()]

    def save_filter(self, name: str, query: dict[str, Any]) -> dict[str, Any]:
        fid = new_id()
        q = {k: query[k] for k in ("tag", "q", "project_id") if query.get(k)}
        with self.db.tx() as c:
            c.execute("INSERT INTO todo_filters(id,name,query) VALUES(?,?,?)", (fid, name.strip(), json.dumps(q)))
        return {"id": fid, "name": name.strip(), **q}

    def delete_filter(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM todo_filters WHERE id=?", (id,))

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a sync hiccup must never break a todo write
                pass

    def list(self, project_id: str | None = "__all__", include_done: bool = False, q: str = "", tag: str = "", list_name: str = "") -> list[dict[str, Any]]:
        where, args = ["deleted_at IS NULL"], []
        if list_name.strip():
            where.append("list_name = ?")
            args.append(list_name.strip())
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
            rows = [self._out(row_to_dict(r)) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]
        if tag.strip():
            want = self.clean_tags(tag)
            rows = [r for r in rows if all(w in r["tags"] for w in want)]  # type: ignore[index]
        return self._attach(rows)  # type: ignore[arg-type]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            row = self._out(row_to_dict(c.execute("SELECT * FROM todos WHERE id=? AND deleted_at IS NULL", (id,)).fetchone()))
        return self._attach([row])[0] if row else None

    def create(self, title: str, project_id: str | None = None, notes: str = "", due: str | None = None, priority: int = 2, source: str = "local", external_id: str | None = None, notify: bool = True, repeat: dict[str, Any] | None = None, estimate_min: int | None = None, tags: Any = None, parent_id: str | None = None, list_name: str | None = None, status: str | None = None) -> dict[str, Any]:
        rep = todo_rules.parse_repeat(repeat)
        tid = new_id()
        t = now()
        due = clean_due(due)
        is_done = is_done_status(status)
        self._check_parent(tid, parent_id)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO todos(id,project_id,title,notes,due,priority,done,source,external_id,created_at,updated_at,repeat,estimate_min,tags,parent_id,list_name,status,position,completed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, project_id, title.strip(), notes, due or None, int(priority), int(is_done), source, external_id, t, t, json.dumps(rep) if rep else None, int(estimate_min) if estimate_min else None,
                 json.dumps(self.clean_tags(tags)), parent_id or None, (list_name or "").strip() or None, (status or "").strip() or None, t, t if is_done else None),
            )
        if notify:
            self._changed()
        return self.get(tid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any], notify: bool = True, today: date | None = None) -> dict[str, Any] | None:
        """Apply a patch. Completing an open repeating todo spawns its next instance (_spawn_next); Tasks-sync
        completions come through here too, so they recur as well. The completed row has its repeat cleared,
        so reopening and completing it again never spawns a second copy."""
        fields = {k: v for k, v in patch.items() if k in {"title", "notes", "due", "priority", "done", "project_id", "calendar_event_id", "calendar_link", "calendar_id", "repeat", "estimate_min", "tags", "parent_id", "list_name", "status", "position"}}
        deps = patch.get("depends_on")
        if not fields and deps is None:
            return self.get(id)
        if "tags" in fields:
            fields["tags"] = json.dumps(self.clean_tags(fields["tags"]))
        if "list_name" in fields:
            fields["list_name"] = (fields["list_name"] or "").strip() or None
        if "status" in fields:
            fields["status"] = (fields["status"] or "").strip() or None
            if fields["status"] is not None and "done" not in fields:
                fields["done"] = is_done_status(fields["status"])  # a column named Done completes the todo
        elif "done" in fields:
            fields["status"] = None  # ticking it off or reopening it puts it back in Done / To do
        if "parent_id" in fields:
            fields["parent_id"] = fields["parent_id"] or None
            self._check_parent(id, fields["parent_id"])
        if deps is not None:
            self.set_deps(id, list(deps))
        if "due" in fields:
            fields["due"] = clean_due(fields["due"])
        if "repeat" in fields:
            rep = todo_rules.parse_repeat(fields["repeat"])
            fields["repeat"] = json.dumps(rep) if rep else None
        if "estimate_min" in fields:
            fields["estimate_min"] = int(fields["estimate_min"]) if fields["estimate_min"] else None
        before = self.get(id)
        completing = bool(before and fields.get("done") and not before["done"] and before.get("repeat"))
        relink = before is not None and "calendar_event_id" in fields
        if relink:
            # A caller placed the event itself: the mirror adopts it rather than rewriting it against
            # the old event's signature, and a replaced event is tombstoned so the mirror deletes it.
            fields["calendar_sig"] = None
        if "done" in fields:
            fields["done"] = int(bool(fields["done"]))
            fields["completed_at"] = now() if fields["done"] else None
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE todos SET {sets} WHERE id=?", (*fields.values(), id))
            if relink and before and before.get("calendar_event_id") not in (None, fields["calendar_event_id"]):
                self._tombstone(c, {"calendar_event_id": before["calendar_event_id"], "calendar_id": before.get("calendar_id")})
            if completing:
                c.execute("UPDATE todos SET repeat=NULL WHERE id=?", (id,))
        if completing and before:
            self._spawn_next(before, today or date.today())
        if notify:
            self._changed()
        return self.get(id)

    def move(self, id: str, status: str, before_id: str | None = None) -> dict[str, Any] | None:
        """Board drag: set the status and slot the todo before `before_id` (or last). Positions are floats, so a slot
        is the midpoint between the neighbours."""
        with self.db.tx() as c:
            if before_id and (b := c.execute("SELECT position FROM todos WHERE id=?", (before_id,)).fetchone()):
                prev = c.execute("SELECT MAX(position) FROM todos WHERE position < ? AND id != ?", (b["position"], id)).fetchone()[0]
                pos = ((prev if prev is not None else b["position"] - 1) + b["position"]) / 2
            else:
                pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM todos").fetchone()[0]
        return self.update(id, {"status": status, "position": pos})

    def lists(self) -> list[str]:
        """The list names in use, for the board's list picker."""
        with self.db.tx() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT list_name FROM todos WHERE list_name IS NOT NULL AND deleted_at IS NULL ORDER BY list_name")]

    def _spawn_next(self, row: dict[str, Any], completed_on: date) -> dict[str, Any]:
        """Next instance of a just-completed repeating todo. source='local' so Tasks sync makes it a fresh
        remote task instead of treating it as the completed one."""
        due = date.fromisoformat(row["due"][:10]) if row.get("due") else None
        nxt = todo_rules.next_due(due, row["repeat"], completed_on)
        return self.create(row["title"], row.get("project_id"), row.get("notes") or "", nxt.isoformat(), row["priority"],
                           source="local", notify=False, repeat=row["repeat"], estimate_min=row.get("estimate_min"),
                           tags=row.get("tags"), parent_id=row.get("parent_id"), list_name=row.get("list_name"))

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
            c.execute("DELETE FROM todo_deps WHERE todo_id=? OR blocks_id=?", (id, id))
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
        # `due` is the user's local date; SQLite date('now') is UTC and is a day ahead every evening.
        today_ = date.today().isoformat()
        with self.db.tx() as c:
            open_ = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL").fetchone()[0]
            overdue = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL AND due IS NOT NULL AND due < ?", (today_,)).fetchone()[0]
            today = c.execute("SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL AND due = ?", (today_,)).fetchone()[0]
        return {"open": open_, "overdue": overdue, "today": today}
