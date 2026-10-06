"""useMemory off stops extraction; pinned memories ride in the always-on profile, not the relevance-gated hits."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memoff-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, learn, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.repos import Documents  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


async def _stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    yield {"type": "delta", "text": "hello"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setattr(llm, "stream_chat", _stream)
    submitted: list = []
    monkeypatch.setattr(appmod.learner, "submit", lambda job: submitted.append(job))
    with client:
        client.put("/settings", json={"autoLearn": True, "baseUrl": ""})
        yield submitted
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})


def _chat(settings: dict, submitted: list) -> int:
    cid = client.post("/conversations", json={}).json()["id"]
    r = client.patch(f"/conversations/{cid}", json={"settings": settings})
    assert r.status_code == 200, r.text
    assert client.post(f"/conversations/{cid}/chat", json={"content": "I like tea a lot"}).status_code == 200
    deadline = time.time() + 15
    while time.time() < deadline and any(r["conversation_id"] == cid for r in client.get("/runs").json()):
        time.sleep(0.02)
    return len(submitted)


def test_memory_off_skips_extraction_on_does_not(api) -> None:
    assert _chat({"useMemory": False}, api) == 0
    assert _chat({"useMemory": True}, api) == 1


def test_pinned_row_rides_in_the_profile_not_the_hits(monkeypatch) -> None:
    mem = appmod.memories
    pin = mem.create(None, "Zebra crossing allergy", kind="fact", pinned=True)
    for i in range(40):
        mem.create(None, f"tea preference number {i}", kind="fact")

    async def qv(cfg, q):
        return np.ones(4, dtype=np.float32)
    monkeypatch.setattr(appmod.memory_index, "query_vec", qv)
    monkeypatch.setattr(appmod.memory_index, "schedule", lambda cfg: None)
    cfg = {"embeddingModel": "x", "hybridRetrieval": True}
    hits = asyncio.run(appmod._memory_hits(None, "tea preference", cfg, {"useMemory": True}))
    assert hits and pin["id"] not in {m["id"] for m in hits}  # it matched nothing; hits are relevance-gated
    kw = dict(memories=mem, graph=appmod.graph, documents=Documents(appmod.db), project=None, project_id=None,
              query="tea preference", settings={}, conv_settings={}, global_system_prompt="sys")
    text, used = build_context(**kw, memory_hits=hits)
    assert "Zebra crossing allergy" in text.split("## Your standing preferences")[1].split("\n\n##")[0]
    assert [m["id"] for m in used["profile"]] == [pin["id"]]

    async def none(cfg, q):
        return None
    monkeypatch.setattr(appmod.memory_index, "query_vec", none)
    # No query vector: the lexical rankers still gate, so there are hits and not a failure.
    assert asyncio.run(appmod._memory_hits(None, "tea", cfg, {"useMemory": True}))
    assert asyncio.run(appmod._memory_hits(None, "tea", cfg, {"useMemory": False})) is None
    text, _ = build_context(**kw, memory_hits=None)
    assert "Zebra crossing allergy" in text

    async def fake_complete(settings, model, messages, kind="learn", **kw):
        return json.dumps({"updates": [{"id": "M1", "content": "changed"}]})
    monkeypatch.setattr(learn.llm, "complete", fake_complete)
    asyncio.run(learn.learn_from_exchange(settings=cfg, memories=mem, graph=appmod.graph, project_id=None,
                                          user_text="zebra", assistant_text="ok", model="m"))
    assert mem.get(pin["id"])["content"] == "Zebra crossing allergy"


# ---- a hand edit through PUT /memories/{id} keeps the previous version ----
def test_hand_edit_keeps_the_previous_version() -> None:
    mem = appmod.memories
    old = mem.create(None, "User walks the dog at 7", kind="fact")
    r = client.put(f"/memories/{old['id']}", json={"content": "User walks the dog at 8"})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != old["id"] and new["content"] == "User walks the dog at 8"
    o = mem.get(old["id"])
    assert o["invalid_at"] is not None and o["superseded_by"] == new["id"]
    assert [m["id"] for m in mem.history(new["id"])] == [old["id"], new["id"]]
    assert client.post(f"/memories/{old['id']}/restore").json()["content"] == "User walks the dog at 7"
    assert mem.get(new["id"])["invalid_at"] is not None


def test_hand_edit_of_a_pinned_row_stays_pinned_and_keeps_history() -> None:
    mem = appmod.memories
    old = mem.create(None, "User's badge number is on the fridge", pinned=True)
    new = client.put(f"/memories/{old['id']}", json={"content": "User's badge is in the drawer", "kind": "note"}).json()
    assert new["id"] != old["id"] and new["pinned"] and new["kind"] == "note"
    assert mem.get(old["id"])["superseded_by"] == new["id"]


def test_pin_only_or_same_text_edit_stays_in_place() -> None:
    mem = appmod.memories
    old = mem.create(None, "User reads before bed")
    assert client.put(f"/memories/{old['id']}", json={"pinned": True}).json()["id"] == old["id"]
    same = client.put(f"/memories/{old['id']}", json={"content": " User reads before bed "}).json()
    assert same["id"] == old["id"] and same["pinned"]
    assert [m["id"] for m in mem.history(old["id"])] == [old["id"]]
