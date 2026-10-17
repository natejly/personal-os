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

Optional flags tie a session to the app (the coding extension's hook bridge). The hook only ever narrows, by printing
deny or ask: it never prints allow or a rewritten input, so Claude Code's own prompt and rules still have the last word.
- `--cap N --counter FILE`: every tool call is counted in FILE, and one past N is denied (the step limit of a run).
- `--bridge URL --session ID --gate`: the call is reported to the app, which asks the extensions that subscribed; the
  strictest of its answer and the local floor wins. If it does not answer in time, the call asks. The per-session key
  is in the environment (GRAIN_HOOK_KEY).
- `--lifted`: Allow everything's session. The local floor stays dropped, only a deny from the app counts, and a failed
  bridge does nothing.
- A PostToolUse event is reported to `--bridge` (when given) and never changes anything.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import urllib.request
from typing import Any

# A crash fallback: a command that may delete, wipe or push asks; anything else gets no opinion.
RISKY = re.compile(r"\b(rm|unlink|srm|shred|dd|diskutil|mkfs\w*|newfs\w*|security|find|git)\b")
WIPES = ("diskutil", "dd to", "formatting")
GIT_DIR = re.compile(r"(^|[\s/'\"=])\.git($|[\s/'\"*])")
ASK_HINT = " If this is not needed, use a path inside the session's worktree or a temp folder instead."
GATE_TIMEOUT_S, POST_TIMEOUT_S = 3, 1  # what the app is given to answer a gate, and to take a report
STRING_CAP = 2_000  # characters of any one string sent to the app
KEY_ENV = "GRAIN_HOOK_KEY"


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


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args[:-1] else None


def _clip(value: Any) -> Any:
    """The event as it is sent to the app: every string cut, so the request stays small. The app scrubs it."""
    if isinstance(value, str):
        return value[:STRING_CAP]
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:100]]
    return value


