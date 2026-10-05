"""Delayed Gmail send, so every send can be taken back.

Nothing in the app calls Gmail's send API directly any more: a send is written to
`pending_sends` and held for `gmailSendHold.seconds` (60-120, default 90) before it goes out.
Agency the user can reverse is agency they will actually hand over, so the hold applies to the
assistant and to the compose window alike -- the assistant has no way to shorten it, only the
person at the keyboard does.

The queue lives in SQLite, not in memory, so a backend restart cannot lose a send or fire one
twice. On startup resume() looks at each held row:

  hold still running           left alone; the loop fires it when its moment comes
  hold expired while down,
    less than STALE_AFTER ago   left alone; the loop fires it on the next tick (the user had
                                their undo window, the backend simply was not there to send)
  hold expired longer ago       marked `expired` and NOT sent, but kept in the list with a
                                Send now button

The last rule is the deliberately conservative one: a mail queued before a laptop slept for a
day should not go out by itself hours later, when the user has had no chance to stop it and may
well have changed their mind -- but it must not vanish silently either, or they would believe
something was sent that never was.

Firing and cancelling race on one atomic UPDATE ... WHERE status='holding', so a send is
cancellable right up to the instant it is claimed and never after. Once it goes out, the
verification in google.py runs on it and its verdict is stored on the row: the UI shows
"sent, verified" or a loud unconfirmed warning, never a bare success.

A send that errors out at the API is marked `failed` and left in the list with its error. It is
not retried automatically and send_now will not touch it: an exception can be raised after the
message was accepted, so an automatic retry is how you send something twice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from .db import Database, new_id, row_to_dict
from . import redact, verify

log = logging.getLogger(__name__)


def _first_line(e: BaseException) -> str:
    """First line of an exception's message; the type name when the message is empty."""
    lines = str(e).strip().splitlines()
    return lines[0] if lines else type(e).__name__

# The user can tune the hold, but not out of the range that makes it useful: shorter than a
# minute is not enough time to notice a mistake, longer than two feels broken.
HOLD_MIN, HOLD_MAX = 60, 120
DEFAULT_HOLD: dict[str, Any] = {"enabled": True, "seconds": 90}
# How long after its moment a send may still go out on its own after a restart (see the docstring).
STALE_AFTER = 15 * 60
# Resolved rows stay in the list this long so the UI can show how a send turned out.
SHOW_RESOLVED_FOR = 15 * 60
# Ceiling on the sleep while something is held, so a clock jump or a missed poke costs a second,
# not a send; with an empty queue the loop just waits for a poke.
MAX_TICK, IDLE_TICK = 2.0, 60.0

