"""doc_delete trashes a file (undoable), asks by default, respects project isolation. Run: python backend/tests/test_doc_delete.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docdelete-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs, toolbox, trash  # noqa: E402
from personal_os.tools import PROMPT_WRITES  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
pa = client.post("/projects", json={"name": "A"}).json()["id"]
client.put(f"/projects/{pa}", json={"memory_mode": "isolated"})


def call(name, args, pid=None):
    return asyncio.new_event_loop().run_until_complete(toolbox.call(name, args, {"project_id": pid}))


assert toolbox.specs["doc_delete"].default == "ask" and "doc_delete" in PROMPT_WRITES
mine = docs.create("Groceries", "milk", None)
out = call("doc_delete", {"doc": "Groceries"})
assert out["deleted"] == "Groceries", out
assert "Groceries" not in {d["title"] for d in call("doc_list", {})}
assert any(i["id"] == mine["id"] for i in trash.list()["groups"]["docs"]), trash.list()
assert "error" in call("doc_delete", {"doc": "nope"})
other = docs.create("Secret", "x", None)
assert "error" in call("doc_delete", {"doc": "Secret"}, pa), "isolated project must not reach other docs"
assert docs.get(other["id"])
print("ok")
