"""GET /cowork/desks/{id}/preview: a picture, a page of text, a page of extracted document text, or a reason.

Run: PERSONAL_OS_DATA_DIR=/tmp/x backend/.venv/bin/python backend/tests/test_desk_preview.py
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="deskpreview-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.app import AUTH_TOKEN, PREVIEW_PAGE, app, convos, desks, workspace  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


conv = convos.create(None, "desk", "test-model")
desk = desks.create(conversation_id=conv["id"], brief="Preview test")
DID = desk["id"]
root = workspace.ensure(DID)


def put(rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def get(path: str, **kw: Any) -> Any:
    return client.get(f"/cowork/desks/{DID}/preview", params={"path": path, **kw})


def png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode == "RGB" else (200, 30, 30, 120)).save(buf, "PNG")
    return buf.getvalue()


def test_image_is_scaled_and_inlined() -> None:
    put("outputs/big.png", png((3200, 1600)))
    r = get("outputs/big.png")
    check(r.status_code == 200, "image 200")
    j = r.json()
    check(j["kind"] == "image", "kind image")
    check(max(j["width"], j["height"]) <= 1600 and j["width"] == 1600, "long edge scaled to 1600")
    check(j["data_url"].startswith("data:image/jpeg;base64,"), "opaque image becomes jpeg")
    check(len(j["data_url"]) < 1_600_000, "within budget")
    img = Image.open(io.BytesIO(base64.b64decode(j["data_url"].split(",", 1)[1])))
    check(img.size == (1600, 800), "decodes back at the scaled size")
    put("work/alpha.png", png((20, 20), "RGBA"))
    check(get("work/alpha.png").json()["data_url"].startswith("data:image/png;base64,"), "transparent image stays png")
    put("work/broken.png", b"not an image")
    check(get("work/broken.png").json()["kind"] == "none", "undecodable image is none, not 500")


def test_text_pages() -> None:
    body = "".join(f"line {i}\n" for i in range(6000))
    put("outputs/long.md", body.encode())
    j = get("outputs/long.md").json()
    check(j["kind"] == "text" and j["offset"] == 0 and len(j["text"]) == PREVIEW_PAGE, "first page is one page long")
    check(j["next_offset"] == PREVIEW_PAGE and j["total_chars"] == len(body), "next_offset and total")
    j2 = get("outputs/long.md", offset=j["next_offset"]).json()
    check(j2["text"] == body[PREVIEW_PAGE:2 * PREVIEW_PAGE], "second page continues")
    last = get("outputs/long.md", offset=len(body) - 5).json()
    check(last["next_offset"] is None and last["text"] == body[-5:], "last page has no next_offset")
    check(get("outputs/long.md", offset=10**9).json()["text"] == "", "offset past the end is empty, not an error")
    put("work/pic.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>")
    s = get("work/pic.svg").json()
    check(s["kind"] == "text" and "<svg" in s["text"], "svg is text, never rendered")


def test_document_extracted() -> None:
    orig = appmod.extract_text
    appmod.extract_text = lambda name, data, mime="": "Page one words. " * 3000   # type: ignore[assignment]
    try:
        put("outputs/report.pdf", b"%PDF-1.4 fake")
        j = get("outputs/report.pdf").json()
        check(j["kind"] == "document" and j["note"], "pdf is a document with a note")
        check(len(j["text"]) == PREVIEW_PAGE and j["next_offset"] == PREVIEW_PAGE, "document pages too")
        put("work/blob.bin", b"\x00\x01\x02binary")
        check(get("work/blob.bin").json()["kind"] == "document", "NUL bytes route to the extractor")
        appmod.extract_text = lambda name, data, mime="": ""   # type: ignore[assignment]
        put("outputs/empty.pdf", b"%PDF-1.4 scanned")
        n = get("outputs/empty.pdf").json()
        check(n["kind"] == "none" and "No readable text" in n["reason"], "no extractable text is none")
        appmod.extract_text = lambda name, data, mime="": 1 / 0   # type: ignore[assignment]
        put("outputs/boom.docx", b"PK")
        check(get("outputs/boom.docx").json()["kind"] == "none", "an extractor that raises is none, not 500")
    finally:
        appmod.extract_text = orig


def test_too_large() -> None:
    orig = appmod.PREVIEW_MAX_BYTES
    appmod.PREVIEW_MAX_BYTES = 10
    try:
        put("work/huge.txt", b"x" * 50)
        j = get("work/huge.txt").json()
        check(j["kind"] == "none" and "too large" in j["reason"], "over the limit is none with a reason")
    finally:
        appmod.PREVIEW_MAX_BYTES = orig


def test_paths() -> None:
    check(get("../../etc/passwd").status_code == 400, "dotdot refused")
    check(get("/etc/passwd").status_code == 400, "absolute refused")
    check(get("outputs/nope.txt").status_code == 404, "missing file is 404")
    check(get("outputs").status_code == 404, "a directory is not a file")
    link = root / "work" / "escape"
    outside = Path(tempfile.mkdtemp(prefix="deskoutside-")) / "secret.txt"
    outside.write_text("secret")
    link.symlink_to(outside)
    check(get("work/escape").status_code == 400, "symlink out of the workspace refused")
    check(client.get("/cowork/desks/dk_missing/preview", params={"path": "x"}).status_code == 404, "unknown desk 404")


if __name__ == "__main__":
    for t in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        t()
        print(f"ok   {t.__name__}")
    print(f"inner totals: {passed} passed")
