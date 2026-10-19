"""Pure logic of the history digest: leaf text, the aligned power-of-two blocks over a scope's log, which merge is due, the cover
(the set of blocks a digest shows), and a tiny BM25 over node summaries. No database, no model: everything here is deterministic.

A block (level L, k) covers the leaf ordinals [k * 2^L, (k + 1) * 2^L). It has a node once all of its ordinals exist, so a log of n
leaves holds the nodes with (k + 1) * 2^L <= n. A tombstoned leaf keeps its ordinal (position is identity) and counts as an empty slot.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

LEAF_MAX_BYTES = 280
RAW_MAX_LEVEL = 4  # blocks up to 16 leaves are summarised from the raw leaves; larger ones from their two child summaries
UNVERIFIED = "[unverified]"
PERSONAL = "personal"


def clip(text: str, limit: int = LEAF_MAX_BYTES) -> str:
    """One line of at most `limit` UTF-8 bytes: whitespace collapsed, cut on a character boundary."""
    one = " ".join(str(text).split())
    return one if len(one.encode("utf-8")) <= limit else one.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip()


def day(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


def leaf_text(content: str, ts: float | None) -> str:
    """The stored line of a memory: its date, then its words, in at most 280 bytes."""
    one = " ".join(str(content).split())
    d = day(ts)
    return clip(f"{d}: {one}" if d else one)


def scope_name(project_id: str | None) -> str:
    return PERSONAL if project_id is None else f"project:{project_id}"


def scope_project(scope: str) -> str | None:
    return None if scope == PERSONAL else scope.removeprefix("project:")


def valid_scope(scope: str) -> bool:
    return scope == PERSONAL or (scope.startswith("project:") and len(scope) > len("project:"))


def level_of(size: int) -> int:
    return size.bit_length() - 1


def parse_block(text: str, n: int) -> tuple[int, int]:
    """'lo-hi' as an aligned power-of-two block inside the first `n` leaves, else ValueError saying what is wrong."""
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(text))
    if not m:
        raise ValueError("a range is written lo-hi, for example 0-8")
    lo, hi = int(m[1]), int(m[2])
    size = hi - lo
    if size < 1 or size & (size - 1):
        raise ValueError("a range covers a power of two of entries (1, 2, 4, 8, ...)")
    if lo % size:
        raise ValueError(f"a block of {size} starts at a multiple of {size}")
    if hi > n:
        raise ValueError(f"the log has {n} entries, so {lo}-{hi} is not complete yet" if lo < n else f"the log has only {n} entries")
    return lo, hi


@dataclass(frozen=True)
class Leaf:
    ord: int
    text: str
    ts: float | None
    tombstoned: bool = False
    tainted: bool = False

    @property
    def line(self) -> str:
        return self.text + (f" {UNVERIFIED}" if self.tainted else "")


@dataclass(frozen=True)
class Node:
    level: int
    k: int
    summary: str
    hash: str = ""
    stale: bool = False
    updated_at: float | None = None


def sha(parts: list[str]) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def raw_parts(leaves: list[Leaf], lo: int, hi: int) -> list[str]:
    return [f"{x.ord}\t{x.line}" for x in leaves[lo:hi] if not x.tombstoned]


def inputs(level: int, k: int, leaves: list[Leaf], nodes: dict[tuple[int, int], Node]) -> tuple[list[str], bool]:
    """What the merge of block (level, k) reads, and whether it is all there: the live leaf lines up to size 16, else the two child summaries."""
    if level <= RAW_MAX_LEVEL:
        size = 1 << level
        return [x.line for x in leaves[k * size:(k + 1) * size] if not x.tombstoned], True
    kids = [nodes.get((level - 1, 2 * k)), nodes.get((level - 1, 2 * k + 1))]
    if None in kids:
        return [], False
    return [c.summary for c in kids if c and c.summary], True  # type: ignore[union-attr]


def input_hash(level: int, k: int, leaves: list[Leaf], nodes: dict[tuple[int, int], Node]) -> str | None:
    """The hash a node built from today's inputs carries; None while an input is missing."""
    if level <= RAW_MAX_LEVEL:
        size = 1 << level
        return sha(raw_parts(leaves, k * size, (k + 1) * size))
    parts, ready = inputs(level, k, leaves, nodes)
    return sha(parts) if ready else None


