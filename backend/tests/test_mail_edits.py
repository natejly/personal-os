"""Edited arguments on a gmail_send / gmail_draft approval: validation, the route, as_draft, and the outbox/verify
paths seeing the EDITED values.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_mail_edits.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mailedits-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_google_api import FakeGoogle, FakeServer  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, approval_edits, mail_edits, verify  # noqa: E402
from personal_os.approval_edits import EditError  # noqa: E402
from personal_os.mail_edits import ApprovalEditError  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.outbox import Outbox  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

GOOD = {"to": "Mira <mira@example.com>; ana@example.com", "subject": "Hi", "body": "Line one\n\nLine two"}


class ValidatorTests(unittest.TestCase):
    def test_editable_and_cleaned(self) -> None:
        self.assertIn("gmail_send", approval_edits.EDITABLE_TOOLS)
        out = approval_edits.validate("gmail_send", GOOD, appmod.toolbox.specs["gmail_send"].parameters)
        self.assertEqual(out["to"], "Mira <mira@example.com>, ana@example.com")
        self.assertEqual(out["body"], GOOD["body"])  # the model's line breaks survive

    def test_rejections(self) -> None:
        schema = appmod.toolbox.specs["gmail_send"].parameters
        bad = [{**GOOD, "to": ""}, {**GOOD, "to": "not-an-address"}, {**GOOD, "to": "a@b.co, nope"},
               {**GOOD, "subject": "x\nBcc: evil@example.com"}, {**GOOD, "subject": "s" * 999},
               {**GOOD, "body": "b" * 200_001}, {**GOOD, "to": ",".join(f"u{i}@example.com" for i in range(51))},
               {**GOOD, "reply_to_message_id": "../x"}, {**GOOD, "as_draft": "yes"}, {**GOOD, "cc": "x@y.co"},
               {"to": "a@b.co", "subject": "s"}]
        for args in bad:
            with self.assertRaises(EditError, msg=str(args)[:60]):
                approval_edits.validate("gmail_send", args, schema)

    def test_a_quoted_name_with_a_comma_is_one_recipient(self) -> None:
        out = approval_edits.validate("gmail_send", {**GOOD, "to": '"Doe, John" <john@x.com>, ana@example.com'},
                                      appmod.toolbox.specs["gmail_send"].parameters)
        self.assertEqual(out["to"], '"Doe, John" <john@x.com>, ana@example.com')
        self.assertEqual(mail_edits.parse_recipients(out["to"]), ['"Doe, John" <john@x.com>', "ana@example.com"])

    def test_as_draft_is_not_a_draft_tool_argument(self) -> None:
        with self.assertRaises(ApprovalEditError):
            mail_edits._clean({**GOOD, "as_draft": True}, allow_draft_flag=False)

    def test_non_editable_tool(self) -> None:
        with self.assertRaises(EditError):
            approval_edits.validate("web_search", {"query": "x"}, {})


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(appmod.app)
        self.h = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
        self.u = uuid.uuid4().hex[:8]

    def _open(self, tool: str, cid: str, args: dict) -> None:
        appmod.run_store.open_approval(cid, None, tool, args)

    def test_bad_edit_is_400_and_leaves_the_card_pending(self) -> None:
        self._open("gmail_send", f"m1{self.u}:call_0", GOOD)
        r = self.client.post(f"/approvals/m1{self.u}:call_0", headers=self.h, json={"decision": "allow", "arguments": {**GOOD, "to": "nope"}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(appmod.run_store.approval(f"m1{self.u}:call_0")["status"], "pending")

    def test_non_editable_tool_rejects_arguments(self) -> None:
        self._open("web_search", f"m2{self.u}:call_0", {"query": "x"})
        r = self.client.post(f"/approvals/m2{self.u}:call_0", headers=self.h, json={"decision": "allow", "arguments": {"query": "y"}})
        self.assertEqual(r.status_code, 400)

    def test_good_edit_is_recorded_for_the_run(self) -> None:
        import asyncio
        cid = f"m3{self.u}:call_0"
        self._open("gmail_send", cid, GOOD)
        loop = asyncio.new_event_loop()
        appmod._approvals[cid] = loop.create_future()  # a run waiting in this process
        try:
            r = self.client.post(f"/approvals/{cid}", headers=self.h, json={"decision": "allow", "arguments": GOOD})
        finally:
            appmod._approvals.pop(cid, None)
            loop.close()
        self.assertEqual(r.status_code, 200)
        # The durable record the waking run reads: normalised by mail_edits, digest re-bound to it.
        self.assertEqual(appmod.run_store.approval(cid)["edited_args"]["to"], "Mira <mira@example.com>, ana@example.com")

    def test_edit_with_no_waiting_run_is_recorded_not_run(self) -> None:
        cid = f"m4{self.u}:call_0"
        self._open("gmail_send", cid, GOOD)
        r = self.client.post(f"/approvals/{cid}", headers=self.h, json={"decision": "allow", "arguments": GOOD})
        self.assertEqual(r.status_code, 200)
        row = appmod.run_store.approval(cid)
        self.assertEqual((row["status"], row["edited_by"]), ("approved", "user"))


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.server = FakeServer()
        self.g = FakeGoogle(self.server)
        self.box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120))
        self.tools = Toolbox(None, None, None, lambda: {}, google=self.g, outbox=self.box)  # type: ignore[arg-type]
        self._d, self._s = verify.MAIL_RETRY_DELAYS, verify.SLEEP
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None

    def tearDown(self) -> None:
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = self._d, self._s
        self.tmp.cleanup()

    def _edited(self) -> dict:
        return approval_edits.validate("gmail_send", {**GOOD, "to": "Edited@Example.com", "subject": "Edited subject", "body": "Human wrote this"},
                                       self.tools.specs["gmail_send"].parameters)

    def test_outbox_gets_the_edited_args(self) -> None:
        out = asyncio.run(self.tools.call("gmail_send", self._edited(), {"conversation_id": None}))
        row = self.box.get(out["queued"])
        self.assertEqual((row["to"], row["subject"]), ("Edited@Example.com", "Edited subject"))
        with self.db.tx() as c:
            self.assertEqual(c.execute("SELECT body FROM pending_sends WHERE id=?", (out["queued"],)).fetchone()["body"], "Human wrote this")
        sent = asyncio.run(self.box.send_now(out["queued"]))
        self.assertTrue(sent["verified"])
        msg = list(self.server.messages.values())[0]
        self.assertIn("Edited subject", str(msg))

    def test_as_draft_writes_a_verified_draft_and_never_sends(self) -> None:
        out = asyncio.run(self.tools.call("gmail_send", {**self._edited(), "as_draft": True}, {"conversation_id": None}))
        self.assertTrue(out.get("draft_id"), out)
        self.assertEqual(out["subject"], "Edited subject")
        self.assertTrue(verify.ok(out["verification"]))
        self.assertEqual(self.server.messages, {})
        self.assertEqual(self.box.list(), [])


if __name__ == "__main__":
    unittest.main()
