"""migrations.drift: a database stepped past this build, or a step number reused under another name, is reported."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402


def test_fresh_database_records_every_step_and_has_no_drift(tmp_path: Path) -> None:
    db = Database(tmp_path)
    assert db.schema_drift == []
    with db.tx() as c:
        rows = c.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    assert [tuple(r) for r in rows] == [(v, n) for v, n, _ in migrations.MIGRATIONS]


def test_newer_and_renamed_steps_are_reported(tmp_path: Path) -> None:
    Database(tmp_path)
    c = sqlite3.connect(tmp_path / "personal-os.db")
    top = migrations.latest()
    c.execute(f"PRAGMA user_version = {top + 1}")
    c.execute("INSERT INTO schema_migrations VALUES(?, 'from_another_branch', 0)", (top + 1,))
    c.execute("UPDATE schema_migrations SET name = 'collided' WHERE version = ?", (top,))
    c.commit()
    c.close()
    problems = Database(tmp_path).schema_drift
    assert any(f"version {top + 1}" in p for p in problems), problems
    assert any("'collided'" in p for p in problems) and any("'from_another_branch'" in p for p in problems), problems
