"""Tool-call bookkeeping that does not depend on the app: ids, argument repair and name resolution."""
from __future__ import annotations

import difflib
import json
import re
import uuid
from typing import Any


def ensure_unique_call_ids(calls: list[dict[str, Any]], seen: set[str]) -> int:
    """Give every call an id no earlier call in this reply used. A provider restarts its numbering each round (and
    a gateway may send none), but a tool message is matched to its call by id, and the journal, the approvals and
    the transcript key on it too. Rewrites in place, adds every id to `seen`, returns how many were replaced."""
    changed = 0
    for c in calls:
        cid = c.get("id") or ""
        if not cid or cid in seen:
            cid = "call_" + uuid.uuid4().hex[:12]
            c["id"] = cid
            changed += 1
        seen.add(cid)
    return changed


_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.S)
_VALID_ESCAPE = frozenset('"\\/bfnrtu')


def _strip_trailing_commas(text: str) -> str:
    """Drop a comma that sits directly before a closing brace or bracket. String-aware: a comma inside a
    string value is never touched."""
    out: list[str] = []
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                continue
        out.append(ch)
    return "".join(out)


def _double_stray_backslashes(text: str) -> str:
    """Double a backslash that does not start a valid JSON escape (a path or a pattern written bare)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt and nxt in _VALID_ESCAPE:
                out.append(ch + nxt)
                i += 2
                continue
            out.append("\\\\")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse_arguments(raw: str | None) -> tuple[dict[str, Any] | None, bool, str | None]:
    """A streamed call's arguments as `(args, repaired, problem)`. Tries the text as sent, then a ladder of
    mechanical repairs (a second decode, a code fence, the outermost braces, trailing commas, stray backslashes),
    stopping at the first that yields an object. Text that was cut off is never completed: closing a truncated
    string would run a write with partial content. A failure is `(None, False, problem)`."""
    text = raw if raw and raw.strip() else "{}"
    first_error: str | None = None
    not_object: str | None = None

    def attempt(candidate: str, twice: bool) -> dict[str, Any] | None:
        nonlocal first_error, not_object
        try:
            v = json.loads(candidate)
            if twice and isinstance(v, str):  # double-encoded: the object was serialised twice
                v = json.loads(v)
        except ValueError as e:
            first_error = first_error or str(e).splitlines()[0]
            return None
        if isinstance(v, dict):
            return v
        not_object = not_object or f"arguments must be a JSON object, got {type(v).__name__}"
        return None

    v = attempt(text, False)
    if v is not None:
        return v, False, None
    v = attempt(text, True)
    if v is not None:
        return v, True, None
    stages: list[str] = []
    cur = text.strip()
    if (m := _FENCE.match(cur)):
        cur = m.group(1).strip()
        stages.append(cur)
    lo, hi = cur.find("{"), cur.rfind("}")
    if 0 <= lo < hi:
        cur = cur[lo:hi + 1]
        stages.append(cur)
    stages.append(_strip_trailing_commas(cur))
    stages.append(_double_stray_backslashes(stages[-1]))
    seen = {text}
    for cand in stages:
        if cand in seen:
            continue
        seen.add(cand)
        v = attempt(cand, False)
        if v is not None:
            return v, True, None
    return None, False, first_error or not_object or "arguments were not valid JSON"


def resolve_name(name: str, known: set[str], advertised: list[str]) -> tuple[str, bool, str | None]:
    """A call's tool name as `(name, resolved, problem)`. An exact hit is returned as is; a name that differs by
    case, surrounding space or one leading `namespace.` segment resolves only when exactly one advertised tool
    matches. Anything else is a problem naming the closest advertised tools (padded to eight)."""
    if name in known:
        return name, False, None
    norm = (name or "").strip().casefold()
    if "." in norm:
        norm = norm.split(".", 1)[1]
    if norm:
        hits = sorted({a for a in advertised if a.casefold() == norm})
        if len(hits) == 1:
            return hits[0], True, None
    shown = (name or "").strip()[:80]
    close = difflib.get_close_matches(shown, advertised, n=8, cutoff=0.3) if shown else []
    for a in sorted(advertised):
        if len(close) >= 8:
            break
        if a not in close:
            close.append(a)
    head = f"Unknown tool {shown}." if shown else "The tool call had no name."
    return name, False, f"{head} Did you mean: {', '.join(close)}" if close else head
