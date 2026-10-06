"""Programmatic tool calling: a run_python script that drives app tools and hands back only its stdout.

Bulk work (grep forty files, edit a dozen sites) costs one model round per call. With `run_python(tools=[...])` the
backend opens a Unix socket and the sandboxed script calls `grain_tools.call("fs_grep", pattern=..., root=...)`. The
socket is the only thing the Seatbelt profile lets the script connect to; the network stays off.

Every call goes through the same gate the model's own calls do, never around it:
  * only names on ALLOWED, that the script asked for, that exist, and whose mode for this chat is on or ask
  * `Toolbox.gate` (taint upgrades on -> ask), then an `ask` parks the script on a real approval card (`approve`)
    and the script's clock stops while it waits; no card to answer (a background run) means refused, not run
  * `Toolbox.call` with the run's own ctx, so taint, the ledger and every tool-side check apply
Never bridged: shell_run, agent_spawn, workflow_run, anything external or scheduling. Not on ALLOWED, not callable.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from typing import Any, Awaitable, Callable

from . import permrules

ALLOWED = frozenset({"fs_glob", "fs_grep", "read_local_file", "fs_edit", "search_documents", "web_search", "fetch_url"})
MAX_SECONDS = 300
STDOUT_KEEP = 50_000
STDOUT_HEAD = 0.4          # share of the kept stdout taken from the start; the rest is the tail, where a failure is
STDERR_KEEP = 10_000
MAX_LINE = 8_000_000

# What the sandboxed script imports. Stdlib only: nothing here can be installed inside the sandbox.
CLIENT_SOURCE = '''"""Call app tools from a run_python script: grain_tools.call("fs_grep", pattern="TODO", root=".")."""
import json
import os
import socket


class ToolError(Exception):
    """The bridge refused the call (tool not offered, off, declined). A tool's own error comes back as data."""


