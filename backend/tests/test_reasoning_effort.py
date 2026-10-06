"""Reasoning effort: new chats start at low, and Kimi K3 only hears a value it accepts.

K3 rejects medium and xhigh, and a missing field is its own max. The dropdown can still say
Medium; the wire value for that model is high.

Runs under pytest, or directly: python backend/tests/test_reasoning_effort.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.llm import effort_param  # noqa: E402
from personal_os.repos import Conversations, DEFAULT_EFFORT  # noqa: E402


def test_new_chat_starts_at_low() -> None:
    with tempfile.TemporaryDirectory() as d:
        conv = Conversations(Database(d)).create(None, "hi", "kimi-k3")
    assert conv["settings"]["effort"] == "low"
    assert DEFAULT_EFFORT == "low"


def test_kimi_k3_medium_is_sent_as_high() -> None:
    assert effort_param("kimi-k3", "medium") == "high"
    assert effort_param("accounts/fireworks/models/kimi-k3", "medium") == "high"
    assert effort_param("kimi-k3-fast", "medium") == "high"
    assert effort_param("kimi-k3", "low") == "low"
    assert effort_param("kimi-k3", "high") == "high"
    assert effort_param("kimi-k3", "xhigh") == "max"
    assert effort_param("kimi-k3", "max") == "max"


def test_omitted_effort_stays_off_the_wire() -> None:
    """'default' means send nothing, including on Kimi, where that is the model's own max."""
    assert effort_param("kimi-k3", "default") is None
    assert effort_param("deepseek-v4-flash", "default") is None
    assert effort_param("kimi-k3", "") is None


def test_other_models_keep_medium() -> None:
    assert effort_param("deepseek-v4-flash", "medium") == "medium"
    assert effort_param("glm-5.3", "xhigh") == "xhigh"


def test_kimi_k2_rejects_the_field() -> None:
    assert effort_param("kimi-k2.7-code", "medium") is None
    assert effort_param("kimi-k2.7-code", "high") is None


def test_caps_without_reasoning_leave_the_field_off() -> None:
    assert effort_param("gpt-x", "medium", {"reasoning": False}) is None
    assert effort_param("gpt-x", "medium", {"reasoning": True}) == "medium"
    assert effort_param("gpt-x", "medium", {}) == "medium"
    # The Kimi mapping is decided before the capability check.
    assert effort_param("kimi-k3", "medium", {"reasoning": False}) == "high"



# --- a provider that rejects the field: step down, drop, remember -------------------------------------------------

_REAL_CLIENT = httpx.AsyncClient
SETTINGS = {"baseUrl": "http://p.test/v1", "apiKey": "k", "llmRetries": 2}
SSE = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


def _reject(field: str, message: str | None = None) -> httpx.Response:
    return httpx.Response(400, json={"error": {"message": message or f"Unsupported parameter: '{field}'", "param": field}})


def _ok(_b: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(200, content=SSE)


def _stream(monkeypatch: Any, handler: Any, model: str = "m1", effort: str = "medium", fast: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []
    real = _REAL_CLIENT

    def h(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return handler(bodies[-1])

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(h)}))
    monkeypatch.setattr(llm, "caps_lookup", lambda _m: {})
    monkeypatch.setattr(llm, "supports_service_tier", lambda _s: True)

    async def go() -> list[dict[str, Any]]:
        return [ev async for ev in llm.stream_chat(SETTINGS, model, [{"role": "user", "content": "x"}], effort=effort, fast=fast)]

    return asyncio.run(go()), bodies


@pytest.fixture(autouse=True)
def _fresh_caps() -> Any:
    """Each case learns from a clean slate, and a failing one leaves nothing behind for the next."""
    llm._model_caps.clear()
    llm._caps_listeners.clear()
    yield
    llm._model_caps.clear()
    llm._caps_listeners.clear()


