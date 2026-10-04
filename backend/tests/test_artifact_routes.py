"""Artifact routes and tools against the real app, with llm.complete stubbed.
Run: backend/.venv/bin/python backend/tests/test_artifact_routes.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="artroutes-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, canvases, toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
bare = TestClient(app)  # no token: what an iframe sends
passed = 0
fx: dict[str, Any] = {}
CALLS: list[str] = []
REPLIES: list[Any] = []

GOOD = "<!doctype html><html><head><title>Tip</title></head><body><h1>Tip splitter</h1><p>Enter a bill</p><script>var a=1;</script></body></html>"
BAD = "<!doctype html><html><head><title>Tip</title></head><body><h1>Tip splitter</h1><script>fetch('/x').then(r=>r.json())</script></body></html>"


async def fake_complete(settings: Any, model: Any, messages: Any, **kw: Any) -> str:
    CALLS.append(messages[-1]["content"][:60])
    r = REPLIES.pop(0)
    if isinstance(r, Exception):
        raise r
    return r

llm.complete = fake_complete  # type: ignore[assignment]


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def test_create_edit_restore_render() -> None:
    a = j("POST", "/artifacts", {"title": "Tip", "code": GOOD})
    check(a["version"] == 1 and a["lint"]["blocked"] == [], "create with code is version 1, clean lint")
    e = j("POST", f"/artifacts/{a['id']}/edit", {"edits": [{"search": "Tip splitter", "replace": "Bill splitter"}]})
    check(e["version"] == 2 and "Bill splitter" in e["code"], "patch edit makes version 2")
    check("Tip splitter" in j("GET", f"/artifacts/{a['id']}/versions/1")["code"], "old version still fetchable")
    check([v["version"] for v in j("GET", f"/artifacts/{a['id']}/versions")] == [2, 1], "history lists both")
    bad = j("POST", f"/artifacts/{a['id']}/edit", {"edits": [{"search": "nope nope", "replace": "x"}]}, expect=422)
    check(bad["detail"]["edit_index"] == 0, "failed patch is a 422 naming the edit")
    check(j("GET", f"/artifacts/{a['id']}")["version"] == 2, "failed patch saved nothing")
    r = j("POST", f"/artifacts/{a['id']}/restore", {"version": 1})
    check(r["version"] == 3 and "Tip splitter" in r["code"], "restore appends version 3")
    j("POST", f"/artifacts/{a['id']}/restore", {"version": 99}, expect=404)
    u = j("POST", f"/artifacts/{a['id']}/code", {"code": GOOD.replace("Enter", "Type")})
    check(u["version"] == 4, "hand edit is a version")
    fx["id"] = a["id"]


def test_render_headers_and_auth() -> None:
    check(bare.get(f"/artifacts/{fx['id']}/render").status_code == 401, "render without its signed token is refused")
    signed = j("GET", f"/artifacts/{fx['id']}")["render_path"]
    check(bare.get(signed.replace("rt=", "rt=0")).status_code == 401, "a tampered token is refused")
    r = bare.get(signed)
    check(r.status_code == 200, "the signed render path needs no app token")
    csp = r.headers["content-security-policy"]
    check("connect-src 'none'" in csp and "sandbox allow-scripts" in csp, "strict CSP on render")
    check(bare.get("/artifacts/nope/render").status_code == 401, "render of an unknown id is refused like a bad token")
    check(bare.get(f"/artifacts/{fx['id']}").status_code == 401, "the JSON route still needs the token")
    check(bare.get(f"/artifacts/{fx['id']}/versions").status_code == 401, "so do the other sub-routes")
    check(bare.post(f"/artifacts/{fx['id']}/render").status_code == 401, "render exemption is GET only")


def test_lint() -> None:
    a = j("POST", "/artifacts", {"code": BAD})
    check("network" in a["lint"]["blocked"], "fetch( flagged")
    e = j("POST", "/artifacts", {"code": "<html><body></body></html>"})
    check(e["lint"]["empty"] is True, "empty body flagged")
    j("DELETE", f"/artifacts/{a['id']}")
    j("DELETE", f"/artifacts/{e['id']}")


def test_generate_with_one_repair() -> None:
    CALLS.clear()
    REPLIES[:] = [BAD, GOOD]
    a = j("POST", "/artifacts", {"prompt": "a tip splitter"})
    check(len(CALLS) == 2, "exactly one repair call")
    check(a["lint"]["blocked"] == [] and a["lint"]["repaired"] is True and "fetch" not in a["code"], "saved version is the repaired one")
    check(a["version"] == 1, "only the repaired document is stored")
    CALLS.clear()
    REPLIES[:] = [BAD, BAD]
    b = j("POST", "/artifacts", {"prompt": "again"})
    check(len(CALLS) == 2 and b["lint"]["blocked"] == ["network"], "a failed repair stops after one round and reports it")
    REPLIES[:] = [BAD, llm.LLMError("timed out")]
    d = j("POST", "/artifacts", {"prompt": "provider hiccup"})
    check(d["version"] == 1 and d["lint"]["repaired"] is False, "a repair call that errors keeps the first generation")
    CALLS.clear()
    REPLIES[:] = [GOOD]
    c = j("POST", f"/artifacts/{a['id']}/revise", {"instruction": "bigger title"})
    check(c["version"] == 2 and len(CALLS) == 1, "revise is one call when clean")
    j("POST", "/artifacts", {}, expect=400)


def test_delete_sweeps_windows() -> None:
    a = j("POST", "/artifacts", {"code": GOOD})
    cid = j("POST", "/canvases", {"name": "art"})["id"]
    check(canvases.add_window(cid, "artifact", ref_id=a["id"]) is not None, "window added")
    j("DELETE", f"/artifacts/{a['id']}")
    left = [x for x in j("GET", f"/canvases/{cid}")["windows"] if x["kind"] == "artifact"]
    check(left == [], "deleting the artifact removes its windows")
    j("DELETE", f"/canvases/{cid}")
    j("GET", f"/artifacts/{a['id']}", expect=404)


def test_tools() -> None:
    names = {t["name"]: t for t in toolbox.list()}
    check(all(n in names and names[n]["danger"] == "writes" for n in ("artifact_create", "artifact_edit", "artifact_update")), "three tools, local writes")
    ctx: dict[str, Any] = {"project_id": None}
    out = asyncio.run(toolbox.call("artifact_create", {"title": "Tip", "html": GOOD}, ctx))
    check(out["version"] == 1 and ctx["artifact"]["action"] == "created", "artifact_create returns id/version and notes the card")
    ok = asyncio.run(toolbox.call("artifact_edit", {"artifact_id": out["artifact_id"], "edits": [{"search": "Tip splitter", "replace": "X"}]}, {"project_id": None}))
    check(ok["version"] == 2, "artifact_edit saves a version")
    bad = asyncio.run(toolbox.call("artifact_edit", {"artifact_id": out["artifact_id"], "edits": [{"search": "zzz", "replace": "X"}]}, {"project_id": None}))
    check("error" in bad and "nothing was changed" in bad["error"], "a failed patch is a tool error")
    miss = asyncio.run(toolbox.call("artifact_edit", {"artifact_id": "nope", "edits": []}, {"project_id": None}))
    check("error" in miss, "unknown id is an error")


TESTS = [test_create_edit_restore_render, test_render_headers_and_auth, test_lint, test_generate_with_one_repair,
         test_delete_sweeps_windows, test_tools]

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
