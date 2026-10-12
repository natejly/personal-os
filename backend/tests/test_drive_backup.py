"""Drive backup: incremental skip, failure reporting, and the not-connected / missing-scope paths. Drive is faked."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="drivebk-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import blobs  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.drive_backup import DriveBackup, device_label  # noqa: E402
from personal_os.repos import Documents  # noqa: E402


class FakeDrive:
    def __init__(self, status=None):
        self._status = status or {"connected": True, "needs_reauth": False, "missing_scopes": []}
        self.puts: list[tuple] = []
        self.bad: set[str] = set()
        self.names: dict[str, str] = {}  # folder id -> Drive name; a missing id is a deleted folder
        self.created: list[tuple] = []
        self.put_props: list = []

    def status(self):
        return self._status

    def drive_backup_folder_name(self, fid):
        return self.names.get(fid)

    def drive_backup_folder(self, name, props=None):
        self.created.append((name, props))
        fid = f"folder{len(self.created)}"
        self.names[fid] = name
        return fid

    def drive_backup_put(self, folder, name, path, data, existing, props=None):
        if name in self.bad:
            raise RuntimeError("quota exceeded")
        self.puts.append((name, existing))
        self.put_props.append(props)
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

    def test_folder_deleted_recreated(self):
        self.b.run()
        self.g.names.clear()  # deleted in Drive
        self.g.puts.clear()
        r = self.b.run()
        self.assertEqual((r["copied"], r["skipped"], r["ok"]), (2, 0, True))
        self.assertEqual([p[1] for p in self.g.puts], [None, None])  # fresh uploads, no stale Drive ids
        self.assertEqual(self.b.state["folder_id"], "folder2")

    def test_label_derivation(self):
        with mock.patch("personal_os.drive_backup.platform.node", return_value="host-1"):
            self.assertEqual(device_label(None), "host-1")
            self.assertEqual(device_label("   "), "host-1")
        with mock.patch("personal_os.drive_backup.platform.node", return_value=""):
            self.assertEqual(device_label(""), "this computer")
        self.assertEqual(device_label("a/b\\c"), "a-b-c")
        self.assertEqual(device_label("a\nb\tc\rd"), "a b c d")
        self.assertEqual(device_label("a\x00b\x1f\n  c\u200bd"), "ab cd")
        long = device_label("x" * 100)
        self.assertEqual(long, "x" * 60)
        self.assertEqual(device_label("y" * 59 + " zzz"), "y" * 59)  # stripped again after the cap
        self.assertEqual(device_label("\x00\x01 \u200b"), "this computer")

    def test_folder_named_for_device_and_props_on_every_put(self):
        self.db.set_settings({"driveBackupLabel": "Desk"})
        r = self.b.run()
        self.assertTrue(r["ok"])
        self.assertEqual(self.g.created, [("Grain Backup (Desk)", {"grainDevice": "Desk"})])
        self.assertEqual(self.b.state["folder_id"], "folder1")
        self.assertEqual((self.b.state["folder_name"], self.b.state["label"]), ("Grain Backup (Desk)", "Desk"))
        self.assertEqual(self.g.put_props, [{"grainDevice": "Desk"}] * 2)  # the upload and the doc
        self.docs.save(self.note["id"], content="v2")
        self.b.run()
        self.assertEqual(self.g.put_props[-1], {"grainDevice": "Desk"})  # in-place update too

    def test_label_change_repoints(self):
        self.db.set_settings({"driveBackupLabel": "Desk"})
        self.b.run()
        self.db.set_settings({"driveBackupLabel": "Laptop"})
        self.g.puts.clear()
        r = self.b.run()
        self.assertEqual((r["copied"], r["skipped"], r["ok"]), (2, 0, True))
        self.assertEqual([p[1] for p in self.g.puts], [None, None])
        self.assertEqual(self.g.created[-1][0], "Grain Backup (Laptop)")
        self.assertEqual(self.b.state["folder_id"], "folder2")
        self.assertEqual(self.b.state["folder_name"], "Grain Backup (Laptop)")

    def test_matching_folder_left_alone(self):
        self.db.set_settings({"driveBackupLabel": "Desk"})
        self.g.names["old"] = "Grain Backup (Desk)"
        self.b.state["folder_id"] = "old"
        self.b.state["files"] = {}
        self.b.run()
        self.assertEqual(self.b.state["folder_id"], "old")
        self.assertEqual(self.g.created, [])
        r = self.b.run()
        self.assertEqual((r["copied"], r["skipped"]), (0, 2))

    def test_empty_run(self):
        db = Database(Path(tempfile.mkdtemp(prefix="drivebk-")))
        Docs(db)  # creates the docs table
        b = DriveBackup(db, self.g)
        r = b.run()
        self.assertEqual((r["copied"], r["skipped"], r["failed"], r["ok"]), (0, 0, [], True))

    def test_status_has_folder_and_label(self):
        self.db.set_settings({"driveBackupLabel": "Desk"})
        s = self.b.status()
        self.assertEqual((s["folder"], s["label"], s["folder_id"]), ("Grain Backup (Desk)", "Desk", None))
        self.b.run()
        self.assertEqual(self.b.status()["folder_id"], "folder1")

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
