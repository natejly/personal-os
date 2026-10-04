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


# (version, name, step). Versions are consecutive from 1; append, never edit or reorder.
MIGRATIONS: list[tuple[int, str, Step]] = [
    (1, "baseline", _baseline),
    (2, "messages_fts", _messages_fts),
    (3, "boards_into_todos", _boards_into_todos),
    (4, "activity_record_everything_keys", _activity_record_everything_keys),
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
