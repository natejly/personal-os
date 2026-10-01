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
# How long a finished run keeps its event tape. The agent_runs row, approvals and executed_calls stay.
EVENTS_RETAIN_S = 14 * 86400

ACTIVE = ("running", "awaiting_approval")
STATUSES = ("running", "awaiting_approval", "done", "error", "interrupted")

RunEvent = tuple[int, str, Any]


def sse(event: str, data: Any, seq: int | None = None) -> str:
    """One SSE block. `id:` carries the seq, so a client knows what to pass as ?since= when it reconnects."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


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
    def create(self, run_id: str, conversation_id: str | None, kind: str = "chat", input: dict[str, Any] | None = None) -> None:
        t = time.time()
        self._exec("INSERT INTO agent_runs(run_id, conversation_id, kind, status, input, started_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                   (run_id, conversation_id, kind, "running", _dumps(input or {}), t, t))

    def update(self, run_id: str, **fields: Any) -> None:
        """Set any of status, message_id, budget, error, last_seq, ended_at. Never raises: the tape must not kill a run."""
        allowed = {"status", "message_id", "budget", "error", "last_seq", "ended_at"}
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

    def latest(self, conversation_id: str) -> dict[str, Any] | None:
        return self._run_row(self._one("SELECT * FROM agent_runs WHERE conversation_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                                       (conversation_id,)))

    def list(self, statuses: Iterable[str] | None = ACTIVE, conversation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, params = [], []
        if statuses is not None:
            st = list(statuses)
            where.append(f"status IN ({','.join('?' * len(st))})")
            params += st
        if conversation_id:
            where.append("conversation_id=?")
            params.append(conversation_id)
        sql = "SELECT * FROM agent_runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY started_at DESC LIMIT ?"
        return [r for r in (self._run_row(x) for x in self._all(sql, (*params, max(1, min(int(limit), 500))))) if r]

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

    def last_seq(self, run_id: str) -> int:
        r = self._one("SELECT MAX(seq) AS s FROM run_events WHERE run_id=?", (run_id,))
        return int((r or {}).get("s") or 0)

    async def tail(self, run_id: str, since: int = 0) -> AsyncIterator[str]:
        """The stored tape of a run that is no longer in memory, as SSE. It cannot grow, so this ends."""
        for seq, event, data in self.events(run_id, since):
            yield sse(event, data, seq)

    # ---- approvals ----
    def open_approval(self, call_id: str, run_id: str | None, tool: str, args: dict[str, Any], *, conversation_id: str | None = None,
                      message_id: str | None = None, forced: bool = False) -> dict[str, Any]:
        self._exec("INSERT INTO approvals(call_id, run_id, conversation_id, message_id, tool, args, args_digest, forced, status, created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,'pending',?) ON CONFLICT(call_id) DO NOTHING",
                   (call_id, run_id, conversation_id, message_id, tool, _dumps(args), args_digest(args), int(forced), time.time()))
        return self.approval(call_id) or {}

    def decide(self, call_id: str, decision: str, by: str = "user") -> dict[str, Any] | None:
        """First decision wins. None if there is no such approval or it was already decided."""
        status = "denied" if decision == "deny" else "approved"
        n = self._exec("UPDATE approvals SET status=?, decision=?, decided_by=?, decided_at=? WHERE call_id=? AND status='pending'",
                       (status, decision, by, time.time(), call_id))
        return self.approval(call_id) if n else None

    def approval(self, call_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM approvals WHERE call_id=?", (call_id,))
        if r:
            r["args"] = json.loads(r["args"])
            r["forced"] = bool(r["forced"])
        return r

    def approvals(self, status: str | None = "pending", run_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if run_id:
            where.append("run_id=?")
            params.append(run_id)
        sql = "SELECT call_id FROM approvals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at LIMIT ?"
        return [a for a in (self.approval(r["call_id"]) for r in self._all(sql, (*params, max(1, min(int(limit), 500))))) if a]

    # ---- idempotency ----
    async def call_once(self, run_id: str | None, step: int, tool: str, args: dict[str, Any],
                        fn: Callable[[], Awaitable[Any]], call_id: str | None = None) -> tuple[Any, bool]:
        """Run fn at most once per (run_id, step, tool, args). Returns (result, replayed).

        done    -> the recorded result, fn not called.
        started -> the process died mid-call last time; the outcome is unknown, so fn is NOT called again.
        error   -> the last attempt failed cleanly; fn is called again.
        """
        digest = args_digest(args)
        key = idempotency_key(run_id or "", step, tool, digest)
        t = time.time()
        n = self._exec("INSERT INTO executed_calls(key, run_id, step, tool, args_digest, call_id, status, created_at) "
                       "VALUES(?,?,?,?,?,?,'started',?) ON CONFLICT(key) DO NOTHING", (key, run_id, step, tool, digest, call_id, t))
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
        failed = isinstance(result, dict) and bool(result.get("error"))
        self._exec("UPDATE executed_calls SET status=?, result=?, finished_at=? WHERE key=?",
                   ("error" if failed else "done", _dumps(result), time.time(), key))
        return result, False

    def executed(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM executed_calls WHERE run_id=? ORDER BY created_at", (run_id,))
        for r in rows:
            if isinstance(r.get("result"), str):
                r["result"] = json.loads(r["result"])
        return rows

    # ---- recovery ----
    def transcript(self, run_id: str, message_id: str) -> tuple[str, list[dict[str, Any]]]:
        """The reply text and finished tool events of one assistant message, rebuilt from the tape."""
        text: list[str] = []
        tool_events: list[dict[str, Any]] = []
        for _, event, data in self.events(run_id):
            if not isinstance(data, dict):
                continue
            if event == "delta" and data.get("id") == message_id:
                text.append(data.get("text") or "")
            elif event == "tool_result" and data.get("message_id") == message_id:
                tool_events.append({k: v for k, v in data.items() if k != "message_id"})
        return "".join(text).strip(), tool_events

    def recover(self, live: Iterable[str] = ()) -> list[dict[str, Any]]:
        """At startup: every run still marked active, but not running in this process, died with the last one.
        Mark it interrupted and append an `error` event saying so (naming any approval it was blocked on).
        Pending approvals stay pending: the UI can still show the card and record the decision.
        Also drops the event tape of runs that ended more than EVENTS_RETAIN_S ago."""
        keep = set(live)
        out = []
        for r in self.list(ACTIVE, limit=500):
            if r["run_id"] in keep:
                continue
            pending = self.approvals("pending", run_id=r["run_id"])
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
                 input: dict[str, Any] | None = None) -> None:
        self.run_id = new_id()
        self.conversation_id = conversation_id
        self.kind = kind
        self.message_id: str | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.seq = 0
        self.status = "running"
        self.error: str | None = None
        # Budget snapshot (Budget.snapshot() in app.py), persisted with each status change and at the end.
        self.budget: dict[str, Any] | None = None
        # Cooperative stop, also registered as _active[message_id] so /messages/{mid}/stop still works.
        self.stop = asyncio.Event()
        # Steered user messages (already persisted) waiting for the run to fold them into its context.
        self.steers: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self._ring: deque[RunEvent] = deque(maxlen=RING)
        self._subs: set[_Sub] = set()
        self.store = store
        if store is not None:
            try:
                store.create(self.run_id, conversation_id, kind, input)
            except sqlite3.Error:  # no row, no tape: the run still works from memory
                log.warning("could not persist run %s", self.run_id, exc_info=True)
                self.store = None

    @property
    def live(self) -> bool:
        return self.ended_at is None

    def info(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "conversation_id": self.conversation_id, "message_id": self.message_id,
                "seq": self.seq, "started_at": self.started_at, "live": self.live, "status": self.status,
                "kind": self.kind, "ended_at": self.ended_at, "error": self.error}

    def set_status(self, status: str) -> None:
        if status == self.status:
            return
        self.status = status
        if self.store is not None:
            self.store.update(self.run_id, status=status, budget=self.budget, last_seq=self.seq)

    def publish(self, event: str, data: Any) -> None:
        self.seq += 1
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


class RunBus:
    """Holds the latest run per conversation, live or recently finished. With a store, every run is also a row."""

    def __init__(self, store: RunStore | None = None) -> None:
        self._runs: dict[str, Run] = {}
        self.store = store

    def get(self, conversation_id: str) -> Run | None:
        return self._runs.get(conversation_id)

    def live(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.live else None

    def live_ids(self) -> set[str]:
        return {r.run_id for r in self._runs.values() if r.live}

    def list(self, statuses: Iterable[str] | None = ACTIVE, conversation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Runs from the table (active ones by default), with a live run's in-memory seq laid over its row."""
        if self.store is None:
            return sorted((r.info() for r in self._runs.values() if r.live), key=lambda i: i["started_at"], reverse=True)
        mem = {r.run_id: r for r in self._runs.values()}
        out = []
        for row in self.store.list(statuses, conversation_id, limit):
            run = mem.get(row["run_id"])
            if run is not None:
                out.append({**row, **run.info()})
            else:
                out.append({**row, "seq": row["last_seq"], "live": False})
        return out

    def start(self, conversation_id: str, runner: Callable[[Run], Awaitable[None]], input: dict[str, Any] | None = None) -> Run:
        self._prune()
        run = Run(conversation_id, self.store, input=input)
        self._runs[conversation_id] = run
        run.task = asyncio.create_task(self._drive(run, runner), name=f"run:{run.run_id}")
        return run

    def stop(self, conversation_id: str, run_id: str | None = None) -> bool:
        run = self.live(conversation_id)
        if not run or (run_id and run_id != run.run_id):
            return False
        run.stop.set()
        return True

    async def shutdown(self) -> None:
        """Cancel every live run and wait for it: a sandboxed run_python writes into data_dir/tmp."""
        for run in self._runs.values():
            run.stop.set()
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run in self._runs.values():
            if run.live:
                run.end("interrupted")
        self._runs.clear()

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
            run.end(status)

    def _prune(self) -> None:
        cutoff = time.time() - RETAIN_S
        for cid, run in list(self._runs.items()):
            if run.ended_at is not None and run.ended_at < cutoff:
                del self._runs[cid]
