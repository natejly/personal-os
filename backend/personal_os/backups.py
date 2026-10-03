"""Backups, restore and export of the one SQLite file (plus uploads/).

A backup is `VACUUM INTO` a timestamped file under <data dir>/backups/, with a `.json` manifest
beside it (app version, schema version, created_at, kind). VACUUM INTO gives a consistent snapshot
while the app is running; it is written to a `.tmp` name and renamed so a half-written file is never
listed.

Kinds and rotation (applied after every backup, per kind):
  daily       taken at startup and then by a timer once nothing newer than 24h exists;
              keep the newest 7, plus the newest one of each of the 4 most recent ISO weeks
  manual      "Back up now": keep the newest 10
  premigrate  before a pending schema migration; prerestore: the live DB set aside by a restore.
              keep the newest 3 of each

Restore never hot-swaps the live database. stage_restore() records the choice in backups/restore.pending;
apply_pending_restore() runs at startup BEFORE the database is opened, sets the current file aside as
a prerestore backup and swaps the chosen one in.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import migrations

log = logging.getLogger("personal_os")

APP_VERSION = "0.1.0"
DB_NAME = "personal-os.db"
PENDING = "restore.pending"
KINDS = ("daily", "manual", "premigrate", "prerestore")
DAILY_KEEP, WEEKLY_KEEP, MANUAL_KEEP, SAFETY_KEEP = 7, 4, 10, 3
DAY = 24 * 3600.0
_NAME = re.compile(r"^grain-\d{8}-\d{6}(-\d+)?-(daily|manual|premigrate|prerestore)\.db$")


def backup_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _vacuum_into(src: Path, dest: Path) -> None:
    c = sqlite3.connect(src)
    try:
        c.execute("VACUUM INTO ?", (str(dest),))
    finally:
        c.close()


def _schema_version(path: Path) -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(c.execute("PRAGMA user_version").fetchone()[0])
    finally:
        c.close()


def create(data_dir: Path, kind: str = "manual", now: float | None = None) -> dict[str, Any]:
    """Snapshot the live database; returns its manifest (with `name`)."""
    assert kind in KINDS
    data_dir = Path(data_dir)
    src = data_dir / DB_NAME
    if not src.exists():
        raise FileNotFoundError("no database to back up")
    ts = time.time() if now is None else now
    d = backup_dir(data_dir)
    stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")
    name, n = f"grain-{stamp}-{kind}.db", 1
    while (d / name).exists():  # two backups inside one second
        n += 1
        name = f"grain-{stamp}-{n}-{kind}.db"
    tmp = d / (name + ".tmp")
    tmp.unlink(missing_ok=True)
    _vacuum_into(src, tmp)
    manifest = {"app_version": APP_VERSION, "schema_version": _schema_version(tmp), "created_at": ts,
                "kind": kind, "size": tmp.stat().st_size}
    os.replace(tmp, d / name)
    (d / (name[:-3] + ".json")).write_text(json.dumps(manifest))
    rotate(data_dir)
    return {**manifest, "name": name}


def list_backups(data_dir: Path) -> list[dict[str, Any]]:
    """Newest first. A backup whose manifest is missing or unreadable is still listed from the file."""
    d = backup_dir(data_dir)
    out: list[dict[str, Any]] = []
    for f in d.glob("grain-*.db"):
        m = _NAME.match(f.name)
        if not m:
            continue
        try:
            meta = json.loads(f.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {"kind": m.group(2), "created_at": f.stat().st_mtime, "app_version": None, "schema_version": None}
        out.append({**meta, "name": f.name, "size": f.stat().st_size})
    return sorted(out, key=lambda b: (b["created_at"], b["name"]), reverse=True)


def delete(data_dir: Path, name: str) -> None:
    d = backup_dir(data_dir)
    (d / name).unlink(missing_ok=True)
    (d / (name[:-3] + ".json")).unlink(missing_ok=True)


def rotate(data_dir: Path) -> list[str]:
    """Apply the retention policy in the module docstring; returns the names removed."""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for b in list_backups(data_dir):
        by_kind.setdefault(b.get("kind", "manual"), []).append(b)
    drop: list[str] = []
    daily = by_kind.get("daily", [])
    keep = {b["name"] for b in daily[:DAILY_KEEP]}
    weeks: dict[tuple[int, int], str] = {}
    for b in daily:  # newest first, so the first seen per week is that week's newest
        iso = datetime.fromtimestamp(b["created_at"]).isocalendar()
        weeks.setdefault((iso[0], iso[1]), b["name"])
    keep.update(n for _, n in sorted(weeks.items(), reverse=True)[:WEEKLY_KEEP])
    drop += [b["name"] for b in daily if b["name"] not in keep]
    drop += [b["name"] for b in by_kind.get("manual", [])[MANUAL_KEEP:]]
    for kind in ("premigrate", "prerestore"):
        drop += [b["name"] for b in by_kind.get(kind, [])[SAFETY_KEEP:]]
    for n in drop:
        delete(data_dir, n)
    return drop


def due(data_dir: Path, now: float | None = None) -> bool:
    """True when no daily or manual backup is newer than 24h."""
    ts = time.time() if now is None else now
    return not any(b["kind"] in ("daily", "manual") and ts - b["created_at"] < DAY for b in list_backups(data_dir))


# ---- restore ----
def _check(path: Path) -> None:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("backup fails its integrity check")
    finally:
        c.close()


def stage_restore(data_dir: Path, name: str) -> dict[str, Any]:
    """Record `name` to be swapped in at the next start. Raises ValueError/FileNotFoundError."""
    d = backup_dir(data_dir)
    if not _NAME.match(name) or not (d / name).is_file():
        raise FileNotFoundError("no such backup")
    _check(d / name)
    if _schema_version(d / name) > migrations.latest():
        raise ValueError("this backup was made by a newer version of the app")
    (d / PENDING).write_text(json.dumps({"name": name, "staged_at": time.time()}))
    return {"name": name}


def pending_restore(data_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((backup_dir(data_dir) / PENDING).read_text())
    except (OSError, ValueError):
        return None


def cancel_restore(data_dir: Path) -> None:
    (backup_dir(data_dir) / PENDING).unlink(missing_ok=True)


def apply_pending_restore(data_dir: Path) -> str | None:
    """Startup, before the database is opened. Returns the restored backup's name, or None.

    A staged backup that has since vanished or is corrupt is dropped with a log line: the app must
    still start on the current database.
    """
    data_dir = Path(data_dir)
    if not (data_dir / "backups" / PENDING).exists():
        return None
    p = pending_restore(data_dir)
    cancel_restore(data_dir)
    try:
        name = (p or {})["name"]
        src = data_dir / "backups" / name
        if not _NAME.match(name) or not src.is_file():
            raise FileNotFoundError(name)
        _check(src)
        live = data_dir / DB_NAME
        if live.exists():
            create(data_dir, "prerestore")
        tmp = data_dir / (DB_NAME + ".restoring")
        shutil.copyfile(src, tmp)
        for suffix in ("-wal", "-shm", "-journal"):
            (data_dir / (DB_NAME + suffix)).unlink(missing_ok=True)
        os.replace(tmp, live)
        log.warning("restored database from backup %s", name)
        return name
    except Exception as e:  # noqa: BLE001
        log.error("staged restore abandoned: %s", e)
        return None


# ---- export ----
_README = """Grain data export
=================
grain.db            A complete SQLite snapshot (open with any SQLite tool, or restore it in Grain).
                    It includes your settings, which can hold API keys: keep this file private.
