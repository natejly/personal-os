"""Models are "<provider id>/<model>": splitting, routing by prefix, bare legacy ids, unkeyed providers, tiers.

Run: python -m pytest backend/tests/test_provider_refs.py
"""
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
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="provrefs-"))

import httpx  # noqa: E402

from personal_os import llm, provider_keys, provider_route, providers  # noqa: E402
from personal_os.app import db  # noqa: E402

AN, FW, LL = "https://api.anthropic.com/v1/", "https://api.fireworks.ai/inference/v1", "http://localhost:4000"
ENTRIES = [
    {"id": "anthropic", "kind": "anthropic", "name": "Anthropic", "baseUrl": AN},
    {"id": "litellm", "kind": "litellm", "name": "LiteLLM proxy", "baseUrl": LL},
    {"id": "fireworks", "kind": "fireworks", "name": "Fireworks AI", "baseUrl": FW},
]
CFG = {"providers": ENTRIES, "defaultModel": "anthropic/claude-sonnet-5-5"}


@pytest.fixture(autouse=True)
def keys():  # type: ignore[no-untyped-def]
    provider_route.set_key_source(lambda: {"anthropic": "an-key", "litellm": "ll-key"})  # fireworks has none
    provider_route._live.clear()
    provider_route._cache.clear()
    yield
    provider_route.set_key_source(lambda: provider_keys.load(db.secrets))


def test_split_ref_only_on_a_configured_head() -> None:
    ids = {"openrouter", "anthropic"}
    assert providers.split_ref("openrouter/anthropic/claude-sonnet-5-5", ids) == ("openrouter", "anthropic/claude-sonnet-5-5")
    assert providers.split_ref("fireworks/accounts/fireworks/models/x", {"fireworks"}) == ("fireworks", "accounts/fireworks/models/x")
    assert providers.split_ref("accounts/fireworks/models/x", ids) == (None, "accounts/fireworks/models/x")
    assert providers.split_ref("gpt-oss:20b", ids) == (None, "gpt-oss:20b")
    assert providers.split_ref("anthropic/", ids) == (None, "anthropic/")
    assert providers.make_ref("ollama", "llama3.2") == "ollama/llama3.2" and providers.make_ref("x", "") == ""
    assert providers.bare("anthropic/claude-x", CFG) == "claude-x" and providers.bare("anthropic/claude-x") == "anthropic/claude-x"


def test_a_ref_goes_to_its_provider_with_its_key() -> None:
    cfg, model = provider_route.resolve(CFG, "anthropic/claude-sonnet-5-5")
    assert (model, cfg["baseUrl"], cfg["apiKey"], cfg["provider"]) == ("claude-sonnet-5-5", AN, "an-key", "anthropic")
    assert cfg["providers"] is None and provider_route.resolve(cfg, model) == (cfg, model)  # resolving twice is a no-op


def test_same_bare_name_on_two_providers_follows_the_prefix() -> None:
    a, _ = provider_route.resolve(CFG, "anthropic/claude-sonnet-5-5")
    b, m = provider_route.resolve(CFG, "litellm/claude-sonnet-5-5")
    assert m == "claude-sonnet-5-5" and (a["baseUrl"], b["baseUrl"]) == (AN, LL) and b["apiKey"] == "ll-key"


