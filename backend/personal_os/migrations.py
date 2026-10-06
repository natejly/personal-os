"""Versioned schema migrations, tracked in SQLite's PRAGMA user_version.

Version 1 is the baseline: "whatever SCHEMA plus Database._migrate produces". Every database that
existed before this module reads user_version 0, runs that baseline code exactly as it always did, and
is stamped 1, so adopting the system changes nothing. Later changes append a numbered step to
MIGRATIONS (a plain function taking the connection); they run in order, each followed by its
user_version bump in the same transaction. A raised step rolls back and leaves the version where it was.

Database.__init__ takes a backup (backups.py) before running anything pending on a database that
already had content.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

Step = Callable[[sqlite3.Connection], None]


def _baseline(c: sqlite3.Connection) -> None:
    """Nothing to do: SCHEMA and Database._migrate have already produced the v1 shape."""


def _messages_fts(c: sqlite3.Connection) -> None:
    """Full-text index over message bodies. External-content, kept in step by triggers because message rows
    also vanish through FK cascades (conversation, trash purge). `UPDATE OF content` reindexes a finished
    reply; trace/reasoning writes do not touch it."""
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='rowid', tokenize='porter unicode61')")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN "
              "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN "
              "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content); END")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN "
              "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content); "
              "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END")
    c.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")


def _boards_into_todos(c: sqlite3.Connection) -> None:
    """Kanban boards merge into Todos: each card becomes a todo on a list named for its board (todos.import_boards)."""
    from . import todos
    todos.import_boards(c)


def _activity_record_everything_keys(c: sqlite3.Connection) -> None:
    """Rename the stored activity keys the old two stored keys to `recordEverything` / `recordEverythingRestore`."""
    import json
    row = c.execute("SELECT value FROM settings WHERE key = 'activity'").fetchone()
    if not row:
        return
    try:
        cfg = json.loads(row[0])
    except ValueError:
        return
    if not isinstance(cfg, dict):
        return
    for old, new in (("pal" "antir", "recordEverything"), ("pal" "antirRestore", "recordEverythingRestore")):
        if old in cfg:
            cfg.setdefault(new, cfg[old])
            del cfg[old]
    c.execute("UPDATE settings SET value = ? WHERE key = 'activity'", (json.dumps(cfg),))


def _permissions_store(c: sqlite3.Connection) -> None:
    """The ~22 top-level permission keys (tools, alwaysAsk, permissionRules, ...) fold into one versioned
    `permissions` row and are deleted (permissions.py). A fresh database gets {"version": 1}; defaults fill the rest."""
    from . import permissions
    permissions.migrate(c)


def _meetings_activity_defaults(c: sqlite3.Connection) -> None:
    """Meetings and Activity now ship on. Only a bare `{"enabled": false}` row (nothing but that key, the
    stub an older whole-settings save could write) is flipped to true. Both services only ever store their
    FULL config, and only on a user action: a Start/Stop, the consent notice, or any edit in their panels.
    So a full row with `enabled: false` - even one equal to the defaults, which is what Stop leaves behind -
    is a user who was in there and left it off, and it stays off. A missing row needs nothing: the new
    default applies on read. Neither switch records on its own; consent and OS permissions still gate that."""
    import json
    for key in ("activity", "meetings"):
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        try:
            cfg = json.loads(row[0]) if row else None
        except ValueError:
            continue
        if cfg == {"enabled": False}:
            c.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps({"enabled": True}), key))


def _approval_history(c: sqlite3.Connection) -> None:
    """The approval decision log (approval_log.py): one row per answer, standing-grant pass and reviewer verdict.
    `approvals.review` keeps the review gate's verdict on the card it opened, so the answer's log row can carry it."""
    c.execute("CREATE TABLE IF NOT EXISTS approval_log ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, conversation_id TEXT, run_id TEXT, desk_id TEXT, "
              "agent TEXT, tool TEXT NOT NULL, args_summary TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL, scope TEXT, "
              "rule_json TEXT, note TEXT, reviewer_verdict TEXT, reviewer_reason TEXT, reviewer_model TEXT, reviewer_ms INTEGER, "
              "call_id TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_approval_log_ts ON approval_log(ts)")
    if "review" not in {r[1] for r in c.execute("PRAGMA table_info(approvals)")}:
        c.execute("ALTER TABLE approvals ADD COLUMN review TEXT")


def _teach_recordings(c: sqlite3.Connection) -> None:
    """Teach-a-task recordings (teach.py): a folder of screen frames plus the step draft extracted from them,
    and the skill / routine that came out of it. Frames live under <data_dir>/teach/<id>/, deleted with the row."""
    c.execute("CREATE TABLE IF NOT EXISTS teach_recordings ("
              "id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
              "status TEXT NOT NULL DEFAULT 'recording', "  # recording | ready | extracted | saved
              "source TEXT NOT NULL DEFAULT 'screen', "     # screen | import
              "dir TEXT NOT NULL, frame_count INTEGER NOT NULL DEFAULT 0, "
              "steps_json TEXT, skill_id TEXT, job_id TEXT)")


def _ship_checklists(c: sqlite3.Connection) -> None:
    """Slot reserved for the job ship checklist (owner: ship-checklist)."""


# (version, name, step). Versions are consecutive from 1; append, never edit or reorder.
MIGRATIONS: list[tuple[int, str, Step]] = [
    (1, "baseline", _baseline),
    (2, "messages_fts", _messages_fts),
    (3, "boards_into_todos", _boards_into_todos),
    (4, "activity_record_everything_keys", _activity_record_everything_keys),
    (5, "permissions_store", _permissions_store),
    (6, "meetings_activity_defaults", _meetings_activity_defaults),
    (7, "approval_history", _approval_history),
    (8, "teach_recordings", _teach_recordings),
    (9, "ship_checklists", _ship_checklists),
]


def latest() -> int:
    return MIGRATIONS[-1][0]


def current(c: sqlite3.Connection) -> int:
    return int(c.execute("PRAGMA user_version").fetchone()[0])


def pending(c: sqlite3.Connection) -> list[tuple[int, str, Step]]:
    have = current(c)
    return [m for m in MIGRATIONS if m[0] > have]


def run(c: sqlite3.Connection) -> list[int]:
    """Apply every pending step in order; returns the versions applied."""
    done: list[int] = []
    for version, _name, step in pending(c):
        try:
            c.execute("BEGIN")
            step(c)
            c.execute(f"PRAGMA user_version = {int(version)}")  # PRAGMA takes no bound parameters
            c.commit()
        except Exception:
            c.rollback()
            raise
        done.append(version)
    return done
