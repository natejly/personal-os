"""Notes an extension raises for the person (design doc 3.4): `api.inbox` leaves a card under "Needs you" in the Agent inbox
until it is dismissed, `api.notify` asks for an operating-system notification and leaves nothing behind but the event.

Both are rows here. The inbox reads the open cards from `GET /inbox`; `GET /inbox/notify` turns every note newer than the
renderer's last look into a notification event. A target is `inbox` or `run:<conversation id>`, as for a job's notification.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db import Database

TITLE_CHARS = 120
BODY_CHARS = 500
NOTIFY_KEEP_S = 86400.0  # a notification nobody asked about within a day is no longer news

SCHEMA = """
CREATE TABLE IF NOT EXISTS ext_notes (
  id TEXT PRIMARY KEY,
  ext_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  target TEXT,
  inbox INTEGER NOT NULL,
  created_at REAL NOT NULL,
  dismissed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ext_notes_open ON ext_notes(inbox, dismissed_at, created_at);
"""


@dataclass(frozen=True)
class InboxItem:
    title: str
    body: str = ""
    target: str | None = None


class ExtNotes:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, ext_id: str, item: InboxItem, *, inbox: bool) -> str:
        note_id, t = uuid.uuid4().hex, time.time()
        with self.db.tx() as c:
            c.execute("DELETE FROM ext_notes WHERE inbox = 0 AND created_at < ?", (t - NOTIFY_KEEP_S,))
            c.execute("INSERT INTO ext_notes (id, ext_id, title, body, target, inbox, created_at) VALUES (?,?,?,?,?,?,?)",
                      (note_id, ext_id, item.title[:TITLE_CHARS], item.body[:BODY_CHARS], item.target, int(inbox), t))
        return note_id

    def open(self) -> list[dict[str, Any]]:
        """The inbox cards nobody has dismissed, newest first."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, ext_id, title, body, target, created_at FROM ext_notes "
                             "WHERE inbox = 1 AND dismissed_at IS NULL ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def dismiss(self, note_id: str) -> bool:
        with self.db.tx() as c:
            return bool(c.execute("UPDATE ext_notes SET dismissed_at = ? WHERE id = ? AND inbox = 1 AND dismissed_at IS NULL",
                                  (time.time(), note_id)).rowcount)

    def notifications(self, since: float) -> list[dict[str, Any]]:
        """Notification events for the notes made after `since`, oldest first, in the shape /inbox/notify returns."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, title, body, target, created_at FROM ext_notes WHERE inbox = 0 AND created_at > ? "
                             "ORDER BY created_at", (since,)).fetchall()
        return [{"id": f"note:{r['id']}", "kind": "ext_note", "title": r["title"], "body": r["body"], "at": r["created_at"],
                 "target": r["target"] or "inbox"} for r in rows]
