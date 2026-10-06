"""Boards merged into Todos: the migration keeps every card, and a todo's status is its board column."""
from __future__ import annotations

import tempfile

from personal_os import migrations
from personal_os.db import Database
from personal_os.todos import Todos

OLD_BOARDS = """
CREATE TABLE boards (id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE board_columns (id TEXT PRIMARY KEY, board_id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, wip_limit INTEGER);
CREATE TABLE cards (id TEXT PRIMARY KEY, board_id TEXT NOT NULL, column_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  position REAL NOT NULL DEFAULT 0, due TEXT, priority INTEGER NOT NULL DEFAULT 2, labels TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE card_events (id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, card_id TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
"""


def test_boards_migrate_into_todos_without_loss() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        with db.connect() as c:
            c.executescript(OLD_BOARDS)
            c.execute("INSERT INTO boards VALUES('b1', NULL, 'Home', 1)")
            for i, n in enumerate(["Backlog", "Doing", "Done"]):
                c.execute("INSERT INTO board_columns VALUES(?, 'b1', ?, ?, NULL)", (f"c{i}", n, i))
            c.execute("INSERT INTO cards VALUES('k1','b1','c1','Tile','grout',3.5,'2026-11-01',1,'[\"Kitchen\"]',10,20)")
            c.execute("INSERT INTO cards VALUES('k2','b1','c2','Paint','',1,NULL,2,'[]',11,30)")
            c.execute("PRAGMA user_version = 2")
        with db.connect() as c:
            assert migrations.run(c) == [3, 4]
        todos = Todos(db)
        rows = {t["title"]: t for t in todos.list(include_done=True)}
        tile, paint = rows["Tile"], rows["Paint"]
        assert (tile["list_name"], tile["status"], tile["notes"], tile["due"], tile["priority"], tile["position"], tile["tags"], tile["done"]) == \
            ("Home", "Doing", "grout", "2026-11-01", 1, 3.5, ["kitchen"], 0)
        assert paint["done"] == 1 and paint["status"] == "Done" and paint["completed_at"] == 30
        assert tile["source"] == paint["source"] == "board"  # Tasks sync never pushes these
        with db.connect() as c:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "legacy_cards" in names and "cards" not in names
            assert migrations.run(c) == []  # once only
        assert todos.lists() == ["Home"]


def test_status_moves_complete_and_reopen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        todos = Todos(Database(tmp))
        a = todos.create("a", list_name="Work")
        b = todos.create("b", list_name="Work")
        assert a["status"] == "To do" and not a["done"]
        t = todos.move(a["id"], "Done")
        assert t["done"] == 1 and t["status"] == "Done" and t["completed_at"]
        t = todos.move(a["id"], "In progress", b["id"])
        assert t["done"] == 0 and t["status"] == "In progress" and t["position"] < b["position"]
        assert todos.update(a["id"], {"done": True})["status"] == "Done"  # ticking it off drops the custom column
        assert [x["title"] for x in todos.list(list_name="Work")] == ["b"]


def test_lists_outlive_their_items_and_rename_moves_them() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        todos = Todos(Database(tmp))
        assert todos.create_list("Groceries") == ["Groceries"]  # an empty list survives on its own
        a = todos.create("milk", list_name="Groceries")
        b = todos.create("read", list_name="Books")  # a todo written with a name registers the list
        assert todos.lists() == ["Books", "Groceries"]
        todos.delete(b["id"])
        assert todos.lists() == ["Books", "Groceries"]
        assert todos.rename_list("Groceries", "Shopping") == ["Books", "Shopping"]
        assert todos.get(a["id"])["list_name"] == "Shopping"
        assert todos.delete_list("Shopping") == ["Books"]
        assert todos.get(a["id"])["list_name"] is None  # kept, back on the default list
