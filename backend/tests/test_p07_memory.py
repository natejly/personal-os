"""`learn: false` keeps a chat out of auto-learn (it stays in history), and forget-learned removes only that chat's rows.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p07_memory.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="p07test-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as A  # noqa: E402

client = TestClient(A.app, headers={"X-Personal-OS-Token": A.AUTH_TOKEN})


def _chat(**kw: object) -> dict:
    return A.convos.create(None, "t", "m", **kw)


def test_learn_false_turns_auto_learn_off_but_keeps_the_chat_listed() -> None:
    c = _chat()
    assert c["settings"]["autoLearn"] is True and A.learner._alive(c["id"])
    r = client.patch(f"/conversations/{c['id']}", json={"settings": {"learn": False}})
    assert r.status_code == 200 and r.json()["settings"]["autoLearn"] is False
    assert not A.learner._alive(c["id"])  # a queued job for it is dropped
    assert any(x["id"] == c["id"] for x in A.convos.list(None))
    assert client.post(f"/conversations/{c['id']}/skills/induce").json()["candidate"] is None


def test_forget_learned_removes_this_chats_rows_only() -> None:
    a, b = _chat(), _chat()
    ma = A.convos.add_message(a["id"], "assistant", "x")
    mb = A.convos.add_message(b["id"], "assistant", "y")
    mem_a = A.memories.create(None, "likes tea", provenance={"conversation_id": a["id"], "message_id": ma["id"]})
    mem_b = A.memories.create(None, "likes coffee", provenance={"conversation_id": b["id"], "message_id": mb["id"]})
    n1, n2 = A.graph.upsert_node(None, "Tea"), A.graph.upsert_node(None, "Cup")
    e_a = A.graph.upsert_edge(None, n1["id"], n2["id"], "in", source_message_id=ma["id"])
    e_b = A.graph.upsert_edge(None, n2["id"], n1["id"], "holds", source_message_id=mb["id"])
    s_a = A.skills.propose("S a", "d", "p", conversation_id=a["id"])
    s_b = A.skills.propose("S b", "d", "p", conversation_id=b["id"])
    out = client.post(f"/conversations/{a['id']}/forget-learned").json()
    assert out == {"memories": 1, "skills": 1, "edges": 1}
    assert A.memories.get(mem_a["id"]) is None and A.memories.get(mem_b["id"])
    assert client.post(f"/trash/memory/{mem_a['id']}/restore").status_code == 200  # trash-backed
    assert A.skills.get(s_a["id"]) is None and A.skills.get(s_b["id"])
    live = {e["id"] for e in A.graph.get(None)["edges"]}
    assert e_a["id"] not in live and e_b["id"] in live
    assert client.post("/conversations/nope/forget-learned").status_code == 404
