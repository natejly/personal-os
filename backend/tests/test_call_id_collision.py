"""The approval wait never adopts a row it did not open.

A provider that restarts its tool-call ids each round gives round 2 the same approval id as round 1; the
insert is ON CONFLICT DO NOTHING, so what comes back is round 1's decided row. Run: PYTHONPATH=<repo>/backend
pytest backend/tests/test_call_id_collision.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="idcollision-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    if ROUNDS:
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": ROUNDS.pop(0), "usage": None}
        return
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real


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


def todo_call(title: str) -> list[dict[str, Any]]:
    return [{"id": "call_0", "name": "todo_add", "arguments": json.dumps({"title": title})}]


def test_an_old_decided_row_does_not_answer_a_new_call() -> None:
    llm.stream_chat = _scripted
    first, second = f"first {time.time()}", f"second {time.time()}"
    ROUNDS[:] = [todo_call(first), todo_call(second)]
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"todo_add": "ask"}}})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]
    a = wait_until(lambda: [a for a in j("GET", "/approvals") if a["run_id"] == rid], "round 1's card")[0]
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert wait_until(lambda: [t for t in appmod.todos.list("__all__") if t["title"] == first], "round 1 ran")
    # Round 2 reuses the id with different arguments. Past a full poll of the wait loop it must still be waiting.
    time.sleep(2.6)
    assert store.get(rid)["status"] == "awaiting_approval", "nobody answered round 2"
    assert not [t for t in appmod.todos.list("__all__") if t["title"] == second], "round 2 did not run unasked"
    j("POST", f"/conversations/{cid}/stop")
    wait_until(lambda: store.get(rid)["status"] not in ("running", "awaiting_approval"), "the run to end", 8)
    assert not [t for t in appmod.todos.list("__all__") if t["title"] == second]
