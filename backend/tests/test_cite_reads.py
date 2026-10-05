"""Whole-source reads are citable: pinned files, read_document slices, doc_read and meeting_read line spans each get
one [n] in the reply's ledger, by character offsets into the text the viewer loads."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="citereads-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import tools  # noqa: E402
from personal_os.context import context_taints  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, docs, documents, meeting_store, toolbox  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
run = asyncio.new_event_loop().run_until_complete
LONG = "".join(f"Sentence number {i} of the handbook. " for i in range(100))
book = documents.create(None, "handbook.txt", "text/plain", len(LONG), "", LONG)
documents.create(None, "lease.txt", "text/plain", 5, "", "The notice period is thirty days.")
pinned = documents.create(None, "rules.txt", "text/plain", 5, "", "\n  House rules: no notice period applies to guests.  \n")
documents.set_pinned(pinned["id"], True)


def test_pinned_doc_is_numbered_before_the_excerpts() -> None:
    used = client.post("/context/preview", json={"query": "notice period"}).json()
    first, rest = used["chunks"][0], used["chunks"][1:]
    assert first["n"] == 1 and first["kind"] == "range" and first["document_id"] == pinned["id"]
    raw = "\n  House rules: no notice period applies to guests.  \n"
    assert raw[first["start"]:first["end"]] == raw.strip()
    assert "### [1] rules.txt" in used["system_prompt"]
    assert rest and rest[0]["n"] == 2 and rest[0]["name"] == "lease.txt"
    assert "### [2] lease.txt" in used["system_prompt"]


def test_pinned_only_context_does_not_taint_the_turn() -> None:
    # Pinned files never tainted a turn; their range citations must not count as uploaded-file excerpts.
    used = client.post("/context/preview", json={"query": "xylophone quasar"}).json()
    assert used["chunks"] and all(c["kind"] == "range" for c in used["chunks"])
    assert context_taints(used) == []
    used = client.post("/context/preview", json={"query": "notice period"}).json()
    assert context_taints(used) == ["chunks"]


def test_read_document_cites_its_slice_once() -> None:
    ctx: dict = {"project_id": None, "citations": []}
    out = run(toolbox.call("read_document", {"document_id": book["id"], "offset": 100, "length": 500}, ctx))
    ref = ctx["citations"][0]
    assert out["cite"] == 1 and (ref["start"], ref["end"]) == (100, 600) and ref["text"] == LONG[100:500]
    assert out["text"] == LONG[100:600]
    again = run(toolbox.call("read_document", {"document_id": book["id"], "offset": 100, "length": 500}, ctx))
    assert again["cite"] == 1 and len(ctx["citations"]) == 1
    other = run(toolbox.call("read_document", {"document_id": book["id"], "offset": 600, "length": 500}, ctx))
    assert other["cite"] == 2


def test_doc_read_cites_the_lines_as_char_offsets() -> None:
    body = "\n".join(f"line {i}" for i in range(1, 31))
    d = docs.create("Plan", body)
    ctx: dict = {"project_id": None, "citations": []}
    out = run(toolbox.call("doc_read", {"doc": d["id"], "from_line": 10, "to_line": 20}, ctx))
    ref = ctx["citations"][0]
    assert out["cite"] == 1 and ref["source"] == "doc" and ref["doc_id"] == d["id"]
    assert body[ref["start"]:ref["end"]] == "\n".join(f"line {i}" for i in range(10, 21))


def test_meeting_read_cites_its_part() -> None:
    m = meeting_store.create("Standup")
    meeting_store.patch(m["id"], {"notes": "alpha\nbeta\ngamma\ndelta"})
    ctx: dict = {"project_id": None, "citations": []}
    out = run(toolbox.call("meeting_read", {"meeting": m["id"], "part": "notes", "from_line": 2, "to_line": 3}, ctx))
    ref = ctx["citations"][0]
    assert out["cite"] == 1 and ref["source"] == "meeting" and ref["meeting_id"] == m["id"] and ref["part"] == "notes"
    assert "alpha\nbeta\ngamma\ndelta"[ref["start"]:ref["end"]] == "beta\ngamma"


def test_line_span() -> None:
    assert tools.line_span("a\r\nbb\nccc", 2, 3) == (3, 9)
    assert tools.line_span("a\nb", 5, 9) == (3, 3)


def test_activity_block_of_app_names_only_does_not_taint():
    """The monitor ships on. A block that is just app names (no Accessibility, so no titles, profile or summaries)
    is the user's own data; one that carries a window title or a distilled summary still taints the turn."""
    assert context_taints({"activity": "In Terminal for 3m.", "activity_foreign": False}) == []
    assert context_taints({"activity": "In Safari - Some page title for 3m.", "activity_foreign": True}) == ["activity"]
    assert context_taints({"activity": "legacy caller without the flag"}) == ["activity"]
