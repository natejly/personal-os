"""The drop_memory_hidden_view migration: Memory is no longer a sidebar row, so it leaves the stored hiddenViews list.

Run: backend/.venv/bin/python backend/tests/test_migration_drop_memory_hidden_view.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import llm, migrations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "drop_memory_hidden_view")


def _db(rows: dict[str, str]) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO settings VALUES (?, ?)", rows.items())
    c.execute(f"PRAGMA user_version = {V - 1}")
    return c


def _hidden(c: sqlite3.Connection):
    row = c.execute("SELECT value FROM settings WHERE key = 'hiddenViews'").fetchone()
    return json.loads(row[0]) if row else None


def test_memory_leaves_the_hidden_list_and_the_rest_stays() -> None:
    c = _db({"hiddenViews": '["memory","library"]', "theme": '"dark"'})
    assert migrations.run(c) == list(range(V, migrations.latest() + 1))
    assert _hidden(c) == ["library"]
    assert c.execute("SELECT value FROM settings WHERE key = 'theme'").fetchone()[0] == '"dark"'
    assert migrations.run(c) == []


def test_a_list_without_memory_and_an_absent_key_are_untouched() -> None:
    c = _db({"hiddenViews": '["mail"]'})
    migrations.run(c)
    assert _hidden(c) == ["mail"]
    c = _db({})
    migrations.run(c)
    assert _hidden(c) is None


def test_a_malformed_value_is_left_alone() -> None:
    c = _db({"hiddenViews": "not json"})
    migrations.run(c)
    assert c.execute("SELECT value FROM settings WHERE key = 'hiddenViews'").fetchone()[0] == "not json"


def test_the_version_and_the_default() -> None:
    assert migrations.latest() >= 28  # later migrations land on top
    assert llm.DEFAULT_SETTINGS["hiddenViews"] == []  # every sidebar row is on by default


def test_sidebar_only_rows_start_shown() -> None:
    assert llm.DEFAULT_SETTINGS["sidebarHidden"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
