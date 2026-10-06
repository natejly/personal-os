"""Space preset routes against the real app. Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_presets.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="presetstest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0
# Shared across tests (they run in order): the source canvas, its preset, and the note it references.
fx: dict[str, Any] = {}


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def test_create_from_canvas() -> None:
    cid = j("POST", "/canvases", {"name": "Preset source"})["id"]
    j("PUT", f"/canvases/{cid}", {"snap_mode": "grid", "grid_size": 24})
    note = j("POST", "/notes", {"body": "preset note"})
    todos = j("POST", f"/canvases/{cid}/windows", {"kind": "todos", "x": 10, "y": 20, "config": {"scope": "all"}})
    j("PUT", f"/windows/{todos['id']}", {"opacity": 0.6})
    nw = j("POST", f"/canvases/{cid}/windows", {"kind": "note", "ref_id": note["id"], "x": 0, "y": 0, "w": 1200, "h": 900})
    j("PUT", f"/windows/{nw['id']}", {"state": "maximized", "restore_bounds": {"x": 40, "y": 60, "w": 300, "h": 280}})
    cal = j("POST", f"/canvases/{cid}/windows", {"kind": "calendar", "x": 700, "y": 30})
    j("PUT", f"/windows/{cal['id']}", {"state": "minimized", "popout_bounds": {"x": 5, "y": 5, "width": 300, "height": 300}})
    src = j("GET", f"/canvases/{cid}")

    p = j("POST", "/canvas-presets", {"canvas_id": cid, "name": "  Morning  "})
    check(p["name"] == "Morning", "preset name is stripped")
    check(len(p["windows"]) == 3, "preset captures every window")
    check([w["z"] for w in p["windows"]] == [0, 1, 2], "z re-based 0..n-1 in z order")
    check([w["kind"] for w in p["windows"]] == ["todos", "note", "calendar"], "windows kept in z order")
    snap = p["windows"][1]
    check((snap["x"], snap["y"], snap["w"], snap["h"]) == (40, 60, 300, 280), "a maximized window keeps its restore bounds")
    check(snap["ref_id"] == note["id"], "ref_id is kept")
    check(p["windows"][0]["config"] == {"scope": "all"}, "config is kept")
    check(p["windows"][0]["opacity"] == 0.6, "transparency is kept")
    check(p["windows"][1]["opacity"] == 1.0, "an opaque window stays opaque")
    check(p["windows"][2]["x"] == 700, "a minimized window keeps its own bounds")
    check(all(k not in w for w in p["windows"] for k in ("state", "popout_bounds", "restore_bounds", "id")), "no live state is kept")
    check((p["snap_mode"], p["grid_size"]) == ("grid", 24), "snap_mode and grid_size are copied")
    check((p["zoom"], p["pan_x"], p["pan_y"], p["wallpaper"]) == (src["zoom"], src["pan_x"], src["pan_y"], src["wallpaper"]), "viewport is copied")
    check(p["project_id"] is None, "unbound canvas -> unbound preset")
    fx.update(cid=cid, pid=p["id"], note=note["id"], src_ids={w["id"] for w in src["windows"]}, todos=todos["id"])


def test_blank_name_and_unknown_canvas() -> None:
    p = j("POST", "/canvas-presets", {"canvas_id": fx["cid"], "name": "   "})
    check(p["name"] == "Preset source", "a blank name takes the canvas name")
    p2 = j("POST", "/canvas-presets", {"canvas_id": fx["cid"]})
    check(p2["name"] == "Preset source", "a missing name takes the canvas name")
    j("POST", "/canvas-presets", {"canvas_id": "nope"}, expect=404)
    j("DELETE", f"/canvas-presets/{p['id']}")
    j("DELETE", f"/canvas-presets/{p2['id']}")


def test_list_and_get() -> None:
    listed = j("GET", "/canvas-presets")
    check(fx["pid"] in [x["id"] for x in listed], "list contains the preset")
    check(all(isinstance(x["windows"], list) for x in listed), "list includes decoded windows")
    got = j("GET", f"/canvas-presets/{fx['pid']}")
    check(got["id"] == fx["pid"] and len(got["windows"]) == 3, "GET /canvas-presets/{id}")
    j("GET", "/canvas-presets/nope", expect=404)


def test_rename() -> None:
    before = j("GET", f"/canvas-presets/{fx['pid']}")
    r = j("PUT", f"/canvas-presets/{fx['pid']}", {"name": "  Focus  "})
    check(r["name"] == "Focus", "rename strips and saves")
    check(r["updated_at"] >= before["updated_at"], "rename touches updated_at")
    check(len(r["windows"]) == 3, "rename leaves windows alone")
    j("PUT", f"/canvas-presets/{fx['pid']}", {"name": "   "}, expect=400)
    check(j("GET", f"/canvas-presets/{fx['pid']}")["name"] == "Focus", "a rejected rename writes nothing")
    j("PUT", "/canvas-presets/nope", {"name": "x"}, expect=404)
    check(j("PUT", f"/canvas-presets/{fx['pid']}", {})["name"] == "Focus", "an empty patch is a no-op")


def test_instantiate() -> None:
    made = j("POST", f"/canvas-presets/{fx['pid']}/instantiate", {})
    positions = [c["position"] for c in j("GET", "/canvases")]
    check(made["id"] != fx["cid"], "instantiate makes a new canvas")
    check(made["position"] == max(positions), "new canvas is last")
    check(made["name"] == "Focus", "canvas takes the preset name")
    check(made["skipped"] == 0, "nothing skipped when every ref exists")
    check(len(made["windows"]) == 3, "every window is recreated")
    check(all(w["state"] == "normal" for w in made["windows"]), "all windows come back normal")
    check(all(w["id"] not in fx["src_ids"] for w in made["windows"]), "windows get fresh ids")
    check(all(w["canvas_id"] == made["id"] for w in made["windows"]), "windows belong to the new canvas")
    check([w["z"] for w in made["windows"]] == [0, 1, 2], "z order kept")
    nw = made["windows"][1]
    check((nw["x"], nw["y"], nw["w"], nw["h"]) == (40, 60, 300, 280), "maximized window returns at its restore bounds")
    check(nw["restore_bounds"] is None and nw["popout_bounds"] is None, "no bounds carried")
    check(nw["ref_id"] == fx["note"], "refs are shared, not copied")
    check(made["windows"][0]["opacity"] == 0.6, "transparency comes back with the window")
    check((made["snap_mode"], made["grid_size"]) == ("grid", 24), "canvas takes the preset's snap settings")
    check(j("GET", f"/canvases/{made['id']}")["id"] == made["id"], "new canvas is persisted")

    named = j("POST", f"/canvas-presets/{fx['pid']}/instantiate", {"name": "  Override  "})
    check(named["name"] == "Override", "name override works (and is stripped)")
    blank = j("POST", f"/canvas-presets/{fx['pid']}/instantiate", {"name": "  "})
    check(blank["name"] == "Focus", "a blank override falls back to the preset name")
    j("POST", "/canvas-presets/nope/instantiate", {}, expect=404)
    for c in (made, named, blank):
        j("DELETE", f"/canvases/{c['id']}")


def test_dangling_ref() -> None:
    j("DELETE", f"/notes/{fx['note']}")
    live = j("GET", f"/canvases/{fx['cid']}")
    check(all(w["kind"] != "note" for w in live["windows"]), "deleting the note sweeps its live window")
    made = j("POST", f"/canvas-presets/{fx['pid']}/instantiate", {})
    check(made["skipped"] == 1, f"the dangling note window is skipped, got {made['skipped']}")
    check([w["kind"] for w in made["windows"]] == ["todos", "calendar"], "the other windows survive")
    check(len(j("GET", f"/canvas-presets/{fx['pid']}")["windows"]) == 3, "the preset itself still lists 3 windows")
    j("DELETE", f"/canvases/{made['id']}")


def test_needs_ref_with_null_ref() -> None:
    cid = j("POST", "/canvases", {"name": "Chatty"})["id"]
    j("POST", f"/canvases/{cid}/windows", {"kind": "chat", "ref_id": None})
    j("POST", f"/canvases/{cid}/windows", {"kind": "recap"})
    p = j("POST", "/canvas-presets", {"canvas_id": cid})
    check(p["windows"][0]["kind"] == "chat" and p["windows"][0]["ref_id"] is None, "a null-ref chat is snapshotted as-is")
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(made["skipped"] == 1, "a needsRef kind with a null ref is skipped")
    check([w["kind"] for w in made["windows"]] == ["recap"], "non-needsRef windows keep their null ref")
    for x in (cid, made["id"]):
        j("DELETE", f"/canvases/{x}")
    j("DELETE", f"/canvas-presets/{p['id']}")


def test_unknown_kind_in_stored_json() -> None:
    import json

    from personal_os.app import db

    cid = j("POST", "/canvases", {"name": "Future"})["id"]
    j("POST", f"/canvases/{cid}/windows", {"kind": "usage"})
    p = j("POST", "/canvas-presets", {"canvas_id": cid})
    wins = [*p["windows"], {**p["windows"][0], "kind": "spreadsheet", "z": 1}]
    with db.tx() as c:
        c.execute("UPDATE canvas_presets SET windows=? WHERE id=?", (json.dumps(wins), p["id"]))
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(made["skipped"] == 1 and [w["kind"] for w in made["windows"]] == ["usage"], "an unknown stored kind is skipped and counted")
    for x in (cid, made["id"]):
        j("DELETE", f"/canvases/{x}")
    j("DELETE", f"/canvas-presets/{p['id']}")


def test_project_binding() -> None:
    proj = j("POST", "/projects", {"name": "Preset project"})["id"]
    cid = j("POST", "/canvases", {"name": "Bound", "project_id": proj})["id"]
    j("POST", f"/canvases/{cid}/windows", {"kind": "project", "ref_id": proj, "project_id": proj})
    j("POST", f"/canvases/{cid}/windows", {"kind": "todos", "project_id": proj})
    p = j("POST", "/canvas-presets", {"canvas_id": cid})
    check(p["project_id"] == proj, "preset keeps the canvas's project binding")
    check(all(w["project_id"] == proj for w in p["windows"]), "preset windows keep their project")

    bound = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(bound["project_id"] == proj and bound["skipped"] == 0, "a live project binds the new canvas")
    j("DELETE", f"/canvases/{bound['id']}")

    j("DELETE", f"/projects/{proj}")
    check(j("GET", f"/canvas-presets/{p['id']}")["project_id"] is None, "deleting the project SET NULLs the preset")
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(made["project_id"] is None, "instantiate makes an unbound canvas")
    check(made["skipped"] == 1, "the project window is skipped once the project is gone")
    check([(w["kind"], w["project_id"]) for w in made["windows"]] == [("todos", None)], "stale window project_id becomes null")
    for x in (cid, made["id"]):
        j("DELETE", f"/canvases/{x}")
    j("DELETE", f"/canvas-presets/{p['id']}")


def test_empty_canvas() -> None:
    cid = j("POST", "/canvases", {"name": "Blank"})["id"]
    p = j("POST", "/canvas-presets", {"canvas_id": cid})
    check(p["windows"] == [], "an empty canvas makes an empty preset")
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(made["windows"] == [] and made["skipped"] == 0, "an empty preset instantiates an empty canvas")
    for x in (cid, made["id"]):
        j("DELETE", f"/canvases/{x}")
    j("DELETE", f"/canvas-presets/{p['id']}")


def test_maximized_then_hidden() -> None:
    """The store sends only {state} on minimize / pop-out, so a once-maximized window still holds the full-viewport box."""
    cid = j("POST", "/canvases", {"name": "Max then hidden"})["id"]
    for kind, state, rb in (("todos", "minimized", (40, 60, 300, 280)), ("calendar", "popped", (90, 110, 320, 260))):
        x, y, w, h = rb
        win = j("POST", f"/canvases/{cid}/windows", {"kind": kind, "x": x, "y": y, "w": w, "h": h})
        j("PUT", f"/windows/{win['id']}", {"state": "maximized", "restore_bounds": {"x": x, "y": y, "w": w, "h": h}, "x": 0, "y": 0, "w": 1600, "h": 1000})
        j("PUT", f"/windows/{win['id']}", {"state": state})
    p = j("POST", "/canvas-presets", {"canvas_id": cid, "name": "mm"})
    got = [(w["x"], w["y"], w["w"], w["h"]) for w in p["windows"]]
    check(got[0] == (40, 60, 300, 280), "maximized then minimized keeps its restore bounds")
    check(got[1] == (90, 110, 320, 260), "maximized then popped keeps its restore bounds")
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check([(w["w"], w["h"], w["state"]) for w in made["windows"]] == [(300, 280, "normal"), (320, 260, "normal")], "instantiated at the real size")
    for x in (cid, made["id"]):
        j("DELETE", f"/canvases/{x}")
    j("DELETE", f"/canvas-presets/{p['id']}")


def test_export_import_round_trip() -> None:
    import json

    from personal_os.app import db

    cid = j("POST", "/canvases", {"name": "Portable"})["id"]
    note = j("POST", "/notes", {"body": "carry me", "color": "blue"})
    j("POST", f"/canvases/{cid}/windows", {"kind": "note", "ref_id": note["id"]})
    j("POST", f"/canvases/{cid}/windows", {"kind": "todos"})
    p = j("POST", "/canvas-presets", {"canvas_id": cid})
    exp = j("GET", f"/canvas-presets/{p['id']}/export")
    check(exp["embedded"][note["id"]]["body"] == "carry me", "export embeds the note body")
    # Drop the referents, then import: they must be recreated, not skipped.
    j("DELETE", f"/notes/{note['id']}")
    got = j("POST", "/canvas-presets/import", {"file": json.loads(json.dumps(exp))})
    made = got["canvas"]
    check(made["skipped"] == 0, "nothing skipped: referents were recreated")
    check([w["kind"] for w in made["windows"]] == ["note", "todos"], "same kinds and count")
    nid = made["windows"][0]["ref_id"]
    check(nid != note["id"] and j("GET", f"/notes/{nid}")["body"] == "carry me", "note recreated under a new id")
    check(j("GET", f"/canvas-presets/{got['preset']['id']}")["name"] == p["name"], "preset stored")
    j("POST", "/canvas-presets/import", {"file": {"windows": []}}, expect=400)
    j("GET", "/canvas-presets/nope/export", expect=404)


def test_delete() -> None:
    j("DELETE", f"/canvases/{fx['cid']}")
    check(len(j("GET", f"/canvas-presets/{fx['pid']}")["windows"]) == 3, "deleting the source canvas keeps the preset")
    check(j("DELETE", f"/canvas-presets/{fx['pid']}")["ok"] is True, "delete returns ok")
    j("GET", f"/canvas-presets/{fx['pid']}", expect=404)
    check(fx["pid"] not in [x["id"] for x in j("GET", "/canvas-presets")], "deleted preset leaves the list")
    check(j("DELETE", f"/canvas-presets/{fx['pid']}")["ok"] is True, "delete is idempotent")


TESTS = [test_create_from_canvas, test_blank_name_and_unknown_canvas, test_list_and_get, test_rename, test_instantiate,
         test_dangling_ref, test_needs_ref_with_null_ref, test_unknown_kind_in_stored_json, test_project_binding,
         test_empty_canvas, test_maximized_then_hidden, test_export_import_round_trip, test_delete]

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
