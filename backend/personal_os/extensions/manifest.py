"""extension.json: parsing and validation of one manifest (design doc section 3.1).

Unknown keys are rejected rather than stripped, so a typo fails loudly. Checks that need other extensions (id and
tool-name collisions, `requires`) are the host's.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hooks import HOOKS

API_VERSION = 1

_ID = re.compile(r"[a-z0-9-]+")
_SEMVER = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")
_ACTIVATION = re.compile(r"startup|onSchedule|(onView|onCommand|onChannel):.+")
_TOP_KEYS = {"id", "name", "description", "version", "apiVersion", "kind", "enabled", "activation", "requires", "provides",
             "contributes", "hooks", "permissions", "settings", "data"}
_CONTRIBUTES = {"tools", "routes", "views", "slots", "settings", "commands", "keybindings", "theme", "defaults", "layout"}
_HOOK_KEYS = {"priority", "timeoutMs", "onFailure"}
SETTING_TYPES = ("string", "number", "integer", "boolean", "secret")  # a secret is a string kept in the secret store
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_TABLE = re.compile(r"[a-z_][a-z0-9_]*")
_PERMISSIONS = {"db:own", "context:volatile", "conversation:read", "input:rewrite", "hooks:gate", "memory:gate",
                "prompt:inject", "ui:unsupported-css"}  # plus "service:<name>"


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    api_version: int
    description: str = ""
    kind: str = "extension"
    enabled: bool | str = True
    activation: tuple[str, ...] = ("startup",)
    requires_services: tuple[str, ...] = ()
    requires_extensions: tuple[str, ...] = ()
    provides_services: tuple[str, ...] = ()
    contributes: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, dict[str, Any]] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any) -> Manifest:
        if not isinstance(raw, dict):
            raise ManifestError("the manifest must be a JSON object")
        if unknown := sorted(set(raw) - _TOP_KEYS):
            raise ManifestError(f"unknown key(s): {', '.join(unknown)}")
        for key in ("id", "name", "version", "apiVersion"):
            if key not in raw:
                raise ManifestError(f"missing {key!r}")
        _check(isinstance(raw["id"], str) and _ID.fullmatch(raw["id"]), "id must match [a-z0-9-]+")
        _check(_is_str(raw["name"]), "name must be a non-empty string")
        _check(isinstance(raw["version"], str) and _SEMVER.fullmatch(raw["version"]), "version must be semver")
        api = raw["apiVersion"]
        _check(_is_int(api) and api >= 1, "apiVersion must be a positive integer")
        if api > API_VERSION:
            raise ManifestError(f"targets apiVersion {api}, this host speaks {API_VERSION}")
        _check(isinstance(raw.get("description", ""), str), "description must be a string")
        _check(raw.get("kind", "extension") in ("extension", "profile"), "kind must be 'extension' or 'profile'")
        _check(isinstance(raw.get("enabled", True), bool) or raw["enabled"] == "always", "enabled must be true, false or 'always'")
        activation = _strings(raw, "activation", ("startup",))
        _check(all(_ACTIVATION.fullmatch(a) for a in activation), "activation has an unknown event")
        requires, provides = _object(raw, "requires", {"services", "extensions"}), _object(raw, "provides", {"services"})
        permissions = _strings(raw, "permissions", ())
        _check(all(p in _PERMISSIONS or p.startswith("service:") for p in permissions), "permissions has an unknown capability")
        return cls(
            id=raw["id"], name=raw["name"], version=raw["version"], api_version=api,
            description=raw.get("description", ""), kind=raw.get("kind", "extension"), enabled=raw.get("enabled", True),
            activation=activation,
            requires_services=_strings(requires, "services", ()), requires_extensions=_strings(requires, "extensions", ()),
            provides_services=_strings(provides, "services", ()),
            contributes=_contributes(raw), hooks=_hooks(raw), permissions=permissions,
            settings=_settings(raw), data=_data(raw),
        )


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {path.name}: {e}") from e
    return Manifest.from_dict(raw)


def _check(ok: object, message: str) -> None:
    if not ok:
        raise ManifestError(message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def _strings(obj: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    v = obj.get(key, default)
    _check(isinstance(v, (list, tuple)) and all(_is_str(x) for x in v), f"{key} must be a list of strings")
    return tuple(v)


def _object(obj: dict[str, Any], key: str, allowed: set[str] | None = None) -> dict[str, Any]:
    v = obj.get(key, {})
    _check(isinstance(v, dict), f"{key} must be an object")
    if allowed is not None and (extra := sorted(set(v) - allowed)):
        raise ManifestError(f"{key} has unknown key(s): {', '.join(extra)}")
    return v


def _contributes(raw: dict[str, Any]) -> dict[str, Any]:
    c = _object(raw, "contributes", _CONTRIBUTES)
    tools = c.get("tools", [])
    _check(isinstance(tools, list) and all(isinstance(t, dict) and _is_str(t.get("name")) for t in tools),
           "contributes.tools must be a list of objects with a name")
    return c


def _settings(raw: dict[str, Any]) -> dict[str, Any]:
    fields = _object(raw, "settings")
    for name, spec in fields.items():
        _check(_FIELD.fullmatch(name), f"settings.{name}: the name must match [A-Za-z][A-Za-z0-9_]*")
        _check(isinstance(spec, dict) and set(spec) == {"type"} and spec["type"] in SETTING_TYPES,
               f"settings.{name} must be {{\"type\": one of {', '.join(SETTING_TYPES)}}}")
    return fields


def _data(raw: dict[str, Any]) -> dict[str, Any]:
    data = _object(raw, "data", {"tables", "migrations"})
    _check(all(_TABLE.fullmatch(t) for t in _strings(data, "tables", ())), "data.tables must be table names")
    _check(isinstance(data.get("migrations", ""), str), "data.migrations must be a folder name")
    return data


def _hooks(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hooks = _object(raw, "hooks")
    for name, opts in hooks.items():
        _check(name in HOOKS, f"hooks declares unknown hook {name!r}")
        _check(isinstance(opts, dict) and set(opts) <= _HOOK_KEYS, f"hooks.{name} takes only {', '.join(sorted(_HOOK_KEYS))}")
        _check(_is_int(opts.get("priority", 0)), f"hooks.{name}: priority must be an integer")
        _check(_is_int(opts.get("timeoutMs", 1)) and opts.get("timeoutMs", 1) > 0,
               f"hooks.{name}: timeoutMs must be a positive integer")
        _check(opts.get("onFailure", "ask") in ("ask", "deny"), f"hooks.{name}: onFailure may be 'ask' or 'deny', never 'allow'")
    return hooks
