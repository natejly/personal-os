"""Scheduled background work, and the proposals a background run leaves behind instead of acting.

Three pieces, all of them rows:

- `Jobs`     — the `jobs` table: a schedule, the prompt to run, and the two stamps the catch-up rule needs
               (`next_due_at`, the slot we are waiting for, and `last_due_at`, the slot the last launch was
               *for*). A schedule is one of two kinds, and past `next_due_at` nothing downstream cares which:
                 · `cron` — an expression read in a timezone, repeating forever.
                 · `once` — a single instant, `run_at`. "Tomorrow at 3pm, do this." It fires one time and then
                   switches itself off, so a spent one-off stays in the list as a record of what it did rather
                   than as a chore the user has to come back and delete.
                 · `watch` — a folder; new or touched files in it fire a run.
                 · `mail` — a Gmail search; a matching thread that is new or has a new message fires a run.
                   Its `next_due_at` is the next poll (every MAIL_POLL_S), not a slot, and never books an OS wake.
               Separately, `target` says what a fire starts: 'run' (a proposal-only chat run) or 'desk' (a desk with
               the prompt as its brief, in plan or propose autonomy; see app._launch_desk_job).
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
import contextlib
import itertools
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone as _utc
from typing import Any, Awaitable, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter

from .approval_edits import EDITABLE_TOOLS
from .db import Database, new_id, now, row_to_dict
from .google import GoogleNotConnected
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

PROPOSAL_STATUSES = ("pending", "accepted", "rejected", "expired")
# Skipped slots kept per job in job_skips; older ones are dropped as new ones arrive.
SKIP_KEEP = 200
KINDS = ("cron", "once", "watch", "mail")
# A mail job asks Gmail at most this often, and reads at most this many matching threads per look.
MAIL_POLL_S = 300.0
MAIL_MAX_THREADS = 20
# What a fire starts: a proposal-only chat run, or a desk. A desk job keeps any schedule kind; it only changes the
# target. A scheduled desk may plan first or propose at the end, never 'ask' (cards each change while nobody watches).
TARGETS = ("run", "desk")
DESK_JOB_AUTONOMY = ("plan", "propose")
# A directory trigger lists one folder (not its subfolders) and remembers at most this many entries.
WATCH_MAX_ENTRIES = 2000
WATCH_CHANGED_NAMES = 50  # names a directory fire hands the run; past this, only the count

# What a job run is launched with. The three the user asked for; seeded disabled, because an unattended
# run costs money and nobody opted in yet (the Agent Inbox offers the toggle).
SEED_JOBS: list[dict[str, Any]] = [
    {"name": "Morning brief", "cron": "30 7 * * *",
     "allowed_tools": ["current_time", "calendar_events", "calendar_get", "gmail_search", "gmail_read", "todo_list"],
     "prompt": "Give me my morning brief: check my calendar for today and tomorrow, scan unread email for anything "
               "that needs a reply, list my open todos and flag the overdue ones, and end with the three things I "
               "should do first. Be concise and use headers."},
    {"name": "Scan unread and draft replies", "cron": "0 9 * * 1-5",
     # gmail_draft is outward-facing, so a job run only ever proposes it; the allowlist just keeps it reachable.
     "allowed_tools": ["current_time", "gmail_search", "gmail_read", "gmail_draft", "search_memory", "writing_style"],
     "prompt": "Scan my unread inbox from the last two days. For each message that genuinely needs a reply from me, "
               "draft one: short, in my voice, and specific about what happens next. Skip newsletters, receipts and "
               "notifications. Finish with a one-line list of what you drafted and what you skipped."},
    {"name": "Weekly review", "cron": "0 17 * * 5",
     "allowed_tools": ["current_time", "todo_list", "calendar_events", "calendar_get", "search_memory"],
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
    # Exactly five fields: croniter also takes a sixth (seconds), which would mean one run per second.
    if len((expr or "").split()) != 5:
        return False
    try:
        # One real next slot, so a never-matching date like "0 0 30 2 *" is refused here and not
        # discovered later as an unhandled error when the job is enabled.
        croniter(expr, datetime.now(_utc.utc)).get_next(datetime)
        return True
    except (CroniterBadCronError, CroniterBadDateError, ValueError, KeyError, TypeError):
        return False


def next_fire(expr: str, tz: str, after: float) -> float:
    """The first slot strictly after `after`, as a unix timestamp. Read in `tz`, so DST moves with the wall clock."""
    it = croniter(expr, _local(after, tz))
    while True:
        cand = it.get_next(datetime)
        ts = float(cand.timestamp())
        # On the fall-back day a wall time happens twice. The second pass (fold=1) of a slot whose first
        # pass is already behind `after` is the same slot again, so skip it: one fire per wall-clock slot.
        wall = cand.replace(tzinfo=None)
        first = float(wall.replace(tzinfo=_zone(tz), fold=0).timestamp())
        if first != ts and first <= after:
            continue
        return ts


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
    if kind == "watch":
        return not cron or valid_cron(cron)  # a directory job may have no clock at all
    if kind == "mail":
        return not cron  # the search is its only trigger; next_due_at is the poll, not a slot
    return valid_cron(cron or "")


def check_watch_dir(raw: str | None) -> str:
    """The folder a directory job may watch: the local-file tools' guard (inside home, no dot-folders)."""
    from . import mac
    p = mac.allowed_path(raw or "")
    if not p.is_dir():
        raise mac.LocalPathError(f"{p} is not a folder")
    return str(p)


