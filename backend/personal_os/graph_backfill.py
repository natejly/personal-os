"""Backfill the knowledge graph from conversations that already exist.

Re-runs graph_learn.learn_graph over past USER messages (the user's own words only, no assistant text), oldest first so
a later fact supersedes an earlier one the way it did live. Never starts by itself: the app exposes it as POST
/graph/backfill, or run it by hand:

    python -m personal_os.graph_backfill --data-dir DIR [--project ID] [--limit N]

The CLI opens the database directly, so it must NOT run against a data dir the app has open (two writers means two
schedulers on one file). It prints counts only, never message text, and resolves names without embeddings.

A message is eligible when its chat is the sort that learns live: not trashed, not private, not tainted, not a
scheduled run or desk chat, not marked "don't learn from this chat", and autoLearn, useMemory and useGraph are not switched off. kg_backfill_done records
each message once its extraction returned (an extraction that found nothing counts); an error leaves it unmarked,
so the next run retries it, and a finished run is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Any

from . import graph_learn, llm
from .db import Database
from .memory_limits import GRAPH_BACKFILL_BATCH, GRAPH_BACKFILL_DELAY_SECONDS, GRAPH_BACKFILL_PROGRESS_SECONDS
from .repos import ALL, Graph

log = logging.getLogger("grain.graph_backfill")

SCHEMA = "CREATE TABLE IF NOT EXISTS kg_backfill_done (message_id TEXT PRIMARY KEY, done_at REAL NOT NULL)"

# The stored half of the decision app.py makes before it queues a LearnJob (a run's taint and the global
# autoLearn switch are not stored per message; the chat's own settings are).
_ELIGIBLE = """FROM messages m JOIN conversations c ON c.id = m.conversation_id
  WHERE m.role = 'user' AND m.kind IS NULL AND TRIM(m.content) <> '' AND m.superseded_at IS NULL AND c.deleted_at IS NULL
    AND COALESCE(json_extract(c.settings,'$.deskId'),'') = '' AND COALESCE(json_extract(c.settings,'$.job_id'),'') = ''
    AND COALESCE(json_extract(c.settings,'$.private'),0) = 0 AND COALESCE(json_extract(c.settings,'$.tainted'),0) = 0
    AND COALESCE(json_extract(c.settings,'$.autoLearn'),1) != 0 AND COALESCE(json_extract(c.settings,'$.useMemory'),1) != 0
    AND COALESCE(json_extract(c.settings,'$.useGraph'),1) != 0 AND COALESCE(json_extract(c.settings,'$.learn'),1) != 0
    AND NOT EXISTS (SELECT 1 FROM kg_backfill_done d WHERE d.message_id = m.id)"""


class BackfillRunning(RuntimeError):
    pass


def _scope(project_id: str | None) -> tuple[str, list[Any]]:
    """The chats whose messages are read: personal only, one project, or (ALL) every chat."""
    if project_id == ALL:
        return "", []
    if project_id is None:
        return " AND c.project_id IS NULL", []
    return " AND c.project_id = ?", [project_id]


class GraphBackfill:
    def __init__(self, db: Database, graph: Graph, recall: Any = None) -> None:
        self.db, self.graph, self.recall = db, graph, recall
        self._task: asyncio.Task[None] | None = None
        self._status: dict[str, Any] = self._fresh(None)
        with db.tx() as c:
            c.execute(SCHEMA)

    @staticmethod
    def _fresh(project_id: str | None, total: int = 0) -> dict[str, Any]:
        return {"running": False, "done": 0, "total": total, "errors": 0, "started_at": None, "finished_at": None,
                "cancelled": False, "project_id": project_id}

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def pending(self, project_id: str | None) -> int:
        where, args = _scope(project_id)
        with self.db.tx() as c:
            return int(c.execute(f"SELECT COUNT(*) {_ELIGIBLE}{where}", args).fetchone()[0])

    def _page(self, project_id: str | None, after: tuple[float, str]) -> list[Any]:
        where, args = _scope(project_id)
        with self.db.tx() as c:
            return c.execute(
                f"SELECT m.id, m.content, m.created_at, m.conversation_id, c.project_id {_ELIGIBLE}{where} "
                "AND (m.created_at, m.id) > (?, ?) ORDER BY m.created_at, m.id LIMIT ?",
                (*args, after[0], after[1], GRAPH_BACKFILL_BATCH)).fetchall()

    def start(self, settings: dict[str, Any], model: str, *, project_id: str | None = None,
              limit: int | None = None) -> dict[str, Any]:
        """Begin in the background (needs a running loop). `total` is what is pending now, capped by `limit`."""
        if self._task is not None and not self._task.done():
            raise BackfillRunning("a graph backfill is already running")
        total = self.pending(project_id)
        self._status = {**self._fresh(project_id, min(total, limit) if limit else total), "running": True, "started_at": time.time()}
        self._task = asyncio.get_running_loop().create_task(self._run(settings, model, project_id, limit), name="graph-backfill")
        return self.status()

    def cancel(self) -> bool:
        """Stop the run; the call in flight is abandoned and what is already extracted stays. False when nothing runs."""
        if self._task is None or self._task.done():
            return False
        self._task.cancel()
        return True

    async def join(self, timeout: float | None = None) -> bool:
        """Wait for the run to end (or the timeout); True when it has ended."""
        if self._task is not None:
            await asyncio.wait({self._task}, timeout=timeout)
        return self._task is None or self._task.done()

    async def _run(self, settings: dict[str, Any], model: str, project_id: str | None, limit: int | None) -> None:
        st = self._status
        after, seen = (0.0, ""), 0
        try:
            while limit is None or seen < limit:
                rows = self._page(project_id, after)
                if not rows:
                    break
                for r in rows:
                    if limit is not None and seen >= limit:
                        break
                    seen += 1
                    after = (r["created_at"], r["id"])
                    # Tagged so the usage page shows a backfill apart from live learning.
                    llm.usage_context.set({"conversation_id": r["conversation_id"], "project_id": r["project_id"], "tag": "graph-backfill"})
                    try:
                        await graph_learn.learn_graph(settings=settings, graph=self.graph, project_id=r["project_id"],
                                                      user_text=r["content"], assistant_text="", model=model,
                                                      message_id=r["id"], ts=r["created_at"], recall=self.recall)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - one bad message must not end the run; it stays unmarked and is retried next time
                        st["errors"] += 1
                        log.exception("graph backfill failed for message %s", r["id"])
                    else:
                        with self.db.tx() as c:
                            c.execute("INSERT OR IGNORE INTO kg_backfill_done(message_id, done_at) VALUES(?, ?)", (r["id"], time.time()))
                        st["done"] += 1
                    await asyncio.sleep(GRAPH_BACKFILL_DELAY_SECONDS)  # keeps a backfill from crowding live chat on the proxy
        except asyncio.CancelledError:
            st["cancelled"] = True
        finally:
            st["running"], st["finished_at"] = False, time.time()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill the knowledge graph from past user messages (the app must be closed).")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--project", default=None, help="project id; 'all' for every scope; omitted: personal chats")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    project = ALL if a.project == "all" else (a.project or None)
    db = Database(a.data_dir)
    settings = {**llm.DEFAULT_SETTINGS, **db.get_settings()}
    model = str(settings.get("extractionModel") or settings.get("defaultModel") or "")
    if not model:
        print("no extractionModel or defaultModel in the settings", file=sys.stderr)
        return 1

    async def go() -> dict[str, Any]:
        bf = GraphBackfill(db, Graph(db))
        print(f"model {model}  pending {bf.pending(project)}")
        bf.start(settings, model, project_id=project, limit=a.limit)
        while not await bf.join(timeout=GRAPH_BACKFILL_PROGRESS_SECONDS):
            s = bf.status()
            print(f"{s['done']}/{s['total']} done, {s['errors']} errors")
        return bf.status()

    s = asyncio.run(go())
    print(f"finished: {s['done']}/{s['total']} done, {s['errors']} errors")
    return 1 if s["errors"] and not s["done"] else 0


if __name__ == "__main__":
    sys.exit(main())
