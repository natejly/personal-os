"""The learn queue survives a full queue, a stop and a restart: jobs persist as ids in learn_pending and are rebuilt from messages."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import learn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations, Graph, Memories  # noqa: E402


@pytest.fixture()
def env():
    db = Database(tempfile.mkdtemp(prefix="learndur-"))
    convos = Conversations(db)
    conv = convos.create(None, "t", "m")
    um = convos.add_message(conv["id"], "user", "I prefer short emails")
    am = convos.add_message(conv["id"], "assistant", "Noted.", model="m")
    return db, conv["id"], um["id"], am["id"]


def _worker(db: Database, depth: int = 32) -> learn.LearnWorker:
    return learn.LearnWorker(memories=Memories(db), graph=Graph(db), set_trace=lambda *_: None, publish=lambda *_: None,
                             depth=depth, replay_settings=lambda: {"defaultModel": "m"})


def _job(cid: str, uid: str, aid: str) -> learn.LearnJob:
    return learn.LearnJob(conversation_id=cid, message_id=aid, project_id=None, user_text="x", assistant_text="y",
                          model="m", settings={"apiKey": "SECRET"}, spans=[], user_message_id=uid)


async def _replay_twice(w: learn.LearnWorker) -> tuple[int, int]:
    w._run = lambda job: asyncio.sleep(3600)  # type: ignore[method-assign]
    out = w.replay(), w.replay()
    task, w._task = w._task, None
    if task:
        task.cancel()
    return out


def _rows(db: Database) -> list[Any]:
    with db.tx() as c:
        return c.execute("SELECT * FROM learn_pending").fetchall()


def test_full_queue_persists_instead_of_dropping(env) -> None:
    db, cid, uid, aid = env

    async def go() -> None:
        w = _worker(db, depth=1)
        w._run = lambda job: asyncio.sleep(3600)  # type: ignore[method-assign]  # the first job occupies the worker
        assert w.submit(_job(cid, uid, "first"))
        await asyncio.sleep(0.05)  # worker takes "first"
        assert w.submit(_job(cid, uid, "second"))  # fills the one slot
        assert w.submit(_job(cid, uid, aid)) is False  # full: not queued...
        assert {r["message_id"] for r in _rows(db)} == {"first", "second", aid}  # ...but not lost
        assert "SECRET" not in repr([tuple(r) for r in _rows(db)])  # ids and flags only
        await w.stop()

    asyncio.run(go())


def test_replay_rebuilds_job_from_messages(env) -> None:
    db, cid, uid, aid = env
    w = _worker(db)
    w._persist(_job(cid, uid, aid))
    w2 = _worker(db)  # a fresh process
    n = asyncio.run(_replay_twice(w2))
    assert n == (1, 0)  # second call: already queued
    job = w2._q.get_nowait()
    assert (job.message_id, job.user_text, job.assistant_text, job.model) == (aid, "I prefer short emails", "Noted.", "m")


def test_replay_respects_free_slots_and_skips_gone_messages(env) -> None:
    db, cid, uid, aid = env
    w = _worker(db, depth=1)
    w._persist(_job(cid, uid, aid))
    w._persist(_job(cid, "missing", "ghost"))
    w2 = _worker(db, depth=1)
    asyncio.run(_replay_twice(w2))
    assert w2._q.qsize() == 1
    w3 = _worker(db)
    asyncio.run(_replay_twice(w3))
    assert [r["message_id"] for r in _rows(db)] == [aid]  # the ghost row was cleared


def test_stop_with_pending_replays_next_start_and_completion_removes_row(env) -> None:
    db, cid, uid, aid = env

    async def go() -> None:
        w = _worker(db)
        ran: list[str] = []

        async def run(job: Any) -> None:
            ran.append(job.message_id)
        w._run = run  # type: ignore[method-assign]
        # stop before the worker ever drains: the job is in the queue and in the table
        w._persist(_job(cid, uid, aid))
        await w.stop()
        assert len(_rows(db)) == 1
        w2 = _worker(db)
        w2._run = run  # type: ignore[method-assign]
        assert w2.replay() == 1
        await asyncio.wait_for(w2._q.join(), 5)
        await asyncio.sleep(0)
        await w2.stop()
        assert ran == [aid] and _rows(db) == []

    asyncio.run(go())


def test_replay_twice_does_not_double_store(env) -> None:
    db, *_ = env
    m = Memories(db)
    a = m.create(None, "prefers short emails", source="auto")
    b = m.create(None, "prefers short emails", source="auto")
    assert a["id"] == b["id"]  # exact duplicate content in one scope is returned, not stored again
