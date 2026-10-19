"""The per-turn context ledger through real scripted chat turns: block/total arithmetic, extension and memory entries,
stable-block identity and the cache verdict across turns, GET /messages/{id}/context, POST /context/preview and the
context meter's turn_tokens.

Run: python -m pytest backend/tests/test_context_ledger.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ledger-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.context import Contribution  # noqa: E402
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _turn(monkeypatch: Any, cid: str, text: str = "hello there") -> tuple[dict[str, Any], str]:
    """Runs one scripted turn; returns the streamed context_used and the reply's message id."""
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom", "autoTitle": False,
                            "followUps": False, "learnStyle": False, "permissionMode": "manual"})
    monkeypatch.setattr(llm, "stream_chat", _fake)
    out: dict[str, Any] = {}

    async def go() -> None:
        async for ev, data in appmod._chat_stream(cid, appmod.ChatIn(content=text), asyncio.Event()):
            if ev == "assistant_message":
                out.update(id=data["id"], used=data["context_used"] or {})
    asyncio.run(go())
    return out["used"], out["id"]


def _ext(ext_id: str, host: ExtensionHost | None = None) -> Any:
    host = host or appmod.host
    return host._add(Manifest.from_dict({"id": ext_id, "name": ext_id, "version": "1.0.0", "apiVersion": 1, "hooks": {"context.contribute": {}},
                                          "permissions": ["context:volatile", "prompt:inject"]}))


def _drop(scope: Any, ext_id: str) -> None:
    scope.disposables.dispose()
    del appmod.host.extensions[ext_id]


def _slot_sums(used: dict[str, Any]) -> dict[str, int]:
    sums = {"stable": 0, "volatile": 0, "tail": 0, "tools": 0, "history": 0}
    for b in used["blocks"]:
        sums[b["slot"]] += b["tokens"]
    return sums


def test_blocks_sum_to_totals_and_the_estimate(monkeypatch: Any) -> None:
    appmod.memories.create(None, "Kettle bells are my favourite ledger fact", pinned=True)
    cid = appmod.convos.create(None, "Ledger", "test-model")["id"]
    used, _ = _turn(monkeypatch, cid)
    sums = _slot_sums(used)
    assert {k: used["totals"][k] for k in sums} == sums
    assert used["totals"]["window"] > 0 and used["totals"]["tools"] > 0 and used["totals"]["history"] > 0
    assert used["tokens_estimate"] == sums["stable"] + sums["volatile"] + sums["tail"]
    assert any(b["kind"] == "memory" and b["id"].startswith("memory:") for b in used["blocks"]), [b["id"] for b in used["blocks"]]
    assert {t["name"] for t in used["tools_offered"]} >= {b["source"] for b in used["blocks"] if b["slot"] == "tools"}
    assert "ledger" not in used and "system_prompt" not in used  # the live object and the prompt text are stripped


def test_an_extension_block_carries_its_owner_and_version(monkeypatch: Any) -> None:
    s = _ext("ledger-demo")
    try:
        s.context(lambda p: Contribution("STABLE-LEDGER-NOTE", 4), slot="stable")
        s.context(lambda p: Contribution("VOLATILE-LEDGER-NOTE"), slot="volatile")
        used, _ = _turn(monkeypatch, appmod.convos.create(None, "Ext", "test-model")["id"])
    finally:
        _drop(s, "ledger-demo")
    mine = [b for b in used["blocks"] if b["owner"] == "ext:ledger-demo"]
    stable = next(b for b in mine if b["slot"] == "stable")
    assert stable["id"] == "ext:ledger-demo:v4" and stable["version"] == "4" and stable["kind"] == "extension" and stable["tokens"] > 0
    assert {b["slot"] for b in mine} == {"stable", "volatile"}
    print("EXT ENTRY", json.dumps(stable))


