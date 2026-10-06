"""autonomousByDefault: an ordinary setting, true by default, kept through PUT /settings, written true for existing installs
by migration 21 (never by editing an old one)."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="autonomous-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, migrations  # noqa: E402


def test_default_is_on_and_reaches_the_renderer() -> None:
    assert llm.DEFAULT_SETTINGS["autonomousByDefault"] is True
    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    assert client.get("/settings").json()["autonomousByDefault"] is True


def test_put_accepts_a_bool_and_refuses_anything_else() -> None:
    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    try:
        assert client.put("/settings", json={"autonomousByDefault": False}).json()["autonomousByDefault"] is False
        assert client.get("/settings").json()["autonomousByDefault"] is False
        assert client.put("/settings", json={"autonomousByDefault": "yes"}).status_code == 422
        assert client.get("/settings").json()["autonomousByDefault"] is False  # a refused update changes nothing
    finally:
        client.put("/settings", json={"autonomousByDefault": True})
    assert client.get("/settings").json()["autonomousByDefault"] is True


def test_migration_21_turns_it_on_for_an_existing_install_and_keeps_a_stored_value() -> None:
    assert migrations.MIGRATIONS[-1][1] == "autonomous_by_default" and migrations.latest() == 21
    assert [v for v, _n, _s in migrations.MIGRATIONS] == list(range(1, 22))  # consecutive, nothing edited or reordered

    def at_20() -> sqlite3.Connection:
        c = sqlite3.connect(":memory:")
        c.isolation_level = None
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("PRAGMA user_version = 20")
        return c

    c = at_20()
    assert [m[0] for m in migrations.pending(c)] == [21]
    assert migrations.run(c) == [21]
    assert json.loads(c.execute("SELECT value FROM settings WHERE key='autonomousByDefault'").fetchone()[0]) is True
    assert migrations.current(c) == 21 and migrations.run(c) == []

    c = at_20()  # a value that is already stored survives
    c.execute("INSERT INTO settings(key, value) VALUES('autonomousByDefault', 'false')")
    migrations.run(c)
    assert json.loads(c.execute("SELECT value FROM settings WHERE key='autonomousByDefault'").fetchone()[0]) is False


def test_a_new_database_ends_up_with_it_on() -> None:
    from personal_os.db import Database
    db = Database(Path(tempfile.mkdtemp(prefix="autonomous-db-")))
    assert db.get_settings()["autonomousByDefault"] is True
