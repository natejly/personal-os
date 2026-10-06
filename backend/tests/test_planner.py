"""Todo estimates and the deterministic time-block planner. Offline: stub Google, no model.

Run: backend/.venv/bin/python backend/tests/test_planner.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm, planner as pl  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.modules import ModuleContext  # noqa: E402
from personal_os.modules.planner import PlannerModule  # noqa: E402
from personal_os.todos import Todos  # noqa: E402

NOW = datetime(2026, 10, 5, 8, 0)  # a Monday
CFG = dict(pl.DEFAULT_CONFIG)


def dt(day: int, hm: str) -> datetime:
    h, m = hm.split(":")
    return datetime(2026, 10, day, int(h), int(m))


def todo(tid: str, **kw: Any) -> dict[str, Any]:
    return {"id": tid, "title": f"T{tid}", "due": None, "priority": 2, "done": 0, "notes": "", "project_id": None,
            "created_at": NOW.timestamp(), "estimate_min": None, **kw}


MEETING = [(dt(5, "10:00"), dt(5, "11:00"))]


def overlaps(a: tuple[datetime, datetime], b: tuple[datetime, datetime], pad: int = 0) -> bool:
    p = timedelta(minutes=pad)
    return a[0] < b[1] + p and b[0] - p < a[1]


def span(b: dict[str, Any]) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])


class WindowTests(unittest.TestCase):
    def test_meeting_with_buffers_splits_the_day(self) -> None:
        w = pl.free_windows(MEETING, NOW, CFG)
        self.assertEqual(w[:2], [(dt(5, "09:00"), dt(5, "09:50")), (dt(5, "11:10"), dt(5, "17:30"))])

    def test_weekends_skipped(self) -> None:
        days = {s.day for s, _ in pl.free_windows([], NOW, CFG)}
        self.assertEqual(days, {5, 6, 7, 8, 9})  # Mon-Fri; the 10th and 11th are the weekend

    def test_past_is_clipped(self) -> None:
        w = pl.free_windows([], datetime(2026, 10, 5, 13, 7), CFG)
        self.assertEqual(w[0], (datetime(2026, 10, 5, 13, 7), dt(5, "17:30")))

    def test_short_windows_dropped(self) -> None:
        w = pl.free_windows([(dt(5, "09:20"), dt(5, "17:10"))], NOW, CFG)
        self.assertEqual([x for x in w if x[0].day == 5], [])

    def test_busy_from_events_ignores_free_declined_all_day_and_mirror(self) -> None:
        ev = [
            {"start": "2026-10-05T10:00:00", "end": "2026-10-05T11:00:00", "all_day": False, "calendar_id": "p"},
            {"start": "2026-10-05", "end": "2026-10-06", "all_day": True},
            {"start": "2026-10-05T12:00:00", "end": "2026-10-05T13:00:00", "transparency": "transparent"},
            {"start": "2026-10-05T14:00:00", "end": "2026-10-05T15:00:00", "self_response": "declined"},
            {"start": "2026-10-05T15:00:00", "end": "2026-10-05T16:00:00", "attendee_details": [{"self": True, "response": "declined"}]},
            {"start": "2026-10-05T16:00:00", "end": "2026-10-05T17:00:00", "calendar_id": "mirror"},
        ]
        self.assertEqual(pl.busy_from_events(ev, ["mirror"]), [(dt(5, "10:00"), dt(5, "11:00"))])


class PlanTests(unittest.TestCase):
    def test_priority_one_due_tomorrow_lands_early_and_clear_of_meetings(self) -> None:
        out = pl.plan([todo("a", priority=1, due="2026-10-06", estimate_min=90)], MEETING, NOW, CFG)
        (b,) = out["blocks"]
        s, e = span(b)
        self.assertLessEqual(e, datetime(2026, 10, 7))
        self.assertIn(s.day, (5, 6))
        self.assertFalse(overlaps((s, e), MEETING[0], pad=10))
        self.assertEqual(e - s, timedelta(minutes=90))
        self.assertEqual(out["unplaced"], [])

    def test_blocks_never_overlap_each_other_or_busy(self) -> None:
        todos = [todo(str(i), priority=1 + i % 3, due="2026-10-07", estimate_min=45 + 15 * i) for i in range(6)]
        out = pl.plan(todos, MEETING, NOW, CFG)
        spans = [span(b) for b in out["blocks"]]
        self.assertEqual(len(spans), 6)
        for i, a in enumerate(spans):
            self.assertFalse(overlaps(a, MEETING[0], pad=10))
            for c in spans[i + 1:]:
                self.assertFalse(overlaps(a, c, pad=10), (a, c))

    def test_long_todo_splits_into_parts(self) -> None:
        out = pl.plan([todo("L", estimate_min=200, due="2026-10-09")], [], NOW, CFG)
        self.assertEqual([b["part"] for b in out["blocks"]], [[1, 2], [2, 2]])
        self.assertEqual(sorted((span(b)[1] - span(b)[0]).seconds // 60 for b in out["blocks"]), [80, 120])

    def test_no_slot_before_due(self) -> None:
        late = datetime(2026, 10, 5, 16, 30)
        out = pl.plan([todo("x", due="2026-10-05", estimate_min=120)], [], late, CFG)
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["unplaced"], [{"id": "x", "reason": "no_slot_before_due"}])

    def test_deterministic(self) -> None:
        todos = [todo(str(i), priority=1 + i % 3, due="2026-10-08", estimate_min=30 * (i + 1)) for i in range(5)]
        self.assertEqual(pl.plan(todos, MEETING, NOW, CFG), pl.plan(list(reversed(todos)), MEETING, NOW, CFG))

    def test_locked_blocks_are_respected_and_not_replanned(self) -> None:
        locked = [{"todo_id": "a", "title": "Ta", "start": "2026-10-05T09:00", "end": "2026-10-05T10:00", "score": 0, "part": [1, 1]}]
        out = pl.plan([todo("a", estimate_min=60), todo("b", estimate_min=60, priority=1, due="2026-10-05")], [], NOW, CFG, locked)
        self.assertEqual(out["already_planned"], ["a"])
        (b,) = out["blocks"]
        self.assertEqual(b["todo_id"], "b")
        self.assertFalse(overlaps(span(b), (dt(5, "09:00"), dt(5, "10:00")), pad=10))

    def test_done_and_unschedulable_todos_are_left_alone(self) -> None:
        out = pl.plan([todo("d", done=1, due="2026-10-06"), todo("n")], [], NOW, CFG)
        self.assertEqual((out["blocks"], out["unplaced"]), ([], []))

    def test_dueless_estimated_todo_is_placed_best_effort(self) -> None:
        self.assertEqual(len(pl.plan([todo("e", estimate_min=30)], [], NOW, CFG)["blocks"]), 1)

    def test_score_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(pl.WEIGHTS.values()), 1.0)

    def test_config_validation(self) -> None:
        self.assertIsNone(pl.validate_config(CFG))
        self.assertIsNotNone(pl.validate_config({**CFG, "workStart": "18:00"}))
        self.assertIsNotNone(pl.validate_config({**CFG, "workDays": [0]}))
        self.assertIsNotNone(pl.validate_config({**CFG, "maxBlockMin": 5, "minBlockMin": 15}))


class StubGoogle:
    def __init__(self, events: list[dict[str, Any]], verified: bool = True) -> None:
        self.events, self.created, self.verified = events, [], verified

    def calendar_events(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return self.events

    def calendar_ensure(self, summary: str) -> dict[str, Any]:
        return {"id": "grain-cal", "summary": summary, "created": False}

    def calendar_create(self, event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> dict[str, Any]:
        self.created.append((event, calendar_id, send_updates))
        return {"id": f"ev{len(self.created)}", "verified": self.verified, "verification": {"status": "verified" if self.verified else "mismatch"}}


class ModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.stored: dict[str, Any] = {}
        self.google = StubGoogle([{"start": "2026-10-05T10:00:00", "end": "2026-10-05T11:00:00", "all_day": False, "calendar_id": "primary", "summary": "Sync"}])
        self.ctx = ModuleContext(db=Database(self.tmp.name), settings=lambda: {**llm.DEFAULT_SETTINGS, **self.stored},
                                 set_settings=self.stored.update, google=self.google, sid=lambda p: p, wsid=lambda p: p)
        self.mod = PlannerModule(self.ctx, clock=lambda: NOW)
        self.todos = Todos(self.ctx.db)
        self.t1 = self.todos.create("Write spec", due="2026-10-06", priority=1, estimate_min=90)
        self.t2 = self.todos.create("Email Bo", due="2026-10-07", estimate_min=20)
        app = FastAPI()
        app.include_router(self.mod.router())
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_estimate_round_trips_on_the_store(self) -> None:
        self.assertEqual(self.t1["estimate_min"], 90)
        self.assertEqual(self.todos.update(self.t1["id"], {"estimate_min": 45})["estimate_min"], 45)
        self.assertIsNone(self.todos.update(self.t1["id"], {"estimate_min": None})["estimate_min"])

    def test_suggest_returns_blocks_and_writes_nothing(self) -> None:
        r = self.client.post("/planner/suggest", json={})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual({b["todo_id"] for b in body["blocks"]}, {self.t1["id"], self.t2["id"]})
        self.assertEqual(self.google.created, [])
        for b in body["blocks"]:
            s, e = span(b)
            self.assertFalse(overlaps((s, e), MEETING[0], pad=10))
            self.assertTrue(dt(5, "09:00").time() <= s.time() and e.time() <= dt(5, "17:30").time())
        self.assertEqual(self.client.post("/planner/suggest", json={}).json(), body)

    def test_apply_creates_exactly_the_given_blocks(self) -> None:
        blocks = [{"todo_id": "a", "title": "Write spec", "start": "2026-10-05T11:10", "end": "2026-10-05T12:40"},
                  {"todo_id": "b", "title": "Email Bo", "start": "2026-10-05T13:00", "end": "2026-10-05T13:20"}]
        r = self.client.post("/planner/apply", json={"blocks": blocks}).json()
        self.assertEqual([x["ok"] for x in r["results"]], [True, True])
        self.assertEqual(len(self.google.created), 2)
        ev, cal, upd = self.google.created[0]
        self.assertEqual((ev["summary"], ev["start"], ev["end"], cal, upd), ("Focus: Write spec", "2026-10-05T11:10", "2026-10-05T12:40", "grain-cal", "none"))
        self.assertIn("todo a", ev["description"])

    def test_apply_reports_unverified_as_failure(self) -> None:
        self.google.verified = False
        r = self.client.post("/planner/apply", json={"blocks": [{"todo_id": "a", "title": "x", "start": "2026-10-05T11:10", "end": "2026-10-05T12:00"}]}).json()
        self.assertFalse(r["results"][0]["ok"])
        self.assertIn("not verified", r["results"][0]["error"])
        self.assertEqual(self.client.post("/planner/apply", json={"blocks": []}).status_code, 400)
        bad = self.client.post("/planner/apply", json={"blocks": [{"title": "x", "start": "nope", "end": "nope"}]}).json()
        self.assertFalse(bad["results"][0]["ok"])

    def test_accepted_focus_block_is_locked_next_time(self) -> None:
        self.google.events.append({"start": "2026-10-05T11:10:00", "end": "2026-10-05T12:40:00", "all_day": False, "calendar_id": "grain-cal",
                                   "summary": f"Focus: Write spec", "description": f"Planned by Grain from todo {self.t1['id']}"})
        self.stored["googleTodoCalendar"] = {"calendarId": "grain-cal"}
        body = self.client.post("/planner/suggest", json={}).json()
        self.assertEqual(body["already_planned"], [self.t1["id"]])
        for b in body["blocks"]:
            self.assertFalse(overlaps(span(b), (dt(5, "11:10"), dt(5, "12:40")), pad=10))

    def test_not_connected_is_409(self) -> None:
        from personal_os.google import GoogleNotConnected

        def boom(*_a: Any, **_k: Any) -> Any:
            raise GoogleNotConnected("Google is not connected.")
        self.google.calendar_events = boom  # type: ignore[method-assign]
        self.assertEqual(self.client.post("/planner/suggest", json={}).status_code, 409)

    def test_config_round_trip_and_validation(self) -> None:
        self.assertEqual(self.client.get("/planner/config").json(), pl.DEFAULT_CONFIG)
        self.assertEqual(self.client.put("/planner/config", json={"workEnd": "16:00", "bufferMin": 5}).json()["workEnd"], "16:00")
        self.assertEqual(self.client.put("/planner/config", json={"workStart": "17:00"}).status_code, 422)
        self.assertEqual(self.client.put("/planner/config", json={"bufferMin": 999}).status_code, 422)

    def test_settings_default_present(self) -> None:
        self.assertIn("planner", llm.DEFAULT_SETTINGS)
        self.assertEqual(llm.DEFAULT_SETTINGS["planner"], pl.DEFAULT_CONFIG)

    def test_tool_is_read_only_and_proposes(self) -> None:
        class Box:
            specs: dict = {}
        box = Box()
        box.specs = {}
        self.mod.register_tools(box)  # type: ignore[arg-type]
        spec = box.specs["schedule_suggest"]
        self.assertEqual((spec.group, spec.danger), ("google", "safe"))
        out = asyncio.run(spec.fn({}, days=5))
        self.assertTrue(out["proposed_blocks"])
        self.assertEqual(self.google.created, [])
        # each change is what /planner/apply would write, so an approved card is seen as planned next time
        c = out["changes"][0]
        self.assertEqual((c["op"], c["calendar_id"]), ("create", "grain-cal"))
        self.assertTrue(c["summary"].startswith(pl.FOCUS_PREFIX))
        self.assertEqual(pl.locked_from_events([{**c, "all_day": False}], ["grain-cal"])[0]["todo_id"], out["proposed_blocks"][0]["todo_id"])


if __name__ == "__main__":
    unittest.main()
