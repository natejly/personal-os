"""Chat runs as background tasks, one event topic per conversation.

A run's lifetime belongs to its task alone: nothing here consults the subscriber set. Zero
subscribers is a normal state — publish writes to the ring and to no queues, the reply completes
and is persisted, and it waits in the ring for the next window to attach.

A run is also a row. Every published event is written through to SQLite (agent_runs + run_events, see
RunStore) before it reaches the ring, so the table is the source of truth and the ring is the hot cache:
a client that reconnects after a backend restart replays its tail from the table with ?since=.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable

from .db import Database, new_id

log = logging.getLogger("personal_os")

# Every streamed delta is an event, so a ring has to hold a whole reply: it is the only thing a
# reconnecting or late-launching pop-out can replay from. QUEUE_MAX < RING is the invariant that
# lets a subscriber that overflowed reconnect from its last seq without a gap.
RING = 2000
QUEUE_MAX = 1000
KEEPALIVE_S = 15.0
# How long a finished run stays in memory, for a window that opens just after it ended. Past that the
# stream is served from run_events instead.
RETAIN_S = 300.0
# A job keeps this many runs; the next insert drops the oldest finished ones.
JOB_RUNS_KEEP = 20
# How long a finished run keeps its event tape. The agent_runs row, approvals and executed_calls stay.
EVENTS_RETAIN_S = 14 * 86400

ACTIVE = ("running", "awaiting_approval")
STATUSES = ("running", "awaiting_approval", "done", "error", "interrupted")

# The app topic carries whole events, not deltas, so its ring holds plenty of history in few slots.
TOPIC_RING = 200

RunEvent = tuple[int, str, Any]


def sse(event: str, data: Any, seq: int | None = None) -> str:
    """One SSE block. `id:` carries the seq, so a client knows what to pass as ?since= when it reconnects."""
    head = f"id: {seq}\n" if seq is not None else ""
    try:
        body = json.dumps(data, allow_nan=False)
    except ValueError:  # NaN / Infinity are not JSON: a browser's JSON.parse would throw and the stream would stall
        body = json.dumps(_finite(data), allow_nan=False)
    return f"{head}event: {event}\ndata: {body}\n\n"


def _finite(v: Any) -> Any:
    """A copy of `v` with every non-finite float turned into its string form."""
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# The idempotency step number a promotion is taped under. Round numbers are round*1000 + index in
# round, so negatives are free for the writes that happen outside the round loop. A promotion has to
# be taped: accepting a deliverable twice would create the doc twice.
PROMOTE_STEP = -2


def args_digest(args: Any) -> str:
    """Canonical digest of a tool call's arguments: key order and whitespace do not matter."""
    canon = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def idempotency_key(run_id: str, step: int, tool: str, digest: str) -> str:
    return hashlib.sha256(f"{run_id}\x1f{step}\x1f{tool}\x1f{digest}".encode()).hexdigest()


def unknown_outcome(tool: str, key: str) -> dict[str, Any]:
    """What a replay gets for a call that started but never recorded an outcome (the process died mid-call)."""
    return {"error": f"{tool} was already started for this exact request, but its outcome was never recorded "
                     "(the backend stopped mid-call). It was not run again, so it cannot happen twice. Do not retry it; "
                     "tell the user to check whether it went through.",
            "unknown_outcome": True, "idempotency_key": key}


