"""Planner as a feature module: suggest time blocks for todos, and apply the ones the user accepts.

Suggesting is read-only: it reads calendar events and todos and returns proposals. The only write is
POST /planner/apply, which the UI calls when the user presses "Add selected to calendar", and it goes
through the same verified `calendar_create` as every other event. The agent tool never writes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..todos import UNTRUSTED_SOURCES

from .. import planner as pl
from ..google import GoogleNotConnected
from ..todos import Todos
from ..tools import ToolSpec, _obj, tool_error
from . import Module, ModuleContext

if TYPE_CHECKING:
    from ..tools import Toolbox


class SuggestIn(BaseModel):
    days: int | None = None
    project_id: str | None = None


class ApplyIn(BaseModel):
    blocks: list[dict[str, Any]]


class PlannerConfigIn(BaseModel):
    workStart: str | None = None
    workEnd: str | None = None
    workDays: list[int] | None = None
    bufferMin: int | None = None
    minBlockMin: int | None = None
    maxBlockMin: int | None = None
    slotStepMin: int | None = None
    lookaheadDays: int | None = None
    calendarName: str | None = None


class PlannerModule(Module):
    key = "planner"
    label = "Planner"

    def __init__(self, ctx: ModuleContext, clock: Callable[[], datetime] = datetime.now) -> None:
        super().__init__(ctx)
        self.todos = Todos(ctx.db)  # same table as the todos module; a second handle is how modules coexist
        self.clock = clock

    def config(self) -> dict[str, Any]:
        stored = self.ctx.settings().get("planner") or {}
        return {**pl.DEFAULT_CONFIG, **{k: v for k, v in stored.items() if k in pl.DEFAULT_CONFIG}}

    def _mirror_ids(self) -> list[str]:
        cid = (self.ctx.settings().get("googleTodoCalendar") or {}).get("calendarId")
        return [cid] if cid else []

    def suggest_sync(self, days: int | None = None, project_id: str | None = None) -> dict[str, Any]:
        """Read events, plan. Blocking (Google); call from a worker thread. Writes nothing."""
        cfg = self.config()
        if days:
            cfg = {**cfg, "lookaheadDays": max(1, min(int(days), 30))}
        now = self.clock().replace(second=0, microsecond=0)
        events = self.ctx.google.calendar_events(cfg["lookaheadDays"] + 1, "primary", 100, None, ["all"])
        mirror = self._mirror_ids()
        scope = "__all__" if project_id in (None, "", "all") else self.ctx.sid(project_id)
        todos = self.todos.list(scope, include_done=False)
        result = pl.plan(todos, pl.busy_from_events(events, mirror), now, cfg, pl.locked_from_events(events, mirror))
        # A meeting, an email follow-up, or a Google task title is other people's words. The proposal shows that title.
        holds_untrusted = any(t.get("source") in UNTRUSTED_SOURCES for t in todos)
        return {**result, "generated_at": now.isoformat(timespec="minutes"), "holds_untrusted": holds_untrusted}

    def apply_sync(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        g = self.ctx.google
        target = (self._mirror_ids() or [None])[0] or g.calendar_ensure(self.config()["calendarName"])["id"]
        results: list[dict[str, Any]] = []
        for b in blocks:
            try:
                start, end = datetime.fromisoformat(str(b["start"])), datetime.fromisoformat(str(b["end"]))
                if end <= start:
                    raise ValueError("end must be after start")
                title, tid = str(b.get("title") or "Task"), b.get("todo_id")
                out = g.calendar_create({"summary": f"{pl.FOCUS_PREFIX}{title}", "start": start.isoformat(timespec="minutes"),
                                         "end": end.isoformat(timespec="minutes"), "description": f"Planned by Grain from todo {tid}",
                                         "transparency": "opaque"}, calendar_id=target, send_updates="none")
                if out.get("verified"):
                    results.append({"todo_id": tid, "ok": True, "event_id": out.get("id"), "link": out.get("link")})
                else:  # unverified is a failure, never a success
                    results.append({"todo_id": tid, "ok": False, "error": "Created but not verified: " + str((out.get("verification") or {}).get("status") or "unknown")})
            except GoogleNotConnected:
                raise
            except Exception as e:  # noqa: BLE001
                results.append({"todo_id": b.get("todo_id"), "ok": False, "error": str(e)})
        return results

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.get("/planner/config")
        def get_config() -> dict[str, Any]:
            return self.config()

        @r.put("/planner/config")
        def put_config(body: PlannerConfigIn) -> dict[str, Any]:
            cfg = {**self.config(), **body.model_dump(exclude_none=True)}
            if (msg := pl.validate_config(cfg)):
                raise HTTPException(422, msg)
            self.ctx.set_settings({"planner": cfg})
            return self.config()

        @r.post("/planner/suggest")
        async def suggest(body: SuggestIn) -> dict[str, Any]:
            try:
                return await asyncio.to_thread(self.suggest_sync, body.days, body.project_id)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Could not read the calendar: {e}") from e

        @r.post("/planner/apply")
        async def apply(body: ApplyIn) -> dict[str, Any]:
            if not body.blocks or len(body.blocks) > 50:
                raise HTTPException(400, "Send between 1 and 50 blocks")
            try:
                return {"results": await asyncio.to_thread(self.apply_sync, body.blocks)}
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Google API error: {e}") from e

        return r

    def register_tools(self, box: Toolbox) -> None:
        async def schedule_suggest(ctx: dict[str, Any], days: int = 5) -> Any:
            plan = await asyncio.to_thread(self.suggest_sync, days, None)
            if plan.pop("holds_untrusted", False):
                ctx["tainted"] = True
                ctx.setdefault("taint_sources", []).append("schedule_suggest")
            titles = {b["todo_id"]: b["title"] for b in plan["blocks"]}
            blocks = [{"todo_id": b["todo_id"], "title": b["title"], "start": b["start"], "end": b["end"], "part": b["part"],
                       "why": "due {due}, priority {priority}, energy {energy}, time {time} (weighted)".format(**b["why"])} for b in plan["blocks"]]
            return {"proposed_blocks": blocks, "unplaced": [{**u, "title": titles.get(u["id"])} for u in plan["unplaced"]],
                    "note": "Proposals only; nothing was added to the calendar. Present these as one calendar_propose call (one create per block) so the user approves them on one card."}
        box.specs["schedule_suggest"] = ToolSpec(
            "schedule_suggest",
            "Suggest calendar time blocks for the user's open todos (uses their estimates, due dates, priorities, work hours and existing meetings). Read-only: it proposes and never creates events. Present these as one calendar_propose call (one create per block) so the user approves them on one card.",
            _obj({"days": {"type": "integer", "default": 5}}, []), schedule_suggest, "google",
            examples=[{}, {"days": 3}])

    def today(self) -> dict[str, Any]:
        """Proposed blocks from open todos and the saved calendar snapshot. Never calls Google; nothing is written."""
        cfg = self.config()
        events = self.ctx.google.calendar_saved(cfg["lookaheadDays"] + 1)
        if events is None:  # no snapshot: planning around unknown meetings would be a guess
            return {"planner_blocks": []}
        now = self.clock().replace(second=0, microsecond=0)
        mirror = self._mirror_ids()
        todos = self.todos.list("__all__", include_done=False)
        return {"planner_blocks": pl.plan(todos, pl.busy_from_events(events, mirror), now, cfg, pl.locked_from_events(events, mirror))["blocks"]}
