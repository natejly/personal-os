"""The run tape: every run, every published event, every tool call and every approval, as rows.

The ring in runs.py is a cache; this is the truth. It exists so a reply survives the process that
produced it: `?since=` can be served after RETAIN_S has dropped the run from memory, an approval can
be decided from another window or after a restart instead of dying with an in-process Future, and an
external write that was in flight when the app stopped leaves a row saying so rather than leaving
nothing. Deltas are coalesced before they are written, so a streamed reply costs a handful of rows
rather than one row per token.

Unlike every other repo here this class keeps ONE long-lived connection behind a lock: it is written
from inside the event loop on every coalesced flush and every tool call, and Database.tx()
(db.py:174-184) opens a fresh connection per call, which at that rate is the wrong trade. The
connection still comes from db.connect() (db.py:168-172) so PRAGMA foreign_keys=ON is applied the
same way as everywhere else, and no other module shares it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator

from .db import Database, now, row_to_dict

# Coalescing: a delta row is written once the buffered text crosses DELTA_FLUSH_CHARS or the buffer
# is DELTA_FLUSH_S old, whichever comes first.
DELTA_FLUSH_CHARS = 2048
DELTA_FLUSH_S = 1.0
# Past this many rows for one run we stop taping deltas and set events_truncated; structural events
# (tool_call, approval, done, ...) keep being written, because they are what the UI rebuilds from.
MAX_RUN_EVENTS = 50_000
EVENTS_RETAIN_S = 14 * 86400
RESULT_CAP = 64_000
ACTIVE = ("running", "awaiting")
TERMINAL = ("done", "error", "stopped", "interrupted")
# Steps outside a model round. round*1000 + index is never negative, so these cannot collide.
PROMOTE_STEP = -2
RECOVER_STEP = -3

# Decision -> approvals.status. Anything that is not a denial opens the gate for this one call.
_DECISION_STATUS = {"allow": "approved", "always_chat": "approved", "always_global": "approved", "deny": "denied"}

_RUN_COLUMNS = ("desk_id", "kind", "status", "message_id", "turn", "input", "budget", "cost", "rounds",
                "error", "last_seq", "events_truncated", "updated_at", "ended_at")
_RUN_JSON = ("input", "budget")

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__, before
# RunStore(db) exists, so a _migrate entry for them would PRAGMA table_info an absent table.
# Post-release columns need an additive ALTER in __init__ below.
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id          TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  desk_id         TEXT,                             -- no FK: the tape outlives a purged desk
  kind            TEXT NOT NULL DEFAULT 'chat',     -- chat | desk
  status          TEXT NOT NULL DEFAULT 'running',  -- running|awaiting|done|error|stopped|interrupted
  message_id      TEXT,
  turn            INTEGER NOT NULL DEFAULT 0,       -- which chained desk turn this is (0 for chat)
  input           TEXT NOT NULL DEFAULT '{}',       -- JSON: the ChatIn that started it
  budget          TEXT NOT NULL DEFAULT '{}',       -- JSON: Budget.snapshot() at end
  cost            REAL NOT NULL DEFAULT 0,
  rounds          INTEGER NOT NULL DEFAULT 0,
  error           TEXT,
  last_seq        INTEGER NOT NULL DEFAULT 0,
  events_truncated INTEGER NOT NULL DEFAULT 0,      -- MAX_RUN_EVENTS hit: deltas stopped being taped
  started_at      REAL NOT NULL,
  updated_at      REAL NOT NULL,
  ended_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_conv   ON agent_runs(conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_desk   ON agent_runs(desk_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status, started_at DESC);

-- Every published SSE event, so ?since= survives the process and RETAIN_S=300. Deltas are
-- coalesced by append(): one row per ~2KB or 1s, never one row per token.
CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  seq    INTEGER NOT NULL,
  type   TEXT NOT NULL,
  data   TEXT NOT NULL,
  ts     REAL NOT NULL,
  PRIMARY KEY (run_id, seq)
);

-- Written at status='started' and COMMITTED before the tool function is awaited. A row still at
-- 'started' after a restart becomes 'unknown': the one ambiguous state, and never auto-retried.
CREATE TABLE IF NOT EXISTS tool_calls (
  key         TEXT PRIMARY KEY,   -- sha256("{run_id}|{step}|{tool}|{args_digest}")
  run_id      TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  desk_id     TEXT,
  step        INTEGER NOT NULL,   -- round*1000 + index in round; negatives reserved (PROMOTE_STEP=-2)
  tool        TEXT NOT NULL,
  args        TEXT NOT NULL,
  args_digest TEXT NOT NULL,
  call_id     TEXT,               -- the uid "{message_id}:{provider_call_id}" (app.py:687)
  status      TEXT NOT NULL,      -- started | done | error | unknown
  result      TEXT,               -- JSON, truncated at RESULT_CAP
  error       TEXT,
  attempts    INTEGER NOT NULL DEFAULT 1,
  created_at  REAL NOT NULL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run  ON tool_calls(run_id, step);
CREATE INDEX IF NOT EXISTS idx_tool_calls_desk ON tool_calls(desk_id, created_at DESC);

-- Approvals as rows, not futures: decidable from any window, and a restart does not orphan them.
CREATE TABLE IF NOT EXISTS approvals (
  call_id         TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL,
  desk_id         TEXT,
  message_id      TEXT,
  tool            TEXT NOT NULL,
  args            TEXT NOT NULL,
  args_digest     TEXT NOT NULL,
  danger          TEXT NOT NULL DEFAULT 'safe',
  forced          INTEGER NOT NULL DEFAULT 0,       -- taint-upgraded: cannot buy a standing grant
  plan_id         TEXT,                             -- set when this card IS a propose_plan card
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|denied|parked|expired
  decision        TEXT,                             -- allow|deny|always_chat|always_global
  decided_by      TEXT,                             -- user|stop|shutdown|timeout|park
  note            TEXT NOT NULL DEFAULT '',
  created_at      REAL NOT NULL,
  decided_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_desk   ON approvals(desk_id, status);
"""

