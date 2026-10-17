"""The dev panel's API (Settings > Advanced > Developer): what each extension registered and how its handlers behave,
and enable, disable and reload. Loading code from a folder is off unless the backend was started with GRAIN_EXT_DEV=1."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .hooks import Subscription

if TYPE_CHECKING:
    from ..tools import Toolbox
    from .host import ExtensionContext, ExtensionHost, ExtensionScope


class LoadIn(BaseModel):
    path: str


def _percentile(sorted_values: list[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def _hook(sub: Subscription) -> dict[str, Any]:
    ms = sorted(s * 1000 for s in sub.latencies)
    return {"hook": sub.hook, "calls": sub.calls, "p50Ms": _percentile(ms, 0.5) if ms else None,
            "p95Ms": _percentile(ms, 0.95) if ms else None, "muted": sub.muted, "errors": list(sub.errors)}


def describe(host: ExtensionHost, scope: ExtensionScope) -> dict[str, Any]:
    m = scope.manifest
    return {"id": m.id, "name": m.name, "version": m.version, "enabled": scope.enabled(), "reloadable": scope.dev,
            "folder": str(scope.root) if scope.dev else None, "contributes": m.contributes, "tools": list(scope.tools),
            "permissions": list(m.permissions), "hooks": [_hook(s) for s in host.bus.subscriptions(m.id)]}


def router(host: ExtensionHost, ctx: ExtensionContext, toolbox: Callable[[], Toolbox]) -> APIRouter:
    r = APIRouter(prefix="/dev/extensions")
    live = os.environ.get("GRAIN_EXT_DEV") == "1"

    def known(ext_id: str) -> ExtensionScope:
        if ext_id not in host.extensions:
            raise HTTPException(404, f"no extension {ext_id!r}")
        return host.extensions[ext_id]

    def require_dev() -> None:
        if not live:
            raise HTTPException(403, "loading extension code needs the backend to be started with GRAIN_EXT_DEV=1")

    def set_hidden(ext_id: str, hidden: bool) -> dict[str, Any]:
        scope = known(ext_id)
        views = [v for v in ctx.settings().get("hiddenViews") or () if v != ext_id]
        ctx.set_settings({"hiddenViews": [*views, ext_id] if hidden else views})
        host.refresh_enabled()
        return describe(host, scope)

    @r.get("")
    def listing() -> dict[str, Any]:
        return {"live": live, "extensions": [describe(host, s) for s in host.extensions.values()],
                "failed": [{"path": str(p), "error": e} for p, e in host.failed.items()]}

    @r.post("/{ext_id}/enable")
    def enable(ext_id: str) -> dict[str, Any]:
        return set_hidden(ext_id, False)

    @r.post("/{ext_id}/disable")
    def disable(ext_id: str) -> dict[str, Any]:
        return set_hidden(ext_id, True)

    @r.post("/load")
    async def load(body: LoadIn) -> dict[str, Any]:
        require_dev()
        try:
            scope = await host.load_dev(Path(body.path).expanduser().resolve(), ctx)
        except Exception as e:  # noqa: BLE001 - the reason goes to the panel
            raise HTTPException(400, f"{type(e).__name__}: {e}") from e
        toolbox().set_extension_tools(scope)
        return describe(host, scope)

    @r.post("/{ext_id}/reload")
    async def reload(ext_id: str) -> dict[str, Any]:
        require_dev()
        toolbox().drop_extension_tools(known(ext_id).tools)
        try:
            scope = await host.reload(ext_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"{type(e).__name__}: {e}") from e
        toolbox().set_extension_tools(scope)
        return describe(host, scope)

    return r
