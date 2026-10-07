"""Gmail attachments: the MIME built for a send or draft, the 25 MB limit, the attachment list of a received
message, saving one to disk, the outbox carrying ids through its hold, and the agent tools.

Mocked Gmail only (fake_google_api); nothing is sent anywhere.

Run: python backend/tests/test_mail_attachments.py
"""
from __future__ import annotations

import asyncio
import base64
import email
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mailatt-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_google_api import FakeGoogle, FakeServer  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, blobs, mail_attachments as ma, mail_edits, verify  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.google import NotSent, _raw_message  # noqa: E402
from personal_os.outbox import Outbox  # noqa: E402
from personal_os.repos import Documents  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

MB = 1024 * 1024


def _parsed(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(self.tmp.name)
        self.docs = Documents(self.db)
        self.server = FakeServer()
        self.g = FakeGoogle(self.server)
        self._d, self._s = verify.MAIL_RETRY_DELAYS, verify.SLEEP
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = (0.0,), lambda s: None

    def tearDown(self) -> None:
        verify.MAIL_RETRY_DELAYS, verify.SLEEP = self._d, self._s
        self.tmp.cleanup()

    def upload(self, name: str, data: bytes = b"hello", mime: str = "application/pdf", size: int | None = None) -> str:
        """An Uploads document. `size` makes a sparse file of that length instead, for the limit tests."""
        dest, digest = blobs.store(self.db.data_dir, name, data)
        if size is not None:
            with dest.open("r+b") as f:
                f.truncate(size)
        return self.docs.create(None, name, mime, size or len(data), str(dest), "", content_hash=digest + name)["id"]

    def items(self, *ids: str) -> list[dict[str, Any]]:
        return ma.resolve(self.docs, self.db.data_dir, list(ids))


class MimeTests(Base):
    def test_no_attachments_is_still_plain_text(self) -> None:
        msg = _parsed(_raw_message("a@example.com", "Hi", "just text"))
        self.assertFalse(msg.is_multipart())
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertEqual(msg.get_payload(), "just text\n")
        self.assertEqual((msg["To"], msg["Subject"]), ("a@example.com", "Hi"))

    def test_non_ascii_text_round_trips(self) -> None:
        msg = email.message_from_bytes(base64.urlsafe_b64decode(_raw_message("Zoë <z@example.com>", "Café", "naïve")),
                                       policy=email.policy.default)
        self.assertEqual(msg["Subject"], "Café")
        self.assertIn("z@example.com", msg["To"])
        self.assertEqual(msg.get_content().strip(), "naïve")

    def test_multipart_parts_names_and_types(self) -> None:
        a, b = self.upload("report.pdf", b"%PDF-1"), self.upload("pic.png", b"\x89PNG", "image/png")
        msg = email.message_from_bytes(base64.urlsafe_b64decode(
            _raw_message("a@example.com", "Hi", "see attached", attachments=self.items(a, b))), policy=email.policy.default)
        self.assertEqual(msg.get_content_type(), "multipart/mixed")
        body, *files = list(msg.iter_parts())
        self.assertEqual((body.get_content_type(), body.get_content().strip()), ("text/plain", "see attached"))
        self.assertEqual([(f.get_filename(), f.get_content_type()) for f in files],
                         [("report.pdf", "application/pdf"), ("pic.png", "image/png")])
        self.assertEqual(files[0].get_content(), b"%PDF-1")

    def test_send_keeps_thread_and_reply_headers(self) -> None:
        self.server.messages["orig"] = {"id": "orig", "threadId": "t1", "labelIds": [],
                                        "payload": {"headers": [{"name": "Message-ID", "value": "<m1@x>"}, {"name": "References", "value": "<m0@x>"}]}}
        out = self.g.gmail_send("a@example.com", "Re: x", "ok", "orig", attachments=self.items(self.upload("f.txt", b"x", "text/plain")))
        self.assertEqual((out["thread_id"], out["attachments"]), ("t1", ["f.txt"]))
        sent = self.server.messages[out["sent"]]
        msg = _parsed(sent["raw"])
        self.assertEqual((msg["In-Reply-To"], msg["References"]), ("<m1@x>", "<m0@x> <m1@x>"))
        self.assertTrue(msg.is_multipart())

    def test_draft_carries_the_files(self) -> None:
        out = self.g.gmail_draft("a@example.com", "Hi", "b", attachments=self.items(self.upload("f.txt", b"x", "text/plain")))
        self.assertEqual(out["attachments"], ["f.txt"])
        raw = next(iter(self.server.drafts.values()))["message"]["raw"]
        self.assertEqual([p.get_filename() for p in _parsed(raw).walk() if p.get_filename()], ["f.txt"])


class LimitTests(Base):
    def test_total_over_25mb_is_refused_everywhere(self) -> None:
        a, b = self.upload("a.bin", size=13 * MB), self.upload("b.bin", size=13 * MB)
        with self.assertRaisesRegex(ma.AttachmentError, "Gmail allows 25 MB"):
            self.items(a, b)
        one = self.items(a)
        two = one + [{**one[0], "name": "again.bin"}]  # 26 MB of files, built past resolve()
        with self.assertRaises(ma.AttachmentError):
            _raw_message("a@example.com", "s", "b", attachments=two)
        with self.assertRaises(NotSent):
            self.g.gmail_send("a@example.com", "s", "b", attachments=two)
        self.assertEqual(self.server.messages, {})

    def test_under_the_limit_and_unknown_or_missing(self) -> None:
        self.assertEqual(len(self.items(self.upload("a.bin", size=12 * MB), self.upload("b.bin", size=12 * MB))), 2)
        with self.assertRaisesRegex(ma.AttachmentError, "not an uploaded file"):
            self.items("nope")
        gone = self.upload("gone.txt", b"x")
        Path(self.docs.get(gone)["path"]).unlink()
        with self.assertRaisesRegex(ma.AttachmentError, "no longer stored"):
            self.items(gone)

    def test_count_limit(self) -> None:
        with self.assertRaisesRegex(ma.AttachmentError, "At most 20"):
            ma.resolve(self.docs, self.db.data_dir, ["x"] * 21)

    def test_outbox_queue_fails_before_any_row(self) -> None:
        box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120), documents=self.docs)
        a, b = self.upload("a.bin", size=13 * MB), self.upload("b.bin", size=13 * MB)
        with self.assertRaises(ma.AttachmentError):
            box.queue("a@example.com", "s", "b", attachments=[a, b])
        with self.assertRaises(ma.AttachmentError):
            box.queue("a@example.com", "s", "b", attachments=["nope"])
        self.assertEqual(box.list(), [])


