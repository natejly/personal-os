"""setup_request cards, and the connect card a Google tool returns when no account is connected.

Run: python backend/tests/test_setup_cards.py
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calendar_ext import calendar_box  # noqa: E402
from mail_ext import mail_tools  # noqa: E402
from personal_os.extensions.google.client import GoogleNotConnected  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


class StubGoogle:
    def __init__(self, **st: Any):
        self.st = {"connected": False, "needs_reauth": False, "reauth_reason": None, **st}

    def status(self) -> dict[str, Any]:
        return self.st

    def __getattr__(self, name: str) -> Any:  # any Google read: no account
        if name == "classify_error":
            raise AttributeError(name)
        def fail(*a: Any, **k: Any) -> Any:
            raise GoogleNotConnected("Google account not connected")
        return fail


def box(google: StubGoogle, cfg: dict[str, Any] | None = None) -> Toolbox:
    return calendar_box(google, settings=cfg)


def call(tb: Toolbox, name: str, **args: Any) -> Any:
    return asyncio.run(tb.call(name, args, {"project_id": "p", "conversation_id": "c"}))


class SetupCards(unittest.TestCase):
    def test_unconfigured_returns_card(self) -> None:
        r = call(box(StubGoogle()), "setup_request", feature="google", reason="to read your calendar")
        self.assertFalse(r["configured"])
        self.assertEqual((r["setup"]["action"], r["setup"]["why"]), ("google", "to read your calendar"))
        self.assertFalse(r["status"]["google"])

    def test_configured_says_so(self) -> None:
        r = call(box(StubGoogle(connected=True), {"braveApiKey": "k"}), "setup_request", feature="web_search")
        self.assertTrue(r["configured"])
        self.assertNotIn("setup", r)
        self.assertTrue(call(box(StubGoogle(connected=True)), "setup_request", feature="google")["configured"])

    def test_settings_target_opens_tab(self) -> None:
        r = call(box(StubGoogle()), "setup_request", feature="telegram")
        self.assertEqual((r["setup"]["action"], r["setup"]["tab"]), ("settings", "integrations"))

    def test_unknown_feature(self) -> None:
        self.assertIn("error", call(box(StubGoogle()), "setup_request", feature="nope"))

    def test_google_tool_without_account_carries_card(self) -> None:
        r = call(box(StubGoogle()), "calendar_events")
        self.assertIn("not connected", r["error"])
        self.assertEqual((r["setup"]["feature"], r["setup"]["action"]), ("google", "google"))
        self.assertNotIn("reauth", r["setup"])

    def test_expired_sign_in_offers_reconnect(self) -> None:
        r = call(mail_tools(StubGoogle(connected=True, needs_reauth=True, reauth_reason="Sign-in expired."))[0], "gmail_search", query="x")
        self.assertTrue(r["setup"]["reauth"])
        self.assertEqual(r["setup"]["title"], "Reconnect Google")


if __name__ == "__main__":
    unittest.main()