def test_a_rejected_effort_is_dropped_remembered_and_not_resent(monkeypatch: Any) -> None:
    heard: list[dict[str, Any]] = []
    llm.on_caps(heard.append)
    evs, bodies = _stream(monkeypatch, lambda b: _reject("reasoning_effort") if "reasoning_effort" in b else _ok())
    assert [("reasoning_effort" in b) for b in bodies] == [True, False]
    assert any(e.get("text") == "hi" for e in evs)
    assert evs[-1]["effort_dropped"] == "medium"
    assert llm.model_cap("http://p.test/v1/", "m1")["effort"] == "none"
    assert len(heard) == 1
    evs, bodies = _stream(monkeypatch, _ok)
    assert len(bodies) == 1 and "reasoning_effort" not in bodies[0]
    assert "effort_dropped" not in evs[-1]


def test_xhigh_steps_to_high_once_without_using_an_attempt(monkeypatch: Any) -> None:
    calls: list[str] = []

    def handler(b: dict[str, Any]) -> httpx.Response:
        calls.append(b.get("reasoning_effort", ""))
        return _reject("reasoning_effort", "reasoning_effort must be one of low, medium, high") if b.get("reasoning_effort") in ("max", "xhigh") else _ok()

    evs, _ = _stream(monkeypatch, handler, effort="max")
    assert calls == ["max", "high"]
    assert "effort_dropped" not in evs[-1]
    assert llm.model_cap("http://p.test/v1", "m1")["effort"] == "high"
    assert llm.effort_param("m1", "max", base_url="http://p.test/v1") == "high"
    assert llm.effort_param("m1", "medium", base_url="http://p.test/v1") == "medium"


def test_the_message_may_say_thinking(monkeypatch: Any) -> None:
    _, bodies = _stream(monkeypatch, lambda b: _reject("x", "This model does not support thinking.") if "reasoning_effort" in b else _ok())
    assert len(bodies) == 2


def test_a_400_that_does_not_name_the_field_is_not_retried(monkeypatch: Any) -> None:
    bodies: list[Any] = []

    def handler(b: dict[str, Any]) -> httpx.Response:
        bodies.append(b)
        return httpx.Response(400, json={"error": {"message": "bad messages"}})

    try:
        _stream(monkeypatch, handler)
        raise AssertionError("expected an LLMError")
    except llm.LLMError:
        pass
    assert len(bodies) == 1 and llm.model_cap("http://p.test/v1", "m1") is None


def test_service_tier_is_dropped_for_the_request_only(monkeypatch: Any) -> None:
    evs, bodies = _stream(monkeypatch, lambda b: _reject("service_tier") if "service_tier" in b else _ok(), effort="default", fast=True)
    assert ["service_tier" in b for b in bodies] == [True, False]
    assert "effort_dropped" not in evs[-1] and not llm._model_caps


def test_learned_caps_expire_and_reload() -> None:
    llm.load_caps({"http://p.test|m1": {"effort": "none", "at": time.time() - 31 * 86400}, "junk": 3})
    assert llm.model_cap("http://p.test", "m1") is None
    assert llm.effort_param("m1", "medium", base_url="http://p.test") == "medium"
    llm.load_caps({"http://p.test|m1": {"effort": "none", "at": time.time()}})
    assert llm.effort_param("m1", "medium", base_url="http://p.test/") is None


def test_complete_sends_effort_only_when_asked(monkeypatch: Any) -> None:
    bodies: list[dict[str, Any]] = []
    real = _REAL_CLIENT

    def h(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(h)}))
    monkeypatch.setattr(llm, "caps_lookup", lambda _m: {})
    msgs = [{"role": "user", "content": "x"}]
    asyncio.run(llm.complete(SETTINGS, "kimi-k3", msgs, effort="low"))
    asyncio.run(llm.complete(SETTINGS, "kimi-k2.7-code", msgs, effort="low"))
    asyncio.run(llm.complete(SETTINGS, "plain", msgs))
    assert bodies[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in bodies[1] and "reasoning_effort" not in bodies[2]


if __name__ == "__main__":
    # The provider cases take pytest's monkeypatch and run only under pytest.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v) and not inspect.signature(v).parameters]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
