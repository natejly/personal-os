"""The hook bridge's app side: Claude Code's own tool calls in a session Grain started, reported to the hook bus.

The session's `--settings` hooks (claude_hook) POST each tool call here, to `/coding-hooks/<session>/pre` before it runs
and `/post` after. `pre` is the gate `coding.tool.pre`: an extension may answer deny or ask, nothing else, and the hook
combines the answer with Grain's own floor (the stricter wins). `post` is `coding.tool.post`, observation only. The route
has no field for approving, answering a permission prompt, changing the mode or rewriting the input.

The hook runs outside the app, so the app token does not reach it. It carries the session's own key instead, which opens
these two routes for that session only, from this machine, while the session is live.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ... import redact
from ...tools import scrub_strings
from ..hooks import Deny
from . import events
from .sessions import LIVE, RESUME_GRACE

if TYPE_CHECKING:
    from .extension import Coding

STRING_CAP, PAYLOAD_CAP = 2_000, 16_000  # characters of one string, and of the whole payload an extension is handed
POST_TIMEOUT_S = 1.0
LOOPBACK = ("127.0.0.1", "::1")
KEY_HEADER = "x-grain-hook-key"
RECENT = 20  # events looked at to see whether the session's own timeline already recorded a tool call


class HookIn(BaseModel):
    tool: str = Field(max_length=100)
    input: dict[str, Any] = {}
    cwd: str | None = None
    call_id: str | None = None
    permission_mode: str | None = None
    lifted: bool = False
    result_preview: str | None = None
    duration_ms: int | None = None


def _cut(value: Any, cap: int) -> Any:
    if isinstance(value, str):
        return value[:cap]
    if isinstance(value, dict):
        return {str(k)[:cap]: _cut(v, cap) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v, cap) for v in value[:100]]
    return value


def payload(row: dict[str, Any], body: HookIn) -> dict[str, Any]:
    """What an extension is handed: credential-scrubbed, every string cut to 2 KB and the whole to 16 KB (the strings are
    cut shorter until it fits, then the input is dropped)."""
    out: dict[str, Any] = {"session_id": row["id"], "agent": "claude", "tool": body.tool, "input": scrub_strings(body.input),
                           "cwd": body.cwd, "worktree": row["worktree"], "permission_mode": body.permission_mode,
                           "lifted": body.lifted, "call_id": body.call_id, "conversation_id": row.get("conversation_id"),
                           "run_id": row.get("run_id")}
    if body.result_preview is not None:
        out.update(result_preview=redact.scrub_command_output(body.result_preview), duration_ms=body.duration_ms)
    cap = STRING_CAP
    while len(json.dumps(_cut(out, cap))) > PAYLOAD_CAP and cap > 50:
        cap //= 2
    out = _cut(out, cap)
    if len(json.dumps(out)) > PAYLOAD_CAP:
        out["input"] = {}
    return out


def _recorded(coding: Coding, sid: str, p: dict[str, Any], line: str) -> bool:
    """Whether the session's feed already has this call: reported twice, or seen by the timeline parser as a step."""
    return any((e["data"] or {}).get("call_id") == p["call_id"] or (e["tool"] == "step" and line and line in e["text"])
               for e in events.recent(coding.sessions.db, sid, RECENT))


def record(coding: Coding, p: dict[str, Any]) -> None:
    """Add a finished tool call to the session's feed, unless the feed has it already."""
    inp = p["input"]
    line = " ".join(str(inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("path") or "").split())[:200]
    if not _recorded(coding, p["session_id"], p, line):
        coding.sessions.event(p["session_id"], "tool", line, p["tool"], {"call_id": p["call_id"], "hook": True})


def bridge_router(coding: Coding) -> APIRouter:
    r = APIRouter(prefix="/coding-hooks")

    def session_for(session_id: str, request: Request) -> dict[str, Any]:
        """The session this key belongs to. Every refusal is the same 403, so a session id cannot be probed."""
        row = coding.sessions.get(session_id)
        key = request.headers.get(KEY_HEADER, "")
        ended = row is not None and row["status"] not in LIVE and row["status"] != "blocked" and time.time() - (row.get("ended_at") or 0) > RESUME_GRACE
        if (row is None or not row.get("hook_key") or row["id"] != session_id or ended
                or not secrets.compare_digest(key, row["hook_key"]) or not request.client or request.client.host not in LOOPBACK):
            raise HTTPException(403, "Forbidden")
        return row

    @r.post("/{session_id}/pre")
    async def pre(session_id: str, body: HookIn, request: Request) -> dict[str, Any]:
        p = payload(session_for(session_id, request), body)
        verdict = await coding.api.bus.gate("coding.tool.pre", p)
        if verdict is None:
            return {"verdict": None, "reason": ""}
        return {"verdict": "deny" if isinstance(verdict, Deny) else "ask", "reason": redact.scrub_command_output(verdict.reason)[:300]}

    @r.post("/{session_id}/post")
    async def post(session_id: str, body: HookIn, request: Request) -> dict[str, Any]:
        p = payload(session_for(session_id, request), body)
        await asyncio.to_thread(record, coding, p)
        try:
            await asyncio.wait_for(coding.api.bus.emit("coding.tool.post", p), POST_TIMEOUT_S)
        except asyncio.TimeoutError:
            pass
        return {}

    return r
