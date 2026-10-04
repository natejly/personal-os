# Cowork + Planning Mode — implementation spec


> **Note (integration).** This spec was written for the branch's own `ActionPlans` module and run
> tape. What shipped is the desk half of it on main's substrate: `plans.Plans` (propose_plan) is the
> approval record, `runs.RunStore` is the tape, and a plan is decided through `POST /approvals/{call_id}`
> rather than through a route of its own. The desk model, the workspace rules, the autonomy modes,
> parking and the chaining guards are as described; `decide_call` as a single pure function is not —
> its rules live next to main's gate in `_chat_stream`. Names like `ActionPlan*` and `aplans` below
> refer to the branch's module, not to anything in the tree.
>
> **Update (worktree-cowork-next).** The run ledger in §3.11 was not built. A woken or chained turn
> gets two things instead. First, `Plans.block(plan)`: the approved plan with each step's status,
> re-sent at the end of every round. Second, `cowork.parked_report`: what the user decided on parked
> cards. That report is read from `approvals.parked_at` / `reported_at`, and an approved parked call
> is spent once through `RunStore.claim_parked`. `desk_ask` is answered on its approval (`note`), not
> by a second turn. Every desk write reaches `GET /events` as `desk_status` through `Desks.on_change`.

Target worktree: `/Users/natejly/Desktop/Personal OS/.claude/worktrees/cowork-planning` (branch `worktree-cowork-planning`, forked from `main` at `ebfa585`).
Every `file:line` below was read in this tree. Where a line number is quoted, that line really is what the text says it is.

---

## 1. Summary and the architectural bet

Two features, one mechanism.

**Cowork** gives the user *desks*: named background sessions started from a task brief. Each desk owns one
conversation, one workspace directory, and one approved plan. Several run in parallel. A desk has a live
status, a streaming activity feed the user can open, leave and come back to, a steer box, pause/stop, and a
review surface where its output files are previewed, diffed, and promoted into the app.

**Planning mode** makes the agent draft an ordered plan — exact tool, exact arguments, one line of why — and
blocks every consequential tool until the user approves, edits or rejects it. It is a guard enforced in three
places, not a prompt. It works the same inline in an ordinary chat and as stage one of every desk.

### The bet

> **A desk is one conversation plus one workspace plus one approved plan, and every run is a row before it is a
> task.** Parallelism, SSE, steering, attach, approvals and persistence then come free from machinery that
> already exists; the only genuinely new things are the plan contract, the workspace, the review surface, and a
> thin durable tape under the run bus.

Four consequences we accept deliberately:

1. **One conversation per desk.** `RunBus` is keyed by `conversation_id` only (`runs.py:127`, `runs.py:142`), and
   `POST /conversations/{id}/chat` 409s on a second live run (`app.py:826-829`). Giving each desk its own
   conversation means N desks are N ordinary runs with *zero* bus surgery, and the desk gets
   `microvm.Sandboxes` per-conversation isolation (`microvm.py:107`) for free. The cost is one filter on
   `Conversations.list`, enumerated in §3.9.
2. **The row is the truth; the ring is a cache.** A new `runlog.py` writes every run, every published event
   (deltas coalesced), every tool call and every approval to SQLite. This is what makes a desk survivable,
   resumable, and decidable from another window or after a restart. Approvals stop being process-memory futures
   that a restart silently orphans.
3. **Long autonomy is bought by chaining bounded replies, never by raising `maxToolRounds`.** `maxToolRounds`
   stays 25 (`llm.py:54`). A desk that exhausts a reply's budget mid-plan starts a *fresh* bounded run against
   the same approved plan, guarded by a desk-level turn budget. That is a standing project anti-goal
   honoured, not dodged.
4. **The plan binds arguments, not intentions.** Approving step 3 approves `sha256(canonical(args))` for that
   one call, once. A different argument, a second use, or an unplanned call still shows a card.

### What we are not building

No second event bus. No heartbeat or poller. No scheduler. No `cowork_spawn` tool (an agent that fans out agents
spends the budget without a gate). No pixel clicking. No new heavy dependency — `hashlib`, `difflib`, `shutil`,
`pathlib` are stdlib.

---

## 2. Data model

Four new modules own nine tables. **None of them goes in `db.py`.** `Database._migrate` (`db.py:154-166`) runs
inside `Database.__init__`, which is `app.py:50` — before any of these classes exist, so a `_migrate` entry for
their tables would `PRAGMA table_info` an absent table. Each module declares a module-level `SCHEMA` and runs
`c.executescript(SCHEMA)` in its repo constructor, the `canvas.py:21-30` / `docs.py` pattern. Post-release
columns get an additive `PRAGMA table_info` + `ALTER TABLE` loop in that constructor (`todos.py:49-53` is the
only worked example in the tree).

Construction order in `app.py` is schema-creation order and is load-bearing: `RunStore` → `ActionPlans` →
`Desks`. `action_plans.run_id` and `desks.conversation_id` carry real foreign keys, so their referents must
exist first.

### 2.1 `backend/personal_os/runlog.py` — the tape

```sql
-- One row per run, written BEFORE the asyncio task is spawned, so a crash one instruction later
-- still leaves something recoverable.
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
  decided_by      TEXT,                             -- user|stop|shutdown|timeout
  note            TEXT NOT NULL DEFAULT '',
  created_at      REAL NOT NULL,
  decided_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_desk   ON approvals(desk_id, status);
```

Module constants: `DELTA_FLUSH_CHARS = 2048`, `DELTA_FLUSH_S = 1.0`, `MAX_RUN_EVENTS = 50_000`,
`EVENTS_RETAIN_S = 14 * 86400`, `RESULT_CAP = 64_000`, `ACTIVE = ("running", "awaiting")`,
`PROMOTE_STEP = -2`, `RECOVER_STEP = -3`.

```python
def args_digest(args: dict[str, Any]) -> str:
    """The one canonicalisation everything binds to. lib/planDigest.ts mirrors it byte for byte;
    if the two ever diverge, claim() silently never matches and every approved step re-prompts."""

def call_key(run_id: str, step: int, tool: str, digest: str) -> str: ...
def sse(event: str, data: Any, seq: int | None = None) -> str:
    """runs.sse plus `id: <seq>`, so a reconnecting client can resume from the last event it saw."""

class RunStore:
    def __init__(self, db: Database) -> None: ...
    # runs
    def create(self, run_id: str, conversation_id: str, *, kind: str = "chat", desk_id: str | None = None,
               turn: int = 0, input: dict[str, Any] | None = None) -> None: ...
    def update(self, run_id: str, **fields: Any) -> None: ...
    def get(self, run_id: str) -> dict[str, Any] | None: ...
    def latest(self, conversation_id: str) -> dict[str, Any] | None: ...
    def list(self, *, status: tuple[str, ...] | None = None, kind: str | None = None,
             conversation_id: str | None = None, desk_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    # events
    def append(self, run_id: str, seq: int, type: str, data: Any) -> None:
        """Coalesces consecutive `delta` events into one row; flushes on 2KB, 1s, any other event, or end()."""
    def flush(self, run_id: str) -> None: ...
    def last_seq(self, run_id: str) -> int: ...
    async def tail(self, run_id: str, since: int = 0) -> AsyncIterator[str]:
        """Replay a run that has left memory, as SSE, then close. Never follows live."""
    def transcript(self, run_id: str) -> str:
        """Concatenated delta text, for salvaging an interrupted reply into its message row."""
    # tool calls
    async def call_once(self, run_id: str, step: int, tool: str, args: dict[str, Any],
                        fn: Callable[[], Awaitable[Any]], *, call_id: str | None = None,
                        desk_id: str | None = None) -> Any:
        """Row committed at 'started' BEFORE fn is awaited. A 'done' row returns its stored result
        without calling. An 'unknown' row returns a shaped tool_error and is never re-executed."""
    def executed(self, run_id: str) -> list[dict[str, Any]]: ...
    def ledger(self, desk_id: str, limit: int = 120) -> list[dict[str, Any]]: ...
    # approvals
    def open_approval(self, *, call_id: str, run_id: str, conversation_id: str, desk_id: str | None,
                      message_id: str | None, tool: str, args: dict[str, Any], danger: str,
                      forced: bool, plan_id: str | None = None) -> dict[str, Any]: ...
    def approval(self, call_id: str) -> dict[str, Any] | None: ...
    def approvals(self, *, status: str | None = "pending", desk_id: str | None = None,
                  run_id: str | None = None) -> list[dict[str, Any]]: ...
    def decide(self, call_id: str, decision: str, *, by: str = "user", note: str = "") -> dict[str, Any] | None:
        """First decision wins: UPDATE ... WHERE call_id=? AND status='pending'; rowcount is the lock.
        None means somebody already decided it."""
    def park(self, call_id: str) -> None:
        """Leave the row pending but mark the waiting run as having let go of it."""
    # lifecycle
    def recover(self) -> dict[str, int]: ...
    def prune(self) -> None: ...
```

`RunStore` keeps **one long-lived connection** guarded by a `threading.Lock`, with
`PRAGMA synchronous=NORMAL` and `PRAGMA foreign_keys=ON` issued on it. This is the only place in the tree that
does so, and it needs a comment saying why: it is written from inside the event loop on every coalesced flush
and every tool call, and `Database.tx()` (`db.py:174-184`) opens a fresh connection per call. The connection is
obtained through `db.connect()` (`db.py:168-172`) so the foreign-keys pragma is applied the same way as
everywhere else, and no other module shares it.

### 2.2 `backend/personal_os/plans.py` — the approval artifact

```sql
-- An APPROVAL artifact. Not a progress checklist and not working memory: nothing here is the
-- model's to-do list. Named action_plans / ActionPlan* throughout so it can coexist with a future
-- working-memory `chat_plans` / `Plan` / `PlanStep` without a rename.
CREATE TABLE IF NOT EXISTS action_plans (
  plan_id         TEXT PRIMARY KEY,
  call_id         TEXT UNIQUE,                      -- the approvals row this plan is decided through
  run_id          TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  desk_id         TEXT,
  message_id      TEXT,
  title           TEXT NOT NULL DEFAULT '',
  intent          TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|superseded
  tainted         INTEGER NOT NULL DEFAULT 0,       -- the reply was already tainted when proposed
  expected_taint  TEXT NOT NULL DEFAULT '[]',       -- JSON: tool names in this plan that taint (see §4.6)
  note            TEXT NOT NULL DEFAULT '',
  decided_by      TEXT,                             -- user | stop | shutdown
  created_at      REAL NOT NULL,
  decided_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_action_plans_conv ON action_plans(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_plans_desk ON action_plans(desk_id, created_at DESC);

CREATE TABLE IF NOT EXISTS plan_steps (
  step_id     TEXT PRIMARY KEY,
  plan_id     TEXT NOT NULL REFERENCES action_plans(plan_id) ON DELETE CASCADE,
  idx         INTEGER NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  tool        TEXT NOT NULL DEFAULT '',             -- '' = a reasoning step that calls nothing
  args        TEXT NOT NULL DEFAULT '{}',
  args_digest TEXT NOT NULL DEFAULT '',             -- runlog.args_digest(args); '' when tool is ''
  why         TEXT NOT NULL DEFAULT '',
  danger      TEXT NOT NULL DEFAULT 'safe',         -- snapshotted at propose time, for the card
  status      TEXT NOT NULL DEFAULT 'proposed',     -- proposed|approved|consumed|done|failed|dropped|rejected
  edited      INTEGER NOT NULL DEFAULT 0,
  call_id     TEXT,
  result_error TEXT,
  consumed_at REAL,
  UNIQUE(plan_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_plan_steps_claim ON plan_steps(plan_id, tool, args_digest, status);
```

Constants: `PLAN_TOOL = "propose_plan"`, `MAX_STEPS = 12`, `PLAN_STATUSES`, `STEP_STATUSES`,
`STEP_JSON = ("args",)`, `PLAN_JSON = ("expected_taint",)`.

