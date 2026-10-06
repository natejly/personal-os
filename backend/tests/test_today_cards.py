"""Today payload: waiting-mail counts, the unconfirmed day plan, urgency order, one copy of a synced task.

Offline: Google is stubbed on the app's own instance.
Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_today_cards.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="todaycards-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import todo_rules  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, google, modules  # noqa: E402
from personal_os.modules import get  # noqa: E402
from personal_os.modules.mailwatch import MailWatchModule  # noqa: E402
from personal_os.modules.planner import PlannerModule  # noqa: E402
from personal_os.modules.todos import TodosModule  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
NOW = datetime(2026, 10, 5, 8, 0)  # a Monday
todos_mod = get(modules, "todos", TodosModule)
planner_mod = get(modules, "planner", PlannerModule)
mail_mod = get(modules, "mailwatch", MailWatchModule)


def dashboard(tasks: list[dict] | None = None, saved: list | None = None, calendars: list | None = None,
              calendar_ids_out: list | None = None) -> dict:
    status = {"connected": True, "configured": True, "scopes": [], "missing_scopes": []}

    def events(*_a, **kw):  # type: ignore[no-untyped-def]
        if calendar_ids_out is not None:
            calendar_ids_out.append(kw.get("calendar_ids"))
        return []

    with mock.patch.object(google, "status", return_value=status), \
         mock.patch.object(google, "calendars", return_value=calendars or []), \
         mock.patch.object(google, "calendar_events", side_effect=events), \
         mock.patch.object(google, "gmail_search", return_value=[]), \
         mock.patch.object(google, "tasks_list", return_value=tasks or []), \
         mock.patch.object(google, "calendar_saved", return_value=saved), \
         mock.patch.object(planner_mod, "clock", lambda: NOW), \
         mock.patch.object(mail_mod.store, "counts", return_value={"to_reply": 3, "awaiting_reply_overdue": 2}):
        return client.get("/dashboard").json()


class TodayCardTests(unittest.TestCase):
    def test_mail_counts_and_plan_blocks_ride_the_dashboard(self) -> None:
        a = todos_mod.store.create("Write the spec", due="2026-10-06", priority=1, estimate_min=60)
        b = todos_mod.store.create("Email the landlord", due="2026-10-07", estimate_min=30)
        d = dashboard(saved=[])
        self.assertEqual(d["mail_watch"], {"to_reply": 3, "awaiting_reply_overdue": 2})
        self.assertEqual({x["title"] for x in d["planner_blocks"]}, {"Write the spec", "Email the landlord"})
        for t in (a, b):
            todos_mod.store.trash(t["id"])

    def test_no_snapshot_means_no_blocks(self) -> None:
        t = todos_mod.store.create("Needs a slot", due="2026-10-06", estimate_min=30)
        self.assertEqual(dashboard(saved=None)["planner_blocks"], [])
        todos_mod.store.trash(t["id"])

    def test_plan_is_unconfirmed_until_apply_and_apply_needs_selected_blocks(self) -> None:
        t = todos_mod.store.create("Plan me", due="2026-10-06", estimate_min=30)
        created: list = []

        def create(ev: dict, calendar_id: str = "primary", send_updates: str = "none") -> dict:
            created.append((ev, send_updates))
            return {"id": "e1", "verified": True}

        with mock.patch.object(google, "calendar_create", side_effect=create), \
             mock.patch.object(google, "calendar_ensure", return_value={"id": "cal"}):
            blocks = dashboard(saved=[])["planner_blocks"]
            self.assertTrue(blocks)
            self.assertEqual(created, [])  # opening Today wrote nothing
            self.assertEqual(client.post("/planner/apply", json={"blocks": []}).status_code, 400)
            self.assertEqual(created, [])  # nothing ticked, nothing created
            r = client.post("/planner/apply", json={"blocks": blocks[:1]})
            self.assertTrue(r.json()["results"][0]["ok"])
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0][1], "none")
        todos_mod.store.trash(t["id"])

    def test_todos_come_back_in_urgency_order(self) -> None:
        today = date.today()
        # due order says near-low first; urgency says the high-priority one outranks it
        near_low = todos_mod.store.create("Near low", due=(today + timedelta(days=1)).isoformat(), priority=3)
        far_high = todos_mod.store.create("Far high", due=(today + timedelta(days=3)).isoformat(), priority=1)
        self.assertGreater(todo_rules.urgency(far_high, today), todo_rules.urgency(near_low, today))
        rows = [r["id"] for r in todos_mod.today()["todos"] if r["id"] in (near_low["id"], far_high["id"])]
        self.assertEqual(rows, [far_high["id"], near_low["id"]])
        for t in (near_low, far_high):
            todos_mod.store.trash(t["id"])

    def test_home_calendar_uses_checked_calendars(self) -> None:
        got: list = []
        cals = [
            {"id": "mine", "primary": True, "selected": True, "hidden": False},
            {"id": "classes", "primary": False, "selected": True, "hidden": False},
            {"id": "unchecked", "primary": False, "selected": False, "hidden": False},
            {"id": "secret", "primary": False, "selected": True, "hidden": True},
        ]
        dashboard(calendars=cals, calendar_ids_out=got)
        self.assertEqual(got, [["mine", "classes"]])

    def test_synced_task_shows_once(self) -> None:
        linked = todos_mod.store.create("Synced thing", external_id="gt-1", source="google_tasks")
        tasks = [{"id": "gt-1", "title": "Synced thing", "due": None}, {"id": "gt-2", "title": "Remote only", "due": None}]
        self.assertEqual([t["id"] for t in dashboard(tasks=tasks)["tasks"]], ["gt-2"])
        todos_mod.store.trash(linked["id"])


if __name__ == "__main__":
    unittest.main()