def pending(leaves: list[Leaf], nodes: dict[tuple[int, int], Node]) -> Iterator[tuple[int, int, bool]]:
    """Every block that needs a merge, smallest level first and oldest first: (level, k, ready). A block is missing or its inputs changed;
    `ready` is False while a child summary it reads is not built yet."""
    n = len(leaves)
    level = 1
    while (1 << level) <= n:
        for k in range(n >> level):
            node = nodes.get((level, k))
            want = input_hash(level, k, leaves, nodes)
            if node is None:
                yield level, k, want is not None
            elif want is not None and want != node.hash:
                yield level, k, True
        level += 1


def unchanged_stale(leaves: list[Leaf], nodes: dict[tuple[int, int], Node]) -> list[tuple[int, int]]:
    """Nodes flagged stale whose inputs still hash the same and whose children are settled: the flag can simply be cleared, no model call."""
    blocked = {(level, k) for level, k, _ in pending(leaves, nodes)}
    out: list[tuple[int, int]] = []
    for (level, k), node in sorted(nodes.items()):
        if not node.stale or (k + 1) << level > len(leaves):
            continue
        kids = [(level - 1, 2 * k), (level - 1, 2 * k + 1)] if level > RAW_MAX_LEVEL else []
        if input_hash(level, k, leaves, nodes) == node.hash and not any(c in blocked for c in kids):
            out.append((level, k))
        else:
            blocked.add((level, k))
    return out


def next_due(leaves: list[Leaf], nodes: dict[tuple[int, int], Node]) -> tuple[int, int] | None:
    for level, k, ready in pending(leaves, nodes):
        if ready:
            return level, k
    return None


# ---- the cover ----
@dataclass(frozen=True)
class Unit:
    """One entry of a cover: a leaf, or a node shown whole."""
    lo: int
    hi: int
    text: str  # the line without its range
    ts: float | None  # newest live leaf
    tainted: bool
    stale: bool = False

    @property
    def size(self) -> int:
        return self.hi - self.lo

    @property
    def kind(self) -> str:
        return "leaf" if self.size == 1 else "node"

    @property
    def line(self) -> str:
        return f"[{self.lo}-{self.hi}) {self.text}" + (f" {UNVERIFIED}" if self.tainted else "")


def tokens(text: str) -> int:
    from ...context import estimate_tokens
    return estimate_tokens(text) + 1  # the newline between lines


