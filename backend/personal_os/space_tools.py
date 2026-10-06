"""space_list / space_add_widget / space_arrange: the model's door into the canvas spaces (canvas.py).

Local and additive: a tool can put an existing object on a space or tile the windows already there, never close
or delete one, and never touches a locked space. Kept out of tools.py; box.canvases is set
by app.py once the store exists, until then the tools are not offered.
"""
from __future__ import annotations

import math
from typing import Any

from . import redact
from .canvas import WIDGET_KINDS
from .presets import REF_TABLES, _exists

GAP, CELL_W, CELL_H, MAX_COLS, CASCADE_STEP = 16, 480, 560, 3, 32


def _rects(n: int, layout: str) -> list[tuple[float, float, float, float]]:
    if layout == "cascade":
        return [(i * CASCADE_STEP, i * CASCADE_STEP, CELL_W, CELL_H) for i in range(n)]
    cols = min(MAX_COLS, math.ceil(math.sqrt(n))) or 1
    return [(GAP + (i % cols) * (CELL_W + GAP), GAP + (i // cols) * (CELL_H + GAP), CELL_W, CELL_H) for i in range(n)]


def register(box: Any) -> None:
    from .tools import ToolSpec, _obj  # late: circular at import time

    def _space(canvas_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        c = box.canvases.get(canvas_id)
        if not c:
            return None, {"error": f"No space {canvas_id}", "hint": "use an id from space_list"}
        if c.get("locked"):
            return None, {"error": "That space is locked by the user; ask them to unlock it."}
        return c, None

    async def space_list(ctx: dict[str, Any]) -> Any:
        return {"spaces": [{"canvas_id": c["id"], "name": redact.scrub_command_output(str(c["name"] or "")),
                            "locked": bool(c.get("locked")),
                            "windows": [{"window_id": w["id"], "kind": w["kind"], "ref_id": w["ref_id"],
                                         "title": redact.scrub_command_output(str(w["title"] or "")),
                                         "state": w["state"]} for w in c["windows"]]} for c in box.canvases.list()]}
    box.specs["space_list"] = ToolSpec("space_list", "List the user's spaces (canvases) and the windows on each.",
                                       _obj({}, []), space_list, "spaces", "safe")

    async def space_add_widget(ctx: dict[str, Any], canvas_id: str, kind: str, ref_id: str = "", title: str = "") -> Any:
        if kind not in WIDGET_KINDS:
            return {"error": f"Unknown kind {kind!r}", "kinds": list(WIDGET_KINDS)}
        c, err = _space(canvas_id)
        if err:
            return err
        if kind in REF_TABLES:
            with box.canvases.db.tx() as conn:
                ok = _exists(conn, REF_TABLES[kind], ref_id)
            if not ok:
                return {"error": f"No {kind} with id {ref_id!r}", "hint": f"a {kind} window needs the id of an existing {kind}"}
        # lands in the next free grid cell, so it never covers what is already there
        x, y, w, h = _rects(len(c["windows"]) + 1, "grid")[-1]  # type: ignore[index]
        win = box.canvases.add_window(canvas_id, kind, ref_id or None, None, title, x, y, w, h)
        return {"window_id": win["id"], "canvas_id": canvas_id, "kind": kind, "added": True} if win else {"error": "space vanished"}
    box.specs["space_add_widget"] = ToolSpec(
        "space_add_widget",
        "Put a window on a space: a todos/calendar/memory/etc. view, or an existing chat, note or project "
        "(pass its id as ref_id). Never closes or moves other windows.",
        _obj({"canvas_id": {"type": "string"}, "kind": {"type": "string", "enum": list(WIDGET_KINDS)},
              "ref_id": {"type": "string", "description": "Required for chat, note, project"},
              "title": {"type": "string"}}, ["canvas_id", "kind"]),
        space_add_widget, "spaces", "writes")

    async def space_arrange(ctx: dict[str, Any], canvas_id: str, layout: str = "grid") -> Any:
        if layout not in ("grid", "cascade"):
            return {"error": "layout must be 'grid' or 'cascade'"}
        c, err = _space(canvas_id)
        if err:
            return err
        wins = [w for w in c["windows"] if w["state"] == "normal"]  # type: ignore[index]
        entries = [{"id": w["id"], "x": x, "y": y, "w": ww, "h": hh} for w, (x, y, ww, hh) in zip(wins, _rects(len(wins), layout))]
        return {"canvas_id": canvas_id, "layout": layout, "arranged": box.canvases.set_layout(canvas_id, entries)}
    box.specs["space_arrange"] = ToolSpec(
        "space_arrange", "Tile ('grid') or stack ('cascade') the open windows of a space. Only moves and resizes; closes nothing.",
        _obj({"canvas_id": {"type": "string"}, "layout": {"type": "string", "enum": ["grid", "cascade"]}}, ["canvas_id"]),
        space_arrange, "spaces", "writes")

    for n in ("space_list", "space_add_widget", "space_arrange"):
        box.specs[n].available_fn = lambda: box.canvases is not None
