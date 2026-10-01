"""Text embeddings through the LiteLLM route, plus the small vector helpers retrieval needs.

Generic on purpose: nothing here knows about documents, chunks or memories. Callers hand over texts
and get vectors back, or None when embeddings are unavailable (no model configured, the route is
down, the model is not routed). None is a normal answer: every caller falls back to keyword search.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Sequence

import httpx
import numpy as np

from . import llm

log = logging.getLogger("grain.embed")

BATCH = 32
TIMEOUT = 10.0


class EmbedError(Exception):
    pass


def normalize(vec: Sequence[float] | np.ndarray) -> np.ndarray:
    """float32, unit length (a zero vector stays zero), so cosine similarity is a plain dot product."""
    v = np.asarray(vec, dtype="<f4")
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def pack(vec: Sequence[float] | np.ndarray) -> bytes:
    return normalize(vec).astype("<f4").tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def cosine(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    x, y = normalize(a), normalize(b)
    return float(x @ y) if x.shape == y.shape else 0.0


def rrf(lists: Sequence[Sequence[str]], weights: Sequence[float] | None = None, k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: each ranked list adds weight / (k + rank) to an id's score (rank is 1-based).

    Rank-based on purpose, since BM25 and cosine scores are not on one scale. Ties keep first-seen order."""
    ws = list(weights) if weights is not None else [1.0] * len(lists)
    scores: dict[str, float] = {}
    for ranked, w in zip(lists, ws):
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


async def embed_texts(settings: dict[str, Any], texts: list[str], model: str | None = None) -> list[list[float]]:
    """POST {baseUrl}/v1/embeddings (OpenAI shape), 32 texts per request. Raises EmbedError."""
    name = model or str(settings.get("embeddingModel") or "")
    if not name:
        raise EmbedError("no embedding model configured")
    out: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for i in range(0, len(texts), BATCH):
                batch = texts[i:i + BATCH]
                r = await client.post(f"{llm._base(settings)}/v1/embeddings", headers=llm._headers(settings),
                                      json={"model": name, "input": batch})
                if r.status_code >= 400:
                    raise EmbedError(f"{r.status_code}: {r.text[:200]}")
                rows = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
                if len(rows) != len(batch):
                    raise EmbedError("embedding count mismatch")
                out.extend(list(map(float, d["embedding"])) for d in rows)
    except EmbedError:
        raise
    except Exception as e:  # noqa: BLE001 - timeouts, refused connections, odd payloads
        raise EmbedError(str(e) or type(e).__name__) from e
    return out


EmbedFn = Callable[[dict[str, Any], list[str], "str | None"], Awaitable[list[list[float]]]]


class Embedder:
    """Wraps an injectable embed function (tests stub `fn`). `embed` never raises."""

    def __init__(self, fn: EmbedFn | None = None):
        self.fn: EmbedFn = fn or embed_texts

    @staticmethod
    def model(settings: dict[str, Any]) -> str:
        return str(settings.get("embeddingModel") or "")

    def available(self, settings: dict[str, Any]) -> bool:
        return bool(self.model(settings))

    async def embed(self, settings: dict[str, Any], texts: list[str]) -> list[np.ndarray] | None:
        """Unit vectors, one per text, or None when embeddings are unavailable."""
        if not texts:
            return []
        if not self.available(settings):
            return None
        try:
            vecs = await self.fn(settings, texts, self.model(settings))
        except EmbedError as e:
            log.warning("embeddings unavailable: %s", e)
            return None
        return [normalize(v) for v in vecs]
