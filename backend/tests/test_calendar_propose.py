"""calendar_propose execution, calendar_find_time and calendar_free_busy through the Toolbox.

The Google client is real (FakeGoogle swaps only the transport), so every read-back verifier runs.

Run: python backend/tests/test_calendar_propose.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_google_api import FakeGoogle, FakeServer, _Call  # noqa: E402
from personal_os import tools, verify  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


class _FreeBusy:
    def __init__(self, rows: dict[str, Any], sink: list[Any]):
        self.rows, self.sink = rows, sink

    def query(self, body: dict[str, Any]) -> _Call:
        self.sink.append(body)
        return _Call(lambda: {"calendars": {i["id"]: self.rows.get(i["id"], {"busy": []}) for i in body["items"]}})


class _CalList:
    def list(self, **_: Any) -> _Call:
        return _Call(lambda: {"items": [{"id": "primary", "summary": "Me", "primary": True, "accessRole": "owner"},
                                        {"id": "work@x.com", "summary": "Work", "accessRole": "owner"},
                                        {"id": "hidden@x.com", "summary": "Old", "accessRole": "reader", "hidden": True}]})


class _Svc:
    def __init__(self, inner: Any, rows: dict[str, Any], sink: list[Any]):
        self.inner, self.rows, self.sink = inner, rows, sink

    def events(self) -> Any:
        return self.inner.events()

    def freebusy(self) -> _FreeBusy:
        return _FreeBusy(self.rows, self.sink)

    def calendarList(self) -> _CalList:
        return _CalList()


class Fake(FakeGoogle):
    def __init__(self, server: FakeServer):
        super().__init__(server)
        self.fb_rows: dict[str, Any] = {}
        self.fb_queries: list[Any] = []

    def _svc(self, name: str, version: str) -> Any:  # type: ignore[override]
        return _Svc(self.server.service(name), self.fb_rows, self.fb_queries)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.g = Fake(self.server)
        self._delays, self._sleep = verify.RETRY_DELAYS, verify.SLEEP
        verify.RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None
        self.box = Toolbox(None, None, None, lambda: {}, google=self.g)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        verify.RETRY_DELAYS, verify.SLEEP = self._delays, self._sleep

    def call(self, name: str, **args: Any) -> Any:
        return asyncio.run(self.box.call(name, args, {"project_id": None, "conversation_id": "c1"}))


class ProposeTests(Base):
    def seed(self, summary: str = "Standup") -> str:
        return self.g.calendar_create({"summary": summary, "start": "2026-10-07T09:00", "end": "2026-10-07T09:30"})["id"]

    def test_modes_and_tiers(self) -> None:
        sp = self.box.specs
        self.assertEqual(sp["calendar_propose"].danger, "external")
        self.assertEqual(sp["calendar_propose"].default_mode, "ask")
        for n in ("calendar_find_time", "calendar_free_busy"):
            self.assertEqual(sp[n].default_mode, "on", n)

    def test_a_tainted_run_still_asks_for_propose(self) -> None:
        self.assertEqual(self.box.gate("calendar_propose", "on", {"tainted": True}), "ask")

    def test_proposal_only_runs_cannot_execute_it(self) -> None:
        out = asyncio.run(self.box.call("calendar_propose", {"changes": [{"op": "delete", "event_id": "x"}]}, {"proposal_only": True}))
        self.assertIn("error", out)

    def test_all_changes_succeed_and_are_verified(self) -> None:
        e1, e2 = self.seed("Standup"), self.seed("Old")
        out = self.call("calendar_propose", note="rearrange", changes=[
            {"op": "create", "summary": "Design sync", "start": "2026-10-07T15:00", "end": "2026-10-07T15:30", "attendees": ["mira@example.com"], "location": "Room 2"},
            {"op": "update", "event_id": e1, "start": "2026-10-07T10:00", "end": "2026-10-07T10:30"},
            {"op": "delete", "event_id": e2}])
        self.assertIsNone(out.get("error"), out)
        self.assertTrue(out["ok"])
        self.assertEqual((out["applied"], out["failed"], out["total"]), (3, 0, 3))
        self.assertEqual([r["op"] for r in out["results"]], ["create", "update", "delete"])
        self.assertTrue(all(r["ok"] and r["v"] == "verified" for r in out["results"]))
        self.assertTrue(out["results"][0]["link"].startswith("https://cal.test/"))
        self.assertEqual(out["note"], "rearrange")
        made = self.server.events[("primary", out["results"][0]["id"])]
        self.assertEqual(made["summary"], "Design sync")
        self.assertEqual(made["attendees"], [{"email": "mira@example.com"}])
        self.assertEqual(self.server.events[("primary", e1)]["start"]["dateTime"], "2026-10-07T10:00")
        self.assertNotIn(("primary", e2), self.server.events)

    def test_partial_failure_is_reported_per_change_and_the_rest_still_run(self) -> None:
        e1 = self.seed()
        out = self.call("calendar_propose", changes=[
            {"op": "delete", "event_id": "does-not-exist"},
            {"op": "update", "event_id": e1, "summary": "Standup (moved)"},
            {"op": "create", "summary": "Lunch", "start": "2026-10-08T12:00"}])
        self.assertFalse(out["ok"])
        self.assertEqual((out["applied"], out["failed"]), (2, 1))
        self.assertIn("2 of 3", out["error"])
        self.assertIn("#1 delete", out["error"])
        self.assertIn("do NOT repeat", out["try_instead"])
        bad, good1, good2 = out["results"]
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["err"])
        self.assertTrue(good1["ok"] and good2["ok"])
        self.assertEqual(self.server.events[("primary", e1)]["summary"], "Standup (moved)")
        self.assertEqual(len([k for k in self.server.events if k != ("primary", e1)]), 1)

    def test_an_unverified_change_is_a_failure_not_a_success(self) -> None:
        self.server.hide.add("ev2")  # the first seeded event is ev1; the created one is ev2
        self.seed()
        out = self.call("calendar_propose", changes=[{"op": "create", "summary": "Ghost", "start": "2026-10-08T12:00"}])
        row = out["results"][0]
        self.assertFalse(row["ok"])
        self.assertEqual(row["v"], "unverified")
        self.assertIn("error", out)
        self.assertEqual(row["id"], "ev2")  # the id survives so the user can go and check

    def test_a_mismatched_change_is_flagged(self) -> None:
        self.server.corrupt["ev1"] = {"summary": "Something else"}
        out = self.call("calendar_propose", changes=[{"op": "create", "summary": "Real", "start": "2026-10-08T12:00"}])
        self.assertEqual(out["results"][0]["v"], "mismatch")
        self.assertFalse(out["ok"])

    def test_edited_arguments_are_what_runs(self) -> None:
        """The server executes exactly the changes it is handed: the human's edit, not the model's draft."""
        model = [{"op": "create", "summary": "Sync", "start": "2026-10-07T15:00", "end": "2026-10-07T15:30", "attendees": ["a@x.com", "b@x.com"]}]
        edited = [{"op": "create", "summary": "Sync (Mira only)", "start": "2026-10-07T16:00", "end": "2026-10-07T16:45", "attendees": ["a@x.com"]}]
        self.assertNotEqual(model, edited)
        out = self.call("calendar_propose", changes=edited)
        ev = self.server.events[("primary", out["results"][0]["id"])]
        self.assertEqual(ev["summary"], "Sync (Mira only)")
        self.assertEqual(ev["start"]["dateTime"], "2026-10-07T16:00")
        self.assertEqual(ev["attendees"], [{"email": "a@x.com"}])
        self.assertEqual(len(self.server.events), 1)

    def test_a_bad_edit_is_rejected_before_anything_is_written(self) -> None:
        out = self.call("calendar_propose", changes=[
            {"op": "create", "summary": "Fine", "start": "2026-10-07T15:00"},
            {"op": "create", "summary": "Backwards", "start": "2026-10-07T16:00", "end": "2026-10-07T15:00"}])
        self.assertIn("changes[1]", out["error"])
        self.assertEqual(self.server.events, {})

    def test_empty_and_oversized_batches_are_rejected(self) -> None:
        self.assertIn("error", self.call("calendar_propose", changes=[]))
        too_many = [{"op": "delete", "event_id": f"e{i}"} for i in range(30)]
        self.assertIn("error", self.call("calendar_propose", changes=too_many))

    def test_conference_flag_asks_for_a_meet_link(self) -> None:
        seen: list[Any] = []
        orig = self.g.calendar_create

        def spy(event: dict[str, Any], *a: Any, **k: Any) -> Any:
            seen.append(event)
            return orig(event, *a, **k)
        self.g.calendar_create = spy  # type: ignore[method-assign]
        self.call("calendar_propose", changes=[{"op": "create", "summary": "Call", "start": "2026-10-07T15:00", "conference": True}])
        self.assertTrue(seen[0].get("create_meet"))

    def test_send_updates_and_calendar_are_passed_through(self) -> None:
        seen: list[Any] = []
        orig = self.g.calendar_create

        def spy(event: dict[str, Any], calendar_id: str = "primary", send_updates: str = "none") -> Any:
            seen.append((calendar_id, send_updates))
            return orig(event, calendar_id, send_updates)
        self.g.calendar_create = spy  # type: ignore[method-assign]
        self.call("calendar_propose", changes=[{"op": "create", "summary": "C", "start": "2026-10-07T15:00", "calendar_id": "work@x.com", "send_updates": "all"}])
        self.assertEqual(seen, [("work@x.com", "all")])

    def test_results_stay_inside_the_preview_budget_for_a_typical_batch(self) -> None:
        ids = [self.seed(f"E{i}") for i in range(4)]
        out = self.call("calendar_propose", changes=[{"op": "update", "event_id": i, "start": "2026-10-09T10:00"} for i in ids])
        self.assertNotIn("truncated", tools.summarize_result(out))


class FindTimeTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.events: list[dict[str, Any]] = []
        self.fb: dict[str, Any] = {}
        self.fb_calls: list[Any] = []
        self.g.calendar_events = lambda days, cal, mx, start, ids: list(self.events)  # type: ignore[method-assign]

        def fb(tmin: str, tmax: str, cals: Any = None, att: Any = None) -> Any:
            self.fb_calls.append((cals, att))
            return {"calendars": {a: {"busy": self.fb.get(a, []), "attendee": True, **({"errors": ["notFound"]} if a == "ghost@x.com" else {})} for a in att or []},
                    "unreachable": [a for a in att or [] if a == "ghost@x.com"]}
        self.g.calendar_free_busy = fb  # type: ignore[method-assign]

    def _window(self) -> tuple[str, str]:
        d = dt.date.today() + dt.timedelta(days=7)
        while d.weekday() != 2:  # a Wednesday next week
            d += dt.timedelta(days=1)
        return d.isoformat(), d.isoformat()

    def test_slots_avoid_the_users_events(self) -> None:
        day, _ = self._window()
        self.events = [{"id": "a", "summary": "All morning", "start": f"{day}T09:00:00", "end": f"{day}T13:00:00", "all_day": False}]
        out = self.call("calendar_find_time", duration_minutes=30, window_start=day, window_end=day, timezone="UTC", max_results=3)
        self.assertGreaterEqual(out["count"], 1)
        for s in out["slots"]:
            self.assertGreaterEqual(s["start"][11:16], "13:00")

    def test_attendee_busy_time_is_respected_and_unreachable_ones_are_called_out(self) -> None:
        day, _ = self._window()
        self.fb = {"mira@x.com": [{"start": f"{day}T13:00:00Z", "end": f"{day}T18:00:00Z"}]}
        out = self.call("calendar_find_time", duration_minutes=60, window_start=day, window_end=day, timezone="UTC",
                        attendees=["mira@x.com", "ghost@x.com"], max_results=20)
        self.assertTrue(out["slots"])
        for s in out["slots"]:
            self.assertLessEqual(s["end"][11:16], "13:00")
        self.assertEqual(out["unreachable"], ["ghost@x.com"])
        self.assertIn("NOT reflected", out["note"])

    def test_no_room_says_so(self) -> None:
        day, _ = self._window()
        self.events = [{"id": "a", "start": f"{day}T00:00:00", "end": f"{day}T23:59:00", "all_day": False}]
        out = self.call("calendar_find_time", duration_minutes=30, window_start=day, window_end=day, timezone="UTC")
        self.assertEqual(out["slots"], [])
        self.assertIn("No free slot", out["note"])

    def test_bad_input_is_a_tool_error_with_an_example(self) -> None:
        out = self.call("calendar_find_time", duration_minutes=30, window_start="soon", window_end="later")
        self.assertIn("error", out)
        self.assertIn("example", out)
        self.assertIn("error", self.call("calendar_find_time", duration_minutes=0, window_start="2030-01-01", window_end="2030-01-02"))
        self.assertIn("error", self.call("calendar_find_time", duration_minutes=30, window_start="2030-01-01", window_end="2030-01-02", working_hours="18-9"))

    def test_a_window_in_the_past_is_an_error(self) -> None:
        out = self.call("calendar_find_time", duration_minutes=30, window_start="2020-01-01", window_end="2020-01-02")
        self.assertIn("already over", out["error"])


class FreeBusyTests(Base):
    def test_defaults_to_visible_calendars_and_adds_attendees(self) -> None:
        self.g.fb_rows = {"mira@x.com": {"busy": [{"start": "2026-10-07T14:00:00Z", "end": "2026-10-07T15:00:00Z"}]},
                          "stranger@x.com": {"busy": [], "errors": [{"reason": "notFound"}]}}
        out = self.call("calendar_free_busy", time_min="2026-10-07T00:00:00Z", time_max="2026-10-08T00:00:00Z", attendees=["mira@x.com", "stranger@x.com"])
        ids = [i["id"] for i in self.g.fb_queries[-1]["items"]]
        self.assertEqual(ids, ["primary", "work@x.com", "mira@x.com", "stranger@x.com"])  # the hidden one is left out
        self.assertEqual(out["calendars"]["mira@x.com"]["busy"][0]["start"], "2026-10-07T14:00:00Z")
        self.assertTrue(out["calendars"]["mira@x.com"]["attendee"])
        self.assertFalse(out["calendars"]["primary"]["attendee"])
        self.assertEqual(out["unreachable"], ["stranger@x.com"])

    def test_explicit_calendars_are_used_as_given(self) -> None:
        self.call("calendar_free_busy", time_min="2026-10-07T00:00:00Z", time_max="2026-10-08T00:00:00Z", calendars=["work@x.com"])
        self.assertEqual([i["id"] for i in self.g.fb_queries[-1]["items"]], ["work@x.com"])


if __name__ == "__main__":
    unittest.main()
