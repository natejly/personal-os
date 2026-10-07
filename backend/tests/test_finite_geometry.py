"""Non-finite canvas geometry must be refused, not stored.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_finite_geometry.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="finitetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def check(cond: Any, label: str) -> None:
    assert cond, label


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def raw(method: str, path: str, body_text: str) -> Any:
    """Send a hand-written body: json.dumps emits bare NaN/Infinity, which a client library would too."""
    return client.request(method, path, content=body_text.encode(), headers={"Content-Type": "application/json"})


def _canvas_and_window() -> tuple[str, str]:
    cv = j("POST", "/canvases", {"name": "finite"})["id"]
    w = j("POST", f"/canvases/{cv}/windows", {"kind": "todos", "x": 10, "y": 20, "w": 300, "h": 200})["id"]
    return cv, w


def test_sane_geometry_round_trips() -> None:
    _, w = _canvas_and_window()
    got = j("GET", f"/windows/{w}")
    check((got["x"], got["y"], got["w"], got["h"]) == (10, 20, 300, 200), f"geometry round trip: {got}")
    moved = j("PUT", f"/windows/{w}", {"x": -12.5, "y": 0, "w": 800, "h": 600})
    check((moved["x"], moved["y"], moved["w"], moved["h"]) == (-12.5, 0, 800, 600), f"move: {moved}")


def test_non_finite_window_geometry_is_refused() -> None:
    _, w = _canvas_and_window()
    for body in ('{"x": NaN}', '{"y": Infinity}', '{"w": -Infinity}', '{"h": 1e400}', '{"x": 1e309}'):
        r = raw("PUT", f"/windows/{w}", body)
        check(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")
    # the window must be untouched, and above all must not come back with null geometry
    got = j("GET", f"/windows/{w}")
    check((got["x"], got["y"], got["w"], got["h"]) == (10, 20, 300, 200), f"geometry survived: {got}")
    check(all(got[k] is not None for k in "xywh"), f"no null geometry: {got}")


def test_non_finite_on_create_is_refused() -> None:
    cv, _ = _canvas_and_window()
    for body in ('{"kind": "todos", "x": Infinity}', '{"kind": "todos", "h": NaN}'):
        r = raw("POST", f"/canvases/{cv}/windows", body)
        check(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")


def test_non_finite_in_bulk_layout_is_refused() -> None:
    cv, w = _canvas_and_window()
    for body in ('{"windows": [{"id": "%s", "x": NaN}]}' % w, '{"windows": [{"id": "%s", "y": Infinity}]}' % w):
        r = raw("PUT", f"/canvases/{cv}/layout", body)
        check(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")
    got = j("GET", f"/windows/{w}")
    check((got["x"], got["y"]) == (10, 20), f"layout left geometry alone: {got}")


def test_bulk_layout_still_applies_finite_values() -> None:
    cv, w = _canvas_and_window()
    out = j("PUT", f"/canvases/{cv}/layout", {"windows": [{"id": w, "x": 5, "y": 6, "z": 3}]})
    check(out["updated"] == 1, f"one window updated: {out}")
    got = j("GET", f"/windows/{w}")
    check((got["x"], got["y"]) == (5, 6), f"layout applied: {got}")


def test_zoom_must_be_positive_and_finite() -> None:
    cv, _ = _canvas_and_window()
    for body in ('{"zoom": 0}', '{"zoom": -5}', '{"zoom": Infinity}', '{"zoom": NaN}'):
        r = raw("PUT", f"/canvases/{cv}", body)
        check(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")
    # zero zoom is what mints an infinite coordinate, so the canvas keeps its old value
    check(j("GET", f"/canvases/{cv}")["zoom"] == 1.0, "zoom unchanged")
    for z in (0.5, 1.5, 2):  # the band Canvas.tsx clamps to
        check(j("PUT", f"/canvases/{cv}", {"zoom": z})["zoom"] == z, f"zoom {z} accepted")


def test_pan_and_grid_are_validated() -> None:
    cv, _ = _canvas_and_window()
    for body in ('{"pan_x": Infinity}', '{"pan_y": NaN}'):
        r = raw("PUT", f"/canvases/{cv}", body)
        check(r.status_code == 422, f"{body} -> {r.status_code} {r.text[:200]}")
    for body in ({"grid_size": 0}, {"grid_size": -4}):
        j("PUT", f"/canvases/{cv}", body, expect=422)
    check(j("PUT", f"/canvases/{cv}", {"pan_x": -1200, "pan_y": 40})["pan_x"] == -1200, "finite pan accepted")
    check(j("PUT", f"/canvases/{cv}", {"grid_size": 16})["grid_size"] == 16, "grid accepted")


def test_a_rejected_non_finite_body_still_returns_json() -> None:
    """The 422 echoes the rejected input, and Starlette serialises with allow_nan=False -- so an un-scrubbed
    echo of inf turned the 422 into a 500. The refusal has to be readable."""
    _, w = _canvas_and_window()
    r = raw("PUT", f"/windows/{w}", '{"x": Infinity}')
    check(r.status_code == 422, f"status {r.status_code}")
    body = r.json()  # would raise if the response were not valid JSON
    check("detail" in body, f"detail present: {body}")
    check("finite" in str(body).lower(), f"says why: {body}")


def test_canvas_payload_has_no_null_geometry() -> None:
    cv, w = _canvas_and_window()
    for body in ('{"x": Infinity}', '{"y": NaN}'):
        raw("PUT", f"/windows/{w}", body)
    c = j("GET", f"/canvases/{cv}")
    for win in c["windows"]:
        check(all(win[k] is not None for k in "xywh"), f"window has full geometry: {win}")


if __name__ == "__main__":
    import inspect

    fns = [(n, f) for n, f in sorted(globals().items(), key=lambda kv: getattr(kv[1], "__code__", None).co_firstlineno
                                     if inspect.isfunction(kv[1]) else 0)
           if n.startswith("test_") and inspect.isfunction(f)]
    ok = 0
    for name, fn in fns:
        fn()
        print(f"PASS {name}")
        ok += 1
    print(f"\n{ok} passed")
