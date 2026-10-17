"""The recipe tools (group `shell`): list, show, save, run and read the status of coding recipes (recipes.py)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ... import shell
from ...tools import ToolSpec, _obj, tool_error
from .recipes import FIELDS, RecipeError
from .sessions import CodingError, claude_binary, opencode, pi

if TYPE_CHECKING:
    from .extension import Coding

PREVIEW_PROMPT = 300  # characters of the prompt the approval card shows


def run_card(resolved: dict[str, Any]) -> list[str]:
    """The lines an approval shows for a run: what it will do, with which settings, and what proves it worked."""
    start, verify = resolved["start"], resolved["verify"]
    where = f"{start['repo_path']} -> new worktree {resolved['worktree']} (branch {start['branch']})" if start["new_worktree"] \
        else f"in the existing worktree {start['repo_path']}"
    opts = start["cli_opts"] or {}
    return [f"{resolved['recipe']} v{resolved['version']} with {resolved['driver']}" + (f" ({start['model']})" if start["model"] else ""),
            f"Works {where}",
            f"Task: {start['prompt'][:PREVIEW_PROMPT]}" + ("..." if len(start["prompt"]) > PREVIEW_PROMPT else ""),
            f"Permission mode: {start['permission_mode'] or 'follows Grain'}"
            + (f"; allowed tools: {', '.join(opts['allowed_tools'])}" if opts.get("allowed_tools") else "")
            + (f"; denied tools: {', '.join(opts['denied_tools'])}" if opts.get("denied_tools") else ""),
            f"Limits: {opts.get('max_steps') or 'no'} tool-call steps, {resolved['timeout_s'] or 'no'} seconds",
            f"Done only when `{verify['command']}` exits 0 (in {verify['cwd'] or resolved['worktree']}, up to {verify['timeout_s']} s); "
            f"{resolved['max_retries']} retry"]


def tools(coding: Coding) -> list[ToolSpec]:
    def available() -> bool:
        return claude_binary() is not None or opencode.binary() is not None or pi.binary() is not None

    def unattended(name: str) -> dict[str, Any]:
        return tool_error(f"{name} starts a coding agent on this Mac and this is an unattended background run, so it cannot run here.",
                          alternative="describe the task in your report for the user")

    async def coding_recipe_list(ctx: dict[str, Any]) -> Any:
        return {"recipes": await asyncio.to_thread(coding.recipes.list)}

    async def coding_recipe_show(ctx: dict[str, Any], recipe: str, version: int | None = None) -> Any:
        found = coding.recipes.find(recipe)
        if not found:
            return tool_error(f"No recipe '{recipe}'.", field="recipe", alternative="coding_recipe_list shows the names")
        try:
            v = coding.recipes.version(found["id"], version)
        except RecipeError as e:
            return tool_error(str(e), field="version")
        return {"id": found["id"], "name": found["name"], "version": v["version"], "approved": v["approved_at"] is not None,
                "created_by": v["created_by"], "recipe": v["body"], "versions": coding.recipes.versions(found["id"])}

    async def coding_recipe_save(ctx: dict[str, Any], **fields: Any) -> Any:
        try:
            out = await asyncio.to_thread(coding.recipes.save, fields, "agent")
        except RecipeError as e:
            return tool_error(str(e))
        return {**out, "note": "Saved as a new version." + ("" if out["approved"] else " It cannot run until the user approves it in "
                "Settings > Advanced > Coding recipes: tell them.")}

    async def coding_recipe_run(ctx: dict[str, Any], recipe: str, inputs: dict[str, Any] | None = None, version: int | None = None) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_recipe_run")
        try:
            out = await coding.recipes.run(recipe, inputs, "chat", ctx, version=version)
        except (RecipeError, CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)), alternative=getattr(e, "alt", None))
        shell.taint(ctx, f"coding_recipe:{out['run_id']}")  # the agent's text comes back through the run's status
        return {**out, "note": "Started. It works in the background; coding_recipe_status(run_id) reads the outcome, and you are told "
                "when it ends. It is done only when its verify command passes, whatever the agent says."}

    async def coding_recipe_status(ctx: dict[str, Any], run_id: str) -> Any:
        run = coding.recipes.get_run(str(run_id))
        if run is None:
            return tool_error(f"No recipe run '{run_id}'.", field="run_id")
        shell.taint(ctx, f"coding_recipe:{run['id']}")  # the verify output and the agent's detail are program text
        return {k: run[k] for k in ("id", "recipe_id", "recipe_version", "origin", "status", "attempt", "attempts", "session_id",
                                    "verify_exit", "verify_tail", "created_at", "started_at", "ended_at")}

    name = {"recipe": {"type": "string", "description": "The recipe's name or id"}}
    save_props: dict[str, Any] = {
        "name": {"type": "string"}, "description": {"type": "string"},
        "prompt": {"type": "string", "description": "The task; {{input}} marks where an input goes"},
        "inputs": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": ["string", "number", "boolean", "enum"]},
            "required": {"type": "boolean"}, "default": {}, "max_len": {"type": "integer"}, "values": {"type": "array", "items": {"type": "string"}}},
            "required": ["name"]}},
        "repo_path": {"type": "string", "description": "Absolute path of a git repo on this Mac"},
        "worktree": {"type": "string", "enum": ["new", "existing"], "description": "A fresh branch and worktree per run (default) or an existing one"},
        "worktree_path": {"type": "string", "description": "With worktree=existing: the worktree's path"},
        "branch_prefix": {"type": "string", "description": "Prefix of the new branch (default grain/)"},
        "driver": {"type": "string", "enum": ["claude", "opencode", "pi"], "description": "Omit for the user's default"},
        "model": {"type": "string"},
        "permission_mode": {"type": "string", "enum": ["acceptEdits", "auto", "dontAsk"]},
        "allowed_tools": {"type": "array", "items": {"type": "string"}, "description": "Claude Code tool patterns, driver claude only"},
        "denied_tools": {"type": "array", "items": {"type": "string"}, "description": "Claude Code tool patterns, driver claude only"},
        "max_steps": {"type": "integer", "description": "Cap on the agent's tool calls (1 to 500, default 60), driver claude only"},
        "timeout_s": {"type": "integer", "description": "Wall clock for the whole run, up to 7200"},
        "verify": {"type": "object", "properties": {"command": {"type": "string"}, "timeout_s": {"type": "integer"}, "cwd": {"type": "string"}},
                   "required": ["command"], "description": "The check that proves the work; the run is done only when it exits 0"},
        "max_retries": {"type": "integer", "enum": [0, 1], "description": "Tell the agent what verify said and let it try once more"}}
    assert set(save_props) == set(FIELDS)

    built = [
        ToolSpec("coding_recipe_list", "List the saved coding recipes: a recipe is a repeatable coding task (prompt, repo, settings) that is "
                 "done only when its verify command exits 0. Each has its inputs and whether its current version is approved.",
                 _obj({}, []), coding_recipe_list, "shell", "safe"),
        ToolSpec("coding_recipe_show", "One recipe in full (a version, default the current one) and its versions with who saved each and "
                 "whether it is approved.", _obj({**name, "version": {"type": "integer"}}, ["recipe"]), coding_recipe_show, "shell", "safe"),
        ToolSpec("coding_recipe_save", "Save a coding recipe, or a new version of the one with that name (versions never change). A version you "
                 "save cannot run until the user approves it, because a recipe carries permission settings. Needs a verify command that proves "
                 "the work, such as the test suite; inputs are filled into the prompt, never into a command.",
                 _obj(save_props, ["name", "prompt", "repo_path", "verify"]), coding_recipe_save, "shell", "writes"),
        ToolSpec("coding_recipe_run", "Run an approved coding recipe now: it starts a coding session with the recipe's settings, then runs its "
                 "verify command; the run passes only if that exits 0, and may retry once if the recipe allows. Returns the run id and the "
                 "session id; coding_session_wait follows the session and coding_recipe_status the outcome.",
                 _obj({**name, "inputs": {"type": "object", "description": "Values for the recipe's inputs"},
                       "version": {"type": "integer", "description": "Default the current version"}}, ["recipe"]),
                 coding_recipe_run, "shell", "external"),
        ToolSpec("coding_recipe_status", "The outcome of a recipe run: status (working, verifying, passed, failed, agent_failed, timeout, "
                 "stopped, verify_blocked), the attempt, and the verify exit code and output.",
                 _obj({"run_id": {"type": "string"}}, ["run_id"]), coding_recipe_status, "shell", "safe"),
    ]
    by_name = {s.name: s for s in built}
    by_name["coding_recipe_save"].force_ask = lambda _args, ctx: bool(ctx.get("tainted"))  # a recipe is a lasting prompt and permission settings
    run = by_name["coding_recipe_run"]
    run.default = "ask"
    run.force_ask = lambda _args, ctx: bool(ctx.get("tainted"))
    run.preview = lambda args: _preview(coding, args)
    for s in built:
        s.available_fn = available
    return built


def _preview(coding: Coding, args: dict[str, Any]) -> list[str] | None:
    """The card's lines for coding_recipe_run, from the settings the run would use now; None when the arguments would not run."""
    try:
        return run_card(coding.recipes.preview(str(args.get("recipe") or ""), args.get("inputs"), args.get("version")))
    except (RecipeError, CodingError, TypeError):
        return None
