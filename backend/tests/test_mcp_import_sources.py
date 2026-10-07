"""MCP import: every source's config shape, JSONC and TOML tolerance, cross-source dedupe, pasted JSON, and no secret in the view.

A temp HOME holds fake config files (values like FAKE_TOKEN). Run: timeout 300 uv run pytest tests/test_mcp_import_sources.py -x -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import mcp_import  # noqa: E402

FAKE = "FAKE_TOKEN_0123456789"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def by_source(view: list[dict]) -> dict[str, dict[str, dict]]:
    return {s["id"]: {x["name"]: x for x in s["servers"]} for s in view}


def test_every_source_shape(home: Path) -> None:
    support = home / "Library" / "Application Support"
    put(support / "Code" / "User" / "mcp.json",
        json.dumps({"servers": {"vs-local": {"type": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {"API_KEY": FAKE}},
                                "vs-remote": {"type": "http", "url": "https://example.com/mcp", "headers": {"Authorization": f"Bearer {FAKE}"}}}}))
    put(support / "Code" / "User" / "settings.json",  # JSONC: comments and a trailing comma
        '{\n // editor\n "editor.fontSize": 14, /* x */\n "mcp": {"servers": {"vs-settings": {"command": "uvx", "args": ["tool"],},},},\n}\n')
    put(home / ".codeium" / "windsurf" / "mcp_config.json", json.dumps({"mcpServers": {"ws": {"serverUrl": "https://ws.example.com/sse"}}}))
    put(home / ".codex" / "config.toml",
        f'model = "x"\n[mcp_servers.cx-local]\ncommand = "node"\nargs = ["a.js"]\nenv = {{ TOKEN = "{FAKE}" }}\n'
        f'[mcp_servers.cx-remote]\nurl = "https://cx.example.com/mcp"\nhttp_headers = {{ "X-Key" = "{FAKE}" }}\nbearer_token_env_var = "SOME_VAR"\n')
    put(home / ".config" / "opencode" / "opencode.jsonc",
        '{\n // servers\n "mcp": {\n  "oc-local": {"type": "local", "command": ["bunx", "pkg", "--flag"], "environment": {"SECRET_KEY": "' + FAKE + '"}, "enabled": false},\n'
        '  "oc-remote": {"type": "remote", "url": "https://oc.example.com/mcp", "headers": {"X-Auth": "' + FAKE + '"}},\n }\n}\n')
    put(home / ".claude" / "settings.json", json.dumps({"mcpServers": {"cc-settings": {"command": "claude-x", "args": []}}}))

    view, configs = mcp_import.scan([])
    got = by_source(view)

    vs = got["vscode"]
    assert vs["vs-local"]["transport"] == "stdio" and vs["vs-local"]["command"] == "npx" and vs["vs-local"]["args"] == ["-y", "pkg"]
    assert vs["vs-local"]["env_keys"] == ["API_KEY"] and vs["vs-local"]["secret_keys"] == ["API_KEY"]
    assert vs["vs-remote"]["transport"] == "http" and vs["vs-remote"]["header_keys"] == ["Authorization"]
    assert vs["vs-settings"]["command"] == "uvx"
    assert got["windsurf"]["ws"]["transport"] == "http" and got["windsurf"]["ws"]["url"] == "https://ws.example.com/sse"
    cx = got["codex"]
    assert cx["cx-local"]["command"] == "node" and cx["cx-local"]["args"] == ["a.js"] and cx["cx-local"]["env_keys"] == ["TOKEN"]
    assert cx["cx-remote"]["header_keys"] == ["X-Key"]
    oc = got["opencode"]
    assert oc["oc-local"]["command"] == "bunx" and oc["oc-local"]["args"] == ["pkg", "--flag"]
    assert oc["oc-local"]["env_keys"] == ["SECRET_KEY"] and oc["oc-local"]["enabled"] is False
    assert oc["oc-remote"]["transport"] == "http" and oc["oc-remote"]["enabled"] is True
    assert got["claude_code"]["cc-settings"]["command"] == "claude-x"

    assert FAKE not in json.dumps(view)  # no env or header value reaches the view
    kw = mcp_import.create_kwargs(configs[oc["oc-local"]["ref"]])
    assert kw["enabled"] is False and kw["secrets"] == {"SECRET_KEY": FAKE}
    assert mcp_import.create_kwargs(configs[vs["vs-remote"]["ref"]])["headers"] == {"Authorization": f"Bearer {FAKE}"}


def test_bad_files_are_errors_not_crashes(home: Path) -> None:
    put(home / ".codex" / "config.toml", "[mcp_servers\nbroken")
    put(home / ".codeium" / "windsurf" / "mcp_config.json", "{not json")
    got = {s["id"]: s for s in mcp_import.scan([])[0]}
    assert got["codex"]["error"] == "not valid TOML" and got["windsurf"]["error"] == "not valid JSON"


def test_cross_source_dedupe(home: Path) -> None:
    same = {"command": "npx", "args": ["-y", "pkg"]}
    put(home / ".cursor" / "mcp.json", json.dumps({"mcpServers": {"one": same}}))
    put(home / ".codeium" / "windsurf" / "mcp_config.json", json.dumps({"mcpServers": {"renamed": same, "other": {"command": "x"}}}))
    view, configs = mcp_import.scan([])
    ws = by_source(view)["windsurf"]
    assert ws["renamed"]["duplicate_of"] == "Cursor" and "duplicate_of" not in ws["other"]
    assert ws["renamed"]["ref"] not in configs and ws["other"]["ref"] in configs  # a duplicate cannot be imported
    assert by_source(view)["cursor"]["one"]["ref"] in configs


def test_installed_marker(home: Path) -> None:
    put(home / ".cursor" / "mcp.json", json.dumps({"mcpServers": {"one": {"command": "npx", "args": ["a"]}}}))
    have = [{"transport": "stdio", "command": "npx", "args": ["a"], "url": ""}]
    assert by_source(mcp_import.scan(have)[0])["cursor"]["one"]["installed"] is True


@pytest.mark.parametrize("text,names", [
    ('{"mcpServers": {"a": {"command": "x"}, "b": {"url": "https://h.example.com/mcp"}}}', ["a", "b"]),
    ('{"servers": {"a": {"command": "x"}}}', ["a"]),
    ('{"mcp": {"a": {"type": "local", "command": ["x", "y"]}}}', ["a"]),
    ('{"a": {"command": "x"}, "b": {"command": "y"}}', ["a", "b"]),
    ('{"command": "npx", "args": ["-y", "p"]}', ["npx"]),
    ('{"name": "mine", "url": "https://h.example.com/mcp"}', ["mine"]),
    ('{"url": "https://h.example.com/mcp"}', ["h.example.com"]),
    ('// c\n{"mcpServers": {"a": {"command": "x",},},}', ["a"]),
])
def test_paste_shapes(text: str, names: list[str]) -> None:
    assert [c["key"] for c in mcp_import.parse_pasted(text)] == names


def test_paste_opencode_array_and_errors() -> None:
    cfg = mcp_import.parse_pasted('{"mcp": {"a": {"command": ["bunx", "p"], "environment": {"K": "v"}}}}')[0]
    assert (cfg["command"], cfg["args"], cfg["env"]) == ("bunx", ["p"], {"K": "v"})
    for bad in ("nope", "[1]", '{"mcpServers": {}}', '{"x": 1}'):
        with pytest.raises(ValueError) as e:
            mcp_import.parse_pasted(bad)
        assert len(str(e.value)) < 80  # a fixed message, never the pasted text
    with pytest.raises(ValueError) as e:
        mcp_import.parse_pasted('{"a": 1, "token": "' + FAKE + '"')
    assert FAKE not in str(e.value)
