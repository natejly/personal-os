"""Discovering MCP servers in other apps' config files, under a throwaway HOME.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_import.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personal_os import mcp_import

SECRET = "test-token-123"


def write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def by_id(view: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in view}


def test_nothing_installed_means_nothing_found(home: Path) -> None:
    view, configs = mcp_import.scan([])
    assert [s["id"] for s in view] == ["claude_desktop", "claude_code", "cursor"]
    assert all(not s["found"] and s["servers"] == [] and s["error"] is None for s in view) and configs == {}


def test_claude_desktop_stdio_and_cursor_remote(home: Path) -> None:
    write(home / "Library/Application Support/Claude/claude_desktop_config.json", {"mcpServers": {
        "files": {"command": "npx", "args": ["-y", "@x/server-filesystem", "/tmp"], "env": {"GITHUB_TOKEN": SECRET, "LOG_LEVEL": "debug"}}}})
    write(home / ".cursor/mcp.json", {"mcpServers": {
        "docs": {"url": "https://mcp.example.com/sse", "type": "sse", "headers": {"Authorization": f"Bearer {SECRET}"}},
        "api": {"url": "https://mcp.example.com/mcp", "transport": "streamable-http"},
        "junk": {"nothing": True}}})
    view, configs = mcp_import.scan([])
    src = by_id(view)
    f = src["claude_desktop"]["servers"][0]
    assert src["claude_desktop"]["found"] and f["transport"] == "stdio" and f["command"] == "npx"
    assert f["env_keys"] == ["GITHUB_TOKEN", "LOG_LEVEL"] and f["secret_keys"] == ["GITHUB_TOKEN"]
    cur = {s["key"]: s for s in src["cursor"]["servers"]}
    assert set(cur) == {"docs", "api"}  # the entry that is neither command nor url is skipped
    assert cur["docs"]["transport"] == "sse" and cur["docs"]["header_keys"] == ["Authorization"]
    assert cur["api"]["transport"] == "http"
    assert SECRET not in json.dumps(view), "values are never returned"
    assert len(configs) == 3


def test_claude_code_reads_top_level_projects_and_project_mcp_json(home: Path) -> None:
    proj = home / "work" / "app"
    write(proj / ".mcp.json", {"mcpServers": {"db": {"command": "uvx", "args": ["mcp-server-sqlite"]}}})
    write(home / ".claude.json", {
        "mcpServers": {"global": {"command": "npx", "args": ["g"]}},
        "projects": {str(proj): {"mcpServers": {"local": {"type": "http", "url": "https://x.example/mcp"}}},
                     str(home / "other"): {"mcpServers": {"global": {"command": "npx", "args": ["g"]}}}}})
    servers = by_id(mcp_import.scan([])[0])["claude_code"]["servers"]
    assert sorted(s["key"] for s in servers) == ["db", "global", "local"]  # the same server under two projects shows once
    assert len({s["ref"] for s in servers}) == 3


def test_refs_are_opaque_stable_and_distinct_per_origin(home: Path) -> None:
    write(home / ".claude.json", {"projects": {"/a": {"mcpServers": {"s": {"command": "x"}}}, "/b": {"mcpServers": {"s": {"command": "y"}}}}})
    first = [s["ref"] for s in by_id(mcp_import.scan([])[0])["claude_code"]["servers"]]
    again = [s["ref"] for s in by_id(mcp_import.scan([])[0])["claude_code"]["servers"]]
    assert first == again and len(set(first)) == 2 and all(len(r) == mcp_import.REF_CHARS and "/" not in r for r in first)


def test_bad_json_and_oversize_files_are_errors_not_exceptions(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(home / ".cursor/mcp.json", "{ not json")
    write(home / ".claude.json", json.dumps({"mcpServers": {"s": {"command": "x"}}}) + " " * 200)
    monkeypatch.setattr(mcp_import, "MAX_FILE_BYTES", 100)
    src = by_id(mcp_import.scan([])[0])
    assert src["cursor"]["error"] == "not valid JSON" and src["cursor"]["found"] and src["cursor"]["servers"] == []
    assert "too large" in src["claude_code"]["error"]


def test_a_json_top_level_that_is_not_an_object_is_harmless(home: Path) -> None:
    write(home / ".cursor/mcp.json", "[1, 2]")
    write(home / ".claude.json", {"projects": "nope", "mcpServers": ["x"]})
    assert all(s["servers"] == [] for s in mcp_import.scan([])[0])


def test_installed_matches_by_command_and_args_or_by_url(home: Path) -> None:
    write(home / ".cursor/mcp.json", {"mcpServers": {
        "a": {"command": "npx", "args": ["-y", "p"]}, "b": {"command": "npx", "args": ["-y", "other"]},
        "c": {"url": "https://x.example/mcp"}}})
    existing = [{"transport": "stdio", "command": "npx", "args": ["-y", "p"], "url": ""},
                {"transport": "http", "command": "", "args": [], "url": "https://x.example/mcp"}]
    got = {s["key"]: s["installed"] for s in by_id(mcp_import.scan(existing)[0])["cursor"]["servers"]}
    assert got == {"a": True, "b": False, "c": True}


@pytest.mark.parametrize("key,value,expected", [
    ("GITHUB_PERSONAL_ACCESS_TOKEN", "x", True), ("API_KEY", "x", True), ("DB_PASSWORD", "x", True), ("SESSION", "x", True),
    ("apiKey", "x", True), ("PAT", "x", True), ("MY_COOKIE", "x", True), ("AUTH_HEADER", "x", True),
    ("PATH", "/usr/bin", False), ("LOG_LEVEL", "debug", False), ("HOME", "/Users/a", False), ("NODE_ENV", "production", False),
    ("CONN", "postgres://user:hunter2@db.example.com/x", True), ("X", "ghp_" + "a" * 36, True), ("X", "", False),
])
def test_looks_secret(key: str, value: str, expected: bool) -> None:
    assert mcp_import.looks_secret(key, value) is expected


def test_create_kwargs_routes_secret_looking_env_to_secrets(home: Path) -> None:
    write(home / ".cursor/mcp.json", {"mcpServers": {"s": {"command": "npx", "args": ["x"], "env": {"API_TOKEN": SECRET, "REGION": "eu"}}}})
    _view, configs = mcp_import.scan([])
    kw = mcp_import.create_kwargs(next(iter(configs.values())))
    assert kw["secrets"] == {"API_TOKEN": SECRET} and kw["env"] == {"REGION": "eu"} and kw["name"] == "s"
    write(home / ".cursor/mcp.json", {"mcpServers": {"r": {"url": "https://x.example/mcp", "headers": {"X-Key": SECRET}}}})
    kw = mcp_import.create_kwargs(next(iter(mcp_import.scan([])[1].values())))
    assert kw["headers"] == {"X-Key": SECRET} and kw["transport"] == "http" and kw["url"] == "https://x.example/mcp"


def test_a_secret_in_an_argument_or_url_is_not_shown(home: Path) -> None:
    pat = "ghp_" + "b" * 36
    write(home / ".cursor/mcp.json", {"mcpServers": {"s": {"command": "x", "args": ["--token", pat]},
                                                       "u": {"url": f"https://user:{SECRET}@x.example/mcp?api_key={SECRET}"}}})
    assert SECRET not in json.dumps(mcp_import.scan([])[0]) and pat not in json.dumps(mcp_import.scan([])[0])
