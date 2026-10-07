"""Memory retrieval eval: metric math, fixture consistency, and a regression floor on the offline run.

Offline only: recorded embedding vectors (tests/fixtures/memory_eval/vectors.npz), no network.
Re-measure with `cd backend && uv run --with pytest python scripts/memory_eval.py`.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import memory_eval as me  # noqa: E402
from personal_os import llm  # noqa: E402

CATEGORIES = {"lexical", "paraphrase", "graph", "temporal", "negative", "supersession", "anaphoric"}
SETTINGS = {**llm.DEFAULT_SETTINGS, "embeddingModel": me.OFFLINE_MODEL, "hybridRetrieval": True}


# ---- metric math ----

def test_recall_at_k() -> None:
    assert me.recall_at_k(["a", "b", "c"], ["a", "c"], 1) == 0.5
    assert me.recall_at_k(["a", "b", "c"], ["a", "c"], 3) == 1.0
    assert me.recall_at_k([], ["a"], 5) == 0.0
    assert me.recall_at_k(["a"], [], 5) is None  # nothing expected: undefined, scored as a negative instead
    assert me.recall_at_k(["a", "a", "b"], ["a", "b"], 2) == 0.5  # k counts returned positions


def test_reciprocal_rank() -> None:
    assert me.reciprocal_rank(["x", "a", "b"], ["a", "b"]) == 0.5  # first expected hit only
    assert me.reciprocal_rank(["a"], ["a"]) == 1.0
    assert me.reciprocal_rank(["x", "y"], ["a"]) == 0.0
    assert me.reciprocal_rank([], ["a"]) == 0.0
    assert me.reciprocal_rank(["a"], []) is None


def test_score_groups_negatives_and_must_not() -> None:
    cases = [
        {"id": "p", "category": "lexical", "expected": ["a"], "must_not": ["z"]},
        {"id": "q", "category": "graph", "expected": ["b", "c"], "must_not": []},
        {"id": "n1", "category": "negative", "expected": [], "must_not": []},
        {"id": "n2", "category": "negative", "expected": [], "must_not": []},
    ]
    s = me.score(cases, {"p": ["z", "a"], "q": ["x", "c"], "n1": [], "n2": ["a", "b", "c"]})
    assert s["overall"]["n"] == 2 and "negative" not in s["categories"]
    assert s["categories"]["lexical"] == {"n": 1, "recall@5": 1.0, "recall@10": 1.0, "mrr": 0.5}
    assert s["categories"]["graph"]["recall@5"] == 0.5 and s["overall"]["mrr"] == 0.5
    assert s["negatives"] == {"n": 2, "empty_correct": 1, "mean_results": 1.5}
    assert s["must_not"] == {"violations": 1, "top10": 1}


# ---- fixtures ----

def test_fixtures_are_consistent() -> None:
    store, spec = me.load_store(), me.load_cases()
    mems = {m["id"]: m for m in store["memories"]}
    assert len(mems) == len(store["memories"]) and 100 <= len(mems) <= 140
    now = datetime.fromisoformat(spec["now"])
    assert now.utcoffset() is not None and set(spec["categories"]) == CATEGORIES
    for m in mems.values():
        assert m["kind"] in {"fact", "preference", "instruction", "goal", "note"}
        created = datetime.fromisoformat(m["created_at"])
        assert created < now and (now - created).days <= 560, m["id"]  # roughly 18 months before NOW
        if "superseded_by" in m:
            nxt = mems[m["superseded_by"]]
            assert datetime.fromisoformat(nxt["created_at"]) > created and "superseded_by" not in nxt
        if "expires_at" in m:
            assert datetime.fromisoformat(m["expires_at"]) < now
    assert {m["project_id"] for m in mems.values() if m.get("project_id")} == {p["id"] for p in store["projects"]}
    ents = {e["id"] for e in store["entities"]}
    assert len(ents) == len(store["entities"]) and sum(bool(e.get("self")) for e in store["entities"]) == 1
    assert all(e["source"] in ents and e["target"] in ents for e in store["edges"])

    seen = set()
    for c in spec["cases"]:
        assert c["id"] not in seen and c["category"] in CATEGORIES
        seen.add(c["id"])
        assert (c["category"] == "negative") == (not c["expected"]), c["id"]
        for i in c["expected"]:
            m = mems[i]  # a KeyError names the dangling id
            assert "superseded_by" not in m and "expires_at" not in m, f"{c['id']}: {i} is not live"
            assert m.get("project_id") in (None, c.get("project_id")), f"{c['id']}: {i} is out of scope"
        assert all(i in mems for i in c["must_not"]) and not set(c["expected"]) & set(c["must_not"])
    counts = {cat: sum(c["category"] == cat for c in spec["cases"]) for cat in CATEGORIES}
    assert 55 <= len(spec["cases"]) <= 70 and min(counts.values()) >= 6, counts
    # A temporal case must have same-topic rows outside its window to rule out.
    assert all(len(c["must_not"]) >= 2 for c in spec["cases"] if c["category"] == "temporal")


# ---- the run ----

@pytest.fixture(scope="module")
def run() -> dict:
    return asyncio.run(me.evaluate(SETTINGS))


def test_every_text_has_a_recorded_vector(run: dict) -> None:
    assert run["fallbacks"] == 0, "vectors.npz is missing recordings: re-run scripts/memory_eval.py --live --record"


def test_harness_runs_end_to_end_and_hides_history(run: dict) -> None:
    spec = me.load_cases()["cases"]
    assert set(run["ranked"]) == {c["id"] for c in spec}
    returned = {i for ids in run["ranked"].values() for i in ids}
    store = {m["id"]: m for m in me.load_store()["memories"]}
    assert not {i for i in returned if "superseded_by" in store[i] or "expires_at" in store[i]}  # history never comes back
    for c in spec:
        assert len(run["ranked"][c["id"]]) <= me.memory_limits.CONTEXT_HITS
        if c.get("project_id") is None:  # personal scope never sees project rows
            assert not [i for i in run["ranked"][c["id"]] if store[i].get("project_id")], c["id"]


# Floors: the offline run on 2026-10-07 (recorded qwen3-embedding-8b vectors, retrieval before any ranking change), minus a small margin.
# Baseline: overall R@5 0.638, R@10 0.836, MRR 0.483; negatives empty-correct 0/7; must_not violations 46 (38 in the top 10).
FLOORS = {  # category -> (recall@10, MRR) baseline in the comment
    "lexical": (0.95, 0.78),       # 1.000, 0.831
    "paraphrase": (0.72, 0.26),    # 0.773, 0.310
    "graph": (0.50, 0.18),         # 0.556, 0.233
    "temporal": (0.72, 0.30),      # 0.769, 0.352
    "supersession": (0.95, 0.57),  # 1.000, 0.621
    "anaphoric": (0.95, 0.59),     # 1.000, 0.640
}
OVERALL_FLOOR = {"recall@5": 0.60, "recall@10": 0.79, "mrr": 0.44}


def test_retrieval_does_not_regress(run: dict) -> None:
    s = run["scores"]
    for metric, floor in OVERALL_FLOOR.items():
        assert s["overall"][metric] >= floor, f"overall {metric} {s['overall'][metric]:.3f} < {floor}"
    for cat, (r10, mrr) in FLOORS.items():
        got = s["categories"][cat]
        assert got["recall@10"] >= r10, f"{cat} recall@10 {got['recall@10']:.3f} < {r10}"
        assert got["mrr"] >= mrr, f"{cat} mrr {got['mrr']:.3f} < {mrr}"
    assert s["must_not"]["violations"] <= 50 and s["must_not"]["top10"] <= 42
