"""space_list / space_add_widget / space_arrange (space_tools.py).

Run: pytest backend/tests/test_space_tools.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="spacetools-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402

tb, cv = appmod.toolbox, appmod.canvases


def call(name, **a):  # type: ignore[no-untyped-def]
    return asyncio.run(tb.call(name, a, {}))


def _overlap(a, b):  # type: ignore[no-untyped-def]
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def _offered(tools):  # type: ignore[no-untyped-def]
    return {s["function"]["name"] for s in tb.schemas(tb.effective(tools, None, None))}


def test_add_arrange_and_guards() -> None:
    sid = call("space_list")["spaces"][0]["canvas_id"]
    assert "space_add_widget" in _offered({})
    assert "space_add_widget" not in _offered({"space_add_widget": "off"})
    assert call("space_add_widget", canvas_id=sid, kind="bogus")["error"]
    assert call("space_add_widget", canvas_id=sid, kind="note", ref_id="missing")["error"]
    assert call("space_add_widget", canvas_id=sid, kind="note")["error"]
    assert call("space_add_widget", canvas_id="nope", kind="todos")["error"]
    for _ in range(5):
        assert call("space_add_widget", canvas_id=sid, kind="todos")["added"]
    for layout in ("grid", "cascade"):
        assert call("space_arrange", canvas_id=sid, layout=layout)["arranged"] == 5
        wins = cv.get(sid)["windows"]
        assert len(wins) == 5
        if layout == "grid":
            assert not any(_overlap(a, b) for i, a in enumerate(wins) for b in wins[i + 1:])
    assert call("space_arrange", canvas_id=sid, layout="x")["error"]
    cv.update(sid, {"locked": True})
    assert call("space_arrange", canvas_id=sid)["error"]
    assert len(cv.get(sid)["windows"]) == 5
