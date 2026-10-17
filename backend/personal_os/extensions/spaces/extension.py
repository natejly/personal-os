"""Spaces as a built-in extension: the canvases and their windows (store.py), the /canvases and /windows routes, and
the space_* agent tools.

The store is offered as the service `canvases`, because deleting a chat or a project sweeps its windows. A widget kind
is open: the app provides CORE_WIDGET_KINDS and an extension adds its own under contributes.slots["canvas.widget"].
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import AfterValidator, BaseModel, Field

from . import tools
from .store import CORE_WIDGET_KINDS, FALLBACK_NAME, SNAP_MODES, WINDOW_STATES, Canvases

if TYPE_CHECKING:
    from ..host import ExtensionScope


def _finite(v: float) -> float:
    """A coordinate that is not a real number is not a position.

    SQLite stores inf happily and pydantic then serialises it back as JSON null, so one Infinity -- a drag divided by
    a zero zoom, say -- left a window with x/y/w/h of null that the canvas could no longer place, and the damage
    persisted across reloads. Reject it at the edge instead.
    """
    if not math.isfinite(v):
        raise ValueError("must be a finite number")
    return v


Finite = Annotated[float, AfterValidator(_finite)]
# Zoom divides drag deltas, so zero or negative is both meaningless and the thing that mints the infinities above.
# The renderer clamps the range it actually uses (Canvas.tsx MIN_ZOOM/MAX_ZOOM); this only rules out the impossible.
PositiveFinite = Annotated[float, AfterValidator(_finite), Field(gt=0)]


class CanvasIn(BaseModel):
    name: str = FALLBACK_NAME
    project_id: str | None = None
    copy_from: str | None = None


class CanvasPatch(BaseModel):
    name: str | None = None
    project_id: str | None = None
    position: int | None = None
    snap_mode: str | None = None
    grid_size: Annotated[int, Field(ge=1)] | None = None
    zoom: PositiveFinite | None = None
    pan_x: Finite | None = None
    pan_y: Finite | None = None
    wallpaper: str | None = None
    locked: bool | None = None
    clear_project: bool = False


class WindowIn(BaseModel):
    kind: str
    ref_id: str | None = None
    project_id: str | None = None
    title: str = ""
    x: Finite = 0
    y: Finite = 0
    w: Finite = 520
    h: Finite = 640
    config: dict[str, Any] = {}


class WindowPatch(BaseModel):
    title: str | None = None
    # Re-point the window at another referent (a chat window switching conversations).
    ref_id: str | None = None
    config: dict[str, Any] | None = None
    state: str | None = None
    pinned: bool | None = None
    opacity: Finite | None = None   # window alpha while popped; the store clamps it to [0.2, 1.0]
    x: Finite | None = None
    y: Finite | None = None
    w: Finite | None = None
    h: Finite | None = None
    z: int | None = None
    canvas_id: str | None = None
    restore_bounds: dict[str, Any] | None = None
    popout_bounds: dict[str, Any] | None = None  # PopoutBounds.display is an int; dict[str, float] would round it
    clear_restore_bounds: bool = False
    clear_popout_bounds: bool = False


class WindowLayoutIn(BaseModel):
    id: str
    x: Finite | None = None
    y: Finite | None = None
    w: Finite | None = None
    h: Finite | None = None
    z: int | None = None
    state: str | None = None


class LayoutIn(BaseModel):
    windows: list[WindowLayoutIn] = []


def canvas_router(canvases: Canvases, wsid: Callable[[str | None], str | None], widget_kinds: Callable[[], tuple[str, ...]]) -> APIRouter:
    router = APIRouter()

    @router.get("/canvases")
    def list_canvases() -> list[dict[str, Any]]:
        return canvases.list()

    @router.post("/canvases")
    def create_canvas(body: CanvasIn) -> dict[str, Any]:
        c = canvases.create(body.name, wsid(body.project_id), body.copy_from)
        if not c:
            raise HTTPException(404, "Unknown copy_from canvas")
        return c

    @router.get("/canvases/{id}")
    def get_canvas(id: str) -> dict[str, Any]:
        c = canvases.get(id)
        if not c:
            raise HTTPException(404)
        return c

    @router.put("/canvases/{id}")
    def update_canvas(id: str, body: CanvasPatch) -> dict[str, Any]:
        patch = body.model_dump(exclude_none=True, exclude={"clear_project"})
        if patch.get("snap_mode") not in (None, *SNAP_MODES):
            raise HTTPException(400, f"Unknown snap_mode: {patch['snap_mode']}")
        if body.clear_project:
            patch["project_id"] = None
        elif "project_id" in patch:
            patch["project_id"] = wsid(patch["project_id"])
        c = canvases.update(id, patch)
        if not c:
            raise HTTPException(404)
        return c

    @router.delete("/canvases/{id}")
    def delete_canvas(id: str) -> dict[str, bool]:
        canvases.delete(id)
        return {"ok": True}

    @router.post("/canvases/{id}/windows")
    def add_canvas_window(id: str, body: WindowIn) -> dict[str, Any]:
        if body.kind not in widget_kinds():
            raise HTTPException(400, f"Unknown widget kind: {body.kind}")
        w = canvases.add_window(id, body.kind, body.ref_id, wsid(body.project_id), body.title, body.x, body.y, body.w, body.h, body.config)
        if not w:
            raise HTTPException(404)
        return w

    @router.put("/canvases/{id}/layout")
    def put_canvas_layout(id: str, body: LayoutIn) -> dict[str, Any]:
        for e in body.windows:
            if e.state is not None and e.state not in WINDOW_STATES:
                raise HTTPException(400, f"Unknown window state: {e.state}")
        return {"ok": True, "updated": canvases.set_layout(id, [e.model_dump() for e in body.windows])}

    @router.get("/windows/{wid}")
    def get_canvas_window(wid: str) -> dict[str, Any]:
        w = canvases.window(wid)
        if not w:
            raise HTTPException(404)
        return w

    @router.put("/windows/{wid}")
    def update_canvas_window(wid: str, body: WindowPatch) -> dict[str, Any]:
        patch = body.model_dump(exclude_none=True, exclude={"clear_restore_bounds", "clear_popout_bounds"})
        if patch.get("state") not in (None, *WINDOW_STATES):
            raise HTTPException(400, f"Unknown window state: {patch['state']}")
        if body.clear_restore_bounds:
            patch["restore_bounds"] = None
        if body.clear_popout_bounds:
            patch["popout_bounds"] = None
        w = canvases.update_window(wid, patch)
        if not w:
            raise HTTPException(404)
        return w

    @router.post("/windows/{wid}/raise")
    def raise_canvas_window(wid: str) -> dict[str, Any]:
        w = canvases.raise_window(wid)
        if not w:
            raise HTTPException(404)
        return w

    @router.delete("/windows/{wid}")
    def delete_canvas_window(wid: str) -> dict[str, bool]:
        canvases.delete_window(wid)
        return {"ok": True}
    return router


def activate(api: ExtensionScope) -> None:
    canvases = Canvases(api.db)

    def widget_kinds() -> tuple[str, ...]:
        return CORE_WIDGET_KINDS + api.app.widget_kinds()

    async def drop_unknown_kinds(_: Any) -> None:
        canvases.drop_unknown_kinds(widget_kinds())

    api.provide("canvases", canvases)
    api.router(canvas_router(canvases, api.app.wsid, widget_kinds))
    tools.register(api, canvases, widget_kinds)
    api.on("app.start", drop_unknown_kinds)
