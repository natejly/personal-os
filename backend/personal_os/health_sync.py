"""Pull daily numbers from a connected fitness service's MCP server into the health log.

A *source* is a provider plan (which read-only tools to call, which fields hold which metric) bound to
one configured MCP server. Sync calls those tools for the last few days, finds dated records in what
comes back, and files one reading per (metric, day, source) via Health.upsert_synced, so a re-sync
replaces instead of piling up and two services describing the same day are not double counted.

Trust: an MCP server is third-party code. Two rules keep sync from widening what the user approved:
- the tools a source may call are pinned by schema_hash when the user connects it; a server that
  later renames or reshapes one gets that tool skipped with "changed since you connected" until the
  user re-confirms (the same shape-not-name rule as tool grants in mcp_servers);
- output is only ever parsed for numbers and dates. No text from it reaches a prompt from here.

Shapes: Garmin's server (Taxuspt/garmin_mcp) documents curated JSON per tool. COROS's official server
publishes its schemas only after sign-in, so arguments are built from each tool's advertised input
schema and fields are matched against candidate names. Whatever cannot be read is reported back with
a short sample, rather than guessed at.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .db import Database, new_id, now, row_to_dict
from .health import Health, HealthError

SCHEMA = """
CREATE TABLE IF NOT EXISTS health_sources (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  server_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  pinned TEXT NOT NULL DEFAULT '{}',     -- {tool name: schema_hash} approved at connect time
  days_back INTEGER NOT NULL DEFAULT 7,
  last_sync_at REAL,
  last_error TEXT NOT NULL DEFAULT '',
  last_result TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
"""

MAX_DAYS = 31

# ---- parsing helpers -------------------------------------------------------------------------

DATE_KEYS = ("date", "calendarDate", "calendar_date", "day", "happenDay", "happen_day", "dateStr", "date_str",
             "sleepDate", "wakeDate", "start_time", "startTime", "startTimeLocal", "start_time_local", "startTimestamp",
             "timestamp_gmt", "time")


def parse_date(v: Any) -> date | None:
    """Dates the way fitness APIs write them: 2026-10-01, 20261001, ISO datetimes, epoch s/ms."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        if 19000101 <= n <= 21001231:
            s = str(n)
            try:
                return date(int(s[:4]), int(s[4:6]), int(s[6:]))
            except ValueError:
                return None
        if n > 10**11:  # epoch milliseconds
            n //= 1000
        if 10**9 <= n < 10**11:
            return datetime.fromtimestamp(n, tz=timezone.utc).astimezone().date()
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if re.fullmatch(r"\d{8}", s):
        return parse_date(int(s))
    return None


def records(obj: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every dict in a JSON document, outermost first."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from records(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from records(v, depth + 1)


def record_date(rec: dict[str, Any], fallback: date | None) -> date | None:
    for k in DATE_KEYS:
        if k in rec and (d := parse_date(rec[k])):
            return d
    return fallback


def lookup(rec: dict[str, Any], path: str) -> Any:
    cur: Any = rec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def number(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _json_text(text: str) -> Any:
    text = text.strip()
    for candidate in (text, text[text.find("{"):] if "{" in text else "", text[text.find("["):] if "[" in text else ""):
        if candidate:
            try:
                return json.loads(candidate)
            except ValueError:
                continue
    return None


def parse_output(out: dict[str, Any]) -> Any:
    """An MCP result as JSON. Servers that return a JSON *string* from a tool get it wrapped by the SDK
    as structured `{"result": "<json>"}`, so string values are unwrapped, and the text content is the
    fallback when the structured form holds nothing parseable."""
    s = out.get("structured")
    if isinstance(s, dict) and len(s) == 1 and isinstance(next(iter(s.values())), str):
        s = _json_text(next(iter(s.values())))
    if s is not None and not isinstance(s, str):
        return s
    return _json_text(out.get("content") or "")


# ---- provider plans --------------------------------------------------------------------------

@dataclass
class Field:
    """One metric read from a tool: the first matching key path, scaled into the metric's unit."""
    metric: str
    keys: tuple[str, ...]
    scale: float | Callable[[float, dict[str, Any]], float] = 1.0
    # 'day': one value per date (first match). 'sum': add every record's value on that date (activities).
    mode: str = "day"
    # Values outside this range are treated as unreadable rather than filed (a minutes/seconds mix-up).
    sane: tuple[float, float] = (0, float("inf"))


@dataclass
class ToolPlan:
    names: tuple[str, ...]           # the first one the server offers is used
    fields: list[Field]
    note: str = ""


@dataclass
class Provider:
    key: str
    label: str
    tools: list[ToolPlan]
    server: Callable[[dict[str, Any]], dict[str, Any]]   # launch config from the user's connect form
    needs: list[dict[str, Any]] = field(default_factory=list)  # connect-form fields
    setup: str = ""                                            # one-time steps shown before connecting


def _hours_from_seconds(v: float, _: dict[str, Any]) -> float:
    return v / 3600


def _hours_from_minutes(v: float, _: dict[str, Any]) -> float:
    return v / 60


def _minutes_from_seconds(v: float, _: dict[str, Any]) -> float:
    return v / 60


SLEEP_H = (2, 16)
STEPS = (0, 150_000)
RHR = (25, 130)
EXERCISE_MIN = (0, 1440)
WEIGHT_KG = (25, 350)


def _coros_server(form: dict[str, Any]) -> dict[str, Any]:
    region = str(form.get("region") or "us").lower()
    host = {"us": "mcpus", "eu": "mcpeu", "cn": "mcpcn"}.get(region, "mcpus")
    return {"name": "COROS", "transport": "http", "url": f"https://{host}.coros.com/mcp",
            "description": "COROS official MCP server (sign in with your COROS account)."}


def _uvx() -> str:
    found = shutil.which("uvx") or next((str(p) for p in (Path.home() / ".local/bin/uvx", Path("/opt/homebrew/bin/uvx"),
                                                          Path("/usr/local/bin/uvx")) if p.exists()), None)
    return found or "uvx"


GARMIN_FROM = "git+https://github.com/Taxuspt/garmin_mcp"


def _garmin_server(form: dict[str, Any]) -> dict[str, Any]:
    env = {"GARMIN_EMAIL": str(form.get("email") or "").strip()} if form.get("email") else {}
    secrets = {"GARMIN_PASSWORD": str(form["password"])} if form.get("password") else {}
    return {"name": "Garmin", "transport": "stdio", "command": _uvx(),
            "args": ["--python", "3.12", "--from", GARMIN_FROM, "garmin-mcp"], "env": env, "secrets": secrets,
            "description": "Garmin Connect via Taxuspt/garmin_mcp (unofficial; tokens in ~/.garminconnect)."}


PROVIDERS: dict[str, Provider] = {
    "coros": Provider(
        key="coros", label="COROS",
        server=_coros_server,
        needs=[{"key": "region", "label": "Region", "options": [["us", "Americas / most regions"], ["eu", "Europe"], ["cn", "China"]], "default": "us"}],
        setup="Signs in with your COROS account in the browser. No password is stored here.",
        tools=[
            ToolPlan(("queryDailyHealthData",), [
                Field("steps", ("steps", "step", "totalSteps", "total_steps", "stepCount", "dailySteps"), sane=STEPS),
                Field("resting_hr", ("rhr", "restingHeartRate", "resting_heart_rate", "restingHr", "resting_hr"), sane=RHR),
            ]),
            ToolPlan(("querySleepData",), [
                Field("sleep", ("totalSleepMinutes", "total_sleep_minutes", "sleepMinutes", "total_duration_minutes", "mainSleepMinutes"), _hours_from_minutes, sane=SLEEP_H),
                Field("sleep", ("totalSleepSeconds", "sleepSeconds", "sleep_seconds", "totalSleepTime", "sleepTime", "duration"), _hours_from_seconds, sane=SLEEP_H),
            ]),
            ToolPlan(("queryRestingHeartRate",), [
                Field("resting_hr", ("rhr", "restingHeartRate", "resting_heart_rate", "restingHr", "value", "avg"), sane=RHR),
            ]),
            ToolPlan(("querySportRecords",), [
                Field("exercise", ("totalTimeSeconds", "durationSeconds", "duration_seconds", "totalTime", "duration", "workoutTime"), _minutes_from_seconds, mode="sum", sane=EXERCISE_MIN),
            ]),
        ]),
    "garmin": Provider(
        key="garmin", label="Garmin",
        server=_garmin_server,
        needs=[{"key": "email", "label": "Garmin email"},
               {"key": "password", "label": "Garmin password (optional once signed in)", "secret": True}],
        setup=("Garmin has no public API for individuals, so this uses the community garmin_mcp server (needs uv). "
               "Garmin asks for a one-time code: run this once in a terminal, then connect:\n"
               f"uvx --python 3.12 --from {GARMIN_FROM} garmin-mcp-auth"),
        tools=[
            ToolPlan(("get_sleep_summary_range", "get_sleep_summary"), [
                Field("sleep", ("sleep_seconds", "sleepTimeSeconds", "dailySleepDTO.sleepTimeSeconds"), _hours_from_seconds, sane=SLEEP_H),
            ]),
            ToolPlan(("get_stats",), [
                Field("steps", ("total_steps", "totalSteps"), sane=STEPS),
                Field("resting_hr", ("resting_heart_rate_bpm", "restingHeartRate"), sane=RHR),
            ]),
            ToolPlan(("get_activities_by_date",), [
                Field("exercise", ("duration_seconds", "duration"), _minutes_from_seconds, mode="sum", sane=EXERCISE_MIN),
            ]),
            ToolPlan(("get_weigh_ins", "get_daily_weigh_ins"), [
                Field("weight", ("weight_kg",), sane=WEIGHT_KG),
                Field("weight", ("weight_grams", "weight"), lambda v, _: v / 1000, sane=WEIGHT_KG),
            ]),
        ]),
}


# ---- arguments from a tool's input schema ----------------------------------------------------

RANGE_PAIRS = [("start_date", "end_date"), ("startDate", "endDate"), ("start_day", "end_day"), ("startDay", "endDay"),
               ("from_date", "to_date"), ("fromDate", "toDate"), ("begin_date", "end_date"), ("beginDate", "endDate"),
               ("start", "end"), ("from", "to"), ("startTime", "endTime")]
SINGLE_KEYS = ("date", "day", "queryDate", "query_date", "dateStr", "date_str", "happenDay", "calendarDate", "target_date")


def _compact(prop: dict[str, Any]) -> bool:
    text = json.dumps(prop).lower()
    return prop.get("type") == "integer" or "yyyymmdd" in text or "\\d{8}" in text


def _fmt(d: date, prop: dict[str, Any]) -> Any:
    if _compact(prop):
        s = d.strftime("%Y%m%d")
        return int(s) if prop.get("type") == "integer" else s
    return d.isoformat()


def build_calls(schema: dict[str, Any], start: date, end: date) -> list[tuple[dict[str, Any], date | None]] | None:
    """Calls covering [start, end]: one ranged call if the tool takes a range, else one per day.
    Each comes with the date to file undated records under (None for a range). None = can't call it."""
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    for a, b in RANGE_PAIRS:
        if a in props and b in props:
            args = {a: _fmt(start, props[a]), b: _fmt(end, props[b])}
            if required - set(args):
                break
            if "page_size" in props:
                args["page_size"] = 100
            return [(args, None)]
    for k in SINGLE_KEYS:
        if k in props:
            calls = []
            d = start
            while d <= end:
                calls.append(({k: _fmt(d, props[k])}, d))
                d += timedelta(days=1)
            return None if required - {k} else calls
    if not required:
        for k in ("days", "day_count", "size", "limit"):
            if k in props:
                return [({k: (end - start).days + 1}, None)]
        return [({}, None)]
    return None


# ---- the store and the sync --------------------------------------------------------------------

class HealthSources:
    def __init__(self, db: Database, health: Health, mcp: Callable[[], Any]):
        self.db, self.health, self._mcp = db, health, mcp
        with db.tx() as c:
            c.executescript(SCHEMA)

    @property
    def mcp(self) -> Any:
        m = self._mcp()
        if m is None:
            raise HealthError("Connectors are not available in this build.")
        return m

    def _out(self, r: Any) -> dict[str, Any]:
        d = row_to_dict(r, ("pinned", "last_result")) or {}
        d["enabled"] = bool(d.get("enabled"))
        p = PROVIDERS.get(d.get("provider", ""))
        d["label"] = p.label if p else d.get("provider")
        try:
            srv = self.mcp.store.server(d["server_id"])
        except HealthError:
            srv = None
        d["server"] = {"id": d["server_id"], "name": srv["name"], "transport": srv["transport"], "status": srv["status"],
                       "detail": srv["status_detail"]} if srv else None
        return d

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [self._out(r) for r in c.execute("SELECT * FROM health_sources ORDER BY created_at").fetchall()]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM health_sources WHERE id=?", (id,)).fetchone()
        return self._out(r) if r else None

    def create(self, provider: str, server_id: str) -> dict[str, Any]:
        if provider not in PROVIDERS:
            raise HealthError(f"Unknown provider '{provider}'.")
        sid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO health_sources(id,provider,server_id,created_at) VALUES(?,?,?,?)", (sid, provider, server_id, now()))
        return self.get(sid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in ("enabled", "days_back")}
        if "enabled" in fields:
            fields["enabled"] = int(bool(fields["enabled"]))
        if "days_back" in fields:
            fields["days_back"] = max(1, min(int(fields["days_back"]), MAX_DAYS))
        if fields:
            with self.db.tx() as c:
                c.execute(f"UPDATE health_sources SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str, keep_data: bool = True) -> None:
        s = self.get(id)
        if not s:
            return
        with self.db.tx() as c:
            if not keep_data:
                c.execute("DELETE FROM health_entries WHERE source=?", (s["provider"],))
            c.execute("DELETE FROM health_sources WHERE id=?", (id,))

    def _tools(self, s: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {t["name"]: t for t in self.mcp.store.tools(s["server_id"]) if not t.get("missing_since")}

    def plan(self, id: str) -> dict[str, Any]:
        """What sync would call: per planned tool, the server's tool (if offered) and whether it is pinned."""
        s = self.get(id)
        if not s:
            raise HealthError("No such source.")
        offered = self._tools(s)
        rows = []
        for tp in PROVIDERS[s["provider"]].tools:
            name = next((n for n in tp.names if n in offered), None)
            t = offered.get(name) if name else None
            rows.append({"wants": list(tp.names), "tool": name, "metrics": sorted({f.metric for f in tp.fields}),
                         "description": (t or {}).get("description", "")[:300],
                         "pinned": bool(t and s["pinned"].get(name) == t["schema_hash"]),
                         "changed": bool(t and name in s["pinned"] and s["pinned"][name] != t["schema_hash"])})
        return {"source": s, "tools": rows}

    def pin(self, id: str) -> dict[str, Any]:
        """The user approves the tools as the server offers them now."""
        s = self.get(id)
        if not s:
            raise HealthError("No such source.")
        offered = self._tools(s)
        if not offered:
            raise HealthError(f"{s['label']} is not connected yet, so there is nothing to approve.")
        pinned = {}
        for tp in PROVIDERS[s["provider"]].tools:
            name = next((n for n in tp.names if n in offered), None)
            if name:
                pinned[name] = offered[name]["schema_hash"]
        with self.db.tx() as c:
            c.execute("UPDATE health_sources SET pinned=? WHERE id=?", (json.dumps(pinned), id))
        return self.plan(id)

    async def sync(self, id: str, today: date | None = None) -> dict[str, Any]:
        s = self.get(id)
        if not s:
            raise HealthError("No such source.")
        provider = PROVIDERS[s["provider"]]
        end = today or date.today()
        start = end - timedelta(days=s["days_back"] - 1)
        offered = self._tools(s)
        weight_unit = (self.health.metric("weight") or {}).get("unit", "kg").lower()
        result: dict[str, Any] = {"from": start.isoformat(), "to": end.isoformat(), "written": {}, "unchanged": 0, "problems": []}
        found: dict[tuple[str, date], float] = {}
        summed: dict[tuple[str, date], float] = {}
        for tp in provider.tools:
            name = next((n for n in tp.names if n in offered), None)
            if not name:
                result["problems"].append({"tool": tp.names[0], "error": "not offered by this server"})
                continue
            tool = offered[name]
            if s["pinned"].get(name) != tool["schema_hash"]:
                result["problems"].append({"tool": name, "error": "changed since you connected, or not approved yet"})
                continue
            calls = build_calls(tool.get("parameters") or {}, start, end)
            if calls is None:
                result["problems"].append({"tool": name, "error": "couldn't work out its date arguments"})
                continue
            got_any = False
            sample = ""
            for args, day in calls:
                try:
                    out = await self.mcp.call(tool["slug"], args, timeout=60)
                except Exception as e:  # noqa: BLE001 - one failing tool must not stop the others
                    result["problems"].append({"tool": name, "error": str(e).splitlines()[0][:200] if str(e) else type(e).__name__})
                    break
                if out.get("is_error"):
                    result["problems"].append({"tool": name, "error": (out.get("error") or "the tool reported an error")[:200]})
                    break
                doc = parse_output(out)
                sample = sample or (out.get("content") or "")[:240]
                if doc is None:
                    continue
                for metric, d, v in _extract(tp.fields, doc, day):
                    if not start <= d <= end:
                        continue
                    if metric == "weight" and weight_unit in ("lb", "lbs", "pound", "pounds"):
                        v = v * 2.20462
                    got_any = True
                    if any(f.metric == metric and f.mode == "sum" for f in tp.fields):
                        summed[(metric, d)] = summed.get((metric, d), 0.0) + v
                    else:
                        found.setdefault((metric, d), v)
            if not got_any and not any(p["tool"] == name for p in result["problems"]):
                result["problems"].append({"tool": name, "error": "returned nothing this app could read", "sample": sample})
        for (metric, d), v in {**found, **summed}.items():
            if not self.health.metric(metric):
                continue
            m = self.health.metric(metric) or {}
            try:
                changed = self.health.upsert_synced(metric, d.isoformat(), round(v, int(m.get("decimals", 0))), s["provider"])
            except HealthError as e:
                result["problems"].append({"tool": metric, "error": str(e)})
                continue
            if changed:
                result["written"][metric] = result["written"].get(metric, 0) + 1
            else:
                result["unchanged"] += 1
        err = "; ".join(f"{p['tool']}: {p['error']}" for p in result["problems"])[:500]
        with self.db.tx() as c:
            c.execute("UPDATE health_sources SET last_sync_at=?, last_error=?, last_result=? WHERE id=?",
                      (now(), err, json.dumps(result), id))
        return result


def _extract(fields: list[Field], doc: Any, day: date | None) -> Iterator[tuple[str, date, float]]:
    """(metric, date, value) for every dated record that carries one of the fields."""
    seen: set[tuple[str, date, int]] = set()
    for rec in records(doc):
        d = record_date(rec, day)
        if d is None:
            continue
        for i, f in enumerate(fields):
            for k in f.keys:
                raw = number(lookup(rec, k))
                if raw is None:
                    continue
                v = f.scale(raw, rec) if callable(f.scale) else raw * f.scale
                if not f.sane[0] <= v <= f.sane[1]:
                    continue
                key = (f.metric, d, id(rec) if f.mode == "sum" else i)
                if key in seen or (f.mode != "sum" and any(s[0] == f.metric and s[1] == d for s in seen)):
                    break
                seen.add(key)
                yield f.metric, d, v
                break
