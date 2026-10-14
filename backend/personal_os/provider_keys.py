"""One API key per provider, in the providerKeys secret (a JSON map of provider id to key).

The map is touched only here and by the /providers routes: it is not a settings key, so PUT /settings can neither read nor
write it, and GET /settings only ever reports which providers have a key. A key is saved only after the provider accepted it.
"""
from __future__ import annotations

import json
from typing import Any

from . import providers

SECRET = "providerKeys"


def load(secrets: Any) -> dict[str, str]:
    try:
        keys = json.loads(secrets.get(SECRET) or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in keys.items() if isinstance(v, str) and v} if isinstance(keys, dict) else {}


def save(secrets: Any, keys: dict[str, str]) -> None:
    keys = {k: v for k, v in keys.items() if v}
    if keys:
        secrets.set(SECRET, json.dumps(keys))
    else:
        secrets.delete(SECRET)


def saved_for(secrets: Any) -> dict[str, bool]:
    """Which providers have a key, never the keys themselves."""
    return {k: True for k in load(secrets)}


def migrate(db: Any) -> None:
    """Startup, idempotent: bring an install from one active provider (`provider`, `baseUrl`, `apiKey`) to the provider list.

    Builds the `providers` entry of the stored provider if there is none, files the active `apiKey` as that provider's
    key, adds an entry for every preset that has a saved key, turns bare conversation models into refs, and only then
    deletes the old rows. A second run finds nothing to do."""
    from . import provider_route
    cur = db.get_settings()
    ents = [dict(e) for e in cur.get("providers") or [] if isinstance(e, dict) and e.get("id")]
    old = providers.effective({"provider": cur.get("provider"), "baseUrl": cur.get("baseUrl")})
    if old and not any(e["kind"] == old for e in ents):
        ents.append(providers.new_entry(old, str(cur.get("baseUrl") or "")))
    active = next((e for e in ents if e["kind"] == old), None) if old else None
    keys = load(db.secrets)
    if active and cur.get("apiKey") and active["id"] not in keys:
        keys[active["id"]] = cur["apiKey"]
    for pid in keys:
        if pid in providers.BY_ID and providers.BY_ID[pid]["needsKey"] and not any(e["id"] == pid for e in ents):
            ents.append(providers.new_entry(pid))
    save(db.secrets, keys)
    if ents != (cur.get("providers") or []):
        db.set_settings({"providers": ents})
    aid = (active or (ents[0] if ents else {})).get("id")
    if ents:
        with db.tx() as c:
            for cid, model in c.execute("SELECT id, model FROM conversations WHERE model IS NOT NULL AND model != ''").fetchall():
                if (ref := provider_route.canon(model, ents, aid)) != model:
                    c.execute("UPDATE conversations SET model = ? WHERE id = ?", (ref, cid))
    if active or not cur.get("apiKey"):  # an apiKey that found no provider to belong to stays, so nothing is lost
        if cur.get("apiKey"):
            db.set_settings({"apiKey": None})
        with db.tx() as c:
            c.execute("DELETE FROM settings WHERE key IN ('provider', 'baseUrl')")
