"""System access panel: what macOS has granted this app, read without ever prompting, plus a shell self-check.

GET /system/access reads TCC state (full disk, input monitoring, per-app automation), installed browsers, the file scope
(what is off limits and what asks first, from mac.py) and the claude/opencode CLIs. POST /system/shell-check runs `echo ok`
in the home folder through the same sandboxed runner the agent's shell tool uses."""
from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import APIRouter

from . import activity, codingagents, mac, opencode, shell

AUTOMATION = {"finder": "com.apple.finder", "systemEvents": "com.apple.systemevents",
              "contacts": "com.apple.AddressBook", "calendar": "com.apple.iCal", "reminders": "com.apple.reminders"}
CLAUDE_INSTALL_HINT = "Install the claude CLI: npm i -g @anthropic-ai/claude-code"


def _safe(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken probe must not fail the whole panel
        return activity.UNKNOWN


def _full_disk() -> str:
    if not activity.IS_MAC:
        return activity.UNKNOWN
    return activity.GRANTED if activity.full_disk_access() else activity.DENIED


@lru_cache(maxsize=8)
def _version(path: str) -> str | None:
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        lines = (r.stdout or r.stderr).strip().splitlines()
        return lines[0].strip() if r.returncode == 0 and lines else None
    except Exception:  # noqa: BLE001
        return None


def _safe_scope() -> dict[str, list[str]]:
    try:
        return mac.scope_summary()
    except Exception:  # noqa: BLE001
        return {"protected": [], "sensitive": []}


def _cli(path: str | None, hint: str) -> dict[str, Any]:
    return {"path": path, "version": _version(path) if path else None, "hint": hint}


def _cli_safe(find: Callable[[], str | None], hint: str) -> dict[str, Any]:
    try:
        return _cli(find(), hint)
    except Exception:  # noqa: BLE001
        return _cli(None, hint)


def router(settings: Callable[[], dict[str, Any]], shell_jobs: Callable[[], Any]) -> APIRouter:
    """settings: effective settings; shell_jobs: the shell job registry (resolved late, it is built after the routers)."""
    r = APIRouter()

    def access() -> dict[str, Any]:
        try:
            names = list(activity.installed_browsers())
        except Exception:  # noqa: BLE001
            names = []
        return {
            "fullDisk": _safe(_full_disk),
            "inputMonitoring": _safe(activity.input_monitoring_status),
            "automation": {k: _safe(lambda b=b: activity.automation_status(b)) for k, b in AUTOMATION.items()},
            "browsers": [{"name": n, "state": _safe(lambda n=n: activity.automation_status(activity.BROWSER_BUNDLES[n]))}
                         for n in names],
            "scope": _safe_scope(),
            "clis": {"claude": _cli_safe(codingagents.claude_binary, CLAUDE_INSTALL_HINT),
                     "opencode": _cli_safe(opencode.binary, opencode.INSTALL_HINT)},
        }

    @r.get("/system/access")
    async def get_access() -> dict[str, Any]:
        return await asyncio.to_thread(access)

    @r.post("/system/shell-check")
    async def shell_check() -> dict[str, Any]:
        cfg = settings()
        cwd: str | None = None
        try:
            cwd = str(shell.resolve_cwd(None, mac.home()))
            ok, out = await shell.run_fixed(shell_jobs(), ["/bin/sh", "-c", "echo ok"], cwd, cfg, sandboxed=True, timeout=10)
            return {"ok": bool(ok and "ok" in out), "output": out, "cwd": cwd, "error": None}
        except shell.ShellError as e:
            return {"ok": False, "output": "", "cwd": cwd, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "output": "", "cwd": cwd, "error": f"{type(e).__name__}: {e}"}

    return r
