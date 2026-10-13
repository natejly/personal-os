"""An id where a filename is wanted (and the reverse) resolves when exactly one thing matches; several list their ids."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="resolveref-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import tools  # noqa: E402
from personal_os.app import docs, documents, toolbox  # noqa: E402

run = asyncio.new_event_loop().run_until_complete
slides = documents.create(None, "14_object_detection.pdf", "application/pdf", 9, "", "anchor boxes and IoU")
documents.create(None, "week3_notes.txt", "text/plain", 5, "", "alpha")
documents.create(None, "week3_notes_old.txt", "text/plain", 5, "", "beta")
plan = docs.create("Launch plan", "step one\nstep two")


def test_resolve_ref_unit() -> None:
    rows = [{"id": "ab12", "name": "Report.PDF"}, {"id": "cd34", "name": "Report v2.pdf"}]
    assert tools.resolve_ref(rows, "doc_ab12", "id", "name")[0] == rows[0]
    assert tools.resolve_ref(rows, "/tmp/report.pdf", "id", "name")[0] == rows[0]
    assert tools.resolve_ref(rows, "port", "id", "name") == (None, rows)
    assert tools.resolve_ref(rows, "nothing", "id", "name") == (None, [])


def test_read_document_takes_a_filename() -> None:
    out = run(toolbox.call("read_document", {"document_id": "14_object_detection.pdf"}, {"project_id": None}))
    assert out["name"] == "14_object_detection.pdf" and "anchor boxes" in out["text"]
    assert run(toolbox.call("read_document", {"document_id": "doc_" + slides["id"]}, {"project_id": None}))["name"] == slides["name"]


def test_read_document_ambiguous_lists_ids() -> None:
    out = run(toolbox.call("read_document", {"document_id": "week3"}, {"project_id": None}))
    assert "matches 2" in out["error"] and slides["id"] not in out["expected"]
    assert all(len(p.split(" ")[0]) > 8 for p in out["expected"].removeprefix("one of: ").split("; "))


def test_read_document_given_a_files_doc_points_to_doc_read() -> None:
    out = run(toolbox.call("read_document", {"document_id": plan["id"]}, {"project_id": None}))
    assert out["try_instead"] == "doc_read with that id"


def test_doc_read_takes_a_path_and_an_upload_id_gets_a_pointer() -> None:
    out = run(toolbox.call("doc_read", {"doc": "Notes/Launch plan.md"}, {"project_id": None}))
    assert out["doc_id"] == plan["id"]
    miss = run(toolbox.call("doc_read", {"doc": slides["id"]}, {"project_id": None}))
    assert "read_document" in miss["hint"]
