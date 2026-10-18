"""Routes for permission presets and the effective configuration (design doc 10.8)."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .presets import Held, PresetError, Presets


class ApplyIn(BaseModel):
    scope: str = "global"
    scope_id: str = ""
    confirm: str | None = None  # the diff_hash of the 409 this call answers


def router(presets: Presets, effective: Callable[[str | None, str | None], dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    def found(preset_id: str) -> dict[str, Any]:
        try:
            return presets.get(preset_id)
        except KeyError:
            raise HTTPException(404, "no such preset") from None

    def refused(e: PresetError) -> HTTPException:
        return HTTPException(422, str(e))

    @r.get("/permissions/presets")
    def list_presets() -> list[dict[str, Any]]:
        return presets.catalog()

    @r.post("/permissions/presets")
    def create_preset(doc: dict[str, Any]) -> dict[str, Any]:
        try:
            return presets.save(doc)
        except PresetError as e:
            raise refused(e) from e

    @r.post("/permissions/presets/import")
    def import_preset(doc: dict[str, Any]) -> dict[str, Any]:
        """Stored as a user preset marked as imported; never applied by being imported."""
        try:
            return presets.save(doc, source="import")
        except PresetError as e:
            raise refused(e) from e

    @r.get("/permissions/presets/{preset_id}")
    def get_preset(preset_id: str) -> dict[str, Any]:
        return found(preset_id)

    @r.put("/permissions/presets/{preset_id}")
    def edit_preset(preset_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        found(preset_id)
        try:
            return presets.save(doc, preset_id=preset_id)
        except PresetError as e:
            raise refused(e) from e

    @r.delete("/permissions/presets/{preset_id}")
    def delete_preset(preset_id: str) -> None:
        found(preset_id)
        try:
            presets.delete(preset_id)
        except PresetError as e:
            raise refused(e) from e

    @r.post("/permissions/presets/{preset_id}/duplicate")
    def duplicate_preset(preset_id: str) -> dict[str, Any]:
        found(preset_id)
        try:
            return presets.duplicate(preset_id)
        except PresetError as e:
            raise refused(e) from e

    @r.get("/permissions/presets/{preset_id}/export")
    def export_preset(preset_id: str) -> dict[str, Any]:
        found(preset_id)
        return presets.export(preset_id)

    @r.post("/permissions/presets/{preset_id}/apply", response_model=None)
    def apply_preset(preset_id: str, body: ApplyIn) -> dict[str, Any] | JSONResponse:
        """Applies at once when nothing loosens; otherwise answers 409 with the diff, and applies on a second call that quotes its diff_hash."""
        found(preset_id)
        try:
            return presets.apply(preset_id, body.scope, body.scope_id, body.confirm)
        except Held as e:
            return JSONResponse(e.diff, status_code=409)
        except KeyError as e:  # the project or chat the scope names
            raise HTTPException(404, e.args[0]) from None
        except PresetError as e:
            raise refused(e) from e

    @r.get("/config/effective")
    def effective_config(project_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        if project_id and presets.projects.get(project_id) is None:
            raise HTTPException(404, "no such project")
        if conversation_id and presets.convos.get(conversation_id, with_messages=False) is None:
            raise HTTPException(404, "no such chat")
        return effective(project_id, conversation_id)

    return r
