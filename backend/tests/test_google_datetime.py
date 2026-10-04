"""JS toISOString() / RFC3339 values that Python 3.10's fromisoformat rejects."""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.google import _overlaps, _parse_iso, _rfc2822_iso  # noqa: E402


def test_js_toisostring_with_millis_and_z() -> None:
    got = _parse_iso("2026-09-28T04:00:00.000Z")
    assert got == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)


def test_z_without_millis() -> None:
    got = _parse_iso("2026-09-28T04:00:00Z")
    assert got == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)


def test_offset_and_naive() -> None:
    assert _parse_iso("2026-09-28T04:00:00+00:00") == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)
    assert _parse_iso("2026-09-28T10:00:00") == dt.datetime(2026, 9, 28, 10, 0, 0)


def test_rfc2822_gmail_date_to_iso() -> None:
    got = _rfc2822_iso("Mon, 29 Sep 2026 10:15:00 -0400")
    assert got is not None
    parsed = dt.datetime.fromisoformat(got)
    assert parsed.year == 2026 and parsed.month == 9 and parsed.day == 29
    assert parsed.hour == 10 and parsed.minute == 15


def test_rfc2822_empty_and_passthrough() -> None:
    assert _rfc2822_iso(None) is None
    assert _rfc2822_iso("") is None
    assert _rfc2822_iso("not a date") == "not a date"


def test_all_day_event_lasts_the_whole_local_day() -> None:
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        ev = {"start": "2026-10-03", "end": "2026-10-04"}
        now = dt.datetime(2026, 10, 3, 21, 0).astimezone()  # 9pm local is already the 4th in UTC
        assert _overlaps(ev, now, now + dt.timedelta(days=1))
        assert not _overlaps(ev, now + dt.timedelta(hours=4), now + dt.timedelta(days=1))  # 1am on the 4th
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


if __name__ == "__main__":
    test_all_day_event_lasts_the_whole_local_day()
    test_js_toisostring_with_millis_and_z()
    test_z_without_millis()
    test_offset_and_naive()
    test_rfc2822_gmail_date_to_iso()
    test_rfc2822_empty_and_passthrough()
    print("ok")
