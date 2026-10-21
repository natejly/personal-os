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

from ..permissions import CHOICES
from ..repos import EFFORTS
from ..skillbuild import _authority
from ..runtime.slots import PERMISSIONS as RUNTIME_PERMISSIONS, SLOTS
from .catalog import HOOKS

API_VERSION = 1
API_DEPRECATION_WINDOW = 1  # how many majors below API_VERSION a manifest may target before the host warns

ID = re.compile(r"[a-z][a-z0-9-]*")
_SEMVER = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")
_ACTIVATION = re.compile(r"startup|(onView|onCommand):.+")  # the events the host raises: a view opens, a slash command is typed
_TOP_KEYS = {"id", "name", "description", "version", "apiVersion", "kind", "enabled", "activation", "requires", "provides",
             "contributes", "hooks", "permissions", "settings", "data"}
_CONTRIBUTES = {"tools", "routes", "views", "slots", "settings", "commands", "keybindings", "theme", "defaults", "runtime"}
_RUNTIME_KEYS = {"slot", "id", "label", "meta"}
_HOOK_KEYS = {"priority", "timeoutMs", "onFailure"}
SETTING_TYPES = ("string", "number", "integer", "boolean", "secret")  # a secret is a string kept in the secret store
_SETTING_KEYS = {"type", "title", "description", "default", "enum", "minimum", "maximum"}  # the JSON Schema subset a settings field takes
_SETTING_PYTHON = {"string": (str,), "number": (int, float), "integer": (int,), "boolean": (bool,)}
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]*")
_TABLE = re.compile(r"[a-z_][a-z0-9_]*")
_DEFAULTS = {"views", "model", "effort", "theme", "permissions", "runtime"}
PERMISSION_MODES = CHOICES["permissionMode"]
_THEMES = ("dark", "light", "system")
_TOKEN = re.compile(r"--[A-Za-z0-9_-]+")
_CALL = re.compile(r"([\w-]*)\(")
_TOKEN_VALUE = re.compile(r"(?![\s\S]*/\*)[^;{}<>@\\]{1,200}")  # one declaration value: no way to open a rule, a comment, an import or an escape
_TOKEN_FUNCTIONS = {"", "rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color", "color-mix", "calc",
                    "var", "min", "max", "clamp", "linear-gradient", "radial-gradient", "conic-gradient"}  # none fetches anything
_PERMISSIONS = {"db:own", "context:volatile", "conversation:read", "input:rewrite", "hooks:gate", "memory:gate",
                "prompt:inject", "ui:unsupported-css", "coding:read", "terminal:control", *RUNTIME_PERMISSIONS}  # plus "service:<name>"


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
        _check(isinstance(raw["id"], str) and ID.fullmatch(raw["id"]), "id must match [a-z][a-z0-9-]*")
        _check(raw["id"] != "core", "id \"core\" is reserved for the host")
        _check(_is_str(raw["name"]), "name must be a non-empty string")
        _check(isinstance(raw["version"], str) and _SEMVER.fullmatch(raw["version"]), "version must be semver")
        api = raw["apiVersion"]
        _check(_is_int(api) and api >= 1, "apiVersion must be a positive integer")
        if api > API_VERSION:
            raise ManifestError(f"targets apiVersion {api}, this host speaks {API_VERSION}")
        _check(isinstance(raw.get("description", ""), str), "description must be a string")
        _check(raw.get("kind", "extension") == "extension", "kind must be 'extension'")
        _check(isinstance(raw.get("enabled", True), bool) or raw["enabled"] == "always", "enabled must be true, false or 'always'")
        activation = _strings(raw, "activation", ("startup",))
        _check(activation and all(_ACTIVATION.fullmatch(a) for a in activation), "activation takes startup, onView:<view id> or onCommand:<command name>")
        requires, provides = _object(raw, "requires", {"services", "extensions"}), _object(raw, "provides", {"services"})
        permissions = _strings(raw, "permissions", ())
        _check(all("." not in svc for svc in _strings(provides, "services", ())), "a service name has no dot: dotted names are runtime slots")
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


def stale_api_warning(manifest: Manifest) -> str | None:
    """A manifest that targets an apiVersion older than the deprecation window still loads; the author is told to move on."""
    if manifest.api_version < API_VERSION - API_DEPRECATION_WINDOW:
        return f"targets apiVersion {manifest.api_version}, which is past the deprecation window: this host speaks {API_VERSION}"
    return None


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {path.name}: {e}") from e
    manifest = Manifest.from_dict(raw)
    _check_static_text(path.parent, manifest)
    return manifest


def _check_static_text(root: Path, manifest: Manifest) -> None:
    """Static text the model will read (names, descriptions, command hints, tool schemas) must not claim authority: the check a skill's procedure gets."""
    texts = {"extension.json": [manifest.name, manifest.description, *(c.get("hint", "") for c in manifest.contributes.get("commands", ()))]}
    for tool in manifest.contributes.get("tools", ()):
        texts[f"tool {tool['name']}"] = [tool.get("description", "")]
        path = root / tool["schema"] if isinstance(tool.get("schema"), str) else None
        if path and path.is_file():
            try:
                texts[f"tool {tool['name']}"] += schema_descriptions(json.loads(path.read_text("utf-8")))
            except (ValueError, RecursionError):
                pass  # reported by the tool-schema lint
    found = [f"{where}: text that claims a permission or tells the assistant not to ask ({f['excerpt']})"
             for where, parts in texts.items() for text in parts if text for f in _authority(text, where)]
    if found:
        raise ManifestError("; ".join(found))


def schema_descriptions(schema: Any) -> list[str]:
    if isinstance(schema, dict):
        return [v for k, v in schema.items() if k == "description" and isinstance(v, str)] + [d for v in schema.values() for d in schema_descriptions(v)]
    if isinstance(schema, list):
        return [d for v in schema for d in schema_descriptions(v)]
    return []


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
    from ..tools import DEFAULT_MODE
    _check(all(_TOOL_NAME.fullmatch(t["name"]) for t in tools), "contributes.tools: a name must match [a-z][a-z0-9_]*")
    _check(len({t["name"] for t in tools}) == len(tools), "contributes.tools lists a name twice")
    _check(all(t["danger"] in DEFAULT_MODE for t in tools if "danger" in t), f"contributes.tools: danger must be one of {', '.join(DEFAULT_MODE)}")
    _check(all(isinstance(t.get("description", ""), str) for t in tools), "contributes.tools: description must be text")
    views = c.get("views", [])
    _check(isinstance(views, list) and all(isinstance(v, dict) and _is_str(v.get("id")) for v in views), "contributes.views must be a list of objects with an id")
    _check(len({v["id"] for v in views}) == len(views), "contributes.views lists an id twice")
    _check(all(_is_str(t["after"]) for t in tools if "after" in t), "contributes.tools 'after' must be the name of an existing tool")
    slots = c.get("slots", {})
    _check(isinstance(slots, dict) and all(isinstance(v, list) and all(isinstance(i, dict) for i in v) for v in slots.values()),
           "contributes.slots must map a slot name to a list of objects")
    _check(all(_is_str(i.get("id")) for i in slots.get("recap.section", ())), "contributes.slots['recap.section'] entries need an id")
    _check(all(_is_str(i.get("id")) and i["id"].startswith(f"ext:{raw.get('id')}:") for i in slots.get("canvas.widget", ())),
           "contributes.slots['canvas.widget'] entries need an id of the form ext:<extension id>:<name>")
    _commands(c)
    _activation(raw, c)
    _theme(c)
    _defaults(c)
    _runtime(c, raw.get("permissions", ()))
    return c


def _commands(c: dict[str, Any]) -> None:
    """contributes.commands: the slash commands the extension registers, listed before any code runs."""
    from ..commands import NAME_RE, RESERVED
    entries = c.get("commands", [])
    _check(isinstance(entries, list) and all(isinstance(e, dict) and set(e) <= {"name", "hint"} for e in entries),
           "contributes.commands must be a list of objects with a name and a hint")
    for e in entries:
        _check(isinstance(e.get("name"), str) and NAME_RE.match(e["name"]), "contributes.commands: name must be lowercase letters, digits, - or _ (max 40)")
        _check(e["name"] not in RESERVED, f"contributes.commands: /{e['name']} is one of the app's own commands")
        _check(isinstance(e.get("hint", ""), str), "contributes.commands: hint must be text")
    _check(len({e["name"] for e in entries}) == len(entries), "contributes.commands lists a name twice")


