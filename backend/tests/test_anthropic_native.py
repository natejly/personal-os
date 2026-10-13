"""Edge translation to Anthropic's native Messages API (anthropic_native.py) and the routing that selects it."""
from __future__ import annotations

import asyncio
import json

from personal_os import anthropic_native as an
from personal_os import llm
from personal_os.context import layout_messages

TOOL = {"type": "function", "function": {"name": "get_weather", "description": "d", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}


def _openai_body() -> dict:
    hist = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "rain"},
        {"role": "tool", "tool_call_id": "t2", "content": "sun"},
        {"role": "user", "content": [{"type": "text", "text": "u2"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
    ]
    body = {"model": "claude-haiku-5-5", "messages": layout_messages("STABLE", ["vol"], hist), "tools": [TOOL], "tool_choice": "auto",
            "stream": True, "stream_options": {"include_usage": True}, "reasoning_effort": "high", "max_tokens": 1000}
    return llm.with_cache_breakpoints("claude-haiku-5-5", body)


def test_request_translation():
    r = an.to_request(_openai_body())
    assert r["system"] == [{"type": "text", "text": "STABLE", "cache_control": {"type": "ephemeral"}}]
    assert r["max_tokens"] == 1000 and r["stream"] is True
    assert not {"reasoning_effort", "stream_options"} & set(r)
    assert r["output_config"] == {"effort": "high"}
    roles = [m["role"] for m in r["messages"]]
    assert roles == ["user", "assistant", "user"]  # both tool results and the retrieval block + user turn merge
    assert r["messages"][1]["content"] == [{"type": "tool_use", "id": "t1", "name": "get_weather", "input": {"city": "Oslo"}}]
    last = r["messages"][2]["content"]
    assert [b["type"] for b in last] == ["tool_result", "tool_result", "text", "text", "image"]
    assert last[0]["tool_use_id"] == "t1" and last[0]["content"] == "rain"
    assert last[2]["text"].startswith("## Context for this turn")
    assert last[4]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert r["tools"] == [{"name": "get_weather", "description": "d", "input_schema": TOOL["function"]["parameters"], "cache_control": {"type": "ephemeral"}}]
    assert r["tool_choice"] == {"type": "auto"}
    marks = sum("cache_control" in b for m in r["messages"] for b in m["content"]) + ("cache_control" in r["system"][0]) + ("cache_control" in r["tools"][-1])
    assert marks <= 4 and "cache_control" in last[-1]


def test_tool_choice_mapping():
    b = {"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": [TOOL]}
    assert an.to_request({**b, "tool_choice": "required"})["tool_choice"] == {"type": "any"}
    assert an.to_request({**b, "tool_choice": {"type": "function", "function": {"name": "get_weather"}}})["tool_choice"] == {"type": "tool", "name": "get_weather"}


def test_effort_mapping_and_refusal_retry():
    b = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert [an.to_request({**b, "reasoning_effort": e}).get("output_config") for e in ("low", "xhigh", "max", "none")] == [
        {"effort": "low"}, {"effort": "xhigh"}, {"effort": "max"}, None]
    # a 400 naming output_config is handled by the existing optional-field drop, which pops reasoning_effort and re-translates
    err = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "output_config.effort: not supported by this model"}})
    assert llm._rejected_optional(400, err, {"reasoning_effort": "high"}) == "reasoning_effort"


def test_usage_mapping():
    u = an.usage({"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 90, "output_tokens": 5})
    p = llm.parse_usage(u)
    assert (p["prompt_tokens"], p["completion_tokens"], p["cached_tokens"], p["cache_write_tokens"]) == (1000, 5, 900, 90)


EVENTS = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {}}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"city":'}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ' "Oslo"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
    {"type": "message_stop"},
]


def test_sse_translation_through_the_chunk_parser():
    async def lines():
        yield "event: ping"
        for e in EVENTS:
            yield f"event: {e['type']}"
            yield "data: " + json.dumps(e)

    async def run():
        return [x async for x in an.stream_lines(lines())]

    out = asyncio.run(run())
    assert out[-1] == "data: [DONE]"
    chunks = [json.loads(x[5:]) for x in out[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "Hi there"
    assert [c["choices"][0]["finish_reason"] for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]] == ["tool_calls"]
    frags = [tc for c in chunks if c["choices"] for tc in c["choices"][0]["delta"].get("tool_calls", [])]
    assert frags[0]["id"] == "tu1" and frags[0]["index"] == 0
    assert "".join(f["function"]["arguments"] for f in frags) == '{"city": "Oslo"}'
    usage = next(c["usage"] for c in chunks if "usage" in c)
    assert (usage["prompt_tokens"], usage["completion_tokens"], usage["prompt_tokens_details"]["cached_tokens"]) == (53, 20, 50)


def test_non_stream_response():
    r = an.to_response({"content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "a", "name": "n", "input": {"x": 1}}],
                        "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 2}})
    m = r["choices"][0]
    assert m["finish_reason"] == "tool_calls" and m["message"]["content"] == "ok"
    assert json.loads(m["message"]["tool_calls"][0]["function"]["arguments"]) == {"x": 1}


def test_only_the_anthropic_provider_goes_native():
    assert an.is_native({"baseUrl": "https://api.anthropic.com/v1/", "provider": "anthropic"})
    for url, prov in (("https://openrouter.ai/api/v1", "openrouter"), ("http://localhost:4000", "litellm"), ("https://api.fireworks.ai/inference/v1", "fireworks")):
        assert not an.is_native({"baseUrl": url, "provider": prov})
