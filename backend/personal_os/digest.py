"""The daily digest: one quiet Agent Inbox row a day, assembled from counts the app already keeps.

No model call and no OS notification. The row is a finished `kind='job'` run with no job behind it, so the
inbox lists it under "While you were away" with the usual read flag, and the notification feed (which only
speaks for failures and proposals) has nothing to say about it. Settings key `digest`: {enabled, hour}.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from .insights import _mins

NAME = "Daily digest"
# A relaunch never repeats it, and a late day still lets the next one come back to the set hour.
MIN_GAP_S = 20 * 3600


def config(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get("digest") if isinstance(settings.get("digest"), dict) else {}
    try:
        hour = min(23, max(0, int(raw.get("hour", 8))))
    except (TypeError, ValueError):
        hour = 8
    return {"enabled": raw.get("enabled", True) is not False, "hour": hour}


def due(now: float, hour: int, last: float | None) -> bool:
    """Past today's hour (local time), and no digest yet today or within MIN_GAP_S."""
    t = datetime.fromtimestamp(now)
    if t.hour < hour:
        return False
    if last is None:
        return True
    return datetime.fromtimestamp(last).date() != t.date() and now - last >= MIN_GAP_S


def app_seconds(focus_events: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for e in focus_events:
        app = e.get("app") or ""
        if app:
            out[app] = out.get(app, 0.0) + float(e.get("duration_ms") or 0) / 1000.0
    return out


def assemble(*, recorded: int, notes_pending: int, apps: dict[str, float],
             gaps: list[tuple[str, dict[str, str]]]) -> tuple[str, list[dict[str, str]]]:
    """(markdown body, fix links). An empty body means there is nothing to say, and no row is written.

    `gaps` is [(sentence, {label, view} or {label, settings})]: one setup problem and the one place that fixes it.
    """
    lines: list[str] = []
    if recorded or notes_pending:
        parts = []
        if recorded:
            parts.append(f"{recorded} recorded")
        if notes_pending:
            parts.append(f"{notes_pending} set{'s' if notes_pending != 1 else ''} of notes waiting for review")
        lines.append("**Meetings:** " + ", ".join(parts) + ".")
    total = sum(apps.values())
    if total >= 60:
        top = sorted(((a, s) for a, s in apps.items() if a != "(private)"), key=lambda x: -x[1])[:3]
        most = ", ".join(f"{a} {_mins(s)}" for a, s in top)
        lines.append(f"**Activity:** {_mins(total)} in apps" + (f". Most time: {most}." if most else "."))
    for sentence, _ in gaps:
        lines.append(f"**Setup:** {sentence}")
    return "\n\n".join(lines), [link for _, link in gaps]


def write(runs: Any, body: str, links: list[dict[str, str]], at: float | None = None) -> str:
    """Store the digest as a finished job run whose reply is `body`. Returns the run id."""
    t = time.time() if at is None else at
    rid = f"digest-{uuid.uuid4().hex[:12]}"
    runs.create(rid, None, kind="job", input={"job": NAME, "kind": "digest", "fired_at": t, "links": links})
    runs.append(rid, 1, "delta", {"id": rid, "text": body})
    runs.update(rid, status="done", message_id=rid, last_seq=1, ended_at=t)
    return rid


def last_at(runs: Any, now: float) -> float | None:
    """When the newest digest was written, looking back two days (older is as good as never)."""
    for r in runs.of_kind("job", since=now - 2 * 86400, limit=500):
        if isinstance(r.get("input"), dict) and r["input"].get("kind") == "digest":
            return float(r["started_at"])
    return None
