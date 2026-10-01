"""Search/replace patches for artifacts (Vercel `editDocument` semantics).

A small change to a long document should cost a small response, not a whole rewrite that drifts. The model
sends ordered {search, replace} pairs; each `search` must match exactly one place in the running text. The
patch is atomic: any bad edit raises PatchError and the caller keeps the original code untouched.
"""
from __future__ import annotations

import re
from typing import Any

from .artifacts import _check_code

MAX_EDITS = 20
MAX_PATCH_CHARS = 60_000


class PatchError(ValueError):
    def __init__(self, index: int, reason: str, snippet: str = ""):
        self.index, self.reason, self.snippet = index, reason, snippet
        super().__init__(f"edit {index}: {reason}" + (f" (nearby: {snippet!r})" if snippet else ""))


def _fuzzy_span(code: str, search: str) -> tuple[int, int] | None | str:
    """One whitespace-tolerant match as a (start, end) span in `code`, None for no match, 'many' for several."""
    want = [ln.strip() for ln in search.strip().split("\n")]
    if not any(want):
        return None
    lines = code.split("\n")
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    hits = [i for i in range(len(lines) - len(want) + 1)
            if all(lines[i + k].strip() == want[k] for k in range(len(want)))]
    if not hits:
        return None
    if len(hits) > 1:
        return "many"
    i = hits[0]
    end = starts[i + len(want) - 1] + len(lines[i + len(want) - 1])
    return starts[i], end


def _nearby(code: str, search: str) -> str:
    """The line that shares the most with the failed search, so the model can see what it should have quoted."""
    first = next((ln.strip() for ln in search.split("\n") if ln.strip()), "")
    if not first:
        return ""
    words = set(re.findall(r"\w+", first))
    best = max(code.split("\n"), key=lambda ln: len(words & set(re.findall(r"\w+", ln))), default="")
    return best.strip()[:160]


def apply_patch(code: str, edits: list[dict[str, Any]]) -> str:
    """Apply ordered exact-match edits to `code`. Raises PatchError; never returns a partial result."""
    if not isinstance(edits, list) or not edits:
        raise PatchError(0, "no edits given")
    if len(edits) > MAX_EDITS:
        raise PatchError(MAX_EDITS, f"too many edits ({len(edits)}); the limit is {MAX_EDITS} - use a rewrite instead")
    total = 0
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or not isinstance(e.get("search"), str) or not isinstance(e.get("replace", ""), str):
            raise PatchError(i, "each edit needs string 'search' and 'replace'")
        total += len(e["search"]) + len(e.get("replace") or "")
    if total > MAX_PATCH_CHARS:
        raise PatchError(0, f"edits total {total} characters, over the {MAX_PATCH_CHARS} limit - use a rewrite instead")
    cur = code
    for i, e in enumerate(edits):
        search, repl = e["search"], e.get("replace") or ""
        if not search.strip():
            raise PatchError(i, "search is empty")
        n = cur.count(search)
        if n == 1:
            at = cur.index(search)
            cur = cur[:at] + repl + cur[at + len(search):]
            continue
        if n > 1:
            raise PatchError(i, f"search matches {n} places; include more surrounding text so it is unique", _nearby(cur, search))
        span = _fuzzy_span(cur, search)
        if span == "many":
            raise PatchError(i, "search matches several places after ignoring indentation; include more surrounding text", _nearby(cur, search))
        if span is None:
            raise PatchError(i, "search text not found; quote the current document exactly", _nearby(cur, search))
        a, b = span  # type: ignore[misc]
        cur = cur[:a] + repl + cur[b:]
    try:
        return _check_code(cur)
    except ValueError as e:
        raise PatchError(len(edits) - 1, str(e)) from e
