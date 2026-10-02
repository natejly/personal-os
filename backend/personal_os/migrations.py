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


# (version, name, step). Versions are consecutive from 1; append, never edit or reorder.
MIGRATIONS: list[tuple[int, str, Step]] = [
    (1, "baseline", _baseline),
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
