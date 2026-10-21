"""The effective configuration route (design doc 10.8)."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from .repos import Conversations, Projects


def router(projects: Projects, convos: Conversations, effective: Callable[[str | None, str | None], dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    @r.get("/config/effective")
    def effective_config(project_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        if project_id and projects.get(project_id) is None:
            raise HTTPException(404, "no such project")
        if conversation_id and convos.get(conversation_id, with_messages=False) is None:
            raise HTTPException(404, "no such chat")
        return effective(project_id, conversation_id)

    return r
