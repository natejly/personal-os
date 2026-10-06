"""A connected MCP server's own notes reach the system prompt only while one of its tools is offered.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_mcp_server_notes.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest.mock
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpnotes-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
NOTE = "Call list_notebooks before read_note."
SEEN: list[list[dict[str, Any]]] = []


async def _stream(settings: Any, model: str, messages: list[dict[str, Any]], tools: Any = None, **_: Any) -> Any:
    SEEN.append([dict(m) for m in messages])
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _system_text(ready: list[str], notes_on: bool = True) -> str:
    for row in appmod.mcp_store.servers():
        appmod.mcp_store.delete_server(row["id"])
    srv = appmod.mcp_store.create_server(name="Notebook", command="/bin/true")
    appmod.mcp_store.sync_tools(srv["id"], [{"name": "read_note", "description": "Read a note.", "parameters": {"type": "object"}}])
    appmod.db.set_settings({"mcpServerNotes": notes_on})
    live = [{"server_id": srv["id"], "name": "Notebook", "status": "ready", "server_info": {"instructions": NOTE}}]
    cid = appmod.convos.create(None, "t", "m")["id"]

    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="hi"), asyncio.Event()):
            pass

    SEEN.clear()
    with unittest.mock.patch.object(appmod.mcp, "ready_slugs", lambda: list(ready)), \
            unittest.mock.patch.object(appmod.mcp, "status", lambda server_id=None: live), \
            unittest.mock.patch.object(llm, "stream_chat", _stream):
        asyncio.run(go())
    return "\n".join(m["content"] for m in SEEN[0] if m["role"] == "system" and isinstance(m.get("content"), str))


def test_notes_ride_along_while_a_tool_of_that_server_is_ready() -> None:
    text = _system_text(["mcp__notebook__read_note"])
    assert "<<<CONNECTOR NOTES: Notebook>>>" in text and NOTE in text


def test_no_ready_tool_no_notes() -> None:
    assert NOTE not in _system_text([])


def test_the_setting_turns_them_off() -> None:
    assert NOTE not in _system_text(["mcp__notebook__read_note"], notes_on=False)
