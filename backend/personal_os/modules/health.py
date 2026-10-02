"""Health tracking as a feature module: the metric catalog and reading log (health.py), routes, agent tools,
and the Today payload. No background loops and no outside service: everything stays in the local db.

Routes live under /health/…; bare GET /health is the app's unauthenticated liveness probe, and the
auth middleware matches public paths exactly, so these stay token-gated (test_health pins that).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..health import Health, HealthError
from ..tools import ToolSpec, _obj, tool_error
from . import Module, ModuleContext

if TYPE_CHECKING:
    from ..tools import Toolbox


class MetricIn(BaseModel):
    label: str
    unit: str = ""
    kind: str = "number"
    agg: str | None = None
    goal: float | None = None
    goal_dir: str | None = None
    decimals: int = 0


class MetricPatch(BaseModel):
    label: str | None = None
    unit: str | None = None
    agg: str | None = None
    goal: float | None = None
    goal_dir: str | None = None
    decimals: int | None = None
    hidden: bool | None = None
    position: int | None = None
    clear_goal: bool = False


class EntryIn(BaseModel):
    metric: str
    value: float
    day: str | None = None
    note: str = ""


class EntryPatch(BaseModel):
    value: float | None = None
    day: str | None = None
    note: str | None = None


def _fmt(m: dict[str, Any], v: float | None) -> str | None:
    if v is None:
        return None
    if m["kind"] == "check":
        return "yes" if v >= 1 else "no"
    s = f"{v:.{m['decimals']}f}"
    if m["unit"] == "/5":
        return f"{s}/5"
    return f"{s} {m['unit']}" if m["unit"] else s


class HealthModule(Module):
    key = "health"
    label = "Health"

    def __init__(self, ctx: ModuleContext) -> None:
        super().__init__(ctx)
        self.store = Health(ctx.db)

    # ---- routes ----
    def router(self) -> APIRouter:
        r = APIRouter(prefix="/health")
        store = self.store

        def guard(fn: Any, *a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except HealthError as e:
                raise HTTPException(400, str(e)) from e

        @r.get("/metrics")
        def list_metrics() -> list[dict[str, Any]]:
            return store.metrics()

        @r.post("/metrics")
        def create_metric(body: MetricIn) -> dict[str, Any]:
            return guard(store.create_metric, body.label, body.unit, body.kind, body.agg, body.goal, body.goal_dir, body.decimals)

        @r.put("/metrics/{key}")
        def update_metric(key: str, body: MetricPatch) -> dict[str, Any]:
            patch = body.model_dump(exclude_none=True, exclude={"clear_goal"})
            if body.clear_goal:
                patch["goal"] = patch["goal_dir"] = None
            m = guard(store.update_metric, key, patch)
            if not m:
                raise HTTPException(404)
            return m

        @r.delete("/metrics/{key}")
        def delete_metric(key: str) -> dict[str, bool]:
            guard(store.delete_metric, key)
            return {"ok": True}

        @r.get("/summary")
        def summary(days: int = 30, today: str | None = None, include_hidden: bool = False) -> list[dict[str, Any]]:
            return guard(store.summary, days, today, include_hidden)

        @r.get("/entries")
        def list_entries(metric: str | None = None, since: str | None = None, until: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
            return guard(store.entries, metric, since, until, limit)

        @r.post("/entries")
        def create_entry(body: EntryIn) -> dict[str, Any]:
            return guard(store.log, body.metric, body.value, body.day, body.note)

        @r.put("/entries/{id}")
        def update_entry(id: str, body: EntryPatch) -> dict[str, Any]:
            e = guard(store.update_entry, id, body.model_dump(exclude_none=True))
            if not e:
                raise HTTPException(404)
            return e

        @r.delete("/entries/{id}")
        def delete_entry(id: str) -> dict[str, bool]:
            if not store.delete_entry(id):
                raise HTTPException(404)
            return {"ok": True}

        return r

    # ---- agent tools ----
    def register_tools(self, box: Toolbox) -> None:
        R = box.specs.__setitem__
        store = self.store

        def unknown(name: str) -> Any:
            keys = [m["key"] for m in store.metrics()]
            return tool_error(f"No health metric '{name}'.", field="metric", expected=f"one of: {', '.join(keys)}",
                              example={"metric": "sleep", "value": 7.5}, alternative="health_summary to see every metric")

        async def health_summary(ctx: dict[str, Any], days: int = 7, metric: str | None = None, today: str | None = None) -> Any:
            m = None
            if metric:
                m = store.resolve(metric)
                if not m:
                    return unknown(metric)
            try:
                rows = store.summary(days, today, include_hidden=m is not None, metric=m["key"] if m else None)
            except HealthError as e:
                return tool_error(str(e), field="today", expected="YYYY-MM-DD", example={"days": 7})
            out = []
            for s in rows:
                row: dict[str, Any] = {
                    "metric": s["key"], "label": s["label"], "kind": s["kind"], "unit": s["unit"], "per_day": s["agg"],
                    "today": _fmt(s, s["today"]), "avg": _fmt(s, s["avg"]), "previous_avg": _fmt(s, s["prev_avg"]),
                    "days_logged": s["logged_days"], "last": s["last"],
                }
                if s["goal"] is not None:
                    row.update(goal=f"{s['goal_dir'].replace('_', ' ')} {_fmt(s, s['goal'])}", days_met=s["met_days"], streak=s["streak"])
                if m or days <= 14:
                    row["daily"] = {p["day"]: _fmt(s, p["value"]) for p in s["series"] if p["value"] is not None}
                if m:
                    row["recent_entries"] = [{"id": e["id"], "day": e["day"], "value": e["value"], "note": e["note"]}
                                             for e in store.entries(m["key"], limit=20)]
                out.append(row)
            return {"days": days, "metrics": out}
        R("health_summary", ToolSpec("health_summary",
            "Read the user's health log: each tracked metric (sleep, steps, water, exercise, weight, mood, meds, and any custom ones) with today's value, "
            "the average over the last `days` days versus the period before, goal and streak. Pass `metric` for one metric's daily values and its recent entries (with ids).",
            _obj({"days": {"type": "integer", "default": 7}, "metric": {"type": "string"}, "today": {"type": "string", "description": "the user's local date, YYYY-MM-DD"}}, []),
            health_summary, "health", examples=[{}, {"days": 30}, {"metric": "sleep", "days": 14}]))

        async def health_log(ctx: dict[str, Any], metric: str, value: float, day: str | None = None, note: str = "") -> Any:
            m = store.resolve(metric)
            if not m:
                return unknown(metric)
            try:
                e = store.log(m["key"], value, day, note, source="assistant")
            except HealthError as err:
                return tool_error(str(err), field="value", expected="a number in the metric's range (scale 1-5, check 1/0)",
                                  example={"metric": m["key"], "value": 1 if m["kind"] == "check" else 3})
            return {"id": e["id"], "metric": m["key"], "day": e["day"], "logged": _fmt(m, e["value"])}
        R("health_log", ToolSpec("health_log",
            "Log a health reading for the user. `metric` is a key or label from health_summary. Values: hours for sleep, a count for steps/water, minutes for exercise, "
            "the metric's unit for weight/heart rate, 1-5 for mood/energy, 1 (yes) or 0 (no) for yes/no metrics like meds. `day` is YYYY-MM-DD and defaults to today; "
            "file last night's sleep under the day the user woke up.",
            _obj({"metric": {"type": "string"}, "value": {"type": "number"}, "day": {"type": "string"}, "note": {"type": "string"}}, ["metric", "value"]),
            health_log, "health", "writes",
            examples=[{"metric": "sleep", "value": 7.5}, {"metric": "water", "value": 2}, {"metric": "mood", "value": 4, "note": "good run"},
                      {"metric": "meds", "value": 1, "day": "2026-10-01"}]))

        async def health_delete_entry(ctx: dict[str, Any], id: str) -> Any:
            e = store.entry(id)
            if not e:
                return tool_error(f"No health entry with id '{id}'.", field="id", expected="an id from health_summary(metric=…).recent_entries",
                                  example={"id": "a1b2c3"}, alternative="health_summary with `metric` to list entry ids")
            store.delete_entry(id)
            return {"deleted": {"metric": e["metric"], "day": e["day"], "value": e["value"]}}
        R("health_delete_entry", ToolSpec("health_delete_entry", "Delete one logged health reading by id (from health_summary with `metric`). Only when the user asks to remove or correct an entry.",
            _obj({"id": {"type": "string"}}, ["id"]), health_delete_entry, "health", "writes", examples=[{"id": "a1b2c3"}]))

    def today(self) -> dict[str, Any]:
        return {"health": self.store.today()}
