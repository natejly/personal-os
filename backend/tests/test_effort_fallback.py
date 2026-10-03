"""Capabilities from /model/info: Pricing.caps, list_models modes, effort_param on a non-reasoning model.

Runs under pytest.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