def _activation(raw: dict[str, Any], c: dict[str, Any]) -> None:
    """An extension whose code waits for an event names only contributions the manifest declares, and offers nothing that must exist
    before the event: a tool's schema comes from code and a service is looked up late."""
    events = raw.get("activation", ["startup"])
    if "startup" in events:
        return
    views = {v["id"] for v in c.get("views", ())}
    commands = {e.get("name") for e in c.get("commands", ())}
    for event in events:
        kind, _, target = event.partition(":")
        _check(target in (views if kind == "onView" else commands), f"activation {event!r} names a {'view' if kind == 'onView' else 'command'} the manifest does not declare")
    _check(not c.get("tools") and not _object(raw, "provides").get("services"), "an extension that activates on an event cannot contribute tools or provide services: they must exist at startup")


def _runtime(c: dict[str, Any], permissions: list[str]) -> None:
    """contributes.runtime: the slot providers the extension offers, listed before any code runs."""
    entries = c.get("runtime", [])
    _check(isinstance(entries, list) and all(isinstance(e, dict) for e in entries), "contributes.runtime must be a list of objects")
    seen: set[tuple[str, str]] = set()
    for e in entries:
        _check(set(e) <= _RUNTIME_KEYS and {"slot", "id", "label"} <= set(e), "contributes.runtime entries take slot, id, label and meta")
        _check(isinstance(e["slot"], str) and e["slot"] in SLOTS, f"contributes.runtime: {e['slot']!r} is not a runtime slot")
        _check(isinstance(e["id"], str) and ID.fullmatch(e["id"]), "contributes.runtime: id must match [a-z][a-z0-9-]*")
        _check(_is_str(e["label"]) and isinstance(e.get("meta", {}), dict), "contributes.runtime: label must be text and meta an object")
        _check((e["slot"], e["id"]) not in seen, f"contributes.runtime lists {e['slot']} {e['id']} twice")
        _check(SLOTS[e["slot"]].permission in permissions, f"providing {e['slot']} needs the permission {SLOTS[e['slot']].permission!r}")
        seen.add((e["slot"], e["id"]))


def _theme(c: dict[str, Any]) -> None:
    theme = c.get("theme", {})
    _check(isinstance(theme, dict) and set(theme) <= {"light", "dark"}, "contributes.theme takes only 'light' and 'dark'")
    for scheme, tokens in theme.items():
        _check(isinstance(tokens, dict), f"contributes.theme.{scheme} must be an object of tokens")
        for name, value in tokens.items():
            _check(_TOKEN.fullmatch(name), f"contributes.theme.{scheme}: {name!r} is not a custom property name")
            _check(isinstance(value, str) and _TOKEN_VALUE.fullmatch(value) and _TOKEN_FUNCTIONS.issuperset(_CALL.findall(value.lower())),
                   f"contributes.theme.{scheme}.{name} must be a plain CSS value")


def _defaults(c: dict[str, Any]) -> None:
    d = c.get("defaults", {})
    _check(isinstance(d, dict) and set(d) <= _DEFAULTS, f"contributes.defaults takes only {', '.join(sorted(_DEFAULTS))}")
    views = d.get("views", {})
    _check(isinstance(views, dict) and all(_is_str(k) and isinstance(v, bool) for k, v in views.items()),
           "defaults.views must map a view id to true or false")
    _check(_is_str(d.get("model", "x")), "defaults.model must be a model id")
    _check(d.get("effort", "high") in EFFORTS, f"defaults.effort must be one of {', '.join(EFFORTS)}")
    _check(d.get("theme", "dark") in _THEMES, f"defaults.theme must be one of {', '.join(_THEMES)}")
    _check(d.get("permissions", "auto") in PERMISSION_MODES, f"defaults.permissions must be one of {', '.join(PERMISSION_MODES)}")
    runtime = d.get("runtime", {})
    _check(isinstance(runtime, dict) and set(runtime) <= set(SLOTS), "defaults.runtime must map a runtime slot to a provider")
    for slot, value in runtime.items():
        if SLOTS[slot].multi:
            _check(isinstance(value, dict) and set(value) <= {"enable", "disable"} and all(isinstance(v, list) and all(_is_str(i) for i in v) for v in value.values()),
                   f"defaults.runtime.{slot} must be {{enable, disable}} lists of provider ids")
        else:
            _check(_is_str(value), f"defaults.runtime.{slot} must be a provider id")


