"""screenshot (screenshot.py): the permission preflight, a solid capture read as a missing grant, a good capture saved to
Uploads with a thumbnail and an attachment, region validation, and a window that is not on screen. screencapture is faked."""
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

from personal_os import macos, screenshot, vision  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _png(solid: bool = False, size: tuple[int, int] = (64, 32)) -> bytes:
    im = Image.new("RGB", size, (20, 30, 40))
    if not solid:
        im.paste((250, 10, 10), (0, 0, size[0] // 2, size[1]))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Box:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tb = Toolbox(None, None, Documents(Database(tmp_path)), lambda: {})  # type: ignore[arg-type]
        self.calls: list[list[str]] = []
        self.png = _png()
        self.status = macos.GRANTED
        monkeypatch.setattr(macos, "screen_recording_status", lambda: self.status)
        monkeypatch.setattr(vision, "_which", lambda b: "/usr/sbin/screencapture")
        monkeypatch.setattr(screenshot.subprocess, "run", self._run)

    def _run(self, cmd: list[str], **kw: Any) -> Any:
        self.calls.append(cmd)
        Path(cmd[-1]).write_bytes(self.png)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    def call(self, **args: Any) -> Any:
        return asyncio.run(self.tb.specs["screenshot"].fn({"project_id": None}, **args))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    return Box(tmp_path, monkeypatch)


def test_registered_as_a_safe_tainting_mac_tool(box: Box) -> None:
    spec = box.tb.specs["screenshot"]
    assert spec.group == "mac" and spec.danger == "safe" and spec.taints is True


def test_denied_permission_names_screen_recording_and_settings(box: Box) -> None:
    box.status = macos.DENIED
    out = box.call()
    assert "Screen Recording permission" in out["error"] and "Settings → Permissions" in out["try_instead"]
    assert box.calls == []  # nothing was captured


def test_unknown_permission_with_a_solid_capture_is_the_same_error(box: Box) -> None:
    box.status = macos.UNKNOWN
    box.png = _png(solid=True)
    assert "Screen Recording permission" in box.call()["error"]
    box.png = _png()  # a real picture goes through when the preflight cannot say
    assert box.call()["attachment"]["mime"] == "image/png"


def test_a_failed_capture_while_granted_is_a_plain_failure(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screenshot.subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 1})())
    assert "capture failed" in box.call()["error"]


def test_a_screen_capture_is_saved_and_returned(box: Box) -> None:
    out = box.call(target="screen", display=2)
    assert box.calls[0][0].endswith("screencapture") and box.calls[0][1:4] == ["-x", "-t", "png"] and box.calls[0][4:6] == ["-D", "2"]
    s, att = out["saved"][0], out["attachment"]
    assert (s["width"], s["height"]) == (64, 32) and Path(s["path"]).read_bytes() == box.png
    assert Path(s["path"]).parent.parent.name == "uploads"
    doc = box.tb.documents.get(att["id"])
    assert doc["mime"] == "image/png" and att == {"id": s["doc_id"], "name": doc["name"], "mime": "image/png", "size": len(box.png)}
    assert att["name"].startswith("screenshot-") and att["name"].endswith(".png")
    assert out["images"][0]["data"].startswith("data:image/png;base64,") and "send_files" in out["note"]


def test_a_big_capture_gets_a_jpeg_thumbnail_and_keeps_the_full_file(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screenshot, "THUMB_PNG_MAX", 10)
    out = box.call()
    assert out["images"][0]["data"].startswith("data:image/jpeg;base64,") and out["images"][0]["bytes"] == len(box.png)
    assert Path(out["saved"][0]["path"]).read_bytes() == box.png


def test_region_is_validated_and_passed_on(box: Box) -> None:
    for bad in ("", "1,2,3", "a,b,c,d", "-1,0,10,10", "0,0,0,10"):
        assert "region must be" in box.call(target="region", region=bad)["error"]
    assert box.calls == []
    box.call(target="region", region="10, 20, 300, 200")
    assert box.calls[0][4:6] == ["-R", "10,20,300,200"]
    assert "-x" in box.calls[0] and "-i" not in box.calls[0]  # silent, never interactive


def test_target_is_validated(box: Box) -> None:
    assert "target must be" in box.call(target="desktop")["error"]


def test_window_not_found_lists_the_apps(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screenshot, "find_window", lambda app, title: (None, ["Safari", "Notes"]))
    out = box.call(target="window", app="Mail")
    assert "no on-screen window" in out["error"] and "Safari, Notes" in out["try_instead"] and box.calls == []
    assert "needs app or title" in box.call(target="window")["error"]


def test_window_capture_uses_the_window_id_and_names_the_file(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screenshot, "find_window", lambda app, title: (4242, ["Safari"]))
    out = box.call(target="window", app="Safari")
    assert box.calls[0][4:7] == ["-o", "-l", "4242"]
    assert out["attachment"]["name"].endswith("-safari.png")