HOLDING, SENDING, SENT, CANCELLED, FAILED, EXPIRED = "holding", "sending", "sent", "cancelled", "failed", "expired"
OPEN_STATUSES = (HOLDING, SENDING, EXPIRED, FAILED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_sends (
  id TEXT PRIMARY KEY,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  reply_to_message_id TEXT,
  origin TEXT NOT NULL DEFAULT 'app',          -- app | assistant
  conversation_id TEXT,
  status TEXT NOT NULL DEFAULT 'holding',      -- holding | sending | sent | cancelled | failed | expired
  hold_seconds INTEGER NOT NULL DEFAULT 90,
  created_at REAL NOT NULL,
  send_after REAL NOT NULL,
  resolved_at REAL,
  message_id TEXT,                             -- Gmail id, once it is out
  thread_id TEXT,
  error TEXT,
  verification TEXT                            -- JSON verdict from the post-send read-back
);
CREATE INDEX IF NOT EXISTS idx_pending_sends_due ON pending_sends(status, send_after);
"""
SEND_JSON = ("verification",)


class Outbox:
    def __init__(self, db: Database, google: Any, get_settings: Callable[[], dict[str, Any]] | None = None,
                 bounds: tuple[int, int] = (HOLD_MIN, HOLD_MAX), clock: Callable[[], float] = time.time):
        self.db = db
        self.google = google
        self.get_settings = get_settings or db.get_settings
        self.bounds = bounds
        self.clock = clock
        self._event: asyncio.Event | None = None
        self._loop_ref: asyncio.AbstractEventLoop | None = None
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---- config ----
    def config(self) -> dict[str, Any]:
        stored = self.get_settings().get("gmailSendHold") or {}
        lo, hi = self.bounds
        seconds = stored.get("seconds", DEFAULT_HOLD["seconds"])
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            seconds = DEFAULT_HOLD["seconds"]
        return {"enabled": bool(stored.get("enabled", DEFAULT_HOLD["enabled"])),
                "seconds": max(lo, min(seconds, hi)), "min": lo, "max": hi}

    # ---- queue / cancel / send now ----
    def queue(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None,
              origin: str = "app", conversation_id: str | None = None) -> dict[str, Any]:
        """Hold a send. With the hold turned off it goes out immediately, verification and all."""
        cfg = self.config()
        if not cfg["enabled"]:
            out = self.google.gmail_send(to, subject, body, reply_to_message_id)
            v = out.get("verification")
            # Same shape as a queued row, so one caller handles both: this one is just already over.
            return {"id": "", "to": to, "subject": subject, "status": SENT, "held": False, "seconds_left": 0,
                    "message_id": out.get("sent"), "thread_id": out.get("thread_id"),
                    "verified": verify.ok(v), "verification": v,
                    "error": None if verify.ok(v) else verify.summary_text(v)}
        pid, t = new_id(), self.clock()
        row = {"id": pid, "to_addr": to, "subject": subject, "body": body,
               "reply_to_message_id": reply_to_message_id, "origin": origin, "conversation_id": conversation_id,
               "status": HOLDING, "hold_seconds": cfg["seconds"], "created_at": t,
               "send_after": t + cfg["seconds"]}
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO pending_sends(id, to_addr, subject, body, reply_to_message_id, origin, conversation_id,"
                " status, hold_seconds, created_at, send_after) VALUES(:id, :to_addr, :subject, :body,"
                " :reply_to_message_id, :origin, :conversation_id, :status, :hold_seconds, :created_at, :send_after)",
                row)
        self.poke()
        return self._out(row)

    def get(self, pid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM pending_sends WHERE id = ?", (pid,)).fetchone()
        d = row_to_dict(r, SEND_JSON)
        return self._out(d) if d else None

    def list(self) -> list[dict[str, Any]]:
        """Everything still open, plus whatever resolved recently so the UI can show the outcome."""
        marks = ", ".join("?" * len(OPEN_STATUSES))
        with self.db.tx() as c:
            rows = c.execute(
                f"SELECT * FROM pending_sends WHERE status IN ({marks}) OR resolved_at > ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 50",  # rowid: two sends in the same second still order
                (*OPEN_STATUSES, self.clock() - SHOW_RESOLVED_FOR)).fetchall()
        return [self._out(row_to_dict(r, SEND_JSON) or {}) for r in rows]

    def cancel(self, pid: str) -> dict[str, Any] | None:
        """Undo. Wins over the worker only while the row is still `holding`."""
        return self._resolve(pid, CANCELLED)

    def discard(self, pid: str) -> dict[str, Any] | None:
        """Drop a send that expired across a restart, or one that failed: it is not going out."""
        with self.db.tx() as c:
            cur = c.execute("UPDATE pending_sends SET status = ?, resolved_at = ? WHERE id = ? AND status IN (?, ?)",
                            (CANCELLED, self.clock(), pid, EXPIRED, FAILED))
            changed = cur.rowcount
        return self.get(pid) if changed else None

    def _resolve(self, pid: str, status: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            cur = c.execute("UPDATE pending_sends SET status = ?, resolved_at = ? WHERE id = ? AND status = ?",
                            (status, self.clock(), pid, HOLDING))
            changed = cur.rowcount
        return self.get(pid) if changed else None

    async def send_now(self, pid: str) -> dict[str, Any] | None:
        """Skip the rest of the hold. Only ever reachable from the UI, never from a tool."""
        with self.db.tx() as c:
            cur = c.execute("UPDATE pending_sends SET send_after = ?, status = ?, error = NULL"
                            " WHERE id = ? AND status IN (?, ?)", (self.clock(), HOLDING, pid, HOLDING, EXPIRED))
            if not cur.rowcount:
                return None
        await self.run_due()
        return self.get(pid)

    # ---- the worker ----
    def resume(self) -> dict[str, int]:
        """Startup pass over the unresolved rows. See the module docstring for the three hold cases."""
        cutoff = self.clock() - STALE_AFTER
        with self.db.tx() as c:
            expired = c.execute(
                "UPDATE pending_sends SET status = ?, error = ? WHERE status = ? AND send_after < ?",
                (EXPIRED, "The backend was not running when this was due, and it was too old to send unattended. "
                          "Nothing was sent.", HOLDING, cutoff)).rowcount
            # A row still marked `sending` means the process died mid-call: Gmail may or may not have
            # taken it, so it is never retried. The user is told to check instead.
            interrupted = c.execute(
                "UPDATE pending_sends SET status = ?, resolved_at = ?, error = ? WHERE status = ?",
                (FAILED, self.clock(), "The backend stopped while this was being sent, so it is unknown whether it "
                                       "went out. It was NOT retried — check your Gmail Sent folder.", SENDING)).rowcount
            held = c.execute("SELECT COUNT(*) AS n FROM pending_sends WHERE status = ?", (HOLDING,)).fetchone()["n"]
        if expired or interrupted:
            log.warning("outbox: %d held send(s) expired across a restart and were NOT sent; %d were interrupted mid-send",
                        expired, interrupted)
        return {"expired": expired, "interrupted": interrupted, "holding": int(held)}

    def _claim_due(self) -> list[dict[str, Any]]:
        """Take ownership of the oldest due row that is not stale. The UPDATE is the handshake with cancel().

        One row per call: a delivery can take seconds (read-back retries), and every row claimed
        up front would already be past its Undo while it waits its turn.
        """
        claimed: list[dict[str, Any]] = []
        now = self.clock()
        with self.db.tx() as c:
            # A laptop that slept past the staleness window must not fire the mail hours later; the
            # same rule resume() applies at startup, now applied on every tick.
            c.execute("UPDATE pending_sends SET status = ?, error = ? WHERE status = ? AND send_after < ?",
                      (EXPIRED, "This was due while the computer was asleep or the backend was not running, and it "
                                "was too old to send unattended. Nothing was sent.", HOLDING, now - STALE_AFTER))
            due = c.execute("SELECT id FROM pending_sends WHERE status = ? AND send_after <= ? ORDER BY send_after LIMIT 1",
                            (HOLDING, now)).fetchall()
            for r in due:
                if c.execute("UPDATE pending_sends SET status = ? WHERE id = ? AND status = ?",
                             (SENDING, r["id"], HOLDING)).rowcount:
                    row = c.execute("SELECT * FROM pending_sends WHERE id = ?", (r["id"],)).fetchone()
                    claimed.append(dict(row))
        return claimed

    async def run_due(self) -> int:
        n = 0
        while True:
            rows = await asyncio.to_thread(self._claim_due)
            if not rows:
                return n
            await asyncio.to_thread(self._deliver, rows[0])
            n += 1

    def _deliver(self, row: dict[str, Any]) -> None:
        try:
            out = self.google.gmail_send(row["to_addr"], row["subject"], row["body"], row["reply_to_message_id"])
        except Exception as e:  # noqa: BLE001 - the row carries the failure; the loop keeps running
            log.warning("outbox: send %s failed: %s", row["id"], e)
            with self.db.tx() as c:
                c.execute("UPDATE pending_sends SET status = ?, resolved_at = ?, error = ? WHERE id = ?",
                          (FAILED, self.clock(), f"{type(e).__name__}: {_first_line(e)[:300]}", row["id"]))
            return
        v = out.get("verification")
        with self.db.tx() as c:
            c.execute("UPDATE pending_sends SET status = ?, resolved_at = ?, message_id = ?, thread_id = ?,"
                      " verification = ?, error = ? WHERE id = ?",
                      (SENT, self.clock(), out.get("sent"), out.get("thread_id"),
                       json.dumps(v) if v else None,
                       None if verify.ok(v) else verify.summary_text(v), row["id"]))

    def next_wake(self) -> float:
        with self.db.tx() as c:
            r = c.execute("SELECT MIN(send_after) AS t FROM pending_sends WHERE status = ?", (HOLDING,)).fetchone()
        t = r["t"] if r else None
        if t is None:
            return IDLE_TICK  # nothing held; queue() pokes the loop awake
        return max(0.0, min(float(t) - self.clock(), MAX_TICK))

    def poke(self) -> None:
        """Wake the loop now (a new send may be due sooner than its current sleep)."""
        ev, loop = self._event, self._loop_ref
        if ev and loop and not loop.is_closed():
            loop.call_soon_threadsafe(ev.set)

    async def loop(self) -> None:
        self._event = asyncio.Event()
        self._loop_ref = asyncio.get_running_loop()
        try:
            await asyncio.to_thread(self.resume)
        except Exception as e:  # noqa: BLE001 - a bad resume must not stop the backend booting
            log.warning("outbox: resume failed: %s", e)
        while True:
            try:
                await self.run_due()
            except Exception as e:  # noqa: BLE001
                log.warning("outbox: delivery pass failed: %s", e)
            try:
                await asyncio.wait_for(self._event.wait(), timeout=max(0.05, self.next_wake()))
            except asyncio.TimeoutError:
                pass
            self._event.clear()

    # ---- shaping ----
    def _out(self, row: dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        d["to"] = d.pop("to_addr", d.get("to"))
        d.pop("body", None)  # the list is an undo affordance, not a mail client
        if isinstance(d.get("verification"), str):
            try:
                d["verification"] = json.loads(d["verification"])
            except ValueError:
                d["verification"] = None
        d["verified"] = verify.ok(d.get("verification")) if d.get("status") == SENT else None
        d["seconds_left"] = max(0, int(round(float(d.get("send_after") or 0) - self.clock()))) if d.get("status") == HOLDING else 0
        return d


def router(outbox: Outbox) -> Any:
    """The outbox's own routes, so app.py only has to include them.

    send-now is here and deliberately not a tool: shortening the undo window is the user's call.
    """
    from fastapi import APIRouter, HTTPException

    r = APIRouter(prefix="/outbox/gmail", tags=["outbox"])

    @r.get("")
    def list_pending() -> dict[str, Any]:
        return {"sends": outbox.list(), "config": outbox.config()}

    @r.post("/{pid}/cancel")
    def cancel(pid: str) -> dict[str, Any]:
        row = outbox.cancel(pid) or outbox.discard(pid)
        if not row:
            raise HTTPException(409, "Too late — that email has already gone out.")
        return row

    @r.post("/{pid}/send-now")
    async def send_now(pid: str) -> dict[str, Any]:
        row = await outbox.send_now(pid)
        if not row:
            raise HTTPException(409, "That email is no longer waiting to be sent.")
        return row

    return r


def queued_result(row: dict[str, Any]) -> dict[str, Any]:
    """What the model is told when its send is held. It has not been sent, and must not say so."""
    if row.get("status") == SENT:  # the hold is turned off, so it really did go out
        shown = dict(row)
        for key in ("subject", "body", "error"):
            if isinstance(shown.get(key), str):
                shown[key] = redact.scrub_command_output(shown[key])
        return shown
    left = row.get("seconds_left") or row.get("hold_seconds") or 0
    return {"queued": row.get("id"), "to": row.get("to"),
            "subject": redact.scrub_command_output(str(row.get("subject") or "")),
            "status": row.get("status"), "sends_in_seconds": left,
            "note": f"NOT SENT YET. Held for {left}s so the user can undo it, then it goes out on its own. "
                    "Tell them it will send shortly and that they can cancel it; do not say it was sent. "
                    "To stop it, call gmail_outbox with action='cancel'."}
