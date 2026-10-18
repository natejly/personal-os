"""Routes for profile extensions (design doc section 6.1): list them, turn one on or off.

Turning one on applies its contributes.defaults. A looser permission preset is not applied until the request says
the user clicked for it (`confirm`): without it the response lists what is held and nothing changes.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .host import ExtensionHost, ExtensionScope


class Enable(BaseModel):
    confirm: bool = False


def router(host: ExtensionHost, changed: Callable[[dict[str, Any]], None]) -> APIRouter:
    """`changed(before)` reports the settings a toggle wrote, given the settings as they were."""
    r = APIRouter()

    def profile(profile_id: str) -> ExtensionScope:
        scope = host.extensions.get(profile_id)
        if scope is None or scope.manifest.kind != "profile":
            raise HTTPException(404, "no such profile")
        return scope

    @r.get("/profiles")
    def list_profiles() -> list[dict[str, Any]]:
        return [{"id": m.id, "name": m.name, "description": m.description, "enabled": s.enabled(), "theme": m.contributes.get("theme", {}),
                 "layout": m.contributes.get("layout", {})}
                for s in host.extensions.values() if (m := s.manifest).kind == "profile"]

    @r.post("/profiles/{profile_id}/enable")
    def enable(profile_id: str, body: Enable | None = None) -> dict[str, Any]:
        scope, confirm = profile(profile_id), bool(body and body.confirm)
        if scope.enabled():
            return {"held": []}
        if (held := [c for c in scope.defaults.plan(scope.manifest) if c.looser]) and not confirm:
            return {"held": [c.to_dict() for c in held]}
        before = scope.defaults.db.get_settings()
        scope.defaults.apply(scope.manifest, confirmed=confirm)
        changed(before)
        host.refresh_enabled()
        return {"held": []}

    @r.post("/profiles/{profile_id}/disable")
    def disable(profile_id: str) -> None:
        scope = profile(profile_id)
        before = scope.defaults.db.get_settings()
        scope.defaults.revert(profile_id)
        changed(before)
        host.refresh_enabled()

    return r
