"""A Toolbox that carries the coding extension's tools and its refusal of repo edits, for tests that call them."""
from __future__ import annotations

import asyncio
import itertools
import tempfile
from typing import Any

from personal_os.db import Database
from personal_os.dispatch import dispatch_tool
from personal_os.extensions.coding.extension import Coding
from personal_os.extensions.host import ExtensionContext, ExtensionHost, ExtensionScope
from personal_os.tools import Toolbox

_calls = itertools.count(1)


def coding_scope(host: ExtensionHost, db: Database | None = None, **services: Any) -> ExtensionScope:
    """The coding extension loaded into `host` over `db` (a fresh one by default), its tables migrated."""
    db = db or Database(tempfile.mkdtemp(prefix="coding-ext-"))
    scope = host.load_builtin("coding", ExtensionContext(
        db=db, hidden_views=lambda: (), services=dict(services), settings=db.get_settings, set_settings=db.set_settings,
        sid=lambda p: p, wsid=lambda p: p, publish=lambda _n, _d: None))
    host.migrate(db)
    return scope


def coding_box(settings: dict[str, Any] | None = None, **toolbox_args: Any) -> tuple[Toolbox, Coding, ExtensionHost]:
    """A Toolbox over `settings` whose bus, prechecks and tool list come from a host that has the coding extension."""
    host = ExtensionHost()
    scope = coding_scope(host)
    cfg = settings if settings is not None else {}
    tb = Toolbox(None, None, None, lambda: cfg, bus=host.bus, prechecks=host.prechecks, **toolbox_args)  # type: ignore[arg-type]
    scope.app.services["coding"]().bind(tb)
    tb.extensions.append(scope)
    tb.set_extension_tools(scope)
    return tb, scope.app.services["coding"](), host


def call(tb: Toolbox, tool: str, ctx: dict[str, Any], /, **args: Any) -> Any:
    """One tool call the way every loop makes it: under the tool hooks."""
    return asyncio.run(dispatch_tool(tb, ctx, tool, args, call_id=f"t{next(_calls)}"))


class FakeHub:
    """Stands in for the Electron main process's terminal hub: answers the bridge commands over scripted terminals."""

    def __init__(self) -> None:
        self.terms: dict[str, dict[str, Any]] = {}
        self.opened: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def feed(self, tid: str, data: str) -> None:
        t = self.terms[tid]
        t["ring"] += data
        t["screen"] = data.replace("\r\n", "\n").rstrip("\n").split("\n")[-1]

    def exit(self, tid: str, code: int = 0) -> None:
        self.terms[tid].update(exited=True, code=code)

    @staticmethod
    def _state(t: dict[str, Any]) -> dict[str, Any]:
        return {"attached": t["attached"], "dirty": t["dirty"], "exited": t["exited"], "exit_code": t["code"], "cursor": len(t["ring"])}

    async def call(self, command: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        from personal_os.extensions.coding.terminals import TerminalError
        self.calls.append(command)
        kind, tid = command["type"], command.get("terminal_id")
        if kind == "open":
            self.opened.append(command)
            self.terms[tid] = {"ring": "", "screen": "", "exited": False, "code": None, "attached": False, "dirty": False, "written": []}
            return {"ok": True}
        t = self.terms.get(tid)
        if t is None:
            raise TerminalError("No such terminal")
        if kind == "write":
            if t["exited"]:
                raise TerminalError("The terminal has exited")
            if t["attached"]:
                return {"blocked": "attached"}
            if t["dirty"] and not command.get("clear_dirty"):
                return {"blocked": "dirty"}
            t["dirty"] = t["dirty"] and not command.get("clear_dirty")
            t["written"].append(command["data"])
            return {"ok": True, **self._state(t)}
        if kind == "read":
            text = t["ring"][command["cursor"]:]
            return {"text": text, "from_cursor_lost": False, **self._state(t), **({"screen": t["screen"]} if command.get("screen") else {})}
        if kind == "status":
            return {"alive": not t["exited"], **self._state(t)}
        if kind == "close":
            self.terms.pop(tid)
            return {"closed": True, "exit_code": t["code"]}
        raise AssertionError(kind)


def fake_terminals(tb: Toolbox, coding: Coding, monkeypatch: Any) -> FakeHub:
    """Give `coding` a terminal hub that is always connected, and a shell that is always confined (the argv gets a marker)."""
    import time
    from personal_os import shell
    hub = FakeHub()
    coding.terminals.bridge.call = hub.call  # type: ignore[method-assign]
    coding.terminals.bridge._seen = time.monotonic() + 1e9
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)

    async def confine(jobs: Any, argv: list[str], env: dict[str, str], settings: dict[str, Any], writable: list[str], cwd: str, *, unsandboxed: bool = False) -> Any:
        return shell.Confined(["sandboxed", *writable[:1], "--", *argv], False, False, None)
    monkeypatch.setattr(shell, "confine", confine)
    return hub
