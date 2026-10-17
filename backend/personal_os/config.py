"""The settings the app runs on, and where every value came from (design doc 10.8.3).

`assemble` builds the settings dict from a database (`app.settings()` is a call to it); `effective` lays the same settings
out by key with the source of each value (default, profile:<id>, ext:<id>, preset:<id>, user, project, chat), the
modes of the tools, and which secrets are set. Neither imports the app, so `grain config dump` runs against a data folder
with no server. Secrets are reported as "set" or "unset" and nothing else.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from . import llm, permissions, provider_keys, provider_route, providers, setting_sources
from .db import SECRET_SETTINGS
from .extensions.host import SETTING_PREFIX
from .extensions.manifest import Manifest
from .repos import Conversations, Projects

# Google OAuth material lives in settings but never leaves the backend.
PRIVATE_SETTINGS = {"googleToken", "googleAuthPending", "modelCaps", "telegramState", "extDefaults"}


def assemble(db: Any) -> dict[str, Any]:
    """Defaults < stored rows, with the permissions store (permissions.py) both nested under `permissions` and flattened
    to the top level, so a reader that still does cfg.get("tools") sees the same value as permissions.get(cfg, "tools")."""
    stored = db.get_settings()
    perms = permissions.load(stored)
    out = {**llm.DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k not in permissions.KEYS},
           **perms, permissions.KEY: {"version": permissions.VERSION, **perms}}
    if out.get("providers"):
        # The first usable provider stands in for the old single "active" one, for readers that still want a baseUrl and key
        # (speech to text, usage import, setup cards); a model's own provider is resolved per call (provider_route.resolve).
        first = next(iter(provider_route.usable(out)), None)
        out.update(provider=first["kind"] if first else None, baseUrl=first["baseUrl"] if first else "", apiKey=first["apiKey"] if first else "")
    if not out.get("defaultModel"):  # nothing saved: the provider's chat default (a saved model always wins)
        out["defaultModel"] = providers.default_model(out)
    # The legacy helper and fast knobs are the low and medium tiers (providers.TASK_TIERS); a stored value still wins.
    if not out.get("extractionModel"):
        out["extractionModel"] = providers.tier_model(out, "low")
    if not out.get("fastModel"):
        fast = providers.tier_model(out, "medium")
        out["fastModel"] = "" if fast == out["defaultModel"] else fast  # same model: nothing for Auto to route to
    return out


def declared_settings(manifests: Iterable[Manifest]) -> dict[str, str]:
    """{`ext.<id>.<field>`: type} for every setting the manifests declare."""
    return {f"{SETTING_PREFIX}{m.id}.{name}": spec["type"] for m in manifests for name, spec in m.settings.items()}


def _scoped(project: dict[str, Any] | None, conv: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    return (project or {}).get("permissions") or {}, ((conv or {}).get("settings") or {}).get("permissions") or {}


def effective(db: Any, tools: Any, declared: dict[str, str], *, project_id: str | None = None,
              conversation_id: str | None = None) -> dict[str, Any]:
    """The settings in force for a project and a chat, each with where it came from. `tools` is a Toolbox (explain) and
    `declared` the extension settings (declared_settings). A key written before sources were recorded is the user's, flagged `legacy`."""
    stored = db.get_settings()
    cfg = assemble(db)
    with db.tx() as c:
        sources = setting_sources.read(c)
    conv = Conversations(db).get(conversation_id, with_messages=False) if conversation_id else None
    project_of = (conv or {}).get("project_id") or project_id
    project = Projects(db).get(project_of) if project_of else None
    project_perms, chat_perms = _scoped(project, conv)
    present = {k for k in stored if k != permissions.KEY and k not in permissions.KEYS} | permissions.stored_keys(stored)
    secret_keys = {*SECRET_SETTINGS, *(k for k, t in declared.items() if t == "secret")}

    values: dict[str, Any] = {}
    for key in sorted({*llm.DEFAULT_SETTINGS, *permissions.DEFAULTS, *declared} - PRIVATE_SETTINGS - secret_keys):
        default = permissions.DEFAULTS[key] if key in permissions.DEFAULTS else llm.DEFAULT_SETTINGS.get(key)
        row = sources.get((key, "global", ""))
        if key not in present:
            source, legacy = "default", False
        else:
            source, legacy = (row["source"], False) if row else ("user", True)
        entry: dict[str, Any] = {"value": cfg.get(key, default), "source": source, "scope": "global", "shadowed": []}
        if legacy:
            entry["legacy"] = True
        if source != "default":
            entry["shadowed"].append({"source": "default", "value": default})
        if row and row["prev"] is not None:
            entry["shadowed"].append({"source": "previous", "value": row["prev"]})
        for scope, scope_id, layer in (("project", (project or {}).get("id", ""), project_perms), ("chat", conversation_id or "", chat_perms)):
            if key in permissions.SCOPED_KEYS and key in layer:
                entry["shadowed"].append({"source": entry["source"], "value": entry["value"]})
                own = sources.get((key, scope, scope_id))
                entry.update(value=permissions.narrowed(key, entry["value"], layer[key]), source=own["source"] if own else scope, scope=scope)
        values[key] = entry

    global_tools = permissions.get(cfg, "tools") or {}
    return {
        "scope": {"project_id": project_id, "conversation_id": conversation_id},
        "values": values,
        "tools": tools.explain(global_tools, (project or {}).get("tools"), ((conv or {}).get("settings") or {}).get("tools")),
        "runtime": {},
        "secrets": {**{k: "set" if db.secrets.get(k) or cfg.get(k) else "unset" for k in sorted(secret_keys)},
                    "providerKeys": {e["id"]: "set" if e["id"] in provider_keys.saved_for(db.secrets) else "unset"
                                     for e in provider_route.entries(cfg)}},
    }


def render(dump: dict[str, Any]) -> str:
    """The dump as a table grouped by source: key, value, scope. Values are JSON, cut at 80 characters."""
    rows = [(e["source"], k, json.dumps(e["value"], ensure_ascii=False, default=str), e["scope"]) for k, e in dump["values"].items()]
    lines: list[str] = []
    for source in sorted({r[0] for r in rows}, key=lambda s: (s != "default", s)):
        lines.append(f"[{source}]")
        lines += [f"  {k:<34} {v[:80]:<80} {scope}" for src, k, v, scope in rows if src == source]
    lines.append("[tools]")
    lines += [f"  {n:<34} {t['mode']:<6} {t['source']}" for n, t in sorted(dump["tools"].items())]
    lines.append("[secrets]")
    lines += [f"  {k:<34} {v}" for k, v in dump["secrets"].items() if isinstance(v, str)]
    lines += [f"  providerKeys.{k:<20} {v}" for k, v in dump["secrets"]["providerKeys"].items()]
    return "\n".join(lines)
