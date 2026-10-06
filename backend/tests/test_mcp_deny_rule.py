"""A global deny rule can name an MCP slug (or its server) and wins over an `on` grant, with skip-permissions too.

Scripted model, stubbed connector call, no network. Run: python backend/tests/test_mcp_deny_rule.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpdeny-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, permrules  # noqa: E402

CALLED: list[str] = []
ROUNDS: list[list[dict[str, Any]]] = []


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


async def _fake_call(slug: str, args: dict[str, Any]) -> Any:
    CALLED.append(slug)
    return {"ok": True}


appmod._mcp_call = _fake_call
_sid = appmod.mcp_store.create_server("alpha", command="x")["id"]
appmod.mcp_store.sync_tools(_sid, [{"name": "send", "description": "send", "parameters": {"type": "object", "properties": {}}}])
SLUG = appmod.mcp_store.tools()[0]["slug"]
appmod.mcp.ready_slugs = lambda: [SLUG]  # type: ignore[method-assign]


def reply(rules: dict[str, list[str]], grant: str | None, skip: bool, stop_at_card: bool = False) -> list[dict[str, Any]]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workspaceRoots": [], "permissionMode": "allow_all" if skip else "manual",
                            "permissionRules": {"allow": [], "ask": [], "deny": [], **rules}})
    appmod.mcp_store.clear_grant(SLUG)
    if grant:
        appmod.mcp_store.set_grant(SLUG, grant)
    cid = appmod.convos.create(None, "t", "m")["id"]
    CALLED.clear()
    ROUNDS[:] = [[{"id": "c0", "name": SLUG, "arguments": json.dumps({})}]]
    out: list[dict[str, Any]] = []

    async def go() -> None:
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event()):
            if ev[0] in ("tool_call", "tool_result"):
                out.append({"event": ev[0], **ev[1]})
            if stop_at_card and ev[0] == "tool_call":
                return

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    return out


def test_deny_beats_on_grant_and_leaves_the_grant() -> None:
    reply({}, "on", False)
    before = appmod.mcp_store.grants(SLUG)
    ev = reply({"deny": [SLUG]}, "on", False)
    key = lambda gs: [(g["scope"], g["mode"], g["schema_hash"]) for g in gs]  # noqa: E731
    assert not CALLED and key(appmod.mcp_store.grants(SLUG)) == key(before) and before[0]["mode"] == "on"
    assert any(e["event"] == "tool_result" and "permission rule" in json.dumps(e) for e in ev)


def test_server_deny_and_skip_permissions() -> None:
    for rule in (SLUG, "mcp__alpha"):
        for grant in ("on", None):
            reply({"deny": [rule]}, grant, True)
            assert not CALLED, (rule, grant)
        assert not appmod.mcp_store.grants(SLUG)


def test_no_deny_with_grant_runs() -> None:
    reply({}, "on", False)
    assert CALLED == [SLUG]
    reply({"deny": ["mcp__other"]}, "on", False)
    assert CALLED == [SLUG]


def test_allow_rule_without_grant_still_asks_and_creates_no_grant() -> None:
    ev = reply({"allow": [SLUG]}, None, False, stop_at_card=True)
    assert ev and ev[0]["needs_approval"] and not CALLED and appmod.mcp_store.grants(SLUG) == []


def test_only_deny_rules_and_only_mcp_slugs() -> None:
    assert permrules.mcp_denied("mcp__alpha__send", {"allow": ["mcp__alpha__send"], "ask": ["mcp__alpha"]}) is None
    assert permrules.mcp_denied("mcp__alpha__send", {"deny": ["mcp__alpha_other"]}) is None
    assert permrules.mcp_denied("mcp__alpha__send", {"deny": ["mcp__alpha"]})


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
