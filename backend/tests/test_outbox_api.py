"""The outbox over HTTP: the send endpoint queues instead of sending, and Undo is reachable.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_outbox_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="outboxapitest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_google_api import FakeGoogle, FakeServer  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, verify  # noqa: E402


class OutboxApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.client = TestClient(appmod.app)
        self.headers = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
        self._google = appmod.outbox.google
        appmod.outbox.google = FakeGoogle(self.server)
        self._delays, self._sleep = verify.MAIL_RETRY_DELAYS, verify.SLEEP
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None

    def tearDown(self) -> None:
        appmod.outbox.google = self._google
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = self._delays, self._sleep
        for row in appmod.outbox.list():  # leave the shared dev database as it was found
            appmod.outbox.cancel(row["id"])

    def test_the_send_endpoint_queues_and_undo_stops_it(self) -> None:
        r = self.client.post("/integrations/google/gmail/send", headers=self.headers,
                             json={"to": "mira@example.com", "subject": "Hi", "body": "later"})
        self.assertEqual(r.status_code, 200)
        row = r.json()
        self.assertEqual(row["status"], "holding")
        self.assertGreaterEqual(row["seconds_left"], 60)
        self.assertEqual(self.server.messages, {})

        listed = self.client.get("/outbox/gmail", headers=self.headers).json()
        self.assertIn(row["id"], [s["id"] for s in listed["sends"]])
        self.assertEqual(listed["config"]["seconds"], 90)

        cancelled = self.client.post(f"/outbox/gmail/{row['id']}/cancel", headers=self.headers)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.server.messages, {})

    def test_cancelling_twice_is_a_conflict_not_a_silent_ok(self) -> None:
        row = self.client.post("/integrations/google/gmail/send", headers=self.headers,
                               json={"to": "mira@example.com", "subject": "Hi", "body": "later"}).json()
        self.client.post(f"/outbox/gmail/{row['id']}/cancel", headers=self.headers)
        again = self.client.post(f"/outbox/gmail/{row['id']}/cancel", headers=self.headers)
        self.assertEqual(again.status_code, 409)

    def test_send_now_goes_out_and_is_verified(self) -> None:
        row = self.client.post("/integrations/google/gmail/send", headers=self.headers,
                               json={"to": "mira@example.com", "subject": "Hi", "body": "later"}).json()
        sent = self.client.post(f"/outbox/gmail/{row['id']}/send-now", headers=self.headers).json()
        self.assertEqual(sent["status"], "sent")
        self.assertTrue(sent["verified"])
        self.assertEqual(len(self.server.messages), 1)

    def test_the_outbox_routes_need_the_token(self) -> None:
        self.assertEqual(self.client.get("/outbox/gmail").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