class RunStore:
    """The durable side of runs: agent_runs, run_events, approvals and executed_calls.

    One long-lived connection behind a lock: publish() appends one row per event, on the event loop, so a
    connect per write would be the expensive part. synchronous=NORMAL is safe in WAL mode against an app
    crash; only a power loss can drop the last few events.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._c = db.connect()
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            try:
                cur = self._c.execute(sql, tuple(params))
                self._c.commit()
                return cur.rowcount
            except Exception:
                self._c.rollback()
                raise

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._c.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _run_row(r: dict[str, Any] | None) -> dict[str, Any] | None:
        if r is None:
            return None
        for f in ("input", "budget"):
            if isinstance(r.get(f), str):
                try:
                    r[f] = json.loads(r[f])
                except ValueError:
                    pass
        return r

    # ---- runs ----
    def create(self, run_id: str, conversation_id: str | None, kind: str = "chat", input: dict[str, Any] | None = None,
               desk_id: str | None = None, turn: int = 0, parent_run_id: str | None = None) -> None:
        t = time.time()
        self._exec("INSERT INTO agent_runs(run_id, conversation_id, kind, desk_id, turn, status, input, started_at, updated_at, parent_run_id) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (run_id, conversation_id, kind, desk_id, turn, "running", _dumps(input or {}), t, t, parent_run_id))
        if kind == "job" and (input or {}).get("job_id"):
            self.prune_job((input or {})["job_id"])

    def prune_job(self, job_id: str, keep: int = JOB_RUNS_KEEP) -> None:
        """A job's history is its newest `keep` runs: older finished ones go, with their event tapes."""
        old = ("SELECT run_id FROM agent_runs WHERE kind='job' AND json_extract(input,'$.job_id')=? AND ended_at IS NOT NULL "
               "AND run_id NOT IN (SELECT run_id FROM agent_runs WHERE kind='job' AND json_extract(input,'$.job_id')=? "
               "ORDER BY started_at DESC, rowid DESC LIMIT ?)")
        self._exec(f"DELETE FROM run_events WHERE run_id IN ({old})", (job_id, job_id, keep))
        self._exec(f"DELETE FROM agent_runs WHERE run_id IN ({old})", (job_id, job_id, keep))

    def children(self, run_id: str) -> list[dict[str, Any]]:
        """Runs started by `run_id` (subagents), oldest first."""
        return [self._run_row(r) for r in self._all("SELECT * FROM agent_runs WHERE parent_run_id=? ORDER BY started_at, rowid", (run_id,))]  # type: ignore[misc]

    def update(self, run_id: str, **fields: Any) -> None:
        """Set any of status, message_id, budget, error, last_seq, ended_at, resumed_from. Never raises: the tape must not kill a run."""
        allowed = {"status", "message_id", "budget", "error", "last_seq", "ended_at", "resumed_from"}
        cols = {k: (_dumps(v) if k == "budget" and v is not None else v) for k, v in fields.items() if k in allowed}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        try:
            self._exec(f"UPDATE agent_runs SET {sets}, updated_at=? WHERE run_id=?", (*cols.values(), time.time(), run_id))
        except sqlite3.Error:
            log.warning("could not update run %s", run_id, exc_info=True)

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._run_row(self._one("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)))

    def mark_unchanged(self, run_id: str) -> None:
        """Flag a job run whose result equals the previous run's (input.unchanged): the inbox and notifications skip it."""
        try:
            self._exec("UPDATE agent_runs SET input=json_set(input,'$.unchanged',json('true')) WHERE run_id=?", (run_id,))
        except sqlite3.Error:
            log.warning("could not flag run %s unchanged", run_id, exc_info=True)

    def set_resumed_from(self, run_id: str, prior: str) -> None:
        self.update(run_id, resumed_from=prior)

    def resumed_by(self, run_id: str) -> str | None:
        """The run that continued this one, if any (a run is resumed at most once)."""
        r = self._one("SELECT run_id FROM agent_runs WHERE resumed_from=? LIMIT 1", (run_id,))
        return r["run_id"] if r else None

    def latest(self, conversation_id: str) -> dict[str, Any] | None:
        return self._run_row(self._one("SELECT * FROM agent_runs WHERE conversation_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                                       (conversation_id,)))

    def list(self, statuses: Iterable[str] | None = ACTIVE, conversation_id: str | None = None, limit: int = 50,
             desk_id: str | None = None) -> list[dict[str, Any]]:
        where, params = [], []
        if statuses is not None:
            st = list(statuses)
            where.append(f"status IN ({','.join('?' * len(st))})")
            params += st
        if conversation_id:
            where.append("conversation_id=?")
            params.append(conversation_id)
        if desk_id:
            where.append("desk_id=?")
            params.append(desk_id)
        sql = "SELECT * FROM agent_runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY started_at DESC LIMIT ?"
        return [r for r in (self._run_row(x) for x in self._all(sql, (*params, max(1, min(int(limit), 500))))) if r]

    def of_kind(self, kind: str, since: float = 0.0, limit: int = 50) -> list[dict[str, Any]]:
        """Runs of one kind (e.g. 'job'), newest first. What the Agent Inbox's history is built from."""
        rows = self._all("SELECT * FROM agent_runs WHERE kind=? AND started_at>=? ORDER BY started_at DESC LIMIT ?",
                         (kind, since, max(1, min(int(limit), 500))))
        return [r for r in (self._run_row(x) for x in rows) if r]

    def of_job(self, job_id: str, limit: int = 50, since: float = 0.0) -> list[dict[str, Any]]:
        """One job's runs, newest first. Reads agent_runs.input.job_id; no table of its own, no retention change."""
        rows = self._all("SELECT * FROM agent_runs WHERE kind='job' AND json_extract(input,'$.job_id')=? AND started_at>=? "
                         "ORDER BY started_at DESC, rowid DESC LIMIT ?", (job_id, since, max(1, min(int(limit), 200))))
        return [r for r in (self._run_row(x) for x in rows) if r]

    def seen(self, run_ids: Iterable[str]) -> set[str]:
        """Which of `run_ids` the user has marked read in the Agent Inbox."""
        ids = list(run_ids)
        if not ids:
            return set()
        return {r["run_id"] for r in self._all(f"SELECT run_id FROM inbox_seen WHERE run_id IN ({','.join('?' * len(ids))})", ids)}

    def mark_seen(self, run_ids: Iterable[str]) -> None:
        t = time.time()
        with self._lock:
            try:
                self._c.executemany("INSERT OR IGNORE INTO inbox_seen(run_id, seen_at) VALUES(?,?)", [(r, t) for r in run_ids])
                self._c.commit()
            except Exception:
                self._c.rollback()
                raise

    # ---- events ----
    def append(self, run_id: str, seq: int, event: str, data: Any) -> bool:
        try:
            self._exec("INSERT INTO run_events(run_id, seq, type, data, ts) VALUES(?,?,?,?,?)", (run_id, seq, event, _dumps(data), time.time()))
            return True
        except sqlite3.Error:  # e.g. the conversation (and so the run row) was deleted mid-run
            log.warning("could not persist event %s#%s of run %s", event, seq, run_id, exc_info=True)
            return False

    def events(self, run_id: str, since: int = 0, until: int | None = None) -> list[RunEvent]:
        sql, params = "SELECT seq, type, data FROM run_events WHERE run_id=? AND seq>?", [run_id, since]
        if until is not None:
            sql += " AND seq<?"
            params.append(until)
        return [(r["seq"], r["type"], json.loads(r["data"])) for r in self._all(sql + " ORDER BY seq", params)]

    def event_counts(self, run_id: str) -> dict[str, int]:
        """{event type: rows} for one run. The inbox counts tool calls and errors from this, never from prose."""
        return {r["type"]: int(r["n"]) for r in
                self._all("SELECT type, COUNT(*) AS n FROM run_events WHERE run_id=? GROUP BY type", (run_id,))}

    def last_seq(self, run_id: str) -> int:
        r = self._one("SELECT MAX(seq) AS s FROM run_events WHERE run_id=?", (run_id,))
        return int((r or {}).get("s") or 0)

    async def tail(self, run_id: str, since: int = 0) -> AsyncIterator[str]:
        """The stored tape of a run that is no longer in memory, as SSE. It cannot grow, so this ends."""
        for seq, event, data in self.events(run_id, since):
            yield sse(event, data, seq)

    # ---- approvals ----
    def open_approval(self, call_id: str, run_id: str | None, tool: str, args: dict[str, Any], *, conversation_id: str | None = None,
                      message_id: str | None = None, forced: bool = False, desk_id: str | None = None,
                      danger: str = "external") -> dict[str, Any]:
        self._exec("INSERT INTO approvals(call_id, run_id, conversation_id, message_id, tool, args, args_digest, forced, danger, desk_id, status, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?) ON CONFLICT(call_id) DO NOTHING",
                   (call_id, run_id, conversation_id, message_id, tool, _dumps(args), args_digest(args), int(forced),
                    danger, desk_id, time.time()))
        return self.approval(call_id) or {}

    def decide(self, call_id: str, decision: str, by: str = "user", note: str | None = None,
               edited_args: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """First decision wins. None if there is no such approval or it was already decided.

        `note` is what the user said with the answer (a denial's reason, a desk_ask's answer).
        `edited_args` is a human's rewrite (validated by approval_edits before it gets here). It is written in the
        same UPDATE as the decision, so a run woken by the decision always sees it, and `args_digest` is re-bound to
        it: the row then states the digest of what actually runs. The original stays in `args`. A deny never edits."""
        status = "denied" if decision == "deny" else "approved"
        note = (note or "").strip()[:4000] or None
        if edited_args is not None and status == "approved":
            n = self._exec("UPDATE approvals SET status=?, decision=?, decided_by=?, decided_at=?, note=?, edited_args=?, edited_by='user', "
                           "args_digest=? WHERE call_id=? AND status='pending'",
                           (status, decision, by, time.time(), note, _dumps(edited_args), args_digest(edited_args), call_id))
        else:
            n = self._exec("UPDATE approvals SET status=?, decision=?, decided_by=?, decided_at=?, note=? WHERE call_id=? AND status='pending'",
                           (status, decision, by, time.time(), note, call_id))
        return self.approval(call_id) if n else None

    def approval(self, call_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM approvals WHERE call_id=?", (call_id,))
        if r:
            r["args"] = json.loads(r["args"])
            r["forced"] = bool(r["forced"])
            r["edited_args"] = json.loads(r["edited_args"]) if r.get("edited_args") else None
        return r

    def park(self, call_id: str) -> None:
        """Leave the row pending but mark the waiting run as having let go of it.

        The status stays 'pending' on purpose: the card must still be decidable tomorrow, from
        another window, after a restart. `decided_by='park'` is how a reader tells that no run is
        blocked on it any more, and a later decide() overwrites it with the real decider.
        """
        self._exec("UPDATE approvals SET decided_by='park', parked_at=? WHERE call_id=? AND status='pending'",
                   (time.time(), call_id))

    def unreported(self, desk_id: str) -> list[dict[str, Any]]:
        """Parked cards of this desk the user has since answered, which no turn has been told about.

        A parked card's run is gone, so its answer has nowhere to land but the desk's next turn.
        Read and marked in one step (`mark_reported`) by the turn that injects them, oldest first.
        """
        rows = self._all("SELECT call_id FROM approvals WHERE desk_id=? AND parked_at IS NOT NULL "
                         "AND status<>'pending' AND reported_at IS NULL ORDER BY decided_at", (desk_id,))
        return [a for a in (self.approval(r["call_id"]) for r in rows) if a]

    def mark_reported(self, call_ids: Iterable[str]) -> None:
        ids = list(call_ids)
        if ids:
            self._exec(f"UPDATE approvals SET reported_at=? WHERE call_id IN ({','.join('?' * len(ids))})",
                       (time.time(), *ids))

    def claim_parked(self, desk_id: str, tool: str, args: dict[str, Any], call_id: str) -> dict[str, Any] | None:
        """Spend the one-shot grant an approved parked card left behind, or None.

        The user said yes to exactly this call after its run had let go of it; the desk's next turn
        makes the call again and it runs without a second card. Single use, bound to the desk, the
        tool and the argument digest, so a different call - or the same one twice - still asks.
        """
        r = self._one("SELECT call_id FROM approvals WHERE desk_id=? AND tool=? AND args_digest=? AND status='approved' "
                      "AND parked_at IS NOT NULL AND claimed_by IS NULL ORDER BY decided_at LIMIT 1",
                      (desk_id, tool, args_digest(args)))
        if r is None:
            return None
        n = self._exec("UPDATE approvals SET claimed_by=? WHERE call_id=? AND claimed_by IS NULL", (call_id, r["call_id"]))
        return self.approval(r["call_id"]) if n else None

    def approvals(self, status: str | None = "pending", run_id: str | None = None, limit: int = 100,
                  desk_id: str | None = None, newest_first: bool = False) -> list[dict[str, Any]]:
        where, params = [], []
        if status == "decided":
            where.append("status<>'pending'")
        elif status:
            where.append("status=?")
            params.append(status)
        if run_id:
            where.append("run_id=?")
            params.append(run_id)
        if desk_id:
            where.append("desk_id=?")
            params.append(desk_id)
        sql = "SELECT call_id FROM approvals" + (" WHERE " + " AND ".join(where) if where else "") + (
            " ORDER BY COALESCE(decided_at, created_at) DESC" if newest_first else " ORDER BY created_at") + " LIMIT ?"
        return [a for a in (self.approval(r["call_id"]) for r in self._all(sql, (*params, max(1, min(int(limit), 500))))) if a]

    # ---- idempotency ----
    async def call_once(self, run_id: str | None, step: int, tool: str, args: dict[str, Any],
                        fn: Callable[[], Awaitable[Any]], call_id: str | None = None,
                        inherit: str | None = None) -> tuple[Any, bool]:
        """Run fn at most once per (run_id, step, tool, args). Returns (result, replayed).

        `inherit` is the run this one resumes: an identical call (any step) that run already finished is
        replayed from its record, and one that started without an outcome is not run again.

        done    -> the recorded result, fn not called.
        started -> the process died mid-call last time; the outcome is unknown, so fn is NOT called again.
        error   -> the last attempt failed cleanly; fn is called again. An unverified write is not clean: it is done.
        """
        digest = args_digest(args)
        key = idempotency_key(run_id or "", step, tool, digest)
        t = time.time()
        n = self._exec("INSERT INTO executed_calls(key, run_id, step, tool, args_digest, call_id, status, created_at) "
                       "VALUES(?,?,?,?,?,?,'started',?) ON CONFLICT(key) DO NOTHING", (key, run_id, step, tool, digest, call_id, t))
        if n and inherit:
            prior = self.prior_call(inherit, tool, digest)
            if prior and prior["status"] == "done":
                self._exec("UPDATE executed_calls SET status='done', result=?, finished_at=? WHERE key=?", (prior["result"], time.time(), key))
                return json.loads(prior["result"]), True
            if prior and prior["status"] == "started":
                # our own row stays 'started', so a later identical call here is also unknown_outcome, never a re-run
                return unknown_outcome(tool, prior["key"]), True
        if not n:
            row = self._one("SELECT status, result FROM executed_calls WHERE key=?", (key,)) or {}
            if row.get("status") == "done":
                return json.loads(row["result"]), True
            if row.get("status") != "error" or not self._exec(
                    "UPDATE executed_calls SET status='started', attempts=attempts+1, call_id=?, finished_at=NULL WHERE key=? AND status='error'",
                    (call_id, key)):
                return unknown_outcome(tool, key), True
        try:
            result = await fn()
        except asyncio.CancelledError:
            raise  # stays 'started': whether it happened is unknown, so a replay must not do it again
        except Exception as e:
            self._exec("UPDATE executed_calls SET status='error', result=?, finished_at=? WHERE key=?",
                       (_dumps({"error": f"{type(e).__name__}: {e}"}), time.time(), key))
            raise
        # An unverified write (an error that still carries `verification`) may have landed: record it as done, so a
        # retry or a resume replays it instead of writing again.
        failed = isinstance(result, dict) and bool(result.get("error")) and "verification" not in result
        self._exec("UPDATE executed_calls SET status=?, result=?, finished_at=? WHERE key=?",
                   ("error" if failed else "done", _dumps(result), time.time(), key))
        return result, False

    def prior_call(self, run_id: str, tool: str, digest: str) -> dict[str, Any] | None:
        """The newest journal row of a run for this tool and arguments, whatever step it ran at."""
        return self._one("SELECT key, status, result FROM executed_calls WHERE run_id=? AND tool=? AND args_digest=? "
                         "ORDER BY created_at DESC, rowid DESC LIMIT 1", (run_id, tool, digest))

    def executed(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM executed_calls WHERE run_id=? ORDER BY created_at", (run_id,))
        for r in rows:
            if isinstance(r.get("result"), str):
                r["result"] = json.loads(r["result"])
        return rows

    def unsettled_calls(self, desk_id: str) -> list[dict[str, Any]]:
        """A desk's journal rows, over every run it has had, still `started`: after a restart, calls whose
        outcome nobody knows. Auto-resume never relaunches a desk that has one."""
        return self._all("SELECT e.key, e.run_id, e.tool, e.call_id FROM executed_calls e JOIN agent_runs r ON r.run_id=e.run_id "
                         "WHERE r.desk_id=? AND e.status IN ('started','unknown')", (desk_id,))

    # ---- recovery ----
    def transcript(self, run_id: str, message_id: str) -> tuple[str, list[dict[str, Any]]]:
        """The reply text and tool events of one assistant message, rebuilt from the tape. Finished calls come first;
        a call that was announced but never produced a result follows, as its raw tool_call event (needs_approval
        says whether it was a card waiting on the user)."""
        text: list[str] = []
        tool_events: list[dict[str, Any]] = []
        opened: dict[str, dict[str, Any]] = {}
        for _, event, data in self.events(run_id):
            if not isinstance(data, dict):
                continue
            if event == "delta" and data.get("id") == message_id:
                text.append(data.get("text") or "")
            elif event == "tool_call" and data.get("message_id") == message_id:
                opened[str(data.get("id"))] = {k: v for k, v in data.items() if k != "message_id"}
            elif event == "tool_decision" and data.get("message_id") == message_id and str(data.get("id")) in opened:
                opened[str(data.get("id"))].update({"needs_approval": False, "approval": data.get("decision")})
            elif event == "tool_result" and data.get("message_id") == message_id:
                tool_events.append({k: v for k, v in data.items() if k != "message_id"})
                opened.pop(str(data.get("id")), None)
        return "".join(text).strip(), tool_events + list(opened.values())

    def recover(self, live: Iterable[str] = ()) -> list[dict[str, Any]]:
        """At startup: every run still marked active, but not running in this process, died with the last one.
        Mark it interrupted and append an `error` event saying so (naming any approval it was blocked on).
        Pending approvals stay pending: the UI can still show the card and record the decision. A desk's are parked.
        Also drops the event tape of runs that ended more than EVENTS_RETAIN_S ago."""
        keep = set(live)
        out = []
        for r in self.list(ACTIVE, limit=500):
            if r["run_id"] in keep:
                continue
            pending = self.approvals("pending", run_id=r["run_id"])
            for a in pending:
                if a.get("desk_id"):
                    self.park(a["call_id"])  # the desk's next turn picks the answer up, with no second card
            msg = "Interrupted: the backend stopped while this reply was running."
            if pending:
                msg += " It was waiting on your approval of " + ", ".join(f"{a['tool']} ({a['call_id']})" for a in pending) + "."
            seq = self.last_seq(r["run_id"]) + 1
            self.append(r["run_id"], seq, "error", {"message": msg, "interrupted": True, "run_id": r["run_id"],
                                                    "pending_approvals": [a["call_id"] for a in pending]})
            t = time.time()
            self.update(r["run_id"], status="interrupted", error=msg, last_seq=seq, ended_at=t)
            out.append({**r, "status": "interrupted", "error": msg, "last_seq": seq, "ended_at": t, "pending_approvals": pending})
        try:
            self._exec("DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM agent_runs WHERE ended_at IS NOT NULL AND ended_at < ?)",
                       (time.time() - EVENTS_RETAIN_S,))
        except sqlite3.Error:
            log.warning("could not prune old run events", exc_info=True)
        return out


class _Sub:
    """One attached client: a bounded queue, plus the overflow flag that ends its stream."""

    __slots__ = ("q", "overflow")

    def __init__(self) -> None:
        self.q: asyncio.Queue[RunEvent | None] = asyncio.Queue(QUEUE_MAX)
        self.overflow = False


class Run:
    def __init__(self, conversation_id: str, store: RunStore | None = None, kind: str = "chat",
                 input: dict[str, Any] | None = None, desk_id: str | None = None, turn: int = 0) -> None:
        self.run_id = new_id()
        self.conversation_id = conversation_id
        self.kind = kind
        # Which desk this run is a turn of, and which turn. Both are None/0 for an ordinary chat:
        # a desk run is not a different kind of run, only one that something is supervising.
        self.desk_id = desk_id
        self.turn = turn
        # What this reply ended up spending, written by _chat_stream onto the Run it was handed.
        # The desk supervisor reads them to decide whether another bounded turn is worth starting;
        # nothing else does, so they stay 0 on a chat run and cost nothing to carry.
        self.partial: str | None = None
        self.cost = 0.0
        self.rounds = 0
        self.steps_consumed = 0
        # Tool calls that ran and returned without an error: the other half of a desk turn's progress.
        self.tool_ok = 0
        # What launched the run, as stored in agent_runs.input. A job fire record for kind='job'.
        self.input: dict[str, Any] = dict(input or {})
        self.message_id: str | None = None
        # Tape seq of the latest assistant_message: where a window that attaches mid-reply replays from,
        # so it sees the whole in-flight message and not only what streams after it arrives.
        self.message_seq: int | None = None
        # Set by RunBus.start: told when the run's answering / status state changes.
        self.on_change: Callable[[Run], None] | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        # Set when the reply's `done` goes out. The task lives on past that — auto-learn is the last
        # thing it does — so `live` alone cannot tell a working run from one that is only tidying up.
        self.replied = False
        self.seq = 0
        self.status = "running"
        self.error: str | None = None
        # Budget snapshot (Budget.snapshot() in app.py), persisted with each status change and at the end.
        self.budget: dict[str, Any] | None = None
        # Cooperative stop, also registered as _active[message_id] so /messages/{mid}/stop still works.
        self.stop = asyncio.Event()
        # Set by stop and steer. stream_chat waits on it so a blocked provider read ends now, not at
        # the next token. wake_gen catches a poke that lands in the window where the flag is cleared.
        self.wake = asyncio.Event()
        self.wake_gen = 0
        # Steered user messages (already persisted) waiting for the run to fold them into its context.
        self.steers: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self._ring: deque[RunEvent] = deque(maxlen=RING)
        self._subs: set[_Sub] = set()
        self.store = store
        if store is not None:
            try:
                store.create(self.run_id, conversation_id, kind, input, desk_id=desk_id, turn=turn)
            except sqlite3.Error:  # no row, no tape: the run still works from memory
                log.warning("could not persist run %s", self.run_id, exc_info=True)
                self.store = None

    @property
    def watchers(self) -> int:
        """How many clients are attached right now. The park timer will not fire in front of
        somebody who is reading the card."""
        return len(self._subs)

    @property
    def live(self) -> bool:
        return self.ended_at is None

    @property
    def answering(self) -> bool:
        """Still producing a reply, so a steer can be folded in and a second run must not start.

        False for the whole auto-learn tail, where the round loop is already over: a steer accepted
        there would be persisted, published and never answered.
        """
        return self.live and not self.replied

    def poke(self) -> None:
        """Wake a provider read blocked in stream_chat. Stop and steer both call this."""
        self.wake_gen += 1
        self.wake.set()

    def info(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "conversation_id": self.conversation_id, "message_id": self.message_id,
                "seq": self.seq, "message_seq": self.message_seq, "started_at": self.started_at, "live": self.live,
                "answering": self.answering, "status": self.status, "kind": self.kind, "desk_id": self.desk_id,
                "turn": self.turn, "ended_at": self.ended_at, "error": self.error}

    def set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        if self.store is not None:
            self.store.update(self.run_id, status=status, budget=self.budget, last_seq=self.seq)
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception:  # noqa: BLE001 - a listener must never break the run
                log.debug("run change listener failed for %s", self.run_id, exc_info=True)

    def publish(self, event: str, data: Any) -> None:
        self.seq += 1
        if event == "assistant_message":
            self.message_seq = self.seq
        item = (self.seq, event, data)
        if self.store is not None:  # write-through: the table is the source of truth, the ring a cache
            self.store.append(self.run_id, self.seq, event, data)
            if event == "assistant_message" and isinstance(data, dict) and data.get("id"):
                self.store.update(self.run_id, message_id=data["id"])
        if event == "error" and isinstance(data, dict):
            self.error = str(data.get("message") or "error")
        elif event == "done" and isinstance(data, dict) and data.get("error"):
            self.error = str(data["error"])
        self._ring.append(item)
        for sub in self._subs:
            if sub.overflow:
                continue
            try:
                sub.q.put_nowait(item)
            except asyncio.QueueFull:
                sub.overflow = True
        # The two points where `answering` flips: a new segment opens, or the reply's final done goes out.
        # _run_chat sets `replied` just after publishing, so a listener would still see the old value.
        if event == "assistant_message":
            self._changed()
        elif event == "done" and not (isinstance(data, dict) and data.get("segment")):
            self.replied = True
            self._changed()

    def end(self, status: str | None = None) -> None:
        self.ended_at = time.time()
        self.status = status or ("error" if self.error else "done")
        if self.store is not None:
            self.store.update(self.run_id, status=self.status, error=self.error, last_seq=self.seq,
                              budget=self.budget, ended_at=self.ended_at)
        for sub in self._subs:
            try:
                sub.q.put_nowait(None)
            except asyncio.QueueFull:
                sub.overflow = True

    async def subscribe(self, since: int = 0) -> AsyncIterator[str]:
        """Replay past `since` (from the table for whatever the ring no longer holds), then follow live until the run ends."""
        sub = _Sub()
        self._subs.add(sub)
        getter: asyncio.Task[RunEvent | None] | None = None
        try:
            last = since
            ring = list(self._ring)
            first = ring[0][0] if ring else self.seq + 1
            if self.store is not None and first > last + 1:
                for seq, event, data in self.store.events(self.run_id, last, first):
                    yield sse(event, data, seq)
                    last = seq
            for seq, event, data in ring:
                if seq > last:
                    yield sse(event, data, seq)
                    last = seq
            while self.live or not sub.q.empty():
                if sub.overflow and sub.q.empty():
                    return
                if getter is None:
                    getter = asyncio.ensure_future(sub.q.get())
                done, _ = await asyncio.wait({getter}, timeout=KEEPALIVE_S)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                item, getter = getter.result(), None
                if item is None:
                    return
                seq, event, data = item
                if seq > last:
                    yield sse(event, data, seq)
                    last = seq
        finally:
            self._subs.discard(sub)
            if getter is not None:
                getter.cancel()


class Topic:
    """An app-lived event topic: the same fan-out a run has, minus the ending.

    A `Run` closes its subscribers' streams as soon as its reply is persisted. Work that is
    deliberately detached from the reply — auto-learn — finishes after that point and would have
    nowhere to publish, so it publishes here. One topic serves the whole app; each event carries
    its seq as the SSE `id`, so a window that reconnects (or opens late) resumes from the ring
    instead of missing what happened while it was away.
    """

    def __init__(self) -> None:
        self.seq = 0
        self._ring: deque[RunEvent] = deque(maxlen=TOPIC_RING)
        self._subs: set[_Sub] = set()

    def publish(self, event: str, data: Any) -> None:
        self.seq += 1
        item = (self.seq, event, data)
        self._ring.append(item)
        for sub in self._subs:
            if sub.overflow:
                continue
            try:
                sub.q.put_nowait(item)
            except asyncio.QueueFull:
                sub.overflow = True

    async def subscribe(self, since: int = 0) -> AsyncIterator[str]:
        """Replay the ring past `since`, then follow forever — until the client goes away."""
        # seq lives in memory and restarts at 0 with the backend; a cursor ahead of it came from the
        # previous process, and honouring it would drop every event until the counter caught up.
        if since > self.seq:
            since = 0
        sub = _Sub()
        self._subs.add(sub)
        getter: asyncio.Task[RunEvent | None] | None = None
        try:
            last = since
            for seq, event, data in list(self._ring):
                if seq > last:
                    yield sse(event, data, seq)
                    last = seq
            while True:
                if sub.overflow and sub.q.empty():
                    return  # the client fell behind the queue; it reconnects and replays from `last`
                if getter is None:
                    getter = asyncio.ensure_future(sub.q.get())
                done, _ = await asyncio.wait({getter}, timeout=KEEPALIVE_S)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                item, getter = getter.result(), None
                if item is None:
                    return
                seq, event, data = item
                if seq > last:
                    yield sse(event, data, seq)
                    last = seq
        finally:
            self._subs.discard(sub)
            if getter is not None:
                getter.cancel()


class RunBus:
    """Holds the latest run per conversation, live or recently finished. With a store, every run is also a row."""

    def __init__(self, store: RunStore | None = None) -> None:
        self._runs: dict[str, Run] = {}
        self.store = store
        # Runs displaced by a newer one while their auto-learn tail was still open. Only `shutdown`
        # cares: nothing routes to them any more, and the task holds what keeps them alive.
        self._retired: set[Run] = set()
        # Awaited when a run's runner finishes, before the run is closed (the after-snapshot of a reply).
        self.after_hooks: list[Callable[[Run], Awaitable[None]]] = []
        # Told whenever a run's answering / status state changes (the app topic's `run_state`).
        self.on_change: Callable[[Run], None] | None = None

    def changed(self, run: Run) -> None:
        if self.on_change is not None:
            try:
                self.on_change(run)
            except Exception:  # noqa: BLE001 - a listener must never break the run
                log.debug("run change listener failed for %s", run.run_id, exc_info=True)

    def get(self, conversation_id: str) -> Run | None:
        return self._runs.get(conversation_id)

    def live(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.live else None

    def live_ids(self) -> set[str]:
        return {r.run_id for r in self._runs.values() if r.live}

    def answering(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.answering else None

    def list(self, statuses: Iterable[str] | None = ACTIVE, conversation_id: str | None = None, limit: int = 50,
             desk_id: str | None = None) -> list[dict[str, Any]]:
        """Runs from the table (active ones by default), with a live run's in-memory seq laid over its row."""
        if self.store is None:
            return sorted((r.info() for r in self._runs.values() if r.live and (not desk_id or r.desk_id == desk_id)),
                          key=lambda i: i["started_at"], reverse=True)
        mem = {r.run_id: r for r in self._runs.values()}
        out = []
        for row in self.store.list(statuses, conversation_id, limit, desk_id):
            run = mem.get(row["run_id"])
            if run is not None:
                out.append({**row, **run.info()})
            else:
                out.append({**row, "seq": row["last_seq"], "live": False})
        return out

    def start(self, conversation_id: str, runner: Callable[[Run], Awaitable[None]], input: dict[str, Any] | None = None,
              kind: str = "chat", desk_id: str | None = None, turn: int = 0) -> Run:
        self._prune()
        # A conversation can start a new reply while the previous run is still auto-learning. That
        # run keeps writing to its own ring for whoever is attached; it is simply no longer the
        # conversation's current run.
        displaced = self._runs.get(conversation_id)
        if displaced is not None and displaced.live:
            self._retired.add(displaced)
        run = Run(conversation_id, self.store, kind=kind, input=input, desk_id=desk_id, turn=turn)
        self._runs[conversation_id] = run
        run.on_change = self.changed
        run.task = asyncio.create_task(self._drive(run, runner), name=f"run:{run.run_id}")
        self.changed(run)
        return run

    def stop(self, conversation_id: str, run_id: str | None = None) -> bool:
        run = self.live(conversation_id)
        if not run or (run_id and run_id != run.run_id):
            return False
        run.stop.set()
        run.poke()
        return True

    async def shutdown(self) -> None:
        """Cancel every live run and wait for it: a sandboxed run_python writes into data_dir/tmp."""
        every = [*self._runs.values(), *self._retired]
        for run in every:
            run.stop.set()
            run.poke()
        tasks = [r.task for r in every if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run in every:
            if run.live:
                run.end("interrupted")
        self._runs.clear()
        self._retired.clear()

    async def _drive(self, run: Run, runner: Callable[[Run], Awaitable[None]]) -> None:
        status: str | None = None
        try:
            await runner(run)
        except asyncio.CancelledError:
            status = "interrupted"
            run.publish("error", {"message": "Interrupted: the backend shut down while this reply was running.",
                                  "interrupted": True, "run_id": run.run_id})
            raise
        except Exception as e:  # noqa: BLE001 - a dead run must still tell its subscribers why
            log.exception("run %s failed", run.run_id)
            run.publish("error", {"message": str(e)})
        finally:
            for hook in self.after_hooks:
                try:
                    await hook(run)
                except BaseException:  # noqa: BLE001 - a hook must not stop the run from closing
                    log.debug("after hook failed for run %s", run.run_id, exc_info=True)
            run.end(status)
            self._retired.discard(run)
            self.changed(run)

    def _prune(self) -> None:
        cutoff = time.time() - RETAIN_S
        for cid, run in list(self._runs.items()):
            if run.ended_at is not None and run.ended_at < cutoff:
                del self._runs[cid]
