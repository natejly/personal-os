"""Per-model routing: a model is "<providerId>/<model>" and a call for it goes to that provider, with its saved key.

Providers are the `providers` setting (a list of {id, kind, name, baseUrl, models?}); each one's key lives in the
providerKeys secret (provider_keys.py), read through a loader the app registers at startup, so keys never travel in the
settings dict that GET /settings reports. `resolve()` turns (settings, ref) into settings overlaid with the owning
provider's baseUrl, kind and key, plus the bare model name that provider knows. A settings dict with no `providers`
list (or an empty one: what many callers and tests hand to llm.*) passes through unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from . import providers
from .usage import short_model

log = logging.getLogger(__name__)
TTL = 600.0
_keys: Callable[[], dict[str, str]] = lambda: {}
_live: dict[str, set[str]] = {}  # provider id -> ids its last successful /models listing returned
_cache: dict[str, tuple[float, tuple[str, str], list[dict[str, Any]]]] = {}  # provider id -> (fetched at, (base, key), rows)
# Embedding models a provider serves that are not in its chat model list; they count as preset and as mode "embedding".
EMBEDDING_IDS: dict[str, tuple[str, ...]] = {
    "fireworks": ("qwen3-embedding-8b",), "openai": ("text-embedding-3-small", "text-embedding-3-large")}


# Settings that hold a model ref (embeddingModel stays a bare id: the vectors are keyed by it).
REF_SETTINGS = ("defaultModel", "modelHigh", "modelMedium", "modelLow", "workerModel", "visionModel", "imageModel", "retrievalRerankModel")


def set_key_source(fn: Callable[[], dict[str, str]]) -> None:
    global _keys
    _keys = fn


def preset_ids(kind: str) -> set[str]:
    p = providers.get(kind) or {}
    ids = set(p.get("models") or []) | set(providers.TIER_DEFAULTS.get(kind, {}).values()) | set(EMBEDDING_IDS.get(kind, ()))
    ids |= {p["rerankModel"]} if p.get("rerankModel") else set()
    return {i for i in ids if i}


def entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The configured providers in list order, usable or not."""
    return [e for e in settings.get("providers") or [] if isinstance(e, dict) and e.get("id")]


def keyless(kind: str) -> bool:
    """A kind that runs without a key (the user's own server): ollama, litellm, custom."""
    return not (providers.get(kind) or {"needsKey": True})["needsKey"]


