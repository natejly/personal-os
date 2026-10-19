"""The headless streaming driver: one `claude -p` process per session, spoken to in stream-json over its pipes.

    claude -p --output-format stream-json --input-format stream-json --verbose [flags] --settings <json>

Stdin stays open for the life of the session. Every user turn, the first prompt included, is one JSON line
`{"type":"user","message":{"role":"user","content":"..."}}`; closing stdin is how the process is told to finish. Stdout is
newline-delimited JSON, read line by line and handed to `on_line`; a line that is not JSON is passed on as text, never raised.

Why not `shell.run_fixed` (what the coding extension runs Claude Code through)? It starts a job, waits for it to exit and
returns the collected output: it has no stdin to keep open and nothing to read while the process runs. This module starts the
process with `asyncio.create_subprocess_exec` in the same spirit: no shell, the allowlisted environment of `shell.scrubbed_env`
(plus the user's PATH from `claude_env`), the claude binary the coding extension found, unsandboxed (it runs as the user, like
a coding session), in its own process group so a hard kill reaches the tools it started.

Stopping is SIGINT first (the CLI records the turn and exits cleanly), a grace period, then SIGKILL of the group. Never SIGTERM.

The pure helpers (`argv`, `user_line`, `parse`, `result_facts`) have no I/O so tests can drive them directly.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from typing import Any, Awaitable, Callable

from ... import shell
from ..coding import sessions as coding_sessions

PIPE_LIMIT = 16 * 1024 * 1024   # the longest stdout line read; a tool result can be large
STDERR_TAIL = 2000
STOP_GRACE_S = 8.0
Ev = tuple[str, str, "str | None", "dict[str, Any] | None"]   # (kind, text, tool, data)
LineFn = Callable[[dict[str, Any] | str], Awaitable[None]]

SUMMARY_KEYS = ("command", "file_path", "pattern", "path", "url", "query", "description", "prompt")


def binary() -> str | None:
    """The claude CLI. Tests replace this to point at a fake executable."""
    return coding_sessions.claude_binary()


def argv(exe: str, *, settings_json: str, model: str | None = None, max_turns: int | None = None, budget_usd: float | None = None,
         permission_mode: str | None = None, allowed_tools: list[str] | None = None, denied_tools: list[str] | None = None,
         append_system_prompt: str | None = None, json_schema: dict[str, Any] | None = None) -> list[str]:
    """The start command. A flag appears only when the caller set it, so the user's own CLI defaults apply to the rest.
    The tool lists come before `--settings` because they take any number of values."""
    out = [exe, "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose"]
    if model:
        out += ["--model", model]
    if max_turns:
        out += ["--max-turns", str(int(max_turns))]
    if budget_usd:
        out += ["--max-budget-usd", f"{float(budget_usd):g}"]
    if permission_mode and permission_mode != "default":
        out += ["--permission-mode", permission_mode]
    out += coding_sessions._tool_flags(allowed_tools, denied_tools)
    if append_system_prompt:
        out += ["--append-system-prompt", append_system_prompt]
    if json_schema:
        out += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    return [*out, "--settings", settings_json]


def user_line(text: str) -> bytes:
    return (json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n").encode()


def one_line(value: Any, cap: int = 200) -> str:
    return " ".join(str(value or "").split())[:cap]


def tool_summary(name: str, inp: Any) -> str:
    """One line for a tool call: the command, path or pattern it works on."""
    if isinstance(inp, dict):
        for key in SUMMARY_KEYS:
            if inp.get(key):
                return one_line(inp[key])
        return one_line(json.dumps(inp, default=str), 120) if inp else ""
    return one_line(inp)


def _blocks(message: Any) -> list[dict[str, Any]]:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse(obj: Any) -> list[Ev]:
    """The session events one stdout line stands for. Unknown shapes give nothing; nothing here raises."""
    if not isinstance(obj, dict):
        return [("note", one_line(obj, 300), None, None)] if obj else []
    kind, sub = obj.get("type"), obj.get("subtype")
    parent = obj.get("parent_tool_use_id")
    extra = {"parent": parent} if parent else {}
    if kind == "system":
        if sub == "init":
            return [("init", f"Started on {obj.get('model') or 'the default model'}", None,
                     {"claude_session_id": obj.get("session_id"), "model": obj.get("model"), "cwd": obj.get("cwd"),
                      "permission_mode": obj.get("permissionMode"), "capabilities": obj.get("capabilities"),
                      "tools": len(obj.get("tools") or [])})]
        if sub == "api_retry":
            return [("note", f"API retry {obj.get('attempt')}/{obj.get('max_retries')}: {one_line(obj.get('error'), 120)}", None, None)]
        if sub == "permission_denied":
            return [("note", f"Denied {obj.get('tool_name')}: {one_line(obj.get('message'), 200)}", obj.get("tool_name"), None)]
        return []
    if kind == "assistant":
        out: list[Ev] = []
        for b in _blocks(obj.get("message")):
            if b.get("type") == "text" and str(b.get("text") or "").strip():
                out.append(("assistant", str(b["text"]).strip(), None, extra or None))
            elif b.get("type") == "tool_use":
                name = str(b.get("name") or "tool")
                out.append(("tool", tool_summary(name, b.get("input")), name, {"call_id": b.get("id"), "input": b.get("input"), **extra}))
        return out
    if kind == "user":
        out = []
        for b in _blocks(obj.get("message")):
            if b.get("type") == "tool_result":
                out.append(("tool_result", one_line(_result_text(b.get("content")), 400), None,
                            {"call_id": b.get("tool_use_id"), "is_error": bool(b.get("is_error")), **extra}))
        return out
    return []


def result_facts(obj: dict[str, Any]) -> dict[str, Any]:
    """The parts of a `result` line the session keeps. `total_cost_usd` and `modelUsage` are cumulative for the session."""
    errors = obj.get("errors")
    return {"subtype": str(obj.get("subtype") or "unknown"), "is_error": bool(obj.get("is_error")), "text": obj.get("result") if isinstance(obj.get("result"), str) else "",
            "cost_usd": float(obj.get("total_cost_usd") or 0), "num_turns": int(obj.get("num_turns") or 0),
            "model_usage": obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {},
            "permission_denials": obj.get("permission_denials") if isinstance(obj.get("permission_denials"), list) else [],
            "structured_output": obj.get("structured_output"), "errors": errors if isinstance(errors, list) else [],
            "stop_reason": obj.get("stop_reason"), "terminal_reason": obj.get("terminal_reason"),
            "duration_ms": obj.get("duration_ms")}


def child_env() -> dict[str, str]:
    """The allowlisted environment, the user's PATH for their tools, and no prompts. TMPDIR is the user's, not a per-call dir:
    the process outlives any one call."""
    tmp = os.environ.get("TMPDIR") or tempfile.gettempdir()
    env = shell.scrubbed_env(tmp)
    env.update(coding_sessions.claude_env())
    env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return env


class Proc:
    """One running `claude -p` process: write turns to it, read its lines, stop it."""

    def __init__(self, args: list[str], cwd: str, on_line: LineFn, on_exit: Callable[[int | None, str], Awaitable[None]],
                 env: dict[str, str] | None = None) -> None:
        self.args, self.cwd, self.on_line, self.on_exit, self.env = args, cwd, on_line, on_exit, env
        self.proc: asyncio.subprocess.Process | None = None
        self.stdin_open = False
        self.stderr = ""
        self._tasks: list[asyncio.Task[Any]] = []
        self._write = asyncio.Lock()

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.args, cwd=self.cwd, env=self.env if self.env is not None else await asyncio.to_thread(child_env), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=PIPE_LIMIT, start_new_session=True)
        self.stdin_open = True
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._drain_stderr())]
        asyncio.create_task(self._finish())

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    @property
    def can_send(self) -> bool:
        return self.alive and self.stdin_open

    async def send(self, text: str) -> None:
        if not self.can_send:
            raise BrokenPipeError("the process is no longer taking messages")
        assert self.proc and self.proc.stdin
        async with self._write:
            try:
                self.proc.stdin.write(user_line(text))
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                self.stdin_open = False
                raise BrokenPipeError(str(e)) from e

    async def close_stdin(self) -> None:
        if self.stdin_open and self.proc and self.proc.stdin:
            self.stdin_open = False
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                raw = await self.proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):   # a line over PIPE_LIMIT: skipped, the stream goes on
                await self.on_line("(an oversized output line was dropped)")
                continue
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj: Any = json.loads(line)
            except ValueError:
                obj = line   # malformed: reported as text
            try:
                await self.on_line(obj)
            except Exception:   # noqa: BLE001 - a handler bug must not stop the reader
                import logging
                logging.getLogger(__name__).warning("claude-code line handler failed", exc_info=True)

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while chunk := await self.proc.stderr.read(4096):
            self.stderr = (self.stderr + chunk.decode("utf-8", "replace"))[-STDERR_TAIL:]

    async def _finish(self) -> None:
        assert self.proc
        code = await self.proc.wait()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.stdin_open = False
        await self.on_exit(code, self.stderr)

    async def stop(self, grace: float = STOP_GRACE_S) -> bool:
        """SIGINT, wait up to `grace` seconds, then SIGKILL of the whole group. True when it exited on the interrupt."""
        if not self.alive:
            return True
        assert self.proc
        await self.close_stdin()
        self._signal(signal.SIGINT, group=False)
        try:
            await asyncio.wait_for(self.proc.wait(), grace)
            return True
        except asyncio.TimeoutError:
            self._signal(signal.SIGKILL, group=True)
            await self.proc.wait()
            return False

    def _signal(self, sig: int, group: bool) -> None:
        assert self.proc
        try:
            if group:
                os.killpg(self.proc.pid, sig)
            else:
                self.proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass

