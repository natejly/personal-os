"""Category rules for the activity monitor: a nested rule tree, with a productivity score.

A rule is `{name: ["Work", "Coding"], rule: {...}, score: 2}`. The deepest matching rule wins
(longest name path, ties to list order), and totals roll up into every ancestor, so "Work" is the
sum of Coding, Writing and Meetings. Everything here is local and deterministic: no model is
asked what Safari time "was", and only category path strings plus seconds are ever stored.

Rule shapes:
  {"type": "regex", "pattern": "Xcode|Cursor", "fields": ["app", "title"]}   re.I, search
  {"type": "none"}                                                          a folder, never matches
  either may add "hosts": ["github.com"], matched against the URL host by suffix.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .insights import _host

UNCATEGORIZED = ("Uncategorized",)
MAX_RULES = 100
FIELDS = ("app", "title")

DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {"name": ["Work"], "rule": {"type": "none"}, "score": 1},
    {"name": ["Work", "Coding"], "score": 2,
     "rule": {"type": "regex", "pattern": r"Xcode|Cursor|\bCode\b|Terminal|iTerm|PyCharm|IntelliJ|WebStorm|Warp|Ghostty|Zed|Sublime",
              "fields": ["app"]}},
    {"name": ["Work", "Writing"], "score": 1,
     "rule": {"type": "regex", "pattern": r"\bPages\b|Microsoft Word|Obsidian|\bNotion\b|Bear|Notes|Ulysses|iA Writer|Google Docs|Grain",
              "fields": ["app"], "hosts": ["docs.google.com", "notion.so"]}},
    {"name": ["Work", "Meetings"], "score": 1,
     "rule": {"type": "regex", "pattern": r"zoom\.us|Google Meet|\bMeet\b|Microsoft Teams|Slack huddle|FaceTime|Webex",
              "fields": ["app", "title"], "hosts": ["meet.google.com", "zoom.us"]}},
    {"name": ["Comms"], "score": 0,
     "rule": {"type": "regex", "pattern": r"^Mail$|Outlook|Slack|Messages|Discord|Telegram|WhatsApp|Signal",
              "fields": ["app"], "hosts": ["mail.google.com", "slack.com", "discord.com"]}},
    {"name": ["Reference"], "score": 1,
     "rule": {"type": "regex", "pattern": r"Stack Overflow|MDN Web Docs", "fields": ["title"],
              "hosts": ["stackoverflow.com", "github.com", "developer.mozilla.org", "wikipedia.org", "readthedocs.io"]}},
    {"name": ["Social"], "rule": {"type": "none"}, "score": -2},
    {"name": ["Social", "Media"], "score": -2,
     "rule": {"type": "regex", "pattern": r"YouTube|Twitter|Reddit|Instagram|Netflix|TikTok|Twitch|Facebook",
              "fields": ["title"],
              "hosts": ["youtube.com", "twitter.com", "x.com", "reddit.com", "instagram.com", "netflix.com",
                        "tiktok.com", "twitch.tv", "facebook.com"]}},
    {"name": ["Uncategorized"], "rule": {"type": "none"}, "score": 0},
]


def path_str(path: Iterable[str]) -> str:
    return "/".join(path)


def _as_path(key: Any) -> tuple[str, ...]:
    if isinstance(key, str):
        return tuple(p for p in key.split("/") if p)
    return tuple(key)


def validate_rules(rules: Any) -> tuple[bool, str, int]:
    """(ok, message, offending index). Used by PUT /activity/categories."""
    if not isinstance(rules, list):
        return False, "categories must be a list", -1
    if len(rules) > MAX_RULES:
        return False, f"at most {MAX_RULES} rules", -1
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            return False, "rule must be an object", i
        name = r.get("name")
        if (not isinstance(name, list) or not name
                or not all(isinstance(n, str) and n.strip() and "/" not in n for n in name)):
            return False, "name must be a non-empty list of strings without '/'", i
        sc = r.get("score")
        if sc is not None and (isinstance(sc, bool) or not isinstance(sc, (int, float)) or not -2 <= sc <= 2):
            return False, "score must be a number from -2 to 2", i
        rule = r.get("rule") or {"type": "none"}
        if not isinstance(rule, dict) or rule.get("type", "none") not in ("regex", "none"):
            return False, "rule.type must be 'regex' or 'none'", i
        if rule.get("type") == "regex":
            try:
                re.compile(str(rule.get("pattern") or ""), re.I)
            except re.error as e:
                return False, f"invalid regex: {e}", i
        fields = rule.get("fields")
        if fields is not None and (not isinstance(fields, list) or any(f not in FIELDS for f in fields)):
            return False, "rule.fields must be a list of 'app' / 'title'", i
        hosts = rule.get("hosts")
        if hosts is not None and (not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts)):
            return False, "rule.hosts must be a list of strings", i
    return True, "", -1


class CategoryEngine:
    """Compiled rules. `classify` never raises."""

    CACHE_MAX = 2048

    def __init__(self, rules: list[dict[str, Any]] | None = None):
        self.rules = DEFAULT_CATEGORIES if rules is None else rules
        self._compiled: list[tuple[tuple[str, ...], re.Pattern[str] | None, tuple[str, ...], tuple[str, ...]]] = []
        self._scores: dict[tuple[str, ...], float] = {}
        self._cache: dict[tuple[str, str, str], tuple[str, ...]] = {}
        self.compile()

    def compile(self) -> None:
        self._compiled, self._scores, self._cache = [], {}, {}
        for r in self.rules if isinstance(self.rules, list) else []:
            try:
                name = tuple(str(n) for n in r["name"])
                if not name:
                    continue
                if isinstance(r.get("score"), (int, float)) and not isinstance(r.get("score"), bool):
                    self._scores[name] = max(-2.0, min(2.0, float(r["score"])))
                rule = r.get("rule") or {}
                if rule.get("type") != "regex":
                    continue            # a folder only groups; it never matches on its own
                pat = None
                if rule.get("type") == "regex" and rule.get("pattern"):
                    try:
                        pat = re.compile(str(rule["pattern"]), re.I)
                    except re.error:
                        pat = None      # a bad regex drops the pattern, never the engine
                fields = tuple(f for f in (rule.get("fields") or FIELDS) if f in FIELDS)
                hosts = tuple(str(h).lower().lstrip(".") for h in (rule.get("hosts") or []) if h)
                if pat is not None or hosts:
                    self._compiled.append((name, pat, fields, hosts))
            except Exception:  # noqa: BLE001 - a malformed rule is skipped
                continue

    def classify(self, app: str = "", title: str = "", url: str = "") -> tuple[str, ...]:
        try:
            host = _host(url or "")
            title = (title or "")[:120]
            key = (app or "", title, host)
            hit = self._cache.get(key)
            if hit is not None:
                return hit
            best: tuple[str, ...] | None = None
            for name, pat, fields, hosts in self._compiled:     # list order breaks ties: first wins
                if best is not None and len(name) <= len(best):
                    continue
                matched = False
                if pat is not None:
                    vals = {"app": app or "", "title": title}
                    matched = any(pat.search(vals[f]) for f in fields)
                if not matched and host and hosts:
                    matched = any(host == h or host.endswith("." + h) for h in hosts)
                if matched:
                    best = name
            out = best or UNCATEGORIZED
            if len(self._cache) >= self.CACHE_MAX:
                self._cache.clear()
            self._cache[key] = out
            return out
        except Exception:  # noqa: BLE001
            return UNCATEGORIZED

    def score_of(self, path: Iterable[str]) -> float:
        """The category's own score, else the nearest ancestor's, else 0."""
        p = _as_path(path)
        for i in range(len(p), 0, -1):
            if p[:i] in self._scores:
                return self._scores[p[:i]]
        return 0.0

    @staticmethod
    def rollup(seconds_by_path: dict[Any, float]) -> dict[str, float]:
        """Leaf seconds into path strings, with every ancestor prefix summed too."""
        out: dict[str, float] = {}
        for key, secs in seconds_by_path.items():
            p = _as_path(key)
            for i in range(1, len(p) + 1):
                s = path_str(p[:i])
                out[s] = out.get(s, 0.0) + float(secs)
        return out


_engines: dict[str, CategoryEngine] = {}


def engine_for(categories: Any) -> CategoryEngine:
    """The engine for a config value (None = defaults), reused until the rules change."""
    if not isinstance(categories, list):
        key = "default"
    else:
        try:
            key = json.dumps(categories, sort_keys=True)
        except (TypeError, ValueError):
            categories, key = None, "default"
    eng = _engines.get(key)
    if eng is None:
        if len(_engines) > 8:
            _engines.clear()
        eng = _engines[key] = CategoryEngine(categories if isinstance(categories, list) else None)
    return eng


def _leaves(cats: dict[str, float]) -> dict[str, float]:
    """Drop the ancestor rows a rollup added, leaving the seconds counted once."""
    keys = set(cats)
    return {k: v for k, v in cats.items() if not any(o.startswith(k + "/") for o in keys if o != k)}


def effective(cfg: dict[str, Any]) -> dict[str, Any]:
    cats = cfg.get("categories")
    is_default = not isinstance(cats, list)
    return {"rules": DEFAULT_CATEGORIES if is_default else cats, "default": is_default}


def save(monitor: Any, rules: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Validate and store user rules (None resets to the default tree). Raises ValueError(msg, index)."""
    if rules is not None:
        ok, msg, idx = validate_rules(rules)
        if not ok:
            raise ValueError(msg, idx)
    monitor.set_config({"categories": rules})
    return effective(monitor.config())


