"""The drop_meetings_activity migration: the meetings and activity-monitor tables are dropped; their settings rows are left readable.

(a) a bare in-memory database stamped just before it, with the old tables, loses exactly those tables;
(b) a real data directory written by the old build (tables, rows, legacy `meetings`/`activity`/`digest` settings)
    migrates on open, and the app imported on top of it starts and serves /settings and /voice/config;
(c) a fresh data directory ends at the latest version with none of the tables.

Run: backend/.venv/bin/python backend/tests/test_migration_drop_meetings_activity.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402

DROPPED = ("meetings", "meeting_segments", "meeting_revisions", "meeting_action_items", "meeting_vectors", "meetings_fts",
           "activity_events", "activity_summaries", "activity_profile", "activity_day_stats", "activity_habits",
           "activity_suggestions", "activity_patterns")

# The tables as the removed modules created them (trimmed to the columns that matter to a drop).
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE SET NULL, title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'scheduled', notes TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '',
  calendar_event_id TEXT, doc_id TEXT, doc_mode TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(COALESCE(created_at, 0) DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_meetings_event ON meetings(calendar_event_id) WHERE calendar_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meetings_doc ON meetings(doc_id);
CREATE TABLE IF NOT EXISTS meeting_segments (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, channel TEXT NOT NULL,
  seq INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, UNIQUE (meeting_id, channel, seq)
);
CREATE INDEX IF NOT EXISTS idx_mseg_timeline ON meeting_segments(meeting_id, seq);
CREATE TABLE IF NOT EXISTS meeting_revisions (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mrev_pending ON meeting_revisions(meeting_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS meeting_action_items (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  revision_id TEXT REFERENCES meeting_revisions(id) ON DELETE SET NULL, text TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_vectors (
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
  text TEXT NOT NULL, vec BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
  title, notes, enhanced, transcript, meeting_id UNINDEXED, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS activity_events (
  id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, app TEXT NOT NULL DEFAULT '', expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_ts ON activity_events(ts DESC);
CREATE TABLE IF NOT EXISTS activity_summaries (
  id TEXT PRIMARY KEY, day TEXT NOT NULL, period_start REAL NOT NULL, body TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actsum_day ON activity_summaries(day, period_start);
CREATE TABLE IF NOT EXISTS activity_profile (id INTEGER PRIMARY KEY CHECK (id = 1), content TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS activity_day_stats (day TEXT PRIMARY KEY, apps TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS activity_habits (id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, statement TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity_suggestions (id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new', confidence REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_actsug_status ON activity_suggestions(status, confidence DESC);
CREATE TABLE IF NOT EXISTS activity_patterns (id INTEGER PRIMARY KEY CHECK (id = 1), content TEXT NOT NULL DEFAULT '{}');
"""


def _tables(c: sqlite3.Connection) -> set[str]:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def _seed_old_rows(c: sqlite3.Connection) -> None:
    c.execute("INSERT INTO meetings(id, title, created_at, updated_at) VALUES ('m1', 'Standup', 1, 1)")
    c.execute("INSERT INTO meeting_segments(id, meeting_id, channel, seq, text, created_at) VALUES ('s1','m1','mic',0,'hi',1)")
    c.execute("INSERT INTO meetings_fts(title, notes, enhanced, transcript, meeting_id) VALUES ('Standup','','','hi','m1')")
    c.execute("INSERT INTO activity_events(id, ts, kind, expires_at) VALUES ('e1', 1, 'focus', 9)")
    c.execute("INSERT INTO activity_profile(id, content) VALUES (1, 'works in bursts')")


DROP_V = next(v for v, name, _ in migrations.MIGRATIONS if name == "drop_meetings_activity")


def test_migration_drops_exactly_those_tables() -> None:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(f"PRAGMA user_version = {DROP_V - 1}")
    c.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.execute("INSERT INTO settings VALUES ('meetings', '{\"sttBackend\": \"local\"}')")
    c.executescript(OLD_SCHEMA)
    _seed_old_rows(c)
    assert set(DROPPED) <= _tables(c)
    assert migrations.run(c) == list(range(DROP_V, migrations.latest() + 1))
    left = _tables(c)
    assert not set(DROPPED) & left, set(DROPPED) & left
    assert not {n for n in left if n.startswith(("meetings_fts", "meeting_", "activity_"))}, left  # shadow tables too
    assert {"projects", "settings"} <= left
    assert c.execute("SELECT value FROM settings WHERE key='meetings'").fetchone()[0]  # settings rows are not touched
    assert migrations.run(c) == []  # idempotent


def test_a_fresh_database_has_none_of_the_tables() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Database(td)
        with db.connect() as c:
            assert migrations.current(c) == migrations.latest() >= DROP_V
            assert not set(DROPPED) & _tables(c)


def test_an_old_database_migrates_and_the_app_starts_on_it() -> None:
    data = tempfile.mkdtemp(prefix="dropmeet-")
    db = Database(data)
    with db.connect() as c:
        c.executescript(OLD_SCHEMA)
        _seed_old_rows(c)
        c.execute("PRAGMA user_version = 12")
    db.set_settings({"meetings": {"enabled": True, "sttBackend": "local", "whisperModelPath": "/m.bin", "diarize": True,
                                  "consentedAt": 5.0},
                     "activity": {"enabled": True, "signals": {"apps": True}},
                     "digest": {"enabled": True, "hour": 8},
                     "hiddenViews": ["meetings", "activity"], "homeWidgets": {"meetings": True}})
    del db

    reopened = Database(data)  # what the app does at import
    with reopened.connect() as c:
        assert migrations.current(c) == migrations.latest()
        assert not set(DROPPED) & _tables(c)
    assert (Path(data) / "backups").is_dir()  # a database with content is snapshotted before a pending step

    os.environ["PERSONAL_OS_DATA_DIR"] = data
    os.environ["PERSONAL_OS_AUTH_TOKEN"] = "test-token"
    from fastapi.testclient import TestClient

    from personal_os.app import AUTH_TOKEN, app

    with TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN}) as client:  # runs startup and shutdown hooks
        settings = client.get("/settings")
        assert settings.status_code == 200, settings.text
        voice = client.get("/voice/config").json()
        assert voice["sttBackend"] == "local" and voice["whisperModelPath"] == "/m.bin"
        assert "diarize" not in voice
        assert client.get("/inbox").status_code == 200
        assert client.get("/system/access").status_code == 200
        for gone in ("/meetings", "/meetings/status", "/activity/status", "/activity/insights"):
            assert client.get(gone).status_code == 404, gone
    assert json.dumps(settings.json())  # still serialisable with the stale rows in it


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
