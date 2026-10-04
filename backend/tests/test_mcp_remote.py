"""Remote (streamable HTTP) connectors through the API: checking a draft, refusing sse, header secrets."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcpremote-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.mcp_client import _config_from  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def test_check_probes_a_remote_draft_with_its_url_and_headers(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []

    async def probe(cfg: dict[str, Any], **_k: Any) -> dict[str, Any]:
        seen.append(cfg)
        return {"ok": True, "error": "", "server_info": {}, "tools": [], "stderr": []}

    monkeypatch.setattr(appmod.mcp, "probe", probe)
    r = client.post("/mcp/check", json={"transport": "http", "url": "https://example.test/mcp",
                                        "headers": {"Authorization": "Bearer t0k"}})
    assert r.status_code == 200, r.text
    assert seen[0]["transport"] == "http"
    assert seen[0]["url"] == "https://example.test/mcp"
    assert seen[0]["headers"] == {"Authorization": "Bearer t0k"}


def test_sse_is_refused_everywhere() -> None:
    r = client.post("/mcp/servers", json={"name": "Old", "transport": "sse", "url": "https://example.test/sse"})
    assert r.status_code == 400 and "streamable HTTP" in r.json()["detail"]
    assert client.post("/mcp/check", json={"transport": "sse", "url": "https://example.test/sse"}).status_code == 400
    sid = client.post("/mcp/servers", json={"name": "Local", "command": "true", "enabled": False}).json()["id"]
    assert client.patch(f"/mcp/servers/{sid}", json={"transport": "sse"}).status_code == 400


def test_header_values_are_stored_as_secrets() -> None:
    r = client.post("/mcp/servers", json={"name": "Remote", "transport": "http", "url": "https://example.test/mcp",
                                          "headers": {"Authorization": "Bearer s3cret", "X-Team": "blue"},
                                          "enabled": False})
    assert r.status_code == 200, r.text
    view = r.json()
    assert "s3cret" not in r.text and "blue" not in r.text
    assert view["secret_keys"] == ["Authorization", "X-Team"]
    assert view["headers"] == {"Authorization": "", "X-Team": ""}
    # The connection still sends the real values.
    cfg = _config_from(appmod.mcp_store, view["id"])
    assert cfg is not None and cfg.headers == {"Authorization": "Bearer s3cret", "X-Team": "blue"}

    # Editing: an empty value keeps the stored one, a dropped header drops its secret too.
    r = client.patch(f"/mcp/servers/{view['id']}", json={"headers": {"Authorization": ""}})
    assert r.json()["secret_keys"] == ["Authorization"]
    cfg = _config_from(appmod.mcp_store, view["id"])
    assert cfg is not None and cfg.headers == {"Authorization": "Bearer s3cret"}
