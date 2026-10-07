"""Allow everything runs routine work without a card and keeps one floor: Grain's data and credential stores, deletes that
skip the Trash, disk wipes, force-pushes and the email card. Auto and Manual are unchanged.

Pure detector cases, then the real chat loop (app._chat_stream) with a scripted model and stand-in tools.
Run: python -m pytest -q backend/tests/test_allow_all_floor.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_permrules_loop as T  # noqa: E402
from personal_os import permissions, permrules  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

appmod = T.appmod
HOME_PROJ = os.path.expanduser("~/proj")
CALLS: list[str] = []


# ---- the detector

@pytest.mark.parametrize("cmd", [
    "rm -rf ~/Documents/x", "rm a.txt", "rm -f -- notes.md", "unlink ~/x", "shred secret.txt", "srm ~/x",
    "find . -name '*.o' -delete", r"find ~ -exec rm {} ;", "ls | xargs rm", "find . -print0 | xargs -0 rm -f",
    "git push --force", "git push -f origin main", "git push --force-with-lease", "git -C repo push origin +main",
    "diskutil eraseDisk JHFS+ x disk2", "diskutil zeroDisk disk2", "diskutil apfs deleteVolume disk3s1",
    "dd if=/dev/zero of=/dev/disk3", "mkfs.ext4 /dev/sda1",
    "make && rm -rf dist", "true; rm x", "false || rm x", "sudo rm -rf ~/d", "env A=1 nohup rm ~/x", "/bin/rm ~/x",
    "(rm -rf ~/x)", "{ rm x; }", "echo $(rm -f ~/a)", "echo `rm ~/b`", "bash -c 'rm -rf ~/y'", "eval 'rm x'",
    "rm $FILE", "rm ../../outside", "cd ~/proj && rm -rf build", "cd /tmp && cd ~ && rm x",
])
def test_destructive_patterns_ask(cmd: str) -> None:
    assert permrules.destructive(cmd, cwd=HOME_PROJ), cmd


@pytest.mark.parametrize("cmd", [
    "ls -la", "git push origin main", "git push", "rm -rf /tmp/build", "rm -rf /private/tmp/x /var/folders/ab/T/y",
    "rm -rf $TMPDIR/out", "rm -rf ${TMPDIR}/out", "rm -rf /tmp/*", "cd /tmp && rm -rf build", "cd /tmp/w; rm -f *.o",
    "find /tmp/x -name '*.o' -delete", "dd if=a of=/dev/null", "echo rm -rf ~", "grep -r 'rm -rf' .", "mv a b",
    "git commit -m 'force push later'", "npm run build",
])
def test_no_false_positives(cmd: str) -> None:
    assert permrules.destructive(cmd, cwd=HOME_PROJ) is None, cmd


def test_relative_paths_follow_cwd_and_scratch() -> None:
    assert permrules.destructive("rm -rf build", cwd="/tmp/w") is None
    assert permrules.destructive("rm -rf build", cwd="/Users/someone/proj")
    assert permrules.destructive("rm -rf work/old", cwd="/desk", scratch=["/desk/work"]) is None
    assert permrules.allow_all_floor("write_local_file", {"command": "rm -rf ~"}) is None


def test_fresh_install_defaults_to_auto_and_keeps_the_always_ask_list() -> None:
    assert permissions.DEFAULTS["permissionMode"] == "auto"
    assert set(permissions.DEFAULTS["alwaysAsk"]) >= {"gmail_send", "calendar_delete", "trash_local_file", "move_local_file",
                                                     "run_shortcut", "python_install", "schedule_task"}


# ---- the chat loop

async def _shell(ctx: dict[str, Any], command: str, unsandboxed: bool = False, cwd: str | None = None) -> Any:
    T.RAN.append(command)
    return {"ok": True}


async def _tool(ctx: dict[str, Any], x: str = "") -> Any:
    CALLS.append(x)
    return {"ok": True}


shell_spec = ToolSpec("shell_run", "fake shell", _obj({"command": {"type": "string"}, "unsandboxed": {"type": "boolean"},
                                                       "cwd": {"type": "string"}}, ["command"]), _shell, "shell", "executes")
shell_spec.default = "ask"
shell_spec.force_ask = lambda args, ctx: bool(args.get("unsandboxed"))  # the real shell_run's forced card
appmod.toolbox.specs["shell_run"] = shell_spec
LOCKED = {"trash_local_file": "external", "move_local_file": "external", "run_shortcut": "external",
          "python_install": "external", "calendar_delete": "external", "schedule_task": "schedules"}
for n, d in LOCKED.items():
    appmod.toolbox.specs[n] = ToolSpec(n, n, _obj({"x": {"type": "string"}}, []), _tool, "misc", d)


async def _coding(ctx: dict[str, Any], x: str = "", permission_mode: str | None = None) -> Any:
    return await _tool(ctx, x)


# The coding agents keep their real gates (force_ask on taint, the bypass card) around a stand-in run.
CODING = ("opencode_run", "coding_session_start")
for n in CODING:
    real = appmod.toolbox.specs[n]
    s = ToolSpec(n, n, _obj({"x": {"type": "string"}, "permission_mode": {"type": "string"}}, []), _coding, real.group, real.danger)
    s.default, s.force_ask, s.force_card, s.taint_ok = real.default, real.force_ask, real.force_card, real.taint_ok
    appmod.toolbox.specs[n] = s


def go(pmode: str, call: dict[str, Any], rules: dict | None = None, shell_mode: str | None = None,
       tainted: bool = False) -> list[dict[str, Any]]:
    cid = T.setup(rules, permissionMode=pmode, alwaysAsk=permissions.DEFAULTS["alwaysAsk"])
    appmod.convos.update(cid, {"settings": {"tools": {"shell_run": shell_mode} if shell_mode else {},
                                            **({"tainted": True, "taint_sources": ["fetch_url"]} if tainted else {})}})
    CALLS.clear()
    return T.cards(T.drive(cid, [[call], []], ["deny"] * 3))


def sh(command: str, **kw: Any) -> dict[str, Any]:
    return {"id": "c0", "name": "shell_run", "arguments": json.dumps({"command": command, **kw})}


def tool(name: str) -> dict[str, Any]:
    return {"id": "c0", "name": name, "arguments": json.dumps({"x": "a"})}


def test_allow_all_runs_unsandboxed_shell_without_a_card_and_logs_it() -> None:
    assert not go("allow_all", sh("make all", unsandboxed=True)) and T.RAN == ["make all"]
    rows = [r for r in appmod.approval_log.history(appmod.db, tool="shell_run")["items"] if r["scope"] == "allow-all"]
    assert rows and rows[0]["note"] == "allowed (allow-all mode)"


@pytest.mark.parametrize("name", sorted(LOCKED))
def test_allow_all_skips_the_routine_always_ask_tools(name: str) -> None:
    assert not go("allow_all", tool(name)) and CALLS == ["a"], name


@pytest.mark.parametrize("pmode", ["manual", "auto"])
@pytest.mark.parametrize("name", sorted(LOCKED))
def test_auto_and_manual_still_card_them(pmode: str, name: str) -> None:
    assert len(go(pmode, tool(name))) == 1 and not CALLS, (pmode, name)


@pytest.mark.parametrize("pmode", ["manual", "auto"])
def test_auto_and_manual_still_card_unsandboxed_shell(pmode: str) -> None:
    c = go(pmode, sh("make all", unsandboxed=True))
    assert len(c) == 1 and not T.RAN
    assert (c[0].get("permission") or {}).get("kind") != "destructive", "the floor is Allow everything's alone"


@pytest.mark.parametrize("cmd", ["rm -rf ~/Documents/x", "git push --force", "find ~ -name x -delete", "ls | xargs rm",
                                 "diskutil apfs deleteVolume disk3s1", "shred ~/a"])
@pytest.mark.parametrize("unsandboxed", [False, True])
def test_allow_all_floor_cards_destructive_commands(cmd: str, unsandboxed: bool) -> None:
    c = go("allow_all", sh(cmd, unsandboxed=unsandboxed))
    assert len(c) == 1 and not T.RAN, cmd
    perm = c[0]["permission"]
    assert c[0]["forced"] and perm["kind"] == "destructive" and perm["subject"] and not perm["session"]


def test_floor_beats_an_allow_rule_and_a_tool_set_on() -> None:
    c = go("allow_all", sh("rm -rf ~/x"), {"allow": ["Bash(rm *)"]}, shell_mode="on")
    assert len(c) == 1 and not T.RAN and c[0]["permission"]["kind"] == "destructive"


def test_deny_rule_still_refuses_under_allow_all() -> None:
    assert not go("allow_all", sh("rm -rf ~/x"), {"deny": ["Bash(rm *)"]}) and not T.RAN


def test_rm_in_temp_runs_under_allow_all() -> None:
    assert not go("allow_all", sh("rm -rf /tmp/build && cd /tmp && rm -rf out")) and len(T.RAN) == 1
    assert not go("allow_all", sh("rm -rf build", cwd="/tmp")) and len(T.RAN) == 1


@pytest.mark.parametrize("cmd", ["cat ~/.ssh/id_rsa", "cp ~/.aws/credentials /tmp/x", "tar czf /tmp/k.tgz ~/Library/Keychains",
                                 "security find-generic-password -s x -w", "bash -c 'cat ~/.ssh/id_ed25519'"])
def test_unsandboxed_credential_access_still_cards_under_allow_all(cmd: str) -> None:
    c = go("allow_all", sh(cmd, unsandboxed=True))
    assert len(c) == 1 and not T.RAN and c[0]["forced"] and c[0]["permission"]["kind"] == "external_directory", cmd


def test_grain_data_folder_still_cards_under_allow_all() -> None:
    data = os.environ["PERSONAL_OS_DATA_DIR"]
    for unsandboxed in (False, True):
        c = go("allow_all", sh(f"cp x {data}/x", unsandboxed=unsandboxed))
        assert len(c) == 1 and not T.RAN, unsandboxed
    assert permrules.touches_protected(f"sqlite3 {data}/grain.db .dump")
    assert permrules.touches_protected("ls -la ~/proj") is None


@pytest.mark.parametrize("name", CODING)
def test_allow_all_runs_coding_agents_in_a_tainted_reply(name: str) -> None:
    assert not go("allow_all", tool(name), tainted=True) and CALLS == ["a"], name


@pytest.mark.parametrize("pmode", ["manual", "auto"])
@pytest.mark.parametrize("name", CODING)
def test_auto_and_manual_still_card_coding_agents_when_tainted(pmode: str, name: str) -> None:
    c = go(pmode, tool(name), tainted=True)
    assert len(c) == 1 and c[0]["forced"] and not CALLS, (pmode, name)


@pytest.mark.parametrize("pmode", ["manual", "auto"])
def test_auto_and_manual_still_card_a_bypass_session(pmode: str) -> None:
    call = {"id": "c0", "name": "coding_session_start", "arguments": json.dumps({"x": "a", "permission_mode": "bypassPermissions"})}
    assert len(go(pmode, call)) == 1 and not CALLS


def test_floor_still_cards_in_a_tainted_reply() -> None:
    c = go("allow_all", sh("rm -rf ~/Documents/x"), tainted=True)
    assert len(c) == 1 and not T.RAN and c[0]["permission"]["kind"] == "destructive"
