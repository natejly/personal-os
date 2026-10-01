"""Hybrid retrieval: embeddings + BM25 with RRF, graceful BM25 fallback. Offline (stub embedder).
Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_retrieval.py"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="rettest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, db, documents, embedder, retriever, settings, toolbox  # noqa: E402
from personal_os.embed import EmbedError, cosine, normalize, pack, rrf, unpack  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


SYN = {"automobile": "car", "vehicle": "car", "repair": "maintenance", "fix": "maintenance", "servicing": "maintenance"}
DIM = 64


def fake_vec(text: str) -> list[float]:
    v = np.zeros(DIM)
    for w in re.findall(r"[a-z]+", text.lower()):
        w = SYN.get(w, w)
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v.tolist()


calls = {"n": 0}


async def stub(settings_: dict[str, Any], texts: list[str], model: str | None) -> list[list[float]]:
    calls["n"] += 1
    return [fake_vec(t) for t in texts]


async def boom(settings_: dict[str, Any], texts: list[str], model: str | None) -> list[list[float]]:
    raise EmbedError("down")


embedder.fn = stub
S = settings()
check(S["retrievalMode"] == "hybrid" and S["embeddingModel"] and S["retrievalPerDocCap"] == 3, "defaults present")

# (a) rrf
r = rrf([["a", "b", "c"], ["b", "a"]], [1.0, 1.0], k=60)
check([i for i, _ in r][:2] == ["b", "a"] or [i for i, _ in r][:2] == ["a", "b"], "rrf top two")
check(abs(dict(r)["a"] - (1 / 61 + 1 / 62)) < 1e-12, "rrf k=60 arithmetic")
check(abs(dict(r)["c"] - 1 / 63) < 1e-12, "rrf single list")
check(rrf([["x"], ["y"]], [2.0, 1.0])[0][0] == "x", "rrf weights")

# (h) pack/unpack
v = [3.0, 4.0, 0.0]
check(abs(float(np.linalg.norm(unpack(pack(v)))) - 1.0) < 1e-6, "pack normalises")
check(np.allclose(unpack(pack(v)), normalize(v)) and len(pack(v)) == 12, "roundtrip float32")
check(abs(cosine([1, 0], [1, 0]) - 1) < 1e-6 and abs(cosine([1, 0], [0, 1])) < 1e-6, "cosine")

# projects
pa = client.post("/projects", json={"name": "A"}).json()["id"]
pb = client.post("/projects", json={"name": "B"}).json()["id"]


def upload(name: str, text: str, project: str | None = None) -> dict[str, Any]:
    r = client.post("/documents", files={"file": (name, text.encode(), "text/plain")}, data={"project_id": project} if project else {})
    assert r.status_code == 200, r.text
    return r.json()


def flush() -> None:
    run(asyncio.sleep(0.05))
    run(retriever.embed_pending(settings()))


car = upload("garage.txt", "Notes on car maintenance and oil changes for the winter.", pa)
other = upload("recipes.txt", "How to bake sourdough bread with a long fermentation.", pa)
foreign = upload("b-garage.txt", "Car maintenance schedule for the other project.", pb)
flush()
st = client.get("/documents/index-status").json()
check(st["chunks"] == st["embedded"] == 3 and st["model"] == "qwen3-embedding-8b", "index-status after embed")

# (b) paraphrase
hits = run(retriever.search(pa, "automobile repair", settings()))
check(any(h["document_id"] == car["id"] for h in hits), "hybrid finds the paraphrase")
check("vector" in next(h for h in hits if h["document_id"] == car["id"])["sources"], "found via vector")
bm = run(retriever.search(pa, "automobile repair", {**settings(), "retrievalMode": "bm25"}))
check(not bm, "bm25 mode misses the paraphrase")
# (j) scoping
check(all(h["document_id"] != foreign["id"] for h in hits), "other project never appears")

# (c) exact keyword with low cosine survives the floor
low = upload("misc.txt", "zyzzyva " + " ".join(f"filler{i}" for i in range(40)), pa)
flush()
hits = run(retriever.search(pa, "zyzzyva", {**settings(), "retrievalMinSimilarity": 0.99}))
check(any(h["document_id"] == low["id"] for h in hits), "keyword hit survives the floor")
hits = run(retriever.search(pa, "automobile repair", {**settings(), "retrievalMinSimilarity": 0.99}))
check(not hits, "vector-only hits are dropped below the floor")

# (d) embedder raising -> exactly the BM25 result
embedder.fn = boom
bm_direct = documents.search(pa, "car maintenance", limit=40)
hits = run(retriever.search(pa, "car maintenance", settings()))
check([h["chunk_id"] for h in hits] == [h["chunk_id"] for h in bm_direct][:len(hits)] and hits, "EmbedError -> BM25 order")
check(all(h["sources"] == ["bm25"] for h in hits), "fallback is bm25 only")
up = upload("late.txt", "Written while the embedder is down.", pa)
check(up["chunk_count"] == 1, "upload survives embedding failure")
check(client.post("/documents/embed-backfill").json().get("error"), "backfill reports the error")
embedder.fn = stub

# (f) chunks without vectors still appear via bm25
run(asyncio.sleep(0.05))
with db.tx() as c:
    c.execute("DELETE FROM chunk_embeddings WHERE document_id=?", (car["id"],))
hits = run(retriever.search(pa, "car maintenance", settings()))
check(any(h["document_id"] == car["id"] and h["sources"] == ["bm25"] for h in hits), "unembedded chunk found by bm25")

# (i) backfill is idempotent
b1 = client.post("/documents/embed-backfill").json()
b2 = client.post("/documents/embed-backfill").json()
check(b1["embedded"] >= 2 and b1["remaining"] == 0, "backfill embeds the missing rows")
check(b2["embedded"] == 0 and b2["remaining"] == 0, "backfill idempotent")
check(client.get("/documents/index-status").json()["embedded"] == client.get("/documents/index-status").json()["chunks"], "status consistent")

# model change makes old rows stale
S2 = {**settings(), "embeddingModel": "other-model"}
check(run(retriever.search(pa, "automobile repair", S2)) == [], "rows from another model are ignored")

# (e) per-document cap
big = upload("many.txt", "\n\n".join(f"Paragraph {i} about turbines. " + ("turbine blade " * 70) for i in range(8)), pa)
flush()
hits = run(retriever.search(pa, "turbine", settings(), limit=10))
check(0 < len([h for h in hits if h["document_id"] == big["id"]]) <= 3, "per-document cap of 3")
hits = run(retriever.search(pa, "turbine", {**settings(), "retrievalPerDocCap": 0}, limit=10))
check(len([h for h in hits if h["document_id"] == big["id"]]) > 3, "cap 0 disables the cap")

# search_documents tool goes through the retriever
out = run(toolbox.call("search_documents", {"query": "automobile repair"}, {"project_id": pa}))
check("garage.txt" in str(out), "tool uses hybrid search")

# (g) delete removes embeddings
client.delete(f"/documents/{big['id']}")
with db.tx() as c:
    n = c.execute("SELECT COUNT(*) AS n FROM chunk_embeddings WHERE document_id=?", (big["id"],)).fetchone()["n"]
check(n == 0, "embeddings cascade on delete")

# settings survive PUT /settings
client.put("/settings", json={"retrievalPerDocCap": 5})
check(client.get("/settings").json()["retrievalPerDocCap"] == 5 and client.get("/settings").json()["retrievalMode"] == "hybrid", "settings persist")
client.put("/settings", json={"retrievalPerDocCap": 3})

# context preview uses the hybrid hits
pv = client.post("/context/preview", json={"project_id": pa, "query": "automobile repair"}).json()
check(any(c["name"] == "garage.txt" for c in pv["chunks"]), "context preview carries the paraphrase hit")

# a failed route backs off instead of costing a request per turn; an explicit reset retries at once
from personal_os.embed import EmbedError as _EE, Embedder as _Emb  # noqa: E402
_now = [0.0]
_calls = []
async def _down(_s: Any, texts: list[str], _m: str) -> list[list[float]]:
    _calls.append(len(texts))
    raise _EE("down")
_e = _Emb(_down, clock=lambda: _now[0])
_cfg = {"embeddingModel": "m"}
check(run(_e.embed(_cfg, ["a"])) is None and _calls == [1], "first failure is one request")
check(run(_e.embed(_cfg, ["b"])) is None and _calls == [1] and not _e.available(_cfg), "then the route is skipped while backing off")
_now[0] = _Emb.BACKOFF_SECONDS + 1
check(_e.available(_cfg), "and tried again once the window passes")
_e._down["m"] = 1e9
_e.reset()
check(_e.available(_cfg), "reset clears the back-off")

# with no stored vectors for the model, search never embeds the query
_q = []
async def _count(_s: Any, texts: list[str], _m: str) -> list[list[float]]:
    _q.append(texts)
    return [[1.0, 0.0] for _ in texts]
_r = type(retriever)(db, retriever.documents, _Emb(_count), docs=retriever.docs)
run(_r.search(pa, "car maintenance", {**settings(), "embeddingModel": "never-used-model"}))
check(_q == [], "no vectors for this model -> no query embedding round trip")

print(f"test_retrieval: {passed} checks passed")
