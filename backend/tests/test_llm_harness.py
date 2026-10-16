"""Model-call harness: header-wait limit, no re-send of a timed-out helper call, usage on a failed stream, the prompt
estimate, remembered optional-field rejections, the shared client, the auth breaker behind a proxy, and request capture.

Run: backend/.venv/bin/python -m pytest backend/tests/test_llm_harness.py -q
The provider is a httpx.MockTransport: nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="llmharness-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from personal_os import auth_breaker as ab, llm as app_llm, request_log  # noqa: E402

# Other test modules replace llm.stream_chat and never put it back: a private copy of the source is the real thing.
_spec = importlib.util.spec_from_file_location("personal_os._llm_harness_under_test", app_llm.__file__)
llm = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(llm)  # type: ignore[union-attr]

S: dict[str, Any] = {"baseUrl": "http://provider.test/v1", "apiKey": "k", "provider": "litellm", "llmRetries": 2}
FW: dict[str, Any] = {**S, "baseUrl": "https://api.fireworks.ai/inference/v1", "provider": "fireworks"}  # a hosted provider
SSE = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


@pytest.fixture(autouse=True)
def provider(monkeypatch: Any) -> Callable[[Callable[[httpx.Request], Any]], list[httpx.Request]]:
    ab._state.clear()
    llm._rejected_fields.clear()
    made: list[int] = []
    real = httpx.AsyncClient  # once: a second install must not wrap the first one's mock

    async def no_sleep(_d: float, _c: asyncio.Event | None = None) -> bool:
        return True

    monkeypatch.setattr(llm, "_backoff", no_sleep)
    monkeypatch.setattr(llm, "caps_lookup", lambda _m: {})

    def install(handler: Callable[[httpx.Request], Any]) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        async def counted(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            out = handler(req)
            return await out if asyncio.iscoroutine(out) else out

        def client(*a: Any, **k: Any) -> httpx.AsyncClient:
            made.append(1)
            return real(*a, **{**k, "transport": httpx.MockTransport(counted)})

        monkeypatch.setattr(llm.httpx, "AsyncClient", client)
        install.made = made  # type: ignore[attr-defined]
        return seen

    yield install
    ab._state.clear()


async def _collect(settings: dict[str, Any] = S, **kw: Any) -> list[dict[str, Any]]:
    return [ev async for ev in llm.stream_chat(settings, "m", [{"role": "user", "content": "hi"}], **kw)]


def test_silent_header_wait_is_retried_without_affinity(provider: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(llm, "FIRST_BYTE_S", 0.2)
    calls = {"n": 0}

    async def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(30)  # accepts the request, never sends headers
        return httpx.Response(200, content=SSE)

    seen = provider(handler)

    async def go() -> list[dict[str, Any]]:
        llm.session_affinity.set("chat-1")
        return await _collect(FW)

    events = asyncio.run(asyncio.wait_for(go(), 10))
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "hi"
    assert len(seen) == 2
    assert json.loads(seen[0].content)["user"] == "chat-1" and seen[0].headers.get("x-session-affinity") == "chat-1"
    assert "user" not in json.loads(seen[1].content) and "x-session-affinity" not in seen[1].headers  # may land elsewhere


def test_local_server_and_proxy_keep_the_idle_limit_for_headers() -> None:
    assert llm._first_byte_s({"provider": "ollama", "baseUrl": "http://localhost:11434/v1"}) is None
    assert llm._first_byte_s(S) is None  # a proxy may hold its headers until the upstream's first chunk
    assert llm._first_byte_s(FW) == llm.FIRST_BYTE_S


def test_complete_read_timeout_is_not_resent(provider: Any) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    seen = provider(handler)
    with pytest.raises(llm.LLMError) as e:
        asyncio.run(llm.complete(S, "m", [{"role": "user", "content": "x"}]))
    assert len(seen) == 1 and e.value.kind == "timeout"  # the provider is still generating, and billing, the first one


def test_complete_error_in_200_is_an_llm_error(provider: Any) -> None:
    provider(lambda _r: httpx.Response(200, json={"error": {"message": "prompt is too long", "code": 400}}))
    with pytest.raises(llm.LLMError) as e:
        asyncio.run(llm.complete(S, "m", [{"role": "user", "content": "x"}]))
    assert e.value.kind == "overflow"
    provider(lambda _r: httpx.Response(200, json={"choices": []}))
    with pytest.raises(llm.LLMError, match="empty reply"):
        asyncio.run(llm.complete(S, "m", [{"role": "user", "content": "x"}]))
    provider(lambda _r: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(llm.LLMError, match="not JSON"):
        asyncio.run(llm.complete(S, "m", [{"role": "user", "content": "x"}]))


def test_cut_off_stream_still_records_usage(provider: Any) -> None:
    class Cut(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield b'data: {"choices":[{"delta":{"reasoning_content":"thinking hard"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    provider(lambda _r: httpx.Response(200, stream=Cut()))
    rows: list[dict[str, Any]] = []
    listener = llm.bus.subscribe("model.response", rows.append, owner="test", order=0)
    try:
        with pytest.raises(llm.LLMError, match="cut off"):
            asyncio.run(_collect())
    finally:
        listener.dispose()
    assert len(rows) == 1 and rows[0]["completion_tokens"] == len("thinking hard") // 4


def test_silence_before_the_first_token_is_retried(provider: Any) -> None:
    class Stall(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            await asyncio.sleep(30)
            yield b"never"

    answers = [httpx.Response(200, stream=Stall()), httpx.Response(200, content=SSE)]
    seen = provider(lambda _r: answers.pop(0))
    events = asyncio.run(asyncio.wait_for(_collect({**S, "llmIdleSeconds": 0.2}), 10))
    assert len(seen) == 2 and any(e["type"] == "retry" and e.get("attempt") == 1 for e in events)


def test_prompt_estimate_counts_tools_and_images_by_count() -> None:
    pic = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 1_000_000}}
    msgs = [{"role": "user", "content": [{"type": "text", "text": "what is this"}, pic]}]
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}}]
    n = llm._prompt_chars(msgs, tools)
    assert n < 10_000  # not the megabyte of base64
    assert n >= llm.IMAGE_PROMPT_CHARS + len(json.dumps(tools))


def test_rejected_service_tier_is_remembered(provider: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(llm, "supports_service_tier", lambda _s: True)
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if "service_tier" in bodies[-1]:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'service_tier'", "param": "service_tier"}})
        return httpx.Response(200, content=SSE)

    provider(handler)
    asyncio.run(_collect(fast=True))
    asyncio.run(_collect(fast=True))
    assert ["service_tier" in b for b in bodies] == [True, False, False]  # the second round pays no failed request


def test_one_client_serves_every_call_on_a_loop(provider: Any) -> None:
    provider(lambda _r: httpx.Response(200, content=SSE))

    async def go() -> None:
        for _ in range(3):
            await _collect()

    asyncio.run(go())
    assert len(provider.made) == 1  # type: ignore[attr-defined]


def test_no_tools_model_says_so() -> None:
    msg = llm.describe_http_error(400, "Bad Request", '{"error": {"message": "llama2 does not support tools"}}', kind="unsupported_param")
    assert "cannot call tools" in msg and "effort" not in msg


PROXY = {"baseUrl": "http://localhost:4000", "apiKey": "", "provider": "litellm"}
UPSTREAM = json.dumps({"error": {"message": "litellm.AuthenticationError: AuthenticationError: Fireworks_aiException - "
                                            "invalid api key", "type": "auth_error", "code": "401"}})


def test_upstream_rejection_behind_a_proxy_does_not_hold_the_key(provider: Any) -> None:
    ab.failure(PROXY, 401, UPSTREAM)
    ab.failure(PROXY, 401, UPSTREAM)
    assert ab.check(PROXY) is None
    assert ab.upstream(PROXY, UPSTREAM) == "fireworks_ai"
    assert ab.upstream({**PROXY, "provider": "openai", "baseUrl": "https://api.openai.com/v1"}, UPSTREAM) is None
    provider(lambda _r: httpx.Response(401, text=UPSTREAM))
    with pytest.raises(llm.LLMError, match="upstream key for fireworks_ai"):
        asyncio.run(_collect({**PROXY, "llmRetries": 0}))


def test_an_inconclusive_probe_lets_the_next_request_probe(monkeypatch: Any) -> None:
    now = [1000.0]
    monkeypatch.setattr(ab, "clock", lambda: now[0])
    bad = '{"error": {"message": "Invalid API key"}}'
    ab.failure(S, 401, bad)
    ab.failure(S, 401, bad)
    now[0] += ab.COOLDOWN_S + 1
    assert ab.check(S) is None  # the probe
    assert ab.check(S)  # others wait for it
    ab.failure(S, 503, "down")  # says nothing about the key
    assert ab.check(S) is None  # the next caller probes at once
    ab.inconclusive(S)  # a network error on that probe: same
    assert ab.check(S) is None
    ab.failure(S, 401, bad)  # a real rejection still restarts the cooldown
    assert ab.check(S)


def test_request_capture_drops_image_bytes() -> None:
    d = Path(tempfile.mkdtemp(prefix="reqimg-"))
    url = "data:image/png;base64," + "B" * 50_000
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": url}}]}]
    request_log.record(d, "m1", 1, "x", msgs, None, "auto")
    request_log.record(d, "m1", 2, "x", msgs, None, "auto")
    rows = request_log.read(d, "m1") or []
    assert [r["round"] for r in rows] == [1, 2]
    assert rows[0]["messages"][0]["content"][1]["image_url"]["url"] == f"<image {len(url)} bytes>"
    assert msgs[0]["content"][1]["image_url"]["url"] == url  # the live request is untouched