```python
def normalize_plan(args: dict[str, Any], modes: dict[str, str], specs: dict[str, ToolSpec]
                   ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Validate BEFORE any card is shown, against the UNFILTERED mode map (what will be callable
    after approval), so the user is never asked to approve a plan that cannot execute.
    Caps at MAX_STEPS; every non-empty `tool` must exist in specs, be available(), and not be 'off'.
    Computes args_digest and copies ToolSpec.danger onto each step. Returns (steps, tool_error|None)."""

def parse_plan_edits(raw: Any) -> dict[int, dict[str, Any] | None]:
    """[{idx, arguments}|{idx, drop:true}] -> {idx: args or None}. Raises ValueError on a bad shape."""

class ActionPlans:
    def __init__(self, db: Database) -> None: ...
    def open(self, *, conversation_id: str, desk_id: str | None, run_id: str, message_id: str | None,
             call_id: str, title: str, intent: str, steps: list[dict[str, Any]], tainted: bool) -> dict[str, Any]: ...
    def get(self, plan_id: str, with_steps: bool = True) -> dict[str, Any] | None: ...
    def by_call(self, call_id: str) -> dict[str, Any] | None: ...
    def for_desk(self, desk_id: str) -> dict[str, Any] | None: ...
    def active(self, conversation_id: str) -> dict[str, Any] | None:
        """Newest 'approved' plan with at least one unconsumed step. None means plan mode still applies."""
    def latest(self, conversation_id: str) -> dict[str, Any] | None: ...
    def supersede(self, conversation_id: str) -> None:
        """One live plan per conversation; called just before open()."""
    def decide(self, plan_id: str, decision: str, *, steps: dict[int, dict[str, Any] | None] | None = None,
               note: str = "", decided_by: str = "user") -> dict[str, Any] | None:
        """UPDATE ... WHERE plan_id=? AND status='pending' — rowcount is the lock. On approve: edited
        steps get a recomputed digest and edited=1; omitted indexes become 'dropped'."""
    def claim(self, plan_id: str, tool: str, args: dict[str, Any], call_id: str) -> dict[str, Any] | None:
        """The single-use atomic bind. UPDATE plan_steps SET status='consumed', call_id=?, consumed_at=?
        WHERE step_id = (SELECT step_id ... WHERE plan_id=? AND tool=? AND args_digest=? AND
        status='approved' ORDER BY idx LIMIT 1) AND status='approved'. rowcount==1 or nothing."""
    def finish(self, call_id: str, ok: bool, error: str | None = None) -> None:   # consumed -> done | failed
    def rejected(self, conversation_id: str, tool: str, args: dict[str, Any]) -> bool:
        """Read-only blocklist, conversation-scoped, honouring only decided_by='user', so a Stop-induced
        rejection does not poison the rest of the conversation."""
    def remaining(self, plan_id: str) -> list[dict[str, Any]]: ...
    def block(self, plan_id: str) -> str | None:
        """The approved plan as a `[x] / [>] / [ ]` checklist, re-injected as the LAST system message
        each round. convos.history() (repos.py:174-180) replays prose only, so without this the plan is
        invisible to the model from turn two onward."""
    def model_result(self, plan: dict[str, Any]) -> dict[str, Any]: ...
```

### 2.3 `backend/personal_os/cowork.py` — desks

```sql
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
  budget          TEXT NOT NULL DEFAULT '{}',      -- {maxTurns} overriding the global cap
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
```

Constants:

```python
STATUSES   = ("draft","planning","awaiting_plan","working","needs_approval","blocked",
              "paused","interrupted","review","done","failed","stopped")
NEEDS_YOU  = ("awaiting_plan","needs_approval","blocked","interrupted","review")
LIVE       = ("planning","working","needs_approval")
AUTONOMY   = ("plan","ask","propose")
OUTPUT_KINDS = ("doc","doc_append","document","download")
DESK_JSON, EVENT_JSON = ("budget",), ("data",)
```

```python
class Desks:
    def __init__(self, db: Database) -> None: ...
    def list(self, project_id: str | None = "__all__", status: str | None = None,
             archived: bool = False) -> list[dict[str, Any]]: ...
    def get(self, id: str, with_outputs: bool = True) -> dict[str, Any] | None: ...
    def by_conversation(self, conversation_id: str) -> dict[str, Any] | None: ...
    def create(self, *, conversation_id: str, brief: str, title: str = "",
               project_id: str | None = None, autonomy: str = "plan",
               budget: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Whitelist {title, autonomy, project_id, archived}; `budget` merges rather than replaces."""
    def set_status(self, id: str, status: str, *, reason: str = "", headline: str | None = None,
                   question: str | None = None, error: str | None = None, plan_id: str | None = None,
                   run_id: str | None = None, event: bool = True) -> dict[str, Any] | None:
        """The ONLY writer of `status`. One transaction writes the column and appends the matching
        desk_events row, so status and timeline can never disagree."""
    def set_headline(self, id: str, headline: str) -> None:
        """Debounced by the caller (see DeskRuntime, §3.5). Never called from a delta."""
    def claim_run(self, id: str, from_statuses: tuple[str, ...]) -> dict[str, Any] | None:
        """UPDATE desks SET status='planning'|'working', run_id=NULL WHERE id=? AND status IN (...)
        — rowcount is the start lock. None means somebody else already started this desk."""
    def charge(self, id: str, cost: float, turn_delta: int = 1) -> dict[str, Any] | None: ...
    def settle(self, id: str, *, partial: str | None, stopped: bool, error: str | None) -> dict[str, Any]: ...
    def live_count(self) -> int: ...
    def recover(self) -> int:
        """Startup: every row whose status is in LIVE becomes 'interrupted' with a needs_you event.
        No desk is auto-resumed at startup unless deskAutoResume is on."""
    def events(self, id: str, limit: int = 200) -> list[dict[str, Any]]: ...
    def event(self, id: str, kind: str, body: str = "", *, needs_you: bool = False,
              run_id: str | None = None, **data: Any) -> dict[str, Any]: ...
    def inbox(self, limit: int = 40) -> list[dict[str, Any]]: ...          # unseen needs_you rows
    def mark_seen(self, event_id: str) -> None: ...
    def outputs(self, desk_id: str) -> list[dict[str, Any]]: ...
    def declare_output(self, desk_id: str, path: str, title: str, summary: str,
                       sha256: str, bytes_: int, run_id: str | None) -> dict[str, Any]:
        """INSERT ... ON CONFLICT(desk_id, path) DO UPDATE: re-writing a file updates one row."""
    def claim_output(self, output_id: str) -> dict[str, Any] | None:
        """UPDATE desk_outputs SET status='accepted' WHERE id=? AND status IN ('proposed','stale')
        — rowcount is the lock, so a double-clicked Accept promotes once."""
    def finish_output(self, output_id: str, *, kind: str, ref: str | None,
                      verified: bool) -> dict[str, Any] | None: ...
    def reject_output(self, output_id: str) -> dict[str, Any] | None: ...
    def delete(self, id: str) -> None: ...
```

### 2.4 `backend/personal_os/workspace.py` — path safety, no SQL

```python
MAX_FILE_CHARS   = 400_000
MAX_FILES        = 500
MAX_TOTAL_BYTES  = 200_000_000
MAX_PREVIEW      = 200_000
BLOCKED_SUFFIXES = (".command", ".app", ".scpt", ".applescript", ".workflow", ".terminal", ".shortcut")

class Workspace:
    def __init__(self, data_dir: Path) -> None: self.root = Path(data_dir) / "cowork"
    def desk_root(self, desk_id: str) -> Path: ...
    def ensure(self, desk_id: str) -> Path: ...                 # mkdirs outputs/, work/, .baseline/, .trash/
    def resolve_in(self, desk_id: str, rel: str) -> Path:
        """Reject an absolute path and any '..' segment, then Path.resolve() BOTH sides and require
        is_relative_to(desk_root). Resolve-then-contain is what defeats a symlink the agent planted
        inside its own workspace; checking the string first and resolving later does not."""
    def read(self, desk_id: str, rel: str, offset: int = 0, length: int = 6000) -> dict[str, Any]: ...
    def write(self, desk_id: str, rel: str, content: str, mode: str = "create") -> dict[str, Any]:
        """mode create|overwrite|append. create refuses to clobber. Enforces MAX_FILE_CHARS,
        BLOCKED_SUFFIXES, MAX_FILES and MAX_TOTAL_BYTES (checked before the write, reported with
        current usage). Snapshots into .baseline/ on first write so diff() has something to show."""
    def trash(self, desk_id: str, rel: str) -> dict[str, Any]:  # move to .trash/ with " 2" suffixing
    def tree(self, desk_id: str, sub: str = "") -> list[dict[str, Any]]:
        """[{path, bytes, modified, is_dir, is_text, state}] capped at MAX_FILES; state is
        new|modified|unchanged against .baseline/."""
    def diff(self, desk_id: str, rel: str) -> dict[str, Any]:   # stdlib difflib.unified_diff
    def sha(self, desk_id: str, rel: str) -> str: ...
    def usage(self, desk_id: str) -> dict[str, int]: ...        # {files, bytes}
    def purge(self, desk_id: str) -> None: ...
```

### 2.5 Edits to existing tables and defaults

| File:line | Change |
|---|---|
| `repos.py:87` `DEFAULT_CONV_SETTINGS` | add `"planMode": None` (None = inherit global) and `"deskId": None`. No migration: `settings` is a JSON blob (`db.py:37`) hydrated at `repos.py:100-103`. |
| `llm.py:35-74` `DEFAULT_SETTINGS` | add `"planMode": "off"`, `"approvalWaitSeconds": 600`, `"parkAfterSeconds": 180`, `"deskMaxTurns": 12`, `"deskMaxCost": 2.0`, `"deskMaxLive": 4`, `"deskNotify": True`. **Mandatory** — `PUT /settings` (`app.py:287-291`) drops any key not present here, silently. |
| `tools.py:32` `DEFAULT_MODE` | add `"plan": "ask"`. |
| `mcp_servers.py:24` `DANGER_LEVELS` | add `"plan"`, with a comment that it is built-in only and an MCP server may never declare it. |

No `db.py` edit at all.

---

## 3. Backend

### 3.1 New modules

| File | Owns |
|---|---|
| `backend/personal_os/runlog.py` | `agent_runs`, `run_events`, `tool_calls`, `approvals`; `args_digest`, `call_key`, `sse`, `RunStore` |
| `backend/personal_os/plans.py` | `action_plans`, `plan_steps`; `normalize_plan`, `parse_plan_edits`, `ActionPlans`, `PLAN_TOOL`, the prompt/parameter constants for `propose_plan`, and `CallPolicy` + `decide_call` (§4.3) |
| `backend/personal_os/cowork.py` | `desks`, `desk_events`, `desk_outputs`; `Desks`, the status constants, `DeskRuntime` (§3.5), and the desk prompt fragments |
| `backend/personal_os/workspace.py` | the filesystem; `Workspace` |

### 3.2 Edits to `runs.py` (verified anchors)

* `Run.__init__` (`runs.py:46-60`) takes `store: RunStore`, `kind: str = "chat"`, `desk_id: str | None = None`,
  `turn: int = 0`; sets `self.status = "running"`.
* `Run.info()` (`runs.py:65-67`) returns `kind`, `desk_id`, `turn`, `status` alongside what it returns today, so
  `GET /runs` (`app.py:864-866`) can tell a desk run from a chat run without a second query.
* `Run.publish` (`runs.py:70-80`) keeps its ring and queue fan-out verbatim and gains one line:
  `self.store.append(self.run_id, self.seq, event, data)`.
* `Run.end(status="done")` (`runs.py:82-88`) flushes the delta buffer and stamps `ended_at`/`status` on the row.
* **New** `Run.watchers` property → `len(self._subs)`. Two lines; used by the viewer-aware park timer (§4.5).
* `Run.set_status(s)` writes `agent_runs.status`.
* `RunBus.__init__(store)`; `RunBus.start(conversation_id, runner, *, kind="chat", desk_id=None, turn=0, input=None)`
  creates the row **before** `asyncio.create_task` (`runs.py:139-144`).
* `RunBus.shutdown` (`runs.py:153-165`) ends each run with status `interrupted`, not `done`, so the next boot can
  tell a kill from a clean finish.
* Keying stays `self._runs[conversation_id] = run` (`runs.py:142`). Unchanged, on purpose.

> A subscriber holds a reference to its `Run` object, not to `RunBus._runs`, so replacing the map entry never
> breaks an attached stream — it only changes what a *new* attach finds. That is what makes chaining (§3.6) safe.

### 3.3 Singletons and wiring in `app.py`

Import line, added to the local-import run at `app.py:27-46` (it is not alphabetised; drop it next to
`.presets` at `app.py:41`):