def _settings(raw: dict[str, Any]) -> dict[str, Any]:
    fields = _object(raw, "settings")
    for name, spec in fields.items():
        _check(_FIELD.fullmatch(name), f"settings.{name}: the name must match [A-Za-z][A-Za-z0-9_]*")
        _check(isinstance(spec, dict) and set(spec) <= _SETTING_KEYS and spec.get("type") in SETTING_TYPES,
               f"settings.{name} must be an object with a type of {', '.join(SETTING_TYPES)}, and optionally {', '.join(sorted(_SETTING_KEYS - {'type'}))}")
        _setting_constraints(name, spec)
    return fields


def _setting_constraints(name: str, spec: dict[str, Any]) -> None:
    kind = spec["type"]
    _check(all(isinstance(spec.get(k, ""), str) for k in ("title", "description")), f"settings.{name}: title and description must be text")
    _check(kind != "secret" or not {"default", "enum"} & set(spec), f"settings.{name}: a secret has no default or enum")
    _check(kind in ("number", "integer") or not {"minimum", "maximum"} & set(spec), f"settings.{name}: minimum and maximum are for numbers")
    allowed = _SETTING_PYTHON.get(kind, ())

    def fits(v: Any) -> bool:
        return isinstance(v, allowed) and (kind == "boolean" or not isinstance(v, bool))
    _check(all(fits(spec[k]) for k in ("minimum", "maximum") if k in spec), f"settings.{name}: minimum and maximum must be {kind}s")
    if "enum" in spec:
        _check(isinstance(spec["enum"], list) and spec["enum"] and all(fits(v) for v in spec["enum"]), f"settings.{name}: enum must be a non-empty list of {kind}s")
    if "default" in spec:
        v = spec["default"]
        _check(fits(v), f"settings.{name}: default must be a {kind}")
        _check("enum" not in spec or v in spec["enum"], f"settings.{name}: default must be one of enum")
        _check(spec.get("minimum", v) <= v <= spec.get("maximum", v), f"settings.{name}: default is outside minimum and maximum")


def _data(raw: dict[str, Any]) -> dict[str, Any]:
    data = _object(raw, "data", {"tables", "migrations"})
    _check(all(_TABLE.fullmatch(t) for t in _strings(data, "tables", ())), "data.tables must be table names")
    folder = data.get("migrations", "")
    _check(isinstance(folder, str) and not Path(folder).is_absolute() and ".." not in Path(folder).parts,
           "data.migrations must be a folder inside the extension")
    return data


def _hooks(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hooks = _object(raw, "hooks")
    permissions = _strings(raw, "permissions", ())
    for name, opts in hooks.items():
        _check(name in HOOKS, f"hooks declares unknown hook {name!r}")
        _check(all(p in permissions for p in HOOKS[name].permission),
               f"hooks.{name} needs the permission(s) {', '.join(HOOKS[name].permission)} in permissions")
        _check(isinstance(opts, dict) and set(opts) <= _HOOK_KEYS, f"hooks.{name} takes only {', '.join(sorted(_HOOK_KEYS))}")
        _check(_is_int(opts.get("priority", 0)), f"hooks.{name}: priority must be an integer")
        _check(_is_int(opts.get("timeoutMs", 1)) and opts.get("timeoutMs", 1) > 0,
               f"hooks.{name}: timeoutMs must be a positive integer")
        _check(opts.get("onFailure", "ask") in ("ask", "deny"), f"hooks.{name}: onFailure may be 'ask' or 'deny', never 'allow'")
    return hooks
