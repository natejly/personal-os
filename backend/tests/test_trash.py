"""Soft delete: trashed rows vanish from every read path, restore brings them back intact, purge only takes
old items, and a trashed todo still deletes its Google task. Run: pytest backend/tests/test_trash.py"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="trashtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.gtasks import TasksSync  # noqa: E402
from personal_os.repos import Conversations, Documents, Memories, Projects  # noqa: E402
from personal_os.todos import Todos  # noqa: E402
from personal_os.trash import DAY, RETENTION_DAYS, Trash  # noqa: E402
from test_gtasks_sync import _FakeGoogle  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.projects, self.convos, self.memories = Projects(self.db), Conversations(self.db), Memories(self.db)
        self.documents, self.docs, self.todos = Documents(self.db), Docs(self.db), Todos(self.db)
        self.trash = Trash(self.db, self.todos, self.docs)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def age(self, table: str, id: str, days: float) -> None:
        with self.db.tx() as c:
            c.execute(f"UPDATE {table} SET deleted_at=? WHERE id=?", (time.time() - days * DAY, id))


class ReadPathTests(_Base):
    def test_conversation(self) -> None:
        p = self.projects.create("P")
        c = self.convos.create(p["id"], "Plan the launch", "m")
        self.convos.add_message(c["id"], "user", "hello")
        self.assertTrue(self.trash.trash("conversation", c["id"]))
        self.assertEqual(self.convos.list(p["id"]), [])
        self.assertIsNone(self.convos.get(c["id"]))
        self.assertEqual(self.projects.stats(p["id"])["conversations"], 0)
        out = self.trash.restore("conversation", c["id"])
        self.assertFalse(out["moved_to_personal"])
        back = self.convos.get(c["id"])
        self.assertEqual((back["title"], back["messages"][0]["content"]), ("Plan the launch", "hello"))

    def test_project_stats_count_written_docs_apart_from_uploads(self) -> None:
        p = self.projects.create("P")
        d = self.docs.create("Notes", project_id=p["id"])
        self.docs.create("Other", project_id=p["id"])
        self.assertEqual((self.projects.stats(p["id"])["docs"], self.projects.stats(p["id"])["documents"]), (2, 0))
        self.trash.trash("doc", d["id"])
        self.assertEqual(self.projects.stats(p["id"])["docs"], 1)

    def test_memory_context_search_and_dedup(self) -> None:
        m = self.memories.create(None, "Prefers oat milk in coffee")
        self.trash.trash("memory", m["id"])
        self.assertEqual(self.memories.list(None), [])
        self.assertEqual(self.memories.list(None, q="oat"), [])
        self.assertEqual(self.memories.for_context(None, "coffee milk"), [])
        # Saving the same fact again must make a visible row, not hand back the trashed one.
        again = self.memories.create(None, "Prefers oat milk in coffee")
        self.assertNotEqual(again["id"], m["id"])
        self.assertEqual(len(self.memories.list(None)), 1)
        self.trash.restore("memory", m["id"])
        self.assertEqual(len(self.memories.list(None, q="oat")), 2)

    def test_doc_search_list_find_and_folders(self) -> None:
        d = self.docs.create("Launch notes", "the quokka plan", None, "Work/Plans")
        self.trash.trash("doc", d["id"])
        self.assertEqual(self.docs.list(), [])
        self.assertEqual(self.docs.search("quokka"), [])
        self.assertIsNone(self.docs.get(d["id"]))
        self.assertIsNone(self.docs.find("Launch notes"))
        self.assertEqual([f for f in self.docs.folders() if f["docs"]], [])
        self.trash.restore("doc", d["id"])
        self.assertEqual(len(self.docs.search("quokka")), 1)
        self.assertEqual(self.docs.get(d["id"])["content"], "the quokka plan")

    def test_delete_folder_with_docs_goes_to_trash(self) -> None:
        a = self.docs.create("A", "x", None, "F")
        b = self.docs.create("B", "y", None, "F/Sub")
        self.docs.delete_folder("F", delete_docs=True)
        self.assertEqual(self.docs.list(), [])
        titles = {i["title"] for i in self.trash.list()["groups"]["docs"]}
        self.assertEqual(titles, {"A", "B"})
        self.trash.restore("doc", b["id"])
        self.assertEqual(self.docs.get(b["id"])["folder"], "F/Sub")
        self.assertIsNotNone(a)

    def test_uploaded_document_search(self) -> None:
        d = self.documents.create(None, "spec.txt", "text/plain", 5, str(Path(self.tmp.name) / "spec.txt"), "zebra crossing rules")
        self.trash.trash("document", d["id"])
        self.assertEqual(self.documents.list(None), [])
        self.assertEqual(self.documents.search(None, "zebra"), [])
        self.trash.restore("document", d["id"])
        self.assertEqual(len(self.documents.search(None, "zebra")), 1)

    def test_todo_lists_and_stats(self) -> None:
        t = self.todos.create("Pay rent", due="2020-01-01")
        self.trash.trash("todo", t["id"])
        self.assertEqual(self.todos.list(include_done=True), [])
        self.assertEqual(self.todos.list(q="rent"), [])
        self.assertIsNone(self.todos.get(t["id"]))
        self.assertEqual(self.todos.stats(), {"open": 0, "overdue": 0, "today": 0})
        self.trash.restore("todo", t["id"])
        self.assertEqual(self.todos.get(t["id"])["title"], "Pay rent")


class ProjectCascadeTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.p = self.projects.create("Atlas")
        pid = self.p["id"]
        self.conv = self.convos.create(pid, "Atlas chat", "m")
        self.mem = self.memories.create(pid, "Atlas uses postgres")
        self.doc = self.docs.create("Atlas doc", "body", pid, "Specs")
        self.todo = self.todos.create("Atlas todo", project_id=pid)
        self.kb = self.documents.create(pid, "atlas.txt", "text/plain", 3, str(Path(self.tmp.name) / "atlas.txt"), "narwhal facts")
        self.other_conv = self.convos.create(pid, "Deleted earlier", "m")
        self.trash.trash("conversation", self.other_conv["id"])

    def test_everything_hidden_and_listed_once(self) -> None:
        pid = self.p["id"]
        self.trash.trash("project", pid)
        self.assertEqual(self.projects.list(), [])
        self.assertEqual(self.convos.list(None), [])  # not in the personal scope either
        self.assertEqual(self.convos.list(pid), [])
        self.assertEqual(self.memories.list("__all__"), [])
        self.assertEqual(self.documents.list("__all__"), [])
        self.assertEqual(self.documents.search("__all__", "narwhal"), [])
        # Docs and todos are the user's own writing: demoted to personal, never hidden.
        self.assertIsNone(self.docs.get(self.doc["id"])["project_id"])
        self.assertIsNone(self.todos.get(self.todo["id"])["project_id"])
        listing = self.trash.list()
        self.assertEqual([i["id"] for i in listing["groups"]["projects"]], [pid])
        self.assertEqual(listing["groups"]["projects"][0]["contents"]["conversations"], 1)
        # Only the chat deleted on its own is listed separately; the cascaded rows ride with the project.
        self.assertEqual([i["id"] for i in listing["groups"]["conversations"]], [self.other_conv["id"]])
        self.assertEqual(listing["groups"]["docs"], [])
        self.assertEqual(listing["groups"]["todos"], [])

    def test_restore_project_brings_back_only_its_rows(self) -> None:
        pid = self.p["id"]
        self.trash.trash("project", pid)
        self.assertTrue(self.trash.restore("project", pid))
        self.assertEqual(self.projects.get(pid)["name"], "Atlas")
        self.assertEqual([c["id"] for c in self.convos.list(pid)], [self.conv["id"]])
        self.assertEqual(len(self.memories.list(pid)), 1)
        self.assertEqual(len(self.documents.search("__all__", "narwhal")), 1)
        self.assertIsNone(self.convos.get(self.other_conv["id"]))  # was in the trash before the project

    def test_restoring_a_child_of_a_trashed_project_lands_in_personal(self) -> None:
        pid = self.p["id"]
        self.trash.trash("project", pid)
        out = self.trash.restore("conversation", self.other_conv["id"])
        self.assertTrue(out["moved_to_personal"])
        self.assertIsNone(self.convos.get(self.other_conv["id"])["project_id"])

    def test_purge_project_erases_chats_and_keeps_docs_and_todos(self) -> None:
        pid = self.p["id"]
        self.trash.trash("project", pid)
        self.assertTrue(self.trash.purge("project", pid))
        self.assertIsNone(self.projects.get(pid))
        self.assertIsNone(self.convos.get(self.conv["id"]))
        self.assertEqual(self.memories.list("__all__"), [])
        self.assertIsNone(self.documents.get(self.kb["id"]))
        self.assertEqual(self.docs.get(self.doc["id"])["folder"], "Specs")
        self.assertEqual(self.todos.get(self.todo["id"])["title"], "Atlas todo")
        # The chat trashed on its own before the project keeps its own retention window.
        self.assertTrue(self.trash.restore("conversation", self.other_conv["id"]))

    def test_purge_project_spares_items_trashed_on_their_own(self) -> None:
        pid = self.p["id"]
        f = Path(self.tmp.name) / "own.txt"
        f.write_text("x")
        own = self.documents.create(pid, "own.txt", "text/plain", 1, str(f), "walrus facts")
        mem = self.memories.create(pid, "Atlas walrus budget")
        self.trash.trash("document", own["id"])
        self.trash.trash("memory", mem["id"])
        self.trash.trash("project", pid)
        self.trash.purge("project", pid)
        self.assertTrue(f.exists())
        self.assertTrue(self.trash.restore("document", own["id"]))
        self.assertTrue(self.trash.restore("memory", mem["id"]))
        self.assertEqual([m["id"] for m in self.memories.list(None, q="walrus")], [mem["id"]])

    def test_purge_refuses_live_items(self) -> None:
        self.assertFalse(self.trash.purge("project", self.p["id"]))
        self.assertFalse(self.trash.purge("conversation", self.conv["id"]))
        self.assertIsNotNone(self.convos.get(self.conv["id"]))


class PurgeTests(_Base):
    def test_purge_old_only_takes_old(self) -> None:
        old = self.memories.create(None, "old fact")
        new = self.memories.create(None, "new fact")
        olddoc = self.docs.create("old doc", "x", None)
        oldproj = self.projects.create("Old project")
        for kind, i in (("memory", old["id"]), ("memory", new["id"]), ("doc", olddoc["id"]), ("project", oldproj["id"])):
            self.trash.trash(kind, i)
        self.age("memories", old["id"], RETENTION_DAYS + 1)
        self.age("docs", olddoc["id"], RETENTION_DAYS + 1)
        self.age("projects", oldproj["id"], RETENTION_DAYS + 1)
        self.age("memories", new["id"], RETENTION_DAYS - 1)
        self.assertEqual(self.trash.purge_old(), 3)
        left = self.trash.list()
        self.assertEqual([i["id"] for i in left["groups"]["memories"]], [new["id"]])
        self.assertEqual(left["total"], 1)
        self.assertIsNone(self.trash.restore("memory", old["id"]))

    def test_empty_trash(self) -> None:
        for n in range(3):
            self.trash.trash("memory", self.memories.create(None, f"fact {n}")["id"])
        keep = self.memories.create(None, "kept")
        self.assertEqual(self.trash.empty(), 3)
        self.assertEqual([m["id"] for m in self.memories.list(None)], [keep["id"]])

    def test_upload_file_removed_on_purge(self) -> None:
        f = Path(self.tmp.name) / "up.txt"
        f.write_text("hi")
        d = self.documents.create(None, "up.txt", "text/plain", 2, str(f), "hi")
        self.trash.trash("document", d["id"])
        self.assertTrue(f.exists())
        self.trash.purge("document", d["id"])
        self.assertFalse(f.exists())


class GoogleTasksTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.g = _FakeGoogle()
        self.sync = TasksSync(self.todos, self.g, lambda: {"googleTasksSync": {"enabled": True}}, lambda _p: None)  # type: ignore[arg-type]

    def test_trashed_todo_deletes_the_google_task(self) -> None:
        td = self.todos.create("Old task")
        self.sync.sync_once()
        eid = self.todos.get(td["id"])["external_id"]
        self.assertIn(eid, self.g.tasks)
        self.trash.trash("todo", td["id"])
        counts = self.sync.sync_once()
        self.assertEqual(counts["deleted_remote"], 1)
        self.assertNotIn(eid, self.g.tasks)
        self.assertEqual(self.todos.tombstones(), [])
        self.assertEqual(self.todos.list(include_done=True), [])  # and it is not re-imported
        self.assertEqual(self.sync.sync_once()["created_remote"], 0)  # nor pushed again while trashed

    def test_restored_todo_comes_back_as_a_new_task(self) -> None:
        td = self.todos.create("Back again")
        self.sync.sync_once()
        self.trash.trash("todo", td["id"])
        self.sync.sync_once()
        self.trash.restore("todo", td["id"])
        counts = self.sync.sync_once()
        self.assertEqual(counts["created_remote"], 1)
        self.assertEqual([t["title"] for t in self.g.tasks.values()], ["Back again"])
        self.assertEqual(len(self.todos.list()), 1)

    def test_purging_a_project_erases_its_chats_files(self) -> None:
        from personal_os.workspace import Workspace
        chats = Workspace(self.db.data_dir, sub="chats")
        p = self.projects.create("P")
        c = self.convos.create(p["id"], "Inside", "m")
        chats.save_bytes(c["id"], "outputs/a.txt", b"a")
        self.trash.trash("project", p["id"])
        self.assertTrue(chats.desk_root(c["id"]).exists())
        self.trash.purge("project", p["id"])
        self.assertFalse(chats.desk_root(c["id"]).exists())

    def test_purge_does_not_double_tombstone(self) -> None:
        td = self.todos.create("Once")
        self.sync.sync_once()
        self.trash.trash("todo", td["id"])
        self.sync.sync_once()
        self.trash.purge("todo", td["id"])
        self.assertEqual(self.todos.tombstones(), [])


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from personal_os.app import AUTH_TOKEN, app
        cls.client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})

    def j(self, method: str, path: str, body: object = None, expect: int = 200):
        r = self.client.request(method, path, json=body)
        self.assertEqual(r.status_code, expect, f"{method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json()

    def test_delete_routes_trash_and_restore_roundtrip(self) -> None:
        j = self.j
        proj = j("POST", "/projects", {"name": "Route project"})
        conv = j("POST", "/conversations", {"project_id": proj["id"], "title": "Route chat"})
        doc = j("POST", "/docs", {"title": "Route doc", "content": "findable zzyzx", "project_id": proj["id"]})
        mem = j("POST", "/memories", {"content": "route memory zzyzx", "project_id": None})
        todo = j("POST", "/todos", {"title": "Route todo"})

        j("DELETE", f"/conversations/{conv['id']}")
        j("DELETE", f"/docs/{doc['id']}")
        j("DELETE", f"/memories/{mem['id']}")
        j("DELETE", f"/todos/{todo['id']}")
        j("GET", f"/conversations/{conv['id']}", expect=404)
        j("GET", f"/docs/{doc['id']}", expect=404)
        self.assertNotIn(todo["id"], [t["id"] for t in j("GET", "/todos")])
        self.assertNotIn(mem["id"], [m["id"] for m in j("GET", "/memories")])

        listing = j("GET", "/trash")
        ids = {i["id"] for g in listing["groups"].values() for i in g}
        self.assertTrue({conv["id"], doc["id"], mem["id"], todo["id"]} <= ids)

        for kind, i in (("conversation", conv["id"]), ("doc", doc["id"]), ("memory", mem["id"]), ("todo", todo["id"])):
            j("POST", f"/trash/{kind}/{i}/restore")
        self.assertEqual(j("GET", f"/docs/{doc['id']}")["content"], "findable zzyzx")
        self.assertEqual(j("GET", f"/conversations/{conv['id']}")["title"], "Route chat")
        j("POST", f"/trash/doc/{doc['id']}/restore", expect=404)  # already back

        j("DELETE", f"/projects/{proj['id']}")
        self.assertNotIn(proj["id"], [p["id"] for p in j("GET", "/projects")])
        j("POST", f"/trash/project/{proj['id']}/restore")
        self.assertIn(proj["id"], [p["id"] for p in j("GET", "/projects")])

        j("DELETE", f"/memories/{mem['id']}")
        j("DELETE", f"/trash/memory/{mem['id']}")
        j("DELETE", f"/trash/memory/{mem['id']}", expect=404)
        j("POST", f"/trash/bogus/{mem['id']}/restore", expect=404)

    def test_chat_outputs_routes_and_purge(self) -> None:
        from personal_os.app import toolbox
        conv = self.j("POST", "/conversations", {"title": "Outputs chat"})
        cid = conv["id"]
        self.assertEqual(self.j("GET", f"/conversations/{cid}/outputs")["files"], [])
        toolbox.chat_outputs.save_bytes(cid, "outputs/report.html", b"<script>x</script>")
        listing = self.j("GET", f"/conversations/{cid}/outputs")
        self.assertEqual([f["path"] for f in listing["files"]], ["outputs/report.html"])
        self.assertTrue(listing["folder"].endswith(f"chats/{cid}/outputs"))
        r = self.client.get(f"/conversations/{cid}/outputs/download", params={"path": "outputs/report.html"})
        self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "application/octet-stream", b"<script>x</script>"))
        self.assertEqual(self.client.get(f"/conversations/{cid}/outputs/download", params={"path": "../../personal-os.db"}).status_code, 400)
        self.assertEqual(self.client.get(f"/conversations/{cid}/outputs/download", params={"path": "outputs/nope.bin"}).status_code, 404)
        self.assertEqual(self.client.get("/conversations/nosuchchat/outputs").status_code, 404)
        root = toolbox.chat_outputs.desk_root(cid)
        self.j("DELETE", f"/conversations/{cid}")
        self.assertTrue(root.exists())  # trashed, not erased: restoring the chat brings its files back
        self.j("DELETE", f"/trash/conversation/{cid}")
        self.assertFalse(root.exists())

    def test_trash_routes_require_the_token(self) -> None:
        from personal_os.app import app
        bare = TestClient(app)
        for method, path in (("GET", "/trash"), ("POST", "/trash/todo/x/restore"), ("DELETE", "/trash/todo/x"), ("DELETE", "/trash")):
            self.assertEqual(bare.request(method, path).status_code, 401, f"{method} {path}")

    def test_empty_trash_route(self) -> None:
        m = self.j("POST", "/memories", {"content": "to be emptied", "project_id": None})
        self.j("DELETE", f"/memories/{m['id']}")
        self.assertGreaterEqual(self.j("DELETE", "/trash")["purged"], 1)
        self.assertEqual(self.j("GET", "/trash")["total"], 0)


class MigrationTests(unittest.TestCase):
    def test_existing_database_gains_columns(self) -> None:
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            Database(d)
            con = sqlite3.connect(Path(d) / "personal-os.db")
            for t, col in (("conversations", "deleted_at"), ("memories", "deleted_with"), ("projects", "deleted_at")):
                con.execute(f"ALTER TABLE {t} DROP COLUMN {col}")
            con.commit()
            con.close()
            db = Database(d)  # reopen: the old shape must upgrade in place
            Todos(db), Docs(db)
            with db.tx() as c:
                have = {r["name"] for r in c.execute("PRAGMA table_info(conversations)")}
            self.assertIn("deleted_at", have)


if __name__ == "__main__":
    unittest.main()
