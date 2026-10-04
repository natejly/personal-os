"""Live MCP connections: one supervisor task per server.

The rule this module exists for, above any feature in it: **a third-party server must never be
able to wedge a reply.** Everything that touches a server process is bounded -
  * connecting (spawn + initialize + first tools/list) has `connect_timeout`;
  * every tools/call has its own timeout, and a caller that hits it gets `McpTimeout` back
    while the chat loop keeps going;
  * a caller whose server is mid-reconnect waits `READY_TIMEOUT` at most, then gets
    `McpUnavailable` - tools of a sick server go unavailable rather than blocking;
  * an idle connection is pinged on a heartbeat, so a server that quietly stops answering is
    noticed and recycled instead of being discovered by the next user who waits on it.
Nothing here holds a lock across an await on a server, and each supervisor is independent: a
wedged server cannot slow another one down.

Ownership: one asyncio task per server owns its stdio transport and its `ClientSession` for the
whole life of a connection. Callers never touch the session; they queue a `_Call` and await a
future. That is what makes shutdown reliable - the owning task exits its `async with` blocks, and
the SDK's stdio transport closes stdin, waits, then SIGTERMs and SIGKILLs the whole process
group, so quitting the app leaves no orphaned servers.

Confirmed against mcp 2.2.0 (`mcp.client.stdio.stdio_client`, `mcp.client.session.ClientSession`):
tools carry `name` / `description` / `input_schema` / `annotations`, and `call_tool` returns a
`CallToolResult` with `content` blocks, `structured_content` and `is_error`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, TextIO

import anyio
from anyio.abc import TaskGroup
from mcp import ClientSession, Implementation, StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from . import mcp_drift, redact
from .mcp_oauth import McpNeedsAuth, OAuthFlows, SignIn, headers_of
from .mcp_servers import DEFAULT_DANGER, McpServers

log = logging.getLogger(__name__)

CLIENT_INFO = Implementation(name="grain", version="0.1.0")

CONNECT_TIMEOUT = 20.0     # spawn + initialize + first tools/list
CALL_TIMEOUT = 45.0        # one tools/call, unless the caller asks for less
READY_TIMEOUT = 8.0        # how long a caller waits on a server that is (re)connecting
PING_TIMEOUT = 5.0         # a healthy server answers ping in well under this
PING_INTERVAL = 25.0
SHUTDOWN_TIMEOUT = 8.0     # graceful stop before the task is cancelled outright
CALL_GRACE = 5.0           # slack on the caller's own wait, so the supervisor times out first

BACKOFF_BASE = 1.0
BACKOFF_MAX = 60.0
SPAWN_ATTEMPTS = 4         # a command that cannot be spawned is a config error, not a blip

STDERR_LINES = 200
STDERR_LINE_CHARS = 400
STDERR_MAX_BYTES = 1 << 20   # the file is truncated past this, read or not
STDERR_TRIM_BYTES = 64 << 10  # ...and once fully read past this
STDERR_DRAIN_BYTES = 256 << 10  # most one drain reads, so the event loop is never held long
MAX_RESULT_CHARS = 20_000

# Every discovered tool gets the strictest danger level there is, whatever the server says about
# itself. MCP `annotations.read_only_hint` is self-reported by the same party that would benefit
# from lying, and a tool's description is written by that party too, so neither can lower this.
# Only the user, through a grant in mcp_servers, decides that a third-party tool may run unasked.
MCP_DANGER = DEFAULT_DANGER


class McpError(RuntimeError):
    """A call could not be completed. Carries a message fit to show the model."""


class McpUnavailable(McpError):
    """The server is not connected: disabled, failed, or still coming up."""


class McpTimeout(McpError):
    """The server accepted the call and did not answer in time."""


@dataclass(eq=False)  # identity, not value: calls live in a set and two identical calls are distinct
class _Call:
    name: str
    arguments: dict[str, Any]
    timeout: float
    future: asyncio.Future[dict[str, Any]]


_SHUTDOWN = _Call("", {}, 0.0, None)  # type: ignore[arg-type]


@dataclass
class _Config:
    """The launch configuration of one server, snapshotted so a mid-flight edit cannot change it."""
    id: str
    slug: str
    name: str
    transport: str
    command: str
    args: list[str]
    cwd: str
    env: dict[str, str]
    url: str = ""
    headers: dict[str, str] | None = None

    def signature(self) -> str:
        return json.dumps([self.transport, self.command, self.args, self.cwd, sorted(self.env.items()),
                           self.url, sorted((self.headers or {}).items())], sort_keys=True)


def _config_from(store: McpServers, server_id: str) -> _Config | None:
    row = store.server(server_id)
    if not row:
        return None
    return _Config(id=row["id"], slug=row["slug"], name=row["name"], transport=row["transport"],
                   command=row["command"], args=[str(a) for a in (row["args"] or [])], cwd=row["cwd"] or "",
                   env=store.launch_env(server_id), url=row.get("url") or "", headers=headers_of(row.get("headers")))


@contextlib.asynccontextmanager
async def _open(config: _Config, err: "_Stderr", oauth: OAuthFlows | None, sign_in: SignIn | None = None,
                redirect_uri: str | None = None) -> Any:
    """The (read, write) streams for one connection, whatever the transport.

    Remote servers authenticate through `oauth` when one is given: without `sign_in` the provider
    can refresh tokens but raises McpNeedsAuth rather than open a browser."""
    if config.transport == "stdio":
        if not config.command.strip():
            raise McpUnavailable("no command configured")
        params = StdioServerParameters(command=config.command, args=config.args, env=config.env or None, cwd=config.cwd or None)
        async with stdio_client(params, errlog=err.file) as streams:
            yield streams
        return
    if config.transport != "http":
        raise McpUnavailable(f"{config.transport} servers are not supported yet")
    if not config.url.strip():
        raise McpUnavailable("no URL configured")
    auth = oauth.provider(config.id, config.url, redirect_uri, sign_in) if oauth else None
    async with create_mcp_http_client(headers=config.headers or None, auth=auth) as http:
        async with streamable_http_client(config.url, http_client=http) as streams:
            yield streams


class _Stderr:
    """Bounded tail over a server's stderr, on disk and in memory.

    `stdio_client` hands `errlog` to the subprocess, so it has to be a real file - an in-memory
    sink has no fileno. It is opened in append mode and unlinked at once. Each drain reads at most
    DRAIN_MAX_BYTES in bounded chunks, keeps the last STDERR_LINES lines, and once the file has
    been read through (or has outgrown STDERR_MAX_BYTES unread) truncates it. Append mode is what
    makes that safe: the child's writes always land at the new end, never at its old offset.
    A chatty child can still write between two drains, but the file cannot stay large for long.
    """

    def __init__(self, lines: list[str]):
        self.lines = lines
        fd, path = tempfile.mkstemp(prefix="pos-mcp-stderr-")
        os.close(fd)
        # O_APPEND on the child's copy of the descriptor: it shares the file offset with this one.
        self.file: TextIO = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
        self._reader = open(path, "rb")
        self._carry = b""
        self._dropped = False
        try:
            os.unlink(path)  # POSIX: both handles stay valid, nothing is left behind on a crash
        except OSError:
            pass

    def drain(self) -> None:
        try:
            self._drain()
        except (OSError, ValueError):
            pass  # closed or truncated under us; stderr is a convenience, never a failure

    def _drain(self) -> None:
        budget = STDERR_DRAIN_BYTES
        while budget > 0:
            chunk = self._reader.read(min(65536, budget))
            if not chunk:
                break
            budget -= len(chunk)
            data = self._carry + chunk
            *complete, self._carry = data.split(b"\n")
            self._carry = self._carry[:STDERR_LINE_CHARS * 4]  # a newline-free flood stays small
            for raw in complete:
                line = raw.decode("utf-8", errors="replace").strip("\r")
                if line:
                    self._append(line)
        size = os.fstat(self.file.fileno()).st_size
        at_end = self._reader.tell() >= size
        if size > STDERR_MAX_BYTES or (at_end and size > STDERR_TRIM_BYTES):
            if not at_end and not self._dropped:
                self._dropped = True
                self._append("[stderr skipped: server wrote faster than it was read]")
            os.ftruncate(self.file.fileno(), 0)
            self._reader.seek(0)
            self._carry = b""

    def _append(self, line: str) -> None:
        self.lines.append(line[:STDERR_LINE_CHARS])
        del self.lines[:-STDERR_LINES]

    def close(self) -> None:
        self.drain()
        for handle in (self.file, self._reader):
            try:
                handle.close()
            except OSError:
                pass


def _describe(exc: BaseException) -> str:
    """Flatten an anyio ExceptionGroup down to something a status line can carry."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return _describe(inner[0])
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _public_value(value: Any) -> Any:
    """Credentials out of a tool result. The server's own payload is not stored here."""
    if isinstance(value, str):
        return redact.scrub_command_output(value)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, dict):
        return {(redact.scrub_command_output(key) if isinstance(key, str) else key): _public_value(item)
                for key, item in value.items()}
    return value


