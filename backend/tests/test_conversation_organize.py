"""Pin, archive and move a conversation. Run: pytest backend/tests/test_conversation_organize.py"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="organizetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations, Projects  # noqa: E402


class RepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.projects, self.convos = Projects(self.db), Conversations(self.db)
        self.p1 = self.projects.create("One")["id"]
        self.p2 = self.projects.create("Two")["id"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_adds_columns(self) -> None:
        with self.db.tx() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(conversations)")}
        self.assertIn("pinned_at", cols)
        self.assertIn("archived_at", cols)

    def test_pin_sets_and_clears_without_touching_updated_at(self) -> None:
        c = self.convos.create(None, "t", "m")
        out = self.convos.update(c["id"], {"pinned": True})
        self.assertTrue(out["pinned_at"])
        self.assertEqual(out["updated_at"], c["updated_at"])
        self.assertIsNone(self.convos.update(c["id"], {"pinned": False})["pinned_at"])

    def test_archive_hides_clears_pin_and_still_opens(self) -> None:
        c = self.convos.create(None, "t", "m")
        self.convos.add_message(c["id"], "user", "hi")
        self.convos.update(c["id"], {"pinned": True})
        self.convos.update(c["id"], {"archived": True})
        self.assertEqual(self.convos.list(None), [])
        self.assertEqual([x["id"] for x in self.convos.list(None, archived=True)], [c["id"]])
        got = self.convos.get(c["id"])
        self.assertIsNone(got["pinned_at"])
        self.assertEqual(len(got["messages"]), 1)
        self.convos.update(c["id"], {"archived": False})
        self.assertEqual(len(self.convos.list(None)), 1)

    def test_project_move(self) -> None:
        c = self.convos.create(self.p1, "t", "m")
        self.convos.update(c["id"], {"title": "x"})
        self.assertEqual(self.convos.get(c["id"])["project_id"], self.p1)
        self.convos.update(c["id"], {"project_id": self.p2})
        self.assertEqual(len(self.convos.list(self.p2)), 1)
        self.convos.update(c["id"], {"project_id": None})
        self.assertIsNone(self.convos.get(c["id"])["project_id"])


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from personal_os import app as appmod
        cls.appmod = appmod
        cls.client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})

    def j(self, method: str, path: str, body: object = None, expect: int = 200):
        r = self.client.request(method, path, json=body)
        self.assertEqual(r.status_code, expect, f"{method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json()

    def test_routes(self) -> None:
        proj = self.j("POST", "/projects", {"name": "P"})
        c = self.j("POST", "/conversations", {"project_id": proj["id"]})
        self.j("PATCH", f"/conversations/{c['id']}", {"project_id": "nope"}, 404)
        self.assertEqual(self.j("PATCH", f"/conversations/{c['id']}", {"title": "x"})["project_id"], proj["id"])
        self.assertIsNone(self.j("PATCH", f"/conversations/{c['id']}", {"project_id": None})["project_id"])
        # a live run blocks a move and an archive
        orig = self.appmod.bus.live
        self.appmod.bus.live = lambda _id: object()  # type: ignore[method-assign]
        try:
            self.j("PATCH", f"/conversations/{c['id']}", {"project_id": proj["id"]}, 409)
            self.j("PATCH", f"/conversations/{c['id']}", {"archived": True}, 409)
        finally:
            self.appmod.bus.live = orig  # type: ignore[method-assign]
        # desk-owned
        d = self.j("POST", "/conversations", {"project_id": None})
        self.j("PATCH", f"/conversations/{d['id']}", {"settings": {"deskId": "d1"}})
        self.j("PATCH", f"/conversations/{d['id']}", {"project_id": proj["id"]}, 409)
        # archive list
        self.j("PATCH", f"/conversations/{c['id']}", {"archived": True})
        ids = [x["id"] for x in self.j("GET", "/conversations?project_id=all&archived=true")]
        self.assertEqual(ids, [c["id"]])
        self.assertNotIn(c["id"], [x["id"] for x in self.j("GET", "/conversations?project_id=all")])


if __name__ == "__main__":
    unittest.main()
