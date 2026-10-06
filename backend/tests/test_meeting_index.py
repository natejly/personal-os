"""Semantic meeting search: by-meaning chunk hits fused with FTS, FTS-only when off or down."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import meetings  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import EmbedError, Embedder  # noqa: E402
from personal_os.meeting_index import MeetingIndex  # noqa: E402

SYN = {"price": "cost", "pricing": "cost", "budget": "cost"}
ON = {"embeddingModel": "fake", "meetingEmbeddings": True}


def _vec(text: str) -> list[float]:
    v = [0.0] * 509
    for w in text.lower().replace(".", " ").replace("?", " ").split():
        w = SYN.get(w, w)
        v[sum(map(ord, w)) % 509] += 1.0
    return v


class Fake:
    fail = False

    async def __call__(self, settings, texts, model):
        if self.fail:
            raise EmbedError("down")
        return [_vec(t) for t in texts]


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        repo = meetings.Meetings(db)
        fake = Fake()
        idx = MeetingIndex(db, repo, Embedder(fake))
        m = repo.create("Weekly sync")
        repo.finalize(m["id"], "we need to settle the pricing before launch", status="ready")
        o = repo.create("Standup")
        repo.finalize(o["id"], "gardening notes and lunch plans", status="ready")
        yield repo, idx, fake, m["id"]


def test_meaning_match_with_embeddings_and_fts_only_without(env) -> None:
    repo, idx, fake, mid = env
    q = "budget"
    assert repo.search(q) == []  # no shared literal word with the transcript
    assert asyncio.run(idx.index(ON)) == 2
    hits = asyncio.run(idx.search(ON, q))
    assert hits[0]["meeting_id"] == mid and hits[0]["field"] == "semantic" and "pricing" in hits[0]["snippet"]
    assert asyncio.run(idx.index(ON)) == 0  # idempotent
    # Off (the default) leaves plain FTS behaviour; so does a route that is down.
    assert asyncio.run(idx.search({"embeddingModel": "fake"}, q)) == []
    fake.fail = True
    assert asyncio.run(idx.search(ON, "weekly")) == repo.search("weekly")
    assert asyncio.run(idx.search(ON, q)) == []


def test_edit_reindexes_and_delete_cascades(env) -> None:
    repo, idx, fake, mid = env
    asyncio.run(idx.index(ON))
    repo.patch(mid, {"notes": "something new entirely"})
    assert asyncio.run(idx.index(ON)) == 1
    repo.delete(mid)
    with idx.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM meeting_vectors WHERE meeting_id=?", (mid,)).fetchone()[0] == 0


def test_semantic_respects_project_scope(env) -> None:
    repo, idx, fake, mid = env
    asyncio.run(idx.index(ON))
    pid = "p-other"
    with idx.db.tx() as c:
        c.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", (pid, "P", 0))
        c.execute("UPDATE meetings SET project_id=? WHERE id=?", (pid, mid))
    assert asyncio.run(idx.search(ON, "budget", pid))[0]["meeting_id"] == mid
    assert asyncio.run(idx.search(ON, "budget", "p-none")) == []
