"""Edge translation between the OpenAI-shaped requests the app builds and Anthropic's native Messages API.

The OpenAI-compatible endpoint ignores `cache_control`, so a request to the Anthropic provider goes to /v1/messages
instead. Everything above llm._send_attempts keeps seeing chat-completions data: `to_request` rewrites the body,
`stream_lines` re-emits the Messages SSE events as chat-completion chunk lines, `to_response` does the same for a
non-streamed reply.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from . import providers

VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 64_000
EFFORTS = ("low", "medium", "high", "xhigh", "max")  # what output_config.effort takes (probed live; "none" is a 400)
STOP_REASONS = {"tool_use": "tool_calls", "end_turn": "stop", "stop_sequence": "stop", "pause_turn": "stop",
                "max_tokens": "length", "refusal": "content_filter"}


def is_native(settings: dict[str, Any]) -> bool:
    return providers.effective(settings) == "anthropic"


def headers(settings: dict[str, Any]) -> dict[str, str]:
    return {"Content-Type": "application/json", "anthropic-version": VERSION, "x-api-key": str(settings.get("apiKey") or "")}


def _parts(content: Any) -> list[dict[str, Any]]:
    """OpenAI message content (string or parts) -> Messages content blocks; empty text is dropped (the API rejects it)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    out: list[dict[str, Any]] = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "image_url":
            url = str((p.get("image_url") or {}).get("url") or "")
            if url.startswith("data:") and ";base64," in url:
                head, data = url[5:].split(";base64,", 1)
                out.append({"type": "image", "source": {"type": "base64", "media_type": head, "data": data}})
            elif url:
                out.append({"type": "image", "source": {"type": "url", "url": url}})
            if url and "cache_control" in p:
                out[-1]["cache_control"] = p["cache_control"]
        elif p.get("type") == "text" and p.get("text"):
            out.append({k: v for k, v in p.items() if k in ("type", "text", "cache_control")})
    return out


def _tool_result(m: dict[str, Any]) -> dict[str, Any]:
    blocks = _parts(m.get("content"))
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                             "content": "".join(b.get("text", "") for b in blocks if b["type"] == "text") or "(no output)"}
    if mark := next((b["cache_control"] for b in blocks if "cache_control" in b), None):
        block["cache_control"] = mark  # on the result itself, not on the text inside it
    return block


def to_request(body: dict[str, Any]) -> dict[str, Any]:
    """A chat-completions body -> a Messages body. `reasoning_effort` becomes `output_config.effort`. Fields only the OpenAI shape has (service_tier, user,
    stream_options, parallel_tool_calls) are not sent."""
    system: list[dict[str, Any]] = []
    msgs: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if msgs and msgs[-1]["role"] == role:  # consecutive tool results, and a retrieval block ahead of the user turn, share one message
            msgs[-1]["content"].extend(blocks)
        else:
            msgs.append({"role": role, "content": list(blocks)})

    for m in body["messages"]:
        role = m.get("role")
        if role == "system":
            blocks = _parts(m.get("content"))
            if msgs:
                add("user", blocks)  # mid-history (the per-turn retrieval block): stays where it is, as user text
            else:
                system.extend(blocks)
        elif role == "tool":
            add("user", [_tool_result(m)])
        elif role == "assistant":
            blocks = _parts(m.get("content"))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args if isinstance(args, dict) else {}})
            add("assistant", blocks)
        else:
            add("user", _parts(m.get("content")))
    out: dict[str, Any] = {"model": body["model"], "messages": msgs, "max_tokens": body.get("max_tokens") or DEFAULT_MAX_TOKENS,
                           "stream": bool(body.get("stream"))}
    if system:
        out["system"] = system
    if body.get("reasoning_effort") in EFFORTS:  # anything else ("none", "off") means no field
        out["output_config"] = {"effort": body["reasoning_effort"]}
    if tools := body.get("tools"):
        out["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description") or "",
                         "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}},
                         **({"cache_control": t["cache_control"]} if "cache_control" in t else {})} for t in tools]
        choice = body.get("tool_choice")
        if isinstance(choice, dict):
            out["tool_choice"] = {"type": "tool", "name": (choice.get("function") or {}).get("name")}
        elif choice in ("required", "any"):
            out["tool_choice"] = {"type": "any"}
        elif choice == "none":
            out["tool_choice"] = {"type": "none"}
        else:
            out["tool_choice"] = {"type": "auto"}
    return out


def usage(u: dict[str, Any] | None) -> dict[str, Any]:
    """Messages usage -> chat-completions usage. input_tokens excludes the cached part, which is added back so
    prompt_tokens is the whole prompt (the billing code subtracts cached and written from it)."""
    u = u or {}
    read, write = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
    pt, ct = int(u.get("input_tokens") or 0) + read + write, int(u.get("output_tokens") or 0)
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
            "prompt_tokens_details": {"cached_tokens": read}, "cache_creation_input_tokens": write}


def to_response(data: dict[str, Any]) -> dict[str, Any]:
    """A non-streamed Messages reply -> a chat-completions reply."""
    blocks = data.get("content") or []
    calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
             for b in blocks if b.get("type") == "tool_use"]
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(b.get("text", "") for b in blocks if b.get("type") == "text")}
    if calls:
        msg["tool_calls"] = calls
    return {"choices": [{"index": 0, "message": msg, "finish_reason": STOP_REASONS.get(data.get("stop_reason"), "stop")}],
            "usage": usage(data.get("usage"))}


async def stream_lines(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Messages SSE lines -> chat-completion chunk lines ("data: {...}", ending with "data: [DONE]")."""
    def chunk(delta: dict[str, Any] | None = None, finish: str | None = None, **extra: Any) -> str:
        choices = [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if (delta is not None or finish) else []
        return "data: " + json.dumps({"choices": choices, **extra})

    start: dict[str, Any] = {}
    tools: dict[int, int] = {}  # content block index -> tool call index
    async for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "message_start":
            start = dict((ev.get("message") or {}).get("usage") or {})
        elif kind == "content_block_start" and (ev.get("content_block") or {}).get("type") == "tool_use":
            cb, tools[ev["index"]] = ev["content_block"], len(tools)
            yield chunk({"tool_calls": [{"index": tools[ev["index"]], "id": cb["id"], "type": "function", "function": {"name": cb["name"], "arguments": ""}}]})
        elif kind == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta":
                yield chunk({"content": d.get("text", "")})
            elif d.get("type") == "thinking_delta":
                yield chunk({"reasoning_content": d.get("thinking", "")})
            elif d.get("type") == "input_json_delta" and ev.get("index") in tools:
                yield chunk({"tool_calls": [{"index": tools[ev["index"]], "function": {"arguments": d.get("partial_json", "")}}]})
        elif kind == "message_delta":
            start.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
            yield chunk({}, STOP_REASONS.get((ev.get("delta") or {}).get("stop_reason"), "stop"))
        elif kind == "message_stop":
            yield chunk(usage=usage(start))
            yield "data: [DONE]"
            return
        elif kind == "error":
            yield "data: " + json.dumps({"error": ev.get("error") or {}})
            return
