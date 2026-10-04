"""Versioned migrations, backups and rotation, staged restore, and the export archive.

Run: python -m unittest backend/tests/test_data_safety.py  (every test uses its own temp data dir)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import backups, migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402


def _old_db(d: Path) -> None:
    """A database as the app made it before user_version existed, with data in it."""
    db = Database(d)
    with db.tx() as c:
        c.execute("PRAGMA user_version = 0")
        c.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES ('c1','Hello','m',1,1)")
        c.execute("INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES ('m1','c1','user','hi there',1)")
        c.execute("INSERT INTO memories (id, content, created_at, updated_at) VALUES ('mem1','likes tea',1,1)")
        c.execute("INSERT INTO documents (id, name, path, text, created_at) VALUES ('d1','notes.txt','x','doc body',1)")


def _version(d: Path) -> int:
    c = sqlite3.connect(d / "personal-os.db")
    try:
        return int(c.execute("PRAGMA user_version").fetchone()[0])
    finally:
        c.close()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_fresh_database_is_stamped_without_a_backup(self) -> None:
        Database(self.d)
        self.assertEqual(_version(self.d), migrations.latest())
        self.assertEqual(backups.list_backups(self.d), [])

    def test_existing_database_is_adopted_without_data_loss(self) -> None:
        _old_db(self.d)
        self.assertEqual(_version(self.d), 0)
        db = Database(self.d)
        self.assertEqual(_version(self.d), migrations.latest())
        with db.tx() as c:
            self.assertEqual(c.execute("SELECT content FROM messages").fetchone()[0], "hi there")
            self.assertEqual(c.execute("SELECT content FROM memories").fetchone()[0], "likes tea")
        # Opening again is a no-op: nothing pending, so no further backup.
        n = len(backups.list_backups(self.d))
        Database(self.d)
        self.assertEqual(len(backups.list_backups(self.d)), n)

    def test_pending_migration_takes_a_backup_first_and_runs_in_order(self) -> None:
        _old_db(self.d)
        Database(self.d)
        before = len(backups.list_backups(self.d))
        seen: list[int] = []

        def step2(c: sqlite3.Connection) -> None:
            # The backup already exists by the time the step runs.
            seen.append(len(backups.list_backups(self.d)))
            c.execute("ALTER TABLE memories ADD COLUMN flavor TEXT")

        nxt = migrations.latest() + 1
        migrations.MIGRATIONS.append((nxt, "flavor", step2))
        try:
            Database(self.d)
        finally:
            migrations.MIGRATIONS.pop()
        bs = backups.list_backups(self.d)
        self.assertEqual(len(bs), before + 1)
        self.assertEqual(seen, [before + 1])
        self.assertEqual(bs[0]["kind"], "premigrate")
        self.assertEqual(bs[0]["schema_version"], nxt - 1)
        self.assertEqual(_version(self.d), nxt)

    def test_failed_step_rolls_back_and_keeps_version(self) -> None:
        _old_db(self.d)
        Database(self.d)

        def boom(c: sqlite3.Connection) -> None:
            c.execute("ALTER TABLE memories ADD COLUMN half TEXT")
            raise RuntimeError("nope")

        was = migrations.latest()
        migrations.MIGRATIONS.append((was + 1, "boom", boom))
        try:
            with self.assertRaises(RuntimeError):
                Database(self.d)
        finally:
            migrations.MIGRATIONS.pop()
        self.assertEqual(_version(self.d), was)
        c = sqlite3.connect(self.d / "personal-os.db")
        self.assertNotIn("half", [r[1] for r in c.execute("PRAGMA table_info(memories)")])
        c.close()


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        _old_db(self.d)
        self.db = Database(self.d)
        for b in backups.list_backups(self.d):
            backups.delete(self.d, b["name"])

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_backup_has_manifest_and_the_data(self) -> None:
        m = backups.create(self.d, "manual")
        self.assertEqual(m["schema_version"], migrations.latest())
        self.assertEqual(m["kind"], "manual")
        self.assertTrue(m["app_version"])
        c = sqlite3.connect(self.d / "backups" / m["name"])
        self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        c.close()
        self.assertEqual([b["name"] for b in backups.list_backups(self.d)], [m["name"]])

    def test_daily_rotation_keeps_seven_plus_four_weeks(self) -> None:
        start = datetime(2026, 1, 5, 12).timestamp()  # a Monday
        for i in range(60):  # 60 consecutive days
            backups.create(self.d, "daily", now=start + i * 86400)
        kept = backups.list_backups(self.d)
        stamps = {datetime.fromtimestamp(b["created_at"]) for b in kept}
        last7 = {datetime.fromtimestamp(start + i * 86400) for i in range(53, 60)}
        self.assertTrue(last7 <= stamps)
        # The last 7 days span 2 or 3 ISO weeks; the 4 newest weeks add at most 3 more.
        self.assertTrue(8 <= len(kept) <= 10, len(kept))
        self.assertTrue(min(stamps) > datetime.fromtimestamp(start) + timedelta(days=20))

    def test_manual_and_safety_kinds_rotate_independently(self) -> None:
        for i in range(13):
            backups.create(self.d, "manual", now=1000 + i)
        for i in range(5):
            backups.create(self.d, "premigrate", now=2000 + i)
        backups.create(self.d, "daily", now=3000)
        kinds = [b["kind"] for b in backups.list_backups(self.d)]
        self.assertEqual(kinds.count("manual"), backups.MANUAL_KEEP)
        self.assertEqual(kinds.count("premigrate"), backups.SAFETY_KEEP)
        self.assertEqual(kinds.count("daily"), 1)

    def test_due_waits_a_day(self) -> None:
        self.assertTrue(backups.due(self.d))
        backups.create(self.d, "daily", now=10_000)
        self.assertFalse(backups.due(self.d, now=10_000 + 3600))
        self.assertTrue(backups.due(self.d, now=10_000 + 90_000))

    def test_restore_is_staged_then_swapped_at_startup(self) -> None:
        m = backups.create(self.d, "manual")
        with self.db.tx() as c:
            c.execute("INSERT INTO memories (id, content, created_at, updated_at) VALUES ('later','added after backup',2,2)")
        backups.stage_restore(self.d, m["name"])
        # Staging alone leaves the live database untouched.
        with self.db.tx() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM memories").fetchone()[0], 2)
        self.assertEqual(backups.pending_restore(self.d)["name"], m["name"])
        self.assertEqual(backups.apply_pending_restore(self.d), m["name"])
        self.assertIsNone(backups.pending_restore(self.d))
        db2 = Database(self.d)
        with db2.tx() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT content FROM memories")], ["likes tea"])
        self.assertIn("prerestore", [b["kind"] for b in backups.list_backups(self.d)])
        self.assertIsNone(backups.apply_pending_restore(self.d))

    def test_stage_rejects_unknown_traversal_and_newer_schema(self) -> None:
        for bad in ("nope.db", "../personal-os.db"):
            with self.assertRaises(FileNotFoundError):
                backups.stage_restore(self.d, bad)
        m = backups.create(self.d, "manual")
        c = sqlite3.connect(self.d / "backups" / m["name"])
        c.execute("PRAGMA user_version = 99")
        c.close()
        with self.assertRaises(ValueError):
            backups.stage_restore(self.d, m["name"])

    def test_vanished_staged_backup_does_not_block_startup(self) -> None:
        m = backups.create(self.d, "manual")
        backups.stage_restore(self.d, m["name"])
        backups.delete(self.d, m["name"])
        self.assertIsNone(backups.apply_pending_restore(self.d))
        self.assertIsNone(backups.pending_restore(self.d))
        self.assertIn("no longer exists", backups.restore_failed(self.d)["error"])  # Settings → Data can say so
        Database(self.d)  # still opens

    def test_staged_backup_outlives_rotation_and_its_own_prerestore(self) -> None:
        pre = [backups.create(self.d, "prerestore", now=1000 + i)["name"] for i in range(backups.SAFETY_KEEP)]
        backups.stage_restore(self.d, pre[0])  # the oldest kept prerestore: "undo an earlier restore"
        backups.create(self.d, "prerestore", now=2000)  # rotation would drop it
        self.assertIn(pre[0], [b["name"] for b in backups.list_backups(self.d)])
        self.assertEqual(backups.apply_pending_restore(self.d), pre[0])
        self.assertIsNone(backups.restore_failed(self.d))

    def test_failed_export_leaves_no_part_file_or_dest(self) -> None:
        dest = self.d / "out.zip"
        with unittest.mock.patch.object(backups, "human_export", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                backups.export_zip(self.d, dest)
        self.assertFalse(dest.exists())
        self.assertFalse((self.d / "out.zip.part").exists())

    def test_export_zip_contents(self) -> None:
        (self.d / "uploads" / "a.txt").write_text("uploaded")
        dest = self.d / "out.zip"
        backups.export_zip(self.d, dest)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            self.assertTrue({"README.txt", "grain.db", "uploads/a.txt", "export/conversations.md",
                             "export/memories.json", "export/documents.md"} <= names)
            convs = json.loads(z.read("export/conversations.json"))
            self.assertEqual(convs[0]["messages"][0]["content"], "hi there")
            self.assertIn("hi there", z.read("export/conversations.md").decode())
            self.assertIn("likes tea", z.read("export/memories.md").decode())
            self.assertIn("doc body", z.read("export/documents.md").decode())
            snap = self.d / "snap.db"
            snap.write_bytes(z.read("grain.db"))
        c = sqlite3.connect(snap)
        self.assertEqual(c.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
        c.close()
        self.assertFalse(dest.with_name("out.zip.part").exists())


class RouteTests(unittest.TestCase):
    def test_routes(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            Database(d)
            app = FastAPI()
            app.include_router(backups.router(d))
            c = TestClient(app)
            self.assertIsNone(c.get("/data").json()["last_backup"])
            name = c.post("/data/backups").json()["name"]
            self.assertEqual(c.get("/data").json()["backups"][0]["name"], name)
            self.assertEqual(c.post("/data/backups/missing.db/restore").status_code, 404)
            self.assertTrue(c.post(f"/data/backups/{name}/restore").json()["restart_required"])
            self.assertEqual(c.get("/data").json()["pending_restore"]["name"], name)
            c.delete("/data/restore")
            self.assertIsNone(c.get("/data").json()["pending_restore"])
            self.assertEqual(c.post("/data/export", json={"dest": "relative.zip"}).status_code, 400)
            r = c.post("/data/export", json={"dest": str(d / "e.zip")})
            self.assertTrue((d / "e.zip").exists() and r.json()["size"] > 0)


if __name__ == "__main__":
    unittest.main()
