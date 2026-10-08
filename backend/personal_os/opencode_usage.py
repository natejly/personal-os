"""OpenCode's own model calls, read from its local database (read-only) into usage_log as source 'opencode'.

OpenCode keeps every assistant message in ~/.local/share/opencode/opencode.db (session_message, type 'assistant',
data = JSON with model, cost and tokens). Grain launches OpenCode with that same default data dir and records none
of its calls itself (opencode.py, codingagents.py write no usage), so importing every message counts each call once,
whether Grain or a terminal started it. external_id is the message id and unique, so an import never doubles a row.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .db import Database, new_id
from .usage import Pricing

# A message is written when it starts and updated when it finishes; an import re-reads everything updated within this
# window of the newest row it has, so a long call that started before that row still lands once it completes.
OVERLAP_S = 86400  # ponytail: a call running longer than a day is missed; keep a time_updated watermark if that matters


def default_path() -> Path:
    """PERSONAL_OS_OPENCODE_DB points elsewhere (tests set it, so they never read the real one)."""
    if os.environ.get("PERSONAL_OS_OPENCODE_DB"):
        return Path(os.environ["PERSONAL_OS_OPENCODE_DB"])
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "opencode" / "opencode.db"


def import_usage(db: Database, pricing: Pricing, settings: dict[str, Any], path: Path | None = None) -> int:
    """Add the finished OpenCode messages not yet in usage_log. Returns rows added; a missing or unreadable OpenCode
    database is 0. OpenCode's own cost is kept as reported; a message without one is priced like Grain's calls."""
    path = path or default_path()
    if not path.is_file():
        return 0
    with db.tx() as c:
        newest = c.execute("SELECT MAX(created_at) FROM usage_log WHERE source = 'opencode'").fetchone()[0] or 0
    try:
        src = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            found = src.execute("SELECT id, time_created, data FROM session_message "
                                "WHERE type = 'assistant' AND time_updated >= ?", (int((newest - OVERLAP_S) * 1000),)).fetchall()
        finally:
            src.close()
    except sqlite3.Error:
        return 0  # an older layout or a locked file: nothing to import this time
    rows = []
    for mid, created_ms, raw in found:
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            continue
        t = d.get("tokens")
        if not isinstance(t, dict) or not ((d.get("time") or {}).get("completed") or d.get("error")):
            continue  # still running: its tokens are not final
        cache = t.get("cache") or {}
        # OpenCode's input excludes cached tokens and its output excludes reasoning; Grain's prompt and completion include them.
        cached, written, reasoning = int(cache.get("read") or 0), int(cache.get("write") or 0), int(t.get("reasoning") or 0)
        pt, ct = int(t.get("input") or 0) + cached + written, int(t.get("output") or 0) + reasoning
        if not pt and not ct:
            continue
        model = str((d.get("model") or {}).get("id") or d.get("modelID") or "")
        cost, how = d.get("cost"), "opencode"
        if not isinstance(cost, (int, float)):
            cost, how = pricing.cost(settings, model, pt, ct, cached, written), None
        done = (d.get("time") or {}).get("completed")
        ms = max(0, int(done - created_ms)) if isinstance(done, (int, float)) else 0
        rows.append((new_id(), created_ms / 1000, model, pt, ct, ms, cost, cached, written, reasoning, how, mid))
    if not rows:
        return 0
    with db.tx() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO usage_log(id,created_at,model,kind,prompt_tokens,completion_tokens,duration_ms,cost,cached_tokens,"
            "cache_write_tokens,reasoning_tokens,tag,source,cost_source,external_id) "
            "VALUES(?,?,?,'opencode',?,?,?,?,?,?,?,'opencode','opencode',?,?)", rows)
        return c.total_changes - before
