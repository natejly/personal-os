"""Migration 12: sticky notes become docs, their windows become doc windows.

Run: python -m unittest backend/tests/test_migration_sticky_notes.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import docs, migrations  # noqa: E402

BODY = "## Buy milk\n\n- eggs\n- bread  \n\ttabbed line\n"


def _db(with_docs: bool = True) -> sqlite3.Connection:
    """A database stamped at version 11 with sticky notes, windows and presets in it."""
    c = sqlite3.connect(":memory:", isolation_level=None)  # autocommit, like Database: migrations.run owns the transaction
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA user_version = 11")
    c.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    c.execute("INSERT INTO projects VALUES ('p1')")
    c.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, project_id TEXT, body TEXT NOT NULL DEFAULT '',"
              " color TEXT NOT NULL DEFAULT 'yellow', created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    c.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?)", [
        ("n1", "p1", BODY, "blue", 11.5, 22.5),
        ("n2", None, "  \n\n- [ ] 1. Plan the trip\nsecond", "yellow", 33.0, 44.0),
        ("n3", None, "", "pink", 55.0, 66.0),
        ("n4", None, "x" * 200, "green", 77.0, 88.0),
        ("n5", "gone", "orphan", "yellow", 1.0, 2.0),  # its project vanished while foreign keys were off
    ])
    c.execute("CREATE TABLE canvas_windows (id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT, x REAL)")
    c.executemany("INSERT INTO canvas_windows VALUES (?,?,?,?)",
                  [("w1", "note", "n1", 10), ("w2", "todos", None, 20), ("w3", "chat", "n1", 30)])
    c.execute("CREATE TABLE canvas_presets (id TEXT PRIMARY KEY, windows TEXT NOT NULL DEFAULT '[]')")
    c.executemany("INSERT INTO canvas_presets VALUES (?,?)", [
        ("s1", json.dumps([{"kind": "note", "ref_id": "n2", "x": 1}, {"kind": "todos", "ref_id": None}])),
        ("s2", json.dumps([{"kind": "todos"}])),
        ("s3", "not json"),
    ])
    if with_docs:
        c.executescript(docs.SCHEMA)
        c.execute("ALTER TABLE docs ADD COLUMN deleted_at REAL")
    return c


class StickyNotesIntoDocs(unittest.TestCase):
    def test_notes_become_docs_with_the_same_ids(self) -> None:
        c = _db()
        self.assertEqual(migrations.run(c), [12])
        rows = {r["id"]: r for r in c.execute("SELECT * FROM docs")}
        self.assertEqual(set(rows), {"n1", "n2", "n3", "n4", "n5"})
        self.assertIsNone(rows["n5"]["project_id"])
        d = rows["n1"]
        self.assertEqual((d["title"], d["content"], d["project_id"], d["folder"]), ("Buy milk", BODY, "p1", ""))
        self.assertEqual((d["created_at"], d["updated_at"]), (11.5, 22.5))
        self.assertEqual(rows["n2"]["title"], "Plan the trip")  # heading, list and checkbox markers stripped
        self.assertEqual(rows["n2"]["project_id"], None)
        self.assertEqual(rows["n3"]["title"], "Sticky note")  # an empty note still gets a title
        self.assertEqual(rows["n3"]["content"], "")
        self.assertEqual(rows["n4"]["title"], "x" * 60)
        self.assertEqual(rows["n4"]["content"], "x" * 200)

    def test_search_row_is_written(self) -> None:
        c = _db()
        migrations.run(c)
        hit = c.execute("SELECT doc_id FROM docs_fts WHERE docs_fts MATCH 'bread'").fetchall()
        self.assertEqual([r["doc_id"] for r in hit], ["n1"])

    def test_windows_and_presets_are_rewritten(self) -> None:
        c = _db()
        migrations.run(c)
        wins = {r["id"]: (r["kind"], r["ref_id"]) for r in c.execute("SELECT * FROM canvas_windows")}
        self.assertEqual(wins, {"w1": ("doc", "n1"), "w2": ("todos", None), "w3": ("chat", "n1")})
        presets = {r["id"]: r["windows"] for r in c.execute("SELECT * FROM canvas_presets")}
        self.assertEqual(json.loads(presets["s1"]), [{"kind": "doc", "ref_id": "n2", "x": 1}, {"kind": "todos", "ref_id": None}])
        self.assertEqual(json.loads(presets["s2"]), [{"kind": "todos"}])
        self.assertEqual(presets["s3"], "not json")

    def test_notes_table_is_dropped_and_version_stamped(self) -> None:
        c = _db()
        migrations.run(c)
        self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='notes'").fetchone())
        self.assertEqual(migrations.current(c), 12)

    def test_rerunning_is_a_no_op(self) -> None:
        c = _db()
        migrations.run(c)
        before = [tuple(r) for r in c.execute("SELECT * FROM docs ORDER BY id")]
        self.assertEqual(migrations.run(c), [])
        migrations._sticky_notes_into_docs(c)  # the table is gone: nothing to do
        self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM docs ORDER BY id")], before)

    def test_an_existing_doc_with_the_same_id_is_left_alone(self) -> None:
        c = _db()
        c.execute("INSERT INTO docs(id,title,content,created_at,updated_at) VALUES('n1','Mine','keep',1,2)")
        migrations.run(c)
        row = c.execute("SELECT title, content FROM docs WHERE id='n1'").fetchone()
        self.assertEqual((row["title"], row["content"]), ("Mine", "keep"))

    def test_a_database_without_docs_gets_the_base_table(self) -> None:
        c = _db(with_docs=False)
        migrations.run(c)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(docs)")}
        self.assertTrue({"id", "project_id", "title", "content", "folder", "starred", "created_at", "updated_at"} <= cols)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 5)

    def test_no_notes_table_means_nothing_to_do(self) -> None:
        c = sqlite3.connect(":memory:", isolation_level=None)
        c.execute("PRAGMA user_version = 11")
        self.assertEqual(migrations.run(c), [12])
        self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='docs'").fetchone())


class TitleFromBody(unittest.TestCase):
    def test_rules(self) -> None:
        t = docs.title_from_body
        self.assertEqual(t("# Heading\nbody"), "Heading")
        self.assertEqual(t("\n  \n* item one"), "item one")
        self.assertEqual(t("3) third"), "third")
        self.assertEqual(t("> quoted"), "quoted")
        self.assertEqual(t("plain"), "plain")
        self.assertEqual(t("#"), "Sticky note")
        self.assertEqual(t(""), "Sticky note")


if __name__ == "__main__":
    unittest.main()
