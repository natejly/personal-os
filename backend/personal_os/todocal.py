"""One-way mirror of the native todo list onto a Google Calendar.

Every open todo that has a due date becomes an all-day event, so todos show up next to
meetings in Google Calendar (phone included) and in this app's own week grid. Todos are the
source of truth: the mirror creates, patches and deletes events, and never writes back.

Events land on a calendar of their own (created on first run, "Grain Todos" by default)
rather than the primary one, so the whole layer can be hidden with one checkbox in Google
Calendar and removing it never touches a real appointment. Pointing `calendarId` at
`primary` instead is supported.

Per todo, `calendar_sig` (todos.py) records the title/notes/due last mirrored plus the
event's clock time, which lets a pass do three things:
- patch only the fields that actually changed;
- keep a time someone set by hand — dragging a todo onto 2pm in the week grid, or the
  per-todo "add to calendar" button — instead of flattening it back to all-day;
- adopt an event created by those two paths (no sig yet) without rewriting it.

Completing a todo removes its event unless `keepCompleted` is on; clearing a due date or
deleting the todo (via the event tombstone) removes it too. An event deleted by hand in
Google Calendar comes back on the next pass — it is a mirror, not a two-way sync.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from typing import Any, Callable

from .google import Google, GoogleNotConnected
from .todos import Todos

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    # Empty until the first pass resolves (or creates) the calendar named below.
    "calendarId": "",
    "calendarName": "Grain Todos",
    "intervalMinutes": 15,
    # Off: a completed todo's event disappears, so the calendar shows what is still to do.
    "keepCompleted": False,
}
POKE_DEBOUNCE = 2.0


class _TargetGone(Exception):
    """The calendar the mirror writes to no longer exists; carries its id."""


def _is_missing(e: Exception) -> bool:
    s = str(e)
    return "404" in s or "410" in s or "not found" in s.lower() or "deleted" in s.lower()


def _sig(td: dict[str, Any], clock: str | None) -> str:
    """The todo fields this mirror puts on the event, plus the event's time of day."""
    return json.dumps({"title": td["title"], "notes": td["notes"], "due": td["due"], "time": clock},
                      sort_keys=True)


