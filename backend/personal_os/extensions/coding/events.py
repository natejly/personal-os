"""The structured live feed of a coding session (table `coding_session_events`, migration 37).

Each row is one thing the agent did or said: the prompt and follow-ups (`user`), assistant text (`text`), a tool call
that started (`tool`: `tool` is the tool name, `text` a one-line summary such as the command or file path), a short
result (`tool_result`), a failure (`error`) and status transitions (`status`, "working → done"). `seq` counts up from 1
per session and is what a reader passes as `after`. A session keeps its newest CAP events.

The parsers here are pure: they turn a chunk of a driver's raw output into (kind, text, tool, data) tuples and keep no
state. CodingSessions owns the cursors, so each refresh feeds them only what is new.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

CAP = 2000          # events kept per session; the oldest are dropped
TEXT_MAX = 2000     # characters of text kept per event
KINDS = ("user", "text", "tool", "tool_result", "error", "status")

Ev = tuple[str, str, "str | None", "dict[str, Any] | None"]  # (kind, text, tool, data)
_lock = threading.Lock()  # seq is max+1 inside a transaction: two threads must not pick the same one


def _row(r: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(r["data"]) if r["data"] else None
    except ValueError:
        data = None
    return {"id": r["id"], "session_id": r["session_id"], "seq": r["seq"], "ts": r["ts"], "kind": r["kind"],
            "text": r["text"], "tool": r["tool"], "data": data}


def append(db: Any, session_id: str, kind: str, text: str, tool: str | None = None, data: dict[str, Any] | None = None,
           publish: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
    """Add one event and return it. `publish`, when given, gets ("coding_session_event", event) after it is saved."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    text = str(text or "")[:TEXT_MAX]
    with _lock, db.tx() as c:
        seq = (c.execute("SELECT COALESCE(MAX(seq), 0) FROM coding_session_events WHERE session_id = ?", (session_id,)).fetchone()[0]) + 1
        cur = c.execute("INSERT INTO coding_session_events(session_id, seq, ts, kind, text, tool, data) VALUES(?,?,?,?,?,?,?)",
                        (session_id, seq, ts, kind, text, tool, json.dumps(data) if data is not None else None))
        if seq > CAP:
            c.execute("DELETE FROM coding_session_events WHERE session_id = ? AND seq <= ?", (session_id, seq - CAP))
        ev = {"id": cur.lastrowid, "session_id": session_id, "seq": seq, "ts": ts, "kind": kind, "text": text,
              "tool": tool, "data": data}
    if publish:
        publish("coding_session_event", ev)
    return ev


def list_after(db: Any, session_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """Events with seq > `after`, oldest first, at most `limit` (1..CAP)."""
    limit = max(1, min(int(limit), CAP))
    with db.tx() as c:
        rows = c.execute("SELECT * FROM coding_session_events WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                         (session_id, int(after), limit)).fetchall()
    return [_row(r) for r in rows]


def recent(db: Any, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """The last `limit` events, oldest first."""
    limit = max(1, min(int(limit), CAP))
    with db.tx() as c:
        rows = c.execute("SELECT * FROM coding_session_events WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                         (session_id, limit)).fetchall()
    return [_row(r) for r in reversed(rows)]


def last_from(db: Any, session_id: str, src: str) -> dict[str, Any] | None:
    """The newest event that came from the timeline of claude job `src` (its data carries {"src", "line"}), or None.
    This is how a restarted backend knows how far it had read."""
    with db.tx() as c:
        r = c.execute("SELECT * FROM coding_session_events WHERE session_id = ? AND data LIKE ? ORDER BY seq DESC LIMIT 1",
                      (session_id, '%"src": ' + json.dumps(src) + '%')).fetchone()
    return _row(r) if r else None


# ---- parsers ----
def _clip(v: Any, n: int = 200) -> str:
    return " ".join(str(v or "").split())[:n]


def parse_opencode(chunk: str) -> tuple[list[Ev], str | None]:
    """(events, session id) from complete lines of OpenCode's `--format json` output. Mirrors opencode.summarize."""
    out: list[Ev] = []
    session: str | None = None
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            out.append(("text", line, None, {"raw": True}))
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            out.append(("text", line, None, {"raw": True}))
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else ev
        session = session or ev.get("sessionID") or part.get("sessionID")
        kind = part.get("type") or ev.get("type")
        if kind == "text" and part.get("text"):
            out.append(("text", str(part["text"]).rstrip(), None, None))
        elif kind == "tool":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            title = state.get("title") or inp.get("command") or inp.get("filePath") or ""
            out.append(("tool", _clip(title), str(part.get("tool") or "tool"), None))
        elif kind == "error" or ev.get("error"):
            err = ev.get("error") or part.get("error") or {}
            msg = (err.get("data") or {}).get("message") if isinstance(err, dict) else str(err)
            out.append(("error", _clip(msg or err, TEXT_MAX), None, None))
    return out, session if isinstance(session, str) else None


def parse_pi(chunk: str) -> tuple[list[Ev], str | None]:
    """(events, session id) from complete lines of pi's `--mode json` output. Mirrors pi.summarize."""
    out: list[Ev] = []
    session: str | None = None
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            out.append(("text", line, None, {"raw": True}))
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            out.append(("text", line, None, {"raw": True}))
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "session" and isinstance(ev.get("id"), str):
            session = session or ev["id"]
        elif kind == "tool_execution_start":
            args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
            out.append(("tool", _clip(args.get("command") or args.get("pattern") or args.get("path")),
                        str(ev.get("toolName") or "tool"), None))
        elif kind == "tool_execution_end" and ev.get("isError"):
            res = ev.get("result")
            text = ""
            if isinstance(res, dict):
                parts = res.get("content") if isinstance(res.get("content"), list) else []
                text = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)) or str(res.get("error") or "")
            out.append(("tool_result", _clip(text or "failed", TEXT_MAX), str(ev.get("toolName") or "tool"), {"is_error": True}))
        elif kind == "message_end" and isinstance(ev.get("message"), dict) and ev["message"].get("role") == "assistant":
            msg: dict[str, Any] = ev["message"]
            for part in msg.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip():
                    out.append(("text", str(part["text"]).rstrip(), None, None))
            if msg.get("stopReason") in ("error", "aborted"):
                out.append(("error", _clip(msg.get("errorMessage") or f"request {msg['stopReason']}", 300), None, None))
    return out, session


def parse_claude(lines: list[str], first: int, src: str, prev: tuple[str, str] | None = None) -> tuple[list[Ev], tuple[str, str] | None]:
    """(events, last (kind, text) emitted) from timeline.jsonl lines. `first` is the 1-based number of lines[0] in the
    file and `src` the job id; both go in each event's data so a restart can find its place (last_from). An entry with
    `text` is assistant text; one with only `detail` is a progress step (tool "step"). Anything else is skipped. The CLI
    repeats the same detail many times in a row: a repeat of the previous event is dropped."""
    out: list[Ev] = []
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        text, detail = ev.get("text"), ev.get("detail")
        if isinstance(text, str) and text.strip():
            cur = ("text", text.strip())
        elif isinstance(detail, str) and detail.strip():
            cur = ("tool", detail.strip())
        else:
            continue
        if cur == prev:
            continue
        prev = cur
        out.append((cur[0], cur[1], "step" if cur[0] == "tool" else None, {"src": src, "line": first + i}))
    return out, prev