def test_a_stable_block_is_identical_across_turns_and_the_cache_holds(monkeypatch: Any) -> None:
    cid = appmod.convos.create(None, "Stable", "test-model")["id"]
    u1, m1 = _turn(monkeypatch, cid)
    u2, m2 = _turn(monkeypatch, cid, "and again")
    stable1 = [b for b in u1["blocks"] if b["slot"] == "stable"]
    stable2 = [b for b in u2["blocks"] if b["slot"] == "stable"]
    assert stable1 and stable1 == stable2
    assert u1["stable_hash"] == u2["stable_hash"] and u2["cache"] == {"changed": False}
    t1 = client.get(f"/messages/{m1}/context").json()["texts"]
    t2 = client.get(f"/messages/{m2}/context").json()["texts"]
    assert all(t1[b["id"]].encode() == t2[b["id"]].encode() for b in stable1)


def test_message_context_route_serves_texts_until_the_log_is_gone(monkeypatch: Any) -> None:
    s = _ext("ledger-texts")
    try:
        s.context(lambda p: Contribution("VOLATILE-TEXT-MARK"), slot="volatile")
        cid = appmod.convos.create(None, "Texts", "test-model")["id"]
        used, mid = _turn(monkeypatch, cid)
    finally:
        _drop(s, "ledger-texts")
    r = client.get(f"/messages/{mid}/context")
    assert r.status_code == 200
    body = r.json()
    assert body["texts_available"] is True and body["blocks"] == used["blocks"] and body["totals"] == used["totals"]
    assert set(body) >= {"blocks", "totals", "stable_hash", "cache", "skipped", "tools_offered", "tokens_estimate", "system_prompt", "texts"}
    assert "VOLATILE-TEXT-MARK" in body["system_prompt"]
    ext = next(b for b in body["blocks"] if b["owner"] == "ext:ledger-texts")
    assert "VOLATILE-TEXT-MARK" in body["texts"][ext["id"]]
    assert {b["id"] for b in body["blocks"] if b["slot"] in ("stable", "volatile", "tail")} <= set(body["texts"])
    row = json.dumps(appmod.convos.get(cid)["messages"])
    assert "VOLATILE-TEXT-MARK" not in row  # never in the message row
    (appmod.db.data_dir / "requests" / f"{mid}.jsonl").unlink()
    gone = client.get(f"/messages/{mid}/context").json()
    assert gone["texts_available"] is False and "texts" not in gone and gone["blocks"] == used["blocks"]
    assert client.get("/messages/nope/context").status_code == 404


def test_preview_returns_the_ledger_of_a_turn(monkeypatch: Any) -> None:
    cid = appmod.convos.create(None, "Preview", "test-model")["id"]
    _turn(monkeypatch, cid)
    s = _ext("ledger-preview")
    try:
        s.context(lambda p: Contribution("PREVIEW-EXT-NOTE", 2), slot="stable")
        r = client.post("/context/preview", json={"query": "kettle", "conversation_id": cid})
    finally:
        _drop(s, "ledger-preview")
    assert r.status_code == 200
    body = r.json()
    assert body["blocks"] and body["tools_offered"] and body["totals"]["tools"] > 0 and body["totals"]["history"] > 0
    assert _slot_sums(body) == {k: body["totals"][k] for k in _slot_sums(body)}
    ids = [b["id"] for b in body["blocks"]]
    assert "hint:no-emoji" in ids and "ext:ledger-preview:v2" in ids and "volatile:today" in ids
    assert "PREVIEW-EXT-NOTE" in body["texts"]["ext:ledger-preview:v2"] and body["system_prompt"]
    assert client.post("/context/preview", json={"conversation_id": "nope"}).status_code == 404
    assert client.post("/context/preview", json={"query": "x"}).json()["totals"]["history"] == 0


def test_context_meter_adds_turn_tokens(monkeypatch: Any) -> None:
    cid = appmod.convos.create(None, "Meter", "test-model")["id"]
    _turn(monkeypatch, cid)
    m = client.get(f"/conversations/{cid}/context-meter").json()
    assert m["history_tokens"] == m["estimated_tokens"] > 0
    assert m["turn_tokens"] > m["history_tokens"]
    assert {"window", "compact_at", "summary", "spend"} <= set(m)
