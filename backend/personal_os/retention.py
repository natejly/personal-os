"""Daily sweep for the tables that only ever grow.

What is pruned is bookkeeping, never something the user wrote or would look for: the per-call usage log, the
span traces on finished messages, the paged tool-result payloads the model could re-read, decided approval
cards, the idempotency journal of long-finished runs and their event tape. Messages, memories, documents,
notes, todos, plans, proposals and desks are never touched. Each threshold is a setting (retain*Days); the
defaults live in llm.DEFAULT_SETTINGS.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from typing import Any, Callable

from .db import Database
from .runs import EVENTS_RETAIN_S

log = logging.getLogger("personal_os.retention")

DAY = 86400.0
FIRST_RUN_DELAY_S = 120.0
INTERVAL_S = DAY


def _days(cfg: dict[str, Any], key: str, default: float) -> float:
    v = cfg.get(key, default)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else default


def archive_sweep(db: Database, days: float, t: float) -> dict[str, int]:
    """Archive (not delete) chats and job-run inbox messages whose last activity is older than `days`.

    Never touches what is still going: a pinned chat, a desk's chat, a chat with a live run, a run with a
    pending approval or proposal all stay. Restore is the existing chat unarchive or POST /inbox/runs/{id}/restore."""
    cut = t - days * DAY
    live = "('running','awaiting_approval')"
    with db.tx() as c:
        chats = c.execute(
            "UPDATE conversations SET archived_at=? WHERE archived_at IS NULL AND deleted_at IS NULL AND pinned_at IS NULL AND updated_at < ? "
            "AND id NOT IN (SELECT conversation_id FROM desks) "
            f"AND id NOT IN (SELECT conversation_id FROM agent_runs WHERE conversation_id IS NOT NULL AND status IN {live})", (t, cut)).rowcount
        runs = c.execute(
            "UPDATE agent_runs SET archived_at=? WHERE kind='job' AND archived_at IS NULL "
            f"AND status NOT IN {live} AND COALESCE(ended_at, updated_at, started_at) < ? "
            "AND run_id NOT IN (SELECT run_id FROM proposals WHERE status='pending' AND run_id IS NOT NULL) "
            "AND run_id NOT IN (SELECT run_id FROM approvals WHERE status='pending' AND run_id IS NOT NULL)", (t, cut)).rowcount
    return {"chats_archived": chats, "runs_archived": runs}


def sweep(db: Database, cfg: dict[str, Any], now: float | None = None) -> dict[str, int]:
    """Prune once. Returns rows removed (or message traces cleared) per table; a missing table counts 0."""
    t = time.time() if now is None else now
    usage_cut = t - _days(cfg, "retainUsageDays", 365) * DAY
    trace_cut = t - _days(cfg, "retainTraceDays", 60) * DAY
    result_cut = t - _days(cfg, "retainToolResultDays", 30) * DAY
    approval_cut = t - _days(cfg, "retainApprovalDays", 90) * DAY
    steps: dict[str, tuple[str, tuple[Any, ...]]] = {
        "usage_log": ("DELETE FROM usage_log WHERE created_at < ?", (usage_cut,)),
        # The message stays; only its span trace goes. NULL is what a message with no trace already has.
        "message_traces": ("UPDATE messages SET trace=NULL WHERE trace IS NOT NULL AND created_at < ?", (trace_cut,)),
        "tool_results": ("DELETE FROM tool_results WHERE created_at < ?", (result_cut,)),
        "tool_results_fts": ("DELETE FROM tool_results_fts WHERE result_id NOT IN (SELECT id FROM tool_results)", ()),
        # A pending card waits forever on purpose (runs.py); only decided ones age out.
        "approvals": ("DELETE FROM approvals WHERE status != 'pending' AND COALESCE(decided_at, created_at) < ?", (approval_cut,)),
        # `started` means "outcome unknown" and must stay for the user to resolve, however old.
        "executed_calls": ("DELETE FROM executed_calls WHERE status != 'started' AND COALESCE(finished_at, created_at) < ?", (approval_cut,)),
        "run_events": ("DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM agent_runs "
                       "WHERE ended_at IS NOT NULL AND ended_at < ?)", (t - EVENTS_RETAIN_S,)),
    }
    out: dict[str, int] = {}
    for name, (sql, params) in steps.items():
        try:
            with db.tx() as c:
                out[name] = c.execute(sql, params).rowcount
        except sqlite3.Error as e:
            out[name] = 0
            log.warning("retention: %s skipped (%s)", name, e)
    days = cfg.get("autoArchiveDays")
    if isinstance(days, (int, float)) and not isinstance(days, bool) and days > 0:  # 0 / missing = off
        try:
            out.update(archive_sweep(db, float(days), t))
        except sqlite3.Error as e:
            log.warning("retention: archive skipped (%s)", e)
    if any(out.values()):
        log.info("retention sweep: %s", ", ".join(f"{k}={v}" for k, v in out.items() if v))
    return out


class RetentionWorker:
    """Runs sweep() shortly after startup and then daily, off the event loop's critical path."""

    def __init__(self, db: Database, settings: Callable[[], dict[str, Any]]) -> None:
        self.db, self.settings = db, settings
        self.task: asyncio.Task[None] | None = None
        self.last: dict[str, Any] | None = None

    def run_now(self) -> dict[str, int]:
        counts = sweep(self.db, self.settings())
        self.last = {"at": time.time(), "removed": counts}
        return counts

    async def _loop(self, first_delay: float, interval: float) -> None:
        await asyncio.sleep(first_delay)
        while True:
            try:
                await asyncio.to_thread(self.run_now)
            except Exception:  # noqa: BLE001 - housekeeping must never take the backend down
                log.warning("retention sweep failed", exc_info=True)
            await asyncio.sleep(interval)

    def start(self, first_delay: float = FIRST_RUN_DELAY_S, interval: float = INTERVAL_S) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(first_delay, interval), name="retention")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
            self.task = None
