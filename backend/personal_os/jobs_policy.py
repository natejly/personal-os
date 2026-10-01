"""What happens around a job run, not in it: the overlap guard, the retry, and the failure streak.

`Scheduler` fires a slot and forgets it; this is the part that looks at how the run ended.

- Overlap. A slot that comes due while the job's previous run is still live is skipped (APScheduler's
  `max_instances=1`). The skip is recorded on the job (`last_skip_*`), and the slot is still consumed, so a
  long run does not buy a catch-up the moment it finishes.
- Retry. A run that ENDS in `error` or `interrupted` is launched again for the same slot, up to the job's
  `max_retries`, after an exponential backoff. The attempt number travels in the run's own input
  (`agent_runs.input.attempt`), so it is a stored fact and cannot reset when a watcher is rebuilt (a retry budget kept in memory loops forever).
- Streak. A *fire* that finally fails (its retries spent) extends `consecutive_failures`; a success clears it.
  At `jobFailureStreakLimit` the job is switched off with a `paused_reason`, which the Agent Inbox shows under
  "Needs you" with a Resume button. A job whose key is broken stops costing a run every slot.

What does not count: a run the user stopped (it ends `done`, or `stopped`/`cancelled`, never `error`); a manual
"Run now" (the user is watching, and a manual failure should not switch off the schedule); a dry run.
A run left `interrupted` by a backend restart is never retried either: the watcher died with the process, a
restart is not a model failure, and the outcome of the half-finished run is unknown. Nothing re-attaches on boot.

There is no heartbeat here: a watcher exists only while a run it launched is live, and it polls that one row.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

LIVE = ("running", "awaiting_approval")
FAILED = ("error", "interrupted")
# The longest a watcher will follow one run before giving up on it (the job budget is minutes, not hours).
WATCH_MAX_S = 2 * 3600.0
BACKOFF_CAP_S = 1800.0


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f and f >= 0 else default


class JobPolicy:
    def __init__(self, jobs: Any, run_store: Any, launch: Callable[[dict[str, Any], dict[str, Any]], Awaitable[str | None]],
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[None]] | None = None,
                 settings: Callable[[], dict[str, Any]] = dict) -> None:
        self.jobs = jobs
        self.runs = run_store
        self.launch = launch
        self.clock = clock
        self._sleep = sleep
        self.settings = settings
        self._tasks: set[asyncio.Task[None]] = set()

    async def _nap(self, s: float) -> None:
        await (self._sleep(s) if self._sleep is not None else asyncio.sleep(s))

    # ---- knobs ----
    def backoff(self, attempt: int) -> float:
        base = _num(self.settings().get("jobRetryBackoffS"), 120.0)
        return min(BACKOFF_CAP_S, base * (2 ** max(0, attempt - 1)))

    def streak_limit(self) -> int:
        return max(1, int(_num(self.settings().get("jobFailureStreakLimit"), 3)))

    # ---- overlap guard ----
    def live_run(self, job_id: str) -> dict[str, Any] | None:
        """The job's run that is still going, from rows (a run with no end), or None."""
        for r in self.runs.of_kind("job", since=self.clock() - 86400, limit=500):
            inp = r.get("input") if isinstance(r.get("input"), dict) else {}
            if inp.get("dry_run"):
                continue  # a preview never blocks, or counts as, the real thing
            if inp.get("job_id") == job_id and r.get("ended_at") is None and r.get("status") in LIVE:
                return r
        return None

    async def admit(self, job: dict[str, Any], fire: dict[str, Any]) -> tuple[bool, str | None]:
        """Whether this fire may launch. A refusal is recorded on the job, except for a manual run (no slot)."""
        if self.live_run(job["id"]) is None:
            return True, None
        why = "previous run still running"
        if not fire.get("manual"):
            self.jobs.record_skip(job["id"], why, self.clock())
        return False, why

    # ---- the watcher ----
    def watch_soon(self, job: dict[str, Any], fire: dict[str, Any], run_id: str) -> None:
        """Follow a launched run in the background. Manual and dry runs are not watched (see module docstring)."""
        if fire.get("manual") or fire.get("dry_run"):
            return
        t = asyncio.ensure_future(self.watch(job, fire, run_id))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for every watcher (tests, and a clean shutdown that wants the outcomes written)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        for t in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _wait_end(self, run_id: str) -> dict[str, Any] | None:
        deadline = self.clock() + WATCH_MAX_S
        gap = 1.0
        while True:
            row = self.runs.get(run_id)
            if row is None:
                return None
            if row.get("ended_at") is not None or row.get("status") not in LIVE:
                return row
            if self.clock() > deadline:
                return None
            await self._nap(gap)
            gap = min(5.0, gap + 1.0)

    async def watch(self, job: dict[str, Any], fire: dict[str, Any], run_id: str) -> None:
        try:
            row = await self._wait_end(run_id)
            if row is None:
                return
            await self.settle(job, fire, run_id, row)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a watcher bug must never reach the scheduler
            log.warning("job watcher for %s failed", run_id, exc_info=True)

    async def settle(self, job: dict[str, Any], fire: dict[str, Any], run_id: str, row: dict[str, Any]) -> None:
        jid = job["id"]
        attempt = int(fire.get("attempt") or 1)
        status = row.get("status")
        if status not in FAILED:
            if status == "done":
                self.jobs.record_outcome(jid, True)
            return  # stopped / cancelled by the user: neither a success nor a failure
        cur = self.jobs.get(jid)
        if cur is None:
            return
        retries = int(cur.get("max_retries") or 0)
        if attempt <= retries and cur["enabled"] and self.live_run(jid) is None:
            await self._nap(self.backoff(attempt))
            cur = self.jobs.get(jid)
            if cur is not None and cur["enabled"] and self.live_run(jid) is None:
                retry = {**fire, "attempt": attempt + 1, "retry_of": run_id, "late": False}
                nxt = await self.launch(cur, retry)
                if nxt:
                    self.jobs.mark_launched(jid, nxt)
                    await self.watch(cur, retry, nxt)
                return
        streak = self.jobs.record_outcome(jid, False)
        if streak >= self.streak_limit():
            err = (row.get("error") or "the run ended in an error").strip().splitlines()[0][:200]
            self.jobs.pause(jid, f"paused after {streak} failed runs: {err}", self.clock())
