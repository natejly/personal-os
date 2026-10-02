"""MCP servers: storage, namespacing and the collision rules.

Third-party tools land in the same flat namespace the built-ins live in, so the slug of an
MCP tool is derived server-side (`mcp__<server>__<tool>`) and the client never proposes one.
Two invariants do the security work:
  - every MCP slug carries RESERVED_PREFIX, and no built-in name may, so shadowing a built-in
    is impossible by construction rather than by convention (see RESERVED_TOOL_NAMES);
  - a slug, once handed out, is stable across reconnects - a server cannot get a tool renamed
    under the user, and grants keep pointing at the thing that was approved.
Approval is granted for a shape, not a name: every tool carries a schema_hash and a grant
remembers the hash it was granted for, so a server that silently rewrites a tool's schema or
description falls back to asking.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Iterable

from .db import Database, new_id, now, row_to_dict

# Mirrors DEFAULT_MODE / ToolSpec.danger in tools.py, which owns the built-in permission model.
DANGER_LEVELS = ("safe", "writes", "network", "executes", "external")  # "plan" is built-in only: see plans.py
MODES = ("on", "ask", "off")
# Third-party code we did not write: it asks by default, whatever the server claims.
DEFAULT_DANGER = "external"
DEFAULT_MODE = "ask"

TRANSPORTS = ("stdio", "sse", "http")
SCOPES = ("global", "project", "chat")
EVAL_STATUSES = ("pass", "warn", "fail", "error")

RESERVED_PREFIX = "mcp__"
MAX_SLUG = 64  # OpenAI function names: ^[a-zA-Z0-9_-]{1,64}$
SLUG_ATTEMPTS = 4

# Every built-in tool name registered by Toolbox (tools.py). Listed here, not imported, so this
# module stays free of the tool implementations' dependencies; test_mcp_servers keeps it honest.
RESERVED_TOOL_NAMES = frozenset({
    "search_documents", "read_document", "list_documents",
    "search_memory", "save_memory",
    "graph_search", "graph_traverse", "graph_add",
    "web_search", "fetch_url",
    "youtube_video", "youtube_search", "github_search", "github_read", "read_feed",
    "run_python", "current_time", "propose_plan",
    "agent_spawn", "agent_wait", "agent_stop", "desk_start",
    "workflow_list", "workflow_run", "workflow_resume", "command_list", "command_run",
    "todo_write", "read_tool_result", "skill_list", "skill_draft", "skill_revise", "skill_view",
    "mcp_tool_search",
    "fs_glob", "fs_grep", "fs_edit", "fs_copy", "fs_mkdir",
    "todo_list", "todo_add", "todo_update", "todo_delete",
    "health_summary", "health_log", "health_delete_entry",
    "mail_followups", "schedule_suggest",
    "calendar_events", "calendar_create", "calendar_get", "calendar_update", "calendar_delete", "calendar_respond",
    "calendar_free_busy", "calendar_find_time", "calendar_propose",
    "gmail_search", "gmail_read", "gmail_draft", "gmail_send", "gmail_modify", "gmail_outbox",
    "google_tasks_list", "google_tasks_add", "google_tasks_complete",
    "google_drive_search", "google_drive_read",
    "google_docs_search", "google_docs_read", "google_docs_create", "google_docs_append",
    "google_sheets_read", "google_sheets_write", "google_sheets_create",
    "board_list", "board_add_card", "board_move_card", "board_create",
    "sandbox_exec", "sandbox_write_file", "sandbox_read_file", "sandbox_list_files",
    "sandbox_put_document", "sandbox_reset", "sandbox_checkpoint", "sandbox_restore",
    "doc_list", "doc_search", "doc_read", "doc_create", "doc_edit",
    "activity_recent", "activity_pause", "activity_access", "activity_insights", "activity_report",
    "find_files", "read_local_file", "write_local_file", "move_local_file", "trash_local_file",
    "list_shortcuts", "run_shortcut", "open_page",
    "schedule_task", "scheduled_tasks", "cancel_scheduled_task",
    "writing_style", "save_writing_sample",
    "meeting_list", "meeting_search", "meeting_read",
    "desk_list_files", "desk_read_file", "desk_write_file", "desk_trash_file",
    "desk_deliver", "desk_ask", "desk_done", "desk_import_sandbox",
    "shell_run", "shell_poll", "shell_kill",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_servers (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL,                          -- derived server-side, never sent by the client
  name TEXT NOT NULL,
  transport TEXT NOT NULL DEFAULT 'stdio',     -- stdio | sse | http (remote unused for now)
  command TEXT NOT NULL DEFAULT '',            -- stdio: executable
  args TEXT NOT NULL DEFAULT '[]',             -- stdio: argv after the command
  cwd TEXT NOT NULL DEFAULT '',
  env TEXT NOT NULL DEFAULT '{}',              -- plain environment, safe to show
  secrets TEXT NOT NULL DEFAULT '{}',          -- API keys; injected at launch, never leaves the backend
  url TEXT NOT NULL DEFAULT '',                -- remote transports
  headers TEXT NOT NULL DEFAULT '{}',          -- remote transports
  description TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'idle',         -- idle | connecting | ready | error | disabled
  status_detail TEXT NOT NULL DEFAULT '',
  last_connected_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_server_slug ON mcp_servers(lower(slug));

CREATE TABLE IF NOT EXISTS mcp_tools (
  id TEXT PRIMARY KEY,
  server_id TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,                          -- as the server exports it, unsanitised
  slug TEXT NOT NULL,                          -- mcp__<server>__<tool>, stable for this (server, name)
  description TEXT NOT NULL DEFAULT '',
  parameters TEXT NOT NULL DEFAULT '{}',       -- JSON Schema the server advertised
  schema_hash TEXT NOT NULL,                   -- hash of name + description + parameters
  danger TEXT NOT NULL DEFAULT 'external',
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  schema_changed_at REAL,                      -- last time the advertised shape changed
  missing_since REAL,                          -- gone from the server's list, row kept so the slug holds
  quarantined_at REAL,                         -- a changed shape that added a fail-level finding: withheld until accepted
  reviewed_hash TEXT NOT NULL DEFAULT '',      -- the shape the user last saw or accepted
  UNIQUE(server_id, name)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_tool_slug ON mcp_tools(lower(slug));

CREATE TABLE IF NOT EXISTS mcp_tool_versions (
  id TEXT PRIMARY KEY,
  tool_slug TEXT NOT NULL,                     -- by slug, like grants: history outlives the server row
  schema_hash TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  parameters TEXT NOT NULL DEFAULT '{}',
  seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_versions ON mcp_tool_versions(tool_slug, seen_at DESC);

CREATE TABLE IF NOT EXISTS mcp_grants (
  id TEXT PRIMARY KEY,
  tool_slug TEXT NOT NULL,                     -- by slug, not row id: survives a server being re-added
  scope TEXT NOT NULL DEFAULT 'global',        -- global | project | chat
  scope_id TEXT NOT NULL DEFAULT '',           -- project / conversation id, '' for global
  mode TEXT NOT NULL,                          -- on | ask | off
  schema_hash TEXT NOT NULL DEFAULT '',        -- the shape this grant was granted for
  granted_by TEXT NOT NULL DEFAULT 'user',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_grant_target ON mcp_grants(lower(tool_slug), scope, scope_id);

CREATE TABLE IF NOT EXISTS mcp_evals (
  id TEXT PRIMARY KEY,
  server_id TEXT REFERENCES mcp_servers(id) ON DELETE CASCADE,
  tool_slug TEXT NOT NULL DEFAULT '',          -- '' = the server as a whole
  status TEXT NOT NULL DEFAULT 'pass',         -- pass | warn | fail | error
  summary TEXT NOT NULL DEFAULT '',
  findings TEXT NOT NULL DEFAULT '[]',
  schema_hash TEXT NOT NULL DEFAULT '',        -- the shape that was evaluated
  model TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_eval_server ON mcp_evals(server_id, created_at DESC);
"""

