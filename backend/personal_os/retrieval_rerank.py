"""Rerank fused retrieval candidates with a model before they are trimmed to the final top-N.

`rerank` never raises: on any failure (or while backed off after one) the fused order is returned
unchanged. It asks {baseUrl}/v1/rerank first and, when that route is absent, makes one completion
asking for candidate indices in relevance order.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from . import llm

log = logging.getLogger("grain.rerank")
TIMEOUT = 10.0
BACKOFF_SECONDS = 300.0
_down_until = 0.0


async def _route(settings: dict[str, Any], model: str, query: str, docs: list[str]) -> list[int] | None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(llm._url(settings, "/rerank"), headers=llm._headers(settings),
                              json={"model": model, "query": query, "documents": docs})
    if r.status_code in (404, 405, 501):
        return None  # no such route: use the completion fallback
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    res = sorted(r.json()["results"], key=lambda x: -float(x["relevance_score"]))
    return [int(x["index"]) for x in res]


async def _complete(settings: dict[str, Any], model: str, query: str, docs: list[str]) -> list[int]:
    listing = "\n".join(f"[{i}] {d[:600]}" for i, d in enumerate(docs))
    out = await llm.complete(settings, model, [{"role": "user", "content": (
        f"Query: {query}\n\nPassages:\n{listing}\n\nReturn a JSON list of passage numbers, most relevant to "
        "the query first. Answer with only the JSON list.")}], "rerank")
    m = re.search(r"\[[\d,\s]*\]", out)
    return [int(x) for x in json.loads(m.group(0))] if m else []


async def rerank(settings: dict[str, Any], query: str, fused: list[tuple[str, float]],
                 by_key: dict[str, dict[str, Any]]) -> list[tuple[str, float]]:
    global _down_until
    if time.monotonic() < _down_until:
        return fused
    n = max(1, int(settings.get("retrievalCandidates") or 20))
    head, tail = [x for x in fused[:n] if x[0] in by_key], fused[n:]
    model = str(settings["retrievalRerankModel"])
    docs = [str(by_key[k].get("text", "")) for k, _ in head]
    try:
        order = await _route(settings, model, query, docs)
        if order is None:
            order = await _complete(settings, model, query, docs)
    except Exception as e:  # noqa: BLE001 - timeouts, odd payloads, no route
        _down_until = time.monotonic() + BACKOFF_SECONDS
        log.warning("rerank unavailable, keeping fused order for %ds: %s", int(BACKOFF_SECONDS), e)
        return fused
    seen: list[int] = []
    for i in order:
        if 0 <= i < len(head) and i not in seen:
            seen.append(i)
    seen += [i for i in range(len(head)) if i not in seen]  # anything the model skipped keeps its place, after
    return [head[i] for i in seen] + tail
