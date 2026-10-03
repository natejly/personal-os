"""A provider context overflow is recovered once; the window is per model; Stop during setup ends the reply as stopped.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_context_overflow.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ovtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction, llm  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos = appmod.convos

SEEN: list[list[dict[str, Any]]] = []
SCRIPT: list[str] = []  # per request: 'overflow', 'late-overflow' (after a token), 'tool' (one tool call) or 'ok'


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                  effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    step = SCRIPT.pop(0) if SCRIPT else "ok"
    if step == "overflow":
        raise llm.ContextOverflowError("The conversation does not fit the model.", limit=8000)
    if step == "tool":
        yield {"type": "end", "finish_reason": "tool_calls", "usage": None,
               "tool_calls": [{"id": f"call_{len(SEEN)}", "name": "current_time", "arguments": "{}"}]}
        return
    yield {"type": "delta", "text": "partial "}
    if step == "late-overflow":
        raise llm.ContextOverflowError("The conversation does not fit the model.", limit=8000)
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _summary(cfg: Any, model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
    return "SUMMARY-OF-EARLIER"


def seed(n: int = 14, size: int = 600) -> str:
    cid = convos.create(None, "t", "ov-model")["id"]
    for i in range(n):
        convos.add_message(cid, "user" if i % 2 == 0 else "assistant", f"m{i} " + "w" * size)
    return cid


def drive(cid: str, text: str = "next") -> list[tuple[str, Any]]:
    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content=text), asyncio.Event())]
    prev_s, prev_c = llm.stream_chat, llm.complete
    llm.stream_chat, llm.complete = _stream, _summary  # type: ignore[assignment]
    SEEN.clear()
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat, llm.complete = prev_s, prev_c


def done_of(evs: list[tuple[str, Any]]) -> dict[str, Any]:
    return [d for e, d in evs if e == "done"][-1]


def setup_function() -> None:
    compaction._learned.clear()
    appmod.db.set_settings({"contextWindow": 128000, "compactAt": 0.7, "compactKeepRecent": 8})


def test_one_overflow_is_recovered_in_the_same_round() -> None:
    cid = seed()
    SCRIPT[:] = ["overflow", "ok"]
    evs = drive(cid)
    d = done_of(evs)
    assert d["error"] is None and d["outcome"] is None
    assert len(SEEN) == 2 and len(SEEN[1]) < len(SEEN[0]), "the second request is smaller"
    assert any(m["content"].startswith(compaction.SUMMARY_PREFIX) for m in SEEN[1] if isinstance(m.get("content"), str))
    spans = [d2["span"] for e, d2 in evs if e == "span"]
    assert any(s["kind"] == "compact" and s["meta"].get("kind") == "overflow" for s in spans), "a compact span with kind overflow"
    rounds = {s["meta"].get("round") for s in spans if s["kind"] == "llm"}
    assert rounds == {1}, "the retry is the same round, not a second one"
    assert compaction._learned.get("ov-model") == 4096 or compaction._learned.get("ov-model", 0) <= 8000


def test_a_second_overflow_ends_with_the_readable_error() -> None:
    cid = seed()
    SCRIPT[:] = ["overflow", "overflow"]
    d = done_of(drive(cid))
    assert d["error"] and d["error_kind"] == "overflow"
    assert len(SEEN) == 2, "no third request"


def test_an_overflow_after_a_token_is_not_recovered() -> None:
    cid = seed()
    SCRIPT[:] = ["late-overflow", "ok"]
    d = done_of(drive(cid))
    assert len(SEEN) == 1 and d["error_kind"] == "overflow"


def test_an_overflow_after_tool_rounds_keeps_the_runs_own_turns_paired() -> None:
    cid = seed()
    SCRIPT[:] = ["tool", "tool", "overflow", "ok"]
    d = done_of(drive(cid))
    assert d["error"] is None and len(SEEN) == 4
    retry = SEEN[3]
    assert any(m["content"].startswith(compaction.SUMMARY_PREFIX) for m in retry if isinstance(m.get("content"), str))
    calls = [c["id"] for m in retry if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])]
    answered = [m["tool_call_id"] for m in retry if m.get("role") == "tool"]
    assert len(calls) == 2 and calls == answered, "the rebuilt head left this run's tool turns intact and in order"
    tail = [m for m in SEEN[2] if m.get("role") in ("tool",) or m.get("tool_calls")]
    assert [m for m in retry if m.get("role") in ("tool",) or m.get("tool_calls")] == tail, "the tail is carried over verbatim"


def test_a_stop_during_the_overflow_summarizer_ends_as_stopped() -> None:
    cid = seed()
    SCRIPT[:] = ["overflow", "ok"]
    stop = asyncio.Event()

    async def blocked(cfg: Any, model: str, messages: Any, kind: str = "learn", *, cancel: Any = None) -> str:
        stop.set()
        raise llm.LLMError("cancelled", "cancelled")

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="next"), stop)]
    prev_s, prev_c = llm.stream_chat, llm.complete
    llm.stream_chat, llm.complete = _stream, blocked  # type: ignore[assignment]
    SEEN.clear()
    try:
        d = done_of(asyncio.run(go()))
    finally:
        llm.stream_chat, llm.complete = prev_s, prev_c
    assert d["stopped"] is True and d["outcome"] == "stopped" and d["error"] is None
    assert len(SEEN) == 1, "the round was not re-issued after the Stop"


def test_window_never_exceeds_the_setting_and_shrinks_on_overflow() -> None:
    cfg = {"contextWindow": 100000}
    assert compaction.window_for(cfg, "m1", None) == 100000
    assert compaction.window_for(cfg, "m1", 500000) == 100000
    assert compaction.window_for(cfg, "m1", 32000) == 32000
    compaction.note_overflow("m1", 16000, 40000)
    assert compaction.window_for(cfg, "m1", 32000) == 16000
    compaction.note_overflow("m1", None, 30000)
    assert compaction.window_for(cfg, "m1", 32000) == 16000, "a later, looser overflow never widens the learned limit"
    compaction.note_overflow("m1", 12000, 30000)
    assert compaction.window_for(cfg, "m1", 32000) == 12000, "a tighter one still narrows it"
    assert compaction.window_for(cfg, "m2", None) == 100000, "learned per model"
    compaction.note_overflow("m3", None, 100)
    assert compaction.window_for(cfg, "m3", None) == 4096, "a floor"


def test_the_meter_returns_the_models_window() -> None:
    cid = seed(4, 10)
    compaction.note_overflow("ov-model", 9000, 20000)
    with TestClient(appmod.app) as c:
        r = c.get(f"/conversations/{cid}/context-meter", headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    assert r.status_code == 200 and r.json()["window"] == 9000


def test_stop_during_context_assembly_ends_the_reply_as_stopped() -> None:
    cid = seed(30, 4000)
    appmod.db.set_settings({"contextWindow": 20000})
    stop_ev = asyncio.Event
    started: list[bool] = []

    async def blocked(cfg: Any, model: str, messages: Any, kind: str = "learn", *, cancel: Any = None) -> str:
        started.append(True)
        await cancel.wait()
        raise llm.LLMError("cancelled", "cancelled")

    async def go() -> list[tuple[str, Any]]:
        stop = stop_ev()
        out: list[tuple[str, Any]] = []

        async def consume() -> None:
            async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="more"), stop):
                out.append(ev)
        t = asyncio.create_task(consume())
        for _ in range(200):
            if started:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(t, 5)
        return out
    prev_s, prev_c = llm.stream_chat, llm.complete
    llm.stream_chat, llm.complete = _stream, blocked  # type: ignore[assignment]
    SEEN.clear()
    try:
        evs = asyncio.run(go())
    finally:
        llm.stream_chat, llm.complete = prev_s, prev_c
    d = done_of(evs)
    assert d["stopped"] is True and d["outcome"] == "stopped" and d["id"] is None and d["error"] is None
    assert not SEEN, "the model was never asked"