def _changed(names: list[str]) -> dict[str, Any]:
    """What a directory fire carries: how many entries changed, and the first WATCH_CHANGED_NAMES of their names."""
    return {"collapsed": len(names), "changed": names[:WATCH_CHANGED_NAMES]}


def scan_dir(path: str) -> dict[str, int]:
    """name -> mtime_ns of what sits directly in `path`. One listing, no native watcher; {} if unreadable.
    ponytail: top level only, capped; recurse or use fsevents if nested drops matter."""
    try:
        with os.scandir(path) as it:
            return {e.name: e.stat().st_mtime_ns for e in itertools.islice(it, WATCH_MAX_ENTRIES)
                    if not e.name.startswith(".")}
    except OSError:
        return {}


class PowerWake:
    """The real wake sink: asks the macOS power scheduler to wake the Mac at an instant. That needs root, which the
    app does not have, so the first refusal marks it `unavailable` for good and every later call is a no-op. Missed
    slots then catch up when the Mac next wakes (see Scheduler.nudge), and the inbox says jobs need the Mac awake."""

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self._run = runner
        self.unavailable = False

    def _pmset(self, *args: str) -> None:
        if self.unavailable:
            return
        try:
            done = self._run(["pmset", "schedule", *args], capture_output=True, timeout=10, check=False)
            ok = done.returncode == 0
        except Exception:  # noqa: BLE001 - never a scheduler fault
            ok = False
        if not ok:
            self.unavailable = True
            log.info("pmset schedule is not available here; jobs will run when the Mac is awake")

    @staticmethod
    def _stamp(at: float) -> str:
        return datetime.fromtimestamp(at).strftime("%m/%d/%y %H:%M:%S")

    def schedule(self, at: float) -> None:
        self._pmset("wake", self._stamp(at))

    def cancel(self, at: float) -> None:
        self._pmset("cancel", "wake", self._stamp(at))


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
    if job.get("kind") == "watch" and not job.get("cron"):
        return None  # a directory-only job has no clock slot; the folder is its trigger
    if job.get("kind") == "mail":
        return after  # poll as soon as it is armed; that first look only takes the baseline
    return next_fire(job["cron"], job["timezone"], after)