MAX_VERSIONS = 10  # shapes kept per tool; the history is for a human to read, not an audit log
SERVER_JSON = ("args", "env", "headers")
TOOL_JSON = ("parameters",)
_SERVER_FIELDS = {"name", "transport", "command", "args", "cwd", "env", "url", "headers", "description", "enabled"}


def _clean(text: str) -> str:
    """Lowercase, [a-z0-9_] only, no doubled or edge underscores."""
    out: list[str] = []
    for ch in text.strip().lower():
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def _fit(slug: str, room: int = MAX_SLUG) -> str:
    return slug[:room].rstrip("_")


def unique_slug(base: str, taken: Iterable[str] = ()) -> str:
    """Disambiguate an already-clean slug: never a reserved built-in name, never one of `taken`."""
    used = {str(s).lower() for s in taken}
    base = _fit(base) or "server"
    candidate = base
    n = 1
    while candidate.lower() in used or candidate.lower() in RESERVED_TOOL_NAMES:
        n += 1
        suffix = f"_{n}"
        candidate = _fit(base, MAX_SLUG - len(suffix)) + suffix
    return candidate


def slugify(name: str, taken: Iterable[str] = ()) -> str:
    """Derive a slug for a server-supplied name. The client never chooses one."""
    return unique_slug(_clean(name) or "server", taken)


