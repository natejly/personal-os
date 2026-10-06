"""Excerpts are numbered once per reply: the prompt's 1..n, then search_documents continues from there.
Run: python backend/tests/test_doc_citations.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="doccite-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, documents, toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
documents.create(None, "lease.txt", "text/plain", 5, "", "The notice period is thirty days.")
documents.create(None, "pets.txt", "text/plain", 5, "", "Zebra pets are not allowed in the flat.")

# The prompt: excerpts carry [n], the block says how to cite, and context_used keeps n.
used = client.post("/context/preview", json={"query": "notice period"}).json()
assert used["chunks"] and used["chunks"][0]["n"] == 1, used["chunks"]
assert "### [1] lease.txt" in used["system_prompt"] and "[1] or [1][3]" in used["system_prompt"], used["system_prompt"]

# The tool: numbering continues after the prompt's excerpts and a passage keeps its number.
ctx = {"project_id": None, "citations": list(used["chunks"])}
run = asyncio.new_event_loop().run_until_complete
first = run(toolbox.call("search_documents", {"query": "zebra"}, ctx))["results"]
assert [r["cite"] for r in first] == [len(used["chunks"]) + 1], first
again = run(toolbox.call("search_documents", {"query": "zebra"}, ctx))["results"]
assert again[0]["cite"] == first[0]["cite"] and len(ctx["citations"]) == len(used["chunks"]) + 1
lease = run(toolbox.call("search_documents", {"query": "notice period"}, ctx))["results"]
assert lease[0]["cite"] == 1, lease  # already cited by the prompt
print("ok")
