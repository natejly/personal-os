"""Catalog binary detection ({bin}, {grain_python}) and the OpenCode shim's pure parts (no OpenCode, no network).

Run: timeout 300 uv run pytest tests/test_mcp_connectors.py -x -q
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from personal_os import mcp_catalog, opencode_mcp  # noqa: E402


def entry(**over: object) -> dict:
    e = {"id": "demo", "name": "Demo", "description": "d", "category": "Developer", "icon": "code", "publisher": "p", "official": False,
         "docs": "https://example.com", "transport": "stdio", "runtime": "binary", "auth": "none", "fields": [],
         "detect": {"binary": "demo-bin", "dirs": [], "hint": "Install demo"},
         "install": {"command": "{bin}", "args": ["serve"], "env": {}, "url": "", "headers": {}},
         "verified": {"source": "vendor", "ref": "https://example.com", "on": "2026-10-06"}}
    return {**e, **over}


def test_bundled_entries_validate() -> None:
    ids = {e["id"]: e for e in mcp_catalog.load()["entries"]}
    assert ids["claude-code"]["install"]["args"] == ["mcp", "serve"] and ids["claude-code"]["detect"]["binary"] == "claude"
    assert ids["opencode"]["install"]["command"] == "{grain_python}"
    assert all(not mcp_catalog.validate_entry(e) for e in ids.values())


def test_detect_dirs_then_path(tmp_path: Path, monkeypatch) -> None:
    assert mcp_catalog.detect({"id": "x"}) is None
    e = entry(detect={"binary": "demo-bin", "dirs": [str(tmp_path)], "hint": "Install demo"})
    monkeypatch.setattr(mcp_catalog.mcp_path, "resolve", lambda name: None)
    assert mcp_catalog.detect(e) == {"found": False, "path": "", "hint": "Install demo"}
    exe = tmp_path / "demo-bin"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert mcp_catalog.detect(e) == {"found": True, "path": str(exe), "hint": "Install demo"}
    monkeypatch.setattr(mcp_catalog.mcp_path, "resolve", lambda name: "/via/path/" + name)
    assert mcp_catalog.detect(entry(detect={"binary": "demo-bin", "dirs": []}))["path"] == "/via/path/demo-bin"


def test_placeholders() -> None:
    assert mcp_catalog.validate_entry(entry()) == []
    assert any("detect" in m for m in mcp_catalog.validate_entry(entry(detect=None)))  # {bin} without detect
    assert mcp_catalog.validate_entry(entry(detect={"binary": 3}))
    assert mcp_catalog.validate_entry(entry(install={"command": "x", "args": ["{bin}"], "env": {}, "url": "", "headers": {}}))
    got = mcp_catalog.render_install(entry(), {}, "/opt/demo-bin")
    assert got["command"] == "/opt/demo-bin" and got["args"] == ["serve"]
    py = mcp_catalog.render_install(entry(install={"command": "{grain_python}", "args": ["-m", "m"], "env": {}, "url": "", "headers": {}}), {})
    assert py["command"] == sys.executable


def test_shim_parsing_and_auth() -> None:
    assert opencode_mcp.parse_url("http://127.0.0.1:49374\n") == "http://127.0.0.1:49374"
    assert opencode_mcp.parse_url('noise\n{"url":"http://127.0.0.1:4096"}\n') == "http://127.0.0.1:4096"
    assert opencode_mcp.parse_url("not running") is None
    assert opencode_mcp.auth_headers("") == {}
    h = opencode_mcp.auth_headers("FAKE_PW")["Authorization"]
    assert base64.b64decode(h.removeprefix("Basic ")) == b"opencode:FAKE_PW"


def test_service_password_reads_xdg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert opencode_mcp.service_password() == ""
    (tmp_path / "opencode").mkdir()
    (tmp_path / "opencode" / "service.json").write_text(json.dumps({"password": "FAKE_PW"}))
    assert opencode_mcp.service_password() == "FAKE_PW"


def test_shaping() -> None:
    msg = {"id": "m", "type": "assistant", "content": [{"type": "reasoning", "text": "hidden"}, {"type": "text", "text": "hi"},
                                                         {"type": "tool", "name": "bash"}, {"type": "text", "text": "!"}]}
    assert opencode_mcp.shape_message(msg) == {"id": "m", "type": "assistant", "text": "hi!", "tools": ["bash"]}
    assert opencode_mcp.shape_message({"id": "u", "type": "user", "text": "q"})["text"] == "q"
    s = opencode_mcp.shape_session({"id": "s", "title": "t", "location": {"directory": "/d"}, "time": {"created": 1, "updated": 2}})
    assert s == {"id": "s", "title": "t", "directory": "/d", "updated": 2, "created": 1}


def fake(handler) -> opencode_mcp.OpenCodeClient:
    return opencode_mcp.OpenCodeClient(httpx.AsyncClient(base_url="http://oc.test", transport=httpx.MockTransport(handler)))


def test_prompt_flow_collects_the_reply_after_the_prompt() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.method} {req.url.path}")
        p = req.url.path
        if p == "/api/session" and req.method == "POST":
            assert json.loads(req.content)["location"] == {"directory": "/work"}
            return httpx.Response(200, json={"data": {"id": "ses_1"}})
        if p.endswith("/prompt"):
            return httpx.Response(200, json={"data": {"id": "msg_u2"}})
        if p.endswith("/wait"):
            return httpx.Response(204)
        if p.endswith("/message"):  # newest first
            return httpx.Response(200, json={"data": [
                {"id": "idle", "type": "idle"},
                {"id": "a2", "type": "assistant", "content": [{"type": "text", "text": "done"}]},
                {"id": "msg_u2", "type": "user", "text": "go"},
                {"id": "a1", "type": "assistant", "content": [{"type": "text", "text": "old"}]}]})
        return httpx.Response(404)

    out = asyncio.run(fake(handler).prompt("go", "/work", None, 5))
    assert out == {"session_id": "ses_1", "reply": "done"}
    assert seen[-1] == "GET /api/session/ses_1/message"


def test_prompt_timeout_reports_still_running_and_errors_raise() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/wait"):
            raise httpx.ReadTimeout("slow")
        if req.url.path.endswith("/prompt"):
            return httpx.Response(200, json={"data": {"id": "u"}})
        return httpx.Response(200, json={"data": []})

    out = asyncio.run(fake(handler).prompt("go", None, "ses_9", 5))
    assert out["session_id"] == "ses_9" and "Still running" in out["note"]
    bad = fake(lambda req: httpx.Response(401, json={}))
    try:
        asyncio.run(bad.info())
    except RuntimeError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("an error status must raise")


def _app(tmp_path: Path):
    import tempfile

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from personal_os import mcp_routes
    from personal_os.db import Database
    from personal_os.mcp_servers import McpServers

    class Client:
        async def sync(self) -> None:
            pass

    store = McpServers(Database(Path(tempfile.mkdtemp(prefix="mcproutes-", dir=tmp_path))))
    app = FastAPI()
    app.include_router(mcp_routes.router(store, Client(), lambda row: row))  # type: ignore[arg-type]
    return TestClient(app), store


def test_routes_detected_install_409_and_paste(tmp_path: Path, monkeypatch) -> None:
    os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
    http, store = _app(tmp_path)
    monkeypatch.setattr(mcp_catalog, "detect", lambda e: None if "detect" not in e else {"found": False, "path": "", "hint": "Install it"})
    rows = {e["id"]: e for e in http.get("/mcp/catalog").json()["entries"]}
    assert rows["opencode"]["detected"]["found"] is False and rows["filesystem"]["detected"] is None
    r = http.post("/mcp/catalog/opencode/install", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Install it"
    monkeypatch.setattr(mcp_catalog, "detect", lambda e: {"found": True, "path": "/opt/x/claude", "hint": ""})
    made = http.post("/mcp/catalog/claude-code/install", json={}).json()
    assert made["command"] == "/opt/x/claude" and made["args"] == ["mcp", "serve"]

    body = {"text": '{"mcpServers": {"a": {"command": "x", "env": {"API_KEY": "FAKE_TOKEN"}}, "off": {"command": "y", "disabled": true}}}'}
    out = http.post("/mcp/import/json", json=body).json()
    assert [c["name"] for c in out["created"]] == ["a", "off"] and out["skipped"] == []
    assert not store.server(out["created"][1]["id"])["enabled"]
    assert "FAKE_TOKEN" not in json.dumps(out)
    again = http.post("/mcp/import/json", json=body).json()
    assert again["created"] == [] and [s["name"] for s in again["skipped"]] == ["a", "off"]
    bad = http.post("/mcp/import/json", json={"text": "FAKE_TOKEN {"})
    assert bad.status_code == 400 and "FAKE_TOKEN" not in bad.text


def test_a_service_that_went_away_is_found_again_once() -> None:
    """The user's service restarted on a new port: the next call reconnects instead of failing for good; a second
    refusal is reported with what to do, and nothing that reached the service is ever retried."""
    urls = iter(["http://old.test", "http://new.test", "http://new2.test", "http://new3.test"])
    found: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host != "new.test":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"version": "2"})

    async def connect() -> tuple[opencode_mcp.OpenCodeClient, str]:
        url = next(urls)
        found.append(url)
        return opencode_mcp.OpenCodeClient(httpx.AsyncClient(base_url=url, transport=httpx.MockTransport(handler))), url

    svc = opencode_mcp.Service(connect)
    assert asyncio.run(svc.call(lambda c: c.info())) == {"version": "2"}
    assert found == ["http://old.test", "http://new.test"] and svc.url == "http://new.test"
    svc.client = None  # the service moves again and stays down: one retry, then an actionable error
    try:
        asyncio.run(svc.call(lambda c: c.info()))
        raise AssertionError("expected an error")
    except RuntimeError as e:
        assert "opencode service start" in str(e)
    assert found[2:] == ["http://new2.test", "http://new3.test"]

    sent: list[str] = []

    def flaky(req: httpx.Request) -> httpx.Response:  # reached the service, then broke: not retried
        sent.append(req.url.path)
        raise httpx.ReadError("reset")

    async def once() -> tuple[opencode_mcp.OpenCodeClient, str]:
        return opencode_mcp.OpenCodeClient(httpx.AsyncClient(base_url="http://x.test", transport=httpx.MockTransport(flaky))), "u"

    try:
        asyncio.run(opencode_mcp.Service(once).call(lambda c: c.info()))
    except httpx.ReadError:
        pass
    assert sent == ["/api/info"]
