"""Catalog install, registry search and config import through the real app routes. No network, no child processes.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_routes.py -q
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mcproutes-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import mcp_catalog, mcp_path, mcp_routes  # noqa: E402

SECRET = "test-token-123"
CATALOG = {"version": 1, "entries": [{
    "id": "demo", "name": "Demo", "description": "A demo connector.", "category": "Developer", "icon": "database",
    "publisher": "Acme", "official": True, "docs": "https://example.com/docs", "transport": "stdio", "runtime": "node",
    "install": {"command": "npx", "args": ["-y", "demo-mcp", "{folder}"], "env": {"DEMO_TOKEN": "{token}", "DEMO_REGION": "{region}"}},
    "auth": "api_key",
    "fields": [{"id": "token", "label": "Token", "secret": True, "required": True},
               {"id": "region", "label": "Region", "default": "eu"}, {"id": "folder", "label": "Folder"}],
    "verified": {"source": "npm", "ref": "demo-mcp", "version": "1.0.0", "on": "2026-10-06"}}]}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    for row in appmod.mcp_store.servers():
        appmod.mcp_store.delete_server(row["id"])
    monkeypatch.setattr(mcp_catalog, "_bundled", lambda: CATALOG)
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path))
    synced: list[int] = []

    async def fake_sync() -> list[Any]:  # no child process: the supervisor is not what is under test
        synced.append(1)
        return []
    monkeypatch.setattr(appmod.mcp, "sync", fake_sync)
    monkeypatch.setenv("HOME", str(tmp_path))
    mcp_routes._cache.clear()
    return TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def table_dump() -> str:
    with appmod.db.tx() as c:
        return json.dumps([dict(r) for t in ("mcp_servers", "mcp_tools", "mcp_grants") for r in c.execute(f"SELECT * FROM {t}").fetchall()], default=str)


# ---------- catalog ----------
def test_catalog_lists_entries_runtimes_and_what_is_installed(api: TestClient) -> None:
    body = api.get("/mcp/catalog").json()
    assert [e["id"] for e in body["entries"]] == ["demo"] and body["entries"][0]["installed"] == []
    assert body["categories"] == mcp_catalog.CATEGORIES
    assert set(body["runtimes"]) == {"node", "python", "docker"} and body["runtimes"]["node"]["found"] is False
    assert "Node.js" in body["runtimes"]["node"]["hint"]
    sid = api.post("/mcp/catalog/demo/install", json={"values": {"token": SECRET}}).json()["id"]
    assert api.get("/mcp/catalog").json()["entries"][0]["installed"] == [sid]


def test_install_never_leaks_the_secret(api: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = api.post("/mcp/catalog/demo/install", json={"name": "My demo", "values": {"token": SECRET, "folder": "/tmp/x"}})
    assert r.status_code == 200
    view = r.json()
    assert SECRET not in r.text, "not in the response"
    assert SECRET not in caplog.text, "not in any log line"
    assert SECRET not in table_dump(), "not in the database tables"
    assert view["secret_keys"] == ["DEMO_TOKEN"] and view["env"] == {"DEMO_REGION": "eu"}
    assert view["name"] == "My demo" and view["catalog_id"] == "demo" and view["args"] == ["-y", "demo-mcp", "/tmp/x"]
    assert view["description"] == "A demo connector." and view["command"] == "npx"
    assert appmod.mcp_store._load_secrets(view["id"]) == {"DEMO_TOKEN": SECRET}, "it is in the secret store"
    assert SECRET not in api.get("/mcp/servers").text and SECRET not in api.get("/mcp/catalog").text


def test_install_errors_name_the_field_and_not_a_value(api: TestClient) -> None:
    assert api.post("/mcp/catalog/ghost/install", json={"values": {}}).status_code == 404
    r = api.post("/mcp/catalog/demo/install", json={"values": {"region": "sekret-region"}})
    assert r.status_code == 400 and "token" in r.json()["detail"] and "sekret-region" not in r.text
    assert appmod.mcp_store.servers() == [], "a refused install creates nothing"


# ---------- registry ----------
def item(**srv: Any) -> dict[str, Any]:
    return {"server": {"name": "io.example/demo-server", "description": "Does demo things.", "version": "2.1.0",
                       "repository": {"url": "https://github.com/example/demo", "source": "github"}, **srv}}


def test_registry_mapping_npm_pypi_oci_and_remotes() -> None:
    npm = mcp_routes.map_registry_result(item(packages=[{"registryType": "npm", "identifier": "@ex/demo", "version": "2.1.0",
        "transport": {"type": "stdio"}, "environmentVariables": [{"name": "DEMO_KEY", "isSecret": True, "isRequired": True}, {"name": "DEMO_URL"}]}]))
    assert npm["id"] == "io.example/demo-server" and npm["name"] == "demo-server" and npm["verified"] is False
    assert npm["transport"] == "stdio" and npm["install"]["command"] == "npx" and npm["install"]["args"] == ["-y", "@ex/demo@2.1.0"]
    assert npm["install"]["env"] == {"DEMO_KEY": "", "DEMO_URL": ""} and npm["secret_keys"] == ["DEMO_KEY"] and npm["env_keys"] == ["DEMO_KEY", "DEMO_URL"]
    assert npm["repository"] == "https://github.com/example/demo" and npm["docs"] == npm["repository"]
    py = mcp_routes.map_registry_result(item(packages=[{"registryType": "pypi", "identifier": "demo-mcp", "version": "1.0"}]))
    assert (py["install"]["command"], py["install"]["args"]) == ("uvx", ["demo-mcp==1.0"])
    oci = mcp_routes.map_registry_result(item(packages=[{"registryType": "oci", "identifier": "ghcr.io/ex/demo:1",
                                                          "environmentVariables": [{"name": "A_KEY", "isSecret": True}]}]))
    assert (oci["install"]["command"], oci["install"]["args"]) == ("docker", ["run", "-i", "--rm", "-e", "A_KEY", "ghcr.io/ex/demo:1"])
    remote = mcp_routes.map_registry_result(item(remotes=[{"type": "streamable-http", "url": "https://r.example/mcp",
        "headers": [{"name": "Authorization", "isSecret": True, "value": "Bearer {k}"}]}], websiteUrl="https://example.com"))
    assert remote["transport"] == "http" and remote["install"]["url"] == "https://r.example/mcp" and remote["install"]["command"] == ""
    assert remote["secret_keys"] == ["Authorization"] and remote["install"]["headers"] == {"Authorization": ""} and remote["docs"] == "https://example.com"
    sse = mcp_routes.map_registry_result(item(remotes=[{"type": "sse", "url": "https://r.example/sse"}]))
    assert sse["transport"] == "sse"


def test_registry_drops_what_grain_cannot_launch() -> None:
    assert mcp_routes.map_registry_result(item(packages=[{"registryType": "nuget", "identifier": "X"}])) is None
    assert mcp_routes.map_registry_result(item(remotes=[{"type": "streamable-http", "url": "http://insecure.example"}])) is None
    assert mcp_routes.map_registry_result({"server": {"description": "no name"}}) is None


class FakeClient:
    calls: list[dict[str, Any]] = []
    fail: Exception | None = None

    def __init__(self, **kw: Any) -> None:
        assert kw["timeout"] == mcp_routes.REGISTRY_TIMEOUT

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any]) -> Any:
        FakeClient.calls.append(params)
        if FakeClient.fail:
            raise FakeClient.fail
        body = {"servers": [item(packages=[{"registryType": "npm", "identifier": "a"}]), item(packages=[{"registryType": "npm", "identifier": "a"}]),
                            {"server": {"name": "x/none", "packages": []}}]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


def test_registry_route_searches_dedupes_caches_and_reports_errors(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_routes.httpx, "AsyncClient", FakeClient)
    FakeClient.calls, FakeClient.fail = [], None
    body = api.get("/mcp/registry/search", params={"q": "Demo", "limit": 500}).json()
    assert body["error"] is None and [r["id"] for r in body["results"]] == ["io.example/demo-server"]
    assert FakeClient.calls == [{"search": "Demo", "limit": mcp_routes.REGISTRY_MAX_LIMIT}], "the limit is clamped"
    api.get("/mcp/registry/search", params={"q": "demo", "limit": 500})  # same query, any case: from the cache
    assert len(FakeClient.calls) == 1
    FakeClient.fail = httpx.ConnectError("boom")
    body = api.get("/mcp/registry/search", params={"q": "other"}).json()
    assert body["results"] == [] and "could not be reached" in body["error"]


def test_registry_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    monkeypatch.setattr(mcp_routes.httpx, "AsyncClient", FakeClient)
    FakeClient.calls, FakeClient.fail = [], None
    mcp_routes._cache.clear()
    now = [1000.0]
    monkeypatch.setattr(mcp_routes.time, "monotonic", lambda: now[0])
    asyncio.run(mcp_routes.registry_search("q", 5))
    now[0] += mcp_routes.REGISTRY_TTL - 1
    asyncio.run(mcp_routes.registry_search("q", 5))
    assert len(FakeClient.calls) == 1
    now[0] += 2
    asyncio.run(mcp_routes.registry_search("q", 5))
    assert len(FakeClient.calls) == 2


# ---------- import ----------
def test_import_lists_without_values_and_creates_only_known_refs(api: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "fs"], "env": {"API_TOKEN": SECRET, "REGION": "eu"}},
        "remote": {"url": "https://r.example/mcp", "headers": {"X-Key": SECRET}}}}))
    listing = api.get("/mcp/import/sources")
    assert listing.status_code == 200 and SECRET not in listing.text
    cursor = next(s for s in listing.json()["sources"] if s["id"] == "cursor")
    refs = {s["key"]: s["ref"] for s in cursor["servers"]}
    assert cursor["found"] and set(refs) == {"files", "remote"}
    r = api.post("/mcp/import", json={"refs": [refs["files"], refs["remote"], refs["files"], "/etc/passwd", "deadbeef"]})
    body = r.json()
    assert r.status_code == 200 and SECRET not in r.text and SECRET not in caplog.text and SECRET not in table_dump()
    assert sorted(c["name"] for c in body["created"]) == ["files", "remote"]
    assert [s["ref"] for s in body["skipped"]] == ["/etc/passwd", "deadbeef"]
    files = next(c for c in body["created"] if c["name"] == "files")
    assert files["secret_keys"] == ["API_TOKEN"] and files["env"] == {"REGION": "eu"}
    remote = next(c for c in body["created"] if c["name"] == "remote")
    assert remote["transport"] == "http" and remote["headers"] == {"X-Key": ""}
    assert appmod.mcp_store._load_secrets(files["id"]) == {"API_TOKEN": SECRET} and appmod.mcp_store._load_secrets(remote["id"]) == {"X-Key": SECRET}
    again = api.post("/mcp/import", json={"refs": [refs["files"]]}).json()
    assert again["created"] == [] and again["skipped"][0]["reason"] == "already added"
    after = next(s for s in api.get("/mcp/import/sources").json()["sources"] if s["id"] == "cursor")
    assert all(s["installed"] for s in after["servers"])
