"""Per-model routing: a call for a model goes to the provider that owns it, with that provider's saved key.

`route()` returns the settings unchanged when the active provider owns the model (or nobody else does); otherwise an
overlay with the owner's baseUrl, provider and key. The model string is never rewritten. Keys come from a loader the
app registers at startup, so they never travel in the settings dict that GET /settings reports.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from . import providers
from .usage import short_model

log = logging.getLogger(__name__)
TTL = 600.0
_keys: Callable[[], dict[str, str]] = lambda: {}
_live: dict[str, set[str]] = {}  # provider -> ids its last successful /models listing returned
_cache: dict[str, tuple[float, str, list[dict[str, Any]]]] = {}  # provider -> (fetched at, key, tagged rows)
# ponytail: local presets (ollama/litellm/custom) are never routing targets and, when active, own every model name
# (their catalog is whatever the user's server defines); cloud-only routing. Revisit if a proxy user wants direct keys.
_LOCAL = ("ollama", "litellm", "custom")
# Embedding models a provider serves that are not in its chat model list; they count as preset and as mode "embedding".
EMBEDDING_IDS: dict[str, tuple[str, ...]] = {
    "fireworks": ("qwen3-embedding-8b",), "openai": ("text-embedding-3-small", "text-embedding-3-large")}


def set_key_source(fn: Callable[[], dict[str, str]]) -> None:
    global _keys
    _keys = fn


def preset_ids(pid: str) -> set[str]:
    p = providers.get(pid) or {}
    ids = set(p.get("models") or []) | set(providers.TIER_DEFAULTS.get(pid, {}).values()) | set(EMBEDDING_IDS.get(pid, ()))
    ids |= {p["rerankModel"]} if p.get("rerankModel") else set()
    return {i for i in ids if i}


def _owns(pid: str, model: str) -> bool:
    ids = preset_ids(pid) | _live.get(pid, set())
    if pid == "fireworks" and ("/" not in model or model.startswith("accounts/")):
        return short_model(model) in {short_model(i) for i in ids}  # bare and accounts/... spellings are one model
    return model in ids


def route(settings: dict[str, Any], model: str | None) -> dict[str, Any]:
    active = providers.effective(settings)
    if not model or active in _LOCAL or (active and _owns(active, model)):
        return settings
    keys = _keys()
    for p in providers.PROVIDERS:
        if p["id"] != active and p["needsKey"] and keys.get(p["id"]) and _owns(p["id"], model):
            return {**settings, "provider": p["id"], "baseUrl": p["baseUrl"], "apiKey": keys[p["id"]]}
    return settings


def tag(pid: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pre, emb = preset_ids(pid), set(EMBEDDING_IDS.get(pid, ()))
    return [{**r, **({"mode": "embedding"} if r["id"] in emb and not r.get("mode") else {}), "provider": pid,
             **({"preset": True} if r["id"] in pre else {})} for r in rows]


def invalidate(pid: str) -> None:
    _cache.pop(pid, None)
    _live.pop(pid, None)


async def all_models(cfg: dict[str, Any], list_fn: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """The active provider's rows plus those of every other provider with a saved key (cached TTL s each). A provider
    whose listing fails contributes its preset models; the endpoint itself never fails. De-duplicated, active first."""
    active = providers.effective(cfg) or "custom"
    keys = _keys()

    async def fetch(pid: str, overlay: dict[str, Any], use_cache: bool) -> list[dict[str, Any]]:
        hit = _cache.get(pid)
        if use_cache and hit and time.time() - hit[0] < TTL and hit[1] == overlay.get("apiKey"):
            return hit[2]
        try:
            rows = tag(pid, await list_fn(overlay))
        except Exception as e:  # noqa: BLE001 - one provider down must not take the picker with it
            log.warning("model listing failed for %s: %s", pid, type(e).__name__)
            return tag(pid, [{"id": i} for i in sorted(preset_ids(pid))])
        _live[pid] = {r["id"] for r in rows}
        if use_cache:
            _cache[pid] = (time.time(), str(overlay.get("apiKey")), rows)
        return rows

    others = [p for p in providers.PROVIDERS if p["id"] != active and p["needsKey"] and keys.get(p["id"])]
    lists = await asyncio.gather(fetch(active, cfg, False), *(
        fetch(p["id"], {**cfg, "provider": p["id"], "baseUrl": p["baseUrl"], "apiKey": keys[p["id"]]}, True) for p in others))
    seen: set[str] = set()
    return [r for rows in lists for r in rows if not (r["id"] in seen or seen.add(r["id"]))]


def first_embedding(cfg: dict[str, Any]) -> str:
    """The embedding model of the first cloud provider with a key, the active one first; "" when none serves one."""
    active, keys = providers.effective(cfg), _keys()
    order = [active, *(p["id"] for p in providers.PROVIDERS if p["id"] != active)]
    return next((EMBEDDING_IDS[pid][0] for pid in order if EMBEDDING_IDS.get(pid)
                 and (keys.get(pid) or (pid == active and cfg.get("apiKey"))) and pid not in _LOCAL), "")


async def local_embedding(cfg: dict[str, Any], list_fn: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]) -> str:
    """For a local or custom provider: the first model its /models listing marks as an embedding model, else ""."""
    if providers.effective(cfg) not in _LOCAL:
        return ""
    try:
        return next((r["id"] for r in await list_fn(cfg) if r.get("mode") == "embedding"), "")
    except Exception:  # noqa: BLE001 - an unreachable server just means no pick
        return ""


def can_embed(cfg: dict[str, Any], model: str) -> bool:
    """Whether some provider with a key (or the local server) serves `model`."""
    active = providers.effective(cfg)
    if not model:
        return False
    if active in _LOCAL or (active and _owns(active, model)):
        return True
    return route(cfg, model) is not cfg
