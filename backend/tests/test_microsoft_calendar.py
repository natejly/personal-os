"""Outlook calendar over Graph: same dicts as google.py, writes proven by a read-back (no network).

Run: PYTHONPATH=. .venv/bin/python -m pytest tests/test_microsoft_calendar.py -q
"""
from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from personal_os import verify
from personal_os.cache import TTLCache
from personal_os.google import _event_out as google_event_out
from personal_os.microsoft_calendar import CalendarMixin

# The keys google._event_out emits: the first 17 for list rows, plus the editor-grade ones for `full`.
LITE_KEYS = {"id", "calendar_id", "summary", "start", "end", "all_day", "location", "link", "attendees", "description", "meet",
             "color_id", "recurring_event_id", "transparency", "status", "self_response", "my_response"}
FULL_KEYS = LITE_KEYS | {"time_zone", "recurrence", "visibility", "reminders", "organizer", "attendee_details",
                         "guests_can_invite_others", "guests_can_modify", "guests_can_see_other_guests"}


class GraphErr(Exception):
    """Stands in for the Graph client's error: only `.status` matters to the mixin."""

    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"HTTP {status}")
        self.status = status


class FakeMs(CalendarMixin):
    me = {"mail": "me@corp.com", "userPrincipalName": "me@corp.com"}

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], Any]):
        self._cache = TTLCache()
        self.calls: list[dict[str, Any]] = []
        self._handler = handler

    def _tz(self) -> str:
        return "America/Los_Angeles"

    def _req(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None, headers: dict | None = None) -> Any:
        call = {"method": method, "path": path, "params": params, "json": json, "headers": headers}
        self.calls.append(call)
        return self._handler(method, path, call)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "SLEEP", lambda s: None)


