"""Drive backup: incremental skip, failure reporting, and the not-connected / missing-scope paths. Drive is faked."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="drivebk-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import blobs  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.drive_backup import DriveBackup  # noqa: E402
from personal_os.repos import Documents  # noqa: E402


class FakeDrive:
    def __init__(self, status=None):
        self._status = status or {"connected": True, "needs_reauth": False, "missing_scopes": []}
        self.puts: list[tuple] = []
        self.bad: set[str] = set()

    def status(self):
        return self._status

    def drive_backup_folder(self, name):
        return "folder1"

    def drive_backup_put(self, folder, name, path, data, existing):
        if name in self.bad:
            raise RuntimeError("quota exceeded")
        self.puts.append((name, existing))
        return existing or f"id{len(self.puts)}"


class DriveBackupTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(Path(tempfile.mkdtemp(prefix="drivebk-")))
        self.docs = Docs(self.db)
        path, digest = blobs.store(self.db.data_dir, "a.txt", b"hello")
        Documents(self.db).create(None, "a.txt", "text/plain", 5, str(path), "hello")
        self.note = self.docs.create("Note", "v1")
        self.g = FakeDrive()
        self.b = DriveBackup(self.db, self.g)

    def test_incremental(self):
        r = self.b.run()
        self.assertEqual((r["copied"], r["skipped"], r["ok"]), (2, 0, True))
        r = self.b.run()
        self.assertEqual((r["copied"], r["skipped"]), (0, 2))
        self.docs.save(self.note["id"], content="v2")
        r = DriveBackup(self.db, self.g).run()  # state reloads from disk
        self.assertEqual((r["copied"], r["skipped"]), (1, 1))
        self.assertIsNotNone(self.g.puts[-1][1])  # edited doc updated in place

    def test_failure_reported(self):
        self.g.bad.add("a.txt")
        r = self.b.run()
        self.assertFalse(r["ok"])
        self.assertEqual((r["copied"], [f["name"] for f in r["failed"]]), (1, ["a.txt"]))
        self.g.bad.clear()
        self.assertEqual(self.b.run()["copied"], 1)  # the failed one is retried, the good one skipped

    def test_not_connected(self):
        self.g._status = {"connected": False, "needs_reauth": False, "missing_scopes": []}
        r = self.b.run()
        self.assertFalse(r["ok"])
        self.assertIn("not connected", r["error"])
        self.assertEqual(self.g.puts, [])

    def test_missing_scope(self):
        self.g._status = {"connected": True, "needs_reauth": True,
                          "missing_scopes": ["https://www.googleapis.com/auth/drive.file"]}
        r = self.b.run()
        self.assertIn("Reconnect", r["error"])
        self.assertEqual(self.b.status()["last"]["error"], r["error"])


if __name__ == "__main__":
    unittest.main()
