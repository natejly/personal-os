"""System access panel: what macOS has granted this app, read without ever prompting, plus a shell self-check.

GET /system/access reads TCC state (full disk, per-app automation), installed browsers, the effective
workspace roots and the claude/opencode CLIs. POST /system/shell-check runs `echo ok` through the same sandboxed
runner the agent's shell tool uses."""
from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import codingagents, imessage, macos, opencode, shell

AUTOMATION = {"messages": "com.apple.MobileSMS", "finder": "com.apple.finder", "systemEvents": "com.apple.systemevents",
              "contacts": "com.apple.AddressBook", "calendar": "com.apple.iCal", "reminders": "com.apple.reminders"}
CLAUDE_INSTALL_HINT = "Install the claude CLI: npm i -g @anthropic-ai/claude-code"


class PermissionIn(BaseModel):
    id: str
    browser: str = ""


def _safe(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken probe must not fail the whole panel
        return macos.UNKNOWN


def _full_disk() -> str:
    if not macos.IS_MAC:
        return macos.UNKNOWN
    if macos.full_disk_access():
        return macos.GRANTED
    try:
        imessage._open_ro(imessage.DEFAULT_CHAT_DB).close()
        return macos.GRANTED
    except imessage.NeedsFullDiskAccess:
        return macos.DENIED
    except Exception:  # noqa: BLE001 - e.g. no Messages database on this Mac
        return macos.DENIED


@lru_cache(maxsize=8)
def _version(path: str) -> str | None:
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        lines = (r.stdout or r.stderr).strip().splitlines()
        return lines[0].strip() if r.returncode == 0 and lines else None
    except Exception:  # noqa: BLE001
        return None


def _cli(path: str | None, hint: str) -> dict[str, Any]:
    return {"path": path, "version": _version(path) if path else None, "hint": hint}


def _cli_safe(find: Callable[[], str | None], hint: str) -> dict[str, Any]:
    try:
        return _cli(find(), hint)
    except Exception:  # noqa: BLE001
        return _cli(None, hint)


def router(settings: Callable[[], dict[str, Any]], stored_roots: Callable[[], list[str]],
           shell_jobs: Callable[[], Any]) -> APIRouter:
    """settings: effective settings (roots already default to ~/Grain); stored_roots: the stored list, so `defaulted`
    can tell; shell_jobs: the shell job registry (resolved late, it is built after the routers)."""
    r = APIRouter()

    def access() -> dict[str, Any]:
        try:
            roots = [str(x) for x in settings().get("workspaceRoots") or []]
            defaulted = not stored_roots()
        except Exception:  # noqa: BLE001
            roots, defaulted = [], False
        try:
            names = list(macos.installed_browsers())
        except Exception:  # noqa: BLE001
            names = []
        return {
            "fullDisk": _safe(_full_disk),
            "automation": {k: _safe(lambda b=b: macos.automation_status(b)) for k, b in AUTOMATION.items()},
            "browsers": [{"name": n, "state": _safe(lambda n=n: macos.automation_status(macos.BROWSER_BUNDLES[n]))}
                         for n in names],
            "roots": {"roots": roots, "defaulted": defaulted},
            "clis": {"claude": _cli_safe(codingagents.claude_binary, CLAUDE_INSTALL_HINT),
                     "opencode": _cli_safe(opencode.binary, opencode.INSTALL_HINT)},
        }

    @r.get("/system/access")
    async def get_access() -> dict[str, Any]:
        return await asyncio.to_thread(access)

    @r.post("/system/permissions/request")
    async def permission_request(body: PermissionIn) -> dict[str, Any]:
        """Ask macOS for one permission: the only route that can put a system dialog on screen, and it
        exists because the user pressed Grant."""
        return {"result": await asyncio.to_thread(macos.request_permission, body.id, body.browser)}

    @r.post("/system/permissions/open")
    def permission_open(body: PermissionIn) -> dict[str, bool]:
        """Open the Privacy & Security pane for one permission. Opening a pane grants nothing."""
        return {"ok": macos.open_settings(body.id)}

    @r.post("/system/shell-check")
    async def shell_check() -> dict[str, Any]:
        cfg = settings()
        cwd: str | None = None
        try:
            roots = shell.granted_roots(cfg, None)
            where, _root = shell.resolve_cwd(None, roots)
            cwd = str(where)
            ok, out = await shell.run_fixed(shell_jobs(), ["/bin/sh", "-c", "echo ok"], cwd, cfg, sandboxed=True, timeout=10)
            return {"ok": bool(ok and "ok" in out), "output": out, "cwd": cwd, "error": None}
        except shell.ShellError as e:
            return {"ok": False, "output": "", "cwd": cwd, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "output": "", "cwd": cwd, "error": f"{type(e).__name__}: {e}"}

    return r