```python
from .cowork import AUTONOMY, LIVE as DESK_LIVE, NEEDS_YOU, STATUSES as DESK_STATUSES, DeskRuntime, Desks
from .plans import PLAN_TOOL, ActionPlans, CallPolicy, decide_call, normalize_plan, parse_plan_edits
from .runlog import RunStore, args_digest
from .workspace import Workspace
```

Singletons:

```python
# after app.py:56 (`docs = Docs(db)`); conversations/projects are core db.py tables, so the FKs resolve.
run_store = RunStore(db)
aplans    = ActionPlans(db)
desks     = Desks(db)
workspace = Workspace(db.data_dir)
```

`app.py:184` becomes `bus = RunBus(run_store)`. `app.py:241` gains two kwargs:

```python
toolbox = Toolbox(memories, graph, documents, settings, todos=todos, google=google, boards=boards,
                  sandboxes=sandboxes, docs=docs, activity=monitor,
                  desks=desks, plans=aplans, workspace=workspace)
```

Beside `_approvals` (`app.py:188`):

```python
# Fast-wake only. The `approvals` row is the source of truth; a restart loses the Future and the
# waiting run falls back to polling the row, so no card is ever orphaned.
_answers: dict[str, str] = {}
# One supervisor task per desk, so a chained turn is owned by something outside the run it follows.
_desk_tasks: dict[str, asyncio.Task[None]] = {}
```

### 3.4 Prompt fragments and caps (beside `RENDER_HINT`/`TOOLS_HINT`, `app.py:411-424`)

```python
PLAN_MODE_HINT = """## Planning mode
You cannot change anything yet. Only read-only tools are available: everything that writes, runs code,
sends, or touches anything outside this app is withheld until the user approves a plan.
Investigate with the read-only tools if you need to, then call `propose_plan` once with the ordered
steps you intend to take. One step per real action, with the exact tool name and the exact arguments
you will call it with — approving a step approves those arguments and nothing else. If the task needs
no actions at all, just answer; do not propose an empty plan."""

PLAN_BOUND_HINT = ("The plan is approved. Run its steps with exactly the arguments that were approved; "
                   "anything else still asks the user. If reality differs from the plan, say so and "
                   "propose a new one rather than improvising around it.")

PLAN_BLOCKED = ("{name} is not available while planning. Put it in a plan step with these exact "
                "arguments and call propose_plan.")

DESK_HINT = """## This is a cowork desk
You are working on your own, in the background, in a private workspace directory. Scratch work goes
under `work/`; anything the user should keep goes under `outputs/` and is nominated with
`desk_deliver`. If you need a decision only the user can make, call `desk_ask` and end your turn —
do not guess and do not trail off. When the brief is finished, call `desk_done` with a short summary."""

DESK_CONTINUE = ("Continuing this desk. The approved plan below shows what is already done. Pick up at "
                 "the first unfinished step; do not redo completed work. Files you already wrote are "
                 "still in the workspace — read them rather than regenerating them.")
DESK_RESUME   = ("Resuming this desk after an interruption. Check the ledger and the workspace before "
                 "repeating anything: a call marked 'outcome unknown' may or may not have happened.")

GATE_TOOLS = (PLAN_TOOL, "desk_ask")   # exempt from the chat approval timeout; they park instead


def _caps(cfg: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """Downward only: a desk may be stricter than the user's settings, never looser. 0 = unlimited,
    so it loses to any positive limit."""
```

### 3.5 `DeskRuntime` — the rail's live label, without a write per token

`cowork.DeskRuntime(desks, desk_id)` is a small in-memory object held by the desk runner. `observe(event, data)`
returns a dict to publish or `None`.

* `delta` is ignored outright.
* `tool_call` / `tool_result` / `plan` / `desk_status` update an in-memory `headline`
  (`"writing outputs/brief.md"`, `"waiting on your plan"`, `"searching the web"`).
* The headline is flushed to the row at most once a second (`HEADLINE_FLUSH_S = 1.0`) or immediately on a
  status change.
* Every call site wraps it: `except Exception: pass  # noqa: BLE001 - a rail label must never kill a run`.

### 3.6 The run adapters

`_chat_stream`'s signature (`app.py:483`) becomes:

```python
async def _chat_stream(conv_id: str, body: ChatIn, stop: asyncio.Event,
                       steers: list[dict[str, Any]] | None = None, run: Run | None = None
                       ) -> AsyncIterator[tuple[str, Any]]:
```

`_run_chat` (`app.py:814-818`) passes `run=run`. The desk adapter sits beside it:

```python
async def _run_desk(run: Run, desk_id: str, body: ChatIn) -> dict[str, Any]:
    """A desk turn is an ordinary reply with run.desk_id set. Returns the settled desk row; the
    supervisor decides whether to chain. Everything autonomous about it — the loop, the budget, the
    breakers, the approval pause — is _chat_stream's, unchanged."""
    rt = DeskRuntime(desks, desk_id)
    partial = stopped = None
    desks.update_run(desk_id, run.run_id)
    async for event, data in _chat_stream(run.conversation_id, body, run.stop, run.steers, run=run):
        if event == "assistant_message":
            run.message_id = data.get("id")
        if event == "done":
            partial, stopped = data.get("partial"), bool(data.get("stopped"))
        run.publish(event, data)
        try:
            if (upd := rt.observe(event, data)) is not None:
                run.publish("desk_status", upd)
        except Exception:  # noqa: BLE001 - a rail label must never kill a run
            pass
    return desks.settle(desk_id, partial=partial, stopped=bool(stopped), error=None)
```

**The supervisor** owns the chain. It is its own task, tracked in `_desk_tasks`, so a chained turn is never
started from inside the previous run's teardown:

```python
async def _desk_supervisor(desk_id: str, first: ChatIn) -> None:
    body = first
    try:
        while True:
            desk = desks.get(desk_id)
            if not desk:
                return
            run = bus.start(desk["conversation_id"], lambda r: _run_desk(r, desk_id, body),
                            kind="desk", desk_id=desk_id, turn=desk["turn"])
            await (run.task or asyncio.sleep(0))          # _drive already converted errors into `error`
            state = desks.get(desk_id) or {}
            if not _should_chain(state, run):
                return
            desks.charge(desk_id, run.cost, 1)
            body = ChatIn(content=DESK_CONTINUE)
    finally:
        _desk_tasks.pop(desk_id, None)


def _should_chain(desk: dict[str, Any], run: Run) -> bool:
    """Five guards, all of which must hold. `progress` is the one that stops a desk burning twelve
    turns re-reading the same file: a turn that consumed no plan step is not progress."""
    caps = _caps(settings(), desk.get("budget"))
    return (desk["status"] == "working"
            and run.partial == "rounds"               # only a budget-window stop chains
            and not run.stop.is_set()
            and bool(aplans.remaining(desk["plan_id"] or ""))
            and run.steps_consumed > 0
            and desk["turn"] + 1 < int(caps["deskMaxTurns"])
            and desk["cost"] < float(caps["deskMaxCost"]))
```

**The handoff** is the fix for the one bug every judge found: a chained run is a *new* `Run` with a *new*
`run_id` and `seq` restarting at 0, and no client is subscribed to it. So the outgoing run announces its
successor before its stream closes. `_run_desk` publishes, immediately before `done` is published and only when
`_should_chain` has already been computed as true:

```python
run.publish("desk_handoff", {"desk_id": desk_id, "conversation_id": run.conversation_id,
                             "turn": desk["turn"] + 1})
```

The renderer treats `desk_handoff` as a promise: when the current `chatStream` generator ends, it calls
`attachSession(convId)` in a **bounded** retry (6 attempts, 250 ms apart, stop on success). `attachSession`
(`store.ts:704-714`) already reads `GET /runs` and `watchRun` dedupes on `run_id` (`store.ts:420-422`), so a
retry that lands early is a harmless no-op and a retry that lands late finds the run. Bounded, triggered by a
known event: not a poller.

`run.partial`, `run.cost` and `run.steps_consumed` are three plain attributes `_chat_stream` sets on the `Run`
it was handed; they also go onto the `agent_runs` row at `end()`.

### 3.7 The desk state machine

| From | Event | To | Who writes it |
|---|---|---|---|
| — | `POST /cowork/desks` | `draft` | route |
| `draft` | `POST .../start` (claim_run succeeds) | `planning` | `Desks.claim_run` |
| `planning` | model calls `propose_plan`, row opened | `awaiting_plan` | gate in `_chat_stream` |
| `awaiting_plan` | `POST /cowork/plans/{id}` approve/edit | `working` | plan route |
| `awaiting_plan` | `POST /cowork/plans/{id}` reject | `planning` | plan route |
| `awaiting_plan` | nobody attached for `parkAfterSeconds` | `blocked` (`reason="plan"`) | park timer |
| `working` | an `ask` tool card opens | `needs_approval` | approval gate |
| `needs_approval` | `POST /approvals/{call_id}` | `working` | approval route |
| `needs_approval` | nobody attached for `parkAfterSeconds` | `blocked` (`reason="approval"`) | park timer |
| `working` | `desk_ask` | `blocked` (`reason="question"`) | tool handler |
| `working` | `done` with `partial == "rounds"` and `_should_chain` | `working` (turn+1) | supervisor |
| `working` | `done` with `partial == "rounds"` and not `_should_chain` | `review` (`reason="budget"`) | `Desks.settle` |
| `working` | `desk_done` | `review`, or `done` with no outputs | tool handler |
| `working` | unhandled error | `failed` | `Desks.settle` |
| any live | `POST .../pause` | `paused` | route |
| any live | `POST .../stop` | `stopped` | route |
| `blocked\|paused\|interrupted\|review` | `POST .../resume` or `.../message` | `working` (or `planning` with no plan) | route → supervisor |
| `done\|failed\|stopped` | `POST .../message` | `working` (or `planning` with no plan) | route → supervisor |
| any in `LIVE`, or `awaiting_plan` | backend startup | `interrupted` | `Desks.recover` |
| `review` | all outputs decided | `done` | accept/reject route |

`NEEDS_YOU = (awaiting_plan, needs_approval, blocked, interrupted, review)` drives the rail's first section and
the Today card; `LIVE = (planning, working, needs_approval)` drives `live_count()`. `recover()` sweeps
`RECOVER_FROM = (*LIVE, awaiting_plan)`: a desk holding a plan card is not live, but the run holding that
card died with the process, so a restart would strand it with nothing able to wake it.

A finished desk is not a dead one. `MESSAGE_FROM` covers `done`, `failed` and `stopped`, so typing into
the desk's box is the documented way to pick a desk back up — the same box, awake or not.

### 3.8 Endpoints

Appended at EOF (`app.py:2468`) under `# ---------------- cowork: desks, plans and workspaces ----------------`,
with Pydantic `XIn`/`XPatch` immediately above each handler, `wsid()` (`app.py:253-262`) on every writer's
`project_id`, `HTTPException(404, ...)` on a falsy repo return, `{"ok": True}` from deletes.

**Desks**

| Method / path | Request | Response |
|---|---|---|
| `GET /cowork/desks?project_id=all&status=&archived=false` | — | `Desk[]` (each with `live: bool`, `unseen: int`) |
| `POST /cowork/desks` | `{brief, title?, project_id?, autonomy?, budget?, start?}` | `{desk, run_id?, seq?, conversation_id}` — over `deskMaxLive` the desk is `queued` instead: `{queued, position, live, max}` |
| `GET /cowork/desks/{id}` | — | `FullDesk` = desk + `plan` + `outputs` + `events` + `runs` |
| `PATCH /cowork/desks/{id}` | `DeskPatch{title?, autonomy?, project_id?, archived?, budget?, clear_project}` | `Desk` |
| `DELETE /cowork/desks/{id}?purge=false` | — | `{ok: true}` |
| `POST /cowork/desks/{id}/start` | — | `{run_id, seq, conversation_id}` |
| `POST /cowork/desks/{id}/resume` | `{reason?}` | `{run_id, seq}` |
| `POST /cowork/desks/{id}/message` | `{content}` | `{ok, steered: bool, run_id?}` |
| `POST /cowork/desks/{id}/pause` | — | `Desk` |
| `POST /cowork/desks/{id}/stop` | — | `Desk` |
| `GET /cowork/desks/{id}/events?limit=200` | — | `DeskEvent[]` |
| `GET /cowork/desks/{id}/files?path=` | — | `{files: DeskFile[], usage: {files, bytes}}` |
| `GET /cowork/desks/{id}/file?path=&offset=&length=` | — | `{path, text, bytes, truncated, binary?, state}` |
| `GET /cowork/desks/{id}/diff?path=` | — | `{path, diff, added, removed}` |
| `GET /cowork/desks/{id}/outputs` | — | `DeskOutput[]` (sha re-checked, `stale` applied) |
| `POST /cowork/desks/{id}/accept` | `{outputs: [{output_id, destination, title?, doc_id?, project_id?}]}` | `{results: [{output_id, ok, verified, kind, ref, error?}]}` |
| `POST /cowork/desks/{id}/reject` | `{output_ids?: string[], note?}` | `Desk` |
| `GET /cowork/inbox?limit=40` | — | `DeskEvent[]` (unseen, `needs_you`) |
| `POST /cowork/inbox/{event_id}/seen` | — | `{ok: true}` |

