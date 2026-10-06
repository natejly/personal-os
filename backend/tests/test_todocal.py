"""todos -> Google Calendar mirror against a real (temp) todos DB and an in-memory fake Google.

Run: python backend/tests/test_todocal.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.todocal import TodoCalendarMirror  # noqa: E402
from personal_os.todos import Todos  # noqa: E402


class _FakeGoogle:
    """Just the calendar methods the mirror uses, over a dict."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.calendars_made: list[str] = []
        self.n = 0
        self.ensure_calls = 0

    def calendar_ensure(self, summary: str) -> dict[str, Any]:
        self.ensure_calls += 1
        self.calendars_made.append(summary)
        return {"id": "cal-todos", "summary": summary, "created": True}

    def _out(self, e: dict[str, Any]) -> dict[str, Any]:
        start = e["start"]
        return {**e, "all_day": len(start) == 10, "link": f"https://cal/{e['id']}", "status": "confirmed"}

    def calendar_create(self, event: dict[str, Any], calendar_id: str = "primary") -> dict[str, Any]:
        self.n += 1
        eid = event.get("id") or f"e{self.n}"
        if eid in self.events and self.events[eid]["calendar_id"] == calendar_id:  # ids are unique per calendar
            raise RuntimeError("409 The requested identifier already exists.")
        self.events[eid] = {"id": eid, "calendar_id": calendar_id, "summary": event.get("summary", ""),
                            "description": event.get("description", ""), "start": str(event["start"]),
                            "transparency": event.get("transparency", "opaque")}
        return self._out(self.events[eid])

    def calendar_get(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        if event_id not in self.events:
            raise RuntimeError("404 event not found")
        return self._out(self.events[event_id])

    def calendar_update(self, event_id: str, event: dict[str, Any], calendar_id: str = "primary") -> dict[str, Any]:
        if event_id not in self.events:
            raise RuntimeError("404 event not found")
        row = self.events[event_id]
        for k in ("summary", "description", "start", "transparency"):
            if event.get(k) is not None:
                row[k] = str(event[k]) if k == "start" else event[k]
        return self._out(row)

    def calendar_delete(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        if event_id not in self.events:
            raise RuntimeError("404 event not found")
        del self.events[event_id]
        return {"deleted": event_id}

    # Stands in for an event the user made by hand (per-todo button, drag onto the grid).
    def hand_made(self, start: str, summary: str = "By hand", calendar_id: str = "primary") -> str:
        self.n += 1
        eid = f"h{self.n}"
        self.events[eid] = {"id": eid, "calendar_id": calendar_id, "summary": summary,
                            "description": "", "start": start, "transparency": "opaque"}
        return eid


class MirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.todos = Todos(Database(self.tmp.name))
        self.g = _FakeGoogle()
        self.stored: dict[str, Any] = {"googleTodoCalendar": {"enabled": True}}
        self.mirror = TodoCalendarMirror(
            self.todos, self.g,  # type: ignore[arg-type]
            lambda: self.stored,
            lambda p: self.stored.update(p),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _event_of(self, todo_id: str) -> dict[str, Any]:
        td = self.todos.get(todo_id) or {}
        return self.g.events[td["calendar_event_id"]]

    # ---- creating ----
    def test_an_insert_whose_link_was_lost_is_adopted_not_duplicated(self) -> None:
        td = self.todos.create("Renew visa", due="2026-11-01")
        real = self.todos.set_calendar_state
        self.todos.set_calendar_state = lambda *a, **k: None  # type: ignore[method-assign]  # the write-back is lost
        self.mirror.sync_once()
        self.todos.set_calendar_state = real  # type: ignore[method-assign]
        self.assertEqual(len(self.g.events), 1)
        self.mirror.sync_once()
        self.assertEqual(len(self.g.events), 1)  # the 409 on the same id links it instead
        self.assertEqual((self.todos.get(td["id"]) or {})["calendar_event_id"], next(iter(self.g.events)))

    def test_due_todo_becomes_an_all_day_event(self) -> None:
        td = self.todos.create("Buy milk", due="2026-10-02", notes="2%")
        counts = self.mirror.sync_once()
        self.assertEqual(counts["created"], 1)
        ev = self._event_of(td["id"])
        self.assertEqual((ev["summary"], ev["start"], ev["description"]), ("Buy milk", "2026-10-02", "2%"))
        # Free, not busy: a due-date marker should not block the day.
        self.assertEqual(ev["transparency"], "transparent")
        row = self.todos.get(td["id"]) or {}
        self.assertEqual(row["calendar_id"], "cal-todos")
        self.assertEqual(row["calendar_link"], f"https://cal/{ev['id']}")

    def test_todo_without_a_due_date_is_not_mirrored(self) -> None:
        self.todos.create("Someday")
        self.assertEqual(self.mirror.sync_once()["created"], 0)
        self.assertEqual(self.g.events, {})

    def test_nothing_to_mirror_does_not_create_a_calendar(self) -> None:
        self.todos.create("Someday")
        self.mirror.sync_once()
        self.assertEqual(self.g.ensure_calls, 0)

    def test_calendar_id_is_remembered_after_the_first_pass(self) -> None:
        self.todos.create("Dated", due="2026-10-02")
        self.mirror.sync_once()
        self.assertEqual(self.stored["googleTodoCalendar"]["calendarId"], "cal-todos")
        self.todos.create("Another", due="2026-10-03")
        self.mirror.sync_once()
        self.assertEqual(self.g.ensure_calls, 1)  # not re-created every pass

    def test_idempotent_when_nothing_changed(self) -> None:
        self.todos.create("Dated", due="2026-10-02")
        self.mirror.sync_once()
        self.assertEqual(self.mirror.sync_once(), {"created": 0, "updated": 0, "removed": 0, "adopted": 0})

    # ---- updating ----
    def test_title_and_notes_edits_are_pushed(self) -> None:
        td = self.todos.create("Draft plan", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"title": "Draft the plan", "notes": "with numbers"})
        self.assertEqual(self.mirror.sync_once()["updated"], 1)
        ev = self._event_of(td["id"])
        self.assertEqual((ev["summary"], ev["description"]), ("Draft the plan", "with numbers"))

    def test_moving_the_due_date_moves_the_event(self) -> None:
        td = self.todos.create("Ship it", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"due": "2026-10-09"})
        self.assertEqual(self.mirror.sync_once()["updated"], 1)
        self.assertEqual(self._event_of(td["id"])["start"], "2026-10-09")

    # ---- removing ----
    def test_completing_a_todo_removes_its_event(self) -> None:
        td = self.todos.create("Pay rent", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"done": True})
        self.assertEqual(self.mirror.sync_once()["removed"], 1)
        self.assertEqual(self.g.events, {})
        row = self.todos.get(td["id"]) or {}
        self.assertIsNone(row["calendar_event_id"])
        self.assertIsNone(row["calendar_sig"])

    def test_keep_completed_leaves_the_event(self) -> None:
        self.stored["googleTodoCalendar"]["keepCompleted"] = True
        td = self.todos.create("Pay rent", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"done": True})
        self.assertEqual(self.mirror.sync_once()["removed"], 0)
        self.assertEqual(len(self.g.events), 1)

    def test_clearing_the_due_date_removes_the_event(self) -> None:
        td = self.todos.create("Undated soon", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"due": None})
        self.assertEqual(self.mirror.sync_once()["removed"], 1)
        self.assertEqual(self.g.events, {})

    def test_deleting_a_todo_removes_the_event(self) -> None:
        td = self.todos.create("Old", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.delete(td["id"])
        self.assertEqual(self.mirror.sync_once()["removed"], 1)
        self.assertEqual(self.g.events, {})
        self.assertEqual(self.todos.event_tombstones(), [])

    def test_reopening_a_todo_puts_the_event_back(self) -> None:
        td = self.todos.create("Toggle", due="2026-10-02")
        self.mirror.sync_once()
        self.todos.update(td["id"], {"done": True})
        self.mirror.sync_once()
        self.todos.update(td["id"], {"done": False})
        self.assertEqual(self.mirror.sync_once()["created"], 1)
        self.assertEqual(self._event_of(td["id"])["start"], "2026-10-02")

    # ---- events made by hand ----
    def test_a_hand_made_event_is_adopted_not_flattened(self) -> None:
        td = self.todos.create("Standup", due="2026-10-02")
        eid = self.g.hand_made("2026-10-02T14:00:00", "Standup")
        self.todos.update(td["id"], {"calendar_event_id": eid, "calendar_link": "https://cal/hand"})
        counts = self.mirror.sync_once()
        self.assertEqual((counts["adopted"], counts["created"]), (1, 0))
        # The time the user picked survives, and the mirror now knows about it.
        self.assertEqual(self.g.events[eid]["start"], "2026-10-02T14:00:00")
        sig = json.loads((self.todos.get(td["id"]) or {})["calendar_sig"])
        self.assertEqual(sig["time"], "14:00:00")

    def test_moving_a_timed_todo_keeps_its_time_of_day(self) -> None:
        td = self.todos.create("Standup", due="2026-10-02")
        eid = self.g.hand_made("2026-10-02T14:00:00", "Standup")
        self.todos.update(td["id"], {"calendar_event_id": eid})
        self.mirror.sync_once()  # adopt
        self.todos.update(td["id"], {"due": "2026-10-05"})
        self.assertEqual(self.mirror.sync_once()["updated"], 1)
        self.assertEqual(self.g.events[eid]["start"], "2026-10-05T14:00:00")

    def test_an_adopted_event_stays_on_its_own_calendar(self) -> None:
        td = self.todos.create("On primary", due="2026-10-02")
        eid = self.g.hand_made("2026-10-02", "On primary", calendar_id="primary")
        self.todos.update(td["id"], {"calendar_event_id": eid})
        self.mirror.sync_once()
        self.assertEqual((self.todos.get(td["id"]) or {})["calendar_id"], "primary")
        self.todos.update(td["id"], {"title": "Renamed"})
        self.mirror.sync_once()
        self.assertEqual(self.g.events[eid]["summary"], "Renamed")
        self.assertEqual(self.g.events[eid]["calendar_id"], "primary")

    def test_an_adopted_event_that_is_gone_is_recreated(self) -> None:
        td = self.todos.create("Vanished", due="2026-10-02")
        self.todos.update(td["id"], {"calendar_event_id": "nope"})
        counts = self.mirror.sync_once()
        self.assertEqual((counts["created"], counts["adopted"]), (1, 0))
        self.assertEqual(self._event_of(td["id"])["start"], "2026-10-02")

    # ---- recovery ----
    def test_an_event_deleted_in_google_comes_back(self) -> None:
        td = self.todos.create("Persistent", due="2026-10-02")
        self.mirror.sync_once()
        self.g.events.clear()
        self.todos.update(td["id"], {"title": "Persistent!"})
        self.assertEqual(self.mirror.sync_once()["created"], 1)
        self.assertEqual(self._event_of(td["id"])["summary"], "Persistent!")

    def test_deleting_an_already_gone_event_is_not_an_error(self) -> None:
        td = self.todos.create("Ghost", due="2026-10-02")
        self.mirror.sync_once()
        self.g.events.clear()
        self.todos.update(td["id"], {"done": True})
        self.assertEqual(self.mirror.sync_once()["removed"], 1)

    def test_changing_target_calendar_remirrors(self) -> None:
        td = self.todos.create("Moving", due="2026-10-02")
        self.mirror.sync_once()
        first = (self.todos.get(td["id"]) or {})["calendar_event_id"]
        self.mirror.set_config({"calendarId": "primary"})
        self.assertEqual(self.mirror.sync_once()["created"], 1)
        row = self.todos.get(td["id"]) or {}
        self.assertEqual(row["calendar_id"], "primary")
        self.assertEqual(self.g.events[row["calendar_event_id"]]["calendar_id"], "primary")
        self.assertEqual(row["calendar_event_id"], first)  # derived from the todo; unique per calendar only

    def test_untitled_todo_still_gets_a_summary(self) -> None:
        td = self.todos.create("x", due="2026-10-02")
        self.todos.update(td["id"], {"title": ""})
        self.mirror.sync_once()
        self.assertEqual(self._event_of(td["id"])["summary"], "(untitled)")


if __name__ == "__main__":
    unittest.main()
