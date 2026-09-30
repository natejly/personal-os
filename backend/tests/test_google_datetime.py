"""JS toISOString() / RFC3339 values that Python 3.10's fromisoformat rejects."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.google import _parse_iso  # noqa: E402


def test_js_toisostring_with_millis_and_z() -> None:
    got = _parse_iso("2026-09-28T04:00:00.000Z")
    assert got == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)


def test_z_without_millis() -> None:
    got = _parse_iso("2026-09-28T04:00:00Z")
    assert got == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)


def test_offset_and_naive() -> None:
    assert _parse_iso("2026-09-28T04:00:00+00:00") == dt.datetime(2026, 9, 28, 4, 0, 0, tzinfo=dt.timezone.utc)
    assert _parse_iso("2026-09-28T10:00:00") == dt.datetime(2026, 9, 28, 10, 0, 0)


if __name__ == "__main__":
    test_js_toisostring_with_millis_and_z()
    test_z_without_millis()
    test_offset_and_naive()
    print("ok")