`POST .../message` forks on `bus.live(conv_id)`: live → the existing steer path (persist with
`convos.add_message`, `run.publish("user_message", um)`, append to `run.steers` — `app.py:838-853`); not live →
clear `desks.question`, then start the supervisor with that message as the turn's content. The user types the
same way whether the agent is awake or not.

**Plans**

| Method / path | Request | Response |
|---|---|---|
| `GET /cowork/plans/{plan_id}` | — | `ActionPlan` with steps |
| `GET /conversations/{id}/plan` | — | `ActionPlan \| null` (`aplans.latest`), so a reloaded chat still shows its card |
| `POST /cowork/plans/{plan_id}` | `{decision: 'approve'\|'edit'\|'reject', steps?: [{idx, arguments?, drop?}], note?}` | `{ok, plan, resumed: bool}` |

The plan route shape-checks `steps` with `parse_plan_edits`, calls `aplans.decide(...)` **first** (the row is
the truth), then `run_store.decide(call_id, "allow"|"deny", by="user", note=note)`, then resolves
`_approvals[call_id]` if a Future exists, then — if the plan's desk is `blocked` — resumes it. A 404 only when
no plan row exists; a plan whose run has died still decides cleanly.

**Runs and approvals** (edits in place, `app.py:856-888`)

| Method / path | Change |
|---|---|
| `GET /conversations/{id}/stream?since=&run_id=` | live run from the bus as today; otherwise `run_store.tail(run_id or latest, since)`. A `run_id` that does not match the live run is served from the tape, so a stale `since` from a previous turn can never be applied to a new run's seq space. |
| `GET /runs?status=&kind=&conversation_id=&desk_id=&limit=` | table-backed via `run_store.list`, so an `interrupted` run still appears |
| `GET /runs/{run_id}` | row + `executed()` + `approvals()` |
| `GET /approvals?status=pending&desk_id=` | the durable pending list |
| `POST /approvals/{call_id}` | `ApprovalIn` gains `note: str \| None`. Writes the row first (`run_store.decide`, first-decision-wins), stashes `note` in `_answers[call_id]`, resolves the Future if present, resumes a blocked desk, returns `{ok, resumed}`. 404 only when no row exists. |

**Startup / shutdown**

```python
@app.on_event("startup")
async def _cowork_startup() -> None:
    """Recovery must never stop the backend from starting."""
    try:
        run_store.recover()     # runs -> interrupted; tool_calls 'started' -> 'unknown';
                                # salvage transcript() into any empty message row; prune()
        desks.recover()         # LIVE -> interrupted + a needs_you event each
    except Exception:  # noqa: BLE001
        log.warning("cowork recovery failed", exc_info=True)
```

Shutdown needs no new hook: `bus.shutdown()` is already wired at `app.py:1110-1114`, it cancels every run task,
`_chat_stream`'s `CancelledError` path (`app.py:770-775`) persists the partial message, and `_desk_supervisor`'s
`finally` drops its entry.

### 3.9 `Conversations.list` — the blast radius, enumerated

`repos.py:94` gains `include_desks: bool = False` and filters hydrated rows whose `settings.deskId` is truthy.
There are exactly three callers in the backend (`grep -n "convos.list(" backend/personal_os/`):

| Call site | Decision |
|---|---|
| `app.py:370` `GET /conversations` | default (`include_desks=False`), **plus** a query param `include_desks: bool = False` so a desk's conversation is still listable on demand |
| `app.py:1482` dashboard `recent_conversations` | default — a desk belongs on the Cowork rail, not in Recent chats |
| `app.py:1892` recap source | `include_desks=True` — the recap summarises what happened, and a desk's work happened |

`GET /conversations/{id}` is untouched, so the detail pane can always open a desk's transcript by id. Nothing
else in the tree calls `list()`.

### 3.10 Crash and restart semantics, stated exactly

| What | What survives | What is lost |
|---|---|---|
| Desk row, plan, steps, outputs, events | SQLite | — |
| Workspace files | disk | — |
| Transcript | `messages` rows via `convos.finish_message` (`repos.py:157-164`) | — |
| Run tape | `agent_runs` + `run_events` (deltas coalesced, pruned after 14 d once ended) | fidelity past `MAX_RUN_EVENTS`, where deltas stop being taped and `events_truncated=1` |
| Pending approval / plan | the `approvals` + `action_plans` rows | the in-process `Future`; the waiting run is gone |
| In-flight tool call | the `tool_calls` row, flipped `started` → `unknown` | whether it actually happened |
| The asyncio task | nothing | the current round's partial reasoning |

On boot: every active run becomes `interrupted`; an interrupted run with a `message_id` whose `messages.content`
is empty gets `run_store.transcript(run_id)` written into it with `error="Interrupted"`, so the user sees the
partial reply rather than a blank bubble; every desk in `LIVE` becomes `interrupted` with a `needs_you` event.
**No desk is auto-resumed at startup unless `deskAutoResume` is on** — otherwise the user presses Resume. With it
on, a desk that was planning, working or waiting on a live approval is relaunched with DESK_RESUME, except one
with a `started`/`unknown` journal row in any of its runs or a pending card: those stay interrupted with an event
saying why. A desk waiting on its plan is never relaunched. Desks still `queued` from before the restart launch
first, oldest first, as `deskMaxLive` allows.

The `unknown` rule is the only honest answer for an external write. `call_once` commits `started` *before*
awaiting the function, so after a crash the row says the call was attempted and nothing says it finished. On
resume, `run_store.ledger(desk_id)` renders that row as
`! gmail_send — in flight when the app stopped; outcome unknown, verify before repeating`, and the frozen tool
card in the Activity tab says the same in those words. It is never auto-retried: a retry is a new step with a
new `call_key`, and the plan step is already `consumed`, so it hits an approval card. **Nothing on any path
silently repeats an external write, and nothing claims an unknown call succeeded.**

### 3.11 The ledger, and why it is prose

A resumed desk does not get a reconstructed `assistant(tool_calls)` / `tool` message history.
`convos.history()` (`repos.py:174-180`) returns `{role, content}` only and drops every row with empty content,
so rebuilding a legal pair sequence from the tape is fragile and one malformed pair breaks the request on
OpenAI-compatible backends (the same reason `_final_round` still sends schemas with `tool_choice="none"`,
`app.py:570-573`). Instead `context.build_context` gains `ledger: str | None = None`, appended as a `parts`
entry with `used["ledger"] = n`, rendering the last 120 `tool_calls` rows as
`idx · tool · one-line args · ok|err|unknown · age`, under *"Work already completed on this desk. Do not repeat
it."* The agent's real memory of its own work is the workspace, which it can re-read.

`POST /context/preview` (`app.py:928-938`) hardcodes its flag set and must gain the new parameter as `None`.

---

## 4. Planning mode

### 4.1 Where the flag comes from

```python
desk_id    = conv["settings"].get("deskId")
desk       = desks.get(desk_id) if desk_id else None
plan       = aplans.active(conv_id)                         # the approved, unconsumed plan, or None
mode_pref  = conv["settings"].get("planMode") or cfg["planMode"]   # 'off' | 'auto' | 'always'
planning   = plan is None and (bool(desk) or mode_pref != "off")
```

A desk always plans its first run: `POST /cowork/desks` writes `planMode: "always"` into the new conversation's
settings. `'auto'` lets a reply act until its first mutating call, then switches it to planning — see §4.7.

### 4.2 Guard 1 — the model is never offered the tool

A single closure replaces **both** `toolbox.schemas(modes)` call sites, `app.py:546` and `app.py:730`:

```python
def _schemas() -> list[dict[str, Any]]:
    """One function, because the always_chat/always_global grant path recomputes tool_schemas at
    app.py:729-730; a plan-mode filter applied at only one of the two sites lets a granted write tool
    reappear mid-plan. `modes` is mutated in place by that grant path, so this filters a copy and
    writes it back rather than rebuilding from a stale snapshot — otherwise the user's 'Always' click
    is silently discarded on the next recompute."""
    m = dict(modes)
    if not desk_id:
        m = {n: v for n, v in m.items() if toolbox.specs[n].group != "desk"}
    if tool_ctx["plan_phase"]:
        m = {n: v for n, v in m.items()
             if n == PLAN_TOOL or toolbox.specs[n].danger in PLAN_SAFE_DANGER}
        m[PLAN_TOOL] = "ask"
    return toolbox.schemas(m)
```

`PLAN_SAFE_DANGER = ("safe", "network", "plan")`.

> **Why `network` is allowed while planning.** A plan whose arguments were invented without looking at anything
> is a plan whose exact-argument binding is worth very little — the user approves guessed file paths and guessed
> queries. The usual objection is that one `web_search` taints the run (`tools.py:377-378`) and then every
> `external` tool is forced to `ask` (`tools.py:352-357`), which would void every later claim. §4.6 removes that
> objection by distinguishing taint the plan predicted from taint it did not, so research during planning is
> safe to allow and we allow it.

### 4.3 Guard 2 — one decision function at the call site

`app.py:680-682` is today exactly:

```python
raw_mode = modes.get(c["name"], "off")
mode = toolbox.gate(c["name"], raw_mode, tool_ctx)
forced = mode != raw_mode
```

It becomes a single call into `plans.decide_call`, a pure function so it can be unit-tested and so the one
contested region of `_chat_stream` has a testable centre:

```python
@dataclass(frozen=True)
class CallPolicy:
    mode: str                 # on | ask | off
    forced: bool              # taint upgraded on -> ask, or blocked while planning
    claimed_step: str | None  # the plan_steps.step_id this call consumed
    off_plan: bool            # a mutating call the approved plan did not contain
    is_plan: bool
    deny: str | None          # a reason, when the call must not run at all


def decide_call(name, args, *, raw_mode, specs, ctx, planning, plan, autonomy, gate, plans
                ) -> CallPolicy:
    """Effective policy for one call, in priority order. Every rule that can stop a call lives here.
    Note the ordering: the taint verdict is computed BEFORE claim() is attempted, because claim()
    consumes the step and a step burnt on a call the user then denies can never be reclaimed."""
```

Resolution order, top to bottom:

1. `name == PLAN_TOOL` → `("ask", forced=False, is_plan=True)`. A plan is always a card; it can never resolve to
   `on` through any settings layer, and it can never buy a standing grant.
2. `name == "desk_ask"` → `("ask", forced=False)` — handled as a question card (§5 tools).
3. `planning and specs[name].danger not in PLAN_SAFE_DANGER` →
   `deny=PLAN_BLOCKED.format(name=name)`, `forced=True`. `forced` rides out on the `tool_call` event
   (`app.py:688-689`) and into the tool event record (`app.py:753-755`), so the UI can say *"blocked while
   planning"* rather than *"turned off"*.
4. `autonomy == "propose" and danger == "external"` → `deny="this desk may only propose external actions"`.
5. Compute `would_force = gate(name, raw_mode, ctx) != raw_mode` **and** the expected-taint verdict of §4.6.
   If the call is `external`, the run is tainted, and the taint is *not* expected by this plan → `("ask",
   forced=True)` and **no claim is attempted**.
6. Otherwise, if `plan` is approved: `step = plans.claim(plan_id, name, args, call_id)`.
   `step` → `("on", forced=False, claimed_step=step_id)`.
7. `plan` approved but no claim, and `danger != "safe"` → `("ask", off_plan=True)`.
8. `plans.rejected(conv_id, name, args)` → `deny="the user rejected this exact step"`.
9. Otherwise `gate(name, raw_mode, ctx)`, i.e. today's behaviour unchanged.

