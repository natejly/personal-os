"""Deferred MCP tool search: keep third-party tool schemas out of the prompt until the model asks.

A big connector (GitHub's is about 90 tools) pushes the tools array past the point where weak
models still pick the right one, and costs thousands of tokens on every round. Past a threshold the
chat loop offers only `mcp_tool_search`; a search loads the best matches into the next round.

Everything here is pure: no I/O, no model, no store. Grants, ask mode, the taint rule and the
schema-hash decay all key off the slug in app.py and are untouched by whether a tool is loaded.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

MAX_QUERY = 500
MAX_LIMIT = 10
DEFAULT_LIMIT = 5

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens of at least two characters; '_' and '__' split like any other separator."""
    return [t for t in _WORD.findall((text or "").lower()) if len(t) >= 2]


@dataclass
class Doc:
    slug: str
    tokens: list[str]


def _props(parameters: Any) -> dict[str, Any]:
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    return props if isinstance(props, dict) else {}


def build_docs(tools: Iterable[dict[str, Any]], server_names: dict[str, str] | None = None) -> list[Doc]:
    """One searchable doc per tool: slug x3, description, argument names x2, argument descriptions, server name."""
    out: list[Doc] = []
    for t in tools:
        slug = str(t.get("slug") or "")
        if not slug:
            continue
        toks = tokenize(slug) * 3 + tokenize(str(t.get("description") or ""))
        for arg, spec in _props(t.get("parameters")).items():
            toks += tokenize(str(arg)) * 2
            if isinstance(spec, dict):
                toks += tokenize(str(spec.get("description") or ""))
        toks += tokenize(str(t.get("server") or (server_names or {}).get(str(t.get("server_id") or "")) or ""))
        out.append(Doc(slug, toks))
    return out


def bm25_search(docs: list[Doc], query: str, limit: int = DEFAULT_LIMIT, k1: float = 1.5, b: float = 0.75) -> list[tuple[str, float]]:
    """Okapi BM25. Zero scores are dropped; ties break by slug so results are stable."""
    q = tokenize((query or "")[:MAX_QUERY])
    limit = max(1, min(int(limit), MAX_LIMIT))
    if not q or not docs:
        return []
    n = len(docs)
    avg = (sum(len(d.tokens) for d in docs) / n) or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for term in set(d.tokens):
            df[term] = df.get(term, 0) + 1
    scored: list[tuple[str, float]] = []
    for d in docs:
        tf: dict[str, int] = {}
        for term in d.tokens:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in set(q):
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d.tokens) / avg))
        if score > 0:
            scored.append((d.slug, score))
    scored.sort(key=lambda p: (-p[1], p[0]))
    return scored[:limit]


def should_defer(n_tools: int, threshold: int) -> bool:
    return threshold > 0 and n_tools > threshold


def catalog_hint(servers_with_counts: Iterable[tuple[str, int]]) -> str:
    parts = [f"{name} ({n} tool{'s' if n != 1 else ''})" for name, n in servers_with_counts]
    if not parts:
        return ""
    return "MCP connectors available via mcp_tool_search: " + ", ".join(parts) + "."


def select_schemas(all_schemas: list[dict[str, Any]], loaded: Iterable[str], defer: bool) -> list[dict[str, Any]]:
    """The MCP schemas to offer this round: all of them, or only those a search has loaded."""
    if not defer:
        return all_schemas
    keep = set(loaded)
    return [s for s in all_schemas if s["function"]["name"] in keep]
