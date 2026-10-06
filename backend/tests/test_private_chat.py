"""A private chat (set only at creation) reads and writes nothing that carries over to other chats."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="private-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
PROSE = ("Thanks for the note about the quarterly offsite. I think we should keep the agenda short, leave the "
         "afternoon open for walks, and make sure everyone has the travel details a week ahead of time. I would also like us to "
         "book the same caterer as last spring, since everyone enjoyed the food and the staff were lovely to work with.")


@pytest.fixture()
def api(monkeypatch):
    seen: dict = {"systems": [], "tools": [], "learn": [], "style": 0}

    async def stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
        seen["systems"].append(" ".join(str(m.get("content")) for m in messages if m["role"] == "system"))
        seen["tools"].append({t["function"]["name"] for t in tools or []})
        yield {"type": "delta", "text": "zebrapuzzle reply"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    async def style(**kw):
        seen["style"] += 1
        return None

    monkeypatch.setattr(llm, "stream_chat", stream)
    monkeypatch.setattr(appmod.learner, "submit", lambda job: seen["learn"].append(job))
    monkeypatch.setattr(appmod, "learn_style_from_exchange", style)
    with client:
        client.put("/settings", json={"autoLearn": True, "learnStyle": True, "autoTitle": False, "baseUrl": ""})
        appmod.memories.create(None, "Zebra crossing allergy", kind="fact", pinned=True)
        yield seen
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})


def _run(cid: str) -> None:
    assert client.post(f"/conversations/{cid}/chat", json={"content": PROSE}).status_code == 200
    deadline = time.time() + 15
    while time.time() < deadline and any(r["conversation_id"] == cid for r in client.get("/runs").json()):
        time.sleep(0.02)


def test_private_chat_learns_nothing_injects_nothing_and_is_not_searchable(api) -> None:
    plain = client.post("/conversations", json={}).json()["id"]
    _run(plain)
    assert len(api["learn"]) == 1 and api["style"] == 1
    assert "Zebra crossing allergy" in api["systems"][-1]
    assert "save_memory" in api["tools"][-1]

    priv = client.post("/conversations", json={"private": True}).json()
    s = priv["settings"]
    assert s["private"] is True and not (s["useMemory"] or s["useGraph"] or s["useStyle"] or s["autoLearn"])
    _run(priv["id"])
    assert len(api["learn"]) == 1, "no LearnJob for a private chat"
    assert api["style"] == 1, "no style sample banked from a private chat"
    assert "Zebra crossing allergy" not in api["systems"][-1]
    assert not api["tools"][-1] & {"save_memory", "search_memory", "graph_add", "graph_search", "save_writing_sample", "writing_style"}

    hits = {h["id"] for h in client.get("/conversations/search", params={"q": "zebrapuzzle"}).json()}
    assert plain in hits and priv["id"] not in hits
    assert priv["id"] in {c["id"] for c in client.get("/conversations").json()}, "still listed, so it can be found and trashed"


def test_patch_cannot_reenable_memory_or_change_private(api) -> None:
    priv = client.post("/conversations", json={"private": True}).json()["id"]
    r = client.patch(f"/conversations/{priv}", json={"settings": {"useMemory": True, "autoLearn": True, "useStyle": True,
                                                                  "useGraph": True, "private": False, "effort": "high"}})
    s = r.json()["settings"]
    assert s["private"] is True and s["effort"] == "high"
    assert not (s["useMemory"] or s["useGraph"] or s["useStyle"] or s["autoLearn"])

    plain = client.post("/conversations", json={}).json()["id"]
    s = client.patch(f"/conversations/{plain}", json={"settings": {"private": True}}).json()["settings"]
    assert not s.get("private") and s["useMemory"] is True
