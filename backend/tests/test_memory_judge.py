"""Write-time merge judge: a reworded fact replaces its older rows instead of adding a second one."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from personal_os import learn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.embed import Embedder  # noqa: E402
from personal_os.memory_index import MemoryIndex  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402

CFG = {"embeddingModel": "fake-embed", "hybridRetrieval": True}
VEC: dict[str, list[float]] = {}
NEW = "User keeps answers under one hundred words"
OLD1 = "User wants short answers"
OLD2 = "User dislikes long replies, a hundred words at most"


async def fake_embed(settings: Any, texts: list[str], model: Any) -> list[list[float]]:
    return [VEC[t] for t in texts]


def unit(cos: float) -> list[float]:
    return [cos, float(np.sqrt(1 - cos ** 2)), 0.0]


class Env:
    def __init__(self, tmp: str) -> None:
        self.db = Database(tmp)
        self.memories, self.graph = Memories(self.db), Graph(self.db)
        self.index = MemoryIndex(self.db, self.memories, self.graph, Embedder(fake_embed))
        self.judge_calls = 0
        self.judge_reply: Any = None

    def run(self, extracted: list[dict[str, Any]]) -> dict[str, Any]:
        async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **kw: Any) -> str:
            if messages[0]["content"].startswith(learn.JUDGE_PROMPT[:20]):
                self.judge_calls += 1
                if isinstance(self.judge_reply, Exception):
                    raise self.judge_reply
                return self.judge_reply if isinstance(self.judge_reply, str) else json.dumps(self.judge_reply)
            return json.dumps({"memories": extracted})

        learn.llm.complete = fake_complete  # restored by the fixture
        VEC.setdefault("hi", unit(0.0))
        return asyncio.run(learn.learn_from_exchange(
            settings=CFG, memories=self.memories, graph=self.graph, project_id=None, user_text="hi",
            assistant_text="ok", model="m", index=self.index))


@pytest.fixture()
def env():
    VEC.clear()
    orig = learn.llm.complete
    with tempfile.TemporaryDirectory() as tmp:
        e = Env(tmp)
        VEC.update({NEW: unit(1.0), OLD1: unit(0.8), OLD2: unit(0.75)})
        e.old1 = e.memories.create(None, OLD1, kind="preference")
        e.old2 = e.memories.create(None, OLD2, kind="preference")
        asyncio.run(e.index.index(CFG))
        yield e
    learn.llm.complete = orig


def live(e: Env) -> list[str]:
    return [m["content"] for m in e.memories.list(None)]


def test_paraphrase_merges_and_supersedes_both_targets(env: Env) -> None:
    merged = "User wants answers under one hundred words"
    env.judge_reply = {"decisions": [{"n": 1, "action": "merge", "target_ids": ["C1", "C2"], "content": merged, "kind": "preference"}]}
    out = env.run([{"content": NEW, "kind": "fact"}])
    assert env.judge_calls == 1
    assert live(env) == [merged]
    new = out["updated"][0]
    assert {s["old_id"] for s in out["superseded"]} == {env.old1["id"], env.old2["id"]}
    for old in (env.old1, env.old2):  # history kept, never deleted
        row = env.memories.get(old["id"])
        assert row["invalid_at"] is not None and row["superseded_by"] == new["id"]
    assert new["kind"] == "preference" and new["created_at"] == min(env.old1["created_at"], env.old2["created_at"])


def test_skip_stores_nothing(env: Env) -> None:
    env.judge_reply = {"decisions": [{"n": 1, "action": "skip"}]}
    out = env.run([{"content": NEW, "kind": "fact"}])
    assert out["memories"] == [] and out["updated"] == []
    assert sorted(live(env)) == sorted([OLD1, OLD2])


@pytest.mark.parametrize("reply", ["not json at all", RuntimeError("provider down"), asyncio.TimeoutError()])
def test_failure_falls_back_to_storing(env: Env, reply: Any) -> None:
    env.judge_reply = reply
    out = env.run([{"content": NEW, "kind": "fact"}])
    assert [m["content"] for m in out["memories"]] == [NEW]
    assert len(live(env)) == 3


def test_foreign_ids_are_ignored(env: Env) -> None:
    env.judge_reply = {"decisions": [{"n": 1, "action": "merge", "target_ids": ["C9", env.old1["id"], "M1"],
                                      "content": "User wants answers under one hundred words"}]}
    out = env.run([{"content": NEW, "kind": "fact"}])
    assert [m["content"] for m in out["memories"]] == [NEW]  # nothing resolvable: stored as usual
    assert env.memories.get(env.old1["id"])["invalid_at"] is None


def test_invented_merge_text_is_rejected(env: Env) -> None:
    env.judge_reply = {"decisions": [{"n": 1, "action": "merge", "target_ids": ["C1"],
                                      "content": "User lives in Lisbon and owns three sailboats"}]}
    out = env.run([{"content": NEW, "kind": "fact"}])
    assert [m["content"] for m in out["memories"]] == [NEW]
    assert env.memories.get(env.old1["id"])["invalid_at"] is None


def test_no_close_candidate_makes_no_llm_call(env: Env) -> None:
    VEC["User owns a red bicycle"] = [0.0, 0.0, 1.0]
    out = env.run([{"content": "User owns a red bicycle", "kind": "fact"}])
    assert env.judge_calls == 0
    assert [m["content"] for m in out["memories"]] == ["User owns a red bicycle"]
