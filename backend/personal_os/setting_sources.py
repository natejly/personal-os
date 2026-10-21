"""Where each setting's value came from (design doc 10.8.3): one row per (key, scope, scope id) in `setting_sources`.

The source is `user` or `ext:<id>`; a project or chat value without a row is that scope's
own. `prev` holds the value before an extension default overwrote it. Rows stamped `preset:` or `profile:` by earlier versions still read. A stored value with no row was written
before this table existed and is reported as the user's.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable


def stamp(c: sqlite3.Connection, keys: Iterable[str], source: str, *, scope: str = "global", scope_id: str = "",
          prev: dict[str, Any] | None = None) -> None:
    """Record `source` as the writer of each key. `prev` maps a key to the value it replaced."""
    for key in keys:
        c.execute("INSERT INTO setting_sources(key, scope, scope_id, source, prev, set_at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(key, scope, scope_id) DO UPDATE SET source=excluded.source, prev=excluded.prev, set_at=excluded.set_at",
                  (key, scope, scope_id, source, json.dumps(prev[key]) if prev and key in prev else None, time.time()))


def read(c: sqlite3.Connection) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(r[0], r[1], r[2]): {"source": r[3], "prev": json.loads(r[4]) if r[4] is not None else None, "set_at": r[5]}
            for r in c.execute("SELECT key, scope, scope_id, source, prev, set_at FROM setting_sources")}
