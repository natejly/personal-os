"""Reply tracker: which threads need the user's reply, and which sent mail is still unanswered.

Four statuses per thread, as a reply-zero tracker: to_reply (they wrote last and want something),
awaiting_reply (I wrote last and asked something), fyi (nothing needed) and actioned. The classifier
works from headers and snippets only; an LLM refinement is opt-in, injected, and sees only subject,
sender domain and snippet. Nothing here labels, archives, drafts or sends: it reads thread metadata
and writes its own `thread_status` table.

A thread dict is `Google.gmail_threads_recent`'s shape:
{thread_id, subject, messages: [{id, from, to, cc, date (ISO), labels, snippet, auto}]}, oldest first.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from email.utils import parseaddr
from typing import Any, Callable

from .db import Database, now as _now

STATUSES = ("to_reply", "awaiting_reply", "fyi", "actioned")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "awaitingAfterDays": 3,
    "needsReplyAfterHours": 24,
    "useLLM": False,
    "query": "newer_than:14d -category:promotions -category:social",
    "proposeFollowups": True,
}

REQUEST_PHRASES = ("let me know", "can you", "could you", "please confirm", "when are you", "would you", "do you ", "are you able", "please send", "please let")
AUTOMATED_LOCALPARTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "notifications", "notification", "mailer-daemon", "postmaster")


def _line(text: str, limit: int = 180) -> str:
    """One line. A subject is someone else's text and becomes a todo title."""
    return " ".join(str(text or "").replace("\r", " ").split())[:limit]

SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_status (
  thread_id TEXT PRIMARY KEY,
  subject TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  last_msg_id TEXT,
  last_from TEXT,
  last_date TEXT,
  age_days REAL,
  dismissed INTEGER NOT NULL DEFAULT 0,
  followup_todo_id TEXT,
  updated_at REAL,
  snoozed_until REAL
);
"""


def _addr(value: str | None) -> str:
    return parseaddr(value or "")[1].strip().lower()


def _addrs(value: str | None) -> list[str]:
    from email.utils import getaddresses
    return [a.strip().lower() for _, a in getaddresses([value or ""]) if a]


def _when(iso: str | None, fallback: datetime) -> datetime:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def has_request(text: str | None) -> bool:
    t = (text or "").lower()
    return "?" in t or any(p in t for p in REQUEST_PHRASES)


def _automated_sender(frm: str | None) -> bool:
    local = _addr(frm).split("@", 1)[0]
    return any(local == p or local.startswith(p) for p in AUTOMATED_LOCALPARTS)


def classify(thread: dict[str, Any], me: str, now: datetime, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deterministic status for one thread. Rules run in order; see the module docstring."""
    msgs = thread.get("messages") or []
    me = me.strip().lower()
    now = _aware(now)
    if not msgs:
        return {"status": "fyi", "reason": "empty thread", "age_days": 0.0, "last_from": ""}
    last = msgs[-1]
    last_from = _addr(last.get("from"))
    age = max(0.0, (now - _when(last.get("date"), now)).total_seconds() / 86400)
    out = {"age_days": round(age, 3), "last_from": last_from}

    def done(status: str, reason: str) -> dict[str, Any]:
        # Rule: when I sent last, "fyi" is not a possible outcome.
        assert not (last_from == me and status == "fyi"), "fyi is impossible when the user sent last"
        return {"status": status, "reason": reason, **out}

    if last_from == me:
        if has_request(last.get("snippet")):
            return done("awaiting_reply", "my last message asks something")
        return done("actioned", "my last message needs no answer")

    if last.get("auto") or _automated_sender(last.get("from")):
        return done("fyi", "automated")

    # Did I reply after their last request?
    last_q = max((i for i, m in enumerate(msgs) if _addr(m.get("from")) != me and not m.get("auto") and has_request(m.get("snippet"))), default=-1)
    replied = [i for i, m in enumerate(msgs) if _addr(m.get("from")) == me]
    if last_q >= 0 and any(i > last_q for i in replied):
        return done("actioned", "I replied after their last question")
    if has_request(last.get("snippet")):
        return done("to_reply", "their last message asks something")
    if me in _addrs(last.get("to")) and not replied:
        return done("to_reply", "addressed to me and I have not replied")
    return done("fyi", "nothing asked of me")