def graph_event(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "AAA=", "subject": "Standup", "bodyPreview": "daily", "body": {"contentType": "text", "content": "daily"},
        "start": {"dateTime": "2026-10-06T10:00:00.0000000", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-10-06T10:30:00.0000000", "timeZone": "America/Los_Angeles"},
        "isAllDay": False, "location": {"displayName": "Room 4"}, "webLink": "https://outlook.office.com/x",
        "attendees": [{"type": "required", "status": {"response": "accepted"}, "emailAddress": {"name": "Ann", "address": "ann@corp.com"}}],
        "organizer": {"emailAddress": {"address": "me@corp.com"}}, "responseStatus": {"response": "organizer"},
        "showAs": "busy", "sensitivity": "normal", "isCancelled": False, "isReminderOn": True, "reminderMinutesBeforeStart": 15,
        "changeKey": "ck1",
    }
    return {**base, **kw}


def test_calendar_events_asks_calendar_view_and_matches_google_keys() -> None:
    allday = graph_event(id="BBB=", subject="Offsite", isAllDay=True, attendees=[], responseStatus={"response": "organizer"},
                         start={"dateTime": "2026-10-07T00:00:00.0000000", "timeZone": "America/Los_Angeles"},
                         end={"dateTime": "2026-10-08T00:00:00.0000000", "timeZone": "America/Los_Angeles"})
    ms = FakeMs(lambda m, p, c: {"value": [graph_event(), allday, graph_event(id="CCC=", isCancelled=True)]})
    rows = ms.calendar_events(2, start="2026-10-06T00:00:00Z")
    call = ms.calls[0]
    assert call["path"] == "/me/calendarView"
    assert call["params"]["startDateTime"] == "2026-10-06T00:00:00Z" and call["params"]["endDateTime"] == "2026-10-08T00:00:00Z"
    assert call["params"]["$orderby"] == "start/dateTime"
    assert call["headers"] == {"Prefer": 'outlook.timezone="America/Los_Angeles"'}
    assert len(rows) == 2  # the cancelled one is dropped, as Google drops its tombstones
    assert all(set(r) == LITE_KEYS for r in rows)
    assert set(google_event_out({"id": "x", "start": {"date": "2026-10-06"}, "end": {"date": "2026-10-07"}})) == LITE_KEYS  # the literal has not drifted
    assert set(google_event_out({"id": "x"}, full=True)) == FULL_KEYS
    timed, day = rows
    assert timed["start"] == "2026-10-06T10:00:00-07:00" and timed["all_day"] is False
    assert timed["link"] == "https://outlook.office.com/x" and timed["attendees"] == ["ann@corp.com"] and timed["my_response"] == "accepted"
    assert day["start"] == "2026-10-07" and day["end"] == "2026-10-08" and day["all_day"] is True and day["my_response"] is None


def test_calendar_get_has_the_full_key_set() -> None:
    ms = FakeMs(lambda m, p, c: graph_event(recurrence=None, onlineMeeting={"joinUrl": "https://teams/j"}))
    e = ms.calendar_get("AAA=")
    assert set(e) == FULL_KEYS and e["meet"] == "https://teams/j" and e["organizer"] == "me@corp.com"
    assert e["attendee_details"] == [{"email": "ann@corp.com", "name": "Ann", "optional": False, "response": "accepted",
                                      "organizer": False, "self": False}]


def test_create_posts_graph_body_and_verifies_the_read_back() -> None:
    stored = graph_event(id="NEW=", subject="Planning")
    ms = FakeMs(lambda m, p, c: stored)  # the POST echo and the GET read-back agree
    out = ms.calendar_create({"summary": "Planning", "start": "2026-10-06T10:00", "end": "2026-10-06T10:30",
                              "attendees": [{"email": "ann@corp.com"}, "bo@corp.com"], "location": "Room 4"}, "primary", "all")
    post = ms.calls[0]
    assert (post["method"], post["path"]) == ("POST", "/me/events")
    assert post["json"]["subject"] == "Planning"
    assert post["json"]["start"] == {"dateTime": "2026-10-06T10:00:00", "timeZone": "America/Los_Angeles"}
    assert post["json"]["end"]["timeZone"] == "America/Los_Angeles"
    assert post["json"]["attendees"][1] == {"emailAddress": {"address": "bo@corp.com"}, "type": "required"}
    assert out["verified"] is True and out["verification"]["status"] == "verified"
    assert ms.calls[1]["method"] == "GET" and ms.calls[1]["path"] == "/me/events/NEW%3D"


def test_create_reports_unverified_when_the_read_back_disagrees() -> None:
    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        return graph_event(id="NEW=", subject="Planning") if method == "POST" else graph_event(id="NEW=", subject="Something else")

    out = FakeMs(handler).calendar_create({"summary": "Planning", "start": "2026-10-06T10:00", "end": "2026-10-06T10:30"})
    assert out["verified"] is False
    assert out["verification"]["status"] == "mismatch" and "summary" in out["verification"]["differences"]


def test_create_reports_unverified_when_the_read_back_404s() -> None:
    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        if method == "GET":
            raise GraphErr(404)
        return graph_event(id="NEW=")

    out = FakeMs(handler).calendar_create({"summary": "Standup", "start": "2026-10-06T10:00"})
    assert out["verified"] is False and out["verification"]["reason"] == "not_visible"


def test_weekly_rrule_maps_to_graph_recurrence() -> None:
    echo = graph_event(id="R=", recurrence={
        "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["monday", "wednesday"]},
        "range": {"type": "numbered", "startDate": "2026-10-05", "numberOfOccurrences": 5}})
    ms = FakeMs(lambda m, p, c: echo)
    out = ms.calendar_create({"summary": "Gym", "start": "2026-10-05T07:00", "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5"]})
    rec = ms.calls[0]["json"]["recurrence"]
    assert rec["pattern"] == {"interval": 1, "type": "weekly", "daysOfWeek": ["monday", "wednesday"]}
    assert rec["range"] == {"type": "numbered", "startDate": "2026-10-05", "numberOfOccurrences": 5}
    assert out["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5"] and out["verified"] is True


