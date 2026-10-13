"""Keyword variants for a recall query, for when embeddings are unavailable.

One short completion on the low tier turns the query into a few synonyms and related words, so the full-text search
can still reach a paraphrase ("car trouble" -> "vehicle", "repair", "mechanic"). Callers time-box it and fall back to
the plain query on any error.
"""
from __future__ import annotations

import re
from typing import Any

from . import llm, providers

MAX_TERMS = 10
SYSTEM = ("Reply with 6 to 10 single words or short phrases, comma separated, nothing else: synonyms and closely related "
          "terms a note about this topic would use instead of the query's own words.")


def parse(raw: str, query: str) -> list[str]:
    """The reply's terms: lowercased, de-duplicated, without the query's own words, at most MAX_TERMS."""
    own = set(re.findall(r"\w+", query.lower()))
    out: list[str] = []
    for t in re.split(r"[,\n;]", raw):
        t = " ".join(t.strip(" -*\"'.").lower().split())
        if t and len(t) <= 40 and t not in out and t not in own:
            out.append(t)
    return out[:MAX_TERMS]


async def expand(settings: dict[str, Any], query: str) -> list[str]:
    raw = await llm.complete(settings, providers.tier_model(settings, "low"),
                             [{"role": "system", "content": SYSTEM}, {"role": "user", "content": query[:500]}],
                             kind="recall", effort="low")
    return parse(raw, query)
