"""Scheduled background work, and the proposals a background run leaves behind instead of acting.

Three pieces, all of them rows:

- `Jobs`     — the `jobs` table: a schedule, the prompt to run, and the two stamps the catch-up rule needs
               (`next_due_at`, the slot we are waiting for, and `last_due_at`, the slot the last launch was
               *for*). A schedule is one of two kinds, and past `next_due_at` nothing downstream cares which:
                 · `cron` — an expression read in a timezone, repeating forever.
                 · `once` — a single instant, `run_at`. "Tomorrow at 3pm, do this." It fires one time and then
                   switches itself off, so a spent one-off stays in the list as a record of what it did rather
                   than as a chore the user has to come back and delete.
- `Proposals` — the `proposals` table: an outward-facing tool call a background run was not allowed to
               make. Accepting one is a user action, and is what actually executes it, once.
- `Scheduler` — a clock, not a heartbeat. It wakes on the earliest `next_due_at` (capped, so a config
               change is noticed), compares two floats per job, and launches nothing unless a slot is
               due. There is no "are you there?" poll and no model call on an idle tick.

Catch-up, when the machine was asleep or the backend was down across one or more slots:
  exactly ONE run for the MOST RECENT missed slot, reported late. A one-off missed the same way still runs,
  late, because the whole point of "at 3pm, do this" is that closing the lid does not cancel it.
Firing once per missed slot is a thundering herd (and N times the money); dropping the slot silently is
the behaviour this feature exists to avoid. So the pass collapses the whole gap into one launch, records
the slot it was *for* (`last_due_at`) next to when it actually ran (`last_fired_at`), and counts the slots
it skipped, which is what the Agent Inbox shows as "ran late".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone as _utc
from typing import Any, Awaitable, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

from .db import Database, new_id, now, row_to_dict
from .runs import args_digest

log = logging.getLogger(__name__)

# How long the scheduler may sleep in one go, even when the next slot is hours off. This is not a liveness
# poll and it never calls a model: one wake is one SELECT over a handful of rows. It is short because
# asyncio's clock does not advance while the machine is suspended, so a single long sleep would come back
# with hours still "left" on it and make every catch-up that much later. It is also how a job enabled in
# another window is noticed.
MAX_SLEEP_S = 60.0
# A fire this far past its slot is just scheduling jitter, not "late".
LATE_GRACE_S = 90.0
# Upper bound on how many skipped slots are counted, so a job asleep for a year does not walk a million dates.
MAX_MISSED_COUNTED = 500

PROPOSAL_STATUSES = ("pending", "accepted", "rejected")
KINDS = ("cron", "once")

# What a job run is launched with. The three the user asked for; seeded disabled, because an unattended
# run costs money and nobody opted in yet (the Agent Inbox offers the toggle).
SEED_JOBS: list[dict[str, Any]] = [
    {"name": "Morning brief", "cron": "30 7 * * *",
     "prompt": "Give me my morning brief: check my calendar for today and tomorrow, scan unread email for anything "
               "that needs a reply, list my open todos and flag the overdue ones, and end with the three things I "
               "should do first. Be concise and use headers."},
    {"name": "Scan unread and draft replies", "cron": "0 9 * * 1-5",
     "prompt": "Scan my unread inbox from the last two days. For each message that genuinely needs a reply from me, "
               "draft one: short, in my voice, and specific about what happens next. Skip newsletters, receipts and "
               "notifications. Finish with a one-line list of what you drafted and what you skipped."},
    {"name": "Weekly review", "cron": "0 17 * * 5",
     "prompt": "Write my weekly review: what moved this week (from my todos, calendar and recent chats), what slipped, "
               "and the three things that matter most next week. Be specific and short; no filler."},
]


def local_tz_name() -> str:
    """The machine's IANA timezone name, or UTC if it cannot be named.

    A job stores a zone *name* rather than an offset so that "07:30" keeps meaning 07:30 across a DST change.
    That rules out the two obvious sources: `datetime.now().astimezone().tzinfo` is a fixed offset with no name,
    and `time.tzname[0]` is the *standard-time* abbreviation — 'EST' even in July — which ZoneInfo accepts as a
    zone that never observes DST, so every job booked in summer would fire an hour off for half the year.
    So: $TZ, then whatever /etc/localtime points at, then the abbreviations as a last resort.
    """
    tz = (os.environ.get("TZ") or "").strip()
    if tz and valid_tz(tz):
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            name = link.split("zoneinfo/")[-1]
            if valid_tz(name):
                return name
    except OSError:
        pass
    for cand in (getattr(datetime.now().astimezone().tzinfo, "key", None), *time.tzname):
        if cand and valid_tz(str(cand)):
            return str(cand)
    return "UTC"


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _local(ts: float, tz: str) -> datetime:
    return datetime.fromtimestamp(ts, _utc.utc).astimezone(_zone(tz))


def valid_tz(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def valid_cron(expr: str) -> bool:
    try:
        croniter(expr)
        return True
    except (CroniterBadCronError, ValueError, KeyError, TypeError):
        return False


def next_fire(expr: str, tz: str, after: float) -> float:
    """The first slot strictly after `after`, as a unix timestamp. Read in `tz`, so DST moves with the wall clock."""
    return float(croniter(expr, _local(after, tz)).get_next(datetime).timestamp())


def prev_fire(expr: str, tz: str, at_or_before: float) -> float | None:
    """The most recent slot at or before `at_or_before`. None if the expression has no earlier slot."""
    # croniter's get_prev is strictly before its start, so start one second later to keep an exact hit.
    try:
        return float(croniter(expr, _local(at_or_before + 1.0, tz)).get_prev(datetime).timestamp())
    except (CroniterBadCronError, ValueError):
        return None


def slots_between(expr: str, tz: str, frm: float, to: float) -> int:
    """How many slots fall in [frm, to]. Counts the one we were waiting for, so an on-time fire is 1."""
    if to < frm:
        return 0
    it = croniter(expr, _local(frm - 1.0, tz))
    n = 0
    while n < MAX_MISSED_COUNTED:
        t = float(it.get_next(datetime).timestamp())
        if t > to:
            break
        n += 1
    return n


def parse_when(when: str, tz: str) -> float | None:
    """An ISO-8601 instant from `when`, read in `tz` when it carries no offset of its own.

    Deliberately not a natural-language parser: the model knows today's date and is asked for
    "2026-10-01T15:00", which is unambiguous, rather than "next Tuesday", which is not. A bare date is
    rejected rather than silently turned into midnight — nobody schedules a task for midnight by saying
    only the date.
    """
    s = (when or "").strip()
    if not s:
        return None
    dated_only = "T" not in s and ":" not in s
    if " " in s and "T" not in s:  # "2026-10-01 15:00" is what people type
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dated_only:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tz))
    return float(dt.timestamp())


def valid_schedule(kind: str, cron: str | None, run_at: float | None) -> bool:
    """Whether this pair of fields is a schedule the scheduler can actually read."""
    if kind == "once":
        return run_at is not None
    return valid_cron(cron or "")


def spent(job: dict[str, Any]) -> bool:
    """True if this one-off has already fired for the instant it is currently set to."""
    if job.get("kind") != "once" or job.get("run_at") is None or job.get("last_due_at") is None:
        return False
    return abs(float(job["last_due_at"]) - float(job["run_at"])) < 1.0


def next_due_for(job: dict[str, Any], after: float) -> float | None:
    """The instant this job should next fire, or None if it never will again.

    A cron job always has a next slot. A one-off has exactly one — `run_at` — and keeps it even when that is
    already past, which is what makes a missed one-off run late instead of vanishing. Once it has fired for
    that instant it is spent, and there is no next.
    """
    if job.get("kind") == "once":
        return None if spent(job) or job.get("run_at") is None else float(job["run_at"])
    return next_fire(job["cron"], job["timezone"], after)


class Jobs:
    """CRUD over the `jobs` table. Every writer keeps `next_due_at` in step with the schedule and `enabled`."""

    FIELDS = ("name", "kind", "cron", "run_at", "timezone", "enabled", "prompt", "project_id")
    # Changing any of these re-arms the job: a new schedule must not inherit the old one's pending slot.
    RE_ARM = frozenset({"kind", "cron", "run_at", "timezone", "enabled"})

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: Any) -> dict[str, Any] | None:
        d = row_to_dict(r)
        if d is not None:
            d["enabled"] = bool(d["enabled"])
        return d

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY name").fetchall()
        return [d for d in (self._row(r) for r in rows) if d]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._row(c.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone())

    def create(self, name: str, cron: str, prompt: str, *, kind: str = "cron", run_at: float | None = None,
               timezone: str | None = None, enabled: bool = False, project_id: str | None = None,
               at: float | None = None) -> dict[str, Any]:
        tz = timezone or local_tz_name()
        t = at if at is not None else now()
        jid = new_id()
        kind = kind if kind in KINDS else "cron"
        cron = "" if kind == "once" else cron
        fresh = {"kind": kind, "cron": cron, "run_at": run_at, "timezone": tz, "last_due_at": None}
        nxt = next_due_for(fresh, t) if enabled else None
        with self.db.tx() as c:
            c.execute("INSERT INTO jobs(id, name, kind, cron, run_at, timezone, enabled, prompt, project_id, next_due_at, "
                      "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (jid, name, kind, cron, run_at, tz, int(enabled), prompt, project_id, nxt, t, t))
        return self.get(jid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any], at: float | None = None) -> dict[str, Any] | None:
        job = self.get(id)
        if not job:
            return None
        t = at if at is not None else now()
        cols = {k: v for k, v in patch.items() if k in self.FIELDS}
        if "enabled" in cols:
            cols["enabled"] = int(bool(cols["enabled"]))
        if cols.get("kind") == "once":
            cols["cron"] = ""
        merged = {**job, **cols}
        # Re-arm from now whenever the schedule or the switch changes: a job disabled across a slot has no
        # missed slot to catch up on, and a new cron expression must not inherit the old one's pending slot.
        # A one-off given a new run_at stops being spent, so this is also how a fired task is rescheduled.
        if self.RE_ARM & cols.keys():
            cols["next_due_at"] = next_due_for(merged, t) if merged["enabled"] else None
        if not cols:
            return job
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.db.tx() as c:
            c.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?", (*cols.values(), t, id))
        return self.get(id)

    def delete(self, id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM jobs WHERE id=?", (id,)).rowcount > 0

    def due(self, at: float) -> list[dict[str, Any]]:
        """Enabled jobs whose slot has arrived. A disabled job is never here, armed or not."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM jobs WHERE enabled=1 AND next_due_at IS NOT NULL AND next_due_at <= ? "
                             "ORDER BY next_due_at", (at,)).fetchall()
        return [d for d in (self._row(r) for r in rows) if d]

    def earliest_due(self) -> float | None:
        with self.db.tx() as c:
            r = c.execute("SELECT MIN(next_due_at) AS t FROM jobs WHERE enabled=1 AND next_due_at IS NOT NULL").fetchone()
        return float(r["t"]) if r and r["t"] is not None else None

    def arm(self, at: float) -> int:
        """Give every enabled job with no armed slot one, from now. A job armed this way has nothing to catch up.

        A spent one-off is never re-armed here, however it was left enabled: `next_due_for` has no next instant
        for it, so a fired task cannot quietly run a second time.
        """
        n = 0
        for job in self.list():
            if not (job["enabled"] and job["next_due_at"] is None):
                continue
            if not valid_schedule(job["kind"], job["cron"], job["run_at"]):
                continue
            nxt = next_due_for(job, at)
            if nxt is None:
                continue
            with self.db.tx() as c:
                c.execute("UPDATE jobs SET next_due_at=?, updated_at=? WHERE id=?", (nxt, at, job["id"]))
            n += 1
        return n

    def mark_fired(self, id: str, *, fired_at: float, due_at: float, next_due_at: float | None,
                   run_id: str | None = None, error: str | None = None, disable: bool = False) -> None:
        """Book the fire. `disable` is how a one-off retires itself: it has no next slot, so leaving it switched
        on would only offer the user a toggle that does nothing."""
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET last_fired_at=?, last_due_at=?, last_run_id=?, last_error=?, next_due_at=?, "
                      "updated_at=?" + (", enabled=0" if disable else "") + " WHERE id=?",
                      (fired_at, due_at, run_id, error, next_due_at, fired_at, id))

    def mark_launched(self, id: str, run_id: str | None, error: str | None = None) -> None:
        """The outcome of the launch itself, written after the clock bookkeeping is already safe."""
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET last_run_id=?, last_error=? WHERE id=?", (run_id, error, id))

    def seed(self, at: float | None = None) -> int:
        """The three jobs the app ships with, once. Disabled: an unattended run spends money, so the user opts in."""
        have = {j["name"] for j in self.list()}
        made = 0
        for s in SEED_JOBS:
            if s["name"] not in have:
                self.create(s["name"], s["cron"], s["prompt"], enabled=False, at=at)
                made += 1
        return made


