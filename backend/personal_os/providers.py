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
     "keyUrl": "https://fireworks.ai/account/api-keys", "defaultModel": "accounts/fireworks/models/deepseek-v4p1-flash",
     "models": ["accounts/fireworks/models/deepseek-v4p1-flash", "accounts/fireworks/models/ember-1", "accounts/fireworks/models/glm-5p3", "accounts/fireworks/models/glm-5p3-flash", "accounts/fireworks/models/kimi-k3",
                "accounts/fireworks/models/deepseek-v4-pro",
                "accounts/fireworks/models/qwen3p8-max", "accounts/fireworks/models/gpt-oss-120b"],
     "note": None,
     "rerankModel": "accounts/fireworks/models/qwen3-reranker-8b"},
    {"id": "openai", "name": "OpenAI", "baseUrl": "https://api.openai.com/v1", "needsKey": True,
     "keyUrl": "https://platform.openai.com/api-keys", "defaultModel": "gpt-5-mini",
     "models": ["gpt-5-mini", "gpt-5", "gpt-5-nano", "gpt-4.1"], "note": None,
     "rerankModel": ""},
    {"id": "anthropic", "name": "Anthropic", "baseUrl": "https://api.anthropic.com/v1/", "needsKey": True,
     "keyUrl": "https://console.anthropic.com/settings/keys", "defaultModel": "claude-sonnet-5-5",
     "models": ["claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"],
     "note": "Uses Anthropic's OpenAI-compatible endpoint.",
     "rerankModel": ""},
    {"id": "openrouter", "name": "OpenRouter", "baseUrl": "https://openrouter.ai/api/v1", "needsKey": True,
     "keyUrl": "https://openrouter.ai/keys", "defaultModel": "anthropic/claude-sonnet-5-5",
     "models": ["anthropic/claude-sonnet-5-5", "openai/gpt-5-mini", "google/gemini-2.5-flash"],
     "note": "One key for many providers.",
     "rerankModel": ""},
    {"id": "ollama", "name": "Ollama (local)", "baseUrl": "http://localhost:11434/v1", "needsKey": False,
     "keyUrl": None, "defaultModel": "llama3.2", "models": ["llama3.2", "qwen3", "gpt-oss:20b"],
     "note": "Runs on this Mac; pull the model first (ollama pull llama3.2).",
     "rerankModel": ""},
    {"id": "litellm", "name": "LiteLLM proxy", "baseUrl": "http://localhost:4000", "needsKey": False,
     "keyUrl": None, "defaultModel": "deepseek-v4-flash", "models": ["deepseek-v4-flash", "ember-1", "glm-5.3", "glm-5.3-flash", "kimi-k3"],
     "note": "Your own proxy; model names are whatever its config defines.",
     "rerankModel": "qwen3-reranker-8b"},
    {"id": "custom", "name": "Custom (OpenAI-compatible)", "baseUrl": "", "needsKey": False,
     "keyUrl": None, "defaultModel": "", "models": [], "note": "Any server that speaks the OpenAI chat API.",
     "rerankModel": ""},
]
BY_ID = {p["id"]: p for p in PROVIDERS}


def get(provider_id: str | None) -> dict[str, Any] | None:
    return BY_ID.get(provider_id or "")


# Three model tiers, spelled the way each preset names them. A preset with no row (ollama, custom) uses its defaultModel.
TIER_DEFAULTS: dict[str, dict[str, str]] = {
    # Ember 1 for the explicit high-tier work; DeepSeek V4.1 Flash (also the chat default) for everything else.
    "fireworks": {"high": "accounts/fireworks/models/ember-1", "medium": "accounts/fireworks/models/deepseek-v4p1-flash",
                  "low": "accounts/fireworks/models/deepseek-v4p1-flash"},
    "litellm": {"high": "ember-1", "medium": "deepseek-v4-flash", "low": "deepseek-v4-flash"},
    "openai": {"high": "gpt-5", "medium": "gpt-5-mini", "low": "gpt-5-nano"},
    "anthropic": {"high": "claude-opus-5-5", "medium": "claude-sonnet-5-5", "low": "claude-haiku-4-5-20251001"},
    "openrouter": {"high": "anthropic/claude-sonnet-5-5", "medium": "openai/gpt-5-mini", "low": "google/gemini-2.5-flash"},
}

# Which tier each kind of work runs on: cost against quality. The one place to change it.
#   default - chat and agent turns, and the workers they start: the provider's defaultModel (default_model), not a tier.
#   high   - explicit high-tier work: drafting skills and agents. Planning is listed here, but a plan is drafted
#            inside the chat turn, so it runs on that chat's model.
#   medium - quality matters but the user does not watch it: Auto's fast path (still a visible reply), the auto-review
#            of tool calls, compaction and recaps (a bad summary poisons later turns), memory consolidation.
#   low    - high-volume and checkable: memory and graph extraction, auto-learn, style learning, titles, follow-ups,
#            the thinking summariser.
# `settings()` in app.py resolves the legacy keys from these: a blank extractionModel is the low tier, a blank fastModel
# the medium tier (an explicit saved value still wins). Medium sites call tier_model(cfg, "medium") directly.
TASK_TIERS: dict[str, str] = {
    "chat": "default", "planning": "high", "draft_skill": "high", "draft_agent": "high",
    "auto_route_fast": "medium", "auto_review": "medium", "compaction": "medium", "recap": "medium", "consolidation": "medium",
    "extraction": "low", "auto_learn": "low", "style": "low", "title": "low", "followups": "low", "thinking_summary": "low",
}
for _p in PROVIDERS:  # shipped to the Settings pickers as the blank-state placeholder
    _p["tiers"] = TIER_DEFAULTS.get(_p["id"], {})
TIERS = ("high", "medium", "low")
TIER_KEYS = {"high": "modelHigh", "medium": "modelMedium", "low": "modelLow"}


def tier_model(settings: dict[str, Any], tier: str) -> str:
    """The model for a tier: the saved one, else the active provider's default for it, else the chat model."""
    saved = str(settings.get(TIER_KEYS[tier]) or "").strip()
    if saved:
        return saved
    p = get(effective(settings))
    return (TIER_DEFAULTS.get(p["id"], {}).get(tier) if p else None) or str(settings.get("defaultModel") or (p or {}).get("defaultModel") or "")


def default_model(settings: dict[str, Any]) -> str:
    """The chat model when none is saved: the active provider's defaultModel, which is deliberately not its high tier."""
    p = get(effective(settings))
    return str(p.get("defaultModel") or "") if p else ""


def rerank_model(settings: dict[str, Any]) -> str:
    """The saved rerank model, else the active provider's default; blank = reranking is skipped."""
    saved = str(settings.get("retrievalRerankModel") or "").strip()
    return saved or (get(effective(settings)) or {}).get("rerankModel", "")


def _hostport(url: str) -> str:
    """host[:port], lowercase. An address that cannot be parsed (a stored typo such as host:11x34) yields its raw
    text, which matches no preset, so it reads as a custom address instead of raising."""
    try:
        u = urlparse(url if "//" in url else f"//{url}")
        host = (u.hostname or "").lower()
        return f"{host}:{u.port}" if u.port else host
    except ValueError:
        return url.strip().lower()


def check_base_url(url: str) -> str:
    """The trimmed address when it is empty or an http(s) URL with a host and a valid port; else ValueError."""
    base = url.strip()
    if not base:
        return base
    try:
        u = urlparse(base)
        ok = u.scheme in ("http", "https") and bool(u.hostname)
        u.port  # noqa: B018 - raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("baseUrl must be an http:// or https:// address with a valid host and port")
    return base


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
