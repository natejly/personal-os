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
import re
import sqlite3
from bisect import bisect_left, bisect_right
from typing import Callable

from .memory_limits import BACKFILL_WINDOW_S

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


def _memories_expires_at(c: sqlite3.Connection) -> None:
    """Short-lived notes: a memory past `expires_at` (epoch seconds) leaves context and search but stays as history.
    NULL = no expiry."""
    if "expires_at" not in {r[1] for r in c.execute("PRAGMA table_info(memories)")}:
        c.execute("ALTER TABLE memories ADD COLUMN expires_at REAL")


def sync_memories_fts(c: sqlite3.Connection, ids: list[str] | None = None) -> None:
    """Make memories_fts hold exactly the live memories (not trashed, not superseded or forgotten), for `ids` or all.
    Idempotent. Trash and restore call it for the rows they flip, so search never ranks a row that context drops."""
    for i in range(0, len(ids), 500) if ids is not None else [None]:
        chunk = None if ids is None else ids[i:i + 500]
        ph = "" if chunk is None else f" AND id IN ({','.join('?' * len(chunk))})"
        fph = "" if chunk is None else f" AND memory_id IN ({','.join('?' * len(chunk))})"
        args = tuple(chunk or ())
        c.execute(f"DELETE FROM memories_fts WHERE memory_id NOT IN (SELECT id FROM memories WHERE deleted_at IS NULL AND invalid_at IS NULL){fph}", args)
        c.execute(f"INSERT INTO memories_fts(content, memory_id) SELECT content, id FROM memories WHERE deleted_at IS NULL AND invalid_at IS NULL{ph} "
                  "AND id NOT IN (SELECT memory_id FROM memories_fts)", args)


def _cols(c: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}


def _memories_fts_live(c: sqlite3.Connection) -> None:
    """Trashed and superseded memories kept their search rows, and some deletes left orphans: rebuild to the live set."""
    if {"deleted_at", "invalid_at"} <= _cols(c, "memories") and _cols(c, "memories_fts"):
        sync_memories_fts(c)


def _memory_provenance_backfill(c: sqlite3.Connection) -> None:
    """Link an auto memory that has no source to the one assistant reply that finished just before it was written.

    Extraction lands within BACKFILL_WINDOW_S of its reply finishing. Exactly one reply in that window is a
    match; none or several is ambiguous and the memory stays unlinked, because a wrong source is worse than none.
    Finish time is the latest trace span end (epoch ms) of the reply itself, else the row's created_at. The
    auto-learn span is skipped: it is appended after the memory is written, so it always ends later. A memory
    that replaced another row (an edit or a merge) is skipped too."""
    if not {"source_message_id", "superseded_by"} <= _cols(c, "memories") or "trace" not in _cols(c, "messages"):
        return
    # A row that replaced another is a user edit or a tidy-up merge, not a fresh learn: its time says nothing about a reply.
    mems = c.execute("SELECT id, created_at FROM memories WHERE source='auto' "
                     "AND source_conversation_id IS NULL AND source_message_id IS NULL "
                     "AND id NOT IN (SELECT superseded_by FROM memories WHERE superseded_by IS NOT NULL)").fetchall()
    replies = []  # (finish, id, conversation_id, created_at), computed once and sorted by finish
    for mid, conv, created, trace in c.execute("SELECT id, conversation_id, created_at, trace FROM messages WHERE role='assistant'").fetchall():
        fin = created
        try:
            ends = [sp["end"] / 1000 for sp in json.loads(trace or "[]")
                    if isinstance(sp, dict) and sp.get("kind") != "learn" and isinstance(sp.get("end"), (int, float))]
            fin = max(ends) if ends else created
        except (ValueError, TypeError):
            pass
        replies.append((fin, mid, conv, created))
    replies.sort()
    fins = [r[0] for r in replies]
    for mem in mems:
        t = mem[1]
        cands = [r for r in replies[bisect_left(fins, t - BACKFILL_WINDOW_S):bisect_right(fins, t)] if r[3] <= t]
        if len(cands) != 1:
            continue
        _, _, conv, created = cands[0]
        um = c.execute("SELECT id FROM messages WHERE conversation_id=? AND role='user' AND created_at<=? ORDER BY created_at DESC LIMIT 1",
                       (conv, created)).fetchone()
        if um:
            c.execute("UPDATE memories SET source_conversation_id=?, source_message_id=? WHERE id=?", (conv, um[0], mem[0]))


def _graph_canonical_types(c: sqlite3.Connection) -> None:
    """Graph vocabulary becomes closed: node types map onto graph_learn.TYPES and edge relations onto its predicates.
    The old phrase is kept as the edge's fact (when it has none) unless it only spells the predicate ("works at"): an
    unknown phrase becomes `related_to`, and a synonym ("works for") keeps what the word said. A row whose
    new name would collide with another edge of the same pair (idx_edge_uniq) keeps its old relation."""
    from .graph_learn import canonical_type, normalize_predicate  # lazy: graph_learn imports memory_limits

    for nid, typ in c.execute("SELECT id, type FROM kg_nodes").fetchall():
        if canonical_type(typ) != typ:
            c.execute("UPDATE kg_nodes SET type=? WHERE id=?", (canonical_type(typ), nid))
    for eid, src, dst, rel, fact in c.execute("SELECT id, source_id, target_id, relation, fact FROM kg_edges").fetchall():
        pred, label = normalize_predicate(rel)
        if not pred or pred == rel:
            continue
        if c.execute("SELECT 1 FROM kg_edges WHERE source_id=? AND target_id=? AND lower(relation)=lower(?) AND id<>?",
                     (src, dst, pred, eid)).fetchone():
            continue
        spelled = re.sub(r"[\s\-]+", "_", rel.strip().lower()) == pred
        c.execute("UPDATE kg_edges SET relation=?, fact=? WHERE id=?", (pred, fact or label or ("" if spelled else rel.strip()), eid))


