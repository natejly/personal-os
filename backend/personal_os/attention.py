"""One attention state per chat run, desk and job: idle, working, needs_you or blocked.

The renderer mirrors this table in src/renderer/src/lib/attention.ts; both are checked against
backend/tests/fixtures/attention_cases.json so they cannot drift apart.
"""
from __future__ import annotations

from typing import Any

# Statuses not listed are idle: done, draft, paused, stopped, queued, a job waiting for its slot.
_RUN = {"running": "working", "awaiting_approval": "needs_you", "error": "blocked", "interrupted": "blocked"}
_DESK = {"planning": "working", "working": "working",
         "awaiting_plan": "needs_you", "needs_approval": "needs_you", "review": "needs_you",
         "blocked": "blocked", "interrupted": "blocked", "failed": "blocked"}


def attention(kind: str, status: str | None, *, pending_approvals: int = 0, pending_proposals: int = 0,
              last_error: str | None = None, paused_reason: str | None = None) -> str:
    """`kind` is run, desk or job; a job's `status` is its last run's. Something to decide wins over everything."""
    if pending_approvals or pending_proposals:
        return "needs_you"
    if kind == "job":
        if paused_reason or last_error:
            return "blocked"
        kind = "run"
    return (_DESK if kind == "desk" else _RUN).get(status or "", "idle")


def for_run(info: dict[str, Any]) -> str:
    return attention("run", info.get("status"))


def for_desk(row: dict[str, Any]) -> str:
    return attention("desk", row.get("status"))


def for_job(job: dict[str, Any], last_run_status: str | None, pending_proposals: int = 0) -> str:
    return attention("job", last_run_status, pending_proposals=pending_proposals,
                     last_error=job.get("last_error"), paused_reason=job.get("paused_reason"))
