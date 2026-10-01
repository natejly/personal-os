"""Artifact windows: widget kind, presets, render shim. Run: backend/.venv/bin/python backend/tests/test_artifact_widget.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="artwidget-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import artifacts as A  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.canvas import WIDGET_KINDS  # noqa: E402
from personal_os.presets import REF_TABLES  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0
DOC = "<!doctype html><html><head><title>T</title></head><body><h1>Hello there, this is a page</h1></body></html>"


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def test_kind_and_ref_table() -> None:
    check("artifact" in WIDGET_KINDS and REF_TABLES["artifact"] == "artifacts", "artifact is a widget kind with a ref table")
    cid = j("POST", "/canvases", {"name": "art space"})["id"]
    art = j("POST", "/artifacts", {"title": "T", "code": DOC})
    w = j("POST", f"/canvases/{cid}/windows", {"kind": "artifact", "ref_id": art["id"]})
    check(w["kind"] == "artifact" and w["ref_id"] == art["id"], "add_window accepts kind artifact")
    j("POST", f"/canvases/{cid}/windows", {"kind": "nonsense"}, expect=400)
    check(True, "unknown kinds still fail")
    globals()["fx"] = {"cid": cid, "art": art["id"]}


def test_preset_round_trip_and_dangling() -> None:
    fx = globals()["fx"]
    p = j("POST", "/canvas-presets", {"canvas_id": fx["cid"], "name": "with artifact"})
    check([w["kind"] for w in p["windows"]] == ["artifact"], "preset keeps the artifact window")
    made = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(len(made["windows"]) == 1 and made["windows"][0]["ref_id"] == fx["art"], "instantiate restores it while the artifact exists")
    j("DELETE", f"/artifacts/{fx['art']}")
    gone = [w for w in j("GET", f"/canvases/{fx['cid']}")["windows"] if w["kind"] == "artifact"]
    check(gone == [], "deleting the artifact removes its windows")
    again = j("POST", f"/canvas-presets/{p['id']}/instantiate", {})
    check(again["skipped"] == 1 and again["windows"] == [], "instantiate skips the window once the artifact is gone")


def test_shim() -> None:
    out = A.inject_shim(DOC)
    check(out.count(A.SHIM_MARKER) == 1 and out.index(A.SHIM_MARKER) < out.lower().index("</body>"), "shim is injected once, before </body>")
    check(A.inject_shim(out) == out, "inject_shim is idempotent")
    check(A.blocked_capabilities(out) == [], "the shim uses no banned API")
    check(A.inject_shim("<p>fragment</p>").endswith("</script>"), "a document with no body still gets it")
    check("fetch" not in A.SHIM and "localStorage" not in A.SHIM, "postMessage only")
    art = j("POST", "/artifacts", {"code": DOC})
    r = client.get(f"/artifacts/{art['id']}/render")
    check(r.text.count(A.SHIM_MARKER) == 1 and "connect-src 'none'" in r.headers["content-security-policy"], "render route injects the shim under the CSP")
    check(A.SHIM_MARKER not in j("GET", f"/artifacts/{art['id']}")["code"], "the stored version never carries the shim")


TESTS = [test_kind_and_ref_table, test_preset_round_trip_and_dangling, test_shim]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed")
    sys.exit(1 if failures else 0)
