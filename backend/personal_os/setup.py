"""First-run setup routes: which provider, does the key work, stamp that onboarding is done."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import providers

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


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        msg = err.get("message") if isinstance(err, dict) else err
    except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
        msg = None
    return str(msg or r.text)[:300]


async def test_connection(body: SetupIn) -> dict[str, Any]:
    base = body.baseUrl.strip()
    model = body.model.strip()
    fail = lambda msg: {"ok": False, "error": msg, "latencyMs": None, "models": None}  # noqa: E731
    if not base:
        return fail("Enter a base URL first.")
    if not model:
        return fail("Choose a model first.")
    headers = _headers(body.apiKey)
    t0 = time.time()
    models: list[str] | None = None
    try:
        async with httpx.AsyncClient(timeout=20, transport=_transport) as client:
            r = await client.get(providers.endpoint(base, "/models"), headers=headers)
            if r.status_code in (401, 403):
                return fail(f"That key was rejected ({r.status_code}).")
            if r.status_code < 400:
                try:
                    models = sorted(m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m)
                except Exception:  # noqa: BLE001 - listing is optional
                    models = None
            # Newer OpenAI models refuse max_tokens; the others ignore or require it, so try it first.
            chat = {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "stream": False}
            url = providers.endpoint(base, "/chat/completions")
            r = await client.post(url, headers=headers, json={**chat, "max_tokens": 1})
            if r.status_code == 400 and "max_completion_tokens" in r.text:
                r = await client.post(url, headers=headers, json={**chat, "max_completion_tokens": 16})
    except httpx.TimeoutException:
        return fail(f"{base} took too long to answer.")
    except (httpx.TransportError, httpx.InvalidURL, ValueError):
        return fail(f"Couldn't reach {base} — is it running?")
    if r.status_code in (401, 403):
        return fail(f"That key was rejected ({r.status_code}).")
    if r.status_code == 404 or (r.status_code == 400 and any(w in r.text.lower() for w in ("model", "not found"))
                                and any(w in r.text.lower() for w in ("not found", "does not exist", "invalid", "unknown"))):
        return fail(f"Model {model} not found.")
    if r.status_code >= 400:
        return fail(f"The provider returned {r.status_code}: {_error_text(r)}")
    return {"ok": True, "error": None, "latencyMs": int((time.time() - t0) * 1000), "models": models}


def router(get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None],
           google_connected: Callable[[], bool]) -> APIRouter:
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
        # Settings never holds the saved key, so a blank key means "the saved one", but only against the
        # saved base URL, so testing a new host can't send it the old key.
        if not (body.apiKey or "").strip():
            saved = get_settings()
            if body.baseUrl.strip().rstrip("/") == (saved.get("baseUrl") or "").strip().rstrip("/"):
                body.apiKey = saved.get("apiKey") or None
        return await test_connection(body)

    @r.post("/complete")
    def complete(body: SetupIn) -> dict[str, Any]:
        preset = providers.get(body.provider)
        if not preset:
            raise HTTPException(422, f"Unknown provider {body.provider!r}")
        base, model = body.baseUrl.strip() or preset["baseUrl"], body.model.strip()
        key = (body.apiKey or "").strip()
        if not base:
            raise HTTPException(422, "A base URL is required")
        if not model:
            raise HTTPException(422, "A model is required")
        if preset["needsKey"] and not key:
            raise HTTPException(422, f"{preset['name']} needs an API key")
        # extractionModel is cleared: one left over from another provider (a LiteLLM alias) would fail every
        # background call. Empty means "use the default model".
        set_settings({"provider": body.provider, "baseUrl": base, "apiKey": key, "defaultModel": model,
                      "extractionModel": "", "onboardedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return status()

    @r.post("/reset")
    def reset() -> dict[str, Any]:
        set_settings({"onboardedAt": None})
        return status()

    return r
