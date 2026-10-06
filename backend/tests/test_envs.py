"""The shared work environment (envs.py): name validation, installer choice, status, idempotence, failure reporting.
Every subprocess goes through a fake runner, so nothing here touches the network or builds a real venv."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import envs  # noqa: E402
from personal_os.envs import BASE_PACKAGES, EnvError, WorkEnv, validate_packages  # noqa: E402


class Fake:
    """Records command lines. `venv` commands create the interpreter file so status() sees a venv, like the real tools."""

    def __init__(self, root: Path, fail_on: str | None = None) -> None:
        self.root, self.calls, self.fail_on = root, [], fail_on

    def __call__(self, cmd: list[str], **kw: Any) -> Any:
        self.calls.append(cmd)
        assert kw.get("timeout") == 600 and kw.get("capture_output")
        if self.fail_on and self.fail_on in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: No matching distribution found for nope")
        if "venv" in cmd:
            py = self.root / "envs" / "work" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def make(tmp: Path, monkeypatch: pytest.MonkeyPatch, uv: bool, extra: list[str] | None = None,
         fail_on: str | None = None) -> tuple[WorkEnv, Fake]:
    monkeypatch.setattr(envs.shutil, "which", lambda n: "/usr/bin/uv" if (uv and n == "uv") else None)
    fake = Fake(tmp, fail_on)
    return WorkEnv(tmp, lambda: {"workEnvPackages": extra if extra is not None else []}, runner=fake), fake


def test_name_validation() -> None:
    assert validate_packages(["pandas", "scipy>=1.11", "requests[socks]==2.31.0", "pandas"]) == [
        "pandas", "scipy>=1.11", "requests[socks]==2.31.0"]
    for bad in ["", "-e .", "--index-url=http://x", "git+https://x/y.git", "https://x/y.whl", "./local", "/abs/path", "a b",
                "pkg;rm -rf", "pkg>=1,<2", "pkg @ http://x", "-r req.txt", "pkg\nother", 5, None]:
        with pytest.raises(EnvError):
            validate_packages([bad])
    with pytest.raises(EnvError):
        validate_packages([])
    with pytest.raises(EnvError, match="at most 10"):
        validate_packages([f"p{i}" for i in range(11)])


def test_uv_command_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, fake = make(tmp_path, monkeypatch, uv=True)
    st = env.ensure()
    assert st["ready"] and st["installer"] == "uv" and st["error"] is None
    venv, inst = fake.calls
    assert venv[:3] == ["uv", "venv", str(tmp_path / "envs" / "work")]
    py = str(tmp_path / "envs" / "work" / "bin" / "python")
    assert inst[:5] == ["uv", "pip", "install", "--python", py] and "--only-binary=:all:" in inst
    assert inst[-len(BASE_PACKAGES):] == list(BASE_PACKAGES)
    assert st["python"] == py and set(BASE_PACKAGES) <= set(st["packages"])


def test_pip_command_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, fake = make(tmp_path, monkeypatch, uv=False)
    st = env.ensure()
    assert st["ready"] and st["installer"] == "pip"
    venv, inst = fake.calls
    assert venv == [sys.executable, "-m", "venv", str(tmp_path / "envs" / "work")]
    assert inst[1:4] == ["-m", "pip", "install"] and "--only-binary=:all:" in inst


def test_status_before_and_ensure_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, fake = make(tmp_path, monkeypatch, uv=True)
    assert env.status()["ready"] is False and env.python_path() is None and envs.work_bin() is None
    env.ensure()
    n = len(fake.calls)
    again = env.ensure()
    assert again["ready"] and len(fake.calls) == n  # nothing re-run
    assert envs.work_bin() == str(tmp_path / "envs" / "work" / "bin")


def test_settings_packages_are_added_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extra: list[str] = []
    env, fake = make(tmp_path, monkeypatch, uv=True, extra=extra)
    env.ensure()
    extra.append("scipy")
    n = len(fake.calls)
    st = env.ensure()
    assert len(fake.calls) == n + 1 and fake.calls[-1][-1] == "scipy" and "scipy" in st["packages"]


def test_install_validates_and_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, fake = make(tmp_path, monkeypatch, uv=True)
    with pytest.raises(EnvError):
        env.install(["--index-url=http://evil"])
    assert fake.calls == []  # refused before anything ran
    st = env.install(["seaborn>=0.13"])
    assert st["ready"] and "seaborn>=0.13" in st["packages"] and fake.calls[-1][-1] == "seaborn>=0.13"


def test_failure_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, _ = make(tmp_path, monkeypatch, uv=True, fail_on="install")
    st = env.ensure()
    assert st["ready"] is False and "failed" in st["error"] and "No matching distribution" in st["error"]
    assert env.python_path() is None  # a half-built venv never reads as ready


def test_timeout_and_missing_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envs.shutil, "which", lambda n: "/usr/bin/uv")

    def slow(cmd: list[str], **kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd, 600)
    assert "timed out" in WorkEnv(tmp_path, lambda: {}, runner=slow).ensure()["error"]

    def gone(cmd: list[str], **kw: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory")
    assert "could not run" in WorkEnv(tmp_path / "b", lambda: {}, runner=gone).ensure()["error"]


def test_python_install_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os.tools import Toolbox
    env, fake = make(tmp_path, monkeypatch, uv=True)
    tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    spec = tb.specs["python_install"]
    assert (spec.group, spec.danger, tb.default_mode(spec)) == ("code", "external", "ask"), "under alwaysAsk by default"
    ctx: dict[str, Any] = {}
    assert "not available" in asyncio.run(spec.fn(ctx, packages=["scipy"]))["error"]  # no env wired yet
    tb.work_env = env
    bad = asyncio.run(spec.fn(ctx, packages=["git+https://x/y"]))
    assert bad["error"] and bad["field"] == "packages"
    ok = asyncio.run(spec.fn(ctx, packages=["scipy"]))
    assert ok["installed"] == ["scipy"] and ok["ready"]


def test_uv_venv_is_seeded_with_pip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`uv venv` makes a venv with no pip; the shell has this bin first on PATH, so `pip install` there must exist."""
    env, fake = make(tmp_path, monkeypatch, uv=True)
    env.ensure()
    assert "--seed" in fake.calls[0]
