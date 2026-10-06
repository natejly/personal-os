"""Opt-in export of a reply's trace as OpenTelemetry OTLP/HTTP JSON, using the GenAI semantic conventions.

No SDK: a trace is a list of plain span dicts (trace.py), so the request body is built by hand.
Any OTLP collector accepts it. Attributes use the `gen_ai.*` names and mirror
the OpenInference `llm.token_count.*` / `openinference.span.kind` ones so OpenInference-aware viewers render them natively.

Privacy is the whole design:
- Off by default, and it posts only to the endpoint the user typed.
- A loopback endpoint is fine; a remote one is refused unless `allowRemote` is set.
- Without `includeContent`, only names, timings, token counts, model, finish reasons, tool names,
  error strings and ids go out. The system prompt, memories and retrieved text never do, with or
  without the flag.
- Nothing here can fail or delay a reply.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException

from .db import Database
from .trace import now_ms

log = logging.getLogger(__name__)

SERVICE_VERSION = "0.1.0"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
Post = Callable[[str, dict[str, Any], dict[str, str], float], Awaitable[int]]

KIND = {"llm": "LLM", "tool": "TOOL", "context": "RETRIEVER"}  # everything else is a CHAIN


def _hex(text: str, n: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def _val(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_val(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _val(v)} for k, v in d.items() if v is not None]


def _ns(ms: float) -> str:
    return str(int(ms * 1_000_000))


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _span_attrs(s: dict[str, Any], model: str, with_content: bool) -> dict[str, Any]:
    m = s.get("meta") or {}
    kind = s.get("kind")
    a: dict[str, Any] = {"openinference.span.kind": KIND.get(kind, "CHAIN"), "grain.span.kind": kind}
    if kind == "llm":
        u = m.get("usage") or {}
        pt, ct = _int(u.get("prompt_tokens")), _int(u.get("completion_tokens"))
        a.update({
            "gen_ai.operation.name": "chat", "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": pt, "gen_ai.usage.output_tokens": ct,
            "gen_ai.response.finish_reasons": [str(m["finish_reason"])] if m.get("finish_reason") else None,
            "llm.token_count.prompt": pt, "llm.token_count.completion": ct, "llm.token_count.total": _int(u.get("total_tokens")) or pt + ct,
            "llm.token_count.prompt_details.cache_read": _int(u.get("cached_tokens")),
            "llm.token_count.prompt_details.cache_write": _int(u.get("cache_write_tokens")),
            "llm.token_count.completion_details.reasoning": _int(u.get("reasoning_tokens")),
            "grain.round": m.get("round"),
        })
    elif kind == "tool":
        a.update({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": s.get("name")})
        if with_content and m.get("arguments") is not None:
            a["gen_ai.tool.call.arguments"] = json.dumps(m["arguments"], default=str)
    elif kind == "context":
        a.update({"grain.context.memories": _int(m.get("memories")), "grain.context.entities": _int(m.get("entities")),
                  "grain.context.excerpts": _int(m.get("excerpts")), "grain.context.stable_hash": m.get("stable_hash")})
    elif kind == "compact":
        a.update({"grain.compact.kind": m.get("kind"), "grain.compact.cleared": _int(m.get("cleared")),
                  "grain.compact.summarized": _int(m.get("summarized")), "grain.compact.tokens_saved": _int(m.get("tokens_saved"))})
    return a


def to_otlp(trace_spans: list[dict[str, Any]], *, conversation_id: str, message_id: str, model: str, project_name: str | None = None,
            include_content: bool = False, content: dict[str, Any] | None = None) -> dict[str, Any]:
    """An OTLP/JSON ExportTraceServiceRequest. `content` is {"reply": str, "used": context_used}; it is read
    only when include_content is true."""
    with_content = bool(include_content)
    trace_id = _hex(message_id, 32)
    root_id = _hex("root:" + message_id, 16)
    end_default = now_ms()
    starts = [s["start"] for s in trace_spans] or [end_default]
    ends = [(s.get("end") or end_default) for s in trace_spans] or [end_default]
    t0, t1 = min(starts), max(ends)
    known = {s["id"] for s in trace_spans}

    root_attrs: dict[str, Any] = {"gen_ai.operation.name": "invoke_agent", "gen_ai.request.model": model,
                                  "openinference.span.kind": "AGENT", "session.id": conversation_id,
                                  "gen_ai.conversation.id": conversation_id}
    if with_content and (content or {}).get("reply"):
        root_attrs["gen_ai.output.messages"] = json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": content["reply"]}]}])
    spans: list[dict[str, Any]] = [{
        "traceId": trace_id, "spanId": root_id, "name": "grain.reply", "kind": 1,
        "startTimeUnixNano": _ns(t0), "endTimeUnixNano": _ns(t1), "attributes": _attrs(root_attrs), "status": {"code": 1},
    }]
    for s in trace_spans:
        parent = s.get("parent_id")
        end = s.get("end") or end_default
        out: dict[str, Any] = {
            "traceId": trace_id, "spanId": _hex(s["id"], 16),
            "parentSpanId": _hex(parent, 16) if parent in known else root_id,
            "name": s.get("name") or s.get("kind") or "span", "kind": 3 if s.get("kind") == "llm" else 1,
            "startTimeUnixNano": _ns(s["start"]), "endTimeUnixNano": _ns(max(end, s["start"])),
            "attributes": _attrs(_span_attrs(s, model, with_content)),
            "status": {"code": 2, "message": str(s["error"])} if s.get("error") else {"code": 1},
        }
        spans.append(out)
    res = {"service.name": "grain", "service.version": SERVICE_VERSION, "session.id": conversation_id,
           "gen_ai.conversation.id": conversation_id, "grain.project": project_name if with_content else None}
    return {"resourceSpans": [{"resource": {"attributes": _attrs(res)},
                               "scopeSpans": [{"scope": {"name": "grain.trace", "version": SERVICE_VERSION}, "spans": spans}]}]}


def _endpoint(raw: str) -> str:
    url = raw.strip()
    return url if url.rstrip("/").endswith("/v1/traces") else url.rstrip("/") + "/v1/traces"


async def _default_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> int:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload, headers={"Content-Type": "application/json", **headers})
    return r.status_code


async def export(settings: dict[str, Any], payload: dict[str, Any], post: Post | None = None) -> dict[str, Any]:
    """POST the payload to the configured endpoint. Never raises."""
    cfg = settings.get("otelExport") if isinstance(settings.get("otelExport"), dict) else {}
    if not cfg.get("enabled") or not str(cfg.get("endpoint") or "").strip():
        return {"sent": False, "reason": "disabled", "status": None, "error": None}
    try:
        url = _endpoint(str(cfg["endpoint"]))
        host = (urlparse(url).hostname or "").lower()
        if host not in LOOPBACK and not cfg.get("allowRemote"):
            return {"sent": False, "reason": "remote-blocked", "status": None, "error": None}
        headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()} if isinstance(cfg.get("headers"), dict) else {}
        timeout = float(cfg.get("timeoutSeconds") or 5)
        status = await (post or _default_post)(url, payload, headers, timeout)
        ok = 200 <= int(status) < 300
        return {"sent": ok, "status": int(status), "error": None if ok else f"HTTP {status}"}
    except Exception as e:  # noqa: BLE001 - tracing must never break a reply
        return {"sent": False, "status": None, "error": str(e) or type(e).__name__}


_tasks: set[asyncio.Task[Any]] = set()


def export_in_background(settings: dict[str, Any], conversation_id: str, message_id: str, model: str, project_name: str | None,
                         spans: list[dict[str, Any]], used: dict[str, Any] | None, reply: str) -> None:
    """Fire and forget after the reply is saved, so it can never delay `done`."""
    cfg = settings.get("otelExport")
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return
    try:
        payload = to_otlp(list(spans), conversation_id=conversation_id, message_id=message_id, model=model, project_name=project_name,
                          include_content=bool(cfg.get("includeContent")), content={"reply": reply, "used": used or {}})
        task = asyncio.get_running_loop().create_task(export(settings, payload))
    except Exception:  # noqa: BLE001
        log.warning("otel export setup failed", exc_info=True)
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def router(db: Database, settings_fn: Callable[[], dict[str, Any]]) -> APIRouter:
    r = APIRouter()

    @r.get("/messages/{message_id}/otlp")
    def message_otlp(message_id: str) -> dict[str, Any]:
        """The saved trace as OTLP/JSON, content omitted. Works with no endpoint configured."""
        with db.tx() as c:
            row = c.execute("SELECT m.conversation_id, m.model, m.trace, p.name AS project FROM messages m "
                            "JOIN conversations v ON v.id = m.conversation_id LEFT JOIN projects p ON p.id = v.project_id WHERE m.id=?",
                            (message_id,)).fetchone()
        if not row or not row["trace"]:
            raise HTTPException(404, "No trace for that message")
        return to_otlp(json.loads(row["trace"]), conversation_id=row["conversation_id"], message_id=message_id, model=row["model"] or "")

    @r.post("/traces/export-test")
    async def export_test() -> dict[str, Any]:
        t = now_ms()
        span = {"id": "test", "kind": "context", "name": "Grain export test", "start": t - 5, "end": t, "meta": {}, "error": None}
        payload = to_otlp([span], conversation_id="test", message_id=f"test-{t}", model="")
        return await export(settings_fn(), payload)

    return r
