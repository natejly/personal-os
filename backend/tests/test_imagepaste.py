"""POST /docs/{id}/describe-image: alt, OCR and description land on an upload row that search finds; no model is graceful."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="imgpaste-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from personal_os import app as appmod, llm, vision  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, "PNG")
    return buf.getvalue()


def _note_with_image(color: tuple[int, int, int]) -> tuple[str, str]:
    did = client.post("/docs", json={"title": "Trip", "content": ""}).json()["id"]
    url = client.post(f"/docs/{did}/assets", files={"file": ("a.png", _png(color), "image/png")}).json()["url"]
    return did, url


def test_describe_stores_text_on_an_upload_row_search_finds(monkeypatch: Any) -> None:
    async def complete(*_a: Any, **_k: Any) -> str:
        return "ALT: A boarding pass\nTEXT: gate zebulon42\nDESCRIPTION: A boarding pass for a flight."
    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(vision, "model_for", lambda *_a, **_k: "vision-model")
    did, url = _note_with_image((10, 20, 30))
    r = client.post(f"/docs/{did}/describe-image", json={"url": url}).json()
    assert r["alt"] == "A boarding pass" and r["document_id"] and "zebulon42" in r["text"]
    row = appmod.documents.get(r["document_id"])
    assert row["name"].startswith("Trip-") and row["name"].endswith(".png") and "zebulon42" in row["text"]
    assert any(h["document_id"] == r["document_id"] for h in appmod.documents.search(row["project_id"], "zebulon42"))


def test_no_vision_model_keeps_the_image(monkeypatch: Any) -> None:
    monkeypatch.setattr(vision, "model_for", lambda *_a, **_k: None)
    monkeypatch.setattr(vision, "ocr_available", lambda: False)
    did, url = _note_with_image((90, 80, 70))
    r = client.post(f"/docs/{did}/describe-image", json={"url": url}).json()
    assert r["alt"] == "image" and r["document_id"] is None and r["notice"]


def test_a_foreign_or_bad_url_is_refused() -> None:
    did, _ = _note_with_image((1, 2, 3))
    assert client.post(f"/docs/{did}/describe-image", json={"url": "/docs/assets/other/x.png"}).status_code == 404
    assert client.post(f"/docs/{did}/describe-image", json={"url": "https://x/y.png"}).status_code == 404
