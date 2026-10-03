"""Capabilities from /model/info: Pricing.caps, list_models modes, effort_param on a non-reasoning model.

Runs under pytest.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="caps-data-"))

import httpx  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.usage import Pricing  # noqa: E402

SETTINGS = {"baseUrl": "http://proxy.test/v1", "apiKey": "k"}
INFO = {"data": [
    {"model_name": "embedder", "model_info": {"mode": "embedding"}},
    {"model_name": "plain", "model_info": {"mode": "chat", "supports_reasoning": False, "input_cost_per_token": 1e-6}},
    {"model_name": "thinker", "model_info": {"mode": "chat", "supports_reasoning": True, "max_input_tokens": 1000}},
]}
LISTING = {"data": [{"id": i} for i in ("thinker", "plain", "embedder", "text-embedding-3", "whisper-1", "mystery")]}


def _patch_http(monkeypatch: Any) -> None:
    real = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/model/info"):
            return httpx.Response(200, json=INFO)
        return httpx.Response(200, json=LISTING)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def _refreshed() -> Pricing:
    p = Pricing()
    asyncio.run(p.refresh({"baseUrl": "http://proxy.test", "apiKey": "k"}))
    return p


def test_refresh_fills_caps_independent_of_prices(monkeypatch: Any) -> None:
    _patch_http(monkeypatch)
    p = _refreshed()
    assert p.caps("embedder") == {"mode": "embedding"}
    assert p.caps("plain")["reasoning"] is False
    assert p.caps("thinker") == {"mode": "chat", "reasoning": True, "max_input_tokens": 1000}
    assert p.caps("nope") == {}


def test_effort_param_honours_caps() -> None:
    assert llm.effort_param("plain", "medium", {"reasoning": False}) is None
    assert llm.effort_param("thinker", "medium", {"reasoning": True}) == "medium"


def test_list_models_merges_caps_and_id_patterns(monkeypatch: Any) -> None:
    _patch_http(monkeypatch)
    p = _refreshed()
    monkeypatch.setattr(llm, "caps_lookup", p.caps)
    rows = {r["id"]: r for r in asyncio.run(llm.list_models(SETTINGS))}
    assert rows["embedder"]["mode"] == "embedding"
    assert rows["plain"]["reasoning"] is False and rows["plain"]["mode"] == "chat"
    assert rows["thinker"]["reasoning"] is True
    assert rows["text-embedding-3"]["mode"] == "embedding"
    assert rows["whisper-1"]["mode"] != "chat"
    assert "mode" not in rows["mystery"] and "reasoning" not in rows["mystery"]


def test_list_models_without_caps(monkeypatch: Any) -> None:
    _patch_http(monkeypatch)
    monkeypatch.setattr(llm, "caps_lookup", lambda _m: {})
    rows = {r["id"]: r for r in asyncio.run(llm.list_models(SETTINGS))}
    assert rows["plain"] == {"id": "plain"}
    assert rows["text-embedding-3"]["mode"] == "embedding"


SSE = b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


def test_stream_chat_leaves_the_field_off_for_a_known_non_reasoning_model(monkeypatch: Any) -> None:
    bodies: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    monkeypatch.setattr(llm, "caps_lookup", lambda m: {"reasoning": False} if m == "plain" else {})

    async def go(model: str) -> None:
        async for _ in llm.stream_chat(SETTINGS, model, [{"role": "user", "content": "x"}], effort="medium"):
            pass

    asyncio.run(go("plain"))
    asyncio.run(go("mystery"))
    assert "reasoning_effort" not in bodies[0], bodies[0]
    assert bodies[1]["reasoning_effort"] == "medium"


def test_models_route_refreshes_caps_first(monkeypatch: Any) -> None:
    from fastapi.testclient import TestClient

    from personal_os import app as app_module

    _patch_http(monkeypatch)
    monkeypatch.setattr(app_module, "settings", lambda: {**llm.DEFAULT_SETTINGS, **SETTINGS})
    client = TestClient(app_module.app, headers={"X-Personal-OS-Token": app_module.AUTH_TOKEN})
    rows = {r["id"]: r for r in client.get("/models").json()}
    assert rows["plain"]["reasoning"] is False and rows["embedder"]["mode"] == "embedding"
    assert rows["whisper-1"]["mode"] == "audio" and rows["mystery"] == {"id": "mystery"}
    # The hook app.py wired is the same object the route just filled.
    assert llm.caps_lookup("thinker") == {"mode": "chat", "reasoning": True, "max_input_tokens": 1000}
