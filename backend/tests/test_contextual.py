"""Contextual chunk blurbs: off by default, written on embed-backfill, indexed and re-embedded. Offline (stub model).
Run: python backend/tests/test_contextual.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ctxtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, db, documents, retriever, settings  # noqa: E402
from personal_os.chunker import contextualize  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


calls: list[str] = []
mode = {"fail": False}


async def stub(settings_: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "") -> str:
    calls.append(messages[0]["content"])
    if mode["fail"]:
        raise RuntimeError("model down")
    return "Quarterly zeppelin audit findings."


retriever.complete = stub
S = {**settings(), "defaultModel": "m"}
check(S["contextualChunks"] is False, "off by default")
check(contextualize("T", "H", "x", "B") == "B\nT > H\nx" and contextualize("T", "H", "x") == "T > H\nx", "blurb prefixes ctx")

d = client.post("/documents", files={"file": ("notes.txt", b"Revenue rose three percent in the period.", "text/plain")}).json()
check(run(retriever.contextualize_pending(S)) == 0 and not calls, "off makes no calls")
S["contextualChunks"] = True
check(not documents.search(None, "zeppelin", limit=5), "term absent before")

mode["fail"] = True
check(run(retriever.contextualize_pending(S)) == 0, "LLM error writes nothing and does not raise")
check(not documents.search(None, "zeppelin", limit=5), "search unchanged after error")
mode["fail"] = False
n = len(calls)
check(run(retriever.contextualize_pending({**S, "defaultModel": ""})) == 0 and len(calls) == n, "no model is a no-op")

check(run(retriever.contextualize_pending(S)) == 1, "blurb written")
check(any(h["document_id"] == d["id"] for h in documents.search(None, "zeppelin", limit=5)), "BM25 matches blurb-only term")
with db.tx() as c:
    check(c.execute("SELECT blurb FROM chunks").fetchone()["blurb"].startswith("Quarterly"), "blurb stored")
    check(c.execute("SELECT COUNT(*) n FROM chunk_embeddings").fetchone()["n"] == 0, "embedding cleared")
n = len(calls)
check(run(retriever.contextualize_pending(S)) == 0 and len(calls) == n, "second run makes zero calls")

seen: list[str] = []


async def emb(_s: Any, texts: list[str], _m: Any) -> list[list[float]]:
    seen.extend(texts)
    return [[1.0, 0.0] for _ in texts]


retriever.embedder.fn = emb
with db.tx() as c:
    c.execute("UPDATE chunks SET blurb=''")  # force the route to redo the pass, with the setting still off
r = client.post("/documents/embed-backfill").json()
check(r["embedded"] >= 1 and not any("zeppelin" in t for t in seen) and len(calls) == n, "setting off: backfill adds no blurbs")


# ---- the background run after an upload writes blurbs when the setting is on, also in bm25 mode ----
async def scheduled(cfg: dict[str, Any]) -> None:
    retriever.schedule(lambda: cfg)
    await asyncio.gather(*list(retriever._tasks))


def blurbs() -> list[str]:
    with db.tx() as c:
        return [r["blurb"] for r in c.execute("SELECT blurb FROM chunks")]


with db.tx() as c:
    c.execute("UPDATE chunks SET blurb=''")
n = len(calls)
run(scheduled({**S, "contextualChunks": False}))
check(len(calls) == n and not any(blurbs()), "setting off: scheduled run never calls the model")

seen.clear()
run(scheduled(S))
check(all(b.startswith("Quarterly") for b in blurbs()), "setting on: scheduled run writes every blurb")
check(any("Quarterly" in t for t in seen), "blurb is in the text that gets embedded")

with db.tx() as c:
    c.execute("UPDATE chunks SET blurb=''")
seen.clear()
run(scheduled({**S, "retrievalMode": "bm25"}))
check(all(b.startswith("Quarterly") for b in blurbs()) and not seen, "bm25 mode: blurbs written, nothing embedded")

# ---- a large upload is finished, not left at one 64-chunk pass ----
with db.tx() as c:
    c.execute("UPDATE chunks SET blurb=''")
    did = c.execute("SELECT document_id FROM chunks LIMIT 1").fetchone()["document_id"]
    for i in range(70):
        c.execute("INSERT INTO chunks(id, document_id, idx, text, blurb) VALUES(?,?,?,?, '')", (f"big{i}", did, 100 + i, f"part {i}"))
check(run(retriever.contextualize_all(S)) >= 71 and all(blurbs()), "contextualize_all drains past one pass")


# ---- overlapping runs (Rebuild index on 2 files + embed-backfill) send each chunk to the model once ----
async def slow(settings_: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "") -> str:
    calls.append(messages[0]["content"].split("<chunk>\n")[1])
    await asyncio.sleep(0.001)  # yield, so overlapping passes would interleave
    return "Quarterly zeppelin audit findings."


async def overlapping() -> None:
    documents.reindex(None)  # on_chunks schedules one background run per re-chunked file
    await asyncio.gather(retriever.contextualize_all(S), *list(retriever._tasks))


retriever.complete = slow
client.post("/documents", files={"file": ("second.txt", b"Costs fell two percent this quarter.", "text/plain")})
with db.tx() as c:
    c.execute("DELETE FROM chunks WHERE id LIKE 'big%'")
calls.clear()
prev = documents.on_chunks
documents.on_chunks = lambda did, _rows: retriever.schedule(lambda: S, did)
run(overlapping())
documents.on_chunks = prev
with db.tx() as c:
    n_chunks = c.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
check(n_chunks >= 2 and len(calls) == n_chunks == len(set(calls)) and all(blurbs()), "one model call per chunk")
print(f"test_contextual: {passed} checks passed")
