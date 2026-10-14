"""Setup helpers: the connection probe the /providers routes verify a key with, and first-run status."""
from __future__ import annotations

import time
from typing import Any, Callable

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from . import auth_breaker, provider_keys, provider_route, providers

# Tests swap in an httpx.MockTransport; production leaves it None.
_transport: httpx.AsyncBaseTransport | None = None


class SetupIn(BaseModel):
    provider: str  # the preset kind: it decides local-server timeouts and the Ollama hints
    baseUrl: str = ""
    apiKey: str | None = None
    model: str = ""


def status_of(settings: dict[str, Any], google_connected: bool) -> dict[str, Any]:
    use = provider_route.usable(settings)
    onboarded = settings.get("onboardedAt") or None
    return {
        "needsOnboarding": not (onboarded or use),
        "hasApiKey": any(e["apiKey"] for e in use),
        "providers": [e["id"] for e in use],
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


# Kinds whose server is the user's own machine or proxy: a longer probe timeout for a model still loading.
_OWN_ADDRESS = ("custom", "litellm", "ollama")
CHECK_TIMEOUT = 10
# The probe reply from a server on the user's own machine may first have to load the model into memory.
LOCAL_PROBE_TIMEOUT = 90


def _resolve_key(body: SetupIn, secrets: Any = None) -> str | None:
    """The key to test with: the one typed, else the key saved under this provider id."""
    if (body.apiKey or "").strip():
        return body.apiKey
    return provider_keys.load(secrets).get(body.provider) if secrets is not None else None


def _parse_models(r: httpx.Response) -> list[str] | None:
    try:
        return sorted(m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m)
    except Exception:  # noqa: BLE001 - listing is optional
        return None


def _unreachable(base: str, e: Exception, provider: str = "", local: bool = False) -> str:
    if isinstance(e, httpx.TimeoutException):
        return f"{base} took too long to answer." + (" The model may still be loading: try again in a minute." if local else "")
    if provider == "ollama":
        return f"Couldn't reach {base} — is Ollama running? Get it at https://ollama.com/download."
    return f"Couldn't reach {base} — is it running?"


def _denied(key: str | None, code: int) -> str:
    # A blank key was never "rejected": the server wants one.
    return f"That key was rejected ({code})." if (key or "").strip() else "This server needs an API key."


async def test_connection(body: SetupIn, secrets: Any = None) -> dict[str, Any]:
    """Probe a provider with the key in the body (else the one saved under body.provider). Without a model, listing the
    models is the whole check; with one, a one-token chat call decides."""
    base = body.baseUrl.strip()
    model = body.model.strip()
    fail = lambda msg: {"ok": False, "error": msg, "latencyMs": None, "models": None}  # noqa: E731
    if not base:
        return fail("Enter a base URL first.")
    key = _resolve_key(body, secrets)
    headers = _headers(key)
    local = body.provider in _OWN_ADDRESS
    t0 = time.time()
    models: list[str] | None = None
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, transport=_transport) as client:
            r = await client.get(providers.endpoint(base, "/models"), headers=headers)
            # With a model, the chat probe decides: some servers guard the listing differently from chat.
            if r.status_code in (401, 403) and not model:
                return fail(_denied(key, r.status_code))
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
            wait = httpx.Timeout(CHECK_TIMEOUT, read=LOCAL_PROBE_TIMEOUT) if local else CHECK_TIMEOUT
            r = await client.post(url, headers=headers, json={**chat, "max_tokens": 1}, timeout=wait)
            if r.status_code == 400 and "max_completion_tokens" in r.text:
                r = await client.post(url, headers=headers, json={**chat, "max_completion_tokens": 16}, timeout=wait)
    except (httpx.TransportError, httpx.InvalidURL, ValueError) as e:
        return fail(_unreachable(base, e, body.provider, local))
    if r.status_code in (401, 403):
        return fail(_denied(key, r.status_code))
    if r.status_code == 404 and unlisted:
        return fail(f"Nothing answered at {base}: check the address.")
    if r.status_code == 404 or (r.status_code == 400 and any(w in r.text.lower() for w in ("model", "not found"))
                                and any(w in r.text.lower() for w in ("not found", "does not exist", "invalid", "unknown"))):
        if body.provider == "ollama":
            pulled = f" Pulled: {', '.join(models)}." if models else ""
            return fail(f"Model {model} not found. Run: ollama pull {model}.{pulled}")
        return fail(f"Model {model} not found.")
    if r.status_code >= 400:
        return fail(f"The provider returned {r.status_code}: {_error_text(r)}")
    auth_breaker.success({"baseUrl": base, "apiKey": (key or "").strip()})  # a passing Test reopens a held key at once
    return {"ok": True, "error": None, "latencyMs": int((time.time() - t0) * 1000), "models": models}


def router(get_settings: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None],
           google_connected: Callable[[], bool]) -> APIRouter:
    r = APIRouter(prefix="/setup", tags=["setup"])

    def status() -> dict[str, Any]:
        return status_of(get_settings(), google_connected())

    @r.get("/status")
    def get_status() -> dict[str, Any]:
        return status()

    @r.post("/reset")
    def reset() -> dict[str, Any]:
        set_settings({"onboardedAt": None})
        return status()

    return r
