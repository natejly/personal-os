"""The drop_permission_presets migration: old preset rows and profile records go, everything else stays."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "drop_permission_presets")


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("CREATE TABLE permission_presets(id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, "
              "source TEXT NOT NULL DEFAULT 'user', created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    c.execute("INSERT INTO permission_presets VALUES ('user:x', 'X', '{}', 'user', 0, 0)")
    c.execute("CREATE TABLE setting_sources(key TEXT NOT NULL, scope TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '', "
              "source TEXT NOT NULL, prev TEXT, set_at REAL NOT NULL, PRIMARY KEY (key, scope, scope_id))")
    c.executemany("INSERT INTO setting_sources VALUES (?, 'global', '', ?, NULL, 0)",
                  [("permissionMode", "preset:builtin:auto"), ("defaultEffort", "profile:focus"), ("theme", "ext:acme"), ("accent", "user")])
    records = {"lean": {"view:mail": {"was": True, "set": False}}, "focus": {}, "acme": {"theme": {"was": "dark", "set": "light"}}}
    c.execute("INSERT INTO settings VALUES ('extDefaults', ?)", (json.dumps(records),))
    c.execute(f"PRAGMA user_version = {V - 1}")
    return c


def test_preset_rows_and_profile_records_go_and_the_rest_stays() -> None:
    c = _db()
    assert V in migrations.run(c)
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'permission_presets'").fetchone()
    assert {r[0] for r in c.execute("SELECT source FROM setting_sources")} == {"ext:acme", "user"}
    assert list(json.loads(c.execute("SELECT value FROM settings WHERE key = 'extDefaults'").fetchone()[0])) == ["acme"]
    assert migrations.run(c) == []


def test_malformed_defaults_do_not_stop_the_migration() -> None:
    c = _db()
    c.execute("UPDATE settings SET value = 'not json' WHERE key = 'extDefaults'")
    assert V in migrations.run(c)
    assert c.execute("SELECT value FROM settings WHERE key = 'extDefaults'").fetchone()[0] == "not json"
