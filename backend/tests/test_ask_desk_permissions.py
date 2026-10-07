"""An autonomous chat (an 'ask' desk) follows the permission mode like a plain chat: no card is forced on every change.

Offline, one scripted llm.stream_chat; the reviewer (autoreview.review) is stubbed. Each test asserts on the
`tool_call` event of the desk's run (needs_approval, forced) or on the approval rows.

Run: backend/.venv/bin/python -m pytest backend/tests/test_ask_desk_permissions.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="askperm-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import autoreview, llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
FRONT: list[Any] = []
REVIEWED: list[str] = []


def call(cid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def write(cid: str) -> dict[str, Any]:
    return {"calls": [call(cid, "desk_write_file", {"path": "outputs/r.md", "content": "hello", "mode": "overwrite"})]}


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                cancel: asyncio.Event | None = None) -> Any:
    step = FRONT.pop(0) if FRONT else {"text": "ok"}
    calls = [] if tool_choice == "none" else step.get("calls", [])
    if step.get("text"):
        yield {"type": "delta", "text": step["text"]}
    yield {"type": "end", "finish_reason": "tool_calls" if calls else "stop", "tool_calls": calls, "usage": None}


def stub_reviewer(monkeypatch: pytest.MonkeyPatch, verdict: str, confidence: str = "high") -> None:
    async def review(cfg: Any, model: str, *, name: str, **kw: Any) -> dict[str, Any]:
        REVIEWED.append(name)
        return {"verdict": verdict, "reason": "stubbed", "confidence": confidence, "model": "stub", "ms": 1}
    monkeypatch.setattr(autoreview, "review", review)


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0, "autoTitle": False, "followUps": False, "learnStyle": False})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    FRONT.clear()
    REVIEWED.clear()
    monkeypatch.setattr(llm, "stream_chat", _fake)
    appmod.db.set_settings({"permissionMode": "manual", "workspaceRoots": [], "tools": {}, "permissionRules": {"allow": [], "ask": [], "deny": []}})


def wait(pred: Callable[[], Any], what: str, timeout: float = 10.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def start_desk() -> tuple[str, str]:
    cid = appmod.convos.create(None, "Autonomous", "test-model")["id"]
    did = client.post("/cowork/desks", json={"conversation_id": cid, "autonomy": "ask", "brief": "go", "start": False}).json()["desk"]["id"]
    assert client.post(f"/cowork/desks/{did}/message", json={"content": "go"}).status_code == 200
    return cid, did


def cards(did: str) -> list[dict[str, Any]]:
    return client.get(f"/approvals?desk_id={did}").json()


def tool_calls(cid: str, did: str, name: str) -> list[dict[str, Any]]:
    """The `tool_call` events for `name` across the desk's runs, replayed from the tape."""
    out: list[dict[str, Any]] = []
    for r in appmod.run_store.list(desk_id=did, statuses=None):
        text = client.get(f"/conversations/{cid}/stream?since=0&run_id={r['run_id']}").text
        for block in text.split("\n\n"):
            lines = block.split("\n")
            if "event: tool_call" in lines:
                data = json.loads("".join(ln[5:].strip() for ln in lines if ln.startswith("data:")))
                if data["name"] == name:
                    out.append(data)
    return out


def settled(cid: str) -> None:
    wait(lambda: not appmod.bus.live(cid), "the desk run to end")


def with_tool_mode(mode: str) -> None:
    appmod.db.set_settings({"tools": {"desk_write_file": mode}})


def test_auto_mode_sends_the_call_to_the_reviewer_and_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    appmod.db.set_settings({"permissionMode": "auto"})  # the tool keeps its default mode: an explicit 'on' skips the reviewer
    stub_reviewer(monkeypatch, "allow")
    FRONT[:] = [write("w1"), {"text": "Written."}]
    cid, did = start_desk()
    settled(cid)
    assert REVIEWED == ["desk_write_file"]
    [ev] = tool_calls(cid, did, "desk_write_file")
    assert ev["needs_approval"] is False and ev["forced"] is False
    assert cards(did) == []


def test_auto_mode_reviewer_ask_is_an_ordinary_card(monkeypatch: pytest.MonkeyPatch) -> None:
    appmod.db.set_settings({"permissionMode": "auto"})  # the tool keeps its default mode: an explicit 'on' skips the reviewer
    stub_reviewer(monkeypatch, "ask")
    FRONT[:] = [write("w1"), {"text": "ok"}]
    cid, did = start_desk()
    row = wait(lambda: cards(did), "a card")[0]
    assert REVIEWED == ["desk_write_file"] and row["tool"] == "desk_write_file" and not row["forced"]
    client.post(f"/approvals/{row['call_id']}", json={"decision": "deny"})
    settled(cid)  # the tape of a live run would block, so read it once the run has ended
    [ev] = tool_calls(cid, did, "desk_write_file")
    assert ev["needs_approval"] is True and ev["forced"] is False


def test_manual_mode_card_with_always_chat_lifts_the_next_identical_call() -> None:
    with_tool_mode("ask")
    FRONT[:] = [write("w1"), write("w2"), {"text": "Written twice."}]
    cid, did = start_desk()
    row = wait(lambda: cards(did), "the first card")[0]
    assert not row["forced"]
    client.post(f"/approvals/{row['call_id']}", json={"decision": "always_chat"})
    settled(cid)
    evs = tool_calls(cid, did, "desk_write_file")
    assert [e["needs_approval"] for e in evs] == [True, False], evs
    assert cards(did) == []


def test_manual_mode_allow_rule_lifts_the_card() -> None:
    with_tool_mode("ask")
    appmod.db.set_settings({"permissionRules": {"allow": ["desk_write_file"], "ask": [], "deny": []}})
    FRONT[:] = [write("w1"), {"text": "Written."}]
    cid, did = start_desk()
    settled(cid)
    [ev] = tool_calls(cid, did, "desk_write_file")
    assert ev["needs_approval"] is False and ev["forced"] is False
    assert cards(did) == []


def test_allow_all_still_cards_a_credential_file_read(tmp_path: Path) -> None:
    appmod.db.set_settings({"permissionMode": "allow_all"})
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=abc\n")
    FRONT[:] = [{"calls": [call("r1", "read_local_file", {"path": str(secret)})]}, {"text": "ok"}]
    cid, did = start_desk()
    row = wait(lambda: cards(did), "a card")[0]
    assert row["tool"] == "read_local_file"
    client.post(f"/approvals/{row['call_id']}", json={"decision": "deny"})
    settled(cid)
