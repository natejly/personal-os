"""Supportability routes: GET /diagnostics (a redacted bundle for a bug report) and POST /maintenance/sweep.

Kept out of app.py: the router is built from the few things it needs and included with one line.
"""
from __future__ import annotations

import os
import platform
import sqlite3
import sys
import time
from typing import Any, Callable

from fastapi import APIRouter

from . import logs
from .db import Database
from .retention import RetentionWorker

LOG_LINES = 300
# A settings key that holds a credential, by name. Matching by name means a key added later is masked by default.
_SECRET_KEY = ("key", "secret", "token", "password", "credential")


def _is_secret_key(k: str) -> bool:
    low = k.lower()
    return any(w in low for w in _SECRET_KEY)


def mask_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Settings with every credential replaced by "set"/"unset", at any depth. Prompts are dropped: they are the user's text."""
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k == "systemPrompt":
            out[k] = f"<{len(str(v))} chars>"
        elif _is_secret_key(k):
            out[k] = "set" if v else "unset"
        elif isinstance(v, dict):
            out[k] = mask_settings(v)
        else:
            out[k] = v
    return out


def secret_values(cfg: dict[str, Any]) -> list[str]:
    """Every string credential in settings, so the log redactor can match them literally."""
    found: list[str] = []
    for k, v in cfg.items():
        if _is_secret_key(k):
            if isinstance(v, str):
                found.append(v)
            elif isinstance(v, dict):
                found.extend(x for x in v.values() if isinstance(x, str))
        elif isinstance(v, dict):
            found.extend(secret_values(v))
    return [s for s in found if len(s) >= 8]


def table_counts(db: Database) -> dict[str, int]:
    """Row counts of the tables retention manages, so a report shows what is growing."""
    out: dict[str, int] = {}
    for t in ("messages", "usage_log", "tool_results", "approvals", "executed_calls", "run_events", "agent_runs"):
        try:
            with db.tx() as c:
                out[t] = int(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except sqlite3.Error:
            pass
    return out


def build_report(db: Database, cfg: dict[str, Any], retention: RetentionWorker, permissions: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    extra = secret_values(cfg)
    d = logs.log_dir()
    lines: dict[str, list[str]] = {}
    for name in (logs.LOG_NAME, "main.log"):
        got = logs.tail_lines(d / name, LOG_LINES)
        if got:
            lines[name] = [logs.redact(ln, extra) for ln in got]
    try:
        perms = [{"id": p.get("id"), "label": p.get("label"), "state": p.get("state")} for p in permissions()]
    except Exception as e:  # noqa: BLE001 - a failing probe is itself a diagnostic
        perms = [{"error": str(e)[:200]}]
    try:
        db_bytes = db.path.stat().st_size
    except OSError:
        db_bytes = None
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "versions": {
            "app": os.environ.get("PERSONAL_OS_APP_VERSION") or None,
            "backend": "0.1.0",
            "python": sys.version.split()[0],
            "os": platform.platform(),
            "machine": platform.machine(),
        },
        "model": {"baseUrl": logs.redact(str(cfg.get("baseUrl") or "")), "defaultModel": cfg.get("defaultModel"), "extractionModel": cfg.get("extractionModel")},
        "settings": mask_settings(cfg),
        "database": {"bytes": db_bytes, "rows": table_counts(db)},
        "retention_last_sweep": retention.last,
        "permissions": perms,
        "log_dir": str(d),
        "logs": lines,
    }


def router(db: Database, settings: Callable[[], dict[str, Any]], retention: RetentionWorker,
           permissions: Callable[[], list[dict[str, Any]]]) -> APIRouter:
    r = APIRouter()

    @r.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        return build_report(db, settings(), retention, permissions)

    @r.post("/maintenance/sweep")
    def sweep_now() -> dict[str, Any]:
        """Run the retention sweep now instead of waiting for the daily one."""
        return {"removed": retention.run_now()}

    return r
