"""Nested spans and the opt-in OTLP/JSON export.

Run: backend/.venv/bin/python backend/tests/test_otel_export.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="oteltest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import otel_export  # noqa: E402
from personal_os.trace import Tracer  # noqa: E402

passed = 0
SECRET = "SEKRIT-planted-string"


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---- 1. Tracer parent ids
t = Tracer()
root = t.start("llm", "m", {"round": 1})
a = t.start("tool", "web_search", {}, parent=root)
b = t.start("tool", "fetch_url", {}, parent=root)
plain = t.start("context", "ctx")
check(a["parent_id"] == root["id"] and b["parent_id"] == root["id"] and "parent_id" not in plain, "parent_id set only when given")
t.end(root, {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": 4, "reasoning_tokens": 2}})
sm = t.summary()
check(sm["prompt_tokens"] == 10 and sm["cached_tokens"] == 4 and sm["reasoning_tokens"] == 2 and sm["tool_calls"] == 2, "summary sums")
old = Tracer([{"id": "x", "kind": "llm", "name": "m", "start": 1, "end": 2, "meta": {"usage": {"prompt_tokens": 3, "completion_tokens": 1}}, "error": None}])
check(old.summary()["prompt_tokens"] == 3 and old.summary()["cached_tokens"] == 0, "old flat spans unchanged")

# ---- 2. to_otlp
spans = [
    {"id": "s-ctx", "kind": "context", "name": "Assemble context", "start": 1000, "end": 1010, "meta": {"memories": 2, "entities": 1, "excerpts": 0}, "error": None},
    {"id": "s-llm", "kind": "llm", "name": "m", "start": 1010, "end": 1500,
     "meta": {"round": 1, "finish_reason": "tool_calls", "usage": {"prompt_tokens": 900, "completion_tokens": 40, "cached_tokens": 700, "reasoning_tokens": 10}}, "error": None},
    {"id": "s-tool", "kind": "tool", "name": "web_search", "start": 1500, "end": 1800, "meta": {"arguments": {"query": SECRET}}, "parent_id": "s-llm", "error": None},
    {"id": "s-bad", "kind": "tool", "name": "fetch_url", "start": 1800, "end": 1900, "meta": {}, "parent_id": "s-llm", "error": "timed out"},
    {"id": "s-cmp", "kind": "compact", "name": "Compact history", "start": 1005, "end": 1008, "meta": {"kind": "history", "summarized": 12}, "parent_id": "s-ctx", "error": None},
]
p = otel_export.to_otlp(spans, conversation_id="conv1", message_id="msg1", model="m")
sp = p["resourceSpans"][0]["scopeSpans"][0]["spans"]
byname = {s["name"]: s for s in sp}


def attr(span: dict[str, Any], key: str) -> Any:
    for kv in span["attributes"]:
        if kv["key"] == key:
            return next(iter(kv["value"].values()))
    return None


check(len(sp) == 6 and byname["grain.reply"]["name"] == "grain.reply", "root plus one span per span")
check(len(sp[0]["traceId"]) == 32 and len({s["traceId"] for s in sp}) == 1, "trace id 32 hex, shared")
check(p == otel_export.to_otlp(spans, conversation_id="conv1", message_id="msg1", model="m"), "deterministic")
check(all(len(s["spanId"]) == 16 for s in sp) and len({s["spanId"] for s in sp}) == 6, "span ids 16 hex and unique")
check(byname["web_search"]["parentSpanId"] == byname["m"]["spanId"], "tool nests under its llm round")
check(byname["m"]["parentSpanId"] == byname["grain.reply"]["spanId"], "unparented spans hang off the root")
check(byname["Compact history"]["parentSpanId"] == byname["Assemble context"]["spanId"], "compact nests under context")
check(attr(byname["m"], "gen_ai.usage.input_tokens") == "900" and attr(byname["m"], "gen_ai.usage.output_tokens") == "40", "usage mapped")
check(attr(byname["m"], "llm.token_count.prompt_details.cache_read") == "700", "cache tokens mirrored")
check(attr(byname["web_search"], "gen_ai.tool.name") == "web_search" and attr(byname["web_search"], "gen_ai.operation.name") == "execute_tool", "tool attrs")
check(byname["fetch_url"]["status"] == {"code": 2, "message": "timed out"}, "error status")
check(all(int(s["startTimeUnixNano"]) <= int(s["endTimeUnixNano"]) for s in sp) and int(sp[1]["startTimeUnixNano"]) == 1000 * 1_000_000, "ns timestamps")
check(attr(sp[0], "session.id") == "conv1", "session id on root")

# ---- 3. privacy
reply = f"the answer is {SECRET}"
off = json.dumps(otel_export.to_otlp(spans, conversation_id="c", message_id="m", model="m", include_content=False, content={"reply": reply, "used": {}}))
check(SECRET not in off, "no content when flag is off")
on = json.dumps(otel_export.to_otlp(spans, conversation_id="c", message_id="m", model="m", include_content=True, content={"reply": reply, "used": {}}))
check(SECRET in on, "content present when flag is on")
vetoed = json.dumps(otel_export.to_otlp(spans, conversation_id="c", message_id="m", model="m", include_content=True,
                                        content={"reply": reply, "used": {"meetings": "standup notes"}}))
check(SECRET not in vetoed, "meetings in context vetoes content")
vetoed2 = json.dumps(otel_export.to_otlp(spans, conversation_id="c", message_id="m", model="m", include_content=True,
                                         content={"reply": reply, "used": {"activity": "block"}}))
check(SECRET not in vetoed2, "activity in context vetoes content")

# ---- 4. export
sent: list[tuple[str, dict[str, str]]] = []


async def ok_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> int:
    sent.append((url, headers))
    return 200


async def bad_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> int:
    raise RuntimeError("connection refused")


def cfg(**kw: Any) -> dict[str, Any]:
    return {"otelExport": {"enabled": True, "endpoint": "http://localhost:6006", "headers": {"x-key": "k"}, **kw}}


r = run(otel_export.export({"otelExport": {"enabled": False, "endpoint": "http://localhost:6006"}}, p, post=ok_post))
check(r["sent"] is False and r["reason"] == "disabled" and not sent, "disabled never posts")
r = run(otel_export.export(cfg(), p, post=ok_post))
check(r["sent"] is True and sent[0][0] == "http://localhost:6006/v1/traces" and sent[0][1] == {"x-key": "k"}, "loopback posts with /v1/traces and headers")
r = run(otel_export.export(cfg(endpoint="http://localhost:6006/v1/traces"), p, post=ok_post))
check(sent[1][0] == "http://localhost:6006/v1/traces", "no double suffix")
n = len(sent)
r = run(otel_export.export(cfg(endpoint="https://example.com"), p, post=ok_post))
check(r["sent"] is False and r["reason"] == "remote-blocked" and len(sent) == n, "remote refused by default")
r = run(otel_export.export(cfg(endpoint="https://example.com", allowRemote=True), p, post=ok_post))
check(r["sent"] is True and sent[-1][0] == "https://example.com/v1/traces", "remote allowed when explicit")
r = run(otel_export.export(cfg(), p, post=bad_post))
check(r["sent"] is False and "refused" in r["error"], "post failure returned, not raised")

# ---- 5. routes
client = TestClient(appmod.app)
H = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
conv = appmod.convos.create(None, "t", "m")
am = appmod.convos.add_message(conv["id"], "assistant", "", model="m")
appmod.convos.finish_message(am["id"], "hello", None, None, None, spans)
res = client.get(f"/messages/{am['id']}/otlp", headers=H)
check(res.status_code == 200 and "resourceSpans" in res.json() and SECRET not in res.text, "otlp download, content omitted")
check(client.get("/messages/nope/otlp", headers=H).status_code == 404, "unknown message 404")
appmod.db.set_settings({"otelExport": {"enabled": True, "endpoint": "http://127.0.0.1:4318", "headers": {}}})
orig = otel_export._default_post
otel_export._default_post = ok_post  # type: ignore[assignment]
try:
    r = client.post("/traces/export-test", headers=H)
finally:
    otel_export._default_post = orig  # type: ignore[assignment]
check(r.status_code == 200 and r.json()["sent"] is True and sent[-1][0] == "http://127.0.0.1:4318/v1/traces", "export-test posts via stub")

print(f"test_otel_export: {passed} checks passed")
