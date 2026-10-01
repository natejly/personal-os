"""Structure-aware chunking: split by heading, carry the heading path and page, index a contextualised string.

`text` is what the user and the model read; `ctx` ("title > heading path\\ntext") is what goes into
FTS and the embedder, so a query that only matches a heading still finds the chunk beneath it
(the Docling contextualize() idea). Pure functions, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

MAX_CHARS = 1200
MIN_CHARS = 300
OVERLAP = 120


@dataclass
class Chunk:
    text: str
    heading: list[str] = field(default_factory=list)
    page: int | None = None
    ctx: str = ""

    @property
    def heading_str(self) -> str:
        return " > ".join(self.heading)


def contextualize(title: str, heading: str | Sequence[str], text: str) -> str:
    """The string that is indexed and embedded for a chunk. `heading` is a path list or 'A > B'."""
    path = heading if isinstance(heading, str) else " > ".join(heading)
    head = " > ".join(x for x in (title, path) if x)
    return f"{head}\n{text}" if head else text


def _hard_cut(s: str, size: int) -> list[str]:
    return [s[i:i + size] for i in range(0, len(s), size)]


def split_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Split on line then sentence boundaries into pieces of at most max_chars, each seeded with the
    tail of the previous one (word-aligned) so a sentence cut at the seam stays readable."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    units: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_chars:
            units.append(line)
            continue
        for sent in re.split(r"(?<=[.!?])\s+", line):
            units.extend(_hard_cut(sent, max_chars) if len(sent) > max_chars else [sent])
    pieces: list[str] = []
    cur = ""
    for u in units:
        joined = f"{cur}\n{u}" if cur else u
        if len(joined) <= max_chars:
            cur = joined
            continue
        if cur:
            pieces.append(cur)
        tail = cur[-overlap:] if overlap and cur else ""
        if tail and " " in tail:
            tail = tail.split(" ", 1)[1]
        cur = f"{tail}\n{u}" if tail and len(tail) + 1 + len(u) <= max_chars else u
    if cur.strip():
        pieces.append(cur)
    return pieces


def split_table(table: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split a pipe table by rows, repeating the header (and its --- rule) on every piece."""
    lines = [ln for ln in table.split("\n") if ln.strip()]
    n_head = 2 if len(lines) > 1 and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[1]) and "-" in lines[1] else 1
    head, rows = lines[:n_head], lines[n_head:]
    hlen = sum(len(h) + 1 for h in head)
    pieces: list[str] = []
    cur: list[str] = []
    size = hlen
    for r in rows:
        r = r[:max(1, max_chars - hlen - 1)]  # a single monstrous row still has to fit
        if cur and size + len(r) + 1 > max_chars:
            pieces.append("\n".join(head + cur))
            cur, size = [], hlen
        cur.append(r)
        size += len(r) + 1
    if cur or not pieces:
        pieces.append("\n".join(head + cur))
    return pieces


def _mergeable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Docling merge_peers analogue. Never across a top-level heading; only a child, or a peer under one parent."""
    pa, pb = a["path"], b["path"]
    if not pa or not pb or pa == pb:
        return False
    if len(pb) > len(pa) and pb[:len(pa)] == pa:
        return True
    return len(pa) == len(pb) >= 2 and pa[:-1] == pb[:-1]


def chunk_blocks(blocks: Sequence[dict[str, Any]], title: str = "", max_chars: int = MAX_CHARS,
                 min_chars: int = MIN_CHARS, overlap: int = OVERLAP) -> list[Chunk]:
    stack: list[tuple[int, str]] = []
    page: int | None = None
    # 1. per heading section: items = (text, page), already split so each fits
    sections: list[dict[str, Any]] = []

    def section() -> dict[str, Any]:
        path = [t for _, t in stack]
        if not sections or sections[-1]["path"] != path or sections[-1]["closed"]:
            sections.append({"path": path, "items": [], "closed": False})
        return sections[-1]

    for b in blocks:
        kind = b.get("kind")
        if b.get("page") is not None:
            page = b["page"]
        if kind == "page":
            continue
        text = (b.get("text") or "").strip()
        if not text:
            continue
        if kind == "heading" and b.get("level", 0) > 0:
            lvl = int(b["level"])
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, text))
            if sections:
                sections[-1]["closed"] = True
            continue
        if len(text) <= max_chars:
            parts = [text]
        elif kind == "table":
            parts = split_table(text, max_chars)
        else:
            parts = split_text(text, max_chars, overlap)
        sec = section()
        for part in parts:
            sec["items"].append((part, page))
    # a heading with nothing under it still anchors its children's path; nothing to emit for it.

    # 2. pack items into chunks within each section
    packed: list[dict[str, Any]] = []
    for sec in sections:
        cur: list[str] = []
        cur_page: int | None = None
        size = 0
        for text, pg in sec["items"]:
            if cur and size + len(text) + 2 > max_chars:
                packed.append({"path": sec["path"], "text": "\n\n".join(cur), "page": cur_page})
                cur, size = [], 0
            if not cur:
                cur_page = pg
            cur.append(text)
            size += len(text) + 2
        if cur:
            packed.append({"path": sec["path"], "text": "\n\n".join(cur), "page": cur_page})

    # 3. merge undersized neighbours that share structure
    merged: list[dict[str, Any]] = []
    for c in packed:
        prev = merged[-1] if merged else None
        if prev and len(prev["text"]) < min_chars and _mergeable(prev, c):
            label = c["path"][len(prev["path"]):] if c["path"][:len(prev["path"])] == prev["path"] else c["path"][-1:]
            body = "\n\n".join(([f"## {' > '.join(label)}"] if label else []) + [c["text"]])
            if len(prev["text"]) + len(body) + 2 <= max_chars:
                prev["text"] = f"{prev['text']}\n\n{body}"
                continue
        merged.append(dict(c))

    return [Chunk(text=m["text"], heading=m["path"], page=m["page"], ctx=contextualize(title, m["path"], m["text"])) for m in merged]
