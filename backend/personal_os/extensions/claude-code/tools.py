"""The claude_code_* tools (group `shell`), over the ClaudeCode manager.

Starting, steering and fleets are external (they run an agent on this Mac as the user, outside the OS sandbox), so a chat asks
first and an unattended run only proposes them. Reading a session (status, result, wait) is safe but taints the run: what the
agent wrote comes back through it.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ... import shell
from ...tools import ToolSpec, _obj, tool_error
from ..coding.sessions import CodingError
from . import driver, store
from .manager import APPROVAL_TOOL, MAX_FLEET, PERMISSION_MODES, ClaudeCode, grain_mode

if TYPE_CHECKING:
    from .manager import ClaudeCode as CC

TASK_PROPS = {
    "repo_path": {"type": "string", "description": "Absolute path of a git repo on this Mac"},
    "prompt": {"type": "string", "description": "The complete task: the agent does not see this conversation"},
    "name": {"type": "string", "description": "A short label"},
    "model": {"type": "string", "description": "Model id; default the user's setting or the CLI default"},
    "max_turns": {"type": "integer", "description": "Stop after this many turns (0 or omitted: the setting)"},
    "budget_usd": {"type": "number", "description": "Stop when this much has been spent (0 or omitted: the setting)"},
    "permission_mode": {"type": "string", "enum": list(PERMISSION_MODES), "description": "How the agent asks for permission; default follows the setting"},
    "allowed_tools": {"type": "array", "items": {"type": "string"}, "description": "Tools it may use without asking, e.g. Read, Bash(git log:*)"},
    "json_schema": {"type": "object", "description": "Make the final answer a value that fits this JSON Schema (result.structured_output)"},
    "new_worktree": {"type": "boolean", "description": "Work in a fresh git worktree on its own branch (default true)"},
    "branch": {"type": "string", "description": "Branch for the new worktree (default grain/<name>); never main/master"},
}
TAINT_VERBS = ("run", "fleet", "status", "result", "wait", "stop", "send")


def brief(cc: CC, row: dict[str, Any]) -> dict[str, Any]:
    """A session in a tool result: small, and with the agent's last words capped."""
    v = cc.view(row)
    out = {k: v[k] for k in ("id", "name", "status", "branch", "worktree", "cost_usd", "num_turns", "result_subtype", "fleet_id")}
    out["last_text"] = v["last_text"][:300]
    if v["pending_approval"]:
        out["pending_approval"] = v["pending_approval"]
    if v["detail"]:
        out["detail"] = v["detail"]
    return out


def own_taint(sid: str, sources: Any) -> bool:
    """True when the run's taint came only from reading this session (so steering it is not driven by other untrusted text)."""
    srcs = [str(x) for x in sources or []]
    return bool(srcs) and all(
        (m := re.fullmatch(r"claude_code:(?:%s):([\w,-]*)" % "|".join(TAINT_VERBS), x)) is not None
        and all(i == sid for i in m.group(1).split(",") if i) for x in srcs)


