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
