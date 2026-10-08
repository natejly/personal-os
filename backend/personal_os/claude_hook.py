"""Claude Code PreToolUse hook for Grain's coding sessions: the allow-all floor judged on what a Bash command runs.

Claude Code's own Bash rules match a command as written, so `bash -c 'rm -rf ~/x'`, `eval`, `sudo`, `xargs rm`,
`find -exec rm`, `$(...)` and chains slip past them. Every `claude --bg` start and resume Grain makes carries this hook
in its inline `--settings` (`settings_hooks`). It reads the hook event JSON on stdin and judges `tool_input.command` with
the same parser and lists as the shell floor (permrules.hardline / destructive / touches_protected), which see through
those wrappers:

- deny: the hardline list, disk wipes / formatting, and any command that names Grain's own data folder or app.
- ask: what Allow everything cards (deletes that skip the Trash, force-pushes, credential stores, the Keychain CLI).
  In a --bg session an ask shows as needs_you, in bypassPermissions too.
- nothing (no opinion, Claude Code's own rules and mode decide) for everything else, so routine work stays fast.

Deletes are not asked for when every target is inside a temp folder (permrules.temp_roots: /tmp, $TMPDIR,
/private/var/folders) or inside the session's own Grain-made worktree (`--worktree`), which is the session's own
workspace. The worktree root itself and its `.git` still ask. If judging fails, a command with a destructive verb asks.
Prints nothing but the decision; the reason names the rule, never the command's arguments beyond the paths it deletes.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Any

# A crash fallback: a command that may delete, wipe or push asks; anything else gets no opinion.
RISKY = re.compile(r"\b(rm|unlink|srm|shred|dd|diskutil|mkfs\w*|newfs\w*|security|find|git)\b")
WIPES = ("diskutil", "dd to", "formatting")
GIT_DIR = re.compile(r"(^|[\s/'\"=])\.git($|[\s/'\"*])")
ASK_HINT = " If this is not needed, use a path inside the session's worktree or a temp folder instead."


def decide(event: dict[str, Any], worktree: str | None = None) -> tuple[str, str] | None:
    """(decision, reason) for one PreToolUse event, or None for no opinion."""
    from . import permrules
    if event.get("tool_name") != "Bash":
        return None
    cmd = str((event.get("tool_input") or {}).get("command") or "")
    cwd = event.get("cwd") or worktree or None
    if why := permrules.hardline(cmd):
        return "deny", f"Grain never runs this ({why})."
    if why := permrules.touches_protected(cmd, cwd):
        if "Grain's own" in why:
            return "deny", f"Grain's own data folder and app are off limits ({why.split(':')[0]})."
        return "ask", f"Grain asks before this: {why}."
    scratch = [worktree] if worktree else []
    why = permrules.destructive(cmd, cwd, scratch)
    if why and why.startswith(WIPES):
        return "deny", f"Grain never runs this ({why})."
    if why:
        return "ask", f"Grain asks before this: {why}." + (ASK_HINT if "delete" in why else "")
    if scratch and permrules.destructive(cmd, cwd) and GIT_DIR.search(cmd):
        return "ask", "Grain asks before deleting the worktree's .git."
    return None


def output(decision: str, reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                              "permissionDecisionReason": reason}})


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    worktree = args[args.index("--worktree") + 1] if "--worktree" in args[:-1] else None
    raw = ""
    try:
        raw = sys.stdin.read()
        verdict = decide(json.loads(raw), worktree and os.path.realpath(worktree))
    except Exception:  # fail safe: never allow, ask when the command could be destructive
        verdict = ("ask", "Grain could not check this command, so it asks.") if RISKY.search(raw) else None
    if verdict:
        sys.stdout.write(output(*verdict))
    return 0


def command(python: str, worktree: str | None = None, data_dir: str | None = None) -> str:
    """The hook's shell command: this interpreter, isolated (-I: no cwd or PYTHONPATH on sys.path, so a file in the
    session's repo cannot stand in for this module) and writing no .pyc (-B, the app bundle is signed). The hook runs in
    the claude daemon's environment, not Grain's, so Grain's data folder is passed in (PERSONAL_OS_DATA_DIR). If the
    interpreter cannot run at all, the fallback asks for every Bash call rather than letting it through unchecked."""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boot = f"import sys;sys.path.insert(0,{pkg!r});from personal_os.claude_hook import main;sys.exit(main())"
    wt = f" --worktree {shlex.quote(worktree)}" if worktree else ""
    fallback = shlex.quote(output("ask", "Grain's command check could not run, so it asks."))
    env = f"PERSONAL_OS_DATA_DIR={shlex.quote(data_dir)} " if data_dir else ""
    return f"{env}{shlex.quote(python)} -I -B -c {shlex.quote(boot)}{wt} || printf '%s' {fallback}"


def settings_hooks(worktree: str | None = None) -> dict[str, Any]:
    """The `hooks` block for `claude --settings`."""
    from . import mac
    data = mac._app_data_dir()
    cmd = command(sys.executable, worktree, str(data.resolve()) if data else None)
    return {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd,
                                                          "timeout": 10}]}]}


if __name__ == "__main__":
    sys.exit(main())
