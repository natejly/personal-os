"""Deleting a chat or project stops its live run; a trashed chat is not learned from; message delete is scoped.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_delete_stops_run.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="deletestops-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
bus = appmod.bus
CHUNKS = [f"w{i} " for i in range(40)]
ROUNDS: list[Any] = []
SUBMITTED: list[Any] = []
STYLED: list[Any] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    if ROUNDS:
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": ROUNDS.pop(0), "usage": None}
        return
    for chunk in CHUNKS:
        await asyncio.sleep(0.05)
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real, real_submit, real_style = llm.stream_chat, appmod.learner.submit, appmod.learn_style_from_exchange
    llm.stream_chat = _scripted
    appmod.learner.submit = lambda job: SUBMITTED.append(job)  # type: ignore[method-assign]

    async def _style(*a: Any, **k: Any) -> Any:
        STYLED.append(1)
        return None

    appmod.learn_style_from_exchange = _style  # type: ignore[assignment]
    with client:
        client.put("/settings", json={"autoLearn": True, "learnStyle": True, "baseUrl": ""})
        yield
    llm.stream_chat, appmod.learner.submit, appmod.learn_style_from_exchange = real, real_submit, real_style


@pytest.fixture(autouse=True)
def _reset():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    SUBMITTED.clear()
    STYLED.clear()
    yield


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def begin(project_id: str | None = None) -> tuple[str, str]:
    cid = j("POST", "/conversations", {"project_id": project_id} if project_id else {})["id"]
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "write me a long, careful paragraph about gardening"})["run_id"]
    wait_until(lambda: any(e == "delta" for _, e, _ in store.events(rid)), "the first delta")
    return cid, rid


def ended(rid: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, f"run {rid} to end", 8)


def test_delete_mid_reply_stops_the_run_and_restores_with_the_partial() -> None:
    cid, rid = begin()
    res = j("DELETE", f"/conversations/{cid}")
    assert res == {"ok": True, "stopped": True}
    ended(rid)
    done = [d for _, e, d in store.events(rid) if e == "done"][-1]
    assert done["stopped"] is True
    time.sleep(0.3)  # the tail has run by now
    assert not SUBMITTED and not STYLED, "nothing learned from a trashed chat"
    assert not any(c["id"] == cid for c in j("GET", "/conversations"))
    j("POST", f"/trash/conversation/{cid}/restore")
    msgs = j("GET", f"/conversations/{cid}")["messages"]
    text = msgs[-1]["content"]
    assert msgs[-1]["role"] == "assistant" and text and len(text) < len("".join(CHUNKS).strip()), "the partial reply came back"
    assert j("DELETE", f"/conversations/{cid}")["stopped"] is False  # idle now
    assert j("DELETE", f"/conversations/{cid}")["ok"] is True  # already trashed: still 200


def test_an_idle_chat_deletes_with_stopped_false() -> None:
    cid = j("POST", "/conversations", {})["id"]
    assert j("DELETE", f"/conversations/{cid}") == {"ok": True, "stopped": False}


def test_delete_project_stops_its_runs_and_leaves_others() -> None:
    pid = j("POST", "/projects", {"name": "to delete"})["id"]
    inside, rid_in = begin(pid)
    outside, rid_out = begin()
    res = j("DELETE", f"/projects/{pid}")
    assert res["ok"] is True and res["stopped"] == 1
    ended(rid_in)
    assert store.get(rid_out)["status"] == "running", "an unrelated chat keeps replying"
    j("POST", f"/conversations/{outside}/stop")
    ended(rid_out)


def test_delete_message_is_scoped_and_refused_while_answering() -> None:
    cid = j("POST", "/conversations", {})["id"]
    other = j("POST", "/conversations", {})["id"]
    mid = appmod.convos.add_message(cid, "user", "hello")["id"]
    foreign = appmod.convos.add_message(other, "user", "not yours")["id"]
    j("DELETE", f"/conversations/{cid}/messages/{foreign}", expect=404)
    j("DELETE", f"/conversations/{cid}/messages/nope", expect=404)
    j("DELETE", f"/conversations/missing/messages/{mid}", expect=404)
    assert any(m["id"] == foreign for m in j("GET", f"/conversations/{other}")["messages"]), "the foreign row survives"
    live, rid = begin()
    r = client.request("DELETE", f"/conversations/{live}/messages/{mid}")
    assert r.status_code == 409 and r.json()["detail"]["run_id"] == rid
    j("POST", f"/conversations/{live}/stop")
    ended(rid)
    j("DELETE", f"/conversations/{cid}/messages/{mid}")
    assert not j("GET", f"/conversations/{cid}")["messages"]


def test_pending_approvals_of_a_trashed_chat_are_hidden_until_restore() -> None:
    cid = j("POST", "/conversations", {})["id"]
    store.open_approval(f"hidden:{cid}", None, "todo_add", {"title": "x"}, conversation_id=cid, message_id=None)
    ids = lambda: {a["call_id"] for a in j("GET", "/approvals")}  # noqa: E731
    inbox = lambda: {a["call_id"] for a in j("GET", "/inbox")["needs_you"]["approvals"]}  # noqa: E731
    assert f"hidden:{cid}" in ids() and f"hidden:{cid}" in inbox()
    j("DELETE", f"/conversations/{cid}")
    assert f"hidden:{cid}" not in ids() and f"hidden:{cid}" not in inbox()
    j("POST", f"/trash/conversation/{cid}/restore")
    assert f"hidden:{cid}" in ids()


def test_delete_while_awaiting_approval_denies_it_by_stop() -> None:
    ROUNDS.append([{"id": "c1", "name": "todo_add", "arguments": json.dumps({"title": "never"})}])
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"todo_add": "ask"}}})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "add it"})["run_id"]
    a = wait_until(lambda: [a for a in j("GET", "/approvals") if a["run_id"] == rid], "the approval row")[0]
    assert j("DELETE", f"/conversations/{cid}")["stopped"] is True
    ended(rid)
    row = store.approval(a["call_id"])
    assert row["status"] == "denied" and row["decided_by"] == "stop"
    assert not store.executed(rid), "the tool never ran"
    assert not [t for t in appmod.todos.list("__all__") if t["title"] == "never"]


def test_learn_worker_drops_a_job_for_a_trashed_chat() -> None:
    from personal_os import learn as learn_mod

    ran: list[Any] = []

    async def fake(**kw: Any) -> Any:
        ran.append(kw)
        return {}

    real = learn_mod.learn_from_exchange
    learn_mod.learn_from_exchange = fake  # type: ignore[assignment]
    try:
        w = learn_mod.LearnWorker(memories=appmod.memories, graph=appmod.graph, set_trace=lambda *a: None,
                                  publish=lambda *a: None, alive=lambda cid: False)
        job = learn_mod.LearnJob(conversation_id="gone", message_id="m", project_id=None, user_text="u", assistant_text="a",
                                 model="m", settings={}, spans=[])
        asyncio.run(w._run(job))
    finally:
        learn_mod.learn_from_exchange = real  # type: ignore[assignment]
    assert not ran
