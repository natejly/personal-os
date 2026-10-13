"""Credential folders are per platform: keyrings and browser profiles on Linux, DPAPI and browser data on Windows, the
Keychain folders on macOS. Reads, writes, listings and shell path checks all consult the current platform's lists."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import mac, osplat, permrules  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

LINUX_STORES = (".local/share/keyrings/login.keyring", ".mozilla/firefox/abc.default/cookies.sqlite",
                ".config/google-chrome/Default/Login Data", ".config/google-chrome/Default/Cookies",
                ".config/chromium/Default/Web Data", ".password-store/x.gpg", ".config/Grain/anything", ".gnupg/pubring.kbx")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    (h / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(tmp_path / "data"))
    return h


def _tb(home: Path, tmp_path: Path) -> Toolbox:
    cfg: dict[str, Any] = {}
    return Toolbox(None, None, None, lambda: cfg, workspace=Workspace(tmp_path / "data"))  # type: ignore[arg-type]


def _call(tb: Toolbox, name: str, ctx: dict[str, Any], **args: Any) -> Any:
    return asyncio.run(tb.call(name, args, ctx))


def _make(home: Path) -> None:
    for rel in LINUX_STORES:
        f = home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("secret")
    (home / "notes.txt").write_text("hello")


def test_linux_stores_are_sensitive_and_not_readable(home: Path) -> None:
    _make(home)
    with osplat.use("linux"):
        for rel in LINUX_STORES:
            assert mac.sensitive_reason(home / rel), rel
            with pytest.raises(mac.LocalPathError):
                mac.readable_path(str(home / rel))
        assert not mac.sensitive_reason(home / "notes.txt")
        assert not mac.sensitive_reason(home / ".config" / "nvim" / "init.lua")
        assert mac.readable_path(str(home / "notes.txt"))
        assert not mac.sensitive_reason(home / "Library" / "Keychains" / "login.keychain-db")


def test_file_tools_gate_linux_stores(home: Path, tmp_path: Path) -> None:
    _make(home)
    with osplat.use("linux"):
        tb = _tb(home, tmp_path)
        ctx = {"conversation_id": "c1"}
        for rel in LINUX_STORES:
            p = str(home / rel)
            assert tb.fs_needs_ask("read_local_file", {"path": p}, ctx), rel
            assert "approval" in _call(tb, "read_local_file", ctx, path=p)["error"], rel
            assert tb.fs_needs_ask("fs_edit", {"path": p, "old": "s", "new": "t"}, ctx), rel
            assert "approval" in _call(tb, "fs_edit", ctx, path=p, old="secret", new="x")["error"]
            assert (home / rel).read_text() == "secret"
        for d in (".local/share/keyrings", ".mozilla/firefox/abc.default", ".config/google-chrome/Default"):
            assert tb.fs_needs_ask("fs_glob", {"pattern": "*", "root": str(home / d)}, ctx), d
            assert tb.fs_needs_ask("fs_grep", {"pattern": "s", "root": str(home / d)}, ctx), d
            assert tb.fs_needs_ask("fs_mkdir", {"path": str(home / d / "new")}, ctx), d
            assert "approval" in _call(tb, "fs_glob", ctx, pattern="*", root=str(home / d))["error"]
        assert tb.fs_needs_ask("fs_copy", {"src": str(home / "notes.txt"), "dst": str(home / ".mozilla" / "c.txt")}, ctx)
        assert not tb.fs_needs_ask("read_local_file", {"path": str(home / "notes.txt")}, ctx)
        assert not tb.fs_needs_ask("fs_glob", {"pattern": "*", "root": str(home / ".config")}, ctx)
        # with the user's yes the read goes through
        assert "error" not in _call(tb, "read_local_file", {**ctx, "fs_outside_ok": True}, path=str(home / LINUX_STORES[0]))
        # a search rooted in ~ skips the credential folders
        out = _call(tb, "fs_grep", ctx, pattern="secret", root=str(home))
        assert out["count"] == 0, out


def test_shell_commands_naming_linux_stores_are_not_readonly(home: Path) -> None:
    with osplat.use("linux"):
        for c in ("cat ~/.local/share/keyrings/login.keyring", "cat ~/.mozilla/firefox/x/cookies.sqlite",
                  f"cat {home}/.config/google-chrome/Default/Cookies"):
            assert permrules.evaluate("shell_run", {"command": c}, None).action != "allow", c
            assert permrules.touches_protected(c), c
        assert permrules.evaluate("shell_run", {"command": "cat ~/notes.txt"}, None).action == "allow"
        assert not permrules.touches_protected("cat ~/notes.txt")
        floor = permrules.claude_code_floor()["permissions"]["ask"]
        assert "Read(~/.local/share/keyrings/**)" in floor and "Read(~/.mozilla/**)" in floor
        assert not any("Library/Application Support" in r for r in floor)
    with osplat.use("darwin"):
        assert permrules.evaluate("shell_run", {"command": "cat ~/.local/share/keyrings/login.keyring"}, None).action == "allow"
        floor = permrules.claude_code_floor()["permissions"]["ask"]
        assert any(r.startswith("Read(~/Library/Application Support/Google/Chrome/") for r in floor)


def test_darwin_lists_unchanged(home: Path) -> None:
    with osplat.use("darwin"):
        assert mac.sensitive_reason(home / "Library/Keychains/login.keychain-db")
        assert mac.sensitive_reason(home / "Library/Application Support/Google/Chrome/Default/Cookies")
        assert not mac.sensitive_reason(home / ".config/google-chrome/Default/Cookies")
        assert not mac.sensitive_reason(home / ".mozilla/firefox/a/cookies.sqlite")
        assert mac.sensitive_reason(home / ".ssh/config")
        assert mac.cred_home_dirs() == mac.CRED_HOME_DIRS
        assert "Keychains" in mac.scope_summary()["sensitive"]


def test_ssh_stays_covered_on_every_platform(home: Path) -> None:
    for plat in ("linux", "win32"):
        with osplat.use(plat):
            assert mac.sensitive_reason(home / ".ssh" / "config"), plat
            assert mac.sensitive_reason(home / ".gnupg" / "pubring.kbx"), plat


def test_win32_lists_are_relative_and_found(home: Path) -> None:
    with osplat.use("win32") as plat:
        dirs = plat.paths.keychain_dirs()
        assert dirs and not any(d.startswith("/") or ":" in d for d in dirs)
        assert {"AppData/Roaming/Microsoft/Credentials", "AppData/Local/Google/Chrome/User Data"} <= set(dirs)
        assert mac.sensitive_reason(home / "AppData/Local/Google/Chrome/User Data/Default/Cookies")
        assert mac.sensitive_reason(home / "AppData/Roaming/Microsoft/Protect/S-1-5/key")
        assert mac.sensitive_reason(home / "AppData/Roaming/Mozilla/Firefox/Profiles/x/cookies.sqlite")
        assert not mac.sensitive_reason(home / "AppData/Roaming/Code/settings.json")
        assert plat.paths.system_keychains() == ()
