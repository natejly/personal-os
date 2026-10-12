"""The default_sidebar_rows migration: an untouched sidebarHidden gets the new default, a customized one stays."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm, migrations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "default_sidebar_rows")


def _run(rows: dict[str, str]):
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO settings VALUES (?, ?)", rows.items())
    c.execute(f"PRAGMA user_version = {V - 1}")
    migrations.run(c)
    row = c.execute("SELECT value FROM settings WHERE key = 'sidebarHidden'").fetchone()
    return json.loads(row[0]) if row else None


def test_missing_or_empty_gets_the_new_default() -> None:
    assert _run({}) == ["mail", "health"]
    assert _run({"sidebarHidden": "[]"}) == ["mail", "health"]
    assert llm.DEFAULT_SETTINGS["sidebarHidden"] == ["mail", "health"]


def test_a_customized_list_is_unchanged() -> None:
    assert _run({"sidebarHidden": '["docs"]'}) == ["docs"]
