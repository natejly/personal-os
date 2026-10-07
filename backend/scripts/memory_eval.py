"""Score memory retrieval (MemoryIndex.search) on a synthetic persona against tests/fixtures/memory_eval/.

    cd backend && uv run --with pytest python scripts/memory_eval.py [--live] [--record] [--case ID] [--json]
                  [--config KEY=VALUE ...] [--label TEXT] [--verbose]

Default: offline. A temp database is filled from store.json; embeddings come from vectors.npz (recorded vectors keyed by
sha1(text), float16) with a hashed bag-of-words fallback for any text that has no recording. No network.
--live: real embeddings (settings read-only from the data dir, never written, never printed).
--record (with --live): save vectors.npz for every memory, node label and query text. Texts already recorded are kept
as they are, so re-recording is idempotent; delete the file to re-embed everything.
--config KEY=VALUE (repeatable): extra settings for the run, values parsed as JSON ("memoryRerank=true").
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from personal_os import graph_recall, llm, memory_limits  # noqa: E402
from personal_os.context import retrieval_query  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import Embedder, embed_texts, normalize  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.migrations import sync_memories_fts  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402
from graph_eval import DEFAULT_DATA_DIR, read_settings  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "memory_eval"
VECTORS = FIXTURES / "vectors.npz"
OFFLINE_MODEL = "eval-embed"
FALLBACK_DIM = 512


# ---- fixtures ----

def load_store() -> dict[str, Any]:
    return json.loads((FIXTURES / "store.json").read_text())


def load_cases() -> dict[str, Any]:
    return json.loads((FIXTURES / "cases.json").read_text())


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# ---- embeddings: recorded vectors, a deterministic fallback, or the live route ----

def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_recording() -> dict[str, np.ndarray]:
    if not VECTORS.exists():
        return {}
    z = np.load(VECTORS, allow_pickle=False)
    return {str(k): normalize(v.astype(np.float32)) for k, v in zip(z["keys"], z["vecs"])}


def save_recording(rec: dict[str, np.ndarray], keep: set[str], model: str) -> None:
    keys = sorted(k for k in rec if k in keep)
    np.savez_compressed(VECTORS, keys=np.array(keys), vecs=np.stack([rec[k] for k in keys]).astype(np.float16),
                        model=np.array(model))


def hashed_bow(text: str, dim: int) -> list[float]:
    v = [0.0] * dim
    for w in re.findall(r"\w+", text.lower()):
        v[int(hashlib.sha1(w.encode()).hexdigest(), 16) % dim] += 1.0
    return v


class Vectors:
    """The embed function the harness hands the Embedder. Recorded first; a miss goes live (--live) or to the fallback."""

    def __init__(self, rec: dict[str, np.ndarray], live: bool):
        self.rec, self.live, self.fallbacks = rec, live, set()
        self.dim = next(iter(rec.values())).shape[0] if rec else FALLBACK_DIM

    async def __call__(self, settings: dict[str, Any], texts: list[str], model: Any) -> list[list[float]]:
        miss = list(dict.fromkeys(t for t in texts if _key(t) not in self.rec))
        if miss and self.live:
            try:
                got = await embed_texts(settings, miss, model)
            except Exception as e:  # noqa: BLE001 - report the class and stop; the key is never in this text
                raise SystemExit(f"live embedding failed: {type(e).__name__}: {str(e)[:200]}")
            self.rec.update({_key(t): normalize(v) for t, v in zip(miss, got)})
        else:
            self.fallbacks.update(miss)
        return [self.rec[_key(t)].tolist() if _key(t) in self.rec else hashed_bow(t, self.dim) for t in texts]


# ---- the store as a Grain database ----

async def build(tmp: str, store: dict[str, Any], vectors: Vectors, settings: dict[str, Any]) -> MemoryIndex:
    db = Database(tmp)
    by_id = {m["id"]: m for m in store["memories"]}
    with db.tx() as c:
        for p in store["projects"]:
            c.execute("INSERT INTO projects(id,name,created_at) VALUES(?,?,?)", (p["id"], p["name"], 0.0))
        for m in store["memories"]:
            t = _ts(m["created_at"])
            nxt = by_id.get(m.get("superseded_by", ""))
            c.execute(
                "INSERT INTO memories(id,project_id,content,kind,source,pinned,created_at,updated_at,valid_from,invalid_at,superseded_by,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (m["id"], m.get("project_id"), m["content"], m["kind"], "auto", int(bool(m.get("pinned"))), t, t, t,
                 _ts(nxt["created_at"]) if nxt else None, m.get("superseded_by"),
                 _ts(m["expires_at"]) if m.get("expires_at") else None))
        sync_memories_fts(c)
    graph = Graph(db)
    ids: dict[str, str] = {}
    for e in store["entities"]:
        props = {"aliases": e["aliases"], **({"self": True} if e.get("self") else {})}
        ids[e["id"]] = graph.upsert_node(None, e["label"], e["type"], props)["id"]
    for e in store["edges"]:
        graph.upsert_edge(None, ids[e["source"]], ids[e["target"]], e["relation"], valid_at=_ts(e["since"]))
    embedder = Embedder(vectors)
    idx = MemoryIndex(db, Memories(db), graph, embedder)
    idx.recall = graph_recall.GraphRecall(db, graph, embedder)
    while await idx.index(settings):
        pass
    while await idx.recall.index(settings):
        pass
    return idx


def all_texts(store: dict[str, Any], cases: list[dict[str, Any]]) -> list[str]:
    """Everything the run embeds: memory contents, node texts and query texts."""
    nodes = [graph_recall.node_text({"label": e["label"], "properties": {"aliases": e["aliases"]}})
             for e in store["entities"] if not e.get("self")]
    return list(dict.fromkeys([*(m["content"] for m in store["memories"]), *nodes, *(query_text(c) for c in cases)]))


# ---- ranking: the single place retrieval is invoked ----

def query_text(case: dict[str, Any]) -> str:
    """What retrieval searches with: a short or anaphoric message is joined with the previous turn (context.retrieval_query)."""
    prior = [{"role": "user", "content": case["prev_user"]}, {"role": "assistant", "content": case.get("prev_reply", "")}] \
        if case.get("prev_user") else []
    return retrieval_query(prior, case["query"])


async def run_case(idx: MemoryIndex, settings: dict[str, Any], case: dict[str, Any], now: datetime) -> list[str]:
    """Memory ids for one case, best first. `now` is the fixed eval clock (unused until retrieval reads a date window)."""
    q = query_text(case)
    qvec = await idx.query_vec(settings, q)
    hits = idx.search(case.get("project_id"), q, qvec, limit=memory_limits.CONTEXT_HITS, settings=settings)
    return [m["id"] for m in hits]


# ---- metrics (pure) ----

def recall_at_k(ranked: list[str], expected: list[str], k: int) -> float | None:
    """Share of the expected ids in the top k; None when nothing is expected."""
    return len(set(expected) & set(ranked[:k])) / len(set(expected)) if expected else None


def reciprocal_rank(ranked: list[str], expected: list[str]) -> float | None:
    """1 / rank of the first expected id (0 when none is returned); None when nothing is expected."""
    if not expected:
        return None
    return next((1 / r for r, i in enumerate(ranked, 1) if i in expected), 0.0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def score(cases: list[dict[str, Any]], ranked: dict[str, list[str]]) -> dict[str, Any]:
    """Per-category and overall recall@5, recall@10, MRR over cases with an expected set; negatives (nothing expected)
    count as correct when the result is empty; must_not violations are counted over every case."""
    def agg(cs: list[dict[str, Any]]) -> dict[str, Any]:
        r5, r10, rr = ([f(ranked[c["id"]], c["expected"], *a) for c in cs] for f, a in
                       ((recall_at_k, (5,)), (recall_at_k, (10,)), (reciprocal_rank, ())))
        return {"n": len(cs), "recall@5": _mean(r5), "recall@10": _mean(r10), "mrr": _mean(rr)}

    pos = [c for c in cases if c["expected"]]
    neg = [c for c in cases if not c["expected"]]
    out: dict[str, Any] = {"overall": agg(pos), "categories": {}}
    for cat in dict.fromkeys(c["category"] for c in pos):
        out["categories"][cat] = agg([c for c in pos if c["category"] == cat])
    out["negatives"] = {"n": len(neg), "empty_correct": sum(not ranked[c["id"]] for c in neg),
                        "mean_results": _mean([len(ranked[c["id"]]) for c in neg])}
    out["must_not"] = {"violations": sum(len(set(c["must_not"]) & set(ranked[c["id"]])) for c in cases),
                       "top10": sum(len(set(c["must_not"]) & set(ranked[c["id"]][:10])) for c in cases)}
    return out


def print_table(label: str, s: dict[str, Any], note: str = "") -> None:
    print(f"== {label} {note}".rstrip())
    print(f"{'category':<13}{'n':>3}{'R@5':>7}{'R@10':>7}{'MRR':>7}")
    for name, a in [*s["categories"].items(), ("overall", s["overall"])]:
        print(f"{name:<13}{a['n']:>3}{a['recall@5']:>7.3f}{a['recall@10']:>7.3f}{a['mrr']:>7.3f}")
    n = s["negatives"]
    print(f"negatives: empty-correct {n['empty_correct']}/{n['n']}, mean results returned {n['mean_results']:.1f}")
    m = s["must_not"]
    print(f"must_not violations: {m['violations']} in the returned list, {m['top10']} in the top 10")


def print_case(case: dict[str, Any], ranked: list[str], contents: dict[str, str]) -> None:
    print(f"{case['id']} [{case['category']}] {query_text(case)!r}  expected={case['expected']} must_not={case['must_not']}")
    for r, i in enumerate(ranked[:15], 1):
        mark = "+" if i in case["expected"] else "x" if i in case["must_not"] else " "
        print(f"  {r:>2} {mark} {i:<20} {contents[i][:80]}")
    miss = [i for i in case["expected"] if i not in ranked]
    if miss:
        print(f"  not returned: {miss}")


# ---- running ----

def parse_config(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise SystemExit(f"--config expects KEY=VALUE, got {it!r}")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


async def evaluate(settings: dict[str, Any], live: bool = False, record: bool = False, only: str | None = None) -> dict[str, Any]:
    """Build the persona database and rank every case. Returns the scores plus the rankings and embedding provenance."""
    store, spec = load_store(), load_cases()
    cases = [c for c in spec["cases"] if not only or c["id"] == only]
    if not cases:
        raise SystemExit(f"no case {only!r}")
    now = datetime.fromisoformat(spec["now"])
    vectors = Vectors(load_recording() if (record or not live) else {}, live)
    texts = all_texts(store, cases)
    with tempfile.TemporaryDirectory() as tmp:
        await vectors(settings, texts, settings["embeddingModel"])  # warm: live calls happen here, once
        idx = await build(tmp, store, vectors, settings)
        ranked = {c["id"]: await run_case(idx, settings, c, now) for c in cases}
        contents = {m["id"]: m["content"] for m in store["memories"]}
    if record:
        save_recording(vectors.rec, {_key(t) for t in texts}, settings["embeddingModel"])
    return {"cases": cases, "ranked": ranked, "contents": contents, "scores": score(cases, ranked),
            "recorded": len(vectors.rec), "fallbacks": len(vectors.fallbacks)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--case")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every case's ranking, not just the table")
    ap.add_argument("--config", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--label", default="memory retrieval")
    ap.add_argument("--data-dir", default=os.environ.get("PERSONAL_OS_DATA_DIR") or DEFAULT_DATA_DIR)
    a = ap.parse_args()
    if a.record and not a.live:
        ap.error("--record needs --live")
    if a.record and a.case:
        ap.error("--record needs every case, not --case")
    if a.live:
        settings = read_settings(Path(a.data_dir).expanduser())
        if not settings.get("embeddingModel"):
            raise SystemExit("no embeddingModel in the settings")
    else:
        settings = {**llm.DEFAULT_SETTINGS, "embeddingModel": OFFLINE_MODEL}
    settings = {**settings, "hybridRetrieval": True, **parse_config(a.config)}
    res = asyncio.run(evaluate(settings, a.live, a.record, a.case))
    if a.json:
        print(json.dumps({"label": a.label, "config": parse_config(a.config), **res["scores"], "fallbacks": res["fallbacks"],
                          "ranked": res["ranked"]}, indent=1))
        return
    note = (f"(live, model {settings['embeddingModel']})" if a.live
            else f"(offline; {res['recorded']} recorded vectors, {res['fallbacks']} texts on the bag-of-words fallback)")
    print_table(a.label, res["scores"], note)
    if a.record:
        print(f"recorded {res['recorded']} vectors to {VECTORS}")
    if a.verbose or a.case:
        for c in res["cases"]:
            print_case(c, res["ranked"][c["id"]], res["contents"])


if __name__ == "__main__":
    main()
