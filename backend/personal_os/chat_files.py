"""Which chat each file belongs to: attached uploads, notes the agent made or edited, files a chat's tools saved,
local files the agent wrote, and coding worktrees. One row per file per chat (the first action wins).

Three sources are recorded by SQLite triggers (messages.attachments, file_snapshots finalize, coding_sessions), so
the repos that own those tables need no hook; notes, Workspace saves and accepted review cards call `record*`.
Every write is INSERT OR IGNORE and never raises: a missing index row must not break the write it describes.
There is no foreign key: rows of a purged chat are hidden by joining conversations at read time, and an FK could
abort the insert that fired a trigger.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import mac
from .db import Database, new_id

log = logging.getLogger(__name__)

# The attachments JSON list [{id,name,mime,size}]; a non-object element or invalid JSON yields no rows, never an error.
_ATT = "json_each(CASE WHEN json_valid({m}.attachments) THEN {m}.attachments ELSE '[]' END) AS j"
_ATT_ID = "CASE WHEN j.type='object' THEN json_extract(j.value,'$.id') END"
_ATT_NAME = "CASE WHEN j.type='object' THEN json_extract(j.value,'$.name') END"
_LOCAL_OPS = "('create','overwrite','append','move')"
_BASENAME = "replace({p}, rtrim({p}, replace({p}, '/', '')), '')"  # SQLite has no basename()


def _upload_select(m: str) -> str:
    return (f"SELECT lower(hex(randomblob(16))), {m}.conversation_id, 'upload', {_ATT_ID}, coalesce({_ATT_NAME}, {_ATT_ID}), "
            f"'attached', {m}.id, {m}.created_at FROM {_ATT.format(m=m)} WHERE {_ATT_ID} IS NOT NULL")


_INSERT = "INSERT OR IGNORE INTO chat_files(id, conversation_id, kind, ref, name, action, message_id, created_at) "


def _has(c: sqlite3.Connection, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def migrate(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS chat_files (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      kind TEXT NOT NULL,       -- upload | note | output | local | coding
      ref TEXT NOT NULL,        -- documents.id, docs.id, or an absolute path
      name TEXT NOT NULL,
      action TEXT NOT NULL,     -- attached | created | edited | saved
      message_id TEXT,
      created_at REAL NOT NULL)""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_files_ref ON chat_files(conversation_id, kind, ref)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_files_conv ON chat_files(conversation_id, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_files_time ON chat_files(created_at)")
    upload = _INSERT + _upload_select("new")
    # A source table can be absent in a bare test database; the real schema always has all three by v14.
    for name, ev in (("chat_files_att_ins", "AFTER INSERT"), ("chat_files_att_upd", "AFTER UPDATE OF attachments")):
        if _has(c, "messages"):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {ev} ON messages "
                      f"WHEN new.attachments IS NOT NULL AND json_valid(new.attachments) BEGIN {upload}; END")
    # Finalize runs only after a successful write (capture inserts first, discard deletes on failure), and a restore is not "the chat's file".
    if _has(c, "file_snapshots"):
        c.execute(f"""CREATE TRIGGER IF NOT EXISTS chat_files_local AFTER UPDATE OF after_digest ON file_snapshots
      WHEN new.conversation_id IS NOT NULL AND new.op IN {_LOCAL_OPS} BEGIN
      {_INSERT}VALUES(lower(hex(randomblob(16))), new.conversation_id, 'local', new.path, {_BASENAME.format(p='new.path')},
        CASE WHEN new.op='create' THEN 'created' ELSE 'edited' END, new.message_id, new.created_at); END""")
    if _has(c, "coding_sessions"):
        c.execute(f"""CREATE TRIGGER IF NOT EXISTS chat_files_coding AFTER INSERT ON coding_sessions
      WHEN new.conversation_id IS NOT NULL AND new.conversation_id NOT LIKE 'coding:%' BEGIN
      {_INSERT}VALUES(lower(hex(randomblob(16))), new.conversation_id, 'coding', new.worktree, new.name, 'created', NULL, new.created_at); END""")


class ChatFiles:
    def __init__(self, db: Database, chats_root: Path | None) -> None:
        self.db = db
        self.root = Path(chats_root) if chats_root is not None else None

    # ---- recording ----
    def record(self, conversation_id: str | None, kind: str, ref: str, name: str, action: str, message_id: str | None = None) -> None:
        if not conversation_id:
            return
        try:
            with self.db.tx() as c:
                c.execute(_INSERT + "VALUES(?,?,?,?,?,?,?,?)",
                          (new_id(), conversation_id, kind, ref, name, action, message_id, time.time()))
        except Exception as e:  # noqa: BLE001 - an index row never breaks the write it describes
            log.warning("chat file not recorded (%s): %s", kind, type(e).__name__)

    def record_output(self, owner: str, path: Path) -> None:
        """Workspace.on_save for the chat outbox: `owner` is the conversation id."""
        try:
            p = Path(path).resolve()
            self.record(owner, "output", str(p), p.name, "saved")
        except Exception as e:  # noqa: BLE001
            log.warning("chat file not recorded (output): %s", type(e).__name__)

    def record_accepted_revision(self, rev_id: str, doc: dict[str, Any]) -> None:
        """A doc_edit proposal accepted from the review card. The revision has no conversation column, so the chat is
        the newest message whose tool events name it."""
        try:
            with self.db.tx() as c:
                rev = c.execute("SELECT tool, author FROM doc_revisions WHERE id=?", (rev_id,)).fetchone()
                if not rev or rev["tool"] != "doc_edit" or rev["author"] != "assistant":
                    return
                # ponytail: full scan of messages.tool_events (rev ids are random hex, so LIKE is exact enough); add a
                # conversation_id column on doc_revisions if this gets slow.
                m = c.execute("SELECT conversation_id, id FROM messages WHERE tool_events LIKE '%'||?||'%' "
                              "ORDER BY created_at DESC LIMIT 1", (rev_id,)).fetchone()
            if m:
                self.record(m["conversation_id"], "note", doc["id"], doc["title"], "edited", m["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("chat file not recorded (note): %s", type(e).__name__)

    def backfill(self) -> int:
        """The triggers' work over rows that predate them. Idempotent and cheap to re-run; returns rows inserted."""
        with self.db.tx() as c:
            before = c.total_changes
            c.execute(_INSERT + _upload_select("m").replace("FROM json_each", "FROM messages m, json_each", 1))
            c.execute(f"""{_INSERT}SELECT lower(hex(randomblob(16))), conversation_id, 'local', path, {_BASENAME.format(p='path')},
              CASE WHEN op='create' THEN 'created' ELSE 'edited' END, message_id, created_at FROM file_snapshots
              WHERE conversation_id IS NOT NULL AND after_digest IS NOT NULL AND op IN {_LOCAL_OPS}""")
            if _has(c, "coding_sessions"):
                c.execute(f"""{_INSERT}SELECT lower(hex(randomblob(16))), conversation_id, 'coding', worktree, name, 'created', NULL, created_at
                  FROM coding_sessions WHERE conversation_id IS NOT NULL AND conversation_id NOT LIKE 'coding:%'""")
            for cid, p, mtime in self._output_files(c):
                c.execute(_INSERT + "VALUES(?,?,?,?,?,?,?,?)", (new_id(), cid, "output", str(p), p.name, "saved", None, mtime))
            return c.total_changes - before

    def _output_files(self, c: sqlite3.Connection) -> list[tuple[str, Path, float]]:
        """chats/<conversation_id>/outputs/** regular files of conversations that still exist."""
        if self.root is None or not self.root.is_dir():
            return []
        known = {r["id"] for r in c.execute("SELECT id FROM conversations").fetchall()}
        out: list[tuple[str, Path, float]] = []
        for d in self.root.iterdir():
            if d.name not in known or d.is_symlink() or not (d / "outputs").is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(d / "outputs"):
                dirnames[:] = [x for x in dirnames if not x.startswith(".") and not Path(dirpath, x).is_symlink()]
                for fn in filenames:
                    p = Path(dirpath, fn)
                    if fn.startswith(".") or p.is_symlink():
                        continue
                    try:
                        out.append((d.name, p.resolve(), p.stat().st_mtime))
                    except OSError:
                        continue
        return out

    # ---- reading ----
    def _from(self, c: sqlite3.Connection, scope: str) -> tuple[str, list[Any]]:
        """FROM/WHERE shared by list and counts, so counts and paging agree: purged or trashed chats, and uploads or
        notes that are gone or trashed, never show. The docs table is created by Docs.__init__, so it may be absent."""
        notes = "docs" if _has(c, "docs") else "(SELECT NULL AS id, NULL AS title, NULL AS deleted_at WHERE 0)"
        sql = ("FROM chat_files f JOIN conversations c ON c.id=f.conversation_id AND c.deleted_at IS NULL "
               "LEFT JOIN documents d ON f.kind='upload' AND d.id=f.ref "
               f"LEFT JOIN {notes} n ON f.kind='note' AND n.id=f.ref "
               "WHERE (f.kind<>'upload' OR (d.id IS NOT NULL AND d.deleted_at IS NULL)) "
               "AND (f.kind<>'note' OR (n.id IS NOT NULL AND n.deleted_at IS NULL))")
        return (sql + " AND c.project_id IS NULL") if scope == "personal" else sql, []

    def list(self, conversation_id: str | None = None, scope: str = "all", limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        with self.db.tx() as c:
            sql, args = self._from(c, scope)
            if conversation_id:
                sql, args = sql + " AND f.conversation_id=?", args + [conversation_id]
            if cursor:
                ts, _, fid = cursor.partition("|")
                sql, args = sql + " AND (f.created_at < ? OR (f.created_at = ? AND f.id < ?))", args + [float(ts), float(ts), fid]
            rows = c.execute("SELECT f.*, c.title AS conversation_title, c.project_id, d.name AS doc_name, n.title AS note_title "
                             f"{sql} ORDER BY f.created_at DESC, f.id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        more, rows = len(rows) > limit, rows[:limit]
        files = [f for r in rows if (f := self._shape(r)) is not None]
        nxt = f"{rows[-1]['created_at']!r}|{rows[-1]['id']}" if more else None
        return {"files": files, "next_cursor": nxt}

    def _shape(self, r: sqlite3.Row) -> dict[str, Any] | None:
        kind, ref = r["kind"], r["ref"]
        name = (r["doc_name"] if kind == "upload" else r["note_title"] if kind == "note" else None) or r["name"]
        missing, rel = False, None
        if kind in ("output", "local", "coding"):
            missing = not Path(ref).exists()
        if kind == "local":
            try:
                mac.allowed_path(ref)  # never expose a path the local-file tools would refuse
            except mac.LocalPathError:
                return None
        elif kind == "output":
            if self.root is None:
                return None
            try:
                rel = Path(ref).relative_to((self.root / r["conversation_id"]).resolve()).as_posix()
            except ValueError:
                return None
        return {"id": r["id"], "conversation_id": r["conversation_id"], "conversation_title": r["conversation_title"],
                "project_id": r["project_id"], "kind": kind, "ref": ref, "name": name, "action": r["action"],
                "message_id": r["message_id"], "created_at": r["created_at"], "missing": missing, "rel": rel}

    def counts(self, scope: str = "all") -> dict[str, int]:
        with self.db.tx() as c:
            sql, args = self._from(c, scope)
            rows = c.execute(f"SELECT f.conversation_id AS cid, COUNT(*) AS n {sql} GROUP BY f.conversation_id", args).fetchall()
        return {r["cid"]: r["n"] for r in rows}


def router(cf: ChatFiles) -> Any:
    from fastapi import APIRouter, HTTPException

    r = APIRouter(tags=["chat-files"])

    def page(**kw: Any) -> dict[str, Any]:
        try:
            return cf.list(**kw)
        except ValueError:
            raise HTTPException(400, "Bad cursor") from None

    @r.get("/conversations/{id}/files")
    def conversation_files(id: str, limit: int = 200) -> dict[str, Any]:
        with cf.db.tx() as c:
            if not c.execute("SELECT 1 FROM conversations WHERE id=? AND deleted_at IS NULL", (id,)).fetchone():
                raise HTTPException(404, "No such conversation")
        return page(conversation_id=id, limit=limit)

    @r.get("/chat-files")
    def all_files(scope: str = "all", limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if scope not in ("personal", "all"):
            raise HTTPException(400, "scope must be personal or all")
        return page(scope=scope, limit=limit, cursor=cursor)

    @r.get("/chat-files/counts")
    def file_counts(scope: str = "all") -> dict[str, Any]:
        if scope not in ("personal", "all"):
            raise HTTPException(400, "scope must be personal or all")
        return {"counts": cf.counts(scope)}

    return r
