"""Feature-module contract (docs/module-manifest.md) and the todos extension, against the real app.

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

from calendar_ext import calendar_box  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, host, modules, tasks_sync, todos, toolbox  # noqa: E402
from personal_os.extensions.google.client import GoogleNotConnected  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})

TODO_TOOLS = ["todo_list", "todo_add", "todo_update", "todo_delete"]
CALENDAR_TOOLS = ["calendar_events", "calendar_get", "meeting_brief", "calendar_create", "calendar_update", "calendar_delete", "calendar_respond",
                  "calendar_free_busy", "calendar_find_time", "calendar_propose"]


def _route_set() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        rs = r.original_router.routes if hasattr(r, "original_router") else [r]
        out |= {(x.path, m) for x in rs if hasattr(x, "methods") for m in x.methods}
    return out


class HostTests(unittest.TestCase):
    def test_modules_and_built_in_extensions_load_and_the_app_starts_through_the_lifespan(self) -> None:
        from personal_os import app as appmod
        self.assertEqual(list(appmod.host.extensions), ["google", "todos", "calendar", "mail", *(m.key for m in modules), "health", "spaces", "lean", "focus", "coding", "telegram"])
        self.assertIs(appmod.google, appmod._ext_ctx.services["google"]())
        self.assertEqual(app.router.on_startup + app.router.on_shutdown, [])  # no @app.on_event handlers remain
        with TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN}) as c:  # runs the lifespan
            self.assertEqual(c.get("/health").status_code, 200)


class ToolTests(unittest.TestCase):
    def test_todo_tools_follow_working_memory(self) -> None:
        names = list(toolbox.specs)
        i = names.index("todo_list")
        self.assertEqual(names[i:i + 4], TODO_TOOLS)
        self.assertEqual(names[i - 2:i], ["read_tool_result", "search_tool_results"])  # last of the working-memory block
        self.assertFalse([n for n in names if n.startswith("board_")])  # boards are todos now

    def test_health_tools_keep_their_place_after_the_modules_tools(self) -> None:
        names = list(toolbox.specs)
        i = names.index("health_summary")
        self.assertEqual(names[i - 2:i + 4], ["mail_followups", "schedule_suggest", "health_summary", "health_log", "health_delete_entry", "doc_list"])

    def test_calendar_tools_keep_their_place_before_the_gmail_tools(self) -> None:
        names = list(toolbox.specs)
        i = names.index("calendar_events")
        self.assertEqual(names[i:i + 10], CALENDAR_TOOLS)
        self.assertEqual(names[i - 1], "dashboard_pin")
        self.assertEqual(names[i + 10], "gmail_search")

    def test_mail_tools_keep_their_place_after_the_calendar_tools_with_the_times_draft_between_draft_and_send(self) -> None:
        names = list(toolbox.specs)
        i = names.index("gmail_search")
        self.assertEqual(names[i - 1:i + 7], ["calendar_propose", "gmail_search", "gmail_read", "gmail_draft", "propose_times_draft", "gmail_send",
                                              "gmail_outbox", "gmail_modify"])
        self.assertEqual(names[i + 7], "google_tasks_list")

    def test_space_tools_keep_their_place_after_the_google_tools(self) -> None:
        names = list(toolbox.specs)
        i = names.index("space_list")
        self.assertEqual(names[i - 1:i + 4], ["google_sheets_create", "space_list", "space_add_widget", "space_arrange", "find_files"])

    def test_tool_metadata_unchanged(self) -> None:
        for n in TODO_TOOLS:
            self.assertEqual(toolbox.specs[n].group, "todos")
        self.assertEqual(toolbox.specs["todo_list"].danger, "safe")
        self.assertEqual(toolbox.specs["todo_delete"].danger, "writes")
        from personal_os import app as appmod
        appmod.db.set_settings({"hiddenViews": []})  # a fresh install starts with Lists off
        self.assertTrue(all(toolbox.available(n) for n in TODO_TOOLS))

    def test_toolbox_without_modules_has_no_todo_tools(self) -> None:
        box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
        self.assertFalse(set(TODO_TOOLS) & set(box.specs))

    def test_google_tasks_tools_hide_while_tasks_sync_is_on(self) -> None:
        class Google:
            def status(self) -> dict:
                return {"connected": True}

        cfg: dict = {}
        box = calendar_box(Google(), settings=cfg)
        self.assertFalse(box.available("google_tasks_list"))  # sync defaults on
        self.assertTrue(box.available("calendar_events"))
        cfg["googleTasksSync"] = {"enabled": False}
        self.assertTrue(box.available("google_tasks_list"))

    def test_propose_times_draft_needs_both_the_calendar_and_mail_views(self) -> None:
        from personal_os import app as appmod
        for hidden, on in [([], True), (["mail"], False), (["calendar"], False)]:
            appmod.db.set_settings({"hiddenViews": hidden})
            self.assertEqual(toolbox.available("propose_times_draft", google_ok=True), on, hidden)
        appmod.db.set_settings({"hiddenViews": []})

    def test_suggest_times_is_a_409_while_google_is_not_connected(self) -> None:
        body = {"duration_minutes": 30, "window_start": "2026-10-26", "window_end": "2026-10-28"}
        spec = toolbox.specs["calendar_find_time"]
        orig = spec.fn

        async def fail(*a: object, **k: object) -> None:
            raise GoogleNotConnected("Google account not connected")
        spec.fn = fail  # type: ignore[assignment]
        try:
            r = client.post("/integrations/google/gmail/suggest-times", json=body)
        finally:
            spec.fn = orig  # type: ignore[assignment]
        self.assertEqual((r.status_code, r.json()["detail"]), (409, "Google account not connected"))

    def test_tools_use_the_extensions_store(self) -> None:
        t = asyncio.run(toolbox.specs["todo_add"].fn({"project_id": None}, title="module tool todo"))
        try:
            self.assertEqual(todos.get(t["id"])["title"], "module tool todo")
        finally:
            todos.delete(t["id"])


class RouteTests(unittest.TestCase):
    def test_paths_are_registered(self) -> None:
        got = _route_set()
        for want in [("/todos", "GET"), ("/todos", "POST"), ("/todos/{id}", "PUT"), ("/todos/{id}", "DELETE"),
                     ("/integrations/google/tasks-sync", "GET"), ("/integrations/google/tasks-sync", "PUT"),
                     ("/integrations/google/tasks-sync/run", "POST"),
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

    def test_sync_routes_answer_while_lists_is_hidden_but_the_todo_routes_do_not(self) -> None:
        from personal_os import app as appmod
        appmod.db.set_settings({"hiddenViews": ["todos"]})
        try:
            self.assertEqual(client.get("/integrations/google/tasks-sync").status_code, 200)
            self.assertEqual(client.get("/todos").status_code, 404)
        finally:
            appmod.db.set_settings({"hiddenViews": []})

    def test_sync_run_maps_errors(self) -> None:
        # Not-connected is a 409 the UI turns into "connect Google"; anything else is a 502.
        target = tasks_sync
        orig = target.sync_once
        try:
            for exc, code in [(GoogleNotConnected("no"), 409), (RuntimeError("boom"), 502)]:
                def fail(exc: Exception = exc) -> None:
                    raise exc
                target.sync_once = fail  # type: ignore[method-assign]
                self.assertEqual(client.post("/integrations/google/tasks-sync/run").status_code, code)
        finally:
            target.sync_once = orig  # type: ignore[method-assign]


class DashboardTests(unittest.TestCase):
    def test_dashboard_keeps_todo_keys(self) -> None:
        from personal_os import app as appmod
        appmod.db.set_settings({"hiddenViews": []})  # a fresh install starts with Lists off, and then the dashboard omits todos
        t = client.post("/todos", json={"title": "dash todo"}).json()
        try:
            d = client.get("/dashboard").json()
            self.assertIn(t["id"], [x["id"] for x in d["todos"]])
            self.assertEqual(set(d["todo_stats"]), set(todos.stats()))
            self.assertLessEqual(len(d["todos"]), 12)
        finally:
            client.delete(f"/todos/{t['id']}")

    def test_today_payload(self) -> None:
        self.assertEqual(set(host.extensions["todos"].recap_sections[0].card()), {"todos", "todo_stats"})


class LoopTests(unittest.TestCase):
    def test_on_change_pokes_the_sync_loop(self) -> None:
        hits: list[str] = []
        a = tasks_sync.poke
        tasks_sync.poke = lambda: hits.append("tasks")  # type: ignore[method-assign]
        try:
            t = todos.create("poke me")
            todos.delete(t["id"])
        finally:
            tasks_sync.poke = a
        self.assertIn("tasks", hits)


class ModulesStampTests(unittest.TestCase):
    def test_stamp_4_shows_library_and_cowork_once_and_keeps_other_hides(self) -> None:
        from personal_os import app as appmod
        appmod.db.set_settings({"modulesDefault": 3, "hiddenViews": ["library", "cowork", "health"]})
        appmod._seed_hidden_modules()
        s = appmod.db.get_settings()
        self.assertEqual(s["hiddenViews"], ["health"])
        self.assertEqual(s["modulesDefault"], appmod._MODULES_DEFAULT)
        appmod.db.set_settings({"hiddenViews": ["library"]})  # hidden again by the user after the stamp
        appmod._seed_hidden_modules()
        self.assertEqual(appmod.db.get_settings()["hiddenViews"], ["library"])

    def test_fresh_install_hides_nothing(self) -> None:
        from personal_os import app as appmod
        appmod.db.set_settings({"modulesDefault": 0, "hiddenViews": [], "homeWidgets": {}})
        appmod._seed_hidden_modules()
        s = appmod.db.get_settings()
        self.assertEqual(s["hiddenViews"], [])
        self.assertEqual(s["homeWidgets"], {})

    @staticmethod
    def _reset(appmod) -> None:  # type: ignore[no-untyped-def]
        with appmod.db.tx() as c:
            c.execute("DELETE FROM settings WHERE key IN ('modulesDefault', 'hiddenViews', 'homeWidgets', 'extDefaults')")
            c.execute("DELETE FROM conversations")

    def test_first_boot_applies_no_profile(self) -> None:
        """No profile is on by default any more: a fresh install is lean because its extensions start off (test_fresh_install_extensions)."""
        from personal_os import app as appmod
        self._reset(appmod)
        appmod._seed_hidden_modules()
        self.assertEqual(appmod.db.get_settings()["hiddenViews"], [])
        self.assertFalse(host.extensions["lean"].enabled())
        self.assertFalse(host.extensions["focus"].enabled())

    def test_install_with_chats_keeps_every_view(self) -> None:
        from personal_os import app as appmod
        self._reset(appmod)
        appmod.convos.create(None, "old chat", "")
        appmod._seed_hidden_modules()
        self.assertEqual(appmod.db.get_settings()["hiddenViews"], [])
        self._reset(appmod)

    def test_off_views_take_their_tools_with_them(self) -> None:
        from personal_os import app as appmod
        modes = {n: "on" for n in toolbox.specs}
        names = lambda: {t["function"]["name"] for t in toolbox.schemas(modes)}  # noqa: E731
        gated = ("health_summary", "todo_list", "todo_add", "calendar_events", "meeting_brief", "gmail_search", "mail_followups",
                 "schedule_suggest", "space_list")
        appmod.db.set_settings({"hiddenViews": []})
        self.assertTrue(set(gated) <= set(toolbox.specs))
        on = names()
        self.assertIn("todo_write", on)
        appmod.db.set_settings({"hiddenViews": ["todos", "calendar", "mail", "health", "spaces"]})
        off = names()
        self.assertIn("todo_write", off)  # the working checklist is not Lists
        self.assertFalse(set(gated) & off)
        self.assertTrue({"health_summary", "todo_list", "space_list"} <= on)  # the non-Google ones return with the view
        appmod.db.set_settings({"hiddenViews": []})


if __name__ == "__main__":
    unittest.main()
