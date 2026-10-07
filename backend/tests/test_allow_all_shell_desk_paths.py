"""Allow everything does not card ordinary shell commands in a desk chat: a desk's own workspace (`<data>/cowork/<id>`) is
the agent's folder, not Grain's data, and a `cd` earlier in the line moves where later relative words are judged from.
The rest of the data folder, other desks, credential stores and the delete floor still card.

Run: python -m pytest -q backend/tests/test_allow_all_shell_desk_paths.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="grain-desk-paths-"))

from personal_os import autoreview, permrules, shell  # noqa: E402
from personal_os.db import data_dir_from_env  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

DATA = Path(os.path.realpath(data_dir_from_env()))
WS = Workspace(DATA)
DESK, OTHER = "4d647d5807994fbb", "de9e6367f60d4e96"
DROOT = WS.ensure(DESK)
WS.ensure(OTHER)
WORK = str(DROOT / "work")
(DATA / "personal-os.db").touch()
REPO = os.path.expanduser("~/Desktop/Personal OS")

BENIGN = [
    f'cd "{REPO}" && git worktree remove --force .opencode-worktrees/model-tiers && git worktree prune',
    f'cd "{REPO}/.opencode-worktrees/model-tiers" && ln -s ../../node_modules node_modules && npm run typecheck',
    "cd ~/.grain 2>/dev/null || cd ~; mkdir -p hello-test-repo && cd hello-test-repo && git init -q && git add -A",
    f'cd "{DROOT}" && mkdir -p work/hello-repo && cd work/hello-repo && git init -q && echo hi > README.md',
    "mkdir -p build && touch build/out.txt",
    f"rm -rf {WORK}/build",
]
GUARDED = [
    f"cat {DATA}/personal-os.db",
    f"cat {DATA}/.auth_token",
    f"ls {DATA}/cowork/{OTHER}",
    f"cd {DATA} && cat personal-os.db",
    f"cd {DROOT} && cp work/x ../{OTHER}/work/x",
    f"ln -s {DATA}/personal-os.db {WORK}/db && cat {WORK}/db",
    "cat ~/.ssh/id_rsa",
    "cp /Applications/Grain.app/Contents/Info.plist .",
]


class _TB:
    workspace = WS


CTX: dict[str, Any] = {"desk_id": DESK, "conversation_id": "c-desk-paths"}


def _fenced(cmd: str) -> bool:
    """What subagents._exec / the chat loop compute for allow_all."""
    roots, cwd = shell.perm_where(_TB(), CTX)
    args = {"command": cmd}
    perm = permrules.resolve("shell_run", args, "on", False, rules={}, roots=roots, cwd=cwd)
    return perm.kind in ("external_directory", "doom_loop") or bool(shell.floor(_TB(), args, CTX))


@pytest.mark.parametrize("cmd", BENIGN)
def test_desk_commands_do_not_ask(cmd: str) -> None:
    v = permrules.evaluate("shell_run", {"command": cmd}, {}, roots=[WORK])
    assert v.action != "ask" and v.kind != "external_directory", (cmd, v.external)
    assert permrules.allow_all_floor("shell_run", {"command": cmd}, WORK, [WORK]) is None
    assert permrules.allow_all_floor("shell_run", {"command": cmd, "unsandboxed": True}, WORK, [WORK]) is None


@pytest.mark.parametrize("cmd", BENIGN)
def test_desk_commands_run_under_allow_all(cmd: str) -> None:
    assert autoreview.route("allow_all", mode="on", danger="external", fenced=_fenced(cmd)) == "run"


@pytest.mark.parametrize("cmd", GUARDED)
def test_grain_data_other_desks_and_credentials_still_ask(cmd: str) -> None:
    v = permrules.evaluate("shell_run", {"command": cmd}, {}, roots=[WORK])
    assert v.action == "ask" and v.kind == "external_directory", cmd
    assert permrules.touches_protected(cmd, WORK, [WORK]), cmd
    assert autoreview.route("allow_all", mode="on", danger="external", fenced=_fenced(cmd)) == "card"


def test_no_desk_no_exemption() -> None:
    v = permrules.evaluate("shell_run", {"command": f"cd {DROOT} && mkdir x"}, {})
    assert v.kind == "external_directory"


def test_delete_floor_kept() -> None:
    assert permrules.allow_all_floor("shell_run", {"command": "rm ~/Desktop/x.md"}, WORK, [WORK])[0] == "destructive"
    assert permrules.allow_all_floor("shell_run", {"command": f'cd "{REPO}" && rm -rf build'}, WORK, [WORK])[0] == "destructive"
    assert permrules.allow_all_floor("shell_run", {"command": "git push --force"}, WORK, [WORK])[0] == "destructive"
    assert _fenced("rm ~/Desktop/x.md")
