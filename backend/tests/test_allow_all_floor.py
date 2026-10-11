"""Allow everything never asks: deletes, force-pushes, credential and data-folder shell commands, unsandboxed runs, the
email card, doc edits. Auto and Manual are unchanged (they still card the destructive detector's cases).

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
def test_allow_all_runs_destructive_commands_without_a_card(cmd: str, unsandboxed: bool) -> None:
    assert not go("allow_all", sh(cmd, unsandboxed=unsandboxed)) and T.RAN == [cmd], cmd


def test_deny_rule_still_refuses_under_allow_all() -> None:
    assert not go("allow_all", sh("rm -rf ~/x"), {"deny": ["Bash(rm *)"]}) and not T.RAN


def test_rm_in_temp_runs_under_allow_all() -> None:
    assert not go("allow_all", sh("rm -rf /tmp/build && cd /tmp && rm -rf out")) and len(T.RAN) == 1
    assert not go("allow_all", sh("rm -rf build", cwd="/tmp")) and len(T.RAN) == 1


@pytest.mark.parametrize("cmd", ["cat ~/.ssh/id_rsa", "security find-generic-password -s x -w", "bash -c 'cat ~/.ssh/id_ed25519'"])
def test_allow_all_runs_unsandboxed_credential_access_without_a_card(cmd: str) -> None:
    assert not go("allow_all", sh(cmd, unsandboxed=True)) and T.RAN == [cmd], cmd


def test_allow_all_runs_a_grain_data_folder_command_without_a_card() -> None:
    data = os.environ["PERSONAL_OS_DATA_DIR"]
    assert not go("allow_all", sh(f"cp x {data}/x", unsandboxed=True)) and len(T.RAN) == 1


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


def test_allow_all_runs_a_delete_in_a_tainted_reply() -> None:
    assert not go("allow_all", sh("rm -rf ~/Documents/x"), tainted=True) and len(T.RAN) == 1


@pytest.mark.parametrize("name", ["gmail_send", "calendar_propose"])
def test_allow_all_never_cards_mail_or_calendar_cards(name: str) -> None:
    from personal_os import autoreview
    for pmode in ("manual", "auto"):
        assert autoreview.route(pmode, mode="ask", danger="external", hard_forced=True) == "card"
    assert autoreview.route("allow_all", mode="ask", danger="external", hard_forced=True) == "run"
    assert name not in permrules.STILL_ASK


def test_allow_all_applies_doc_edits_directly() -> None:
    from personal_os import tools
    assert tools.doc_edit_applies({"settings": {"permissionMode": "allow_all", "docEditMode": "review"}})
    assert tools.doc_edit_applies({"settings": {"docEditMode": "apply"}})
    assert not tools.doc_edit_applies({"settings": {"permissionMode": "auto", "docEditMode": "review"}})
    assert not tools.doc_edit_applies({"settings": {"permissionMode": "auto"}, "permission_mode": "auto"})
    assert tools.doc_edit_applies({"settings": {"permissionMode": "auto"}, "permission_mode": "allow_all"})


# ---- the same floor handed to Claude Code sessions (codingagents.floor_settings), which run outside the sandbox

def _claude_rules() -> dict[str, list[str]]:
    return permrules.claude_code_floor()["permissions"]


def _bash_hit(rules: list[str], cmd: str) -> bool:
    """Claude Code's Bash(pattern) match, approximated: `*` matches any text, the pattern covers the whole command."""
    import fnmatch
    return any(r.startswith("Bash(") and fnmatch.fnmatchcase(cmd, r[5:-1]) for r in rules)


@pytest.mark.parametrize("cmd", ["git push --force", "git push -f origin main", "git push origin main --force-with-lease",
                                 "git push origin +main", "git push --delete origin x",
                                 "security find-generic-password -s x -w"])
def test_claude_floor_asks_for_what_the_shell_floor_cards(cmd: str) -> None:
    assert permrules.destructive(cmd, cwd=HOME_PROJ) or permrules.touches_protected(cmd, HOME_PROJ), cmd
    assert _bash_hit(_claude_rules()["ask"], cmd), cmd


@pytest.mark.parametrize("cmd", ["diskutil eraseDisk JHFS+ x disk2", "diskutil zeroDisk disk2", "diskutil apfs deleteVolume disk3s1",
                                 "dd if=/dev/zero of=/dev/disk3", "mkfs.ext4 /dev/sda1"])
def test_claude_floor_refuses_disk_wipes(cmd: str) -> None:
    assert permrules.destructive(cmd, cwd=HOME_PROJ), cmd
    assert _bash_hit(_claude_rules()["deny"], cmd), cmd


@pytest.mark.parametrize("cmd", ["ls -la", "git push origin main", "git push --follow-tags", "git push --dry-run origin x",
                                 "git status", "diskutil list", "npm test", "dd if=a of=b"])
def test_claude_floor_leaves_routine_commands_alone(cmd: str) -> None:
    rules = _claude_rules()
    assert not _bash_hit(rules["ask"] + rules["deny"], cmd), cmd


