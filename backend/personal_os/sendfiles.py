"""send_files: hand the user files or pictures: a screenshot, a chart, a document the agent made.

Each file is an Uploads document (an id from screenshot / generate_image / an upload) or a path on this Mac, which is
copied into Uploads so the UI can serve it. The files ride on the reply message in the app. In a Telegram chat the
toolbox's `user_update` hook also delivers them to the phone at once, with the text, as a progress update.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from . import blobs as blob_store, mac, vision
from .extract_text import safe_upload_name

MAX_FILES = 20
MAX_BYTES = 50 * 1024 * 1024
ATTACH_KEYS = ("id", "name", "mime", "size")


def attach_to_reply(ctx: dict[str, Any], doc: dict[str, Any]) -> None:
    """Put an Uploads document ({id, name, mime, size}) on this run's final reply, once per id, in the order added."""
    seen = ctx.setdefault("reply_attachments", [])
    if all(s["id"] != doc["id"] for s in seen):
        seen.append({k: doc[k] for k in ATTACH_KEYS})


def store_file(tb: Any, ctx: dict[str, Any], name: str, mime: str, data: bytes) -> dict[str, Any]:
    """Keep bytes in Uploads (the same bytes share one row per project) and describe them as an attachment."""
    pid = ctx.get("project_id") if isinstance(ctx.get("project_id"), str) else None
    dest, digest = blob_store.store(tb.documents.db.data_dir, name, data)
    row = tb.documents.find_by_hash(pid, digest) or tb.documents.create(pid, name, mime, len(data), str(dest), "", content_hash=digest)
    return {"id": row["id"], "name": row["name"], "mime": row["mime"], "size": row["size"]}


def _read_file(p: Path) -> tuple[str, str, bytes] | None:
    try:
        if not p.is_file() or p.stat().st_size > MAX_BYTES:
            return None
        return safe_upload_name(p.name), mimetypes.guess_type(p.name)[0] or "application/octet-stream", p.read_bytes()
    except OSError:
        return None


def made_files(tb: Any, ctx: dict[str, Any], tool: str, out: dict[str, Any]) -> list[dict[str, Any]]:
    """The attachments for the files a finished tool call made: an `attachment` or Uploads `saved` rows (screenshots, images),
    `outputs` entries in this chat's files (scripts, sandbox exports, downloads), a converted file, a text file written on
    the Mac, a new Files note (as .md), or the charts a script drew. Anything unreadable is skipped."""
    found: list[dict[str, Any]] = []
    att = out.get("attachment")
    if isinstance(att, dict) and att.get("id"):
        found.append(att)
    for s in out.get("saved") if isinstance(out.get("saved"), list) else []:
        d = tb.documents.get(s.get("doc_id") or "") if isinstance(s, dict) else None
        if d:
            found.append({k: d[k] for k in ATTACH_KEYS})
    files: list[tuple[str, str, bytes]] = []
    paths: list[Path] = []
    box = tb.files_for(ctx) if hasattr(tb, "files_for") else None
    rels = [o["path"] for o in out.get("outputs") or [] if isinstance(o, dict) and isinstance(o.get("path"), str)]
    if tool == "convert_document" and isinstance(out.get("output"), str):
        rels.append(out["output"])
    for rel in rels:
        try:
            paths.append(Path(rel) if os.path.isabs(rel) else box[0].resolve_in(box[1], rel))
        except Exception:  # noqa: BLE001 - a WorkspaceError, or no files box for this run: skip the file
            continue
    if tool == "write_local_file" and out.get("mode") in ("create", "overwrite") and isinstance(out.get("path"), str):
        paths.append(Path(out["path"]))
    files += [f for f in map(_read_file, paths[:MAX_FILES]) if f]
    if tool == "doc_create" and getattr(tb, "docs", None) is not None and out.get("doc_id"):
        d = tb.docs.get(out["doc_id"])
        if d:
            files.append((safe_upload_name(f"{d['title'] or 'note'}.md"), "text/markdown", str(d["content"]).encode()))
    if tool == "run_python":
        for img in out.get("images") or []:
            head, _, b64 = str(img.get("data") if isinstance(img, dict) else "").partition(",")
            if head.startswith("data:") and b64:
                files.append((safe_upload_name(os.path.basename(str(img.get("name") or "chart.png"))), str(img.get("mime") or "image/png"), base64.b64decode(b64)))
    found += [store_file(tb, ctx, *f) for f in files[:MAX_FILES]]
    return found[:MAX_FILES]


async def attach_made(tb: Any, ctx: dict[str, Any], tool: str, out: Any) -> None:
    """Toolbox.call, when ctx["auto_attach"] is set (a Telegram chat turn): what the agent just made rides on the reply."""
    if isinstance(out, dict) and not out.get("error"):
        for d in await asyncio.to_thread(made_files, tb, ctx, tool, out):
            attach_to_reply(ctx, d)


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
        return store_file(tb, ctx, safe_upload_name(p.name), mimetypes.guess_type(p.name)[0] or "application/octet-stream", p.read_bytes())

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
        for a in attached:
            attach_to_reply(ctx, a)
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