def test_monthly_and_until_rules_and_unsupported_parts() -> None:
    ms = FakeMs(lambda m, p, c: graph_event())
    body = ms._event_body({"start": "2026-10-05T07:00", "recurrence": ["RRULE:FREQ=MONTHLY;BYDAY=2TU;UNTIL=20261231T000000Z"]})
    assert body["recurrence"]["pattern"] == {"interval": 1, "type": "relativeMonthly", "index": "second", "daysOfWeek": ["tuesday"]}
    assert body["recurrence"]["range"] == {"type": "endDate", "startDate": "2026-10-05", "endDate": "2026-12-31"}
    for bad, part in (("RRULE:FREQ=HOURLY", "FREQ"), ("RRULE:FREQ=DAILY;BYSETPOS=1", "BYSETPOS"), ("EXDATE:20261010", "EXDATE")):
        with pytest.raises(ValueError, match=part):
            ms._event_body({"start": "2026-10-05T07:00", "recurrence": [bad]})
    assert ms._event_body({"recurrence": []}) == {"recurrence": None}  # [] clears a series


def test_calendar_raw_returns_none_on_404_and_the_undo_shape_otherwise() -> None:
    def gone(method: str, path: str, call: dict[str, Any]) -> Any:
        raise GraphErr(404, "ErrorItemNotFound")

    def boom(method: str, path: str, call: dict[str, Any]) -> Any:
        raise GraphErr(500)

    assert FakeMs(gone).calendar_raw("AAA=") is None
    raw = FakeMs(lambda m, p, c: graph_event()).calendar_raw("AAA=")
    assert raw["etag"] == "ck1" and raw["summary"] == "Standup" and raw["status"] == "confirmed"
    assert raw["start"] == {"dateTime": "2026-10-06T10:00:00-07:00", "timeZone": "America/Los_Angeles"}
    assert raw["attendees"] == [{"email": "ann@corp.com", "optional": False, "responseStatus": "accepted"}]
    with pytest.raises(GraphErr):  # anything but a 404 is a real failure
        FakeMs(boom).calendar_raw("AAA=")


def test_respond_accepted_posts_to_accept_and_verifies() -> None:
    state = {"resp": "notResponded"}

    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        if method == "POST":
            state["resp"] = "accepted" if path.endswith("/accept") else "tentativelyAccepted"
            return None
        return graph_event(responseStatus={"response": state["resp"]}, organizer={"emailAddress": {"address": "ann@corp.com"}})

    ms = FakeMs(handler)
    out = ms.calendar_respond("AAA=", "accepted")
    assert ms.calls[0]["method"] == "POST" and ms.calls[0]["path"] == "/me/events/AAA%3D/accept"
    assert ms.calls[0]["json"] == {"sendResponse": False}
    assert out["my_response"] == "accepted" and out["verified"] is True
    with pytest.raises(ValueError):
        ms.calendar_respond("AAA=", "needsAction")
    tent = ms.calendar_respond("AAA=", "tentative", send_updates="all")
    post = [c for c in ms.calls if c["method"] == "POST"][-1]
    assert post["path"].endswith("/tentativelyAccept") and post["json"] == {"sendResponse": True} and tent["verified"] is True


def test_delete_verified_by_404_and_update_start_keeps_length() -> None:
    state = {"gone": False, "ev": graph_event()}

    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        if method == "DELETE":
            state["gone"] = True
            return None
        if method == "PATCH":
            ev = copy.deepcopy(state["ev"])
            ev["start"], ev["end"] = call["json"]["start"], call["json"]["end"]
            state["ev"] = ev
            return ev
        if state["gone"]:
            raise GraphErr(404)
        return state["ev"]

    ms = FakeMs(handler)
    upd = ms.calendar_update("AAA=", {"start": "2026-10-06T14:00"})
    patch = next(c for c in ms.calls if c["method"] == "PATCH")
    assert patch["json"]["end"]["dateTime"] == "2026-10-06T14:30:00"  # the 30 minute length survived
    assert upd["verified"] is True
    d = ms.calendar_delete("AAA=")
    assert d["deleted"] == "AAA=" and d["verified"] is True
    with pytest.raises(ValueError):
        ms.calendar_update("AAA=", {"move_to_calendar_id": "other"})