def call(tool, **args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(os.environ["GRAIN_TOOLS_SOCK"])
        s.sendall(json.dumps({"tool": tool, "args": args}).encode("utf-8") + b"\\n")
        chunks = []
        while True:
            c = s.recv(1 << 16)
            if not c:
                break
            chunks.append(c)
            if c.endswith(b"\\n"):
                break
    finally:
        s.close()
    resp = json.loads(b"".join(chunks).decode("utf-8"))
    if not resp.get("ok"):
        raise ToolError(resp.get("error") or "refused")
    return resp["result"]
'''

Approve = Callable[[str, dict[str, Any], bool], Awaitable[bool]]


def offered(tb: Any, ctx: dict[str, Any], wanted: list[str] | None, modes: dict[str, str] | None = None
            ) -> tuple[list[str], dict[str, str]]:
    """(names the script may call, {refused name: why}). The intersection the spec asks for: the request, ALLOWED,
    the tools that exist and are available, and this chat's effective modes (off is off)."""
    ok: list[str] = []
    why: dict[str, str] = {}
    for n in dict.fromkeys(str(x) for x in (wanted or [])):
        spec = tb.specs.get(n)
        if n not in ALLOWED:
            why[n] = "cannot be called from a script (allowed: " + ", ".join(sorted(ALLOWED)) + ")"
        elif spec is None or not tb.available(n):
            why[n] = "not available here"
        elif (modes.get(n) if modes is not None and n in modes else tb.default_mode(spec)) == "off":
            why[n] = "turned off for this chat"
        else:
            ok.append(n)
    return ok, why


class Bridge:
    def __init__(self, tb: Any, ctx: dict[str, Any], names: list[str], modes: dict[str, str] | None = None,
                 approve: Approve | None = None):
        self.tb, self.ctx, self.names, self.modes = tb, ctx, set(names), modes
        self.approve = approve
        self.calls = 0
        self.log: list[dict[str, Any]] = []
        self.stderr_cap = STDERR_KEEP
        self.full_stdout: str | None = None
        self._dir: str | None = None
        self._server: asyncio.AbstractServer | None = None
        self._paused = 0.0
        self._pending = 0
        self._since = 0.0
        self.socket_path = ""

    # -- lifecycle --
    async def start(self) -> "Bridge":
        self._dir = os.path.realpath(tempfile.mkdtemp(prefix="pos-br-"))  # short: sun_path holds ~104 bytes
        self.socket_path = os.path.join(self._dir, "b.sock")
        self._server = await asyncio.start_unix_server(self._serve, path=self.socket_path, limit=MAX_LINE)
        os.chmod(self.socket_path, 0o600)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._server = None
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    # -- the script's clock --
    def paused_for(self) -> float:
        """Seconds the script has spent parked on an approval card, so its timeout can leave them out."""
        return self._paused + ((time.monotonic() - self._since) if self._pending else 0.0)

    def shape_stdout(self, text: str) -> str:
        """Within STDOUT_KEEP chars: 40% head, 60% tail, the middle named and counted. The whole text is kept."""
        if len(text) <= STDOUT_KEEP:
            return text
        self.full_stdout = text
        head, tail = int(STDOUT_KEEP * STDOUT_HEAD), STDOUT_KEEP - int(STDOUT_KEEP * STDOUT_HEAD)
        return f"{text[:head]}\n[... {len(text) - STDOUT_KEEP} characters omitted ...]\n{text[-tail:]}"

    # -- one request --
    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                req = json.loads(line.decode("utf-8")) if line else None
            except ValueError:
                req = None
            if not isinstance(req, dict) or not isinstance(req.get("args", {}), dict):
                resp: dict[str, Any] = {"ok": False, "error": "bad request"}
            else:
                resp = await self.handle(str(req.get("tool") or ""), req.get("args") or {})
            writer.write(json.dumps(resp, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    def _refuse(self, name: str, why: str) -> dict[str, Any]:
        self.log.append({"tool": name, "ok": False, "why": why})
        return {"ok": False, "error": why}

    async def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self.names:
            return self._refuse(name, f"{name} was not offered to this script. Offered: {', '.join(sorted(self.names)) or 'none'}.")
        self.calls += 1
        raw = (self.modes or {}).get(name) if self.modes is not None else None
        spec = self.tb.specs[name]
        raw = raw or self.tb.default_mode(spec)
        if raw == "off":
            return self._refuse(name, f"{name} is turned off for this chat.")
        # The same effective-mode rules the model's own calls get: taint upgrades on -> ask.
        # Args go too, so a cancel of a queued send is visible to the gate and a list is not.
        mode = self.tb.gate(name, raw, self.ctx, args)
        fs_ask = self.tb.fs_needs_ask(name, args, self.ctx)  # a credential store, or a write after untrusted content
        if fs_ask and mode == "on":
            mode = "ask"
        # Then the user's argument-pattern rules and this chat's session grants: a deny refuses, an ask rule cards.
        cfg = self.ctx.get("settings") or self.tb.settings()
        roots: list[str] = []
        if self.ctx.get("desk_id") and (ws := getattr(self.tb, "workspace", None)) is not None:
            roots.append(str(ws.desk_root(self.ctx["desk_id"])))
        perm = permrules.resolve(name, args, mode, mode != raw or fs_ask, rules=cfg.get("permissionRules"), roots=roots,
                                 conv=self.ctx.get("conversation_id"))
        if perm.refusal:
            return self._refuse(name, f"{name} was refused: {perm.refusal}")
        mode = perm.mode
        if mode == "ask":
            if self.approve is None or self.ctx.get("proposal_only"):
                return self._refuse(name, f"{name} needs the user's approval and there is nobody to ask in this run.")
            # An ask rule's card is never lifted by skip-permissions, the same as in the reply loop.
            forced = perm.forced or perm.kind == "rule"
            self._pending += 1
            if self._pending == 1:
                self._since = time.monotonic()
            try:
                allowed = await self.approve(name, args, forced)
            finally:
                self._pending -= 1
                if self._pending == 0:
                    self._paused += time.monotonic() - self._since
            if not allowed:
                return self._refuse(name, f"the user declined {name}.")
        # The run's own caller when the lane has one (undo snapshot, idempotency journal), else the toolbox directly.
        call = self.ctx.get("bridge_call")
        self.ctx["fs_outside_ok"] = fs_ask  # the user said yes to it above
        try:
            result = await (call(name, args, self.ctx) if call else self.tb.call(name, args, self.ctx))
        finally:
            self.ctx["fs_outside_ok"] = False
        if self.tb.taints(name) and not (isinstance(result, dict) and result.get("error")):
            self.ctx.setdefault("taint_sources", []).append(name)  # appended every time: the fence reads growth
        self.log.append({"tool": name, "ok": not (isinstance(result, dict) and result.get("error"))})
        return {"ok": True, "result": result}


async def run(tb: Any, ctx: dict[str, Any], code: str, timeout: int, wanted: list[str],
              runner: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """The run_python tool's bridged path: open the socket, run the script off the loop, shape the answer."""
    names, refused = offered(tb, ctx, wanted, ctx.get("modes"))
    if not names:
        from .tools import tool_error
        return tool_error("None of the requested tools can be called from a script: "
                          + "; ".join(f"{k} {v}" for k, v in refused.items()),
                          alternative="call the tools directly, or run_python without `tools`")
    bridge = Bridge(tb, ctx, names, ctx.get("modes"), ctx.get("bridge_approve"))
    await bridge.start()
    try:
        out = await asyncio.to_thread(runner, code, max(1, min(int(timeout), MAX_SECONDS)), None, bridge)
    finally:
        await bridge.stop()
    out["bridged_calls"] = bridge.calls
    out["bridge_log"] = bridge.log[-20:]
    if refused:
        out["tools_refused"] = refused
    if bridge.full_stdout is not None and getattr(tb, "results", None) is not None and ctx.get("conversation_id"):
        row = tb.results.store(ctx["conversation_id"], ctx.get("message_id"), "run_python", bridge.full_stdout,
                               {"type": "string", "chars": len(bridge.full_stdout),
                                # Paging it later has to taint again, even after the chat's banner is cleared.
                                **({"untrusted": True} if ctx.get("tainted") else {})})
        out["result_id"] = row["id"]
        out["note"] = (f"stdout was {len(bridge.full_stdout)} characters; the middle is cut. The whole text is behind "
                       f"result_id {row['id']} (read_tool_result with an offset).")
    return out
