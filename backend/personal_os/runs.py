"""Chat runs as background tasks, one event topic per conversation.

A run's lifetime belongs to its task alone: nothing here consults the subscriber set. Zero
subscribers is a normal state — publish writes to the ring and to no queues, the reply completes
and is persisted, and it waits in the ring for the next window to attach.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, AsyncIterator, Awaitable, Callable

from .db import new_id

log = logging.getLogger("personal_os")

# Every streamed delta is an event, so a ring has to hold a whole reply: it is the only thing a
# reconnecting or late-launching pop-out can replay from. QUEUE_MAX < RING is the invariant that
# lets a subscriber that overflowed reconnect from its last seq without a gap.
RING = 2000
QUEUE_MAX = 1000
KEEPALIVE_S = 15.0
# How long a finished run stays replayable, for a window that opens just after it ended.
RETAIN_S = 300.0

RunEvent = tuple[int, str, Any]


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _Sub:
    """One attached client: a bounded queue, plus the overflow flag that ends its stream."""

    __slots__ = ("q", "overflow")

    def __init__(self) -> None:
        self.q: asyncio.Queue[RunEvent | None] = asyncio.Queue(QUEUE_MAX)
        self.overflow = False


class Run:
    def __init__(self, conversation_id: str) -> None:
        self.run_id = new_id()
        self.conversation_id = conversation_id
        self.message_id: str | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        # Set when the reply's `done` goes out. The task lives on past that — auto-learn is the last
        # thing it does — so `live` alone cannot tell a working run from one that is only tidying up.
        self.replied = False
        self.seq = 0
        # Cooperative stop, also registered as _active[message_id] so /messages/{mid}/stop still works.
        self.stop = asyncio.Event()
        # Steered user messages (already persisted) waiting for the run to fold them into its context.
        self.steers: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self._ring: deque[RunEvent] = deque(maxlen=RING)
        self._subs: set[_Sub] = set()

    @property
    def live(self) -> bool:
        return self.ended_at is None

    @property
    def answering(self) -> bool:
        """Still producing a reply, so a steer can be folded in and a second run must not start.

        False for the whole auto-learn tail, where the round loop is already over: a steer accepted
        there would be persisted, published and never answered.
        """
        return self.live and not self.replied

    def info(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "conversation_id": self.conversation_id, "message_id": self.message_id,
                "seq": self.seq, "started_at": self.started_at, "live": self.live, "answering": self.answering}

    def publish(self, event: str, data: Any) -> None:
        self.seq += 1
        item = (self.seq, event, data)
        self._ring.append(item)
        for sub in self._subs:
            if sub.overflow:
                continue
            try:
                sub.q.put_nowait(item)
            except asyncio.QueueFull:
                sub.overflow = True

    def end(self) -> None:
        self.ended_at = time.time()
        for sub in self._subs:
            try:
                sub.q.put_nowait(None)
            except asyncio.QueueFull:
                sub.overflow = True

    async def subscribe(self, since: int = 0) -> AsyncIterator[str]:
        """Replay the ring past `since`, then follow live until the run ends."""
        sub = _Sub()
        self._subs.add(sub)
        getter: asyncio.Task[RunEvent | None] | None = None
        try:
            last = since
            for seq, event, data in list(self._ring):
                if seq > last:
                    yield sse(event, data)
                    last = seq
            while self.live or not sub.q.empty():
                if sub.overflow and sub.q.empty():
                    return
                if getter is None:
                    getter = asyncio.ensure_future(sub.q.get())
                done, _ = await asyncio.wait({getter}, timeout=KEEPALIVE_S)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                item, getter = getter.result(), None
                if item is None:
                    return
                seq, event, data = item
                if seq > last:
                    yield sse(event, data)
                    last = seq
        finally:
            self._subs.discard(sub)
            if getter is not None:
                getter.cancel()


class RunBus:
    """Holds the latest run per conversation, live or recently finished."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        # Runs displaced by a newer one while their auto-learn tail was still open. Only `shutdown`
        # cares: nothing routes to them any more, and the task holds what keeps them alive.
        self._retired: set[Run] = set()

    def get(self, conversation_id: str) -> Run | None:
        return self._runs.get(conversation_id)

    def live(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.live else None

    def answering(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.answering else None

    def list(self) -> list[dict[str, Any]]:
        return sorted((r.info() for r in self._runs.values() if r.live), key=lambda i: i["started_at"], reverse=True)

    def start(self, conversation_id: str, runner: Callable[[Run], Awaitable[None]]) -> Run:
        self._prune()
        # A conversation can start a new reply while the previous run is still auto-learning. That
        # run keeps writing to its own ring for whoever is attached; it is simply no longer the
        # conversation's current run.
        displaced = self._runs.get(conversation_id)
        if displaced is not None and displaced.live:
            self._retired.add(displaced)
        run = Run(conversation_id)
        self._runs[conversation_id] = run
        run.task = asyncio.create_task(self._drive(run, runner), name=f"run:{run.run_id}")
        return run

    def stop(self, conversation_id: str, run_id: str | None = None) -> bool:
        run = self.live(conversation_id)
        if not run or (run_id and run_id != run.run_id):
            return False
        run.stop.set()
        return True

    async def shutdown(self) -> None:
        """Cancel every live run and wait for it: a sandboxed run_python writes into data_dir/tmp."""
        every = [*self._runs.values(), *self._retired]
        for run in every:
            run.stop.set()
        tasks = [r.task for r in every if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run in every:
            if run.live:
                run.end()
        self._runs.clear()
        self._retired.clear()

    async def _drive(self, run: Run, runner: Callable[[Run], Awaitable[None]]) -> None:
        try:
            await runner(run)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a dead run must still tell its subscribers why
            log.exception("run %s failed", run.run_id)
            run.publish("error", {"message": str(e)})
        finally:
            run.end()
            self._retired.discard(run)

    def _prune(self) -> None:
        cutoff = time.time() - RETAIN_S
        for cid, run in list(self._runs.items()):
            if run.ended_at is not None and run.ended_at < cutoff:
                del self._runs[cid]
