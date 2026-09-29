"""Kanban boards: boards → columns → cards."""
from __future__ import annotations

from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS board_columns (
  id TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  wip_limit INTEGER
);
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY,
  board_id TEXT NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  column_id TEXT NOT NULL REFERENCES board_columns(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  position REAL NOT NULL DEFAULT 0,
  due TEXT,
  priority INTEGER NOT NULL DEFAULT 2,
  labels TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_col ON cards(column_id, position);
"""

DEFAULT_COLUMNS = ["Backlog", "To do", "In progress", "Done"]


class Boards:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT b.*, (SELECT COUNT(*) FROM cards WHERE board_id=b.id) AS card_count FROM boards b ORDER BY created_at"
            ).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            b = c.execute("SELECT * FROM boards WHERE id=?", (id,)).fetchone()
            if not b:
                return None
            cols = c.execute("SELECT * FROM board_columns WHERE board_id=? ORDER BY position", (id,)).fetchall()
            cards = c.execute("SELECT * FROM cards WHERE board_id=? ORDER BY position, created_at", (id,)).fetchall()
        return {**row_to_dict(b), "columns": [row_to_dict(x) for x in cols], "cards": [row_to_dict(x, ("labels",)) for x in cards]}  # type: ignore[arg-type]

    def create(self, name: str, project_id: str | None = None, columns: list[str] | None = None) -> dict[str, Any]:
        bid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO boards(id,project_id,name,created_at) VALUES(?,?,?,?)", (bid, project_id, name.strip() or "Board", now()))
            for i, col in enumerate(columns or DEFAULT_COLUMNS):
                c.execute("INSERT INTO board_columns(id,board_id,name,position) VALUES(?,?,?,?)", (new_id(), bid, col, i))
        return self.get(bid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if patch.get("name"):
                c.execute("UPDATE boards SET name=? WHERE id=?", (patch["name"].strip(), id))
            if "project_id" in patch:
                c.execute("UPDATE boards SET project_id=? WHERE id=?", (patch["project_id"], id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM boards WHERE id=?", (id,))

    # columns
    def add_column(self, board_id: str, name: str) -> dict[str, Any]:
        with self.db.tx() as c:
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM board_columns WHERE board_id=?", (board_id,)).fetchone()[0]
            cid = new_id()
            c.execute("INSERT INTO board_columns(id,board_id,name,position) VALUES(?,?,?,?)", (cid, board_id, name.strip() or "Column", pos))
            return row_to_dict(c.execute("SELECT * FROM board_columns WHERE id=?", (cid,)).fetchone())  # type: ignore[return-value]

    def update_column(self, id: str, patch: dict[str, Any]) -> None:
        with self.db.tx() as c:
            if patch.get("name"):
                c.execute("UPDATE board_columns SET name=? WHERE id=?", (patch["name"].strip(), id))
            if "position" in patch:
                c.execute("UPDATE board_columns SET position=? WHERE id=?", (int(patch["position"]), id))
            if "wip_limit" in patch:
                c.execute("UPDATE board_columns SET wip_limit=? WHERE id=?", (patch["wip_limit"], id))

    def delete_column(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM board_columns WHERE id=?", (id,))

    # cards
    def add_card(self, board_id: str, column_id: str | None, title: str, description: str = "", due: str | None = None, priority: int = 2, labels: list[str] | None = None) -> dict[str, Any]:
        import json

        with self.db.tx() as c:
            if not column_id:
                col = c.execute("SELECT id FROM board_columns WHERE board_id=? ORDER BY position LIMIT 1", (board_id,)).fetchone()
                if not col:
                    raise ValueError("Board has no columns")
                column_id = col["id"]
            pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM cards WHERE column_id=?", (column_id,)).fetchone()[0]
            cid = new_id()
            t = now()
            c.execute(
                "INSERT INTO cards(id,board_id,column_id,title,description,position,due,priority,labels,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (cid, board_id, column_id, title.strip(), description, pos, due, int(priority), json.dumps(labels or []), t, t),
            )
            return row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone(), ("labels",))  # type: ignore[return-value]

    def update_card(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        import json

        fields = {k: v for k, v in patch.items() if k in {"title", "description", "column_id", "position", "due", "priority", "labels"}}
        if "labels" in fields:
            fields["labels"] = json.dumps(fields["labels"])
        if not fields:
            return None
        fields["updated_at"] = now()
        with self.db.tx() as c:
            c.execute(f"UPDATE cards SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
            return row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone(), ("labels",))

    def move_card(self, id: str, column_id: str, before_card_id: str | None = None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            if before_card_id:
                b = c.execute("SELECT position FROM cards WHERE id=?", (before_card_id,)).fetchone()
                prev = c.execute("SELECT MAX(position) FROM cards WHERE column_id=? AND position < ?", (column_id, b["position"])).fetchone()[0] if b else None
                pos = ((prev if prev is not None else b["position"] - 1) + b["position"]) / 2 if b else 0
            else:
                pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM cards WHERE column_id=?", (column_id,)).fetchone()[0]
            c.execute("UPDATE cards SET column_id=?, position=?, updated_at=? WHERE id=?", (column_id, pos, now(), id))
            return row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone(), ("labels",))

    def delete_card(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM cards WHERE id=?", (id,))

    def find_board(self, name_or_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM boards WHERE id=? OR lower(name)=lower(?)", (name_or_id, name_or_id.strip())).fetchone()
        return self.get(r["id"]) if r else None
