"""Reading list: save links with a note and list them back, newest first."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from grain_sdk import Extension, ToolSpec

MAX_URL = 2000
MAX_NOTE = 500


def _load(name: str) -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "tools" / f"{name}.json").read_text("utf-8"))


def activate(api: Extension) -> None:
    async def add(ctx: dict[str, Any], url: str, note: str = "") -> dict[str, Any]:
        url = (url or "").strip()
        parsed = urlparse(url)
        if len(url) > MAX_URL or parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"error": f"url must be an http or https link of at most {MAX_URL} characters"}
        note = (note or "").strip()[:MAX_NOTE]
        with api.db.tx() as c:
            cur = c.execute("INSERT INTO reading_list_links (url, note) VALUES (?, ?)", (url, note))
            return {"saved": url, "note": note, "id": cur.lastrowid}

    async def list_links(ctx: dict[str, Any], limit: int = 20) -> dict[str, Any]:
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 20
        with api.db.tx() as c:
            rows = c.execute(
                "SELECT id, url, note, created_at FROM reading_list_links ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return {"links": [{"id": r[0], "url": r[1], "note": r[2], "saved_at": r[3]} for r in rows]}

    api.tool(ToolSpec("reading_list_add", "Save a link with a short note to the reading list.",
                      _load("reading_list_add"), add, "reading-list", danger="writes"))
    api.tool(ToolSpec("reading_list_list", "List saved links, newest first.",
                      _load("reading_list_list"), list_links, "reading-list", danger="safe"))
