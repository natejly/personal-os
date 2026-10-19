"""Claude Code as a built-in extension: fleets of headless `claude -p` sessions in worktrees Grain owns, with Grain's approvals.

The coding extension runs Claude Code as background sessions of the claude daemon and answers their prompts in a terminal;
this one drives the CLI directly over stream-json, keeps every session's event log in the app and turns each permission
question into an approval card in the chat that started the session. It reuses the coding extension's floor (deny rules and
the Bash hook), its worktree and diff helpers and its concurrency limit, so both kinds of session share one budget of live
processes. See manager.py for the mechanics, driver.py for the process and the stream, and docs/coding-agents.md.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .manager import ClaudeCode
from .routes import hook_router, ui_router
from .tools import tools

if TYPE_CHECKING:
    from ..host import ExtensionScope


class Hooks:
    """The hook handlers: sessions stop with the run that started them, and with the extension or the app."""

    def __init__(self, cc: ClaudeCode) -> None:
        self.cc = cc

    async def run_ended(self, run: Any) -> None:
        """run.after: the reply's approval closure is dead from here; a stopped reply also stops its sessions (a finished one leaves
        them working)."""
        self.cc.run_ended(run.run_id)
        if run.stop.is_set():
            await self.cc.stop_owned(run.run_id, f"the {'desk run' if run.desk_id else 'reply'} that started it was stopped")

    async def child_ended(self, ev: dict[str, Any]) -> None:
        """subagent.stop: a child stopped (not finished) takes the sessions it started with it."""
        if ev["exit_reason"] in ("stopped", "stale", "interrupted", "shutdown"):
            await self.cc.stop_owned(ev["child_id"], "the subagent or worker that started it was stopped")

    async def ext_changed(self, ev: dict[str, Any]) -> None:
        if ev["id"] in (self.cc.api.manifest.id, "coding") and not ev["enabled"]:
            await self.cc.stop_all(f"the {'Claude Code' if ev['id'] == self.cc.api.manifest.id else 'Coding'} extension was turned off")

    async def app_start(self, _ev: Any) -> None:
        """app.start (the tables exist by now): sessions a previous run left marked live have no process any more."""
        self.cc.recover()

    async def app_stop(self, _ev: Any) -> None:
        """app.stop: the processes are children of this app, so they end with it (a short grace: the hook has a 5 s budget)."""
        self.cc.closing = True
        await self.cc.stop_all("Grain was shut down while it was running", grace=1.5)


def activate(api: ExtensionScope) -> None:
    cc = ClaudeCode(api)
    for spec in tools(cc):
        api.tool(spec)
    api.router(ui_router(cc))
    api.router(hook_router(cc))
    hooks = Hooks(cc)
    api.on("run.after", hooks.run_ended)
    api.on("subagent.stop", hooks.child_ended)
    api.on("ext.changed", hooks.ext_changed)
    api.on("app.start", hooks.app_start)
    api.on("app.stop", hooks.app_stop)
