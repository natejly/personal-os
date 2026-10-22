"""Pre-first-token and per-round latency: the tool pick overlaps retrieval, microcompact clears in one batch, a spilled result keeps its tail.

Runs under pytest, or directly: python backend/tests/test_orch_latency.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="orchlat-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction, llm, mcp_search, working  # noqa: E402


def test_preselect_runs_beside_retrieval(monkeypatch: Any) -> None:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "toolDeferAbove": 5})
    t: dict[str, float] = {}
    real_gather = appmod._gather_context

    async def slow_gather(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(0.3)
        t["gather_end"] = time.monotonic()
        return await real_gather(*a, **k)

    async def fake_preselect(docs: Any, text: str, settings: Any) -> list[str]:
        t["pre_start"] = time.monotonic()
        await asyncio.sleep(0.3)
        return []

    async def stream(*a: Any, **k: Any) -> Any:
        yield {"type": "delta", "text": "ok"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    monkeypatch.setattr(appmod, "_gather_context", slow_gather)
    monkeypatch.setattr(mcp_search, "preselect_ranked", fake_preselect)
    monkeypatch.setattr(llm, "stream_chat", stream)
    cid = appmod.convos.create(None, "t", "test-model")["id"]

    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="add a todo"), asyncio.Event()):
            pass

    asyncio.run(go())
    assert "pre_start" in t, "the tool pick ran"
    assert t["pre_start"] < t["gather_end"], "it started before retrieval finished"


def test_microcompact_clears_one_batch_then_leaves_the_prefix_alone() -> None:
    def tmsg(i: int) -> list[dict[str, Any]]:
        return [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"c{i}", "content": json.dumps({"rows": ["y" * 4000]})}]  # ~1000 tokens each

    msgs: list[dict[str, Any]] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    for i in range(12):
        msgs += tmsg(i)
    n, _ = compaction.microcompact(msgs, 1, 1_000_000, 0.25, at_tokens=10_000)
    tools = [m for m in msgs if m["role"] == "tool"]
    cleared = [i for i, m in enumerate(tools) if json.loads(m["content"]).get("cleared")]
    assert n == len(cleared) and cleared == list(range(n)), "oldest first, contiguous"
    assert compaction.estimate_messages(msgs) <= 0.6 * 10_000, "down to the low-water mark"
    assert n < 10, "not every eligible result: it stopped at the mark"
    snap = json.dumps(msgs)
    for i in range(12, 14):  # more rounds, still under the threshold: nothing is touched
        msgs += tmsg(i)
        assert compaction.microcompact(msgs, 1, 1_000_000, 0.25, at_tokens=10_000) == (0, 0)
    assert json.dumps(msgs).startswith(snap[:-1]), "the earlier prefix is byte-identical"


def test_spilled_result_keeps_head_and_tail() -> None:
    cid = appmod.convos.create(None, "t", "test-model")["id"]
    result = {"log": "H" * 20_000 + "MIDDLE" + "z" * 20_000 + "Traceback: boom"}
    blob = working._dumps(result)
    out = json.loads(appmod.tool_results.for_model(cid, "m1", "shell_run", result))
    prev = out["preview"]
    assert prev.startswith(blob[:working.SPILL_HEAD_CHARS]) and prev.endswith(blob[-working.SPILL_TAIL_CHARS:]), "head and tail"
    gap = len(blob) - working.SPILL_HEAD_CHARS - working.SPILL_TAIL_CHARS
    assert f"{gap} chars omitted" in prev and out["result_id"] in prev and f"offset={working.SPILL_HEAD_CHARS}" in prev
    page = appmod.tool_results.read(cid, out["result_id"], working.SPILL_HEAD_CHARS, 50)
    assert page["text"] == blob[working.SPILL_HEAD_CHARS: working.SPILL_HEAD_CHARS + 50], "the marker's offset is where the gap starts"
    tail = appmod.tool_results.read(cid, out["result_id"], len(blob) - working.SPILL_TAIL_CHARS, 5000)
    assert tail["text"] == blob[-working.SPILL_TAIL_CHARS:] and not tail["has_more"]
