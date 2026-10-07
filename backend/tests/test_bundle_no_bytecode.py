"""Nothing Grain starts on its own Python writes .pyc files: in the packaged app that interpreter and its stdlib sit inside
the signed bundle, and a rewritten .pyc breaks the code signature.

Run: timeout 300 uv run --with pytest pytest tests/test_bundle_no_bytecode.py -x -q
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mcp_catalog, mcp_client, sandbox  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_grain_python_is_recognised_directly_and_through_a_venv_link(tmp_path: Path) -> None:
    assert mcp_client.is_grain_python(sys.executable)
    link = tmp_path / "venv" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(os.path.realpath(sys.executable))
    assert mcp_client.is_grain_python(str(link))
    assert not mcp_client.is_grain_python("/bin/echo")
    assert not mcp_client.is_grain_python("")


def test_no_bytecode_flags_only_grain_python() -> None:
    env: dict[str, str] = {"PYTHONDONTWRITEBYTECODE": "", "KEEP": "x"}
    mcp_client.no_bytecode(sys.executable, env)
    assert env == {"PYTHONDONTWRITEBYTECODE": "1", "KEEP": "x"}  # forced on, even over a blank value from the server row
    other: dict[str, str] = {}
    mcp_client.no_bytecode("/bin/echo", other)
    assert other == {}


def _captured_launch(command: str, env: dict[str, str] | None = None) -> Any:
    """Open a stdio connection with the launcher stubbed out and return the parameters it was handed."""
    seen: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def fake_stdio(params: Any, errlog: Any = None) -> Any:
        seen["params"] = params
        yield (None, None)

    config = mcp_client._Config(id="s1", slug="s1", name="S1", transport="stdio", command=command, args=["-m", "x"],
                                cwd="", env={"PATH": "/usr/bin:/bin", **(env or {})})

    async def go() -> None:
        async with mcp_client._open(config, _NullErr(), None):
            pass

    orig = mcp_client.stdio_client
    mcp_client.stdio_client = fake_stdio
    try:
        asyncio.run(go())
    finally:
        mcp_client.stdio_client = orig
    return seen["params"]


class _NullErr:
    file = None


def test_stdio_server_on_grain_python_launches_without_bytecode() -> None:
    params = _captured_launch(sys.executable)
    assert params.env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert params.env["PATH"] == "/usr/bin:/bin"


def test_other_stdio_servers_keep_their_environment() -> None:
    params = _captured_launch("/bin/echo", {"FOO": "bar"})
    assert "PYTHONDONTWRITEBYTECODE" not in params.env
    assert params.env["FOO"] == "bar"


def test_every_catalog_entry_on_grain_python_gets_the_flag() -> None:
    entries = [e for e in mcp_catalog.load()["entries"] if (e.get("install") or {}).get("command") == "{grain_python}"]
    assert entries, "the catalog has at least one connector that runs on Grain's own Python (OpenCode)"
    for e in entries:
        rendered = mcp_catalog.render_install(e, {})
        assert rendered["command"] == sys.executable
        params = _captured_launch(rendered["command"], rendered["env"])
        assert params.env["PYTHONDONTWRITEBYTECODE"] == "1", e["id"]


def test_sandboxed_python_runs_with_bytecode_writes_off() -> None:
    out = sandbox.run_python("import sys; print(sys.flags.dont_write_bytecode)", timeout=60)
    assert out["exit_code"] == 0, out
    assert out["stdout"].strip() == "1"


def test_bundle_precompiles_with_mtime_independent_pyc() -> None:
    script = (ROOT / "scripts" / "bundle-backend.sh").read_text()
    line = next(x for x in script.splitlines() if "compileall" in x and not x.lstrip().startswith("#"))
    assert "--invalidation-mode unchecked-hash" in line
    src = (ROOT / "src" / "main" / "backend.ts").read_text()
    assert "PYTHONDONTWRITEBYTECODE: '1'" in src
