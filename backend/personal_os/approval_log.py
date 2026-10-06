"""The approval history: one row per decision about a tool call, whoever or whatever made it.

Rows come from three places. RunStore.decide logs every answer to a card (the UI, the inbox, a desk, a stop,
a steer, the unattended policy), so nothing that settles an approvals row can skip the log. The chat loop logs
the calls that ran without a card because something already stood behind them: an allow rule or a session
grant (decision 'always'), an approved plan step ('plan'), or the review gate's allow verdict ('auto'). The
desk completion gate logs its reviewer's verdict ('review'). Arguments are kept only as a short redacted summary.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from . import redact

log = logging.getLogger(__name__)

SUMMARY_CHARS = 200
_SECRET_KEY = re.compile(r"pass(word|wd|phrase)?$|secret|token|api_?key|auth|credential|cookie", re.I)

# The approvals row's decision → (logged decision, scope).
DECISIONS: dict[str, tuple[str, str | None]] = {
    "allow": ("allow_once", "once"), "allow_host": ("allow_once", "once"), "deny": ("deny", None),
    "always_session": ("always", "conversation"), "always_chat": ("always", "conversation"),
    "always_global": ("always", "global"), "always_rule": ("always", "rule"),
}
COLUMNS = ("ts", "conversation_id", "run_id", "desk_id", "agent", "tool", "args_summary", "decision", "scope", "rule_json",
           "note", "reviewer_verdict", "reviewer_reason", "reviewer_model", "reviewer_ms", "call_id")


def summarize(args: Any) -> str:
    """At most SUMMARY_CHARS of the arguments, with secret-named values blanked and credentials scrubbed."""
    def blank(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: "[secret]" if _SECRET_KEY.search(str(k)) else blank(x) for k, x in v.items()}
        if isinstance(v, list):
            return [blank(x) for x in v]
        return v
    text = args if isinstance(args, str) else json.dumps(blank(args), default=str, ensure_ascii=False)
    return redact.scrub(text, redact.COMMAND_OUTPUT_RULES, secret_assign=False)[:SUMMARY_CHARS]


def record(db: Any, *, tool: str, decision: str, args: Any = None, scope: str | None = None, conversation_id: str | None = None,
           run_id: str | None = None, desk_id: str | None = None, agent: str | None = None, rule: Any = None,
           note: str | None = None, review: dict[str, Any] | None = None, call_id: str | None = None) -> None:
    """Append one row. Never raises: losing a history row must not fail the decision it describes."""
    rv = review or {}
    if rv.get("confidence"):  # no column of its own: it rides on the note
        note = f"{note} · confidence: {rv['confidence']}" if note else f"confidence: {rv['confidence']}"
    row = (time.time(), conversation_id, run_id, desk_id, agent or ("desk" if desk_id else "chat"), tool,
           summarize(args) if args is not None else "", decision, scope,
           json.dumps(rule) if rule else None, (note or "").strip()[:500] or None,
           rv.get("verdict"), (str(rv.get("reason") or "")[:500] or None) if rv else None, rv.get("model"), rv.get("ms"), call_id)
    try:
        with db.tx() as c:
            c.execute(f"INSERT INTO approval_log({','.join(COLUMNS)}) VALUES({','.join('?' * len(COLUMNS))})", row)
    except Exception:  # noqa: BLE001
        log.exception("approval log write failed for %s", tool)


def history(db: Any, *, limit: int = 50, offset: int = 0, tool: str | None = None, decision: str | None = None,
            q: str | None = None) -> dict[str, Any]:
    """Newest first. `q` matches the tool, the arguments summary, the note and the reviewer's reason."""
    where, params = [], []
    if tool:
        where.append("tool=?")
        params.append(tool)
    if decision:
        where.append("decision=?")
        params.append(decision)
    if q:
        where.append("(tool LIKE ? OR args_summary LIKE ? OR note LIKE ? OR reviewer_reason LIKE ?)")
        params += [f"%{q}%"] * 4
    limit = max(1, min(int(limit), 200))
    sql = ("SELECT * FROM approval_log" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?")
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(sql, (*params, limit + 1, max(0, int(offset)))).fetchall()]
    for r in rows:
        r["rule"] = json.loads(r.pop("rule_json")) if r.get("rule_json") else None
    return {"items": rows[:limit], "more": len(rows) > limit}