def _result_dict(result: Any) -> dict[str, Any]:
    """A CallToolResult as plain JSON for the chat loop. Text blocks win; others are named."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        else:
            parts.append(f"[{getattr(block, 'type', type(block).__name__)} content]")
    content = _public_value("\n".join(parts))
    if len(content) > MAX_RESULT_CHARS:
        content = content[:MAX_RESULT_CHARS] + f"\n[truncated at {MAX_RESULT_CHARS} chars]"
    out: dict[str, Any] = {"content": content, "is_error": bool(getattr(result, "is_error", False))}
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        out["structured"] = _public_value(structured)
    if out["is_error"]:
        out["error"] = content or "the tool reported an error"
    return out


def tool_export(tool: Any) -> dict[str, Any]:
    """One `mcp.types.Tool` in the shape McpServers.sync_tools wants."""
    return {"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema or {},
            "danger": MCP_DANGER, "annotations": tool.annotations.model_dump(mode="json") if tool.annotations else {}}


class _Supervisor:
    """Owns one server's process, transport and session for the life of a connection."""

    def __init__(self, store: McpServers, config: _Config, *, connect_timeout: float = CONNECT_TIMEOUT,
                 call_timeout: float = CALL_TIMEOUT, oauth: OAuthFlows | None = None):
        self.store = store
        self.config = config
        self.oauth = oauth
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.status = "idle"
        self.detail = ""
        self.attempts = 0
        self.server_info: dict[str, Any] = {}
        self.stderr: list[str] = []
        self.tool_slugs: list[str] = []
        self._queue: asyncio.Queue[_Call] = asyncio.Queue()
        self._inflight: set[_Call] = set()
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._check_now = anyio.Event()
        self._relist = anyio.Event()
        self._err: _Stderr | None = None
        self._task: asyncio.Task[None] | None = None

    # ---------- lifecycle ----------
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"mcp:{self.config.slug}")

    async def stop(self, *, status: str = "idle", detail: str = "") -> None:
        """Ask the owning task to unwind, then cancel it if it will not. Never leaves a child alive."""
        self._stop.set()
        self._queue.put_nowait(_SHUTDOWN)
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), SHUTDOWN_TIMEOUT)
            except asyncio.CancelledError:
                task.cancel()  # our own caller gave up; the child still must not be left running
                raise
            except asyncio.TimeoutError:
                task.cancel()  # the SDK shields its own process teardown against this
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(task, SHUTDOWN_TIMEOUT)
            except Exception:  # noqa: BLE001 - a dying connection must not block shutdown
                pass
        self._ready.clear()
        self._drain(McpUnavailable(f"{self.config.name} is not connected"), queued=True)
        self._set_status(status, detail)

    async def _run(self) -> None:
        while not self._stop.is_set():
            self._ready.clear()
            self._set_status("connecting")
            try:
                await self._cycle()
                self.attempts = 0
            except BaseException as exc:  # noqa: BLE001 - every failure becomes status, then a retry
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self.attempts += 1
                if _needs_auth(exc):
                    # Retrying cannot help until the user signs in; sign-in restarts this supervisor.
                    self._set_status("error", "sign-in required")
                    break
                self._set_status("error", _describe(exc))
                if _is_spawn_failure(exc) and self.attempts >= SPAWN_ATTEMPTS:
                    self._set_status("error", f"{_describe(exc)} (giving up; fix the command and restart it)")
                    break
            finally:
                self._ready.clear()
                # In-flight calls were already sent, so they fail: a tool that may have run once
                # is never silently run again. Calls still queued never reached the server and
                # stay queued for the next connection, bounded by the caller's own wait.
                self._drain(McpUnavailable(f"{self.config.name} disconnected mid-call"), queued=False)
            if self._stop.is_set():
                break
            delay = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** max(0, self.attempts - 1)))
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except asyncio.TimeoutError:
                pass
        self._drain(McpUnavailable(f"{self.config.name} is not connected"), queued=True)
        if self._stop.is_set():
            self._set_status("idle")

    async def _cycle(self) -> None:
        err = self._err = _Stderr(self.stderr)
        self._check_now = anyio.Event()
        self._relist = anyio.Event()
        try:
            async with _open(self.config, err, self.oauth) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=self.call_timeout,
                                         client_info=CLIENT_INFO, message_handler=self._on_message) as session:
                    with anyio.fail_after(self.connect_timeout):
                        init = await session.initialize()
                        listing = await session.list_tools()
                    self.server_info = {"name": init.server_info.name, "version": init.server_info.version,
                                        "protocol": init.protocol_version, "instructions": init.instructions or ""}
                    err.drain()
                    self._register(listing.tools)
                    self._set_status("ready")
                    self._ready.set()
                    self.attempts = 0  # counts consecutive failed connects, not drops over the app's lifetime
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(self._heartbeat, session, err)
                        tg.start_soon(self._relister, session)
                        await self._serve(session, tg)
                        tg.cancel_scope.cancel()
        finally:
            self._err = None
            err.close()

    async def _on_message(self, message: Any) -> None:
        # Only flag it: re-listing here would block the session's receive loop.
        if getattr(message, "method", "") == "notifications/tools/list_changed":
            self._relist.set()

    async def _relister(self, session: ClientSession) -> None:
        """Re-sync tools after a list_changed notice, so drift review sees mid-session edits."""
        while True:
            await self._relist.wait()
            self._relist = anyio.Event()
            try:
                with anyio.fail_after(self.call_timeout):
                    listing = await session.list_tools()
                self._register(listing.tools)
            except Exception:  # noqa: BLE001 - a failed refresh keeps the old tools; the next notice retries
                log.warning("MCP tool re-list failed for %s", self.config.name, exc_info=True)

    async def _serve(self, session: ClientSession, tg: TaskGroup) -> None:
        while not self._stop.is_set():
            call = await self._queue.get()
            if call is _SHUTDOWN:
                return
            tg.start_soon(self._dispatch, session, call)

    async def _heartbeat(self, session: ClientSession, err: _Stderr) -> None:
        """Ping on an interval, or on demand after a suspicious call. A failed ping ends the cycle."""
        while True:
            with anyio.move_on_after(PING_INTERVAL):
                await self._check_now.wait()
                self._check_now = anyio.Event()
            err.drain()
            with anyio.fail_after(PING_TIMEOUT):
                await session.send_ping()
            if self.status == "ready" and self.detail:
                self._set_status("ready")  # the server answered: clear the last-call note

    async def _dispatch(self, session: ClientSession, call: _Call) -> None:
        if call.future.done():  # the caller already gave up while we were reconnecting
            return
        self._inflight.add(call)
        try:
            with anyio.fail_after(call.timeout):
                result = await session.call_tool(call.name, call.arguments, read_timeout_seconds=call.timeout)
        except TimeoutError:
            # The call is abandoned, not waited on: the caller gets an error now. Whether the
            # server itself is wedged is a separate question, answered by an immediate ping.
            self._settle(call, exc=McpTimeout(f"{call.name} did not answer within {call.timeout:.0f}s"))
            # One slow tool is not a dead connection: the status stays "ready" so the rest of the
            # server's tools stay offered. A dead one fails the ping below and goes to reconnect.
            self.detail = f"last call: {call.name} timed out after {call.timeout:.0f}s"
            self.store.set_status(self.config.id, self.status, self.detail)
            self._check_now.set()
        except anyio.get_cancelled_exc_class():
            self._settle(call, exc=McpUnavailable(f"{self.config.name} disconnected mid-call"))
            raise
        except BaseException as exc:  # noqa: BLE001 - a bad call must not take the connection down
            self._settle(call, exc=McpError(_describe(exc)))
            self._check_now.set()
        else:
            self._settle(call, value=_result_dict(result))
        finally:
            self._inflight.discard(call)

    async def wait_ready(self, timeout: float = CONNECT_TIMEOUT) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    # ---------- calls ----------
    async def call(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        limit = float(timeout or self.call_timeout)
        if self._stop.is_set() or self._task is None or self._task.done():
            raise McpUnavailable(f"{self.config.name} is not running")
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), READY_TIMEOUT)
            except asyncio.TimeoutError:
                raise McpUnavailable(f"{self.config.name} is not connected ({self.detail or self.status})") from None
        call = _Call(name, arguments, limit, asyncio.get_running_loop().create_future())
        self._queue.put_nowait(call)
        try:
            return await asyncio.wait_for(asyncio.shield(call.future), limit + CALL_GRACE)
        except asyncio.TimeoutError:
            raise McpTimeout(f"{name} did not answer within {limit:.0f}s") from None
        finally:
            # The caller has been told the outcome (or cancelled). A call still queued must not be
            # sent later: cancelling the future is what _dispatch's "caller gave up" check sees.
            # One already sent cannot be recalled; its late result is simply dropped by _settle.
            if not call.future.done():
                call.future.cancel()

    def _settle(self, call: _Call, *, value: dict[str, Any] | None = None, exc: BaseException | None = None) -> None:
        if call.future.done():
            return
        if exc is not None:
            call.future.set_exception(exc)
        else:
            call.future.set_result(value or {})

    def _drain(self, exc: BaseException, *, queued: bool) -> None:
        for call in list(self._inflight):
            self._settle(call, exc=exc)
        self._inflight.clear()
        if not queued:
            return
        while True:
            try:
                call = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if call is not _SHUTDOWN:
                self._settle(call, exc=exc)

    # ---------- bookkeeping ----------
    def _register(self, tools: list[Any]) -> None:
        exported = [tool_export(t) for t in tools]
        synced = self.store.sync_tools(self.config.id, exported)
        for slug in synced["added"] + synced["changed"]:  # scan what is new or changed; a new fail-level finding withholds the tool
            try:
                mcp_drift.apply_review(self.store, slug)
            except Exception:  # noqa: BLE001 - a review failure must not stop the server from connecting
                log.warning("MCP drift review failed for %s", slug, exc_info=True)
        self.tool_slugs = sorted(synced["added"] + synced["changed"] + synced["unchanged"])

    def _set_status(self, status: str, detail: str = "") -> None:
        self.status, self.detail = status, detail
        self.store.set_status(self.config.id, status, detail)

    def tail_stderr(self) -> list[str]:
        """Read whatever the process has written since the last look. Cheap, and not async."""
        if self._err is not None:
            self._err.drain()
        return list(self.stderr)

    def info(self) -> dict[str, Any]:
        return {"server_id": self.config.id, "slug": self.config.slug, "name": self.config.name,
                "status": self.status, "detail": self.detail, "attempts": self.attempts,
                "running": bool(self._task and not self._task.done()), "ready": self._ready.is_set(),
                "server_info": self.server_info, "tools": list(self.tool_slugs), "stderr": self.tail_stderr()}


