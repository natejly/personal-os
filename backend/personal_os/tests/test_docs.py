"""Authored docs: storage, version history, diffs, and the /docs routes the editor calls.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_docs -v
The routes matter here because FastAPI serves Swagger at /docs by default: the app must move it
aside (docs_url) or every editor list request would get an HTML page instead of JSON.
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docstest-"))

from personal_os.db import Database, new_id, now
from personal_os.docs import FORMATS, MAX_VERSIONS, Docs


class DocsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(tempfile.mkdtemp(prefix="docstest-"))
        self.d = Docs(self.db)

    def project(self, name: str = "Proj") -> str:
        pid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", (pid, name, now()))
        return pid


class TestCrud(DocsCase):
    def test_create_list_get_delete(self) -> None:
        doc = self.d.create("Notes", "# Hello\nworld", "md")
        self.assertEqual(doc["title"], "Notes")
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["body"], "# Hello\nworld")
        self.assertEqual(doc["chars"], len("# Hello\nworld"))
        rows = self.d.list()
        self.assertEqual([r["id"] for r in rows], [doc["id"]])
        self.assertNotIn("body", rows[0])  # list rows are DocMeta: preview, not the whole body
        self.assertEqual(rows[0]["preview"], "# Hello world")  # whitespace collapsed
        got = self.d.get(doc["id"])
        assert got is not None
        self.assertEqual(got["body"], "# Hello\nworld")
        self.d.delete(doc["id"])
        self.assertIsNone(self.d.get(doc["id"]))
        self.assertEqual(self.d.list(), [])

    def test_empty_doc_starts_at_version_zero(self) -> None:
        doc = self.d.create("Blank")
        self.assertEqual(doc["version"], 0)
        self.assertEqual(doc["body"], "")
        self.assertEqual(self.d.versions(doc["id"]), [])

    def test_untitled_default(self) -> None:
        self.assertEqual(self.d.create()["title"], "Untitled")
        self.assertEqual(self.d.create("   ")["title"], "Untitled")

    def test_format_is_validated(self) -> None:
        for f in FORMATS:
            self.assertEqual(self.d.create(format=f)["format"], f)
        with self.assertRaises(ValueError):
            self.d.create(format="docx")
        doc = self.d.create()
        with self.assertRaises(ValueError):
            self.d.update(doc["id"], {"format": "html"})

    def test_scope_filter_and_search(self) -> None:
        pid = self.project()
        a = self.d.create("Personal", "alpha text", project_id=None)
        b = self.d.create("Project doc", "beta text", project_id=pid)
        self.assertEqual({r["id"] for r in self.d.list()}, {a["id"], b["id"]})
        self.assertEqual([r["id"] for r in self.d.list(None)], [a["id"]])
        self.assertEqual([r["id"] for r in self.d.list(pid)], [b["id"]])
        self.assertEqual([r["id"] for r in self.d.list(q="beta")], [b["id"]])
        self.assertEqual([r["id"] for r in self.d.list(q="Personal")], [a["id"]])

    def test_metadata_update_does_not_touch_versions(self) -> None:
        doc = self.d.create("Old", "body", "md")
        got = self.d.update(doc["id"], {"title": "New", "format": "txt"})
        assert got is not None
        self.assertEqual((got["title"], got["format"], got["version"]), ("New", "txt", 1))
        self.assertEqual(len(self.d.versions(doc["id"])), 1)

    def test_update_missing_doc_returns_none(self) -> None:
        self.assertIsNone(self.d.update("nope", {"title": "x"}))
        self.assertIsNone(self.d.update("nope", {"body": "x"}))


class TestVersions(DocsCase):
    def test_versions_are_monotonic_and_sourced(self) -> None:
        doc = self.d.create("V", "one", source="user")
        self.d.update(doc["id"], {"body": "two", "source": "agent"})
        got = self.d.update(doc["id"], {"body": "three"})
        assert got is not None
        self.assertEqual(got["version"], 3)
        vs = self.d.versions(doc["id"])
        self.assertEqual([v["version"] for v in vs], [3, 2, 1])
        self.assertEqual([v["source"] for v in vs], ["user", "agent", "user"])
        self.assertEqual([v["chars"] for v in vs], [5, 3, 3])
        self.assertNotIn("body", vs[0])

    def test_noop_save_burns_no_version_number(self) -> None:
        doc = self.d.create("V", "same")
        got = self.d.update(doc["id"], {"body": "same"})
        assert got is not None
        self.assertEqual(got["version"], 1)
        self.assertEqual(len(self.d.versions(doc["id"])), 1)

    def test_restore_is_a_new_version(self) -> None:
        doc = self.d.create("V", "one")
        self.d.update(doc["id"], {"body": "two"})
        got = self.d.restore(doc["id"], 1)
        assert got is not None
        self.assertEqual((got["version"], got["body"]), (3, "one"))
        self.assertEqual(self.d.versions(doc["id"])[0]["source"], "restore")
        self.assertIsNone(self.d.restore(doc["id"], 99))

    def test_version_fetch_carries_the_body(self) -> None:
        doc = self.d.create("V", "one")
        self.d.update(doc["id"], {"body": "two"})
        v1 = self.d.version(doc["id"], 1)
        assert v1 is not None
        self.assertEqual((v1["body"], v1["chars"], v1["source"]), ("one", 3, "user"))
        self.assertIsNone(self.d.version(doc["id"], 5))

    def test_pruning_does_not_reuse_numbers(self) -> None:
        doc = self.d.create("V", "v0")
        for i in range(MAX_VERSIONS + 3):
            self.d.update(doc["id"], {"body": f"body {i}"})
        vs = self.d.versions(doc["id"])
        self.assertEqual(len(vs), MAX_VERSIONS)
        self.assertEqual(vs[0]["version"], MAX_VERSIONS + 4)

    def test_delete_cascades_versions(self) -> None:
        doc = self.d.create("V", "one")
        self.d.update(doc["id"], {"body": "two"})
        self.d.delete(doc["id"])
        with self.db.tx() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM doc_versions WHERE doc_id=?", (doc["id"],)).fetchone()["n"]
        self.assertEqual(n, 0)

    def test_diff(self) -> None:
        doc = self.d.create("D", "a\nb\nc\n")
        self.d.update(doc["id"], {"body": "a\nB\nc\n"})
        out = self.d.diff(doc["id"], 1, 2)
        assert out is not None
        self.assertEqual((out["frm"], out["to"]), (1, 2))
        self.assertIn("-b", out["diff"])
        self.assertIn("+B", out["diff"])
        self.assertIsNone(self.d.diff(doc["id"], 1, 9))


class TestRoutes(unittest.TestCase):
    """The seams the renderer's api.docs.* actually hits."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        from personal_os.app import AUTH_TOKEN, app

        cls.client = TestClient(app, headers={"x-personal-os-token": AUTH_TOKEN})

    def test_docs_route_is_json_not_swagger(self) -> None:
        r = self.client.get("/docs")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)  # Swagger UI would be HTML: docs_url must stay off /docs

    def test_crud_versions_diff_round_trip(self) -> None:
        r = self.client.post("/docs", json={"title": "Route doc", "body": "one", "format": "md"})
        self.assertEqual(r.status_code, 200)
        doc = r.json()
        self.assertEqual((doc["title"], doc["version"], doc["chars"]), ("Route doc", 1, 3))
        did = doc["id"]
        self.assertEqual(self.client.put(f"/docs/{did}", json={"body": "two", "source": "agent"}).json()["version"], 2)
        vs = self.client.get(f"/docs/{did}/versions").json()
        self.assertEqual([v["version"] for v in vs], [2, 1])
        self.assertEqual(self.client.get(f"/docs/{did}/versions/1").json()["body"], "one")
        self.assertIn("+two", self.client.get(f"/docs/{did}/diff", params={"frm": 1, "to": 2}).json()["diff"])
        self.assertEqual(self.client.post(f"/docs/{did}/restore", json={"version": 1}).json()["body"], "one")
        self.assertEqual(self.client.delete(f"/docs/{did}").status_code, 200)
        self.assertEqual(self.client.get(f"/docs/{did}").status_code, 404)

    def test_bad_format_is_400_and_missing_doc_404(self) -> None:
        self.assertEqual(self.client.post("/docs", json={"format": "docx"}).status_code, 400)
        self.assertEqual(self.client.get("/docs/nope").status_code, 404)
        self.assertEqual(self.client.put("/docs/nope", json={"body": "x"}).status_code, 404)
        self.assertEqual(self.client.post("/docs/nope/restore", json={"version": 1}).status_code, 404)

    def test_backup_without_google_is_a_clean_error(self) -> None:
        did = self.client.post("/docs", json={"title": "B", "body": "x"}).json()["id"]
        r = self.client.post(f"/docs/{did}/backup")
        self.assertIn(r.status_code, (409, 502))  # 409 GoogleNotConnected in tests; never a 500
        self.client.delete(f"/docs/{did}")


if __name__ == "__main__":
    unittest.main()