Rule 7 is a product decision: an **off-plan mutating call asks, it is not hard-denied**. A plan is a strong
default, not a cage — hard-denying makes the agent brittle the moment reality differs from the plan, and the
card is the honest signal. The two exceptions are rule 4 (`propose` autonomy) and rule 8 (an explicitly rejected
step).

The `tool_call` event gains `off_plan`, `plan_step` and `blocked_by: 'plan_mode' | null`.

### 4.4 Guard 3 — the second gate, in the module that owns the functions

`Toolbox.gate` (`tools.py:352-357`) gains a clause **above** the taint clause, and `Toolbox.call`
(`tools.py:359`) gains a hard refusal at the very top, before `spec.fn` is awaited:

```python
# tools.py, Toolbox.call — this is the second gate, in the module that owns the tool functions, so a
# new call site (the desk runner, a canvas widget, a future background job) cannot execute a write by
# forgetting the first one. Toolbox.call does not invoke gate(), so this clause must live here too.
if ctx.get("plan_phase") and spec.danger not in PLAN_SAFE_DANGER and name != PLAN_TOOL:
    return denied(name, "planning mode is on and no plan has been approved")
if ctx.get("proposal_only") and spec.danger == "external":
    return denied(name, "this desk may only propose external actions")
```

`tool_ctx` (`app.py:538-544`) gains `plan_phase`, `proposal_only`, `desk_id`, `workspace`, `plan_id`, `run_id`,
`message_id`.

### 4.5 The pause: a row, a Future, and a viewer-aware park

The approval block (`app.py:694-730`) keeps its 2 s `asyncio.wait_for(asyncio.shield(fut), timeout=2)` shield
loop, its `budget.paused` accounting (`app.py:715`) and its `t0` reset verbatim. Three changes:

```python
row = run_store.open_approval(call_id=uid, run_id=run.run_id, conversation_id=conv_id, desk_id=desk_id,
                              message_id=am["id"], tool=c["name"], args=args, danger=spec.danger,
                              forced=forced, plan_id=plan_row["plan_id"] if pol.is_plan else None)
if desk_id:
    desks.set_status(desk_id, "awaiting_plan" if pol.is_plan else "needs_approval", run_id=run.run_id)
fut = asyncio.get_event_loop().create_future(); _approvals[uid] = fut
waited = 0.0
while not fut.done():
    if stop.is_set():
        run_store.decide(uid, "deny", by="stop"); fut.set_result("deny"); break
    cur = run_store.approval(uid)                  # decided in another window, or before a restart
    if cur and cur["status"] != "pending":
        fut.set_result(cur["decision"] or "deny"); break
    # Park, do not auto-deny, and never park in front of somebody who is looking at the card.
    if run.watchers == 0 and waited >= park_after and (desk_id or c["name"] in GATE_TOOLS):
        run_store.park(uid); parked = uid; break
    if not desk_id and c["name"] not in GATE_TOOLS and waited >= approval_wait:
        run_store.decide(uid, "deny", by="timeout"); fut.set_result("deny"); break
    try:
        await asyncio.wait_for(asyncio.shield(fut), timeout=2)
    except asyncio.TimeoutError:
        waited += 2
```

* **Parking is not an exception.** When `parked` is set, the loop synthesizes a tool message for this call and
  for every remaining pending call, sets `partial = "blocked"`, and breaks out through the existing
  `_final_round()` path (`app.py:562-584`). That keeps the message list legal — the same reason the budget path
  at `app.py:650-659` answers every pending call — and it means the park never crosses `_chat_stream`'s
  `except Exception` at `app.py:776`. The desk settles to `blocked` with a `needs_you` event. The approval row
  stays `pending`, so the card is still decidable tomorrow, from any window, after a restart.
* **The chat timeout is a setting, not a constant.** `approvalWaitSeconds` defaults to 600, matching today's
  hardcoded `if waited >= 600` (`app.py:710`), and the row records `decided_by='timeout'` so the UI can say the
  card expired rather than pretending the user declined.
* **`run.watchers`** (`len(run._subs)`) is what stops a park from firing in front of a user who is reading a
  twelve-step plan. With a viewer attached, the run waits indefinitely, stop-aware, polling the row.

### 4.6 Expected taint — why "research X and email me" works

Taint is sticky for the conversation (`app.py:542`, `app.py:785-788`), and `Toolbox.gate` forces every
`external` tool to `ask` once set. Naively, a desk that web-searches can never again execute a pre-approved
send, and the single most obvious cowork task ends blocked on its last step.

The fix is to distinguish taint the user already signed off from taint they did not:

* `normalize_plan` records `expected_taint = [step.tool for step in steps if toolbox.taints(step.tool)]` on the
  plan row, and the plan card renders a banner: *"Step 2 reads the web. Approving step 4 means it sends mail
  after reading untrusted content."*
* Approval of that plan therefore **is** approval of that taint.
* At call time, rule 5 of `decide_call` asks: is every entry in `ctx["taint_sources"]` either (a) in
  `plan.expected_taint` or (b) a tool whose call `claimed` a step of this plan? If yes, the taint was predicted
  and the claim stands. If no — the agent fetched something off-plan — the call is forced to `ask` and no step
  is consumed.
* A plan proposed while the run was *already* tainted records `tainted=1`, and its approval degrades to one-shot
  exactly as `app.py:718-719` already does for forced cards.

This is checkable, it is visible on the card, and it is the only place in the design where a pre-approval can
survive a taint — narrowly, provably, and with the user told in advance.

### 4.7 Plan mode in a normal chat

Identical code path with `desk_id=None`. The composer gets a toggle (⌘⇧P) writing
`conv.settings.planMode` ∈ `off | auto | always` — spreading the existing settings object, because the merge at
`repos.py:132-135` is shallow and `tools` is the standing example of what happens if you forget.

* `always` — every turn plans before acting.
* `auto` — the first time `decide_call` sees a mutating call in a reply, it flips `planning` on, denies that one
  call with `PLAN_BLOCKED`, and recomputes `tool_schemas` via `_schemas()`. This works because `_chat_stream`
  reads `conv` once at `app.py:485` and keeps using the in-memory `conv["settings"]` for the whole reply; the
  flip mutates that in-memory copy only. This is the same documented in-memory mutation the approval path
  already performs at `app.py:722`, and it must carry the same kind of comment.
* The card renders inline in the assistant bubble, from the `plan` SSE event, in the reply's own flow where
  the plan was proposed.

### 4.8 Editing a plan

`POST /cowork/plans/{plan_id}` with `{decision: 'edit', steps: [{idx: 2, arguments: {...}}, {idx: 4, drop: true}], note}`:

* Each edited step's `args_digest` is **recomputed** and `edited=1` set, so the agent is bound to the user's
  arguments, not its own.
* A dropped step becomes `dropped` and is never claimable.
* `lib/planDigest.ts` `canon()` mirrors `runlog.args_digest`'s input string byte for byte, so the card can tell a
  real edit from a reformat. `planDigest.test.ts` and `test_plan_mode.py` assert the *same fixture set* produces
  the *same canonical string*. If those two drift, every approved step silently re-prompts — this is the single
  most dangerous failure mode in the design and the reason both tests exist.
* Rejecting leaves `planning` on and returns `{"status": "rejected", "note": ...}` to the model with an explicit
  instruction to stop and re-plan; it cannot fall through to doing the work anyway.

---

## 5. Tools

One new danger level and two new groups. All registration follows the house trampoline pattern: a module-level
`def _register_X(self: Toolbox) -> None:` after the last registrar (`tools.py:1091`), first line
`R = self.specs.__setitem__`, and `Toolbox._register_X = _register_X  # type: ignore[attr-defined]` beside
`tools.py:1093-1094`.

**New danger level `plan`** → `DEFAULT_MODE["plan"] = "ask"` (`tools.py:32`). It means *"this tool is the
approval card"*: always `ask`, allowed while planning, never grantable.

**Group `plan`** (registered when `plans is not None`):

| Tool | Danger | Notes |
|---|---|---|
| `propose_plan` | `plan` | `_obj({title, intent, steps}, ["title","steps"])`. `steps` is the one array: `{title, tool?, arguments?, why}`, max 12. `fn` is an unreachable stub returning `tool_error("propose_plan is answered by the approval gate")` — the gate answers it, the stub exists so `Toolbox.call` never reaches a missing handler. Description states the contract: *"The arguments you write here are the arguments you will be held to."* Three escalating examples. |

**Group `desk`** (registered when `desks is not None and workspace is not None`). Stripped from `modes` outside
a desk conversation by `_schemas()`, and every handler additionally returns
`tool_error("This tool only works inside a cowork desk.")` when `ctx.get("desk_id")` is missing — belt and
braces, because `Toolbox.available()` (`tools.py:312-319`) cannot see `ctx`.

| Tool | Danger | What it does |
|---|---|---|
| `desk_list_files(prefix="", offset=0, limit=50)` | `safe` | `page(entries, key="files")` |
| `desk_read_file(path, offset=0, length=6000)` | `safe` | Line-windowed, returns `next_offset`. **Not** `taints=True`: the workspace holds what this agent wrote. Paging is also the mitigation for the flat 24 000-char cap at `app.py:761`. |
| `desk_write_file(path, content, mode="create")` | `writes` | `create` refuses to clobber; quotas and blocked suffixes enforced in `Workspace.write`, surfaced as a `tool_error` with current usage |
| `desk_trash_file(path)` | `writes` | moves to `.trash/`; nothing in a workspace is ever unlinked |
| `desk_deliver(path, title, summary="")` | `writes` | Nominates a file under `outputs/` as a deliverable: upserts a `desk_outputs` row at `proposed` with its sha. Returns `{"status": "awaiting_review", ...}`. The `docs.propose` (`docs.py:266-279`) propose-not-apply shape — the agent never promotes anything itself. |
| `desk_ask(question, context="")` | `plan` | Writes `desks.question`, sets `blocked`, returns `{"status": "waiting_for_user", "note": "Stop here and end your turn."}`. **No second blocking primitive**: the model ends its turn, the desk sits in Needs you, the user's answer through the steer box starts the next turn — which survives a restart for free. |
| `desk_done(summary, next_steps="")` | `safe` | `review` if there are proposed outputs, else `done`. A desk's terminal state is a decision, not an inference. |
| `desk_import_sandbox(sandbox_path, path)` | `writes` | Copies a file out of the desk's own container (`sandboxes.read_file(ctx["conversation_id"], ...)`, `microvm.py:216`) into the workspace. Taints *conditionally* via a local `_mark()` closure reading `sandboxes.networked(conv_id)` (`microvm.py:180`), the `tools.py:904-911` pattern, and appends to `ctx["taint_sources"]` itself because `ToolSpec.taints` is static. |

`Toolbox.__init__` (`tools.py:290-293`) gains `desks=None, plans=None, workspace=None`, stores them, and adds
the two guarded `self._register_plan()` / `self._register_cowork()` calls to the chain at `tools.py:296-307`.

**Mandatory registrations.** All nine names (`propose_plan`, `desk_list_files`, `desk_read_file`,
`desk_write_file`, `desk_trash_file`, `desk_deliver`, `desk_ask`, `desk_done`, `desk_import_sandbox`) go into
`RESERVED_TOOL_NAMES` (`mcp_servers.py:40-58`) or `backend/personal_os/tests/test_mcp_servers.py:82-88` fails.
That test regexes `ToolSpec\(\s*"([A-Za-z0-9_]+)"`, so every `ToolSpec` first argument must be a string literal.
`ALTERNATIVE` (`tools.py:61-92`) gains an entry for each, including the plan-mode fallbacks
(`"propose_plan": "describe the steps in prose and ask the user how to proceed"`) — without them a call denied
while planning falls back to the generic *"continue without it"* (`tools.py:111-112`) and the model loops
straight into `REPEAT_LIMIT = 5` (`app.py:433`).

**No `cowork_spawn`.** A desk is created by the user.

---

## 6. Workspaces

### 6.1 Layout

```
<data_dir>/cowork/<desk_id>/
  outputs/     the only directory desk_deliver accepts a path from; what the user reviews
  work/        scratch: notes, intermediate data, fetched material
  .baseline/   a copy of each file as it was on first write, so Files shows a real diff
  .trash/      desk_trash_file moves here; nothing is ever unlinked
```

`data_dir` is `db.data_dir` (`db.py:146`), beside the existing `uploads/`, so the workspace rides the
`PERSONAL_OS_DATA_DIR` contract and tests get a tempdir for free. `desks.workspace` stores the **relative**
path `cowork/<id>`, never an absolute one, so moving the data directory does not strand every desk.