uploads/            Files you added to the knowledge base, as stored.
doc_assets/         Images pasted into your documents, one folder per document.
export/             The same content as plain text:
  conversations.md / conversations.json
  memories.md / memories.json
  documents.md / documents.json   (extracted text of every document)
"""


def _rows(c: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(sql)]


def human_export(db_path: Path) -> dict[str, tuple[str, Any]]:
    """{base name: (markdown, json-able)} for conversations, memories and documents."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        projects = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects")}
        convs = _rows(c, "SELECT id, project_id, title, model, created_at, updated_at FROM conversations ORDER BY created_at")
        # Regenerated answers are kept as superseded rows; the export reads one answer per turn. A snapshot
        # taken before the migration has no such column.
        live = "WHERE superseded_at IS NULL " if any(r["name"] == "superseded_at" for r in c.execute("PRAGMA table_info(messages)")) else ""
        msgs = _rows(c, f"SELECT conversation_id, role, content, model, created_at FROM messages {live}ORDER BY created_at")
        mems = _rows(c, "SELECT id, project_id, content, kind, source, pinned, created_at FROM memories ORDER BY created_at")
        docs = _rows(c, "SELECT id, project_id, name, mime, size, text, created_at FROM documents ORDER BY created_at")
    finally:
        c.close()

    def when(t: float) -> str:
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")

    def scope(pid: str | None) -> str:
        return projects.get(pid, "") if pid else "(global)"

    by_conv: dict[str, list[dict[str, Any]]] = {}
    for m in msgs:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    md = ["# Conversations\n"]
    for cv in convs:
        cv["project"] = scope(cv["project_id"])
        cv["messages"] = by_conv.get(cv["id"], [])
        md.append(f"\n## {cv['title']}\n\n{cv['project']} · {cv['model']} · {when(cv['created_at'])}\n")
        for m in cv["messages"]:
            md.append(f"\n**{m['role']}** ({when(m['created_at'])})\n\n{m['content']}\n")
    mmd = ["# Memories\n\n"]
    for m in mems:
        m["project"] = scope(m["project_id"])
        mmd.append(f"- [{m['kind']}] {m['content']} ({m['project']}, {when(m['created_at'])})\n")
    dmd = ["# Documents\n"]
    for d in docs:
        d["project"] = scope(d["project_id"])
        dmd.append(f"\n## {d['name']}\n\n{d['project']} · {when(d['created_at'])}\n\n{d['text']}\n")
    return {"conversations": ("".join(md), convs), "memories": ("".join(mmd), mems), "documents": ("".join(dmd), docs)}