def test_restore_of_an_rsvp_only_change_calls_respond_not_patch() -> None:
    state = {"resp": "accepted"}

    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        if method == "POST":
            state["resp"] = "declined" if path.endswith("/decline") else state["resp"]
            return None
        return graph_event(responseStatus={"response": state["resp"]}, attendees=[
            {"type": "required", "status": {"response": state["resp"]}, "emailAddress": {"address": "me@corp.com"}}],
            organizer={"emailAddress": {"address": "ann@corp.com"}})

    ms = FakeMs(handler)
    out = ms.calendar_restore("AAA=", {"attendees": [{"email": "me@corp.com", "optional": False, "responseStatus": "declined"}]})
    assert [c["method"] for c in ms.calls if c["method"] != "GET"] == ["POST"]  # no PATCH of the guest list
    assert ms.calls[1]["path"].endswith("/decline") and out["my_response"] == "declined" and out["verified"] is True


def test_calendars_ensure_and_free_busy() -> None:
    cals = {"value": [
        {"id": "c2", "name": "Team", "canEdit": False, "isDefaultCalendar": False, "color": "lightGreen", "hexColor": ""},
        {"id": "c1", "name": "Calendar", "canEdit": True, "isDefaultCalendar": True, "color": "auto", "hexColor": "#123456"}]}

    def handler(method: str, path: str, call: dict[str, Any]) -> Any:
        if path == "/me/calendars" and method == "GET":
            return cals
        if path == "/me/calendars":
            return {"id": "c3", "name": call["json"]["name"]}
        if path.endswith("getSchedule"):
            return {"value": [
                {"scheduleId": "me@corp.com", "scheduleItems": [{"status": "busy", "start": {"dateTime": "2026-10-06T17:00:00.0000000", "timeZone": "UTC"},
                                                                  "end": {"dateTime": "2026-10-06T18:00:00.0000000", "timeZone": "UTC"}}]},
                {"scheduleId": "Ann@corp.com", "scheduleItems": [{"status": "free", "start": {"dateTime": "2026-10-06T19:00:00", "timeZone": "UTC"},
                                                                   "end": {"dateTime": "2026-10-06T20:00:00", "timeZone": "UTC"}}]},
                {"scheduleId": "x@elsewhere.com", "scheduleItems": [], "error": {"responseCode": "ErrorMailRecipientNotFound"}}]}
        raise AssertionError(path)

    ms = FakeMs(handler)
    listed = ms.calendars()
    assert [c["id"] for c in listed] == ["c1", "c2"] and listed[0]["primary"] and listed[0]["color"] == "#123456"
    assert listed[1]["access_role"] == "reader" and listed[1]["color"] == "#6CC24A"
    assert ms.enabled_calendar_ids() == ["c1", "c2"]
    assert ms.calendar_ensure("Team")["created"] is True  # the existing "Team" is read-only, so it does not count
    assert ms.calendar_ensure("Calendar") == {"id": "c1", "summary": "Calendar", "created": False}
    fb = ms.calendar_free_busy("2026-10-06T00:00:00Z", "2026-10-07T00:00:00Z", ["primary"], ["Ann@corp.com", "x@elsewhere.com"])
    body = next(c for c in ms.calls if c["path"].endswith("getSchedule"))["json"]
    assert body["schedules"] == ["me@corp.com", "Ann@corp.com", "x@elsewhere.com"] and body["availabilityViewInterval"] == 15
    assert fb["calendars"]["primary"] == {"busy": [{"start": "2026-10-06T17:00:00+00:00", "end": "2026-10-06T18:00:00+00:00"}], "attendee": False}
    assert fb["calendars"]["Ann@corp.com"]["busy"] == [] and fb["calendars"]["Ann@corp.com"]["attendee"] is True
    assert fb["unreachable"] == ["x@elsewhere.com"]
    assert ms.calendar_saved(3) is None
