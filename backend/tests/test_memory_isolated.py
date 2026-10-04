"""A project set to 'this project only' memory sees no personal rows and writes none; switching back restores them."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memisolated-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import (AUTH_TOKEN, app, docs, documents, graph, memories, skills,  # noqa: E402
                             style, toolbox)
from personal_os.context import build_context  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def call(name, args, pid):
    return run(toolbox.call(name, args, {"project_id": pid}))


def ctx(pid):
    project = client.get("/projects").json()
    project = next(p for p in project if p["id"] == pid)
    _, used = build_context(memories=memories, graph=graph, documents=documents, project=project, project_id=pid,
                            query="zebra Acme", settings={}, conv_settings={"draftMode": True, "useStyle": True},
                            global_system_prompt="", skills=skills, style=style, draft=True)
    return used


pid = client.post("/projects", json={"name": "Client", "memory_mode": "isolated"}).json()["id"]
assert client.get("/projects").json()[-1]["memory_mode"] == "isolated"
assert client.put(f"/projects/{pid}", json={"memory_mode": "bogus"}).status_code == 422

memories.create(None, "Personal zebra fact", pinned=True)
memories.create(pid, "Project zebra fact")
a, b = graph.upsert_node(None, "Acme"), graph.upsert_node(None, "Home")
graph.upsert_edge(None, a["id"], b["id"], "near")
graph.upsert_node(pid, "Acme")
documents.create(None, "personal.txt", "text/plain", 5, "", "zebra personal sentence")
documents.create(pid, "client.txt", "text/plain", 5, "", "zebra client sentence")
d = documents.create(None, "pinned.txt", "text/plain", 5, "", "pinned personal text")
documents.set_pinned(d["id"], True)
docs.create("Personal note", "zebra personal note", None)
docs.create("Client note", "zebra client note", pid)
sk = skills.propose("Personal way", "how", "1. do it", project_id=None)
skills.update(sk["id"], {"status": "approved"})
style.save_profile(None, {"summary": "Breezy personal voice", "enabled": True})


def personal_visible(pid) -> dict[str, bool]:
    used = ctx(pid)
    return {
        "memory": any(m["project_id"] is None for m in used["memories"]),
        "graph": any(n["label"] == "Home" for n in used["nodes"]),
        "chunks": any(h["name"] == "personal.txt" for h in used["chunks"]),
        "pinned": bool(used["pinned"]),
        "skills": bool(used["skills"]),
        "style": used["style"] is not None,
        "search_memory": "Personal zebra" in str(call("search_memory", {"query": "zebra"}, pid)),
        "list_documents": "personal.txt" in str(call("list_documents", {}, pid)),
        "search_documents": "personal" in str(call("search_documents", {"query": "zebra"}, pid)),
        "doc_list": "Personal note" in str(call("doc_list", {}, pid)),
        "doc_search": "Personal note" in str(call("doc_search", {"query": "zebra"}, pid)),
    }


iso = personal_visible(pid)
assert not any(iso.values()), iso
used = ctx(pid)
assert [m["content"] for m in used["memories"]] == ["Project zebra fact"], used["memories"]
assert any(h["name"] == "client.txt" for h in used["chunks"])

# Writes from an isolated project stay in it.
out = run(toolbox.specs["save_memory"].fn({"project_id": pid}, "User likes oolong", personal=True))
assert "error" in str(out).lower(), out
assert not [m for m in memories.list(None) if m["content"] == "User likes oolong"]
run(toolbox.specs["save_writing_sample"].fn({"project_id": pid}, "Hey all, quick update on the client launch plan for next week."))
assert style.samples(pid) and not style.samples(None)
mid = memories.list(pid, include_global=False)[0]["id"]
assert client.put(f"/memories/{mid}", json={"move_to_global": True}).status_code == 409
assert memories.get(mid)["project_id"] == pid

# Switching back to shared restores every personal source.
assert client.put(f"/projects/{pid}", json={"memory_mode": "shared"}).json()["memory_mode"] == "shared"
shared = personal_visible(pid)
assert all(shared.values()), shared
assert client.put(f"/memories/{mid}", json={"move_to_global": True}).json()["project_id"] is None
print("ok")