def tools(cc: CC) -> list[ToolSpec]:
    def gate(ctx: dict[str, Any], name: str, acts: bool = True) -> dict[str, Any] | None:
        if not cc.coding_enabled():
            return tool_error("Claude Code runs through the Coding extension, which is turned off.",
                              alternative="ask the user to turn Coding on in Settings > Extensions")
        if acts and ctx.get("proposal_only"):
            return tool_error(f"{name} starts or steers a Claude Code agent on this Mac and this is an unattended background run, so it "
                              "cannot run here.", alternative="describe the task in your report for the user")
        return None

    def mark(ctx: dict[str, Any], verb: str, ids: list[str]) -> None:
        shell.taint(ctx, f"claude_code:{verb}:{','.join(ids)}")   # the agent's text comes back through the session

    def err(e: Exception) -> dict[str, Any]:
        return tool_error(shell._scrub(str(e)), alternative=getattr(e, "alt", None))

    def one(ctx: dict[str, Any], sid: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        try:
            row = cc.known(str(sid or ""))
        except CodingError:
            return None, tool_error(f"No Claude Code session '{sid}'.", field="id", alternative="claude_code_status shows the ids")
        cc.rebind(row["id"], ctx)
        return row, None

    async def claude_code_run(ctx: dict[str, Any], repo_path: str, prompt: str, name: str | None = None, model: str | None = None,
                              max_turns: int | None = None, budget_usd: float | None = None, permission_mode: str | None = None,
                              allowed_tools: list[str] | None = None, json_schema: dict[str, Any] | None = None,
                              new_worktree: bool = True, branch: str | None = None) -> Any:
        if bad := gate(ctx, "claude_code_run"):
            return bad
        spec = {"repo_path": repo_path, "prompt": prompt, "name": name, "model": model, "permission_mode": permission_mode,
                "allowed_tools": allowed_tools, "json_schema": json_schema, "new_worktree": new_worktree, "branch": branch}
        spec.update({k: v for k, v in (("max_turns", max_turns), ("budget_usd", budget_usd)) if v is not None})   # unset: the setting
        try:
            row = await cc.start(spec, ctx)
        except (CodingError, shell.ShellError) as e:
            return err(e)
        mark(ctx, "run", [row["id"]])
        if row["status"] == "failed":
            return tool_error(row.get("detail") or "The session did not start.", alternative="fix the cause named, or do the task another way")
        note = ("Started. It works in the background; claude_code_wait(ids) blocks until it settles and claude_code_result(id) has what it "
                "did. Stay in this reply with claude_code_wait if the session may ask for permission: its questions are cards in this chat.")
        return {**brief(cc, row), "note": note}

    async def claude_code_fleet(ctx: dict[str, Any], tasks: list[dict[str, Any]], max_parallel: int | None = None) -> Any:
        if bad := gate(ctx, "claude_code_fleet"):
            return bad
        try:
            fid, rows = await cc.start_fleet(tasks, ctx, max_parallel)
        except (CodingError, shell.ShellError) as e:
            return err(e)
        mark(ctx, "fleet", [r["id"] for r in rows])
        queued = sum(1 for r in rows if r["status"] == "queued")
        return {"fleet_id": fid, "sessions": [brief(cc, r) for r in rows],
                "note": (f"{queued} task(s) are queued and start as running ones settle. " if queued else "")
                        + "claude_code_wait(ids, mode='all') blocks until they settle; claude_code_result(id) has each outcome."}

    async def claude_code_status(ctx: dict[str, Any], id: str | None = None, events: int = 0) -> Any:
        if bad := gate(ctx, "claude_code_status", False):
            return bad
        if not id:
            rows = cc.sessions(20)
            mark(ctx, "status", [r["id"] for r in rows])
            return {"sessions": [brief(cc, r) for r in store.listing(cc.db, 20)], "fleets": cc.fleets(5)}
        row, bad = one(ctx, id)
        if bad:
            return bad
        mark(ctx, "status", [row["id"]])
        out = cc.view(row)
        out["last_text"] = out["last_text"][:1000]
        if events:
            out["events"] = store.events_tail(cc.db, row["id"], max(1, min(int(events), 100)))
        return out

    async def claude_code_send(ctx: dict[str, Any], id: str, message: str) -> Any:
        if bad := gate(ctx, "claude_code_send"):
            return bad
        try:
            row = await cc.send(str(id), message, ctx)
        except (CodingError, shell.ShellError) as e:
            return err(e)
        mark(ctx, "send", [row["id"]])
        return brief(cc, row)

    async def claude_code_stop(ctx: dict[str, Any], id: str) -> Any:
        if bad := gate(ctx, "claude_code_stop"):
            return bad
        try:
            row = await cc.stop(str(id))
        except (CodingError, shell.ShellError) as e:
            return err(e)
        mark(ctx, "stop", [row["id"]])
        return brief(cc, row)

    async def claude_code_result(ctx: dict[str, Any], id: str, full_diff: bool = False) -> Any:
        if bad := gate(ctx, "claude_code_result", False):
            return bad
        row, bad = one(ctx, id)
        if bad:
            return bad
        mark(ctx, "result", [row["id"]])
        res = row.get("result") or {}
        out = {**brief(cc, row), "structured_output": res.get("structured_output"), "final_text": (res.get("text") or row.get("last_text") or "")[:4000],
               "permission_denials": res.get("permission_denials") or [], "errors": res.get("errors") or []}
        _, by_model = store.cost_rollup([row])
        out["by_model"] = by_model
        if row["status"] in ("queued", *store.LIVE):
            out["note"] = "Still working: these are the latest figures. claude_code_wait(ids) blocks until it settles."
        if row.get("fleet_id"):
            out["fleet_cost"] = cc.fleet_cost(row["fleet_id"])
        try:
            out["diff"] = await cc.coding.sessions.diff_row(row, bool(full_diff))
        except (CodingError, shell.ShellError, OSError) as e:
            out["diff_error"] = shell._scrub(str(e))[:200]
        return out

    async def claude_code_wait(ctx: dict[str, Any], ids: list[str], mode: str = "all", timeout: int = 120) -> Any:
        if bad := gate(ctx, "claude_code_wait", False):
            return bad
        try:
            out = await cc.wait(ids if ids is not None else [], "any" if mode == "any" else "all", timeout)
        except CodingError as e:
            return tool_error(shell._scrub(str(e)), field="ids")
        for r in out["sessions"]:
            cc.rebind(r["id"], ctx)
        mark(ctx, "wait", [r["id"] for r in out["sessions"]])
        return {"sessions": [brief(cc, r) for r in out["sessions"]], "settled": out["settled"], "timed_out": out["timed_out"]}

    def bypass(args: dict[str, Any], ctx: dict[str, Any]) -> bool:
        """An explicit bypassPermissions that Allow everything did not itself choose: a hard card, no grant lifts it."""
        modes = [args.get("permission_mode"), *(t.get("permission_mode") for t in args.get("tasks") or [] if isinstance(t, dict))]
        return "bypassPermissions" in modes and grain_mode(ctx, cc.api.app.settings()) != "allow_all"

    sid = {"id": {"type": "string"}}
    run = ToolSpec(
        "claude_code_run",
        "Run Claude Code headless on a repo, in its own git worktree under the user's worktree folder, and let it work in the background. "
        "Returns at once with a session id. It runs on the user's own Claude account outside the OS sandbox. Whatever it would ask "
        "permission for (edits, commands) becomes an approval card in THIS chat while this reply runs: use claude_code_wait to stay in the "
        "reply, or pick permission_mode 'acceptEdits' for edit-only tasks. Give a complete task; it does not see this conversation. "
        "max_turns and budget_usd bound it; json_schema makes its final answer a structured value (see claude_code_result). Follow with "
        "claude_code_wait, then claude_code_result for the outcome and the diff. For several tasks use claude_code_fleet.",
        _obj(TASK_PROPS, ["repo_path", "prompt"]), claude_code_run, "shell", "external",
        examples=[{"repo_path": "/home/me/code/app", "prompt": "Add a --dry-run flag to cli.py and cover it in tests", "max_turns": 30}])
    fleet = ToolSpec(
        "claude_code_fleet",
        f"Start several Claude Code sessions (at most {MAX_FLEET}) as one fleet, each in its own worktree. At most max_parallel run at once "
        "(and never more than the Claude Code and Coding limits); the rest are queued and start as running ones settle. Returns the "
        "fleet id and each session. Wait with claude_code_wait(ids), then read each with claude_code_result. Fleet cost is the sum of "
        "each session's latest result.",
        _obj({"tasks": {"type": "array", "items": _obj(TASK_PROPS, ["repo_path", "prompt"]), "description": "One object per session"},
              "max_parallel": {"type": "integer", "description": "How many may run at once (default: the limits allow)"}}, ["tasks"]),
        claude_code_fleet, "shell", "external",
        examples=[{"tasks": [{"repo_path": "/home/me/code/app", "prompt": "Fix the flaky test in tests/test_io.py"},
                             {"repo_path": "/home/me/code/lib", "prompt": "Bump the dependency and fix what breaks"}], "max_parallel": 2}])
    send = ToolSpec(
        "claude_code_send", "Send a message to a running or finished Claude Code session over its open input, to steer it or add a task; it keeps "
        "its worktree and context. Errors if the session's process has exited (it closes a few minutes after its last result).",
        _obj({**sid, "message": {"type": "string"}}, ["id", "message"]), claude_code_send, "shell", "external")
    stop = ToolSpec("claude_code_stop", "Stop a Claude Code session (interrupt, then kill if it does not exit). Its worktree and branch stay.",
                    _obj(sid, ["id"]), claude_code_stop, "shell", "executes")
    status = ToolSpec("claude_code_status", "Status of one Claude Code session (events=N adds its last N events), or without id a list of recent sessions and fleets.",
                      _obj({"id": {"type": "string"}, "events": {"type": "integer", "default": 0}}, []), claude_code_status, "shell", "safe")
    result = ToolSpec(
        "claude_code_result", "The outcome of a Claude Code session: result subtype, structured_output (with json_schema), final text, cost and per-model "
        "cost, turns, permission denials, and the diff of its worktree (git status, stat, new files, commits; full_diff=true adds the patch).",
        _obj({**sid, "full_diff": {"type": "boolean", "default": False}}, ["id"]), claude_code_result, "shell", "safe")
    wait = ToolSpec(
        "claude_code_wait", "Wait (without polling) until the named Claude Code sessions have settled (done, failed or stopped): mode 'all' (default) or "
        "'any'; timeout seconds (default 120, max 600). Returns each session's state, or timed_out=true: call again to keep waiting.",
        _obj({"ids": {"type": "array", "items": {"type": "string"}}, "mode": {"type": "string", "enum": ["all", "any"], "default": "all"},
              "timeout": {"type": "integer", "default": 120}}, ["ids"]), claude_code_wait, "shell", "safe")
    for s in (run, fleet):
        s.default = "ask"
        s.force_card = bypass
        s.force_ask = lambda args, ctx: bool(ctx.get("tainted")) or bypass(args, ctx)
    send.default = "ask"
    send.taint_ok = lambda args, ctx: (not ctx.get("taint_unsourced") and (r := cc.row(str(args.get("id") or ""))) is not None
                                       and own_taint(r["id"], ctx.get("taint_sources")))
    send.force_ask = lambda args, ctx: bool(ctx.get("tainted")) and not send.taint_ok(args, ctx)

    async def never(*_a: Any, **_k: Any) -> Any:   # pragma: no cover - the card's name only
        return tool_error("claude_code_permission is not a tool the model can call.")
    card = ToolSpec(APPROVAL_TOOL, "Approval card for something a Claude Code session asks to do (raised by Grain, not callable by the model).",
                    _obj({"session": {"type": "string"}, "tool": {"type": "string"}, "input": {"type": "object"}}, []), never, "shell", "external")
    card.default = "ask"
    card.force_ask = lambda _a, _c: True
    card.available_fn = lambda: False   # never offered to the model: it only names the card
    specs = [run, fleet, status, send, stop, result, wait]
    for s in specs:
        s.available_fn = lambda: driver.binary() is not None
    return [*specs, card]