### 6.2 Isolation — three layers

1. **Path containment.** `Workspace.resolve_in` is the one chokepoint every `desk_*` tool goes through: reject
   absolute, reject any `..` segment, `Path.resolve()` both sides, require `is_relative_to(desk_root)`. Plus
   `BLOCKED_SUFFIXES` and `MAX_FILE_CHARS`. This is `mac.allowed_path`'s discipline applied to an app-owned
   directory, and it is the only thing standing between `desk_write_file` and the rest of the disk — so its
   traversal test is the **first** thing written in that package, not the last.
2. **Per-desk root.** The root is derived from `ctx["desk_id"]` inside the handler; a tool never names a root,
   only a relative path, so desk A cannot address desk B's directory.
3. **Execution.** Unchanged and already right. `run_python` (`sandbox.py`) gets a throwaway `mkdtemp` under
   `sandbox-exec` with the data dir explicitly denied — it therefore *cannot see the workspace*, by design.
   Code that must touch workspace files uses `microvm.Sandboxes`, which already keys one persistent container
   per `conversation_id` (`microvm.py:107`) — and a desk is one conversation, so a desk gets a private
   `/workspace`, network off by default, with **no change to microvm.py**. Moving a file across is explicit in
   both directions (`sandbox_write_file` / `desk_import_sandbox`); there is deliberately no bind mount, because
   a bind mount would let container code bypass layer 1.

Writing inside the workspace is `danger="writes"`, not `external`, so it needs no card: the boundary of free
autonomy is exactly the boundary of the workspace directory, enforced by the danger level rather than by a
prompt.

### 6.3 Quotas

`MAX_FILE_CHARS = 400_000` per write, `MAX_FILES = 500` and `MAX_TOTAL_BYTES = 200_000_000` per desk, checked
*before* the write and reported as a `tool_error` carrying the current usage — so a looping agent fills a quota,
not the disk.

### 6.4 Outputs and promotion

`GET /cowork/desks/{id}/outputs` recomputes each file's sha at read time and flips a row to `stale` when it no
longer matches, so the review UI can never preview bytes the agent has since rewritten.

`POST /cowork/desks/{id}/accept` is the only promotion path, and per output it:

1. **Claims** — `UPDATE desk_outputs SET status='accepted' WHERE id=? AND status IN ('proposed','stale')`.
   The rowcount is the lock, so a double-clicked Accept promotes once.
2. **Books** the work through `run_store.call_once(run_id, PROMOTE_STEP, "promote", ...)`, so a crash between
   the claim and the read-back leaves an auditable `unknown` row rather than a silent half-promotion.
3. **Promotes** by `destination`:
   * `doc` → `docs.create(title, content, project_id)` (`docs.py:175`) — the doc is searchable immediately via
     `docs_fts`.
   * `doc_append` with `doc_id` → `docs.propose(doc_id, after, summary, tool="cowork")` (`docs.py:266-279`),
     which writes a **pending** `doc_revisions` row and leaves the doc untouched until the user accepts it in
     the existing Docs review UI. An agent never overwrites a document the user wrote.
   * `document` → copy into `<data_dir>/uploads/` and run the existing ingest/chunk path.
   * `download` → `GET /cowork/desks/{id}/download?path=<rel>`, a `FileResponse` over the workspace file;
     nothing enters the app. The read-back is sha256 against the bytes `desk_deliver` declared.
4. **Verifies by reading back.** For `doc` and `doc_append`, re-fetch and compare sha + length against the
   output row. For `document`, compare against **the stored upload file**, not the chunk text — the chunks are a
   derived, normalised representation and comparing to them would report a false failure on every upload. For `download` nothing enters the app, so the
   read-back is the workspace file itself, hashed against the declared sha. A mismatch sets `promote_failed`, clears the
   claim so a retry is possible, and the response carries `{ok: false, verified: false, error}` — the UI shows a
   red row, never a tick.

"Verify the write" is a standing project rule; this is it applied to the one place a desk's work crosses into
the app.

### 6.5 Cleanup

`DELETE /cowork/desks/{id}?purge=false` is the default and **keeps the workspace on disk** — a deleted desk's
files are the one thing the user cannot regenerate. `purge=true` rmtrees it after a `confirm()` in the UI that
names what is lost. The desk's conversation cascades with the desk row; `agent_runs` cascade with the
conversation; `run_events` and `tool_calls` cascade with the runs. `run_store.prune()` drops `run_events` for
runs ended more than 14 days ago, once, at startup.

---

## 7. Frontend

### 7.1 Shared types (`src/shared/types.ts`)

Named `ActionPlan*` and `Desk*` throughout — never bare `Plan` / `PlanStep`, which two unmerged branches each
define differently; and CSS classes are `.aplan-*` / `.cowork-*` / `.desk-*`, never `.plan-*`. This costs
nothing now and saves a rename later.

Added after `Recap` (`types.ts:499`), before the Canvas banner:

```ts
export type DeskStatus = 'draft'|'planning'|'awaiting_plan'|'working'|'needs_approval'|'blocked'
                       | 'paused'|'interrupted'|'review'|'done'|'failed'|'stopped'
export type DeskAutonomy = 'plan' | 'ask' | 'propose'
export type PlanDecision = 'approve' | 'edit' | 'reject'
export interface PlanEdit { idx: number; arguments?: Record<string, unknown>; drop?: boolean }
export interface ActionPlanStep { step_id: string; idx: number; title: string; tool: string
  arguments: Record<string, unknown>; args_digest: string; why: string
  danger: ToolDanger; status: 'proposed'|'approved'|'consumed'|'done'|'failed'|'dropped'|'rejected'
  edited: boolean; result_error: string | null }
export interface ActionPlan { plan_id: string; call_id: string | null; conversation_id: string
  desk_id: string | null; message_id: string | null; title: string; intent: string
  status: 'pending'|'approved'|'rejected'|'superseded'; tainted: boolean
  expected_taint: string[]; note: string; decided_by: string | null
  created_at: number; decided_at: number | null; steps: ActionPlanStep[] }
export interface DeskBudget { maxTurns?: number }
export interface Desk { id: string; conversation_id: string; project_id: string | null; title: string
  brief: string; status: DeskStatus; status_reason: string; headline: string; question: string
  autonomy: DeskAutonomy; plan_id: string | null; run_id: string | null; turn: number; cost: number
  budget: DeskBudget; archived: boolean; live: boolean; unseen: number
  created_at: number; updated_at: number; ended_at: number | null }
export interface FullDesk extends Desk { plan: ActionPlan | null; outputs: DeskOutput[]
  events: DeskEvent[]; runs: RunInfo[] }
export interface DeskOutput { id: string; desk_id: string; path: string; title: string; summary: string
  sha256: string; bytes: number
  status: 'proposed'|'stale'|'accepted'|'promoted'|'promote_failed'|'rejected'
  promoted_kind: string | null; promoted_id: string | null; verified: boolean; created_at: number }
export interface DeskFile { path: string; bytes: number; modified: number; is_dir: boolean
  is_text: boolean; state: 'new'|'modified'|'unchanged' }
export interface DeskEvent { id: string; desk_id: string; run_id: string | null; kind: string
  body: string; data: Record<string, unknown>; needs_you: boolean; seen: boolean; created_at: number }
```

Also: `ToolDanger` (the union at `types.ts:33`) gains `'plan'`; `ToolEvent` gains
`off_plan?: boolean`, `plan_step?: string | null`, `blocked_by?: 'plan_mode' | null`; `Message` gains
`plan?: ActionPlan | null` (non-persisted, beside `partial?`); `SessionStatus` (`types.ts:634`) gains
`'awaiting-plan'`; `RunInfo` (`types.ts:643`) gains `kind`, `desk_id`, `turn`, `status`;
`ConversationSettings` gains `planMode: 'off'|'auto'|'always'|null` and `deskId: string | null`;
`Settings` gains `planMode`, `approvalWaitSeconds`, `parkAfterSeconds`, `deskMaxTurns`, `deskMaxCost`,
`deskMaxLive`, `deskNotify` (and `store.ts:504`'s initial-state literal gains them, or the first-run modal
blanks them).

Four new `ChatEvent` members (`types.ts:436-449`):

```ts
| { event: 'plan'; data: { message_id: string; call_id: string; plan: ActionPlan } }
| { event: 'plan_decision'; data: { message_id: string; plan_id: string; call_id: string
      decision: PlanDecision; by: string; note?: string } }
| { event: 'desk_status'; data: Desk }
| { event: 'desk_handoff'; data: { desk_id: string; conversation_id: string; turn: number } }
```

Each is the four-file edit by convention: backend yield, this union, `backend/tests/test_runs.py:30-31`
`CHAT_EVENTS`, and both renderer consumers below.

### 7.2 API (`src/renderer/src/lib/api.ts`)

* `approve(callId, decision, note?)` — widened (`api.ts:71`).
* `chatStream(convId, since = 0, signal?, runId?)` (`api.ts:291`) becomes **reconnecting**: it appends
  `&run_id=`, reads the `id:` line into a `lastSeq`, and on a non-aborted read error retries up to 8 times with
  exponential backoff capped at 5 s, resuming from `lastSeq`. This is what makes "leave and come back tomorrow"
  and a laptop sleep work.
* A new `cowork: { desks: {...}, plans: {...}, inbox: {...} }` group after the `activity:` block (`api.ts:231`),
  one method per endpoint in §3.8, using the module-level `json()` (`api.ts:56`) and `scope()` (`api.ts:59`).
* `conversations.plan(id)`.
* Type imports added to the block at `api.ts:1-7`.

### 7.3 `lib/planDigest.ts` (+ `.test.ts`)