def build_report(day_rows: list[dict[str, Any]], pending: dict[str, dict[str, Any]],
                 engine: CategoryEngine, days: int = 7) -> dict[str, Any]:
    """Category totals per day. `pending` is day_stats_from_events() over events not yet merged."""
    by_day: dict[str, dict[str, Any]] = {}
    for src in (day_rows, [{"day": d, **s} for d, s in pending.items()]):
        for r in src:
            cur = by_day.setdefault(r["day"], {"cats": {}, "apps": {}, "focus_seconds": 0.0})
            for k in ("cats", "apps"):
                for key, v in (r.get(k) or {}).items():
                    cur[k][key] = max(float(cur[k].get(key, 0)), float(v))       # same max-merge as DayStats
            cur["focus_seconds"] = max(cur["focus_seconds"], float(r.get("focus_seconds") or 0))
    keep = sorted(by_day)[-max(1, int(days)):]
    out_days, totals, apps_total = [], {}, {}
    for d in keep:
        row = by_day[d]
        cats = {k: round(v, 1) for k, v in row["cats"].items()}
        out_days.append({"day": d, "total_seconds": round(row["focus_seconds"], 1), "cats": cats})
        for k, v in cats.items():
            totals[k] = totals.get(k, 0.0) + v
        for a, v in row["apps"].items():
            apps_total[a] = apps_total.get(a, 0.0) + float(v)
    leaves = _leaves(totals)
    secs = sum(leaves.values())
    prod = round(sum(v * engine.score_of(k) for k, v in leaves.items()) / secs, 2) if secs > 0 else None
    unc = sorted(((a, s) for a, s in apps_total.items()
                  if a and a != "(private)" and engine.classify(a, "", "") == UNCATEGORIZED),
                 key=lambda kv: -kv[1])[:8]
    return {
        "days": out_days,
        "totals": {k: round(v, 1) for k, v in sorted(totals.items(), key=lambda kv: -kv[1])},
        "productivity": prod,
        "top_uncategorized_apps": [{"app": a, "seconds": round(s)} for a, s in unc],
    }


def report_for(monitor: Any, days: int = 7) -> dict[str, Any]:
    """The report from live state: stored day stats plus the events still waiting to be merged."""
    from .insights import day_stats_from_events
    from .db import now

    cfg = monitor.config()
    eng = engine_for(cfg.get("categories"))
    retention = float(cfg.get("retentionHours") or 48)
    events = monitor.store.recent(limit=20000, since=now() - retention * 3600)
    return build_report(monitor.insights.days.recent(max(1, int(days))), day_stats_from_events(events, eng), eng, days)