def _post(args: list[str], path: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    """POST `body` to the app's per-session hook route and return its JSON answer. Raises on any failure."""
    req = urllib.request.Request(f"{_flag(args, '--bridge')}/coding-hooks/{_flag(args, '--session')}/{path}",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Grain-Hook-Key": os.environ.get(KEY_ENV, "")})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the URL is the app's own loopback address
        return json.load(resp)


def _event_body(event: dict[str, Any], lifted: bool) -> dict[str, Any]:
    body = {"tool": event.get("tool_name"), "input": _clip(event.get("tool_input") or {}), "cwd": event.get("cwd"),
            "call_id": event.get("tool_use_id"), "permission_mode": event.get("permission_mode"), "lifted": lifted}
    if event.get("hook_event_name") == "PostToolUse":
        response = event.get("tool_response")
        body.update(result_preview=_clip(response if isinstance(response, str) else json.dumps(response, default=str)),
                    duration_ms=event.get("duration_ms"))
    return body


def _count(path: str) -> int:
    """Tool calls so far, this one included: one byte appended per call, so the file's size is the count."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, b".")
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


STRICTNESS = {"deny": 2, "ask": 1}


def strictest(*verdicts: tuple[str, str] | None) -> tuple[str, str] | None:
    """The deny over the ask over nothing."""
    found = [v for v in verdicts if v]
    return max(found, key=lambda v: STRICTNESS[v[0]]) if found else None


def bridge_verdict(args: list[str], event: dict[str, Any], lifted: bool) -> tuple[str, str] | None:
    """What the app's extensions say about this call. Only a deny or an ask is ever taken from the answer, and silence
    is never an allow: no usable answer asks. A lifted session (Allow everything) has no asks, so there only a deny counts."""
    try:
        answer = _post(args, "pre", _event_body(event, lifted), GATE_TIMEOUT_S)
        verdict, reason = answer.get("verdict"), str(answer.get("reason") or "")[:300]
    except Exception:  # noqa: BLE001 - the app is gone or too slow
        verdict, reason = "unanswered", "Grain's extension check did not answer."
    if verdict is None or (lifted and verdict != "deny"):
        return None
    return (verdict, reason) if verdict == "deny" else ("ask", reason)


def report(args: list[str], event: dict[str, Any], lifted: bool) -> None:
    """A finished tool call, told to the app. Nothing it does can matter to the session."""
    if "--bridge" in args:
        try:
            _post(args, "post", _event_body(event, lifted), POST_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - best effort
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    worktree = _flag(args, "--worktree")
    lifted = "--lifted" in args
    raw = ""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
        if event.get("hook_event_name") == "PostToolUse":
            report(args, event, lifted)
            return 0
        local = None if lifted else decide(event, worktree and os.path.realpath(worktree))
        cap = _flag(args, "--cap")
        over = None
        if cap and _count(_flag(args, "--counter") or "") > int(cap):
            over = ("deny", "Grain's step limit for this run was reached.")
        gated = bridge_verdict(args, event, lifted) if "--gate" in args else None
        verdict = strictest(local, over, gated)
    except Exception:  # fail safe: never allow; a step cap or an extension gate that could not be checked asks (denies when lifted)
        if "--cap" in args or "--gate" in args:
            verdict = ("deny" if lifted else "ask", "Grain could not check this call, so it does not allow it.")
        else:
            verdict = ("ask", "Grain could not check this command, so it asks.") if not lifted and RISKY.search(raw) else None
    if verdict:
        sys.stdout.write(output(*verdict))
    return 0


def command(python: str, worktree: str | None = None, data_dir: str | None = None, bridge: dict[str, Any] | None = None,
            lifted: bool = False, post: bool = False) -> str:
    """The hook's shell command: this interpreter, isolated (-I: no cwd or PYTHONPATH on sys.path, so a file in the
    session's repo cannot stand in for this module) and writing no .pyc (-B, the app bundle is signed). The hook runs in
    the claude daemon's environment, not Grain's, so Grain's data folder is passed in (PERSONAL_OS_DATA_DIR). If the
    interpreter cannot run at all, the fallback asks for every Bash call rather than letting it through unchecked (a
    PostToolUse hook has nothing to ask, so its fallback is silence). `bridge` adds the app-facing flags and the key."""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boot = f"import sys;sys.path.insert(0,{pkg!r});from personal_os.claude_hook import main;sys.exit(main())"
    wt = f" --worktree {shlex.quote(worktree)}" if worktree else ""
    fallback = shlex.quote(output("ask", "Grain's command check could not run, so it asks."))
    env = f"PERSONAL_OS_DATA_DIR={shlex.quote(data_dir)} " if data_dir else ""
    flags = ""
    if bridge and bridge.get("port"):  # the app-facing half: its address, this session and the key that opens its routes
        env += f"{KEY_ENV}={shlex.quote(bridge['key'])} "
        flags = f" --bridge http://127.0.0.1:{bridge['port']} --session {shlex.quote(bridge['session_id'])}"
        flags += " --gate" if bridge.get("gate") else ""
    if bridge and bridge.get("cap"):
        flags += f" --cap {int(bridge['cap'])} --counter {shlex.quote(bridge['counter'])}"
    flags += " --lifted" if lifted else ""
    otherwise = "true" if post else f"printf '%s' {fallback}"
    return f"{env}{shlex.quote(python)} -I -B -c {shlex.quote(boot)}{wt}{flags} || {otherwise}"


def settings_hooks(worktree: str | None = None, bridge: dict[str, Any] | None = None, lifted: bool = False) -> dict[str, Any]:
    """The `hooks` block for `claude --settings`. `bridge` is {port, session_id, key, gate, post, cap, counter}: where the app
    listens, which session the key opens, whether an extension gates calls or watches them, and the step limit with the file
    that counts toward it (a run with a limit and no app address has only `cap`, `counter`, `gate` and `post`).
    Without a bridge it is the Bash floor alone. A bridge widens the
    PreToolUse matcher to every tool only when something needs to see them (a gate, or a step cap), and adds a
    PostToolUse entry only when something listens (`post`). A lifted session has no floor hook of its own, so it gets a
    PreToolUse entry only for what the bridge needs."""
    from . import mac
    data = mac._app_data_dir()
    data_dir = str(data.resolve()) if data else None
    watching = bool(bridge and (bridge.get("gate") or bridge.get("cap")))
    hooks: dict[str, Any] = {}
    if watching or not lifted:
        hooks["PreToolUse"] = [{"matcher": "*" if watching else "Bash", "hooks": [{
            "type": "command", "command": command(sys.executable, worktree, data_dir, bridge, lifted), "timeout": 10}]}]
    if bridge and bridge.get("post"):
        hooks["PostToolUse"] = [{"matcher": "*", "hooks": [{
            "type": "command", "command": command(sys.executable, worktree, data_dir, bridge, lifted, post=True), "timeout": 10}]}]
    return hooks


if __name__ == "__main__":
    sys.exit(main())
