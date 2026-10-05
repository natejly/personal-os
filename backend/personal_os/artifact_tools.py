"""artifact_create / artifact_update / artifact_edit / artifact_read / artifact_list: the model's door into artifacts.py.

In-app tier ("writes", default on): an artifact is a page the user owns and can restore any version of, it
renders under artifacts.RENDER_HEADERS with no network at all, and nothing leaves the machine. Kept out of
tools.py so that file only gains a constructor argument and one registration call.

The call leaves a small `ctx["artifact"]` note ({id, title, version, action}) that app._chat_stream pops into
the tool event and a run event, which is how the card finds its artifact again after an app reload.

artifact_edit is the cheap path for small changes: exact search/replace pairs (artifact_patch.py), all-or-nothing,
so a fix does not cost a whole document's worth of output tokens.
"""
from __future__ import annotations

from typing import Any

from . import redact
from .artifact_patch import PatchError, apply_patch
from .artifacts import Artifacts, _clean_html, blocked_capabilities

HTML_NOTE = ("A complete self-contained HTML document: inline CSS and JS only, no network, no external scripts, fonts or images "
             "(embed assets as data: URIs or inline SVG), no localStorage, no form submits, no window.open. Set a short <title>.")


def register(box: Any, store: Artifacts) -> None:
    from .tools import ToolSpec, _obj  # late: tools.py imports this module's caller, so a top-level import would be circular

    R = box.specs.__setitem__

    def _note(ctx: dict[str, Any], a: dict[str, Any], action: str) -> None:
        ctx["artifact"] = {"id": a["id"], "title": a["title"], "version": a["version"], "action": action}

    def _result(a: dict[str, Any], action: str, code: str) -> dict[str, Any]:
        out: dict[str, Any] = {"artifact_id": a["id"], "title": redact.scrub_command_output(str(a["title"] or "")),
                               "version": a["version"], action: True,
                               "note": "Shown to the user in the chat as a live preview; they can open it full size from the card."}
        blocked = blocked_capabilities(code)
        if blocked:
            out["warning"] = (f"The document uses {', '.join(blocked)}, which its Content-Security-Policy blocks, so that part "
                              "will not work. Fix it with artifact_edit, or artifact_update with a version that does without.")
        return out

    async def artifact_create(ctx: dict[str, Any], title: str, html: str, description: str = "") -> Any:
        code = _clean_html(html)
        if not code:
            return {"error": "html is empty", "hint": "pass the whole HTML document"}
        try:
            a = store.create(title=title, code=code, prompt=description or title, project_id=ctx.get("project_id"),
                             conversation_id=ctx.get("conversation_id"), run_id=ctx.get("run_id"), message_id=ctx.get("message_id"))
        except ValueError as e:
            return {"error": str(e)}
        _note(ctx, a, "created")
        return _result(a, "created", code)
    R("artifact_create", ToolSpec(
        "artifact_create",
        "Create an artifact: a self-contained interactive HTML page the user sees live in the chat and keeps in Library -> Made "
        "(a calculator, visualisation, mock-up, game, formatted report, small tool). Use it for anything visual or interactive that "
        "is bigger than a snippet. " + HTML_NOTE,
        _obj({"title": {"type": "string", "description": "Short name, 2-4 words"},
              "html": {"type": "string", "description": "The full HTML document"},
              "description": {"type": "string", "description": "One line: what was asked for"}}, ["title", "html"]),
        artifact_create, "artifacts", "writes"))

    async def artifact_update(ctx: dict[str, Any], artifact_id: str, html: str, instruction: str = "") -> Any:
        a = store.get(artifact_id)
        if not a:
            return {"error": f"No artifact {artifact_id}", "hint": "use the artifact_id artifact_create returned, or artifact_list"}
        code = _clean_html(html)
        if not code:
            return {"error": "html is empty", "hint": "pass the whole revised HTML document, not a diff"}
        try:
            a = store.save_version(artifact_id, code, instruction=instruction, source="llm")
        except ValueError as e:
            return {"error": str(e)}
        _note(ctx, a, "updated")  # type: ignore[arg-type]
        return _result(a, "updated", code)  # type: ignore[arg-type]
    R("artifact_update", ToolSpec(
        "artifact_update",
        "Revise an artifact by writing its full new HTML. This appends a new version (the old ones stay and the user can restore them), "
        "so send the whole document, never a diff. Read it first with artifact_read if you do not already have its source. " + HTML_NOTE,
        _obj({"artifact_id": {"type": "string"}, "html": {"type": "string", "description": "The full revised HTML document"},
              "instruction": {"type": "string", "description": "One line: what changed"}}, ["artifact_id", "html"]),
        artifact_update, "artifacts", "writes"))

    async def artifact_edit(ctx: dict[str, Any], artifact_id: str, edits: list[dict[str, Any]]) -> Any:
        a = store.get(artifact_id)
        if not a:
            return {"error": f"No artifact {artifact_id}", "hint": "use the artifact_id artifact_create returned, or artifact_list"}
        try:
            code = apply_patch(a["code"], edits)
        except PatchError as e:
            near = f" Nearest line in the document: {redact.scrub_command_output(e.snippet)!r}." if e.snippet else ""
            return {"error": f"artifact_edit: {e.reason} (edit {e.index}); nothing was changed.{near}",
                    "hint": "quote the current text exactly (artifact_read shows it), or use artifact_update"}
        try:
            a = store.save_version(artifact_id, code, instruction="patch", source="llm")
        except ValueError as e:
            return {"error": str(e)}
        _note(ctx, a, "updated")  # type: ignore[arg-type]
        return _result(a, "updated", code)  # type: ignore[arg-type]
    R("artifact_edit", ToolSpec(
        "artifact_edit",
        "Change an existing artifact with exact search/replace pairs, applied in order. Each `search` must appear exactly once "
        "in the current document (include surrounding text to make it unique). All-or-nothing: if any pair fails nothing is "
        "saved. Use for bug fixes, renaming, adding or removing a few lines; a new version is appended as with artifact_update.",
        _obj({"artifact_id": {"type": "string"},
              "edits": {"type": "array", "maxItems": 20,
                        "items": _obj({"search": {"type": "string"}, "replace": {"type": "string"}}, ["search", "replace"])}},
             ["artifact_id", "edits"]),
        artifact_edit, "artifacts", "writes",
        examples=[{"artifact_id": "a1b2c3", "edits": [{"search": "background:#262624", "replace": "background:#1b2a3a"}]}]))

    async def artifact_read(ctx: dict[str, Any], artifact_id: str, version: int | None = None) -> Any:
        a = store.get(artifact_id)
        if not a:
            return {"error": f"No artifact {artifact_id}",
                    "artifacts": [{"artifact_id": x["id"], "title": redact.scrub_command_output(str(x["title"] or ""))}
                                  for x in store.list()[:10]]}
        if version is not None:
            v = store.version(artifact_id, int(version))
            if not v:
                return {"error": f"No version {version}", "versions": [x["version"] for x in store.versions(artifact_id)]}
            return {"artifact_id": a["id"], "title": redact.scrub_command_output(str(a["title"] or "")),
                    "version": v["version"], "latest": a["version"],
                    "html": redact.scrub_command_output(str(v["code"] or ""))}
        return {"artifact_id": a["id"], "title": redact.scrub_command_output(str(a["title"] or "")),
                "version": a["version"], "latest": a["version"],
                "html": redact.scrub_command_output(str(a["code"] or ""))}
    R("artifact_read", ToolSpec(
        "artifact_read", "Read an artifact's current (or a given version's) HTML source, e.g. before revising it with artifact_update.",
        _obj({"artifact_id": {"type": "string"}, "version": {"type": "integer"}}, ["artifact_id"]), artifact_read, "artifacts"))

    async def artifact_list(ctx: dict[str, Any], query: str = "") -> Any:
        return [{"artifact_id": a["id"], "title": redact.scrub_command_output(str(a["title"] or "")), "version": a["version"]}
                for a in store.list(q=query)[:30]]
    R("artifact_list", ToolSpec("artifact_list", "List the user's artifacts (newest first) to find an artifact_id.",
        _obj({"query": {"type": "string"}}, []), artifact_list, "artifacts"))
