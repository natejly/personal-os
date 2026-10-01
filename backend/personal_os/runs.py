"""Chat runs as background tasks, one event topic per conversation.

A run's lifetime belongs to its task alone: nothing here consults the subscriber set. Zero
subscribers is a normal state — publish writes to the ring and to no queues, the reply completes
and is persisted, and it waits in the ring for the next window to attach.

The ring is a cache; the row is the truth. Every run is written to `agent_runs` before its task is
spawned and every published event is taped by `runlog.RunStore`, so a run survives the process: a
client that reconnects after RETAIN_S, or after a restart, replays from the tape instead of finding
nothing. Keying stays one run per conversation on purpose — a desk is one conversation, so N desks
are N ordinary runs with no bus surgery.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from .db import new_id

if TYPE_CHECKING:  # the store is injected; importing it here would be a cycle through db only
    from .runlog import RunStore

log = logging.getLogger("personal_os")

# Every streamed delta is an event, so a ring has to hold a whole reply: it is the only thing a
# reconnecting or late-launching pop-out can replay from. QUEUE_MAX < RING is the invariant that
# lets a subscriber that overflowed reconnect from its last seq without a gap.
RING = 2000
QUEUE_MAX = 1000
KEEPALIVE_S = 15.0
# How long a finished run stays replayable, for a window that opens just after it ended.
RETAIN_S = 300.0
# A status a run cannot leave. `end()` never downgrades one of these to 'done'.
TERMINAL = ("done", "error", "stopped", "interrupted")

RunEvent = tuple[int, str, Any]


def sse(event: str, data: Any, seq: int | None = None) -> str:
    """`id: <seq>` when there is one: the renderer only advances its resume cursor on an `id:` line,
    so a live stream without one replays from 0 after a dropped socket."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


class _NoTape:
    """What a Run built without a store writes to. A Run is normally handed `RunStore`, but the
    class stays usable on its own — in a unit test, or anywhere the tape is not wanted — and the
    alternative is an `if self.store` around every one of these calls."""

    def create(self, *a: Any, **k: Any) -> None: ...
    def update(self, *a: Any, **k: Any) -> None: ...
    def append(self, *a: Any, **k: Any) -> None: ...


_NO_TAPE = _NoTape()


class _Sub:
    """One attached client: a bounded queue, plus the overflow flag that ends its stream."""

    __slots__ = ("q", "overflow")

    def __init__(self) -> None:
        self.q: asyncio.Queue[RunEvent | None] = asyncio.Queue(QUEUE_MAX)
        self.overflow = False


class Run:
    def __init__(self, conversation_id: str, store: RunStore | None = None, *, kind: str = "chat",
                 desk_id: str | None = None, turn: int = 0) -> None:
        self.run_id = new_id()
        self.conversation_id = conversation_id
        self.store: Any = store if store is not None else _NO_TAPE
        self.kind = kind
        self.desk_id = desk_id
        self.turn = turn
        self.status = "running"
        self.message_id: str | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.seq = 0
        # What the reply ended up spending, set by _chat_stream on the Run it was handed. The desk
        # supervisor reads them to decide whether another bounded turn is worth starting.
        self.partial: str | None = None
        self.cost = 0.0
        self.rounds = 0
        self.steps_consumed = 0
        self.budget: dict[str, Any] = {}
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
    def watchers(self) -> int:
        """How many clients are attached right now. The park timer (§4.5) will not fire in front of
        somebody who is reading the card."""
        return len(self._subs)

    def info(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "conversation_id": self.conversation_id, "message_id": self.message_id,
                "seq": self.seq, "started_at": self.started_at, "live": self.live,
                "kind": self.kind, "desk_id": self.desk_id, "turn": self.turn, "status": self.status}

    def set_status(self, status: str) -> None:
        """'awaiting' while a card is open, so GET /runs tells a blocked run from a busy one."""
        self.status = status
        self.store.update(self.run_id, status=status)

    def publish(self, event: str, data: Any) -> None:
        self.seq += 1
        item = (self.seq, event, data)
        self._ring.append(item)
        self.store.append(self.run_id, self.seq, event, data)
        for sub in self._subs:
            if sub.overflow:
                continue
            try:
                sub.q.put_nowait(item)
            except asyncio.QueueFull:
                sub.overflow = True

    def end(self, status: str = "done") -> None:
        """Terminal. Stamps the row — update() flushes the coalesced delta buffer itself — and wakes
        every subscriber. A status the runner already settled on (stopped, error) is never
        downgraded to 'done' by the generic end() in RunBus._drive's finally."""
        if self.ended_at is not None:
            return
        self.ended_at = time.time()
        if self.status not in TERMINAL:
            self.status = status
        try:
            self.store.update(self.run_id, status=self.status, message_id=self.message_id,
                              cost=self.cost, rounds=self.rounds, budget=self.budget)
        except Exception:  # noqa: BLE001 - the tape must never keep a finished run from closing
            log.warning("could not stamp run %s", self.run_id, exc_info=True)
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
                    yield sse(event, data, seq)
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
                    yield sse(event, data, seq)
                    last = seq
        finally:
            self._subs.discard(sub)
            if getter is not None:
                getter.cancel()


class RunBus:
    """Holds the latest run per conversation, live or recently finished."""

    def __init__(self, store: RunStore) -> None:
        self._runs: dict[str, Run] = {}
        self.store = store

    def get(self, conversation_id: str) -> Run | None:
        return self._runs.get(conversation_id)

    def live(self, conversation_id: str) -> Run | None:
        run = self._runs.get(conversation_id)
        return run if run and run.live else None

    def list(self) -> list[dict[str, Any]]:
        return sorted((r.info() for r in self._runs.values() if r.live), key=lambda i: i["started_at"], reverse=True)

    def start(self, conversation_id: str, runner: Callable[[Run], Awaitable[None]], *, kind: str = "chat",
              desk_id: str | None = None, turn: int = 0, input: dict[str, Any] | None = None) -> Run:
        self._prune()
        run = Run(conversation_id, self.store, kind=kind, desk_id=desk_id, turn=turn)
        # The row is written BEFORE the task exists: a crash one instruction later still leaves
        # something recoverable, rather than work nothing knows happened.
        self.store.create(run.run_id, conversation_id, kind=kind, desk_id=desk_id, turn=turn, input=input)
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
        for run in self._runs.values():
            run.stop.set()
        tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for run in self._runs.values():
            if run.live:
                # 'interrupted', not 'done': the next boot has to be able to tell a kill from a
                # clean finish, and only then is salvaging the partial transcript honest.
                run.end("interrupted")
        self._runs.clear()

    async def _drive(self, run: Run, runner: Callable[[Run], Awaitable[None]]) -> None:
        status = "done"
        try:
            await runner(run)
            if run.stop.is_set():
                status = "stopped"
        except asyncio.CancelledError:
            run.status = "interrupted"
            raise
        except Exception as e:  # noqa: BLE001 - a dead run must still tell its subscribers why
            log.exception("run %s failed", run.run_id)
            status = "error"
            run.status = "error"
            try:
                run.store.update(run.run_id, error=str(e))
            except Exception:  # noqa: BLE001
                pass
            run.publish("error", {"message": str(e)})
        finally:
            run.end(status)

    def _prune(self) -> None:
        cutoff = time.time() - RETAIN_S
        for cid, run in list(self._runs.items()):
            if run.ended_at is not None and run.ended_at < cutoff:
                del self._runs[cid]
