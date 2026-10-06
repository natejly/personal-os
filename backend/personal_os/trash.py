"""Trash: soft delete for the things a person wrote or chose to keep.

Deleting a chat, project, doc, uploaded document, memory or todo sets `deleted_at` instead of erasing the
row. Every list/search/get on those tables filters it out (repos.py, docs.py, todos.py), so a trashed item is
invisible everywhere (chat context, memory injection, doc search, sidebar) until it is restored, and gone
for good once it is purged: by hand, or automatically after RETENTION_DAYS.

Deleting a project takes its chats, memories and uploaded documents with it. They are stamped
`deleted_with = <project id>` and are not listed on their own: restoring the project brings back exactly
those rows, and rows deleted separately before stay in the trash. Its docs and todos are not trashed but
demoted to personal, which is what deleting a project has always done to them (the writing outlives the
project); restoring the project does not move them back.

Todos keep their Google behaviour: trashing one tombstones its Google task and calendar event exactly as a
hard delete does (todos.Todos.trash).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .db import Database, now
from .docs import Docs, drop_doc_windows
from .migrations import sync_memories_fts
from .todos import Todos
from .workspace import Workspace, WorkspaceError

log = logging.getLogger("personal_os")

RETENTION_DAYS = 30
DAY = 86400.0

# type -> (table, column shown as the item's title)
TYPES: dict[str, tuple[str, str]] = {
    "project": ("projects", "name"),
    "conversation": ("conversations", "title"),
    "doc": ("docs", "title"),
    "document": ("documents", "name"),
    "memory": ("memories", "content"),
    "todo": ("todos", "title"),
}
GROUPS = {"project": "projects", "conversation": "conversations", "doc": "docs", "document": "documents",
          "memory": "memories", "todo": "todos"}
CHILD_TABLES = ("conversations", "memories", "documents")  # docs and todos are demoted instead


class Trash:
    def __init__(self, db: Database, todos: Todos, docs: Docs):
        self.db = db
        self.todos = todos
        self.docs = docs

    @staticmethod
    def _check(kind: str) -> tuple[str, str]:
        if kind not in TYPES:
            raise ValueError(f"unknown trash type '{kind}'")
        return TYPES[kind]

    # ---- trash ----
    def trash(self, kind: str, id: str) -> bool:
        table, _ = self._check(kind)
        if kind == "todo":
            return self.todos.trash(id)
        t = now()
        with self.db.tx() as c:
            hit = c.execute(f"UPDATE {table} SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (t, id)).rowcount
            if kind == "doc" and hit:
                drop_doc_windows(c, [id])
            if kind == "memory" and hit:
                sync_memories_fts(c, [id])  # a trashed memory leaves search until it is restored
            if kind != "project" or not hit:
                return bool(hit)
            for ct in CHILD_TABLES:
                c.execute(f"UPDATE {ct} SET deleted_at=?, deleted_with=? WHERE project_id=? AND deleted_at IS NULL", (t, id, id))
            sync_memories_fts(c, [r["id"] for r in c.execute("SELECT id FROM memories WHERE deleted_with=?", (id,)).fetchall()])
            self._demote(c, id)
            c.execute("DELETE FROM doc_folders WHERE scope=?", (id,))  # the tree is gone; the docs' own folder paths are not
        return True

    @staticmethod
    def _demote(c: Any, project_id: str) -> None:
        """What the FK's ON DELETE SET NULL used to do when a project row was erased: docs, todos, notes,
        boards, canvases, presets, jobs... drop to personal. The row now stays, so do it by hand,
        for every table that declares it rather than a list that goes stale."""
        tables = [r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        for t in tables:
            for fk in c.execute(f'PRAGMA foreign_key_list("{t}")').fetchall():
                if fk["table"] == "projects" and (fk["on_delete"] or "").upper() == "SET NULL":
                    c.execute(f'UPDATE "{t}" SET "{fk["from"]}"=NULL WHERE "{fk["from"]}"=?', (project_id,))

    # ---- restore ----
    def restore(self, kind: str, id: str) -> dict[str, Any] | None:
        """Bring an item back. One whose project is itself still in the trash comes back in Personal
        instead, since restoring it into a project nobody can see would lose it a second time."""
        table, _ = self._check(kind)
        with self.db.tx() as c:
            row = c.execute(f"SELECT * FROM {table} WHERE id=? AND deleted_at IS NOT NULL", (id,)).fetchone()
            if not row:
                return None
            moved = False
            if kind != "project" and row["project_id"]:
                proj = c.execute("SELECT deleted_at FROM projects WHERE id=?", (row["project_id"],)).fetchone()
                if not proj or proj["deleted_at"] is not None:
                    c.execute(f"UPDATE {table} SET project_id=NULL WHERE id=?", (id,))
                    moved = True
            if kind == "project":
                c.execute("UPDATE projects SET deleted_at=NULL WHERE id=?", (id,))
                mem_ids = [r["id"] for r in c.execute("SELECT id FROM memories WHERE deleted_with=?", (id,)).fetchall()]  # before deleted_with is cleared
                for ct in CHILD_TABLES:
                    c.execute(f"UPDATE {ct} SET deleted_at=NULL, deleted_with=NULL WHERE deleted_with=?", (id,))
                sync_memories_fts(c, mem_ids)
            elif kind != "todo":
                c.execute(f"UPDATE {table} SET deleted_at=NULL, deleted_with=NULL WHERE id=?", (id,))
                if kind == "memory":
                    sync_memories_fts(c, [id])
        if kind == "todo":
            self.todos.restore(id)
        return {"ok": True, "type": kind, "id": id, "moved_to_personal": moved}

    # ---- permanent delete ----
    def purge(self, kind: str, id: str) -> bool:
        table, _ = self._check(kind)
        with self.db.tx() as c:
            if not c.execute(f"SELECT 1 FROM {table} WHERE id=? AND deleted_at IS NOT NULL", (id,)).fetchone():
                return False
        if kind == "project":
            self._purge_project(id)
        elif kind == "todo":
            self.todos.delete(id, notify=False, tombstone=False)  # tombstoned when it was trashed
        elif kind == "doc":
            self.docs.delete(id)
        elif kind == "memory":
            with self.db.tx() as c:
                c.execute("DELETE FROM memories WHERE id=?", (id,))
                c.execute("DELETE FROM memories_fts WHERE memory_id=?", (id,))
        elif kind == "document":
            self._purge_documents([id])
        else:
            with self.db.tx() as c:
                c.execute("DELETE FROM conversations WHERE id=?", (id,))
            self._purge_chat_files([id])
        return True

    def _purge_chat_files(self, ids: list[str]) -> None:
        """A chat's saved outputs (<data>/chats/<id>/) go when the chat itself is erased, never at trash time."""
        chats = Workspace(self.db.data_dir, sub="chats")
        for cid in ids:
            with contextlib.suppress(WorkspaceError):
                chats.purge(cid)

    def _purge_documents(self, ids: list[str]) -> None:
        paths: list[str] = []
        with self.db.tx() as c:
            for did in ids:
                r = c.execute("SELECT path FROM documents WHERE id=?", (did,)).fetchone()
                if r and r["path"]:
                    paths.append(r["path"])
                c.execute("DELETE FROM chunks_fts WHERE document_id=?", (did,))
                c.execute("DELETE FROM documents WHERE id=?", (did,))
        for p in paths:
            with contextlib.suppress(OSError):
                Path(p).unlink()

    def _purge_project(self, id: str) -> None:
        with self.db.tx() as c:
            for ct in CHILD_TABLES:  # trashed on their own earlier: they keep their own 30 days (and files, search entries), so step out first
                c.execute(f"UPDATE {ct} SET project_id=NULL WHERE project_id=? AND deleted_with IS NULL AND deleted_at IS NOT NULL", (id,))
            mem_ids = [r["id"] for r in c.execute("SELECT id FROM memories WHERE project_id=?", (id,)).fetchall()]
            doc_ids = [r["id"] for r in c.execute("SELECT id FROM documents WHERE project_id=?", (id,)).fetchall()]
            for mid in mem_ids:  # the project's FK cascade removes the rows but not their search entries
                c.execute("DELETE FROM memories_fts WHERE memory_id=?", (mid,))
        self._purge_documents(doc_ids)
        with self.db.tx() as c:
            chat_ids = [r["id"] for r in c.execute("SELECT id FROM conversations WHERE project_id=?", (id,)).fetchall()]
            c.execute("DELETE FROM projects WHERE id=?", (id,))
        self._purge_chat_files(chat_ids)

    # ---- reading the trash ----
    def list(self) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS.values()}
        with self.db.tx() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects").fetchall()}
            for kind, (table, col) in TYPES.items():
                extra = "" if kind == "project" else ", project_id"
                where = "deleted_at IS NOT NULL" + ("" if kind == "project" else " AND deleted_with IS NULL")
                for r in c.execute(f"SELECT id, {col} AS title, deleted_at{extra} FROM {table} WHERE {where} ORDER BY deleted_at DESC").fetchall():
                    pid = r["project_id"] if kind != "project" else None
                    item = {"type": kind, "id": r["id"], "title": (r["title"] or "").strip()[:160] or "Untitled",
                            "deleted_at": r["deleted_at"], "purge_at": r["deleted_at"] + RETENTION_DAYS * DAY,
                            "project_id": pid, "project_name": names.get(pid) if pid else None}
                    if kind == "project":
                        item["contents"] = {ct: c.execute(f"SELECT COUNT(*) FROM {ct} WHERE deleted_with=?", (r["id"],)).fetchone()[0]
                                            for ct in CHILD_TABLES}
                    groups[GROUPS[kind]].append(item)
        return {"groups": groups, "total": sum(len(v) for v in groups.values()), "retention_days": RETENTION_DAYS}

    def empty(self) -> int:
        n = 0
        for items in self.list()["groups"].values():
            for it in items:
                n += self.purge(it["type"], it["id"])
        return n

    def purge_old(self, days: float = RETENTION_DAYS) -> int:
        """Erase what has sat in the trash longer than `days`. Projects first: purging one takes its
        stamped children, so they are not counted or visited twice."""
        cutoff = now() - days * DAY
        n = 0
        for kind in ("project", "conversation", "doc", "document", "memory", "todo"):
            table, _ = TYPES[kind]
            with self.db.tx() as c:
                ids = [r["id"] for r in c.execute(
                    f"SELECT id FROM {table} WHERE deleted_at IS NOT NULL AND deleted_at < ?"
                    + ("" if kind == "project" else " AND deleted_with IS NULL"), (cutoff,)).fetchall()]
            for i in ids:
                n += self.purge(kind, i)
        return n

    async def loop(self) -> None:
        """Purge at startup, then once a day, and sweep old MCP media with it. A failed pass is logged and retried tomorrow."""
        while True:
            try:
                n = await asyncio.to_thread(self.purge_old)
                if n:
                    log.info("trash: purged %d item(s) older than %d days", n, RETENTION_DAYS)
            except Exception:  # noqa: BLE001 - housekeeping must never take the backend down
                log.warning("trash purge failed", exc_info=True)
            try:  # pictures and files MCP tools returned (mcp_client._save_media) are kept a week
                from .mcp_client import sweep_media
                await asyncio.to_thread(sweep_media)
            except Exception:  # noqa: BLE001
                log.warning("mcp media sweep failed", exc_info=True)
            await asyncio.sleep(DAY)


def router(trash: Trash) -> APIRouter:
    r = APIRouter()

    def _kind(kind: str) -> str:
        if kind not in TYPES:
            raise HTTPException(404, f"unknown trash type '{kind}'")
        return kind

    @r.get("/trash")
    def list_trash() -> dict[str, Any]:
        return trash.list()

    @r.post("/trash/{kind}/{id}/restore")
    def restore_item(kind: str, id: str) -> dict[str, Any]:
        out = trash.restore(_kind(kind), id)
        if not out:
            raise HTTPException(404, "Not in the trash")
        return out

    @r.delete("/trash/{kind}/{id}")
    def purge_item(kind: str, id: str) -> dict[str, bool]:
        if not trash.purge(_kind(kind), id):
            raise HTTPException(404, "Not in the trash")
        return {"ok": True}

    @r.delete("/trash")
    def empty_trash() -> dict[str, Any]:
        return {"ok": True, "purged": trash.empty()}

    return r
