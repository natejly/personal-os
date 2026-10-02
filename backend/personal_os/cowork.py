"""Cowork desks: one conversation, one workspace, one approved plan, run in the background.

A desk is a row before it is a task, which is the whole bet: parallelism, SSE, steering, attach and
restart survival then come from machinery that already exists. This module owns the three tables that
make that true and nothing else — no asyncio, no routes, no filesystem (that is `workspace.py`) and
no run tape (that is `runlog.py`).

Two invariants are enforced here rather than asked for politely. First, `set_status` is the ONLY
writer of `desks.status`, and it writes the column and the matching `desk_events` row inside one
transaction, so the rail and the timeline can never disagree about what a desk is doing — a failing
event insert rolls the status back with it. Second, `claim_run` and `claim_output` are locks: the
`WHERE ... AND status IN (...)` rowcount is the arbiter, so a double Start cannot produce two runs
and a double-clicked Accept cannot promote twice. Both are written as a single leading UPDATE with
no read before it, because a read-then-write transaction in WAL mode fails the second caller with
SQLITE_BUSY_SNAPSHOT instead of making it wait.

`desk_events` does two jobs on purpose: it is the human timeline AND the "inbox the rest" queue.
`needs_you=1` rows are what the rail's Needs you section and the Today card are built from, so a desk
that finished or broke while the app was closed is still visible without a poller. `recover()` is the
other half of that: on boot every LIVE desk becomes `interrupted` with a needs_you event, and nothing
is ever auto-resumed — the user presses Resume.

`desks.workspace` stores the RELATIVE `cowork/<id>` that `Workspace.rel_root` builds, never an
absolute path, so moving the data directory does not strand every desk.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Callable

from .db import Database, new_id, now, row_to_dict
from .workspace import Workspace

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__,
# before Desks(db) exists, so a _migrate entry for them would PRAGMA an absent table. Post-release
# columns need an additive ALTER in __init__ below.
SCHEMA = """
CREATE TABLE IF NOT EXISTS desks (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  project_id      TEXT REFERENCES projects(id) ON DELETE SET NULL,   -- demote to personal, like todos/docs
  title           TEXT NOT NULL DEFAULT 'Untitled desk',
  brief           TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'draft',
  status_reason   TEXT NOT NULL DEFAULT '',
  headline        TEXT NOT NULL DEFAULT '',        -- the rail's live "now" line; debounced, never per delta
  question        TEXT NOT NULL DEFAULT '',        -- set by desk_ask, cleared by the answering steer
  autonomy        TEXT NOT NULL DEFAULT 'plan',    -- plan | ask | propose
  plan_id         TEXT,
  run_id          TEXT,                            -- the run driving it right now, if any
  workspace       TEXT NOT NULL,                   -- "cowork/<id>", RELATIVE to db.data_dir
  turn            INTEGER NOT NULL DEFAULT 0,
  cost            REAL NOT NULL DEFAULT 0,
  budget          TEXT NOT NULL DEFAULT '{}',      -- {maxTurns, maxCost} overriding the global caps
  last_error      TEXT,
  archived        INTEGER NOT NULL DEFAULT 0,
  created_at      REAL NOT NULL,
  updated_at      REAL NOT NULL,
  ended_at        REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_desks_conversation ON desks(conversation_id);
CREATE INDEX        IF NOT EXISTS idx_desks_status       ON desks(status, updated_at DESC);

-- One table, two jobs: the human-readable milestone timeline AND the "inbox the rest" queue.
-- needs_you=1 rows are what the rail's "Needs you" section and the Today card are built from, so a
-- desk that finished while the app was closed is still visible without a poller.
CREATE TABLE IF NOT EXISTS desk_events (
  id         TEXT PRIMARY KEY,
  desk_id    TEXT NOT NULL REFERENCES desks(id) ON DELETE CASCADE,
  run_id     TEXT,
  kind       TEXT NOT NULL,                        -- status|plan|step|output|question|blocked|review|failed|promoted|interrupted|note
  body       TEXT NOT NULL DEFAULT '',
  data       TEXT NOT NULL DEFAULT '{}',
  needs_you  INTEGER NOT NULL DEFAULT 0,
  seen       INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_desk_events_desk   ON desk_events(desk_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_desk_events_unseen ON desk_events(seen, needs_you, created_at DESC);

-- The disk is the truth for file contents; this row records what the agent nominated, what the user
-- did with it, and whether the promoted copy was verified.
CREATE TABLE IF NOT EXISTS desk_outputs (
  id            TEXT PRIMARY KEY,
  desk_id       TEXT NOT NULL REFERENCES desks(id) ON DELETE CASCADE,
  path          TEXT NOT NULL,                     -- workspace-relative, always under outputs/
  title         TEXT NOT NULL DEFAULT '',
  summary       TEXT NOT NULL DEFAULT '',
  sha256        TEXT NOT NULL DEFAULT '',          -- at declare time; re-checked at read time (-> 'stale')
  bytes         INTEGER NOT NULL DEFAULT 0,
  run_id        TEXT,
  status        TEXT NOT NULL DEFAULT 'proposed',  -- proposed|stale|accepted|promoted|promote_failed|rejected
  promoted_kind TEXT,                              -- doc | doc_append | document | download
  promoted_id   TEXT,
  verified      INTEGER NOT NULL DEFAULT 0,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  decided_at    REAL,
  UNIQUE(desk_id, path)
);
"""

STATUSES = ("draft", "planning", "awaiting_plan", "working", "needs_approval", "blocked",
            "paused", "interrupted", "review", "done", "failed", "stopped")
NEEDS_YOU = ("awaiting_plan", "needs_approval", "blocked", "interrupted", "review")
LIVE = ("planning", "working", "needs_approval")
# What a restart has to sweep. `awaiting_plan` is not LIVE, but the card it is waiting on was held
# open by an in-process run that died with the process, so a boot that left it alone would strand
# the desk in a state nothing can wake.
RECOVER_FROM = (*LIVE, "awaiting_plan")
AUTONOMY = ("plan", "ask", "propose")
OUTPUT_KINDS = ("doc", "doc_append", "document", "download")
DESK_JSON, EVENT_JSON = ("budget",), ("data",)

# The `kind` column's vocabulary. `status` is the fallback; the others let the timeline and the
# Needs-you queue be styled without re-deriving meaning from the body text.
EVENT_KINDS = ("status", "plan", "step", "output", "question", "blocked", "review", "failed",
               "promoted", "interrupted", "note")
TERMINAL = ("done", "failed", "stopped")
# An output the user has not finished deciding. `promote_failed` is here because §6.4 wants a failed
# read-back to be retryable: the claim is cleared, so Accept can take it again.
UNDECIDED_OUTPUTS = ("proposed", "stale", "promote_failed")
CLAIMABLE_OUTPUTS = ("proposed", "stale", "promote_failed")
WORKSPACE_ROOT = "cowork"

UPDATE_FIELDS = ("title", "autonomy", "project_id", "archived")

# ---------------- prompt fragments ----------------
# These belong to the desk, not to app.py, so the runner and the tools import one copy.
DESK_HINT = """## This is a cowork desk
You are working on your own, in the background, in a private workspace directory. Scratch work goes
under `work/`; anything the user should keep goes under `outputs/` and is nominated with
`desk_deliver`. If you need a decision only the user can make, call `desk_ask` and end your turn —
do not guess and do not trail off. When the brief is finished, call `desk_done` with a short summary."""

# Only while the plan is being drafted: the tools that write, deliver or finish are not offered yet, so a
# model told nothing about why concludes they do not exist and answers in chat instead.
DESK_PLAN_HINT = """## Planning first
This desk starts in plan mode. Writing files, `desk_deliver` and `desk_done` are not offered until the user
approves a plan, so their absence now is expected. Call `propose_plan` with the steps you will take after
approval (including writing to `outputs/`, `desk_deliver` and `desk_done`); do not answer with the work in chat."""

DESK_CONTINUE = ("Continuing this desk. The approved plan below shows what is already done. Pick up at "
                 "the first unfinished step; do not redo completed work. Files you already wrote are "
                 "still in the workspace — read them rather than regenerating them.")
DESK_RESUME = ("Resuming this desk after an interruption. Check the ledger and the workspace before "
               "repeating anything: a call marked 'outcome unknown' may or may not have happened.")

# One line per status, so the timeline reads as prose rather than as a column dump.
_STATUS_BODY = {
    "draft": "Desk created.",
    "planning": "Planning.",
    "awaiting_plan": "Waiting for you to approve the plan.",
    "working": "Working.",
    "needs_approval": "Waiting for your approval.",
    "blocked": "Blocked.",
    "paused": "Paused.",
    "interrupted": "Interrupted when the app stopped. Nothing was resumed for you.",
    "review": "Finished. Outputs are waiting for review.",
    "done": "Done.",
    "failed": "Failed.",
    "stopped": "Stopped.",
}
# A status whose own event kind is more specific than "status".
_STATUS_KIND = {"awaiting_plan": "plan", "blocked": "blocked", "interrupted": "interrupted",
                "review": "review", "failed": "failed"}


def _body(status: str, reason: str, error: str | None) -> str:
    base = _STATUS_BODY.get(status, status)
    if error:
        return f"{base} {error}".strip()
    if reason:
        return f"{base} ({reason})"
    return base


class Desks:
    def __init__(self, db: Database, workspace: Workspace | None = None) -> None:
        """`workspace` is optional only so the app can keep §3.3's construction order; without it the
        desk's directory is created lazily by the first `desk_*` tool call instead of at create time."""
        self.db = db
        self.workspace = workspace
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------------- views ----------------
    @staticmethod
    def _unseen(c: sqlite3.Connection, desk_id: str | None = None) -> dict[str, int]:
        sql = "SELECT desk_id, COUNT(*) FROM desk_events WHERE seen=0 AND needs_you=1"
        args: list[Any] = []
        if desk_id is not None:
            sql += " AND desk_id=?"
            args.append(desk_id)
        return {r[0]: r[1] for r in c.execute(sql + " GROUP BY desk_id", args).fetchall()}

    @staticmethod
    def _desk_view(r: sqlite3.Row, unseen: int) -> dict[str, Any]:
        d = row_to_dict(r, DESK_JSON) or {}
        d["archived"] = bool(d["archived"])
        d["live"] = d["status"] in LIVE
        d["unseen"] = unseen
        return d

    @staticmethod
    def _event_view(r: sqlite3.Row) -> dict[str, Any]:
        e = row_to_dict(r, EVENT_JSON) or {}
        e["needs_you"], e["seen"] = bool(e["needs_you"]), bool(e["seen"])
        return e

    @staticmethod
    def _output_view(r: sqlite3.Row) -> dict[str, Any]:
        o = row_to_dict(r) or {}
        o["verified"] = bool(o["verified"])
        return o

    def _one(self, c: sqlite3.Connection, id: str) -> dict[str, Any] | None:
        r = c.execute("SELECT * FROM desks WHERE id=?", (id,)).fetchone()
        return self._desk_view(r, self._unseen(c, id).get(id, 0)) if r else None

    # ---------------- desks ----------------
    def list(self, project_id: str | None = "__all__", status: str | None = None,
             archived: bool = False) -> list[dict[str, Any]]:
        where, args = ["archived = ?"], [1 if archived else 0]
        if project_id != "__all__":
            if project_id is None:
                where.append("project_id IS NULL")
            else:
                where.append("project_id = ?")
                args.append(project_id)
        if status:
            where.append("status = ?")
            args.append(status)
        sql = f"SELECT * FROM desks WHERE {' AND '.join(where)} ORDER BY updated_at DESC"
        with self.db.tx() as c:
            rows = c.execute(sql, args).fetchall()
            unseen = self._unseen(c)
        return [self._desk_view(r, unseen.get(r["id"], 0)) for r in rows]

    def get(self, id: str, with_outputs: bool = True) -> dict[str, Any] | None:
        with self.db.tx() as c:
            d = self._one(c, id)
            if d and with_outputs:
                rows = c.execute("SELECT * FROM desk_outputs WHERE desk_id=? ORDER BY created_at, rowid",
                                 (id,)).fetchall()
                d["outputs"] = [self._output_view(r) for r in rows]
        return d

    def by_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM desks WHERE conversation_id=?", (conversation_id,)).fetchone()
            return self._one(c, r["id"]) if r else None

    def create(self, *, conversation_id: str, brief: str, title: str = "",
               project_id: str | None = None, autonomy: str = "plan",
               budget: dict[str, Any] | None = None) -> dict[str, Any]:
        if autonomy not in AUTONOMY:
            raise ValueError(f"Unknown autonomy: {autonomy}")
        did = new_id()
        t = now()
        name = (title or "").strip() or _title_from(brief)
        rel = self.workspace.rel_root(did) if self.workspace else f"{WORKSPACE_ROOT}/{did}"
        if self.workspace:
            self.workspace.ensure(did)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO desks(id,conversation_id,project_id,title,brief,status,status_reason,headline,"
                "question,autonomy,plan_id,run_id,workspace,turn,cost,budget,last_error,archived,"
                "created_at,updated_at,ended_at)"
                " VALUES(?,?,?,?,?,'draft','','','',?,NULL,NULL,?,0,0,?,NULL,0,?,?,NULL)",
                (did, conversation_id, project_id, name, brief, autonomy, rel,
                 json.dumps(budget or {}), t, t),
            )
            self._event(c, did, "status", _body("draft", "", None), needs_you=False, run_id=None,
                        data={"status": "draft"}, t=t)
            return self._one(c, did)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Whitelist {title, autonomy, project_id, archived}; `budget` merges rather than replaces."""
        fields = {k: v for k, v in patch.items() if k in UPDATE_FIELDS}
        if "autonomy" in fields and fields["autonomy"] not in AUTONOMY:
            raise ValueError(f"Unknown autonomy: {fields['autonomy']}")
        if "title" in fields:
            fields["title"] = str(fields["title"] or "").strip() or "Untitled desk"
        if "archived" in fields:
            fields["archived"] = int(bool(fields["archived"]))
        with self.db.tx() as c:
            row = c.execute("SELECT budget FROM desks WHERE id=?", (id,)).fetchone()
            if not row:
                return None
            if isinstance(patch.get("budget"), dict):
                # Merge, so sending {maxCost} does not silently drop maxTurns.
                fields["budget"] = json.dumps({**json.loads(row["budget"] or "{}"), **patch["budget"]})
            if fields:
                fields["updated_at"] = now()
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE desks SET {sets} WHERE id=?", (*fields.values(), id))
            return self._one(c, id)

    def set_status(self, id: str, status: str, *, reason: str = "", headline: str | None = None,
                   question: str | None = None, error: str | None = None, plan_id: str | None = None,
                   run_id: str | None = None, event: bool = True) -> dict[str, Any] | None:
        """The ONLY writer of `status`. One transaction writes the column and appends the matching
        desk_events row, so status and timeline can never disagree."""
        if status not in STATUSES:
            raise ValueError(f"Unknown desk status: {status}")
        t = now()
        fields: dict[str, Any] = {"status": status, "status_reason": reason, "updated_at": t}
        if headline is not None:
            fields["headline"] = headline
        if question is not None:
            fields["question"] = question
        if error is not None:
            fields["last_error"] = error
        if plan_id is not None:
            fields["plan_id"] = plan_id
        if run_id is not None:
            fields["run_id"] = run_id
        elif status not in LIVE:
            # Nothing is driving a desk that is not live; leaving a stale run_id makes the rail lie.
            fields["run_id"] = None
        fields["ended_at"] = t if status in TERMINAL else None
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            if not c.execute("SELECT 1 FROM desks WHERE id=?", (id,)).fetchone():
                return None
            c.execute(f"UPDATE desks SET {sets} WHERE id=?", (*fields.values(), id))
            if event:
                self._event(c, id, _STATUS_KIND.get(status, "status"), _body(status, reason, error),
                            needs_you=status in NEEDS_YOU, run_id=fields.get("run_id"),
                            data={"status": status, "reason": reason, "error": error}, t=t)
            return self._one(c, id)

    def set_headline(self, id: str, headline: str) -> None:
        """Debounced by the caller (see DeskRuntime, §3.5). Never called from a delta."""
        with self.db.tx() as c:
            c.execute("UPDATE desks SET headline=? WHERE id=?", (headline, id))

    def update_run(self, id: str, run_id: str | None) -> dict[str, Any] | None:
        """Which run is driving the desk right now. Not a status change, so no event."""
        with self.db.tx() as c:
            c.execute("UPDATE desks SET run_id=?, updated_at=? WHERE id=?", (run_id, now(), id))
            return self._one(c, id)

    def claim_run(self, id: str, from_statuses: tuple[str, ...]) -> dict[str, Any] | None:
        """The start lock: the UPDATE's rowcount decides, so a double Start makes one run, not two.
        None means somebody else already started this desk. A desk with no plan claims into
        `planning`, one with a plan into `working` — the CASE keeps it a single statement, because a
        SELECT before the UPDATE would make the losing caller fail with a snapshot error instead of
        waiting for the winner to commit."""
        if not from_statuses:
            return None
        t = now()
        marks = ",".join("?" for _ in from_statuses)
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE desks SET status = CASE WHEN COALESCE(plan_id,'')='' THEN 'planning' ELSE 'working' END,"
                " status_reason='', run_id=NULL, last_error=NULL, ended_at=NULL, updated_at=?"
                f" WHERE id=? AND status IN ({marks})",
                (t, id, *from_statuses),
            )
            if not cur.rowcount:
                return None
            row = c.execute("SELECT status FROM desks WHERE id=?", (id,)).fetchone()
            self._event(c, id, "status", _body(row["status"], "", None), needs_you=False, run_id=None,
                        data={"status": row["status"], "reason": "", "error": None}, t=t)
            return self._one(c, id)

    def charge(self, id: str, cost: float, turn_delta: int = 1) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE desks SET cost = cost + ?, turn = turn + ?, updated_at=? WHERE id=?",
                      (float(cost or 0.0), int(turn_delta), now(), id))
            return self._one(c, id)

    def settle(self, id: str, *, partial: str | None, stopped: bool, error: str | None,
               chain: bool = False) -> dict[str, Any]:
        """What a finished desk turn means, decided from the three facts the run ends with.

        `chain=True` says the supervisor has already decided another turn follows, so the desk stays
        `working`; that flag is why a budget-window stop can be settled honestly without the row
        having to guess. A desk that is no longer LIVE settled itself during the turn (`desk_ask`,
        `desk_done`, a park, a pause) and is left exactly as it is — only a stop or a crash outranks
        a decision the desk already made.
        """
        desk = self.get(id, with_outputs=False)
        if not desk:
            return {}
        if stopped:
            target, reason = "stopped", "stopped"
        elif error:
            target, reason = "failed", "error"
        elif desk["status"] not in LIVE:
            return desk
        elif chain:
            return desk
        elif partial == "blocked":
            target, reason = "blocked", "approval"
        elif partial:
            # rounds | tokens | time | cost from Budget.exceeded(), or "loop" from the repeat breaker.
            target, reason = "review", "budget" if partial != "loop" else "loop"
        else:
            # The reply ended without `desk_done`. A terminal state is a decision, not an inference,
            # so this is never `done`: it is review when there is something to review and otherwise a
            # block the user has to look at.
            with self.db.tx() as c:
                undecided = self._undecided(c, id)
            target, reason = ("review", "ended") if undecided else ("blocked", "ended")
        if desk["status"] == target:
            return desk
        return self.set_status(id, target, reason=reason, headline="", error=error) or desk

    def live_count(self) -> int:
        with self.db.tx() as c:
            marks = ",".join("?" for _ in LIVE)
            return int(c.execute(f"SELECT COUNT(*) FROM desks WHERE status IN ({marks})", LIVE).fetchone()[0])

    def recover(self) -> int:
        """Startup: every row whose status is in RECOVER_FROM becomes 'interrupted' with a needs_you
        event. No desk is ever auto-resumed at startup, and a desk the user parked (paused, blocked)
        is left exactly as it is — only a state whose in-process run died is swept."""
        with self.db.tx() as c:
            marks = ",".join("?" for _ in RECOVER_FROM)
            ids = [r["id"] for r in
                   c.execute(f"SELECT id FROM desks WHERE status IN ({marks})", RECOVER_FROM).fetchall()]
        for did in ids:
            self.set_status(did, "interrupted", reason="restart", headline="")
        return len(ids)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM desks WHERE id=?", (id,))

    # ---------------- events ----------------
    @staticmethod
    def _event(c: sqlite3.Connection, desk_id: str, kind: str, body: str, *, needs_you: bool,
               run_id: str | None, data: dict[str, Any], t: float) -> str:
        eid = new_id()
        c.execute(
            "INSERT INTO desk_events(id,desk_id,run_id,kind,body,data,needs_you,seen,created_at)"
            " VALUES(?,?,?,?,?,?,?,0,?)",
            (eid, desk_id, run_id, kind, body, json.dumps(data), int(needs_you), t),
        )
        return eid

    def events(self, id: str, limit: int = 200) -> list[dict[str, Any]]:
        """The last `limit` events, oldest first — a timeline is read downwards."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM desk_events WHERE desk_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                             (id, max(1, int(limit)))).fetchall()
        return [self._event_view(r) for r in reversed(rows)]

    def event(self, id: str, kind: str, body: str = "", *, needs_you: bool = False,
              run_id: str | None = None, **data: Any) -> dict[str, Any]:
        t = now()
        with self.db.tx() as c:
            eid = self._event(c, id, kind, body, needs_you=needs_you, run_id=run_id, data=data, t=t)
            r = c.execute("SELECT * FROM desk_events WHERE id=?", (eid,)).fetchone()
        return self._event_view(r)

    def inbox(self, limit: int = 40) -> list[dict[str, Any]]:
        """Unseen needs_you rows across every desk: what the Today card and the rail triage from."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT e.*, d.title AS desk_title FROM desk_events e JOIN desks d ON d.id = e.desk_id"
                # An archived desk is one the user has put away; its unseen rows must not keep the
                # Needs-you badge lit, and the renderer cannot filter them out — a DeskEvent carries
                # no archived flag, and an archived desk is never in the loaded desk list to match on.
                " WHERE e.seen=0 AND e.needs_you=1 AND d.archived=0 ORDER BY e.created_at DESC LIMIT ?",
                (max(1, int(limit)),)).fetchall()
        return [self._event_view(r) for r in rows]

    def mark_seen(self, event_id: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE desk_events SET seen=1 WHERE id=?", (event_id,))

    def mark_desk_seen(self, desk_id: str) -> int:
        """Every unseen needs_you row of one desk, in one statement. `_unseen` counts the desk's
        rows unbounded, so sweeping a page of them would leave a badge the user cannot clear."""
        with self.db.tx() as c:
            cur = c.execute("UPDATE desk_events SET seen=1 WHERE desk_id=? AND seen=0 AND needs_you=1",
                            (desk_id,))
            return cur.rowcount or 0

    # ---------------- outputs ----------------
    @staticmethod
    def _undecided(c: sqlite3.Connection, desk_id: str) -> int:
        marks = ",".join("?" for _ in UNDECIDED_OUTPUTS)
        return int(c.execute(f"SELECT COUNT(*) FROM desk_outputs WHERE desk_id=? AND status IN ({marks})",
                             (desk_id, *UNDECIDED_OUTPUTS)).fetchone()[0])

    def outputs(self, desk_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM desk_outputs WHERE desk_id=? ORDER BY created_at, rowid",
                             (desk_id,)).fetchall()
        return [self._output_view(r) for r in rows]

    def output(self, output_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM desk_outputs WHERE id=?", (output_id,)).fetchone()
        return self._output_view(r) if r else None

    def declare_output(self, desk_id: str, path: str, title: str, summary: str,
                       sha256: str, bytes_: int, run_id: str | None) -> dict[str, Any]:
        """INSERT ... ON CONFLICT(desk_id, path) DO UPDATE: re-writing a file updates one row — and
        resets its decision, because new bytes have not been reviewed."""
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO desk_outputs(id,desk_id,path,title,summary,sha256,bytes,run_id,status,"
                "promoted_kind,promoted_id,verified,created_at,updated_at,decided_at)"
                " VALUES(?,?,?,?,?,?,?,?,'proposed',NULL,NULL,0,?,?,NULL)"
                " ON CONFLICT(desk_id,path) DO UPDATE SET title=excluded.title, summary=excluded.summary,"
                " sha256=excluded.sha256, bytes=excluded.bytes, run_id=excluded.run_id, status='proposed',"
                " promoted_kind=NULL, promoted_id=NULL, verified=0, updated_at=excluded.updated_at,"
                " decided_at=NULL",
                (new_id(), desk_id, path, title, summary, sha256, int(bytes_), run_id, t, t),
            )
            r = c.execute("SELECT * FROM desk_outputs WHERE desk_id=? AND path=?", (desk_id, path)).fetchone()
            self._event(c, desk_id, "output", f"Delivered {path}", needs_you=False, run_id=run_id,
                        data={"output_id": r["id"], "path": path, "title": title}, t=t)
        return self._output_view(r)

    def claim_output(self, output_id: str) -> dict[str, Any] | None:
        """The promotion lock: the rowcount decides, so a double-clicked Accept promotes once.
        `promote_failed` is claimable again because a failed read-back must be retryable (§6.4)."""
        t = now()
        marks = ",".join("?" for _ in CLAIMABLE_OUTPUTS)
        with self.db.tx() as c:
            cur = c.execute(
                f"UPDATE desk_outputs SET status='accepted', verified=0, updated_at=?, decided_at=?"
                f" WHERE id=? AND status IN ({marks})",
                (t, t, output_id, *CLAIMABLE_OUTPUTS),
            )
            if not cur.rowcount:
                return None
            r = c.execute("SELECT * FROM desk_outputs WHERE id=?", (output_id,)).fetchone()
        return self._output_view(r)

    def finish_output(self, output_id: str, *, kind: str, ref: str | None,
                      verified: bool) -> dict[str, Any] | None:
        """An unverified promotion is `promote_failed`, never a tick: the read-back is the evidence."""
        if kind not in OUTPUT_KINDS:
            raise ValueError(f"Unknown output destination: {kind}")
        t = now()
        status = "promoted" if verified else "promote_failed"
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE desk_outputs SET status=?, promoted_kind=?, promoted_id=?, verified=?, updated_at=?,"
                " decided_at=COALESCE(decided_at, ?) WHERE id=?",
                (status, kind, ref, int(bool(verified)), t, t, output_id),
            )
            if not cur.rowcount:
                return None
            r = c.execute("SELECT * FROM desk_outputs WHERE id=?", (output_id,)).fetchone()
            if verified:
                self._event(c, r["desk_id"], "promoted", f"Promoted {r['path']} to {kind}.",
                            needs_you=False, run_id=r["run_id"],
                            data={"output_id": output_id, "kind": kind, "ref": ref}, t=t)
        return self._output_view(r)

    def reject_output(self, output_id: str) -> dict[str, Any] | None:
        t = now()
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE desk_outputs SET status='rejected', updated_at=?, decided_at=? WHERE id=? AND status<>'promoted'",
                (t, t, output_id))
            if not cur.rowcount:
                return None
            r = c.execute("SELECT * FROM desk_outputs WHERE id=?", (output_id,)).fetchone()
        return self._output_view(r)


def _title_from(brief: str) -> str:
    """A desk needs a name in the rail before the agent has said anything, so the brief gives one."""
    line = (brief or "").strip().splitlines()[0].strip() if (brief or "").strip() else ""
    return (line[:57] + "…") if len(line) > 58 else (line or "Untitled desk")


# ---------------- the rail's live label ----------------
HEADLINE_FLUSH_S = 1.0

# tool -> the verb the rail shows. Anything missing falls back to the tool name with its underscores
# opened out, which reads acceptably for every tool in the tree.
_VERBS = {
    "web_search": "searching the web",
    "fetch_url": "reading a page",
    "run_python": "running code",
    "desk_list_files": "listing its files",
    "desk_read_file": "reading",
    "desk_write_file": "writing",
    "desk_trash_file": "trashing",
    "desk_deliver": "delivering",
    "desk_import_sandbox": "importing",
    "desk_ask": "asking you",
    "desk_done": "wrapping up",
    "propose_plan": "writing a plan",
}
_STATUS_HEADLINE = {
    "planning": "planning",
    "awaiting_plan": "waiting on your plan",
    "working": "working",
    "needs_approval": "waiting on your approval",
    "blocked": "waiting on you",
    "paused": "paused",
    "review": "waiting on your review",
}
# Only these change what the rail says; every other event, `delta` first and foremost, is dropped
# before anything is computed.
_OBSERVED = ("tool_call", "tool_result", "plan", "desk_status")
# A status moved: flush now rather than up to a second late, because this is the one the user is
# waiting to see.
_IMMEDIATE = ("plan", "desk_status")


def _call_headline(data: dict[str, Any]) -> str:
    name = str(data.get("name") or "")
    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    verb = _VERBS.get(name) or name.replace("_", " ") or "working"
    obj = args.get("path") or args.get("sandbox_path") or ""
    return f"{verb} {obj}".strip()[:120] if obj else verb


class DeskRuntime:
    """The rail's live label, without a database write per token.

    A desk run publishes hundreds of `delta` events a second and the rail shows one line, so the
    headline is computed in memory and flushed at most once a second — immediately only when the
    status itself moved. `observe` returns the desk row to publish as a `desk_status` event, or None
    when there is nothing new, and every call site wraps it in
    `except Exception: pass  # noqa: BLE001 - a rail label must never kill a run`.
    """

    def __init__(self, desks: Desks, desk_id: str, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.desks = desks
        self.desk_id = desk_id
        self._clock = clock
        self.headline = ""
        self._flushed_at = 0.0
        self._pending = False

    def observe(self, event: str, data: Any) -> dict[str, Any] | None:
        if event not in _OBSERVED:
            return None
        d = data if isinstance(data, dict) else {}
        if event == "tool_call":
            headline = _call_headline(d)
        elif event == "tool_result":
            headline = "thinking"
        elif event == "plan":
            headline = _STATUS_HEADLINE["awaiting_plan"]
        else:
            headline = _STATUS_HEADLINE.get(str(d.get("status") or ""), "")
        if headline and headline != self.headline:
            self.headline, self._pending = headline, True
        if not self._pending:
            return None
        t = self._clock()
        if event not in _IMMEDIATE and t - self._flushed_at < HEADLINE_FLUSH_S:
            return None
        self._flushed_at, self._pending = t, False
        self.desks.set_headline(self.desk_id, self.headline)
        return self.desks.get(self.desk_id, with_outputs=False)
