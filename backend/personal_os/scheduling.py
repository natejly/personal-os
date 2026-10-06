"""Scheduling logic with no network in it: busy time, free slots, and change-batch validation.

google.py fetches events and free/busy; this module decides what they mean. Keeping it pure (every
function takes its inputs, including "now" and the timezone) is what lets the awkward cases be
tested exactly: all-day events, DST days, buffers, working hours, declined invitations.

Conventions
  * Times in and out are ISO strings. A naive one ("2026-10-07T15:00") is wall-clock time in the
    user's timezone, which is how every other calendar tool in this app reads them.
  * All arithmetic is done on aware datetimes converted to UTC, so a 9-to-6 working day on the
    night the clocks change is 10 hours long (or 8), not 9.
  * An interval is half-open, [start, end). A meeting ending at 10:00 does not collide with one
    starting at 10:00.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
Interval = tuple[dt.datetime, dt.datetime]

OPS = ("create", "update", "delete")
MAX_CHANGES = 25
STEP_MIN = 15  # candidate start times land on this grid


# ---------------------------------------------------------------- time parsing
def get_tz(name: str | None) -> dt.tzinfo:
    """A ZoneInfo, or UTC when the name is empty or unknown (never raises)."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return UTC


def parse_dt(value: str, tz: dt.tzinfo) -> dt.datetime:
    """An aware datetime from RFC3339 / JS ISO / naive local ISO. A bare date is local midnight."""
    s = value.strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = _localize(d, tz)
    return d


def _localize(naive: dt.datetime, tz: dt.tzinfo) -> dt.datetime:
    """Attach tz to a wall-clock time. A time inside a spring-forward gap is pushed past it."""
    aware = naive.replace(tzinfo=tz)
    # Round-trip through UTC: a nonexistent wall time comes back moved onto the real clock.
    return aware.astimezone(UTC).astimezone(tz)


def iso_local(d: dt.datetime, tz: dt.tzinfo) -> str:
    """ISO string in the user's zone with its offset, seconds dropped."""
    return d.astimezone(tz).isoformat(timespec="minutes")


def is_date_only(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) == 10


# ---------------------------------------------------------------- busy time
def event_interval(e: dict[str, Any], tz: dt.tzinfo) -> Interval | None:
    """[start, end) of one event dict as returned by google.calendar_events, or None if unusable."""
    s, en = e.get("start"), e.get("end")
    if not s:
        return None
    try:
        if is_date_only(s):
            # All-day: Google's end is exclusive and a one-day event may omit it or repeat the start.
            d0 = dt.date.fromisoformat(s.strip())
            d1 = dt.date.fromisoformat(en.strip()) if is_date_only(en) else d0
            if d1 <= d0:
                d1 = d0 + dt.timedelta(days=1)
            return (_localize(dt.datetime.combine(d0, dt.time()), tz).astimezone(UTC),
                    _localize(dt.datetime.combine(d1, dt.time()), tz).astimezone(UTC))
        a = parse_dt(s, tz).astimezone(UTC)
        b = parse_dt(en, tz).astimezone(UTC) if en else a + dt.timedelta(hours=1)
    except (ValueError, TypeError):
        return None
    return (a, b) if b > a else (a, a + dt.timedelta(minutes=1))


def blocks_time(e: dict[str, Any], *, all_day_blocks: bool = True) -> bool:
    """Whether an event makes its owner busy.

    Cancelled events, events the owner marked Free, and invitations the owner declined do not.
    The declined check reads the owner's own RSVP (`my_response`, or the `self` row of
    `attendee_details`).
    """
    if e.get("status") == "cancelled":
        return False
    if (e.get("transparency") or "opaque") == "transparent":
        return False
    if (_my_response(e) or "") == "declined":
        return False
    if e.get("all_day") or is_date_only(e.get("start")):
        return all_day_blocks
    return True


