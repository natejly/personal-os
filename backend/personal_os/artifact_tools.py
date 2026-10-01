"""Agent tools for artifacts: create_artifact, edit_artifact, rewrite_artifact.

Registered into the Toolbox by `register`. All three write only to the local artifacts store, so they are
`writes` (on by default, never asks) - the document runs under a CSP that cannot reach the network.

One artifact tool per turn (a hard rule): a second call in the same reply is refused, because a model that
creates and then immediately edits is burning a round on something it should have written correctly once.
The flag lives in the run's tool ctx, which is created fresh for every reply.
"""
from __future__ import annotations

from typing import Any

from . import artifacts as A
from .artifact_patch import PatchError, apply_patch

ARTIFACT_HINT = (
    "## Artifacts\n"
    "Use create_artifact for a standalone tool, page, visualisation or game the user will keep. Prefer edit_artifact "
    "(exact search/replace pairs) for small changes; use rewrite_artifact only when most of it changes. "
    "One artifact tool per turn. Never paste the artifact's HTML into chat; say in a sentence what you made."
)

USED_FLAG = "artifact_tool_used"


def _refuse(ctx: dict[str, Any], tool_error: Any) -> Any:
    if ctx.get(USED_FLAG):
        return tool_error("One artifact tool per turn, and this turn already used one.",
                          alternative="describe the result in your reply; the user can ask for another change next turn")
    return None


def register(box: Any, store: A.Artifacts) -> None:
    from .tools import ToolSpec, _obj, tool_error  # late: tools.py imports this module's register at wiring time

    R = box.specs.__setitem__

    def summary(a: dict[str, Any], note: str = "") -> dict[str, Any]:
        report = A.lint(a["code"])
        out: dict[str, Any] = {"id": a["id"], "title": a["title"], "version": a["version"], "lint": report,
                               "note": note or "Saved. The user can open it in a space from the card under your reply."}
        if report["blocked"]:
            out["warning"] = ("This document uses " + ", ".join(report["blocked"]) +
                              ", which the sandbox blocks. Fix it with edit_artifact next turn if the user asks.")
        return out

    def model_for() -> tuple[dict[str, Any], str]:
        cfg = box.settings()
        return cfg, cfg["defaultModel"]

    async def create_artifact(ctx: dict[str, Any], title: str, prompt: str) -> Any:
        if (r := _refuse(ctx, tool_error)):
            return r
        if not prompt.strip():
            return tool_error("prompt is empty.", field="prompt", expected="what the artifact should do and look like")
        cfg, model = model_for()
        ctx[USED_FLAG] = True
        raw = await A.generate_artifact_code(cfg, model, prompt)
        if not raw:
            return tool_error("The model returned nothing.", alternative="try again with a more specific prompt")
        code, _ = await A.checked_code(cfg, model, raw, prompt)
        a = store.create(title=title, code=code, prompt=prompt, project_id=ctx.get("project_id"))
        return summary(a)
    R("create_artifact", ToolSpec("create_artifact", (
        "Create a standalone, self-contained HTML artifact (a tool, calculator, visualisation, game or page) that the "
        "user can keep, reopen and revise. It runs in a sandbox with no network and no storage. The document is "
        "written by a separate model call from your prompt, so describe behaviour, content and look in the prompt."),
        _obj({"title": {"type": "string", "description": "Short name, 2-4 words"},
              "prompt": {"type": "string", "description": "Everything the artifact should do and show"}}, ["title", "prompt"]),
        create_artifact, "artifacts", "writes",
        examples=[{"title": "Tip splitter", "prompt": "A tip calculator: bill, tip %, number of people, shows each person's share"}]))

    def _get(artifact_id: str) -> dict[str, Any] | None:
        return store.get(artifact_id)

    async def edit_artifact(ctx: dict[str, Any], artifact_id: str, edits: list[dict[str, Any]]) -> Any:
        if (r := _refuse(ctx, tool_error)):
            return r
        a = _get(artifact_id)
        if not a:
            return tool_error(f"No artifact with id '{artifact_id}'.", field="artifact_id", expected="an id returned by create_artifact")
        ctx[USED_FLAG] = True
        try:
            code = apply_patch(a["code"], edits)
        except PatchError as e:
            near = f" Nearest line in the document: {e.snippet!r}." if e.snippet else ""
            return tool_error(f"edit_artifact: {e.reason} (edit {e.index}); nothing was changed.{near}",
                              alternative="quote the current text exactly, or use rewrite_artifact")
        return summary(store.save_version(artifact_id, code, instruction="patch", source="llm"))  # type: ignore[arg-type]
    R("edit_artifact", ToolSpec("edit_artifact", (
        "Change an existing artifact with exact search/replace pairs, applied in order. Each `search` must appear "
        "exactly once in the current document (include surrounding text to make it unique). All-or-nothing: if any "
        "pair fails nothing is saved. Use for bug fixes, renaming, adding or removing a few lines."),
        _obj({"artifact_id": {"type": "string"},
              "edits": {"type": "array", "maxItems": 20, "items": _obj({"search": {"type": "string"}, "replace": {"type": "string"}}, ["search", "replace"])}},
             ["artifact_id", "edits"]),
        edit_artifact, "artifacts", "writes",
        examples=[{"artifact_id": "a1b2c3", "edits": [{"search": "background:#262624", "replace": "background:#1b2a3a"}]}]))

    async def rewrite_artifact(ctx: dict[str, Any], artifact_id: str, instruction: str) -> Any:
        if (r := _refuse(ctx, tool_error)):
            return r
        a = _get(artifact_id)
        if not a:
            return tool_error(f"No artifact with id '{artifact_id}'.", field="artifact_id", expected="an id returned by create_artifact")
        cfg, model = model_for()
        ctx[USED_FLAG] = True
        try:
            raw = await A.revise_artifact_code(cfg, model, a["code"], instruction, a["prompt"])
        except ValueError as e:
            return tool_error(f"rewrite_artifact: {e}", alternative="edit_artifact with a few exact edits")
        code, _ = await A.checked_code(cfg, model, raw, a["prompt"])
        return summary(store.save_version(artifact_id, code, instruction=instruction.strip(), source="llm"))  # type: ignore[arg-type]
    R("rewrite_artifact", ToolSpec("rewrite_artifact", (
        "Rewrite an existing artifact from a plain-language instruction, regenerating the whole document. Slower and "
        "costlier than edit_artifact: use only when most of the document has to change."),
        _obj({"artifact_id": {"type": "string"}, "instruction": {"type": "string"}}, ["artifact_id", "instruction"]),
        rewrite_artifact, "artifacts", "writes",
        examples=[{"artifact_id": "a1b2c3", "instruction": "Turn it into a dark dashboard with two charts"}]))