def export_zip(data_dir: Path, dest: Path) -> dict[str, Any]:
    """Write the export archive to `dest` (atomically). Returns {path, size}."""
    data_dir, dest = Path(data_dir), Path(dest)
    src = data_dir / DB_NAME
    with tempfile.TemporaryDirectory(prefix="grain-export-") as td:
        snap = Path(td) / "grain.db"
        _vacuum_into(src, snap)
        part = dest.with_name(dest.name + ".part")
        try:
            _write_zip(part, snap, data_dir)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)  # only still there when the write failed
    return {"path": str(dest), "size": dest.stat().st_size}


def _write_zip(part: Path, snap: Path, data_dir: Path) -> None:
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", _README)
        z.write(snap, "grain.db")
        for base, (md, js) in human_export(snap).items():
            z.writestr(f"export/{base}.md", md)
            z.writestr(f"export/{base}.json", json.dumps(js, indent=2, ensure_ascii=False))
        for sub in ("uploads", "doc_assets"):
            up = data_dir / sub
            for f in sorted(up.rglob("*")) if up.exists() else []:
                if f.is_file() and not f.is_symlink():
                    z.write(f, f"{sub}/{f.relative_to(up).as_posix()}")


# ---- scheduler + routes ----
class Backups:
    """The daily timer. Same shape as Outbox.loop: one task, cancelled at shutdown."""

    def __init__(self, data_dir: Path, tick: float = 3600.0):
        self.data_dir, self.tick = Path(data_dir), tick

    def run_due(self) -> dict[str, Any] | None:
        return create(self.data_dir, "daily") if due(self.data_dir) else None

    async def loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_due)
            except Exception as e:  # noqa: BLE001 - a failed backup must never take the app down
                log.warning("scheduled backup failed: %s", e)
            await asyncio.sleep(self.tick)


class ExportIn(BaseModel):
    dest: str


def router(data_dir: Path) -> Any:
    from fastapi import APIRouter, HTTPException

    r = APIRouter(prefix="/data", tags=["data"])

    @r.get("")
    def overview() -> dict[str, Any]:
        bs = list_backups(data_dir)
        return {"data_dir": str(data_dir), "backups": bs, "last_backup": bs[0]["created_at"] if bs else None,
                "pending_restore": pending_restore(data_dir), "schema_version": migrations.latest(), "app_version": APP_VERSION}

    @r.post("/backups")
    async def backup_now() -> dict[str, Any]:
        return await asyncio.to_thread(create, data_dir, "manual")

    @r.post("/backups/{name}/restore")
    def restore(name: str) -> dict[str, Any]:
        try:
            return {**stage_restore(data_dir, name), "restart_required": True}
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    @r.delete("/restore")
    def unstage() -> dict[str, Any]:
        cancel_restore(data_dir)
        return {"ok": True}

    @r.post("/export")
    async def export(body: ExportIn) -> dict[str, Any]:
        dest = Path(body.dest).expanduser()
        if not dest.is_absolute() or dest.suffix.lower() != ".zip" or not dest.parent.is_dir():
            raise HTTPException(400, "Choose an absolute .zip path in an existing folder")
        return await asyncio.to_thread(export_zip, data_dir, dest)

    return r
