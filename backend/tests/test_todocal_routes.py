"""todo -> calendar mirror routes against the real app.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_todocal_routes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="todocaltest-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})

URL = "/integrations/google/todo-calendar"


class RouteTests(unittest.TestCase):
    # The app module is process-wide, so every test puts the config back for the next one.
    def setUp(self) -> None:
        self.before = client.get(URL).json()["config"]

    def tearDown(self) -> None:
        client.put(URL, json={k: v for k, v in self.before.items() if k != "calendarId"})

    def test_status_exposes_the_defaults(self) -> None:
        r = client.get(URL)
        self.assertEqual(r.status_code, 200)
        cfg = r.json()["config"]
        # Both Google syncs ship on, so a connected account mirrors without extra setup.
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["calendarName"], "Grain Todos")
        self.assertFalse(cfg["keepCompleted"])

    def test_tasks_sync_also_ships_on(self) -> None:
        r = client.get("/integrations/google/tasks-sync")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["config"]["enabled"])

    def test_config_round_trips(self) -> None:
        r = client.put(URL, json={"keepCompleted": True, "intervalMinutes": 30})
        self.assertEqual(r.status_code, 200)
        cfg = r.json()["config"]
        self.assertTrue(cfg["keepCompleted"])
        self.assertEqual(cfg["intervalMinutes"], 30)
        self.assertEqual(client.get(URL).json()["config"]["keepCompleted"], True)

    def test_unknown_keys_are_ignored(self) -> None:
        r = client.put(URL, json={"nonsense": 1})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("nonsense", r.json()["config"])

    def test_plain_settings_put_cannot_clobber_the_config(self) -> None:
        client.put(URL, json={"keepCompleted": True})
        client.put("/settings", json={"googleTodoCalendar": {}})
        self.assertTrue(client.get(URL).json()["config"]["keepCompleted"])

    def test_run_with_nothing_to_mirror_never_touches_google(self) -> None:
        """No dated todos: the pass short-circuits rather than making a calendar to look at."""
        r = client.post(f"{URL}/run")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["last_result"], {"created": 0, "updated": 0, "removed": 0, "adopted": 0})
        self.assertIsNone(r.json()["last_error"])

    def test_run_with_a_dated_todo_needs_google(self) -> None:
        tid = client.post("/todos", json={"title": "Mirror me", "due": "2026-10-02"}).json()["id"]
        try:
            r = client.post(f"{URL}/run")
            self.assertEqual(r.status_code, 409)
            self.assertIn("Google", client.get(URL).json()["last_error"])
        finally:
            client.delete(f"/todos/{tid}")


if __name__ == "__main__":
    unittest.main()
