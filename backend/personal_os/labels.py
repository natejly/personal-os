"""Human names for the ids a tool call carries.

A call such as gmail_modify(message_id="1a12d6...") or calendar_delete(event_id=...) means nothing to the person
watching it. This resolves the id to what they would recognise (an email's subject and sender, an event's title
and time, a task's or file's title) from what we already hold, asking Google only when we have never seen the item.
Reads only, and every failure falls back to None: the caller then shows the id, never an error.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import logging
from typing import Any

log = logging.getLogger(__name__)

# tool -> (kind, name of the argument that holds the id)
TARGETS: dict[str, tuple[str, str]] = {
    "gmail_read": ("message", "message_id"), "gmail_modify": ("message", "message_id"),
    "gmail_send": ("message", "reply_to_message_id"), "gmail_draft": ("message", "reply_to_message_id"),
    "calendar_get": ("event", "event_id"), "calendar_update": ("event", "event_id"),
    "calendar_delete": ("event", "event_id"), "calendar_respond": ("event", "event_id"),
    "google_tasks_complete": ("task", "task_id"),
    "todo_update": ("todo", "id"), "todo_delete": ("todo", "id"),
    "doc_read": ("doc", "doc"), "doc_edit": ("doc", "doc"), "doc_delete": ("doc", "doc"),
    "doc_comments": ("doc", "doc"), "doc_comment_reply": ("doc", "doc"),
}
# The write tools whose result carries the label back to the model, so its reply can name the item.
ECHO = frozenset({"gmail_modify", "calendar_update", "calendar_delete", "calendar_respond", "google_tasks_complete",
                  "todo_update", "todo_delete", "doc_edit", "doc_delete"})


def _day(d: dt.datetime) -> str:
    return f"{d:%a %b} {d.day}"


def _clock(d: dt.datetime) -> str:
    return f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def when(start: str | None, end: str | None = None) -> str:
    """'Mon Oct 12, 8:30 – 10:00 PM', 'Mon Oct 12 (all day)'; '' when the start does not parse."""
    try:
        s = dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if len(str(start)) == 10:
        return f"{_day(s)} (all day)"
    try:
        e = dt.datetime.fromisoformat(str(end).replace("Z", "+00:00")) if end else None
    except ValueError:
        e = None
    if e is None or e.date() != s.date():
        return f"{_day(s)}, {_clock(s)}"
    a, b = _clock(s), _clock(e)
    return f"{_day(s)}, {a.rsplit(' ', 1)[0] if a[-2:] == b[-2:] else a} – {b}"


def _sender(frm: str | None) -> str:
    name, addr = email.utils.parseaddr(frm or "")
    return name or addr


def _short_date(iso: str | None) -> str:
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{d:%b} {d.day}"


def _message(g: Any, mid: str) -> str | None:
    snap = g._reads.get("gmail", "meta") or {}
    row = (snap.get("by_id") or {}).get(mid)
    if not isinstance(row, dict) or not row.get("subject"):
        row = g.gmail_get(mid)
    return " · ".join(p for p in (row.get("subject") or "(no subject)", _sender(row.get("from")), _short_date(row.get("date"))) if p)


def _event(g: Any, eid: str, cal: str) -> str | None:
    e = g.calendar_get(eid, cal)
    parts = [e.get("summary") or "(no title)", when(e.get("start"), e.get("end"))]
    if cal and cal != "primary":
        try:
            name = next((c["summary"] for c in g.calendars() if c.get("id") == cal and not c.get("primary")), "")
        except Exception:  # noqa: BLE001
            name = ""
        parts.append(name)
    return " · ".join(p for p in parts if p)


def _task(g: Any, tid: str) -> str | None:
    t = g.tasks_get(tid, "@default")
    return " · ".join(p for p in (t.get("title"), f"due {_short_date(t['due'])}" if t.get("due") else "") if p) or None


def lookup(tb: Any, kind: str, ident: str, calendar_id: str | None = None) -> str | None:
    """The label for one id, or None when it is unknown or cannot be read. Blocking: call from a thread."""
    ident = (ident or "").strip()
    if not ident:
        return None
    try:
        if kind == "message":
            return _message(tb.google, ident)
        if kind == "event":
            return _event(tb.google, ident, calendar_id or "primary")
        if kind == "task":
            return _task(tb.google, ident)
        if kind == "todo":
            store = next((m.store for m in tb.modules if getattr(m, "key", "") == "todos"), None)
            t = store.get(ident) if store else None
            return " · ".join(p for p in (t["title"], f"due {t['due']}" if t.get("due") else "") if p) if t else None
        if kind == "doc":
            d = tb.docs.find(ident)
            return (d["title"] or "Untitled") if d else None
    except Exception:  # noqa: BLE001 - not connected, offline, gone: the id is shown instead
        log.debug("no label for %s %s", kind, ident, exc_info=True)
    return None


def for_call(tb: Any, tool: str, args: dict[str, Any]) -> str | None:
    """The label of the item a tool call acts on, or None for a tool that does not target one."""
    spec = TARGETS.get(tool)
    if not spec or not isinstance(args, dict) or not isinstance(args.get(spec[1]), str):
        return None
    return lookup(tb, spec[0], args[spec[1]], args.get("calendar_id"))
