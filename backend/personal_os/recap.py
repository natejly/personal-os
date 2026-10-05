"""The daily recap on the home screen: one cached row per day, regenerated on demand."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import llm, redact
from .db import Database, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS recaps (
  day TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

RECAP_SYSTEM = """You write the user's daily recap for the home screen of their personal AI OS. Warm but efficient. Use markdown with short sections:
**Since yesterday** (what happened: chats, things learned, completed todos), **Today** (calendar, due todos, unread mail worth attention), **Suggested focus** (3 bullets max). Under 180 words. Skip empty sections. Never invent facts.
A source given as "not connected" or "unavailable" is unknown, not empty: never call that calendar or inbox clear; say in one short line that it is not connected (or could not be read)."""


class Recaps:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def get(self, day: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM recaps WHERE day=?", (day,)).fetchone())

    def save(self, day: str, content: str) -> dict[str, Any]:
        with self.db.tx() as c:
            c.execute("INSERT INTO recaps(day,content,created_at) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET content=excluded.content, created_at=excluded.created_at", (day, content, now()))
        return {"day": day, "content": content, "created_at": now()}


def _clip_public(blob: str, limit: int, suffix: str = "…") -> str:
    """Credentials out, then the length cap. Scrubbing after the cut would leave a sliced token."""
    blob = redact.scrub_command_output(blob)
    return blob if len(blob) <= limit else blob[:limit] + suffix


async def generate_recap(settings: dict[str, Any], model: str, facts: dict[str, Any]) -> str:
    blob = _clip_public(json.dumps(facts, ensure_ascii=False, default=str), 20000)
    today = dt.date.today().strftime("%A, %B %d")
    return await llm.complete(settings, model, [{"role": "system", "content": RECAP_SYSTEM}, {"role": "user", "content": f"Today is {today}.\n\nFacts:\n{blob}"}])
