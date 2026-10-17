"""The Calendar built-in extension: what it registers, its recap section, its routes, and the `calendar` job kind's
registration. Offline: Google is a stub.

Run: python backend/tests/test_calendar_extension.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from calendar_ext import calendar_scope  # noqa: E402
from personal_os import approval_edits  # noqa: E402
from personal_os.extensions.google.client import NOT_CONNECTED_FACT, GoogleNotConnected  # noqa: E402

TOOLS = ["calendar_events", "calendar_get", "meeting_brief", "calendar_create", "calendar_update", "calendar_delete", "calendar_respond",
         "calendar_free_busy", "calendar_find_time", "calendar_propose"]
EVENT = {"id": "e1", "summary": "Standup", "start": "2026-10-05T09:00:00Z"}


class Google:
    """Just the calendar surface the extension reads, with the calls it makes recorded."""

    def __init__(self, connected: bool = True) -> None:
        self.connected, self.calls, self.fail = connected, [], None

    def status(self) -> dict[str, Any]:
        return {"connected": self.connected}

    def enabled_calendar_ids(self) -> list[str]:
        return ["mine", "classes"]

    def calendar_events(self, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append((args, kw))
        if self.fail:
            raise self.fail
        return [EVENT]

    def calendars(self, *_a: Any) -> list[dict[str, Any]]:
        if not self.connected:
            raise GoogleNotConnected("Google is not connected.")
        return [{"id": "primary"}]

    def classify_error(self, e: Exception) -> Exception:
        return e


class RegistrationTests(unittest.TestCase):
    def test_it_offers_the_ten_calendar_tools_a_trigger_kind_and_a_recap_section(self) -> None:
        scope = calendar_scope(Google())
        self.assertEqual(list(scope.tools), TOOLS)
        self.assertEqual(list(scope.triggers), ["calendar"])
        self.assertEqual([s.id for s in scope.recap_sections], ["calendar"])

    def test_stopping_it_takes_the_trigger_kind_and_the_section_back(self) -> None:
        scope = calendar_scope(Google())
        asyncio.run(scope.stop())
        self.assertEqual((list(scope.triggers), scope.recap_sections, scope.tools), ([], [], {}))

    def test_an_edited_calendar_proposal_is_held_to_the_rules_of_the_model_s(self) -> None:
        scope = calendar_scope(Google())
        params = scope.tools["calendar_propose"].parameters
        good = {"changes": [{"op": "create", "summary": "Sync", "start": "2026-10-07T15:00", "end": "2026-10-07T15:30"}]}
        self.assertEqual(approval_edits.validate("calendar_propose", good, params), good)
        with self.assertRaises(approval_edits.EditError):
            approval_edits.validate("calendar_propose", {"changes": [{"op": "delete"}]}, params)


class RecapSectionTests(unittest.TestCase):
    def test_the_card_lists_the_next_two_days_on_the_checked_calendars(self) -> None:
        g = Google()
        card = calendar_scope(g).recap_sections[0].card()
        self.assertEqual(card, {"calendar": [EVENT]})
        self.assertEqual(g.calls, [((2,), {"calendar_ids": ["mine", "classes"]})])

    def test_the_card_is_null_without_google_and_when_the_read_fails(self) -> None:
        self.assertEqual(calendar_scope(Google(connected=False)).recap_sections[0].card(), {"calendar": None})
        g = Google()
        g.fail = RuntimeError("boom")
        self.assertEqual(calendar_scope(g).recap_sections[0].card(), {"calendar": None})

    def test_the_facts_read_three_days_of_every_visible_calendar(self) -> None:
        g = Google()
        self.assertEqual(calendar_scope(g).recap_sections[0].facts(), {"calendar_next_3_days": [EVENT]})
        self.assertEqual(g.calls, [((3,), {"calendar_ids": ["all"]})])

    def test_the_facts_say_when_the_calendar_cannot_be_seen(self) -> None:
        self.assertEqual(calendar_scope(Google(connected=False)).recap_sections[0].facts(), {"calendar_next_3_days": NOT_CONNECTED_FACT})
        g = Google()
        g.fail = RuntimeError("boom")
        self.assertEqual(calendar_scope(g).recap_sections[0].facts(), {"calendar_next_3_days": "unavailable: boom"})


class RouteTests(unittest.TestCase):
    def client(self, g: Google) -> TestClient:
        app = FastAPI()
        for r in calendar_scope(g).routers:
            app.include_router(r)
        return TestClient(app)

    def test_the_google_calendar_routes_are_served(self) -> None:
        c = self.client(Google())
        self.assertEqual(c.get("/integrations/google/calendars").json(), [{"id": "primary"}])
        self.assertEqual(c.get("/integrations/google/calendar?days=3&calendars=a,b").json(), [EVENT])

    def test_a_missing_account_answers_409_and_an_empty_event_400(self) -> None:
        c = self.client(Google(connected=False))
        self.assertEqual(c.get("/integrations/google/calendars").status_code, 409)
        self.assertEqual(c.post("/integrations/google/calendar", content=json.dumps({"summary": " "}), headers={"content-type": "application/json"}).status_code, 400)

    def test_colors_is_not_read_as_an_event_id(self) -> None:
        paths = [r.path for r in calendar_scope(Google()).routers[0].routes]
        self.assertLess(paths.index("/integrations/google/calendar/colors"), paths.index("/integrations/google/calendar/{event_id}"))


if __name__ == "__main__":
    unittest.main()