def usable(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Configured providers that can take a call (a saved key, or a kind that needs none), in list order, each with its
    `apiKey` and a resolved `baseUrl`."""
    keys, out = _keys(), []
    for e in entries(settings):
        key = keys.get(e["id"], "")
        if key or keyless(e.get("kind", "")):
            out.append({**e, "baseUrl": e.get("baseUrl") or (providers.get(e.get("kind")) or {}).get("baseUrl", ""), "apiKey": key})
    return out


def overlay(settings: dict[str, Any], e: dict[str, Any]) -> dict[str, Any]:
    """`settings` pointed at provider `e` (an item of usable()). The provider list is dropped so the result is a legacy dict:
    resolving it again is a no-op."""
    return {**settings, "provider": e["kind"], "providerId": e["id"], "baseUrl": e["baseUrl"], "apiKey": e["apiKey"], "providers": None}


def _owns(e: dict[str, Any], model: str) -> bool:
    ids = preset_ids(e["kind"]) | _live.get(e["id"], set()) | set(e.get("models") or [])
    if e["kind"] == "fireworks" and ("/" not in model or model.startswith("accounts/")):
        return short_model(model) in {short_model(i) for i in ids}  # bare and accounts/... spellings are one model
    return model in ids


def canon(model: str, ents: list[dict[str, Any]], active_id: str | None) -> str:
    """A bare legacy model id as a "<provider>/<model>" ref: the active provider when it owns it (a provider on the user's
    own server owns everything), else the first preset that does, else the active provider. Blank, "auto" and ids that are
    already refs come back unchanged."""
    model = (model or "").strip()
    if not model or model == "auto":
        return model
    active = next((e for e in ents if e["id"] == active_id), None)
    head, _ = providers.split_ref(model, {e["id"] for e in ents})
    if head and not (active and model in preset_ids(active["kind"])):
        return model
    if active and (keyless(active["kind"]) or _owns(active, model)):
        return providers.make_ref(active["id"], model)
    owner = next((p["id"] for p in providers.PROVIDERS if not keyless(p["id"]) and _owns({"id": p["id"], "kind": p["id"]}, model)), None)
    owner = owner or (active["id"] if active else None)
    return providers.make_ref(owner, model) if owner else model


def _known_head(ref: str) -> bool:
    """A ref whose head names a provider id Grain could have issued (a preset id, or custom-N) with a model after it."""
    head, sep, rest = ref.partition("/")
    return bool(sep and rest and (head in providers.BY_ID or re.fullmatch(r"custom-\d+", head)))


def _unavailable(settings: dict[str, Any], pid: str | None, ref: str = "") -> Exception:
    from .llm import LLMError  # llm imports this module
    ents = entries(settings)
    e = next((x for x in ents if x["id"] == pid), None) or (ents[0] if ents else None)
    if pid and not any(x["id"] == pid for x in ents):
        return LLMError(f"{ref or pid} was on a provider that's been removed. Pick another model in Settings → Models.")
    if not e:
        return LLMError("Add a provider in Settings → Models.")
    return LLMError(f"Add a key for {e.get('name') or e['id']} in Settings → Models.")


def resolve(settings: dict[str, Any], ref: str | None) -> tuple[dict[str, Any], str]:
    """(settings for the owning provider, the bare model name). A ref names its provider; a bare legacy id goes to the
    first usable provider that owns it, else the first usable one. No provider list or no model: unchanged. A provider
    that is unusable (no key, or removed) raises llm.LLMError telling the user what to add."""
    if not settings.get("providers") or not ref:
        return settings, ref or ""
    use = usable(settings)
    pid, model = providers.split_ref(ref, {e["id"] for e in entries(settings)})
    if pid:
        e = next((u for u in use if u["id"] == pid), None)
    elif _known_head(ref):
        raise _unavailable(settings, ref.partition("/")[0], ref)
    else:
        e = next((u for u in use if _owns(u, ref)), use[0] if use else None)
    if e is None:
        raise _unavailable(settings, pid)
    return overlay(settings, e), model


def route(settings: dict[str, Any], model: str | None) -> dict[str, Any]:
    """resolve() for a caller that sends the model name on its own (embeddings, rerank)."""
    return resolve(settings, model)[0]


def tag(e: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Picker rows for provider `e`: {id: "<pid>/<model>", model, provider, mode?, reasoning?, preset?}."""
    pre, emb = preset_ids(e["kind"]), set(EMBEDDING_IDS.get(e["kind"], ()))
    out = []
    for r in rows:
        mode = r.get("mode") or ("embedding" if r["id"] in emb else None)
        out.append({**{k: v for k, v in r.items() if k != "id"}, "id": providers.make_ref(e["id"], r["id"]), "model": r["id"],
                    "provider": e["id"], **({"mode": mode} if mode else {}), **({"preset": True} if r["id"] in pre else {})})
    return out


def invalidate(pid: str) -> None:
    _cache.pop(pid, None)
    _live.pop(pid, None)


def seed(e: dict[str, Any], model_ids: list[str] | None) -> None:
    """Remember a listing a verification just fetched, so the picker shows the new provider's models at once."""
    invalidate(e["id"])
    if model_ids is not None:
        _live[e["id"]] = set(model_ids)


async def all_models(cfg: dict[str, Any], list_fn: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """The models of every usable provider, in list order (cached TTL s for providers that need a key). A provider whose
    listing fails contributes its preset models; the endpoint itself never fails. Models the user typed for a server that
    cannot list them are merged in."""

    async def fetch(e: dict[str, Any]) -> list[dict[str, Any]]:
        stamp, hit = (e["baseUrl"], e["apiKey"]), _cache.get(e["id"])
        if hit and time.time() - hit[0] < TTL and hit[1] == stamp:
            return hit[2]
        typed = [{"id": m} for m in e.get("models") or []]
        try:
            rows = await list_fn(overlay(cfg, e))
        except Exception as ex:  # noqa: BLE001 - one provider down must not take the picker with it
            log.warning("model listing failed for %s: %s", e["id"], type(ex).__name__)
            return tag(e, [{"id": i} for i in sorted(preset_ids(e["kind"]) | set(e.get("models") or []))])
        _live[e["id"]] = {r["id"] for r in rows}
        have = {r["id"] for r in rows}
        rows = tag(e, [*rows, *(r for r in typed if r["id"] not in have)])
        if not keyless(e["kind"]):  # a server on the user's own machine lists fresh: they pull models while Grain runs
            _cache[e["id"]] = (time.time(), stamp, rows)
        return rows

    return [r for rows in await asyncio.gather(*(fetch(e) for e in usable(cfg))) for r in rows]


def first_embedding(cfg: dict[str, Any]) -> str:
    """The embedding model of the first keyed cloud provider that serves one (a bare id); "" when none does."""
    return next((EMBEDDING_IDS[e["kind"]][0] for e in usable(cfg) if EMBEDDING_IDS.get(e["kind"]) and e["apiKey"]), "")


async def local_embedding(cfg: dict[str, Any], list_fn: Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]) -> str:
    """For the user's own servers (ollama, litellm, custom): the first model a /models listing marks as an embedding
    model, else ""."""
    for e in usable(cfg):
        if not keyless(e["kind"]):
            continue
        try:
            if pick := next((r["id"] for r in await list_fn(overlay(cfg, e)) if r.get("mode") == "embedding"), ""):
                return pick
        except Exception:  # noqa: BLE001 - an unreachable server just means no pick
            continue
    return ""


def can_embed(cfg: dict[str, Any], model: str) -> bool:
    """Whether some usable provider (or one of the user's own servers) serves `model`."""
    if not model:
        return False
    if not cfg.get("providers"):
        return bool(cfg.get("baseUrl"))
    return any(keyless(e["kind"]) or _owns(e, model) for e in usable(cfg))
