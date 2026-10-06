"""Uploaded originals, stored once per distinct content: uploads/<sha256>/<original name>.

Rows may share a blob (the same bytes uploaded to two projects), so a file goes only when the last documents
row naming it, live or trashed, is gone."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .db import Database, new_id
from .extract_text import safe_upload_name

log = logging.getLogger("personal_os.blobs")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_PREFIX = re.compile(r"^[0-9a-f]{16}-")
_TMP = ".grain-tmp-"


def _held(d: Path) -> Path | None:
    return next((f for f in sorted(d.iterdir()) if f.is_file() and not f.name.startswith(_TMP)), None) if d.is_dir() else None


def _file_digest(p: Path) -> str:
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _put(tmp_writer: Any, d: Path, name: str) -> Path:
    """Write through a temp file in the target directory, then rename, so a crash never leaves a half blob under its name."""
    dest, tmp = d / name, d / f"{_TMP}{new_id()}"
    try:
        tmp_writer(tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def store(data_dir: Path, name: str, data: bytes) -> tuple[Path, str]:
    # ponytail: a release of the last row for these bytes racing this call can unlink the file just reused; add a lock if it ever bites.
    digest = hashlib.sha256(data).hexdigest()
    d = Path(data_dir) / "uploads" / digest
    d.mkdir(parents=True, exist_ok=True)
    return _held(d) or _put(lambda t: t.write_bytes(data), d, safe_upload_name(name)), digest


def release(db: Database, path: str | None) -> None:
    """Call after the row delete is committed."""
    if not path:
        return
    try:
        with db.tx() as c:
            if c.execute("SELECT 1 FROM documents WHERE path=? LIMIT 1", (path,)).fetchone():
                return
        p = Path(path)
        p.unlink(missing_ok=True)
        if _HEX64.match(p.parent.name) and p.parent.parent == db.data_dir / "uploads":
            p.parent.rmdir()  # only when empty
    except OSError:
        pass


def inside_uploads(data_dir: Path, path: str | None) -> Path | None:
    if not path:
        return None
    try:
        p = Path(path).resolve()
        return p if p.is_file() and p.is_relative_to((Path(data_dir) / "uploads").resolve()) else None
    except (OSError, RuntimeError):
        return None


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _migrate_row(db: Database, uploads: Path, did: str, old: Path) -> None:
    digest = _file_digest(old)
    d = uploads / digest
    held = _held(d)
    if held is not None and _file_digest(held) != digest:
        log.warning("blob %s does not match its folder name; leaving %s alone", held, old)
        return
    if held is None:
        d.mkdir(exist_ok=True)
        held = _put(lambda t: _link_or_copy(old, t), d, safe_upload_name(_LEGACY_PREFIX.sub("", old.name)))
    with db.tx() as c:
        c.execute("UPDATE documents SET path=?, content_hash=? WHERE id=?", (str(held), digest, did))
    release(db, str(old))  # unlinks the old name only when no row still points at it


def migrate(db: Database) -> int:
    """Move legacy uploads/<id>-<name> files into uploads/<sha256>/<name>. Idempotent: any interruption converges on the next run."""
    uploads = db.data_dir / "uploads"
    with db.tx() as c:
        rows = [(r["id"], r["path"]) for r in c.execute("SELECT id, path FROM documents WHERE path IS NOT NULL AND path != ''").fetchall()]
    moved = 0
    for did, path in rows:
        old = Path(path)
        if old.parent != uploads or not old.is_file():  # already migrated, elsewhere, or gone (has_original is false at read time)
            continue
        try:
            _migrate_row(db, uploads, did, old)
            moved += 1
        except Exception:  # noqa: BLE001 - one unreadable file must not stop the rest
            log.warning("could not migrate %s", old, exc_info=True)
    return moved
