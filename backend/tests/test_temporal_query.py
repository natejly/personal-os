"""Temporal phrase parser: exact windows for a fixed Wednesday, and the phrases it must leave alone."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.temporal_query import local_now, parse, relevance  # noqa: E402

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=TZ)  # Wednesday


def D(y: int, m: int, d: int, h: int = 0) -> datetime:
    return datetime(y, m, d, h, tzinfo=TZ)


CASES = [
    ("today", D(2026, 10, 7), D(2026, 10, 8)),
    ("what did I say yesterday?", D(2026, 10, 6), D(2026, 10, 7)),
    ("tomorrow", D(2026, 10, 8), D(2026, 10, 9)),
    ("tonight", D(2026, 10, 7, 18), D(2026, 10, 8, 6)),
    ("this week", D(2026, 10, 5), D(2026, 10, 12)),
    ("what did we decide about pricing last week", D(2026, 9, 28), D(2026, 10, 5)),
    ("next week", D(2026, 10, 12), D(2026, 10, 19)),
    ("this month", D(2026, 10, 1), D(2026, 11, 1)),
    ("last month", D(2026, 9, 1), D(2026, 10, 1)),
    ("next month", D(2026, 11, 1), D(2026, 12, 1)),
    ("this year", D(2026, 1, 1), D(2027, 1, 1)),
    ("last year", D(2025, 1, 1), D(2026, 1, 1)),
    ("last 3 days", D(2026, 10, 4), NOW),
    ("last two weeks", D(2026, 9, 23), NOW),
    ("the last couple of weeks", D(2026, 9, 23), NOW),
    ("last 2 months", D(2026, 8, 7), NOW),
    ("3 days ago", D(2026, 10, 4), D(2026, 10, 5)),
    ("two weeks ago", D(2026, 9, 21), D(2026, 9, 28)),
    ("a week ago", D(2026, 9, 28), D(2026, 10, 5)),
    ("a couple of weeks ago", D(2026, 9, 21), D(2026, 9, 28)),
    ("2 months ago", D(2026, 8, 1), D(2026, 9, 1)),
    ("a year ago", D(2025, 1, 1), D(2026, 1, 1)),
    ("last spring", D(2026, 3, 1), D(2026, 6, 1)),
    ("last summer", D(2026, 6, 1), D(2026, 9, 1)),
    ("last fall", D(2025, 9, 1), D(2025, 12, 1)),
    ("last autumn", D(2025, 9, 1), D(2025, 12, 1)),
    ("last winter", D(2025, 12, 1), D(2026, 3, 1)),  # spans the year boundary
    ("this winter", D(2026, 12, 1), D(2027, 3, 1)),
    ("this fall", D(2026, 9, 1), D(2026, 12, 1)),
    ("in March 2026", D(2026, 3, 1), D(2026, 4, 1)),
    ("October 2025", D(2025, 10, 1), D(2025, 11, 1)),
    ("May 2026 notes", D(2026, 5, 1), D(2026, 6, 1)),
    ("last March", D(2026, 3, 1), D(2026, 4, 1)),
    ("last October", D(2025, 10, 1), D(2025, 11, 1)),  # this month does not count as "last"
    ("in November", D(2025, 11, 1), D(2025, 12, 1)),  # most recent past occurrence
    ("in may", D(2026, 5, 1), D(2026, 6, 1)),
    ("in 2025", D(2025, 1, 1), D(2026, 1, 1)),
    ("2026-03-04", D(2026, 3, 4), D(2026, 3, 5)),
    ("March 5, 2025", D(2025, 3, 5), D(2025, 3, 6)),
    ("Dec 25", D(2025, 12, 25), D(2025, 12, 26)),
    ("5 March 2026", D(2026, 3, 5), D(2026, 3, 6)),
    ("since last week", D(2026, 9, 28), NOW),
    ("since 2024", D(2024, 1, 1), NOW),
    ("after March 2026", D(2026, 3, 1), NOW),
    ("before 2020", D(2006, 10, 7), D(2020, 1, 1)),
    ("until March 2026", D(2006, 10, 7), D(2026, 4, 1)),
    ("between March 2026 and May 2026", D(2026, 3, 1), D(2026, 6, 1)),
    ("between 2024 and 2025", D(2024, 1, 1), D(2026, 1, 1)),
    ("from January to March", D(2026, 1, 1), D(2026, 4, 1)),
]


@pytest.mark.parametrize("text,start,end", CASES)
def test_windows(text: str, start: datetime, end: datetime) -> None:
    w = parse(text, NOW)
    assert w is not None, text
    assert (w.start, w.end) == (start, end), text
    assert w.phrase.lower() in text.lower()


def test_phrase_is_the_matched_span() -> None:
    assert parse("notes from in March 2026 please", NOW).phrase == "in March 2026"  # type: ignore[union-attr]
    assert parse("what happened last week?", NOW).phrase == "last week"  # type: ignore[union-attr]


def test_first_match_wins() -> None:
    assert parse("yesterday or last month", NOW).phrase == "yesterday"  # type: ignore[union-attr]


NEGATIVES = [
    "may I ask", "march ahead", "this may help", "the 2025 budget line item 20251", "weekly report", "monthly",
    "Mayfair street", "a week", "in a year's time", "version 2025", "ticket ABC-2025 is open", "October was rough",
    "in 20251", "February 30", "", "what is the capital of France",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives(text: str) -> None:
    assert parse(text, NOW) is None, text


def test_relevance() -> None:
    w = parse("last week", NOW)
    assert w is not None
    lo, hi = w.start.timestamp(), w.end.timestamp()
    width = hi - lo
    assert relevance(w, lo) == 1.0 and relevance(w, (lo + hi) / 2) == 1.0
    assert relevance(w, hi) == pytest.approx(1.0)  # half-open: the end instant is just outside, full score
    assert relevance(w, hi + width / 2) == pytest.approx(0.5)
    assert relevance(w, lo - width / 4) == pytest.approx(0.75)
    assert relevance(w, hi + width) == 0.0 and relevance(w, lo - 3 * width) == 0.0


def test_local_now_is_aware() -> None:
    assert local_now().tzinfo is not None
