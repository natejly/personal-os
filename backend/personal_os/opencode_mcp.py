"""A stdio MCP server that drives a running OpenCode: run `python -m personal_os.opencode_mcp`.

OpenCode has no MCP-server mode of its own, but its background service speaks an HTTP API (`/api/session`, ...). This shim
starts or finds that service (`opencode service status|start` prints its URL), signs in with the service password
(HTTP basic, user "opencode", read from ~/.config/opencode/service.json; never printed or logged) and offers four tools:
run a prompt in a new or existing session, list sessions, read a session's messages, and report status.

The service is the user's own: this shim starts it when it is down but never stops it. A call that cannot connect (the
service crashed or was restarted on a new port) looks the service up again, starting it if needed, and retries once;
only a connection that never opened is retried, so a prompt is never sent twice.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from . import opencode

USER = "opencode"
URL_RE = re.compile(r"https?://[^\s\"']+")
MAX_TEXT = 20_000  # one reply or message, so a long transcript cannot flood the model's context


def parse_url(output: str) -> str | None:
    """The server URL from `opencode service status` (a bare URL line) or `serve --stdio` (a {"url": ...} JSON line)."""
    for line in output.splitlines():
        if m := URL_RE.search(line):
            return m.group(0).rstrip("/")
    return None


def service_password() -> str:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "service.json"
    try:
        pw = json.loads(cfg.read_text(encoding="utf-8")).get("password")
    except (OSError, ValueError, AttributeError):
        return ""
    return pw if isinstance(pw, str) else ""


def auth_headers(password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{USER}:{password}".encode()).decode()} if password else {}


def _cli(args: list[str], timeout: float = 30) -> str:
    binary = opencode.binary()
    if not binary:
        raise RuntimeError(opencode.INSTALL_HINT)
    try:
        r = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"opencode {' '.join(args)} failed ({type(e).__name__})") from e
    return r.stdout


def server_url() -> str:
    """The running service's URL, starting the service when it is not up."""
    url = parse_url(_cli(["service", "status"]))
    if not url:
        _cli(["service", "start"], timeout=60)
        url = parse_url(_cli(["service", "status"]))
    if not url:
        raise RuntimeError("the OpenCode service did not start; try `opencode service start` in a terminal")
    return url


def assistant_text(msg: dict[str, Any]) -> str:
    return "".join(str(p.get("text") or "") for p in msg.get("content") or [] if isinstance(p, dict) and p.get("type") == "text")


def shape_message(msg: dict[str, Any]) -> dict[str, Any]:
    """One session message as {id, type, text, tools?, error?}: reasoning is dropped, tool parts reduced to names."""
    kind = str(msg.get("type") or "")
    out: dict[str, Any] = {"id": msg.get("id"), "type": kind}
    if kind == "assistant":
        out["text"] = assistant_text(msg)[:MAX_TEXT]
        if tools := [str(p.get("name")) for p in msg.get("content") or [] if isinstance(p, dict) and p.get("type") == "tool"]:
            out["tools"] = tools
        if msg.get("error"):
            out["error"] = str(msg["error"])[:500]
    elif kind == "shell":
        out.update(command=msg.get("command"), status=msg.get("status"), exit=msg.get("exit"), text=str(msg.get("output") or "")[:MAX_TEXT])
    elif "text" in msg:
        out["text"] = str(msg["text"])[:MAX_TEXT]
    return out


def shape_session(s: dict[str, Any]) -> dict[str, Any]:
    t = s.get("time") or {}
    return {"id": s.get("id"), "title": s.get("title") or "", "directory": (s.get("location") or {}).get("directory"),
            "updated": t.get("updated"), "created": t.get("created")}


