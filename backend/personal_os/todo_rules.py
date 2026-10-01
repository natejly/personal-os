"""Pure todo rules: repeat arithmetic (from due date or from completion) and a weighted urgency score.

No db, no clock reads: callers inject `today` / `completed_on`, so every function is testable offline.

repeat = {"every": int 1..366, "unit": "day"|"week"|"month"|"year", "mode": "from_due"|"from_completion"}
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Any

UNITS = ("day", "week", "month", "year")
MODES = ("from_due", "from_completion")

# Urgency coefficients, in one place to tune. `due` is scaled by a ramp (see urgency()).
DEFAULT_COEFFS: dict[str, Any] = {
    "due": 12.0,
    "priority": {1: 6.0, 2: 3.9, 3: 1.8},
    "age": 2.0,
    "age_max_days": 365,
    "notes": 1.0 * 0.8,
    "project": 1.0,
}


def parse_repeat(raw: Any) -> dict[str, Any] | None:
    """Normalise a repeat spec (dict or JSON string); None/empty clears. Raises ValueError when invalid."""
    if raw in (None, "", {}):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            raise ValueError(f"repeat is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("repeat must be an object")
    unit = raw.get("unit")
    if unit not in UNITS:
        raise ValueError(f"repeat unit must be one of {', '.join(UNITS)}")
    try:
        every = int(raw.get("every", 1))
    except (TypeError, ValueError) as e:
        raise ValueError("repeat every must be an integer") from e
    if every < 1:
        raise ValueError("repeat every must be at least 1")
    mode = raw.get("mode") or "from_due"
    if mode not in MODES:
        raise ValueError(f"repeat mode must be one of {', '.join(MODES)}")
    return {"every": min(every, 366), "unit": unit, "mode": mode}


def add_months(d: date, months: int) -> date:
    """d plus whole months by month-index arithmetic, clamping the day to the month's end."""
    idx = d.year * 12 + (d.month - 1) + months
    y, m = divmod(idx, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _step(repeat: dict[str, Any]) -> tuple[int, int]:
    """(days, months) of one interval; exactly one is non-zero."""
    n, unit = repeat["every"], repeat["unit"]
    return {"day": (n, 0), "week": (7 * n, 0), "month": (0, n), "year": (0, 12 * n)}[unit]


def next_due(due: date | None, repeat: dict[str, Any], completed_on: date) -> date:
    """The next due date after completing on `completed_on`.

    from_due: smallest k >= 1 intervals past `due` that lands strictly after completion, by arithmetic
    (never a loop over missed occurrences). from_completion: completion plus one interval.
    """
    rep = parse_repeat(repeat)
    if rep is None:
        raise ValueError("no repeat")
    days, months = _step(rep)
    if rep["mode"] == "from_completion":
        return completed_on + timedelta(days=days) if days else add_months(completed_on, months)
    base = due or completed_on
    if days:
        k = max(1, (completed_on - base).days // days + 1)
        return base + timedelta(days=k * days)
    k = max(1, ((completed_on.year * 12 + completed_on.month) - (base.year * 12 + base.month)) // months)
    while add_months(base, k * months) <= completed_on:  # clamping can leave the estimate one short
        k += 1
    return add_months(base, k * months)


def _due_ramp(days_until: int) -> float:
    if days_until <= -7:
        return 1.0
    if days_until > 14:
        return 0.0
    return 0.2 + 0.8 * (14 - days_until) / 21


def urgency(todo: dict[str, Any], today: date, blocked_count: int = 0, blocking_count: int = 0,
            coeffs: dict[str, Any] | None = None) -> float:
    """Explainable priority number; higher means do it sooner. Done todos score 0.

    blocked_count / blocking_count are accepted for when dependencies exist; they carry no weight yet.
    """
    c = coeffs or DEFAULT_COEFFS
    if todo.get("done"):
        return 0.0
    x = 0.0
    if todo.get("due"):
        x += c["due"] * _due_ramp((date.fromisoformat(str(todo["due"])[:10]) - today).days)
    x += c["priority"].get(int(todo.get("priority") or 2), 0.0)
    created = todo.get("created_at")
    if created:
        age = max(0, (today - date.fromtimestamp(float(created))).days)
        x += c["age"] * min(1.0, age / c["age_max_days"])
    if (todo.get("notes") or "").strip():
        x += c["notes"]
    if todo.get("project_id"):
        x += c["project"]
    return round(x, 3)
