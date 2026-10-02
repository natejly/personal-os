"""Rerank of fused candidates. Offline (stub rerank function).
Run: python backend/tests/test_rerank.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="rrtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import retrieval_rerank  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, retriever, settings  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


for i in range(5):
    client.post("/documents", files={"file": (f"f{i}.txt", f"gadget number {i} " + "gadget " * (5 - i), "text/plain")})
S = {**settings(), "retrievalMode": "bm25", "retrievalPerDocCap": 0}
calls: list[str] = []


async def reverse(s: Any, q: str, fused: list, by_key: dict) -> list:
    calls.append(q)
    return list(reversed(fused))


retriever.rerank_fn = reverse
base = [h["chunk_id"] for h in run(retriever.search(None, "gadget", S, limit=4))]
check(len(base) == 4 and not calls, "off never calls the stub")
on = {**S, "retrievalRerank": True, "retrievalRerankModel": "rr"}
full = [h["chunk_id"] for h in run(retriever.search(None, "gadget", S, limit=10))]
got = [h["chunk_id"] for h in run(retriever.search(None, "gadget", on, limit=4))]
check(calls and got == list(reversed(full))[:4], "reversed candidates trimmed to limit")
check(len(run(retriever.search(None, "gadget", {**on, "retrievalPerDocCap": 1}, limit=10))) == 5, "cap still applies after rerank")

# The real function: a failing route keeps fused order and backs off.
fused = [("files:a", 1.0), ("files:b", 0.5)]
by_key = {"files:a": {"text": "a"}, "files:b": {"text": "b"}}


async def down(*a: Any) -> Any:
    raise RuntimeError("down")


retrieval_rerank._route = down
check(run(retrieval_rerank.rerank(on, "q", fused, by_key)) == fused, "failure keeps the fused order")


async def route(*a: Any) -> list[int]:
    return [1, 0]


retrieval_rerank._route = route
check(run(retrieval_rerank.rerank(on, "q", fused, by_key)) == fused, "backed off after a failure")
retrieval_rerank._down_until = 0.0
check([k for k, _ in run(retrieval_rerank.rerank(on, "q", fused, by_key))] == ["files:b", "files:a"], "route order applied")
print(f"test_rerank: {passed} checks passed")
