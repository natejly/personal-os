"""Follow-up chips: up to three next questions saved on the reply and announced on /events, off the run.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_followups.py
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

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="followtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import followups, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.app import events as topic  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
STATE: dict[str, Any] = {"kinds": [], "reply": '["a?","b?","c?"]', "hold": False}


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                  effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    yield {"type": "delta", "text": "hello"}
    if STATE["hold"]:  # a reply the test stops mid-way
        for _ in range(500):
            if cancel is not None and cancel.is_set():
                break
            await asyncio.sleep(0.01)
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _complete(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "learn", **kw: Any) -> str:
    STATE["kinds"].append(kind)
    return STATE["reply"]


@pytest.fixture(autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    saved = (llm.stream_chat, llm.complete)
    llm.stream_chat, llm.complete = _stream, _complete  # type: ignore[assignment]
    STATE.update(kinds=[], reply='["a?","b?","c?"]', hold=False)
    with client:
        client.put("/settings", json={"autoLearn": False, "autoTitle": False, "followUps": True, "baseUrl": ""})
        yield
    llm.stream_chat, llm.complete = saved  # type: ignore[assignment]


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 10.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def last_assistant(cid: str) -> dict[str, Any]:
    msgs = client.get(f"/conversations/{cid}").json()["messages"]
    return [m for m in msgs if m["role"] == "assistant"][-1]


def followup_events(cid: str) -> list[dict[str, Any]]:
    async def pull() -> list[str]:
        gen = topic.subscribe(0)
        blocks: list[str] = []
        try:
            while True:
                blocks.append(await asyncio.wait_for(gen.__anext__(), timeout=0.5))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            await gen.aclose()
        return blocks

    out = []
    for block in asyncio.run(pull()):
        ev, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev == "followups" and json.loads(data)["conversation_id"] == cid:
            out.append(json.loads(data))
    return out


def chat(content: str = "hi") -> str:
    cid = client.post("/conversations", json={}).json()["id"]
    assert client.post(f"/conversations/{cid}/chat", json={"content": content}).status_code == 200
    return cid


def test_parse_tolerates_wrapping_and_junk() -> None:
    assert followups.parse('<think>x</think>```json\n["a?", "A?", " b? ", 3, "' + "x" * 200 + '"]\n```') == ["a?", "b?", "3"]
    assert followups.parse("not json") == [] and followups.parse('{"a": 1}') == []
    assert len(followups.parse('["1","2","3","4"]')) == 3


def test_a_finished_reply_carries_three_followups_and_announces_them() -> None:
    cid = chat()
    wait_until(lambda: last_assistant(cid).get("followups"), "the follow-ups")
    m = last_assistant(cid)
    assert m["followups"] == ["a?", "b?", "c?"]
    assert followup_events(cid)[-1] == {"conversation_id": cid, "message_id": m["id"], "followups": ["a?", "b?", "c?"]}
    assert "followups" in STATE["kinds"]


def test_off_means_no_call_and_no_chips() -> None:
    client.put("/settings", json={"followUps": False})
    cid = chat()
    time.sleep(0.5)
    assert not last_assistant(cid).get("followups") and "followups" not in STATE["kinds"]


def test_a_stopped_reply_gets_none() -> None:
    STATE["hold"] = True
    cid = client.post("/conversations", json={}).json()["id"]
    client.post(f"/conversations/{cid}/chat", json={"content": "hi"})
    wait_until(lambda: any(r["conversation_id"] == cid for r in client.get("/runs").json()), "the run")
    time.sleep(0.2)
    client.post(f"/conversations/{cid}/stop")
    wait_until(lambda: not any(r["conversation_id"] == cid for r in client.get("/runs").json()), "the run to end")
    time.sleep(0.5)
    assert not last_assistant(cid).get("followups") and "followups" not in STATE["kinds"]