def _llm_payload(thread: dict[str, Any]) -> dict[str, str]:
    last = (thread.get("messages") or [{}])[-1]
    frm = _addr(last.get("from"))
    domain = frm.split("@", 1)[1] if "@" in frm else ""
    # Subject and snippet are someone else's text. One line each, so a newline cannot open a new section
    # if a model is later shown this payload.
    return {"subject": _line(thread.get("subject") or ""), "domain": _line(domain, 80),
            "snippet": _line(last.get("snippet") or "", 300)}


def refine(items: list[tuple[dict[str, Any], dict[str, Any]]], llm_fn: Callable[[list[dict[str, str]]], Any], me: str) -> list[dict[str, Any]]:
    """Let an injected model override heuristic statuses. It sees subject, sender domain and snippet only.

    `llm_fn(payloads)` returns one status per payload (a list, `{"statuses": [...]}`, or either as JSON text).
    Anything unparseable, the wrong length, an unknown value, or `fyi` for a thread where I sent last
    falls back to the heuristic result for that thread (or for all of them, when the call itself fails).
    """
    results = [dict(r) for _, r in items]
    try:
        raw = llm_fn([_llm_payload(t) for t, _ in items])
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            raw = raw.get("statuses")
        if not isinstance(raw, list) or len(raw) != len(items):
            return results
    except Exception:  # noqa: BLE001 - a flaky model must never break the tracker
        return results
    for res, (thread, _), status in zip(results, items, raw):
        if status not in STATUSES:
            continue
        sent_last = _addr(((thread.get("messages") or [{}])[-1]).get("from")) == me.strip().lower()
        if sent_last and status == "fyi":
            continue
        if status != res["status"]:
            res["status"], res["reason"] = status, "model refinement"
    return results