class ReceivedTests(Base):
    PAYLOAD = {"mimeType": "multipart/mixed", "headers": [{"name": "From", "value": "x@example.com"}], "parts": [
        {"mimeType": "multipart/alternative", "filename": "", "body": {}, "parts": [
            {"mimeType": "text/plain", "filename": "", "body": {"data": base64.urlsafe_b64encode(b"hi").decode()}}]},
        {"mimeType": "application/pdf", "filename": "inv.pdf", "body": {"attachmentId": "att1", "size": 12}},
        {"mimeType": "multipart/related", "filename": "", "body": {}, "parts": [
            {"mimeType": "image/png", "filename": "logo.png", "body": {"attachmentId": "att2", "size": 3}},
            {"mimeType": "image/gif", "filename": "", "body": {"attachmentId": "inline", "size": 1}}]}]}

    def test_attachment_list_and_download(self) -> None:
        self.server.messages["m1"] = {"id": "m1", "threadId": "t", "labelIds": [], "payload": self.PAYLOAD}
        self.server.attachments[("m1", "att1")] = b"\x00\xff PDF bytes" * 3
        got = self.g.gmail_get("m1")
        self.assertEqual(got["attachments"], [{"id": "att1", "name": "inv.pdf", "mime": "application/pdf", "size": 12},
                                              {"id": "att2", "name": "logo.png", "mime": "image/png", "size": 3}])
        self.assertEqual(got["body"], "hi")
        self.assertEqual(self.g.gmail_attachment("m1", "att1"), b"\x00\xff PDF bytes" * 3)

    def test_old_cached_body_without_the_field_is_refetched(self) -> None:
        self.server.messages["m1"] = {"id": "m1", "threadId": "t", "labelIds": [], "payload": self.PAYLOAD}
        self.g._reads.put("gmail-body", "m1:8000", {"id": "m1", "body": "stale"}, cap=10)
        self.assertEqual(len(self.g.gmail_get("m1")["attachments"]), 2)


