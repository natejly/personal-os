"""Outlook Calendar over Microsoft Graph, returning the same dicts google.py's calendar methods do.

`CalendarMixin` is mixed into the Microsoft client. It relies on four things from the host class:
`_req(method, path, params=, json=, headers=)` (Graph HTTP, raises an error carrying `.status`),
`me` (the `/me` dict), `_tz()` (the user's IANA zone) and a `_cache` TTLCache.

Every write is read back by id and compared through verify.py, so the result carries
`verified`/`verification` exactly as Google's does. Graph has no event-id-on-create, no tombstones
for deleted events and no per-call "notify guests" switch (it always mails attendees when a meeting
is created, changed or cancelled), so `send_updates` is accepted for signature parity and only
steers RSVP replies.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import html
import re
import zoneinfo
from typing import Any, Callable
from urllib.parse import quote

from . import verify
from .cache import cached, invalidates
from .google import _parse_iso

_TTL = {"list": 10 * 60, "colors": 24 * 3600, "events": 60, "event": 30}

# Calendar colours Graph reports as an enum when a calendar has no hexColor.
_CALENDAR_COLORS = {
    "lightBlue": "#69AFE5", "lightGreen": "#6CC24A", "lightOrange": "#FF9E5E", "lightGray": "#A6A8AB",
    "lightYellow": "#F4D45D", "lightTeal": "#5DCCC4", "lightPink": "#F58FB7", "lightBrown": "#B88A6B",
    "lightRed": "#F26B6B", "auto": "#0078D4",
}

_DOW = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_GRAPH_DOW = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_INDEX = {"first": "1", "second": "2", "third": "3", "fourth": "4", "last": "-1"}
_INDEX_REV = {v: k for k, v in _INDEX.items()}
_RRULE_PARTS = {"FREQ", "INTERVAL", "BYDAY", "COUNT", "UNTIL", "BYMONTHDAY", "BYMONTH"}

# Graph write-body key -> the flat key the read-back compares (mirrors google._EVENT_VERIFY_FIELDS).
_VERIFY_FIELDS = {"subject": "summary", "location": "location", "body": "description",
                  "start": "start", "end": "end", "attendees": "attendees", "recurrence": "recurrence"}
_RESPOND = {"accepted": "accept", "declined": "decline", "tentative": "tentativelyAccept"}
# Graph response enum -> Google's responseStatus words.
_RESPONSE = {"accepted": "accepted", "declined": "declined", "tentativelyAccepted": "tentative",
             "notResponded": "needsAction", "organizer": "accepted"}


def _missing(e: BaseException) -> bool:
    return getattr(e, "status", None) in (404, 410)


def _day(d: str) -> str:
    return d[:10]


def _local_dt(spec: dict[str, Any] | None, all_day: bool) -> str | None:
    """A Graph {dateTime, timeZone} as Google would show it: a date for all-day, else ISO with offset."""
    raw = ((spec or {}).get("dateTime") or "").rstrip("Z")
    if not raw:
        return None
    if all_day:
        return _day(raw)
    naive = dt.datetime.fromisoformat(raw.split(".")[0])
    try:
        tz = zoneinfo.ZoneInfo((spec or {}).get("timeZone") or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        tz = zoneinfo.ZoneInfo("UTC")
    return naive.replace(tzinfo=tz).isoformat()


def _text(body: dict[str, Any] | None) -> str:
    content = (body or {}).get("content") or ""
    if (body or {}).get("contentType") == "html":
        content = re.sub(r"(?is)<(style|head|script).*?</\1>", "", content)
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", content)
        content = html.unescape(re.sub(r"<[^>]+>", "", content))
        content = re.sub(r"\n\s*\n+", "\n", content)
    return content.strip()


def _rrule_from_graph(rec: dict[str, Any] | None) -> list[str] | None:
    p, r = (rec or {}).get("pattern") or {}, (rec or {}).get("range") or {}
    typ = p.get("type") or ""
    freq = {"daily": "DAILY", "weekly": "WEEKLY", "absoluteMonthly": "MONTHLY", "relativeMonthly": "MONTHLY",
            "absoluteYearly": "YEARLY", "relativeYearly": "YEARLY"}.get(typ)
    if not freq:
        return None
    parts = [f"FREQ={freq}"]
    if int(p.get("interval") or 1) > 1:
        parts.append(f"INTERVAL={p['interval']}")
    days = [_DOW[_GRAPH_DOW.index(d)] for d in p.get("daysOfWeek") or [] if d in _GRAPH_DOW]
    if typ.startswith("relative"):
        parts.append("BYDAY=" + ",".join(f"{_INDEX.get(p.get('index'), '1')}{d}" for d in days))
    elif days:
        parts.append("BYDAY=" + ",".join(days))
    if typ == "absoluteMonthly":
        parts.append(f"BYMONTHDAY={p.get('dayOfMonth')}")
    if typ.endswith("Yearly"):
        parts.append(f"BYMONTH={p.get('month')}")
        if typ == "absoluteYearly":
            parts.append(f"BYMONTHDAY={p.get('dayOfMonth')}")
    if r.get("type") == "numbered":
        parts.append(f"COUNT={r.get('numberOfOccurrences')}")
    elif r.get("type") == "endDate" and r.get("endDate"):
        parts.append("UNTIL=" + str(r["endDate"]).replace("-", ""))
    return ["RRULE:" + ";".join(parts)]


def _recurrence(rules: list[str], start_date: str) -> dict[str, Any] | None:
    """RRULE strings -> a Graph recurrence. ValueError names the part Graph cannot express."""
    rules = [r.strip() for r in rules if r and r.strip()]
    if not rules:
        return None
    if len(rules) > 1:
        raise ValueError("only a single RRULE line is supported for Outlook recurrence")
    line = rules[0]
    head, _, rest = line.partition(":")
    if rest:
        if head.upper() != "RRULE":
            raise ValueError(f"unsupported recurrence line {head.upper()!r}: only RRULE is supported")
        line = rest
    parts = {k.upper(): v for k, _, v in (p.partition("=") for p in line.split(";") if p)}
    bad = sorted(set(parts) - _RRULE_PARTS)
    if bad:
        raise ValueError(f"unsupported RRULE part {bad[0]!r}: Outlook recurrence supports {', '.join(sorted(_RRULE_PARTS))}")
    freq, interval = parts.get("FREQ", "").upper(), int(parts.get("INTERVAL") or 1)
    byday = [m for m in (re.fullmatch(r"([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)", x.strip().upper()) for x in parts.get("BYDAY", "").split(",") if x.strip())]
    if len(byday) != len([x for x in parts.get("BYDAY", "").split(",") if x.strip()]):
        raise ValueError(f"unsupported RRULE part 'BYDAY={parts['BYDAY']}'")
    d = dt.date.fromisoformat(start_date)
    pattern: dict[str, Any] = {"interval": interval}
    if freq in ("DAILY", "WEEKLY") and ({"BYMONTHDAY", "BYMONTH"} & set(parts)):
        raise ValueError("unsupported RRULE part 'BYMONTHDAY/BYMONTH' outside a MONTHLY or YEARLY rule")
    if freq == "MONTHLY" and "BYMONTH" in parts:
        raise ValueError("unsupported RRULE part 'BYMONTH' on a MONTHLY rule")
    if freq == "DAILY":
        if byday:
            raise ValueError("unsupported RRULE part 'BYDAY' on a DAILY rule")
        pattern["type"] = "daily"
    elif freq == "WEEKLY":
        if any(m.group(1) for m in byday):
            raise ValueError("unsupported RRULE part 'BYDAY' with an ordinal on a WEEKLY rule")
        pattern.update(type="weekly", daysOfWeek=[_GRAPH_DOW[_DOW.index(m.group(2))] for m in byday] or [_GRAPH_DOW[d.weekday()]])
    elif freq in ("MONTHLY", "YEARLY"):
        kind = freq.lower()
        if byday:
            ords = {m.group(1) for m in byday}
            if len(ords) != 1 or None in ords or next(iter(ords)).lstrip("+") not in _INDEX_REV:
                raise ValueError("unsupported RRULE part 'BYDAY': a monthly/yearly BYDAY needs one ordinal such as 2TU or -1FR")
            pattern.update(type=f"relative{kind.capitalize()}", index=_INDEX_REV[next(iter(ords)).lstrip("+")],
                           daysOfWeek=[_GRAPH_DOW[_DOW.index(m.group(2))] for m in byday])
        else:
            pattern.update(type=f"absolute{kind.capitalize()}", dayOfMonth=int(parts.get("BYMONTHDAY") or d.day))
        if freq == "YEARLY":
            pattern["month"] = int(parts.get("BYMONTH") or d.month)
    else:
        raise ValueError(f"unsupported RRULE part 'FREQ={freq}': Outlook recurrence supports DAILY, WEEKLY, MONTHLY and YEARLY")
    if "COUNT" in parts and "UNTIL" in parts:
        raise ValueError("unsupported RRULE: COUNT and UNTIL together")
    rng: dict[str, Any] = {"type": "noEnd", "startDate": start_date}
    if "COUNT" in parts:
        rng.update(type="numbered", numberOfOccurrences=int(parts["COUNT"]))
    elif "UNTIL" in parts:
        u = parts["UNTIL"][:8]
        rng.update(type="endDate", endDate=f"{u[:4]}-{u[4:6]}-{u[6:8]}")
    return {"pattern": pattern, "range": rng}


def _cal(calendar_id: str | None) -> str:
    """Path prefix of a calendar: the default calendar hangs off /me directly."""
    return "/me" if calendar_id in (None, "", "primary") else f"/me/calendars/{quote(calendar_id, safe='')}"


def _eid(event_id: str) -> str:
    return f"/me/events/{quote(event_id, safe='')}"


class CalendarMixin:
    # ---------- helpers ----------
    def _prefer(self) -> dict[str, str]:
        return {"Prefer": f'outlook.timezone="{self._tz()}"'}  # type: ignore[attr-defined]

    def _my_address(self) -> str:
        me = getattr(self, "me", None) or {}
        return str(me.get("mail") or me.get("userPrincipalName") or "").lower()

    def _event_out(self, e: dict[str, Any], calendar_id: str | None = None, full: bool = False) -> dict[str, Any]:
        """Flatten a Graph event into Google's flat event dict (same keys as google._event_out)."""
        all_day = bool(e.get("isAllDay"))
        atts = e.get("attendees") or []
        desc = _text(e.get("body")) or e.get("bodyPreview") or ""
        resp = (e.get("responseStatus") or {}).get("response")
        mine = _RESPONSE.get(resp or "") if atts else None
        out = {
            "id": e.get("id"), "calendar_id": calendar_id,
            "summary": e.get("subject") or "(no title)",
            "start": _local_dt(e.get("start"), all_day), "end": _local_dt(e.get("end"), all_day),
            "all_day": all_day,
            "location": (e.get("location") or {}).get("displayName") or None, "link": e.get("webLink"),
            "attendees": [(a.get("emailAddress") or {}).get("address") for a in atts][:10],
            "description": desc if full else desc[:400],
            "meet": (e.get("onlineMeeting") or {}).get("joinUrl") or "",
            "color_id": None,  # Outlook colours events by category, not by a per-event id
            "recurring_event_id": e.get("seriesMasterId"),
            "transparency": "transparent" if e.get("showAs") == "free" else "opaque",
            "status": "cancelled" if e.get("isCancelled") else "confirmed",
            "self_response": mine,
            "my_response": mine,
        }
        if full:
            org = ((e.get("organizer") or {}).get("emailAddress") or {}).get("address")
            me = self._my_address()
            out.update({
                "time_zone": (e.get("start") or {}).get("timeZone"),
                "recurrence": _rrule_from_graph(e.get("recurrence")),
                "visibility": {"normal": "default", "personal": "private", "private": "private",
                               "confidential": "confidential"}.get(e.get("sensitivity") or "normal", "default"),
                "reminders": {"useDefault": False, "overrides": (
                    [{"method": "popup", "minutes": e.get("reminderMinutesBeforeStart") or 0}] if e.get("isReminderOn") else [])},
                "organizer": org,
                "attendee_details": [
                    {"email": (a.get("emailAddress") or {}).get("address"), "name": (a.get("emailAddress") or {}).get("name") or "",
                     "optional": a.get("type") == "optional",
                     "response": _RESPONSE.get((a.get("status") or {}).get("response") or "", "needsAction"),
                     "organizer": bool(org) and ((a.get("emailAddress") or {}).get("address") or "").lower() == org.lower(),
                     "self": bool(me) and ((a.get("emailAddress") or {}).get("address") or "").lower() == me}
                    for a in atts
                ][:60],
                # Graph has no per-event guest permissions; these are Google's defaults.
                "guests_can_invite_others": True, "guests_can_modify": False, "guests_can_see_other_guests": True,
            })
        return out

    def _event_body(self, f: dict[str, Any], patch: bool = False, start_date: str | None = None) -> dict[str, Any]:
        """Translate the flat event fields (google._event_body's input) into a Graph event body.

        `id` and `status` are not carried: Graph assigns ids itself and has no way to un-cancel.
        `color_id`, `guests_can_*` and `send_updates` have no Graph equivalent and are dropped.
        """
        body: dict[str, Any] = {}
        if f.get("summary") is not None:
            body["subject"] = f["summary"]
        if f.get("description") is not None:
            body["body"] = {"contentType": "text", "content": f["description"]}
        if f.get("location") is not None:
            body["location"] = {"displayName": f["location"]}
        if f.get("visibility") is not None:
            body["sensitivity"] = {"private": "private", "confidential": "confidential"}.get(f["visibility"], "normal")
        if f.get("transparency") is not None:
            body["showAs"] = "free" if f["transparency"] == "transparent" else "busy"
        if f.get("create_meet") or f.get("conference") is True:
            body["isOnlineMeeting"] = True
        elif f.get("clear_meet"):
            body["isOnlineMeeting"] = False
        if f.get("start"):
            start, end = str(f["start"]), f.get("end")
            all_day = len(start) == 10
            zone = f.get("time_zone") or self._tz()  # type: ignore[attr-defined]
            if not end:
                end = (dt.date.fromisoformat(start) + dt.timedelta(days=1)).isoformat() if all_day else (_parse_iso(start) + dt.timedelta(hours=1)).isoformat()
            elif all_day and end == start:
                end = (dt.date.fromisoformat(start) + dt.timedelta(days=1)).isoformat()

            def spec(v: str) -> dict[str, str]:
                if all_day:
                    return {"dateTime": f"{v[:10]}T00:00:00", "timeZone": zone}
                d = _parse_iso(v)
                if d.tzinfo is not None:  # an instant: write it as wall-clock time in the requested zone (UTC when none)
                    d = d.astimezone(zoneinfo.ZoneInfo(f["time_zone"]) if f.get("time_zone") else dt.timezone.utc)
                    return {"dateTime": d.replace(tzinfo=None).isoformat(timespec="seconds"), "timeZone": f.get("time_zone") or "UTC"}
                return {"dateTime": d.isoformat(timespec="seconds"), "timeZone": zone}

            body["start"], body["end"] = spec(start), spec(str(end))
            if all_day or patch:
                body["isAllDay"] = all_day
        if f.get("attendees") is not None:
            # Graph replaces the whole list; RSVP state is the server's, so `response` is not sent.
            body["attendees"] = [
                {"emailAddress": {"address": a["email"].strip(), **({"name": a["name"]} if a.get("name") else {})},
                 "type": "optional" if a.get("optional") else "required"}
                for a in ({"email": a} if isinstance(a, str) else a for a in f["attendees"])
                if (a.get("email") or "").strip()
            ]
        if f.get("reminders") is not None:
            r = f["reminders"]
            if r.get("use_default", r.get("useDefault", False)):
                body["isReminderOn"] = True
            else:
                # Graph holds one reminder per event; the first override wins.
                first = next(iter(r.get("overrides") or []), None)
                body["isReminderOn"] = bool(first)
                if first:
                    body["reminderMinutesBeforeStart"] = max(0, min(int(first.get("minutes", 10)), 40320))
        if f.get("recurrence") is not None:
            # [] clears the recurrence (a series becomes a single event).
            body["recurrence"] = _recurrence(f["recurrence"], start_date or str(f.get("start") or "")[:10]) if f["recurrence"] else None
        return body

    # ---------- calendars ----------
    @cached("calendar", _TTL["list"])
    def calendars(self) -> list[dict[str, Any]]:
        """The user's calendars: default first, then the ones they can write to."""
        res = self._req("GET", "/me/calendars", params={"$top": 100}) or {}  # type: ignore[attr-defined]
        out = []
        for c in res.get("value", []):
            out.append({
                "id": c["id"],
                "summary": c.get("name") or c["id"],
                "primary": bool(c.get("isDefaultCalendar")),
                "access_role": "owner" if c.get("canEdit") else "reader",
                "color": c.get("hexColor") or _CALENDAR_COLORS.get(c.get("color") or "auto"),
                "time_zone": None,  # Graph does not report a per-calendar zone
                "hidden": False,
                "selected": True,
            })
        return sorted(out, key=lambda c: (not c["primary"], c["access_role"] not in ("owner", "writer"), c["summary"].lower()))

    def enabled_calendar_ids(self) -> list[str]:
        """Calendars shown: not hidden, and selected or the default one."""
        return [c["id"] for c in self.calendars() if c.get("id") and not c.get("hidden") and (c.get("selected") or c.get("primary"))]

    @invalidates("calendar")
    def calendar_ensure(self, summary: str) -> dict[str, Any]:
        """Find, or create, a secondary calendar of this name that we can write to."""
        want = summary.strip().lower()
        for c in self.calendars():
            if c["summary"].strip().lower() == want and c["access_role"] in ("owner", "writer"):
                return {"id": c["id"], "summary": c["summary"], "created": False}
        cal = self._req("POST", "/me/calendars", json={"name": summary}) or {}  # type: ignore[attr-defined]
        return {"id": cal["id"], "summary": cal.get("name") or summary, "created": True}

    @cached("calendar", _TTL["colors"])
    def calendar_colors(self) -> dict[str, Any]:
        """Calendar colours id -> hex. Outlook colours events by category rather than by id, so `event` is empty
        (an empty palette hides the editor's colour picker instead of offering one that does nothing)."""
        return {"event": {}, "calendar": dict(_CALENDAR_COLORS)}

    # ---------- reads ----------
    def _view(self, cid: str, start: dt.datetime, end: dt.datetime, max_results: int) -> list[dict[str, Any]]:
        params: dict[str, Any] | None = {
            "startDateTime": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$orderby": "start/dateTime", "$top": min(max(max_results, 250), 1000),
        }
        path, rows = f"{_cal(cid)}/calendarView", []
        for _ in range(5):  # ponytail: five pages (5000 events) per calendar per window
            res = self._req("GET", path, params=params, headers=self._prefer()) or {}  # type: ignore[attr-defined]
            rows += res.get("value", [])
            nxt = res.get("@odata.nextLink")
            if not nxt:
                break
            path, params = nxt.split("/v1.0", 1)[-1], None
        return [self._event_out(e, cid) for e in rows if not e.get("isCancelled")]

    @cached("calendar", _TTL["events"])
    def calendar_events(self, days: int = 2, calendar_id: str = "primary", max_results: int = 30, start: str | None = None,
                        calendar_ids: list[str] | None = None) -> list[dict[str, Any]]:
        now = _parse_iso(start) if start else dt.datetime.now(dt.timezone.utc)
        if now.tzinfo is None:
            now = now.astimezone()  # a naive start is the user's wall clock, same as calendar_create reads it
        end = now + dt.timedelta(days=max(1, min(int(days), 60)))
        ids = calendar_ids or [calendar_id]
        if ids == ["all"]:
            ids = self.enabled_calendar_ids()[:15]
        out: list[dict[str, Any]] = []
        failures: list[Exception] = []
        for cid in ids:
            try:
                out.extend(self._view(cid, now, end, max_results))
            except Exception as e:  # noqa: BLE001  # one broken calendar should not empty the whole grid
                if len(ids) == 1:
                    raise
                failures.append(e)
        if failures and len(failures) == len(ids):
            raise failures[0]  # every calendar failed: an empty grid would read as "no events"
        out.sort(key=lambda e: e["start"] or "")
        if max_results <= 0:
            return out
        return out[: max_results if len(ids) == 1 else max_results * 2]

    @cached("calendar", _TTL["events"])
    def calendar_free_busy(self, time_min: str, time_max: str, calendars: list[str] | None = None,
                           attendees: list[str] | None = None) -> dict[str, Any]:
        """Busy ranges from Graph getSchedule.

        getSchedule answers per mailbox address, not per calendar: the user's own mailbox is one entry
        (keyed by the first calendar id given, "primary" by default) and each attendee address is its own.
        Addresses Graph cannot see come back under `unreachable`.
        """
        a, b = _parse_iso(time_min), _parse_iso(time_max)
        a = a.astimezone() if a.tzinfo is None else a
        b = b.astimezone() if b.tzinfo is None else b
        ids = [c for c in (calendars or []) if c and c != "all"] or ["primary"]
        me = self._my_address()
        people = [p for p in (attendees or []) if p and p not in ids and p.lower() != me]

        def utc(d: dt.datetime) -> dict[str, str]:
            return {"dateTime": d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}

        res = self._req("POST", "/me/calendar/getSchedule", json={  # type: ignore[attr-defined]
            "schedules": [me, *people], "startTime": utc(a), "endTime": utc(b), "availabilityViewInterval": 15}) or {}
        out: dict[str, Any] = {}
        unreachable: list[str] = []
        for row in res.get("value", []):
            sid = str(row.get("scheduleId") or "")
            key = ids[0] if sid.lower() == me else next((p for p in people if p.lower() == sid.lower()), sid)
            err = row.get("error")
            if err:
                unreachable.append(key)
            busy = [{"start": _local_dt(x.get("start"), False), "end": _local_dt(x.get("end"), False)}
                    for x in row.get("scheduleItems") or [] if x.get("status") != "free"]
            out[key] = {"busy": busy, **({"errors": [err.get("responseCode") or err.get("message")]} if err else {}),
                        "attendee": key in people}
        return {"time_min": a.isoformat(), "time_max": b.isoformat(), "calendars": out, "unreachable": unreachable}

    def calendar_saved(self, days: int) -> list[dict[str, Any]] | None:
        """No saved snapshot for Microsoft yet: None, which callers treat as "nothing ever saved"."""
        return None

    @cached("calendar", _TTL["event"])
    def calendar_get(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        e = self._req("GET", _eid(event_id), headers=self._prefer()) or {}  # type: ignore[attr-defined]
        return self._event_out(e, calendar_id, full=True)

    def calendar_raw(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any] | None:
        """The event as stored, uncached, in the field names extundo.py snapshots and restores
        (summary, start{dateTime,timeZone}, attendees[...], etag ...); None when Outlook no longer has it.
        A cancelled meeting reports status "cancelled"."""
        try:
            e = self._req("GET", _eid(event_id), headers=self._prefer()) or {}  # type: ignore[attr-defined]
        except Exception as err:  # noqa: BLE001
            if _missing(err):
                return None
            raise
        f = self._event_out(e, calendar_id, full=True)

        def when(k: str) -> dict[str, Any]:
            return {"date": f[k]} if f["all_day"] else {"dateTime": f[k], "timeZone": (e.get(k) or {}).get("timeZone")}

        return {
            "id": e.get("id"), "etag": e.get("changeKey"), "status": f["status"],
            "summary": f["summary"], "description": f["description"], "location": f["location"],
            "start": when("start"), "end": when("end"),
            "attendees": [{"email": d["email"], "optional": d["optional"], "responseStatus": d["response"]} for d in f["attendee_details"]],
            "recurrence": f["recurrence"], "reminders": f["reminders"], "visibility": f["visibility"],
            "transparency": f["transparency"], "colorId": None,
            "conferenceData": {"joinUrl": f["meet"]} if e.get("isOnlineMeeting") else None,
        }

    # ---------- writes ----------
    @invalidates("calendar")
    def calendar_create(self, event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        # Graph mails the attendees of a new meeting on its own; `send_updates` cannot switch that off.
        body = self._event_body(event)
        e = self._req("POST", f"{_cal(calendar_id)}/events", json=body, headers=self._prefer()) or {}  # type: ignore[attr-defined]
        out = self._event_out(e, calendar_id, full=True)
        return verify.attach(out, self._verify_event(calendar_id, out["id"], out, body))

    @invalidates("calendar")
    def calendar_update(self, event_id: str, event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        """Patch an event; only the keys present in `event` change. A series master id edits the series,
        an occurrence id edits that occurrence. Graph tells attendees about meeting changes by itself."""
        dest = event.get("move_to_calendar_id")
        if dest and dest != calendar_id:
            raise ValueError("Moving an event to another calendar is not supported for Outlook calendars yet.")
        cur: dict[str, Any] | None = None
        if (event.get("start") and not event.get("end")) or (event.get("recurrence") and not event.get("start")):
            cur = self._get_flat(event_id, calendar_id)
        if cur and event.get("start") and not event.get("end"):
            end = _end_keeping_length(cur, str(event["start"]))
            if end:
                event = {**event, "end": end}
        body = self._event_body(event, patch=True, start_date=(cur["start"][:10] if cur and cur.get("start") else None))
        if not body:
            return self._get_flat(event_id, calendar_id)
        e = self._req("PATCH", _eid(event_id), json=body, headers=self._prefer()) or {}  # type: ignore[attr-defined]
        out = self._event_out(e, calendar_id, full=True)
        return verify.attach(out, self._verify_event(calendar_id, event_id, out, body))

    @invalidates("calendar")
    def calendar_delete(self, event_id: str, calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        # Deleting a meeting you organise makes Outlook send the cancellation; `send_updates` cannot suppress it.
        self._req("DELETE", _eid(event_id))  # type: ignore[attr-defined]
        return verify.attach({"deleted": event_id, "calendar_id": calendar_id}, self._verify_event_gone(event_id))

    @invalidates("calendar")
    def calendar_respond(self, event_id: str, response: str, calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        """RSVP to an invitation: accepted, declined or tentative (Outlook cannot reset to needsAction)."""
        if response == "needsAction":
            raise ValueError("Outlook cannot reset a response to needsAction.")
        if response not in _RESPOND:
            raise ValueError("response must be accepted, declined or tentative")
        self._req("POST", f"{_eid(event_id)}/{_RESPOND[response]}",  # type: ignore[attr-defined]
                  json={"sendResponse": send_updates in ("all", "externalOnly")})
        out = self._get_flat(event_id, calendar_id)
        return verify.attach(out, self._verify_rsvp(event_id, response))

    @invalidates("calendar")
    def calendar_restore(self, event_id: str | None, body: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        """Write a calendar_raw-shaped body back: patch `event_id`, or create when it is None. Used only by undo."""
        if body.get("status") == "confirmed" and len(body) == 1:
            raise ValueError("Outlook does not keep cancelled events, so this one cannot be un-cancelled.")
        flat: dict[str, Any] = {k: body[k] for k in ("summary", "description", "location", "visibility", "transparency", "recurrence", "reminders") if k in body}
        for k in ("start", "end"):
            spec = body.get(k) or {}
            if spec.get("dateTime") or spec.get("date"):
                flat[k] = spec.get("dateTime") or spec["date"]
                if spec.get("timeZone") and k == "start":
                    flat["time_zone"] = spec["timeZone"]
        if "conferenceData" in body:
            flat["create_meet" if body["conferenceData"] else "clear_meet"] = True
        rsvp: str | None = None
        if "attendees" in body:
            atts = [{"email": a["email"], "optional": a.get("optional")} for a in body["attendees"] or []]
            if event_id:
                cur = self._get_flat(event_id, calendar_id)
                if sorted(a.lower() for a in cur["attendees"]) == sorted(a["email"].lower() for a in atts):
                    # Same guests: only an RSVP differs, and an invitee may not rewrite the guest list.
                    me = self._my_address()
                    prev = next((a.get("responseStatus") for a in body["attendees"] if (a.get("email") or "").lower() == me), None)
                    rsvp = prev if prev in _RESPOND and prev != cur.get("my_response") else None
                    atts = []
            if atts or not event_id:
                flat["attendees"] = atts
        start_date = None
        if flat.get("recurrence") and not flat.get("start") and event_id:
            start_date = self._get_flat(event_id, calendar_id)["start"][:10]
        graph_body = self._event_body(flat, patch=bool(event_id), start_date=start_date)
        if event_id:
            e = self._req("PATCH", _eid(event_id), json=graph_body, headers=self._prefer()) if graph_body else None  # type: ignore[attr-defined]
            if rsvp:
                self._req("POST", f"{_eid(event_id)}/{_RESPOND[rsvp]}", json={"sendResponse": send_updates in ("all", "externalOnly")})  # type: ignore[attr-defined]
            if e is None:
                e = self._req("GET", _eid(event_id), headers=self._prefer())  # type: ignore[attr-defined]
        else:
            e = self._req("POST", f"{_cal(calendar_id)}/events", json=graph_body, headers=self._prefer())  # type: ignore[attr-defined]
        out = self._event_out(e or {}, calendar_id, full=True)
        verdict = self._verify_event(calendar_id, out["id"], out, graph_body)
        if rsvp and verify.ok(verdict):
            verdict = self._verify_rsvp(out["id"], rsvp)
        return verify.attach(out, verdict)

    def _get_flat(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        return self._event_out(self._req("GET", _eid(event_id), headers=self._prefer()) or {}, calendar_id, full=True)  # type: ignore[attr-defined]

    # ---------- read-backs ----------
    def _event_reader(self, event_id: str) -> Callable[[], dict[str, Any]]:
        def read_back() -> dict[str, Any]:
            try:
                return self._req("GET", _eid(event_id), headers=self._prefer()) or {}  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001
                if _missing(e):
                    raise verify.NotVisible(f"event {event_id} is not on the calendar") from e
                raise
        return read_back

    def _verify_event(self, calendar_id: str, event_id: str, wrote: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Fetch the event back by id and compare the fields this call actually wrote."""
        keys = [flat for api, flat in _VERIFY_FIELDS.items() if api in body] or ["summary", "start"]
        want = {**{k: wrote.get(k) for k in keys}, "status": wrote.get("status") or "confirmed"}

        def compare(e: dict[str, Any]) -> dict[str, Any]:
            return verify.diff(want, self._event_out(e, calendar_id, full=True), time_fields=("start", "end"))

        return verify.check(f"calendar event {event_id} on {calendar_id}", self._event_reader(event_id),
                            compare=compare, compared=sorted(want))

    def _verify_event_gone(self, event_id: str) -> dict[str, Any]:
        return verify.check(f"calendar event {event_id}", self._event_reader(event_id),
                            absent=True, gone_if=lambda e: bool(e.get("isCancelled")), compared=["absent"])

    def _verify_rsvp(self, event_id: str, response: str) -> dict[str, Any]:
        def compare(e: dict[str, Any]) -> dict[str, Any]:
            return verify.diff({"my_response": response}, {"my_response": self._event_out(e)["my_response"]})

        return verify.check(f"RSVP on event {event_id}", self._event_reader(event_id), compare=compare, compared=["my_response"])


def _end_keeping_length(cur: dict[str, Any], start: str) -> str | None:
    """The end that keeps an event as long as it is now, given a new start. None to use the default."""
    with contextlib.suppress(Exception):
        if len(start) == 10:
            if not cur["all_day"]:
                return None
            days = dt.date.fromisoformat(cur["end"]) - dt.date.fromisoformat(cur["start"])
            return (dt.date.fromisoformat(start) + days).isoformat()
        if cur["all_day"]:
            return None
        length = _parse_iso(cur["end"]) - _parse_iso(cur["start"])
        if length > dt.timedelta(0):
            return (_parse_iso(start) + length).isoformat()
    return None
