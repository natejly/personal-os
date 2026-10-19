"""The Claude Code extension's rows: sessions (`claude_code_sessions`) and their capped event log (`claude_code_events`).

Plain functions over the app database, no state of their own. The JSON columns (`options`, `result`, `pending_approval`) are
decoded here so the rest of the code sees dicts.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

EVENT_CAP = 1000   # events kept per session; the oldest are dropped
TEXT_MAX = 2000    # characters of text kept per event
KINDS = ("init", "assistant", "tool", "tool_result", "result", "approval", "note", "error")
LIVE = ("starting", "running", "waiting_approval")   # a process is at work: these hold a concurrency slot
SETTLED = ("done", "failed", "stopped")
JSON_COLUMNS = ("options", "result", "pending_approval")
COLUMNS = ("id", "name", "repo_path", "worktree", "branch", "base_sha", "own_worktree", "status", "detail", "model", "permission_mode",
           "prompt", "options", "fleet_id", "conversation_id", "run_id", "hook_key", "claude_session_id", "cost_usd", "num_turns",
           "result_subtype", "result", "pending_approval", "last_text", "created_at", "started_at", "ended_at")
_lock = threading.Lock()   # seq is max+1 inside a transaction: two threads must not pick the same one


def _decode(row: Any) -> dict[str, Any]:
    out = dict(row)
    for col in JSON_COLUMNS:
        try:
            out[col] = json.loads(out[col]) if out.get(col) else None
        except ValueError:
            out[col] = None
    return out


def insert(db: Any, row: dict[str, Any]) -> None:
    values = [json.dumps(row.get(c)) if c in JSON_COLUMNS and row.get(c) is not None else row.get(c) for c in COLUMNS]
    with db.tx() as c:
        c.execute(f"INSERT INTO claude_code_sessions({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})", values)


def update(db: Any, sid: str, **fields: Any) -> None:
    if not fields:
        return
    bad = set(fields) - set(COLUMNS)
    assert not bad, bad
    values = [json.dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in fields.items()]
    with db.tx() as c:
        c.execute(f"UPDATE claude_code_sessions SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*values, sid))


def get(db: Any, sid: str) -> dict[str, Any] | None:
    with db.tx() as c:
        r = c.execute("SELECT * FROM claude_code_sessions WHERE id = ?1 OR claude_session_id = ?1 ORDER BY id = ?1 DESC LIMIT 1",
                      (str(sid or ""),)).fetchone()
    return _decode(r) if r else None


def listing(db: Any, limit: int = 100, where: str = "", args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with db.tx() as c:
        rows = c.execute(f"SELECT * FROM claude_code_sessions {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                         (*args, max(1, int(limit)))).fetchall()
    return [_decode(r) for r in rows]


def append_event(db: Any, session_id: str, kind: str, text: str, tool: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add one event and return it: {seq, session_id, kind, text, tool, data, at}. `at` is epoch seconds."""
    text, now = str(text or "")[:TEXT_MAX], time.time()
    with _lock, db.tx() as c:
        seq = c.execute("SELECT COALESCE(MAX(seq), 0) FROM claude_code_events WHERE session_id = ?", (session_id,)).fetchone()[0] + 1
        c.execute("INSERT INTO claude_code_events(session_id, seq, kind, text, tool, data, at) VALUES(?,?,?,?,?,?,?)",
                  (session_id, seq, kind, text, tool, json.dumps(data) if data is not None else None, now))
        if seq > EVENT_CAP:
            c.execute("DELETE FROM claude_code_events WHERE session_id = ? AND seq <= ?", (session_id, seq - EVENT_CAP))
    return {"seq": seq, "session_id": session_id, "kind": kind, "text": text, "tool": tool, "data": data, "at": now}


def events_after(db: Any, session_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), EVENT_CAP))
    with db.tx() as c:
        rows = c.execute("SELECT * FROM claude_code_events WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                         (session_id, int(after), limit)).fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["data"]) if r["data"] else None
        except ValueError:
            data = None
        out.append({"seq": r["seq"], "session_id": r["session_id"], "kind": r["kind"], "text": r["text"], "tool": r["tool"],
                    "data": data, "at": r["at"]})
    return out


def cost_rollup(rows: list[dict[str, Any]]) -> tuple[float, dict[str, dict[str, Any]]]:
    """(total cost, per-model breakdown) over sessions. Each session counts its LATEST result only: a result's cost and
    modelUsage are cumulative for the whole session, so summing the results of one session would count its early turns again."""
    total, by_model = 0.0, {}
    for r in rows:
        total += float(r.get("cost_usd") or 0)
        usage = (r.get("result") or {}).get("model_usage") or {}
        for model, u in usage.items() if isinstance(usage, dict) else ():
            if not isinstance(u, dict):
                continue
            m = by_model.setdefault(str(model), {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0})
            m["cost_usd"] += float(u.get("costUSD") or 0)
            m["input_tokens"] += int(u.get("inputTokens") or 0)
            m["output_tokens"] += int(u.get("outputTokens") or 0)
    return round(total, 6), {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_model.items()}


def events_tail(db: Any, session_id: str, n: int) -> list[dict[str, Any]]:
    """The last n events, oldest first."""
    with db.tx() as c:
        top = c.execute("SELECT COALESCE(MAX(seq), 0) FROM claude_code_events WHERE session_id = ?", (session_id,)).fetchone()[0]
    return events_after(db, session_id, max(0, top - max(1, int(n))), n)