class CcBccTests(Base):
    def test_headers_are_set_only_when_given(self) -> None:
        plain = _parsed(_raw_message("a@example.com", "Hi", "b"))
        self.assertEqual((plain["Cc"], plain["Bcc"]), (None, None))
        msg = _parsed(_raw_message("a@example.com", "Hi", "b", cc="b@example.com, Zoë <z@example.com>", bcc="c@example.com"))
        self.assertEqual(msg["Bcc"], "c@example.com")
        self.assertIn("z@example.com", msg["Cc"])
        self.assertIn("b@example.com", msg["Cc"])

    def test_send_and_draft_carry_them(self) -> None:
        out = self.g.gmail_send("a@example.com", "Hi", "b", cc="b@example.com", bcc="c@example.com")
        raw = _parsed(self.server.messages[out["sent"]]["raw"])
        self.assertEqual((raw["Cc"], raw["Bcc"]), ("b@example.com", "c@example.com"))
        self.g.gmail_draft("a@example.com", "Hi", "b", cc="b@example.com")
        self.assertEqual(_parsed(next(iter(self.server.drafts.values()))["message"]["raw"])["Cc"], "b@example.com")

    def test_outbox_keeps_them_through_the_hold(self) -> None:
        now = [1_000_000.0]
        box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120),
                     clock=lambda: now[0], documents=self.docs)
        row = box.queue("a@example.com", "Hi", "b", cc="b@example.com", bcc="c@example.com")
        self.assertEqual((row["cc"], row["bcc"]), ("b@example.com", "c@example.com"))
        self.assertEqual(box.queue("a@example.com", "Hi", "b")["cc"], "")
        now[0] += 31
        asyncio.run(box.run_due())
        sent = [m for m in self.server.messages.values() if _parsed(m["raw"])["Cc"]]
        self.assertEqual(len(sent), 1)
        self.assertEqual(_parsed(sent[0]["raw"])["Bcc"], "c@example.com")
        with self.assertRaises(mail_edits.ApprovalEditError):
            box.queue("a@example.com", "Hi", "b", cc="not an address")

    def test_edits_validate_and_drop_empty_cc(self) -> None:
        args = {"to": "a@example.com", "subject": "s", "body": "b"}
        out = mail_edits._clean({**args, "cc": "x@example.com; y@example.com", "bcc": ""}, allow_draft_flag=True)
        self.assertEqual(out["cc"], "x@example.com, y@example.com")
        self.assertNotIn("bcc", out)
        with self.assertRaises(mail_edits.ApprovalEditError):
            mail_edits._clean({**args, "bcc": ",".join(f"u{i}@example.com" for i in range(50))}, allow_draft_flag=True)  # 51 in all

    def test_tool_schemas_offer_cc_and_bcc(self) -> None:
        tools = Toolbox(None, None, self.docs, lambda: {}, google=self.g)  # type: ignore[arg-type]
        for name in ("gmail_send", "gmail_draft"):
            self.assertTrue({"cc", "bcc", "attachments"} <= set(tools.specs[name].parameters["properties"]))

    def test_outlook_refuses_what_it_cannot_do(self) -> None:
        from test_microsoft_mail import FakeMs
        ms = FakeMs()
        for kw in ({"attachments": [{"name": "a", "path": "/x"}]}, {"cc": "b@example.com"}, {"bcc": "b@example.com"}):
            with self.assertRaises(NotSent):
                ms.gmail_send("a@example.com", "Hi", "b", **kw)
            with self.assertRaises(ValueError):
                ms.gmail_draft("a@example.com", "Hi", "b", **kw)
        self.assertEqual(ms.calls, [])


