"""Chunk span routes: offsets slice back to the chunk text, unknown chunk 404. Offline.
Run: python backend/tests/test_chunk_span.py"""
from __future__ import annotations

import os
import re
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
# One long paragraph (split_text rejoins its sentences with \\n), an unbroken run (hard-cut mid-word),
# blank-line and space runs, and short ### peers under one parent (merged under a `## b` label the
# source does not contain).
LONG = " ".join(f"Sentence {i} talks about rivers  and   lakes." for i in range(120))
SPLIT = "# Long\n\n" + LONG + "\n\n# Run\n\n" + "x" * 3000 + "\n\n# Gaps\n\nfirst part\n\n\n\n\nsecond    part  here\n"
MERGED = "# H\n\n### a\n\nshort note about apples.\n\n### b\n\nshort note about pears.\n\n### c\n\nshort note about plums.\n"


def _bare(s: str) -> str:
    """Chunk text as it reads in the source: no merge labels, no whitespace."""
    return re.sub(r"\s+", "", "\n".join(ln for ln in s.split("\n") if not re.match(r"^## .+$", ln)))


def check_spans(base: str, rows: list, text: str) -> int:
    n = 0
    for r in rows:
        res = client.get(f"{base}/chunks/{r['id']}")
        assert res.status_code == 200, res.text
        j = res.json()
        assert j["text"] == r["text"]
        assert j["start"] >= 0, f"chunk not located: {r['text'][:80]!r}"
        got, want = re.sub(r"\s+", "", text[j["start"]:j["end"]]), _bare(j["text"])
        if got != want:  # a merge across heading lines: anchored on the start of its first part, end of its last
            parts = [_bare(p) for p in re.split(r"(?m)^## .+$", j["text"]) if p.strip()]
            assert len(parts) > 1 and got.startswith(parts[0][:40]) and got.endswith(parts[-1][-40:]), (got, want)
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


def test_split_and_merged_chunks_are_located() -> None:
    for text in (SPLIT, MERGED):
        d = documents.create(None, "s.md", "text/markdown", len(text), "", text)
        with db.tx() as c:
            rows = c.execute("SELECT id, text FROM chunks WHERE document_id=? ORDER BY idx", (d["id"],)).fetchall()
        assert check_spans(f"/documents/{d['id']}", rows, text) == len(rows)
        doc = docs.create("S", text)
        with db.tx() as c:
            rows = c.execute("SELECT id, text FROM doc_chunks WHERE doc_id=? ORDER BY idx", (doc["id"],)).fetchall()
        assert check_spans(f"/docs/{doc['id']}", rows, text) == len(rows)
    # the cases this test exists for really are produced
    with db.tx() as c:
        texts = [r[0] for r in c.execute("SELECT text FROM doc_chunks").fetchall()]
    assert any("## b" in t for t in texts), texts
    assert any(t not in SPLIT and "rivers" in t for t in texts)


if __name__ == "__main__":
    test_spans()
    test_split_and_merged_chunks_are_located()
    print("test_chunk_span: ok")
