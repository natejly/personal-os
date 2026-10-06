"""Insights: the habits worth remembering, and the automations worth offering.

The activity monitor answers "what happened". This module answers the two questions that come
after it:

  - what does this person do *over and over*?  -> habits, written into the app's own memory
  - what could the app do for them instead?    -> suggestions, proposed and never applied

Four layers, in order:

  day stats  a per-day aggregate of counts only (seconds per app, visits per host, keystrokes),
             merged monotonically so it outlives the 48h raw-sample retention. This is what makes
             "every weekday morning" detectable at all: raw events are gone by then.
  mining     deterministic pattern detection over the day stats plus whatever raw events are still
             held. No model, no network - it works with the proxy down and it is what the panel
             shows you as evidence.
  the pass   one LLM call that turns patterns into habit statements and concrete suggestions, told
             exactly what this app can actually do so it cannot propose fiction.
  the ledger suggestions with a status. Dismissed is forever: a refresh may re-score a suggestion
             but never resurrect one you said no to.

Nothing here acts on its own. A suggestion is a proposal; applying one is always a button the user
pressed, and the only writes a refresh makes by itself are to the habit memories (which the panel
lists with a Forget button beside each one).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import datetime
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from . import redact
from .db import Database, new_id, now, row_to_dict
from .repos import Memories
from .todos import Todos

log = logging.getLogger("personal_os.insights")

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "everyHours": 12,       # how often the pass runs on its own
    "lookbackDays": 21,     # how much day-stats history mining reads
    "minDays": 2,           # a pattern has to show up on at least this many days to count
    "maxSuggestions": 8,
    "autoMemory": False,    # write high-confidence habits into the app's memory
    "memoryConfidence": 0.6,
}

# A suggestion the user has ruled on. A refresh re-scores these but never moves them back to new.
STICKY = ("accepted", "done", "dismissed")
STATUSES = ("new", "accepted", "done", "dismissed", "snoozed")

# Memories this module owns. Only rows with this source are ever edited or deleted by it.
MEMORY_SOURCE = "activity"

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_day_stats (
  day TEXT PRIMARY KEY,            -- local YYYY-MM-DD
  apps TEXT NOT NULL DEFAULT '{}',    -- app -> focused seconds
  hosts TEXT NOT NULL DEFAULT '{}',   -- browser host -> visits
  hours TEXT NOT NULL DEFAULT '{}',   -- local hour "0".."23" -> focused seconds
  typing TEXT NOT NULL DEFAULT '{}',  -- app -> keystrokes
  cats TEXT NOT NULL DEFAULT '{}',    -- category path ("Work/Coding", every ancestor too) -> focused seconds
  switches INTEGER NOT NULL DEFAULT 0,
  keys INTEGER NOT NULL DEFAULT 0,
  clicks INTEGER NOT NULL DEFAULT 0,
  scrolls INTEGER NOT NULL DEFAULT 0,
  focus_seconds REAL NOT NULL DEFAULT 0,
  idle_seconds REAL NOT NULL DEFAULT 0,
  first_ts REAL NOT NULL DEFAULT 0,
  last_ts REAL NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity_habits (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,        -- stable slug, so a re-run updates instead of duplicating
  statement TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'preference',
  confidence REAL NOT NULL DEFAULT 0,
  support INTEGER NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL DEFAULT '[]',
  memory_id TEXT NOT NULL DEFAULT '',   -- the memory row this habit owns, '' when not written
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_suggestions (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'automation',   -- automation | platform | hygiene
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '',
  impact TEXT NOT NULL DEFAULT '',
  effort TEXT NOT NULL DEFAULT 'low',
  action TEXT NOT NULL DEFAULT '{}',
  evidence TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  status_note TEXT NOT NULL DEFAULT '',
  snooze_until REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actsug_status ON activity_suggestions(status, confidence DESC);

CREATE TABLE IF NOT EXISTS activity_patterns (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  content TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------- small helpers

def _model_conf(value: Any, default: float = 0.5) -> float:
    """A model-supplied confidence, clamped to 0..1; a word like "high" falls back to `default`."""
    try:
        return max(0.0, min(1.0, float(value if value is not None else default)))
    except (TypeError, ValueError):
        return default


def _items(value: Any, cap: int) -> list[dict[str, Any]]:
    """The dict items of a model-supplied list. Anything else in there is skipped, not fatal."""
    return [x for x in value if isinstance(x, dict)][:cap] if isinstance(value, list) else []


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _hour_of(ts: float) -> int:
    return datetime.fromtimestamp(ts).hour


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _mins(seconds: float) -> str:
    m = seconds / 60.0
    if m < 1:
        return f"{int(seconds)}s"
    if m < 60:
        return f"{m:.0f}m"
    return f"{m / 60:.1f}h"


def _hhmm(minutes_of_day: float) -> str:
    h, m = divmod(int(round(minutes_of_day)), 60)
    return f"{h % 24:02d}:{m:02d}"


def _host(url: str) -> str:
    """Host only - never the path or query.

    A path says *which document*, a query says *what was searched*. Neither is needed to notice
    "they open this site every morning", and both are exactly the kind of thing that should not
    end up in a habit statement or a prompt sent to a model.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    h = (parts.hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _slug(text: str, prefix: str = "") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48]
    return f"{prefix}{s}" if prefix else s


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


def _top(d: dict[str, float], n: int) -> list[tuple[str, float]]:
    return sorted(d.items(), key=lambda kv: -kv[1])[:n]


def _conf(days: int, window: int, support: int) -> float:
    """How much a pattern is worth believing: mostly recurrence, a little volume.

    Recurrence is what separates a habit from a Tuesday. A pattern seen on 5 of 7 days outranks one
    with a thousand samples on a single day, and the volume term only breaks ties.
    """
    recur = min(1.0, days / max(1.0, min(window, 7)))
    vol = min(1.0, support / 20.0)
    return round(min(0.97, 0.2 + 0.6 * recur + 0.2 * vol), 2)


def _band(hours: dict[int, float], share: float = 0.6) -> tuple[int, int] | None:
    """The tightest contiguous run of hours holding `share` of the time, or None if it is flat."""
    total = sum(hours.values())
    if total <= 0:
        return None
    best: tuple[int, int, int] | None = None   # (width, start, end)
    for start in range(24):
        acc = 0.0
        for width in range(1, 25):
            acc += hours.get((start + width - 1) % 24, 0.0)
            if acc >= share * total:
                if best is None or width < best[0]:
                    best = (width, start, (start + width - 1) % 24)
                break
    if not best or best[0] >= 12:   # spread over half the day is not a band
        return None
    return best[1], best[2]


# ---------------------------------------------------------------- day stats


