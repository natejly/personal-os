"""Calendar as a built-in extension: the Google Calendar routes, the agent's calendar tools, the Today card and recap
facts, and the `calendar` scheduled-job kind (trigger.py)."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import approval_edits, redact, scheduling, verify
from ...jobs import local_tz_name
from ...tools import ToolSpec, _first_line, _obj, page, scrub_strings, tool_error, undoable
from ..google.client import NOT_CONNECTED_FACT, json_safe
from ..google.http import gcall
from .trigger import CalendarTrigger

if TYPE_CHECKING:
    from ..host import ExtensionScope


class AttendeeIn(BaseModel):
    email: str
    optional: bool = False
    # Carried through so a patched guest list does not reset everyone's RSVP.
    response: str | None = None


class ReminderIn(BaseModel):
    method: str = "popup"  # popup | email
    minutes: int = 10


class RemindersIn(BaseModel):
    use_default: bool = False
    overrides: list[ReminderIn] = []


class EventIn(BaseModel):
    """Create/patch body; on PATCH only the fields sent change."""
    summary: str | None = None
    start: str | None = None  # YYYY-MM-DD (all-day) or ISO datetime
    end: str | None = None
    time_zone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[AttendeeIn] | None = None
    recurrence: list[str] | None = None  # RRULE lines; [] clears
    reminders: RemindersIn | None = None
    color_id: str | None = None
    visibility: str | None = None  # default | public | private
    transparency: str | None = None  # opaque (busy) | transparent (free)
    guests_can_invite_others: bool | None = None
    guests_can_modify: bool | None = None
    guests_can_see_other_guests: bool | None = None
    create_meet: bool = False
    clear_meet: bool = False
    calendar_id: str = "primary"
    move_to_calendar_id: str | None = None
    send_updates: str = "none"  # none | all | externalOnly


class RespondIn(BaseModel):
    response: str  # accepted | declined | tentative | needsAction
    calendar_id: str = "primary"
    send_updates: str = "all"


def _event_body_fields(body: EventIn) -> dict[str, Any]:
    f = body.model_dump(exclude_none=True, exclude={"calendar_id", "send_updates"})
    # False is meaningless on a patch (nothing to undo) and harmless on create.
    for k in ("create_meet", "clear_meet"):
        if not f.get(k):
            f.pop(k, None)
    return f


def _validate_changes(args: dict[str, Any]) -> str | None:
    """Hold an edited calendar_propose to the same rules as one the model wrote."""
    try:
        scheduling.validate_changes(args.get("changes"))
    except ValueError as e:
        return str(e)
    return None


class CalendarExtension:
    def __init__(self, api: ExtensionScope) -> None:
        self.api = api
        self.google = api.services.get("google")

    # ---- routes ----
    def router(self) -> APIRouter:
        r = APIRouter()
        google = self.google
        call = functools.partial(gcall, google)

        @r.get("/integrations/google/calendars")
        def google_calendars(refresh: bool = False) -> Any:
            return call(google.calendars, refresh=refresh)

        @r.get("/integrations/google/calendar")
        def google_calendar(days: int = 2, start: str | None = None, calendars: str = "primary", refresh: bool = False) -> Any:
            ids = None if calendars in ("", "primary") else [c.strip() for c in calendars.split(",") if c.strip()]
            return call(google.calendar_events, days, "primary", 0, start, ids, refresh=refresh)

        @r.post("/integrations/google/calendar")
        def google_calendar_create(body: EventIn) -> Any:
            if not (body.summary or "").strip() or not body.start:
                raise HTTPException(400, "An event needs at least a summary and a start.")
            return call(google.calendar_create, _event_body_fields(body), body.calendar_id, body.send_updates)

        # Registered before the {event_id} routes so "colors" is not read as an event id.
        @r.get("/integrations/google/calendar/colors")
        def google_calendar_colors() -> Any:
            return call(google.calendar_colors)

        @r.get("/integrations/google/calendar/{event_id}")
        def google_calendar_get(event_id: str, calendar_id: str = "primary") -> Any:
            return call(google.calendar_get, event_id, calendar_id)

        @r.patch("/integrations/google/calendar/{event_id}")
        def google_calendar_update(event_id: str, body: EventIn) -> Any:
            return call(google.calendar_update, event_id, _event_body_fields(body), body.calendar_id, body.send_updates)

        @r.delete("/integrations/google/calendar/{event_id}")
        def google_calendar_delete(event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> Any:
            return call(google.calendar_delete, event_id, calendar_id, send_updates)

        @r.post("/integrations/google/calendar/{event_id}/respond")
        def google_calendar_respond(event_id: str, body: RespondIn) -> Any:
            return call(google.calendar_respond, event_id, body.response, body.calendar_id, body.send_updates)

        return r

    # ---- agent tools ----
    async def _undoable(self, tool: str, ctx: dict[str, Any], where: dict[str, Any], fn: Callable[..., Any], *a: Any) -> Any:
        return await undoable(self.api.services.get("extundo"), tool, ctx, where, fn, *a)

    def register_tools(self) -> None:
        g = self.google
        run = asyncio.to_thread

        def _event_fields(kw: dict[str, Any]) -> dict[str, Any]:
            """Shared flat-args -> event dict for calendar_create/calendar_update."""
            f = {k: v for k, v in kw.items() if v is not None}
            if "attendees" in f:
                f["attendees"] = [{"email": a} for a in f["attendees"]]
            if "reminder_minutes" in f:
                mins = f.pop("reminder_minutes")
                f["reminders"] = {"use_default": False, "overrides": [{"method": "popup", "minutes": int(m)} for m in mins]}
            if "busy" in f:
                f["transparency"] = "opaque" if f.pop("busy") else "transparent"
            return f

        _EVENT_PROPS = {
            "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "description": {"type": "string"}, "location": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "guest emails"},
            "recurrence": {"type": "array", "items": {"type": "string"}, "description": "RRULE lines, e.g. 'RRULE:FREQ=WEEKLY;BYDAY=MO'; [] removes the recurrence"},
            "reminder_minutes": {"type": "array", "items": {"type": "integer"}, "description": "popup reminders, minutes before start"},
            "color_id": {"type": "string", "description": "Google event color id 1-11"},
            "visibility": {"type": "string", "enum": ["default", "public", "private"]},
            "busy": {"type": "boolean", "description": "false shows the slot as Free"},
            "create_meet": {"type": "boolean", "description": "attach a Google Meet link"},
            "calendar_id": {"type": "string", "default": "primary"},
            "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly"], "description": "email the guests about this change"},
        }

        def _brief_event(e: dict[str, Any]) -> dict[str, Any]:
            """A list row the model can scan: the calendar UI keeps the full row, this drops links and empty fields
            so a week of events stays inline instead of becoming a paged handle the model must spend rounds reading."""
            out = {k: e.get(k) for k in ("id", "summary", "start", "end", "all_day", "location", "calendar_id", "recurring_event_id")}
            for k in ("start", "end"):
                if out[k] and "T" in out[k]:
                    with contextlib.suppress(ValueError):
                        out[k] = datetime.fromisoformat(out[k].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%dT%H:%M")
            if e.get("transparency") == "transparent":
                out["busy"] = False
            if e.get("attendees"):
                out["guests"] = len(e["attendees"])
            if e.get("description"):
                out["description"] = redact.scrub_command_output(str(e["description"]))[:120]
            if isinstance(out.get("calendar_id"), str):
                out["calendar_id"] = redact.scrub_command_output(out["calendar_id"])
            return {k: v for k, v in out.items() if v is not None and v != ""}

        async def calendar_events(ctx: dict[str, Any], days: int = 2, start: str | None = None, offset: int = 0, all_calendars: bool = False,
                                  calendar_id: str | None = None) -> Any:
            if calendar_id:
                ids = [calendar_id]
            else:
                ids = ["all"] if all_calendars else ["enabled"]
            rows = await run(g.calendar_events, days, "primary", 0, start, ids)
            names: dict[str, str] = {}
            if len({e.get("calendar_id") for e in rows}) > 1 or calendar_id:
                with contextlib.suppress(Exception):  # names are a label; the events are still right without them
                    names = {c["id"]: c["summary"] for c in await run(g.calendars)}
            out = []
            for e in rows:
                b = _brief_event(e)
                if names.get(e.get("calendar_id") or ""):
                    b["calendar"] = names[e["calendar_id"]]
                out.append(b)
            return page(scrub_strings(out), offset=offset, limit=30, key="events")
        self.api.tool(ToolSpec("calendar_events", "List Google Calendar events (default: the next 2 days across every calendar the user has checked on in Google, such as work, classes and shared ones). `start` is a local YYYY-MM-DD or YYYY-MM-DDTHH:MM to look from (default now); `days` is the window length. Each event has `calendar` (its name) and `calendar_id`: pass that calendar_id, with the event id, to calendar_get, calendar_update, calendar_delete, calendar_respond or meeting_brief, since event ids belong to one calendar. calendar_id here narrows the list to one calendar; all_calendars also includes hidden and unchecked ones. calendar_get has an event's full details.",
            _obj({"days": {"type": "integer", "default": 2}, "start": {"type": "string"}, "offset": {"type": "integer", "default": 0},
                  "calendar_id": {"type": "string", "description": "only this calendar (an id from a previous result)"},
                  "all_calendars": {"type": "boolean", "default": False, "description": "also hidden and unchecked calendars"}}, []), calendar_events, "google",
            examples=[{}, {"days": 7, "all_calendars": True}, {"days": 1, "start": "2026-10-02T09:00"}], taints=True))

        async def calendar_get(ctx: dict[str, Any], event_id: str, calendar_id: str = "primary") -> Any:
            return scrub_strings(await run(g.calendar_get, event_id, calendar_id))
        self.api.tool(ToolSpec("calendar_get", "One event by id (from calendar_events; pass the calendar_id that row carried): recurrence, reminders, guests and RSVPs, color, visibility.",
            _obj({"event_id": {"type": "string"}, "calendar_id": {"type": "string", "default": "primary"}}, ["event_id"]), calendar_get, "google",
            examples=[{"event_id": "7abc123def"}], taints=True))

        async def meeting_brief(ctx: dict[str, Any], event_id: str, calendar_id: str = "primary") -> Any:
            e = await run(g.calendar_get, event_id, calendar_id)
            guests = [a for a in e.get("attendee_details") or [] if not a.get("self") and a.get("email")]
            people = []
            for a in guests:
                mail = a["email"]
                name = a.get("name") or ""
                terms = [mail] + ([name] if name else [])
                seen: dict[str, str] = {}
                for t in terms:
                    for m in self.api.services.get("memories").list(ctx["project_id"], t)[:5]:
                        seen.setdefault(m["id"], m["content"])
                people.append({"email": mail, "name": name, "notes": list(seen.values())[:5]})
            return scrub_strings({"event": {k: e.get(k) for k in ("summary", "start", "end", "location", "description")},
                                   "people": people})
        self.api.tool(ToolSpec("meeting_brief", "Pre-meeting brief for one calendar event (pass the calendar_id its calendar_events row carried): for each guest, what long-term memory holds about them. Read-only. Use before a meeting, or when asked who someone on the invite is.",
            _obj({"event_id": {"type": "string"}, "calendar_id": {"type": "string", "default": "primary"}}, ["event_id"]), meeting_brief, "google",
            examples=[{"event_id": "7abc123def"}], taints=True))

        async def calendar_create(ctx: dict[str, Any], summary: str, start: str, end: str | None = None, description: str | None = None, location: str | None = None,
                                  attendees: list[str] | None = None, recurrence: list[str] | None = None, reminder_minutes: list[int] | None = None,
                                  color_id: str | None = None, visibility: str | None = None, busy: bool | None = None, create_meet: bool | None = None,
                                  calendar_id: str = "primary", send_updates: str = "none") -> Any:
            f = _event_fields({"summary": summary, "start": start, "end": end, "description": description, "location": location, "attendees": attendees,
                               "recurrence": recurrence, "reminder_minutes": reminder_minutes, "color_id": color_id, "visibility": visibility,
                               "busy": busy, "create_meet": create_meet})
            return scrub_strings(await self._undoable("calendar_create", ctx, {"calendar_id": calendar_id, "send_updates": send_updates},
                                       g.calendar_create, f, calendar_id, send_updates))
        self.api.tool(ToolSpec("calendar_create", "Create ONE Google Calendar event. To schedule or rearrange anything with guests or more than one event, use calendar_find_time then calendar_propose instead: the user reviews the whole change on a calendar. ISO datetimes (YYYY-MM-DDTHH:MM) in the user's local time, or YYYY-MM-DD for all-day. Supports recurrence (RRULE), reminders, guests, Meet links, color and busy/free. send_updates='all' emails the guests their invites.",
            _obj(dict(_EVENT_PROPS), ["summary", "start"]), calendar_create, "google", "external",
            examples=[{"summary": "Dentist", "start": "2026-10-07T15:00", "end": "2026-10-07T16:00", "reminder_minutes": [30]},
                      {"summary": "Sprint review", "start": "2026-10-08T10:00", "attendees": ["mira@example.com"], "location": "Room 2", "create_meet": True, "send_updates": "all"},
                      {"summary": "Standup", "start": "2026-10-05T09:30", "end": "2026-10-05T09:45", "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]}]))

        async def calendar_update(ctx: dict[str, Any], event_id: str, summary: str | None = None, start: str | None = None, end: str | None = None,
                                  description: str | None = None, location: str | None = None, attendees: list[str] | None = None,
                                  recurrence: list[str] | None = None, reminder_minutes: list[int] | None = None, color_id: str | None = None,
                                  visibility: str | None = None, busy: bool | None = None, create_meet: bool | None = None, clear_meet: bool | None = None,
                                  calendar_id: str = "primary", send_updates: str = "none") -> Any:
            f = _event_fields({"summary": summary, "start": start, "end": end, "description": description, "location": location, "attendees": attendees,
                               "recurrence": recurrence, "reminder_minutes": reminder_minutes, "color_id": color_id, "visibility": visibility,
                               "busy": busy, "create_meet": create_meet, "clear_meet": clear_meet})
            return scrub_strings(await self._undoable("calendar_update", ctx, {"event_id": event_id, "calendar_id": calendar_id, "send_updates": send_updates},
                                       g.calendar_update, event_id, f, calendar_id, send_updates))
        self.api.tool(ToolSpec("calendar_update", "Edit ONE Google Calendar event by id (pass the calendar_id its calendar_events row carried, or the edit looks on the primary calendar); for moving several events or rescheduling around conflicts use calendar_propose. Only the fields you pass change. `attendees` replaces the whole guest list. For a recurring event, the instance id edits that occurrence and its recurring_event_id (from calendar_get) edits the series.",
            _obj({"event_id": {"type": "string"}, "clear_meet": {"type": "boolean", "description": "remove the Meet link"}, **_EVENT_PROPS}, ["event_id"]), calendar_update, "google", "external",
            examples=[{"event_id": "7abc123def", "start": "2026-10-07T16:00", "end": "2026-10-07T17:00"},
                      {"event_id": "7abc123def", "summary": "Sprint review (moved)", "send_updates": "all"},
                      {"event_id": "7abc123def", "recurrence": []}]))

        async def calendar_delete(ctx: dict[str, Any], event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> Any:
            return scrub_strings(await self._undoable("calendar_delete", ctx, {"event_id": event_id, "calendar_id": calendar_id, "send_updates": send_updates},
                                       g.calendar_delete, event_id, calendar_id, send_updates))
        self.api.tool(ToolSpec("calendar_delete", "Delete a Google Calendar event by id (pass the calendar_id its calendar_events row carried). For a recurring event, the instance id removes that occurrence and its recurring_event_id removes the whole series. Only when the user asked to delete it.",
            _obj({"event_id": {"type": "string"}, "calendar_id": {"type": "string", "default": "primary"}, "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly"]}}, ["event_id"]), calendar_delete, "google", "external",
            examples=[{"event_id": "7abc123def"}, {"event_id": "7abc123def", "send_updates": "all"}]))

        async def calendar_respond(ctx: dict[str, Any], event_id: str, response: str, calendar_id: str = "primary") -> Any:
            return scrub_strings(await self._undoable("calendar_respond", ctx, {"event_id": event_id, "calendar_id": calendar_id, "send_updates": "all"},
                                       g.calendar_respond, event_id, response, calendar_id, "all"))
        self.api.tool(ToolSpec("calendar_respond", "RSVP to an event the user was invited to (pass the calendar_id its calendar_events row carried): accepted, declined or tentative.",
            _obj({"event_id": {"type": "string"}, "response": {"type": "string", "enum": ["accepted", "declined", "tentative"]}, "calendar_id": {"type": "string", "default": "primary"}}, ["event_id", "response"]), calendar_respond, "google", "external",
            examples=[{"event_id": "7abc123def", "response": "accepted"}]))

        # ---- scheduling: read-only free/busy + slot finding, and one batched, reviewable proposal ----
        def _tz(name: str | None) -> Any:
            if name:
                return scheduling.get_tz(name)
            return scheduling.get_tz(local_tz_name())

        async def calendar_free_busy(ctx: dict[str, Any], time_min: str, time_max: str, calendars: list[str] | None = None,
                                     attendees: list[str] | None = None) -> Any:
            return scrub_strings(await run(g.calendar_free_busy, time_min, time_max, calendars, attendees))
        self.api.tool(ToolSpec("calendar_free_busy", "Busy ranges between two ISO datetimes from Google's free/busy service, across the calendars the user has checked on (or the `calendars` ids you name) plus any `attendees` emails. Use it to see when people are busy; to find a time, use calendar_find_time.",
            _obj({"time_min": {"type": "string"}, "time_max": {"type": "string"}, "calendars": {"type": "array", "items": {"type": "string"}, "description": "calendar ids; default the calendars checked on"},
                  "attendees": {"type": "array", "items": {"type": "string"}, "description": "other people's emails"}}, ["time_min", "time_max"]), calendar_free_busy, "google",
            examples=[{"time_min": "2026-10-05T09:00", "time_max": "2026-10-05T18:00"}, {"time_min": "2026-10-05T00:00", "time_max": "2026-10-09T23:59", "attendees": ["mira@example.com"]}]))

        async def calendar_find_time(ctx: dict[str, Any], duration_minutes: int, window_start: str, window_end: str, attendees: list[str] | None = None,
                                     working_hours: Any = None, buffer_minutes: int = 0, max_results: int = 5, timezone: str | None = None) -> Any:
            try:
                dur, buf, n = int(duration_minutes), int(buffer_minutes or 0), max(1, min(int(max_results or 5), 20))
                tz = _tz(timezone)
                w0 = scheduling.parse_dt(window_start, tz)
                w1 = scheduling.parse_dt(window_end, tz)
                if scheduling.is_date_only(window_end):
                    w1 += dt.timedelta(days=1)
                scheduling.parse_working_hours(working_hours)
                if dur <= 0:
                    raise ValueError("duration_minutes must be positive")
            except (ValueError, TypeError) as e:
                return tool_error(redact.scrub_command_output(f"calendar_find_time: {e}"),
                                  expected="ISO datetimes or dates for window_start/window_end, working_hours like '9-18'",
                                  example={"duration_minutes": 30, "window_start": "2026-10-05", "window_end": "2026-10-09"})
            now = dt.datetime.now(dt.timezone.utc)
            w0 = max(w0, now)
            if w1 <= w0:
                return tool_error("calendar_find_time: the window is already over.", field="window_end", expected="a window that ends in the future")
            if w1 - w0 > dt.timedelta(days=60):
                w1 = w0 + dt.timedelta(days=60)
            days = max(1, -(-int((w1 - w0).total_seconds()) // 86400))
            events = await run(g.calendar_events, days, "primary", 250, w0.isoformat(), ["enabled"])
            busy = scheduling.busy_from_events(events, tz)
            unreachable: list[str] = []
            if attendees:
                fb = await run(g.calendar_free_busy, w0.isoformat(), w1.isoformat(), ["primary"], list(attendees))
                for row in (fb.get("calendars") or {}).values():
                    if row.get("attendee"):
                        busy = scheduling.merge([*busy, *scheduling.busy_from_ranges(row.get("busy") or [], tz)])
                unreachable = list(fb.get("unreachable") or [])
            slots = scheduling.find_slots(dur, w0.isoformat(), w1.isoformat(), busy, tz=tz, working_hours=working_hours, buffer_minutes=buf,
                                          max_results=n, now=now)
            out: dict[str, Any] = {"slots": slots, "timezone": getattr(tz, "key", "UTC"), "duration_minutes": dur, "count": len(slots),
                                   "window": {"start": scheduling.iso_local(w0, tz), "end": scheduling.iso_local(w1, tz)}}
            if attendees:
                out["attendees"] = list(attendees)
            note = ""
            if unreachable:
                out["unreachable"] = unreachable
                note = "Could not see the calendar of: " + ", ".join(unreachable) + ". Their availability is NOT reflected in these slots; say so. "
            if not slots:
                note += "No free slot fits; widen the window, shorten the meeting or loosen working_hours."
            if note.strip():
                out["note"] = note.strip()
            return scrub_strings(out)
        self.api.tool(ToolSpec("calendar_find_time", "Find free slots for a meeting: ranked candidates inside working hours (default 9-18 local, weekdays) that avoid the user's events on every calendar they have checked on (declined and 'free' events do not block) and, when given, the attendees' busy time. ALWAYS use this before proposing a time. Then call calendar_propose with the chosen slot.",
            _obj({"duration_minutes": {"type": "integer"}, "window_start": {"type": "string", "description": "ISO datetime or date to search from"},
                  "window_end": {"type": "string", "description": "ISO datetime, or a date meaning through the end of that day"},
                  "attendees": {"type": "array", "items": {"type": "string"}, "description": "emails whose busy time must also be free"},
                  "working_hours": {"type": "string", "description": "e.g. '9-18' or '10:00-16:30'; default 9-18"},
                  "buffer_minutes": {"type": "integer", "default": 0, "description": "clear gap to keep around every other event"},
                  "max_results": {"type": "integer", "default": 5}, "timezone": {"type": "string", "description": "IANA zone; default the user's own"}},
                 ["duration_minutes", "window_start", "window_end"]), calendar_find_time, "google",
            examples=[{"duration_minutes": 30, "window_start": "2026-10-05", "window_end": "2026-10-09"},
                      {"duration_minutes": 60, "window_start": "2026-10-05T09:00", "window_end": "2026-10-07", "attendees": ["mira@example.com"], "buffer_minutes": 10, "max_results": 3}]))

        async def _propose_one(i: int, c: dict[str, Any]) -> dict[str, Any]:
            row: dict[str, Any] = {"i": i, "op": c["op"], "ok": False}
            cal, upd = c["calendar_id"], c.get("send_updates", "none")
            try:
                if c["op"] == "create":
                    res = await run(g.calendar_create, scheduling.change_fields(c), cal, upd)
                elif c["op"] == "update":
                    res = await run(g.calendar_update, c["event_id"], scheduling.change_fields(c), cal, upd)
                else:
                    res = await run(g.calendar_delete, c["event_id"], cal, upd)
            except Exception as e:  # noqa: BLE001 - one failed change must not stop the rest, or hide itself
                if type(e).__name__ == "GoogleNotConnected":
                    raise
                self.api.log.warning("calendar_propose change %s failed", i, exc_info=True)
                row["err"] = f"{type(e).__name__}: {_first_line(e)}"[:160]
                return row
            v = res.get("verification") if isinstance(res, dict) else None
            row["v"] = (v or {}).get("status") or "unchecked"
            row["ok"] = bool(v) and verify.ok(v)
            if not row["ok"]:
                row["err"] = (verify.tool_error_text("calendar_" + c["op"], v) if v else "no read-back")[:160]
            if c["op"] == "delete":
                row["id"] = c["event_id"]
            else:
                row.update({"id": res.get("id"), "link": res.get("link"), "s": res.get("summary"), "a": res.get("start"), "b": res.get("end")})
            row["cal"] = cal
            return {k: val for k, val in row.items() if val is not None}

        async def calendar_propose(ctx: dict[str, Any], changes: list[dict[str, Any]], note: str | None = None) -> Any:
            """Execute a reviewed batch. By the time this runs the user approved it (possibly after editing),
            so `changes` is whatever they approved; it is validated again here, never trusted."""
            try:
                todo = scheduling.validate_changes(changes, _tz(None))
            except ValueError as e:
                return tool_error(redact.scrub_command_output(f"calendar_propose: {e}"), field="changes",
                                  expected="a list of {op: create|update|delete, ...} objects",
                                  example={"changes": [{"op": "create", "summary": "Sync", "start": "2026-10-07T15:00", "end": "2026-10-07T15:30"}]})
            rows = [await _propose_one(i, c) for i, c in enumerate(todo)]
            for row in rows:
                for key in ("s", "link", "err"):
                    if isinstance(row.get(key), str):
                        row[key] = redact.scrub_command_output(row[key])
            done = sum(1 for r in rows if r["ok"])
            failed = len(rows) - done
            out: dict[str, Any] = {"ok": failed == 0, "applied": done, "failed": failed, "total": len(rows), "results": rows}
            if note:
                out["note"] = redact.scrub_command_output(str(note)[:200])
            if failed:
                bad = [f"#{r['i'] + 1} {r['op']}: {r.get('err') or r.get('v') or 'failed'}" for r in rows if not r["ok"]]
                out["error"] = redact.scrub_command_output(
                    f"calendar_propose: {done} of {len(rows)} changes made; {failed} did not complete ({'; '.join(bad)[:400]}). "
                    "Report exactly which succeeded and which did not.")
                out["try_instead"] = ("do NOT repeat the changes that succeeded; a change marked unverified may still have landed, "
                                      "so ask the user to check Google Calendar before retrying it")
            return out
        self.api.tool(ToolSpec("calendar_propose", "Propose a batch of calendar changes the user reviews as a whole on a calendar view, can edit or switch off per change, and approves once. Prefer this over calendar_create/update/delete whenever you schedule or rearrange (use calendar_find_time first for the times). Each change: {op: create|update|delete, event_id (update/delete), calendar_id, summary, start, end, attendees, location, description, recurrence, conference (true adds a Meet link), send_updates}. Nothing happens until the user approves; then each change is made and read back, with a per-change outcome.",
            _obj({"changes": {"type": "array", "minItems": 1, "maxItems": scheduling.MAX_CHANGES, "items": {"type": "object", "properties": {
                      "op": {"type": "string", "enum": ["create", "update", "delete"]}, "event_id": {"type": "string"}, "calendar_id": {"type": "string", "default": "primary"},
                      "summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                      "attendees": {"type": "array", "items": {"type": "string"}}, "location": {"type": "string"}, "description": {"type": "string"},
                      "recurrence": {"type": "array", "items": {"type": "string"}}, "conference": {"type": "boolean"},
                      "send_updates": {"type": "string", "enum": ["none", "all", "externalOnly"]}}, "required": ["op"]}},
                  "note": {"type": "string", "description": "one line on why, shown above the proposal"}}, ["changes"]), calendar_propose, "google", "external",
            examples=[{"changes": [{"op": "create", "summary": "Design sync", "start": "2026-10-07T15:00", "end": "2026-10-07T15:30", "attendees": ["mira@example.com"], "conference": True}],
                       "note": "Both of you are free then"},
                      {"changes": [{"op": "update", "event_id": "7abc123def", "start": "2026-10-08T10:00", "end": "2026-10-08T11:00"}, {"op": "delete", "event_id": "9xyz"}]}]))

    # ---- recap section ----
    def card(self) -> dict[str, Any]:
        """Today's calendar card: the next 48 hours on every calendar checked on in Google. None until Google is connected,
        and when the read fails (the log has why)."""
        if not self.google.status()["connected"]:
            return {"calendar": None}
        try:
            ids = self.google.enabled_calendar_ids()
            return {"calendar": json_safe(self.google.calendar_events(2, calendar_ids=ids or None))}
        except Exception:  # noqa: BLE001 - the card is a convenience; the dashboard still answers
            self.api.log.info("the calendar card could not read Google", exc_info=True)
            return {"calendar": None}

    def facts(self) -> dict[str, Any]:
        """The next three days for the recap model. Team and shared calendars hold most of the day: read every visible one,
        as the agent tool does. A source Grain cannot see says so instead of reading as an empty day."""
        if not self.google.status()["connected"]:
            return {"calendar_next_3_days": NOT_CONNECTED_FACT}
        try:
            return {"calendar_next_3_days": json_safe(self.google.calendar_events(3, calendar_ids=["all"]))}
        except Exception as e:  # noqa: BLE001
            return {"calendar_next_3_days": f"unavailable: {e}"}


def activate(api: ExtensionScope) -> None:
    ext = CalendarExtension(api)
    api.router(ext.router())
    ext.register_tools()
    approval_edits.register_validator("calendar_propose", _validate_changes)
    api.trigger("calendar", CalendarTrigger(ext.google))
    api.recap_section("calendar", card=ext.card, facts=ext.facts)
