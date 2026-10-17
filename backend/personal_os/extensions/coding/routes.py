"""The coding sessions' routes: the list, one session's logs, events and diff, and stopping or answering one."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import shell as shell_tool
from ...bounds import clamp_limit
from . import events as coding_events
from .sessions import CodingError, summary

if TYPE_CHECKING:
    from .extension import Coding


class CodingSendIn(BaseModel):
    message: str


class BridgeReply(BaseModel):
    id: str
    result: dict[str, Any]


def sessions_router(coding: Coding) -> APIRouter:
    r = APIRouter()

    def known(id: str) -> dict[str, Any]:
        row = coding.sessions.get(id)
        if not row:
            raise HTTPException(404, "No such coding session")
        return coding.sessions.refresh(row)

    async def do(fn: Any, *args: Any) -> Any:
        try:
            return await fn(*args)
        except (CodingError, shell_tool.ShellError) as e:
            raise HTTPException(409, shell_tool._scrub(str(e))) from e

    @r.get("/coding-sessions")
    async def list_coding_sessions() -> dict[str, Any]:
        # Off the loop: each refresh reads files (the topic hands a publish from a worker thread to the loop).
        return {"sessions": await asyncio.to_thread(lambda: [summary(coding.sessions.refresh(s), None) for s in coding.sessions.list(50)])}

    @r.get("/coding-sessions/{id}")
    async def get_coding_session(id: str) -> dict[str, Any]:
        return summary(known(id), None)

    @r.get("/coding-sessions/{id}/logs")
    async def coding_session_logs(id: str, limit: int = 4000) -> dict[str, Any]:
        row = known(id)
        return {"id": id, "output": row["log_tail"][-clamp_limit(limit, 50_000):], "status": row["status"]}

    @r.get("/coding-sessions/{id}/events")
    async def coding_session_events(id: str, after: int = 0, limit: int = 500) -> dict[str, Any]:
        """The session's structured live feed, oldest first: events with seq above `after`."""
        known(id)
        return {"events": coding_events.list_after(coding.sessions.db, id, max(0, after), clamp_limit(limit, coding_events.CAP))}

    @r.get("/coding-sessions/{id}/diff")
    async def coding_session_diff(id: str, full: int = 0) -> dict[str, Any]:
        known(id)
        return await do(coding.sessions.diff, id, bool(full))

    @r.post("/coding-sessions/{id}/stop")
    async def stop_coding_session(id: str) -> dict[str, Any]:
        known(id)
        return summary(await do(coding.sessions.stop, id), None)

    @r.post("/coding-sessions/{id}/send")
    async def send_coding_session(id: str, body: CodingSendIn) -> dict[str, Any]:
        """A follow-up from the user: only a session that has finished or been stopped can take one."""
        known(id)
        return summary(await do(coding.sessions.send, id, body.message), None)

    return r


def terminals_router(coding: Coding) -> APIRouter:
    r = APIRouter()

    @r.post("/terminal-bridge/next")
    async def bridge_next() -> dict[str, Any]:
        """The desktop app's long poll: the next command for the terminal hub, or none after a wait."""
        return {"command": await coding.terminals.bridge.next()}

    @r.post("/terminal-bridge/reply")
    async def bridge_reply(body: BridgeReply) -> dict[str, bool]:
        return {"ok": coding.terminals.bridge.reply(body.id, body.result)}

    @r.get("/coding/terminals")
    async def list_terminals() -> dict[str, Any]:
        """The terminals agents have open, for the Terminal card."""
        return {"terminals": await coding.terminals.live_status()}

    return r
