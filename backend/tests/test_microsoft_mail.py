"""Outlook Mail over Graph: the mixin run on a fake `_req`, checked against the shapes Google's gmail_* return.

Run: PYTHONPATH=. .venv/bin/python -m pytest tests/test_microsoft_mail.py -q
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mailwatch, verify  # noqa: E402
from personal_os.cache import TTLCache  # noqa: E402
from personal_os.google import NotSent  # noqa: E402
from personal_os.microsoft_mail import MailMixin  # noqa: E402

# google.py `_gmail_meta`: the keys every gmail_search row carries.
GMAIL_SEARCH_KEYS = {"id", "thread_id", "from", "subject", "date", "snippet", "unread", "labels"}
# google.py `_gmail_thread_meta`: per-message keys of gmail_threads_recent, which mailwatch.classify reads.
GMAIL_THREAD_MSG_KEYS = {"id", "from", "to", "cc", "date", "labels", "snippet", "auto"}
GMAIL_GET_KEYS = {"id", "thread_id", "from", "to", "subject", "date", "body"}
FOLDER_IDS = {"inbox": "F-inbox", "sentitems": "F-sent", "drafts": "F-drafts", "archive": "F-archive", "junkemail": "F-junk", "deleteditems": "F-trash"}

Handler = Callable[[str, str, dict[str, Any]], Any]


def msg(i: str, conv: str = "c1", read: bool = False, frm: str = "ann@x.com", to: str = "me@x.com", folder: str = "F-inbox",
        when: str = "2026-10-04T10:00:00Z", subject: str = "Hi", preview: str = "can you send it?", **extra: Any) -> dict[str, Any]:
    return {"id": i, "conversationId": conv, "subject": subject, "from": {"emailAddress": {"name": "Ann", "address": frm}},
            "toRecipients": [{"emailAddress": {"address": to}}], "receivedDateTime": when, "bodyPreview": preview, "isRead": read,
            "flag": {"flagStatus": "notFlagged"}, "hasAttachments": False, "categories": [], "parentFolderId": folder, **extra}


class FakeMs(MailMixin):
    def __init__(self, handler: Handler | None = None):
        self._cache = TTLCache()
        self.me = {"mail": "me@x.com"}
        self.calls: list[dict[str, Any]] = []
        self.handler = handler or (lambda m, p, kw: None)

    def get_settings(self) -> dict[str, Any]:
        return {}

    def _req(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None, headers: dict | None = None) -> Any:
        self.calls.append({"method": method, "path": path, "params": params or {}, "json": json, "headers": headers})
        if method == "GET" and path.startswith("/me/mailFolders/") and path.count("/") == 3:
            return {"id": FOLDER_IDS[path.rsplit("/", 1)[1]]}
        return self.handler(method, path, {"params": params or {}, "json": json, "headers": headers})

    def find(self, method: str, prefix: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"].startswith(prefix)]


class MailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._sleep, verify.SLEEP = verify.SLEEP, lambda s: None

    def tearDown(self) -> None:
        verify.SLEEP = self._sleep

    def test_search_translates_query_and_matches_google_keys(self) -> None:
        ms = FakeMs(lambda m, p, kw: {"value": [msg("a", read=False), msg("b", read=False, when="2026-10-05T10:00:00Z", flag={"flagStatus": "flagged"})]})
        rows = ms.gmail_search("is:unread in:inbox newer_than:14d -category:promotions -in:spam", 10)
        call = ms.find("GET", "/me/mailFolders/inbox/messages")[0]
        self.assertTrue(call["params"]["$filter"].startswith("receivedDateTime ge "))
        self.assertIn("isRead eq false", call["params"]["$filter"])
        self.assertNotIn("$search", call["params"])
        self.assertEqual(call["params"]["$top"], 10)
        self.assertEqual({k for r in rows for k in r}, GMAIL_SEARCH_KEYS)
        self.assertEqual([r["id"] for r in rows], ["b", "a"])  # newest first
        self.assertEqual(rows[0]["labels"], ["INBOX", "UNREAD", "STARRED"])
        self.assertEqual(rows[0]["from"], "Ann <ann@x.com>")
        self.assertTrue(rows[0]["unread"])

    def test_search_free_text_uses_search_without_filter_or_orderby(self) -> None:
        ms = FakeMs(lambda m, p, kw: {"value": [msg("a", read=False), msg("b", read=True)]})
        rows = ms.gmail_search("is:unread subject:invoice from:bob@x.com", 5)
        params = ms.find("GET", "/me/messages")[0]["params"]
        self.assertEqual(params["$search"], '"subject:invoice"')
        self.assertNotIn("$filter", params)
        self.assertNotIn("$orderby", params)
        self.assertEqual(rows, [])  # unread and from:bob are applied client-side: neither fake row is from bob

    def test_get_text_body_truncated(self) -> None:
        ms = FakeMs(lambda m, p, kw: {**msg("a"), "body": {"contentType": "text", "content": "x" * 50}})
        out = ms.gmail_get("a", max_chars=10)
        self.assertEqual(out["body"], "x" * 10)
        self.assertEqual(set(out), GMAIL_GET_KEYS)
        self.assertEqual(ms.calls[-1]["headers"], {"Prefer": 'outlook.body-content-type="text"'})

    def test_get_strips_html_when_graph_sends_it_anyway(self) -> None:
        ms = FakeMs(lambda m, p, kw: {**msg("a"), "body": {"contentType": "html", "content": "<p>Hello <b>you</b></p><style>x{}</style>"}})
        self.assertEqual(ms.gmail_get("a")["body"], "Hello you")

    def _modify_fake(self, stored_read: bool) -> FakeMs:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            return msg("a", read=stored_read) if method == "GET" else None
        return FakeMs(h)

    def test_modify_mark_read_verified_and_not(self) -> None:
        ms = self._modify_fake(stored_read=True)
        out = ms.gmail_modify("a", mark_read=True)
        self.assertEqual(ms.find("PATCH", "/me/messages/a")[0]["json"], {"isRead": True})
        self.assertTrue(out["verified"])
        self.assertEqual((out["ok"], out["removed"]), (True, ["UNREAD"]))
        bad = self._modify_fake(stored_read=False).gmail_modify("a", mark_read=True)
        self.assertFalse(bad["verified"])
        self.assertEqual(bad["verification"]["status"], verify.MISMATCH)

    def test_modify_archive_moves_and_verifies_against_new_id(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            if path.endswith("/move"):
                return {"id": "a2"}
            return msg("a2", read=True, folder="F-archive") if path == "/me/messages/a2" else None
        ms = FakeMs(h)
        out = ms.gmail_modify("a", archive=True, star=True)
        self.assertEqual(ms.find("POST", "/me/messages/a/move")[0]["json"], {"destinationId": "archive"})
        self.assertEqual(ms.find("PATCH", "/me/messages/a")[0]["json"], {"flag": {"flagStatus": "flagged"}})
        self.assertEqual(out["moved_id"], "a2")
        self.assertFalse(out["verified"])  # the stored copy is not starred: the verdict must say so
        self.assertIn("+STARRED", out["verification"]["differences"])

    def test_send_not_sent_when_build_fails_before_post(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            raise RuntimeError("createReply failed")
        ms = FakeMs(h)
        with self.assertRaises(NotSent):
            ms.gmail_send("ann@x.com", "Re: Hi", "hello", reply_to_message_id="orig")
        with self.assertRaises(NotSent):
            ms.gmail_send("", "Hi", "hello")  # no recipient: refused before any call
        self.assertFalse(ms.find("POST", "/me/sendMail"))

    def test_send_returns_id_after_sent_items_readback(self) -> None:
        sent = {"id": "S1", "conversationId": "c9", "subject": "Hi", "toRecipients": [{"emailAddress": {"address": "ann@x.com"}}]}
        polls = []

        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            if path == "/me/mailFolders/sentitems/messages":
                polls.append(1)
                return {"value": [sent] if len(polls) > 1 else []}  # not indexed on the first look
            return None  # sendMail: 202, no body
        ms = FakeMs(h)
        out = ms.gmail_send("Ann <ann@x.com>", "Hi", "hello")
        self.assertEqual(ms.find("POST", "/me/sendMail")[0]["json"]["saveToSentItems"], True)
        self.assertEqual((out["sent"], out["thread_id"], out["verified"]), ("S1", "c9", True))
        self.assertEqual(set(out) - {"verified", "verification"}, {"sent", "to", "subject", "thread_id"})  # google gmail_send's keys
        self.assertEqual(len(polls), 2)

    def test_send_reply_uses_create_reply_and_keeps_thread(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            if path.endswith("/createReply"):
                return {"id": "D1", "conversationId": "c1"}
            if path == "/me/mailFolders/sentitems/messages":
                return {"value": [{"id": "S2", "conversationId": "c1", "subject": "Re: Hi", "toRecipients": [{"emailAddress": {"address": "ann@x.com"}}]}]}
            return None
        ms = FakeMs(h)
        out = ms.gmail_send("ann@x.com", "Re: Hi", "yes", reply_to_message_id="orig")
        self.assertEqual(len(ms.find("POST", "/me/messages/D1/send")), 1)
        self.assertEqual(ms.find("PATCH", "/me/messages/D1")[0]["json"]["body"]["content"], "yes")
        self.assertTrue(out["verified"])
        self.assertEqual(out["thread_id"], "c1")

    def test_send_unverified_when_never_in_sent_items(self) -> None:
        ms = FakeMs(lambda m, p, kw: {"value": []} if m == "GET" else None)
        out = ms.gmail_send("ann@x.com", "Hi", "hello")
        self.assertFalse(out["verified"])
        self.assertIsNone(out["sent"])
        self.assertEqual(len(ms.find("POST", "/me/sendMail")), 1)  # sent once; the failed proof is never a retry

    def test_draft_verified_by_readback(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            if method == "POST":
                return {"id": "D1"}
            return {"subject": "Hi", "toRecipients": [{"emailAddress": {"address": "ann@x.com"}}], "isDraft": True}
        out = FakeMs(h).gmail_draft("ann@x.com", "Hi", "body")
        self.assertEqual(set(out) - {"verified", "verification"}, {"draft_id", "to", "subject", "note"})
        self.assertTrue(out["verified"])

    def test_threads_group_by_conversation_and_feed_mailwatch(self) -> None:
        rows = [msg("m1", conv="c1", when="2026-10-01T10:00:00Z", preview="hello"),
                msg("m2", conv="c1", when="2026-10-02T10:00:00Z", frm="me@x.com", to="ann@x.com", folder="F-sent", preview="sure"),
                msg("m3", conv="c2", when="2026-10-03T10:00:00Z", preview="can you confirm?",
                    internetMessageHeaders=[{"name": "List-Unsubscribe", "value": "<x>"}]),
                msg("m4", conv="c3", folder="F-junk"), msg("m5", conv="c4", isDraft=True)]
        ms = FakeMs(lambda m, p, kw: {"value": rows})
        threads = ms.gmail_threads_recent()
        self.assertEqual([t["thread_id"] for t in threads], ["c2", "c1"])
        self.assertEqual({k for t in threads for k in t}, {"thread_id", "subject", "messages"})
        self.assertEqual({k for t in threads for m in t["messages"] for k in m}, GMAIL_THREAD_MSG_KEYS)
        c1 = threads[1]["messages"]
        self.assertEqual([m["id"] for m in c1], ["m1", "m2"])
        self.assertIn("SENT", c1[1]["labels"])
        self.assertTrue(threads[0]["messages"][0]["auto"])
        self.assertTrue(ms.find("GET", "/me/messages")[0]["params"]["$filter"].startswith("receivedDateTime ge "))
        # mailwatch reads exactly these keys
        out = mailwatch.classify(threads[1], "me@x.com", dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc), mailwatch.DEFAULT_CONFIG)
        self.assertIn(out["status"], mailwatch.STATUSES)

    def test_labels_folders_then_categories(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            return {"value": [{"displayName": "Receipts"}, {"displayName": "Clients"}]} if path == "/me/outlook/masterCategories" else None
        labels = FakeMs(h).gmail_labels()
        self.assertEqual({k for l in labels for k in l}, {"id", "name", "type"})  # google's gmail_labels keys
        self.assertEqual([l["id"] for l in labels if l["type"] == "system"], ["ARCHIVE", "DRAFT", "INBOX", "SENT", "SPAM", "TRASH"])  # sorted by name, like Google
        self.assertEqual([l["name"] for l in labels if l["type"] == "user"], ["Clients", "Receipts"])

    def test_label_query_resolves_hyphenated_category(self) -> None:
        def h(method: str, path: str, kw: dict[str, Any]) -> Any:
            return {"value": [{"displayName": "Big Clients"}]} if path == "/me/outlook/masterCategories" else {"value": []}
        ms = FakeMs(h)
        ms.gmail_search("label:Big-Clients")
        self.assertIn("categories/any(c:c eq 'Big Clients')", ms.find("GET", "/me/messages")[0]["params"]["$filter"])


if __name__ == "__main__":
    unittest.main()
