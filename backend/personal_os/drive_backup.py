"""Back up the user's files (uploads and editor docs, nothing else) to a per-computer folder in Google Drive.

The folder is "Grain Backup (<label>)", so two Grain installs on one Google account each get their own instead of
overwriting one another's copies. The label is the `driveBackupLabel` setting, read fresh each run; blank means this
computer's hostname. A folder already recorded in the state whose Drive name matches is kept as is. When the wanted
name differs (the first run after this per-machine change, or after the label is edited) the run re-uploads every
file into the folder of the new name, and the old folder's files are left in Drive: there is no delete path.
Switching back to a label whose folder already exists (or sharing a label with another install) re-uploads into that
folder as new copies, because the state tracks only one folder; older copies there are not reused.

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
import platform
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import blobs
from .db import Database
from .extensions.google.client import Google, GoogleNotConnected

log = logging.getLogger("personal_os.drive_backup")
FOLDER = "Grain Backup"
FALLBACK_LABEL = "this computer"
LABEL_MAX = 60
EVERY_S = 24 * 3600
TICK_S = 3600


def device_label(raw: str | None) -> str:
    """The name of this computer for the folder: the setting, else the hostname, made safe for a Drive name."""
    label = raw if raw and raw.strip() else (platform.node() or FALLBACK_LABEL)
    label = label.replace("/", "-").replace("\\", "-")
    label = re.sub(r"\s+", " ", label)  # tabs and newlines become a space before the other control characters go
    label = "".join(ch for ch in label if not unicodedata.category(ch).startswith("C"))
    label = label.strip()[:LABEL_MAX].strip()
    return label or FALLBACK_LABEL


class DriveBackup:
    def __init__(self, db: Database, google: Google):
        self.db, self.google = db, google
        self.path = Path(db.data_dir) / "drive-backup.json"
        self._lock = threading.Lock()
        self.state: dict[str, Any] = {"folder_id": None, "files": {}, "last": None}
        try:
            self.state.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.path)

    def _label(self) -> str:
        return device_label(self.db.get_settings().get("driveBackupLabel"))

    def status(self) -> dict[str, Any]:
        label = self._label()
        return {"last": self.state.get("last"), "tracked": len(self.state["files"]),
                "folder": f"{FOLDER} ({label})", "label": label, "folder_id": self.state.get("folder_id")}

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
        seen: dict[str, int] = {}
        for r in notes:
            data = (r["content"] or "").encode()
            title = (r["title"] or "Untitled").replace("/", "-")
            seen[title] = seen.get(title, 0) + 1
            # Two docs with one title would be indistinguishable in Drive; tag the later ones.
            fname = f"{title}.md" if seen[title] == 1 else f"{title} ({str(r['id'])[:6]}).md"
            out.append({"key": f"doc:{r['id']}", "name": fname, "sig": hashlib.sha256(data + title.encode()).hexdigest(), "data": data})
        return out

    def run(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"at": time.time(), "copied": 0, "skipped": 0, "failed": [], "ok": False,
                    "error": "A backup is already running; try again when it finishes.", "busy": True}
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self) -> dict[str, Any]:
        res: dict[str, Any] = {"at": time.time(), "copied": 0, "skipped": 0, "failed": [], "error": None}
        st = self.google.status()
        try:
            if not st["connected"]:
                raise GoogleNotConnected("Google is not connected, so nothing was backed up. Sign in under Settings → Integrations.")
            if any(m.endswith("drive.file") for m in st["missing_scopes"]):
                raise GoogleNotConnected("Google Drive access was not granted. Reconnect Google in Settings → Integrations and allow it.")
            if st["needs_reauth"]:
                raise GoogleNotConnected(f"{st['reauth_reason']} Reconnect Google in Settings → Integrations.")
            files: dict[str, Any] = self.state["files"]
            label = self._label()
            wanted = f"{FOLDER} ({label})"
            props = {"grainDevice": label}
            folder = self.state.get("folder_id")
            if folder:
                current = self.google.drive_backup_folder_name(folder)
                if current is None:
                    # Folder deleted or trashed in Drive: its files went with it, so start over this run.
                    folder = self.state["folder_id"] = None
                    files.clear()
                elif current != wanted:
                    log.info("Drive backup: re-pointing from folder %r to %r (device label %r); the old folder's files are left in place",
                             current, wanted, label)
                    folder = self.state["folder_id"] = None
                    files.clear()
            if not folder:
                folder = self.state["folder_id"] = self.google.drive_backup_folder(wanted, props)
            self.state["folder_name"], self.state["label"] = wanted, label
            for it in self._items():
                prev = files.get(it["key"])
                if prev and prev["sig"] == it["sig"]:
                    res["skipped"] += 1
                    continue
                try:
                    did = self.google.drive_backup_put(folder, it["name"], it.get("file"), it.get("data"), prev and prev["id"], props=props)
                    files[it["key"]] = {"sig": it["sig"], "id": did}
                    res["copied"] += 1
                    self._save()  # an interrupted run must not forget what it uploaded, or the next one duplicates it
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