Pure: `canon(args)` (sorted keys, compact separators — the exact mirror of `runlog.args_digest`'s input string),
`edited(step, draft)`, `editPayload(steps, drafts, dropped)`, `argRows(args)`, `invalid(raw)`. No store, no
fetch. Its test pins `canon()` against the same fixture set `test_plan_mode.py` asserts.

### 7.4 Store slice (`src/renderer/src/store.ts`)

`View` (`store.ts:19`) gains `'cowork'`; `ClassicView` derives, and `wireMenu`'s generic
`action.startsWith('view:')` (`store.ts:373`) routes it with no edit.

State, after the activity block (~`store.ts:124`): `desks: Desk[]`, `activeDeskId: string | null`,
`activeDesk: FullDesk | null`, `deskFiles: DeskFile[]`, `deskPreview: { path: string; text: string } | null`,
`deskInbox: DeskEvent[]`, `deskBusy: boolean`. Defaults after `store.ts:549`.

Actions (signatures ~`store.ts:196`, bodies after `purgeActivity` ~`store.ts:1046`, under a
`// ---- cowork desks ----` banner): `refreshDesks`, `refreshDeskInbox`, `openDesk(id)`, `createDesk(p)`,
`startDesk`, `resumeDesk`, `pauseDesk`, `stopDesk`, `messageDesk(id, text)`, `patchDesk`, `deleteDesk`,
`loadDeskFiles(id, path?)`, `previewDeskFile(id, path)`, `acceptOutputs(id, sel)`, `rejectOutputs(id, ids, note)`,
`decidePlan(planId, decision, edits?, note?)`, `setPlanMode(convId, mode)`, `markDeskEventSeen(id)`.

`openDesk(id)` does exactly three things: `api.cowork.desks.get(id)`; `retainSession(conversation_id)`
(`store.ts:1181`) in the caller's effect pair, because the 12-session LRU (`MAX_SESSIONS = 12`, `store.ts:50`)
evicts by `touchedAt` and a desk pane is not `focusedConversationId` — the exact bug
`canvas/widgets/chat.tsx:139-140` guards; and `attachSession(conversation_id)` when the desk is live.

`setView` (`store.ts:607-610`) gains `if (view === 'cowork') { void get().refreshDesks() }`. `init()` kicks
`refreshDeskInbox()` **once** so the sidebar badge and the Today card are live before the view is ever opened.
There is no timer anywhere.

`applyEvent` (`store.ts:302-349`) gains:

```ts
case 'plan':          return mapMsg(ev.data.message_id, (m) => ({ ...m, plan: ev.data.plan }))
case 'plan_decision': return mapMsg(ev.data.message_id, (m) => (m.plan && m.plan.plan_id === ev.data.plan_id
                        ? { ...m, plan: { ...m.plan, status: ev.data.decision === 'reject' ? 'rejected' : 'approved' } } : m))
case 'desk_status':
case 'desk_handoff':  return s          // desk-level; handled in watchRun's side-effect switch
```

`countApprovals` (`store.ts:56`) folds in `m.plan?.status === 'pending'` — a plan gates the run the same way an
approval does, and without this the amber ring never clears.

`watchRun`'s side-effect switch (`store.ts:436-453`) gains:

```ts
case 'desk_status':
  set((s) => ({ desks: s.desks.map((d) => (d.id === ev.data.id ? ev.data : d)),
                activeDesk: s.activeDesk?.id === ev.data.id ? { ...s.activeDesk, ...ev.data } : s.activeDesk }))
  if (ev.data.status === 'review') void get().loadDeskFiles(ev.data.id)
  if (NEEDS_YOU.includes(ev.data.status)) void get().refreshDeskInbox()
  break
case 'desk_handoff': handoff = ev.data; break      // local to this watchRun call
```

and, in the generator's `finally`, when `handoff` is set: a bounded re-attach —
`for (let i = 0; i < 6 && !get().sessions[convId]?.streaming; i++) { await sleep(250); await get().attachSession(convId) }`.
`attachSession` is `share`d and `watchRun` dedupes on `run_id`, so every extra attempt is a no-op.

### 7.5 `sessionStatus.ts`

`reduceStatus` gains explicit cases **before** the `default`, whose `idle → working` would resurrect a finished
run from a late event — exactly the hazard the `span` / `learned` / `learn_error` cases at
`sessionStatus.ts:28-32` already guard:

```ts
case 'plan':          return 'awaiting-plan'
case 'plan_decision': return ev.data.decision === 'reject' ? prev : 'working'
case 'desk_status':
case 'desk_handoff':  return prev
```

`SessionStatus` gains `'awaiting-plan'`; `settleApprovals` is unchanged.

### 7.6 Components

| File | What |
|---|---|
| `components/CoworkView.tsx` | The view, `DocsView.tsx`-shaped. `<main className="page cowork-page">` → `<header className="page-header drag">` with the `!sidebarOpen` un-collapse button (`DocsView.tsx:146`), `<h2><Users size={16}/> Cowork</h2>`, `.no-drag .header-right` with `<ScopeSelect>` and **New desk**. Body `.cowork-body` is `grid-template-columns: 300px minmax(0,1fr)`. Holds the `DeskRail` and `NewDeskCard` sub-components, as `DocsView` holds `DocList`. |
| `components/DeskRail.tsx` | Four fixed sections that **are** the triage model: **Needs you** (`NEEDS_YOU`), **Working**, **Review**, **Done**. Each row: a status ring, the title, the live `headline`, elapsed time, `<ChatPulse>` when live, and badges for pending approvals and unseen events. |
| `components/DeskDetail.tsx` | Title (inline-editable), status pill, a `turn n/12 · $0.42 · 6m` meter, lifecycle buttons, and the four-tab strip. Owns the `retainSession` + `attachSession` effect pair. |
| `components/DeskPlan.tsx` | The Plan tab: `<ActionPlanCard>` while pending; once approved, the same steps as a live checklist with `[x] / [>] / [ ]` marks, tool chips tinted by danger, and per-step failure reasons. **Amend plan** posts a steer asking for a re-plan. |
| `components/DeskFiles.tsx` | Workspace tree (outputs/ first, work/ collapsed) with new/modified badges, a preview pane (markdown rendered, images inline, binary = name and size), and a **Diff** toggle rendering the backend's unified diff with `lib/diff.ts`. |
| `components/DeskReview.tsx` | The Output tab and the reason the feature exists. One card per output: title, path, size, preview excerpt, checkbox, and a `.seg` destination of **Doc / Append to doc / Document / Download**. Footer: **Accept selected**, **Send back** (prompts for a note, posts it as a message and resumes — review is a loop, not a binary), **Reject**. After accept, a verified tick or a red *"could not verify the write"* row, read from the response, never assumed. |
| `components/ActionPlanCard.tsx` | One component, two mount points (inline in chat via `ToolEvents`, and the Plan tab). Header, intent, a **computed side-effect strip** from each step's danger (*"sends 1 email · writes 2 files · reads the web"*), a taint banner when `expected_taint` is non-empty, then numbered step rows with a drop checkbox, tool chip, `why`, and an **Edit** disclosure turning the arguments into a validated JSON textarea. Footer: **Approve & run** (⌘⇧A), **Approve with changes**, **Reject** (⌘⇧D) + a note. Also exports `AskUserCard` for `desk_ask`. Takes actions via single selectors only (`useStore((s) => s.decidePlan)`) and keeps all edit state local — `Message.tsx:10-11` documents that any broader subscription in that subtree re-renders every message per streamed token. |
| `components/PlanModeToggle.tsx` | `ListChecks` icon, `aria-pressed`, ⌘⇧P, cycling `off → auto → always`, spreading the old settings object. |
| `components/AgentInbox.tsx` | The Today card. It lists `deskInbox` rows under "Needs you" beside approvals and proposals; a desk event whose run has a pending approval is dropped so it is not shown twice. |
| `styles/cowork.css` | `.cowork-body` (`min-height: 0; overflow: hidden`, so the `.app` grid row stays definite — `styles.css:120-126`), `.cowork-side`, `.desk-row`/`.active`, `.desk-ring-*`, `.desk-tabs`, `.desk-file-*`, `.diff-add`/`.diff-del`, `.desk-output-*`. `var(--token)` colours only; every class feature-prefixed, because `styles.css` is one flat namespace. |

Edited components: `ToolEvents.tsx` (icons for the nine tools; route a pending `propose_plan` to
`ActionPlanCard` and `desk_ask` to `AskUserCard` above the generic approval block at `ToolEvents.tsx:55`; an
*"in plan"* tag when `plan_step` is set, a *"not in the plan"* warning when `off_plan`, and a *"planning"* tag
when `blocked_by === 'plan_mode'`); `Message.tsx` (render `{message.plan && <ActionPlanCard …/>}` as a sibling
of `<ToolEvents>` inside `.markdown`, `Message.tsx:24-32`); `ToolPermissions.tsx` (`GROUP_ICON` gains
`plan`/`desk`, `DANGER_LABEL` gains `'plan' → 'Always asks'`); `Composer.tsx` (mount `PlanModeToggle`);
`HomeView.tsx`; `styles.css` (an `/* ---------- Action plans ---------- */` block after the
Approvals section at `styles.css:989-995`, reusing the `.approval` recipe, with `.aplan-*` names).

### 7.7 Shell wiring

* `App.tsx:110-111` — `{view === 'cowork' && <CoworkView />}` between the activity and project lines.
* `Sidebar.tsx:33-43` — `{ view: 'cowork', label: 'Cowork', icon: <Users size={15} /> }` after Activity. **No
  `kind`**: there is no canvas widget in v1, and a `kind` would make the row a drag source for a widget that
  does not exist. `libCount` (`Sidebar.tsx:103-111`) gains `if (v === 'cowork') return needsYouCount || null`
  before the fallthrough, which otherwise shows a nonsense document count.
* `modules.ts` — `OPTIONAL_VIEWS` gains `{ view: 'cowork', label: 'Cowork' }` (SettingsModal renders the
  checkbox from this array, `SettingsModal.tsx:125-130`, with no edit there) (the Today card is the Agent inbox, which also lists desks; there is no separate Today toggle).
* `src/main/index.ts:169` — `{ label: 'Cowork', accelerator: 'CmdOrCtrl+Shift+K', click: () => sendMenu('view:cowork') }`
  after the Activity ⌘9 item. ⌘0–⌘9 are exhausted (`index.ts:160-169`) and ⌘⇧C/⌘B/⌘I are taken
  (`index.ts:171-173`); ⌘⇧K is free. No renderer change — `store.ts:373` routes any `view:*`.
* `src/main/index.ts` also gains a one-shot **native notification** on a terminal desk transition
  (`review`/`blocked`/`failed`), fired from an IPC message the renderer sends when it sees that `desk_status`,
  gated by `settings.deskNotify`. It fires on the transition, from an event already flowing — not a poller. It
  closes the only remaining gap in "hand it a task and go away".
* `package.json:14` — append `src/renderer/src/lib/planDigest.test.ts` to the `test` script's hand-maintained
  esbuild file list. It is a list, not a glob: an unlisted test never runs.

### 7.8 Screen states

| Desk status | Detail header actions | Default tab |
|---|---|---|
| `draft` | Start · Delete | Activity |
| `planning` | Pause · Stop | Activity |
| `awaiting_plan` | Stop | **Plan** |
| `working` | Pause · Stop | Activity |
| `needs_approval` | Stop | Activity (the card is inline there) |
| `blocked` | Resume · Stop · the question in a banner with an answer box | Activity |
| `paused` | Resume · Stop | Activity |
| `interrupted` | Resume · Delete — banner: *"interrupted by a restart"*, and the ledger's `unknown` line if any | Activity |
| `review` | Accept all · Send back · Reject all · Archive | **Output** |
| `done` / `failed` / `stopped` | Archive · Delete (`confirm()` naming what is lost) | Activity |

Keyboard: `⌘⇧K` open Cowork · `[` / `]` previous/next desk · `Enter` open · `1`–`4` tabs · `⌘P` pause ·
`⌘.` stop · `⌘⏎` submit the new-desk brief and the steer box · `⌘⇧A` approve plan · `⌘⇧D` reject plan ·
`⌘⇧P` toggle plan mode in the composer.

---

## 8. Build plan — ordered, dependency-aware work packages

**No two packages write the same file.** Where two would, one owns it and the other specifies what to append.

### Tier 1 — new files only, no shared edits. Fully parallel.

**WP-1 · Run tape**
Owns: `backend/personal_os/runlog.py`, `backend/tests/test_runlog.py`.
Build: the four tables, `args_digest`, `call_key`, `sse`, and `RunStore` per §2.1 — delta coalescing,
`call_once` with the pre-commit, `tail`, `transcript`, `ledger`, the approval row methods,
`recover`, `prune`.
Accept: `python backend/tests/test_runlog.py` green; `args_digest` matches the §4.8 fixtures; `call_once`
returns a cached `done` result without re-invoking; a `started` row left behind becomes `unknown` and then
yields the shaped error, never a second call; 400 deltas produce fewer than 20 `run_events` rows while `tail`
reproduces the full text.

**WP-2 · Workspace**
Owns: `backend/personal_os/workspace.py`, `backend/tests/test_workspace.py`.
Build: §2.4 and §6. Write `test_workspace.py` **first**.
Accept: `resolve_in` refuses `../`, an absolute path, and a symlink inside the workspace pointing out of it;
`write(mode="create")` refuses to clobber; quotas reported with current usage; `trash` never unlinks; `diff`
against `.baseline/` is a real unified diff.

**WP-3 · Action plans**
Owns: `backend/personal_os/plans.py`, `backend/tests/test_plans.py`.
Depends on: WP-1 (imports `runlog.args_digest`).
Build: §2.2 plus `CallPolicy` / `decide_call` (§4.3) as a pure function and the `propose_plan` prose constants.
Accept: `claim` is single-use and digest-exact (second identical call returns None; one changed character
returns None); `decide` is first-wins; an edited step gets a recomputed digest and `edited=1`; `normalize_plan`
rejects an unknown / unavailable / `off` tool; `decide_call`'s ordering is asserted directly — in particular
that a taint-forced external call returns `ask` **without consuming a step**.

**WP-4 · Desks repo**
Owns: `backend/personal_os/cowork.py`, `backend/tests/test_desks.py`.
Depends on: WP-2 (reads `Workspace` for paths; no write to it).
Build: §2.3 plus `DeskRuntime` (§3.5) and the desk prompt constants.
Accept: `claim_run` is a lock (two concurrent claims, one wins); `set_status` writes column and `desk_events`
row in one transaction; `claim_output` is a lock; `recover()` flips `LIVE` rows to `interrupted` with a
`needs_you` event; `DeskRuntime.observe` ignores `delta` and writes at most once a second.

### Tier 2 — the two shared backend surfaces.

**WP-5 · Tool registration and both gates**
Owns: `backend/personal_os/tools.py`, `backend/personal_os/mcp_servers.py`.
Depends on: WP-2, WP-3, WP-4 (imports their classes; writes neither).
Build: `DEFAULT_MODE["plan"]`; `Toolbox.__init__` kwargs + guarded registration; the `plan_phase` /
`proposal_only` clauses in **both** `gate()` and `call()` (§4.4); `ALTERNATIVE` entries; `_register_plan` and
`_register_cowork` with their trampolines; `RESERVED_TOOL_NAMES` + `DANGER_LEVELS`.
Accept: `python -m unittest backend.personal_os.tests.test_mcp_servers` green (this is what catches a missing
reserved name); a direct `await toolbox.call("desk_write_file", {...}, {"plan_phase": True})` returns `denied`
with no filesystem touch; `desk_*` handlers refuse without `ctx["desk_id"]`.

**WP-6 · Backend integration**
Owns: `backend/personal_os/app.py`, `backend/personal_os/runs.py`, `backend/personal_os/repos.py`,
`backend/personal_os/llm.py`, `backend/personal_os/context.py`.
Depends on: WP-1 … WP-5.
Build: everything in §3.2, §3.3, §3.4, §3.6, §3.8, §3.9, §3.10, §3.11 plus the `_chat_stream` edits of §4.2,
§4.3, §4.5. Specifically in `app.py`: the imports at `:41`; the four singletons after `:56`; `bus = RunBus(run_store)`
at `:184`; `_answers` / `_desk_tasks` beside `:188`; the prompt constants and `_caps` beside `:411-434`;
`toolbox(...)` at `:241`; `_chat_stream(… run=None)` at `:483`; `tool_ctx` at `:538-544`; the `_schemas()`
closure replacing `:546` **and** `:730`; plan re-injection beside the soft nudge at `:766-768` and at the top of
`_final_round` (`:562`); `decide_call` replacing `:680-682`; the durable approval block replacing `:694-730`
including the park path; `run_store.call_once` wrapping `:739`; `desk_status` / `desk_handoff` publication;
`_run_desk` / `_desk_supervisor` / `_should_chain` beside `:814`; the stream/runs/approvals edits at `:856-888`;
the whole `/cowork/*` + `/cowork/plans/*` section appended at `:2468`; `_cowork_startup`.
Accept: `python backend/tests/test_runs.py` and `backend/tests/test_approvals.py` still green (no regression in
ordinary chat); a desk created, started, planned, approved and finished end to end by hand with a scripted
`llm.stream_chat`.

### Tier 3 — frontend, parallel after WP-6's shapes are fixed.

**WP-7 · Types, API client, digest**
Owns: `src/shared/types.ts`, `src/renderer/src/lib/api.ts`, `src/renderer/src/lib/planDigest.ts`,
`src/renderer/src/lib/planDigest.test.ts`, `package.json`.
Accept: `npm run typecheck` green; `npm test` runs `planDigest.test.ts` and it agrees with
`test_plan_mode.py`'s fixtures character for character.

**WP-8 · Store and status machine**
Owns: `src/renderer/src/store.ts`, `src/renderer/src/sessionStatus.ts`,
`src/renderer/src/sessionStatus.test.ts`.
Depends on: WP-7.
Accept: `npm test` green, including a case asserting that a `desk_status` arriving **after** `done` leaves the
status at `done`, and that a `plan` event moves it to `awaiting-plan` and `plan_decision` clears it.

**WP-9 · Cowork components**
Owns: `components/CoworkView.tsx`, `DeskRail.tsx`, `DeskDetail.tsx`, `DeskPlan.tsx`, `DeskFiles.tsx`,
`DeskReview.tsx`, `ActionPlanCard.tsx`, `PlanModeToggle.tsx`, `styles/cowork.css`.
Depends on: WP-7, WP-8.
Accept: `npm run typecheck` green; every screen state in §7.8 reachable; `ActionPlanCard` has no store
subscription in its render path.

**WP-10 · Shell wiring**
Owns: `src/renderer/src/App.tsx`, `components/Sidebar.tsx`, `src/renderer/src/modules.ts`,
`components/HomeView.tsx`, `components/Message.tsx`, `components/ToolEvents.tsx`,
`components/ToolPermissions.tsx`, `components/Composer.tsx`, `src/renderer/src/styles.css`,
`src/main/index.ts`, `src/renderer/src/menuShortcuts.test.ts`.
Depends on: WP-9.
Accept: ⌘⇧K opens Cowork; the sidebar badge shows the needs-you count; `npm test` green with a
`fire('view:cowork')` assertion; a `propose_plan` card renders inline in an ordinary chat.

### Tier 4 — end-to-end tests.

**WP-11 · Integration tests**
Owns: `backend/tests/test_plan_mode.py`, `backend/tests/test_cowork.py`, `backend/tests/test_runs.py` (the
`CHAT_EVENTS` edit at `:30-31` only).
Depends on: WP-6.
Accept: both new scripts green standalone; `CHAT_EVENTS` gains `plan`, `plan_decision`, `desk_status`,
`desk_handoff`.

Critical path: **WP-1 → WP-3 → WP-5 → WP-6 → WP-7 → WP-8 → WP-9 → WP-10**, with WP-2/WP-4 and WP-11 hanging off it.

---

## 9. Tests

Backend tests are standalone scripts in `backend/tests/`, no pytest, copying `test_canvas.py:1-31` verbatim
(tempdir `PERSONAL_OS_DATA_DIR` and `PERSONAL_OS_AUTH_TOKEN` set **before** importing `personal_os.app`,
`TestClient` with the token header, `check(cond, label)` / `j(method, path, body, expect)` helpers, a
`__main__` block printing the tally). `llm.stream_chat` is monkeypatched with a scripted async generator whose
signature tracks the real one (`test_runs.py:50-57`). Unit-level repo tests live in
`backend/personal_os/tests/` as `unittest.TestCase` subclasses with invariant-stating docstrings.

**`backend/tests/test_runlog.py`** — canonicalisation fixtures; `call_once` caching; the `started` → `unknown`
rule and the shaped error it yields; delta coalescing row counts vs `tail` fidelity; `?since=` served from the
tape after the run left the 300 s ring; `recover()` salvaging a partial transcript into an empty message row;
`shutdown` marking runs `interrupted`.

**`backend/tests/test_workspace.py`** — written first; traversal, symlink escape, absolute path, clobber
refusal, quota errors, `.trash/` suffixing, diff correctness.

**`backend/tests/test_plans.py`** — `claim` single-use and digest-exact; `decide` first-wins; edit recomputes
the digest; `normalize_plan` rejections; `decide_call`'s nine rules each asserted in isolation, including that a
taint-forced external call never consumes a step.

**`backend/tests/test_desks.py`** — `claim_run` as a lock; `set_status` atomicity; `claim_output` as a lock;
`recover()`; `DeskRuntime` debounce.

**`backend/tests/test_plan_mode.py`** — through the real app. While planning, `tool_schemas` contains no
`writes`/`executes`/`external` tool but does contain `network`; a mutating call attempted anyway is denied by
*both* gates independently (the second asserted by calling `toolbox.call` directly with
`ctx["plan_phase"]=True`); `propose_plan` always asks even when settings say `on`; approval flips the schema set
and the next matching call runs with no card; the same call twice gets a card the second time; one changed
character gets a card; an edited step binds to the user's arguments; rejection blocks that exact step and the
model is told to stop; a plan approved before an *unexpected* taint cannot claim afterwards, while one whose
`expected_taint` covers the source can; `always_chat` on a plan card creates no standing grant; source-text
assertions pinning the single `_schemas()` closure and the two gate clauses.

**`backend/tests/test_cowork.py`** — creating a desk creates a conversation carrying `settings.deskId`, absent
from `GET /conversations` and present with `?include_desks=true`; three desks run concurrently with three
`run_id`s; the `deskMaxLive` 409; a double Start loses the `claim_run` race cleanly with no orphaned run; an
unanswered approval with **zero watchers** parks after `parkAfterSeconds` and the desk goes `blocked` with the
run ended and no task alive; the same approval with a watcher attached does **not** park; deciding it resumes
the desk through the same `resume()` the recovery path uses; a chained turn publishes `desk_handoff` before
`done`; `_should_chain` refuses a turn that consumed no step and lands in `failed`; the desk turn budget caps
the chain; `desk_ask` / `desk_done` set `blocked` / `review`; accept is exactly-once
under a repeated POST and read-back verification turns a truncated write into `promote_failed`; `doc_append`
creates a pending revision and does not touch the doc; deleting without `purge` leaves the workspace on disk.

**Frontend** — `node:test` + `node:assert/strict`, pure logic only, each file hand-listed in `package.json:14`.
`sessionStatus.test.ts` gains the four new `reduceStatus` cases including the post-`done` one;
`planDigest.test.ts` pins `canon()`; `menuShortcuts.test.ts` gains `fire('view:cowork')`.

---

## 10. Non-goals

1. **No canvas widget in v1.** `canvas/registry.ts` is a `Record<WidgetKind, WidgetDef>`, so adding `'cowork'`
   to `WidgetKind` and `canvas.py:11-14` turns a half-finished widget into a typecheck failure. It is a clean
   follow-on: add the kind to both mirrors, write `canvas/widgets/cowork.tsx`, register it, give the Sidebar row
   a `kind`, and call `canvases.delete_windows_for("cowork", id)` from the delete route (`canvas_windows.ref_id`
   has no FK, `canvas.py:43`).
2. **No `cowork_spawn`.** Desks are created by the user, never by a model.
3. **No scheduling.** Cowork is not cron. `worktree-agent-jobs`' `Scheduler` is a separate feature.
4. **No writing into the user's filesystem.** `target: "file"` promotion needs `worktree-filesystem-access`'s
   `write_local`, and coupling cowork to an unmerged branch is a dependency it does not need. The Output tab
   shows a disabled **Save to disk** with a tooltip saying so; Download covers the need meanwhile.
5. **No MCP.** `mcp_servers.py` / `mcp_client.py` are unimported on this branch (`grep -i mcp app.py` → nothing).
   `RESERVED_TOOL_NAMES` is still maintained because its test enforces it.
6. **No faithful tool-message replay on resume.** A resumed desk gets a prose ledger (§3.11), not reconstructed
   `assistant(tool_calls)` / `tool` pairs.
7. **No spend limits.** Cost is reported (desk and Usage) but never stops a run; desks are bounded by turns,
   rounds, time and tokens.
8. **No raising `maxToolRounds`, no auto-enabled skills, no heartbeat, no pixel clicking.** Standing project
   anti-goals. The notification in §7.7 fires on a transition from an event already flowing, and the re-attach
   in §7.4 is a bounded retry on a known handoff — neither is a poller.
9. **No working-memory checklist.** `plans.ActionPlans` is an approval artifact. The names `Plan`, `PlanStep`,
   `chat_plans` and `.plan-*` are left free on purpose so `worktree-working-memory` can land beside this with an
   import-line edit.
10. **No durable in-round resume.** A restart loses the current round's partial reasoning; a desk re-enters at a
    turn boundary with its plan, workspace, outputs and ledger intact. Mid-round resumption is not attempted.

---

## 11. Where the judges disagreed, and what we chose

| Question | Choice | Why, in one line |
|---|---|---|
| Base design | Design 3 (Desks), with Design 2's tape grafted whole | Two of three judges picked it for the daily surface, and all three listed durability as a must-graft rather than a rebuild. |
| `network` during planning | **Allowed** (`PLAN_SAFE_DANGER = safe, network, plan`) | §4.6's expected-taint rule removes the objection, and a plan whose exact arguments were guessed is worth less than one researched. |
| Off-plan mutating call | **Asks**, not denied | A plan is a strong default, not a cage; hard-denial makes the agent brittle the moment reality differs. |
| Approval timeout | Park (viewer-aware, row stays pending), never silent auto-deny for a desk; the chat's 600 s becomes a setting that records `decided_by='timeout'` | Lifting a timeout is only safe once the approval is a row; parking in front of a watching user is a bug, so `run.watchers` gates it. |
| Who owns the chain | A supervisor task outside the run, plus a `desk_handoff` event and a bounded re-attach | Starting the next run from the previous run's `finally` is exactly the bug all three judges found. |
| `Plans` naming | `ActionPlans` / `ActionPlan*` / `.aplan-*` everywhere | Costs nothing now, saves a rename against `worktree-working-memory`. |
| Canvas widget | Cut from v1 | `Record<WidgetKind, WidgetDef>` makes a half-done widget a build failure; the Today card gives the same "felt daily" win for twenty lines. |
