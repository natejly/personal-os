"""Finding a stdio connector's command on the PATH the user's terminal has, and failing readably when it is absent.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest personal_os/tests/test_mcp_path.py -q
"""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os import mcp_client, mcp_path
from personal_os.db import Database
from personal_os.mcp_client import McpClient, McpMissingCommand
from personal_os.mcp_servers import McpServers


def fake_bin(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


@pytest.fixture(autouse=True)
def fresh_cache() -> Any:
    mcp_path.user_path.cache_clear()
    yield
    mcp_path.user_path.cache_clear()


def test_build_path_dedupes_and_keeps_only_existing_dirs(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    path = mcp_path.build_path(f"{a}:{tmp_path / 'ghost'}:{a}", [str(b), str(a)], base=str(b))
    assert path.split(os.pathsep) == [str(a), str(b)]


def test_login_shell_path_is_read_between_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    out = f"rc noise\n{mcp_path.MARKER}/x/bin:/y/bin{mcp_path.MARKER}more noise"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert mcp_path._login_shell_path() == "/x/bin:/y/bin"


def test_a_hung_login_shell_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*a: Any, **k: Any) -> Any:
        assert k["timeout"] == mcp_path.PROBE_TIMEOUT and k["stdin"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(a, k["timeout"])
    monkeypatch.setattr(subprocess, "run", hang)
    assert mcp_path._login_shell_path() == ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no shell")))
    assert mcp_path._login_shell_path() == ""


def test_common_dirs_include_the_version_managers_newest_node_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("v18.20.1", "v20.16.0", "v9.0.0"):
        (tmp_path / ".nvm" / "versions" / "node" / v / "bin").mkdir(parents=True)
    dirs = mcp_path.common_dirs()
    nodes = [d for d in dirs if "/.nvm/" in d]
    assert [Path(d).parent.name for d in nodes] == ["v20.16.0", "v18.20.1", "v9.0.0"]
    assert str(tmp_path / ".local/bin") in dirs and mcp_path.DOCKER_DESKTOP_BIN in dirs


def test_user_path_is_resolved_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(mcp_path, "_login_shell_path", lambda: calls.append(1) or str(tmp_path))
    assert mcp_path.user_path() == mcp_path.user_path()
    assert len(calls) == 1 and str(tmp_path) in mcp_path.user_path().split(os.pathsep)


def test_resolve_uses_the_servers_own_path_or_the_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = fake_bin(tmp_path / "bin", "mytool")
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path / "bin"))
    assert mcp_path.resolve("mytool") == str(exe)
    assert mcp_path.resolve("mytool", {"PATH": "/nowhere"}) is None  # an explicit PATH is never extended
    assert mcp_path.resolve(str(exe), {"PATH": "/nowhere"}) == str(exe)  # an absolute command is taken as given
    assert mcp_path.resolve(str(tmp_path / "bin" / "ghost")) is None


def test_hints_and_message() -> None:
    assert "Node.js" in mcp_path.RUNTIME_HINTS["npx"] and "uv" in mcp_path.RUNTIME_HINTS["uvx"]
    assert mcp_path.missing_message("npx") == "npx not found. Install Node.js (nodejs.org), then restart this connector."
    assert mcp_path.hint_for("/opt/homebrew/bin/docker").startswith("Install Docker")
    assert mcp_path.hint_for("whatever") == mcp_path.FALLBACK_HINT


def test_runtimes_report_found_and_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin(tmp_path / "bin", "npx")
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path / "bin"))
    r = mcp_path.runtimes()
    assert r["node"]["found"] and r["node"]["path"].endswith("/npx")
    assert not r["docker"]["found"] and r["docker"]["path"] is None and "Docker" in r["docker"]["hint"]


# ---------- wired into the client ----------
def test_a_missing_command_is_one_readable_failure_with_no_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path))
    store = McpServers(Database(tmp_path / "db"))
    row = store.create_server("Ghost", command="npx", args=["-y", "x"])

    async def run() -> tuple[str, str, int]:
        client = McpClient(store, connect_timeout=5)
        await client.sync()
        sup = client._supervisors[row["id"]]
        await asyncio.wait_for(sup._task, 5)  # the task ends by itself: it is not retrying
        seen = sup.status, sup.detail, sup.attempts
        await client.stop()
        return seen
    status, detail, attempts = asyncio.run(run())
    assert status == "error" and detail == "npx not found. Install Node.js (nodejs.org), then restart this connector."
    assert attempts == 1


def test_probe_reports_the_missing_command_plainly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path))
    out = asyncio.run(McpClient(None).probe({"command": "uvx", "args": ["x"]}))  # type: ignore[arg-type]
    assert not out["ok"] and out["error"] == "uvx not found. Install uv (docs.astral.sh/uv), then restart this connector."


def test_the_resolved_path_reaches_the_child_unless_the_server_sets_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = fake_bin(tmp_path / "bin", "mytool")
    monkeypatch.setattr(mcp_path, "user_path", lambda: str(tmp_path / "bin"))
    seen: list[Any] = []

    class Boom(Exception):
        pass

    def fake_stdio(params: Any, errlog: Any = None) -> Any:
        seen.append(params)
        raise Boom

    monkeypatch.setattr(mcp_client, "stdio_client", fake_stdio)

    async def open_with(env: dict[str, str]) -> None:
        cfg = mcp_client._Config(id="", slug="s", name="s", transport="stdio", command="mytool", args=["a"], cwd="", env=env)
        err = mcp_client._Stderr([])
        try:
            with pytest.raises(Boom):
                async with mcp_client._open(cfg, err, None):
                    pass
        finally:
            err.close()
    asyncio.run(open_with({"K": "v"}))
    assert seen[0].command == str(exe) and seen[0].env["PATH"] == str(tmp_path / "bin") and seen[0].env["K"] == "v"
    asyncio.run(open_with({"PATH": str(tmp_path / "bin")}))  # explicit PATH: used as given
    assert seen[1].env["PATH"] == str(tmp_path / "bin")
    assert issubclass(McpMissingCommand, mcp_client.McpUnavailable)
