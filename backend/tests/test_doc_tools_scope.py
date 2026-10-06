"""doc_list / doc_search respect the chat's project. Run: python backend/tests/test_doc_tools_scope.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="doctoolscope-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs, documents, toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
pa = client.post("/projects", json={"name": "A"}).json()["id"]
pb = client.post("/projects", json={"name": "B"}).json()["id"]
docs.create("Alpha only", "zebra alpha sentence", pa)
docs.create("Beta only", "zebra beta sentence", pb)
docs.create("Personal only", "zebra personal sentence", None)
for pid, name in ((pa, "alpha.txt"), (pb, "beta.txt"), (None, "personal.txt")):
    documents.create(pid, name, "text/plain", 5, "", f"zebra {name} sentence")


def call(name, args, pid):
    return asyncio.new_event_loop().run_until_complete(toolbox.call(name, args, {"project_id": pid}))


for pid, want in ((pa, {"Alpha only", "Personal only"}), (None, {"Personal only"})):
    assert {r["title"] for r in call("doc_list", {}, pid)} == want, pid
    assert {h["title"] for h in call("doc_search", {"query": "zebra"}, pid)["results"]} == want, pid
out = str(call("doc_search", {"query": "zebra"}, pa)) + str(call("doc_list", {}, pa))
assert "Beta" not in out and "beta sentence" not in out
sd = str(call("search_documents", {"query": "zebra"}, pa))
assert "alpha.txt" in sd and "personal.txt" in sd and "beta.txt" not in sd, sd
print("ok")