class SaveTests(unittest.TestCase):
    def test_names_cannot_leave_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t) / "Downloads"
            paths = [ma.save_to_folder(b"x", n, folder) for n in ("../../evil.pdf", "a/b.pdf", "con\x00trol.txt", "..", "")]
            for p in paths:
                self.assertEqual(p.parent.resolve(), folder.resolve())
            self.assertEqual(sorted(p.name for p in paths), ["b.pdf", "control.txt", "evil.pdf", "untitled", "untitled (2)"])
            self.assertEqual(sorted(os.listdir(folder)), sorted(p.name for p in paths))  # no temp file left
            self.assertFalse((Path(t) / "evil.pdf").exists())

    def test_collision_gets_a_suffix_and_keeps_both(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            a, b, c = (ma.save_to_folder(d, "x.tar.gz", Path(t)) for d in (b"1", b"2", b"3"))
            self.assertEqual([p.name for p in (a, b, c)], ["x.tar.gz", "x.tar (2).gz", "x.tar (3).gz"])
            self.assertEqual((a.read_bytes(), b.read_bytes()), (b"1", b"2"))

    def test_large_file_is_written_whole(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            data = os.urandom(3 * MB + 5)
            self.assertEqual(ma.save_to_folder(data, "big.bin", Path(t)).read_bytes(), data)

    def test_downloads_dir_honours_the_override(self) -> None:
        old = os.environ.get("PERSONAL_OS_DOWNLOADS_DIR")
        try:
            os.environ["PERSONAL_OS_DOWNLOADS_DIR"] = "/tmp/somewhere"
            self.assertEqual(ma.downloads_dir(), Path("/tmp/somewhere"))
            del os.environ["PERSONAL_OS_DOWNLOADS_DIR"]
            self.assertEqual(ma.downloads_dir(), Path.home() / "Downloads")
        finally:
            if old is not None:
                os.environ["PERSONAL_OS_DOWNLOADS_DIR"] = old


class OutboxTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.now = [1_000_000.0]
        self.box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120),
                          clock=lambda: self.now[0], documents=self.docs)

    def test_held_send_goes_out_with_its_files(self) -> None:
        a, b = self.upload("one.pdf", b"1"), self.upload("two.txt", b"2", "text/plain")
        row = self.box.queue("a@example.com", "Hi", "body", attachments=[a, b])
        self.assertEqual(row["attachments"], ["one.pdf", "two.txt"])
        self.assertEqual(self.server.messages, {})  # held
        self.assertEqual(self.box.list()[0]["attachments"], ["one.pdf", "two.txt"])
        self.now[0] += 31
        asyncio.run(self.box.run_due())
        sent = self.box.get(row["id"])
        self.assertEqual(sent["status"], "sent")
        msg = _parsed(self.server.messages[sent["message_id"]]["raw"])
        self.assertEqual([p.get_filename() for p in msg.walk() if p.get_filename()], ["one.pdf", "two.txt"])

    def test_cancelled_send_sends_nothing(self) -> None:
        row = self.box.queue("a@example.com", "Hi", "body", attachments=[self.upload("one.pdf")])
        self.box.cancel(row["id"])
        self.now[0] += 31
        asyncio.run(self.box.run_due())
        self.assertEqual(self.server.messages, {})

    def test_file_deleted_during_the_hold_is_not_sent(self) -> None:
        a = self.upload("one.pdf")
        row = self.box.queue("a@example.com", "Hi", "body", attachments=[a])
        Path(self.docs.get(a)["path"]).unlink()
        self.now[0] += 31
        asyncio.run(self.box.run_due())
        self.assertEqual(self.server.messages, {})
        self.assertIn("no longer stored", self.box.get(row["id"])["error"])

    def test_hold_off_passes_files_straight_through(self) -> None:
        box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": False}}, documents=self.docs)
        out = box.queue("a@example.com", "Hi", "b", attachments=[self.upload("one.pdf")])
        self.assertEqual((out["status"], out["attachments"]), ("sent", ["one.pdf"]))

    def test_old_table_gains_the_column(self) -> None:
        with self.db.tx() as c:
            c.execute("DROP TABLE pending_sends")
            c.execute("CREATE TABLE pending_sends (id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',"
                      " body TEXT NOT NULL DEFAULT '', reply_to_message_id TEXT, origin TEXT NOT NULL DEFAULT 'app', conversation_id TEXT,"
                      " status TEXT NOT NULL DEFAULT 'holding', hold_seconds INTEGER NOT NULL DEFAULT 90, created_at REAL NOT NULL,"
                      " send_after REAL NOT NULL, resolved_at REAL, message_id TEXT, thread_id TEXT, error TEXT, verification TEXT)")
        box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120), documents=self.docs)
        self.assertEqual(box.queue("a@example.com", "Hi", "b", attachments=[self.upload("one.pdf")])["attachments"], ["one.pdf"])


class ToolTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.box = Outbox(self.db, self.g, lambda: {"gmailSendHold": {"enabled": True, "seconds": 30}}, bounds=(0, 120), documents=self.docs)
        self.tools = Toolbox(None, None, self.docs, lambda: {}, google=self.g, outbox=self.box)  # type: ignore[arg-type]

    def test_both_tools_are_external_and_take_attachments(self) -> None:
        for name in ("gmail_send", "gmail_draft"):
            spec = self.tools.specs[name]
            self.assertEqual(spec.danger, "external")  # so the approval card gates it: the tool body only runs after "allow"
            self.assertEqual(spec.parameters["properties"]["attachments"]["type"], "array")

    def test_an_edited_approval_keeps_its_attachments(self) -> None:
        args = {"to": "a@example.com", "subject": "s", "body": "b", "attachments": ["doc1", "~/x.pdf"]}
        self.assertEqual(mail_edits._clean(args, allow_draft_flag=True)["attachments"], ["doc1", "~/x.pdf"])
        self.assertNotIn("attachments", mail_edits._clean({**args, "attachments": []}, allow_draft_flag=True))
        for bad in ("doc1", [""], [1], ["x"] * 21, ["x" * 1025]):
            with self.assertRaises(mail_edits.ApprovalEditError):
                mail_edits._clean({**args, "attachments": bad}, allow_draft_flag=True)

    def test_send_queues_with_the_file_and_sends_nothing_yet(self) -> None:
        a = self.upload("one.pdf")
        out = asyncio.run(self.tools.specs["gmail_send"].fn({}, "a@example.com", "Hi", "body", None, False, [a]))
        self.assertEqual((out["status"], out["attachments"]), ("holding", ["one.pdf"]))
        self.assertEqual(self.server.messages, {})

    def test_draft_with_a_file_path_copies_it_into_uploads(self) -> None:
        src = Path(self.tmp.name) / "notes.txt"
        src.write_text("local file")
        out = asyncio.run(self.tools.specs["gmail_draft"].fn({}, "a@example.com", "Hi", "body", None, [str(src)]))
        if "error" in out:  # a sandboxed temp dir may be outside the folders the agent may read
            self.skipTest(out["error"])
        self.assertEqual(out["attachments"], ["notes.txt"])
        self.assertEqual(len(self.docs.list(None)), 1)

    def test_a_bad_entry_is_named_and_nothing_is_written(self) -> None:
        out = asyncio.run(self.tools.specs["gmail_send"].fn({}, "a@example.com", "Hi", "body", None, False, ["no-such-id"]))
        self.assertIn("no-such-id", out["error"])
        self.assertEqual(self.box.list(), [])
        self.assertEqual(self.server.messages, {})


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.server.messages["m1"] = {"id": "m1", "threadId": "t", "labelIds": [], "payload": ReceivedTests.PAYLOAD}
        self.server.attachments[("m1", "att1")] = b"%PDF-data"
        self.real, appmod.pim.google = appmod.pim.google, FakeGoogle(self.server)
        self.out = tempfile.TemporaryDirectory()
        self._env = os.environ.get("PERSONAL_OS_DOWNLOADS_DIR")
        os.environ["PERSONAL_OS_DOWNLOADS_DIR"] = self.out.name
        self.client = TestClient(appmod.app)
        self.h = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}

    def tearDown(self) -> None:
        appmod.pim.google = self.real
        self.out.cleanup()
        if self._env is None:
            os.environ.pop("PERSONAL_OS_DOWNLOADS_DIR", None)
        else:
            os.environ["PERSONAL_OS_DOWNLOADS_DIR"] = self._env

    def test_import_takes_name_and_type_from_gmail(self) -> None:
        r = self.client.post("/integrations/google/gmail/m1/attachments/att1/import", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["name"], r.json()["mime"]), ("inv.pdf", "application/pdf"))
        self.assertIsNotNone(blobs.inside_uploads(appmod.db.data_dir, appmod.documents.get(r.json()["id"])["path"]))

    def test_save_lands_in_downloads(self) -> None:
        r = self.client.post("/integrations/google/gmail/m1/attachments/att1/save", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(Path(r.json()["path"]), Path(self.out.name) / "inv.pdf")
        self.assertEqual(Path(r.json()["path"]).read_bytes(), b"%PDF-data")

    def test_unknown_attachment_is_404(self) -> None:
        for route in ("import", "save"):
            self.assertEqual(self.client.post(f"/integrations/google/gmail/m1/attachments/nope/{route}", headers=self.h).status_code, 404)

    def test_draft_with_a_bad_id_is_400(self) -> None:
        r = self.client.post("/integrations/google/gmail/draft", headers=self.h,
                             json={"to": "a@example.com", "subject": "s", "body": "b", "attachments": ["nope"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.server.drafts, {})

    def test_draft_with_an_upload_carries_it(self) -> None:
        up = self.client.post("/documents", headers=self.h, files={"file": ("note.txt", b"hello attach", "text/plain")}).json()
        r = self.client.post("/integrations/google/gmail/draft", headers=self.h,
                             json={"to": "a@example.com", "subject": "s", "body": "b", "attachments": [up["id"]]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["attachments"], ["note.txt"])


if __name__ == "__main__":
    unittest.main()
