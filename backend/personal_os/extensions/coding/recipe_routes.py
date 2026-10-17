"""The recipe routes the settings panel and the palette use. Saving here is the user's, so the version is approved as saved;
running here is the user's confirmation of the settings the preview showed."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import shell
from .recipe_tools import run_card
from .recipes import RecipeError
from .sessions import CodingError

if TYPE_CHECKING:
    from .extension import Coding


class RunIn(BaseModel):
    inputs: dict[str, Any] = {}
    version: int | None = None


def recipes_router(coding: Coding) -> APIRouter:
    r = APIRouter(prefix="/coding")

    async def guarded(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except RecipeError as e:
            raise HTTPException(400, str(e)) from e
        except (CodingError, shell.ShellError) as e:
            raise HTTPException(409, shell._scrub(str(e))) from e

    def known(ref: str) -> dict[str, Any]:
        found = coding.recipes.find(ref)
        if not found:
            raise HTTPException(404, "No such recipe")
        return found

    @r.get("/recipes")
    async def list_recipes() -> dict[str, Any]:
        return {"recipes": await asyncio.to_thread(coding.recipes.list)}

    @r.get("/recipes/{ref}")
    async def get_recipe(ref: str) -> dict[str, Any]:
        found = known(ref)
        return {**found, "versions": coding.recipes.versions(found["id"]), "current": coding.recipes.version(found["id"])}

    @r.post("/recipes")
    async def save_recipe(body: dict[str, Any]) -> dict[str, Any]:
        return await guarded(asyncio.to_thread, coding.recipes.save, body, "user")

    @r.post("/recipes/{ref}/versions/{version}/approve")
    async def approve_version(ref: str, version: int) -> dict[str, bool]:
        return {"approved": coding.recipes.approve(known(ref)["id"], version)}

    @r.delete("/recipes/{ref}")
    async def archive_recipe(ref: str) -> dict[str, bool]:
        return {"archived": coding.recipes.archive(known(ref)["id"])}

    @r.post("/recipes/{ref}/preview")
    async def preview_run(ref: str, body: RunIn) -> dict[str, Any]:
        """What a run would use, for the form that confirms it."""
        resolved = await guarded(asyncio.to_thread, coding.recipes.preview, ref, body.inputs, body.version)
        return {"resolved": resolved, "lines": run_card(resolved)}

    @r.post("/recipes/{ref}/run")
    async def run_recipe(ref: str, body: RunIn) -> dict[str, Any]:
        ctx = {"conversation_id": None, "run_id": None, "settings": coding.tb.settings(), "tainted": False, "taint_sources": []}
        return await guarded(coding.recipes.run, ref, body.inputs, "palette", ctx, version=body.version)

    @r.get("/recipe-runs")
    async def list_runs(recipe_id: str | None = None, limit: int = 30) -> dict[str, Any]:
        return {"runs": await asyncio.to_thread(coding.recipes.list_runs, recipe_id, limit)}

    @r.get("/recipe-runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = coding.recipes.get_run(run_id)
        if run is None:
            raise HTTPException(404, "No such recipe run")
        return run

    return r