def _needs_auth(exc: BaseException) -> bool:
    inner = getattr(exc, "exceptions", None)
    if inner:
        return any(_needs_auth(e) for e in inner)
    return isinstance(exc, McpNeedsAuth) or (exc.__cause__ is not None and _needs_auth(exc.__cause__))


def _is_spawn_failure(exc: BaseException) -> bool:
    """A misconfiguration retrying cannot fix. TimeoutError is an OSError subclass, and is not one."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return any(_is_spawn_failure(e) for e in inner)
    if isinstance(exc, TimeoutError):
        return False
    return isinstance(exc, (OSError, McpUnavailable))


class McpClient:
    """The app's handle on every configured MCP server.

    `sync()` reconciles supervisors with what McpServers holds, `call()` runs one tool, and
    `stop()` guarantees no child process outlives the app.
    """

    def __init__(self, store: McpServers, *, connect_timeout: float = CONNECT_TIMEOUT,
                 call_timeout: float = CALL_TIMEOUT, oauth: OAuthFlows | None = None):
        self.store = store
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.oauth = oauth
        self._supervisors: dict[str, _Supervisor] = {}
        self._sync_lock = asyncio.Lock()  # overlapping syncs would each build a supervisor for one server

    async def start(self) -> list[dict[str, Any]]:
        return await self.sync()

    async def sync(self) -> list[dict[str, Any]]:
        """Start what should run, stop what should not, restart what was reconfigured."""
        async with self._sync_lock:
            return await self._sync()

    async def _sync(self) -> list[dict[str, Any]]:
        rows = {r["id"]: r for r in self.store.servers()}
        for sid in list(self._supervisors):
            if sid not in rows:
                await self._supervisors.pop(sid).stop()
        for sid, row in rows.items():
            sup = self._supervisors.get(sid)
            if not row["enabled"]:
                if sup:
                    await self._supervisors.pop(sid).stop(status="disabled")
                elif row["status"] != "disabled":
                    self.store.set_status(sid, "disabled")
                continue
            config = _config_from(self.store, sid)
            if config is None:
                continue
            if sup and sup.config.signature() != config.signature():
                await self._supervisors.pop(sid).stop()
                sup = None
            if sup is None:
                sup = _Supervisor(self.store, config, connect_timeout=self.connect_timeout,
                                  call_timeout=self.call_timeout, oauth=self.oauth)
                self._supervisors[sid] = sup
            sup.config = config
            await sup.start()
        return self.status()

    async def wait_ready(self, timeout: float = CONNECT_TIMEOUT) -> bool:
        """Wait for every supervisor to reach 'ready'. False if any is still not up in time."""
        results = await asyncio.gather(*(s.wait_ready(timeout) for s in self._supervisors.values()))
        return all(results)

    async def restart(self, server_id: str) -> dict[str, Any] | None:
        async with self._sync_lock:
            sup = self._supervisors.pop(server_id, None)
            if sup:
                await sup.stop()
            await self._sync()
        sup = self._supervisors.get(server_id)
        return sup.info() if sup else None

    async def stop(self) -> None:
        for sid in list(self._supervisors):
            await self._supervisors.pop(sid).stop()
        if self.oauth:
            await self.oauth.stop()

    async def sign_in(self, server_id: str, redirect_uri: str) -> dict[str, Any]:
        """Begin (or join) a browser sign-in for a remote server. Returns the OAuth status, with
        `auth_url` for the renderer to open while it is waiting on the user."""
        if self.oauth is None:
            raise McpUnavailable("OAuth is not configured")
        config = _config_from(self.store, server_id)
        if config is None or config.transport != "http":
            raise McpUnavailable("only remote (http) servers sign in")

        async def run(s: SignIn) -> None:
            err = _Stderr([])
            try:
                async with _open(config, err, self.oauth, sign_in=s, redirect_uri=redirect_uri) as (read, write):
                    async with ClientSession(read, write, client_info=CLIENT_INFO) as session:
                        await session.initialize()
            finally:
                err.close()
            await self.restart(server_id)  # the supervisor now connects with the stored tokens

        await self.oauth.begin(server_id, run)
        return self.oauth.status(server_id)

    async def call(self, tool_slug: str, arguments: dict[str, Any] | None = None,
                   timeout: float | None = None) -> dict[str, Any]:
        """Run one MCP tool. Raises McpUnavailable / McpTimeout / McpError; never blocks unbounded.

        Permission is not decided here: the caller consults McpServers.effective_mode first.
        """
        tool = self.store.tool(tool_slug)
        if not tool:
            raise McpUnavailable(f"Unknown MCP tool {tool_slug}")
        if tool["missing_since"]:
            raise McpUnavailable(f"{tool_slug} is no longer offered by its server")
        if tool.get("quarantined_at"):
            raise McpUnavailable(f"{tool_slug} is withheld until you accept it in Settings")
        sup = self._supervisors.get(tool["server_id"])
        if sup is None:
            raise McpUnavailable(f"The server for {tool_slug} is not running")
        return await sup.call(tool["name"], arguments or {}, timeout)

    def status(self, server_id: str | None = None) -> list[dict[str, Any]]:
        if server_id is not None:
            sup = self._supervisors.get(server_id)
            return [sup.info()] if sup else []
        return [s.info() for s in self._supervisors.values()]

    def stderr(self, server_id: str) -> list[str]:
        sup = self._supervisors.get(server_id)
        return sup.tail_stderr() if sup else []

    def ready_slugs(self) -> list[str]:
        """Slugs whose server is connected right now: what may be offered to the model."""
        out: list[str] = []
        for sup in self._supervisors.values():
            if sup.status == "ready":
                out.extend(sup.tool_slugs)
        return sorted(out)

    async def probe(self, config: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Connect once, handshake, list tools, disconnect. The eval harness's only live step.

        Outside the supervisors on purpose: it must work for a server that has never been saved,
        and it must not disturb a connection the chat is using.
        """
        limit = float(timeout or self.connect_timeout)
        out: dict[str, Any] = {"ok": False, "error": "", "server_info": {}, "tools": [], "stderr": []}
        cfg = _Config(id=str(config.get("id") or ""), slug="", name="probe", transport=str(config.get("transport") or "stdio"),
                      command=str(config.get("command") or "").strip(), args=[str(a) for a in (config.get("args") or [])],
                      cwd=str(config.get("cwd") or ""), env=dict(config.get("env") or {}),
                      url=str(config.get("url") or ""), headers=headers_of(config.get("headers")))
        err = _Stderr([])
        try:
            # A saved remote server probes with its stored sign-in; an unsaved one has none to use.
            async with _open(cfg, err, self.oauth if cfg.id else None) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=limit, client_info=CLIENT_INFO) as session:
                    with anyio.fail_after(limit):
                        init = await session.initialize()
                        listing = await session.list_tools()
                    out["server_info"] = {"name": init.server_info.name, "version": init.server_info.version,
                                          "protocol": init.protocol_version, "instructions": init.instructions or ""}
                    out["tools"] = [tool_export(t) for t in listing.tools]
                    out["ok"] = True
        except BaseException as exc:  # noqa: BLE001 - a probe reports failure, it does not raise
            if isinstance(exc, asyncio.CancelledError):
                raise
            out["error"] = _describe(exc)
        finally:
            err.close()
            out["stderr"] = list(err.lines)
        return out

    async def probe_server(self, server_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        config = _config_from(self.store, server_id)
        if config is None:
            return {"ok": False, "error": "no such server", "server_info": {}, "tools": [], "stderr": []}
        return await self.probe({"id": config.id, "transport": config.transport, "command": config.command, "args": config.args,
                                 "cwd": config.cwd, "env": config.env, "url": config.url, "headers": config.headers}, timeout=timeout)


def stub_config(mode: str = "friendly", *, python: str | None = None, args: list[str] | None = None) -> dict[str, Any]:
    """Launch config for scripts/mcp_stub.py, used by the tests and handy for manual poking."""
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "mcp_stub.py")
    return {"transport": "stdio", "command": python or sys.executable, "args": [script, "--mode", mode, *(args or [])]}
