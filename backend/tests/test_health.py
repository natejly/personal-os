"""Health tracking (health.py + modules/health.py): daily rollups, goals and streaks, value checks,
the routes, the agent tools, and that /health/... stays behind the token even though bare /health is public.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_health.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="healthtest-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, modules, toolbox  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.health import Health, HealthError, daily_value  # noqa: E402
from personal_os.modules import get  # noqa: E402
from personal_os.modules.health import HealthModule  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
TODAY = date(2026, 10, 1)


def day(n: int) -> str:
    """n days before TODAY."""
    return (TODAY - timedelta(days=n)).isoformat()


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Health(Database(tempfile.mkdtemp(prefix="healthstore-")))

    def test_defaults_are_seeded_once(self) -> None:
        keys = [m["key"] for m in self.h.metrics()]
        self.assertIn("sleep", keys)
        self.assertIn("meds", keys)
        Health(self.h.db)  # a second construction must not duplicate or reset
        self.assertEqual([m["key"] for m in self.h.metrics()], keys)

    def test_daily_aggregation(self) -> None:
        rows = [{"value": 2}, {"value": 4}]
        self.assertEqual(daily_value("sum", rows), 6)
        self.assertEqual(daily_value("avg", rows), 3)
        self.assertEqual(daily_value("last", rows), 4)
        self.assertIsNone(daily_value("sum", []))

    def test_summary_series_avg_and_streak(self) -> None:
        for n in range(4):  # 4 days in a row at or over 8 glasses
            self.h.log("water", 8, day(n))
        self.h.log("water", 3, day(5))
        self.h.log("water", 2, day(0))  # today's total is 10
        s = self.h.summary(7, TODAY.isoformat(), metric="water")[0]
        self.assertEqual(len(s["series"]), 7)
        self.assertEqual(s["series"][-1], {"day": day(0), "value": 10})
        self.assertIsNone(s["series"][2]["value"])  # 4 days back
        self.assertEqual(s["today"], 10)
        self.assertEqual(s["streak"], 4)
        self.assertEqual(s["logged_days"], 5)
        self.assertEqual(s["met_days"], 4)

    def test_streak_counts_from_yesterday_while_today_is_open(self) -> None:
        self.h.log("exercise", 45, day(1))
        self.h.log("exercise", 30, day(2))
        self.h.log("exercise", 10, day(0))  # under goal so far today
        self.assertEqual(self.h.summary(7, TODAY.isoformat(), metric="exercise")[0]["streak"], 2)

    def test_at_most_goal(self) -> None:
        m = self.h.create_metric("Coffee", "cups", agg="sum", goal=2, goal_dir="at_most")
        self.h.log(m["key"], 1, day(0))
        self.h.log(m["key"], 3, day(1))
        s = self.h.summary(2, TODAY.isoformat(), metric=m["key"])[0]
        self.assertEqual(s["streak"], 1)
        self.assertEqual(s["met_days"], 1)

    def test_check_metric_has_one_answer_per_day(self) -> None:
        self.h.log("meds", 0, day(0))
        self.h.log("meds", 1, day(0))
        self.assertEqual(len(self.h.entries("meds")), 1)
        self.assertEqual(self.h.summary(1, TODAY.isoformat(), metric="meds")[0]["today"], 1)

    def test_value_checks(self) -> None:
        for metric, v in [("mood", 6), ("mood", 0), ("meds", 0.5), ("steps", -1), ("sleep", float("nan"))]:
            with self.assertRaises(HealthError, msg=f"{metric}={v}"):
                self.h.log(metric, v)
        with self.assertRaises(HealthError):
            self.h.log("nope", 1)
        with self.assertRaises(HealthError):
            self.h.log("sleep", 7, "yesterday")

    def test_synced_readings_replace_and_do_not_double_count(self) -> None:
        self.assertTrue(self.h.upsert_synced("steps", day(0), 9000, "coros"))
        self.assertFalse(self.h.upsert_synced("steps", day(0), 9000, "coros"))  # unchanged: no write
        self.assertTrue(self.h.upsert_synced("steps", day(0), 9500, "coros"))
        self.h.upsert_synced("steps", day(0), 9100, "garmin")  # same walk seen twice
        self.h.log("steps", 500, day(0))
        self.assertEqual(len(self.h.entries("steps")), 3)
        self.assertEqual(self.h.summary(1, TODAY.isoformat(), metric="steps")[0]["today"], 9500)  # largest source
        self.h.log("sleep", 7.5, day(0))  # the user logged last night, then the watch synced it too
        self.h.upsert_synced("sleep", day(0), 6.9, "garmin")
        self.assertEqual(self.h.summary(1, TODAY.isoformat(), metric="sleep")[0]["today"], 7.5)  # never 14.4
        self.h.log("water", 3, day(0))
        self.h.log("water", 2, day(0))  # manual entries still add up among themselves
        self.assertEqual(self.h.summary(1, TODAY.isoformat(), metric="water")[0]["today"], 5)
        with self.assertRaises(HealthError):
            self.h.upsert_synced("steps", day(0), 1, "manual")

    def test_custom_metrics(self) -> None:
        a = self.h.create_metric("Vitamin D", kind="check")
        b = self.h.create_metric("vitamin d", kind="check")
        self.assertEqual((a["key"], b["key"]), ("vitamin_d", "vitamin_d_2"))
        self.assertEqual(self.h.resolve("Vitamin D")["key"], "vitamin_d")
        self.h.log(a["key"], 1)
        self.h.delete_metric(a["key"])
        self.assertIsNone(self.h.metric(a["key"]))
        self.assertEqual(self.h.entries(a["key"]), [])
        with self.assertRaises(HealthError):
            self.h.delete_metric("sleep")
        with self.assertRaises(HealthError):
            self.h.create_metric("Bad", goal=3)  # goal without a direction

    def test_update_metric_hides_and_clears_goal(self) -> None:
        self.h.update_metric("steps", {"hidden": True})
        self.assertNotIn("steps", [m["key"] for m in self.h.metrics(include_hidden=False)])
        m = self.h.update_metric("sleep", {"goal": None, "goal_dir": None})
        self.assertIsNone(m["goal"])
        m = self.h.update_metric("sleep", {"goal": 7, "goal_dir": "at_least", "kind": "check"})
        self.assertEqual((m["goal"], m["kind"]), (7, "number"))  # kind is fixed


class RouteTests(unittest.TestCase):
    def test_health_routes_need_the_token(self) -> None:
        anon = TestClient(app)
        self.assertEqual(anon.get("/health").status_code, 200)  # liveness stays public
        for path in ["/health/metrics", "/health/summary", "/health/entries"]:
            self.assertEqual(anon.get(path).status_code, 401, path)

    def test_entry_crud_and_errors(self) -> None:
        e = client.post("/health/entries", json={"metric": "sleep", "value": 7.25, "day": "2026-09-30", "note": "ok"}).json()
        try:
            self.assertEqual(e["day"], "2026-09-30")
            self.assertIn(e["id"], [x["id"] for x in client.get("/health/entries?metric=sleep").json()])
            r = client.put(f"/health/entries/{e['id']}", json={"value": 8})
            self.assertEqual(r.json()["value"], 8)
            s = client.get("/health/summary?days=3&today=2026-10-01").json()
            sleep = next(x for x in s if x["key"] == "sleep")
            self.assertEqual(sleep["series"][1]["value"], 8)
        finally:
            self.assertEqual(client.delete(f"/health/entries/{e['id']}").status_code, 200)
        self.assertEqual(client.delete(f"/health/entries/{e['id']}").status_code, 404)
        self.assertEqual(client.post("/health/entries", json={"metric": "mood", "value": 9}).status_code, 400)
        self.assertEqual(client.post("/health/metrics", json={"label": " "}).status_code, 400)
        self.assertEqual(client.put("/health/metrics/nope", json={"unit": "x"}).status_code, 404)
        self.assertEqual(client.delete("/health/metrics/sleep").status_code, 400)

    def test_metric_routes(self) -> None:
        m = client.post("/health/metrics", json={"label": "Stretching", "unit": "min", "agg": "sum", "goal": 10, "goal_dir": "at_least"}).json()
        try:
            r = client.put(f"/health/metrics/{m['key']}", json={"clear_goal": True}).json()
            self.assertIsNone(r["goal"])
            self.assertIsNone(r["goal_dir"])
        finally:
            client.delete(f"/health/metrics/{m['key']}")

    def test_dashboard_carries_health(self) -> None:
        d = client.get("/dashboard").json()
        self.assertIn("sleep", [m["key"] for m in d["health"]])


class ToolTests(unittest.TestCase):
    def call(self, name: str, **kw: object) -> dict:
        return asyncio.run(toolbox.specs[name].fn({"project_id": None}, **kw))

    def test_tools_registered(self) -> None:
        for n, danger in [("health_summary", "safe"), ("health_log", "writes"), ("health_delete_entry", "writes")]:
            self.assertEqual(toolbox.specs[n].group, "health")
            self.assertEqual(toolbox.specs[n].danger, danger)

    def test_log_by_label_then_read_and_delete(self) -> None:
        r = self.call("health_log", metric="Water", value=3, day="2026-10-01")
        self.assertEqual(r["logged"], "3 glasses")
        s = self.call("health_summary", metric="water", days=3, today="2026-10-01")["metrics"][0]
        self.assertEqual(s["daily"]["2026-10-01"], "3 glasses")
        self.assertIn(r["id"], [e["id"] for e in s["recent_entries"]])
        self.assertEqual(self.call("health_delete_entry", id=r["id"])["deleted"]["metric"], "water")
        self.assertIn("error", self.call("health_delete_entry", id=r["id"]))
        self.assertIn("error", self.call("health_log", metric="blood sugar", value=90))
        self.assertIn("error", self.call("health_log", metric="mood", value=11))

    def test_module_is_registered(self) -> None:
        self.assertIsInstance(get(modules, "health", HealthModule).store, Health)


if __name__ == "__main__":
    unittest.main()
