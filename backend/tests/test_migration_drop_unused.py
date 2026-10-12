"""The drop_unused_tables_and_settings migration: dead tables and settings go, live ones stay."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "drop_unused_tables_and_settings")


def _db(rows: dict[str, str]) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO settings VALUES (?, ?)", rows.items())
    for t in ("turns", "dashboards", "data_sources", "artifact_versions", "teach_recordings", "canvas_presets", "legacy_cards"):
        c.execute(f"CREATE TABLE {t} (id TEXT)")
    c.execute("CREATE VIRTUAL TABLE turns_fts USING fts5(body)")
    c.execute(f"PRAGMA user_version = {V - 1}")
    return c


def test_dead_tables_and_settings_go_and_the_rest_stays() -> None:
    c = _db({"maxRunCost": "1.0", "pimProvider": '"google"', "sandboxNetwork": '"off"', "meetings": "{}", "theme": '"dark"',
             "hiddenViews": '["dashboards","mail"]'})
    assert V in migrations.run(c)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} - {"coding_session_events", "model_votes", "sqlite_sequence"}  # made by later migrations
    assert tables == {"settings", "legacy_cards"}  # shadow tables of turns_fts went with it
    keys = {r[0] for r in c.execute("SELECT key FROM settings")}
    assert keys == {"meetings", "theme", "hiddenViews", "sidebarHidden"}  # sidebarHidden: written by the later default_sidebar_rows migration
    assert json.loads(c.execute("SELECT value FROM settings WHERE key = 'hiddenViews'").fetchone()[0]) == ["mail"]
    assert migrations.run(c) == []


def test_a_bare_database_and_a_malformed_list_are_fine() -> None:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO settings VALUES ('hiddenViews', 'not json')")
    c.execute(f"PRAGMA user_version = {V - 1}")
    migrations.run(c)
    assert c.execute("SELECT value FROM settings WHERE key = 'hiddenViews'").fetchone()[0] == "not json"