def test_collision_reaches_the_right_server() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers.get("authorization", "") or req.headers.get("x-api-key", ""), json.loads(req.content)["model"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    real = httpx.AsyncClient.__init__
    patch = pytest.MonkeyPatch()
    patch.setattr(httpx.AsyncClient, "__init__", lambda self, *a, **kw: real(self, *a, **{**kw, "transport": httpx.MockTransport(handler)}))
    try:
        asyncio.run(llm.complete(CFG, "litellm/claude-sonnet-5-5", [{"role": "user", "content": "hi"}], "t"))
    finally:
        patch.undo()
    assert seen and seen[0][0].startswith(LL) and seen[0][1].endswith("ll-key") and seen[0][2] == "claude-sonnet-5-5"


def test_bare_legacy_id_goes_to_the_first_usable_owner() -> None:
    cfg, model = provider_route.resolve(CFG, "claude-opus-5-5")  # a preset model of anthropic
    assert (cfg["providerId"], model) == ("anthropic", "claude-opus-5-5")
    cfg, _ = provider_route.resolve(CFG, "zzz-unknown")  # known to none: the first usable provider
    assert cfg["providerId"] == "anthropic"
    cfg, _ = provider_route.resolve(CFG, "accounts/fireworks/models/deepseek-v4p1-flash")  # fireworks is unkeyed: skipped
    assert cfg["providerId"] == "anthropic"


def test_unkeyed_or_removed_provider_raises_with_the_fix() -> None:
    with pytest.raises(llm.LLMError, match="Add a key for Fireworks AI in Settings → Models"):
        provider_route.resolve(CFG, "fireworks/accounts/fireworks/models/x")
    provider_route.set_key_source(lambda: {})
    with pytest.raises(llm.LLMError, match="Add a key for Anthropic"):
        provider_route.resolve({"providers": ENTRIES[:1]}, "claude-x")  # a bare id with nothing usable


def test_no_provider_list_passes_through_untouched() -> None:
    legacy = {"baseUrl": "http://x", "apiKey": "k"}
    assert provider_route.resolve(legacy, "any/thing") == (legacy, "any/thing")
    assert provider_route.resolve({**legacy, "providers": []}, "m")[0] is not None
    assert provider_route.route(legacy, "m") is legacy


def test_usable_hides_unkeyed_cloud_providers_but_keeps_keyless_kinds() -> None:
    assert [e["id"] for e in provider_route.usable(CFG)] == ["anthropic", "litellm"]
    provider_route.set_key_source(lambda: {})
    assert [e["id"] for e in provider_route.usable(CFG)] == ["litellm"]  # a proxy needs no key


def test_tiers_follow_the_chat_models_provider() -> None:
    assert providers.default_model(CFG) == "anthropic/claude-sonnet-5-5"
    assert providers.tier_model(CFG, "high") == "anthropic/claude-opus-5-5"
    on_proxy = {**CFG, "defaultModel": "litellm/kimi-k3"}
    assert providers.tier_model(on_proxy, "low") == "litellm/deepseek-v4-flash"
    assert providers.tier_model({**CFG, "modelLow": "anthropic/x"}, "low") == "anthropic/x"  # a saved ref wins
    # a chat provider with no tiers (a plain custom server) keeps background work on the chat model, never another provider
    custom = {"providers": [{"id": "custom", "kind": "custom", "name": "Custom", "baseUrl": "http://h"}, *ENTRIES], "defaultModel": "custom/m"}
    provider_route.set_key_source(lambda: {"anthropic": "k"})
    assert [providers.tier_model(custom, t) for t in providers.TIERS] == ["custom/m"] * 3
    assert providers.tier_model({**custom, "modelLow": "anthropic/claude-haiku-5-5"}, "low") == "anthropic/claude-haiku-5-5"  # a saved ref wins
    ollama = {"providers": [providers.new_entry("ollama"), *ENTRIES], "defaultModel": "ollama/llama3.2"}
    assert providers.tier_model(ollama, "high") == "ollama/llama3.2"
    assert providers.default_model({"providers": ENTRIES}) == "anthropic/claude-sonnet-5-5"
    assert providers.rerank_model({"providers": ENTRIES}) == "litellm/qwen3-reranker-8b"  # the proxy needs no key
    assert providers.rerank_model({"providers": ENTRIES[:1]}) == ""
    provider_route.set_key_source(lambda: {"fireworks": "k"})
    assert providers.rerank_model({"providers": ENTRIES[2:]}) == "fireworks/accounts/fireworks/models/qwen3-reranker-8b"


def test_legacy_tier_and_default_untouched_without_a_provider_list() -> None:
    legacy = {"provider": "anthropic", "baseUrl": AN}
    assert providers.default_model(legacy) == "claude-sonnet-5-5" and providers.tier_model(legacy, "high") == "claude-opus-5-5"


def test_all_models_lists_every_usable_provider_prefixed() -> None:
    async def list_fn(s: dict) -> list[dict]:
        return [{"id": "claude-sonnet-5-5"}, {"id": "claude-zeta", "mode": "chat"}] if s["providerId"] == "anthropic" else [{"id": "kimi-k3"}]

    rows = asyncio.run(provider_route.all_models(CFG, list_fn))
    assert [r["id"] for r in rows] == ["anthropic/claude-sonnet-5-5", "anthropic/claude-zeta", "litellm/kimi-k3"]
    assert rows[0]["model"] == "claude-sonnet-5-5" and rows[0]["provider"] == "anthropic" and rows[0].get("preset") is True
    assert "preset" not in rows[1] and rows[1]["mode"] == "chat"


def test_all_models_failure_falls_back_to_presets_and_typed_models() -> None:
    cfg = {"providers": [{"id": "custom", "kind": "custom", "name": "Custom", "baseUrl": "http://h", "models": ["mine"]}]}

    async def boom(_s: dict) -> list[dict]:
        raise RuntimeError("down")

    assert [r["id"] for r in asyncio.run(provider_route.all_models(cfg, boom))] == ["custom/mine"]

    async def ok(_s: dict) -> list[dict]:
        return [{"id": "other"}]

    assert [r["id"] for r in asyncio.run(provider_route.all_models(cfg, ok))] == ["custom/other", "custom/mine"]


def test_canon_makes_refs_from_bare_ids() -> None:
    ents = [{"id": "fireworks", "kind": "fireworks"}]
    c = lambda m, a="fireworks": provider_route.canon(m, ents, a)  # noqa: E731
    assert c("accounts/fireworks/models/deepseek-v4p1-flash") == "fireworks/accounts/fireworks/models/deepseek-v4p1-flash"
    assert c("claude-opus-5-5") == "anthropic/claude-opus-5-5"  # the preset that owns it, though it is not configured yet
    assert c("something-else") == "fireworks/something-else" and c("") == "" and c("auto") == "auto"
    assert c("fireworks/x") == "fireworks/x"
    assert provider_route.canon("anything", [{"id": "custom", "kind": "custom"}], "custom") == "custom/anything"  # your own server owns all


def test_a_ref_to_a_removed_provider_raises_instead_of_rerouting() -> None:
    for ref in ("openai/gpt-6.1-sol", "custom-2/m", "ollama/llama3.2"):  # none of them is configured
        with pytest.raises(llm.LLMError, match="was on a provider that's been removed. Pick another model in Settings → Models."):
            provider_route.resolve(CFG, ref)
    with pytest.raises(llm.LLMError, match="was on a provider"):
        provider_route.resolve({"providers": [ENTRIES[1]]}, "anthropic/claude-x")  # hand-saved, anthropic not configured
    # openrouter's own slashes stay inside its model name
    cfg = {"providers": [providers.new_entry("openrouter")]}
    provider_route.set_key_source(lambda: {"openrouter": "k"})
    got, model = provider_route.resolve(cfg, "openrouter/anthropic/claude-x")
    assert (got["providerId"], model) == ("openrouter", "anthropic/claude-x")
    # a bare id with no such head keeps the legacy fallback
    assert provider_route.resolve(cfg, "mystery")[0]["providerId"] == "openrouter"