class Proposals:
    """The `proposals` table: what a background run wanted to do outside the app, recorded instead of done."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: Any) -> dict[str, Any] | None:
        d = row_to_dict(r, ("args", "result"))
        if d is not None:
            d["edited"] = bool(d["edited"])
        return d

    def create(self, *, run_id: str | None, tool: str, args: dict[str, Any], job_id: str | None = None,
               conversation_id: str | None = None, message_id: str | None = None, call_id: str | None = None,
               at: float | None = None) -> dict[str, Any]:
        pid = new_id()
        t = at if at is not None else now()
        with self.db.tx() as c:
            c.execute("INSERT INTO proposals(id, run_id, job_id, conversation_id, message_id, call_id, tool, args, args_digest, "
                      "status, created_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)",
                      (pid, run_id, job_id, conversation_id, message_id, call_id, tool,
                       json.dumps(args, ensure_ascii=False, default=str), args_digest(args), t))
        return self.get(pid)  # type: ignore[return-value]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._row(c.execute("SELECT * FROM proposals WHERE id=?", (id,)).fetchone())

    def list(self, status: str | None = "pending", run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if run_id:
            where.append("run_id=?")
            params.append(run_id)
        sql = "SELECT * FROM proposals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        with self.db.tx() as c:
            rows = c.execute(sql, (*params, max(1, min(int(limit), 500)))).fetchall()
        return [d for d in (self._row(r) for r in rows) if d]

    def counts(self, run_ids: Iterable[str]) -> dict[str, dict[str, int]]:
        """{run_id: {status: n}} for the inbox, without a query per run."""
        ids = [r for r in run_ids if r]
        if not ids:
            return {}
        with self.db.tx() as c:
            rows = c.execute(f"SELECT run_id, status, COUNT(*) AS n FROM proposals WHERE run_id IN ({','.join('?' * len(ids))}) "
                             "GROUP BY run_id, status", ids).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["run_id"], {})[r["status"]] = int(r["n"])
        return out

    def claim(self, id: str, args: dict[str, Any] | None = None, at: float | None = None) -> dict[str, Any] | None:
        """Move one pending proposal to 'accepted', atomically. None if it is gone or already decided.

        The claim is what makes accepting idempotent: whoever flips the row runs the call, and a second
        accept finds nothing pending and never reaches the tool. `args` is the user's edit.
        """
        t = at if at is not None else now()
        sets, params = ["status='accepted'", "decided_at=?"], [t]
        if args is not None:
            sets += ["args=?", "args_digest=?", "edited=1"]
            params += [json.dumps(args, ensure_ascii=False, default=str), args_digest(args)]
        with self.db.tx() as c:
            n = c.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=? AND status='pending'", (*params, id)).rowcount
        return self.get(id) if n else None

    def reject(self, id: str, at: float | None = None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            n = c.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=? AND status='pending'",
                          (at if at is not None else now(), id)).rowcount
        return self.get(id) if n else None

    def record(self, id: str, result: Any, error: str | None = None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE proposals SET result=?, error=? WHERE id=?",
                      (json.dumps(result, ensure_ascii=False, default=str), error, id))
        return self.get(id)


class Scheduler:
    """Fires due jobs. One pass per wake; one launch per job per pass, whatever the gap.

    `clock` and `sleep` are injected so the tests can run years of schedule in milliseconds.
    """

    def __init__(self, jobs: Jobs, launch: Callable[[dict[str, Any], dict[str, Any]], Awaitable[str | None]],
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.jobs = jobs
        self.launch = launch
        self.clock = clock
        self._sleep = sleep
        self.last_tick: float | None = None
        self.fires = 0

    async def _nap(self, seconds: float) -> None:
        await (self._sleep(seconds) if self._sleep is not None else asyncio.sleep(seconds))

    def plan(self, job: dict[str, Any], at: float) -> dict[str, Any]:
        """What this fire is for: the slot it belongs to, how late it is, how many slots it collapses.

        A one-off collapses nothing — it has a single instant, so the only question is how late we are to it.
        """
        planned = float(job["next_due_at"])
        if job["kind"] == "once":
            due_at = float(job["run_at"] if job["run_at"] is not None else planned)
            counted = 1
        else:
            slot = prev_fire(job["cron"], job["timezone"], at)
            # Never report a slot before the one we were waiting on, and never one in the future.
            due_at = planned if slot is None or slot < planned or slot > at else slot
            counted = slots_between(job["cron"], job["timezone"], planned, due_at) or 1
        late = max(0.0, at - due_at)
        return {"job_id": job["id"], "job": job["name"], "kind": job["kind"], "cron": job["cron"],
                "timezone": job["timezone"], "due_at": due_at, "fired_at": at, "late_seconds": round(late, 3),
                "missed_slots": counted - 1, "late": late > LATE_GRACE_S or counted > 1}

    async def tick(self) -> list[dict[str, Any]]:
        """One pass. Returns the fire record of every job launched, at most one per job."""
        at = self.clock()
        self.last_tick = at
        fired: list[dict[str, Any]] = []
        self.jobs.arm(at)
        for job in self.jobs.due(at):
            once = job["kind"] == "once"
            if not valid_schedule(job["kind"], job["cron"], job["run_at"]):
                bad = "has no time to run at" if once else f"'{job['cron']}' is not a cron expression this can read"
                self.jobs.mark_fired(job["id"], fired_at=at, due_at=float(job["next_due_at"]), next_due_at=None,
                                     error=bad, disable=once)
                log.warning("job %s %s; disarmed", job["name"], bad)
                continue
            fire = self.plan(job, at)
            # A one-off has no next slot, and retires rather than sitting enabled with nothing to wait for.
            nxt = None if once else next_fire(job["cron"], job["timezone"], at)
            # Advance the clock bookkeeping before launching: a launch that throws must not re-fire next pass.
            self.jobs.mark_fired(job["id"], fired_at=at, due_at=fire["due_at"], next_due_at=nxt, disable=once)
            run_id: str | None = None
            try:
                run_id = await self.launch(job, fire)
            except Exception as e:  # noqa: BLE001 - one bad job must not stop the others
                log.exception("job %s could not be launched", job["name"])
                self.jobs.mark_launched(job["id"], None, str(e))
            else:
                self.jobs.mark_launched(job["id"], run_id)
            self.fires += 1
            fired.append({**fire, "run_id": run_id})
        return fired

    async def loop(self) -> None:
        """Sleep to the next slot (capped), tick, repeat. An idle wake is one SELECT and no model call."""
        while True:
            try:
                nxt = self.jobs.earliest_due()
                gap = MAX_SLEEP_S if nxt is None else max(0.5, min(MAX_SLEEP_S, nxt - self.clock()))
                await self._nap(gap)
                await self.tick()
            except Exception as e:  # noqa: BLE001 - asyncio.CancelledError is BaseException, so shutdown still exits
                log.warning("job scheduler pass failed: %s", e, exc_info=True)
                await self._nap(MAX_SLEEP_S)
