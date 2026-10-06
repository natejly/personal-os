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

# The real function over a stubbed HTTP layer and completion.
import httpx  # noqa: E402

from personal_os import llm  # noqa: E402

fused = [("files:a", 1.0), ("files:b", 0.5), ("files:c", 0.2)]
by_key = {k: {"text": k} for k, _ in fused}
fused_keys = [k for k, _ in fused]
rs = {**on, "baseUrl": "http://rr.test", "apiKey": "k"}
posts: list[Any] = []
reply: dict[str, Any] = {}
real_post, real_complete = httpx.AsyncClient.post, llm.complete


async def fake_post(self: Any, url: str, **kw: Any) -> httpx.Response:
    posts.append((url, kw))
    if "err" in reply:
        raise httpx.ConnectError("boom")
    return httpx.Response(reply["status"], json=reply.get("json", {}), request=httpx.Request("POST", url))


completions: list[str] = []
completion_out = ["[2, 0, 1]"]


async def fake_complete(*a: Any, **kw: Any) -> str:
    completions.append("x")
    return completion_out[0]


httpx.AsyncClient.post = fake_post
llm.complete = fake_complete


def keys() -> list[str]:
    retrieval_rerank._down_until = 0.0
    return [k for k, _ in run(retrieval_rerank.rerank(rs, "q", fused, by_key))]


reply.update(status=200, json={"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]})
check(keys() == ["files:b", "files:a", "files:c"], "route order applied")
check(posts[-1][0] == "http://rr.test/v1/rerank" and not completions, "posts to {base}/v1/rerank, no completion")
check(any(k.lower() == "authorization" for k in posts[-1][1]["headers"]), "auth header sent")

reply.update(status=404, json={})
check(keys() == ["files:c", "files:a", "files:b"] and completions, "404 falls back to completion indices")
completion_out[0] = "no idea"
check(keys() == fused_keys, "malformed completion keeps fused order")

reply.update(status=500, json={})
check(keys() == fused_keys, "server error keeps fused order")
n = len(posts)
check([k for k, _ in run(retrieval_rerank.rerank(rs, "q", fused, by_key))] == fused_keys and len(posts) == n,
      "backed off: no further request")
reply.clear()
reply["err"] = 1
check(keys() == fused_keys, "network error keeps fused order")
httpx.AsyncClient.post, llm.complete = real_post, real_complete
print(f"test_rerank: {passed} checks passed")
