"""Todos as a feature module: store, routes, agent tools, the two Google sync loops, and the Today payload."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import date
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import todo_rules
from ..google import GoogleNotConnected
from ..gtasks import TasksSync
from ..todocal import TodoCalendarMirror
from ..todos import Todos
from ..tools import ToolSpec, _obj, page, tool_error
from . import Module, ModuleContext

if TYPE_CHECKING:
    from ..tools import Toolbox


class TodoIn(BaseModel):
    title: str
    project_id: str | None = None
    notes: str = ""
    due: str | None = None
    priority: int = 2
    repeat: dict[str, Any] | None = None


class TodoPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    due: str | None = None
    priority: int | None = None
    done: bool | None = None
    project_id: str | None = None
    clear_due: bool = False
    clear_project: bool = False
    repeat: dict[str, Any] | None = None
    clear_repeat: bool = False
    calendar_event_id: str | None = None
    calendar_link: str | None = None
    calendar_id: str | None = None


class TasksSyncIn(BaseModel):
    enabled: bool | None = None
    tasklist: str | None = None
    intervalMinutes: int | None = None


class TodoCalendarIn(BaseModel):
    enabled: bool | None = None
    calendarId: str | None = None
    calendarName: str | None = None
    intervalMinutes: int | None = None
    keepCompleted: bool | None = None


class TodosModule(Module):
    key = "todos"
    label = "Todos"

    def __init__(self, ctx: ModuleContext) -> None:
        super().__init__(ctx)
        self.store = Todos(ctx.db)
        self.tasks_sync = TasksSync(self.store, ctx.google, ctx.settings, ctx.set_settings)
        self.calendar_mirror = TodoCalendarMirror(self.store, ctx.google, ctx.settings, ctx.set_settings)
        self.store.on_change = self._changed
        self._tasks: list[asyncio.Task[Any]] = []

    def _changed(self) -> None:
        """Any todo write (routes or assistant tools) nudges both Google sync loops."""
        self.tasks_sync.poke()
        self.calendar_mirror.poke()

    # ---- routes ----
    def router(self) -> APIRouter:
        r = APIRouter()
        store, ctx = self.store, self.ctx

        @r.get("/todos")
        def list_todos(project_id: str | None = "all", include_done: bool = False, q: str = "", sort: str = "due") -> list[dict[str, Any]]:
            scope = "__all__" if project_id in (None, "all") else ctx.sid(project_id)
            rows = store.list(scope, include_done, q)
            if sort == "urgency":
                today = date.today()
                for t in rows:
                    t["urgency"] = todo_rules.urgency(t, today)
                rows.sort(key=lambda t: -t["urgency"])  # stable: ties keep the due order
            return rows

        @r.post("/todos")
        def create_todo(body: TodoIn) -> dict[str, Any]:
            if not body.title.strip():
                raise HTTPException(400, "Empty title")
            try:
                return store.create(body.title, ctx.wsid(body.project_id), body.notes, body.due, body.priority, repeat=body.repeat)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

        @r.put("/todos/{id}")
        def update_todo(id: str, body: TodoPatch) -> dict[str, Any]:
            patch = body.model_dump(exclude_none=True, exclude={"clear_due", "clear_project", "clear_repeat"})
            if body.clear_due:
                patch["due"] = None
            if body.clear_repeat:
                patch["repeat"] = None
            if body.clear_project:
                patch["project_id"] = None
            elif "project_id" in patch:
                patch["project_id"] = ctx.wsid(patch["project_id"])
            try:
                t = store.update(id, patch)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            if not t:
                raise HTTPException(404)
            return t

        @r.delete("/todos/{id}")
        def delete_todo(id: str) -> dict[str, bool]:
            store.delete(id)
            return {"ok": True}

        # ---- Google Tasks <-> todos sync ----
        @r.get("/integrations/google/tasks-sync")
        def google_tasks_sync_status() -> dict[str, Any]:
            return self.tasks_sync.status()

        @r.put("/integrations/google/tasks-sync")
        def google_tasks_sync_config(body: TasksSyncIn) -> dict[str, Any]:
            self.tasks_sync.set_config(body.model_dump(exclude_none=True))
            return self.tasks_sync.status()

        @r.post("/integrations/google/tasks-sync/run")
        async def google_tasks_sync_run() -> dict[str, Any]:
            try:
                await asyncio.to_thread(self.tasks_sync.sync_once)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Google Tasks sync failed: {e}") from e
            return self.tasks_sync.status()

        # ---- todos -> Google Calendar mirror ----
        @r.get("/integrations/google/todo-calendar")
        def google_todo_calendar_status() -> dict[str, Any]:
            return self.calendar_mirror.status()

        @r.put("/integrations/google/todo-calendar")
        def google_todo_calendar_config(body: TodoCalendarIn) -> dict[str, Any]:
            self.calendar_mirror.set_config(body.model_dump(exclude_none=True))
            return self.calendar_mirror.status()

        @r.post("/integrations/google/todo-calendar/run")
        async def google_todo_calendar_run() -> dict[str, Any]:
            try:
                await asyncio.to_thread(self.calendar_mirror.sync_once)
            except GoogleNotConnected as e:
                raise HTTPException(409, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Todo calendar sync failed: {e}") from e
            return self.calendar_mirror.status()

        return r

    # ---- agent tools ----
    def register_tools(self, box: Toolbox) -> None:
        R = box.specs.__setitem__
        store = self.store

        async def todo_list(ctx: dict[str, Any], include_done: bool = False, all_projects: bool = False, offset: int = 0, sort: str = "due") -> Any:
            scope = "__all__" if all_projects else ctx["project_id"]
            items = store.list(scope, include_done=include_done) if not all_projects else store.list("__all__", include_done=include_done)
            if not all_projects and ctx["project_id"] is not None:
                items = store.list(ctx["project_id"], include_done=include_done) + store.list(None, include_done=include_done)
            today = date.today()
            if sort == "urgency":
                items = sorted(items, key=lambda t: -todo_rules.urgency(t, today))
            rows = [{"id": t["id"], "title": t["title"], "due": t["due"], "priority": t["priority"], "done": bool(t["done"]), "notes": t["notes"][:200],
                     "urgency": todo_rules.urgency(t, today), "repeat": t.get("repeat")} for t in items]
            return page(rows, offset=offset, limit=50, key="todos")
        R("todo_list", ToolSpec("todo_list", "List the user's todos (open by default) in this chat's scope: the project's todos plus personal ones.",
            _obj({"include_done": {"type": "boolean", "default": False}, "all_projects": {"type": "boolean", "default": False}, "offset": {"type": "integer", "default": 0},
             "sort": {"type": "string", "enum": ["due", "urgency"], "default": "due", "description": "urgency orders by a Taskwarrior-style score (due, priority, age); each row carries its urgency number."}}, []), todo_list, "todos",
            examples=[{}, {"include_done": True}, {"all_projects": True, "offset": 50}, {"sort": "urgency"}]))

        async def todo_add(ctx: dict[str, Any], title: str, due: str | None = None, notes: str = "", priority: int = 2, personal: bool = False,
                           repeat_every: int | None = None, repeat_unit: str | None = None, repeat_mode: str = "from_due") -> Any:
            try:
                rp = {"every": repeat_every or 1, "unit": repeat_unit, "mode": repeat_mode} if repeat_unit else None
                t = store.create(title, None if personal else ctx["project_id"], notes=notes, due=due, priority=priority, repeat=rp)
            except ValueError as e:
                return tool_error(str(e), field="repeat_unit", expected="day, week, month or year", example={"title": "Water plants", "due": "2026-10-05", "repeat_every": 1, "repeat_unit": "week"})
            return {"id": t["id"], "title": t["title"], "due": t["due"]}
        R("todo_add", ToolSpec("todo_add", "Add a todo for the user. Dates as YYYY-MM-DD. Priority 1 (high) to 3 (low).",
            _obj({"title": {"type": "string"}, "due": {"type": "string"}, "notes": {"type": "string"}, "priority": {"type": "integer", "default": 2}, "personal": {"type": "boolean", "default": False},
             "repeat_every": {"type": "integer", "description": "Repeat every N units (default 1)."}, "repeat_unit": {"type": "string", "enum": ["day", "week", "month", "year"]},
             "repeat_mode": {"type": "string", "enum": ["from_due", "from_completion"], "default": "from_due"}}, ["title"]), todo_add, "todos", "writes",
            examples=[{"title": "Renew passport", "due": "2026-10-14", "priority": 1},
                      {"title": "Buy milk", "personal": True},
                      {"title": "Water the plants", "due": "2026-10-05", "repeat_every": 1, "repeat_unit": "week"},
                      {"title": "Draft the migration plan", "notes": "start from the Q3 doc", "priority": 2}]))

        async def todo_update(ctx: dict[str, Any], id: str, done: bool | None = None, title: str | None = None, due: str | None = None, priority: int | None = None, notes: str | None = None,
                              repeat_every: int | None = None, repeat_unit: str | None = None, repeat_mode: str = "from_due") -> Any:
            patch = {k: v for k, v in {"done": done, "title": title, "due": due, "priority": priority, "notes": notes}.items() if v is not None}
            if repeat_unit:
                patch["repeat"] = {"every": repeat_every or 1, "unit": repeat_unit, "mode": repeat_mode}
            try:
                t = store.update(id, patch)
            except ValueError as e:
                return tool_error(str(e), field="repeat_unit", expected="day, week, month or year", example={"id": "td_8c41a2", "repeat_unit": "week"})
            return t or tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                                   example={"id": "td_8c41a2", "done": True}, alternative="todo_list to get the current ids")
        R("todo_update", ToolSpec("todo_update", "Update or complete a todo by id (from todo_list).",
            _obj({"id": {"type": "string"}, "done": {"type": "boolean"}, "title": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer"}, "notes": {"type": "string"},
             "repeat_every": {"type": "integer"}, "repeat_unit": {"type": "string", "enum": ["day", "week", "month", "year"]},
             "repeat_mode": {"type": "string", "enum": ["from_due", "from_completion"], "default": "from_due"}}, ["id"]), todo_update, "todos", "writes",
            examples=[{"id": "td_8c41a2", "done": True}, {"id": "td_8c41a2", "due": "2026-11-01", "priority": 1}, {"id": "td_8c41a2", "title": "Renew passport (expedited)"},
                      {"id": "td_8c41a2", "repeat_every": 2, "repeat_unit": "week"}]))

        async def todo_delete(ctx: dict[str, Any], id: str) -> Any:
            t = store.get(id)
            if not t:
                return tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                                  example={"id": "td_8c41a2"}, alternative="todo_list to get the current ids")
            store.delete(id)
            return {"deleted": t["title"]}
        R("todo_delete", ToolSpec("todo_delete", "Delete a todo permanently by id. Prefer todo_update(done=true) to complete; delete only when the user asks to remove it.",
            _obj({"id": {"type": "string"}}, ["id"]), todo_delete, "todos", "writes", examples=[{"id": "td_8c41a2"}]))

    # ---- loops ----
    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self.tasks_sync.loop()), asyncio.create_task(self.calendar_mirror.loop())]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    def today(self) -> dict[str, Any]:
        return {"todos": self.store.list("__all__", include_done=False)[:12], "todo_stats": self.store.stats()}
