"""Feature-module contract (docs/module-manifest.md): TodosModule against the real app.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_modules.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="modulestest-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, modules, toolbox  # noqa: E402
from personal_os.google import GoogleNotConnected  # noqa: E402
from personal_os.modules import Module, get  # noqa: E402
from personal_os.modules.todos import TodosModule  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
todos = get(modules, "todos", TodosModule)

TODO_TOOLS = ["todo_list", "todo_add", "todo_update", "todo_delete"]


def _route_set() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        rs = r.original_router.routes if hasattr(r, "original_router") else [r]
        out |= {(x.path, m) for x in rs if hasattr(x, "methods") for m in x.methods}
    return out


class ToolTests(unittest.TestCase):
    def test_todo_tools_sit_between_working_and_boards(self) -> None:
        names = list(toolbox.specs)
        i = names.index("todo_list")
        self.assertEqual(names[i:i + 4], TODO_TOOLS)
        self.assertEqual(names[i - 1], "skill_propose")
        self.assertEqual(names[i + 4], "board_list")

    def test_tool_metadata_unchanged(self) -> None:
        for n in TODO_TOOLS:
            self.assertEqual(toolbox.specs[n].group, "todos")
        self.assertEqual(toolbox.specs["todo_list"].danger, "safe")
        self.assertEqual(toolbox.specs["todo_delete"].danger, "writes")
        self.assertTrue(all(toolbox.available(n) for n in TODO_TOOLS))

    def test_toolbox_without_modules_has_no_todo_tools(self) -> None:
        box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
        self.assertFalse(set(TODO_TOOLS) & set(box.specs))

    def test_tool_available_can_veto(self) -> None:
        class Veto(Module):
            key = "veto"

            def tool_available(self, name: str) -> bool | None:
                return False if name == "todo_list" else None

        box = Toolbox(None, None, None, lambda: {}, modules=[todos, Veto(todos.ctx)])  # type: ignore[arg-type]
        self.assertFalse(box.available("todo_list"))
        self.assertTrue(box.available("todo_add"))

    def test_tools_use_the_modules_store(self) -> None:
        t = asyncio.run(toolbox.specs["todo_add"].fn({"project_id": None}, title="module tool todo"))
        try:
            self.assertEqual(todos.store.get(t["id"])["title"], "module tool todo")
        finally:
            todos.store.delete(t["id"])


class RouteTests(unittest.TestCase):
    def test_paths_are_registered(self) -> None:
        got = _route_set()
        for want in [("/todos", "GET"), ("/todos", "POST"), ("/todos/{id}", "PUT"), ("/todos/{id}", "DELETE"),
                     ("/integrations/google/tasks-sync", "GET"), ("/integrations/google/tasks-sync", "PUT"),
                     ("/integrations/google/tasks-sync/run", "POST"),
                     ("/integrations/google/todo-calendar", "GET"), ("/integrations/google/todo-calendar", "PUT"),
                     ("/integrations/google/todo-calendar/run", "POST"),
                     ("/integrations/google/tasklists", "GET")]:
            self.assertIn(want, got)

    def test_todo_crud(self) -> None:
        r = client.post("/todos", json={"title": "  "})
        self.assertEqual(r.status_code, 400)
        t = client.post("/todos", json={"title": "route todo", "priority": 1}).json()
        try:
            self.assertIn(t["id"], [x["id"] for x in client.get("/todos").json()])
            r = client.put(f"/todos/{t['id']}", json={"done": True})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["done"])
            self.assertEqual(client.put("/todos/td_nope", json={"title": "x"}).status_code, 404)
        finally:
            self.assertEqual(client.delete(f"/todos/{t['id']}").json(), {"ok": True})

    def test_sync_routes_report_status(self) -> None:
        self.assertEqual(client.get("/integrations/google/tasks-sync").status_code, 200)
        self.assertEqual(client.get("/integrations/google/todo-calendar").status_code, 200)

    def test_sync_run_maps_errors(self) -> None:
        # Not-connected is a 409 the UI turns into "connect Google"; anything else is a 502.
        for url, target in [("/integrations/google/tasks-sync/run", todos.tasks_sync),
                            ("/integrations/google/todo-calendar/run", todos.calendar_mirror)]:
            orig = target.sync_once
            try:
                for exc, code in [(GoogleNotConnected("no"), 409), (RuntimeError("boom"), 502)]:
                    def fail(exc: Exception = exc) -> None:
                        raise exc
                    target.sync_once = fail  # type: ignore[method-assign]
                    self.assertEqual(client.post(url).status_code, code)
            finally:
                target.sync_once = orig  # type: ignore[method-assign]


class DashboardTests(unittest.TestCase):
    def test_dashboard_keeps_todo_keys(self) -> None:
        t = client.post("/todos", json={"title": "dash todo"}).json()
        try:
            d = client.get("/dashboard").json()
            self.assertIn(t["id"], [x["id"] for x in d["todos"]])
            self.assertEqual(set(d["todo_stats"]), set(todos.store.stats()))
            self.assertLessEqual(len(d["todos"]), 12)
        finally:
            client.delete(f"/todos/{t['id']}")

    def test_today_payload(self) -> None:
        self.assertEqual(set(todos.today()), {"todos", "todo_stats"})


class LoopTests(unittest.TestCase):
    def test_on_change_pokes_both_loops(self) -> None:
        hits: list[str] = []
        a, b = todos.tasks_sync.poke, todos.calendar_mirror.poke
        todos.tasks_sync.poke = lambda: hits.append("tasks")  # type: ignore[method-assign]
        todos.calendar_mirror.poke = lambda: hits.append("cal")  # type: ignore[method-assign]
        try:
            t = todos.store.create("poke me")
            todos.store.delete(t["id"])
        finally:
            todos.tasks_sync.poke, todos.calendar_mirror.poke = a, b
        self.assertIn("tasks", hits)
        self.assertIn("cal", hits)

    def test_start_stop_leave_no_running_tasks(self) -> None:
        async def go() -> list[bool]:
            before = asyncio.all_tasks()
            await todos.start()
            started = [t for t in asyncio.all_tasks() if t not in before]
            states = [not t.done() for t in started]
            await todos.stop()
            return states + [t.done() for t in started] + [bool(todos._tasks)]

        res = asyncio.run(go())
        self.assertEqual(res[:2], [True, True])  # two loops were running
        self.assertEqual(res[2:4], [True, True])  # and both are finished after stop
        self.assertFalse(res[4])

    def test_stop_without_start_is_a_noop(self) -> None:
        asyncio.run(todos.stop())


if __name__ == "__main__":
    unittest.main()
