"""What happens around a job run, not in it: the overlap guard, the retry, and the failure streak.

`Scheduler` fires a slot and forgets it; this is the part that looks at how the run ended.

- Overlap. A slot that comes due while the job's previous run is still live is skipped (at most one
  instance per job). The skip is recorded on the job (`last_skip_*`), and the slot is still consumed, so a
  long run does not buy a catch-up the moment it finishes.
- Retry. A run that ENDS in `error` or `interrupted` is launched again for the same slot, up to the job's
  `max_retries`, after an exponential backoff. The attempt number travels in the run's own input
  (`agent_runs.input.attempt`), so it is a stored fact and cannot reset when a watcher is rebuilt (a retry budget kept in memory loops forever).
- Streak. A *fire* that finally fails (its retries spent) extends `consecutive_failures`; a success clears it.
  At `jobFailureStreakLimit` the job is switched off with a `paused_reason`, which the Agent Inbox shows under
  "Needs you" with a Resume button. A job whose key is broken stops costing a run every slot.

What does not count: a run the user stopped (it ends `done`, or `stopped`/`cancelled`, never `error`); a manual
"Run now" (the user is watching, and a manual failure should not switch off the schedule); a dry run.
A run that fails while the provider auth breaker is open (auth_breaker: the key was rejected) is neither retried nor
counted: the job is not at fault, and its next slot runs once the key works again. A slot that comes due while the
breaker is open is skipped with the breaker's message as the reason.
A run left `interrupted` by a backend restart lost its watcher with the process, so nothing follows it live.
`boot_retry` is the one place that picks it up again: once, on start, only if it is the job's latest run, it is
`interrupted` (a user stop ends `done`), and its stored `attempt` is under the job's `max_retries`.

There is no heartbeat here: a watcher exists only while a run it launched is live, and it polls that one row.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable

from . import auth_breaker
from .job_history import result_digest
from .jobs import Proposals, retryable

log = logging.getLogger(__name__)

LIVE = ("running", "awaiting_approval")
FAILED = ("error", "interrupted")
# The longest a watcher will follow one run before giving up on it (a job run is minutes, not hours; an idle one is stopped after JOB_IDLE_SECONDS).
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
        why = auth_breaker.blocked(self.settings())
        if why is None and self.live_run(job["id"]) is None:
            return True, None
        why = why or "previous run still running"
        if not fire.get("manual"):
            self.jobs.record_skip(job["id"], why, self.clock(), fire.get("due_at"))
        return False, why

    # ---- the watcher ----
    def watch_soon(self, job: dict[str, Any], fire: dict[str, Any], run_id: str) -> None:
        """Follow a launched run in the background. Manual and dry runs are not watched (see module docstring)."""
        # A desk job's fire is a desk: retrying it would be a second desk for the slot. A desk that fails is
        # resumed from the desk itself.
        if fire.get("manual") or fire.get("dry_run") or job.get("target") == "desk":
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

    async def boot_retry(self) -> list[str]:
        """On process start: relaunch, once, an enabled job's latest run if it was left `interrupted` with retries
        to spare. The retry is an ordinary job run (proposal-only) carrying attempt+1 and `retry_of`, and being
        the new latest run it keeps a second boot from retrying the same slot again."""
        out: list[str] = []
        for job in self.jobs.list():
            last = next(iter(self.runs.of_job(job["id"], limit=1)), None)
            inp = last.get("input") if last and isinstance(last.get("input"), dict) else {}
            # A one-off is disabled the moment it fires, yet still owns its retries.
            if (not retryable(job) or not last or last.get("status") != "interrupted" or inp.get("manual")
                    or inp.get("dry_run") or self.live_run(job["id"])):
                continue
            attempt = int(inp.get("attempt") or 0)
            if attempt >= int(job.get("max_retries") or 0):
                continue
            retry = {**inp, "attempt": max(attempt, 1) + 1, "retry_of": last["run_id"], "late": False}
            rid = await self.launch(job, retry)
            if rid:
                self.jobs.mark_launched(job["id"], rid)
                self.watch_soon(job, retry, rid)
                out.append(rid)
        return out

    def note_change(self, job_id: str, run_id: str, row: dict[str, Any]) -> None:
        """For a job set to notify only on change: compare this run's result digest with the last one's and, when
        equal, flag the run `unchanged` so the inbox and notifications leave it out. A run that left proposals is
        news whatever its text says. ponytail: the flag lands when the watcher sees the run end (a few seconds), so
        a very fast inbox refresh can still show it once."""
        job = self.jobs.get(job_id)
        if not job or not job.get("only_on_change") or not row.get("message_id"):
            return
        digest = result_digest(self.runs.transcript(run_id, row["message_id"])[0])
        if not digest:
            return
        changed = self.jobs.record_digest(job_id, digest, self.clock())
        if not changed and not Proposals(self.jobs.db).list(None, run_id=run_id, limit=1):
            self.runs.mark_unchanged(run_id)

    async def settle(self, job: dict[str, Any], fire: dict[str, Any], run_id: str, row: dict[str, Any]) -> None:
        jid = job["id"]
        attempt = int(fire.get("attempt") or 1)
        status = row.get("status")
        if status not in FAILED:
            if status == "done":
                self.jobs.record_outcome(jid, True)
                self.note_change(jid, run_id, row)
            return  # stopped / cancelled by the user: neither a success nor a failure
        cur = self.jobs.get(jid)
        if cur is None:
            return
        if (held := auth_breaker.blocked(self.settings())) is not None:
            self.jobs.record_skip(jid, held, self.clock(), fire.get("due_at"))
            return
        retries = int(cur.get("max_retries") or 0)
        if attempt <= retries and retryable(cur) and self.live_run(jid) is None:
            await self._nap(self.backoff(attempt))
            cur = self.jobs.get(jid)
            if cur is not None and retryable(cur) and self.live_run(jid) is None:
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
