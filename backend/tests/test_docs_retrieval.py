"""The user's own Docs in the same retrieval as uploads. Offline (stub embedder).
Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_docs_retrieval.py"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docretr-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, db, docs, documents, embedder, retriever, settings, toolbox  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.docs import Docs  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]}"
    return r.json()


SYN = {"automobile": "car", "vehicle": "car", "repair": "maintenance", "fix": "maintenance"}


def fake_vec(text: str) -> list[float]:
    v = np.zeros(64)
    for w in re.findall(r"[a-z]+", text.lower()):
        v[int(hashlib.md5(SYN.get(w, w).encode()).hexdigest(), 16) % 64] += 1.0
    return v.tolist()


async def stub(settings_: dict[str, Any], texts: list[str], model: str | None) -> list[list[float]]:
    return [fake_vec(t) for t in texts]


embedder.fn = stub
retriever.DOC_DEBOUNCE = 0


def chunk_rows(doc_id: str) -> list[Any]:
    with db.tx() as c:
        return c.execute("SELECT * FROM doc_chunks WHERE doc_id=? ORDER BY idx", (doc_id,)).fetchall()


def fts_n(doc_id: str) -> int:
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) AS n FROM doc_chunks_fts WHERE doc_id=?", (doc_id,)).fetchone()["n"]


pa = j("POST", "/projects", {"name": "A"})["id"]
pb = j("POST", "/projects", {"name": "B"})["id"]

# (a) create populates, save replaces
d1 = j("POST", "/docs", {"title": "Garage log", "content": "# Cars\n\nThe quokka tuned the engine on Monday.\n", "project_id": pa})
check(len(chunk_rows(d1["id"])) == 1 and fts_n(d1["id"]) == 1, "create populates doc_chunks and its FTS")
check(chunk_rows(d1["id"])[0]["heading"] == "Cars", "chunk carries its heading path")
hits = docs.chunk_search("quokka", pa)
check(hits and hits[0]["source"] == "doc" and hits[0]["doc_id"] == d1["id"] and hits[0]["title"] == "Garage log", "chunk_search hit shape")
j("PUT", f"/docs/{d1['id']}", {"content": "# Cars\n\nThe wombat tuned the engine on Monday.\n"})
check(not docs.chunk_search("quokka", pa) and docs.chunk_search("wombat", pa), "save replaces the old chunks")
check(fts_n(d1["id"]) == 1, "one FTS row after save")

# (h) unchanged content does not rebuild
ids_before = [r["id"] for r in chunk_rows(d1["id"])]
notified: list[str] = []
prev_hook = docs.on_chunks
docs.on_chunks = lambda did: notified.append(did)
docs.update_meta(d1["id"], {"starred": True})  # runs _reindex with identical title+body
docs.save(d1["id"], content="# Cars\n\nThe wombat tuned the engine on Monday.\n")  # no-op save
check([r["id"] for r in chunk_rows(d1["id"])] == ids_before and notified == [], "unchanged content rebuilds nothing")
docs.save(d1["id"], title="Garage log 2")
check(notified == [d1["id"]] and docs.chunk_search("garage", pa), "a title change re-indexes (title is in the indexed context)")
docs.on_chunks = prev_hook

# (b) pending revision is not indexed; accept is
rev = docs.propose(d1["id"], "# Cars\n\nThe axolotl rebuilt the gearbox.\n", "swap")
check(not docs.chunk_search("axolotl", pa), "pending revision is not indexed")
docs.reject(rev["id"])
check(not docs.chunk_search("axolotl", pa), "rejected revision never leaks")
rev2 = docs.propose(d1["id"], "# Cars\n\nThe axolotl rebuilt the gearbox.\n", "swap again")
j("POST", f"/docs/revisions/{rev2['id']}/accept")
check(docs.chunk_search("axolotl", pa) and not docs.chunk_search("wombat", pa), "accept reindexes")

# (d) scoping: A sees A + personal, not B
db_doc = j("POST", "/docs", {"title": "B notes", "content": "The pangolin secret belongs to project B.", "project_id": pb})
pers = j("POST", "/docs", {"title": "Personal", "content": "The pangolin habit is personal.", "project_id": None})
ids = {h["doc_id"] for h in docs.chunk_search("pangolin", pa)}
check(pers["id"] in ids and db_doc["id"] not in ids, "project A sees personal docs, not project B's")
check(db_doc["id"] in {h["doc_id"] for h in docs.chunk_search("pangolin", pb)}, "project B sees its own")
check({h["doc_id"] for h in docs.chunk_search("pangolin", None)} == {pers["id"]}, "personal scope sees only personal")

# (e) unified search: file chunk + doc chunk fused, tagged
f = client.post("/documents", files={"file": ("manual.txt", b"The zeppelin manual explains inflation.", "text/plain")}, data={"project_id": pa}).json()
d2 = j("POST", "/docs", {"title": "Zeppelin diary", "content": "Today the zeppelin landed softly.", "project_id": pa})
run(retriever.embed_pending(settings()))
res = run(retriever.search(pa, "zeppelin", settings(), sources=("files", "docs")))
tags = {(h["source"], h["document_id"]) for h in res}
check(("file", f["id"]) in tags and ("doc", d2["id"]) in tags, "one ranking holds both a file and a doc chunk")
check(all("sources" in h and h["source"] in ("file", "doc") for h in res), "hits tagged by source")
only_files = run(retriever.search(pa, "zeppelin", settings(), sources=("files",)))
check({h["source"] for h in only_files} == {"file"}, "files-only search excludes docs")
# vector path reaches doc chunks too
car = j("POST", "/docs", {"title": "Car log", "content": "Notes on car maintenance for the truck.", "project_id": pa})
run(retriever.embed_pending(settings()))
res = run(retriever.search(pa, "automobile repair", settings(), sources=("files", "docs")))
check(any(h["doc_id"] == car["id"] if h.get("source") == "doc" else False for h in res), "paraphrase finds a doc via vectors")
st = client.get("/documents/index-status").json()
check(st["doc_chunks"] == st["doc_embedded"] > 0, "index-status reports doc vectors")

# (f) build_context
cfg = settings()
conv = {"useMemory": False, "useGraph": False, "useActivity": False, "useSkills": False, "useStyle": False, "useMeetings": False}


def ctx(conv_settings: dict[str, Any], cfg_: dict[str, Any], hits: Any) -> tuple[str, dict[str, Any]]:
    from personal_os.app import graph, memories
    return build_context(memories=memories, graph=graph, documents=documents, project=None, project_id=pa, query="zeppelin",
                         settings=cfg_, conv_settings=conv_settings, global_system_prompt="", doc_hits=hits)


hits = run(retriever.search(pa, "zeppelin", cfg, sources=("files", "docs")))
sysm, used = ctx({**conv, "useDocuments": True}, cfg, hits)
check("(your doc)" in sysm and "Zeppelin diary" in sysm, "context labels the user's doc")
check(any(c["source"] == "doc" and c["doc_id"] == d2["id"] for c in used["chunks"]), "used chunks carry source and doc_id")
sysm, used = ctx({**conv, "useDocuments": True}, {**cfg, "useDocsInContext": False}, hits)
check("(your doc)" not in sysm and any(c["source"] == "file" for c in used["chunks"]), "useDocsInContext off drops docs, keeps files")
sysm, used = ctx({**conv, "useDocuments": False}, cfg, hits)
check(used["chunks"] == [], "per-chat useDocuments off drops both")
pv = j("POST", "/context/preview", {"project_id": pa, "query": "wombat axolotl gearbox"})
check(any(c["source"] == "doc" for c in pv["chunks"]), "preview route retrieves docs without any tool call")
j("PUT", "/settings", {"useDocsInContext": False})
check(settings()["useDocsInContext"] is False, "useDocsInContext persists via PUT /settings")
pv = j("POST", "/context/preview", {"project_id": pa, "query": "gearbox"})
check(all(c["source"] != "doc" for c in pv["chunks"]), "setting off: preview has no doc excerpts")
j("PUT", "/settings", {"useDocsInContext": True})

# (g) search_documents scope
def sd(args: dict[str, Any]) -> Any:
    return run(toolbox.call("search_documents", args, {"project_id": pa}))


both = str(sd({"query": "zeppelin"}))
check("'source': 'file'" in both and "'source': 'doc'" in both, "scope all returns both sources")
only_docs = sd({"query": "zeppelin", "scope": "docs"})
check("'source': 'doc'" in str(only_docs) and "'source': 'file'" not in str(only_docs), "scope docs returns only docs")
only_f = sd({"query": "zeppelin", "scope": "files"})
check("'source': 'file'" in str(only_f) and "'source': 'doc'" not in str(only_f), "scope files returns only files")
check("error" in str(sd({"query": "x", "scope": "nope"})).lower(), "bad scope is a readable error")

# (c) deletes
did = d2["id"]
j("DELETE", f"/docs/{did}")
check(not chunk_rows(did) and fts_n(did) == 0, "delete removes chunks and FTS")
with db.tx() as c:
    check(c.execute("SELECT COUNT(*) AS n FROM doc_chunk_embeddings WHERE doc_id=?", (did,)).fetchone()["n"] == 0, "delete removes vectors")
fd = j("POST", "/docs", {"title": "In folder", "content": "The narwhal lives in a folder.", "project_id": None, "folder": "Tmp"})
j("POST", "/docs/folders", {"path": "Tmp"})
j("DELETE", "/docs/folders?path=Tmp&delete_docs=true")
check(fts_n(fd["id"]) == 0 and not chunk_rows(fd["id"]) and not docs.chunk_search("narwhal", None), "delete_folder(delete_docs) removes chunks")

# (i) backfill for a doc inserted behind our back
with db.tx() as c:
    c.execute("INSERT INTO docs(id,project_id,title,content,folder,starred,created_at,updated_at) VALUES('raw1',NULL,'Raw','# Raw\n\nThe okapi was inserted by raw SQL.','',0,1,1)")
    c.execute("INSERT INTO docs_fts(title, content, doc_id) VALUES('Raw','okapi','raw1')")
check(not docs.chunk_search("okapi", None), "raw row starts unindexed")
r = j("POST", "/documents/embed-backfill")
check(docs.chunk_search("okapi", None) and r["remaining"] == 0, "embed-backfill chunks and embeds it")
with db.tx() as c:
    c.execute("INSERT INTO docs(id,project_id,title,content,folder,starred,created_at,updated_at) VALUES('raw2',NULL,'Raw2','The tapir arrived at startup.','',0,1,1)")
Docs(db)  # constructing Docs is the startup backfill
check(docs.chunk_search("tapir", None), "startup backfill indexes it")
n_before = len(chunk_rows("raw1"))
docs.backfill_chunks()
check(len(chunk_rows("raw1")) == n_before, "backfill is idempotent")

# (j) doc_search unchanged
out = run(toolbox.call("doc_search", {"query": "okapi"}, {"project_id": None}))
check("raw1" in str(out) and "Raw" in str(out), "doc_search still returns whole-doc hits")

# the debounce hook schedules embedding without blocking a write
sched: list[int] = []
orig = retriever.schedule_docs
retriever.schedule_docs = lambda sf: sched.append(1)  # type: ignore[method-assign]
docs.on_chunks = lambda _d: retriever.schedule_docs(settings)
docs.create("Hook", "a body to index", None)
check(sched == [1], "doc write notifies the retriever (no network in the write)")
retriever.schedule_docs = orig  # type: ignore[method-assign]

print(f"test_docs_retrieval: {passed} checks passed")
