"""mcp_oauth: the browser leg and the background leg of a remote server's sign-in."""
from __future__ import annotations

import asyncio
import tempfile
import unittest

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from personal_os.db import Database
from personal_os.mcp_oauth import McpNeedsAuth, OAuthFlows, OAuthStore
from personal_os.mcp_servers import McpServers


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db = Database(tempfile.mkdtemp(prefix="mcpoauth-"))
        self.sid = McpServers(db).create_server("COROS", transport="http", url="https://example.test/mcp")["id"]
        self.store = OAuthStore(db)
        self.flows = OAuthFlows(self.store)

    async def test_storage_round_trip_and_forget(self) -> None:
        self.assertFalse(self.store.signed_in(self.sid))
        self.store.set_tokens(self.sid, OAuthToken(access_token="a", token_type="Bearer", refresh_token="r"))
        self.assertEqual(self.store.tokens(self.sid).refresh_token, "r")  # type: ignore[union-attr]
        self.assertTrue(self.store.signed_in(self.sid))
        self.store.forget(self.sid)
        self.assertIsNone(self.store.tokens(self.sid))

    async def test_background_provider_never_opens_a_browser(self) -> None:
        p = self.flows.provider(self.sid, "https://example.test/mcp")
        with self.assertRaises(McpNeedsAuth):
            await p.context.redirect_handler("https://auth.example.test/authorize?state=s1")  # type: ignore[misc]

    async def test_browser_round_trip_delivers_the_code(self) -> None:
        got: list[str] = []

        async def run(s) -> None:  # type: ignore[no-untyped-def]
            p = self.flows.provider(self.sid, "https://example.test/mcp", "http://127.0.0.1:9/mcp/oauth/callback", s)
            await p.context.redirect_handler("https://auth.example.test/authorize?client_id=x&state=abc")  # type: ignore[misc]
            res = await p.context.callback_handler()  # type: ignore[misc]
            got.append(res.code)

        s = await self.flows.begin(self.sid, run)
        st = self.flows.status(self.sid)
        self.assertEqual((st["status"], st["auth_url"].endswith("state=abc")), ("waiting", True))
        self.assertFalse(self.flows.complete("wrong-state", "c", None))  # an unknown state completes nothing
        self.assertTrue(self.flows.complete("abc", "the-code", None))
        await asyncio.wait_for(s.task, 5)  # type: ignore[arg-type]
        self.assertEqual(got, ["the-code"])
        self.assertEqual(self.flows.status(self.sid)["status"], "done")
        self.assertFalse(self.flows.complete("abc", "again", None))  # single use

    async def test_denied_sign_in_reports_error(self) -> None:
        async def run(s) -> None:  # type: ignore[no-untyped-def]
            p = self.flows.provider(self.sid, "https://example.test/mcp", "http://127.0.0.1:9/cb", s)
            await p.context.redirect_handler("https://auth.example.test/authorize?state=d")  # type: ignore[misc]
            await p.context.callback_handler()  # type: ignore[misc]

        s = await self.flows.begin(self.sid, run)
        self.flows.complete("d", None, "access_denied")
        await asyncio.wait_for(s.task, 5)  # type: ignore[arg-type]
        st = self.flows.status(self.sid)
        self.assertEqual(st["status"], "error")
        self.assertIn("access_denied", st["error"])

    async def test_registration_for_another_redirect_is_dropped(self) -> None:
        self.store.set_client(self.sid, OAuthClientInformationFull(client_id="old", redirect_uris=["http://127.0.0.1:1111/mcp/oauth/callback"]))  # type: ignore[list-item]
        self.flows.provider(self.sid, "https://example.test/mcp", "http://127.0.0.1:2222/mcp/oauth/callback", sign_in=object())  # type: ignore[arg-type]
        self.assertIsNone(self.store.client(self.sid))


if __name__ == "__main__":
    unittest.main()
