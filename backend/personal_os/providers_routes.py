"""/providers: the configured model providers, and adding, re-keying, re-addressing and removing them.

A key or an address is verified against the server first (setup.test_connection) and stored only when that succeeds,
so a failed attempt changes nothing and a failed replacement keeps the old key. Failures of the check come back as
HTTP 200 {"ok": false, "error": ...} so the UI can show them inline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import auth_breaker, provider_keys, provider_route, providers, setup

OWN_ADDRESS = ("custom", "litellm", "ollama")  # kinds whose address the user sets


class AddIn(BaseModel):
    kind: str
    baseUrl: str | None = None
    name: str | None = None
    apiKey: str | None = None
    model: str | None = None


class KeyIn(BaseModel):
    apiKey: str


class AddressIn(BaseModel):
    baseUrl: str


def _fail(msg: str) -> dict[str, Any]:
    return {"ok": False, "error": msg, "latencyMs": None, "models": None}


def router(get_stored: Callable[[], dict[str, Any]], set_settings: Callable[[dict[str, Any]], None], secrets: Any,
           after_change: Callable[[], None]) -> APIRouter:
    """`get_stored` is the raw settings rows; `after_change` runs once a provider or key was saved (embedding pick)."""
    r = APIRouter(tags=["providers"])

    def entries() -> list[dict[str, Any]]:
        return provider_route.entries(get_stored())

    def view(e: dict[str, Any]) -> dict[str, Any]:
        preset = providers.get(e["kind"]) or {}
        key = provider_keys.load(secrets).get(e["id"], "")
        needs = bool(preset.get("needsKey", True))
        base = e.get("baseUrl") or preset.get("baseUrl", "")
        blocked = auth_breaker.blocked(provider_route.overlay({}, {**e, "baseUrl": base, "apiKey": key})) if key else None
        out: dict[str, Any] = {"id": e["id"], "kind": e["kind"], "name": e.get("name") or preset.get("name") or e["id"],
                               "baseUrl": base, "needsKey": needs, "hasKey": bool(key),
                               "status": "blocked" if blocked else ("nokey" if needs and not key else "ok")}
        if blocked:
            out["statusMessage"] = blocked
        elif out["status"] == "nokey":
            out["statusMessage"] = f"Add a key for {out['name']}."
        if (live := provider_route._live.get(e["id"])) is not None:
            out["modelCount"] = len(live)
        return out

    def done(entry: dict[str, Any], key: str, result: dict[str, Any], old_key: str = "") -> dict[str, Any]:
        base = entry.get("baseUrl") or (providers.get(entry["kind"]) or {}).get("baseUrl", "")
        provider_route.seed({**entry}, result.get("models"))
        for k in filter(None, (old_key, key)):
            auth_breaker.reset({"baseUrl": base, "apiKey": k})
        after_change()
        return {"ok": True, "provider": view(entry), "models": result.get("models"), "latencyMs": result.get("latencyMs")}

    @r.get("/providers")
    def list_providers() -> dict[str, Any]:
        return {"presets": providers.PROVIDERS, "providers": [view(e) for e in entries()]}

    @r.post("/providers")
    async def add_provider(body: AddIn) -> dict[str, Any]:
        preset = providers.get(body.kind)
        if not preset:
            return _fail(f"Unknown provider {body.kind!r}.")
        if body.kind != "custom" and any(e["kind"] == body.kind for e in entries()):
            return _fail(f"{preset['name']} is already added — use Replace key.")
        try:
            base = (providers.check_base_url(body.baseUrl or "") if body.kind in OWN_ADDRESS else "") or preset["baseUrl"]
        except ValueError as e:
            return _fail(str(e))
        if not base:
            return _fail("Enter a base URL first.")
        key, model = (body.apiKey or "").strip(), (body.model or "").strip()
        if preset["needsKey"] and not key:
            return _fail(f"{preset['name']} needs an API key.")
        result = await setup.test_connection(setup.SetupIn(provider=body.kind, baseUrl=base, apiKey=key or None, model=model))
        if not result["ok"]:
            return _fail(result["error"])
        have = entries()  # read again: the probe awaited
        ids = {e["id"] for e in have}
        pid = body.kind if body.kind not in ids else next(f"{body.kind}-{n}" for n in range(2, 1000) if f"{body.kind}-{n}" not in ids)
        entry = providers.new_entry(body.kind, base, body.name or "", pid)
        if model and result.get("models") is None:  # a server that cannot list its models: the typed one is all there is
            entry["models"] = [model]
        stored = get_stored()
        patch: dict[str, Any] = {"providers": [*have, entry]}
        if not have:
            first = model or preset["defaultModel"]
            if first:
                patch["defaultModel"] = providers.make_ref(pid, first)
        if not stored.get("onboardedAt"):
            patch["onboardedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if key:
            keys = provider_keys.load(secrets)
            keys[pid] = key
            provider_keys.save(secrets, keys)
        set_settings(patch)
        return done(entry, key, result)

    def find(pid: str) -> dict[str, Any]:
        e = next((x for x in entries() if x["id"] == pid), None)
        if e is None:
            raise HTTPException(404, f"Unknown provider {pid!r}")
        return e

    @r.put("/providers/{pid}/key")
    async def replace_key(pid: str, body: KeyIn) -> dict[str, Any]:
        e = find(pid)
        key = body.apiKey.strip()
        if not key:
            raise HTTPException(422, "apiKey must not be blank")
        base = e.get("baseUrl") or (providers.get(e["kind"]) or {}).get("baseUrl", "")
        probe = (e.get("models") or [""])[0]  # a server that cannot list its models is probed with a typed one
        result = await setup.test_connection(setup.SetupIn(provider=e["kind"], baseUrl=base, apiKey=key, model=probe))
        if not result["ok"]:
            return _fail(result["error"])
        keys = provider_keys.load(secrets)
        old = keys.get(pid, "")
        keys[pid] = key
        provider_keys.save(secrets, keys)
        return done(find(pid), key, result, old)

    @r.put("/providers/{pid}")
    async def set_address(pid: str, body: AddressIn) -> dict[str, Any]:
        e = find(pid)
        if e["kind"] not in OWN_ADDRESS:
            return _fail(f"The address of {e.get('name') or pid} is fixed.")
        try:
            base = providers.check_base_url(body.baseUrl)
        except ValueError as ex:
            return _fail(str(ex))
        if not base:
            return _fail("Enter a base URL first.")
        key = provider_keys.load(secrets).get(pid, "")
        result = await setup.test_connection(setup.SetupIn(provider=e["kind"], baseUrl=base, apiKey=key or None, model=(e.get("models") or [""])[0]))
        if not result["ok"]:
            return _fail(result["error"])
        updated = {**find(pid), "baseUrl": base}
        set_settings({"providers": [updated if x["id"] == pid else x for x in entries()]})
        return done(updated, key, result, key)

    @r.delete("/providers/{pid}")
    def remove_provider(pid: str) -> dict[str, Any]:
        find(pid)
        keys = provider_keys.load(secrets)
        keys.pop(pid, None)
        provider_keys.save(secrets, keys)
        set_settings({"providers": [x for x in entries() if x["id"] != pid]})
        provider_route.invalidate(pid)
        after_change()
        stored = get_stored()
        chosen = {**stored, "autoReviewModel": (stored.get("permissions") or {}).get("autoReviewModel") or stored.get("autoReviewModel")}
        stale = [k for k in (*provider_route.REF_SETTINGS, "autoReviewModel") if str(chosen.get(k) or "").startswith(f"{pid}/")]
        return {"providers": [view(e) for e in entries()], "staleSelections": stale}

    return r
