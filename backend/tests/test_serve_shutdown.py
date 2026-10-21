"""Backend shutdown: open event streams end on the exit signal, and the stop hook cannot be held up by one step."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="servetest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as app_mod  # noqa: E402
from personal_os import runs  # noqa: E402
from personal_os.runs import Run, Topic  # noqa: E402
from personal_os.serve import GRACEFUL_SHUTDOWN_S, Server, uvicorn_kwargs  # noqa: E402


def _reset() -> None:
    runs.reset_shutdown()


def test_uvicorn_kwargs_bound_the_graceful_shutdown() -> None:
    kw = uvicorn_kwargs("127.0.0.1", 8765, True)
    assert kw["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_S > 0
    assert kw["app"] == "personal_os.app:app" and kw["reload"] is True and kw["log_config"] is None
    assert kw["port"] == 8765 and kw["host"] == "127.0.0.1"


def test_server_signals_streams_before_waiting() -> None:
    _reset()
    from uvicorn import Config

    server = Server(Config("personal_os.app:app"))
    server.handle_exit(15, None)
    try:
        assert runs.closing() and server.should_exit
    finally:
        _reset()


async def _drain(gen) -> list[str]:  # type: ignore[no-untyped-def]
    return [chunk async for chunk in gen]


def test_topic_stream_ends_on_shutdown() -> None:
    async def go() -> None:
        _reset()
        topic = Topic()
        topic.publish("learned", {"a": 1})
        task = asyncio.ensure_future(_drain(topic.subscribe(0)))
        await asyncio.sleep(0.1)
        assert not task.done()  # parked on the live follow
        runs.begin_shutdown()
        out = await asyncio.wait_for(task, 2)
        assert any("learned" in c for c in out)
        assert await asyncio.wait_for(_drain(topic.subscribe(0)), 2)  # a late subscriber replays, then ends at once

    try:
        asyncio.run(go())
    finally:
        _reset()


def test_run_stream_ends_on_shutdown() -> None:
    async def go() -> None:
        _reset()
        run = Run("c1")
        run.publish("status", {"s": "x"})
        task = asyncio.ensure_future(_drain(run.subscribe(0)))
        await asyncio.sleep(0.1)
        assert run.live and not task.done()
        runs.begin_shutdown()
        await asyncio.wait_for(task, 2)
        assert runs._attached == {}

    try:
        asyncio.run(go())
    finally:
        _reset()


def test_bounded_step_logs_and_moves_on(caplog) -> None:  # type: ignore[no-untyped-def]
    async def go() -> None:
        async def stuck() -> None:
            await asyncio.sleep(60)

        async def boom() -> None:
            raise RuntimeError("x")

        await asyncio.wait_for(app_mod._bounded("stuck", stuck(), 0.05), 2)
        await app_mod._bounded("boom", boom())

    with caplog.at_level("WARNING"):
        asyncio.run(go())
    text = caplog.text
    assert "'stuck' did not finish" in text and "'boom' failed" in text