class MailWatch:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            if "snoozed_until" not in {r["name"] for r in c.execute("PRAGMA table_info(thread_status)")}:
                c.execute("ALTER TABLE thread_status ADD COLUMN snoozed_until REAL")

    def refresh(self, rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> int:
        """Upsert (thread, classification) pairs. A changed last message un-dismisses the thread."""
        t = _now()
        with self.db.tx() as c:
            for thread, res in rows:
                msgs = thread.get("messages") or []
                last = msgs[-1] if msgs else {}
                old = c.execute("SELECT last_msg_id, dismissed, followup_todo_id, snoozed_until FROM thread_status WHERE thread_id=?", (thread["thread_id"],)).fetchone()
                # A row snooze() made has no last message yet: its first refresh is not new mail.
                same = bool(old) and old["last_msg_id"] in (None, last.get("id"))
                dismissed = int(old["dismissed"]) if same else 0
                snooze = old["snoozed_until"] if same else None  # a new message wakes the thread
                c.execute(
                    "INSERT INTO thread_status(thread_id,subject,status,reason,last_msg_id,last_from,last_date,age_days,dismissed,followup_todo_id,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET subject=excluded.subject, status=excluded.status, "
                    "reason=excluded.reason, last_msg_id=excluded.last_msg_id, last_from=excluded.last_from, last_date=excluded.last_date, "
                    "age_days=excluded.age_days, dismissed=excluded.dismissed, updated_at=excluded.updated_at, snoozed_until=?",
                    (thread["thread_id"], thread.get("subject") or "", res["status"], res["reason"], last.get("id"), res["last_from"],
                     last.get("date"), res["age_days"], dismissed, old["followup_todo_id"] if old else None, t, snooze))
        return len(rows)

    def list(self, status: str | None = None, include_dismissed: bool = False, at: datetime | None = None) -> list[dict[str, Any]]:
        where = ["status IN ('to_reply','awaiting_reply')" if status is None else "status = ?"]
        args: list[Any] = [] if status is None else [status]
        if not include_dismissed:
            where.append("dismissed = 0")
        where.append("(snoozed_until IS NULL OR snoozed_until <= ?)")
        args.append((_aware(at) if at else datetime.now(timezone.utc)).timestamp())
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM thread_status WHERE {' AND '.join(where)} ORDER BY last_date DESC", args).fetchall()]
        if at is not None:  # age is relative to the last message, so recompute it at read time
            for r in rows:
                r["age_days"] = round(max(0.0, (_aware(at) - _when(r["last_date"], _aware(at))).total_seconds() / 86400), 3)
        return rows

    def get(self, thread_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM thread_status WHERE thread_id=?", (thread_id,)).fetchone()
        return dict(r) if r else None

    def dismiss(self, thread_id: str, dismissed: bool) -> bool:
        with self.db.tx() as c:
            return c.execute("UPDATE thread_status SET dismissed=? WHERE thread_id=?", (int(dismissed), thread_id)).rowcount > 0

    def snooze(self, thread_id: str, until: datetime | None, subject: str = "", create: bool = True) -> bool:
        """Hide a thread from list() until `until` (None clears). Local only; Gmail is untouched. With `create`, a
        thread not tracked yet gets a row, so any thread in the mail list can be snoozed."""
        with self.db.tx() as c:
            if create:
                c.execute("INSERT INTO thread_status(thread_id,subject,status,updated_at) VALUES(?,?,'fyi',?) "
                          "ON CONFLICT(thread_id) DO NOTHING", (thread_id, subject, _now()))
            return c.execute("UPDATE thread_status SET snoozed_until=? WHERE thread_id=?",
                             (_aware(until).timestamp() if until else None, thread_id)).rowcount > 0

    def snoozed_ids(self, at: datetime) -> list[str]:
        with self.db.tx() as c:
            return [r["thread_id"] for r in c.execute("SELECT thread_id FROM thread_status WHERE snoozed_until > ?",
                                                      (_aware(at).timestamp(),))]

    def counts(self, cfg: dict[str, Any], at: datetime) -> dict[str, int]:
        to_reply = len(self.list("to_reply", at=at))
        overdue = sum(1 for r in self.list("awaiting_reply", at=at) if r["age_days"] >= cfg["awaitingAfterDays"])
        return {"to_reply": to_reply, "awaiting_reply_overdue": overdue}

    def propose_followups(self, cfg: dict[str, Any], at: datetime, today: date) -> list[dict[str, Any]]:
        """Todo suggestions for stale awaiting_reply threads with no follow-up yet. Creates nothing."""
        if not cfg.get("proposeFollowups"):
            return []
        return [{"thread_id": r["thread_id"], "title": f"Follow up: {_line(r['subject']) or '(no subject)'}",
                 "notes": f"Waiting on a reply since {str(r['last_date'] or '')[:10]}. Gmail thread {r['thread_id']}", "due": today.isoformat()}
                for r in self.list("awaiting_reply", at=at)
                if r["age_days"] >= cfg["awaitingAfterDays"] and not r["followup_todo_id"]]

    def create_followup(self, thread_id: str, todos: Any, today: date) -> dict[str, Any] | None:
        """Make the follow-up todo for one thread, once. A second call returns the same todo."""
        row = self.get(thread_id)
        if not row:
            return None
        if row["followup_todo_id"] and todos.get(row["followup_todo_id"]):
            return todos.get(row["followup_todo_id"])
        subject = _line(row.get("subject") or "") or "(no subject)"
        todo = todos.create(f"Follow up: {subject}",
                            notes=f"Waiting on a reply since {str(row['last_date'] or '')[:10]}. https://mail.google.com/mail/u/0/#all/{thread_id}",
                            due=today.isoformat(), source="email")
        with self.db.tx() as c:
            c.execute("UPDATE thread_status SET followup_todo_id=? WHERE thread_id=?", (todo["id"], thread_id))
        return todo