class Jobs:
    """CRUD over the `jobs` table. Every writer keeps `next_due_at` in step with the schedule and `enabled`."""

    FIELDS = ("name", "kind", "cron", "run_at", "timezone", "enabled", "prompt", "project_id", "max_retries",
              "allowed_tools", "watch_dir", "notify", "model", "budget", "mail_query", "target", "desk_autonomy",
              "desk_budget")
    # Changing any of these re-arms the job: a new schedule must not inherit the old one's pending slot.
    RE_ARM = frozenset({"kind", "cron", "run_at", "timezone", "enabled", "mail_query"})
    # Taking a baseline listing when these change is what makes "idle until a file appears" true.
    WATCH_FIELDS = frozenset({"watch_dir", "enabled", "kind"})
    # Same for a mail search: the next look is a fresh baseline, so mail already there never fires.
    MAIL_FIELDS = frozenset({"mail_query", "enabled", "kind"})

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(r: Any) -> dict[str, Any] | None:
        d = row_to_dict(r)
        if d is not None:
            d.pop("watch_seen", None)  # the folder listing is bookkeeping, not part of the job
            d.pop("mail_seen", None)  # so is the thread baseline
            d["enabled"] = bool(d["enabled"])
            # NULL = inherit every tool (what every job did before this column); otherwise a JSON list of names.
            raw = d.get("allowed_tools")
            try:
                d["allowed_tools"] = json.loads(raw) if raw else None
            except ValueError:
                d["allowed_tools"] = None
            # NULL = JOB_BUDGET as is; otherwise a JSON object of the caps this job tightens.
            try:
                d["budget"] = json.loads(d["budget"]) if d.get("budget") else None
            except ValueError:
                d["budget"] = None
            try:
                d["desk_budget"] = json.loads(d["desk_budget"]) if d.get("desk_budget") else None
            except ValueError:
                d["desk_budget"] = None
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
               at: float | None = None, max_retries: int = 1, allowed_tools: list[str] | None = None,
               watch_dir: str | None = None, notify: str = "problems", model: str | None = None,
               budget: dict[str, Any] | None = None, mail_query: str | None = None, target: str = "run",
               desk_autonomy: str | None = None, desk_budget: dict[str, Any] | None = None) -> dict[str, Any]:
        tz = timezone or local_tz_name()
        t = at if at is not None else now()
        jid = new_id()
        kind = kind if kind in KINDS else "cron"
        target = target if target in TARGETS else "run"
        cron = "" if kind in ("once", "mail") else cron
        fresh = {"kind": kind, "cron": cron, "run_at": run_at, "timezone": tz, "last_due_at": None}
        nxt = next_due_for(fresh, t) if enabled else None
        with self.db.tx() as c:
            c.execute("INSERT INTO jobs(id, name, kind, cron, run_at, timezone, enabled, prompt, project_id, next_due_at, "
                      "created_at, updated_at, max_retries, allowed_tools, watch_dir, watch_seen, notify, model, budget, "
                      "mail_query, target, desk_autonomy, desk_budget) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (jid, name, kind, cron, run_at, tz, int(enabled), prompt, project_id, nxt, t, t, int(max_retries),
                       None if allowed_tools is None else json.dumps(list(allowed_tools)), watch_dir,
                       json.dumps(scan_dir(watch_dir)) if watch_dir else None, notify, model,
                       json.dumps(budget) if budget else None, mail_query, target,
                       (desk_autonomy or "plan") if target == "desk" else desk_autonomy,
                       json.dumps(desk_budget) if desk_budget else None))
        return self.get(jid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any], at: float | None = None) -> dict[str, Any] | None:
        job = self.get(id)
        if not job:
            return None
        t = at if at is not None else now()
        cols = {k: v for k, v in patch.items() if k in self.FIELDS}
        if "enabled" in cols:
            cols["enabled"] = int(bool(cols["enabled"]))
        if cols.get("kind") in ("once", "mail"):
            cols["cron"] = ""
        if "allowed_tools" in cols:
            cols["allowed_tools"] = None if cols["allowed_tools"] is None else json.dumps(list(cols["allowed_tools"]))
        if "budget" in cols:
            cols["budget"] = json.dumps(cols["budget"]) if cols["budget"] else None
        if "model" in cols:
            cols["model"] = cols["model"] or None
        if "desk_budget" in cols:
            cols["desk_budget"] = json.dumps(cols["desk_budget"]) if cols["desk_budget"] else None
        if "enabled" in cols:
            # Any explicit switch is the user acknowledging an auto-pause: the reason and the streak start over.
            cols["paused_reason"] = None
            if cols["enabled"] != job["enabled"]:
                cols["expires_at"] = None  # a real switch restarts the expiry window; re-sending the same state does not
            if cols["enabled"]:
                cols["consecutive_failures"] = 0
        merged = {**job, **cols}
        # Re-arm from now whenever the schedule or the switch changes: a job disabled across a slot has no
        # missed slot to catch up on, and a new cron expression must not inherit the old one's pending slot.
        # A one-off given a new run_at stops being spent, so this is also how a fired task is rescheduled.
        if self.RE_ARM & cols.keys():
            cols["next_due_at"] = next_due_for(merged, t) if merged["enabled"] else None
        if not cols:
            return job
        if self.WATCH_FIELDS & cols.keys() and merged.get("watch_dir"):
            cols["watch_seen"] = json.dumps(scan_dir(merged["watch_dir"]))
        if self.MAIL_FIELDS & cols.keys():
            cols["mail_seen"] = None
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
        """The next slot worth waking for. A mail poll is not one: the capped loop sleep already reaches it, and
        waking a sleeping Mac every few minutes to read mail would be the heartbeat this scheduler is not."""
        with self.db.tx() as c:
            r = c.execute("SELECT MIN(next_due_at) AS t FROM jobs WHERE enabled=1 AND next_due_at IS NOT NULL "
                          "AND kind<>'mail'").fetchone()
        return float(r["t"]) if r and r["t"] is not None else None

    def watching(self) -> list[dict[str, Any]]:
        """Enabled directory jobs."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM jobs WHERE enabled=1 AND kind='watch' AND watch_dir IS NOT NULL "
                             "AND watch_dir<>''").fetchall()
        return [d for d in (self._row(r) for r in rows) if d]

    def poll_dir(self, job: dict[str, Any]) -> list[str]:
        """The entries new or touched in the job's folder since the last look, and remember this look.
        The first look (no baseline yet) only records one."""
        try:
            check_watch_dir(job["watch_dir"])
        except Exception:  # noqa: BLE001 - a folder that left the guard (moved, now hidden) just goes quiet
            return []
        new = scan_dir(job["watch_dir"])
        with self.db.tx() as c:
            r = c.execute("SELECT watch_seen FROM jobs WHERE id=?", (job["id"],)).fetchone()
        raw = r["watch_seen"] if r else None
        old = json.loads(raw) if raw else None
        if old is None or new != old:
            with self.db.tx() as c:
                c.execute("UPDATE jobs SET watch_seen=? WHERE id=?", (json.dumps(new), job["id"]))
        return [] if old is None else sorted(k for k, v in new.items() if old.get(k) != v)

    def poll_mail(self, job: dict[str, Any], threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The threads that are new, or whose newest message changed, since the last look, and remember this
        look. The first look (no baseline yet) only records one. A thread whose newest message is the user's
        own (SENT) is not news."""
        new = {t["thread_id"]: (t.get("messages") or [{}])[-1].get("id") for t in threads if t.get("thread_id")}
        with self.db.tx() as c:
            r = c.execute("SELECT mail_seen FROM jobs WHERE id=?", (job["id"],)).fetchone()
            raw = r["mail_seen"] if r else None
            old = json.loads(raw) if raw else None
            # Merged, so a thread that drops out of the newest N and comes back unchanged is not news again.
            # ponytail: grows by every thread the search ever matched; prune if a broad search makes it large.
            c.execute("UPDATE jobs SET mail_seen=? WHERE id=?", (json.dumps({**(old or {}), **new}), job["id"]))
        if old is None:
            return []
        return [t for t in threads if t.get("thread_id") in new and old.get(t["thread_id"]) != new[t["thread_id"]]
                and "SENT" not in ((t.get("messages") or [{}])[-1].get("labels") or [])]

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

    def record_outcome(self, id: str, ok: bool) -> int:
        """One finished fire: a success clears the failure streak, a failure extends it. Returns the streak."""
        with self.db.tx() as c:
            if ok:
                c.execute("UPDATE jobs SET consecutive_failures=0 WHERE id=?", (id,))
            else:
                c.execute("UPDATE jobs SET consecutive_failures=consecutive_failures+1 WHERE id=?", (id,))
            r = c.execute("SELECT consecutive_failures AS n FROM jobs WHERE id=?", (id,)).fetchone()
        return int(r["n"]) if r else 0

    def record_skip(self, id: str, reason: str, at: float | None = None, due_at: float | None = None) -> None:
        """The latest skip on the job row (the list chip) and one history row, keeping the last SKIP_KEEP."""
        t = at if at is not None else now()
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET last_skip_at=?, last_skip_reason=? WHERE id=?", (t, reason, id))
            c.execute("INSERT INTO job_skips(job_id, due_at, reason, at) VALUES(?,?,?,?)", (id, due_at, reason, t))
            c.execute("DELETE FROM job_skips WHERE job_id=? AND id NOT IN "
                      "(SELECT id FROM job_skips WHERE job_id=? ORDER BY at DESC, id DESC LIMIT ?)", (id, id, SKIP_KEEP))

    def skips(self, id: str, limit: int = 50, since: float | None = None) -> list[dict[str, Any]]:
        """Newest first."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, due_at, reason, at FROM job_skips WHERE job_id=? AND at>=? ORDER BY at DESC, id DESC LIMIT ?",
                             (id, since if since is not None else 0.0, max(1, min(int(limit), SKIP_KEEP)))).fetchall()
        return [dict(r) for r in rows]

    def pause(self, id: str, reason: str, at: float | None = None) -> None:
        """Switch a job off and say why. Not `update`: that clears the reason, which is the user's acknowledgement."""
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET enabled=0, next_due_at=NULL, paused_reason=?, updated_at=? WHERE id=?",
                      (reason, at if at is not None else now(), id))

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
                self.create(s["name"], s["cron"], s["prompt"], enabled=False, at=at, allowed_tools=s.get("allowed_tools"))
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
            d["editable"] = d["tool"] in EDITABLE_TOOLS  # the inbox offers an edit only where accept would take one
        return d

    def create(self, *, run_id: str | None, tool: str, args: dict[str, Any], job_id: str | None = None,
               conversation_id: str | None = None, message_id: str | None = None, call_id: str | None = None,
               at: float | None = None, scope: str | None = None) -> dict[str, Any]:
        """`scope` (a card or conversation id) makes the insert idempotent: the same scope + tool + args digest
        returns the first row instead of adding a second. Without it every call is a new proposal."""
        pid = new_id()
        t = at if at is not None else now()
        digest = args_digest(args)
        key = f"{scope}:{tool}:{digest}" if scope else None
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO proposals(id, run_id, job_id, conversation_id, message_id, call_id, tool, args, args_digest, "
                      "status, created_at, idem_key) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)",
                      (pid, run_id, job_id, conversation_id, message_id, call_id, tool,
                       json.dumps(args, ensure_ascii=False, default=str), digest, t, key))
            if key:
                pid = c.execute("SELECT id FROM proposals WHERE idem_key=?", (key,)).fetchone()["id"]
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

    def reject_job(self, job_id: str, note: str | None = None, at: float | None = None) -> int:
        """Reject every pending proposal of one job. `note` says why, in `error` (rejected rows have no result)."""
        with self.db.tx() as c:
            return c.execute("UPDATE proposals SET status='rejected', decided_at=?, error=? WHERE job_id=? AND status='pending'",
                             (at if at is not None else now(), note, job_id)).rowcount

    def expire(self, before: float, at: float | None = None) -> int:
        """Pending proposals created before `before` stop being actionable. Accepting one is then a 409."""
        with self.db.tx() as c:
            return c.execute("UPDATE proposals SET status='expired', decided_at=? WHERE status='pending' AND created_at<?",
                             (at if at is not None else now(), before)).rowcount

    def reopen(self, id: str) -> dict[str, Any] | None:
        """Put an accepted proposal back to 'pending', for an accept whose call failed before writing anything."""
        with self.db.tx() as c:
            c.execute("UPDATE proposals SET status='pending', decided_at=NULL WHERE id=? AND status='accepted'", (id,))
        return self.get(id)

    def record(self, id: str, result: Any, error: str | None = None) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE proposals SET result=?, error=? WHERE id=?",
                      (json.dumps(result, ensure_ascii=False, default=str), error, id))
        return self.get(id)