def namespaced(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`: a slug in this shape can never equal a built-in tool name."""
    s = _clean(server) or "server"
    t = _clean(tool) or "tool"
    room = MAX_SLUG - len(RESERVED_PREFIX) - 2 - 4  # 4 back for a disambiguating suffix
    if len(s) + len(t) > room:
        s = _fit(s, max(4, room - len(t)))
        t = _fit(t, max(4, room - len(s)))
    return f"{RESERVED_PREFIX}{s}__{t}"


def derive_tool_slug(server_slug: str, tool_name: str, taken: Iterable[str] = ()) -> str:
    return unique_slug(namespaced(server_slug, tool_name), taken)


def schema_hash(name: str, description: str, parameters: Any) -> str:
    """Identity of a tool's advertised shape. Description counts: the model reads it."""
    blob = json.dumps({"name": name, "description": description or "", "parameters": parameters or {}},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class McpServers:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            # mcp_* tables are created here, after Database._migrate ran, so their later columns are added here too.
            have = {r["name"] for r in c.execute("PRAGMA table_info(mcp_tools)")}
            for col, ddl in (("quarantined_at", "REAL"), ("reviewed_hash", "TEXT NOT NULL DEFAULT ''")):
                if col not in have:
                    c.execute(f"ALTER TABLE mcp_tools ADD COLUMN {col} {ddl}")
        self._migrate_secrets()

    # ---------- secrets ----------
    # The column keeps only the secret names ({name: ""}); the values live in the secret store, one JSON
    # blob per server, so listing keys (secret_keys) still works without touching the Keychain.
    def _secret_name(self, id: str) -> str:
        return f"mcp:{id}"

    def _load_secrets(self, id: str) -> dict[str, str]:
        try:
            v = json.loads(self.db.secrets.get(self._secret_name(id)) or "{}")
        except ValueError:
            return {}
        return {k: str(x) for k, x in v.items()} if isinstance(v, dict) else {}

    def _store_secrets(self, id: str, values: dict[str, str]) -> None:
        if values:
            self.db.secrets.set(self._secret_name(id), json.dumps(values))
        else:
            self.db.secrets.delete(self._secret_name(id))

    def _migrate_secrets(self) -> None:
        """Move plaintext values left in the secrets column into the secret store. Idempotent."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, secrets FROM mcp_servers").fetchall()
        for r in rows:
            try:
                col = json.loads(r["secrets"] or "{}")
            except ValueError:
                continue
            plain = {k: str(v) for k, v in col.items() if v}
            if not plain:
                continue
            try:
                self._store_secrets(r["id"], {**plain, **self._load_secrets(r["id"])})
            except Exception:  # noqa: BLE001 - keep the plaintext rather than lose the key
                continue
            with self.db.tx() as c:
                c.execute("UPDATE mcp_servers SET secrets=? WHERE id=?", (json.dumps({k: "" for k in col}), r["id"]))

    # ---------- servers ----------
    def servers(self, with_secrets: bool = False) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM mcp_servers ORDER BY created_at").fetchall()
            counts = {r[0]: r[1] for r in c.execute("SELECT server_id, COUNT(*) FROM mcp_tools WHERE missing_since IS NULL GROUP BY server_id").fetchall()}
        return [self._server_dict(r, counts.get(r["id"], 0), with_secrets) for r in rows]

    def server(self, id: str, with_secrets: bool = False) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM mcp_servers WHERE id=?", (id,)).fetchone()
            if not r:
                return None
            n = c.execute("SELECT COUNT(*) FROM mcp_tools WHERE server_id=? AND missing_since IS NULL", (id,)).fetchone()[0]
        return self._server_dict(r, n, with_secrets)

    def _server_dict(self, r: sqlite3.Row, tool_count: int, with_secrets: bool) -> dict[str, Any]:
        d = row_to_dict(r, SERVER_JSON) or {}
        secrets = json.loads(d.pop("secrets", "{}") or "{}")
        d["secret_keys"] = sorted(secrets)
        d["enabled"] = bool(d["enabled"])
        d["tool_count"] = tool_count
        if with_secrets:
            d["secrets"] = {k: v for k, v in self._load_secrets(d["id"]).items() if k in secrets}
        return d

    def create_server(self, name: str, transport: str = "stdio", command: str = "", args: list[str] | None = None,
                      env: dict[str, str] | None = None, secrets: dict[str, str] | None = None, cwd: str = "",
                      url: str = "", headers: dict[str, str] | None = None, description: str = "",
                      enabled: bool = True) -> dict[str, Any]:
        sid = new_id()
        t = now()
        plain = {k: str(v) for k, v in (secrets or {}).items() if v}
        for attempt in range(SLUG_ATTEMPTS):
            try:
                with self.db.tx() as c:
                    taken = [r["slug"] for r in c.execute("SELECT slug FROM mcp_servers").fetchall()]
                    c.execute(
                        "INSERT INTO mcp_servers(id,slug,name,transport,command,args,cwd,env,secrets,url,headers,description,enabled,status,status_detail,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?)",
                        (sid, slugify(name, taken), name.strip() or "MCP server",
                         transport if transport in TRANSPORTS else "stdio", command.strip(), json.dumps(args or []),
                         cwd, json.dumps(env or {}), json.dumps({k: "" for k in plain}), url.strip(), json.dumps(headers or {}),
                         description, 1 if enabled else 0, "idle" if enabled else "disabled", t, t),
                    )
                break
            except sqlite3.IntegrityError:  # two windows adding a server at once: the index decides, then retry
                if attempt == SLUG_ATTEMPTS - 1:
                    raise
        self._store_secrets(sid, plain)
        return self.server(sid)  # type: ignore[return-value]

    def update_server(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """The slug is never patchable: renaming a server must not rename its tools."""
        fields: dict[str, Any] = {k: v for k, v in patch.items() if k in _SERVER_FIELDS and v is not None}
        if "transport" in fields and fields["transport"] not in TRANSPORTS:
            del fields["transport"]
        for k in ("args", "env", "headers"):
            if k in fields:
                fields[k] = json.dumps(fields[k])
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip() or "MCP server"
        with self.db.tx() as c:
            if fields:
                fields["updated_at"] = now()
                c.execute(f"UPDATE mcp_servers SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
            incoming = patch.get("secrets") if isinstance(patch.get("secrets"), dict) else None
            dropped = list(patch.get("clear_secrets") or [])
            if incoming or dropped:
                row = c.execute("SELECT secrets FROM mcp_servers WHERE id=?", (id,)).fetchone()
                if row:
                    merged = self._load_secrets(id)
                    for k, v in (incoming or {}).items():
                        if v in (None, ""):  # an empty value means "leave it alone", as in dashboards
                            continue
                        merged[k] = str(v)
                    for k in dropped:
                        merged.pop(k, None)
                    self._store_secrets(id, merged)
                    c.execute("UPDATE mcp_servers SET secrets=?, updated_at=? WHERE id=?",
                              (json.dumps({k: "" for k in merged}), now(), id))
        return self.server(id)

    def delete_server(self, id: str) -> None:
        """Tools go with the server; grants stay, keyed by slug, so re-adding it restores the decisions."""
        with self.db.tx() as c:
            c.execute("DELETE FROM mcp_servers WHERE id=?", (id,))
        self._store_secrets(id, {})

    def set_status(self, id: str, status: str, detail: str = "") -> None:
        with self.db.tx() as c:
            c.execute("UPDATE mcp_servers SET status=?, status_detail=?, updated_at=? WHERE id=?", (status, detail, now(), id))
            if status == "ready":
                c.execute("UPDATE mcp_servers SET last_connected_at=? WHERE id=?", (now(), id))

    def launch_env(self, id: str) -> dict[str, str]:
        """Plain env plus secrets, for the process launcher only."""
        s = self.server(id, with_secrets=True)
        if not s:
            return {}
        return {**{k: str(v) for k, v in (s["env"] or {}).items()}, **{k: str(v) for k, v in (s["secrets"] or {}).items()}}

    # ---------- tools ----------
    def tools(self, server_id: str | None = None, include_missing: bool = False) -> list[dict[str, Any]]:
        where, args = [], []
        if server_id:
            where.append("server_id=?")
            args.append(server_id)
        if not include_missing:
            where.append("missing_since IS NULL")
        sql = "SELECT * FROM mcp_tools" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY slug"
        with self.db.tx() as c:
            return [row_to_dict(r, TOOL_JSON) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def tool(self, slug: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM mcp_tools WHERE lower(slug)=lower(?)", (slug,)).fetchone(), TOOL_JSON)

    def sync_tools(self, server_id: str, exported: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Reconcile what a server advertises. Returns slugs by outcome: added / changed / unchanged / missing.

        A tool already known for this server keeps its slug even if the server renames itself or
        re-orders its list, and a tool that disappears is only marked missing - deleting the row
        would free its slug and let a later tool inherit an old tool's grants.
        """
        t = now()
        out: dict[str, list[str]] = {"added": [], "changed": [], "unchanged": [], "missing": []}
        with self.db.tx() as c:
            srv = c.execute("SELECT slug FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
            if not srv:
                raise ValueError("No such MCP server")
            known = {r["name"]: r for r in c.execute("SELECT * FROM mcp_tools WHERE server_id=?", (server_id,)).fetchall()}
            taken = [r["slug"] for r in c.execute("SELECT slug FROM mcp_tools").fetchall()]
            seen: set[str] = set()
            for spec in exported:
                name = str(spec.get("name") or "").strip()
                if not name:
                    continue
                params = spec.get("parameters")
                if params is None:
                    params = spec.get("inputSchema") or {}
                desc = str(spec.get("description") or "")
                danger = spec.get("danger") if spec.get("danger") in DANGER_LEVELS else DEFAULT_DANGER
                h = schema_hash(name, desc, params)
                seen.add(name)
                row = known.get(name)
                if row is None:
                    slug = derive_tool_slug(srv["slug"], name, taken)
                    taken.append(slug)
                    c.execute(
                        "INSERT INTO mcp_tools(id,server_id,name,slug,description,parameters,schema_hash,danger,first_seen_at,last_seen_at,schema_changed_at,missing_since,reviewed_hash)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)",
                        (new_id(), server_id, name, slug, desc, json.dumps(params), h, danger, t, t, h),
                    )
                    self._write_version(c, slug, h, desc, params, t)
                    out["added"].append(slug)
                    continue
                slug = row["slug"]
                if row["schema_hash"] == h:
                    c.execute("UPDATE mcp_tools SET last_seen_at=?, missing_since=NULL, danger=? WHERE id=?", (t, danger, row["id"]))
                    out["unchanged"].append(slug)
                else:
                    if not c.execute("SELECT 1 FROM mcp_tool_versions WHERE tool_slug=? LIMIT 1", (slug,)).fetchone():
                        # a tool that predates the history table: keep the shape it is being changed *from*
                        self._write_version(c, slug, row["schema_hash"], row["description"],
                                            json.loads(row["parameters"] or "{}"), row["first_seen_at"])
                    self._write_version(c, slug, h, desc, params, t)
                    c.execute(
                        "UPDATE mcp_tools SET description=?, parameters=?, schema_hash=?, danger=?, last_seen_at=?, schema_changed_at=?, missing_since=NULL WHERE id=?",
                        (desc, json.dumps(params), h, danger, t, t, row["id"]),
                    )
                    out["changed"].append(slug)
            for name, row in known.items():
                if name not in seen:
                    if row["missing_since"] is None:
                        c.execute("UPDATE mcp_tools SET missing_since=? WHERE id=?", (t, row["id"]))
                    out["missing"].append(row["slug"])
        return out

    @staticmethod
    def _write_version(c: sqlite3.Connection, slug: str, h: str, desc: str, params: Any, seen_at: float) -> None:
        """Append one shape to a tool's history and trim it to the last MAX_VERSIONS."""
        c.execute("INSERT INTO mcp_tool_versions(id,tool_slug,schema_hash,description,parameters,seen_at) VALUES(?,?,?,?,?,?)",
                  (new_id(), slug, h, desc, json.dumps(params), seen_at))
        c.execute("DELETE FROM mcp_tool_versions WHERE tool_slug=? AND id NOT IN ("
                  "SELECT id FROM mcp_tool_versions WHERE tool_slug=? ORDER BY seen_at DESC, rowid DESC LIMIT ?)",
                  (slug, slug, MAX_VERSIONS))

    def versions(self, slug: str, limit: int = MAX_VERSIONS) -> list[dict[str, Any]]:
        """A tool's recorded shapes, newest first."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM mcp_tool_versions WHERE tool_slug=? ORDER BY seen_at DESC, rowid DESC LIMIT ?",
                             (slug, max(1, int(limit)))).fetchall()
        return [row_to_dict(r, TOOL_JSON) for r in rows]  # type: ignore[misc]

    def set_quarantine(self, slug: str, on: bool) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE mcp_tools SET quarantined_at=? WHERE lower(slug)=lower(?)", (now() if on else None, slug))

    def mark_reviewed(self, slug: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE mcp_tools SET quarantined_at=NULL, reviewed_hash=schema_hash WHERE lower(slug)=lower(?)", (slug,))

    # ---------- grants ----------
    def grants(self, tool_slug: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mcp_grants" + (" WHERE lower(tool_slug)=lower(?)" if tool_slug else "") + " ORDER BY created_at"
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute(sql, (tool_slug,) if tool_slug else ()).fetchall()]  # type: ignore[misc]

    def set_grant(self, tool_slug: str, mode: str, scope: str = "global", scope_id: str | None = None,
                  granted_by: str = "user", schema_hash: str | None = None) -> dict[str, Any]:
        """Persisted in SQLite so an always-allow outlives an app update. Bound to the shape it approved."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        tool = self.tool(tool_slug)
        h = schema_hash if schema_hash is not None else (tool["schema_hash"] if tool else "")
        slug = tool["slug"] if tool else tool_slug
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO mcp_grants(id,tool_slug,scope,scope_id,mode,schema_hash,granted_by,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(lower(tool_slug),scope,scope_id) DO UPDATE SET mode=excluded.mode, schema_hash=excluded.schema_hash,"
                " granted_by=excluded.granted_by, updated_at=excluded.updated_at",
                (new_id(), slug, scope, scope_id or "", mode, h, granted_by, t, t),
            )
            r = c.execute("SELECT * FROM mcp_grants WHERE lower(tool_slug)=lower(?) AND scope=? AND scope_id=?",
                          (slug, scope, scope_id or "")).fetchone()
        return row_to_dict(r)  # type: ignore[return-value]

    def clear_grant(self, tool_slug: str, scope: str = "global", scope_id: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM mcp_grants WHERE lower(tool_slug)=lower(?) AND scope=? AND scope_id=?",
                      (tool_slug, scope, scope_id or ""))

    def effective_mode(self, tool_slug: str, project_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        """chat grant → project grant → global grant → tool default, then the stale-schema veto.

        `stale` means the approved shape is not the shape on offer, so an 'on' decays to 'ask':
        the user approved a tool, not a name the server can point anywhere.
        """
        tool = self.tool(tool_slug)
        mode, source, grant = DEFAULT_MODE, "default", None
        by_key = {(g["scope"], g["scope_id"]): g for g in self.grants(tool_slug)}
        for scope, sid in (("global", ""), ("project", project_id or ""), ("chat", conversation_id or "")):
            if scope != "global" and not sid:
                continue
            g = by_key.get((scope, sid))
            if g and g["mode"] in MODES:
                mode, source, grant = g["mode"], scope, g
        stale = bool(tool and grant and grant["schema_hash"] and grant["schema_hash"] != tool["schema_hash"])
        if stale and mode == "on":
            mode = "ask"
        return {"slug": tool["slug"] if tool else tool_slug, "mode": mode, "source": source, "stale": stale,
                "approved_hash": (grant or {}).get("schema_hash", ""), "schema_hash": (tool or {}).get("schema_hash", ""),
                "missing": bool(tool and tool["missing_since"]), "known": tool is not None}

    def effective_modes(self, project_id: str | None = None, conversation_id: str | None = None,
                        include_missing: bool = False) -> dict[str, dict[str, Any]]:
        return {t["slug"]: self.effective_mode(t["slug"], project_id, conversation_id)
                for t in self.tools(include_missing=include_missing)}

    # ---------- eval reports ----------
    def record_eval(self, server_id: str | None, status: str, summary: str = "", findings: list[Any] | None = None,
                    tool_slug: str = "", model: str = "", schema_hash: str = "") -> dict[str, Any]:
        eid = new_id()
        if not schema_hash and tool_slug:
            tool = self.tool(tool_slug)
            schema_hash = tool["schema_hash"] if tool else ""
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO mcp_evals(id,server_id,tool_slug,status,summary,findings,schema_hash,model,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, server_id, tool_slug, status if status in EVAL_STATUSES else "error", summary,
                 json.dumps(findings or []), schema_hash, model, now()),
            )
            return row_to_dict(c.execute("SELECT * FROM mcp_evals WHERE id=?", (eid,)).fetchone(), ("findings",))  # type: ignore[return-value]

    def evals(self, server_id: str | None = None, tool_slug: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, args = [], []
        if server_id:
            where.append("server_id=?")
            args.append(server_id)
        if tool_slug:
            where.append("lower(tool_slug)=lower(?)")
            args.append(tool_slug)
        sql = "SELECT * FROM mcp_evals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        with self.db.tx() as c:
            return [row_to_dict(r, ("findings",)) for r in c.execute(sql, (*args, max(1, int(limit)))).fetchall()]  # type: ignore[misc]

    def latest_eval(self, server_id: str) -> dict[str, Any] | None:
        rows = self.evals(server_id=server_id, limit=1)
        return rows[0] if rows else None
