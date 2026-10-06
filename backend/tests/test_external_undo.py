"""Undo for the agent's Google Calendar and Google Tasks writes (extundo.py), over the fake Google client.

Run: python backend/tests/test_external_undo.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from fake_google_api import FakeGoogle, FakeServer  # noqa: E402
from personal_os import extundo, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

CTX = {"project_id": None, "conversation_id": "c1", "message_id": "m1", "run_id": "r1"}


class ExternalUndoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.g = FakeGoogle(self.server)
        self._delays, self._sleep = verify.RETRY_DELAYS, verify.SLEEP
        verify.RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None
        self.db = Database(tempfile.mkdtemp(prefix="extundo-"))
        self.ex = extundo.ExternalUndo(self.db, self.g)
        self.box = Toolbox(None, None, None, lambda: {}, google=self.g, extundo=self.ex)  # type: ignore[arg-type]
        app = FastAPI()
        app.include_router(extundo.router(self.ex))
        self.http = TestClient(app)

    def tearDown(self) -> None:
        verify.RETRY_DELAYS, verify.SLEEP = self._delays, self._sleep

    def _call(self, name: str, **args: Any) -> Any:
        out = asyncio.run(self.box.call(name, args, dict(CTX)))
        self.assertFalse(isinstance(out, dict) and out.get("error"), out)
        return out

    def _undo(self, out: dict[str, Any]) -> Any:
        return self.http.post(f"/external-undo/{out['undo']['external_id']}")

    def _event(self, eid: str) -> dict[str, Any] | None:
        return self.server.events.get(("primary", eid))

    def test_create_then_undo_deletes_the_event(self) -> None:
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertIn("external_id", out["undo"])
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(verify.ok(r.json()["result"]["verification"]))
        self.assertIsNone(self._event(out["id"]))
        with self.db.tx() as c:
            row = dict(c.execute("SELECT * FROM external_undo").fetchone())
        self.assertEqual((row["status"], row["run_id"], row["message_id"], row["kind"]), ("undone", "r1", "m1", "delete_created"))

    def test_update_then_undo_restores_summary_and_time(self) -> None:
        ev = self.g.calendar_create({"summary": "Dentist", "start": "2026-10-07T15:00:00-04:00", "end": "2026-10-07T16:00:00-04:00"})
        out = self._call("calendar_update", event_id=ev["id"], summary="Dentist (moved)",
                         start="2026-10-08T09:00:00-04:00", end="2026-10-08T10:00:00-04:00")
        self.assertEqual(self._event(ev["id"])["summary"], "Dentist (moved)")
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        e = self._event(ev["id"])
        self.assertEqual(e["summary"], "Dentist")
        self.assertEqual(e["start"]["dateTime"], "2026-10-07T15:00:00-04:00")
        self.assertEqual(e["end"]["dateTime"], "2026-10-07T16:00:00-04:00")
        self.assertTrue(verify.ok(r.json()["result"]["verification"]))

    def test_delete_then_undo_recreates_with_the_same_fields(self) -> None:
        ev = self.g.calendar_create({"summary": "Standup", "location": "Room 2", "start": "2026-10-07T09:30:00-04:00",
                                     "end": "2026-10-07T09:45:00-04:00"})
        out = self._call("calendar_delete", event_id=ev["id"])
        self.assertIsNone(self._event(ev["id"]))
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        back = [e for e in self.server.events.values() if e.get("summary") == "Standup"]
        self.assertEqual(len(back), 1)
        self.assertEqual((back[0]["location"], back[0]["start"]["dateTime"], back[0]["status"]),
                         ("Room 2", "2026-10-07T09:30:00-04:00", "confirmed"))

    def test_delete_undo_reuses_the_cancelled_event_id(self) -> None:
        self.server.tombstone = True
        ev = self.g.calendar_create({"summary": "1:1", "start": "2026-10-07T11:00:00-04:00"})
        out = self._call("calendar_delete", event_id=ev["id"])
        self.assertEqual(self._event(ev["id"])["status"], "cancelled")
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._event(ev["id"])["status"], "confirmed")
        self.assertEqual(len(self.server.events), 1, "no second copy was inserted")

    def test_undo_after_a_later_edit_is_a_conflict(self) -> None:
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.g.calendar_update(out["id"], {"summary": "Dentist, edited by hand"})
        r = self._undo(out)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["detail"]["conflict"])
        self.assertIsNotNone(self._event(out["id"]), "the event the user edited stays")

    def test_a_second_undo_is_refused(self) -> None:
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        self.assertEqual(self._undo(out).status_code, 200)
        r = self._undo(out)
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.json()["detail"]["conflict"])

    def test_expired_undo_is_refused(self) -> None:
        out = self._call("calendar_create", summary="Dentist", start="2026-10-07T15:00")
        with self.db.tx() as c:
            c.execute("UPDATE external_undo SET created_at = created_at - ?", (extundo.UNDO_TTL + 1,))
        self.assertEqual(self._undo(out).status_code, 409)
        self.assertIsNotNone(self._event(out["id"]))

    def test_tasks_add_then_undo_deletes_the_task(self) -> None:
        out = self._call("google_tasks_add", title="File the tax return")
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn(("@default", out["id"]), self.server.tasks)

    def test_tasks_complete_then_undo_reopens(self) -> None:
        t = self.g.tasks_add("Call the landlord")
        out = self._call("google_tasks_complete", task_id=t["id"])
        self.assertEqual(self.server.tasks[("@default", t["id"])]["status"], "completed")
        r = self._undo(out)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.server.tasks[("@default", t["id"])]["status"], "needsAction")

    def test_rsvp_then_undo_restores_the_earlier_response(self) -> None:
        ev = self.g.calendar_create({"summary": "Review", "start": "2026-10-07T11:00:00-04:00",
                                     "attendees": [{"email": "me@x.test"}, {"email": "boss@x.test"}]})
        self.server.events[("primary", ev["id"])]["attendees"][0].update({"self": True, "responseStatus": "needsAction"})
        out = self._call("calendar_respond", event_id=ev["id"], response="declined")
        self.assertTrue(out["undo"].get("notifies"))
        self.assertEqual(self._undo(out).status_code, 200)
        me = next(a for a in self._event(ev["id"])["attendees"] if a.get("self"))
        self.assertEqual(me["responseStatus"], "needsAction")

    def test_an_unverified_write_gets_no_undo(self) -> None:
        self.server.hide.add("ev1")
        out = asyncio.run(self.box.call("calendar_create", {"summary": "Dentist", "start": "2026-10-07T15:00"}, dict(CTX)))
        self.assertNotIn("undo", out if isinstance(out, dict) else {})
        with self.db.tx() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM external_undo").fetchone()[0], 0)

    def test_no_tool_can_undo(self) -> None:
        for name, spec in self.box.specs.items():
            self.assertNotIn("undo", name)
            self.assertNotIn("external-undo", spec.description)


if __name__ == "__main__":
    unittest.main()
