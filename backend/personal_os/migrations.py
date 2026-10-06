"""Versioned schema migrations, tracked in SQLite's PRAGMA user_version.

Version 1 is the baseline: "whatever SCHEMA plus Database._migrate produces". Every database that
existed before this module reads user_version 0, runs that baseline code exactly as it always did, and
is stamped 1, so adopting the system changes nothing. Later changes append a numbered step to
MIGRATIONS (a plain function taking the connection); they run in order, each followed by its
user_version bump in the same transaction. A raised step rolls back and leaves the version where it was.

Database.__init__ takes a backup (backups.py) before running anything pending on a database that
already had content.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

Step = Callable[[sqlite3.Connection], None]


def _baseline(c: sqlite3.Connection) -> None:
    """Nothing to do: SCHEMA and Database._migrate have already produced the v1 shape."""


def _messages_fts(c: sqlite3.Connection) -> None:
    """Full-text index over message bodies. External-content, kept in step by triggers because message rows
    also vanish through FK cascades (conversation, trash purge). `UPDATE OF content` reindexes a finished
    reply; trace/reasoning writes do not touch it."""
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='rowid', tokenize='porter unicode61')")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN "
              "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN "
              "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content); END")
    c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content ON messages BEGIN "
              "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content); "
              "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); END")
    c.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")


def _boards_into_todos(c: sqlite3.Connection) -> None:
    """Kanban boards merge into Todos: each card becomes a todo on a list named for its board (todos.import_boards)."""
    from . import todos
    todos.import_boards(c)


def _activity_record_everything_keys(c: sqlite3.Connection) -> None:
    """Rename the stored activity keys the old two stored keys to `recordEverything` / `recordEverythingRestore`."""
    import json
    row = c.execute("SELECT value FROM settings WHERE key = 'activity'").fetchone()
    if not row:
        return
    try:
        cfg = json.loads(row[0])
    except ValueError:
        return
    if not isinstance(cfg, dict):
        return
    for old, new in (("pal" "antir", "recordEverything"), ("pal" "antirRestore", "recordEverythingRestore")):
        if old in cfg:
            cfg.setdefault(new, cfg[old])
            del cfg[old]
    c.execute("UPDATE settings SET value = ? WHERE key = 'activity'", (json.dumps(cfg),))


def _permissions_store(c: sqlite3.Connection) -> None:
    """The ~22 top-level permission keys (tools, alwaysAsk, permissionRules, ...) fold into one versioned
    `permissions` row and are deleted (permissions.py). A fresh database gets {"version": 1}; defaults fill the rest."""
    from . import permissions
    permissions.migrate(c)


def _permission_mode(c: sqlite3.Connection) -> None:
    """Every existing install gets permissionMode "auto" (permissions.migrate_mode); no other key changes."""
    from . import permissions
    permissions.migrate_mode(c)


def _chat_files(c: sqlite3.Connection) -> None:
    """chat_files: which chat each file belongs to, fed by triggers on messages, file_snapshots and coding_sessions (chat_files.py)."""
    from . import chat_files
    chat_files.migrate(c)


def _meetings_activity_defaults(c: sqlite3.Connection) -> None:
    """Meetings and Activity now ship on. Only a bare `{"enabled": false}` row (nothing but that key, the
    stub an older whole-settings save could write) is flipped to true. Both services only ever store their
    FULL config, and only on a user action: a Start/Stop, the consent notice, or any edit in their panels.
    So a full row with `enabled: false` - even one equal to the defaults, which is what Stop leaves behind -
    is a user who was in there and left it off, and it stays off. A missing row needs nothing: the new
    default applies on read. Neither switch records on its own; consent and OS permissions still gate that."""
    import json
    for key in ("activity", "meetings"):
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        try:
            cfg = json.loads(row[0]) if row else None
        except ValueError:
            continue
        if cfg == {"enabled": False}:
            c.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps({"enabled": True}), key))


def _approval_history(c: sqlite3.Connection) -> None:
    """The approval decision log (approval_log.py): one row per answer, standing-grant pass and reviewer verdict.
    `approvals.review` keeps the review gate's verdict on the card it opened, so the answer's log row can carry it."""
    c.execute("CREATE TABLE IF NOT EXISTS approval_log ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, conversation_id TEXT, run_id TEXT, desk_id TEXT, "
              "agent TEXT, tool TEXT NOT NULL, args_summary TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL, scope TEXT, "
              "rule_json TEXT, note TEXT, reviewer_verdict TEXT, reviewer_reason TEXT, reviewer_model TEXT, reviewer_ms INTEGER, "
              "call_id TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_approval_log_ts ON approval_log(ts)")
    if "review" not in {r[1] for r in c.execute("PRAGMA table_info(approvals)")}:
        c.execute("ALTER TABLE approvals ADD COLUMN review TEXT")


def _teach_recordings(c: sqlite3.Connection) -> None:
    """Teach-a-task recordings (teach.py): a folder of screen frames plus the step draft extracted from them,
    and the skill / routine that came out of it. Frames live under <data_dir>/teach/<id>/, deleted with the row."""
    c.execute("CREATE TABLE IF NOT EXISTS teach_recordings ("
              "id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
              "status TEXT NOT NULL DEFAULT 'recording', "  # recording | ready | extracted | saved
              "source TEXT NOT NULL DEFAULT 'screen', "     # screen | import
              "dir TEXT NOT NULL, frame_count INTEGER NOT NULL DEFAULT 0, "
              "steps_json TEXT, skill_id TEXT, job_id TEXT)")


def _ship_checklists(c: sqlite3.Connection) -> None:
    """A job's ship checklist (ship.py): tests -> push -> pr -> merge for one branch, each step's status, log tail
    and link in steps_json (a fixed ordered list). status: running | awaiting_confirm | done | failed | cancelled."""
    c.execute("""CREATE TABLE IF NOT EXISTS ship_checklists (
      id TEXT PRIMARY KEY,
      job_id TEXT,
      run_id TEXT,
      repo_path TEXT NOT NULL,
      branch TEXT NOT NULL,
      base TEXT NOT NULL,
      test_command TEXT,
      status TEXT NOT NULL,
      steps_json TEXT NOT NULL,
      pr_url TEXT,
      merged_sha TEXT,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ship_checklists_job ON ship_checklists(job_id, created_at)")


def _doc_comments_typography(c: sqlite3.Connection) -> None:
    """Comments on a doc (docs.py): a thread row anchors to a quoted span of the rendered text with ~32 chars of
    context either side plus the offset it was made at, so it can be found again after the text moves; a reply
    row carries the thread's id in parent_id and no anchor. `resolved` lives on the thread row.
    `docs.typography` is the per-doc font choice as JSON ({font, size, measure}); NULL follows the global default.
    On a fresh database `docs` is created later by Docs.__init__, whose column loop adds the same column."""
    c.execute("""CREATE TABLE IF NOT EXISTS doc_comments (
      id TEXT PRIMARY KEY,
      doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
      parent_id TEXT,
      author TEXT NOT NULL DEFAULT 'user',
      body TEXT NOT NULL DEFAULT '',
      quote TEXT NOT NULL DEFAULT '',
      prefix TEXT NOT NULL DEFAULT '',
      suffix TEXT NOT NULL DEFAULT '',
      offset_hint INTEGER NOT NULL DEFAULT 0,
      resolved INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doc_comments_doc ON doc_comments(doc_id, created_at)")
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone():
        if "typography" not in {r[1] for r in c.execute("PRAGMA table_info(docs)")}:
            c.execute("ALTER TABLE docs ADD COLUMN typography TEXT")


def _coding_sessions(c: sqlite3.Connection) -> None:
    """Coding sessions (codingagents.py): one row per coding agent started on a repo or a fresh worktree of it.
    agent: claude | opencode. external_id: claude's short job id, or the opencode job id in the shell registry;
    session_id: claude's full sessionId (what --resume needs) or opencode's own session id when seen.
    status: starting | working | needs_you | blocked | done | stopped | failed."""
    c.execute("""CREATE TABLE IF NOT EXISTS coding_sessions (
      id TEXT PRIMARY KEY,
      agent TEXT NOT NULL,
      external_id TEXT,
      session_id TEXT,
      repo_path TEXT NOT NULL,
      worktree TEXT NOT NULL,
      branch TEXT,
      conversation_id TEXT,
      desk_id TEXT,
      run_id TEXT,
      name TEXT NOT NULL,
      prompt TEXT NOT NULL,
      model TEXT,
      permission_mode TEXT,
      status TEXT NOT NULL,
      detail TEXT,
      log_tail TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      ended_at REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_coding_sessions_conv ON coding_sessions(conversation_id, created_at)")


def _sticky_notes_into_docs(c: sqlite3.Connection) -> None:
    """Sticky notes become docs. Each note row turns into a doc with the SAME id (so a space window keeps its
    ref_id), titled from its first line (docs.title_from_body), body, project and timestamps kept, filed at the
    root. Windows of kind 'note' on spaces and in stored presets become kind 'doc'; then `notes` is dropped
    (a premigrate backup exists). The doc's search row is written here; chunks and tags come from Docs.__init__'s
    backfills on the next start. `canvas_windows` / `canvas_presets` are owned by Canvases / CanvasPresets, so
    they are touched only if they already exist."""
    from .docs import title_from_body  # lazy: docs imports db, which imports this module

    def has(name: str) -> bool:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    if not has("notes"):
        return
    if not has("docs"):  # normally created by Docs.__init__; its column loop completes this base set later
        c.execute("""CREATE TABLE docs (
          id TEXT PRIMARY KEY,
          project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
          title TEXT NOT NULL DEFAULT 'Untitled',
          content TEXT NOT NULL DEFAULT '',
          folder TEXT NOT NULL DEFAULT '',
          starred INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL)""")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(title, content, doc_id UNINDEXED, tokenize='porter unicode61')")
    # A project that vanished while foreign keys were off would fail the docs FK and abort startup: file it personal.
    proj = "CASE WHEN project_id IN (SELECT id FROM projects) THEN project_id END" if has("projects") else "NULL"
    for n in c.execute(f"SELECT id, {proj}, body, created_at, updated_at FROM notes").fetchall():
        if c.execute("SELECT 1 FROM docs WHERE id=?", (n[0],)).fetchone():
            continue
        title = title_from_body(n[2])
        c.execute("INSERT INTO docs(id,project_id,title,content,folder,starred,created_at,updated_at) VALUES(?,?,?,?,'',0,?,?)",
                  (n[0], n[1], title, n[2], n[3], n[4]))
        c.execute("INSERT INTO docs_fts(title, content, doc_id) VALUES(?,?,?)", (title, n[2], n[0]))
    if has("canvas_windows"):
        c.execute("UPDATE canvas_windows SET kind='doc' WHERE kind='note'")
    if has("canvas_presets"):
        for pid, raw in c.execute("SELECT id, windows FROM canvas_presets").fetchall():
            try:
                ws = json.loads(raw or "[]")
            except ValueError:
                continue
            if isinstance(ws, list) and any(isinstance(w, dict) and w.get("kind") == "note" for w in ws):
                for w in ws:
                    if isinstance(w, dict) and w.get("kind") == "note":
                        w["kind"] = "doc"
                c.execute("UPDATE canvas_presets SET windows=? WHERE id=?", (json.dumps(ws), pid))
    c.execute("DROP TABLE notes")


def _drop_meetings_activity(c: sqlite3.Connection) -> None:
    """The meetings recorder and the activity monitor are gone: drop their tables (child tables first; an index
    goes with its table, and a virtual table takes its shadow tables with it). Settings rows `meetings`, `activity`
    and `digest` are left alone: stt.config_for seeds the voice config from a legacy `meetings` row at read time."""
    for table in ("meeting_action_items", "meeting_revisions", "meeting_segments", "meeting_vectors", "meetings_fts",
                  "meetings", "activity_events", "activity_summaries", "activity_profile", "activity_day_stats",
                  "activity_habits", "activity_suggestions", "activity_patterns"):
        c.execute(f"DROP TABLE IF EXISTS {table}")


# (version, name, step). Versions are consecutive from 1; append, never edit or reorder.
MIGRATIONS: list[tuple[int, str, Step]] = [
    (1, "baseline", _baseline),
    (2, "messages_fts", _messages_fts),
    (3, "boards_into_todos", _boards_into_todos),
    (4, "activity_record_everything_keys", _activity_record_everything_keys),
    (5, "permissions_store", _permissions_store),
    (6, "meetings_activity_defaults", _meetings_activity_defaults),
    (7, "approval_history", _approval_history),
    (8, "teach_recordings", _teach_recordings),
    (9, "ship_checklists", _ship_checklists),
    (10, "doc_comments_typography", _doc_comments_typography),
    (11, "coding_sessions", _coding_sessions),
    (12, "sticky_notes_into_docs", _sticky_notes_into_docs),
    (13, "permission_mode", _permission_mode),
    (14, "chat_files", _chat_files),
    (15, "drop_meetings_activity", _drop_meetings_activity),
]


def latest() -> int:
    return MIGRATIONS[-1][0]


def current(c: sqlite3.Connection) -> int:
    return int(c.execute("PRAGMA user_version").fetchone()[0])


def pending(c: sqlite3.Connection) -> list[tuple[int, str, Step]]:
    have = current(c)
    return [m for m in MIGRATIONS if m[0] > have]


def run(c: sqlite3.Connection) -> list[int]:
    """Apply every pending step in order; returns the versions applied."""
    done: list[int] = []
    for version, _name, step in pending(c):
        try:
            c.execute("BEGIN")
            step(c)
            c.execute(f"PRAGMA user_version = {int(version)}")  # PRAGMA takes no bound parameters
            c.commit()
        except Exception:
            c.rollback()
            raise
        done.append(version)
    return done
