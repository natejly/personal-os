"""generate_image: make pictures from a prompt with the provider's `/images/generations` endpoint (OpenAI shape).

Results are saved into Uploads as PNG files and ride to the UI as thumbnails; the model only gets ids and sizes.
One request, no retries: an image call is slow and billed, so a failure is reported instead of repeated.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import time
from typing import Any

import httpx

from . import llm, redact
from .db import new_id
from .extract_text import safe_upload_name

TIMEOUT_S = 180.0
MAX_N = 4
MAX_IMAGE_BYTES = 25 * 1024 * 1024
SIZE_RE = re.compile(r"^\d{3,4}x\d{3,4}$")


def _slug(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "image"


async def _fetch(client: httpx.AsyncClient, item: dict[str, Any]) -> bytes:
    if item.get("b64_json"):
        try:
            return base64.b64decode(item["b64_json"])
        except (binascii.Error, ValueError) as e:
            raise llm.LLMError("the provider sent an unreadable image") from e
    if str(item.get("url") or "").startswith(("http://", "https://")):
        r = await client.get(item["url"], follow_redirects=True)
        if r.status_code >= 400:
            raise llm.LLMError(f"could not download the image ({r.status_code})")
        return r.content
    raise llm.LLMError("the provider returned no image")


async def generate(settings: dict[str, Any], prompt: str, size: str, n: int) -> list[bytes]:
    """Image bytes, one per picture. Raises llm.LLMError with a message fit to show."""
    model = str(settings.get("imageModel") or "").strip()
    body = {"model": model, "prompt": prompt, "n": n, "size": size, "response_format": "b64_json"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            r = await client.post(llm._url(settings, "/images/generations", model), headers=llm._headers(settings), json=body)
        except httpx.HTTPError as e:
            raise llm.LLMError(f"the image provider did not answer ({type(e).__name__})") from e
        if r.status_code >= 400:
            raise llm.LLMError(f"the image provider refused ({r.status_code}): {r.text[:300]}")
        try:
            items = [i for i in (r.json().get("data") or []) if isinstance(i, dict)]
        except ValueError as e:
            raise llm.LLMError("the image provider sent a reply that is not JSON") from e
        try:
            out = [await _fetch(client, i) for i in items[:n]]
        except httpx.HTTPError as e:
            raise llm.LLMError(f"could not download the image ({type(e).__name__})") from e
    if not out:
        raise llm.LLMError("the provider returned no image")
    return out


def register(tb: Any) -> None:
    """Register generate_image on a Toolbox (group `media`)."""
    from .tools import ToolSpec, _obj, tool_error

    async def generate_image(ctx: dict[str, Any], prompt: str = "", size: str = "1024x1024", n: int = 1, style: str = "") -> Any:
        s = ctx.get("settings") or tb.settings()
        prompt = (prompt or "").strip()
        if not prompt:
            return tool_error("generate_image: prompt is required", field="prompt", example={"prompt": "a lighthouse at dusk, watercolor"})
        if not str(s.get("imageModel") or "").strip():
            return tool_error("generate_image: no image model is set", alternative="ask the user to choose one in Settings → Model(s) → Image model; "
                              "the provider must offer an OpenAI-compatible /images/generations endpoint")
        size = (size or "1024x1024").strip()
        if not SIZE_RE.match(size):
            return tool_error("generate_image: size must look like 1024x1024", field="size", example={"prompt": prompt, "size": "1024x1024"})
        n = max(1, min(MAX_N, int(n or 1)))
        full = f"{prompt}\nStyle: {style.strip()}" if (style or "").strip() else prompt
        t0 = time.time()
        try:
            blobs = await generate(s, full, size, n)
        except llm.LLMError as e:
            return tool_error(redact.scrub_command_output(f"generate_image: {e}"), alternative="tell the user; do not retry the same request")
        llm._emit_usage(str(s["imageModel"]), "image", {"prompt_tokens": None, "completion_tokens": len(blobs)}, int((time.time() - t0) * 1000), 0, 0)
        from PIL import Image
        pid = ctx.get("project_id") if isinstance(ctx.get("project_id"), str) else None
        uploads = tb.documents.db.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        saved: list[dict[str, Any]] = []
        shown: list[dict[str, Any]] = []
        for i, data in enumerate(blobs):
            if len(data) > MAX_IMAGE_BYTES:
                continue
            try:
                im = Image.open(io.BytesIO(data))
                im.load()
            except Exception:  # noqa: BLE001 - Pillow raises many things for a damaged file
                continue
            buf = io.BytesIO()
            im.save(buf, "PNG")
            png = buf.getvalue()
            name = safe_upload_name(f"{_slug(prompt)}-{i + 1}.png" if len(blobs) > 1 else f"{_slug(prompt)}.png")
            dest = uploads / f"{new_id()}-{name}"
            dest.write_bytes(png)
            row = tb.documents.create(pid, name, "image/png", len(png), str(dest), "", content_hash=hashlib.sha256(png).hexdigest())
            saved.append({"doc_id": row["id"], "path": str(dest), "width": im.width, "height": im.height})
            shown.append({"name": name, "mime": "image/png", "bytes": len(png), "data": "data:image/png;base64," + base64.b64encode(png).decode("ascii")})
        if not saved:
            return tool_error("generate_image: the provider's images could not be read", alternative="tell the user; try another image model")
        # ponytail: no side-panel `show`: Uploads sit in the app data folder, which /local/raw refuses to serve.
        return {"images": shown, "saved": saved, "prompt": prompt, "model": s["imageModel"],
                "note": "The pictures are saved in Uploads and the user can see them in the chat."}

    spec = ToolSpec(
        "generate_image",
        "Make a picture from a text prompt with the configured image model and save it in Uploads. The user sees the result in the chat; you get ids and sizes. "
        "Write a concrete prompt (subject, setting, style). Up to 4 images per call.",
        _obj({"prompt": {"type": "string", "description": "What the picture shows"},
              "size": {"type": "string", "default": "1024x1024", "description": "WIDTHxHEIGHT, e.g. 1024x1024 or 1536x1024"},
              "n": {"type": "integer", "default": 1, "description": "How many pictures (1-4)"},
              "style": {"type": "string", "description": "Optional style, e.g. watercolor, photo, flat vector"}}, ["prompt"]),
        generate_image, "media", "network", examples=[{"prompt": "a lighthouse at dusk, watercolor", "size": "1536x1024"}])
    spec.default = "ask"
    tb.specs["generate_image"] = spec
