"""The routes of folder-loaded extensions, served under /ext/<id>/ by one dispatcher (design doc section 4).

FastAPI cannot unmount a router, so the app mounts this once and it looks the extension up on every request: a
reload swaps the handlers, and a disabled or unloaded extension answers 404.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.routing import get_route_path
from starlette.types import Receive, Scope, Send

if TYPE_CHECKING:
    from .host import ExtensionHost, ExtensionScope


class ExtensionRoutes:
    def __init__(self, host: ExtensionHost) -> None:
        self._host = host
        self._apps: dict[str, tuple[ExtensionScope, FastAPI]] = {}  # built on first request, rebuilt when the scope is replaced

    def _app(self, ext_id: str) -> FastAPI | None:
        scope = self._host.extensions.get(ext_id)
        if scope is None or not scope.dev or not scope.routers or not scope.enabled():
            return None
        built = self._apps.get(ext_id)
        if built is None or built[0] is not scope:
            app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
            for router in scope.routers:
                app.include_router(router)
            built = self._apps[ext_id] = (scope, app)
        return built[1]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ext_id = get_route_path(scope).strip("/").partition("/")[0]
        app = self._app(ext_id)
        if app is None:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        await app({**scope, "root_path": f"{scope['root_path']}/{ext_id}"}, receive, send)
