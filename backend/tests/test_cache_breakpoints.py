"""Anthropic models get cache_control breakpoints; automatic-prefix providers get none; cache reads parse to cached_tokens."""
from __future__ import annotations

from personal_os import llm
from personal_os.context import layout_messages


def _body(model: str) -> dict:
    hist = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "u2"}]
    return {"model": model, "messages": layout_messages("STABLE", ["vol"], hist),
            "tools": [{"type": "function", "function": {"name": "a"}}, {"type": "function", "function": {"name": "b"}}]}


def _marked(body: dict) -> list:
    out = [i for i, m in enumerate(body["messages"]) if isinstance(m["content"], list) and "cache_control" in m["content"][-1]]
    return out + (["tools"] if "cache_control" in body["tools"][-1] else [])


def test_claude_marks_four_breakpoints():
    body = llm.with_cache_breakpoints("claude-sonnet-5-5", _body("claude-sonnet-5-5"))
    # [system, u1, a1, volatile, u2]: stable system, message before the volatile block, last message, last tool
    assert _marked(body) == [0, 2, 4, "tools"]
    assert "cache_control" not in body["tools"][0]


def test_tool_loop_also_marks_last_message():
    b = _body("claude-sonnet-5-5")
    b["messages"] += [{"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}, {"role": "tool", "tool_call_id": "1", "content": "r"}]
    assert _marked(llm.with_cache_breakpoints("claude-sonnet-5-5", b)) == [0, 2, 6, "tools"]


def test_other_providers_untouched():
    assert llm.with_cache_breakpoints("kimi-k3", _body("kimi-k3")) == _body("kimi-k3")


def test_cache_read_input_tokens_parse():
    u = llm.parse_usage({"prompt_tokens": 1000, "completion_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50})
    assert (u["cached_tokens"], u["cache_write_tokens"]) == (900, 50)
    assert llm.parse_usage({"prompt_tokens_details": {"cached_tokens": 7}})["cached_tokens"] == 7
