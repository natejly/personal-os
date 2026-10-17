"""pi as a coding-session driver (sessions.py): `pi --mode json "<task>"` under the OS sandbox, as a background job.

pi is a terminal coding agent installed separately on this Mac (`npm i -g @mariozechner/pi-coding-agent`). `--mode json`
runs one prompt without a terminal UI and prints every session event as a JSON line, the first being the session header
(`{"type":"session","id":...}`). A follow-up is a new process with `--session <id>`, which reopens that saved session.

pi has no permission prompts of its own: every tool it has (read, bash, edit, write, ...) runs as soon as the model calls
it. So it runs the way OpenCode does: under the OS sandbox (sandbox.shell_profile: it may write anywhere but Grain's own
data folder and app, the credential stores and the files that run code later), with outbound https only, in the shell job
registry so Stop and app shutdown end its process group.

What it keeps from pi's own install: model and sign-in (`pi` then `/login`, or its settings under ~/.pi/agent) and its
session files under ~/.pi/agent/sessions, so `pi -r` in a terminal lists the sessions started here. Grain passes
`--model` only when the caller names one; nothing of Grain's provider setup is injected, and the scrubbed environment
carries no API keys, so a provider pi reads from an environment variable is not available to it here.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import tempfile
from typing import Any

from ... import osplat, sandbox, shell

# npm -g lands in Homebrew's or the system prefix, a user prefix (~/.npm-global), a node version manager's bin, or ~/.local/bin.
BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin", "~/.local/bin", "~/.bun/bin", "~/.volta/bin")
INSTALL_HINT = "pi is not installed on this Mac. The user can install it with `npm i -g @mariozechner/pi-coding-agent`, then try again."
ALLOW_HOSTS = ["*:443"]


def binary() -> str | None:
    dirs = [os.path.expanduser(d) for d in BIN_DIRS] + sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
    for d in dirs:
        p = os.path.join(d, "pi")
        if os.access(p, os.X_OK):
            return p
    return shutil.which("pi")


def argv(exe: str, prompt: str, model: str | None = None, session: str | None = None) -> list[str]:
    """`-p` makes pi process the prompt and exit; `--mode json` prints its events as JSON lines. A message starting with "-" would be read as a flag and one
    starting with "@" as a file to attach, so either gets a leading word."""
    lead = f"Task: {prompt}" if prompt.startswith(("-", "@")) else prompt
    return [exe, "-p", "--mode", "json", *(["--model", model] if model else []), *(["--session", session] if session else []), lead]


def summarize(raw: str) -> tuple[str, str | None, str | None]:
    """(what to show, session id, error) from `--mode json` output: the assistant's text and one line per tool call, in
    order. `error` is set when the last assistant message ended in an error or abort (pi still exits 0 then). Lines that
    are not events are kept as they are (a crash message, an install notice)."""
    out: list[str] = []
    session: str | None = None
    error: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            if line:
                out.append(line)
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "session" and isinstance(ev.get("id"), str):
            session = session or ev["id"]
        elif kind == "tool_execution_start":
            args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
            what = args.get("command") or args.get("pattern") or args.get("path") or ""
            out.append(f"[{ev.get('toolName') or 'tool'}] {str(what)[:200]}".rstrip())
        elif kind == "message_end" and isinstance(ev.get("message"), dict) and ev["message"].get("role") == "assistant":
            msg: dict[str, Any] = ev["message"]
            for part in msg.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip():
                    out.append(str(part["text"]).rstrip())
            error = None
            if msg.get("stopReason") in ("error", "aborted"):
                error = str(msg.get("errorMessage") or f"request {msg['stopReason']}")[:300]
                out.append(f"error: {error}")
    return "\n".join(out).strip(), session, error


async def launch(tb: Any, ctx: dict[str, Any], prompt: str, *, cwd: str, session: str | None = None, model: str | None = None,
                 conversation_id: str | None = None, run_id: str | None = None, pool: str = "coding",
                 max_background: int | None = None) -> shell.Job:
    """Start pi under the OS sandbox as a background job with no time limit. Raises shell.ShellError when it cannot."""
    exe = binary()
    if not exe:
        raise shell.ShellError(INSTALL_HINT)
    if not shell.sandbox_available():
        raise shell.ShellError("The OS sandbox pi runs in is not available here (it needs macOS sandbox-exec), so nothing was run.")
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="pos-pi-"))
    env = shell.scrubbed_env(tmp)
    env["PATH"] = f"{os.path.dirname(exe)}:{env['PATH']}"  # pi is a node script: node sits beside it in an npm prefix
    env["npm_config_cache"] = os.path.join(tmp, "npm-cache")
    env["PI_OFFLINE"] = "1"  # no update check or telemetry at startup; the model's own requests are unaffected
    # loopback: a model served on this Mac (a local proxy) is a common pi provider
    profile = sandbox.shell_profile([tmp], network=False, allow_hosts=ALLOW_HOSTS, loopback=True)
    try:
        return await tb.shell.start(osplat.current().sandbox.wrap(argv(exe, prompt, model, session), profile),
                                    command=f"pi {prompt[:160]!r}", cwd=cwd, env=env, tmp=tmp,
                                    conversation_id=conversation_id, run_id=run_id, background=True, timeout=None,
                                    notify=False, max_background=max_background or 4, pool=pool, on_timeout="kill")
    except shell.ShellError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
