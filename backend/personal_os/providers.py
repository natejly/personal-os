"""Provider presets: every one is an OpenAI-compatible endpoint, so llm.py talks to all of them the same way.

The LiteLLM proxy is just one preset ("litellm"); nothing here needs it. Model ids are suggestions and stay
editable in the UI, because catalogs move faster than releases.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

PROVIDERS: list[dict[str, Any]] = [
    {"id": "fireworks", "name": "Fireworks AI", "baseUrl": "https://api.fireworks.ai/inference/v1", "needsKey": True,
     "keyUrl": "https://fireworks.ai/account/api-keys", "defaultModel": "accounts/fireworks/models/kimi-k3",
     "models": ["accounts/fireworks/models/kimi-k3", "accounts/fireworks/models/deepseek-v4-pro",
                "accounts/fireworks/models/deepseek-v4p1-flash", "accounts/fireworks/models/qwen3p8-max",
                "accounts/fireworks/models/glm-5p3", "accounts/fireworks/models/gpt-oss-120b"],
     "note": None},
    {"id": "openai", "name": "OpenAI", "baseUrl": "https://api.openai.com/v1", "needsKey": True,
     "keyUrl": "https://platform.openai.com/api-keys", "defaultModel": "gpt-5-mini",
     "models": ["gpt-5-mini", "gpt-5", "gpt-5-nano", "gpt-4.1"], "note": None},
    {"id": "anthropic", "name": "Anthropic", "baseUrl": "https://api.anthropic.com/v1/", "needsKey": True,
     "keyUrl": "https://console.anthropic.com/settings/keys", "defaultModel": "claude-sonnet-5-5",
     "models": ["claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"],
     "note": "Uses Anthropic's OpenAI-compatible endpoint."},
    {"id": "openrouter", "name": "OpenRouter", "baseUrl": "https://openrouter.ai/api/v1", "needsKey": True,
     "keyUrl": "https://openrouter.ai/keys", "defaultModel": "anthropic/claude-sonnet-5-5",
     "models": ["anthropic/claude-sonnet-5-5", "openai/gpt-5-mini", "google/gemini-2.5-flash"],
     "note": "One key for many providers."},
    {"id": "ollama", "name": "Ollama (local)", "baseUrl": "http://localhost:11434/v1", "needsKey": False,
     "keyUrl": None, "defaultModel": "llama3.2", "models": ["llama3.2", "qwen3", "gpt-oss:20b"],
     "note": "Runs on this Mac; pull the model first (ollama pull llama3.2)."},
    {"id": "litellm", "name": "LiteLLM proxy", "baseUrl": "http://localhost:4000", "needsKey": False,
     "keyUrl": None, "defaultModel": "kimi-k3", "models": ["kimi-k3", "deepseek-v4-flash"],
     "note": "Your own proxy; model names are whatever its config defines."},
    {"id": "custom", "name": "Custom (OpenAI-compatible)", "baseUrl": "", "needsKey": False,
     "keyUrl": None, "defaultModel": "", "models": [], "note": "Any server that speaks the OpenAI chat API."},
]
BY_ID = {p["id"]: p for p in PROVIDERS}


def get(provider_id: str | None) -> dict[str, Any] | None:
    return BY_ID.get(provider_id or "")


def _hostport(url: str) -> str:
    u = urlparse(url if "//" in url else f"//{url}")
    host = (u.hostname or "").lower()
    return f"{host}:{u.port}" if u.port else host


def infer(base_url: str | None) -> str | None:
    """The provider an already-stored baseUrl belongs to, or None when there is no URL at all.

    localhost:4000 is the LiteLLM proxy scripts/dev.sh starts, so installs from before provider
    existed keep working unchanged. An unrecognised URL is "custom".
    """
    base = (base_url or "").strip()
    if not base:
        return None
    hp = _hostport(base)
    if hp in ("localhost:4000", "127.0.0.1:4000"):
        return "litellm"
    for p in PROVIDERS:
        if p["baseUrl"] and p["id"] not in ("litellm", "custom") and _hostport(p["baseUrl"]) == hp:
            return p["id"]
    return "custom"


def effective(settings: dict[str, Any]) -> str | None:
    """The stored provider, unless the baseUrl was since edited (Settings saves it on its own) to another
    vendor's host; then the URL wins, so provider-specific behaviour never follows a stale label."""
    stored, inferred = settings.get("provider"), infer(settings.get("baseUrl"))
    if not stored or not inferred:
        return stored or inferred
    if stored in ("litellm", "custom") or stored == inferred:
        return stored
    return inferred


def endpoint(base_url: str, path: str) -> str:
    """base + path, adding /v1 only when the base does not already end in a version segment."""
    base = base_url.strip().rstrip("/")
    return f"{base}{path}" if re.search(r"/v\d+$", base) else f"{base}/v1{path}"
