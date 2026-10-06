"""web_search: a misbehaving keyed engine retries (429/5xx) or falls through to the keyless engines; queries and counts are clamped.

Run: PYTHONPATH=backend python backend/tests/test_websearch_fallback.py"""
from __future__ import annotations

import asyncio

import httpx

from personal_os import websearch as ws

ws.RETRY_DELAYS = (0, 0)
calls: list[tuple[str, int]] = []


def status(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=httpx.Response(code))


async def run(cfg: dict, query: str = "q", want: int = 6):
    return await ws.search(cfg, query, want)


# 422 from Brave: no retry, results from the fallback, meta names the 422
async def brave422(cfg, q, n, tr):
    calls.append(("brave", n))
    raise status(422)


async def fallback(cfg, q, n, tr):
    return [{"title": "t", "url": "https://a.example/x", "snippet": "s"}]

ws.brave, ws.exa = brave422, fallback
rows, meta = asyncio.run(run({"braveApiKey": "k"}))
assert rows and meta["engines_used"] == ["exa"] and "422" in meta["failed"]["brave"], meta
assert len(calls) == 1, calls

# 429 then 200: retried and answered by Brave
n429 = 0


async def flaky(cfg, q, n, tr):
    global n429
    n429 += 1
    if n429 == 1:
        raise status(429)
    return [{"title": "b", "url": "https://b.example", "snippet": ""}]

ws.brave = flaky
rows, meta = asyncio.run(run({"braveApiKey": "k"}))
assert n429 == 2 and meta["engines_used"] == ["brave"] and "failed" not in meta, meta

# clamping: 51 words -> 50, long query -> 400 chars, count 25 -> 20 at the real Brave call
assert len(ws.clamp_query(" ".join(["w"] * 51)).split()) == 50
assert len(ws.clamp_query("x" * 900)) == 400
seen: dict = {}


class FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def get(self, url, params=None, headers=None):
        seen.update(params)
        return httpx.Response(200, json={"web": {"results": []}}, request=httpx.Request("GET", url))

orig = httpx.AsyncClient
httpx.AsyncClient = FakeClient
import importlib
ws2 = importlib.reload(ws)
asyncio.run(ws2.brave({"braveApiKey": "k"}, "q", 25, ""))
httpx.AsyncClient = orig
assert seen["count"] == 20, seen
print("ok")
