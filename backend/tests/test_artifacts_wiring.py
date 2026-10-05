"""Artifacts wired into the app: routes, signed render, tools, and run/conversation linkage that survives a reload.

Run: uv run --project backend --with pytest pytest backend/tests/test_artifacts_wiring.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="artifactwire-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
appmod.db.set_settings({"toolDeferAbove": 0})  # these tests drive their own tools; deferral is test_tool_search.py
from personal_os import artifacts as art  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
anon = TestClient(appmod.app)
store = appmod.run_store
ROUNDS: list[Any] = []
DOC = "<!doctype html><html><head><title>Tip</title></head><body><script>1</script></body></html>"


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):  # type: ignore[no-untyped-def]
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    yield
    ROUNDS.clear()


def mk(code: str = DOC, **kw: Any) -> dict[str, Any]:
    r = client.post("/artifacts", json={"title": "T", "code": code, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def wait_until(pred, label: str, timeout: float = 15.0):  # type: ignore[no-untyped-def]
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out: {label}")


def test_routes_require_auth_except_signed_render() -> None:
    a = mk()
    for method, path in (("get", "/artifacts"), ("get", f"/artifacts/{a['id']}"), ("post", "/artifacts"),
                         ("get", f"/artifacts/{a['id']}/versions"), ("delete", f"/artifacts/{a['id']}")):
        assert getattr(anon, method)(path).status_code == 401, path
    # the render route is token-exempt in the middleware, so it must refuse on its own
    assert anon.get(f"/artifacts/{a['id']}/render").status_code == 401
    assert anon.get(f"/artifacts/{a['id']}/render?re=1&rt=nope").status_code == 401
    ok = anon.get(a["render_path"])
    assert ok.status_code == 200 and "Tip" in ok.text


def test_render_token_is_bound_to_one_artifact_and_expires() -> None:
    a, b = mk(), mk()
    qs = a["render_path"].split("?", 1)[1]
    assert anon.get(f"/artifacts/{b['id']}/render?{qs}").status_code == 401, "a token for A must not open B"
    exp = int(time.time()) - 5
    stale = f"/artifacts/{a['id']}/render?re={exp}&rt={appmod._artifact_render_token(a['id'], exp)}"
    assert anon.get(stale).status_code == 401
    assert anon.get(f"/artifacts/nope/render?{qs}").status_code == 401


def test_render_serves_the_hardened_headers() -> None:
    a = mk()
    r = anon.get(a["render_path"])
    for k, v in art.RENDER_HEADERS.items():
        if k != "Content-Type":
            assert r.headers[k.lower()] == v, k
    csp = r.headers["content-security-policy"]
    assert "connect-src 'none'" in csp and "sandbox allow-scripts" in csp and "default-src 'none'" in csp


def test_versions_restore_and_pinned_render() -> None:
    a = mk()
    v2 = DOC.replace("Tip", "Tip two")
    r = client.put(f"/artifacts/{a['id']}", json={"code": v2, "instruction": "rename"})
    assert r.json()["version"] == 2
    vs = client.get(f"/artifacts/{a['id']}/versions").json()
    assert [v["version"] for v in vs] == [2, 1] and "code" not in vs[0]
    old = client.get(f"/artifacts/{a['id']}/versions/1").json()
    assert "Tip two" not in anon.get(old["render_path"]).text
    assert client.post(f"/artifacts/{a['id']}/restore/1").json()["version"] == 3
    assert "Tip two" not in client.get(f"/artifacts/{a['id']}").json()["code"]
    assert client.delete(f"/artifacts/{a['id']}").status_code == 200
    assert client.get(f"/artifacts/{a['id']}").status_code == 404


def test_tool_creates_and_links_to_conversation_run_and_reload() -> None:
    ROUNDS.append({"tool_calls": [{"id": "c0", "name": "artifact_create", "arguments": json.dumps({"title": "Calc", "html": DOC})}]})
    cid = client.post("/conversations", json={}).json()["id"]
    rid = client.post(f"/conversations/{cid}/chat", json={"content": "make a calc"}).json()["run_id"]
    row = wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "run")
    mine = client.get(f"/artifacts?conversation_id={cid}").json()
    assert len(mine) == 1 and mine[0]["run_id"] == rid and mine[0]["message_id"] == row["message_id"]
    aid = mine[0]["id"]
    # persisted on the run tape, so a reload replays it
    tape = store.events(rid)
    ev = [d for _, t, d in tape if t == "artifact"]
    assert ev and ev[0]["id"] == aid and ev[0]["version"] == 1 and ev[0]["action"] == "created"
    res = [d for _, t, d in tape if t == "tool_result"][0]
    assert res["artifact"]["id"] == aid
    # and on the stored message, which is what the chat shows after an app restart
    msgs = client.get(f"/conversations/{cid}").json()["messages"]
    tes = [t for m in msgs for t in (m.get("tool_events") or [])]
    assert tes and tes[0]["artifact"]["id"] == aid
    # update appends a version and emits a second event
    ROUNDS.append({"tool_calls": [{"id": "c1", "name": "artifact_update", "arguments": json.dumps({"artifact_id": aid, "html": DOC.replace("Tip", "Tip 2")})}]})
    rid2 = client.post(f"/conversations/{cid}/chat", json={"content": "tweak"}).json()["run_id"]
    wait_until(lambda: (r := store.get(rid2)) and r["status"] not in ("running", "awaiting_approval") and r, "run2")
    assert client.get(f"/artifacts/{aid}").json()["version"] == 2
    assert [d["action"] for _, t, d in store.events(rid2) if t == "artifact"] == ["updated"]


def test_artifact_tools_are_in_app_tier() -> None:
    for n in ("artifact_create", "artifact_update"):
        assert appmod.toolbox.specs[n].danger == "writes" and appmod.toolbox.default_mode(appmod.toolbox.specs[n]) == "on"
    assert appmod.toolbox.specs["artifact_read"].danger == "safe"


def test_render_exemption_is_only_get_on_the_exact_render_path() -> None:
    a = mk()
    # the middleware must not wave through look-alike paths or other methods
    assert anon.delete(f"/artifacts/{a['id']}/render").status_code == 401
    assert anon.get("/artifacts/render").status_code == 401
    assert anon.get(f"/artifacts/{a['id']}/versions/1/render").status_code == 401
    assert client.get(f"/artifacts/{a['id']}").status_code == 200
