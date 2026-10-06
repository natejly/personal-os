"""The SSE transport: an sse server's config reaches mcp.client.sse with its headers, timeouts and sign-in. No network.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_sse.py -q
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from personal_os import mcp_client
from personal_os.db import Database
from personal_os.mcp_client import McpClient, McpUnavailable
from personal_os.mcp_servers import McpServers


class FakeOAuth:
    def provider(self, server_id: str, url: str, redirect_uri: Any, sign_in: Any) -> str:
        return f"auth-for-{server_id}"


def cfg(**over: Any) -> mcp_client._Config:
    base: dict[str, Any] = dict(id="srv1", slug="s", name="s", transport="sse", command="", args=[], cwd="", env={},
                                url="https://mcp.example.com/sse", headers={"X-Key": "test-token-123"})
    return mcp_client._Config(**{**base, **over})


def test_an_sse_config_reaches_sse_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def fake_sse(url: str, **kw: Any) -> Any:
        seen.update(url=url, **kw)
        yield ("read", "write")
    monkeypatch.setattr(mcp_client, "sse_client", fake_sse)

    async def run() -> Any:
        err = mcp_client._Stderr([])
        try:
            async with mcp_client._open(cfg(), err, FakeOAuth()) as streams:  # type: ignore[arg-type]
                return streams
        finally:
            err.close()
    assert asyncio.run(run()) == ("read", "write")
    assert seen == {"url": "https://mcp.example.com/sse", "headers": {"X-Key": "test-token-123"},
                    "timeout": mcp_client.SSE_CONNECT_TIMEOUT, "sse_read_timeout": mcp_client.SSE_READ_TIMEOUT, "auth": "auth-for-srv1"}


def test_an_sse_server_without_a_url_is_unavailable_and_other_transports_still_refused() -> None:
    async def opened(c: mcp_client._Config) -> None:
        err = mcp_client._Stderr([])
        try:
            async with mcp_client._open(c, err, None):
                pass
        finally:
            err.close()
    with pytest.raises(McpUnavailable, match="no URL"):
        asyncio.run(opened(cfg(url="")))
    with pytest.raises(McpUnavailable, match="not supported"):
        asyncio.run(opened(cfg(transport="carrier-pigeon")))


def test_sse_servers_may_sign_in(tmp_path: Any) -> None:
    store = McpServers(Database(tmp_path))
    row = store.create_server("Remote", transport="sse", url="https://mcp.example.com/sse")
    assert row["transport"] == "sse"
    started: list[str] = []

    class Flows(FakeOAuth):
        async def begin(self, sid: str, run: Any) -> None:
            started.append(sid)

        def status(self, sid: str) -> dict[str, Any]:
            return {"state": "pending"}
    client = McpClient(store, oauth=Flows())  # type: ignore[arg-type]
    assert asyncio.run(client.sign_in(row["id"], "http://127.0.0.1:1/cb")) == {"state": "pending"} and started == [row["id"]]
    stdio = store.create_server("Local", command="x")
    with pytest.raises(McpUnavailable, match="remote"):
        asyncio.run(client.sign_in(stdio["id"], "http://127.0.0.1:1/cb"))


def test_the_check_and_save_routes_accept_sse() -> None:
    import os
    import tempfile
    os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpsse-"))
    from fastapi import HTTPException
    from personal_os import app as appmod
    appmod._mcp_transport_ok("sse")
    appmod._mcp_transport_ok("http")
    appmod._mcp_transport_ok(None)
    with pytest.raises(HTTPException):
        appmod._mcp_transport_ok("carrier-pigeon")