def _expire_days(policy: Any, key: str = "jobExpireDays") -> float:
    """`jobExpireDays` / `proposalExpireDays` (0 = never)."""
    try:
        return max(0.0, float(policy.settings().get(key) or 0)) if policy is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


class Scheduler:
    """Fires due jobs. One pass per wake; one launch per job per pass, whatever the gap.

    `clock` and `sleep` are injected so the tests can run years of schedule in milliseconds.
    """

    def __init__(self, jobs: Jobs, launch: Callable[[dict[str, Any], dict[str, Any]], Awaitable[str | None]],
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] | None = None, policy: Any = None, wake: Any = None,
                 mail: Callable[[str], list[dict[str, Any]]] | None = None) -> None:
        self.jobs = jobs
        self.launch = launch
        # Optional run policy (jobs_policy.JobPolicy): the overlap guard before a launch, the watcher after it.
        self.policy = policy
        self.clock = clock
        self._sleep = sleep
        # Optional OS wake sink with schedule(at) / cancel(at): asks the machine to be awake for the next slot.
        self.wake = wake
        # Blocking Gmail search -> thread metadata (Google.gmail_threads_recent shape), run off the event loop.
        # It raises when Google is not connected. None = mail jobs never fire.
        self.mail = mail
        self._woken: float | None = None
        # Set by nudge() (the Mac woke or unlocked): cuts the current nap short so a missed slot fires now.
        self._poke = asyncio.Event()
        self.last_tick: float | None = None
        self.fires = 0

    async def _nap(self, seconds: float) -> None:
        if self._sleep is not None:
            await self._sleep(seconds)
        else:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._poke.wait(), seconds)
        self._poke.clear()

    def nudge(self) -> None:
        """Run the next pass now instead of at the end of the nap. On the event loop."""
        self._poke.set()

    @property
    def wake_unavailable(self) -> bool:
        return bool(getattr(self.wake, "unavailable", False))

    def sync_wake(self) -> None:
        """Keep one OS wake booked for the earliest due slot: none when nothing is due, and an unchanged instant
        books nothing new. This only asks the machine to be awake; `tick` is still the one launcher."""
        if self.wake is None:
            return
        want = self.jobs.earliest_due()
        if want == self._woken:
            return
        try:
            if self._woken is not None:
                self.wake.cancel(self._woken)
            if want is not None:
                self.wake.schedule(want)
        except Exception:  # noqa: BLE001 - a wake that cannot be booked must not stop the scheduler
            log.debug("OS wake could not be updated", exc_info=True)
        self._woken = want

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
        days = _expire_days(self.policy)
        if days > 0:  # start the clock for recurring jobs that have none yet
            with self.jobs.db.tx() as c:
                c.execute("UPDATE jobs SET expires_at=? WHERE enabled=1 AND kind!='once' AND expires_at IS NULL",
                          (at + days * 86400,))
        if (pdays := _expire_days(self.policy, "proposalExpireDays")) > 0:
            Proposals(self.jobs.db).expire(at - pdays * 86400, at)
        due = self.jobs.due(at)
        mail_due = [j for j in due if j["kind"] == "mail"]
        due = [j for j in due if j["kind"] != "mail"]
        # Directory jobs: one listing each. A job that is also clock-due is still one launch this tick.
        seen = {j["id"] for j in due}
        hits = {j["id"]: n for j in self.jobs.watching() if (n := self.jobs.poll_dir(j))}
        due += [j for j in self.jobs.watching() if j["id"] in hits and j["id"] not in seen]
        for job in due:
            once = job["kind"] == "once"
            if job["next_due_at"] is None:  # directory-only: fires on the folder, leaves any clock slot alone
                fire = {"job_id": job["id"], "job": job["name"], "kind": job["kind"], "cron": job["cron"],
                        "timezone": job["timezone"], "due_at": at, "fired_at": at, "late_seconds": 0.0,
                        "missed_slots": 0, "late": False}
                self.jobs.mark_fired(job["id"], fired_at=at, due_at=at, next_due_at=None)
                await self._start(job, {**fire, "trigger": "dir", **_changed(hits[job["id"]])}, fired)
                continue
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
            if job["id"] in hits:
                fire = {**fire, "trigger": "clock+dir", **_changed(hits[job["id"]])}
            if days > 0 and job.get("expires_at") is not None and at >= float(job["expires_at"]):
                # One last fire (this one), then the job stops until the user switches it back on.
                self.jobs.pause(job["id"], "expired", at)
            await self._start(job, fire, fired)
        for job in mail_due:
            threads = await self._poll_mail(job, at)
            if not threads:
                continue
            fire = {"job_id": job["id"], "job": job["name"], "kind": "mail", "cron": "", "timezone": job["timezone"],
                    "due_at": at, "fired_at": at, "late_seconds": 0.0, "missed_slots": 0, "late": False,
                    "trigger": "mail", "collapsed": len(threads),
                    "mail": [{"thread_id": t["thread_id"], "subject": t.get("subject") or "",
                              "from": ((t.get("messages") or [{}])[-1].get("from") or "")} for t in threads]}
            self.jobs.mark_fired(job["id"], fired_at=at, due_at=at, next_due_at=at + MAIL_POLL_S)
            if days > 0 and job.get("expires_at") is not None and at >= float(job["expires_at"]):
                self.jobs.pause(job["id"], "expired", at)
            await self._start(job, fire, fired)
        self.sync_wake()
        return fired

    async def _poll_mail(self, job: dict[str, Any], at: float) -> list[dict[str, Any]]:
        """One Gmail look for a mail job (the next is booked either way). Fails closed: no Google, no fire."""
        with self.jobs.db.tx() as c:
            c.execute("UPDATE jobs SET next_due_at=? WHERE id=?", (at + MAIL_POLL_S, job["id"]))
        if self.mail is None or not (job.get("mail_query") or "").strip():
            return []
        try:
            threads = await asyncio.to_thread(self.mail, job["mail_query"])
        except Exception as e:  # noqa: BLE001 - a failed look skips this pass, it never fires blind
            log.info("mail job %s skipped: %s", job["name"], e)
            self.jobs.record_skip(job["id"], "google_disconnected" if isinstance(e, GoogleNotConnected)
                                  else "mail_unreachable", at)
            return []
        return self.jobs.poll_mail(job, threads)

    async def _start(self, job: dict[str, Any], fire: dict[str, Any], fired: list[dict[str, Any]]) -> None:
        """Admit and launch one fire, after its clock bookkeeping is already safe."""
        run_id: str | None = None
        if self.policy is not None:
            ok, why = await self.policy.admit(job, fire)
            if not ok:
                # The slot is consumed (mark_fired ran): a long run must not buy a catch-up when it ends.
                self.fires += 1
                fired.append({**fire, "run_id": None, "skipped": why})
                return
        try:
            run_id = await self.launch(job, fire)
        except Exception as e:  # noqa: BLE001 - one bad job must not stop the others
            log.exception("job %s could not be launched", job["name"])
            self.jobs.mark_launched(job["id"], None, str(e))
        else:
            self.jobs.mark_launched(job["id"], run_id)
            if self.policy is not None and run_id:
                self.policy.watch_soon(job, fire, run_id)
        self.fires += 1
        fired.append({**fire, "run_id": run_id})

    async def loop(self) -> None:
        """Sleep to the next slot (capped), tick, repeat. An idle wake is one SELECT and no model call."""
        while True:
            try:
                self.sync_wake()
                nxt = self.jobs.earliest_due()
                gap = MAX_SLEEP_S if nxt is None else max(0.5, min(MAX_SLEEP_S, nxt - self.clock()))
                await self._nap(gap)
                await self.tick()
            except Exception as e:  # noqa: BLE001 - asyncio.CancelledError is BaseException, so shutdown still exits
                log.warning("job scheduler pass failed: %s", e, exc_info=True)
                await self._nap(MAX_SLEEP_S)
