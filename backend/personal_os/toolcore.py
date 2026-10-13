"""Which built-in tools are always offered (the core), ranked by what was really called, refreshed once a day.

The core is the static CORE_ORDER (chat) and WORKER_CORE (workers) until 14 days of run_events hold enough calls to rank.
Then: a pinned floor the standing prompts rely on, plus the most-used other tools up to the same size and the same schema
size as the static list. A tool in the core stays while it ranks within SLACK places of the cut; a riser takes the
place of the lowest incumbent only when it ranks SLACK or more places above it. Pinned tools first in fixed order, the rest sorted by name, so a count change never
reorders bytes. Nothing here runs per turn: a request or a child reads CURRENT once when it starts, so a refresh
changes the tools array of the next request only (one provider prefix-cache miss a day).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from .db import Database

log = logging.getLogger("personal_os")

KEY = "toolCore"      # settings row: {"chat": [...], "worker": [...]}, so a restart keeps hysteresis
WINDOW_DAYS = 14
MIN_CALLS = 50        # fewer tool calls than this in the window: keep the static lists
SLACK = 4
DAY = 86400.0
KINDS = {"chat": ("chat", "desk"), "worker": ("worker", "subagent")}
# Named by a standing prompt or hint, so they stay whatever the counts say.
PINNED = {
    "chat": ("tool_search", "todo_write", "ask_user", "delegate", "message_worker", "check_worker", "resume_worker",
             "stop_worker", "read_tool_result", "save_memory", "search_memory", "show", "calendar_events", "gmail_search"),
    "worker": ("read_tool_result", "shell_poll"),
}
# Schema characters the whole chat core may take (tests/test_tool_search.py caps the first request under 14.5k); a role never gets
# less than its static list weighs.
CHARS = {"chat": 14_000}
CURRENT: dict[str, tuple[str, ...]] = {}  # role -> computed core; absent = the static list


def ranking(db: Database, role: str, now: float | None = None) -> tuple[list[str], int]:
    """(tool names, most distinct runs first; ties by calls then name), total calls. A run counts once per tool, so one
    runaway run cannot lift a tool."""
    since = (now or time.time()) - WINDOW_DAYS * DAY
    marks = ",".join("?" * len(KINDS[role]))
    with db.tx() as c:
        rows = c.execute(
            "SELECT json_extract(e.data,'$.name') AS n, COUNT(*) AS calls, COUNT(DISTINCT e.run_id) AS runs "
            f"FROM run_events e JOIN agent_runs r USING(run_id) WHERE e.type='tool_call' AND e.ts>? AND r.kind IN ({marks}) "
            "GROUP BY n", (since, *KINDS[role])).fetchall()
    rows = [r for r in rows if r["n"]]
    return [r["n"] for r in sorted(rows, key=lambda r: (-r["runs"], -r["calls"], r["n"]))], sum(r["calls"] for r in rows)


def pick(role: str, ranked: list[str], prev: tuple[str, ...], static: tuple[str, ...], size: Callable[[str], int | None],
         fixed: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The core for one role. size(name) is the schema's character count, None when the tool does not exist. `fixed` are
    always-core names the role does not rank (the desk tools in chat)."""
    pinned = PINNED[role]
    slots = len(static) - len(pinned)
    budget = max(CHARS.get(role, 0), sum(size(n) or 0 for n in static)) - sum(size(n) or 0 for n in pinned)
    ranked = [n for n in ranked if size(n) is not None and n not in pinned and n not in fixed]
    top = ranked[:slots]
    keep = [n for n in prev if n not in pinned and n in ranked[:slots + SLACK]]
    keep.sort(key=ranked.index)
    new = []
    for n in (n for n in top if n not in keep):
        if len(keep) + len(new) < slots:
            new.append(n)
        elif keep and ranked.index(keep[-1]) - ranked.index(n) >= SLACK:  # a clear riser displaces the lowest incumbent
            keep.pop()
            new.append(n)
    out: list[str] = []
    for n in keep + new + [n for n in ranked if n not in keep and n not in new]:
        if len(out) < slots and n not in out and size(n) <= budget:
            out.append(n)
            budget -= size(n)
    return pinned + tuple(sorted(out))


def refresh(db: Database, sizes: Callable[[str], int | None], statics: dict[str, tuple[str, ...]],
            fixed: tuple[str, ...] = ()) -> dict[str, tuple[str, ...]]:
    """Recompute both cores from the DB and persist them. A role with too little data keeps its static list."""
    stored = db.get_settings().get(KEY) or {}
    out: dict[str, tuple[str, ...]] = {}
    for role, static in statics.items():
        ranked, calls = ranking(db, role)
        if calls < MIN_CALLS:
            continue
        prev = tuple(stored.get(role) or static)
        out[role] = pick(role, ranked, prev, static, sizes, fixed if role == "chat" else ())
    CURRENT.clear()
    CURRENT.update(out)
    db.set_settings({KEY: {r: list(v) for r, v in out.items()}})
    return out


def load(db: Database) -> None:
    CURRENT.clear()
    CURRENT.update({r: tuple(v) for r, v in (db.get_settings().get(KEY) or {}).items() if r in PINNED and v})


async def loop(db: Database, sizes: Callable[[str], int | None], statics: dict[str, tuple[str, ...]], fixed: tuple[str, ...]) -> None:
    """Load what the last run saved, then recompute now and every 24 hours. A failed pass is logged and retried tomorrow."""
    await asyncio.to_thread(load, db)
    while True:
        try:
            await asyncio.to_thread(refresh, db, sizes, statics, fixed)
        except Exception:  # noqa: BLE001 - housekeeping must never take the backend down
            log.warning("tool core refresh failed", exc_info=True)
        await asyncio.sleep(DAY)


def schema_chars(specs: dict[str, Any]) -> Callable[[str], int | None]:
    return lambda n: len(json.dumps(specs[n].schema())) if n in specs else None
