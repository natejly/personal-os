"""Robustness fixes in llm.py (stream timeout, tool-call assembly), jobs.py (DST, cron validation), runs.py (topic cursor)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import jobs, llm, runs  # noqa: E402

# Bound at import: other test files swap llm.stream_chat for a fake and leave it there, and this
# file is about the real one.
_STREAM_CHAT = llm.stream_chat


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stream(monkeypatch: Any, chunks: list[dict[str, Any]] | None = None, raise_timeout: bool = False) -> None:
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            for c in chunks or []:
                yield f"data: {json.dumps(c)}\n\n".encode()
            if raise_timeout:
                raise httpx.ReadTimeout("idle")
            yield b"data: [DONE]\n\n"

    real = httpx.AsyncClient
    seen: dict[str, Any] = {}

    def client(*a: Any, **k: Any) -> httpx.AsyncClient:
        seen["timeout"] = k.get("timeout")
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Body())), **k)

    monkeypatch.setattr(llm.httpx, "AsyncClient", client)
    monkeypatch.setattr(llm, "_url", lambda s, path, model=None: "http://x/v1" + path)
    monkeypatch.setattr(llm, "_headers", lambda s: {})
    _stream.seen = seen  # type: ignore[attr-defined]


async def _end(**kw: Any) -> dict[str, Any]:
    last: dict[str, Any] = {}
    async for ev in _STREAM_CHAT({}, "m", [{"role": "user", "content": "hi"}], **kw):
        last = ev
    return last


def _tc(*frags: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"delta": {"tool_calls": list(frags)}}]}


def test_stream_has_idle_read_timeout_and_surfaces_it(monkeypatch: Any) -> None:
    _stream(monkeypatch, raise_timeout=True)
    with pytest.raises(llm.LLMError, match="stopped responding"):
        run(_end())
    t = _stream.seen["timeout"]  # type: ignore[attr-defined]
    assert t.read == llm.DEFAULT_IDLE_S + 5 and llm.DEFAULT_IDLE_S == 300.0  # headers wait; the per-chunk idle limit is enforced in the loop


def test_well_formed_chunked_calls_unchanged(monkeypatch: Any) -> None:
    _stream(monkeypatch, [
        _tc({"index": 0, "id": "a", "function": {"name": "read_", "arguments": '{"p'}}),
        _tc({"index": 1, "id": "b", "function": {"name": "write", "arguments": '{"q'}}),
        _tc({"index": 0, "function": {"name": "file", "arguments": '":1}'}}),
        _tc({"index": 1, "function": {"arguments": '":2}'}}),
    ])
    calls = run(_end())["tool_calls"]
    assert [(c["id"], c["name"], c["arguments"]) for c in calls] == [("a", "read_file", '{"p":1}'), ("b", "write", '{"q":2}')]


def test_null_or_missing_index_splits_on_new_id(monkeypatch: Any) -> None:
    _stream(monkeypatch, [
        _tc({"index": None, "id": "a", "function": {"name": "one", "arguments": "{"}}),
        _tc({"index": None, "function": {"arguments": "}"}}),
        _tc({"id": "b", "function": {"name": "two", "arguments": "{}"}}),
        _tc({"index": 0, "id": "c", "function": {"name": "three", "arguments": "{}"}}),
    ])
    calls = run(_end())["tool_calls"]
    assert [(c["id"], c["name"], c["arguments"]) for c in calls] == [
        ("a", "one", "{}"), ("b", "two", "{}"), ("c", "three", "{}")]


def test_dst_fall_back_fires_once() -> None:
    tz = "America/New_York"
    z = ZoneInfo(tz)
    first = datetime(2026, 11, 1, 1, 30, tzinfo=z).timestamp()  # fold=0, EDT
    nxt = jobs.next_fire("30 1 * * *", tz, first)
    assert datetime.fromtimestamp(nxt, z).day == 2  # not the 01:30 EST repeat
    # A late tick inside the repeated hour must not fire the slot again either.
    late = first + 900
    assert datetime.fromtimestamp(jobs.next_fire("30 1 * * *", tz, late), z).day == 2
    # Before the first pass, the slot is still the first pass.
    assert jobs.next_fire("30 1 * * *", tz, first - 600) == first


def test_dst_spring_forward_unchanged() -> None:
    tz = "America/New_York"
    z = ZoneInfo(tz)
    after = datetime(2026, 3, 7, 12, 0, tzinfo=z).timestamp()
    nxt = datetime.fromtimestamp(jobs.next_fire("30 2 * * *", tz, after), z)
    assert nxt.date().isoformat() in ("2026-03-08", "2026-03-09")
    nxt = datetime.fromtimestamp(jobs.next_fire("0 9 * * *", tz, after), z)
    assert (nxt.day, nxt.hour) == (7, 9) or (nxt.day, nxt.hour) == (8, 9)


def test_valid_cron_rejects_six_fields_and_never_matching() -> None:
    assert jobs.valid_cron("30 1 * * *")
    assert not jobs.valid_cron("* * * * * *")
    assert not jobs.valid_cron("0 0 30 2 *")
    assert not jobs.valid_cron("")
    assert not jobs.valid_cron("nope")


def test_topic_cursor_from_previous_process_replays_from_start() -> None:
    async def go() -> list[str]:
        t = runs.Topic()
        t.publish("learned", {"n": 1})
        t.publish("learned", {"n": 2})
        out: list[str] = []
        gen = t.subscribe(since=57)
        async for chunk in gen:
            out.append(chunk)
            if len(out) == 2:
                break
        await gen.aclose()
        return out

    got = run(go())
    assert len(got) == 2 and '"n": 1' in got[0].replace('"n":1', '"n": 1')
