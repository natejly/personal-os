"""Kanban boards: boards → columns → cards."""
from __future__ import annotations

import json
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
-- Append-only: rows are only ever inserted, in the same transaction as the change they describe.
CREATE TABLE IF NOT EXISTS card_events (
  id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL UNIQUE,
  card_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_events_card ON card_events(card_id, seq);
"""

# What an agent holding a claim may log. `completed` is deliberately absent: only Boards.complete writes it.
AGENT_EVENT_KINDS = {"comment", "artifact", "failed"}
COMPLETE_CHECKS = {"user_accepted", "path_exists", "tests_passed"}
RELEASE_REASONS = {"finished", "expired", "preempted", "cancelled"}

DEFAULT_COLUMNS = ["Backlog", "To do", "In progress", "Done"]


class Boards:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(cards)")}
            for col, ddl in (("claimed_by", "TEXT"), ("claim_token", "TEXT"), ("lease_expires_at", "REAL")):
                if col not in have:
                    c.execute(f"ALTER TABLE cards ADD COLUMN {col} {ddl}")

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
            cards = c.execute("SELECT *, EXISTS(SELECT 1 FROM card_events e WHERE e.card_id=cards.id AND e.kind='completed') AS completed "
                              "FROM cards WHERE board_id=? ORDER BY position, created_at", (id,)).fetchall()
        return {**row_to_dict(b), "columns": [row_to_dict(x) for x in cols],  # type: ignore[arg-type]
                "cards": [{**row_to_dict(x, ("labels",)), "completed": bool(x["completed"])} for x in cards]}  # type: ignore[arg-type]

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

    def delete_column(self, id: str) -> int:
        """Delete a column and (by cascade) its cards; returns how many cards went with it."""
        with self.db.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM cards WHERE column_id=?", (id,)).fetchone()[0]
            c.execute("DELETE FROM board_columns WHERE id=?", (id,))
        return n

    @staticmethod
    def _check_column(c: Any, board_id: str, column_id: str) -> None:
        """A card may only sit in a column of its own board, or it vanishes from the board view."""
        col = c.execute("SELECT board_id FROM board_columns WHERE id=?", (column_id,)).fetchone()
        if not col:
            raise KeyError(f"Unknown column: {column_id}")
        if col["board_id"] != board_id:
            raise ValueError("Column belongs to a different board")

    @staticmethod
    def _flag_limit(c: Any, card: dict[str, Any]) -> dict[str, Any]:
        """Soft WIP check: never blocks (agents move cards too), only tells the caller the column is over its limit."""
        lim = c.execute("SELECT wip_limit FROM board_columns WHERE id=?", (card["column_id"],)).fetchone()["wip_limit"]
        n = c.execute("SELECT COUNT(*) FROM cards WHERE column_id=?", (card["column_id"],)).fetchone()[0]
        card["over_limit"] = bool(lim) and n > lim
        return card

    @staticmethod
    def _log(c: Any, card_id: str, actor: str, kind: str, payload: dict[str, Any] | None = None, at: float | None = None) -> None:
        # seq is computed inside the writing transaction; SQLite has one writer, and UNIQUE(seq) backs that up.
        c.execute("INSERT INTO card_events(id,seq,card_id,actor,kind,payload,created_at) "
                  "VALUES(?,(SELECT COALESCE(MAX(seq),0)+1 FROM card_events),?,?,?,?,?)",
                  (new_id(), card_id, actor, kind, json.dumps(payload or {}, default=str), now() if at is None else at))

    def events(self, card_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM card_events WHERE card_id=? ORDER BY seq", (card_id,)).fetchall()
        return [row_to_dict(r, ("payload",)) for r in rows]  # type: ignore[misc]

    # claims: one conditional UPDATE decides who holds a card; the lease is cleared by moving the card
    def claim(self, card_id: str, holder: str, ttl_s: float = 600, at: float | None = None) -> str | None:
        t = now() if at is None else at
        token = new_id()
        with self.db.tx() as c:
            prev = c.execute("SELECT claimed_by, claim_token FROM cards WHERE id=?", (card_id,)).fetchone()
            n = c.execute("UPDATE cards SET claimed_by=?, claim_token=?, lease_expires_at=? WHERE id=? "
                          "AND (claim_token IS NULL OR lease_expires_at<=?)", (holder, token, t + ttl_s, card_id, t)).rowcount
            if not n:
                return None
            if prev and prev["claim_token"]:
                self._log(c, card_id, "system", "released", {"reason": "expired", "holder": prev["claimed_by"]}, t)
            self._log(c, card_id, holder, "claimed", {"ttl_s": ttl_s}, t)
        return token

    def renew(self, card_id: str, token: str, ttl_s: float = 600, at: float | None = None) -> bool:
        t = now() if at is None else at
        with self.db.tx() as c:
            return bool(c.execute("UPDATE cards SET lease_expires_at=? WHERE id=? AND claim_token=? AND lease_expires_at>?",
                                  (t + ttl_s, card_id, token, t)).rowcount)

    def release(self, card_id: str, token: str, reason: str = "finished", at: float | None = None) -> bool:
        if reason not in RELEASE_REASONS:
            raise ValueError(f"reason must be one of {sorted(RELEASE_REASONS)}")
        with self.db.tx() as c:
            r = c.execute("SELECT claimed_by FROM cards WHERE id=? AND claim_token=?", (card_id, token)).fetchone()
            if not r or not c.execute("UPDATE cards SET claim_token=NULL, claimed_by=NULL, lease_expires_at=NULL "
                                      "WHERE id=? AND claim_token=?", (card_id, token)).rowcount:
                return False
            self._log(c, card_id, r["claimed_by"], "released", {"reason": reason}, at)
        return True

    def sweep(self, at: float | None = None) -> int:
        """Expire stale leases. Logs released(expired); never completed."""
        t = now() if at is None else at
        with self.db.tx() as c:
            rows = c.execute("SELECT id, claimed_by FROM cards WHERE claim_token IS NOT NULL AND lease_expires_at<=?", (t,)).fetchall()
            for r in rows:
                c.execute("UPDATE cards SET claim_token=NULL, claimed_by=NULL, lease_expires_at=NULL WHERE id=?", (r["id"],))
                self._log(c, r["id"], "system", "released", {"reason": "expired", "holder": r["claimed_by"]}, t)
        return len(rows)

    def event(self, card_id: str, token: str, kind: str, payload: dict[str, Any] | None = None, at: float | None = None) -> bool:
        """An agent's comment/artifact/failed note: written only while its lease is live. `completed` is refused."""
        if kind not in AGENT_EVENT_KINDS:
            raise ValueError(f"an agent may log only {sorted(AGENT_EVENT_KINDS)}; completion goes through Boards.complete")
        t = now() if at is None else at
        with self.db.tx() as c:
            r = c.execute("SELECT claimed_by FROM cards WHERE id=? AND claim_token=? AND lease_expires_at>?", (card_id, token, t)).fetchone()
            if not r:
                return False
            self._log(c, card_id, r["claimed_by"], kind, payload, t)
        return True

    def agent_move(self, card_id: str, token: str, column_id: str, at: float | None = None) -> bool:
        """Move a card as its lease holder. Zero rows changed (False) when the token is wrong or the lease ran out."""
        t = now() if at is None else at
        with self.db.tx() as c:
            card = c.execute("SELECT board_id, column_id, claimed_by FROM cards WHERE id=?", (card_id,)).fetchone()
            if not card:
                raise KeyError(f"Unknown card: {card_id}")
            self._check_column(c, card["board_id"], column_id)
            pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM cards WHERE column_id=?", (column_id,)).fetchone()[0]
            if not c.execute("UPDATE cards SET column_id=?, position=?, updated_at=? WHERE id=? AND claim_token=? AND lease_expires_at>?",
                             (column_id, pos, t, card_id, token, t)).rowcount:
                return False
            self._log(c, card_id, card["claimed_by"], "moved", {"from": card["column_id"], "to": column_id}, t)
        return True

    def complete(self, card_id: str, check: str, pointer: str, by: str = "checker", at: float | None = None) -> bool:
        """The only writer of `completed`: a check passed, and `pointer` says what was checked (a path, a test run, a user action)."""
        if check not in COMPLETE_CHECKS:
            raise ValueError(f"check must be one of {sorted(COMPLETE_CHECKS)}")
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone():
                return False
            self._log(c, card_id, by, "completed", {"check": check, "pointer": pointer}, at)
        return True

    def _user_moved(self, c: Any, card_id: str, old: str, new: str) -> None:
        """A user's column change commits no matter who holds the card: it logs moved and, if a lease was live, preempts it."""
        self._log(c, card_id, "user", "moved", {"from": old, "to": new})
        r = c.execute("SELECT claimed_by, claim_token FROM cards WHERE id=?", (card_id,)).fetchone()
        if r and r["claim_token"]:
            c.execute("UPDATE cards SET claim_token=NULL, claimed_by=NULL, lease_expires_at=NULL WHERE id=?", (card_id,))
            self._log(c, card_id, "user", "released", {"reason": "preempted", "holder": r["claimed_by"]})

    # cards
    def add_card(self, board_id: str, column_id: str | None, title: str, description: str = "", due: str | None = None, priority: int = 2, labels: list[str] | None = None) -> dict[str, Any]:
        import json

        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM boards WHERE id=?", (board_id,)).fetchone():
                raise KeyError(f"Unknown board: {board_id}")
            if column_id:
                self._check_column(c, board_id, column_id)
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
            self._log(c, cid, "user", "created", {"title": title.strip()}, t)
            return self._flag_limit(c, row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone(), ("labels",)))

    def update_card(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        import json

        fields = {k: v for k, v in patch.items() if k in {"title", "description", "column_id", "position", "due", "priority", "labels"}}
        if "labels" in fields:
            fields["labels"] = json.dumps(fields["labels"])
        if not fields:
            return None
        fields["updated_at"] = now()
        with self.db.tx() as c:
            card = c.execute("SELECT board_id, column_id FROM cards WHERE id=?", (id,)).fetchone()
            if not card:
                raise KeyError(f"Unknown card: {id}")
            if fields.get("column_id"):
                self._check_column(c, card["board_id"], fields["column_id"])
                if fields["column_id"] != card["column_id"]:
                    self._user_moved(c, id, card["column_id"], fields["column_id"])
            c.execute(f"UPDATE cards SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
            return self._flag_limit(c, row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone(), ("labels",)))

    def move_card(self, id: str, column_id: str, before_card_id: str | None = None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            card = c.execute("SELECT board_id, column_id FROM cards WHERE id=?", (id,)).fetchone()
            if not card:
                raise KeyError(f"Unknown card: {id}")
            self._check_column(c, card["board_id"], column_id)
            self._user_moved(c, id, card["column_id"], column_id)
            if before_card_id:
                b = c.execute("SELECT position FROM cards WHERE id=?", (before_card_id,)).fetchone()
                prev = c.execute("SELECT MAX(position) FROM cards WHERE column_id=? AND position < ?", (column_id, b["position"])).fetchone()[0] if b else None
                pos = ((prev if prev is not None else b["position"] - 1) + b["position"]) / 2 if b else 0
            else:
                pos = c.execute("SELECT COALESCE(MAX(position),0)+1 FROM cards WHERE column_id=?", (column_id,)).fetchone()[0]
            c.execute("UPDATE cards SET column_id=?, position=?, updated_at=? WHERE id=?", (column_id, pos, now(), id))
            return self._flag_limit(c, row_to_dict(c.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone(), ("labels",)))

    def delete_card(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM cards WHERE id=?", (id,))

    def find_board(self, name_or_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM boards WHERE id=? OR lower(name)=lower(?)", (name_or_id, name_or_id.strip())).fetchone()
        return self.get(r["id"]) if r else None
