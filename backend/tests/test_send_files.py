"""send_files (sendfiles.py): a document id or a path becomes an attachment on the reply, a path is copied into Uploads,
another project's file is refused, and the toolbox's user_update hook decides whether it says "telegram" or "app"."""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from personal_os import blobs  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents, Projects  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "data")
        self.docs = Documents(self.db)
        self.tb = Toolbox(None, None, self.docs, lambda: {})  # type: ignore[arg-type]
        self.src = tmp_path / "src"
        self.src.mkdir()
        self.ctx: dict[str, Any] = {"project_id": None}

    def doc(self, name: str, data: bytes, mime: str, pid: str | None = None) -> dict[str, Any]:
        dest, digest = blobs.store(self.db.data_dir, name, data)
        return self.docs.create(pid, name, mime, len(data), str(dest), "", content_hash=digest)

    def call(self, **args: Any) -> Any:
        return asyncio.run(self.tb.specs["send_files"].fn(self.ctx, **args))


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_registered_as_a_safe_utility_tool(box: Box) -> None:
    spec = box.tb.specs["send_files"]
    assert spec.group == "utility" and spec.danger == "safe" and box.tb.user_update is None


def test_a_document_id_rides_on_the_reply_with_a_thumbnail(box: Box) -> None:
    d = box.doc("shot.png", _png(), "image/png")
    out = box.call(files=[d["id"]], text="  here  ")
    att = {"id": d["id"], "name": "shot.png", "mime": "image/png", "size": len(_png())}
    assert out["attached"] == [att] and box.ctx["reply_attachments"] == [att]
    assert out["delivered"] == "app" and out["text"] == "here"
    assert out["images"][0]["data"].startswith("data:image/jpeg;base64,") and out["images"][0]["name"] == "shot.png"
    box.call(files=[d["id"]])  # the same file twice in a reply is one attachment
    assert box.ctx["reply_attachments"] == [att]


def test_a_non_image_has_no_thumbnail(box: Box) -> None:
    d = box.doc("notes.pdf", b"%PDF-1.4 fake", "application/pdf")
    out = box.call(files=[d["id"]])
    assert out["images"] == [] and out["attached"][0]["mime"] == "application/pdf"


def test_a_path_is_copied_into_uploads(box: Box) -> None:
    p = box.src / "chart.png"
    p.write_bytes(_png())
    out = box.call(files=[str(p)])
    att = out["attached"][0]
    doc = box.docs.get(att["id"])
    assert att["name"] == "chart.png" and att["mime"] == "image/png" and doc["path"] != str(p)
    assert Path(doc["path"]).parent.parent.name == "uploads" and Path(doc["path"]).read_bytes() == _png()
    assert box.call(files=[str(p)])["attached"][0]["id"] == att["id"]  # same bytes, same document
    t = box.src / "report.csv"
    t.write_text("a,b\n1,2\n")
    assert box.call(files=[str(t)])["attached"][0]["mime"] == "text/csv"


def test_a_file_of_another_project_is_refused(box: Box) -> None:
    pid = Projects(box.db).create("Other")["id"]
    foreign = box.doc("secret.png", _png(), "image/png", pid)
    out = box.call(files=[foreign["id"]])
    assert "belongs to another project" in out["error"] and "reply_attachments" not in box.ctx
    box.ctx["project_id"] = pid
    assert box.call(files=[foreign["id"]])["attached"][0]["id"] == foreign["id"]


def test_good_files_still_go_when_one_is_bad(box: Box) -> None:
    d = box.doc("a.png", _png(), "image/png")
    out = box.call(files=[d["id"], "no-such-id", str(box.src / "missing.txt")])
    assert [a["id"] for a in out["attached"]] == [d["id"]] and len(out["errors"]) == 2
    assert "none of the files" in box.call(files=["nope"])["error"]
    assert "pass files, text, or both" in box.call(files=[])["error"]


def test_limits_per_call(box: Box) -> None:
    ids = [box.doc(f"f{i}.txt", f"x{i}".encode(), "text/plain")["id"] for i in range(22)]
    out = box.call(files=ids)
    assert len(out["attached"]) == 20 and len(out["errors"]) == 2 and "at most 20" in out["errors"][0]["error"]
    big = box.src / "big.bin"
    with big.open("wb") as f:
        f.truncate(51 * 1024 * 1024)
    assert "over the 50 MB limit" in box.call(files=[str(big)])["error"]


def test_the_hook_decides_delivery(box: Box) -> None:
    d = box.doc("a.png", _png(), "image/png")
    seen: list[tuple[Any, ...]] = []

    async def accept(ctx: dict[str, Any], text: str, atts: list[dict[str, Any]]) -> bool:
        seen.append((ctx["project_id"], text, [a["id"] for a in atts]))
        return True

    box.tb.user_update = accept
    out = box.call(files=[d["id"]], text="halfway")
    assert out["delivered"] == "telegram" and seen == [(None, "halfway", [d["id"]])]

    async def decline(ctx: dict[str, Any], text: str, atts: list[dict[str, Any]]) -> bool:
        return False

    box.tb.user_update = decline
    assert box.call(files=[d["id"]])["delivered"] == "app"

    async def boom(ctx: dict[str, Any], text: str, atts: list[dict[str, Any]]) -> bool:
        raise RuntimeError("telegram down")

    box.tb.user_update = boom
    out = box.call(files=[d["id"]])
    assert out["delivered"] == "app" and "error" not in out and box.ctx["reply_attachments"][0]["id"] == d["id"]
    box.tb.user_update = None
    assert box.call(text="just words")["delivered"] == "app"  # text alone is allowed
