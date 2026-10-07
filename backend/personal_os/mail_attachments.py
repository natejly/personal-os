"""Mail attachments: turn Uploads document ids into files to attach, and save a received attachment to disk."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import blobs
from .db import new_id
from .extract_text import safe_upload_name

# Gmail's 25 MB limit is on the files. Base64 adds a third, and Gmail's raw-message cap of 35 MB
# still fits, so the check is on the raw file sizes.
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_COUNT = 20
_CHUNK = 1 << 20


class AttachmentError(ValueError):
    """An attachment cannot be sent; the message is fit to show the user."""


def check_total(items: list[dict[str, Any]]) -> None:
    if len(items) > MAX_COUNT:
        raise AttachmentError(f"At most {MAX_COUNT} attachments per email.")
    total = sum(os.path.getsize(i["path"]) for i in items)
    if total > MAX_TOTAL_BYTES:
        raise AttachmentError(f"Attachments total {total / (1024 * 1024):.1f} MB; Gmail allows 25 MB.")


def resolve(documents: Any, data_dir: Path, ids: list[str]) -> list[dict[str, Any]]:
    """Uploads document ids -> {id, name, mime, size, path}; AttachmentError when one cannot be attached."""
    if len(ids) > MAX_COUNT:
        raise AttachmentError(f"At most {MAX_COUNT} attachments per email.")
    items = []
    for did in ids:
        d = documents.get(did)
        if not d:
            raise AttachmentError(f"Attachment {did!r} is not an uploaded file.")
        p = blobs.inside_uploads(data_dir, d.get("path"))
        if p is None:
            raise AttachmentError(f"The original of {d.get('name') or did!r} is no longer stored, so it cannot be attached.")
        items.append({"id": did, "name": d["name"], "mime": d.get("mime") or "application/octet-stream",
                      "size": p.stat().st_size, "path": str(p)})
    check_total(items)
    return items


def downloads_dir() -> Path:
    return Path(os.environ.get("PERSONAL_OS_DOWNLOADS_DIR") or Path.home() / "Downloads")


def save_to_folder(data: bytes, name: str, folder: Path) -> Path:
    """Write `data` as a file in `folder` under a sanitised name, never over an existing file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    safe = safe_upload_name(name)
    stem, suffix = Path(safe).stem, Path(safe).suffix
    tmp = folder / f".grain-tmp-{new_id()}"
    try:
        with tmp.open("wb") as f:
            for i in range(0, len(data), _CHUNK):
                f.write(data[i:i + _CHUNK])
        dest, n = folder / safe, 1
        # ponytail: check-then-replace can race another save of the same name; link() would be atomic if it ever matters.
        while dest.exists():
            n += 1
            dest = folder / f"{stem} ({n}){suffix}"
        os.replace(tmp, dest)
        return dest
    finally:
        tmp.unlink(missing_ok=True)