class DayStats:
    """Per-day aggregates, merged so they only ever grow.

    Raw samples are deleted after 48 hours, so anything that wants to say "most weekday mornings"
    has to have written down the shape of the day while the samples were still there. Every merge
    takes the max of what is stored and what the current events add up to, which means re-mining
    the same day is idempotent and a retention sweep can never shrink history.
    """

    NUM = ("switches", "keys", "clicks", "scrolls", "focus_seconds", "idle_seconds")
    MAPS = ("apps", "hosts", "hours", "typing", "cats")

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            # Tables made before categories existed lack the column; the CREATE above skips them.
            if "cats" not in {r["name"] for r in c.execute("PRAGMA table_info(activity_day_stats)").fetchall()}:
                c.execute("ALTER TABLE activity_day_stats ADD COLUMN cats TEXT NOT NULL DEFAULT '{}'")

    def merge(self, day: str, stats: dict[str, Any]) -> None:
        cur = self.get(day) or {}
        out: dict[str, Any] = {}
        for k in self.MAPS:
            old, new = dict(cur.get(k) or {}), dict(stats.get(k) or {})
            out[k] = {key: max(float(old.get(key, 0)), float(new.get(key, 0))) for key in set(old) | set(new)}
        for k in self.NUM:
            out[k] = max(float(cur.get(k) or 0), float(stats.get(k) or 0))
        first_new, first_old = float(stats.get("first_ts") or 0), float(cur.get("first_ts") or 0)
        out["first_ts"] = min(x for x in (first_new, first_old) if x) if (first_new or first_old) else 0.0
        out["last_ts"] = max(float(stats.get("last_ts") or 0), float(cur.get("last_ts") or 0))
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO activity_day_stats(day,apps,hosts,hours,typing,cats,switches,keys,clicks,scrolls,
                                                  focus_seconds,idle_seconds,first_ts,last_ts,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET apps=excluded.apps, hosts=excluded.hosts, hours=excluded.hours,
                     typing=excluded.typing, cats=excluded.cats, switches=excluded.switches, keys=excluded.keys, clicks=excluded.clicks,
                     scrolls=excluded.scrolls, focus_seconds=excluded.focus_seconds, idle_seconds=excluded.idle_seconds,
                     first_ts=excluded.first_ts, last_ts=excluded.last_ts, updated_at=excluded.updated_at""",
                (day, json.dumps(out["apps"]), json.dumps(out["hosts"]), json.dumps(out["hours"]),
                 json.dumps(out["typing"]), json.dumps(out["cats"]), int(out["switches"]), int(out["keys"]), int(out["clicks"]),
                 int(out["scrolls"]), out["focus_seconds"], out["idle_seconds"], out["first_ts"],
                 out["last_ts"], now()),
            )

    def get(self, day: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM activity_day_stats WHERE day=?", (day,)).fetchone(),
                               self.MAPS)

    def recent(self, days: int = 21) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM activity_day_stats ORDER BY day DESC LIMIT ?", (int(days),)).fetchall()
        return [row_to_dict(r, self.MAPS) for r in rows]  # type: ignore[misc]

    def purge(self, keep_days: float = 90.0, *, everything: bool = False) -> int:
        with self.db.tx() as c:
            if everything:
                return max(0, c.execute("DELETE FROM activity_day_stats").rowcount)
            cutoff = _day_of(now() - max(1.0, keep_days) * 86400)
            return max(0, c.execute("DELETE FROM activity_day_stats WHERE day < ?", (cutoff,)).rowcount)


def day_stats_from_events(events: Iterable[dict[str, Any]], engine: Any = None) -> dict[str, dict[str, Any]]:
    """Fold raw events into one aggregate per local day. Counts only; no titles, no URLs, no text.

    `engine` is an activity_categories.CategoryEngine; titles and URLs are classified and then
    dropped - only the category path and its seconds are kept.
    """
    if engine is None:
        from .activity_categories import engine_for
        engine = engine_for(None)
    out: dict[str, dict[str, Any]] = {}
    last_app_by_day: dict[str, str] = {}
    for e in sorted(events, key=lambda x: x["ts"]):
        day = _day_of(e["ts"])
        d = out.setdefault(day, {"apps": {}, "hosts": {}, "hours": {}, "typing": {}, "cats": {},
                                 "switches": 0, "keys": 0, "clicks": 0, "scrolls": 0, "focus_seconds": 0.0,
                                 "idle_seconds": 0.0, "first_ts": 0.0, "last_ts": 0.0})
        d["first_ts"] = min(d["first_ts"], e["ts"]) if d["first_ts"] else e["ts"]
        d["last_ts"] = max(d["last_ts"], e["ts"] + e.get("duration_ms", 0) / 1000.0)
        meta = e.get("meta") or {}
        if e["kind"] == "focus":
            secs = float(e.get("duration_ms") or 0) / 1000.0
            app = e.get("app") or "?"
            d["apps"][app] = d["apps"].get(app, 0.0) + secs
            d["hours"][str(_hour_of(e["ts"]))] = d["hours"].get(str(_hour_of(e["ts"])), 0.0) + secs
            d["focus_seconds"] += secs
            path = engine.classify(app, e.get("title") or "", e.get("url") or "")
            for i in range(1, len(path) + 1):
                ck = "/".join(path[:i])
                d["cats"][ck] = d["cats"].get(ck, 0.0) + secs
            host = _host(e.get("url") or "")
            if host:
                d["hosts"][host] = d["hosts"].get(host, 0.0) + 1
            if last_app_by_day.get(day) and last_app_by_day[day] != app:
                d["switches"] += 1
            last_app_by_day[day] = app
        elif e["kind"] == "input":
            keys = int(meta.get("keys") or 0)
            d["keys"] += keys
            d["clicks"] += int(meta.get("clicks") or 0)
            d["scrolls"] += int(meta.get("scrolls") or 0)
            if keys and e.get("app"):
                d["typing"][e["app"]] = d["typing"].get(e["app"], 0) + keys
        elif e["kind"] == "idle":
            d["idle_seconds"] += float(meta.get("since_seconds") or 0)
    return out


# ---------------------------------------------------------------- mining


def _pattern(kind: str, title: str, detail: str, *, support: int, days: int, window: int,
             evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": f"{kind}:{_slug(title)}" or kind, "kind": kind, "title": title, "detail": detail,
            "support": int(support), "days": int(days), "confidence": _conf(days, window, support),
            "evidence": evidence or {}}


def _focus_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive focus events on the same app into uninterrupted stretches."""
    runs: list[dict[str, Any]] = []
    for e in events:
        if e["kind"] != "focus":
            continue
        secs = float(e.get("duration_ms") or 0) / 1000.0
        app = e.get("app") or "?"
        if runs and runs[-1]["app"] == app and e["ts"] - runs[-1]["end"] < 90:
            runs[-1]["end"] = e["ts"] + secs
            runs[-1]["seconds"] += secs
        else:
            runs.append({"app": app, "start": e["ts"], "end": e["ts"] + secs, "seconds": secs})
    return runs


def _cat_leaves(cats: dict[str, float]) -> dict[str, float]:
    """The seconds counted once: drop the ancestor rows a rollup added."""
    return {k: v for k, v in cats.items() if not any(o.startswith(k + "/") for o in cats if o != k)}


def mine(*, events: list[dict[str, Any]], days: list[dict[str, Any]],
         summaries: list[dict[str, Any]], min_days: int = 2, engine: Any = None) -> dict[str, Any]:
    """Everything deterministic we can say about how this person works. No model involved."""
    if engine is None:
        from .activity_categories import engine_for
        engine = engine_for(None)
    window = max(1, len(days))
    apps_total: dict[str, float] = {}
    hosts_total: dict[str, float] = {}
    hours_total: dict[int, float] = {}
    typing_total: dict[str, float] = {}
    app_days: dict[str, int] = {}
    host_days: dict[str, int] = {}
    totals = {"focus_seconds": 0.0, "idle_seconds": 0.0, "keys": 0, "clicks": 0, "scrolls": 0, "switches": 0}
    starts: list[float] = []
    ends: list[float] = []
    focus_per_day: list[float] = []
    cats_total: dict[str, float] = {}
    cat_days: dict[str, int] = {}

    for d in days:
        for cat, secs in (d.get("cats") or {}).items():
            cats_total[cat] = cats_total.get(cat, 0.0) + float(secs)
            cat_days[cat] = cat_days.get(cat, 0) + 1
        for app, secs in (d.get("apps") or {}).items():
            apps_total[app] = apps_total.get(app, 0.0) + float(secs)
            app_days[app] = app_days.get(app, 0) + 1
        for host, n in (d.get("hosts") or {}).items():
            hosts_total[host] = hosts_total.get(host, 0.0) + float(n)
            host_days[host] = host_days.get(host, 0) + 1
        for hour, secs in (d.get("hours") or {}).items():
            hours_total[int(hour)] = hours_total.get(int(hour), 0.0) + float(secs)
        for app, keys in (d.get("typing") or {}).items():
            typing_total[app] = typing_total.get(app, 0.0) + float(keys)
        for k in totals:
            totals[k] += float(d.get(k) or 0)   # type: ignore[literal-required]
        if d.get("first_ts"):
            dt = datetime.fromtimestamp(float(d["first_ts"]))
            starts.append(dt.hour * 60 + dt.minute)
        if d.get("last_ts"):
            dt = datetime.fromtimestamp(float(d["last_ts"]))
            ends.append(dt.hour * 60 + dt.minute)
        focus_per_day.append(float(d.get("focus_seconds") or 0))

    patterns: list[dict[str, Any]] = []

    # 1. which apps own the day, and when
    for app, secs in _top(apps_total, 12):
        nd = app_days.get(app, 0)
        if nd < min_days or secs < 600:
            continue
        # Approximate: the day aggregate keeps hours and apps as separate histograms, not a
        # per-app-per-hour grid (which would be apps x 24 numbers a day for a band nobody reads to
        # the minute). So the app's share of each day is spread across that day's hour histogram.
        # It places a morning app in the morning, which is all the band is for.
        per_app_hours = {h: 0.0 for h in range(24)}
        for d in days:
            if app in (d.get("apps") or {}):
                share = float(d["apps"][app]) / max(1.0, float(d.get("focus_seconds") or 1))
                for hour, hs in (d.get("hours") or {}).items():
                    per_app_hours[int(hour)] += float(hs) * share
        band = _band(per_app_hours)
        when = f", mostly {band[0]:02d}:00-{(band[1] + 1) % 24:02d}:00" if band else ""
        patterns.append(_pattern(
            "app_routine", f"{app} on {nd} of {window} days{when}",
            f"{_mins(secs)} of attention in {app} across {nd} days ({_mins(secs / nd)} a day).",
            support=int(secs / 60), days=nd, window=window,
            evidence={"app": app, "seconds": round(secs), "days": nd, "band": band},
        ))

    # 2. sites they keep going back to (host only)
    for host, visits in _top(hosts_total, 10):
        nd = host_days.get(host, 0)
        per_day = visits / max(1, nd)
        if nd < min_days or per_day < 3:
            continue
        patterns.append(_pattern(
            "site_habit", f"Opens {host} ~{per_day:.0f}x a day",
            f"{int(visits)} visits to {host} over {nd} days. A recurring check, not a one-off lookup.",
            support=int(visits), days=nd, window=window,
            evidence={"host": host, "visits": int(visits), "days": nd, "per_day": round(per_day, 1)},
        ))

    # 3. ping-pong between two apps: the cheapest thing in here to fix, and the most annoying
    runs = _focus_runs(events)
    pair_counts: dict[tuple[str, str], int] = {}
    dwell: dict[str, list[float]] = {}
    for a, b in zip(runs, runs[1:]):
        if a["app"] == b["app"]:
            continue
        pair_counts[(a["app"], b["app"])] = pair_counts.get((a["app"], b["app"]), 0) + 1
        dwell.setdefault(b["app"], []).append(b["seconds"])
    seen_pairs: set[frozenset[str]] = set()
    for (a, b), n in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
        back = pair_counts.get((b, a), 0)
        trips = min(n, back)
        pair = frozenset((a, b))
        if trips < 4 or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        med = statistics.median(dwell.get(b) or [0]) if dwell.get(b) else 0
        if med > 180:                      # a real stretch in the other app is work, not thrash
            continue
        patterns.append(_pattern(
            "thrash", f"Bounces {a} <-> {b} {trips}x",
            f"{trips} round trips between {a} and {b}, median {int(med)}s in {b} before going back. "
            "That shape usually means waiting on something, or copying between the two.",
            support=trips * 2, days=1, window=window,
            evidence={"apps": [a, b], "round_trips": trips, "median_dwell_seconds": int(med)},
        ))
        if len(seen_pairs) >= 3:
            break

    # 4. where the long uninterrupted stretches actually land
    deep = [r for r in runs if r["seconds"] >= 900]
    if deep:
        hist = {h: 0.0 for h in range(24)}
        for r in deep:
            hist[_hour_of(r["start"])] += r["seconds"]
        band = _band(hist, 0.7)
        longest = max(deep, key=lambda r: r["seconds"])
        when = f"{band[0]:02d}:00-{(band[1] + 1) % 24:02d}:00" if band else "no clear window"
        patterns.append(_pattern(
            "deep_work", f"Deep work lands {when}",
            f"{len(deep)} stretches of 15 minutes or more without switching apps; the longest was "
            f"{_mins(longest['seconds'])} in {longest['app']}.",
            support=len(deep), days=len({_day_of(r['start']) for r in deep}), window=window,
            evidence={"stretches": len(deep), "band": band, "longest_app": longest["app"],
                      "longest_seconds": round(longest["seconds"])},
        ))

    # 5. the shape of a typical day
    if starts and ends and len(starts) >= min_days:
        med_start, med_end = statistics.median(starts), statistics.median(ends)
        med_focus = statistics.median(focus_per_day) if focus_per_day else 0
        patterns.append(_pattern(
            "day_shape", f"A typical day runs {_hhmm(med_start)}-{_hhmm(med_end)}",
            f"Median first activity {_hhmm(med_start)}, last {_hhmm(med_end)}, "
            f"{_mins(med_focus)} actually at the keyboard.",
            support=len(starts), days=len(starts), window=window,
            evidence={"median_start": _hhmm(med_start), "median_end": _hhmm(med_end),
                      "median_focus_seconds": round(med_focus)},
        ))

    # 6. after-hours load
    off = sum(s for h, s in hours_total.items() if h >= 19 or h < 8)
    tot = sum(hours_total.values())
    if tot > 3600 and off / tot >= 0.2:
        patterns.append(_pattern(
            "after_hours", f"{off / tot * 100:.0f}% of screen time is outside 08:00-19:00",
            f"{_mins(off)} of {_mins(tot)} lands in the evening or early morning.",
            support=int(off / 60), days=window, window=window,
            evidence={"off_hours_share": round(off / tot, 2), "off_hours_seconds": round(off)},
        ))

    # 7. where the typing happens
    if typing_total:
        app, keys = _top(typing_total, 1)[0]
        if keys >= 500:
            share = keys / max(1.0, sum(typing_total.values()))
            patterns.append(_pattern(
                "input_load", f"Most typing happens in {app}",
                f"{int(keys)} keystrokes in {app}, {share * 100:.0f}% of everything typed.",
                support=int(keys / 100), days=app_days.get(app, 1), window=window,
                evidence={"app": app, "keys": int(keys), "share": round(share, 2)},
            ))

    # 8. documents and windows they keep coming back to
    title_days: dict[str, set[str]] = {}
    for e in events:
        if e["kind"] == "focus" and len(e.get("title") or "") >= 8:
            title_days.setdefault(e["title"][:120], set()).add(_day_of(e["ts"]))
    for title, ds in sorted(title_days.items(), key=lambda kv: -len(kv[1]))[:3]:
        if len(ds) < max(2, min_days):
            continue
        patterns.append(_pattern(
            "recurring_window", f"Keeps returning to \"{title[:60]}\"",
            f"The same window came back on {len(ds)} separate days.",
            support=len(ds), days=len(ds), window=window, evidence={"title": title[:120], "days": len(ds)},
        ))

    # 9. topics the summaries keep naming (these outlive the raw samples)
    topic_days: dict[str, set[str]] = {}
    for s in summaries:
        m = re.search(r"^Topics:\s*(.+)$", s.get("body") or "", re.M)
        if not m:
            continue
        for t in m.group(1).split(","):
            t = t.strip()
            if len(t) >= 3:
                topic_days.setdefault(t, set()).add(s["day"])
    span = len({s["day"] for s in summaries}) or 1
    for topic, ds in sorted(topic_days.items(), key=lambda kv: -len(kv[1]))[:6]:
        if len(ds) < min_days:
            continue
        patterns.append(_pattern(
            "topic", f"\"{topic}\" on {len(ds)} of {span} days",
            f"{topic} keeps coming back across days - a thread, not an errand.",
            support=len(ds) * 3, days=len(ds), window=window, evidence={"topic": topic, "days": len(ds)},
        ))

    # 10. how fragmented the attention is
    focus_hours = totals["focus_seconds"] / 3600.0
    if focus_hours >= 1 and totals["switches"]:
        rate = totals["switches"] / focus_hours
        if rate >= 30:
            patterns.append(_pattern(
                "switch_rate", f"{rate:.0f} app switches an hour",
                f"{int(totals['switches'])} switches over {focus_hours:.1f}h of focused time - "
                f"a new window roughly every {60 / rate:.1f} minutes.",
                support=int(totals["switches"]), days=window, window=window,
                evidence={"switches_per_hour": round(rate, 1)},
            ))

    # 11. where the time goes by category, and the evenings that drift to the negative ones
    top_total = sum(v for k, v in cats_total.items() if "/" not in k)
    for cat, secs in _top({k: v for k, v in cats_total.items() if "/" not in k and k != "Uncategorized"}, 6):
        nd = cat_days.get(cat, 0)
        if nd < min_days or top_total <= 0 or secs / top_total < 0.2:
            continue
        share = secs / top_total
        patterns.append(_pattern(
            "category_share", f"{cat} is {share * 100:.0f}% of focus time",
            f"{cat} {share * 100:.0f}% of focus time, {secs / 3600.0 / nd:.1f}h/day.",
            support=int(secs // 60), days=nd, window=window,
            evidence={"category": cat, "share": round(share, 2), "hours_per_day": round(secs / 3600.0 / nd, 1),
                      "days": nd},
        ))
    drift_days: list[dict[str, Any]] = []
    drift_cats: dict[str, float] = {}
    for d in days:
        leaves = _cat_leaves({k: float(v) for k, v in (d.get("cats") or {}).items()})
        tot = sum(leaves.values())
        if tot < 600:
            continue
        bad = {k: v for k, v in leaves.items() if engine.score_of(k) < 0}
        weighted = sum(v * min(1.0, -engine.score_of(k) / 2.0) for k, v in bad.items()) / tot
        if weighted > 0.30:
            drift_days.append(d)
            for k, v in bad.items():
                drift_cats[k] = drift_cats.get(k, 0.0) + v
    if len(drift_days) >= max(1, min_days):
        hrs: dict[int, float] = {}
        for d in drift_days:
            for h, v in (d.get("hours") or {}).items():
                hrs[int(h)] = hrs.get(int(h), 0.0) + float(v)
        band = _band(hrs)
        names = ", ".join(k for k, _ in _top(drift_cats, 2))
        when = f", mostly {band[0]:02d}:00-{(band[1] + 1) % 24:02d}:00" if band else ""
        patterns.append(_pattern(
            "distraction_drift", f"Drifts to {names} on {len(drift_days)} days",
            f"On {len(drift_days)} of {window} days over 30% of focus time went to {names}{when}.",
            support=int(sum(drift_cats.values()) // 60), days=len(drift_days), window=window,
            evidence={"categories": [k for k, _ in _top(drift_cats, 3)], "days": len(drift_days),
                      "band": list(band) if band else None},
        ))

    patterns.sort(key=lambda p: -(p["confidence"] * (1 + min(3.0, p["support"] / 20.0))))
    return {
        "generated_at": now(),
        "window": {"days": window, "first_day": days[-1]["day"] if days else "", "last_day": days[0]["day"] if days else ""},
        "totals": {**totals, "focus_seconds": round(totals["focus_seconds"]),
                   "idle_seconds": round(totals["idle_seconds"])},
        "apps": [{"app": a, "seconds": round(s), "days": app_days.get(a, 0)} for a, s in _top(apps_total, 10)],
        "hosts": [{"host": h, "visits": int(v), "days": host_days.get(h, 0)} for h, v in _top(hosts_total, 10)],
        "hours": [{"hour": h, "seconds": round(hours_total.get(h, 0.0))} for h in range(24)],
        "categories": [{"path": k, "seconds": round(v), "days": cat_days.get(k, 0),
                        "score": engine.score_of(k)} for k, v in _top(cats_total, 12)],
        "patterns": patterns[:24],
    }


def _line(text: Any, limit: int = 200) -> str:
    """One line. A title or a profile sits in the insight prompt, so a newline cannot open a new section."""
    return " ".join(str(text or "").replace("\r", " ").split())[:limit]


def _shown(text: Any, limit: int = 200) -> str:
    """One line, with credentials removed. The mined pattern itself stays as stored."""
    return _line(redact.scrub_command_output(str(text or "")), limit)


def _fence(text: str) -> str:
    return "```\n" + str(text or "").replace("```", "'''") + "\n```"


def digest(pat: dict[str, Any]) -> str:
    """The mined patterns as the compact text the model is asked to reason over."""
    w = pat.get("window") or {}
    t = pat.get("totals") or {}
    lines = [
        f"Window: {w.get('days', 0)} days ({w.get('first_day', '?')} to {w.get('last_day', '?')})",
        f"Totals: {_mins(float(t.get('focus_seconds') or 0))} focused, {int(t.get('keys') or 0)} keystrokes, "
        f"{int(t.get('switches') or 0)} app switches.",
        "",
        "Patterns (id | confidence | what):",
    ]
    for p in pat.get("patterns") or []:
        lines.append(f"- {p['id']} | {p['confidence']:.2f} | {_shown(p.get('title'), 160)} - {_shown(p.get('detail'), 300)}")
    apps = ", ".join(f"{_shown(a['app'], 80)} {_mins(a['seconds'])}" for a in (pat.get("apps") or [])[:8])
    if apps:
        lines += ["", f"Time by app: {apps}"]
    cats = ", ".join(f"{_shown(c['path'], 80)} {_mins(c['seconds'])}" for c in (pat.get("categories") or []) if "/" not in str(c.get("path") or ""))
    if cats:
        lines.append(f"Time by category: {cats}")
    hosts = ", ".join(f"{_shown(h['host'], 80)} x{h['visits']}" for h in (pat.get("hosts") or [])[:8])
    if hosts:
        lines.append(f"Sites (host only): {hosts}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the LLM pass

# What the app can actually do. The model is told this so it proposes things that exist; without it
# every suggestion comes back as "integrate with Zapier".
SURFACE = """\
- Chat with tools: the assistant can search and write memories and the knowledge graph, search
  documents, read and propose edits to docs, search the web and read pages, run Python, manage
  todos (also shown as a kanban board), and (when Google is connected) read the calendar, triage Gmail, draft
  mail and manage Google Tasks. Tools are per-tool on / ask / off.
- Projects: a group of chats with their own instructions, knowledge files and memories.
- Documents and docs: uploads that get indexed, and docs the user writes that the assistant can
  propose diffs against.
- Todos, a week calendar, notes.
- Today screen with a generated daily recap and a one-click brief.
- Canvas spaces: arranged widget layouts the user can switch between.
- Memory: facts, preferences and goals, injected into chats; a knowledge graph beside it.
- The activity monitor itself: signals, pause, and the context file fed into chats."""

SUGGEST_PROMPT = """You turn observed computer activity into (a) durable habits worth remembering and
(b) concrete automations this app can actually set up.

You are given deterministic patterns mined from local activity data, a profile of how the person
works, and the list of what this app can do. You are NOT given their keystrokes or page contents.

Return ONLY a JSON object:
{
  "habits": [
    {"key": "stable-kebab-slug", "statement": "User ...", "kind": "preference|fact|goal",
     "confidence": 0.0-1.0, "evidence": ["pattern id"], "supersedes": "existing-habit-key-or-empty"}
  ],
  "suggestions": [
    {"key": "stable-kebab-slug",
     "kind": "automation|platform|hygiene",
     "title": "imperative, under 70 chars",
     "detail": "2-4 sentences: exactly what to set up, in terms of this app's features.",
     "why": "the observed pattern it answers, naming the numbers",
     "impact": "an honest estimate, e.g. 'saves ~20 min/day' or 'removes a daily context switch'",
     "effort": "low|medium|high",
     "confidence": 0.0-1.0,
     "evidence": ["pattern id", "..."],
     "action": {"type": "prompt|todo|memory|none",
                "prompt": "a ready-to-send message to the assistant that sets this up",
                "title": "todo title when type=todo",
                "content": "memory text when type=memory"}}
  ]
}

Rules:
- Every suggestion must cite at least one pattern id in `evidence`. No pattern, no suggestion.
- Only propose things the app can do from the surface list. Never propose new integrations,
  background daemons, or anything that needs permissions it does not have.
- Never propose turning on more recording signals, and never propose that the app act on its own
  without the user approving it.
- `kind: "automation"` sets something up for the user. `kind: "platform"` is a change the app
  itself should make to remove friction you can see in the data - say what and why.
  `kind: "hygiene"` is about their working pattern (breaks, after-hours, fragmentation).
- `action.prompt` must be a message the user could send as-is, naming the concrete artifact to
  create (a widget, a doc, a todo list, a project). Write it in first person as the user.
- Habits are durable third-person statements about the person, not about one day. No URLs with
  paths, no document names, no message content, no names of other people.
- Prefer few, specific, high-confidence items over a long list. Return empty arrays rather than
  padding. Never invent a pattern that is not in the input.
- Do not repeat anything in "already proposed" - those were already shown, accepted or dismissed.
"""


# Deterministic fallbacks, used when the model call fails or no model is configured. They are the
# same shape as the model's output, so everything downstream is identical - the feature degrades to
# "obvious suggestions" rather than to nothing.
def fallback(pat: dict[str, Any], taken: set[str]) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    habits: list[dict[str, Any]] = []
    for p in pat.get("patterns") or []:
        k, ev = p["kind"], p["evidence"]
        if k == "thrash" and len(ev.get("apps") or []) == 2:
            a, b = _line(ev["apps"][0], 80), _line(ev["apps"][1], 80)
            out.append({
                "key": _slug(f"batch-{a}-{b}", "sug-"), "kind": "automation",
                "title": f"Batch the {b} trips instead of {ev['round_trips']} round trips",
                "detail": f"Have a scheduled task summarize {b} at times you choose, so you read it once "
                          f"instead of switching out of {a} all day.",
                "why": f"{ev['round_trips']} round trips between {a} and {b}, median "
                       f"{ev['median_dwell_seconds']}s in {b}.",
                "impact": f"removes ~{ev['round_trips']} context switches a day",
                "effort": "low", "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt",
                           "prompt": f"I keep bouncing between {a} and {b} all day. Set up a scheduled task that "
                                     f"gives me a short digest of what's waiting in {b}, so I can check it twice a "
                                     f"day instead of every few minutes."},
            })
        elif k == "site_habit":
            host = _line(ev.get("host", ""), 80)
            out.append({
                "key": _slug(f"digest-{host}", "sug-"), "kind": "automation",
                "title": f"Turn the {host} habit into one digest",
                "detail": f"Register {host} as a data source (its feed or API) and describe an AI summary widget "
                          f"over it. The Today screen then carries the digest and the tab stops being a reflex.",
                "why": f"{ev.get('visits')} visits across {ev.get('days')} days, about "
                       f"{ev.get('per_day')} a day.",
                "impact": f"replaces ~{ev.get('per_day')} visits a day with one glance",
                "effort": "low", "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt",
                           "prompt": f"I open {host} many times a day. Add it as a data source and build me an AI "
                                     f"summary widget for it on my Today screen."},
            })
        elif k == "deep_work" and ev.get("band"):
            b0, b1 = ev["band"]
            out.append({
                "key": "sug-protect-deep-work", "kind": "hygiene",
                "title": f"Protect {b0:02d}:00-{(b1 + 1) % 24:02d}:00 on the calendar",
                "detail": "Your longest unbroken stretches start in that window. Block it as a recurring calendar "
                          "event so meetings land outside it, and pause the monitor there if you want it quiet.",
                "why": f"{ev.get('stretches')} stretches of 15 minutes or more, clustered in that band.",
                "impact": "keeps the one window that actually produces work",
                "effort": "low", "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt",
                           "prompt": f"Block {b0:02d}:00-{(b1 + 1) % 24:02d}:00 on my calendar every weekday as "
                                     f"focus time, and keep meetings out of it."},
            })
            habits.append({"key": "habit-deep-work-window", "kind": "preference",
                           "statement": f"User does their longest uninterrupted work between {b0:02d}:00 and "
                                        f"{(b1 + 1) % 24:02d}:00; schedule demanding work there and meetings elsewhere.",
                           "confidence": p["confidence"], "evidence": [p["id"]], "supersedes": ""})
        elif k == "category_share":
            category = _line(ev.get("category", ""), 80)
            habits.append({"key": _slug(f"habit-category-{category}"), "kind": "fact",
                           "statement": f"About {int((ev.get('share') or 0) * 100)}% of the user's focused screen "
                                        f"time goes to {category}.",
                           "confidence": p["confidence"], "evidence": [p["id"]], "supersedes": ""})
        elif k == "distraction_drift":
            band = ev.get("band")
            when = f"{band[0]:02d}:00-{(band[1] + 1) % 24:02d}:00" if band else "the evening"
            cats = ", ".join(line for c in (ev.get("categories") or []) if (line := _line(c, 40)))
            out.append({
                "key": "sug-guard-drift", "kind": "hygiene",
                "title": f"Guard {when} against drifting",
                "detail": f"Over a third of focus time slides to {cats} around "
                          f"then. Decide the plan for that window ahead of time - a calendar block, or a todo "
                          f"picked in advance - so the default is not the feed.",
                "why": f"{ev.get('days')} days where the low-value categories passed 30% of focus time.",
                "impact": "gets back the part of the day that leaks", "effort": "low",
                "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt",
                           "prompt": f"I tend to drift into distractions around {when}. Pick the one todo I should "
                                     f"start with at that time and put a calendar block on it."},
            })
        elif k == "topic":
            topic = _line(ev.get("topic", ""), 80)
            out.append({
                "key": _slug(f"project-{topic}", "sug-"), "kind": "automation",
                "title": f"Give \"{topic}\" a project",
                "detail": f"{topic} keeps coming back across days. A project collects its chats, instructions, "
                          f"knowledge files and memories so you stop re-explaining the context each time.",
                "why": f"{topic} appeared on {ev.get('days')} separate days.",
                "impact": "stops re-establishing context every session", "effort": "low",
                "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt", "prompt": f"Create a project for \"{topic}\" and write its instructions "
                                                       f"based on what you know I've been doing on it."},
            })
        elif k == "day_shape":
            out.append({
                "key": "sug-morning-brief", "kind": "automation",
                "title": f"Have the brief ready by {ev.get('median_start', '09:00')}",
                "detail": "The Today screen can generate a daily recap and a one-click brief. Ask for it as the "
                          "first thing you do, so the day starts from a summary instead of six tabs.",
                "why": f"Days reliably start around {ev.get('median_start')} and end around {ev.get('median_end')}.",
                "impact": "replaces the morning catch-up sweep", "effort": "low",
                "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt", "prompt": "Brief me: calendar, unread mail that needs me, open todos, "
                                                       "and what I left unfinished yesterday."},
            })
            habits.append({"key": "habit-day-shape", "kind": "fact",
                           "statement": f"User's working day typically runs {ev.get('median_start')} to "
                                        f"{ev.get('median_end')}.",
                           "confidence": p["confidence"], "evidence": [p["id"]], "supersedes": ""})
        elif k == "after_hours":
            out.append({
                "key": "sug-shutdown-routine", "kind": "hygiene",
                "title": "Add an end-of-day shutdown routine",
                "detail": "Ask for a wrap-up at a fixed hour: what moved, what is still open, what tomorrow starts "
                          "with. Written down, the evening tail usually stops being necessary.",
                "why": f"{int((ev.get('off_hours_share') or 0) * 100)}% of screen time is outside 08:00-19:00.",
                "impact": "shortens the evening tail", "effort": "low",
                "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt", "prompt": "Write my end-of-day wrap-up: what I moved today, what's "
                                                       "still open, and the first thing to pick up tomorrow."},
            })
        elif k == "switch_rate":
            out.append({
                "key": "sug-one-space", "kind": "hygiene",
                "title": "Put the day's tools in one canvas space",
                "detail": f"At {ev.get('switches_per_hour')} switches an hour most of the movement is window "
                          f"hunting. A canvas space with the three or four surfaces you actually need side by side "
                          f"removes the hunt.",
                "why": f"{ev.get('switches_per_hour')} app switches an hour.",
                "impact": "cuts window hunting", "effort": "low",
                "confidence": p["confidence"], "evidence": [p["id"]],
                "action": {"type": "prompt", "prompt": "Set up a canvas space with my calendar, todos and inbox side "
                                                       "by side so I stop switching windows."},
            })
        elif k == "app_routine" and ev.get("band"):
            b0, b1 = ev["band"]
            app = _line(ev.get("app", ""), 80)
            habits.append({"key": _slug(f"habit-{app}-window"), "kind": "fact",
                           "statement": f"User spends most of their {app} time between {b0:02d}:00 and "
                                        f"{(b1 + 1) % 24:02d}:00.",
                           "confidence": p["confidence"], "evidence": [p["id"]], "supersedes": ""})
        elif k == "input_load":
            habits.append({"key": "habit-primary-tool", "kind": "fact",
                           "statement": f"User does most of their writing in {_line(ev.get('app', ''), 80)}.",
                           "confidence": p["confidence"], "evidence": [p["id"]], "supersedes": ""})
    return {"habits": [h for h in habits if h["key"] not in taken][:8],
            "suggestions": [s for s in out if s["key"] not in taken]}


