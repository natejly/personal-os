"""Recurring todos (Vikunja semantics) and the Taskwarrior-style urgency score. Offline.

Run: backend/.venv/bin/python backend/tests/test_todo_rules.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import todo_rules as R  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.modules import ModuleContext  # noqa: E402
from personal_os.modules.todos import TodosModule  # noqa: E402
from personal_os.todos import Todos  # noqa: E402

D = date.fromisoformat


def rep(unit: str, mode: str = "from_due", every: int = 1) -> dict:
    return {"every": every, "unit": unit, "mode": mode}


class NextDueTests(unittest.TestCase):
    def test_daily_overdue_lands_strictly_after_completion(self) -> None:
        self.assertEqual(R.next_due(D("2026-10-04"), rep("day"), D("2026-10-05")), D("2026-10-06"))

    def test_daily_due_today_gives_tomorrow(self) -> None:
        self.assertEqual(R.next_due(D("2026-10-05"), rep("day"), D("2026-10-05")), D("2026-10-06"))

    def test_weekly_three_weeks_late_stays_week_aligned(self) -> None:
        due, today = D("2026-09-14"), D("2026-10-05")  # 21 days late
        got = R.next_due(due, rep("week"), today)
        self.assertGreater(got, today)
        self.assertEqual((got - due).days % 7, 0)
        self.assertEqual(got, D("2026-10-12"))

    def test_future_due_still_advances_one_interval(self) -> None:
        self.assertEqual(R.next_due(D("2026-10-20"), rep("week"), D("2026-10-05")), D("2026-10-27"))

    def test_monthly_clamps_to_month_end(self) -> None:
        self.assertEqual(R.next_due(D("2026-01-31"), rep("month"), D("2026-01-31")), D("2026-02-28"))
        self.assertEqual(R.next_due(D("2028-01-31"), rep("month"), D("2028-01-31")), D("2028-02-29"))

    def test_monthly_from_due_recovers_day_after_clamp(self) -> None:
        self.assertEqual(R.next_due(D("2026-01-31"), rep("month"), D("2026-02-28")), D("2026-03-31"))

    def test_yearly_leap_day(self) -> None:
        self.assertEqual(R.next_due(D("2028-02-29"), rep("year"), D("2028-02-29")), D("2029-02-28"))

    def test_from_completion_monthly(self) -> None:
        self.assertEqual(R.next_due(D("2026-01-01"), rep("month", "from_completion"), D("2026-01-31")), D("2026-02-28"))

    def test_from_completion_every_two_weeks(self) -> None:
        self.assertEqual(R.next_due(None, rep("week", "from_completion", 2), D("2026-10-05")), D("2026-10-19"))

    def test_no_due_starts_from_completion(self) -> None:
        self.assertEqual(R.next_due(None, rep("day"), D("2026-10-05")), D("2026-10-06"))

    def test_invalid_specs_raise(self) -> None:
        with self.assertRaises(ValueError):
            R.parse_repeat({"every": 0, "unit": "day"})
        with self.assertRaises(ValueError):
            R.parse_repeat({"every": 1, "unit": "fortnight"})
        with self.assertRaises(ValueError):
            R.parse_repeat({"every": 1, "unit": "day", "mode": "sometimes"})
        self.assertEqual(R.parse_repeat({"every": 9999, "unit": "day"})["every"], 366)
        self.assertIsNone(R.parse_repeat(None))


class SpawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.todos = Todos(Database(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_completing_spawns_exactly_one_next(self) -> None:
        t = self.todos.create("Water plants", due="2026-09-28", priority=1, notes="n", repeat=rep("week"))
        self.todos.update(t["id"], {"done": True}, today=D("2026-10-05"))
        rows = self.todos.list()
        self.assertEqual(len(rows), 1)
        n = rows[0]
        # 2026-09-28 + k weeks, strictly after 2026-10-05 -> 2026-10-12
        self.assertEqual((n["title"], n["priority"], n["due"], n["notes"]), ("Water plants", 1, "2026-10-12", "n"))
        self.assertEqual(n["repeat"], rep("week"))
        self.assertEqual(n["source"], "local")
        self.assertNotEqual(n["id"], t["id"])

    def test_completed_row_loses_repeat_so_no_double_spawn(self) -> None:
        t = self.todos.create("Chore", due="2026-10-01", repeat=rep("day"))
        self.todos.update(t["id"], {"done": True}, today=D("2026-10-05"))
        self.assertIsNone(self.todos.get(t["id"])["repeat"])
        self.todos.update(t["id"], {"done": False})
        self.todos.update(t["id"], {"done": True}, today=D("2026-10-05"))
        self.assertEqual(len(self.todos.list(include_done=True)), 2)

    def test_non_repeating_spawns_nothing(self) -> None:
        t = self.todos.create("One-off", due="2026-10-01")
        self.todos.update(t["id"], {"done": True})
        self.assertEqual(len(self.todos.list(include_done=True)), 1)

    def test_sync_path_still_recurs(self) -> None:
        t = self.todos.create("Synced", due="2026-10-04", repeat=rep("day"))
        self.todos.update(t["id"], {"done": True}, notify=False, today=D("2026-10-05"))
        self.assertEqual([r["due"] for r in self.todos.list()], ["2026-10-06"])

    def test_update_sets_and_clears_repeat(self) -> None:
        t = self.todos.create("x")
        self.assertEqual(self.todos.update(t["id"], {"repeat": rep("month")})["repeat"], rep("month"))
        self.assertIsNone(self.todos.update(t["id"], {"repeat": None})["repeat"])
        with self.assertRaises(ValueError):
            self.todos.update(t["id"], {"repeat": {"every": 0, "unit": "day"}})


def todo(**kw: Any) -> dict:
    base = {"due": None, "priority": 2, "done": 0, "notes": "", "project_id": None, "created_at": datetime(2026, 10, 5, 12).timestamp()}
    return {**base, **kw}


class UrgencyTests(unittest.TestCase):
    today = D("2026-10-05")

    def u(self, **kw: Any) -> float:
        return R.urgency(todo(**kw), self.today)

    def test_due_ordering_at_equal_priority(self) -> None:
        overdue = self.u(due="2026-09-20")
        tomorrow = self.u(due="2026-10-06")
        month = self.u(due="2026-11-05")
        none = self.u()
        self.assertTrue(overdue > tomorrow > month)
        self.assertEqual(month, none)

    def test_priority_ordering(self) -> None:
        self.assertGreater(self.u(priority=1, due="2026-10-10"), self.u(priority=3, due="2026-10-10"))

    def test_done_is_zero(self) -> None:
        self.assertEqual(self.u(done=1, due="2026-09-01", priority=1), 0.0)

    def test_hand_computed_values(self) -> None:
        self.assertEqual(self.u(due="2026-09-20", priority=1), 18.0)  # 12*1.0 + 6
        self.assertEqual(self.u(priority=3), 1.8)
        self.assertEqual(self.u(due="2026-10-19", priority=2), 6.3)  # 12*0.2 + 3.9
        self.assertEqual(self.u(priority=2, notes="x", project_id="p"), 5.7)  # 3.9 + 0.8 + 1

    def test_fixture_order(self) -> None:
        rows = [
            todo(id="a", due="2026-10-06", priority=2),  # 12.243
            todo(id="b", due="2026-09-01", priority=3),  # 13.8
            todo(id="c", priority=1),                    # 6.0
            todo(id="d", due="2026-10-05", priority=1),  # 14.8
            todo(id="e", priority=2),                    # 3.9
        ]
        ordered = [r["id"] for r in sorted(rows, key=lambda r: -R.urgency(r, self.today))]
        self.assertEqual(ordered, ["d", "b", "a", "c", "e"])
        self.assertEqual(ordered, [r["id"] for r in sorted(rows, key=lambda r: -R.urgency(r, self.today))])


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        ctx = ModuleContext(db=Database(self.tmp.name), settings=lambda: {}, set_settings=lambda _s: None, google=None,
                            sid=lambda p: p, wsid=lambda p: p)
        self.mod = TodosModule(ctx)
        app = FastAPI()
        app.include_router(self.mod.router())
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sort_urgency_returns_ordered_rows_with_field(self) -> None:
        self.client.post("/todos", json={"title": "low", "priority": 3})
        self.client.post("/todos", json={"title": "late", "priority": 2, "due": "2020-01-01"})
        self.client.post("/todos", json={"title": "hi", "priority": 1})
        rows = self.client.get("/todos?sort=urgency").json()
        self.assertEqual([r["title"] for r in rows], ["late", "hi", "low"])
        self.assertTrue(all("urgency" in r for r in rows))
        self.assertNotIn("urgency", self.client.get("/todos").json()[0])

    def test_repeat_round_trip_and_validation(self) -> None:
        t = self.client.post("/todos", json={"title": "r", "due": "2026-10-01", "repeat": rep("week")}).json()
        self.assertEqual(t["repeat"], rep("week"))
        self.assertIsNone(self.client.put(f"/todos/{t['id']}", json={"clear_repeat": True}).json()["repeat"])
        self.assertEqual(self.client.put(f"/todos/{t['id']}", json={"repeat": {"every": 0, "unit": "day"}}).status_code, 400)
        self.assertEqual(self.client.post("/todos", json={"title": "z", "repeat": {"every": 1, "unit": "x"}}).status_code, 400)

    def test_completing_through_route_spawns(self) -> None:
        t = self.client.post("/todos", json={"title": "r", "due": "2020-01-01", "repeat": rep("week")}).json()
        self.client.put(f"/todos/{t['id']}", json={"done": True})
        opened = self.client.get("/todos").json()
        self.assertEqual(len(opened), 1)
        self.assertGreater(opened[0]["due"], date.today().isoformat())

    def test_todo_list_tool_exposes_urgency(self) -> None:
        class Box:
            specs: dict = {}
        box = Box()
        box.specs = {}
        self.mod.register_tools(box)  # type: ignore[arg-type]
        self.mod.store.create("a", priority=3)
        self.mod.store.create("b", priority=1)
        out = asyncio.run(box.specs["todo_list"].fn({"project_id": None}, sort="urgency"))
        self.assertEqual(out["todos"][0]["title"], "b")
        self.assertIn("urgency", out["todos"][0])


if __name__ == "__main__":
    unittest.main()