class Cover:
    """The digest of one scope's log: build with `fit(budget)`."""

    def __init__(self, leaves: list[Leaf], nodes: dict[tuple[int, int], Node], estimate: Callable[[str], int] = tokens) -> None:
        self.leaves, self.nodes, self.n, self.estimate = leaves, nodes, len(leaves), estimate
        self._span: dict[tuple[int, int], tuple[float | None, float | None, bool]] = {}

    # (oldest ts, newest ts, any tainted) of the live leaves in [lo, hi)
    def span(self, lo: int, hi: int) -> tuple[float | None, float | None, bool]:
        key = (lo, hi)
        if key not in self._span:
            if hi - lo == 1:
                x = self.leaves[lo]
                self._span[key] = (None, None, False) if x.tombstoned else (x.ts, x.ts, x.tainted)
            else:
                mid = (lo + hi) // 2
                a, b = self.span(lo, mid), self.span(mid, hi)
                olds = [t for t in (a[0], b[0]) if t is not None]
                news = [t for t in (a[1], b[1]) if t is not None]
                self._span[key] = (min(olds) if olds else None, max(news) if news else None, a[2] or b[2])
        return self._span[key]

    def node_text(self, lo: int, hi: int, summary: str) -> str:
        old, new, _ = self.span(lo, hi)
        a, b = day(old), day(new)
        head = "" if not a else f"{a}: " if a == b else f"{a}..{b}: "
        return head + summary

    def unit(self, lo: int, hi: int) -> Unit | None:
        """The block as one entry: a leaf, or its node with a summary. None for a tombstoned leaf or an empty node."""
        if hi - lo == 1:
            x = self.leaves[lo]
            return None if x.tombstoned else Unit(lo, hi, x.text, x.ts, x.tainted)
        node = self.nodes.get((level_of(hi - lo), lo // (hi - lo)))
        if node is None or not node.summary:
            return None
        _, new, tainted = self.span(lo, hi)
        return Unit(lo, hi, self.node_text(lo, hi, node.summary), new, tainted, node.stale)

    def available(self, lo: int, hi: int) -> bool:
        if hi - lo == 1:
            return True
        node = self.nodes.get((level_of(hi - lo), lo // (hi - lo)))
        return node is not None  # an empty node (every leaf tombstoned) is available and shows nothing

    def expand(self, lo: int, hi: int, alpha: float) -> list[Unit]:
        """The coarsest covering of [lo, hi): a block stays whole when it has a node and its size is at most alpha times its age
        (distance of its newest leaf from the newest one, plus one); otherwise it is shown as its halves."""
        size = hi - lo
        if size == 1:
            u = self.unit(lo, hi)
            return [u] if u else []
        if self.available(lo, hi) and size <= alpha * (self.n - hi + 1):
            u = self.unit(lo, hi)
            return [u] if u else []
        mid = (lo + hi) // 2
        return self.expand(lo, mid, alpha) + self.expand(mid, hi, alpha)

    def maximal(self) -> list[tuple[int, int]]:
        out, p = [], 0
        while p < self.n:
            size = (p & -p) or 1 << self.n.bit_length()
            while p + size > self.n:
                size >>= 1
            out.append((p, p + size))
            p += size
        return out

    def at(self, alpha: float) -> list[Unit]:
        return [u for lo, hi in self.maximal() for u in self.expand(lo, hi, alpha)]

    def cost(self, units: list[Unit]) -> int:
        return sum(self.estimate(u.line) for u in units)

    def fit(self, budget: int) -> list[Unit]:
        """The units that tile the log within `budget` tokens, finest near the present. Everything verbatim when it fits;
        otherwise the smallest alpha that fits, then the spare budget spent on splitting the blocks nearest the present."""
        if self.n == 0:
            return []
        units = self.at(0.0)
        if self.cost(units) <= budget:
            return units
        hi = float(self.n + 1)
        units = self.at(hi)
        if self.cost(units) > budget:
            return self._trim(units, budget)
        lo = 0.0
        for _ in range(48):
            mid = (lo + hi) / 2
            if self.cost(self.at(mid)) <= budget:
                hi = mid
            else:
                lo = mid
        units = self.at(hi)
        spare = budget - self.cost(units)
        while True:
            for i in range(len(units) - 1, -1, -1):  # nearest the present first
                u = units[i]
                if u.size == 1:
                    continue
                mid = (u.lo + u.hi) // 2
                halves = self.expand(u.lo, mid, math.inf) + self.expand(mid, u.hi, math.inf)
                delta = self.cost(halves) - self.cost([u])
                if delta <= spare:
                    units[i:i + 1] = halves
                    spare -= delta
                    break
            else:
                return units

    def _trim(self, units: list[Unit], budget: int) -> list[Unit]:
        """Not even the coarsest tiling fits: the oldest entries go first."""
        units = list(units)
        while len(units) > 1 and self.cost(units) > budget:
            units.pop(0)
        return units


# ---- BM25 over node summaries ----
_WORD = re.compile(r"\w+", re.U)


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1]


def bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """BM25 score of each document for the query."""
    q = set(words(query))
    toks = [words(d) for d in docs]
    if not q or not toks:
        return [0.0] * len(docs)
    avg = (sum(len(t) for t in toks) / len(toks)) or 1.0
    df = {w: sum(1 for t in toks if w in t) for w in q}
    out = []
    for t in toks:
        score = 0.0
        for w in q:
            tf = t.count(w)
            if tf:
                idf = math.log(1 + (len(toks) - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(t) / avg))
        out.append(score)
    return out
