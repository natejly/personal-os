"""The calendar tools as the model sees them: local wall-clock times in, compact local rows out.

Run: uv run --project backend --with pytest pytest backend/tests/test_calendar_tools.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="caltest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from tests.test_google_cache import _Events, _google, _Req  # noqa: E402


class _Recording(_Events):
    def __init__(self, items: list[dict[str, Any]] | None = None):
        super().__init__(items)
        self.kw: dict[str, Any] = {}

    def list(self, **kw: Any) -> _Req:
        self.kw = kw
        return super().list(**kw)

    def insert(self, **kw: Any) -> _Req:
        self.kw = kw
        return super().insert(**kw)


def test_create_sends_rfc3339_datetimes_with_seconds() -> None:
    """The API answers 400 Bad Request to the YYYY-MM-DDTHH:MM form the tool schema asks the model for."""
    events = _Recording()
    _google(events).calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00", "end": "2026-10-07T16:00"})
    body = events.kw["body"]
    assert (body["start"]["dateTime"], body["end"]["dateTime"]) == ("2026-10-07T15:00:00", "2026-10-07T16:00:00")
    assert body["start"]["timeZone"]  # naive wall-clock, so the user's zone rides along


def test_a_naive_start_is_the_users_wall_clock_not_utc() -> None:
    events = _Recording()
    _google(events).calendar_events(days=1, start="2026-10-05T09:00")
    want = dt.datetime(2026, 10, 5, 9, 0).astimezone()
    assert dt.datetime.fromisoformat(events.kw["timeMin"]) == want, "09:00 local, as calendar_create reads it"


def test_a_date_only_start_is_local_midnight() -> None:
    events = _Recording()
    _google(events).calendar_events(days=1, start="2026-10-05")
    assert dt.datetime.fromisoformat(events.kw["timeMin"]) == dt.datetime(2026, 10, 5).astimezone()


def test_the_list_tool_returns_compact_local_rows() -> None:
    long = "x" * 1000
    rows = [{"id": "e1", "calendar_id": "primary", "summary": "Standup", "start": "2026-10-05T13:00:00Z",
             "end": "2026-10-05T13:15:00Z", "all_day": False, "location": None, "link": "https://calendar.google.com/x",
             "attendees": ["a@x.com", "b@x.com"], "description": long, "meet": "", "color_id": None,
             "recurring_event_id": None, "transparency": "transparent", "status": "confirmed"},
            {"id": "e2", "calendar_id": "primary", "summary": "Off", "start": "2026-10-06", "end": "2026-10-07",
             "all_day": True, "transparency": "opaque"}]
    g = appmod.toolbox.google
    real = g.calendar_events
    g.calendar_events = lambda *a, **k: rows  # type: ignore[method-assign]
    try:
        out = asyncio.run(appmod.toolbox.call("calendar_events", {"days": 3}, {"project_id": None}))
    finally:
        g.calendar_events = real  # type: ignore[method-assign]
    first, second = out["events"]
    local = dt.datetime(2026, 10, 5, 13, tzinfo=dt.timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M")
    assert first["start"] == local, "UTC from Google comes back as the local time the model writes"
    assert first["busy"] is False and first["guests"] == 2 and len(first["description"]) == 120
    assert "link" not in first and "meet" not in first and "location" not in first, "empty and UI-only fields dropped"
    assert second == {"id": "e2", "summary": "Off", "start": "2026-10-06", "end": "2026-10-07", "all_day": True,
                      "calendar_id": "primary"}, "an all-day date is left alone"


def test_meeting_brief_lists_attendee_memories() -> None:
    tb = appmod.toolbox
    tb.memories.create(None, "Mira Chen prefers short agendas", kind="fact", source="auto")
    ev = {"summary": "Review", "start": "2026-10-05T10:00", "end": "2026-10-05T11:00", "attendee_details": [
        {"email": "me@example.com", "self": True}, {"email": "mira@example.com", "name": "Mira Chen"}]}
    g = tb.google
    real = g.calendar_get
    g.calendar_get = lambda *a, **k: ev  # type: ignore[method-assign]
    try:
        out = asyncio.run(tb.call("meeting_brief", {"event_id": "e9"}, {"project_id": None}))
    finally:
        g.calendar_get = real  # type: ignore[method-assign]
    (p,) = out["people"]
    assert p["email"] == "mira@example.com" and "short agendas" in p["notes"][0]
    assert "past_meetings" not in p


# ---- which calendars a read covers by default ----
def _ev(eid: str, title: str, hour: int) -> dict[str, Any]:
    return {"id": eid, "summary": title, "start": {"dateTime": f"2026-10-05T{hour:02d}:00:00Z"}, "end": {"dateTime": f"2026-10-05T{hour + 1:02d}:00:00Z"}}


class _PerCalendar(_Events):
    """events.list answers per calendarId; a calendar mapped to an exception raises it."""

    def __init__(self, by_cal: dict[str, Any]):
        super().__init__()
        self.by_cal, self.asked = by_cal, []  # type: ignore[var-annotated]

    def list(self, **kw: Any) -> _Req:
        self.asked.append(kw["calendarId"])
        res = self.by_cal[kw["calendarId"]]
        if isinstance(res, Exception):
            raise res
        return _Req({"items": res})


_CALS = [
    {"id": "primary", "summary": "Me", "primary": True, "accessRole": "owner", "selected": True},
    {"id": "classes@x", "summary": "Classes", "accessRole": "reader", "selected": True},
    {"id": "off@x", "summary": "Unchecked", "accessRole": "reader", "selected": False},
    {"id": "gone@x", "summary": "Hidden", "accessRole": "reader", "selected": True, "hidden": True},
]


def _multi(by_cal: dict[str, Any]) -> tuple[Any, _PerCalendar]:
    from tests.test_google_cache import _CalendarList
    ev = _PerCalendar(by_cal)
    return _google(ev, _CalendarList(_CALS)), ev


def _all_cals() -> dict[str, Any]:
    return {"primary": [_ev("p1", "Standup", 9)], "classes@x": [_ev("c1", "Lecture", 10)],
            "off@x": [_ev("o1", "Noise", 11)], "gone@x": [_ev("h1", "Secret", 12)]}


def test_the_enabled_rule_is_checked_on_not_hidden_or_primary() -> None:
    g, _ = _multi(_all_cals())
    assert g.enabled_calendar_ids() == ["primary", "classes@x"]


def test_enabled_read_covers_every_checked_calendar_and_skips_the_rest() -> None:
    g, ev = _multi(_all_cals())
    rows = g.calendar_events(days=2, start="2026-10-05T00:00:00Z", calendar_ids=["enabled"])
    assert sorted(ev.asked) == ["classes@x", "primary"], "unchecked and hidden calendars are not read"
    assert [(r["id"], r["calendar_id"]) for r in rows] == [("p1", "primary"), ("c1", "classes@x")]


def test_all_includes_hidden_and_unchecked_calendars() -> None:
    g, ev = _multi(_all_cals())
    g.calendar_events(days=2, start="2026-10-05T00:00:00Z", calendar_ids=["all"])
    assert sorted(ev.asked) == ["classes@x", "gone@x", "off@x", "primary"]


def test_one_failing_calendar_does_not_empty_the_result() -> None:
    by = _all_cals()
    by["classes@x"] = RuntimeError("403")
    g, _ = _multi(by)
    rows = g.calendar_events(days=2, start="2026-10-05T00:00:00Z", calendar_ids=["enabled"])
    assert [r["id"] for r in rows] == ["p1"]


def test_a_failed_calendar_list_falls_back_to_primary() -> None:
    g, ev = _multi(_all_cals())
    g.calendars = lambda: (_ for _ in ()).throw(RuntimeError("list down"))  # type: ignore[method-assign]
    rows = g.calendar_events(days=2, start="2026-10-05T00:00:00Z", calendar_ids=["enabled"])
    assert ev.asked == ["primary"] and [r["id"] for r in rows] == ["p1"]


def test_the_tool_defaults_to_enabled_and_labels_each_event_with_its_calendar() -> None:
    g = appmod.toolbox.google
    seen: dict[str, Any] = {}
    rows = [{"id": "p1", "calendar_id": "primary", "summary": "Standup", "start": "2026-10-05T09:00:00Z", "end": "2026-10-05T10:00:00Z"},
            {"id": "c1", "calendar_id": "classes@x", "summary": "Lecture", "start": "2026-10-05T10:00:00Z", "end": "2026-10-05T11:00:00Z"}]

    def fake(*a: Any, **k: Any) -> Any:
        seen["ids"] = a[4]
        return rows

    real_ev, real_cal = g.calendar_events, g.calendars
    g.calendar_events = fake  # type: ignore[method-assign]
    g.calendars = lambda: [{"id": "primary", "summary": "Me"}, {"id": "classes@x", "summary": "Classes"}]  # type: ignore[method-assign]
    try:
        out = asyncio.run(appmod.toolbox.call("calendar_events", {}, {"project_id": None}))
        assert seen["ids"] == ["enabled"]
        assert [(e["calendar"], e["calendar_id"]) for e in out["events"]] == [("Me", "primary"), ("Classes", "classes@x")]
        asyncio.run(appmod.toolbox.call("calendar_events", {"calendar_id": "classes@x"}, {"project_id": None}))
        assert seen["ids"] == ["classes@x"], "calendar_id narrows to one calendar"
        asyncio.run(appmod.toolbox.call("calendar_events", {"all_calendars": True}, {"project_id": None}))
        assert seen["ids"] == ["all"]
    finally:
        g.calendar_events, g.calendars = real_ev, real_cal  # type: ignore[method-assign]
