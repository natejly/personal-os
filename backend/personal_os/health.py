"""Health tracking: a small catalog of metrics and a log of dated readings against them.

A metric says how its readings become one number per day, so every reader (the page, the Today
card, the assistant) agrees on what "today's sleep" is:
- `kind`: 'number' (any value), 'scale' (1-5, mood/energy), 'check' (0/1, took meds).
- `agg`: 'sum' (steps, water: readings add up), 'last' (weight: the latest reading wins),
  'avg' (mood logged twice in a day averages).
- `goal` + `goal_dir`: 'at_least' or 'at_most', or no goal. A day "meets" the goal on its daily value.

Readings are keyed by a local calendar `day` (YYYY-MM-DD) rather than a timestamp, so a 1am entry
for last night's sleep can be filed against the day the user means. Built-in metrics can be hidden
and re-tuned but not deleted; custom ones can be deleted along with their readings.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS health_metrics (
  key TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'number',
  agg TEXT NOT NULL DEFAULT 'last',
  goal REAL,
  goal_dir TEXT,
  decimals INTEGER NOT NULL DEFAULT 0,
  builtin INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS health_entries (
  id TEXT PRIMARY KEY,
  metric TEXT NOT NULL REFERENCES health_metrics(key) ON DELETE CASCADE,
  value REAL NOT NULL,
  day TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_entries_day ON health_entries(metric, day);
"""

KINDS = ("number", "scale", "check")
AGGS = ("sum", "last", "avg")
GOAL_DIRS = ("at_least", "at_most")

# (key, label, unit, kind, agg, goal, goal_dir, decimals)
DEFAULT_METRICS: list[tuple[str, str, str, str, str, float | None, str | None, int]] = [
    ("sleep", "Sleep", "h", "number", "sum", 8, "at_least", 1),
    ("steps", "Steps", "steps", "number", "sum", 8000, "at_least", 0),
    ("water", "Water", "glasses", "number", "sum", 8, "at_least", 0),
    ("exercise", "Exercise", "min", "number", "sum", 30, "at_least", 0),
    ("weight", "Weight", "lb", "number", "last", None, None, 1),
    ("resting_hr", "Resting heart rate", "bpm", "number", "last", None, None, 0),
    ("mood", "Mood", "/5", "scale", "avg", None, None, 1),
    ("energy", "Energy", "/5", "scale", "avg", None, None, 1),
    ("meds", "Meds taken", "", "check", "last", 1, "at_least", 0),
]


class HealthError(ValueError):
    """A reading or metric the store refuses; the message is safe to show the user."""


def parse_day(day: str | None, default: date | None = None) -> date:
    if not day:
        return default or date.today()
    try:
        return date.fromisoformat(day)
    except ValueError as e:
        raise HealthError(f"Dates are YYYY-MM-DD, got '{day}'.") from e


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return s[:40] or "metric"


# Readings the user (or the assistant on their behalf) typed in. Anything else came from a connected
# service, one row per (metric, day, source), replaced on every sync.
MANUAL_SOURCES = ("manual", "assistant")


def daily_value(agg: str, readings: list[dict[str, Any]]) -> float | None:
    """One day's readings (oldest first) as the metric's daily number.

    For a summed metric, each source's readings add up within that source (the user's own entries
    count as one source), and the day's value is the largest source total. Two descriptions of the
    same night or the same walk (a watch, the app it uploads to, the user typing it in) must never be
    added together; the cost is that a manual extra the watch missed does not stack on top of it."""
    if not readings:
        return None
    if agg == "sum":
        totals: dict[str, float] = {}
        for r in readings:
            src = r.get("source", "manual")
            key = "manual" if src in MANUAL_SOURCES else src
            totals[key] = totals.get(key, 0.0) + float(r["value"])
        return max(totals.values())
    vals = [float(r["value"]) for r in readings]
    if agg == "avg":
        return sum(vals) / len(vals)
    return vals[-1]


