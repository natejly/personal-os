"""The exact request of each model round is kept per reply, and old replies are pruned.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_request_log.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="reqlog-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, request_log  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SEEN: list[dict[str, Any]] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append({"model": model, "messages": [dict(m) for m in messages], "tools": tools})
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def test_reply_request_is_captured_exactly() -> None:
    real = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        with client:
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
            cid = client.post("/conversations", json={}).json()["id"]
            client.post(f"/conversations/{cid}/chat", json={"content": "hello there"})
            deadline = time.time() + 15
            msgs: list[dict[str, Any]] = []
            while time.time() < deadline:
                msgs = client.get(f"/conversations/{cid}").json()["messages"]
                if any(m["role"] == "assistant" and m["content"] for m in msgs):
                    break
                time.sleep(0.05)
            am = [m for m in msgs if m["role"] == "assistant"][-1]
            rounds = client.get(f"/messages/{am['id']}/requests").json()
            assert client.get("/messages/nope/requests").status_code == 404
    finally:
        llm.stream_chat = real
    sent = SEEN[0]
    assert rounds[0]["messages"] == sent["messages"]
    assert rounds[0]["tools"] == (sent["tools"] or [])
    assert rounds[0]["model"] == sent["model"] and rounds[0]["tokens"] > 0
    assert rounds[0]["messages"][-1]["role"] == "user" and "hello there" in str(rounds[0]["messages"][-1]["content"])


def test_old_replies_are_pruned() -> None:
    d = Path(tempfile.mkdtemp(prefix="reqprune-"))
    for i in range(request_log.KEEP + 5):
        request_log.record(d, f"m{i}", 1, "x", [{"role": "user", "content": "hi"}], None, "auto")
        os.utime(d / "requests" / f"m{i}.jsonl", (i, i))
    names = {p.stem for p in (d / "requests").glob("*.jsonl")}
    assert len(names) == request_log.KEEP and "m0" not in names and f"m{request_log.KEEP + 4}" in names
