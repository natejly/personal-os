"""SQLite storage. One file, WAL mode, FTS5 for document + memory search."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import backups, migrations
from .secrets import SecretStore

log = logging.getLogger("personal_os.db")

# Settings whose values live in the secret store, not in SQLite (the settings row is left blank).
SECRET_SETTINGS = ("apiKey", "braveApiKey", "tavilyApiKey", "exaApiKey", "githubToken", "googleClientSecret")
# googleToken is a dict; only these fields are secret, the rest (email, expiry, scopes) stays in SQLite.
GOOGLE_TOKEN_SECRET_FIELDS = ("token", "refresh_token", "client_secret")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  color TEXT NOT NULL DEFAULT '#d97757',
  tools TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  model TEXT NOT NULL,
  settings TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversations(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  model TEXT,
  error TEXT,
  context_used TEXT,
  tool_events TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',
  source TEXT NOT NULL DEFAULT 'user',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS kg_nodes (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'entity',
  properties TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_project ON kg_nodes(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_label ON kg_nodes(IFNULL(project_id, ''), lower(label));

CREATE TABLE IF NOT EXISTS kg_edges (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  target_id TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  properties TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edge_project ON kg_edges(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_uniq ON kg_edges(source_id, target_id, lower(relation));

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  mime TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_project ON documents(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_log (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  model TEXT NOT NULL,
  kind TEXT NOT NULL,
  conversation_id TEXT,
  project_id TEXT,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  cost REAL,
  estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_log_created ON usage_log(created_at);
CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(document_id, idx);
-- float32 little-endian, L2-normalised, so cosine = dot (see embed.py). Rows for another model are stale.
CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  document_id TEXT NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_emb_doc ON chunk_embeddings(document_id);

-- Durable runs (see runs.RunStore). A run is a row; its SSE stream is a tail on run_events.
-- status: running | awaiting_approval | done | error | interrupted
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'chat',
  -- Which desk this run is a turn of, and which turn. A desk's work is many runs, so "what is this
  -- desk doing?" has to be answerable from the table rather than only from the live bus.
  desk_id TEXT,
  turn INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running',
  message_id TEXT,
  input TEXT NOT NULL DEFAULT '{}',
  budget TEXT,
  error TEXT,
  last_seq INTEGER NOT NULL DEFAULT 0,
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  ended_at REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_conv ON agent_runs(conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON agent_runs(status, started_at DESC);

-- User-authored agent definitions (subagents.py). Inert until approved by hand, like skills; the
-- built-in researcher / worker / reviewer live in code and are not rows.
CREATE TABLE IF NOT EXISTS agent_defs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  model TEXT,
  steps INTEGER,
  tools TEXT NOT NULL DEFAULT '[]',
  hidden INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

-- Workflows (workflows.py): a saved definition, the runs of it (each carries the exact definition and
-- parameters it was approved with), and one row per step of a run. status of a run: awaiting_approval |
-- running | waiting_approval | done | failed | cancelled | interrupted | stale.
CREATE TABLE IF NOT EXISTS workflows (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  definition TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  workflow_id TEXT,
  name TEXT NOT NULL,
  definition TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  plan_digest TEXT NOT NULL,
  approved_digest TEXT,
  approved_at REAL,
  status TEXT NOT NULL DEFAULT 'awaiting_approval',
  error TEXT,
  result TEXT,
  project_id TEXT,
  conversation_id TEXT,
  source TEXT NOT NULL DEFAULT 'user',
  started_at REAL,
  ended_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wfruns_wf ON workflow_runs(workflow_id, created_at DESC);
CREATE TABLE IF NOT EXISTS workflow_steps (
  run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  step_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  items TEXT,
  error TEXT,
  approval_call_id TEXT,
  idempotency_key TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  started_at REAL,
  ended_at REAL,
  PRIMARY KEY (run_id, step_id)
);

-- Commands (commands.py): markdown prompt templates, the light tier beside workflows.
CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  subtask INTEGER NOT NULL DEFAULT 0,
  role TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  data TEXT NOT NULL,
  ts REAL NOT NULL,
  PRIMARY KEY (run_id, seq)
);

-- One row per tool call that asked the user. status: pending | approved | denied. A pending row waits forever.
CREATE TABLE IF NOT EXISTS approvals (
  call_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  conversation_id TEXT,
  message_id TEXT,
  tool TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  args_digest TEXT NOT NULL,
  forced INTEGER NOT NULL DEFAULT 0,
  -- The tool's danger tier, copied onto the row so a card can say what it is asking for without
  -- having to resolve the tool spec (which may not even exist any more by the time it is read).
  danger TEXT NOT NULL DEFAULT 'external',
  -- Which desk is waiting on this card, when one is. A desk's card outlives the run that raised it
  -- (the run ends, the desk stays blocked), so "is this desk still waiting on someone?" has to be
  -- answerable without the run. NULL for an ordinary chat approval.
  desk_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  decision TEXT,
  decided_by TEXT,
  -- One line the user (or the unattended policy) gave back with a denial; the model reads it as the tool result.
  note TEXT,
  -- A human's rewrite of `args` (approval_edits.py). `args` stays the model's original; `args_digest` is
  -- re-bound to whatever will actually run, so the row proves what was authorised.
  edited_args TEXT,
  edited_by TEXT,
  created_at REAL NOT NULL,
  decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id);

-- Idempotency journal for side-effecting tool calls. key = sha256(run_id, step, tool, args_digest).
-- status: started (in flight, or the process died mid-call: outcome unknown) | done | error
CREATE TABLE IF NOT EXISTS executed_calls (
  key TEXT PRIMARY KEY,
  run_id TEXT REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  step INTEGER NOT NULL,
  tool TEXT NOT NULL,
  args_digest TEXT NOT NULL,
  call_id TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  result TEXT,
  attempts INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_exec_run ON executed_calls(run_id, step);

-- Pre-images of local files the agent overwrote, appended to, created or moved (see filesnap.py).
-- op: overwrite | append | create | move | restore. status: live | restored | expired.
CREATE TABLE IF NOT EXISTS file_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  conversation_id TEXT,
  message_id TEXT,
  call_id TEXT,
  op TEXT NOT NULL,
  path TEXT NOT NULL,
  from_path TEXT,
  before_path TEXT,
  before_digest TEXT,
  before_existed INTEGER NOT NULL DEFAULT 0,
  after_digest TEXT,
  status TEXT NOT NULL DEFAULT 'live',
  created_at REAL NOT NULL,
  restored_at REAL
);
CREATE INDEX IF NOT EXISTS idx_filesnap_conv ON file_snapshots(conversation_id, created_at);

-- Scheduled background work (see jobs.Jobs / jobs.Scheduler). A job fires one run with kind='job'.
-- next_due_at is the slot the scheduler is waiting for; last_due_at is the slot the last launch was *for*,
-- so last_fired_at - last_due_at is how late that fire was (the machine was asleep, or the backend was down).
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  -- kind='cron': `cron` is the expression, read in `timezone`, and the job repeats forever.
  -- kind='once': `run_at` is the single instant it fires, and `cron` is ''. A fired one-off switches itself
  -- off (enabled=0, next_due_at=NULL) rather than being deleted, so the inbox can still show what it did.
  kind TEXT NOT NULL DEFAULT 'cron',
  cron TEXT NOT NULL,
  run_at REAL,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  enabled INTEGER NOT NULL DEFAULT 0,
  prompt TEXT NOT NULL DEFAULT '',
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  last_fired_at REAL,
  last_due_at REAL,
  last_run_id TEXT,
  last_error TEXT,
  next_due_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(enabled, next_due_at);

-- An outward-facing tool call a background run was not allowed to make: recorded here instead of executed
-- (see app.PROPOSAL_ONLY_KINDS). Accepting one is a user action and is what actually runs it, exactly once.
-- status: pending | accepted | rejected
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  job_id TEXT,
  conversation_id TEXT,
  message_id TEXT,
  call_id TEXT,
  tool TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  args_digest TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  error TEXT,
  edited INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_proposals_run ON proposals(run_id);

-- propose_plan (see plans.py): one approval artifact per batch of consequential calls. This is an
-- approval record, not a progress checklist -- approving a plan pre-authorises exactly the argument
-- values in plan_steps, one use per step. status: pending | approved | rejected
CREATE TABLE IF NOT EXISTS action_plans (
  plan_id TEXT PRIMARY KEY,
  call_id TEXT UNIQUE,                 -- the propose_plan call whose approval decides this plan
  run_id TEXT REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  conversation_id TEXT,
  message_id TEXT,
  -- Set when the plan was approved for a desk. A desk's work spans many runs, so its approved
  -- steps have to outlive the run that proposed them; a chat plan leaves this NULL and stays
  -- run-scoped. See Plans.claim.
  desk_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  -- One line on what the plan is for, in the user's terms. The card leads with it.
  intent TEXT NOT NULL DEFAULT '',
  -- Tools in this plan that read untrusted content, as a JSON list. A later external step is then
  -- not re-gated by the plan's own research: see plans.taint_expected.
  expected_taint TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  tainted INTEGER NOT NULL DEFAULT 0,  -- the chat had read untrusted content when the plan was proposed
  decided_by TEXT,                     -- user | stop; only the user's own rejection blocks its steps later
  note TEXT,
  created_at REAL NOT NULL,
  decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_plans_run ON action_plans(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plans_conv ON action_plans(conversation_id, created_at);

-- One proposed call. args_digest is runs.args_digest, the same canonicalisation the approvals table uses.
-- status: proposed | approved (claimable once) | consumed -> done | failed | dropped (edited out) | rejected
CREATE TABLE IF NOT EXISTS plan_steps (
  step_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES action_plans(plan_id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  tool TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  args_digest TEXT NOT NULL,
  why TEXT NOT NULL DEFAULT '',
  -- A short label for the card, so a step reads as a line rather than as a tool name plus JSON.
  title TEXT NOT NULL DEFAULT '',
  -- The tool's tier when the plan was proposed, so the card can sort consequential steps first
  -- without resolving a spec that may have changed since.
  danger TEXT NOT NULL DEFAULT 'safe',
  status TEXT NOT NULL DEFAULT 'proposed',
  edited INTEGER NOT NULL DEFAULT 0,
  call_id TEXT,                        -- the call that consumed this step
  consumed_at REAL,
  result_error TEXT,                   -- set when the consumed call failed; NULL on a step that ran clean
  UNIQUE(plan_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_plan_steps_claim ON plan_steps(tool, args_digest, status);

-- fetch_url's response cache (webread.WebCache); rows are disposable.
CREATE TABLE IF NOT EXISTS web_cache (
  url TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  status INTEGER,
  content_type TEXT,
  body BLOB,
  final_url TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, chunk_id UNINDEXED, document_id UNINDEXED, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, memory_id UNINDEXED, tokenize='porter unicode61'
);

-- Whole-folder snapshots a run took of a granted root (snapshots.py). `files` is the ledger of what the run
-- changed: [{status, path, before, after}] with blob ids. state: applied | undone.
CREATE TABLE IF NOT EXISTS run_snapshots (
  run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  root TEXT NOT NULL,
  before_tree TEXT,
  after_tree TEXT,
  files TEXT NOT NULL DEFAULT '[]',
  skipped TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'applied',
  created_at REAL NOT NULL,
  PRIMARY KEY (run_id, root)
);
"""


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Database:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        self.path = self.data_dir / "personal-os.db"
        self.secrets = SecretStore(self.data_dir)
        existing = self.path.exists() and self.path.stat().st_size > 0
        with self.connect() as c:
            # A database with content gets a snapshot before anything pending touches it (adopting the
            # versioned system on a pre-existing file counts: the baseline re-runs _migrate).
            if existing and migrations.pending(c):
                backups.create(self.data_dir, "premigrate")
            c.executescript(SCHEMA)
            self._migrate(c)
            migrations.run(c)
        self._migrate_secrets()
        self._lock_down()

    def _lock_down(self) -> None:
        """Owner-only: 0700 on the data dir, 0600 on the database and its WAL/SHM sidecars."""
        try:
            os.chmod(self.data_dir, 0o700)
            for suffix in ("", "-wal", "-shm"):
                p = self.path.with_name(self.path.name + suffix)
                if p.exists():
                    os.chmod(p, 0o600)
        except OSError as e:
            log.warning("Could not tighten permissions under %s: %s", self.data_dir, e)

    def _migrate_secrets(self) -> None:
        """Move plaintext secrets left in the settings table into the secret store. Idempotent: a blank
        row (or a token without secret fields) has nothing to move, and a failed move leaves the value."""
        with self.tx() as c:
            rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings").fetchall()}
        for k in SECRET_SETTINGS:
            try:
                v = json.loads(rows[k]) if k in rows else ""
            except ValueError:
                continue
            if isinstance(v, str) and v:
                try:
                    self.set_settings({k: v})
                except Exception as e:  # noqa: BLE001 - keep the plaintext rather than lose the key
                    log.warning("Could not move %s into the secret store: %s", k, e)
        try:
            tok = json.loads(rows["googleToken"]) if "googleToken" in rows else None
        except ValueError:
            tok = None
        if isinstance(tok, dict) and any(tok.get(f) for f in GOOGLE_TOKEN_SECRET_FIELDS):
            try:
                self.set_settings({"googleToken": tok})
            except Exception as e:  # noqa: BLE001
                log.warning("Could not move the Google token into the secret store: %s", e)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Add columns introduced after the first release (CREATE TABLE IF NOT EXISTS won't)."""
        wanted = {
            # Soft delete (trash.py): deleted_at hides a row from every read; deleted_with names the project
            # whose deletion took it along, so restoring the project brings back exactly those rows.
            "projects": {"tools": "TEXT NOT NULL DEFAULT '{}'", "deleted_at": "REAL"},
            "conversations": {"deleted_at": "REAL", "deleted_with": "TEXT"},
            "memories": {"deleted_at": "REAL", "deleted_with": "TEXT", "valid_from": "REAL", "invalid_at": "REAL", "superseded_by": "TEXT",
                         "source_conversation_id": "TEXT", "source_message_id": "TEXT"},
            "documents": {"deleted_at": "REAL", "deleted_with": "TEXT", "content_hash": "TEXT NOT NULL DEFAULT ''",
                          "pinned": "INTEGER NOT NULL DEFAULT 0"},
            "chunks": {"heading": "TEXT NOT NULL DEFAULT ''", "page": "INTEGER", "blurb": "TEXT NOT NULL DEFAULT ''"},
            "kg_edges": {"valid_at": "REAL", "invalid_at": "REAL", "superseded_by": "TEXT",
                         "source_message_id": "TEXT", "fact": "TEXT NOT NULL DEFAULT ''"},
            "messages": {"tool_events": "TEXT", "trace": "TEXT", "reasoning": "TEXT"},
            "jobs": {"kind": "TEXT NOT NULL DEFAULT 'cron'", "run_at": "REAL",
                     "max_retries": "INTEGER NOT NULL DEFAULT 1", "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
                     "paused_reason": "TEXT", "last_skip_at": "REAL", "last_skip_reason": "TEXT",
                     "allowed_tools": "TEXT", "expires_at": "REAL", "watch_dir": "TEXT", "watch_seen": "TEXT"},
            "action_plans": {"desk_id": "TEXT", "intent": "TEXT NOT NULL DEFAULT ''",
                             "expected_taint": "TEXT NOT NULL DEFAULT '[]'"},
            "approvals": {"desk_id": "TEXT", "danger": "TEXT NOT NULL DEFAULT 'external'",
                          # A parked desk card (runs.RunStore.park): when it was let go, what the user
                          # said with their answer, when the desk's next turn was told, and which call
                          # spent the one-shot grant an approved parked card leaves behind.
                          "note": "TEXT", "parked_at": "REAL", "reported_at": "REAL", "claimed_by": "TEXT",
                          "edited_args": "TEXT", "edited_by": "TEXT"},
            "agent_runs": {"desk_id": "TEXT", "turn": "INTEGER NOT NULL DEFAULT 0", "resumed_from": "TEXT", "parent_run_id": "TEXT"},
            "usage_log": {"cached_tokens": "INTEGER NOT NULL DEFAULT 0", "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
                          "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
                          # Who spent it: 'chat', 'job:<id>' or 'desk:<id>', and which tool round of the reply.
                          "tag": "TEXT NOT NULL DEFAULT ''", "round": "INTEGER NOT NULL DEFAULT 0"},
            # idem_key: opt-in "same proposal twice is one proposal" (jobs.Proposals.create), unique when set.
            "proposals": {"idem_key": "TEXT"},
            "plan_steps": {"result_error": "TEXT", "title": "TEXT NOT NULL DEFAULT ''",
                           "danger": "TEXT NOT NULL DEFAULT 'safe'"},
        }
        for table, cols in wanted.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # These index columns the block above may have just added, so they cannot live in SCHEMA:
        # executescript runs before the migration and would hit a column that is not there yet.
        c.execute("CREATE INDEX IF NOT EXISTS idx_mem_valid ON memories(project_id, invalid_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_desk ON agent_runs(desk_id, started_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_parent ON agent_runs(parent_run_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_approvals_desk ON approvals(desk_id, status)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_idem ON proposals(idem_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_plans_desk ON action_plans(desk_id, created_at)")
        c.commit()

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self.connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ---- settings ----
    def get_settings(self) -> dict[str, Any]:
        with self.tx() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        out = {r["key"]: json.loads(r["value"]) for r in rows}
        for k in SECRET_SETTINGS:
            if k in out or self.secrets.get(k):
                out[k] = self.secrets.get(k) or out.get(k) or ""  # the SQLite value is only a not-yet-migrated legacy
        tok = out.get("googleToken")
        if isinstance(tok, dict) and tok:
            try:
                tok = {**tok, **json.loads(self.secrets.get("googleToken") or "{}")}
            except ValueError:
                pass
            out["googleToken"] = tok
        return out

    def set_settings(self, patch: dict[str, Any]) -> None:
        """Secret keys go to the secret store and leave a blank (or secret-free) row behind. An empty
        value here is an explicit clear; the HTTP layer is what treats an empty apiKey as "unchanged"."""
        rows: dict[str, Any] = {}
        for k, v in patch.items():
            if k in SECRET_SETTINGS:
                if v:
                    self.secrets.set(k, str(v))
                else:
                    self.secrets.delete(k)
                v = ""
            elif k == "googleToken":
                if isinstance(v, dict):
                    hidden = {f: v[f] for f in GOOGLE_TOKEN_SECRET_FIELDS if v.get(f)}
                    if hidden:
                        self.secrets.set(k, json.dumps(hidden))
                    else:
                        self.secrets.delete(k)
                    v = {f: x for f, x in v.items() if f not in GOOGLE_TOKEN_SECRET_FIELDS}
                else:
                    self.secrets.delete(k)
            rows[k] = v
        with self.tx() as c:
            for k, v in rows.items():
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v)),
                )


def row_to_dict(r: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except ValueError:
                pass
    return d


def data_dir_from_env() -> Path:
    return Path(os.environ.get("PERSONAL_OS_DATA_DIR", "./data")).expanduser().resolve()
