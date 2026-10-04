"""widget_create / widget_place: the model's door into declarative dashboard widgets (widget_spec.py) and the canvas.

Both are local writes. The spec comes from widget_spec.generate_spec (validated, at most one repair); a refresh afterwards is a
re-bind with no model call. Results never carry a source's secret: the source is named, not described.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import widget_spec
from .dashboards import INTERNAL_SOURCES, Dashboards

DASHBOARD = "Chat widgets"


def register(box: Any, store: Dashboards, canvases: Any, fetch: Callable[[str], Awaitable[Any]]) -> None:
    from .tools import ToolSpec, _obj, tool_error

    R = box.specs.__setitem__

    def _source(ref: str) -> dict[str, Any] | None:
        ref = (ref or "").strip()
        if s := store.source(ref):
            return s
        for s in store.sources():
            if s["name"].lower() == ref.lower():
                return s
        if ref.lower() in INTERNAL_SOURCES:
            for s in store.sources():
                if s["kind"] == "internal" and (s["config"] or {}).get("internal") == ref.lower():
                    return s
            return store.create_source(ref.lower(), "internal", {"internal": ref.lower()})
        return None

    async def widget_create(ctx: dict[str, Any], kind: str, source: str, prompt: str, title: str = "") -> Any:
        if kind not in widget_spec.KINDS:
            return tool_error(f"widget_create: kind must be one of {', '.join(widget_spec.KINDS)}", field="kind")
        src = _source(source)
        if not src:
            return tool_error(f"widget_create: no data source {source!r}", field="source",
                              expected="an internal source name (" + ", ".join(INTERNAL_SOURCES) + ") or the id of an existing source",
                              example={"kind": "chart", "source": "todos", "prompt": "open todos by priority"})
        d = next((x for x in store.list() if x["name"] == DASHBOARD), None) or store.create(DASHBOARD)
        w = store.create_widget(d["id"], title or prompt[:40], kind, prompt, [src["id"]])
        cfg = box.settings()
        w = await widget_spec.run_widget(store, w, cfg, cfg.get("extractionModel") or cfg["defaultModel"], fetch, regenerate=True)
        if w.get("data_error") and not (w.get("spec") or {}).get("kind"):
            store.delete_widget(w["id"])
            return tool_error(f"widget_create: {w['data_error']}", field="prompt")
        out: dict[str, Any] = {"widget_id": w["id"], "title": w["title"], "kind": w["kind"], "source": src["name"],
                               "note": "Call widget_place to put it on a space."}
        if w.get("data_error"):
            out["warning"] = w["data_error"]
        return out
    R("widget_create", ToolSpec(
        "widget_create", "Create a live chart, stat or table widget from a data source. The layout is generated and checked once; afterwards "
        "it refreshes from the source with no model call. source is an internal source name (" + ", ".join(INTERNAL_SOURCES) + ") or the id of an existing source.",
        _obj({"kind": {"type": "string", "enum": list(widget_spec.KINDS)}, "source": {"type": "string"},
              "prompt": {"type": "string", "description": "What to show"}, "title": {"type": "string"}}, ["kind", "source", "prompt"]),
        widget_create, "widgets", "writes",
        examples=[{"kind": "chart", "source": "todos", "prompt": "open todos by priority"}]))

    async def widget_place(ctx: dict[str, Any], widget_id: str, space: str = "") -> Any:
        w = store.widget(widget_id)
        if not w:
            return tool_error(f"widget_place: no widget {widget_id}", field="widget_id")
        spaces = canvases.list()
        c = next((x for x in spaces if space and (x["id"] == space or x["name"].lower() == space.lower())), None) \
            or (None if space else next((x for x in spaces if not x.get("locked")), None))
        if not c:
            return tool_error(f"widget_place: no space {space!r}" if space else "widget_place: every space is locked by the user",
                              field="space", expected="a space name or id; omit for the first unlocked space")
        if c.get("locked"):
            return tool_error("widget_place: that space is locked by the user; ask them to unlock it", field="space")
        win = canvases.add_window(c["id"], "dashboard-widget", ref_id=w["id"], title=w["title"], w=420, h=320)
        return {"placed": True, "window_id": win["id"], "space": c["name"], "widget_id": w["id"]}
    R("widget_place", ToolSpec(
        "widget_place", "Put a widget made with widget_create on a space (canvas) as a window. Omit space for the first unlocked one.",
        _obj({"widget_id": {"type": "string"}, "space": {"type": "string"}}, ["widget_id"]), widget_place, "widgets", "writes"))