# ---------------------------------------------------------------- the ledger


class Insights:
    """Mines patterns, runs the pass, and owns the habit and suggestion rows.

    Deliberately not given the ability to act: `refresh` writes habit memories and suggestion rows,
    and that is all. Suggestions are applied only through `apply`, which is reachable only from a
    button.
    """

    def __init__(self, db: Database, config_fn: Callable[[], dict[str, Any]],
                 settings_fn: Callable[[], dict[str, Any]], complete_fn: Callable[..., Any],
                 store: Any):
        self.db = db
        self.config_fn = config_fn
        self.settings = settings_fn
        self._complete = complete_fn
        self.store = store                 # the activity Store: events, summaries, profile
        self.days = DayStats(db)
        self.memories = Memories(db)
        self.todos = Todos(db)
        self.last_run = 0.0
        self.last_error = ""
        self.tools_fn: Callable[[], list[str]] | None = None   # wired from app.py; optional

    # ---- config ----
    def cfg(self) -> dict[str, Any]:
        stored = (self.config_fn() or {}).get("insights")
        return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}

    # ---- deterministic half ----
    def mine_now(self) -> dict[str, Any]:
        """Fold today's raw events into the day stats, then mine everything. No network."""
        cfg = self.cfg()
        retention = float((self.config_fn() or {}).get("retentionHours") or 48)
        events = self.store.recent(limit=20000, since=now() - retention * 3600)
        from .activity_categories import engine_for
        engine = engine_for((self.config_fn() or {}).get("categories"))
        for day, stats in day_stats_from_events(events, engine).items():
            self.days.merge(day, stats)
        pat = mine(
            events=events,
            days=self.days.recent(int(cfg["lookbackDays"])),
            summaries=self.store.summaries(since=now() - int(cfg["lookbackDays"]) * 86400, limit=400),
            min_days=int(cfg["minDays"]), engine=engine,
        )
        self._save_patterns(pat)
        return pat

    def patterns(self) -> dict[str, Any]:
        with self.db.tx() as c:
            r = c.execute("SELECT content, updated_at FROM activity_patterns WHERE id=1").fetchone()
        out = _parse_json(r["content"]) if r else {}
        if r:
            out.setdefault("generated_at", float(r["updated_at"]))
        return out

    def _save_patterns(self, pat: dict[str, Any]) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO activity_patterns(id,content,updated_at) VALUES(1,?,?)"
                " ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
                (json.dumps(pat), now()),
            )

    # ---- the pass ----
    async def refresh(self, *, force: bool = False) -> dict[str, Any]:
        """Mine, ask the model, persist. Safe to call repeatedly: everything upserts by key."""
        cfg = self.cfg()
        pat = self.mine_now()
        if not pat.get("patterns") and not force:
            self.last_run = now()
            return {"ok": False, "reason": "not enough activity yet", **self.overview()}

        taken = {r["key"] for r in self.list_suggestions(include_all=True)} | {h["key"] for h in self.list_habits()}
        data: dict[str, Any] = {}
        model = (self.config_fn() or {}).get("summaryModel") or self.settings().get("extractionModel") \
            or self.settings().get("defaultModel")
        if model:
            try:
                raw = await self._complete(
                    self.settings(), model,
                    [{"role": "system", "content": SUGGEST_PROMPT},
                     {"role": "user", "content": self._user_prompt(pat, taken)}],
                    kind="activity",
                )
                data = _parse_json(raw)
                self.last_error = ""
            except Exception as e:  # noqa: BLE001 - the deterministic half must still land
                self.last_error = f"insights pass failed: {type(e).__name__}: {e}"
                log.warning("insights: %s", self.last_error)
        if not (data.get("suggestions") or data.get("habits")):
            data = fallback(pat, taken)

        try:
            habits = self._persist_habits(data.get("habits") or [], bool(cfg["autoMemory"]),
                                         float(cfg["memoryConfidence"]))
            sugs = self._persist_suggestions(data.get("suggestions") or [], int(cfg["maxSuggestions"]))
        finally:
            # Even when persisting blows up: otherwise maybe_refresh retries (and bills an LLM
            # call) on every pass of the loop.
            self.last_run = now()
        log.info("insights: %d patterns, %d habits, %d suggestions", len(pat.get("patterns") or []),
                 len(habits), len(sugs))
        return {"ok": True, "new_habits": habits, "new_suggestions": sugs, **self.overview()}

    async def maybe_refresh(self) -> dict[str, Any] | None:
        """The cadence check the monitor's loop calls. Returns None when it was not time."""
        cfg = self.cfg()
        if not cfg.get("enabled"):
            return None
        every = float(cfg.get("everyHours") or 12)
        if every <= 0 or now() - self.last_run < every * 3600:
            return None
        if not self.store.summaries(limit=1) and not self.days.recent(1):
            return None
        return await self.refresh()

    def _user_prompt(self, pat: dict[str, Any], taken: set[str]) -> str:
        prof = (self.store.profile() or {}).get("content") or ""
        tools = ""
        if self.tools_fn:
            try:
                tools = ", ".join(sorted(self.tools_fn()))[:1200]
            except Exception:  # noqa: BLE001
                tools = ""
        already = [f"- {_line(r['key'], 60)} ({_line(r['status'], 20)}): {_line(r['title'], 120)}"
                   for r in self.list_suggestions(include_all=True)]
        parts = [
            "## Mined patterns", digest(pat),
            "", "## How they work (profile built from the same data)",
            _fence(redact.scrub_command_output(prof)) if prof.strip() else "(none yet)",
            "", "## What this app can do", SURFACE,
        ]
        if tools:
            parts += ["", f"Tools currently registered: {tools}"]
        if already:
            parts += ["", "## Already proposed (do not repeat; pick new ground)", *already[:40]]
        if taken:
            parts += ["", f"Keys already in use: {', '.join(sorted(taken))[:800]}"]
        return "\n".join(parts)

    # ---- habits ----
    def list_habits(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM activity_habits ORDER BY confidence DESC, last_seen DESC").fetchall()
        return [row_to_dict(r, ("evidence",)) for r in rows]  # type: ignore[misc]

    def _persist_habits(self, items: list[dict[str, Any]], auto_memory: bool, min_conf: float) -> list[dict[str, Any]]:
        """Upsert habits by key, and keep each one's memory row in step.

        A habit owns at most one memory. Re-running rewrites that memory's text rather than adding a
        near-duplicate, and `supersedes` retires the habit it replaces along with its memory - which
        is the only way a stale "works late every night" ever leaves the memory panel on its own.
        """
        out: list[dict[str, Any]] = []
        existing = {h["key"]: h for h in self.list_habits()}
        seen: set[str] = set()
        for raw in _items(items, 10):
            key = _slug(str(raw.get("key") or raw.get("statement") or ""), "habit-" if not str(raw.get("key") or "").startswith("habit") else "")
            statement = str(raw.get("statement") or "").strip()
            if not key or len(statement) < 8 or key in seen:
                continue
            seen.add(key)
            kind = str(raw.get("kind") or "preference")
            kind = kind if kind in ("fact", "preference", "goal", "note") else "preference"
            conf = _model_conf(raw.get("confidence"))
            ev = [str(x) for x in (raw.get("evidence") if isinstance(raw.get("evidence"), list) else [])][:6]
            prev = existing.get(key)
            mem_id = prev["memory_id"] if prev else ""

            created_mem = ""
            if auto_memory and conf >= min_conf:
                if mem_id:
                    # Trashed rows included: once the user trashed, forgot, pinned or edited the memory, it is theirs.
                    with self.db.tx() as c:
                        mem = row_to_dict(c.execute("SELECT * FROM memories WHERE id=?", (mem_id,)).fetchone())
                    if (mem and mem.get("source") == MEMORY_SOURCE and not mem["deleted_at"] and mem["invalid_at"] is None
                            and not mem["pinned"] and mem["content"] == prev["statement"]):
                        self.memories.update(mem_id, {"content": statement, "kind": kind})
                else:  # create dedupes on content: a user's own row comes back, and a habit never adopts (or later rewrites) it
                    row = self.memories.create(None, statement, kind=kind, source=MEMORY_SOURCE)
                    mem_id = created_mem = row["id"] if row.get("source") == MEMORY_SOURCE else ""
            elif mem_id and not auto_memory:
                self._drop_memory(mem_id)
                mem_id = ""

            t = now()
            try:
                with self.db.tx() as c:
                    if prev:
                        c.execute(
                            "UPDATE activity_habits SET statement=?,kind=?,confidence=?,support=support+1,"
                            "evidence=?,memory_id=?,last_seen=? WHERE key=?",
                            (statement, kind, conf, json.dumps(ev), mem_id, t, key),
                        )
                    else:
                        c.execute(
                            "INSERT INTO activity_habits(id,key,statement,kind,confidence,support,evidence,"
                            "memory_id,first_seen,last_seen) VALUES(?,?,?,?,?,1,?,?,?,?)",
                            (new_id(), key, statement, kind, conf, json.dumps(ev), mem_id, t, t),
                        )
            except Exception:
                # A memory no habit owns could never be forgotten from the habit panel.
                if created_mem:
                    self._drop_memory(created_mem)
                raise
            sup = str(raw.get("supersedes") or "").strip()
            if sup and sup != key and sup in existing:
                self.forget_habit(existing[sup]["id"], drop_memory=True)
            out.append(self.get_habit_by_key(key) or {})
        return [h for h in out if h]

    def get_habit_by_key(self, key: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM activity_habits WHERE key=?", (key,)).fetchone(),
                               ("evidence",))

    def forget_habit(self, hid: str, *, drop_memory: bool = True) -> None:
        """Forget a habit and, by default, the memory it wrote. Never touches a memory it did not write."""
        with self.db.tx() as c:
            row = c.execute("SELECT memory_id FROM activity_habits WHERE id=?", (hid,)).fetchone()
            mem = row["memory_id"] if row else ""
            c.execute("DELETE FROM activity_habits WHERE id=?", (hid,))
        if mem and drop_memory:
            self._drop_memory(mem)

    def _drop_memory(self, mem_id: str) -> None:
        m = self.memories.get(mem_id)
        if m and m.get("source") == MEMORY_SOURCE:
            self.memories.delete(mem_id)

    # ---- suggestions ----
    def list_suggestions(self, *, include_all: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM activity_suggestions"
        if not include_all:
            sql += " WHERE status IN ('new','accepted','snoozed')"
        sql += " ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END, confidence DESC"
        with self.db.tx() as c:
            rows = c.execute(sql).fetchall()
        out = [row_to_dict(r, ("action", "evidence")) for r in rows]
        return [r for r in out if r]  # type: ignore[misc]

    def get(self, sid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM activity_suggestions WHERE id=?", (sid,)).fetchone(),
                               ("action", "evidence"))

    def _persist_suggestions(self, items: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
        """Upsert by key. A status the user set wins over anything a refresh produces."""
        out: list[dict[str, Any]] = []
        existing = {r["key"]: r for r in self.list_suggestions(include_all=True)}
        seen: set[str] = set()
        for raw in _items(items, max(1, cap)):
            title = _line(raw.get("title"), 120)
            if not title:
                continue
            key = _slug(str(raw.get("key") or title), "sug-" if not str(raw.get("key") or "").startswith("sug") else "")
            if key in seen:
                continue
            seen.add(key)
            kind = str(raw.get("kind") or "automation")
            kind = kind if kind in ("automation", "platform", "hygiene") else "automation"
            action = raw.get("action") if isinstance(raw.get("action"), dict) else {}
            atype = str((action or {}).get("type") or "none")
            if atype not in ("prompt", "todo", "memory", "none"):
                atype = "none"
            action = {**(action or {}), "type": atype}
            if atype == "prompt":
                action["prompt"] = _line(action.get("prompt"), 1000)
            elif atype == "todo":
                action["title"] = _line(action.get("title") or title, 200)
            elif atype == "memory":
                action["content"] = _line(action.get("content"), 1000)
            fields = (
                kind, title, _line(raw.get("detail"), 1200), _line(raw.get("why"), 600),
                _line(raw.get("impact"), 200),
                (str(raw.get("effort") or "low") if str(raw.get("effort") or "low") in ("low", "medium", "high") else "low"),
                json.dumps(action), json.dumps([str(x) for x in (raw.get("evidence") if isinstance(raw.get("evidence"), list) else [])][:8]),
                _model_conf(raw.get("confidence")),
            )
            prev = existing.get(key)
            with self.db.tx() as c:
                if prev:
                    # Re-score and refresh the wording, but never move a ruled-on row back to new.
                    c.execute(
                        "UPDATE activity_suggestions SET kind=?,title=?,detail=?,why=?,impact=?,effort=?,"
                        "action=?,evidence=?,confidence=?,updated_at=? WHERE key=?",
                        (*fields, now(), key),
                    )
                else:
                    c.execute(
                        "INSERT INTO activity_suggestions(id,key,kind,title,detail,why,impact,effort,action,"
                        "evidence,confidence,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?)",
                        (new_id(), key, *fields, now(), now()),
                    )
            row = self._by_key(key)
            if row and (not prev or prev["status"] not in STICKY):
                out.append(row)
        return out

    def _by_key(self, key: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM activity_suggestions WHERE key=?", (key,)).fetchone(),
                               ("action", "evidence"))

    def set_status(self, sid: str, status: str, note: str = "", snooze_days: float = 7.0) -> dict[str, Any] | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        until = now() + max(0.5, snooze_days) * 86400 if status == "snoozed" else 0.0
        with self.db.tx() as c:
            c.execute("UPDATE activity_suggestions SET status=?,status_note=?,snooze_until=?,updated_at=? WHERE id=?",
                      (status, note[:300], until, now(), sid))
        return self.get(sid)

    def wake_snoozed(self) -> int:
        """A snooze is a 'not now', so it comes back on its own once the clock runs out."""
        with self.db.tx() as c:
            return max(0, c.execute(
                "UPDATE activity_suggestions SET status='new',snooze_until=0,updated_at=? "
                "WHERE status='snoozed' AND snooze_until>0 AND snooze_until<=?", (now(), now())).rowcount)

    def apply(self, sid: str) -> dict[str, Any]:
        """Do the one thing a suggestion's action says, nothing more.

        `prompt` is the common case and it does not act at all: it hands the message back for the
        user to send, because setting the thing up is a conversation with tool approvals in it, not
        a side effect of pressing a button in a settings panel.
        """
        s = self.get(sid)
        if not s:
            raise KeyError(sid)
        action = s.get("action") or {}
        atype = str(action.get("type") or "none")
        result: dict[str, Any] = {"type": atype}
        # A double click (or a retry after a slow answer) must not write a second todo or memory. Two requests
        # run on separate threads, so the claim is one atomic UPDATE (not a read of the status): only the
        # request that flips the row to `done` goes on to write. "Put it back" is the way to offer it again.
        if atype in ("todo", "memory"):
            with self.db.tx() as c:
                claimed = c.execute("UPDATE activity_suggestions SET status='done',status_note='applying',updated_at=? "
                                    "WHERE id=? AND status!='done'", (now(), sid)).rowcount
            if not claimed:
                return {**result, "already": True, "suggestion": self.get(sid)}
            try:
                if atype == "todo":
                    todo = self.todos.create(
                        title=_line(action.get("title") or s["title"], 200),
                        notes=_line(f"{s['detail']} Why: {s['why']}", 1500), priority=2, source="activity-insight",
                    )
                    result["todo"] = todo
                    self.set_status(sid, "done", f"todo created: {todo['id']}")
                else:
                    content = _line(action.get("content") or s["detail"], 1000)
                    mem = self.memories.create(None, content, kind="preference", source=MEMORY_SOURCE)
                    result["memory"] = mem
                    self.set_status(sid, "done", f"memory created: {mem['id']}")
            except Exception:
                self.set_status(sid, s["status"], s.get("status_note") or "")   # the write failed: offer it again as it was
                raise
            return {**result, "suggestion": self.get(sid)}
        if atype == "prompt":
            result["prompt"] = _line(action.get("prompt") or f"{s['title']}. {s['detail']}", 1500)
            self.set_status(sid, "accepted", "sent to chat")
        else:
            self.set_status(sid, "accepted", "acknowledged")
        return {**result, "suggestion": self.get(sid)}

    def purge_patterns(self, older_than_seconds: float = 0.0) -> None:
        """Drop the stored pattern snapshot. It holds raw window titles (the recurring-window
        pattern), so it must not outlive the events it was mined from: 0 clears it now, a positive
        value clears it once the snapshot is that old."""
        with self.db.tx() as c:
            if older_than_seconds <= 0:
                c.execute("DELETE FROM activity_patterns")
            else:
                c.execute("DELETE FROM activity_patterns WHERE updated_at < ?", (now() - older_than_seconds,))

    def purge(self, *, everything: bool = False, keep_days: float = 90.0) -> dict[str, int]:
        """Part of the activity purge, so 'delete everything' really does mean everything."""
        if everything:
            for h in self.list_habits():
                self.forget_habit(h["id"], drop_memory=True)
            with self.db.tx() as c:
                sug = max(0, c.execute("DELETE FROM activity_suggestions").rowcount)
                c.execute("DELETE FROM activity_patterns")
            return {"habits": 0, "suggestions": sug, "days": self.days.purge(everything=True)}
        return {"habits": 0, "suggestions": 0, "days": self.days.purge(keep_days)}

    # ---- what the panel and the assistant read ----
    def overview(self) -> dict[str, Any]:
        self.wake_snoozed()
        pat = self.patterns()
        sugs = self.list_suggestions(include_all=True)
        cfg = self.cfg()
        return {
            "enabled": bool(cfg.get("enabled")),
            "generated_at": float(pat.get("generated_at") or 0),
            "last_run": self.last_run,
            "next_run": (self.last_run + float(cfg.get("everyHours") or 12) * 3600) if self.last_run else 0.0,
            "last_error": self.last_error,
            "window": pat.get("window") or {},
            "totals": pat.get("totals") or {},
            "apps": pat.get("apps") or [],
            "hosts": pat.get("hosts") or [],
            "hours": pat.get("hours") or [],
            "patterns": pat.get("patterns") or [],
            "habits": self.list_habits(),
            "suggestions": sugs,
            "counts": {
                "open": len([s for s in sugs if s["status"] == "new"]),
                "accepted": len([s for s in sugs if s["status"] == "accepted"]),
                "dismissed": len([s for s in sugs if s["status"] == "dismissed"]),
                "habits": len(self.list_habits()),
                "days": len(self.days.recent(400)),
            },
        }

    def brief(self, limit: int = 5) -> dict[str, Any]:
        """The compact form the assistant gets through a tool: habits plus what is still on offer."""
        o = self.overview()

        def shown(text: Any) -> str:
            return redact.scrub_command_output(str(text or ""))

        return {
            "habits": [{"statement": shown(h["statement"]), "confidence": h["confidence"]} for h in o["habits"][:10]],
            "patterns": [{"what": shown(p["title"]), "detail": shown(p["detail"]), "confidence": p["confidence"]}
                         for p in o["patterns"][:8]],
            "open_suggestions": [
                {"id": s["id"], "kind": s["kind"], "title": shown(s["title"]), "why": shown(s["why"]),
                 "impact": shown(s["impact"]), "how": shown(s["detail"]),
                 "prompt": shown((s.get("action") or {}).get("prompt", ""))}
                for s in o["suggestions"] if s["status"] == "new"
            ][:limit],
            "note": "Suggestions are proposals the user has not accepted. Offer one when it is relevant; "
                    "never act on it without being asked.",
        }
