"""First-run setup routes: which provider, does the key work, stamp that onboarding is done."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import auth_breaker, provider_keys, providers

# Tests swap in an httpx.MockTransport; production leaves it None.
_transport: httpx.AsyncBaseTransport | None = None


class SetupIn(BaseModel):
    provider: str
    baseUrl: str = ""
    apiKey: str | None = None
    model: str = ""


def status_of(settings: dict[str, Any], google_connected: bool) -> dict[str, Any]:
    prov = providers.effective(settings)
    has_key = bool((settings.get("apiKey") or "").strip())
    onboarded = settings.get("onboardedAt") or None
    # An existing install never sees the wizard: it either has a key, or it points at the LiteLLM proxy
    # (which may run with no master key) because that was the only setup there was.
    existing = has_key or (not settings.get("provider") and prov == "litellm")
    return {
        "needsOnboarding": not (onboarded or existing),
        "hasApiKey": has_key,
        "provider": prov,
        "baseUrl": settings.get("baseUrl") or "",
        "model": settings.get("defaultModel") or "",
        "googleConnected": google_connected,
        "onboardedAt": onboarded,
    }


def _headers(api_key: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        h["Authorization"] = f"Bearer {api_key.strip()}"
    return h


def _same_base(a: str | None, b: str | None) -> bool:
    return (a or "").strip().rstrip("/") == (b or "").strip().rstrip("/")


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        msg = err.get("message") if isinstance(err, dict) else err
    except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
        msg = None
    return str(msg or r.text)[:300]


# Providers whose address is the user's own: a saved key is only reused for the address it was saved with.
_OWN_ADDRESS = ("custom", "litellm", "ollama")
CHECK_TIMEOUT = 10
LIST_TIMEOUT = 8
LIST_TTL = 60
# (provider, base) -> (fetched at, ids). Only successful listings are kept; tests clear it.
_models_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def _resolve_key(body: SetupIn, stored: dict[str, Any] | None, secrets: Any = None) -> str | None:
    """The key to test with: the one typed, else the key saved for this provider, else the active one. A saved key
    is only used for the address it belongs to (a preset's fixed address, or the stored one), so it never
    travels to a different server."""
    if (body.apiKey or "").strip():
        return body.apiKey
    base, stored = body.baseUrl.strip(), stored or {}
    preset = providers.get(body.provider)
    if secrets is not None:
        saved = provider_keys.load(secrets).get(body.provider)
        fixed = bool(preset and preset["baseUrl"] and body.provider not in _OWN_ADDRESS and _same_base(base, preset["baseUrl"]))
        if saved and (fixed or _same_base(base, stored.get("baseUrl"))):
            return saved
    return stored.get("apiKey") if _same_base(base, stored.get("baseUrl")) else None


def _parse_models(r: httpx.Response) -> list[str] | None:
    try:
        return sorted(m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m)
    except Exception:  # noqa: BLE001 - listing is optional
        return None


def _unreachable(base: str, e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return f"{base} took too long to answer."
    return f"Couldn't reach {base} — is it running?"


async def test_connection(body: SetupIn, stored: dict[str, Any] | None = None, secrets: Any = None) -> dict[str, Any]:
    """`stored` is the saved settings: with no key in the body, a saved key is reused, but only for the
    endpoint it was saved for (see _resolve_key). Without a model, listing the models is the whole check."""
    base = body.baseUrl.strip()
    model = body.model.strip()
    fail = lambda msg: {"ok": False, "error": msg, "latencyMs": None, "models": None}  # noqa: E731
    if not base:
        return fail("Enter a base URL first.")
    key = _resolve_key(body, stored, secrets)
    headers = _headers(key)
    t0 = time.time()
    models: list[str] | None = None
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, transport=_transport) as client:
            r = await client.get(providers.endpoint(base, "/models"), headers=headers)
            if r.status_code in (401, 403):
                return fail(f"That key was rejected ({r.status_code}).")
            unlisted = r.status_code in (404, 405)
            if r.status_code < 400:
                models = _parse_models(r)
            if not model:
                if unlisted:
                    return fail("Enter a model to test with: this server does not list its models.")
                if r.status_code >= 400:
                    return fail(f"The provider returned {r.status_code}: {_error_text(r)}")
                return {"ok": True, "error": None, "latencyMs": int((time.time() - t0) * 1000), "models": models}
            # A key/connection probe, not a reply, and the one capped call: a reasoning model would otherwise think at
            # length and the timeout would read as a bad key. Newer OpenAI models refuse max_tokens; the others ignore or require it, so try it first.
            chat = {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "stream": False}
            url = providers.endpoint(base, "/chat/completions")
            r = await client.post(url, headers=headers, json={**chat, "max_tokens": 1})
            if r.status_code == 400 and "max_completion_tokens" in r.text:
                r = await client.post(url, headers=headers, json={**chat, "max_completion_tokens": 16})
    except (httpx.TransportError, httpx.InvalidURL, ValueError) as e:
        return fail(_unreachable(base, e))
    if r.status_code in (401, 403):
        return fail(f"That key was rejected ({r.status_code}).")
    if r.status_code == 404 and unlisted:
        return fail(f"Nothing answered at {base}: check the address.")
    if r.status_code == 404 or (r.status_code == 400 and any(w in r.text.lower() for w in ("model", "not found"))
                                and any(w in r.text.lower() for w in ("not found", "does not exist", "invalid", "unknown"))):
        return fail(f"Model {model} not found.")
    if r.status_code >= 400:
        return fail(f"The provider returned {r.status_code}: {_error_text(r)}")
    auth_breaker.success({"baseUrl": base, "apiKey": (key or "").strip()})  # a passing Test reopens a held key at once
    return {"ok": True, "error": None, "latencyMs": int((time.time() - t0) * 1000), "models": models}


async def list_models(body: SetupIn, stored: dict[str, Any] | None = None, secrets: Any = None) -> dict[str, Any]:
    """The ids a provider's /models lists, for the model pickers. Never raises: {"models": None, "error": why}."""
    base = body.baseUrl.strip()
    if not base:
        return {"models": None, "error": "Enter a base URL first."}
    cached = _models_cache.get((body.provider, base))
    if cached and time.time() - cached[0] < LIST_TTL:
        return {"models": cached[1], "error": None}
    key = _resolve_key(body, stored, secrets)
    preset = providers.get(body.provider)
    if preset and preset["needsKey"] and not (key or "").strip():
        return {"models": None, "error": None}  # nothing to ask yet: the preset's own suggestions stand
    try:
        async with httpx.AsyncClient(timeout=LIST_TIMEOUT, transport=_transport) as client:
            r = await client.get(providers.endpoint(base, "/models"), headers=_headers(key))
    except (httpx.TransportError, httpx.InvalidURL, ValueError) as e:
        return {"models": None, "error": _unreachable(base, e)}
    if r.status_code in (401, 403):
        return {"models": None, "error": f"That key was rejected ({r.status_code})."}
    if r.status_code in (404, 405):
        return {"models": None, "error": "This server does not list its models."}
    if r.status_code >= 400:
        return {"models": None, "error": f"The provider returned {r.status_code}: {_error_text(r)}"}
    models = _parse_models(r)
    if models is not None:
        _models_cache[(body.provider, base)] = (time.time(), models)
    return {"models": models, "error": None}


def router(get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None],
           google_connected: Callable[[], bool], secrets: Any = None) -> APIRouter:
    r = APIRouter(prefix="/setup", tags=["setup"])

    def status() -> dict[str, Any]:
        return status_of(get_settings(), google_connected())

    @r.get("/status")
    def get_status() -> dict[str, Any]:
        return status()

    @r.get("/providers")
    def list_providers() -> dict[str, Any]:
        return {"providers": providers.PROVIDERS}

    @r.post("/test")
    async def test(body: SetupIn) -> dict[str, Any]:
        return await test_connection(body, get_settings(), secrets)

    @r.post("/models")
    async def models(body: SetupIn) -> dict[str, Any]:
        return await list_models(body, get_settings(), secrets)

    @r.post("/complete")
    def complete(body: SetupIn) -> dict[str, Any]:
        preset = providers.get(body.provider)
        if not preset:
            raise HTTPException(422, f"Unknown provider {body.provider!r}")
        try:
            base = providers.check_base_url(body.baseUrl) or preset["baseUrl"]
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        model = body.model.strip()
        key = (body.apiKey or "").strip()
        if not base:
            raise HTTPException(422, "A base URL is required")
        if not model:
            raise HTTPException(422, "A model is required")
        if preset["needsKey"] and not key:
            raise HTTPException(422, f"{preset['name']} needs an API key")
        # extractionModel is cleared: one left over from another provider (a LiteLLM alias) would fail every
        # background call. Empty means "use the default model".
        patch = {"provider": body.provider, "baseUrl": base, "apiKey": key, "defaultModel": model,
                 "extractionModel": "", "fastModel": "", "onboardedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        # No key for the endpoint the saved key belongs to means "unchanged", as in PUT /settings; an empty
        # value would delete it from the secret store. A different endpoint never inherits the old key.
        if not key and _same_base(base, get_settings().get("baseUrl")):
            del patch["apiKey"]
        set_settings(patch)
        return status()

    @r.post("/reset")
    def reset() -> dict[str, Any]:
        set_settings({"onboardedAt": None})
        return status()

    return r
