"""Chunk span routes: offsets slice back to the chunk text, unknown chunk 404. Offline.
Run: python backend/tests/test_chunk_span.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="chunkspan-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, db, docs, documents  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
TEXT = "# Alpha\n\n" + "alpha paragraph about boats. " * 40 + "\n\n# Beta\n\n" + "beta paragraph about trains. " * 40


def check_spans(base: str, rows: list, text: str) -> int:
    n = 0
    for r in rows:
        res = client.get(f"{base}/chunks/{r['id']}")
        assert res.status_code == 200, res.text
        j = res.json()
        assert j["text"] == r["text"]
        if j["start"] >= 0:
            assert text[j["start"]:j["end"]] == j["text"]
            n += 1
    assert client.get(f"{base}/chunks/nope").status_code == 404
    return n


def test_spans() -> None:
    d = documents.create(None, "t.md", "text/markdown", len(TEXT), "", TEXT)
    with db.tx() as c:
        rows = c.execute("SELECT id, text FROM chunks WHERE document_id=? ORDER BY idx", (d["id"],)).fetchall()
    assert rows
    assert check_spans(f"/documents/{d['id']}", rows, TEXT) >= 1
    assert client.get("/documents/missing/chunks/x").status_code == 404

    doc = docs.create("Notes", TEXT)
    with db.tx() as c:
        rows = c.execute("SELECT id, text FROM doc_chunks WHERE doc_id=? ORDER BY idx", (doc["id"],)).fetchall()
    assert rows
    check_spans(f"/docs/{doc['id']}", rows, TEXT)


if __name__ == "__main__":
    test_spans()
    print("test_chunk_span: ok")
