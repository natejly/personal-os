"""Read-only calls of one reply round run together; everything else is a barrier.

Offline, against a scripted llm.stream_chat and stub tools:
  - three slow reads finish in about one read's time, and tool messages / events keep call order;
  - a write in the middle is a barrier: the read after it starts only once the write has finished;
  - an identical (tool, args) pair runs once; a tool that asks is never started ahead of its card;
  - a stateful (browser) tool is never batched; parallelReads = 1 turns it all off.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="prtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
appmod.db.set_settings({"toolDeferAbove": 0})  # these tests drive their own tools; deferral is test_tool_search.py
from personal_os import llm  # noqa: E402
from personal_os.subagents import parallel_safe  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "alwaysAsk": ["pr_ask"]})
LOG: list[tuple[str, str, float]] = []   # (start|end, "tool:tag", monotonic time)
ROUNDS: list[dict[str, Any]] = []
SEEN: list[list[dict[str, Any]]] = []
DELAY = 0.2


def _spec(name: str, danger: str, group: str = "knowledge") -> None:
    async def fn(ctx: dict[str, Any], tag: str = "") -> Any:
        LOG.append(("start", f"{name}:{tag}", time.monotonic()))
        await asyncio.sleep(DELAY)
        LOG.append(("end", f"{name}:{tag}", time.monotonic()))
        if tag == "boom":
            raise ValueError("bad")
        return {"tag": tag}
    appmod.toolbox.specs[name] = ToolSpec(name, name, _obj({"tag": {"type": "string"}}, []), fn, group, danger)


_spec("pr_read", "safe")
_spec("pr_net", "network")
_spec("pr_write", "writes")
_spec("pr_ask", "external")
_spec("pr_browse", "network", "browser")


async def _stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto",  # type: ignore[no-untyped-def]
                  fast=False, cancel=None):
    SEEN.append([dict(m) for m in messages])
    step = ROUNDS.pop(0) if ROUNDS else {"calls": []}
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": step["calls"], "usage": None}


def c(cid: str, name: str, **args: Any) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def run(calls: list[dict[str, Any]], width: int = 8) -> tuple[list[tuple[str, Any]], float]:
    cid = appmod.convos.create(None, "t", "m")["id"]
    appmod.db.set_settings({"parallelReads": width})
    LOG.clear()
    SEEN.clear()
    ROUNDS[:] = [{"calls": calls}]

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event())]
    prev, llm.stream_chat = llm.stream_chat, _stream
    t0 = time.monotonic()
    try:
        events = asyncio.run(go())
    finally:
        llm.stream_chat = prev
    return events, time.monotonic() - t0


def tool_msgs() -> list[str]:
    return [m["tool_call_id"] for m in SEEN[-1] if m["role"] == "tool"]


def when(kind: str) -> dict[str, float]:
    return {a: t for k, a, t in LOG if k == kind}


def test_three_reads_overlap_and_keep_order() -> None:
    events, _ = run([c("1", "pr_read", tag="a"), c("2", "pr_net", tag="b"), c("3", "pr_read", tag="c")])
    st, en = when("start"), when("end")   # overlap measured on the tools themselves, not on chat setup time
    assert max(en.values()) - min(st.values()) < DELAY * 2, (st, en)
    assert tool_msgs() == ["1", "2", "3"]
    assert [d["arguments"]["tag"] for e, d in events if e == "tool_result"] == ["a", "b", "c"]


def test_write_is_a_barrier() -> None:
    run([c("1", "pr_read", tag="a"), c("2", "pr_write", tag="w"), c("3", "pr_read", tag="b"), c("4", "pr_read", tag="c")])
    s, e = when("start"), when("end")
    assert s["pr_write:w"] >= e["pr_read:a"] - 0.01
    assert s["pr_read:b"] >= e["pr_write:w"] - 0.01       # a read after a write waits for it
    assert abs(s["pr_read:b"] - s["pr_read:c"]) < 0.1      # but the reads after it overlap
    assert tool_msgs() == ["1", "2", "3", "4"]


def test_identical_call_runs_once() -> None:
    run([c("1", "pr_read", tag="same"), c("2", "pr_read", tag="same"), c("3", "pr_read", tag="other")])
    assert [a for k, a, _ in LOG if k == "start"].count("pr_read:same") == 1
    assert tool_msgs() == ["1", "2", "3"]
    msgs = [m for m in SEEN[-1] if m["role"] == "tool"]
    assert msgs[0]["content"] == msgs[1]["content"]


def test_ask_tool_is_never_started_ahead_of_its_card() -> None:
    seen: list[str] = []

    async def go() -> None:
        conv = appmod.convos.create(None, "t", "m")["id"]
        appmod.db.set_settings({"parallelReads": 8})
        LOG.clear()
        ROUNDS[:] = [{"calls": [c("1", "pr_read", tag="a"), c("2", "pr_ask", tag="x"), c("3", "pr_read", tag="b")]}]
        stop = asyncio.Event()

        async def drain() -> None:
            async for _ in appmod._chat_stream(conv, appmod.ChatIn(content="go"), stop):
                pass
        t = asyncio.create_task(drain())
        await asyncio.sleep(DELAY * 2)   # the card is open and nobody answers
        seen.extend(a for _, a, _ in LOG)
        stop.set()
        await asyncio.wait_for(t, 5)
    prev, llm.stream_chat = llm.stream_chat, _stream
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    assert not any(n.startswith("pr_ask") for n in seen)


def test_stateful_tools_stay_serial() -> None:
    _, wall = run([c("1", "pr_browse", tag="a"), c("2", "pr_browse", tag="b")])
    assert wall >= DELAY * 2 - 0.05


def test_error_is_isolated_and_width_one_is_serial() -> None:
    events, _ = run([c("1", "pr_read", tag="boom"), c("2", "pr_read", tag="ok")])
    res = [d for e, d in events if e == "tool_result"]
    assert res[0]["error"] and not res[1]["error"]
    _, wall = run([c("1", "pr_read", tag="a"), c("2", "pr_read", tag="b")], width=1)
    assert wall >= DELAY * 2 - 0.05


def test_predicate() -> None:
    s = appmod.toolbox.specs
    assert parallel_safe(s["pr_read"], "pr_read", "on")
    assert not parallel_safe(s["pr_read"], "pr_read", "ask")
    assert not parallel_safe(s["pr_write"], "pr_write", "on")
    assert not parallel_safe(s["pr_browse"], "pr_browse", "on")


def test_stop_mid_segment_skips_the_queued_reads() -> None:
    conv = appmod.convos.create(None, "t", "m")["id"]
    appmod.db.set_settings({"parallelReads": 2})
    LOG.clear()
    ROUNDS[:] = [{"calls": [c("1", "pr_read", tag="a"), c("2", "pr_read", tag="b"), c("3", "pr_read", tag="c")]}]
    stop = asyncio.Event()

    events: list[tuple[str, Any]] = []

    async def go() -> None:
        async def press() -> None:
            await asyncio.sleep(DELAY / 2)   # calls 1 and 2 are running, call 3 waits for a slot
            stop.set()
        t = asyncio.create_task(press())
        events.extend([ev async for ev in appmod._chat_stream(conv, appmod.ChatIn(content="go"), stop)])
        await t
    prev, llm.stream_chat = llm.stream_chat, _stream
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    assert "pr_read:c" not in [a for _, a, _ in LOG]   # reads already running side by side finish; queued ones never start
    assert [d["arguments"]["tag"] for e, d in events if e == "tool_result"] == ["a"]  # the rest of the round is reported as not executed
