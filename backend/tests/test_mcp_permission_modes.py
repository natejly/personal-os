"""Connector calls under the permission modes: taint keeps a card even in allow-all, a self-declared destructive tool
needs a confident untainted allow from the auto reviewer, and the tool_call event says which connector asks.

Scripted model, stubbed connector call and reviewer, no network. Run: python backend/tests/test_mcp_permission_modes.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpmodes-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import autoreview, llm  # noqa: E402

CALLED: list[str] = []
ROUNDS: list[list[dict[str, Any]]] = []
VERDICT: dict[str, str] = {"verdict": "allow", "confidence": "high"}


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


async def _fake_call(slug: str, args: dict[str, Any]) -> Any:
    CALLED.append(slug)
    return {"content": "third-party text"}


async def _fake_review(*a: Any, **k: Any) -> dict[str, Any]:
    return {**VERDICT, "reason": "test", "model": "m", "ms": 0}


appmod._mcp_call = _fake_call
autoreview.review = _fake_review  # type: ignore[assignment]
_sid = appmod.mcp_store.create_server("alpha", command="x")["id"]
_empty = {"type": "object", "properties": {}}
appmod.mcp_store.sync_tools(_sid, [
    {"name": "read", "description": "read", "parameters": _empty, "annotations": {"readOnlyHint": True}},
    {"name": "wipe", "description": "wipe", "parameters": _empty, "annotations": {"destructiveHint": True}},
    {"name": "send", "description": "send", "parameters": _empty},
])
SLUGS = {t["name"]: t["slug"] for t in appmod.mcp_store.tools()}
appmod.mcp.ready_slugs = lambda: sorted(SLUGS.values())  # type: ignore[method-assign]


def reply(pmode: str, calls: list[str]) -> list[dict[str, Any]]:
    """One reply whose model calls `calls` in successive rounds. Stops at the first approval card."""
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workspaceRoots": [], "permissionMode": pmode,
                            "permissionRules": {"allow": [], "ask": [], "deny": []}})
    cid = appmod.convos.create(None, "t", "m")["id"]
    CALLED.clear()
    ROUNDS[:] = [[{"id": f"c{i}", "name": SLUGS[n], "arguments": json.dumps({})}] for i, n in enumerate(calls)]
    out: list[dict[str, Any]] = []

    async def go() -> None:
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event()):
            if ev[0] == "tool_call":
                out.append(ev[1])
                if ev[1]["needs_approval"]:
                    return

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    return out


def test_allow_all_runs_until_a_connector_result_taints_the_reply() -> None:
    ev = reply("allow_all", ["read", "send"])
    assert [e["needs_approval"] for e in ev] == [False, True]  # read ran, its result tainted the reply, send asks
    assert CALLED == [SLUGS["read"]]


def test_event_names_the_connector_and_its_claims() -> None:
    ev = reply("allow_all", ["wipe"])
    assert ev[0]["mcp"] == {"server": "alpha", "read_only": False, "destructive": True}


def test_auto_reviews_a_destructive_tool_strictly() -> None:
    VERDICT.update(verdict="allow", confidence="medium")
    try:
        assert reply("auto", ["send"])[0]["needs_approval"] is False  # plain review: an allow runs
        assert reply("auto", ["wipe"])[0]["needs_approval"] is True   # strict: medium confidence is not enough
        VERDICT.update(confidence="high")
        assert reply("auto", ["wipe"])[0]["needs_approval"] is False
    finally:
        VERDICT.update(verdict="allow", confidence="high")


def test_read_only_claim_never_skips_manual_ask() -> None:
    assert reply("manual", ["read"])[0]["needs_approval"] is True


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