class OpenCodeClient:
    """The HTTP calls, over an httpx client the caller owns (a test passes one with a mock transport)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        r = await self.http.request(method, "/api" + path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenCode answered {r.status_code} for {method} {path}")
        return r.json() if r.content else None

    async def info(self) -> dict[str, Any]:
        return (await self._json("GET", "/info")) or {}

    async def sessions(self, limit: int) -> list[dict[str, Any]]:
        d = await self._json("GET", "/session", params={"limit": limit, "order": "desc"})
        return [shape_session(s) for s in (d or {}).get("data") or []]

    async def messages(self, session_id: str, limit: int, stop_at: str | None = None) -> list[dict[str, Any]]:
        """The newest `limit` messages, oldest first; with `stop_at`, only those after that message id."""
        d = await self._json("GET", f"/session/{session_id}/message", params={"limit": limit, "order": "desc"})
        out: list[dict[str, Any]] = []
        for m in (d or {}).get("data") or []:
            if stop_at and m.get("id") == stop_at:
                break
            out.append(m)
        return out[::-1]

    async def prompt(self, text: str, directory: str | None, session_id: str | None, timeout: float) -> dict[str, Any]:
        if not session_id:
            body: dict[str, Any] = {"title": "Grain"}
            if directory:
                body["location"] = {"directory": os.path.expanduser(directory)}
            session_id = ((await self._json("POST", "/session", json=body)) or {})["data"]["id"]
        sent = ((await self._json("POST", f"/session/{session_id}/prompt", json={"text": text})) or {})["data"]["id"]
        try:  # blocks until the session is idle again
            await self._json("POST", f"/experimental/session/{session_id}/wait", timeout=timeout)
            running = False
        except httpx.TimeoutException:
            running = True
        shaped = [shape_message(m) for m in await self.messages(session_id, 50, stop_at=sent)]
        reply = "\n\n".join(m["text"] for m in shaped if m["type"] == "assistant" and m.get("text"))
        out: dict[str, Any] = {"session_id": session_id, "reply": reply[:MAX_TEXT]}
        if tools := [t for m in shaped for t in m.get("tools", [])]:
            out["tools_used"] = tools
        if errs := [m["error"] for m in shaped if m.get("error")]:
            out["error"] = errs[-1]
        if running:
            out["note"] = (f"Still running after {int(timeout)}s. Read it later with opencode_session_messages({session_id!r}); "
                           "if it never finishes it may be waiting on a permission prompt in OpenCode itself.")
        return out


class Service:
    """The connection to the service, made on first use and made again once when a call cannot connect."""

    def __init__(self, connect: Callable[[], Awaitable[tuple[OpenCodeClient, str]]] | None = None) -> None:
        self._connect = connect or self._find
        self.client: OpenCodeClient | None = None
        self.url: str | None = None

    @staticmethod
    async def _find() -> tuple[OpenCodeClient, str]:
        url = await asyncio.to_thread(server_url)
        return OpenCodeClient(httpx.AsyncClient(base_url=url, headers=auth_headers(service_password()), timeout=30)), url

    async def call(self, fn: Callable[[OpenCodeClient], Awaitable[Any]]) -> Any:
        for retry in (False, True):
            if self.client is None:
                self.client, self.url = await self._connect()
            try:
                return await fn(self.client)
            except httpx.ConnectError:
                stale, self.client = self.client, None
                await stale.http.aclose()
                if retry:
                    raise RuntimeError("the OpenCode service is not answering; try `opencode service start` in a terminal") from None
        raise AssertionError("unreachable")


def build_server() -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp_types import ToolAnnotations

    server = MCPServer("opencode", instructions="Drives a local OpenCode coding agent: give it a self-contained task and a folder.")
    svc = Service()

    @server.tool(description="Run a prompt in OpenCode, in a new session (optionally in `directory`) or an existing `session_id`. "
                             "Returns the reply text, the session id and the tools it used.")
    async def opencode_prompt(prompt: str, directory: str | None = None, session_id: str | None = None,
                              timeout_seconds: int = 900) -> dict[str, Any]:
        return await svc.call(lambda c: c.prompt(prompt, directory, session_id, float(max(5, min(timeout_seconds, 3600)))))

    @server.tool(description="List recent OpenCode sessions, newest first.", annotations=ToolAnnotations(read_only_hint=True))
    async def opencode_sessions(limit: int = 20) -> list[dict[str, Any]]:
        return await svc.call(lambda c: c.sessions(max(1, min(limit, 100))))

    @server.tool(description="Read the latest messages of an OpenCode session, oldest first.", annotations=ToolAnnotations(read_only_hint=True))
    async def opencode_session_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [shape_message(m) for m in await svc.call(lambda c: c.messages(session_id, max(1, min(limit, 200))))]

    @server.tool(description="OpenCode's version, server address and whether it answers.", annotations=ToolAnnotations(read_only_hint=True))
    async def opencode_status() -> dict[str, Any]:
        try:
            info = await svc.call(lambda c: c.info())
        except Exception as e:  # noqa: BLE001 - status reports, never raises
            return {"reachable": False, "error": str(e)[:300]}
        return {"reachable": True, "version": info.get("version"), "url": svc.url}

    return server


if __name__ == "__main__":
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines would land in the connector's log
    build_server().run("stdio")
