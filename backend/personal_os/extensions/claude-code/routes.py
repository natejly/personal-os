"""Routes: the UI's (mounted at /claude-code) and the permission hook's (under /coding-hooks/claude-code, the app's public
prefix for routes that carry their own per-session key).

The hook route is the only door a session's process has into the app. It answers a loopback caller that presents the
session's key (header X-Grain-Hook-Key, compared in constant time) while that session's process is alive; every other case is
the same 403, so a session id cannot be probed. It can only ask a question and be answered: it has no field for approving by
itself, and the decision comes from the approval card.
"""
from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ... import shell
from ...bounds import clamp_limit
from ..coding.sessions import CodingError
from . import store

if TYPE_CHECKING:
    from .manager import ClaudeCode

LOOPBACK = ("127.0.0.1", "::1")
KEY_HEADER = "x-grain-hook-key"


class SendIn(BaseModel):
    message: str


class PermissionIn(BaseModel):
    tool_name: str = Field(max_length=100)
    tool_input: dict[str, Any] = {}
    cwd: str | None = None
    permission_mode: str | None = None
    claude_session_id: str | None = None


def ui_router(cc: ClaudeCode) -> APIRouter:
    r = APIRouter(prefix="/claude-code")

    def known(sid: str) -> dict[str, Any]:
        try:
            return cc.known(sid)
        except CodingError as e:
            raise HTTPException(404, "No such Claude Code session") from e

    @r.get("/sessions")
    async def list_sessions(limit: int = 100) -> dict[str, Any]:
        return {"sessions": cc.sessions(clamp_limit(limit, 500))}

    @r.get("/sessions/{sid}")
    async def get_session(sid: str) -> dict[str, Any]:
        return cc.view(known(sid))

    @r.get("/sessions/{sid}/events")
    async def session_events(sid: str, after: int = 0, limit: int = 500) -> dict[str, Any]:
        row = known(sid)
        return {"events": store.events_after(cc.db, row["id"], max(0, after), clamp_limit(limit, store.EVENT_CAP))}

    @r.post("/sessions/{sid}/send")
    async def send_session(sid: str, body: SendIn) -> dict[str, Any]:
        row = known(sid)
        try:
            return cc.view(await cc.send(row["id"], body.message))
        except (CodingError, shell.ShellError) as e:
            raise HTTPException(409, shell._scrub(str(e))) from e

    @r.post("/sessions/{sid}/stop")
    async def stop_session(sid: str) -> dict[str, Any]:
        row = known(sid)
        try:
            return cc.view(await cc.stop(row["id"]))
        except (CodingError, shell.ShellError) as e:
            raise HTTPException(409, shell._scrub(str(e))) from e

    @r.get("/fleets")
    async def list_fleets() -> dict[str, Any]:
        return {"fleets": cc.fleets()}

    return r


def hook_router(cc: ClaudeCode) -> APIRouter:
    r = APIRouter(prefix="/coding-hooks/claude-code")

    @r.post("/{session_id}/permission")
    async def permission(session_id: str, body: PermissionIn, request: Request) -> dict[str, Any]:
        h = cc.handles.get(session_id)
        key = request.headers.get(KEY_HEADER, "")
        if (not request.client or request.client.host not in LOOPBACK or h is None or h.row["id"] != session_id
                or h.proc is None or not h.proc.alive or not h.row.get("hook_key")
                or not secrets.compare_digest(key.encode(), h.row["hook_key"].encode())):
            raise HTTPException(403, "Forbidden")
        return await cc.decide_permission(session_id, body.model_dump())

    return r
