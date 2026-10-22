"""The wake queue: every hidden turn the backend owes a chat once background work finishes (a worker's report, a scheduled
task's result, an extension's news, another chat's message) is a row here until the turn that carries it has ended.

app.py's drainer starts the turn when the chat is idle; `claimed_at` is set while one is being built or is live, and
cleared again at startup, so a turn that died with the last process is delivered again. `dedupe_key` (unique when given)
makes a second enqueue of the same event a no-op, even after the first was delivered.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS wake_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  dedupe_key TEXT,
  created_at REAL NOT NULL,
  claimed_at REAL,
  delivered_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wake_queue_dedupe ON wake_queue(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_wake_queue_open ON wake_queue(conversation_id) WHERE delivered_at IS NULL;
"""
KEEP_SECONDS = 7 * 86400  # delivered rows are kept this long so their dedupe keys still refuse a repeat


class WakeQueue:
    def __init__(self, db: Any) -> None:
        self.db = db

    def add(self, conversation_id: str, kind: str, payload: dict[str, Any], dedupe_key: str | None = None) -> bool:
        """Queue one wake. False when its dedupe_key is already there (queued or delivered)."""
        with self.db.tx() as c:
            return c.execute("INSERT OR IGNORE INTO wake_queue(conversation_id, kind, payload, dedupe_key, created_at) VALUES(?,?,?,?,?)",
                             (conversation_id, kind, json.dumps(payload), dedupe_key, time.time())).rowcount > 0

    def _rows(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(f"SELECT * FROM wake_queue WHERE delivered_at IS NULL AND {where} ORDER BY id", params).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def pending(self, conversation_id: str) -> list[dict[str, Any]]:
        """Undelivered rows of a chat nobody has claimed, oldest first."""
        return self._rows("conversation_id=? AND claimed_at IS NULL", (conversation_id,))

    def open(self, conversation_id: str | None, kind: str) -> list[dict[str, Any]]:
        """Undelivered rows of one kind (every chat's when no conversation is given), claimed or not."""
        if conversation_id:
            return self._rows("kind=? AND conversation_id=?", (kind, conversation_id))
        return self._rows("kind=?", (kind,))

    def convs(self) -> list[str]:
        with self.db.tx() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT conversation_id FROM wake_queue WHERE delivered_at IS NULL")]

    def _set(self, col: str, value: float | None, ids: Iterable[int]) -> None:
        ids = list(ids)
        if ids:
            with self.db.tx() as c:
                c.execute(f"UPDATE wake_queue SET {col}=? WHERE delivered_at IS NULL AND id IN ({','.join('?' * len(ids))})", (value, *ids))

    def claim(self, ids: Iterable[int]) -> None:
        self._set("claimed_at", time.time(), ids)

    def release(self, ids: Iterable[int]) -> None:
        self._set("claimed_at", None, ids)

    def done(self, ids: Iterable[int]) -> None:
        self._set("delivered_at", time.time(), ids)

    def done_key(self, dedupe_key: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE wake_queue SET delivered_at=? WHERE delivered_at IS NULL AND dedupe_key=?", (time.time(), dedupe_key))

    def startup(self) -> None:
        """Turns that were live when the last process died are owed again; long-delivered rows go."""
        with self.db.tx() as c:
            c.execute("UPDATE wake_queue SET claimed_at=NULL WHERE delivered_at IS NULL")
            c.execute("DELETE FROM wake_queue WHERE delivered_at < ?", (time.time() - KEEP_SECONDS,))
