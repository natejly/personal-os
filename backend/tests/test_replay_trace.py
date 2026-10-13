"""Replay traces: local JSONL, redacted, never raising.

Run: PERSONAL_OS_TEST_CHILD=1 PYTHONPATH=backend python -m pytest backend/tests/test_replay_trace.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="replaytest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import replay_trace as rt  # noqa: E402

KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD"


def lines(d: Path) -> list[dict[str, Any]]:
    rt.flush()
    return [json.loads(ln) for f in sorted((d / "traces").glob("*.jsonl")) for ln in f.read_text().splitlines()]


def write_trace(d: Path, **kw: Any) -> None:
    base: dict[str, Any] = dict(
        conv_id="c1", message_id="m2", user_msg_id="m1", user_text="hello", reply="hi", requested_model="m", cfg={"defaultModel": "m", "modelHigh": "H", "modelMedium": "M", "modelLow": "L"},
        routed=False, model="m", used={"stable_system": "SYS", "skills": [{"id": "s1"}], "memories": [{"id": "x", "content": "likes tea"}],
                                      "section_tokens": {"memories": 5}, "tokens_estimate": 50},
        spans=[{"id": "a", "kind": "llm", "name": "m", "start": 1000, "end": 1500, "meta": {"usage": {"prompt_tokens": 9, "completion_tokens": 3, "cached_tokens": 4}}, "error": None},
               {"id": "b", "kind": "tool", "name": "web_search", "start": 1500, "end": 1700, "meta": {}, "parent_id": "a", "error": None}],
        tool_events=[{"id": "t1", "name": "web_search", "arguments": {"q": "x"}, "duration_ms": 200, "error": None}],
        cost=0.01, outcome="ok")
    rt.trace(d, **{**base, **kw})


def test_trace_written(tmp_path: Path) -> None:
    write_trace(tmp_path)
    (rec,) = lines(tmp_path)
    assert rec["schema"] == 1 and rec["type"] == "trace" and rec["gen_ai.response.id"] == "m2"
    assert rec["gen_ai.conversation.id"] == "c1" and rec["session.id"] == rt.SESSION_ID and rec["grain.message.user_id"] == "m1"
    assert rec["gen_ai.usage.input_tokens"] == 9 and rec["gen_ai.usage.cache_read.input_tokens"] == 4
    assert rec["grain.latency_ms"] == 700 and rec["grain.outcome"] == "ok" and rec["grain.cost.usd"] == 0.01
    assert rec["grain.model.tier_requested"] == "default" and rec["gen_ai.response.model"] == "m"
    assert rec["tool_calls"][0]["grain.tool.outcome"] == "ok" and rec["spans"][1]["parent_id"] == "a" and rec["spans"][0]["latency_ms"] == 500
    assert {"name": "total", "tokens": 50} in rec["grain.context.sections"]
    assert "SYS" not in json.dumps(rec) and "likes tea" not in json.dumps(rec)  # hashes only
    assert len(rec["grain.prompt.hash"]) == 64
    assert list((tmp_path / "traces").glob("????-??-??.jsonl"))


def test_redaction(tmp_path: Path) -> None:
    write_trace(tmp_path, user_text=f"use {KEY} please", reply=f"auth {BEARER}",
                tool_events=[{"id": "t", "name": "http", "arguments": {"api_key": "hunter2", "Authorization": BEARER, "url": "u", "n": {"password": "p", "h": f"x {KEY}"}},
                              "error": f"failed with {KEY}"}])
    raw = json.dumps(lines(tmp_path))
    assert KEY not in raw and BEARER not in raw and "hunter2" not in raw and '"password"' not in raw
    (rec,) = lines(tmp_path)
    args = rec["tool_calls"][0]["gen_ai.tool.call.arguments"]
    assert "api_key" not in args and "Authorization" not in args and args["url"] == "u"
    assert rec["tool_calls"][0]["grain.tool.outcome"] == "error"


@pytest.mark.parametrize("text,yes", [
    ("No, that's not what I asked", True), ("nope", True), ("That's wrong", True), ("not what I wanted", True), ("Actually, use blue", True),
    ("I meant the other file", True), ("wrong file", True), ("incorrect", True), ("You forgot the tests", True), ("instead do X", True),
    ("no problem, thanks", False), ("now do the next one", False), ("Thanks, that works", False), ("notes for later", False), ("", False),
])
def test_is_correction(text: str, yes: bool) -> None:
    assert rt.is_correction(text) is yes


def test_correction_followup(tmp_path: Path) -> None:
    rt.followup(tmp_path, conv_id="c1", kind="correction", message_id="m2", text=f"no, I meant {KEY}")
    rt.followup(tmp_path, conv_id="c1", kind="edit", message_id="m2")
    cor, edit = lines(tmp_path)
    assert cor["type"] == "followup" and cor["grain.followup.kind"] == "correction" and cor["gen_ai.response.id"] == "m2"
    assert cor["grain.correction.text"].startswith("no, I meant") and KEY not in cor["grain.correction.text"]
    assert edit["grain.followup.kind"] == "edit" and "grain.correction.text" not in edit


def test_boundaries(tmp_path: Path) -> None:
    rt.boundary(tmp_path, "c1", "clear")
    rt.boundary(tmp_path, "c1", "compact", 900, 200)
    clear, comp = lines(tmp_path)
    assert clear["type"] == "boundary" and clear["grain.boundary.kind"] == "clear"
    assert comp["grain.boundary.kind"] == "compact" and comp["grain.compact.tokens_before"] == 900 and comp["grain.compact.tokens_after"] == 200


def test_write_failure_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = blocker / "sub"  # a path under a regular file cannot be created
    write_trace(bad)
    write_trace(tmp_path, spans=None, used=None, tool_events=None)  # malformed input
    rt.followup(bad, conv_id="c", kind="edit", message_id=None)
    rt.boundary(bad, "c", "clear")
    rt.boundary(None, "c", "clear")
    rt.flush()


def test_concurrent_lines_do_not_interleave(tmp_path: Path) -> None:
    import threading
    ts = [threading.Thread(target=rt._write, args=(tmp_path, {"ts": int(time.time() * 1000), "pad": "x" * 50_000, "i": i})) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r["i"] for r in lines(tmp_path)) == list(range(20))


def test_chat_endpoint_writes_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from personal_os import llm
    from personal_os.app import AUTH_TOKEN, app, db

    async def fake(settings: Any, model: str, messages: Any, tools: Any = None, kind: str = "chat", **kw: Any) -> Any:
        yield {"type": "delta", "text": "pong"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}, "incomplete": False}

    monkeypatch.setattr(llm, "stream_chat", fake)
    with TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN}) as c:
        c.put("/settings", json={"autoLearn": False, "followUps": False, "baseUrl": ""})
        cid = c.post("/conversations", json={}).json()["id"]

        def turn(text: str) -> None:
            assert c.post(f"/conversations/{cid}/chat", json={"content": text}).status_code == 200
            end = time.time() + 15
            while time.time() < end and [r for r in c.get("/runs").json() if r["conversation_id"] == cid]:
                time.sleep(0.05)

        turn(f"ping {KEY}")
        turn("no, that is wrong")
        assert c.post(f"/conversations/{cid}/clear").status_code == 200
        recs = [r for r in lines(Path(db.data_dir)) if r.get("gen_ai.conversation.id") == cid]
    kinds = [r["type"] for r in recs]
    assert kinds.count("trace") == 2 and "followup" in kinds and "boundary" in kinds
    tr = next(r for r in recs if r["type"] == "trace")
    assert tr["grain.outcome"] == "ok" and tr["gen_ai.usage.input_tokens"] == 7 and KEY not in json.dumps(recs)
    assert "pong" in json.dumps(tr["gen_ai.output.messages"])
    assert next(r for r in recs if r["type"] == "followup")["grain.followup.kind"] == "correction"
