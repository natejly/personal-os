"""Stored MCP OAuth tokens keep their expiry across a restart, so an expired access token is refreshed, not re-signed-in."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.shared.auth import OAuthToken  # noqa: E402

from personal_os import mcp_oauth, mcp_servers  # noqa: E402
from personal_os.db import Database  # noqa: E402


def test_restored_provider_knows_when_the_stored_token_expires(tmp_path: Path) -> None:
    db = Database(tmp_path / "data")
    with db.tx() as c:
        c.executescript(mcp_servers.SCHEMA)
        c.execute("INSERT INTO mcp_servers(id, slug, name, created_at, updated_at) VALUES('s1', 's1', 'S1', 0, 0)")
    store = mcp_oauth.OAuthStore(db)
    assert store.token_expiry("s1") is None
    store.set_tokens("s1", OAuthToken(access_token="a", refresh_token="r", expires_in=3600))
    with db.tx() as c:  # written long ago: long expired
        c.execute("UPDATE mcp_oauth SET updated_at=? WHERE server_id=?", (1000.0, "s1"))
    assert store.token_expiry("s1") == 4600.0

    prov = mcp_oauth.OAuthFlows(store).provider("s1", "https://mcp.example.test/mcp")
    asyncio.run(prov._initialize())
    assert prov.context.token_expiry_time == 4600.0 and not prov.context.is_token_valid()

    store.set_tokens("s1", OAuthToken(access_token="b"))  # no expires_in: valid until the server says otherwise
    asyncio.run(prov._initialize())
    assert prov.context.token_expiry_time is None and prov.context.is_token_valid()
