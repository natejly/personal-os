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
from ..todos import UNTRUSTED_SOURCES, Todos
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
    estimate_min: int | None = None
    tags: list[str] | None = None
    parent_id: str | None = None
    depends_on: list[str] | None = None
    list_name: str | None = None
    status: str | None = None


class TodoFilterIn(BaseModel):
    name: str
    tag: str = ""
    q: str = ""
    project_id: str | None = None


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
    estimate_min: int | None = None
    clear_estimate: bool = False
    calendar_event_id: str | None = None
    calendar_link: str | None = None
    calendar_id: str | None = None
    tags: list[str] | None = None
    parent_id: str | None = None
    clear_parent: bool = False
    depends_on: list[str] | None = None  # replaces the whole set; [] clears it
    list_name: str | None = None
    clear_list: bool = False
    status: str | None = None  # the board column; a done-like one completes the todo
    position: float | None = None


class TodoMoveIn(BaseModel):
    status: str
    before_id: str | None = None


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
        def list_todos(project_id: str | None = "all", include_done: bool = False, q: str = "", sort: str = "due", tag: str = "", list_name: str = "") -> list[dict[str, Any]]:
            scope = "__all__" if project_id in (None, "all") else ctx.sid(project_id)
            rows = store.list(scope, include_done, q, tag, list_name)
            if sort == "urgency":
                today = date.today()
                for t in rows:
                    t["urgency"] = todo_rules.urgency_of(t, today)
                rows.sort(key=lambda t: -t["urgency"])  # stable: ties keep the due order
            return store.nest(rows)  # subtasks follow their parent

        @r.get("/todo-lists")
        def list_todo_lists() -> list[str]:
            """The list names in use (what boards used to be), for the board view's picker."""
            return store.lists()

        @r.get("/todo-filters")
        def list_todo_filters() -> list[dict[str, Any]]:
            return store.filters()

        @r.post("/todo-filters")
        def save_todo_filter(body: TodoFilterIn) -> dict[str, Any]:
            if not body.name.strip():
                raise HTTPException(400, "Empty name")
            return store.save_filter(body.name, body.model_dump())

        @r.delete("/todo-filters/{id}")
        def delete_todo_filter(id: str) -> dict[str, bool]:
            store.delete_filter(id)
            return {"ok": True}

        @r.post("/todos")
        def create_todo(body: TodoIn) -> dict[str, Any]:
            if not body.title.strip():
                raise HTTPException(400, "Empty title")
            try:
                return store.create(body.title, ctx.wsid(body.project_id), body.notes, body.due, body.priority, repeat=body.repeat, estimate_min=body.estimate_min, tags=body.tags, parent_id=body.parent_id, list_name=body.list_name, status=body.status)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

        @r.put("/todos/{id}")
        def update_todo(id: str, body: TodoPatch) -> dict[str, Any]:
            patch = body.model_dump(exclude_none=True, exclude={"clear_due", "clear_project", "clear_repeat", "clear_estimate", "clear_parent", "clear_list"})
            if body.clear_due:
                patch["due"] = None
            if body.clear_estimate:
                patch["estimate_min"] = None
            if body.clear_repeat:
                patch["repeat"] = None
            if body.clear_parent:
                patch["parent_id"] = None
            if body.clear_list:
                patch["list_name"] = None
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

        @r.post("/todos/{id}/move")
        def move_todo(id: str, body: TodoMoveIn) -> dict[str, Any]:
            """Board drag: change the status (column) and slot the todo before `before_id`."""
            t = store.move(id, body.status, body.before_id)
            if not t:
                raise HTTPException(404)
            return t

        @r.delete("/todos/{id}")
        def delete_todo(id: str) -> dict[str, bool]:
            store.trash(id)  # restorable from the trash; Google still gets the delete
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

        async def todo_list(ctx: dict[str, Any], include_done: bool = False, all_projects: bool = False, offset: int = 0, sort: str = "due", tag: str = "", list_name: str = "", status: str = "") -> Any:
            scope = "__all__" if all_projects else ctx["project_id"]
            items = store.list(scope, include_done=include_done, tag=tag, list_name=list_name) if not all_projects else store.list("__all__", include_done=include_done, tag=tag, list_name=list_name)
            if not all_projects and ctx["project_id"] is not None:
                items = store.list(ctx["project_id"], include_done=include_done, tag=tag, list_name=list_name) + store.list(None, include_done=include_done, tag=tag, list_name=list_name)
            if status.strip():
                items = [t for t in items if t["status"].lower() == status.strip().lower()]
            today = date.today()
            if sort == "urgency":
                items = sorted(items, key=lambda t: -todo_rules.urgency_of(t, today))
            rows = [{"id": t["id"], "title": t["title"], "due": t["due"], "priority": t["priority"], "done": bool(t["done"]), "notes": t["notes"][:200],
                     "urgency": todo_rules.urgency_of(t, today), "repeat": t.get("repeat"), "estimate_min": t.get("estimate_min"),
                     "tags": t.get("tags"), "parent_id": t.get("parent_id"), "blocked_by": t.get("depends_on"), "list": t.get("list_name"), "status": t["status"]} for t in items]
            out = page(rows, offset=offset, limit=50, key="todos")
            # A meeting, an email follow-up, or a task synced from Google was not typed in this app.
            shown = {r["id"] for r in out["todos"]}
            if any(t.get("source") in UNTRUSTED_SOURCES and t["id"] in shown for t in items):
                ctx["tainted"] = True
                ctx.setdefault("taint_sources", []).append("todo_list")
            return out
        R("todo_list", ToolSpec("todo_list", "List the user's todos (open by default) in this chat's scope: the project's todos plus personal ones.",
            _obj({"include_done": {"type": "boolean", "default": False}, "all_projects": {"type": "boolean", "default": False}, "offset": {"type": "integer", "default": 0},
             "sort": {"type": "string", "enum": ["due", "urgency"], "default": "due", "description": "urgency orders by a weighted score (due, priority, age); each row carries its urgency number."},
             "tag": {"type": "string", "description": "Only todos carrying this tag."},
             "list_name": {"type": "string", "description": "Only todos on this list (a named group, shown as one board)."},
             "status": {"type": "string", "description": "Only todos in this board column, e.g. Backlog, To do, In progress, Done."}}, []), todo_list, "todos",
            examples=[{}, {"include_done": True}, {"all_projects": True, "offset": 50}, {"sort": "urgency"}]))

        async def todo_add(ctx: dict[str, Any], title: str, due: str | None = None, notes: str = "", priority: int = 2, personal: bool = False,
                           repeat_every: int | None = None, repeat_unit: str | None = None, repeat_mode: str = "from_due",
                           estimate_min: int | None = None, tags: list[str] | None = None, parent_id: str | None = None,
                           list_name: str | None = None, status: str | None = None) -> Any:
            try:
                rp = {"every": repeat_every or 1, "unit": repeat_unit, "mode": repeat_mode} if repeat_unit else None
                t = store.create(title, None if personal else ctx["project_id"], notes=notes, due=due, priority=priority, repeat=rp, estimate_min=estimate_min, tags=tags, parent_id=parent_id, list_name=list_name, status=status)
            except ValueError as e:
                return tool_error(str(e), field="repeat_unit or parent_id", expected="day, week, month or year; parent_id from todo_list", example={"title": "Water plants", "due": "2026-10-05", "repeat_every": 1, "repeat_unit": "week"})
            return {"id": t["id"], "title": t["title"], "due": t["due"]}
        R("todo_add", ToolSpec("todo_add", "Add a todo for the user. Dates as YYYY-MM-DD. Priority 1 (high) to 3 (low). Not for your own working checklist — that is todo_write.",
            _obj({"title": {"type": "string"}, "due": {"type": "string"}, "notes": {"type": "string"}, "priority": {"type": "integer", "default": 2}, "personal": {"type": "boolean", "default": False},
             "repeat_every": {"type": "integer", "description": "Repeat every N units (default 1)."}, "repeat_unit": {"type": "string", "enum": ["day", "week", "month", "year"]},
             "repeat_mode": {"type": "string", "enum": ["from_due", "from_completion"], "default": "from_due"},
             "estimate_min": {"type": "integer", "description": "Expected minutes of work; the planner uses it to time-block."},
             "tags": {"type": "array", "items": {"type": "string"}}, "parent_id": {"type": "string", "description": "Make this a subtask of the todo with this id."},
             "list_name": {"type": "string", "description": "Put it on this named list (a project-like group, shown as a board), e.g. \"Home renovation\"."},
             "status": {"type": "string", "description": "Board column: Backlog, To do, In progress or Done (default To do)."}}, ["title"]), todo_add, "todos", "writes",
            examples=[{"title": "Renew passport", "due": "2026-10-14", "priority": 1},
                      {"title": "Buy milk", "personal": True},
                      {"title": "Water the plants", "due": "2026-10-05", "repeat_every": 1, "repeat_unit": "week"},
                      {"title": "Draft the migration plan", "notes": "start from the Q3 doc", "priority": 2},
                      {"title": "Order tiles", "list_name": "Home renovation", "status": "Backlog"}]))

        async def todo_update(ctx: dict[str, Any], id: str, done: bool | None = None, title: str | None = None, due: str | None = None, priority: int | None = None, notes: str | None = None,
                              repeat_every: int | None = None, repeat_unit: str | None = None, repeat_mode: str = "from_due",
                              estimate_min: int | None = None, tags: list[str] | None = None, parent_id: str | None = None,
                              depends_on: list[str] | None = None, list_name: str | None = None, status: str | None = None) -> Any:
            patch = {k: v for k, v in {"done": done, "title": title, "due": due, "priority": priority, "notes": notes, "estimate_min": estimate_min,
                                       "tags": tags, "parent_id": parent_id, "depends_on": depends_on, "list_name": list_name, "status": status}.items() if v is not None}
            if repeat_unit:
                patch["repeat"] = {"every": repeat_every or 1, "unit": repeat_unit, "mode": repeat_mode}
            try:
                t = store.update(id, patch)
            except ValueError as e:
                return tool_error(str(e), field="repeat_unit, parent_id or depends_on", expected="a valid value; ids from todo_list, no cycles", example={"id": "td_8c41a2", "repeat_unit": "week"})
            return t or tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                                   example={"id": "td_8c41a2", "done": True}, alternative="todo_list to get the current ids")
        R("todo_update", ToolSpec("todo_update", "Update or complete a todo by id (from todo_list).",
            _obj({"id": {"type": "string"}, "done": {"type": "boolean"}, "title": {"type": "string"}, "due": {"type": "string"}, "priority": {"type": "integer"}, "notes": {"type": "string"},
             "repeat_every": {"type": "integer"}, "repeat_unit": {"type": "string", "enum": ["day", "week", "month", "year"]},
             "repeat_mode": {"type": "string", "enum": ["from_due", "from_completion"], "default": "from_due"},
             "estimate_min": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}, "description": "Replaces the todo's tags."},
             "parent_id": {"type": "string", "description": "Nest under this todo (subtask)."},
             "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Ids of todos that must finish first; replaces the set, [] clears it."},
             "list_name": {"type": "string", "description": "Move it to this named list; \"\" takes it off its list."},
             "status": {"type": "string", "description": "Move it to this board column (Backlog, To do, In progress, Done). Done completes it; any other reopens it."}}, ["id"]), todo_update, "todos", "writes",
            examples=[{"id": "td_8c41a2", "done": True}, {"id": "td_8c41a2", "due": "2026-11-01", "priority": 1}, {"id": "td_8c41a2", "title": "Renew passport (expedited)"},
                      {"id": "td_8c41a2", "repeat_every": 2, "repeat_unit": "week"}, {"id": "td_8c41a2", "status": "In progress"}]))

        async def todo_delete(ctx: dict[str, Any], id: str) -> Any:
            t = store.get(id)
            if not t:
                return tool_error(f"No todo with id '{id}'.", field="id", expected="an id from todo_list",
                                  example={"id": "td_8c41a2"}, alternative="todo_list to get the current ids")
            store.trash(id)
            return {"deleted": t["title"], "note": "moved to the trash; the user can restore it for 30 days"}
        R("todo_delete", ToolSpec("todo_delete", "Delete a todo by id (it goes to the trash, restorable for 30 days). Prefer todo_update(done=true) to complete; delete only when the user asks to remove it.",
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
        d = date.today()
        rows = sorted(self.store.list("__all__", include_done=False), key=lambda t: -todo_rules.urgency(t, d))  # stable: ties keep the due order
        return {"todos": rows[:12], "todo_stats": self.store.stats()}
