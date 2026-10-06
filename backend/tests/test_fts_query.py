"""BM25 terms keep non-Latin words, acronyms and digit codes; CJK words inside an unspaced run are found by a LIKE scan.
Run: PYTHONPATH=backend python -m pytest backend/tests/test_fts_query.py"""
from __future__ import annotations

import tempfile

from personal_os.db import Database
from personal_os.docs import Docs
from personal_os.repos import Documents, Memories, fts_query


def terms(text: str) -> list[str]:
    return [t.strip('"') for t in fts_query(text).split(" OR ")] if fts_query(text) else []


def test_terms() -> None:
    assert terms("café menu") == ["café", "menu"]
    assert terms("Q3 AI plan") == ["q3", "ai", "plan"]
    assert terms("the of") == ["the"]  # "of" stays dropped, as before
    assert terms("Zürich") == ["zürich"]
    assert terms("東京都の会議") == ["東京都の会議"]
    assert fts_query("lite", prefix=True) == '"lite"*'


def _db() -> Database:
    return Database(tempfile.mkdtemp(prefix="ftsq-"))


def test_documents_unicode_and_cjk() -> None:
    db = _db()
    docs = Documents(db)
    z = docs.create(None, "office.txt", "text/plain", 10, "", "Die Adresse ist Zürich Büro, dritter Stock.")
    jp = docs.create(None, "meeting.txt", "text/plain", 10, "", "東京都の会議は月曜日です")
    docs.create(None, "other.txt", "text/plain", 10, "", "Unrelated grocery list with apples.")
    assert [h["document_id"] for h in docs.search(None, "Zürich")] == [z["id"]]
    assert [h["document_id"] for h in docs.search(None, "Q3 AI")] == []
    # 京都 sits inside the indexed run 東京都の会議, which FTS treats as one token: the LIKE scan finds it.
    assert [h["document_id"] for h in docs.search(None, "京都")] == [jp["id"]]
    assert [h["document_id"] for h in docs.search(None, "会議 月曜日")] == [jp["id"]]
    assert docs.search(None, "大阪") == []


def test_docs_and_memories_cjk() -> None:
    db = _db()
    d = Docs(db).create(title="Notes", content="東京都の会議は月曜日です")
    assert [h["doc_id"] for h in Docs(db).chunk_search("京都")] == [d["id"]]
    mem = Memories(db)
    m = mem.create(None, "東京都の会議が好き")
    for i in range(45):
        mem.create(None, f"filler fact number {i}")
    assert m["id"] in [x["id"] for x in mem.for_context(None, "京都", limit=20)]
    assert m["id"] not in [x["id"] for x in mem.for_context(None, "大阪", limit=20)]
