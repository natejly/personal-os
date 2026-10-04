"""A day plan that includes a meeting task taints the run.

Run: PYTHONPATH=backend python backend/tests/test_planner_taint.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os.modules.planner import PlannerModule  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _mod(source: str) -> PlannerModule:
    class Todos:
        def list(self, *_a, **_k):
            return [{"id": "t1", "title": "Forward the contract", "source": source, "done": 0,
                     "due": "2026-10-02", "priority": 1, "estimate_min": 30, "notes": ""}]

    class Google:
        def calendar_events(self, *_a, **_k):
            return []

    class Ctx:
        google = Google()

        def settings(self):
            return {}

        def sid(self, project_id):
            return project_id

    mod = PlannerModule.__new__(PlannerModule)
    mod.todos = Todos()
    mod.ctx = Ctx()
    mod.clock = lambda: datetime(2026, 10, 2, 9, 0)
    return mod


def test_a_meeting_task_taints_the_day_plan() -> None:
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    _mod("meeting").register_tools(box)
    ctx: dict = {"project_id": None}
    out = asyncio.run(box.call("schedule_suggest", {"days": 1}, ctx))
    assert "error" not in out, out
    assert "holds_untrusted" not in out
    assert ctx.get("tainted") is True
    assert "schedule_suggest" in ctx.get("taint_sources", [])
    assert box.gate("fetch_url", "on", ctx) == "ask"


def test_an_email_followup_taints_the_day_plan() -> None:
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    _mod("email").register_tools(box)
    ctx: dict = {"project_id": None}
    out = asyncio.run(box.call("schedule_suggest", {"days": 1}, ctx))
    assert "error" not in out, out
    assert ctx.get("tainted") is True
    assert box.gate("fetch_url", "on", ctx) == "ask"


def test_a_google_task_taints_the_day_plan() -> None:
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    _mod("google").register_tools(box)
    ctx: dict = {"project_id": None}
    out = asyncio.run(box.call("schedule_suggest", {"days": 1}, ctx))
    assert "error" not in out, out
    assert ctx.get("tainted") is True


def test_a_local_task_does_not_taint_the_day_plan() -> None:
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    _mod("local").register_tools(box)
    ctx: dict = {"project_id": None}
    out = asyncio.run(box.call("schedule_suggest", {"days": 1}, ctx))
    assert "error" not in out, out
    assert ctx.get("tainted") is not True
    assert "calendar_propose" in out["note"]  # the plan ends in one approvable card, not a button hunt


if __name__ == "__main__":
    test_a_meeting_task_taints_the_day_plan()
    test_an_email_followup_taints_the_day_plan()
    test_a_google_task_taints_the_day_plan()
    test_a_local_task_does_not_taint_the_day_plan()
    print("ok")
