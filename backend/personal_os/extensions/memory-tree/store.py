"""SQLite access of the history digest: its two tables, nothing else. `db` is the extension's own (scoped) database."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from .tree import Leaf, Node


class TreeStore:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.version = 0  # bumped by every write, so a reader can keep a snapshot until it changes

    @contextmanager
    def _write(self) -> Iterator[Any]:
        """A transaction that bumps `version` on both sides, so a snapshot taken while it runs is never kept."""
        self.version += 1
        try:
            with self.db.tx() as c:
                yield c
        finally:
            self.version += 1

    # ---- leaves ----
    def append(self, scope: str, memory_id: str, text: str, ts: float | None, tainted: bool = False) -> int:
        """Add a leaf at the end of the scope's log; returns its ordinal. Dense per scope: the next ordinal is the count."""
        with self._write() as c:
            c.execute("BEGIN IMMEDIATE")
            ord_ = c.execute("SELECT COALESCE(MAX(ord) + 1, 0) FROM memory_tree_leaf WHERE scope=?", (scope,)).fetchone()[0]
            c.execute("INSERT INTO memory_tree_leaf(memory_id, scope, ord, text, ts, tainted) VALUES(?,?,?,?,?,?)",
                      (memory_id, scope, ord_, text, ts, int(tainted)))
        return int(ord_)

    def live_leaf(self, memory_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM memory_tree_leaf WHERE memory_id=? AND tombstoned=0 ORDER BY seq DESC LIMIT 1", (memory_id,)).fetchone()
        return dict(r) if r else None

    def tombstone(self, scope: str, ord_: int) -> None:
        """Empty the slot and mark every node above it stale: their summaries stay until they are rebuilt."""
        with self._write() as c:
            c.execute("UPDATE memory_tree_leaf SET tombstoned=1 WHERE scope=? AND ord=?", (scope, ord_))
            c.execute("UPDATE memory_tree_node SET stale=1 WHERE scope=? AND k = (? >> level)", (scope, ord_))

    def leaves(self, scope: str) -> list[Leaf]:
        with self.db.tx() as c:
            rows = c.execute("SELECT ord, text, ts, tombstoned, tainted FROM memory_tree_leaf WHERE scope=? ORDER BY ord", (scope,)).fetchall()
        return [Leaf(r["ord"], r["text"], r["ts"], bool(r["tombstoned"]), bool(r["tainted"])) for r in rows]

    def count(self, scope: str) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM memory_tree_leaf WHERE scope=?", (scope,)).fetchone()[0])

    def scopes(self) -> list[str]:
        with self.db.tx() as c:
            rows = c.execute("SELECT DISTINCT scope FROM memory_tree_leaf ORDER BY scope").fetchall()
        return [r[0] for r in rows]

    # ---- nodes ----
    def nodes(self, scope: str) -> dict[tuple[int, int], Node]:
        with self.db.tx() as c:
            rows = c.execute("SELECT level, k, summary, children_hash, stale, updated_at FROM memory_tree_node WHERE scope=?", (scope,)).fetchall()
        return {(r["level"], r["k"]): Node(r["level"], r["k"], r["summary"], r["children_hash"], bool(r["stale"]), r["updated_at"]) for r in rows}

    def put_node(self, scope: str, level: int, k: int, summary: str, children_hash: str) -> None:
        with self._write() as c:
            c.execute("INSERT INTO memory_tree_node(scope, level, k, summary, children_hash, stale, updated_at) VALUES(?,?,?,?,?,0,?) "
                      "ON CONFLICT(scope, level, k) DO UPDATE SET summary=excluded.summary, children_hash=excluded.children_hash, stale=0, "
                      "updated_at=excluded.updated_at", (scope, level, k, summary, children_hash, time.time()))

    def clear_stale(self, scope: str, keys: list[tuple[int, int]]) -> None:
        if keys:
            with self._write() as c:
                c.executemany("UPDATE memory_tree_node SET stale=0 WHERE scope=? AND level=? AND k=?", [(scope, lv, k) for lv, k in keys])

    def has_nodes(self) -> bool:
        with self.db.tx() as c:
            return c.execute("SELECT 1 FROM memory_tree_node WHERE summary != '' LIMIT 1").fetchone() is not None

    def wipe(self, scope: str) -> None:
        with self._write() as c:
            c.execute("DELETE FROM memory_tree_node WHERE scope=?", (scope,))
            c.execute("DELETE FROM memory_tree_leaf WHERE scope=?", (scope,))

    def last_merge(self) -> float | None:
        with self.db.tx() as c:
            r = c.execute("SELECT MAX(updated_at) FROM memory_tree_node").fetchone()
        return r[0] if r else None

    def tombstoned(self, scope: str) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM memory_tree_leaf WHERE scope=? AND tombstoned=1", (scope,)).fetchone()[0])
