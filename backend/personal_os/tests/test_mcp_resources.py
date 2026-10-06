"""Resources and prompts from a real stub server: listed on connect, readable through the synthetic read tool.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_resources.py -q
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from personal_os import mcp_client
from personal_os.db import Database
from personal_os.mcp_client import McpClient, stub_config
from personal_os.mcp_servers import McpServers

SLUG = "mcp__rich__grain_read_resource"


async def connected(tmp_path: Any, mode: str) -> tuple[McpClient, McpServers, str]:
    store = McpServers(Database(tmp_path))
    row = store.create_server("Rich", **stub_config(mode))
    client = McpClient(store, connect_timeout=20)
    await client.sync()
    assert await client.wait_ready(20)
    return client, store, row["id"]


def test_resources_and_prompts_are_listed_and_the_read_tool_works(tmp_path: Any) -> None:
    async def run() -> None:
        client, store, sid = await connected(tmp_path, "rich")
        try:
            info = client.status(sid)[0]
            assert info["resources"] == [{"uri": "note://hello", "name": "hello", "description": "A greeting note.", "mime_type": "text/plain"}]
            assert info["prompts"][0]["name"] == "summarize"
            assert info["prompts"][0]["arguments"] == [{"name": "text", "required": True}, {"name": "style", "required": False}]
            tool = store.tool(SLUG)
            assert tool and tool["name"] == mcp_client.READ_RESOURCE_TOOL and tool["danger"] == "external"
            assert store.effective_mode(SLUG)["mode"] == "ask", "reading a resource goes through the same grants as any tool"
            listed = await client.call(SLUG, {})
            assert "note://hello" in listed["content"]
            read = await client.call(SLUG, {"uri": "note://hello"})
            assert "hello from a resource" in read["content"] and "note://hello" in read["content"]
            with pytest.raises(mcp_client.McpError):  # an unknown URI is a failure the model can read
                await client.call(SLUG, {"uri": "note://missing"})
        finally:
            await client.stop()
    asyncio.run(run())


def test_a_server_without_resources_gets_no_read_tool(tmp_path: Any) -> None:
    async def run() -> None:
        store = McpServers(Database(tmp_path))
        row = store.create_server("Plain", **stub_config("friendly"))
        client = McpClient(store, connect_timeout=20)
        await client.sync()
        assert await client.wait_ready(20)
        try:
            assert client.status(row["id"])[0]["resources"] == [] and client.status(row["id"])[0]["prompts"] == []
            assert all(t["name"] != mcp_client.READ_RESOURCE_TOOL for t in store.tools())
        finally:
            await client.stop()
    asyncio.run(run())


def test_the_read_tool_text_is_fixed_so_a_growing_list_never_changes_its_hash() -> None:
    a, b = mcp_client.read_resource_export(), mcp_client.read_resource_export()
    assert a == b and ":" in a["name"], "':' is not a legal MCP tool name, so no server can export a colliding tool"


def test_the_stub_marks_destructive_and_read_only_tools(tmp_path: Any) -> None:
    async def run() -> None:
        client, store, sid = await connected(tmp_path, "rich")
        try:
            assert store.tool("mcp__rich__wipe")["destructive"] and store.tool("mcp__rich__look")["read_only"]
            assert not store.tool("mcp__rich__look")["destructive"]
        finally:
            await client.stop()
    t0 = time.time()
    asyncio.run(run())
    assert time.time() - t0 < 60
