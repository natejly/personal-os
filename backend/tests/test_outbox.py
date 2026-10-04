"""Delayed Gmail send: the hold, the undo, and what a restart does with a send left waiting.

Time is a parameter here (`clock`), so the 90-second hold is exercised without waiting for it.

Run: python backend/tests/test_outbox.py
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

from fake_google_api import FakeGoogle, FakeServer  # noqa: E402
from personal_os import outbox as outbox_mod, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.google import NotSent  # noqa: E402
from personal_os.outbox import Outbox  # noqa: E402


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class OutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.server = FakeServer()
        self.google = FakeGoogle(self.server)
        self.clock = Clock()
        self.cfg: dict[str, Any] = {"gmailSendHold": {"enabled": True, "seconds": 30}}
        self._delays, self._sleep = verify.MAIL_RETRY_DELAYS, verify.SLEEP
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None
        self.box = self._outbox()

    def tearDown(self) -> None:
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = self._delays, self._sleep
        self.tmp.cleanup()

    def _outbox(self, db: Database | None = None) -> Outbox:
        # bounds are relaxed so a test hold can be seconds rather than a minute and a half.
        return Outbox(db or self.db, self.google, lambda: self.cfg, bounds=(0, 120), clock=self.clock)

    def _restart(self) -> Outbox:
        """A fresh backend over the same database file, resume() and all."""
        box = self._outbox(Database(self.tmp.name))
        box.resume()
        return box

    def _queue(self, subject: str = "Running late") -> dict[str, Any]:
        return self.box.queue("mira@example.com", subject, "I will be 10 minutes late.")

    # ---- the hold ----
    def test_a_send_is_held_not_sent(self) -> None:
        row = self._queue()
        self.assertEqual(row["status"], "holding")
        self.assertEqual(row["seconds_left"], 30)
        self.assertEqual(self.server.messages, {})

    def test_a_bad_recipient_is_refused_before_the_hold(self) -> None:
        for to in ("bob", "a@b.co, nope", ""):
            with self.assertRaises(ValueError):
                self.box.queue(to, "Hi", "body")
        with self.assertRaises(ValueError):
            self.box.queue("mira@example.com", "Hi\nBcc: x@y.co", "body")
        self.assertEqual(self.box.list(), [])

    def test_the_model_is_told_it_is_not_sent_yet(self) -> None:
        told = outbox_mod.queued_result(self._queue())
        self.assertEqual(told["sends_in_seconds"], 30)
        self.assertIn("NOT SENT YET", told["note"])
        self.assertNotIn("sent", told.get("status", ""))

    def test_nothing_goes_out_before_the_hold_expires(self) -> None:
        self._queue()
        self.clock.advance(29)
        self.assertEqual(asyncio.run(self.box.run_due()), 0)
        self.assertEqual(self.server.messages, {})

    def test_it_goes_out_when_the_hold_expires(self) -> None:
        row = self._queue()
        self.clock.advance(31)
        self.assertEqual(asyncio.run(self.box.run_due()), 1)
        after = self.box.get(row["id"])
        self.assertEqual(after["status"], "sent")
        self.assertEqual(len(self.server.messages), 1)

    def test_the_hold_can_be_turned_off(self) -> None:
        self.cfg["gmailSendHold"] = {"enabled": False, "seconds": 30}
        row = self.box.queue("mira@example.com", "Now", "body")
        self.assertEqual(row["status"], "sent")
        self.assertFalse(row["held"])
        self.assertEqual(len(self.server.messages), 1)

    def test_the_configured_hold_is_clamped_to_a_useful_range(self) -> None:
        box = Outbox(self.db, self.google, lambda: {"gmailSendHold": {"seconds": 5}})
        self.assertEqual(box.config()["seconds"], outbox_mod.HOLD_MIN)
        box = Outbox(self.db, self.google, lambda: {"gmailSendHold": {"seconds": 9999}})
        self.assertEqual(box.config()["seconds"], outbox_mod.HOLD_MAX)
        self.assertEqual(Outbox(self.db, self.google, lambda: {}).config()["seconds"], 90)

    # ---- undo ----
    def test_undo_works_while_it_is_still_holding(self) -> None:
        row = self._queue()
        self.assertIsNotNone(self.box.cancel(row["id"]))
        self.assertEqual(self.box.get(row["id"])["status"], "cancelled")
        self.clock.advance(60)
        self.assertEqual(asyncio.run(self.box.run_due()), 0)
        self.assertEqual(self.server.messages, {})

    def test_undo_fails_once_it_has_gone_out(self) -> None:
        row = self._queue()
        self.clock.advance(31)
        asyncio.run(self.box.run_due())
        self.assertIsNone(self.box.cancel(row["id"]))
        self.assertEqual(self.box.get(row["id"])["status"], "sent")

    def test_send_now_skips_the_rest_of_the_hold(self) -> None:
        row = self._queue()
        sent = asyncio.run(self.box.send_now(row["id"]))
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.server.messages), 1)

    def test_a_cancelled_send_cannot_be_sent_now(self) -> None:
        row = self._queue()
        self.box.cancel(row["id"])
        self.assertIsNone(asyncio.run(self.box.send_now(row["id"])))
        self.assertEqual(self.server.messages, {})

    def test_the_list_is_the_undo_affordance(self) -> None:
        row = self._queue("One")
        self._queue("Two")
        rows = self.box.list()
        self.assertEqual([r["subject"] for r in rows], ["Two", "One"])
        self.assertEqual(rows[1]["id"], row["id"])
        self.assertTrue(all(r["seconds_left"] == 30 for r in rows))
        self.assertNotIn("body", rows[0])

    # ---- restart ----
    def test_a_held_send_survives_a_restart(self) -> None:
        row = self._queue()
        box = self._restart()
        still = box.get(row["id"])
        self.assertEqual(still["status"], "holding")
        self.assertEqual(still["seconds_left"], 30)
        self.clock.advance(31)
        self.assertEqual(asyncio.run(box.run_due()), 1)
        self.assertEqual(len(self.server.messages), 1)

    def test_a_send_due_during_a_short_outage_still_goes_out(self) -> None:
        row = self._queue()
        self.clock.advance(60)  # due 30s ago, well inside the grace window
        box = self._restart()
        self.assertEqual(box.get(row["id"])["status"], "holding")
        self.assertEqual(asyncio.run(box.run_due()), 1)
        self.assertEqual(len(self.server.messages), 1)

    def test_a_send_stale_across_a_long_outage_is_never_sent_unattended(self) -> None:
        row = self._queue()
        self.clock.advance(30 + outbox_mod.STALE_AFTER + 1)
        box = self._restart()
        after = box.get(row["id"])
        self.assertEqual(after["status"], "expired")
        self.assertIn("Nothing was sent", after["error"])
        self.assertEqual(asyncio.run(box.run_due()), 0)
        self.assertEqual(self.server.messages, {})
        # It is still listed, so it cannot be mistaken for something that went out.
        self.assertIn(row["id"], [r["id"] for r in box.list()])

    def test_an_expired_send_can_still_be_sent_by_hand(self) -> None:
        row = self._queue()
        self.clock.advance(30 + outbox_mod.STALE_AFTER + 1)
        box = self._restart()
        sent = asyncio.run(box.send_now(row["id"]))
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(self.server.messages), 1)

    def test_a_send_interrupted_mid_call_is_not_retried(self) -> None:
        row = self._queue()
        with self.db.tx() as c:  # the process died between the claim and the API call
            c.execute("UPDATE pending_sends SET status = 'sending' WHERE id = ?", (row["id"],))
        box = self._restart()
        after = box.get(row["id"])
        self.assertEqual(after["status"], "failed")
        self.assertIn("NOT retried", after["error"])
        self.assertEqual(self.server.messages, {})

    # ---- verification of what finally went out ----
    def test_the_send_is_verified_when_it_fires(self) -> None:
        row = self._queue()
        self.clock.advance(31)
        asyncio.run(self.box.run_due())
        after = self.box.get(row["id"])
        self.assertTrue(after["verified"])
        self.assertEqual(after["verification"]["status"], verify.VERIFIED)
        self.assertIsNone(after["error"])
        self.assertTrue(after["message_id"])

    def test_an_unverifiable_send_is_not_reported_as_a_clean_send(self) -> None:
        self.server.send_labels = []  # Gmail took it but never filed it under SENT
        row = self._queue()
        self.clock.advance(31)
        asyncio.run(self.box.run_due())
        after = self.box.get(row["id"])
        self.assertFalse(after["verified"])
        self.assertEqual(after["verification"]["status"], verify.UNVERIFIED)
        self.assertIn("Could not confirm", after["error"])

    def test_a_send_that_errors_is_left_for_the_user_not_retried(self) -> None:
        def boom(*a: Any, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("network is down")

        self.google.gmail_send = boom  # type: ignore[method-assign]
        row = self._queue()
        self.clock.advance(31)
        asyncio.run(self.box.run_due())
        after = self.box.get(row["id"])
        self.assertEqual(after["status"], "failed")
        self.assertIn("network is down", after["error"])
        self.assertEqual(asyncio.run(self.box.run_due()), 0)

    def test_a_send_that_never_reached_gmail_stays_open_for_send_now(self) -> None:
        real = self.google.gmail_send

        def offline(*a: Any, **kw: Any) -> dict[str, Any]:
            raise NotSent("TransportError: offline")

        self.google.gmail_send = offline  # type: ignore[method-assign]
        row = self._queue()
        self.clock.advance(31)
        asyncio.run(self.box.run_due())
        after = self.box.get(row["id"])
        self.assertEqual(after["status"], "expired")  # not `failed`: nothing went out, and Send now is offered
        self.assertIn("offline", after["error"])
        self.google.gmail_send = real  # type: ignore[method-assign]
        self.assertEqual(asyncio.run(self.box.send_now(row["id"]))["status"], "sent")

    # ---- the agent's reach ----
    def test_the_agent_can_cancel_but_not_hurry_a_send(self) -> None:
        from personal_os.tools import Toolbox

        box = Toolbox(None, None, None, lambda: {}, google=self.google, outbox=self.box)  # type: ignore[arg-type]
        actions = box.specs["gmail_outbox"].parameters["properties"]["action"]["enum"]
        self.assertEqual(actions, ["list", "cancel"])
        queued = asyncio.run(box.call("gmail_send", {"to": "mira@example.com", "subject": "Hi", "body": "x"},
                                      {"project_id": None, "conversation_id": "c1"}))
        self.assertIn("NOT SENT YET", queued["note"])
        listed = asyncio.run(box.call("gmail_outbox", {}, {"project_id": None, "conversation_id": "c1"}))
        self.assertEqual(listed["count"], 1)
        out = asyncio.run(box.call("gmail_outbox", {"action": "cancel", "id": queued["queued"]},
                                   {"project_id": None, "conversation_id": "c1"}))
        self.assertEqual(out["cancelled"], queued["queued"])
        self.assertEqual(self.server.messages, {})

    def test_another_chat_cannot_see_or_cancel_a_queued_send(self) -> None:
        from personal_os.tools import Toolbox

        box = Toolbox(None, None, None, lambda: {}, google=self.google, outbox=self.box)  # type: ignore[arg-type]
        queued = asyncio.run(box.call("gmail_send", {"to": "mira@example.com", "subject": "Hi", "body": "x"},
                                      {"project_id": None, "conversation_id": "c1"}))
        other = {"project_id": None, "conversation_id": "c2"}
        listed = asyncio.run(box.call("gmail_outbox", {}, other))
        self.assertEqual(listed["count"], 0)
        denied = asyncio.run(box.call("gmail_outbox", {"action": "cancel", "id": queued["queued"]}, other))
        self.assertIn("error", denied)
        self.assertEqual(self.box.get(queued["queued"])["status"], "holding")


if __name__ == "__main__":
    unittest.main(verbosity=2)
