"""Per-model provider routing by "<provider>/<model>" ref, the multi-provider /models listing, the embedding lock."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="provroute-"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import embed, llm, provider_keys, provider_route, providers  # noqa: E402
from personal_os.app import AUTH_TOKEN, _lock_embedding, _pin_embedding_model, app, db  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
MSG_SSE = ('data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\n\n'
           'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
           'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
           'data: {"type":"message_stop"}\n\n')  # the Messages API's own events, as the Anthropic provider streams them
MSG_JSON = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}

FW, AN, OAI = (providers.get(p)["baseUrl"] for p in ("fireworks", "anthropic", "openai"))
FW_KEY, AN_KEY = "fw-key-1", "an-key-1"
ENTRIES = [providers.new_entry(k) for k in ("fireworks", "anthropic", "openai")]
FW_CFG = {"providers": ENTRIES}
AN_CFG = {"providers": [ENTRIES[1], ENTRIES[0], ENTRIES[2]]}
EMBED_FW = "qwen3-embedding-8b"


@pytest.fixture(autouse=True)
def keys():  # type: ignore[no-untyped-def]
    provider_route.set_key_source(lambda: {"fireworks": FW_KEY, "anthropic": AN_KEY})
    provider_route._live.clear()
    provider_route._cache.clear()
    yield
    provider_route.set_key_source(lambda: provider_keys.load(db.secrets))


@pytest.fixture
def seen(monkeypatch):  # type: ignore[no-untyped-def]
    """Every outgoing request is recorded and answered by a canned body, whatever client built it."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {"prompt_tokens": 1}})
        if req.url.path.endswith("/models"):
            if "anthropic" in str(req.url):
                return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-zeta"}]})
            if "openai" in str(req.url):
                return httpx.Response(500, text="down")
            return httpx.Response(200, json={"data": [{"id": "accounts/fireworks/models/ember-1"}, {"id": "accounts/fireworks/models/other"}]})
        body = json.loads(req.content or b"{}")
        if "anthropic" in str(req.url):
            if body.get("stream"):
                return httpx.Response(200, content=MSG_SSE.encode(), headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json=MSG_JSON)
        if body.get("stream"):
            sse = ('data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    real = httpx.AsyncClient.__init__

    def init(self, *a, **kw):  # type: ignore[no-untyped-def]
        kw["transport"] = httpx.MockTransport(handler)
        real(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    return calls


# ---- resolve() ----
def test_ref_goes_to_its_provider() -> None:
    got, model = provider_route.resolve(FW_CFG, "fireworks/accounts/fireworks/models/ember-1")
    assert (got["provider"], got["baseUrl"], got["apiKey"], model) == ("fireworks", FW, FW_KEY, "accounts/fireworks/models/ember-1")


def test_bare_claude_goes_to_anthropic_with_its_key() -> None:
    got, model = provider_route.resolve(FW_CFG, "claude-opus-5-5")
    assert (got["provider"], got["baseUrl"], got["apiKey"], model) == ("anthropic", AN, AN_KEY, "claude-opus-5-5")


def test_unkeyed_provider_is_an_error_not_a_silent_fallback() -> None:
    provider_route.set_key_source(lambda: {"fireworks": FW_KEY})
    with pytest.raises(llm.LLMError, match="Add a key for Anthropic"):
        provider_route.resolve(FW_CFG, "anthropic/claude-opus-5-5")


def test_collision_follows_the_prefix_not_the_listing() -> None:
    provider_route._live["fireworks"] = {"claude-sonnet-5-5"}
    assert provider_route.resolve(FW_CFG, "anthropic/claude-sonnet-5-5")[0]["provider"] == "anthropic"
    assert provider_route.resolve(FW_CFG, "claude-sonnet-5-5")[0]["provider"] == "fireworks"  # a bare id: first usable owner


def test_embedding_default_resolves_to_fireworks_both_spellings() -> None:
    for m in (EMBED_FW, "accounts/fireworks/models/qwen3-embedding-8b"):
        got = provider_route.route(AN_CFG, m)
        assert (got["provider"], got["apiKey"]) == ("fireworks", FW_KEY), m


def test_bare_unknown_name_goes_to_the_first_usable() -> None:
    cfg = {"providers": [providers.new_entry("litellm"), ENTRIES[1]]}
    assert provider_route.resolve(cfg, "claude-opus-5-5")[0]["providerId"] == "anthropic"  # an owner of the name wins
    assert provider_route.resolve(cfg, "mystery-model")[0]["providerId"] == "litellm"  # nobody owns it: the first usable


# ---- calls ----
def test_embed_goes_to_fireworks_while_anthropic_active(seen) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(embed.embed_texts(AN_CFG, ["x"], EMBED_FW))
    assert str(seen[0].url).startswith(FW) and seen[0].headers["authorization"] == f"Bearer {FW_KEY}"


def test_complete_routed_to_anthropic_sends_key_url_and_max_tokens(seen) -> None:  # type: ignore[no-untyped-def]
    assert asyncio.run(llm.complete(FW_CFG, "anthropic/claude-opus-5-5", [{"role": "user", "content": "hi"}])) == "ok"
    req = seen[0]
    assert str(req.url).startswith("https://api.anthropic.com/v1/") and req.headers["x-api-key"] == AN_KEY
    assert json.loads(req.content)["max_tokens"] == llm.ANTHROPIC_FALLBACK_CAP


def test_high_tier_model_from_another_provider(seen) -> None:  # type: ignore[no-untyped-def]
    cfg = {**FW_CFG, "modelHigh": "anthropic/claude-opus-5-5"}
    asyncio.run(llm.complete(cfg, providers.tier_model(cfg, "high"), [{"role": "user", "content": "hi"}]))
    assert "api.anthropic.com" in str(seen[0].url) and "max_tokens" in json.loads(seen[0].content)


def test_stream_routed_to_anthropic(seen) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async for _ in llm.stream_chat(FW_CFG, "anthropic/claude-sonnet-5-5", [{"role": "user", "content": "hi"}]):
            pass
    asyncio.run(run())
    assert "api.anthropic.com" in str(seen[0].url) and json.loads(seen[0].content)["max_tokens"] > 0


def test_unrouted_fireworks_call_has_no_max_tokens(seen) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(llm.complete(FW_CFG, "fireworks/accounts/fireworks/models/ember-1", [{"role": "user", "content": "hi"}]))
    assert str(seen[0].url).startswith(FW) and "max_tokens" not in json.loads(seen[0].content)


# ---- /models ----
def test_models_merge_tag_and_survive_a_failing_provider(seen) -> None:  # type: ignore[no-untyped-def]
    provider_route.set_key_source(lambda: {"fireworks": FW_KEY, "anthropic": AN_KEY, "openai": "oai"})
    rows = asyncio.run(provider_route.all_models(FW_CFG, llm.list_models))
    by = {r["id"]: r for r in rows}
    ember = by["fireworks/accounts/fireworks/models/ember-1"]
    assert ember["provider"] == "fireworks" and ember["preset"] and ember["model"] == "accounts/fireworks/models/ember-1"
    assert "preset" not in by["fireworks/accounts/fireworks/models/other"]
    assert by["anthropic/claude-sonnet-5-5"]["provider"] == "anthropic" and "preset" not in by["anthropic/claude-zeta"]
    assert by["openai/gpt-6.1-sol"]["provider"] == "openai" and by["openai/gpt-6.1-sol"]["preset"]  # openai listing 500: preset rows
    assert by["openai/text-embedding-3-small"]["mode"] == "embedding"
    assert len(rows) == len(by)


@pytest.fixture
def clean():  # type: ignore[no-untyped-def]
    def reset() -> None:
        db.set_settings({"embeddingModel": ""})
        with db.tx() as c:
            c.execute("DELETE FROM memory_vectors")
    reset()
    yield
    reset()


def test_picker_models_setting_round_trips(clean) -> None:  # type: ignore[no-untyped-def]
    assert client.put("/settings", json={"pickerModels": ["a", "b"]}).json()["pickerModels"] == ["a", "b"]
    assert client.put("/settings", json={"pickerModels": "a"}).status_code == 422
    client.put("/settings", json={"pickerModels": []})


# ---- embedding lock ----
def test_embedding_lock(clean) -> None:  # type: ignore[no-untyped-def]
    assert client.put("/settings", json={"embeddingModel": "m1"}).status_code == 200  # from blank
    assert client.put("/settings", json={"embeddingModel": "m2"}).status_code == 200  # no vectors yet: still changeable
    assert client.put("/settings", json={"embeddingModel": "m1"}).status_code == 200
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO memories(id,content,kind,source,pinned,created_at,updated_at) VALUES('lk','x','fact','auto',0,1,1)")
        c.execute("INSERT OR REPLACE INTO memory_vectors(memory_id,model,dim,vec,content_hash,updated_at) VALUES('lk','m1',1,x'00','h',1)")
    r = client.put("/settings", json={"embeddingModel": "m2"})
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    assert db.get_settings()["embeddingModel"] == "m1"


def test_startup_pin_from_existing_vectors(clean) -> None:  # type: ignore[no-untyped-def]
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO memories(id, content, created_at, updated_at) VALUES (?, 'x', 0, 0)", [("pin-a",), ("pin-b",)])
        c.execute("DELETE FROM memory_vectors")
        c.executemany("INSERT INTO memory_vectors VALUES (?, 'old-model', 2, x'00', 'h', 0)", [("pin-a",), ("pin-b",)])
    try:
        _pin_embedding_model()
        assert db.get_settings()["embeddingModel"] == "old-model"
    finally:
        with db.tx() as c:
            c.execute("DELETE FROM memories WHERE id IN ('pin-a','pin-b')")


# ---- function tools vs reasoning_effort on chat completions ----
TOOL = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
OAI_CFG = {"provider": "openai", "baseUrl": OAI, "apiKey": "oai"}


def test_gpt6_with_tools_is_sent_effort_none(seen) -> None:  # type: ignore[no-untyped-def]
    async def run() -> None:
        async for _ in llm.stream_chat(OAI_CFG, "gpt-6.1-sol", [{"role": "user", "content": "hi"}], TOOL, effort="high"):
            pass
    asyncio.run(run())
    assert json.loads(seen[0].content)["reasoning_effort"] == "none"


def test_effort_tools_400_is_retried_with_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bodies: list[dict] = []
    msg = "Function tools with reasoning_effort are not supported for x in /v1/chat/completions. set reasoning_effort to 'none'."

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        if bodies[-1].get("reasoning_effort") != "none":
            return httpx.Response(400, json={"error": {"message": msg}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    real = httpx.AsyncClient.__init__
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda self, *a, **kw: real(self, *a, **{**kw, "transport": httpx.MockTransport(handler)}))
    cfg = {"provider": "custom", "baseUrl": "http://x.test/v1", "apiKey": "k"}
    body = {"model": "m", "messages": [], "tools": TOOL}

    async def go() -> None:
        async with httpx.AsyncClient() as c:
            await llm._send_with_retry(c, cfg, body, stream=False)
    asyncio.run(go())
    assert [b.get("reasoning_effort") for b in bodies] == [None, "none"]