def meets(metric: dict[str, Any], value: float | None) -> bool | None:
    """Whether a daily value meets the metric's goal; None when there is no goal or no value."""
    if value is None or metric.get("goal") is None or metric.get("goal_dir") not in GOAL_DIRS:
        return None
    return value >= metric["goal"] if metric["goal_dir"] == "at_least" else value <= metric["goal"]


class Health:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            t = now()
            for i, (key, label, unit, kind, agg, goal, goal_dir, decimals) in enumerate(DEFAULT_METRICS):
                c.execute(
                    "INSERT OR IGNORE INTO health_metrics(key,label,unit,kind,agg,goal,goal_dir,decimals,builtin,hidden,position,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,1,0,?,?)",
                    (key, label, unit, kind, agg, goal, goal_dir, decimals, i * 10, t),
                )

    # ---- metrics ----
    def metrics(self, include_hidden: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM health_metrics" + ("" if include_hidden else " WHERE hidden = 0") + " ORDER BY position, created_at"
        with self.db.tx() as c:
            return [self._metric_out(r) for r in c.execute(sql).fetchall()]

    def metric(self, key: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM health_metrics WHERE key=?", (key,)).fetchone()
        return self._metric_out(r) if r else None

    def resolve(self, name: str) -> dict[str, Any] | None:
        """A metric by key, or by label ignoring case (the assistant says 'Sleep' as often as 'sleep')."""
        if m := self.metric(name):
            return m
        want = name.strip().lower()
        return next((m for m in self.metrics() if m["label"].lower() == want or _slug(m["label"]) == _slug(name)), None)

    @staticmethod
    def _metric_out(r: Any) -> dict[str, Any]:
        d = row_to_dict(r) or {}
        d["builtin"], d["hidden"] = bool(d["builtin"]), bool(d["hidden"])
        return d

    def create_metric(self, label: str, unit: str = "", kind: str = "number", agg: str | None = None,
                      goal: float | None = None, goal_dir: str | None = None, decimals: int = 0) -> dict[str, Any]:
        label = label.strip()
        if not label:
            raise HealthError("A metric needs a name.")
        if kind not in KINDS:
            raise HealthError(f"kind is one of {', '.join(KINDS)}.")
        agg = agg or ("avg" if kind == "scale" else "last")
        self._check_shape(agg, goal, goal_dir)
        base = key = _slug(label)
        n = 1
        while self.metric(key):
            n += 1
            key = f"{base}_{n}"
        with self.db.tx() as c:
            pos = (c.execute("SELECT COALESCE(MAX(position), 0) FROM health_metrics").fetchone()[0] or 0) + 10
            c.execute(
                "INSERT INTO health_metrics(key,label,unit,kind,agg,goal,goal_dir,decimals,builtin,hidden,position,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,0,0,?,?)",
                (key, label, unit.strip(), kind, agg, goal, goal_dir if goal is not None else None, int(decimals), pos, now()),
            )
        return self.metric(key)  # type: ignore[return-value]

    def update_metric(self, key: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Label, unit, goal, aggregation, precision, visibility and order. `kind` is fixed at creation:
        changing it would reinterpret every stored reading."""
        m = self.metric(key)
        if not m:
            return None
        fields = {k: v for k, v in patch.items() if k in {"label", "unit", "agg", "goal", "goal_dir", "decimals", "hidden", "position"}}
        if "label" in fields and not str(fields["label"]).strip():
            raise HealthError("A metric needs a name.")
        merged = {**m, **fields}
        if merged.get("goal") is None:
            fields["goal"] = fields["goal_dir"] = None
            merged["goal_dir"] = None
        self._check_shape(merged["agg"], merged.get("goal"), merged.get("goal_dir"))
        if "hidden" in fields:
            fields["hidden"] = int(bool(fields["hidden"]))
        if not fields:
            return m
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE health_metrics SET {sets} WHERE key=?", (*fields.values(), key))
        return self.metric(key)

    def delete_metric(self, key: str) -> None:
        m = self.metric(key)
        if not m:
            return
        if m["builtin"]:
            raise HealthError(f"'{m['label']}' is built in; hide it instead.")
        with self.db.tx() as c:
            c.execute("DELETE FROM health_entries WHERE metric=?", (key,))
            c.execute("DELETE FROM health_metrics WHERE key=?", (key,))

    @staticmethod
    def _check_shape(agg: str, goal: float | None, goal_dir: str | None) -> None:
        if agg not in AGGS:
            raise HealthError(f"agg is one of {', '.join(AGGS)}.")
        if goal is not None and goal_dir not in GOAL_DIRS:
            raise HealthError(f"A goal needs goal_dir: {' or '.join(GOAL_DIRS)}.")

    # ---- readings ----
    def _check_value(self, m: dict[str, Any], value: float) -> float:
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            raise HealthError("Value must be a finite number.")
        if m["kind"] == "scale" and not 1 <= v <= 5:
            raise HealthError(f"{m['label']} is a 1-5 scale.")
        if m["kind"] == "check" and v not in (0, 1):
            raise HealthError(f"{m['label']} is yes/no: 1 or 0.")
        if m["kind"] == "number" and v < 0:
            raise HealthError(f"{m['label']} can't be negative.")
        return v

    def log(self, metric: str, value: float, day: str | None = None, note: str = "", source: str = "manual") -> dict[str, Any]:
        m = self.metric(metric)
        if not m:
            raise HealthError(f"No metric '{metric}'.")
        v = self._check_value(m, value)
        d = parse_day(day).isoformat()
        eid = new_id()
        with self.db.tx() as c:
            # A yes/no metric has one answer per day: logging it again replaces the earlier one.
            if m["kind"] == "check":
                c.execute("DELETE FROM health_entries WHERE metric=? AND day=?", (metric, d))
            c.execute("INSERT INTO health_entries(id,metric,value,day,note,source,created_at) VALUES(?,?,?,?,?,?,?)",
                      (eid, metric, v, d, note.strip(), source, now()))
        return self.entry(eid)  # type: ignore[return-value]

    def upsert_synced(self, metric: str, day: str, value: float, source: str, note: str = "") -> bool:
        """A connected service's reading for one day: replaces that source's earlier reading for the
        same metric and day, so re-syncing never piles up. Returns whether anything changed."""
        if source in MANUAL_SOURCES:
            raise HealthError(f"'{source}' is reserved for entries the user makes.")
        m = self.metric(metric)
        if not m:
            raise HealthError(f"No metric '{metric}'.")
        v = self._check_value(m, value)
        d = parse_day(day).isoformat()
        with self.db.tx() as c:
            old = c.execute("SELECT value FROM health_entries WHERE metric=? AND day=? AND source=?", (metric, d, source)).fetchall()
            if len(old) == 1 and float(old[0]["value"]) == v:
                return False
            c.execute("DELETE FROM health_entries WHERE metric=? AND day=? AND source=?", (metric, d, source))
            c.execute("INSERT INTO health_entries(id,metric,value,day,note,source,created_at) VALUES(?,?,?,?,?,?,?)",
                      (new_id(), metric, v, d, note.strip(), source, now()))
        return True

    def entry(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM health_entries WHERE id=?", (id,)).fetchone())

    def entries(self, metric: str | None = None, since: str | None = None, until: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, args = [], []
        if metric:
            where.append("metric = ?")
            args.append(metric)
        if since:
            where.append("day >= ?")
            args.append(parse_day(since).isoformat())
        if until:
            where.append("day <= ?")
            args.append(parse_day(until).isoformat())
        sql = "SELECT * FROM health_entries" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY day DESC, created_at DESC LIMIT ?"
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute(sql, (*args, max(1, min(int(limit), 2000)))).fetchall()]  # type: ignore[misc]

    def update_entry(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        e = self.entry(id)
        if not e:
            return None
        fields: dict[str, Any] = {}
        if patch.get("value") is not None:
            fields["value"] = self._check_value(self.metric(e["metric"]) or {"kind": "number", "label": e["metric"]}, patch["value"])
        if patch.get("day") is not None:
            fields["day"] = parse_day(patch["day"]).isoformat()
        if patch.get("note") is not None:
            fields["note"] = str(patch["note"]).strip()
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self.db.tx() as c:
                c.execute(f"UPDATE health_entries SET {sets} WHERE id=?", (*fields.values(), id))
        return self.entry(id)

    def delete_entry(self, id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM health_entries WHERE id=?", (id,)).rowcount > 0

    # ---- rollups ----
    def _by_day(self, since: date, until: date, metrics: list[str] | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """metric -> day -> readings oldest first, for days in [since, until]."""
        sql = "SELECT metric, value, day, source, created_at FROM health_entries WHERE day >= ? AND day <= ?"
        args: list[Any] = [since.isoformat(), until.isoformat()]
        if metrics is not None:
            sql += f" AND metric IN ({','.join('?' * len(metrics))})"
            args += metrics
        out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        with self.db.tx() as c:
            for r in c.execute(sql + " ORDER BY created_at", args).fetchall():
                out.setdefault(r["metric"], {}).setdefault(r["day"], []).append(dict(r))
        return out

    def summary(self, days: int = 30, today: str | None = None, include_hidden: bool = False, metric: str | None = None) -> list[dict[str, Any]]:
        """Per metric: today's value, a daily series over `days` (oldest first, None where nothing was
        logged), the average of logged days in this window and the one before it, the goal streak, and
        the most recent reading at any date."""
        days = max(1, min(int(days), 366))
        end = parse_day(today)
        start = end - timedelta(days=days - 1)
        prev_start = start - timedelta(days=days)
        ms = [m for m in self.metrics(include_hidden) if metric is None or m["key"] == metric]
        if not ms:
            return []
        # Back far enough to count a streak longer than the window.
        rows = self._by_day(min(prev_start, end - timedelta(days=366)), end, [m["key"] for m in ms])
        out = []
        for m in ms:
            per_day = rows.get(m["key"], {})
            val = lambda d: daily_value(m["agg"], per_day.get(d.isoformat(), []))  # noqa: E731
            series = [{"day": (start + timedelta(days=i)).isoformat(), "value": val(start + timedelta(days=i))} for i in range(days)]
            prev = [val(prev_start + timedelta(days=i)) for i in range(days)]
            logged = [p["value"] for p in series if p["value"] is not None]
            prev_logged = [v for v in prev if v is not None]
            out.append({
                **m,
                "today": val(end),
                "series": series,
                "avg": sum(logged) / len(logged) if logged else None,
                "prev_avg": sum(prev_logged) / len(prev_logged) if prev_logged else None,
                "logged_days": len(logged),
                "met_days": sum(1 for v in logged if meets(m, v)),
                "streak": self._streak(m, val, end),
                "last": self._last(m["key"]),
            })
        return out

    @staticmethod
    def _streak(m: dict[str, Any], val: Any, end: date) -> int:
        """Consecutive days meeting the goal, ending today — or yesterday, while today is still open."""
        if m.get("goal") is None:
            return 0
        d = end if meets(m, val(end)) else end - timedelta(days=1)
        n = 0
        while n < 366 and meets(m, val(d)):
            n += 1
            d -= timedelta(days=1)
        return n

    def _last(self, metric: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT value, day FROM health_entries WHERE metric=? ORDER BY day DESC, created_at DESC LIMIT 1", (metric,)).fetchone()
        return dict(r) if r else None

    def today(self, today: str | None = None) -> list[dict[str, Any]]:
        """The Today card: each visible metric with today's value. One query."""
        d = parse_day(today)
        rows = self._by_day(d, d)
        return [{"key": m["key"], "label": m["label"], "unit": m["unit"], "kind": m["kind"], "goal": m["goal"],
                 "goal_dir": m["goal_dir"], "decimals": m["decimals"],
                 "today": daily_value(m["agg"], rows.get(m["key"], {}).get(d.isoformat(), []))}
                for m in self.metrics(include_hidden=False)]
