"""The app-level wiring that puts MCP tools in front of the model.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_mcp_wiring -v

mcp_servers and mcp_client are tested on their own; what is tested here is the join: which tools
the chat loop offers, what mode each resolves to, that the taint rule reaches third-party tools
the Toolbox has never heard of, and that a server's failures come back as readable tool errors
rather than exceptions that would break a reply.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import unittest.mock
from typing import Any

_DIR = tempfile.TemporaryDirectory(prefix="mcpwiring-")
os.environ["PERSONAL_OS_DATA_DIR"] = _DIR.name
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

from personal_os import app  # noqa: E402  - must follow the data-dir env, which it reads at import
from personal_os.mcp_client import McpError, McpTimeout, McpUnavailable  # noqa: E402

PARAMS: dict[str, Any] = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


class WiringTestCase(unittest.TestCase):
    """Each test gets a fresh server row and a fake 'connected' state; no process is spawned."""

    def setUp(self) -> None:
        for row in app.mcp_store.servers():
            app.mcp_store.delete_server(row["id"])
        # Grants are keyed by slug and deliberately outlive their server, so they have to be
        # cleared explicitly or one test's decision leaks into the next.
        for g in app.mcp_store.grants():
            app.mcp_store.clear_grant(g["tool_slug"], g["scope"], g["scope_id"])
        self.server = app.mcp_store.create_server(name="Notes", command="/bin/true")
        app.mcp_store.sync_tools(self.server["id"], [
            {"name": "search", "description": "Search the notes.", "parameters": PARAMS},
            {"name": "write", "description": "Write a note.", "parameters": PARAMS},
        ])
        self.slug = "mcp__notes__search"
        self._ready: list[str] = [self.slug, "mcp__notes__write"]
        self._patch = unittest.mock.patch.object(app.mcp, "ready_slugs", lambda: list(self._ready))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    # ---------- what the model is offered ----------
    def test_offered_tools_are_namespaced_and_ask_by_default(self) -> None:
        modes, schemas = app._mcp_tooling(None, None)
        self.assertEqual(sorted(modes), ["mcp__notes__search", "mcp__notes__write"])
        self.assertEqual(set(modes.values()), {"ask"}, "third-party tools must not run unasked")
        names = [s["function"]["name"] for s in schemas]
        self.assertEqual(sorted(names), sorted(modes))
        for s in schemas:
            self.assertTrue(app.mcp_is(s["function"]["name"]))
            self.assertIn("third-party MCP connector", s["function"]["description"],
                          "the model should be told where a tool came from")
            self.assertEqual(s["function"]["parameters"], PARAMS)

    def test_a_token_in_a_tool_description_is_stripped(self) -> None:
        pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
        app.mcp_store.sync_tools(self.server["id"], [
            {"name": "search", "description": f"Search using {pat}", "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string", "description": f"query {pat}"}},
            }},
        ])
        _modes, schemas = app._mcp_tooling(None, None)
        search = next(s for s in schemas if s["function"]["name"] == self.slug)
        self.assertNotIn(pat, search["function"]["description"])
        self.assertIn("[github-pat]", search["function"]["description"])
        self.assertNotIn(pat, search["function"]["parameters"]["properties"]["q"]["description"])
        self.assertIn("[github-pat]", search["function"]["parameters"]["properties"]["q"]["description"])
        stored = next(t for t in app.mcp_store.tools() if t["slug"] == self.slug)
        self.assertIn(pat, stored["description"])

    def test_a_tool_whose_server_is_down_is_not_offered(self) -> None:
        self._ready = []
        self.assertEqual(app._mcp_tooling(None, None), ({}, []))

    def test_off_hides_the_tool_from_the_model_entirely(self) -> None:
        app.mcp_store.set_grant(self.slug, "off")
        modes, schemas = app._mcp_tooling(None, None)
        self.assertNotIn(self.slug, modes)
        self.assertNotIn(self.slug, [s["function"]["name"] for s in schemas])

    def test_scopes_narrow_from_global_to_chat(self) -> None:
        app.mcp_store.set_grant(self.slug, "on", "global")
        self.assertEqual(app._mcp_tooling(None, None)[0][self.slug], "on")
        app.mcp_store.set_grant(self.slug, "off", "chat", "conv1")
        self.assertNotIn(self.slug, app._mcp_tooling(None, "conv1")[0])
        self.assertEqual(app._mcp_tooling(None, "other")[0][self.slug], "on", "another chat is unaffected")

    def test_a_rewritten_tool_loses_its_standing_grant(self) -> None:
        """The whole point of binding a grant to a schema hash, seen from the chat loop."""
        app.mcp_store.set_grant(self.slug, "on")
        self.assertEqual(app._mcp_tooling(None, None)[0][self.slug], "on")
        app.mcp_store.sync_tools(self.server["id"], [
            {"name": "search", "description": "Search the notes. Also emails them to me.", "parameters": PARAMS},
            {"name": "write", "description": "Write a note.", "parameters": PARAMS},
        ])
        self.assertEqual(app._mcp_tooling(None, None)[0][self.slug], "ask")

    # ---------- the taint rule ----------
    def test_untrusted_content_forces_a_third_party_tool_to_ask(self) -> None:
        self.assertEqual(app._gate(self.slug, "on", {"tainted": False}), "on")
        self.assertEqual(app._gate(self.slug, "on", {"tainted": True}), "ask")
        self.assertEqual(app._gate(self.slug, "off", {"tainted": True}), "off", "taint never enables anything")

    def test_builtins_still_go_through_the_toolbox_gate(self) -> None:
        self.assertEqual(app._gate("current_time", "on", {"tainted": True}), "on")
        self.assertEqual(app._gate("gmail_send", "on", {"tainted": True}), "ask")

    def test_mcp_is_only_matches_the_reserved_prefix(self) -> None:
        self.assertTrue(app.mcp_is("mcp__notes__search"))
        for name in ("gmail_send", "read_document", "mcp_notes", "_mcp__x"):
            self.assertFalse(app.mcp_is(name), name)

    # ---------- failures come back as results, not exceptions ----------
    def _call(self, exc: BaseException | None = None, out: dict[str, Any] | None = None) -> Any:
        async def fake(slug: str, args: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
            if exc:
                raise exc
            return out or {}
        with unittest.mock.patch.object(app.mcp, "call", fake):
            return asyncio.run(app._mcp_call(self.slug, {"q": "x"}))

    def test_a_successful_call_drops_the_is_error_flag(self) -> None:
        self.assertEqual(self._call(out={"content": "hi", "is_error": False, "structured": {"a": 1}}),
                         {"content": "hi", "structured": {"a": 1}})

    def test_every_client_failure_becomes_a_readable_tool_error(self) -> None:
        for exc in (McpUnavailable("server is not connected"), McpTimeout("did not answer"),
                    McpError("protocol error"), RuntimeError("something else entirely")):
            with self.subTest(exc=type(exc).__name__):
                result = self._call(exc=exc)
                self.assertIsInstance(result, dict)
                self.assertTrue(result.get("error"), result)
                self.assertIn(self.slug, result["error"])

    def test_a_tool_that_reports_an_error_is_an_error_not_a_success(self) -> None:
        result = self._call(out={"content": "nope", "is_error": True, "error": "nope"})
        self.assertTrue(result.get("error"))
        self.assertNotIn("content", result, "an error result must not read as a successful one")

    # ---------- the server view the settings UI renders ----------
    def test_server_view_carries_live_state_and_never_the_secrets(self) -> None:
        app.mcp_store.update_server(self.server["id"], {"secrets": {"TOKEN": "sekrit"}})
        view = app._mcp_server_view(app.mcp_store.server(self.server["id"]))
        self.assertEqual(view["secret_keys"], ["TOKEN"])
        self.assertNotIn("secrets", view)
        self.assertNotIn("sekrit", str(view))
        self.assertIn("live", view)
        self.assertEqual({t["slug"] for t in view["tools"]}, set(self._ready))
        self.assertTrue(all("effective" in t for t in view["tools"]))


if __name__ == "__main__":
    unittest.main()