# The exact words §3.10 requires, shared by the ledger line, the frozen tool card and the shaped
# result, so the user and the model are told the same thing about an unknown call.
UNKNOWN_LINE = "in flight when the app stopped; outcome unknown, verify before repeating"


def _plain(v: Any) -> Any:
    """Coerce to the value set JSON.stringify would see, so canon() can be mirrored in TypeScript.

    The only real divergence between json.dumps and JSON.stringify on JSON-shaped data is numbers:
    JS has one number type, so 1.0 serialises as `1` and -0.0 as `0` while Python writes `1.0` and
    `-0.0`. Non-finite floats become null, which is what JSON.stringify does with NaN/Infinity.
    """
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return int(v) if v == int(v) else v
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return str(v)


def canon(args: dict[str, Any] | None) -> str:
    """The canonical string args_digest hashes: sorted keys at every depth, compact separators, no
    ASCII escaping. lib/planDigest.ts `canon()` must produce this byte for byte."""
    return json.dumps(_plain(args or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_digest(args: dict[str, Any] | None) -> str:
    """The one canonicalisation everything binds to. lib/planDigest.ts mirrors it byte for byte;
    if the two ever diverge, claim() silently never matches and every approved step re-prompts."""
    return hashlib.sha256(canon(args).encode("utf-8")).hexdigest()


def call_key(run_id: str, step: int, tool: str, digest: str) -> str:
    return hashlib.sha256(f"{run_id}|{step}|{tool}|{digest}".encode("utf-8")).hexdigest()


def sse(event: str, data: Any, seq: int | None = None) -> str:
    """runs.sse plus `id: <seq>`, so a reconnecting client can resume from the last event it saw."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


def unknown_result(tool: str) -> dict[str, Any]:
    """What an `unknown` row answers with. Never a retry: the call may or may not have happened."""
    return {"error": f"{tool} was {UNKNOWN_LINE}.",
            "try_instead": "check whether it already happened, and only then call it again"}


class _Deltas:
    """The un-flushed tail of one run's delta stream."""

    __slots__ = ("mid", "text", "seq", "opened")

    def __init__(self, mid: Any, seq: int) -> None:
        self.mid = mid
        self.text: list[str] = []
        self.seq = seq
        self.opened = time.time()


class RunStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._conn = db.connect()
        # One notch below the default durability: a commit happens per coalesced delta, and losing
        # the last few events of a run to an OS crash costs less than an fsync on each of them.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Re-entrant because recover() calls transcript() and prune() while already in a transaction.
        self._lock = threading.RLock()
        self._buf: dict[str, _Deltas] = {}
        self._rows: dict[str, int] = {}
        with self._tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- runs ----
    def create(self, run_id: str, conversation_id: str, *, kind: str = "chat", desk_id: str | None = None,
               turn: int = 0, input: dict[str, Any] | None = None) -> None:
        t = now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO agent_runs(run_id,conversation_id,desk_id,kind,status,turn,input,started_at,updated_at)"
                " VALUES(?,?,?,?,'running',?,?,?,?)",
                (run_id, conversation_id, desk_id, kind, turn, json.dumps(input or {}), t, t),
            )

    def update(self, run_id: str, **fields: Any) -> None:
        sets: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in _RUN_COLUMNS:
                raise ValueError(f"agent_runs has no updatable column {k!r}")
            sets[k] = json.dumps(v) if k in _RUN_JSON and not isinstance(v, str) else v
        status = sets.get("status")
        if status in TERMINAL:
            self.flush(run_id)
        sets["updated_at"] = now()
        with self._tx() as c:
            if status in TERMINAL and "ended_at" not in sets:
                # Stamp the end exactly once, so a later update(status=...) cannot move it.
                c.execute("UPDATE agent_runs SET ended_at=? WHERE run_id=? AND ended_at IS NULL", (now(), run_id))
            cols = ",".join(f"{k}=?" for k in sets)
            c.execute(f"UPDATE agent_runs SET {cols} WHERE run_id=?", (*sets.values(), run_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._tx() as c:
            r = c.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        return row_to_dict(r, _RUN_JSON)

    def latest(self, conversation_id: str) -> dict[str, Any] | None:
        with self._tx() as c:
            r = c.execute("SELECT * FROM agent_runs WHERE conversation_id=? ORDER BY started_at DESC LIMIT 1",
                          (conversation_id,)).fetchone()
        return row_to_dict(r, _RUN_JSON)

    def list(self, *, status: tuple[str, ...] | None = None, kind: str | None = None,
             conversation_id: str | None = None, desk_id: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        where: list[str] = ["1=1"]
        params: list[Any] = []
        if status:
            where.append(f"status IN ({','.join('?' * len(status))})")
            params.extend(status)
        for col, val in (("kind", kind), ("conversation_id", conversation_id), ("desk_id", desk_id)):
            if val is not None:
                where.append(f"{col}=?")
                params.append(val)
        with self._tx() as c:
            rows = c.execute(
                f"SELECT * FROM agent_runs WHERE {' AND '.join(where)} ORDER BY started_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [d for d in (row_to_dict(r, _RUN_JSON) for r in rows) if d]

    # ---- events ----
    def append(self, run_id: str, seq: int, type: str, data: Any) -> None:
        """Coalesces consecutive `delta` events into one row; flushes on 2KB, 1s, any other event, or end()."""
        if type != "delta" or not isinstance(data, dict) or not isinstance(data.get("text"), str):
            self.flush(run_id)
            self._write(run_id, seq, type, data)
            return
        buf = self._buf.get(run_id)
        if buf is not None and buf.mid != data.get("id"):
            # A new assistant message: a coalesced row carries one id, so it cannot span two.
            self.flush(run_id)
            buf = None
        if buf is None:
            buf = self._buf[run_id] = _Deltas(data.get("id"), seq)
        buf.text.append(data["text"])
        buf.seq = seq
        if sum(len(s) for s in buf.text) >= DELTA_FLUSH_CHARS or time.time() - buf.opened >= DELTA_FLUSH_S:
            self.flush(run_id)

    def flush(self, run_id: str) -> None:
        buf = self._buf.pop(run_id, None)
        if buf is None:
            return
        self._write(run_id, buf.seq, "delta", {"id": buf.mid, "text": "".join(buf.text)}, coalesced=True)

    def _write(self, run_id: str, seq: int, type: str, data: Any, coalesced: bool = False) -> None:
        """A coalesced delta row is keyed by the LAST seq it covers: the id a client resumes from is
        the last event it saw, so anchoring at the end keeps `id:` monotonic and never drops text —
        at worst a resume landing inside a group replays that group's (<=2KB) text."""
        with self._tx() as c:
            n = self._rows.get(run_id)
            if n is None:
                n = c.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
            if coalesced and n >= MAX_RUN_EVENTS:
                # Fidelity is the thing we give up first: the structural events still fit.
                c.execute("UPDATE agent_runs SET events_truncated=1, last_seq=MAX(last_seq,?), updated_at=?"
                          " WHERE run_id=?", (seq, now(), run_id))
                self._rows[run_id] = n
                return
            c.execute("INSERT OR REPLACE INTO run_events(run_id,seq,type,data,ts) VALUES(?,?,?,?,?)",
                      (run_id, seq, type, json.dumps(data), now()))
            self._rows[run_id] = n + 1
            c.execute("UPDATE agent_runs SET last_seq=MAX(last_seq,?), updated_at=? WHERE run_id=?",
                      (seq, now(), run_id))

    def last_seq(self, run_id: str) -> int:
        buf = self._buf.get(run_id)
        with self._tx() as c:
            r = c.execute("SELECT last_seq FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        return max(r["last_seq"] if r else 0, buf.seq if buf else 0)

    async def tail(self, run_id: str, since: int = 0) -> AsyncIterator[str]:
        """Replay a run that has left memory, as SSE, then close. Never follows live."""
        self.flush(run_id)
        last = since
        while True:
            with self._tx() as c:
                rows = c.execute(
                    "SELECT seq,type,data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200",
                    (run_id, last),
                ).fetchall()
            if not rows:
                return
            for r in rows:
                yield sse(r["type"], json.loads(r["data"]), r["seq"])
                last = r["seq"]
            # A 50k-row replay must not hold the loop; the tape is append-only, so resuming by seq is safe.
            await asyncio.sleep(0)

    def transcript(self, run_id: str) -> str:
        """Concatenated delta text, for salvaging an interrupted reply into its message row."""
        self.flush(run_id)
        with self._tx() as c:
            rows = c.execute("SELECT data FROM run_events WHERE run_id=? AND type='delta' ORDER BY seq",
                             (run_id,)).fetchall()
        out: list[str] = []
        for r in rows:
            try:
                out.append(json.loads(r["data"]).get("text") or "")
            except ValueError:
                continue
        return "".join(out)

    # ---- tool calls ----
    async def call_once(self, run_id: str, step: int, tool: str, args: dict[str, Any],
                        fn: Callable[[], Awaitable[Any]], *, call_id: str | None = None,
                        desk_id: str | None = None) -> Any:
        """Row committed at 'started' BEFORE fn is awaited. A 'done' row returns its stored result
        without calling. An 'unknown' row returns a shaped tool_error and is never re-executed."""
        digest = args_digest(args)
        key = call_key(run_id, step, tool, digest)
        with self._tx() as c:
            row = c.execute("SELECT * FROM tool_calls WHERE key=?", (key,)).fetchone()
            if row is not None:
                c.execute("UPDATE tool_calls SET attempts=attempts+1 WHERE key=?", (key,))
        if row is not None:
            # Every replay of a key is answered from the row. 'started' can only mean this exact call
            # is already in flight somewhere, which is still not a reason to make the write twice.
            # A retry the model genuinely intends arrives in a later round, so with a different step.
            if row["status"] == "done":
                return json.loads(row["result"]) if row["result"] else None
            if row["status"] == "error":
                return {"error": row["error"] or f"{tool} already failed in this step."}
            return unknown_result(tool)
        with self._tx() as c:
            c.execute(
                "INSERT INTO tool_calls(key,run_id,desk_id,step,tool,args,args_digest,call_id,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,'started',?)",
                (key, run_id, desk_id, step, tool, json.dumps(args or {}), digest, call_id, now()),
            )
        try:
            result = await fn()
        except asyncio.CancelledError:
            # Deliberately leaves the row at 'started'. The await was cut mid-call and nothing here
            # knows whether the effect landed; recover() turns that into the honest 'unknown'.
            raise
        except Exception as e:  # noqa: BLE001 - the tape records the failure, the caller still sees it
            with self._tx() as c:
                c.execute("UPDATE tool_calls SET status='error', error=?, finished_at=? WHERE key=?",
                          (f"{type(e).__name__}: {e}", now(), key))
            raise
        blob = json.dumps(result, default=str)
        if len(blob) > RESULT_CAP:
            # Still valid JSON, so a replay of this key can be loaded back rather than crashing.
            blob = json.dumps({"truncated": True, "preview": blob[:RESULT_CAP]})
        with self._tx() as c:
            c.execute("UPDATE tool_calls SET status='done', result=?, finished_at=? WHERE key=?",
                      (blob, now(), key))
        return result

    def executed(self, run_id: str) -> list[dict[str, Any]]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM tool_calls WHERE run_id=? ORDER BY step, created_at",
                             (run_id,)).fetchall()
        return [d for d in (row_to_dict(r, ("args",)) for r in rows) if d]

    def ledger(self, desk_id: str, limit: int = 120) -> list[dict[str, Any]]:
        """The desk's own work: the newest `limit` rows, oldest first, each carrying the one line
        context.build_context renders. An `unknown` row says so in words, because the agent has to
        verify before repeating it rather than assume either outcome."""
        with self._tx() as c:
            rows = c.execute("SELECT * FROM tool_calls WHERE desk_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                             (desk_id, limit)).fetchall()
        out: list[dict[str, Any]] = []
        for i, r in enumerate(reversed(rows)):
            d = row_to_dict(r, ("args",)) or {}
            d["line"] = _ledger_line(i + 1, d)
            out.append(d)
        return out

    # ---- approvals ----
    def open_approval(self, *, call_id: str, run_id: str, conversation_id: str, desk_id: str | None,
                      message_id: str | None, tool: str, args: dict[str, Any], danger: str,
                      forced: bool, plan_id: str | None = None) -> dict[str, Any]:
        with self._tx() as c:
            c.execute(
                "INSERT INTO approvals(call_id,run_id,conversation_id,desk_id,message_id,tool,args,args_digest,"
                "danger,forced,plan_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?)"
                " ON CONFLICT(call_id) DO NOTHING",
                (call_id, run_id, conversation_id, desk_id, message_id, tool, json.dumps(args or {}),
                 args_digest(args), danger, 1 if forced else 0, plan_id, now()),
            )
            row = c.execute("SELECT * FROM approvals WHERE call_id=?", (call_id,)).fetchone()
        return row_to_dict(row, ("args",)) or {}

    def approval(self, call_id: str) -> dict[str, Any] | None:
        with self._tx() as c:
            r = c.execute("SELECT * FROM approvals WHERE call_id=?", (call_id,)).fetchone()
        return row_to_dict(r, ("args",))

    def approvals(self, *, status: str | None = "pending", desk_id: str | None = None,
                  run_id: str | None = None) -> list[dict[str, Any]]:
        where: list[str] = ["1=1"]
        params: list[Any] = []
        for col, val in (("status", status), ("desk_id", desk_id), ("run_id", run_id)):
            if val is not None:
                where.append(f"{col}=?")
                params.append(val)
        with self._tx() as c:
            rows = c.execute(f"SELECT * FROM approvals WHERE {' AND '.join(where)} ORDER BY created_at DESC",
                             tuple(params)).fetchall()
        return [d for d in (row_to_dict(r, ("args",)) for r in rows) if d]

    def decide(self, call_id: str, decision: str, *, by: str = "user", note: str = "") -> dict[str, Any] | None:
        """First decision wins: UPDATE ... WHERE call_id=? AND status='pending'; rowcount is the lock.
        None means somebody already decided it."""
        status = _DECISION_STATUS.get(decision)
        if status is None:
            raise ValueError(f"unknown approval decision {decision!r}")
        with self._tx() as c:
            cur = c.execute(
                "UPDATE approvals SET status=?, decision=?, decided_by=?, note=?, decided_at=?"
                " WHERE call_id=? AND status='pending'",
                (status, decision, by, note, now(), call_id),
            )
            if cur.rowcount != 1:
                return None
            row = c.execute("SELECT * FROM approvals WHERE call_id=?", (call_id,)).fetchone()
        return row_to_dict(row, ("args",))

    def park(self, call_id: str) -> None:
        """Leave the row pending but mark the waiting run as having let go of it.

        The status stays 'pending' on purpose (§4.5): the card must still be decidable tomorrow, from
        another window, after a restart. `decided_by='park'` is how a reader tells that no run is
        blocked on it any more, and a later decide() overwrites it with the real decider.
        """
        with self._tx() as c:
            c.execute("UPDATE approvals SET decided_by='park' WHERE call_id=? AND status='pending'", (call_id,))

    # ---- lifecycle ----
    def recover(self) -> dict[str, int]:
        """Boot-time honesty pass: nothing is resumed, everything ambiguous is labelled."""
        for run_id in list(self._buf):
            self.flush(run_id)
        self._rows.clear()
        t = now()
        marks = ",".join("?" * len(ACTIVE))
        with self._tx() as c:
            runs = [r["run_id"] for r in
                    c.execute(f"SELECT run_id FROM agent_runs WHERE status IN ({marks})", ACTIVE).fetchall()]
            c.execute(f"UPDATE agent_runs SET status='interrupted', updated_at=?, ended_at=COALESCE(ended_at,?)"
                      f" WHERE status IN ({marks})", (t, t, *ACTIVE))
            calls = c.execute("UPDATE tool_calls SET status='unknown', finished_at=? WHERE status='started'",
                              (t,)).rowcount
        salvaged = 0
        for run_id in runs:
            mid = (self.get(run_id) or {}).get("message_id")
            if not mid:
                continue
            text = self.transcript(run_id)
            if not text:
                continue
            # Only an EMPTY row: a reply that already persisted its content is not ours to overwrite.
            with self._tx() as c:
                salvaged += c.execute("UPDATE messages SET content=?, error='Interrupted' WHERE id=? AND content=''",
                                      (text, mid)).rowcount
        return {"runs": len(runs), "calls": calls, "salvaged": salvaged, "pruned": self.prune()}

    def prune(self) -> int:
        """Drop the event tape of runs that ended more than EVENTS_RETAIN_S ago. The agent_runs row
        itself stays: it is what `GET /runs` and the desk ledger are listed from."""
        with self._tx() as c:
            n = c.execute(
                "DELETE FROM run_events WHERE run_id IN"
                " (SELECT run_id FROM agent_runs WHERE ended_at IS NOT NULL AND ended_at < ?)",
                (now() - EVENTS_RETAIN_S,),
            ).rowcount
        self._rows.clear()
        return n


def _ledger_line(idx: int, row: dict[str, Any]) -> str:
    tool = row.get("tool") or "?"
    if row.get("status") == "unknown":
        return f"! {tool} — {UNKNOWN_LINE}"
    mark = {"done": "ok", "error": "err"}.get(str(row.get("status")), str(row.get("status")))
    age = _age(row.get("finished_at") or row.get("created_at"))
    return f"{idx} · {tool} · {_one_line(row.get('args'))} · {mark} · {age}"


def _one_line(args: Any) -> str:
    s = canon(args if isinstance(args, dict) else {})
    return s if len(s) <= 160 else s[:157] + "..."


def _age(ts: Any) -> str:
    if not isinstance(ts, (int, float)):
        return "just now"
    d = max(0.0, now() - ts)
    if d < 90:
        return f"{int(d)}s ago"
    if d < 5400:
        return f"{int(d // 60)}m ago"
    if d < 172800:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"
