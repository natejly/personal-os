"""Pre-image snapshots for local file writes, so an agent edit can be taken back.

write_local_file (overwrite / append / create) and move_local_file are ask-first but were irreversible:
mac.write_local truncates in place. Before the write, the old bytes are copied under data_dir/snapshots/
and a row is kept; the tool result carries `undo: {snapshot_id}`. Restoring is a user action (a route),
never a model tool, and refuses when the file changed since the agent wrote it unless forced. A restore
is itself snapshotted, so undo is undoable.

Blobs stay in the data dir and are never sent to the model (results carry ids only).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from . import mac
from .db import Database, new_id

log = logging.getLogger(__name__)


class RestoreIn(BaseModel):
    force: bool = False


class Conflict(Exception):
    """The file is not in the state the agent left it, so a restore would clobber someone's edit."""


class NotFound(Exception):
    pass


class Unavailable(Exception):
    """Already restored, expired, or there is nothing left to restore from."""


def _digest(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FileSnapshots:
    def __init__(self, db: Database, root: Path, settings_fn: Callable[[], dict[str, Any]]) -> None:
        self.db, self.root, self.settings = db, Path(root), settings_fn

    # ---- settings ----
    def _on(self) -> bool:
        return bool(self.settings().get("fileSnapshots", True))

    def _max(self) -> int:
        return int(self.settings().get("fileSnapshotMaxBytes", 5_000_000))

    # ---- rows ----
    def _insert(self, op: str, path: str, ctx: dict[str, Any] | None, *, from_path: str | None = None, before: Path | None = None,
                before_digest: str | None = None, existed: bool = False, after_digest: str | None = None,
                sid: str | None = None) -> str:
        sid = sid or new_id()
        ctx = ctx or {}
        with self.db.tx() as c:
            c.execute("INSERT INTO file_snapshots(snapshot_id, conversation_id, message_id, call_id, op, path, from_path, before_path, "
                      "before_digest, before_existed, after_digest, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'live',?)",
                      (sid, ctx.get("conversation_id"), ctx.get("message_id"), ctx.get("call_id"), op, path, from_path,
                       str(before) if before else None, before_digest, 1 if existed else 0, after_digest, time.time()))
        return sid

    def _row(self, sid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM file_snapshots WHERE snapshot_id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def _blob(self, sid: str, src: Path) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        dst = self.root / sid
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        return dst

    # ---- capture ----
    def capture(self, op: str, path: str, ctx: dict[str, Any] | None = None, to: str | None = None) -> dict[str, Any] | None:
        """Record what is about to be overwritten, appended to, created or moved. Raises mac.LocalPathError
        for a path the write itself would refuse. Never blocks the write for any other reason."""
        if not self._on():
            return None
        if op == "move":
            src = mac.allowed_path(path)
            dst = mac._writable_path(to or "")
            if dst.is_dir():
                dst = dst / src.name
            if not src.exists() or dst.exists() or dst == src:
                return None  # the move will refuse; nothing to undo
            return {"snapshot_id": self._insert("move", str(dst), ctx, from_path=str(src))}
        p = mac._writable_path(path)
        if p.is_dir():
            return None
        try:
            if not p.exists():
                if op != "create":
                    return None
                return {"snapshot_id": self._insert("create", str(p), ctx, existed=False)}
            if op == "create":
                return None  # refused by write_local
            if p.stat().st_size > self._max():
                return {"snapshot_id": None, "reason": "file larger than limit, no undo"}
            sid = new_id()
            blob = self._blob(sid, p)
            self._insert(op, str(p), ctx, before=blob, before_digest=_digest(blob), existed=True, sid=sid)
            return {"snapshot_id": sid}
        except OSError as e:
            log.warning("file snapshot failed for %s: %s", p, e)
            return {"snapshot_id": None, "reason": "could not snapshot the file, no undo"}

    def finalize(self, sid: str, path: str | None = None) -> None:
        row = self._row(sid)
        if not row:
            return
        p = Path(path or row["path"])
        with self.db.tx() as c:
            c.execute("UPDATE file_snapshots SET after_digest=? WHERE snapshot_id=?", (_digest(p), sid))

    def discard(self, sid: str) -> None:
        """The write failed, so there is nothing to undo: forget the row and its blob."""
        row = self._row(sid)
        if not row:
            return
        self._unlink(row.get("before_path"))
        with self.db.tx() as c:
            c.execute("DELETE FROM file_snapshots WHERE snapshot_id=?", (sid,))

    @staticmethod
    def _unlink(path: str | None) -> None:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ---- restore ----
    def restore(self, sid: str, force: bool = False) -> dict[str, Any]:
        row = self._row(sid)
        if not row:
            raise NotFound(sid)
        if row["status"] != "live":
            raise Unavailable(f"snapshot is {row['status']}")
        p = Path(row["path"])
        try:
            p = mac._writable_path(str(p))  # a row cannot point a restore outside the home folder
        except mac.LocalPathError as e:
            raise Unavailable(str(e)) from e
        if row["from_path"]:
            return self._restore_move(row, force)
        cur = _digest(p)
        if cur != row["after_digest"] and not force:
            raise Conflict("file changed since the agent wrote it" if cur else "the file is gone")
        if p.is_dir():
            raise Conflict(f"{p} is now a folder")
        # Snapshot the current state first, so this restore can itself be undone.
        undo_id: str | None = None
        if cur is not None:
            undo_id = new_id()
            blob = self._blob(undo_id, p)
            self._insert("restore", str(p), {"conversation_id": row["conversation_id"], "message_id": row["message_id"]},
                         before=blob, before_digest=cur, existed=True, sid=undo_id)
        if row["before_existed"]:
            blob_path = row["before_path"]
            if not blob_path or not Path(blob_path).is_file():
                if undo_id:
                    self.discard(undo_id)
                raise Unavailable("the saved copy is gone")
            self._write_atomic(p, Path(blob_path))
            after = _digest(p)
        else:
            if cur is not None:
                mac.trash_local(str(p))
            after = None
        if undo_id:
            with self.db.tx() as c:
                c.execute("UPDATE file_snapshots SET after_digest=? WHERE snapshot_id=?", (after, undo_id))
        elif row["before_existed"]:
            # the file had been trashed since; the restore recreated it, so undoing that is trashing it
            undo_id = self._insert("restore", str(p), {"conversation_id": row["conversation_id"], "message_id": row["message_id"]},
                                   existed=False, after_digest=after)
        self._done(sid)
        return {"ok": True, "path": str(p), "undo_snapshot_id": undo_id}

    def _restore_move(self, row: dict[str, Any], force: bool) -> dict[str, Any]:
        here, there = Path(row["path"]), Path(row["from_path"])  # undo moves `here` back to `there`
        if not here.exists():
            raise Conflict("the moved file is no longer where the agent put it")
        if there.exists():
            raise Conflict(f"{there} is occupied, so it cannot be moved back")
        mac.move_local(str(here), str(there))
        undo_id = self._insert("restore", str(there), {"conversation_id": row["conversation_id"], "message_id": row["message_id"]},
                               from_path=str(here))
        self._done(row["snapshot_id"])
        return {"ok": True, "path": str(there), "undo_snapshot_id": undo_id}

    def _done(self, sid: str) -> None:
        row = self._row(sid)
        with self.db.tx() as c:
            c.execute("UPDATE file_snapshots SET status='restored', restored_at=?, before_path=NULL WHERE snapshot_id=?", (time.time(), sid))
        if row:
            self._unlink(row.get("before_path"))

    @staticmethod
    def _write_atomic(dst: Path, blob: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        mode = (dst.stat().st_mode & 0o777) if dst.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".grain-restore-")
        try:
            with os.fdopen(fd, "wb") as out, open(blob, "rb") as src:
                shutil.copyfileobj(src, out)
            os.chmod(tmp, mode)
            os.replace(tmp, dst)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- housekeeping ----
    def list(self, conversation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM file_snapshots", []
        if conversation_id:
            sql += " WHERE conversation_id=?"
            params.append(conversation_id)
        with self.db.tx() as c:
            rows = c.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*params, max(1, min(int(limit), 500)))).fetchall()
        return [{k: v for k, v in dict(r).items() if k != "before_path"} for r in rows]

    def _expire(self, sid: str, blob: str | None) -> None:
        self._unlink(blob)
        with self.db.tx() as c:
            c.execute("UPDATE file_snapshots SET status='expired', before_path=NULL WHERE snapshot_id=? AND status='live'", (sid,))

    def prune(self) -> int:
        """Expire by age, then oldest-first until the blobs fit the byte budget. Returns rows expired."""
        s = self.settings()
        cutoff = time.time() - float(s.get("fileSnapshotRetainDays", 14)) * 86400
        budget = int(float(s.get("fileSnapshotBudgetMB", 200)) * 1_000_000)
        n = 0
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT snapshot_id, before_path, created_at FROM file_snapshots "
                                               "WHERE status='live' ORDER BY created_at").fetchall()]
        keep = []
        for r in rows:
            if r["created_at"] < cutoff:
                self._expire(r["snapshot_id"], r["before_path"])
                n += 1
            else:
                keep.append(r)
        sizes = {r["snapshot_id"]: (os.path.getsize(r["before_path"]) if r["before_path"] and os.path.isfile(r["before_path"]) else 0) for r in keep}
        total = sum(sizes.values())
        for r in keep:  # oldest first
            if total <= budget:
                break
            if sizes[r["snapshot_id"]]:
                self._expire(r["snapshot_id"], r["before_path"])
                total -= sizes[r["snapshot_id"]]
                n += 1
        return n


def router(fs: FileSnapshots) -> Any:
    """The user-only routes. There is deliberately no tool that restores or deletes a snapshot."""
    from fastapi import APIRouter, HTTPException

    r = APIRouter(prefix="/file-snapshots", tags=["file-snapshots"])

    @r.get("")
    def list_snapshots(conversation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return fs.list(conversation_id, limit)

    @r.post("/{sid}/restore")
    def restore(sid: str, body: RestoreIn | None = None) -> dict[str, Any]:
        try:
            return fs.restore(sid, bool(body and body.force))
        except NotFound:
            raise HTTPException(404, "No such snapshot") from None
        except Conflict as e:
            raise HTTPException(409, {"reason": str(e), "conflict": True}) from None
        except Unavailable as e:
            raise HTTPException(409, {"reason": str(e), "conflict": False}) from None
        except (mac.LocalPathError, OSError) as e:
            raise HTTPException(409, {"reason": str(e), "conflict": False}) from None

    return r
