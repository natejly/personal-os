"""HTTP surface for artifacts: CRUD, versions, patch edit, whole-document revise, and the sandboxed render route.

The router is built by a factory so it can be tested against a bare Artifacts store, and so app.py only
passes in what it already owns. Everything except `/render` goes through the app's token middleware; the render
route is the one an iframe loads, so it cannot send the token. It is inert (artifacts.RENDER_HEADERS) and also
refuses anything without the signed, expiring, per-artifact token that `sign` puts in every `render_path`.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import artifacts as A
from .artifact_patch import PatchError, apply_patch


class ArtifactIn(BaseModel):
    title: str = ""
    prompt: str = ""
    code: str = ""
    project_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None


class ArtifactPatch(BaseModel):
    title: str | None = None
    project_id: str | None = None
    prompt: str | None = None
    clear_project: bool = False
    # A hand edit of the document itself, saved as a new version.
    code: str | None = None
    instruction: str = ""


class EditIn(BaseModel):
    edits: list[dict[str, Any]]


class ReviseIn(BaseModel):
    instruction: str


class CodeIn(BaseModel):
    code: str


class RestoreIn(BaseModel):
    version: int


def is_render_path(method: str, path: str) -> bool:
    """Exactly GET /artifacts/{id}/render: the one artifact route that is reachable without the app token."""
    parts = path.strip("/").split("/")
    return method == "GET" and len(parts) == 3 and parts[0] == "artifacts" and parts[2] == "render" and bool(parts[1])


def _unsigned(aid: str) -> str:
    return f"/artifacts/{aid}/render"


def make_router(store: A.Artifacts, settings_fn: Callable[[], dict[str, Any]],
                on_delete: Callable[[str], None] | None = None,
                sign: Callable[[str], str] = _unsigned,
                verify: Callable[[str, str, str], bool] | None = None) -> APIRouter:
    """`sign(aid)` is the render path an iframe may load; `verify(aid, rt, re)` checks the token it carries.
    Without `verify` (bare-store tests) the render route takes no token."""
    r = APIRouter()

    def out(a: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {**a, "render_path": sign(a["id"]), "blocked": A.blocked_capabilities(a.get("code") or ""), **extra}

    def need(aid: str) -> dict[str, Any]:
        a = store.get(aid)
        if not a:
            raise HTTPException(404, "No such artifact")
        return a

    def model(cfg: dict[str, Any]) -> str:
        return cfg["defaultModel"]

    async def finish(aid: str, a: dict[str, Any]) -> dict[str, Any]:
        return out(a, lint=A.lint(a["code"]))

    @r.get("/artifacts")
    def list_artifacts(project_id: str | None = None, q: str = "", conversation_id: str | None = None) -> list[dict[str, Any]]:
        pid = "__all__" if project_id in (None, "all") else (project_id or None)
        return [{**a, "render_path": sign(a["id"])} for a in store.list(pid, q, conversation_id=conversation_id)]

    @r.post("/artifacts")
    async def create_artifact(body: ArtifactIn) -> dict[str, Any]:
        code, prompt, report = body.code, body.prompt.strip(), None
        if not code.strip():
            if not prompt:
                raise HTTPException(400, "code or prompt required")
            cfg = settings_fn()
            try:
                raw = await A.generate_artifact_code(cfg, model(cfg), prompt)
                code, report = await A.checked_code(cfg, model(cfg), raw, prompt)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            except Exception as e:  # noqa: BLE001 - a provider failure is a bad gateway, not a server bug
                raise HTTPException(502, f"Generation failed: {e}") from e
        try:
            a = store.create(title=body.title, code=code, prompt=prompt, project_id=body.project_id or None,
                             source="llm" if report is not None else "user",
                             conversation_id=body.conversation_id, message_id=body.message_id)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return out(a, lint=report or A.lint(a["code"]))

    @r.get("/artifacts/{aid}")
    def get_artifact(aid: str) -> dict[str, Any]:
        a = need(aid)
        return out(a, lint=A.lint(a["code"]))

    @r.put("/artifacts/{aid}")
    def update_artifact(aid: str, body: ArtifactPatch) -> dict[str, Any]:
        need(aid)
        store.update(aid, body.model_dump(exclude={"code", "instruction"}))
        if body.code is not None:
            try:
                store.save_version(aid, body.code, instruction=body.instruction, source="user")
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        return out(need(aid))

    @r.delete("/artifacts/{aid}")
    def delete_artifact(aid: str) -> dict[str, bool]:
        store.delete(aid)
        if on_delete:
            on_delete(aid)
        return {"ok": True}

    @r.get("/artifacts/{aid}/versions")
    def versions(aid: str) -> list[dict[str, Any]]:
        need(aid)
        return store.versions(aid)

    @r.get("/artifacts/{aid}/versions/{n}")
    def one_version(aid: str, n: int) -> dict[str, Any]:
        v = store.version(aid, n)
        if not v:
            raise HTTPException(404, "No such version")
        return {**v, "render_path": sign(aid) + ("&" if "?" in sign(aid) else "?") + f"v={n}"}

    @r.post("/artifacts/{aid}/restore/{n}")
    async def restore_n(aid: str, n: int) -> dict[str, Any]:
        return await restore(aid, RestoreIn(version=n))

    @r.post("/artifacts/{aid}/restore")
    async def restore(aid: str, body: RestoreIn) -> dict[str, Any]:
        need(aid)
        a = store.restore(aid, body.version)
        if not a:
            raise HTTPException(404, "No such version")
        return await finish(aid, a)

    @r.post("/artifacts/{aid}/edit")
    async def edit(aid: str, body: EditIn) -> dict[str, Any]:
        a = need(aid)
        try:
            code = apply_patch(a["code"], body.edits)
        except PatchError as e:
            raise HTTPException(422, {"error": e.reason, "edit_index": e.index, "nearby": e.snippet}) from e
        saved = store.save_version(aid, code, instruction="patch", source="llm")
        return await finish(aid, saved)  # type: ignore[arg-type]

    @r.post("/artifacts/{aid}/revise")
    async def revise(aid: str, body: ReviseIn) -> dict[str, Any]:
        a = need(aid)
        cfg = settings_fn()
        try:
            raw = await A.revise_artifact_code(cfg, model(cfg), a["code"], body.instruction, a["prompt"])
            code, report = await A.checked_code(cfg, model(cfg), raw, a["prompt"])
            saved = store.save_version(aid, code, instruction=body.instruction.strip(), source="llm")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Revision failed: {e}") from e
        return {**saved, "lint": report}  # type: ignore[dict-item]

    @r.post("/artifacts/{aid}/code")
    async def set_code(aid: str, body: CodeIn) -> dict[str, Any]:
        need(aid)
        try:
            saved = store.save_version(aid, body.code, instruction="edited by hand", source="user")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return await finish(aid, saved)  # type: ignore[arg-type]

    @r.get("/artifacts/{aid}/render")
    def render(aid: str, rt: str = "", re: str = "", v: int | None = None) -> HTMLResponse:
        # A bad token is refused before the id is looked up, so ids cannot be probed through this route.
        if verify is not None and not verify(aid, rt, re):
            raise HTTPException(401, "Unauthorized")
        a = need(aid)
        code = a["code"]
        if v is not None and v != a["version"]:
            ver = store.version(aid, v)
            if not ver:
                raise HTTPException(404)
            code = ver["code"]
        return HTMLResponse(A.inject_shim(code or A.EMPTY_HTML), headers=A.render_headers())

    return r
