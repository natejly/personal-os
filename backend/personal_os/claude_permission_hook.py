"""Claude Code PermissionRequest hook for Grain's headless Claude Code sessions: the question goes to Grain's approval card.

A `claude -p` session started by the claude-code extension carries this hook in its inline `--settings`. Claude Code runs
it only when the call would otherwise prompt (its own rules and mode did not already allow or deny it). The hook POSTs the
event to the app's loopback route for this session, waits for the user's answer there (a card in the chat that started the
session), and prints the decision Claude Code reads:

    {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
    {"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"deny","message":"..."}}}

Anything that goes wrong (the app is gone, the answer is late or malformed, the event cannot be read) is a deny. It never
prints an allow the app did not give. The wait has a hard ceiling (WAIT_CEILING_S); the hook's `timeout` in the settings is
set above it so the deny is ours, not a timeout kill.

Flags: `--bridge URL --session ID` (where the app listens and which session the key opens). The per-session key is in the
environment (GRAIN_HOOK_KEY), as in claude_hook, never on the command line.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import urllib.request
from typing import Any

KEY_ENV = "GRAIN_HOOK_KEY"
KEY_HEADER = "X-Grain-Hook-Key"
WAIT_CEILING_S = 600     # the longest the hook waits for the user
HOOK_TIMEOUT_S = 660     # what the settings tell Claude Code: above the ceiling
STRING_CAP = 2_000
ROUTE = "/coding-hooks/claude-code/{session}/permission"   # under the app's public prefix for per-session-key routes


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args[:-1] else None


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value[:STRING_CAP]
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value[:100]]
    return value


def output(behavior: str, message: str | None = None) -> str:
    decision: dict[str, Any] = {"behavior": behavior}
    if message and behavior == "deny":
        decision["message"] = message
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}})


def ask(args: list[str], event: dict[str, Any], timeout: float = WAIT_CEILING_S) -> tuple[str, str | None]:
    """(behavior, message) from the app. Raises on any failure; the caller turns that into a deny."""
    body = {"tool_name": event.get("tool_name"), "tool_input": _clip(event.get("tool_input") or {}), "cwd": event.get("cwd"),
            "permission_mode": event.get("permission_mode"), "claude_session_id": event.get("session_id")}
    path = ROUTE.format(session=_flag(args, "--session"))
    req = urllib.request.Request(f"{_flag(args, '--bridge')}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", KEY_HEADER: os.environ.get(KEY_ENV, "")})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the URL is the app's own loopback address
        answer = json.load(resp)
    behavior = answer.get("behavior")
    if behavior not in ("allow", "deny"):
        raise ValueError("the app's answer was not allow or deny")
    return behavior, str(answer.get("message") or "")[:300] or None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        event = json.loads(sys.stdin.read())
        if event.get("hook_event_name") not in (None, "PermissionRequest"):
            return 0   # not ours: no opinion
        ceiling = float(_flag(args, "--ceiling") or WAIT_CEILING_S)
        behavior, message = ask(args, event, min(ceiling, WAIT_CEILING_S))
        sys.stdout.write(output(behavior, message))
    except Exception:  # noqa: BLE001 - every failure is a deny
        sys.stdout.write(output("deny", "Grain could not ask the user, so it did not allow this."))
    return 0


def command(python: str, bridge: dict[str, Any], data_dir: str | None = None) -> str:
    """The hook's shell command: this interpreter, isolated (-I, -B) as in claude_hook.command. `bridge` is {port, session_id,
    key}. If the interpreter cannot run at all the fallback prints the deny."""
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boot = f"import sys;sys.path.insert(0,{pkg!r});from personal_os.claude_permission_hook import main;sys.exit(main())"
    env = f"{KEY_ENV}={shlex.quote(bridge['key'])} " + (f"PERSONAL_OS_DATA_DIR={shlex.quote(data_dir)} " if data_dir else "")
    flags = f" --bridge http://127.0.0.1:{int(bridge['port'])} --session {shlex.quote(bridge['session_id'])}"
    fallback = shlex.quote(output("deny", "Grain's permission check could not run, so it did not allow this."))
    return f"{env}{shlex.quote(python)} -I -B -c {shlex.quote(boot)}{flags} || printf '%s' {fallback}"


def settings_hooks(bridge: dict[str, Any]) -> dict[str, Any]:
    """The `PermissionRequest` entry for `claude --settings` (merged into the session's other hooks)."""
    from . import mac
    data = mac._app_data_dir()
    return {"PermissionRequest": [{"matcher": "*", "hooks": [{
        "type": "command", "command": command(sys.executable, bridge, str(data.resolve()) if data else None), "timeout": HOOK_TIMEOUT_S}]}]}


if __name__ == "__main__":
    sys.exit(main())
