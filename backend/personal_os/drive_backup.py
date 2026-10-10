"""Back up the user's files (uploads and editor docs, nothing else) to a "Grain Backup" folder in Google Drive.

Incremental: <data dir>/drive-backup.json remembers, per file, the signature last uploaded and its Drive id, so an
unchanged file is skipped and an edited doc updates its Drive copy in place. Every run ends with a summary
(copied / skipped / failed with reasons) kept in that file; a run that could not start says why instead of
looking clean. Only the drive.file scope is needed: the app sees just the folder and files it created.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from . import blobs
from .db import Database
from .google import Google, GoogleNotConnected

log = logging.getLogger("personal_os.drive_backup")
FOLDER = "Grain Backup"
EVERY_S = 24 * 3600
TICK_S = 3600


class DriveBackup:
    def __init__(self, db: Database, google: Google):
        self.db, self.google = db, google
        self.path = Path(db.data_dir) / "drive-backup.json"
        self.state: dict[str, Any] = {"folder_id": None, "files": {}, "last": None}
        try:
            self.state.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.path)

    def status(self) -> dict[str, Any]:
        return {"last": self.state.get("last"), "tracked": len(self.state["files"])}

    def _items(self) -> list[dict[str, Any]]:
        """What would be backed up: key, Drive name, signature, and either a local path or text bytes."""
        out: list[dict[str, Any]] = []
        with self.db.tx() as c:
            docs = c.execute("SELECT id, name, path FROM documents WHERE deleted_at IS NULL AND path != ''").fetchall()
            notes = c.execute("SELECT id, title, content FROM docs").fetchall()
        for r in docs:
            p = blobs.inside_uploads(self.db.data_dir, r["path"])
            if not p:
                continue
            # uploads/<sha256>/<name>: the folder name is already the content hash.
            st = p.stat()
            sig = p.parent.name if len(p.parent.name) == 64 else f"{st.st_size}:{st.st_mtime_ns}"
            out.append({"key": f"upload:{sig}:{r['name']}", "name": r["name"], "sig": sig, "file": p})
        for r in notes:
            data = (r["content"] or "").encode()
            title = (r["title"] or "Untitled").replace("/", "-")
            out.append({"key": f"doc:{r['id']}", "name": f"{title}.md", "sig": hashlib.sha256(data + title.encode()).hexdigest(), "data": data})
        return out

    def run(self) -> dict[str, Any]:
        res: dict[str, Any] = {"at": time.time(), "copied": 0, "skipped": 0, "failed": [], "error": None}
        st = self.google.status()
        try:
            if not st["connected"]:
                raise GoogleNotConnected("Google is not connected, so nothing was backed up. Sign in under Settings → Integrations.")
            if st["needs_reauth"] or any(m.endswith("drive.file") for m in st["missing_scopes"]):
                raise GoogleNotConnected("Google Drive access was not granted. Reconnect Google in Settings → Integrations and allow it.")
            files: dict[str, Any] = self.state["files"]
            folder = self.state.get("folder_id")
            if folder and not self.google.drive_backup_folder_alive(folder):
                # Folder deleted or trashed in Drive: its files went with it, so start over this run.
                folder = self.state["folder_id"] = None
                files.clear()
            if not folder:
                folder = self.state["folder_id"] = self.google.drive_backup_folder(FOLDER)
            for it in self._items():
                prev = files.get(it["key"])
                if prev and prev["sig"] == it["sig"]:
                    res["skipped"] += 1
                    continue
                try:
                    did = self.google.drive_backup_put(folder, it["name"], it.get("file"), it.get("data"), prev and prev["id"])
                    files[it["key"]] = {"sig": it["sig"], "id": did}
                    res["copied"] += 1
                except GoogleNotConnected:
                    raise
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest, and must be reported
                    res["failed"].append({"name": it["name"], "error": str(e)[:300]})
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)
        res["ok"] = not res["error"] and not res["failed"]
        self.state["last"] = res
        self._save()
        return res

    async def loop(self) -> None:
        while True:
            last = self.state.get("last") or {}
            if time.time() - (last.get("at") or 0) >= EVERY_S and self.google.status()["connected"]:
                try:
                    await asyncio.to_thread(self.run)
                except Exception as e:  # noqa: BLE001
                    log.warning("scheduled Drive backup failed: %s", e)
            await asyncio.sleep(TICK_S)
