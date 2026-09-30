"""Canvas + notes routes against the real app. Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_canvas.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="canvastest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def test_default_seed() -> None:
    first = j("GET", "/canvases")
    check(len(first) == 1, "one seeded canvas")
    check(first[0]["name"] == "Desk 1", "seeded canvas is Desk 1")
    check(first[0]["windows"] == [], "seeded canvas has no windows")
    check(first[0]["snap_mode"] == "both" and first[0]["grid_size"] == 16, "seed defaults")
    again = j("GET", "/canvases")
    check(len(again) == 1 and again[0]["id"] == first[0]["id"], "seeding is idempotent across two GETs")


def test_canvas_crud() -> None:
    seeded = j("GET", "/canvases")[0]
    made = j("POST", "/canvases", {"name": "  Research  "})
    check(made["name"] == "Research", "name is stripped")
    check(made["position"] == seeded["position"] + 1, "position is MAX+1")
    check(j("GET", f"/canvases/{made['id']}")["id"] == made["id"], "GET /canvases/{id}")
    j("GET", "/canvases/nope", expect=404)

    upd = j("PUT", f"/canvases/{made['id']}", {"name": "Deep work", "snap_mode": "grid", "grid_size": 24, "zoom": 1.5, "pan_x": -40})
    check(upd["name"] == "Deep work" and upd["snap_mode"] == "grid", "canvas update")
    check(upd["grid_size"] == 24 and upd["zoom"] == 1.5 and upd["pan_x"] == -40, "viewport persists")
    j("PUT", f"/canvases/{made['id']}", {"snap_mode": "diagonal"}, expect=400)
    j("PUT", "/canvases/nope", {"name": "x"}, expect=404)

    listed = j("GET", "/canvases")
    check([c["id"] for c in listed] == [seeded["id"], made["id"]], "list ordered by position")
    check(j("DELETE", f"/canvases/{made['id']}")["ok"] is True, "delete returns ok")
    check(len(j("GET", "/canvases")) == 1, "deleted canvas is gone")
    check(j("DELETE", f"/canvases/{made['id']}")["ok"] is True, "delete is idempotent")


def test_windows_and_z() -> None:
    cid = j("GET", "/canvases")[0]["id"]
    a = j("POST", f"/canvases/{cid}/windows", {"kind": "todos", "x": 10, "y": 20})
    b = j("POST", f"/canvases/{cid}/windows", {"kind": "note", "ref_id": "n1", "w": 300, "h": 300})
    c = j("POST", f"/canvases/{cid}/windows", {"kind": "recap", "title": "Today"})
    check([a["z"], b["z"], c["z"]] == [0, 1, 2], "z is MAX+1 per canvas")
    check(a["w"] == 520 and a["h"] == 640 and a["state"] == "normal", "window defaults")
    check(a["config"] == {} and a["restore_bounds"] is None and a["popout_bounds"] is None, "empty json columns")

    canvas = j("GET", f"/canvases/{cid}")
    check([w["id"] for w in canvas["windows"]] == [a["id"], b["id"], c["id"]], "windows ordered by z")
    check(j("GET", f"/windows/{a['id']}")["kind"] == "todos", "GET /windows/{wid}")
    j("GET", "/windows/nope", expect=404)
    j("POST", "/canvases/nope/windows", {"kind": "todos"}, expect=404)
    j("POST", f"/canvases/{cid}/windows", {"kind": "spreadsheet"}, expect=400)


def test_layout_bulk() -> None:
    cid = j("GET", "/canvases")[0]["id"]
    wins = j("GET", f"/canvases/{cid}")["windows"]
    a, b = wins[0], wins[1]
    res = j("PUT", f"/canvases/{cid}/layout", {"windows": [
        {"id": a["id"], "x": 100, "y": 200, "w": 480, "h": 320, "z": 7},
        {"id": b["id"], "x": 640, "y": 40},
        {"id": "ghost", "x": 1, "y": 1},
        {"id": b["id"]},
    ]})
    check(res == {"ok": True, "updated": 2}, f"layout reports updated count, got {res}")
    after = {w["id"]: w for w in j("GET", f"/canvases/{cid}")["windows"]}
    check((after[a["id"]]["x"], after[a["id"]]["y"], after[a["id"]]["w"], after[a["id"]]["h"], after[a["id"]]["z"]) == (100, 200, 480, 320, 7), "layout persists x/y/w/h/z")
    check((after[b["id"]]["x"], after[b["id"]]["y"]) == (640, 40), "partial layout entry only writes its fields")
    check(after[b["id"]]["w"] == b["w"], "unlisted fields untouched")
    check(after[a["id"]]["updated_at"] == after[b["id"]]["updated_at"], "one now() for the whole batch")

    j("PUT", f"/canvases/{cid}/layout", {"windows": [{"id": a["id"], "state": "maximized"}]})
    check(j("GET", f"/windows/{a['id']}")["state"] == "maximized", "layout writes state too")
    j("PUT", f"/canvases/{cid}/layout", {"windows": [{"id": a["id"], "state": "normal"}]})
    other = j("POST", "/canvases", {"name": "Foreign"})["id"]
    check(j("PUT", f"/canvases/{other}/layout", {"windows": [{"id": a["id"], "x": -1}]})["updated"] == 0, "a foreign canvas_id is a silent no-op")
    check(j("GET", f"/windows/{a['id']}")["x"] == 100, "the foreign write did not move the window")
    j("DELETE", f"/canvases/{other}")

    bad = client.put(f"/canvases/{cid}/layout", json={"windows": [{"id": a["id"], "x": 999, "state": "floating"}]})
    check(bad.status_code == 400, "unknown state 400s")
    check(j("GET", f"/windows/{a['id']}")["x"] == 100, "a rejected batch writes nothing")


def test_window_config_merges() -> None:
    cid = j("GET", "/canvases")[0]["id"]
    w = j("POST", f"/canvases/{cid}/windows", {"kind": "todos", "config": {"scope": "all", "includeDone": False}})
    merged = j("PUT", f"/windows/{w['id']}", {"config": {"q": "tax"}})
    check(merged["config"] == {"scope": "all", "includeDone": False, "q": "tax"}, f"config merges, got {merged['config']}")
    over = j("PUT", f"/windows/{w['id']}", {"config": {"scope": "personal"}})
    check(over["config"] == {"scope": "personal", "includeDone": False, "q": "tax"}, "merge overwrites only its own keys")
    check(j("PUT", f"/windows/{w['id']}", {"config": {}})["config"] == over["config"], "empty config is a no-op")

    titled = j("PUT", f"/windows/{w['id']}", {"title": "Taxes", "pinned": True, "state": "minimized"})
    check(titled["title"] == "Taxes" and titled["pinned"] == 1 and titled["state"] == "minimized", "title/pinned/state")
    check(j("PUT", f"/windows/{w['id']}", {"title": ""})["title"] == "", "title can be cleared back to derive-from-object")
    j("PUT", f"/windows/{w['id']}", {"state": "floating"}, expect=400)
    j("PUT", "/windows/nope", {"title": "x"}, expect=404)

    bounded = j("PUT", f"/windows/{w['id']}", {"restore_bounds": {"x": 0, "y": 0, "w": 520, "h": 640}, "popout_bounds": {"x": 12, "y": 24, "width": 400, "height": 300}})
    check(bounded["restore_bounds"] == {"x": 0, "y": 0, "w": 520, "h": 640}, "restore_bounds round-trips as JSON")
    check(bounded["popout_bounds"]["width"] == 400, "popout_bounds uses screen width/height")
    cleared = j("PUT", f"/windows/{w['id']}", {"clear_restore_bounds": True, "clear_popout_bounds": True})
    check(cleared["restore_bounds"] is None and cleared["popout_bounds"] is None, "clear_* flags null the bounds")
    j("DELETE", f"/windows/{w['id']}")
    j("GET", f"/windows/{w['id']}", expect=404)


def test_raise() -> None:
    cid = j("GET", "/canvases")[0]["id"]
    wins = j("GET", f"/canvases/{cid}")["windows"]
    bottom, top = wins[0], wins[-1]
    check(bottom["z"] < top["z"], "fixture has a bottom and a top")
    raised = j("POST", f"/windows/{bottom['id']}/raise")
    check(raised["z"] == top["z"] + 1, "raise puts the window on top")
    order = [w["id"] for w in j("GET", f"/canvases/{cid}")["windows"]]
    check(order[-1] == bottom["id"], "raised window is last in z order")
    check(j("POST", f"/windows/{bottom['id']}/raise")["z"] == raised["z"], "raise is a no-op when already topmost")
    j("POST", "/windows/nope/raise", expect=404)


def test_move_between_canvases() -> None:
    src = j("GET", "/canvases")[0]["id"]
    dest = j("POST", "/canvases", {"name": "Second"})["id"]
    j("POST", f"/canvases/{dest}/windows", {"kind": "usage"})
    w = j("POST", f"/canvases/{src}/windows", {"kind": "graph"})
    moved = j("PUT", f"/windows/{w['id']}", {"canvas_id": dest})
    check(moved["canvas_id"] == dest, "canvas_id moves the window")
    check(moved["z"] == 1, f"z re-bases to MAX+1 in the destination, got {moved['z']}")
    check(w["id"] not in [x["id"] for x in j("GET", f"/canvases/{src}")["windows"]], "window left the source space")
    j("DELETE", f"/canvases/{dest}")
    j("GET", f"/windows/{w['id']}", expect=404)
    check(True, "deleting a canvas CASCADEs its windows")


def test_copy_from() -> None:
    src = j("GET", "/canvases")[0]
    j("PUT", f"/windows/{src['windows'][0]['id']}", {"state": "popped", "popout_bounds": {"x": 5, "y": 5, "width": 300, "height": 300}})
    copy = j("POST", "/canvases", {"name": "Clone", "copy_from": src["id"]})
    check(len(copy["windows"]) == len(src["windows"]), "copy_from duplicates every window")
    check(all(w["id"] not in [s["id"] for s in src["windows"]] for w in copy["windows"]), "copies get fresh ids")
    check(all(w["state"] == "normal" and w["popout_bounds"] is None for w in copy["windows"]), "copies are normal and not popped")
    check([w["z"] for w in copy["windows"]] == [w["z"] for w in src["windows"]], "copies keep z")
    check((copy["snap_mode"], copy["grid_size"], copy["zoom"]) == (src["snap_mode"], src["grid_size"], src["zoom"]), "copies keep viewport settings")
    j("POST", "/canvases", {"name": "x", "copy_from": "nope"}, expect=404)
    j("DELETE", f"/canvases/{copy['id']}")


def test_reset_popped() -> None:
    from personal_os.app import canvases

    cid = j("GET", "/canvases")[0]["id"]
    w = j("POST", f"/canvases/{cid}/windows", {"kind": "chat", "ref_id": "c1"})
    j("PUT", f"/windows/{w['id']}", {"state": "popped"})
    check(canvases.reset_popped() >= 1, "reset_popped clears stale rows")
    check(j("GET", f"/windows/{w['id']}")["state"] == "normal", "popped windows come back to the canvas")
    check(canvases.reset_popped() == 0, "reset_popped is a no-op when nothing is popped")
    j("DELETE", f"/windows/{w['id']}")


def test_notes() -> None:
    n = j("POST", "/notes", {"body": "milk, eggs"})
    check(n["color"] == "yellow" and n["project_id"] is None, "note defaults")
    check(j("GET", f"/notes/{n['id']}")["body"] == "milk, eggs", "GET /notes/{id}")
    j("GET", "/notes/nope", expect=404)
    upd = j("PUT", f"/notes/{n['id']}", {"body": "milk, eggs, bread", "color": "blue"})
    check(upd["body"] == "milk, eggs, bread" and upd["color"] == "blue", "note update")
    check(upd["updated_at"] >= n["updated_at"], "note update touches updated_at")
    j("PUT", "/notes/nope", {"body": "x"}, expect=404)

    pid = j("POST", "/projects", {"name": "Canvas test"})["id"]
    scoped = j("POST", "/notes", {"body": "project-only", "project_id": pid})
    check(scoped["project_id"] == pid, "note takes a project")
    check(len(j("GET", "/notes")) == 2, "default list is every scope")
    check([x["id"] for x in j("GET", f"/notes?project_id={pid}")] == [scoped["id"]], "project scope filters")
    check([x["id"] for x in j("GET", "/notes?project_id=personal")] == [n["id"]], "personal scope is project_id IS NULL")
    check(len(j("GET", "/notes?project_id=all")) == 2, "all scope")
    check([x["id"] for x in j("GET", "/notes?q=bread")] == [n["id"]], "q is a LIKE on body")
    check(j("PUT", f"/notes/{scoped['id']}", {"clear_project": True})["project_id"] is None, "clear_project unbinds")

    j("PUT", f"/notes/{scoped['id']}", {"project_id": pid})
    j("DELETE", f"/projects/{pid}")
    survivor = j("GET", f"/notes/{scoped['id']}")
    check(survivor["project_id"] is None, "deleting a project SET NULLs its notes rather than deleting them")

    cid = j("GET", "/canvases")[0]["id"]
    w = j("POST", f"/canvases/{cid}/windows", {"kind": "note", "ref_id": n["id"]})
    keep = j("POST", f"/canvases/{cid}/windows", {"kind": "note", "ref_id": scoped["id"]})
    j("DELETE", f"/notes/{n['id']}")
    j("GET", f"/notes/{n['id']}", expect=404)
    j("GET", f"/windows/{w['id']}", expect=404)
    check(j("GET", f"/windows/{keep['id']}")["ref_id"] == scoped["id"], "deleting a note only sweeps its own windows")
    j("DELETE", f"/notes/{scoped['id']}")


TESTS = [test_default_seed, test_canvas_crud, test_windows_and_z, test_layout_bulk, test_window_config_merges,
         test_raise, test_move_between_canvases, test_copy_from, test_reset_popped, test_notes]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
