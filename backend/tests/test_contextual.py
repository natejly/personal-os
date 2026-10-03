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
print(f"test_contextual: {passed} checks passed")
