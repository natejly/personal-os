"""generate_image (imagegen.py): b64 and url replies land in Uploads, the side payload is popped from the model's view,
an unset model errors cleanly, a provider error passes through, and a usage row is emitted. httpx is faked."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from personal_os import imagegen, llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _png(w: int = 48, h: int = 24) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.settings: dict[str, Any] = {"baseUrl": "http://fake/v1", "apiKey": "k", "imageModel": "flux-1", "defaultModel": ""}
        self.requests: list[httpx.Request] = []
        self.usage: list[dict[str, Any]] = []
        self.reply: Any = None
        self.tb = Toolbox(None, None, Documents(Database(tmp_path)), lambda: self.settings)  # type: ignore[arg-type]

    def call(self, **args: Any) -> Any:
        return asyncio.run(self.tb.specs["generate_image"].fn({"project_id": None}, **args))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    b = Box(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        b.requests.append(req)
        if req.url.path.endswith("/images/generations"):
            return b.reply(req)
        return httpx.Response(200, content=_png())  # the url-style download

    real = httpx.AsyncClient
    monkeypatch.setattr(imagegen.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm, "_usage_listeners", [b.usage.append])
    return b


def test_b64_reply_is_saved_to_uploads(box: Box) -> None:
    box.reply = lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]})
    out = box.call(prompt="A red fox, in the snow!")
    s = out["saved"][0]
    assert (s["width"], s["height"]) == (48, 24) and Path(s["path"]).read_bytes()[:4] == b"\x89PNG"
    assert Path(s["path"]).name == "a-red-fox-in-the-snow.png" and Path(s["path"]).parent.parent.name == "uploads"
    assert box.tb.documents.get(s["doc_id"])["mime"] == "image/png"
    assert out["images"][0]["data"].startswith("data:image/png;base64,")
    body = json.loads(box.requests[0].content)
    assert body["response_format"] == "b64_json" and body["model"] == "flux-1" and box.requests[0].headers["authorization"] == "Bearer k"
    assert box.usage[0]["kind"] == "image" and box.usage[0]["completion_tokens"] == 1 and box.usage[0]["estimated"] is True


def test_url_reply_is_downloaded(box: Box) -> None:
    box.reply = lambda req: httpx.Response(200, json={"data": [{"url": "http://cdn/x.png"}, {"url": "http://cdn/y.png"}]})
    out = box.call(prompt="two cats", n=2, style="ink")
    assert len(out["saved"]) == 2 and len(box.requests) == 3
    assert "Style: ink" in json.loads(box.requests[0].content)["prompt"]


def test_images_are_popped_from_what_the_model_sees(box: Box) -> None:
    box.reply = lambda req: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]})
    ctx = {"conversation_id": "c1", "settings": box.settings, "tainted": False, "taint_sources": [], "message_id": None, "project_id": None}
    result = asyncio.run(box.tb.call("generate_image", {"prompt": "a fox"}, ctx))
    images = result.pop("images", None)  # what app.py does before the result reaches the model
    assert images and "data:image" not in json.dumps(result) and result["saved"][0]["doc_id"]


def test_unset_model_and_provider_error(box: Box) -> None:
    box.settings["imageModel"] = ""
    assert "Settings" in box.call(prompt="x")["try_instead"] and not box.requests
    box.settings["imageModel"] = "flux-1"
    box.reply = lambda req: httpx.Response(400, text="content policy")
    err = box.call(prompt="x")["error"]
    assert "400" in err and "content policy" in err and len(box.requests) == 1  # no retry
    assert "size must" in box.call(prompt="x", size="big")["error"]
