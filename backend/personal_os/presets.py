"""Named permission presets (design doc 10.8.1): bundles of permission settings that apply at global, project or chat scope.

A preset is a JSON document (`grain.preset/1`): the permission mode, rules, always-ask list, destructive and unattended
defaults, a tool table by group, danger tier or name, connector modes (ask or off, never on) and the network keys. It is
validated on save, import and apply. Applying computes the change set against what is in force at that scope; a change that
lets more run without a card, or reach out further, is held until the caller quotes the diff's hash. The floor, the hardline
list and the protected paths have no field here and are enforced on top of every preset.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import permissions, setting_sources
from .db import Database
from .extensions.hooks import HookBus
from .mcp_client import MCP_DANGER
from .mcp_servers import MODES as MCP_MODES, McpServers
from .repos import Conversations, Projects
from .tools import DEFAULT_MODE, Toolbox

SCHEMA = "grain.preset/1"
MAX_BYTES = 64 * 1024
ID = re.compile(r"builtin:[a-z0-9-]+|user:[a-z0-9-]+|profile:[a-z0-9-]+:[a-z0-9-]+")
SLUG = re.compile(r"[a-z0-9-]+")
NETWORK_KEYS = ("shellNetwork", "shellRegistryAccess", "shellAllowedDomains", "fetchAllowlist")
_FIELDS = {"schema", "id", "name", "description", "tools", "connectors", "network", *permissions.SCOPED_KEYS}
_TOOL_TABLES = {"groups": None, "danger": tuple(DEFAULT_MODE), "tools": None}  # the allowed keys of each table, None for any name
SCOPES = ("global", "project", "chat")
NAME_MAX = 80

# What a fresh install has, so applying one of these to it changes nothing (permissions.DEFAULTS).
_SAFE_DEFAULTS = {"destructiveDefaults": permissions.DEFAULTS["destructiveDefaults"], "unattendedApprovals": permissions.DEFAULTS["unattendedApprovals"]}
BUILTINS: dict[str, dict[str, Any]] = {
    "builtin:auto": {"name": "Auto", "description": "A reviewer model reads every call that is not known safe.",
                     "permissionMode": "auto", **_SAFE_DEFAULTS},
    "builtin:manual": {"name": "Manual", "description": "Per-tool modes and rules alone; destructive actions are refused by default.",
                       "permissionMode": "manual", **_SAFE_DEFAULTS},
    "builtin:allow-all": {"name": "Allow everything", "description": "Runs everything without a card, except the floor.",
                          "permissionMode": "allow_all", **_SAFE_DEFAULTS},
    "builtin:read-only": {"name": "Read only", "description": "Manual mode with every tool that writes, runs, reaches out or schedules turned off.",
                          "permissionMode": "manual",
                          "tools": {"danger": {"writes": "off", "executes": "off", "external": "off", "schedules": "off"}}},
}


class PresetError(ValueError):
    """A refused preset operation; the route turns it into a 4xx."""


@dataclass
class Plan:
    changes: dict[str, dict[str, Any]]  # key -> {"old", "new", "loosens"}
    not_applied: list[str]              # keys this scope cannot hold
    pending: list[str]                  # always-ask, tool and group names nothing loaded has (kept, not dropped)

    @property
    def loosened(self) -> list[str]:
        return [k for k, c in self.changes.items() if c["loosens"]]

    def digest(self, preset_id: str, scope: str, scope_id: str) -> str:
        blob = json.dumps({"preset": preset_id, "scope": scope, "scope_id": scope_id, "changes": self.changes,
                           "not_applied": self.not_applied, "pending": self.pending}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def to_dict(self, preset_id: str, scope: str, scope_id: str) -> dict[str, Any]:
        return {"changes": self.changes, "not_applied": self.not_applied, "pending": self.pending, "loosened": self.loosened,
                "diff_hash": self.digest(preset_id, scope, scope_id)}


class Held(Exception):
    """The preset loosens something and the caller has not quoted the diff hash."""

    def __init__(self, diff: dict[str, Any]) -> None:
        super().__init__("this preset loosens permissions")
        self.diff = diff


def validate(doc: Any, *, rule_check: Callable[[Any], None] | None = None) -> dict[str, Any]:
    """The preset as it may be stored, or PresetError. The same check runs on create, edit, import and apply."""
    if not isinstance(doc, dict):
        raise PresetError("a preset is a JSON object")
    if len(json.dumps(doc, ensure_ascii=False).encode()) > MAX_BYTES:
        raise PresetError("a preset is at most 64 KB")
    if bad := sorted(set(doc) - _FIELDS):
        raise PresetError(f"unknown preset field {bad[0]!r}")
    if doc.get("schema", SCHEMA) != SCHEMA:
        raise PresetError(f"schema must be {SCHEMA!r}")
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
        raise PresetError(f"a preset needs a name of at most {NAME_MAX} characters")
    if not isinstance(doc.get("description", ""), str) or len(doc.get("description", "")) > 500:
        raise PresetError("description must be text of at most 500 characters")
    if "id" in doc and not (isinstance(doc["id"], str) and ID.fullmatch(doc["id"])):
        raise PresetError("id must look like user:<slug>, builtin:<slug> or profile:<profile>:<slug> with [a-z0-9-]")
    out: dict[str, Any] = {"schema": SCHEMA, "name": name.strip(), "description": doc.get("description", "")}
    if "id" in doc:
        out["id"] = doc["id"]
    try:
        for key in permissions.SCOPED_KEYS:
            if key in doc:
                if key == "permissionRules" and isinstance(doc[key], dict) and (bad := sorted(set(doc[key]) - {"allow", "ask", "deny"})):
                    raise PresetError(f"unknown permissionRules key {bad[0]!r}; use allow, ask or deny")
                out[key] = permissions.validate(key, doc[key], rule_check=rule_check)
        if "network" in doc:
            net = doc["network"]
            if not isinstance(net, dict) or set(net) - set(NETWORK_KEYS):
                raise PresetError(f"network takes only {', '.join(NETWORK_KEYS)}")
            out["network"] = {k: permissions.validate(k, v) for k, v in net.items()}
    except ValueError as e:
        raise PresetError(str(e)) from e
    if "tools" in doc:
        out["tools"] = _tool_tables(doc["tools"])
    if "connectors" in doc:
        con = doc["connectors"]
        if not isinstance(con, dict) or any(v not in ("ask", "off") for v in con.values()):
            raise PresetError("connectors maps a server id to 'ask' or 'off'; a preset can never switch a connector on")
        out["connectors"] = dict(con)
    return out


def _tool_tables(tools: Any) -> dict[str, dict[str, str]]:
    if not isinstance(tools, dict) or set(tools) - set(_TOOL_TABLES):
        raise PresetError("tools takes groups, danger and tools")
    out: dict[str, dict[str, str]] = {}
    for table, allowed in _TOOL_TABLES.items():
        entries = tools.get(table, {})
        if not isinstance(entries, dict) or any(v not in MCP_MODES for v in entries.values()):
            raise PresetError(f"tools.{table} maps a name to 'on', 'ask' or 'off'")
        if allowed is not None and (unknown := sorted(set(entries) - set(allowed))):
            raise PresetError(f"tools.danger has no tier {unknown[0]!r}; use {', '.join(allowed)}")
        if entries:
            out[table] = dict(entries)
    return out


def tool_modes(tools: dict[str, dict[str, str]], toolbox: Toolbox) -> dict[str, str]:
    """The per-tool map a tools section means today: danger tier, then group, then name, the most specific last. A tool
    added later is covered by its tier or group the next time the preset is applied. Built-in tools only: connector and
    extension-source tools are read by `Presets._connector_modes`."""
    out: dict[str, str] = {}
    for name, spec in toolbox.specs.items():
        for table, key in (("danger", spec.danger), ("groups", spec.group), ("tools", name)):
            if (mode := tools.get(table, {}).get(key)):
                out[name] = mode
    return toolbox.cap_modes(out)


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "preset"


class Presets:
    def __init__(self, db: Database, toolbox: Toolbox, mcp: McpServers, projects: Projects, convos: Conversations,
                 shipped: Callable[[], list[tuple[str, str, dict[str, Any]]]], rule_check: Callable[[Any], None],
                 bus: HookBus, source_slugs: Callable[[], set[str]]) -> None:
        self.db, self.toolbox, self.mcp, self.projects, self.convos = db, toolbox, mcp, projects, convos
        self.source_slugs = source_slugs  # the tools extension sources have listed; like MCP tools they are not in toolbox.specs
        self.shipped, self.rule_check, self.bus = shipped, rule_check, bus  # shipped: [(profile id, profile name, preset)]

    # ---- the list
    def catalog(self) -> list[dict[str, Any]]:
        rows = [{"id": i, "source": "builtin", "owner": None, "readonly": True, "body": {"schema": SCHEMA, **b}, **_head(b)} for i, b in BUILTINS.items()]
        with self.db.tx() as c:
            for r in c.execute("SELECT id, name, body, source FROM permission_presets ORDER BY created_at, id"):
                body = json.loads(r["body"])
                rows.append({"id": r["id"], "source": r["source"], "owner": None, "readonly": False, "body": body, **_head(body)})
        for profile_id, profile_name, doc in self.shipped():
            body = {"schema": SCHEMA, **doc}
            rows.append({"id": f"profile:{profile_id}:{doc['id']}", "source": "profile", "owner": profile_name, "readonly": True,
                         "body": {**body, "id": f"profile:{profile_id}:{doc['id']}"}, **_head(doc)})
        return rows

    def get(self, preset_id: str) -> dict[str, Any]:
        row = next((r for r in self.catalog() if r["id"] == preset_id), None)
        if row is None:
            raise KeyError(preset_id)
        return row

    # ---- edit
    def save(self, doc: Any, *, source: str = "user", preset_id: str | None = None) -> dict[str, Any]:
        """Create a preset (a new `user:<slug>` id) or, with `preset_id`, replace one of yours."""
        body = validate(doc, rule_check=self.rule_check)
        body.pop("id", None)
        t = time.time()
        if preset_id is None:
            taken = {r["id"] for r in self.catalog()}
            base, n = slug_of(body["name"]), 1
            while (preset_id := f"user:{base}" + (f"-{n}" if n > 1 else "")) in taken:
                n += 1
            with self.db.tx() as c:
                c.execute("INSERT INTO permission_presets(id, name, body, source, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                          (preset_id, body["name"], json.dumps(body), source, t, t))
        else:
            self._mutable(preset_id)
            with self.db.tx() as c:
                c.execute("UPDATE permission_presets SET name=?, body=?, updated_at=? WHERE id=?", (body["name"], json.dumps(body), t, preset_id))
        return self.get(preset_id)

    def duplicate(self, preset_id: str) -> dict[str, Any]:
        src = self.get(preset_id)
        return self.save({**src["body"], "name": f"{src['name'][:NAME_MAX - 5]} copy"})

    def delete(self, preset_id: str) -> None:
        self._mutable(preset_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM permission_presets WHERE id=?", (preset_id,))

    def export(self, preset_id: str) -> dict[str, Any]:
        row = self.get(preset_id)
        return {k: v for k, v in row["body"].items() if k != "id"}

    def _mutable(self, preset_id: str) -> None:
        if not preset_id.startswith("user:"):
            raise PresetError("built-in and profile presets are read-only; duplicate one to change it")
        self.get(preset_id)

    def keys(self, preset_id: str) -> dict[str, Any]:
        """The permission values the preset sets at global scope, tools resolved to the per-tool map they would become."""
        body = validate(self.get(preset_id)["body"], rule_check=self.rule_check)
        out = {k: body[k] for k in permissions.SCOPED_KEYS if k in body} | body.get("network", {})
        if "tools" in body:
            out["tools"] = {**(permissions.get(permissions.load(self.db.get_settings()), "tools") or {}), **tool_modes(body["tools"], self.toolbox)}
        return out

    # ---- apply
    def plan(self, preset_id: str, scope: str, scope_id: str = "") -> Plan:
        """What applying the preset would change at this scope, against what is in force there now."""
        if scope not in SCOPES:
            raise PresetError(f"scope must be one of {', '.join(SCOPES)}")
        body = validate(self.get(preset_id)["body"], rule_check=self.rule_check)
        project, conv = self._rows(scope, scope_id)
        stored = self.db.get_settings()
        wide = permissions.load(stored)
        have = {"project": (project or {}).get("permissions") or {}, "chat": ((conv or {}).get("settings") or {}).get("permissions") or {}}
        wants = {k: body[k] for k in permissions.SCOPED_KEYS if k in body}
        net = body.get("network", {}) if scope == "global" else {}
        changes: dict[str, dict[str, Any]] = {}

        def note(key: str, old: Any, new: Any, loosens: bool) -> None:
            if old != new:
                changes[key] = {"old": old, "new": new, "loosens": loosens}

        before = permissions.overlay(wide, *_in_force(scope, have))
        after_layers = {**have, scope: {**have.get(scope, {}), **wants}} if scope != "global" else have
        after = permissions.overlay({**wide, **(wants if scope == "global" else {}), **net}, *_in_force(scope, after_layers))
        for key in (*wants, *net):
            old, new = permissions.get(before, key), permissions.get(after, key)
            note(key, old, new, bool(permissions.loosening({key: old}, {key: new})))
        self._tool_changes(body, scope, project, conv, wide, note)
        self._connector_changes(body, project, conv, note)
        return Plan(changes, [k for k in body.get("network", {}) if scope != "global"], self._unknown_names(body))

    def _unknown_names(self, body: dict[str, Any]) -> list[str]:
        """Names the preset uses that nothing loaded has: they may belong to something not loaded yet, or be a typo."""
        tools = body.get("tools", {})
        known = set(self.toolbox.specs) | {t["slug"] for t in self.mcp.tools()} | self.source_slugs()
        groups = {s.group for s in self.toolbox.specs.values()}
        return [*(n for n in body.get("alwaysAsk", []) if n not in self.toolbox.specs),
                *(f"tools.{n}" for n in tools.get("tools", {}) if n not in known),
                *(f"groups.{g}" for g in tools.get("groups", {}) if g not in groups)]

    def _rows(self, scope: str, scope_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if scope == "global":
            return None, None
        row = (self.projects if scope == "project" else self.convos).get(scope_id, **({} if scope == "project" else {"with_messages": False}))
        if row is None:
            raise KeyError(f"no such {scope}")
        if scope == "project":
            return row, None
        return (self.projects.get(row["project_id"]) if row.get("project_id") else None), row

    def _tool_changes(self, body: dict[str, Any], scope: str, project: dict[str, Any] | None, conv: dict[str, Any] | None,
                      wide: dict[str, Any], note: Callable[[str, Any, Any, bool], None]) -> None:
        if "tools" not in body:
            return
        wanted = tool_modes(body["tools"], self.toolbox)
        layers = {"global": permissions.get(wide, "tools") or {}, "project": (project or {}).get("tools") or {},
                  "chat": ((conv or {}).get("settings") or {}).get("tools") or {}}
        old = self.toolbox.effective(layers["global"], *_in_force(scope, layers))
        layers[scope] = {**layers[scope], **wanted}
        new = self.toolbox.effective(layers["global"], *_in_force(scope, layers))
        for name in wanted:
            note(f"tools.{name}", old[name], new[name], permissions.MODE_RANK[new[name]] > permissions.MODE_RANK[old[name]])

    def _connector_modes(self, body: dict[str, Any]) -> dict[str, str]:
        """The mode each connector tool gets: the tools table read against MCP and extension-source tools (all external, in no
        group; a connector is never switched on), then the connectors section."""
        out: dict[str, str] = {}
        table = body.get("tools", {})
        for slug in [*(t["slug"] for t in self.mcp.tools()), *self.source_slugs()]:
            if mode := table.get("tools", {}).get(slug) or table.get("danger", {}).get(MCP_DANGER):
                out[slug] = "ask" if mode == "on" else mode
        for server, mode in body.get("connectors", {}).items():
            out.update({t["slug"]: mode for t in self.mcp.tools(server)})
        return out

    def _connector_changes(self, body: dict[str, Any], project: dict[str, Any] | None, conv: dict[str, Any] | None,
                           note: Callable[[str, Any, Any, bool], None]) -> None:
        for slug, mode in self._connector_modes(body).items():
            now = self.mcp.effective_mode(slug, (project or {}).get("id"), (conv or {}).get("id"))["mode"]
            note(f"connectors.{slug}", now, mode, permissions.MODE_RANK[mode] > permissions.MODE_RANK[now])

    def apply(self, preset_id: str, scope: str, scope_id: str = "", confirm: str | None = None, by: str = "user") -> dict[str, Any]:
        """Apply the preset. Raises Held (with the diff) when it loosens anything and `confirm` is not the diff's hash."""
        plan = self.plan(preset_id, scope, scope_id)
        diff = plan.to_dict(preset_id, scope, scope_id)
        if plan.loosened and confirm != diff["diff_hash"]:
            raise Held(diff)
        body = validate(self.get(preset_id)["body"], rule_check=self.rule_check)
        self._write(body, preset_id, scope, scope_id, plan)
        changed = sorted({k.split(".")[0] if k.startswith(("tools.", "connectors.")) else k for k in plan.changes})
        self.bus.fire("preset.applied", {"preset_id": preset_id, "scope": scope, "scope_id": scope_id, "changed_keys": changed,
                                     "loosened": plan.loosened, "by": by})
        return diff

    def _write(self, body: dict[str, Any], preset_id: str, scope: str, scope_id: str, plan: Plan) -> None:
        """Store what the plan says changes, and only that: a key that already holds the preset's value keeps its source."""
        source = f"preset:{preset_id}"
        wants = {k: body[k] for k in permissions.SCOPED_KEYS if k in plan.changes} | {
            k: v for k, v in body.get("network", {}).items() if k in plan.changes}
        tools = {n: m for n, m in (tool_modes(body["tools"], self.toolbox) if "tools" in body else {}).items() if f"tools.{n}" in plan.changes}
        if wants or tools:
            self._store(scope, scope_id, wants, tools, source)
        for key in plan.changes:
            if key.startswith("connectors."):
                self.mcp.set_grant(key.removeprefix("connectors."), plan.changes[key]["new"], scope, scope_id, granted_by=source)

    def _store(self, scope: str, scope_id: str, wants: dict[str, Any], tools: dict[str, str], source: str) -> None:
        if scope == "global":
            prev = {k: v for k, v in permissions.load(self.db.get_settings()).items() if k in (*wants, "tools")}
            permissions.update(self.db, lambda cur: {**wants, **({"tools": {**cur["tools"], **tools}} if tools else {})}, source=source, prev=prev)
            return
        if scope == "project":
            have = self.projects.get(scope_id) or {}
            self.projects.update(scope_id, {"permissions": {**have.get("permissions", {}), **wants}, "tools": {**have.get("tools", {}), **tools}})
        else:
            have = (self.convos.get(scope_id, with_messages=False) or {}).get("settings") or {}
            self.convos.update(scope_id, {"settings": {"permissions": {**have.get("permissions", {}), **wants}, "tools": {**have.get("tools", {}), **tools}}})
        with self.db.tx() as c:
            setting_sources.stamp(c, [*wants, *(["tools"] if tools else [])], source, scope=scope, scope_id=scope_id)


def _in_force(scope: str, layers: dict[str, Any]) -> tuple[Any, Any]:
    """(project layer, chat layer) as they apply at `scope`: None for a layer narrower than the scope."""
    return (layers["project"] if scope != "global" else None, layers["chat"] if scope == "chat" else None)


def _head(body: dict[str, Any]) -> dict[str, Any]:
    return {"name": body["name"], "description": body.get("description", "")}
