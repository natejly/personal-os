"""search_reranked: model reorder of the fused memory rows, and every way it falls back. Offline (stub rerank fn)."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import memory_limits as ml  # noqa: E402
from personal_os import providers  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import Embedder  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402

ON = {"provider": "fireworks", "memoryRerank": True}


class Stub:
    def __init__(self, mode: str = "reverse") -> None:
        self.mode, self.calls = mode, 0

    async def __call__(self, settings: Any, model: str, query: str, docs: list[str], *, timeout: float, top_n: int) -> Any:
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "none":
            return None
        if self.mode == "slow":
            await asyncio.sleep(1)
        n = len(docs)
        return [(i, (i + 1) / n) for i in reversed(range(n))] if self.mode == "reverse" else []


@pytest.fixture()
def idx():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        memories = Memories(db)
        for i in range(6):
            memories.create(None, f"Gardening note number {i} about tomatoes", kind="note")
        yield MemoryIndex(db, memories, Graph(db), Embedder())


def run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [m["id"] for m in rows]


def test_off_or_no_model_keeps_fused_order(idx) -> None:
    idx.rerank_fn = stub = Stub()
    fused = idx.search(None, "gardening", settings=ON, limit=20)
    assert len(fused) == 6
    assert ids(run(idx.search_reranked(None, "gardening", settings={**ON, "memoryRerank": False}))) == ids(fused)
    assert ids(run(idx.search_reranked(None, "gardening", settings={"provider": "openai"}))) == ids(fused)  # no model known
    assert ids(run(idx.search_reranked(None, "gardening", settings=None))) == ids(fused)
    assert stub.calls == 0


def test_reorders_limits_and_scores(idx) -> None:
    idx.rerank_fn = Stub()
    fused = idx.search(None, "gardening", settings=ON, limit=20)
    out = run(idx.search_reranked(None, "gardening", settings=ON))
    assert ids(out) == ids(reversed(fused))
    assert all("rerank_score" in m for m in out)
    assert ids(run(idx.search_reranked(None, "gardening", settings=ON, limit=2))) == ids(reversed(fused))[:2]


def test_few_candidates_skip_the_call(idx) -> None:
    idx.rerank_fn = stub = Stub()
    assert len(run(idx.search_reranked(None, "gardening", settings=ON, limit=3))) == 3  # 6 rows, so the call is made
    assert stub.calls == 1
    assert idx.memories.create(None, "Zebra facts", kind="note") and len(run(idx.search_reranked(None, "zebra", settings=ON))) == 1
    assert stub.calls == 1  # one candidate: not worth a call


@pytest.mark.parametrize("mode", ["raise", "none"])
def test_failure_keeps_fused_order_and_backs_off(idx, mode: str) -> None:
    idx.rerank_fn = stub = Stub(mode)
    fused = ids(idx.search(None, "gardening", settings=ON))
    assert ids(run(idx.search_reranked(None, "gardening", settings=ON))) == fused
    assert stub.calls == 1 and idx._rerank_down_until > 0
    assert ids(run(idx.search_reranked(None, "gardening", settings=ON))) == fused
    assert stub.calls == 1  # backed off
    idx._rerank_down_until = 0.0
    run(idx.search_reranked(None, "gardening", settings=ON))
    assert stub.calls == 2  # back-off over


def test_slow_call_times_out(idx, monkeypatch) -> None:
    monkeypatch.setattr(ml, "RERANK_TIMEOUT", 0.05)
    idx.rerank_fn = Stub("slow")
    assert ids(run(idx.search_reranked(None, "gardening", settings=ON))) == ids(idx.search(None, "gardening", settings=ON))


def test_min_score_drops_low_rows(idx, monkeypatch) -> None:
    monkeypatch.setattr(ml, "RERANK_MIN_SCORE", 0.5)
    idx.rerank_fn = Stub()  # scores 1/6 .. 6/6: four rows reach 0.5
    out = run(idx.search_reranked(None, "gardening", settings=ON))
    assert len(out) == 4 and all(m["rerank_score"] >= 0.5 for m in out)


def test_rerank_model_resolution() -> None:
    assert providers.rerank_model({"provider": "fireworks"}) == "accounts/fireworks/models/qwen3-reranker-8b"
    assert providers.rerank_model({"provider": "litellm"}) == "qwen3-reranker-8b"
    assert providers.rerank_model({"provider": "openai"}) == ""
    assert providers.rerank_model({}) == ""
    assert providers.rerank_model({"provider": "openai", "retrievalRerankModel": " my-rr "}) == "my-rr"
    # a stale label loses to the base URL, as everywhere else
    assert providers.rerank_model({"provider": "openai", "baseUrl": "https://api.fireworks.ai/inference/v1"}).endswith("qwen3-reranker-8b")
    assert providers.rerank_model({"baseUrl": "http://localhost:4000"}) == "qwen3-reranker-8b"
