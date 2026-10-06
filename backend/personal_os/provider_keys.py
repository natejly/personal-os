"""One API key per provider, kept in the secret store beside the active `apiKey`.

The active key stays the `apiKey` secret, so every call path reads it exactly as before. This module only
remembers the keys of the providers that are not active, so switching provider and switching back needs no
re-paste. The map lives in one JSON secret and is touched only here: it is not a settings key, so PUT /settings
can neither read nor write it, and GET /settings only ever reports which providers have a key.
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
    """Startup: the key an install already runs on becomes the saved key of its provider."""
    cur = db.get_settings()
    prov, active = providers.effective(cur), cur.get("apiKey")
    keys = load(db.secrets)
    if prov and active and prov not in keys:
        keys[prov] = active
        save(db.secrets, keys)


def apply(db: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """The settings patch to store, once the per-provider keys have been brought along.

    `"apiKey" in patch` is an explicit choice (a string is a new key, None removes it; the HTTP layer has already
    dropped a blank one). A provider switch with no typed key loads that provider's saved key, or clears the
    active one, so one vendor's key never goes to another vendor's server.
    """
    patch = dict(patch)
    cur = db.get_settings()
    old = providers.effective(cur)
    new = patch.get("provider") or old
    keys = load(db.secrets)
    switched = bool(new) and new != old
    if switched and old and cur.get("apiKey"):
        keys[old] = cur["apiKey"]
    if "apiKey" in patch:
        if new and patch["apiKey"]:
            keys[new] = patch["apiKey"]
        elif new:
            keys.pop(new, None)
    elif switched:
        patch["apiKey"] = keys.get(new) or None
    if switched and "baseUrl" not in patch:
        preset = providers.get(new)
        if preset and preset["baseUrl"]:
            patch["baseUrl"] = preset["baseUrl"]
    save(db.secrets, keys)
    return patch