_LEGACY_TEXTING_KEYS = ("imessageEnabled", "imessageHandles", "imessageSelfChatGuid", "imessageReplyMarker",
                        "imessageConversationId", "imessageNotifyLongRuns", "imessageLongRunMinutes", "imessageState")


def _drop_legacy_texting_keys(c: sqlite3.Connection) -> None:
    """Texting moved to Telegram: the retired bridge's stored settings are dead weight."""
    c.execute(f"DELETE FROM settings WHERE key IN ({','.join('?' * len(_LEGACY_TEXTING_KEYS))})", _LEGACY_TEXTING_KEYS)


def _messages_kind(c: sqlite3.Connection) -> None:
    """messages.kind: NULL for what was said in the chat, 'wake' for the hidden turn that hands a finished worker's report
    to the assistant. Wake rows are replayed to the model and hidden from the transcript, search and exports."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    if cols and "kind" not in cols:  # no columns: no messages table yet (the schema creates it with the rest)
        c.execute("ALTER TABLE messages ADD COLUMN kind TEXT")


BUDGET_SETTING_KEYS = ("maxToolRounds", "maxRunTokens", "maxRunSeconds", "subagentMaxRounds", "deskMaxTurns",
                       "codingSessionTimeoutMinutes", "contextBudget", "skillsInlineBudget", "usageAlerts")


def _drop_budget_settings(c: sqlite3.Connection) -> None:
    """Round, token, time, turn and spend limits no longer exist; their stored values are dead rows."""
    c.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in BUDGET_SETTING_KEYS])


def _autonomous_by_default(c: sqlite3.Connection) -> None:
    """Every existing install gets `autonomousByDefault` true: a new chat starts as a task (a desk) that works through its
    steps, and a plain question is simply answered. A value already stored (impossible before this step) is kept."""
    c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('autonomousByDefault', ?)", (json.dumps(True),))


def _drop_nav_placement(c: sqlite3.Connection) -> None:
    """Views no longer move to the title bar: Lists, Calendar, Mail and Health are sidebar rows, so the stored placement is dead."""
    c.execute("DELETE FROM settings WHERE key = 'navPlacement'")


def _drop_memory_hidden_view(c: sqlite3.Connection) -> None:
    """Memory moved into Settings and is no longer a sidebar row, so a stored hiddenViews entry for it is dead
    (and would tell the model Memory is hidden)."""
    row = c.execute("SELECT value FROM settings WHERE key = 'hiddenViews'").fetchone()
    if not row:
        return
    try:
        views = json.loads(row[0])
    except ValueError:
        return
    if isinstance(views, list) and "memory" in views:
        c.execute("UPDATE settings SET value = ? WHERE key = 'hiddenViews'", (json.dumps([v for v in views if v != "memory"]),))


def _allow_all_connections_for_existing(c: sqlite3.Connection) -> None:
    """An install that already has chats keeps the connections it had: allowAllConnections on. A fresh database (every
    migration runs at once, nothing yet written) stays off. The permissions row exists either way, so it is no signal."""
    from . import permissions
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('conversations', 'messages')")}
    if any(c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in tables):  # a bare test schema may lack both
        permissions._write(c, {"allowAllConnections": True})


# Frozen prefixes of the backend's own desk messages that were stored as plain user rows before they carried a kind.
_INTERNAL_PREFIXES_BEFORE_KIND = (("nudge", "You ended your reply without calling"), ("continue", "Continuing this desk."),
                                  ("resume", "Resuming this desk after an interruption"),
                                  ("handoff", "The user has asked you to carry on with the task in this conversation"))


def _internal_message_kinds(c: sqlite3.Connection) -> None:
    """Hide desk nudges, continues, resumes and hand-offs that leaked into transcripts as the user's words: they get a kind
    (the model still reads them; the transcript, search and exports skip them). Nothing is deleted."""
    if "kind" not in {r[1] for r in c.execute("PRAGMA table_info(messages)")}:
        return
    for kind, prefix in _INTERNAL_PREFIXES_BEFORE_KIND:
        c.execute("UPDATE messages SET kind=? WHERE role='user' AND kind IS NULL AND substr(content, 1, ?) = ?", (kind, len(prefix), prefix))


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
    (15, "memories_expires_at", _memories_expires_at),
    (16, "memories_fts_live", _memories_fts_live),
    (17, "memory_provenance_backfill", _memory_provenance_backfill),
    (18, "graph_canonical_types", _graph_canonical_types),
    (19, "drop_legacy_texting_keys", _drop_legacy_texting_keys),
    (20, "drop_budget_settings", _drop_budget_settings),
    (21, "autonomous_by_default", _autonomous_by_default),
    (22, "drop_meetings_activity", _drop_meetings_activity),
    (23, "messages_kind", _messages_kind),
    (24, "drop_nav_placement", _drop_nav_placement),
    (25, "allow_all_connections_for_existing", _allow_all_connections_for_existing),
    (26, "internal_message_kinds", _internal_message_kinds),
    (27, "drop_memory_hidden_view", _drop_memory_hidden_view),
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
