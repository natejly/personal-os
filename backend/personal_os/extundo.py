"""Undo for the agent's Google Calendar and Google Tasks writes.

Each verified agent write keeps one row: the id it created, or the object as it stood before the write
(read just before it, since the write's own verification only reads afterwards). The tool result carries
`undo: {external_id}`, which the tool card turns into an Undo button. Undoing is the user's action through
POST /external-undo/{id}, never a tool, and the inverse write is verified like any other.

An undo refuses (409) when the object changed after the agent's write (its etag, or a task's `updated`),
when it already ran, or after UNDO_TTL. It never overwrites someone else's later edit.

Inverses, all with the original call's send_updates, so guests hear about the undo exactly as they heard
about the write:
  delete_created   a created event or task is deleted
  restore_fields   an edited event (or an RSVP) gets its earlier fields back; a completed task reopens
  recreate         a deleted event is un-cancelled in place (same id, links and guests); only when Google
                   has purged it is a copy inserted, which gets a new id
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import verify
from .db import Database, new_id

log = logging.getLogger(__name__)

UNDO_TTL = 7 * 86400

# Fields an agent edit can touch, restored when they differ from the pre-image.
_EVENT_FIELDS = ("summary", "description", "location", "start", "end", "attendees", "recurrence", "reminders",
                 "colorId", "visibility", "transparency", "conferenceData",
                 "guestsCanInviteOthers", "guestsCanModify", "guestsCanSeeOtherGuests")
# Server-owned fields a re-inserted copy must not carry.
_SERVER_FIELDS = ("id", "etag", "kind", "htmlLink", "iCalUID", "created", "updated", "creator", "organizer",
                  "sequence", "hangoutLink", "recurringEventId", "originalStartTime", "status")

CALENDAR = {"calendar_create", "calendar_update", "calendar_delete", "calendar_respond"}
TASKS = {"google_tasks_add", "google_tasks_complete"}
TOOLS = CALENDAR | TASKS


class Conflict(Exception):
    """The object is not as the agent left it, so undoing would clobber a later change."""


class NotFound(Exception):
    pass


class Unavailable(Exception):
    """Already undone, or expired."""


class ExternalUndo:
    def __init__(self, db: Database, google: Any) -> None:
        self.db, self.google = db, google

    # ---- capture (called by the tool layer around the write) ----
    def before(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """The pre-image an update, delete, RSVP or completion needs. Never blocks the write: None means no undo."""
        try:
            if tool in ("calendar_update", "calendar_delete", "calendar_respond"):
                return self.google.calendar_raw(args["event_id"], args.get("calendar_id") or "primary")
            if tool == "google_tasks_complete":
                return self.google.tasks_get(args["task_id"], "@default")
        except Exception:  # noqa: BLE001
            log.warning("no undo pre-image for %s", tool, exc_info=True)
        return None

    def after(self, tool: str, args: dict[str, Any], pre: dict[str, Any] | None, result: Any, ctx: dict[str, Any] | None) -> Any:
        """Record the undo row for a verified write and put its handle on the result."""
        if not isinstance(result, dict) or not verify.ok(result.get("verification")):
            return result
        try:
            kind, payload, etag = self._payload(tool, args, pre, result)
        except Exception:  # noqa: BLE001 - the write stands; it just cannot be undone
            log.warning("no undo row for %s", tool, exc_info=True)
            return result
        if kind is None:
            return result
        uid, ctx = new_id(), ctx or {}
        with self.db.tx() as c:
            c.execute("INSERT INTO external_undo(id, run_id, message_id, conversation_id, tool, kind, payload, after_etag, status, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,'live',?)",
                      (uid, ctx.get("run_id"), ctx.get("message_id"), ctx.get("conversation_id"), tool, kind,
                       json.dumps(payload), etag, time.time()))
        handle: dict[str, Any] = {"external_id": uid}
        if payload.get("send_updates") in ("all", "externalOnly"):
            handle["notifies"] = True  # the Undo button says guests will be emailed
        return {**result, "undo": handle}

    def _payload(self, tool: str, args: dict[str, Any], pre: dict[str, Any] | None,
                 res: dict[str, Any]) -> tuple[str | None, dict[str, Any], str | None]:
        send = args.get("send_updates") or "none"
        if tool == "calendar_create":
            cal = res.get("calendar_id") or args.get("calendar_id") or "primary"
            now = self.google.calendar_raw(res["id"], cal) or {}
            return "delete_created", {"calendar_id": cal, "event_id": res["id"], "send_updates": send}, now.get("etag")
        if tool in ("calendar_update", "calendar_respond"):
            if not pre:
                return None, {}, None
            cal = res.get("calendar_id") or args.get("calendar_id") or "primary"
            now = self.google.calendar_raw(args["event_id"], cal) or {}
            return "restore_fields", {"calendar_id": cal, "from_calendar_id": args.get("calendar_id") or "primary",
                                      "event_id": args["event_id"], "before": pre, "send_updates": send}, now.get("etag")
        if tool == "calendar_delete":
            if not pre:
                return None, {}, None
            return "recreate", {"calendar_id": args.get("calendar_id") or "primary", "event_id": args["event_id"],
                                "before": pre, "send_updates": send}, None
        if tool == "google_tasks_add":
            now = self.google.tasks_get(res["id"], "@default")
            return "delete_created", {"tasklist": "@default", "task_id": res["id"]}, now.get("updated")
        if tool == "google_tasks_complete":
            if not pre:
                return None, {}, None
            now = self.google.tasks_get(args["task_id"], "@default")
            return "restore_fields", {"tasklist": "@default", "task_id": args["task_id"], "before": pre}, now.get("updated")
        return None, {}, None

    # ---- undo (the user's route) ----
    def _row(self, uid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM external_undo WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None

    def _set(self, uid: str, status: str, when: str = "live") -> bool:
        with self.db.tx() as c:
            n = c.execute("UPDATE external_undo SET status=?, undone_at=? WHERE id=? AND status=?",
                          (status, time.time() if status == "undone" else None, uid, when)).rowcount
        return n == 1

    def undo(self, uid: str) -> dict[str, Any]:
        row = self._row(uid)
        if not row:
            raise NotFound(uid)
        if row["status"] == "live" and time.time() - row["created_at"] > UNDO_TTL:
            self._set(uid, "expired")
            raise Unavailable("this undo has expired")
        # Claim it atomically, so two clicks cannot both run the inverse.
        if not self._set(uid, "running"):
            raise Unavailable(f"already {self._row(uid)['status']}")
        try:
            p = json.loads(row["payload"])
            out = (self._calendar if row["tool"] in CALENDAR else self._task)(row["kind"], p, row["after_etag"])
        except BaseException:
            self._set(uid, "live", "running")
            raise
        if not verify.ok(out.get("verification")):
            # The etag check guards a retry: if the inverse did land, the object changed and the next try refuses.
            self._set(uid, "live", "running")
            raise Unavailable(verify.summary_text(out.get("verification")))
        self._set(uid, "undone", "running")
        return {"ok": True, "kind": row["kind"], "result": out}

    def _calendar(self, kind: str, p: dict[str, Any], etag: str | None) -> dict[str, Any]:
        g, cal, eid, send = self.google, p["calendar_id"], p["event_id"], p.get("send_updates") or "none"
        now = g.calendar_raw(eid, cal)
        live = now is not None and now.get("status") != "cancelled"
        if kind == "recreate":
            if live:
                raise Conflict("the event is already back")
            if now is not None:
                # Google kept the cancelled event: confirm it again, so the id, links and guest list survive.
                return g.calendar_restore(eid, {"status": "confirmed"}, cal, send)
            body = {k: v for k, v in p["before"].items() if k not in _SERVER_FIELDS}
            return g.calendar_restore(None, body, cal, send)
        if not live:
            raise Conflict("the event is gone")
        if etag and now.get("etag") != etag:
            raise Conflict("the event changed since the assistant edited it")
        if kind == "delete_created":
            return g.calendar_delete(eid, cal, send)
        before = p["before"]
        if p.get("from_calendar_id") and p["from_calendar_id"] != cal:
            g.calendar_update(eid, {"move_to_calendar_id": p["from_calendar_id"]}, cal, send)
            cal = p["from_calendar_id"]
        body: dict[str, Any] = {}
        for k in _EVENT_FIELDS:
            if before.get(k) != now.get(k):
                body[k] = before.get(k)
        for k in ("start", "end"):
            if body.get(k):  # null the other form, or a timed event keeps its old all-day date (and vice versa)
                body[k] = {"date": None, "dateTime": None, "timeZone": None, **body[k]}
        if not body:
            return {"id": eid, "verified": True, "verification": {"status": verify.VERIFIED, "what": "nothing to restore"}}
        return g.calendar_restore(eid, body, cal, send)

    def _task(self, kind: str, p: dict[str, Any], updated: str | None) -> dict[str, Any]:
        g, tl, tid = self.google, p["tasklist"], p["task_id"]
        try:
            now = g.tasks_get(tid, tl)
        except Exception as e:  # noqa: BLE001
            if verify.is_missing(e):
                raise Conflict("the task is gone") from e
            raise
        if now.get("deleted"):
            raise Conflict("the task is gone")
        if updated and now.get("updated") != updated:
            raise Conflict("the task changed since the assistant wrote it")
        if kind == "delete_created":
            return g.tasks_delete(tid, tl)
        return g.tasks_update(tid, {"status": p["before"].get("status") or "needsAction"}, tl)

    def prune(self) -> int:
        with self.db.tx() as c:
            return c.execute("DELETE FROM external_undo WHERE created_at < ?", (time.time() - UNDO_TTL,)).rowcount


def router(ex: ExternalUndo) -> Any:
    """The user-only route. There is deliberately no tool that undoes."""
    from fastapi import APIRouter, HTTPException

    r = APIRouter(prefix="/external-undo", tags=["external-undo"])

    @r.post("/{uid}")
    def undo(uid: str) -> dict[str, Any]:
        try:
            return ex.undo(uid)
        except NotFound:
            raise HTTPException(404, "No such undo") from None
        except Conflict as e:
            raise HTTPException(409, {"reason": str(e), "conflict": True}) from None
        except Unavailable as e:
            raise HTTPException(409, {"reason": str(e), "conflict": False}) from None
        except Exception as e:  # noqa: BLE001 - Google refused the inverse; the row stays live
            log.warning("external undo %s failed", uid, exc_info=True)
            raise HTTPException(502, {"reason": f"{type(e).__name__}: {str(e)[:200]}", "conflict": False}) from None

    return r
