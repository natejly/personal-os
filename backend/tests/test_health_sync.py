"""health_sync: dated numbers out of a fitness MCP server's answers, into the health log.

Run: python backend/tests/test_health_sync.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.health import Health, HealthError  # noqa: E402
from personal_os.health_sync import MAX_DAYS, PROVIDERS, HealthSources, build_calls, parse_date, prose_records  # noqa: E402
from personal_os.mcp_servers import McpServers  # noqa: E402

TODAY = date(2026, 10, 1)


def tool(name: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": f"{name} tool", "danger": "external",
            "parameters": {"type": "object", "properties": props, "required": required}}


GARMIN_TOOLS = [
    tool("get_sleep_summary_range", {"start_date": {"type": "string"}, "end_date": {"type": "string"}}, ["start_date", "end_date"]),
    tool("get_stats", {"date": {"type": "string"}}, ["date"]),
    tool("get_activities_by_date", {"start_date": {"type": "string"}, "end_date": {"type": "string"}, "page_size": {"type": "integer"}}, ["start_date", "end_date"]),
    tool("get_weigh_ins", {"start_date": {"type": "string"}, "end_date": {"type": "string"}}, ["start_date", "end_date"]),
]


class FakeMcp:
    """McpClient's surface that sync uses: `store` and `call(slug, args, timeout)`."""

    def __init__(self, store: McpServers, answers: dict[str, Any]):
        self.store, self.answers, self.calls = store, answers, []

    async def call(self, slug: str, args: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        name = self.store.tool(slug)["name"]
        self.calls.append((name, args))
        a = self.answers[name]
        a = a(args) if callable(a) else a
        return a if isinstance(a, dict) and "content" in a else {"content": a if isinstance(a, str) else json.dumps(a), "is_error": False}


def garmin_answers() -> dict[str, Any]:
    return {
        "get_sleep_summary_range": [{"date": "2026-09-30", "sleep_seconds": 27000, "sleep_score": 80},
                                    {"date": "2026-10-01", "sleep_seconds": 25200}],
        "get_stats": lambda a: {"date": a["date"], "total_steps": 9000 if a["date"] == "2026-10-01" else 7000, "resting_heart_rate_bpm": 51},
        "get_activities_by_date": {"count": 2, "date_range": {"start": "2026-09-30", "end": "2026-10-01"}, "activities": [
            {"id": 1, "start_time": "2026-10-01 07:00:00", "duration_seconds": 1800},
            {"id": 2, "start_time": "2026-10-01 18:00:00", "duration_seconds": 2700},
            {"id": 3, "start_time": "2026-09-30T06:30:00", "duration_seconds": 3600}]},
        "get_weigh_ins": {"average_weight_kg": 80, "measurements": [{"date": "2026-10-01", "weight_kg": 80.0, "weight_grams": 80000}]},
    }


class Helpers(unittest.TestCase):
    def test_parse_date(self) -> None:
        for v in ["2026-10-01", "2026-10-01T23:10:00Z", 20261001, "20261001", "2026-10-01 07:00:00"]:
            self.assertEqual(parse_date(v), TODAY, v)
        self.assertIsNone(parse_date("soon"))
        self.assertIsNone(parse_date(True))
        self.assertIsNotNone(parse_date(1790000000000))  # epoch ms

    def test_build_calls(self) -> None:
        start, end = date(2026, 9, 29), TODAY
        rng = build_calls({"properties": {"startDate": {"type": "integer", "description": "YYYYMMDD"}, "endDate": {"type": "integer"}}}, start, end)
        self.assertEqual(rng, [({"startDate": 20260929, "endDate": 20261001}, None)])
        per_day = build_calls({"properties": {"date": {"type": "string"}}, "required": ["date"]}, start, end)
        self.assertEqual([c[0]["date"] for c in per_day], ["2026-09-29", "2026-09-30", "2026-10-01"])
        self.assertEqual(per_day[0][1], start)
        self.assertIsNone(build_calls({"properties": {"athlete": {"type": "string"}}, "required": ["athlete"]}, start, end))
        compact = build_calls({"properties": {"day": {"type": "string", "pattern": "^\\\\d{8}$"}}}, end, end)
        self.assertEqual(compact[0][0], {"day": "20261001"})
        # Filters described as optional stay required in the schema. Pass the dates anyway.
        sport = build_calls({
            "properties": {
                "startDate": {"type": "string", "description": "Start date in yyyyMMdd format, defaults to 7 days ago"},
                "endDate": {"type": "string", "description": "End date in yyyyMMdd format, defaults to today"},
                "sportTypeCodes": {"type": "array", "description": "Optional sport type codes"},
                "limit": {"type": "integer", "description": "Maximum number of records to return, default 20"},
            },
            "required": ["startDate", "endDate", "sportTypeCodes", "limit"],
        }, start, end)
        self.assertEqual(sport, [({"startDate": "20260929", "endDate": "20261001", "limit": 100}, None)])

    def test_prose_records_read_labeled_days(self) -> None:
        daily = prose_records(
            "Daily Health Data | Resting HR: 49 bpm\n\n"
            "--- 20261001 ---\nSteps: 3,190 | Exercise: 1h 14min\n"
            "--- 20260930 ---\nSteps: 8,000 | Exercise: 0 min\n")
        self.assertEqual(daily[0]["steps"], 3190)
        self.assertEqual(daily[0]["exerciseMinutes"], 74)
        self.assertNotIn("exerciseMinutes", daily[1])
        self.assertFalse(any("restingHeartRate" in r for r in daily))
        rhr = prose_records("2026-10-01: 52 bpm\n2026-09-30: No data\n")
        self.assertEqual(rhr, [{"date": "2026-10-01", "restingHeartRate": 52.0}])
        sleep = prose_records("2026-10-01\nDaily Sleep: 8h 52min (incl. naps)\n2026-09-30\nSleep detail for this day is not available yet.\n")
        self.assertAlmostEqual(sleep[0]["sleepHours"], 8 + 52 / 60)
        sport = prose_records("1. Indoor Cycling — 2026-10-01\n   Duration: 47:01\n2. Indoor Cycling — 2026-10-01\n   Duration: 1:00:30\n")
        self.assertEqual([r["totalTimeSeconds"] for r in sport], [47 * 60 + 1, 3600 + 30])


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db = Database(tempfile.mkdtemp(prefix="healthsync-"))
        self.health = Health(db)
        self.store = McpServers(db)
        self.server = self.store.create_server("Garmin", command="uvx")
        self.store.sync_tools(self.server["id"], GARMIN_TOOLS)
        self.mcp = FakeMcp(self.store, garmin_answers())
        self.sources = HealthSources(db, self.health, lambda: self.mcp)
        self.src = self.sources.create("garmin", self.server["id"])
        self.sources.update(self.src["id"], {"days_back": 2})

    async def test_nothing_runs_until_approved(self) -> None:
        res = await self.sources.sync(self.src["id"], TODAY)
        self.assertEqual(self.mcp.calls, [])
        self.assertEqual(len(res["problems"]), 4)
        self.assertTrue(all("approved" in p["error"] for p in res["problems"]))

    async def test_garmin_sync_files_converted_daily_values(self) -> None:
        plan = self.sources.pin(self.src["id"])
        self.assertTrue(all(t["pinned"] for t in plan["tools"]))
        res = await self.sources.sync(self.src["id"], TODAY)
        self.assertEqual(res["problems"], [])
        today = {m["key"]: m["today"] for m in self.health.summary(2, TODAY.isoformat())}
        self.assertEqual(today["sleep"], 7.0)
        self.assertEqual(today["steps"], 9000)
        self.assertEqual(today["resting_hr"], 51)
        self.assertEqual(today["exercise"], 75)        # 30 + 45 minutes
        self.assertAlmostEqual(today["weight"], 176.4, places=1)  # 80 kg in the default lb unit
        self.assertEqual({e["source"] for e in self.health.entries()}, {"garmin"})
        # get_stats takes one date, so it is called per day; the ranged tools once.
        self.assertEqual(sum(1 for n, _ in self.mcp.calls if n == "get_stats"), 2)
        self.assertEqual(sum(1 for n, _ in self.mcp.calls if n == "get_weigh_ins"), 1)
        again = await self.sources.sync(self.src["id"], TODAY)
        self.assertEqual(again["written"], {})          # nothing changed: no rewrites
        self.assertGreater(again["unchanged"], 0)
        self.assertEqual(len(self.health.entries("steps")), 2)

    async def test_a_reshaped_tool_is_skipped_until_reapproved(self) -> None:
        self.sources.pin(self.src["id"])
        changed = [dict(t) for t in GARMIN_TOOLS]
        changed[1] = tool("get_stats", {"date": {"type": "string"}, "also_send_to": {"type": "string"}}, ["date"])
        self.store.sync_tools(self.server["id"], changed)
        res = await self.sources.sync(self.src["id"], TODAY)
        self.assertIn("get_stats", [p["tool"] for p in res["problems"]])
        self.assertNotIn("get_stats", [n for n, _ in self.mcp.calls])
        self.assertTrue(next(t for t in self.sources.plan(self.src["id"])["tools"] if t["tool"] == "get_stats")["changed"])

    async def test_unreadable_and_failing_tools_are_reported(self) -> None:
        self.sources.pin(self.src["id"])
        self.mcp.answers["get_stats"] = "No stats found for this date"
        self.mcp.answers["get_weigh_ins"] = {"content": "Error: run garmin-mcp-auth", "is_error": True, "error": "run garmin-mcp-auth"}
        res = await self.sources.sync(self.src["id"], TODAY)
        probs = {p["tool"]: p for p in res["problems"]}
        self.assertIn("could read", probs["get_stats"]["error"])
        self.assertIn("No stats", probs["get_stats"]["sample"])
        self.assertIn("garmin-mcp-auth", probs["get_weigh_ins"]["error"])
        self.assertEqual(self.health.summary(1, TODAY.isoformat(), metric="sleep")[0]["today"], 7.0)  # others still landed

    async def test_a_gap_since_the_last_good_sync_is_filled_and_a_failure_is_not_a_sync(self) -> None:
        self.sources.pin(self.src["id"])
        sid = self.src["id"]

        def last_sync(ts: float | None) -> None:
            with self.sources.db.tx() as c:
                c.execute("UPDATE health_sources SET last_sync_at=? WHERE id=?", (ts, sid))

        noon = lambda d: datetime.combine(d, datetime.min.time()).timestamp() + 12 * 3600  # noqa: E731
        last_sync(noon(TODAY - timedelta(days=5)))  # offline for five days: days_back (2) alone would miss three
        res = await self.sources.sync(sid, TODAY)
        self.assertEqual(res["from"], (TODAY - timedelta(days=5)).isoformat())
        last_sync(noon(TODAY - timedelta(days=200)))  # a long gap is capped at MAX_DAYS
        res = await self.sources.sync(sid, TODAY)
        self.assertEqual(res["from"], (TODAY - timedelta(days=MAX_DAYS - 1)).isoformat())

        before = noon(TODAY - timedelta(days=3))
        last_sync(before)
        self.mcp.answers["get_weigh_ins"] = {"content": "Error: down", "is_error": True, "error": "down"}
        await self.sources.sync(sid, TODAY)
        self.assertEqual(self.sources.get(sid)["last_sync_at"], before)  # the failed call's days are fetched next time

    async def test_sdk_wrapped_structured_output_is_unwrapped(self) -> None:
        # A FastMCP-style server returning a JSON string arrives as structured {"result": "<json>"}.
        self.sources.pin(self.src["id"])
        doc = json.dumps([{"date": "2026-10-01", "sleep_seconds": 28800}])
        self.mcp.answers["get_sleep_summary_range"] = {"content": doc, "structured": {"result": doc}, "is_error": False}
        await self.sources.sync(self.src["id"], TODAY)
        self.assertEqual(self.health.summary(1, TODAY.isoformat(), metric="sleep")[0]["today"], 8.0)

    async def test_insane_values_are_not_filed(self) -> None:
        self.sources.pin(self.src["id"])
        self.mcp.answers["get_sleep_summary_range"] = [{"date": "2026-10-01", "sleep_seconds": 420}]  # 7 min: a units mix-up
        await self.sources.sync(self.src["id"], TODAY)
        self.assertIsNone(self.health.summary(1, TODAY.isoformat(), metric="sleep")[0]["today"])

    async def test_delete_can_keep_or_drop_synced_data(self) -> None:
        self.sources.pin(self.src["id"])
        await self.sources.sync(self.src["id"], TODAY)
        self.sources.delete(self.src["id"], keep_data=False)
        self.assertEqual(self.health.entries(), [])
        with self.assertRaises(HealthError):
            await self.sources.sync(self.src["id"], TODAY)


COROS_TOOLS = [
    tool("queryDailyHealthData", {"days": {"type": "integer", "description": "Number of recent days to query, default 7"}}, []),
    tool("querySleepOverview", {
        "startDate": {"type": "string", "description": "Optional start date in yyyyMMdd format"},
        "endDate": {"type": "string", "description": "Optional end date in yyyyMMdd format"},
        "days": {"type": "integer", "description": "Number of recent days, default 7"},
    }, []),
    tool("queryRestingHeartRate", {"days": {"type": "integer", "description": "Number of recent days, default 7"}}, []),
    tool("querySportRecords", {
        "startDate": {"type": "string", "description": "Start date in yyyyMMdd format, defaults to 7 days ago"},
        "endDate": {"type": "string", "description": "End date in yyyyMMdd format, defaults to today"},
        "sportTypeCodes": {"type": "array", "description": "Optional sport type codes"},
        "limit": {"type": "integer", "description": "Maximum number of records to return, default 20"},
    }, ["startDate", "endDate", "sportTypeCodes", "limit"]),
]


def _quoted(text: str) -> dict[str, Any]:
    return {"content": json.dumps(text), "is_error": False}


class CorosSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_labeled_text_is_filed_and_workouts_replace_the_day_total(self) -> None:
        db = Database(tempfile.mkdtemp(prefix="healthsync-"))
        health = Health(db)
        store = McpServers(db)
        server = store.create_server("COROS", transport="http", url="https://mcpus.coros.com/mcp")
        store.sync_tools(server["id"], COROS_TOOLS)
        mcp = FakeMcp(store, {
            "queryDailyHealthData": _quoted(
                "Daily Health Data | Resting HR: 49 bpm\n\n"
                "--- 20261001 ---\nSteps: 3,190 | Exercise: 1h 14min\n"
                "--- 20260930 ---\nSteps: 8,000 | Exercise: 40 min\n"),
            "querySleepOverview": _quoted("2026-10-01\nDaily Sleep: 8h 52min (incl. naps)\n"),
            "queryRestingHeartRate": _quoted("2026-10-01: 52 bpm\n2026-09-30: No data\n"),
            "querySportRecords": _quoted(
                "1. Indoor Cycling — 2026-10-01\n   Duration: 47:01\n"
                "2. Indoor Cycling — 2026-10-01\n   Duration: 25:01\n"),
        })
        sources = HealthSources(db, health, lambda: mcp)
        src = sources.create("coros", server["id"])
        sources.update(src["id"], {"days_back": 2})
        sources.pin(src["id"])
        res = await sources.sync(src["id"], TODAY)
        self.assertEqual(res["problems"], [])
        by_day = {m["key"]: {p["day"]: p["value"] for p in m["series"]} for m in health.summary(2, TODAY.isoformat())}
        self.assertEqual(by_day["steps"][TODAY.isoformat()], 3190)
        self.assertEqual(by_day["steps"]["2026-09-30"], 8000)
        self.assertEqual(by_day["resting_hr"][TODAY.isoformat()], 52)
        self.assertIsNone(by_day["resting_hr"]["2026-09-30"])
        self.assertAlmostEqual(by_day["sleep"][TODAY.isoformat()], 8.9, places=1)
        # 47:01 + 25:01 replaces the day's "1h 14min"; the other day keeps its own total.
        self.assertEqual(by_day["exercise"][TODAY.isoformat()], 72)
        self.assertEqual(by_day["exercise"]["2026-09-30"], 40)
        sport = next(args for name, args in mcp.calls if name == "querySportRecords")
        self.assertEqual(sport, {"startDate": "20260930", "endDate": "20261001", "limit": 100})


class ProviderTests(unittest.TestCase):
    def test_launch_configs(self) -> None:
        c = PROVIDERS["coros"].server({"region": "eu"})
        self.assertEqual((c["transport"], c["url"]), ("http", "https://mcpeu.coros.com/mcp"))
        g = PROVIDERS["garmin"].server({"email": "a@b.c", "password": "pw"})
        self.assertEqual(g["transport"], "stdio")
        self.assertEqual(g["args"][-1], "garmin-mcp")
        self.assertEqual(g["secrets"], {"GARMIN_PASSWORD": "pw"})  # a secret, never plain env
        self.assertNotIn("GARMIN_PASSWORD", g["env"])


if __name__ == "__main__":
    unittest.main()