def test_claude_floor_paths_come_from_the_sandbox_lists() -> None:
    from personal_os import mac
    rules = _claude_rules()
    for root in mac.protected_paths():
        pat = "//" + str(root).lstrip("/") + "/**"
        assert f"Edit({pat})" in rules["deny"], root
        assert (f"Read({pat})" in rules["deny"]) is (root.suffix.lower() != ".app"), root
    for d in mac.CRED_HOME_DIRS:
        assert {f"Read(~/{d}/**)", f"Edit(~/{d}/**)"} <= set(rules["ask"]), d
    for f in mac.CRED_HOME_FILES:
        assert f"Read(~/{f})" in rules["ask"], f
    assert {"Read(.env)", "Read(.env.*)", "Read(id_ed25519)", *permrules.CLAUDE_MAIL_SENDS} <= set(rules["ask"])
    assert not set(rules["ask"]) & set(rules["deny"])
    assert not [r for r in rules["ask"] + rules["deny"] if r.endswith(":*)")], "`:*` at the end is the CLI's prefix syntax"


# ---- the Claude Code Bash hook (claude_hook): the shell floor's parser on what a session's command runs

def _hook(cmd: str, cwd: str = HOME_PROJ, worktree: str | None = None) -> str | None:
    from personal_os import claude_hook
    v = claude_hook.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}, worktree)
    return v[0] if v else None


def test_claude_floor_leaves_deletes_to_the_hook() -> None:
    rules = _claude_rules()
    assert not _bash_hit(rules["ask"], "rm -rf /tmp/build"), "a static rm rule would ask before the hook could exempt temp"


@pytest.mark.parametrize("cmd", ["rm -rf ~/Documents/x", "rm a.txt", "unlink ~/x", "find . -name '*.o' -delete",
                                 "sh -c 'rm -rf ~/x'", "bash -c \"rm -rf ~/x\"", "zsh -c 'rm -rf ~/x'", "bash -lc 'rm ~/x'",
                                 "eval 'rm -rf ~/x'", "bash -c \"sh -c 'eval rm -rf ~/x'\"", "sudo rm -rf ~/x",
                                 "ls | xargs rm", "echo $(rm ~/a)", "git push --force",
                                 "rm -rf /tmp/../Users/x", "rm -rf /tmp"])
def test_hook_asks_for_deletes_through_wrappers(cmd: str) -> None:
    assert _hook(cmd) == "ask", cmd


@pytest.mark.parametrize("cmd", ["rm -rf /tmp/build", "rm -rf /private/tmp/x", "rm -rf /tmp/a/*", "bash -c 'rm -rf /tmp/x'",
                                 "rm -rf $TMPDIR/x", "rm -rf /private/var/folders/ab/T/x", "cd /tmp && rm -rf out",
                                 "ls -la", "git status", "npm test"])
def test_hook_lets_temp_deletes_and_routine_work_through(cmd: str) -> None:
    assert _hook(cmd) is None, cmd


def test_hook_exempts_the_sessions_own_worktree_only() -> None:
    w, other = os.path.join(HOME_PROJ, ".claude", "worktrees", "s1"), HOME_PROJ  # outside every temp folder
    assert _hook("rm -rf build node_modules", w, w) is None
    assert _hook(f"rm -rf {w}/build", HOME_PROJ, w) is None
    assert _hook("bash -c 'rm -rf build'", w, w) is None
    assert _hook("rm -rf build", w, None) == "ask"  # a checkout the session was only pointed at
    assert _hook(f"rm -rf {other}/src", w, w) == "ask"  # repo files outside the session's worktree
    assert _hook("rm -rf ../s2", w, w) == "ask"
    assert _hook(f"rm -rf {w}", w, w) == "ask"
    assert _hook("rm -rf .git", w, w) == "ask"


def test_hook_does_not_follow_a_link_out_of_temp() -> None:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="hooklink-"))
    try:
        (tmp / "out").symlink_to("/usr")
        assert _hook(f"rm -rf {tmp}/out/x") == "ask"
        assert _hook(f"rm -rf {tmp}/plain") is None
    finally:
        (tmp / "out").unlink()
        tmp.rmdir()


def test_hook_refuses_grain_data_and_wipes_and_reads_credentials() -> None:
    data = os.environ["PERSONAL_OS_DATA_DIR"]
    assert _hook(f"bash -c 'rm -rf {data}'") == "deny"
    assert _hook("sh -c 'diskutil zeroDisk disk2'") == "deny"
    assert _hook("bash -c 'cat ~/.ssh/id_rsa'") == "ask"
    assert _hook("cat ~/.aws/credentials") == "ask"


def test_hook_main_prints_a_decision_and_fails_safe(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io
    from personal_os import claude_hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/x"}})))
    claude_hook.main([])
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json rm -rf"))
    claude_hook.main([])
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    claude_hook.main([])
    assert capsys.readouterr().out == ""