def _my_response(e: dict[str, Any]) -> str | None:
    if e.get("my_response"):
        return e["my_response"]
    for a in e.get("attendee_details") or []:
        if a.get("self"):
            return a.get("response")
    return None


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, with overlapping and touching intervals fused."""
    out: list[Interval] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def busy_from_events(events: Sequence[dict[str, Any]], tz: dt.tzinfo, *, ignore_ids: Iterable[str] = (),
                     all_day_blocks: bool = True) -> list[Interval]:
    skip = set(ignore_ids)
    out: list[Interval] = []
    for e in events:
        if e.get("id") in skip or not blocks_time(e, all_day_blocks=all_day_blocks):
            continue
        iv = event_interval(e, tz)
        if iv:
            out.append(iv)
    return merge(out)


def busy_from_ranges(ranges: Iterable[dict[str, str]], tz: dt.tzinfo) -> list[Interval]:
    """Free/busy API rows ({start, end}) as intervals."""
    out: list[Interval] = []
    for r in ranges:
        try:
            a, b = parse_dt(r["start"], tz).astimezone(UTC), parse_dt(r["end"], tz).astimezone(UTC)
        except (KeyError, ValueError, TypeError):
            continue
        if b > a:
            out.append((a, b))
    return merge(out)


def pad(intervals: Iterable[Interval], minutes: int) -> list[Interval]:
    """Grow each interval by `minutes` on both sides (a buffer between meetings), then fuse."""
    m = dt.timedelta(minutes=max(0, minutes))
    return merge((a - m, b + m) for a, b in intervals)


# ---------------------------------------------------------------- working hours
_HM = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$")


def parse_working_hours(v: Any) -> tuple[int, int]:
    """(start_minute, end_minute) of the working day, from 9-18, "9-18", "09:00-17:30" or [9, 18]."""
    if v is None or v == "":
        return (9 * 60, 18 * 60)
    parts: list[Any]
    if isinstance(v, str):
        parts = re.split(r"\s*[-–]\s*|\s+to\s+", v.strip())
    elif isinstance(v, dict):
        parts = [v.get("start"), v.get("end")]
    else:
        parts = list(v)
    if len(parts) != 2:
        raise ValueError("working_hours must be two times, like '9-18' or '09:00-17:30'")
    mins = [_minutes(p) for p in parts]
    if not 0 <= mins[0] < mins[1] <= 24 * 60:
        raise ValueError("working_hours must start before it ends, within one day")
    return (mins[0], mins[1])


def _minutes(p: Any) -> int:
    if isinstance(p, (int, float)):
        return int(round(float(p) * 60))
    m = _HM.match(str(p))
    if not m:
        raise ValueError(f"cannot read the time {p!r}")
    return int(m.group(1)) * 60 + int(m.group(2) or 0)


def working_windows(start: dt.datetime, end: dt.datetime, tz: dt.tzinfo, hours: tuple[int, int],
                    weekdays_only: bool = True) -> list[Interval]:
    """The working-hours spans that fall inside [start, end), one per local day, in UTC."""
    out: list[Interval] = []
    day = start.astimezone(tz).date() - dt.timedelta(days=1)
    last = end.astimezone(tz).date() + dt.timedelta(days=1)
    while day <= last:
        if not (weekdays_only and day.weekday() >= 5):
            a = _wall(day, hours[0], tz)
            b = _wall(day, hours[1], tz)
            a, b = max(a, start), min(b, end)
            if b > a:
                out.append((a, b))
        day += dt.timedelta(days=1)
    return out


def _wall(day: dt.date, minute: int, tz: dt.tzinfo) -> dt.datetime:
    """Local wall-clock `minute` of the day as an instant. 24:00 is the next midnight."""
    base = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minute)
    return _localize(base, tz).astimezone(UTC)


def subtract(spans: Iterable[Interval], busy: Sequence[Interval]) -> list[Interval]:
    """What is left of `spans` once every busy interval is cut out."""
    out: list[Interval] = []
    for a, b in spans:
        cur = a
        for x, y in busy:
            if y <= cur:
                continue
            if x >= b:
                break
            if x > cur:
                out.append((cur, x))
            cur = max(cur, y)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


# ---------------------------------------------------------------- slot finding
def find_slots(duration_minutes: int, window_start: str, window_end: str, busy: Sequence[Interval], *,
               tz: dt.tzinfo = UTC, working_hours: Any = None, buffer_minutes: int = 0, max_results: int = 5,
               now: dt.datetime | None = None, weekdays_only: bool = True, step_minutes: int = STEP_MIN) -> list[dict[str, Any]]:
    """Ranked candidate slots of `duration_minutes` that fit inside working hours and miss all `busy`.

    The buffer is kept clear on both sides of every busy interval. Candidates start on the quarter
    hour. Ranking prefers the soonest day, then mid-morning and mid-afternoon over the edges of the
    day, then :00/:30 starts, then slots that do not strand a sliver too short to use; it avoids
    lunch. The picks never overlap each other, so the options offered are genuinely different.
    """
    dur = int(duration_minutes)
    if dur <= 0:
        raise ValueError("duration_minutes must be positive")
    step = max(5, int(step_minutes))
    w0, w1 = parse_dt(window_start, tz).astimezone(UTC), parse_dt(window_end, tz).astimezone(UTC)
    if is_date_only(window_end):  # "through Friday" means through the end of Friday
        w1 += dt.timedelta(days=1)
    if now is not None:
        w0 = max(w0, now.astimezone(UTC))
    if w1 <= w0:
        return []
    hours = parse_working_hours(working_hours)
    free = subtract(working_windows(w0, w1, tz, hours, weekdays_only), pad(busy, buffer_minutes))
    d = dt.timedelta(minutes=dur)
    first_day = w0.astimezone(tz).date()
    cands: list[tuple[float, dt.datetime, dt.datetime]] = []
    for a, b in free:
        if b - a < d:
            continue
        t = _ceil(a, step, tz)
        while t + d <= b:
            cands.append((_score(t, t + d, a, b, tz, first_day), t, t + d))
            t += dt.timedelta(minutes=step)
    cands.sort(key=lambda c: (c[0], c[1]))
    picks: list[tuple[float, dt.datetime, dt.datetime]] = []
    for c in cands:
        if len(picks) >= max(1, int(max_results)):
            break
        # Keep the options distinct: no pick may overlap one already chosen.
        if all(c[2] <= p[1] or c[1] >= p[2] for p in picks):
            picks.append(c)
    return [{"rank": i + 1, "start": iso_local(s, tz), "end": iso_local(e, tz), "minutes": dur,
             "label": slot_label(s, e, tz)} for i, (_, s, e) in enumerate(picks)]


def _ceil(t: dt.datetime, step: int, tz: dt.tzinfo) -> dt.datetime:
    """The first grid time at or after t, with the grid anchored to local wall-clock minutes."""
    loc = t.astimezone(tz)
    mins = loc.hour * 60 + loc.minute + (1 if (loc.second or loc.microsecond) else 0)
    up = -(-mins // step) * step
    base = dt.datetime.combine(loc.date(), dt.time()) + dt.timedelta(minutes=up)
    out = _localize(base, tz).astimezone(UTC)
    return out if out >= t else t


def _score(s: dt.datetime, e: dt.datetime, gap_a: dt.datetime, gap_b: dt.datetime, tz: dt.tzinfo, first_day: dt.date) -> float:
    ls, le = s.astimezone(tz), e.astimezone(tz)
    score = float((ls.date() - first_day).days) * 10.0
    sm, em = ls.hour * 60 + ls.minute, le.hour * 60 + le.minute + (24 * 60 if le.date() > ls.date() else 0)
    if sm < 12 * 60 + 0 and em > 12 * 60:  # straddles noon
        score += 3
    if sm < 13 * 60 and em > 12 * 60:  # touches the lunch hour
        score += 3
    if sm < 9 * 60 + 30 or em > 17 * 60 + 30:  # the raw edges of the day
        score += 2
    if ls.minute not in (0, 30):
        score += 1
    # Strand test: leaving under half an hour before or after in this gap wastes the sliver.
    before, after = (s - gap_a).total_seconds() / 60, (gap_b - e).total_seconds() / 60
    for rest in (before, after):
        if 0 < rest < 30:
            score += 1.5
    # Hugging an existing meeting keeps the rest of the day whole.
    if before == 0 or after == 0:
        score -= 0.5
    return score


def slot_label(s: dt.datetime, e: dt.datetime, tz: dt.tzinfo) -> str:
    a, b = s.astimezone(tz), e.astimezone(tz)
    return f"{a:%a %b} {a.day}, {_clock(a)}–{_clock(b)}"


def _clock(d: dt.datetime) -> str:
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d}{'am' if d.hour < 12 else 'pm'}" if d.minute else f"{h}{'am' if d.hour < 12 else 'pm'}"


# ---------------------------------------------------------------- change batches (calendar_propose)
_CHANGE_FIELDS = ("op", "event_id", "calendar_id", "summary", "start", "end", "attendees", "location", "description",
                  "recurrence", "conference", "send_updates")


def validate_changes(changes: Any, tz: dt.tzinfo = UTC) -> list[dict[str, Any]]:
    """Check a proposed batch and return it normalised. Raises ValueError naming the first problem.

    This runs on every execution, so a batch the user edited in the card is held to exactly the same
    rules as one the model wrote.
    """
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes must be a non-empty list")
    if len(changes) > MAX_CHANGES:
        raise ValueError(f"at most {MAX_CHANGES} changes per proposal")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(changes):
        where = f"changes[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object")
        c = {k: v for k, v in raw.items() if k in _CHANGE_FIELDS and v is not None}
        op = c.get("op")
        if op not in OPS:
            raise ValueError(f"{where}.op must be one of {', '.join(OPS)}")
        c.setdefault("calendar_id", "primary")
        if not isinstance(c["calendar_id"], str) or not c["calendar_id"]:
            raise ValueError(f"{where}.calendar_id must be a calendar id")
        if op in ("update", "delete") and not (isinstance(c.get("event_id"), str) and c["event_id"].strip()):
            raise ValueError(f"{where}: {op} needs the event_id from calendar_events")
        if op == "create":
            if not str(c.get("summary") or "").strip():
                raise ValueError(f"{where}: a new event needs a summary")
            if not c.get("start"):
                raise ValueError(f"{where}: a new event needs a start")
        if "attendees" in c:
            at = c["attendees"]
            if not isinstance(at, list) or not all(isinstance(a, str) and "@" in a for a in at):
                raise ValueError(f"{where}.attendees must be a list of email addresses")
        if "recurrence" in c and not (isinstance(c["recurrence"], list) and all(isinstance(r, str) for r in c["recurrence"])):
            raise ValueError(f"{where}.recurrence must be a list of RRULE strings")
        if "conference" in c and not isinstance(c["conference"], bool):
            raise ValueError(f"{where}.conference must be true or false")
        if "send_updates" in c and c["send_updates"] not in ("none", "all", "externalOnly"):
            raise ValueError(f"{where}.send_updates must be none, all or externalOnly")
        if op != "delete":
            _check_times(c, where, tz)
        out.append(c)
    return out


def _check_times(c: dict[str, Any], where: str, tz: dt.tzinfo) -> None:
    for k in ("start", "end"):
        if c.get(k) is None:
            continue
        if not isinstance(c[k], str):
            raise ValueError(f"{where}.{k} must be an ISO date or datetime")
        try:
            parse_dt(c[k], tz)
        except ValueError as e:
            raise ValueError(f"{where}.{k} {c[k]!r} is not an ISO date or datetime") from e
    if c.get("start") and c.get("end"):
        if is_date_only(c["start"]) != is_date_only(c["end"]):
            raise ValueError(f"{where}: start and end must both be dates or both be times")
        if parse_dt(c["end"], tz) < parse_dt(c["start"], tz):
            raise ValueError(f"{where}: end is before start")
    elif c.get("end") and not c.get("start") and c.get("op") == "create":
        raise ValueError(f"{where}: end without a start")


def change_fields(c: dict[str, Any]) -> dict[str, Any]:
    """A validated change as the flat event dict google.calendar_create/update take."""
    f: dict[str, Any] = {}
    for k in ("summary", "start", "end", "description", "location", "recurrence"):
        if c.get(k) is not None:
            f[k] = c[k]
    if c.get("attendees") is not None:
        f["attendees"] = [{"email": a} for a in c["attendees"]]
    if c.get("conference"):
        f["create_meet"] = True
    return f


def conflicts(changes: Sequence[dict[str, Any]], events: Sequence[dict[str, Any]], tz: dt.tzinfo = UTC) -> list[dict[str, Any]]:
    """Which timed creates/updates land on top of existing events (or on each other).

    Returns [{index, with: <event summary or 'change N'>}]. An update never conflicts with the event
    it is moving. Used by tests and mirrored client-side for the card's warning.
    """
    moving = {c.get("event_id") for c in changes if c.get("op") in ("update", "delete")}
    busy = [(e, event_interval(e, tz)) for e in events if blocks_time(e) and e.get("id") not in moving]
    spans: list[tuple[int, Interval]] = []
    out: list[dict[str, Any]] = []
    for i, c in enumerate(changes):
        if c.get("op") == "delete" or not c.get("start") or is_date_only(c["start"]):
            continue
        try:
            a = parse_dt(c["start"], tz).astimezone(UTC)
            b = parse_dt(c["end"], tz).astimezone(UTC) if c.get("end") else a + dt.timedelta(hours=1)
        except ValueError:
            continue
        for e, iv in busy:
            if iv and a < iv[1] and iv[0] < b and not (e.get("all_day")):
                out.append({"index": i, "with": e.get("summary") or "an event"})
        for j, (x, y) in spans:
            if a < y and x < b:
                out.append({"index": i, "with": f"change {j + 1}"})
        spans.append((i, (a, b)))
    return out
