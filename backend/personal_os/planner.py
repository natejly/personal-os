"""Deterministic time-block planner: todos with estimates onto free work-hour calendar time.

Busy intervals out of work-hour windows, candidate slots of task duration, a fixed-weight score per slot,
greedy best pick. No Google, no db, no clock reads: `now` and
tz-naive local datetimes are injected, so the same input always gives the same plan. The output is a
proposal; nothing is written anywhere until the user applies it (modules/planner.py).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from . import todo_rules

Busy = tuple[datetime, datetime]
Block = dict[str, Any]

DEFAULT_CONFIG: dict[str, Any] = {
    "workStart": "09:00", "workEnd": "17:30", "workDays": [1, 2, 3, 4, 5],  # ISO weekdays, Mon=1
    "bufferMin": 10, "minBlockMin": 15, "maxBlockMin": 120, "slotStepMin": 15, "lookaheadDays": 7,
    "calendarName": "Grain Todos",
}
WEIGHTS = {"due": 0.40, "priority": 0.25, "energy": 0.20, "time": 0.15}
PRIORITY_SCORE = {1: 1.0, 2: 0.6, 3: 0.3}
DUE_DECAY_PER_DAY = 0.15
DEFAULT_ESTIMATE_MIN = 30
FOCUS_PREFIX = "Focus: "
_TODO_RE = re.compile(r"from todo (\S+)")


def _hm(s: str) -> tuple[int, int]:
    h, m = str(s).split(":")
    return int(h), int(m)


def validate_config(cfg: dict[str, Any]) -> str | None:
    """A message when the config is unusable, else None."""
    try:
        (sh, sm), (eh, em) = _hm(cfg["workStart"]), _hm(cfg["workEnd"])
        if not (0 <= sh < 24 and 0 <= eh < 24 and 0 <= sm < 60 and 0 <= em < 60) or (sh, sm) >= (eh, em):
            return "workStart must be before workEnd (HH:MM)"
    except (ValueError, KeyError, AttributeError):
        return "workStart and workEnd must be HH:MM"
    days = cfg.get("workDays")
    if not isinstance(days, list) or not days or any(not isinstance(d, int) or not 1 <= d <= 7 for d in days):
        return "workDays must be a non-empty list of 1 (Mon) to 7 (Sun)"
    for k, lo, hi in (("bufferMin", 0, 120), ("minBlockMin", 5, 240), ("maxBlockMin", 5, 480), ("slotStepMin", 5, 60), ("lookaheadDays", 1, 30)):
        v = cfg.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            return f"{k} must be an integer between {lo} and {hi}"
    if cfg["maxBlockMin"] < cfg["minBlockMin"]:
        return "maxBlockMin must be at least minBlockMin"
    return None


def _day_window(d: date, cfg: dict[str, Any]) -> tuple[datetime, datetime]:
    (sh, sm), (eh, em) = _hm(cfg["workStart"]), _hm(cfg["workEnd"])
    return datetime(d.year, d.month, d.day, sh, sm), datetime(d.year, d.month, d.day, eh, em)


def free_windows(busy: list[Busy], now: datetime, cfg: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    pad = timedelta(minutes=cfg["bufferMin"])
    padded = sorted((s - pad, e + pad) for s, e in busy if e > s)
    min_len = timedelta(minutes=cfg["minBlockMin"])
    out: list[tuple[datetime, datetime]] = []
    for i in range(cfg["lookaheadDays"]):
        day = now.date() + timedelta(days=i)
        if day.isoweekday() not in cfg["workDays"]:
            continue
        ws, we = _day_window(day, cfg)
        cur = max(ws, now)
        for bs, be in padded:
            if be <= cur or bs >= we:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < we:
            out.append((cur, we))
    return [(s, e) for s, e in out if e - s >= min_len]


def _local_naive(value: str) -> datetime:
    """Event times arrive as ISO strings, naive local or offset-aware; the planner works in naive local."""
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def busy_from_events(events: list[dict[str, Any]], mirror_calendar_ids: tuple[str, ...] | list[str] = ()) -> list[Busy]:
    """Blocking intervals from Google.calendar_events rows. Skips all-day, free ('transparent'), cancelled and
    declined events, and anything on the planner calendar (our own Focus blocks;
    they come back through locked_from_events)."""
    out: list[Busy] = []
    for e in events:
        if e.get("all_day") or e.get("transparency") == "transparent" or e.get("status") == "cancelled":
            continue
        if e.get("self_response") == "declined" or (e.get("calendar_id") and e["calendar_id"] in mirror_calendar_ids):
            continue
        if any(a.get("self") and a.get("response") == "declined" for a in e.get("attendee_details") or []):
            continue
        if not e.get("start") or not e.get("end") or len(str(e["start"])) == 10:
            continue
        out.append((_local_naive(e["start"]), _local_naive(e["end"])))
    return out


def locked_from_events(events: list[dict[str, Any]], mirror_calendar_ids: tuple[str, ...] | list[str]) -> list[Block]:
    """Focus blocks earlier accepted onto the planner calendar, as locked blocks (busy, and their todo is not re-planned)."""
    out: list[Block] = []
    for e in events:
        if e.get("calendar_id") not in mirror_calendar_ids or e.get("all_day") or not str(e.get("summary") or "").startswith(FOCUS_PREFIX):
            continue
        m = _TODO_RE.search(e.get("description") or "")
        out.append({"todo_id": m.group(1) if m else None, "title": e["summary"][len(FOCUS_PREFIX):],
                    "start": _local_naive(e["start"]).isoformat(timespec="minutes"), "end": _local_naive(e["end"]).isoformat(timespec="minutes"),
                    "score": 0.0, "part": [1, 1]})
    return out


def _due_eod(todo: dict[str, Any], now: datetime) -> datetime | None:
    if not todo.get("due"):
        return None
    d = max(date.fromisoformat(str(todo["due"])[:10]), now.date())  # an overdue todo is treated as due today
    return datetime(d.year, d.month, d.day) + timedelta(days=1)


def score_parts(todo: dict[str, Any], start: datetime, end: datetime, now: datetime, cfg: dict[str, Any], earliest: date | None = None) -> dict[str, float]:
    dl = _due_eod(todo, now)
    if dl is None:
        due = 0.5
    elif end > dl:
        due = 0.0
    else:
        due = max(0.0, 1.0 - DUE_DECAY_PER_DAY * max(0, (start.date() - (earliest or now.date())).days))
    prio = PRIORITY_SCORE.get(int(todo.get("priority") or 2), 0.3)
    morning = start.hour < 12
    long_task = (todo.get("estimate_min") or DEFAULT_ESTIMATE_MIN) >= 60
    energy = 1.0 if morning == long_task else 0.4  # long tasks want mornings, short ones afternoons
    if int(todo.get("priority") or 2) == 1:
        ws, we = _day_window(start.date(), cfg)
        time_pref = max(0.0, 1.0 - (start - ws).total_seconds() / max(1.0, (we - ws).total_seconds()))
    else:
        time_pref = 0.5
    return {"due": due, "priority": prio, "energy": energy, "time": time_pref}


def score_slot(todo: dict[str, Any], start: datetime, end: datetime, now: datetime, cfg: dict[str, Any], earliest: date | None = None) -> float:
    p = score_parts(todo, start, end, now, cfg, earliest)
    return round(sum(WEIGHTS[k] * p[k] for k in WEIGHTS), 4)


def _durations(todo: dict[str, Any], cfg: dict[str, Any]) -> list[int]:
    est = int(todo.get("estimate_min") or DEFAULT_ESTIMATE_MIN)
    lo, hi = cfg["minBlockMin"], cfg["maxBlockMin"]
    if est <= hi:
        return [max(lo, est)]
    n = math.ceil(est / hi)
    return [hi] * (n - 1) + [max(lo, est - hi * (n - 1))]


def _candidate_starts(ws: datetime, we: datetime, dur: timedelta, step: int) -> list[datetime]:
    starts = {ws}
    mid = datetime(ws.year, ws.month, ws.day)
    k = math.ceil((ws - mid).total_seconds() / 60 / step)
    t = mid + timedelta(minutes=k * step)
    while t + dur <= we:
        starts.add(t)
        t += timedelta(minutes=step)
    return sorted(s for s in starts if s + dur <= we)


def _subtract(windows: list[tuple[datetime, datetime]], hole: Busy, min_len: timedelta) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    hs, he = hole
    for s, e in windows:
        if he <= s or hs >= e:
            out.append((s, e))
            continue
        if hs > s:
            out.append((s, hs))
        if he < e:
            out.append((he, e))
    return [(s, e) for s, e in out if e - s >= min_len]


def plan(todos: list[dict[str, Any]], busy: list[Busy], now: datetime, cfg: dict[str, Any], locked: list[Block] | None = None) -> dict[str, Any]:
    """Greedy plan: most urgent todo first, each part on its best-scoring free slot. Returns
    {'blocks': [...], 'unplaced': [{id, reason}], 'already_planned': [todo ids]}."""
    locked = locked or []
    pad = timedelta(minutes=cfg["bufferMin"])
    min_len = timedelta(minutes=cfg["minBlockMin"])
    all_busy = list(busy) + [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in locked]
    windows = free_windows(all_busy, now, cfg)
    planned_ids = {b.get("todo_id") for b in locked if b.get("todo_id")}
    today = now.date()
    order = sorted((t for t in todos if not t.get("done")),
                   key=lambda t: (-todo_rules.urgency_of(t, today), t.get("due") or "9999-12-31", int(t.get("priority") or 2), str(t.get("id"))))
    blocks: list[Block] = []
    unplaced: list[dict[str, Any]] = []
    already: list[str] = []
    for t in order:
        if t["id"] in planned_ids:
            already.append(t["id"])
            continue
        if not t.get("due") and not t.get("estimate_min"):
            continue  # nothing to schedule it by: leave it on the list
        durs = _durations(t, cfg)
        dl = _due_eod(t, now)
        placed: list[Block] = []
        for i, d in enumerate(durs, 1):
            dur = timedelta(minutes=d)
            cands = [(s, s + dur) for ws, we in windows for s in _candidate_starts(ws, we, dur, cfg["slotStepMin"])]
            if dl is not None:
                cands = [c for c in cands if c[1] <= dl]
            if not cands:
                break
            earliest = min(c[0] for c in cands).date()
            best = max(cands, key=lambda c: (score_slot(t, c[0], c[1], now, cfg, earliest), -c[0].timestamp()))
            parts = score_parts(t, best[0], best[1], now, cfg, earliest)
            placed.append({"todo_id": t["id"], "title": t["title"], "start": best[0].isoformat(timespec="minutes"), "end": best[1].isoformat(timespec="minutes"),
                           "score": score_slot(t, best[0], best[1], now, cfg, earliest), "part": [i, len(durs)],
                           "why": {k: round(WEIGHTS[k] * v, 3) for k, v in parts.items()}})
            windows = _subtract(windows, (best[0] - pad, best[1] + pad), min_len)
        if len(placed) < len(durs):
            unplaced.append({"id": t["id"], "reason": "no_slot_before_due" if dl is not None else "no_free_time"})
        blocks.extend(placed)
    blocks.sort(key=lambda b: (b["start"], b["todo_id"]))
    return {"blocks": blocks, "unplaced": unplaced, "already_planned": already}