def _read_sig(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _clock_of(event: dict[str, Any]) -> str | None:
    """The local clock time of an event's start, or None when it is all-day."""
    start = event.get("start") or ""
    if event.get("all_day") or len(start) <= 10 or "T" not in start:
        return None
    return start.split("T", 1)[1][:8] or None


def _start_for(due: str, clock: str | None) -> str:
    return f"{due}T{clock}" if clock else due


class TodoCalendarMirror:
    def __init__(self, todos: Todos, google: Google, get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None]):
        self.todos = todos
        self.google = google
        self.get_settings = get_settings
        self.set_settings = set_settings
        self.last_sync: float | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, int] | None = None
        self._lock = threading.Lock()  # manual "sync now" may race the loop
        self._event: asyncio.Event | None = None
        self._loop_ref: asyncio.AbstractEventLoop | None = None
        self._syncing = False
        self._skipped: list[str] = []

    # ---- config / status ----
    def config(self) -> dict[str, Any]:
        stored = self.get_settings().get("googleTodoCalendar") or {}
        return {**DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in DEFAULT_CONFIG}}

    def set_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        cfg = {**self.config(), **{k: v for k, v in patch.items() if k in DEFAULT_CONFIG}}
        # Moving to another calendar re-creates every event there. The old ones are left
        # alone rather than deleted: on `primary` they may be events the user made by hand.
        if "calendarId" in patch and patch["calendarId"] != self.config()["calendarId"]:
            self._forget_all()
        self.set_settings({"googleTodoCalendar": cfg})
        if cfg["enabled"]:
            self.poke()
        return cfg

    def status(self) -> dict[str, Any]:
        return {"config": self.config(), "last_sync": self.last_sync, "last_error": self.last_error,
                "last_result": self.last_result, "syncing": self._syncing}

    # ---- scheduling ----
    def poke(self) -> None:
        """Ask the loop to mirror soon; safe from any thread (todo routes run in a threadpool).

        The loop reference outlives the loop itself if the backend is torn down and rebuilt in
        one process, so a closed loop is ignored rather than raising into the caller's request.
        """
        loop, ev = self._loop_ref, self._event
        if not loop or not ev or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(ev.set)

    async def loop(self) -> None:
        """Periodic pass plus a fast follow-up after local edits. Started once at app startup."""
        self._event = asyncio.Event()
        self._loop_ref = asyncio.get_running_loop()
        while True:
            try:
                interval = max(60.0, float(self.config().get("intervalMinutes") or 15) * 60)
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=interval)
                    await asyncio.sleep(POKE_DEBOUNCE)
                except asyncio.TimeoutError:
                    pass
                self._event.clear()
                if self.config()["enabled"]:
                    await asyncio.to_thread(self.sync_once)
            except asyncio.CancelledError:
                raise
            except GoogleNotConnected:
                pass  # recorded in last_error; nothing to do until the user reconnects
            except Exception as e:  # noqa: BLE001 - the loop must outlive any single failure
                log.warning("todo calendar mirror: %s", e)
                await asyncio.sleep(30)

    def sync_once(self) -> dict[str, int]:
        """One full pass. Blocking; run it off the event loop."""
        with self._lock:
            self._syncing = True
            try:
                self._skipped = []
                try:
                    counts = self._mirror()
                except _TargetGone as gone:
                    # The remembered calendar was deleted in Google, or belongs to an account
                    # that is no longer signed in. Forget it and start over on a fresh one,
                    # once: without this every pass failed the same way until the id was
                    # cleared by hand.
                    cfg = self.config()
                    if str(cfg["calendarId"] or "") != str(gone):
                        raise
                    log.warning("todo calendar mirror: calendar %s is gone, re-creating", gone)
                    self._forget_all()
                    self.set_settings({"googleTodoCalendar": {**cfg, "calendarId": ""}})
                    self._skipped = []
                    counts = self._mirror()
                self.last_sync = time.time()
                # A todo Google refused is reported, but it no longer stops the rest mirroring.
                self.last_error = (f"{len(self._skipped)} todo(s) could not be mirrored: {self._skipped[0]}"
                                   if self._skipped else None)
                self.last_result = counts
                return counts
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e) if isinstance(e, GoogleNotConnected) else f"{type(e).__name__}: {e}"
                raise
            finally:
                self._syncing = False

    # ---- the mirror ----
    def target_calendar(self) -> str:
        """The calendar events go on, creating it on first use and remembering its id."""
        cfg = self.config()
        if cfg["calendarId"]:
            return str(cfg["calendarId"])
        cal = self.google.calendar_ensure(cfg["calendarName"] or DEFAULT_CONFIG["calendarName"])
        self.set_settings({"googleTodoCalendar": {**cfg, "calendarId": cal["id"]}})
        log.info("todo calendar mirror: using calendar %s (%s)", cal["summary"], cal["id"])
        return str(cal["id"])

    def _forget_all(self) -> None:
        """Drop every event link without deleting remotely: used when the target changes."""
        for td in self.todos.all_for_sync():
            if td.get("calendar_event_id"):
                self.todos.set_calendar_state(td["id"], None, None, None, None)

    def _wanted(self, td: dict[str, Any], keep_completed: bool) -> bool:
        """A todo earns a calendar event when it has a date and is still relevant."""
        return bool(td.get("due")) and (not td["done"] or keep_completed)

    def _mirror(self) -> dict[str, int]:
        counts = {"created": 0, "updated": 0, "removed": 0, "adopted": 0}
        cfg = self.config()
        keep_completed = bool(cfg["keepCompleted"])
        rows = self.todos.all_for_sync()
        tombstones = self.todos.event_tombstones()
        # Nothing to mirror and nothing to clean up: do not create a calendar just to look at it.
        if not tombstones and not any(self._wanted(td, keep_completed) or td.get("calendar_event_id") for td in rows):
            return counts

        target = self.target_calendar()

        # Todos deleted here leave the event behind otherwise.
        for ts in tombstones:
            self._delete_event(ts["event_id"], ts.get("calendar_id") or target)
            self.todos.clear_event_tombstone(ts["event_id"])
            counts["removed"] += 1

        for td in rows:
            try:
                outcome = self._mirror_one(td, target, keep_completed)
            except (GoogleNotConnected, _TargetGone):
                raise
            except Exception as e:  # noqa: BLE001 - one refused todo must not block the rest
                log.warning("todo calendar mirror: skipped todo %s: %s", td["id"], e)
                self._skipped.append(f"{(td.get('title') or '(untitled)')[:60]}: {str(e)[:160] or type(e).__name__}")
                continue
            if outcome != "unchanged":
                counts[outcome] += 1
        return counts

    def _mirror_one(self, td: dict[str, Any], target: str, keep_completed: bool) -> str:
        eid = td.get("calendar_event_id")
        cal = td.get("calendar_id") or (target if eid is None else "primary")
        if not self._wanted(td, keep_completed):
            if not eid:
                return "unchanged"
            self._delete_event(eid, cal)
            self.todos.set_calendar_state(td["id"], None, None, None, None)
            return "removed"
        if not eid:
            self._create(td, target)
            return "created"
        sig = _read_sig(td.get("calendar_sig"))
        if sig is None:
            # An event someone made by hand (per-todo button, drag onto the week grid).
            # Record where it stands; do not rewrite what they chose.
            if self._adopt(td, eid, cal):
                return "adopted"
            self._create(td, target)
            return "created"
        return self._update(td, eid, cal, sig, target)

    def _body(self, td: dict[str, Any], clock: str | None) -> dict[str, Any]:
        return {
            "summary": td["title"] or "(untitled)",
            "description": td["notes"] or "",
            "start": _start_for(str(td["due"]), clock),
            # A due-date marker should not make the day look booked.
            "transparency": "transparent",
        }

    def _create(self, td: dict[str, Any], calendar_id: str) -> None:
        try:
            ev = self.google.calendar_create(self._body(td, None), calendar_id)
        except Exception as e:  # noqa: BLE001
            # An insert only 404s when the calendar itself is gone.
            if _is_missing(e):
                raise _TargetGone(calendar_id) from e
            raise
        self.todos.set_calendar_state(td["id"], ev["id"], ev.get("link"), calendar_id, _sig(td, None))

    def _adopt(self, td: dict[str, Any], event_id: str, calendar_id: str) -> bool:
        """Take over an existing event as this todo's mirror. False if it is gone."""
        try:
            ev = self.google.calendar_get(event_id, calendar_id)
        except Exception as e:  # noqa: BLE001
            if _is_missing(e):
                return False
            raise
        if ev.get("status") == "cancelled":
            return False
        # The event's own date wins here: a drag onto Thursday 2pm is what the user meant,
        # even before the due date has caught up.
        self.todos.set_calendar_state(td["id"], event_id, ev.get("link") or td.get("calendar_link"),
                                      calendar_id, _sig(td, _clock_of(ev)))
        return True

    def _update(self, td: dict[str, Any], event_id: str, calendar_id: str, sig: dict[str, Any], target: str) -> str:
        clock = sig.get("time")
        patch: dict[str, Any] = {}
        if td["title"] != sig.get("title"):
            patch["summary"] = td["title"] or "(untitled)"
        if td["notes"] != sig.get("notes"):
            patch["description"] = td["notes"] or ""
        if td["due"] != sig.get("due"):
            patch["start"] = _start_for(str(td["due"]), clock)
        if not patch:
            return "unchanged"
        try:
            ev = self.google.calendar_update(event_id, patch, calendar_id)
        except Exception as e:  # noqa: BLE001
            if not _is_missing(e):
                raise
            # Deleted in Google Calendar: a mirror puts it back.
            self._create(td, target)
            return "created"
        self.todos.set_calendar_state(td["id"], event_id, ev.get("link") or td.get("calendar_link"),
                                      calendar_id, _sig(td, clock))
        return "updated"

    def _delete_event(self, event_id: str, calendar_id: str) -> None:
        try:
            self.google.calendar_delete(event_id, calendar_id)
        except Exception as e:  # noqa: BLE001
            if not _is_missing(e):
                raise
            # Already deleted in Google Calendar; the local link is being dropped anyway.
            log.debug("todo calendar mirror: event %s already gone", event_id)
