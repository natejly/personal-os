"""send_files: hand the user files or pictures: a screenshot, a chart, a document the agent made.

Each file is an Uploads document (an id from screenshot / generate_image / an upload) or a path on this Mac, which is
copied into Uploads so the UI can serve it. The files ride on the reply message in the app. In a Telegram chat the
toolbox's `user_update` hook also delivers them to the phone at once, with the text, as a progress update.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

from . import blobs as blob_store, mac, vision
from .extract_text import safe_upload_name

MAX_FILES = 20
MAX_BYTES = 50 * 1024 * 1024


def register(tb: Any) -> None:
    """Register send_files on a Toolbox (group `utility`)."""
    from .tools import ToolSpec, _obj, tool_error

    def resolve(ctx: dict[str, Any], raw: str) -> Path:
        did = str(ctx.get("desk_id") or "")
        ws = getattr(tb, "workspace", None)
        if did and ws is not None and not (raw.startswith("/") or raw.startswith("~")):
            from .workspace import WorkspaceError
            try:
                return ws.resolve_in(did, raw)
            except WorkspaceError as e:
                raise mac.LocalPathError(str(e)) from e
        return vision.allowed_image_path(raw) if Path(raw).suffix.lower() in vision.IMAGE_EXT else mac.readable_path(raw)

    def document(ctx: dict[str, Any], raw: str) -> dict[str, Any]:
        """The attachment for one entry: {id, name, mime, size}. Raises ValueError with a message fit to show."""
        pid = ctx.get("project_id") if isinstance(ctx.get("project_id"), str) else None
        d = tb.documents.get(raw)
        if d:
            if d["project_id"] and d["project_id"] != pid:
                raise ValueError("that file belongs to another project")
            return {"id": d["id"], "name": d["name"], "mime": d["mime"], "size": d["size"]}
        try:
            p = resolve(ctx, raw)
            st = p.stat()
        except (mac.LocalPathError, OSError) as e:
            raise ValueError(str(e) if isinstance(e, mac.LocalPathError) else "no such document id or file") from e
        if not p.is_file():
            raise ValueError("not a file")
        if st.st_size > MAX_BYTES:
            raise ValueError(f"{st.st_size // (1024 * 1024)} MB is over the {MAX_BYTES // (1024 * 1024)} MB limit")
        data = p.read_bytes()
        name, mime = safe_upload_name(p.name), mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        dest, digest = blob_store.store(tb.documents.db.data_dir, name, data)
        row = tb.documents.find_by_hash(pid, digest) or tb.documents.create(pid, name, mime, len(data), str(dest), "", content_hash=digest)
        return {"id": row["id"], "name": row["name"], "mime": row["mime"], "size": row["size"]}

    def thumb(att: dict[str, Any]) -> dict[str, Any] | None:
        """The chat thumbnail of an attached picture (JPEG), None for anything else or an unreadable file."""
        if not str(att["mime"]).startswith("image/") or att["mime"] == "image/svg+xml":
            return None
        d = tb.documents.get(att["id"])
        p = blob_store.inside_uploads(tb.documents.db.data_dir, d and d.get("path"))
        try:
            jpeg, _, _ = vision.prepare(p.read_bytes()) if p else (b"", 0, 0)
        except (vision.ImageError, OSError):
            return None
        return {"name": att["name"], "mime": "image/jpeg", "bytes": len(jpeg), "data": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")} if jpeg else None

    async def send_files(ctx: dict[str, Any], files: Any = None, text: str = "") -> Any:
        entries = [files] if isinstance(files, str) else [str(f) for f in (files or [])]
        text = (text or "").strip()
        if not entries and not text:
            return tool_error("send_files: pass files, text, or both", field="files", example={"files": ["<document id>"], "text": "Here is the page."})
        attached: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for raw in entries[:MAX_FILES]:
            try:
                attached.append(await asyncio.to_thread(document, ctx, raw.strip()))
            except ValueError as e:
                errors.append({"file": raw, "error": str(e)})
        errors += [{"file": raw, "error": f"at most {MAX_FILES} files per call"} for raw in entries[MAX_FILES:]]
        if entries and not attached:
            return tool_error("send_files: none of the files could be sent: " + "; ".join(f"{e['file']}: {e['error']}" for e in errors),
                              field="files", alternative="pass document ids from screenshot / generate_image, or paths that exist on this Mac")
        seen = ctx.setdefault("reply_attachments", [])
        for a in attached:
            if all(s["id"] != a["id"] for s in seen):
                seen.append(a)
        delivered = "app"
        hook = getattr(tb, "user_update", None)
        if hook is not None:
            try:
                delivered = "telegram" if await hook(ctx, text, attached) else "app"
            except Exception:  # noqa: BLE001 - a failed delivery must not fail the tool; the files still ride on the reply
                delivered = "app"
        shown = [t for t in await asyncio.gather(*(asyncio.to_thread(thumb, a) for a in attached)) if t]
        out: dict[str, Any] = {"images": shown, "attached": attached, "delivered": delivered, "text": text}
        if errors:
            out["errors"] = errors
        return out

    spec = ToolSpec(
        "send_files",
        "Show the user a screenshot, chart or file: pass document ids (from screenshot, browser_manage(screenshot), generate_image or an upload) or paths on this Mac, and an "
        "optional short text. Up to 20 files, 50 MB each. In the app the files appear on your reply. In a Telegram chat they are also delivered "
        "to the user's phone at once as a progress update: send one at meaningful milestones, not every step, and attach a screenshot when it "
        "shows more than words. Files always ride on the final reply in the app.",
        _obj({"files": {"type": "array", "items": {"type": "string"}, "description": "Document ids, or paths on this Mac"},
              "text": {"type": "string", "description": "A short message sent with them (optional)"}}, ["files"]),
        send_files, "utility", "safe",
        examples=[{"files": ["<document id>"], "text": "The settings page, as it is now."}])
    tb.specs["send_files"] = spec
