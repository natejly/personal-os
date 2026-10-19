"""permissions_update: the assistant proposes a change to what needs the user's approval; the user's click applies it.

The tool is a settings write through the one permission store (Toolbox.permission_store -> permissions.update), so every
value passes the same validation as PUT /settings. Its approval card is the SETTINGS_KIND floor (permissions.floor_action):
a card in every permission mode, never reviewed, never lifted by a grant, rule or per-tool mode, never offered to a worker.
Hardline refusals and the destructive-shell card in Auto and Allow everything are code, not settings, so nothing here reaches them.
"""
from __future__ import annotations

from typing import Any, Callable

from . import permissions
from .tools import ToolSpec, _obj, tool_error

NAME = "permissions_update"
KINDS = ("mode", "destructive", "tool", "rule")
MODE_NAMES = {"auto": "Auto", "manual": "Manual", "allow_all": "Allow everything"}
TOOL_MODE_WORDS = {"on": "runs without asking", "ask": "asks every time", "off": "turned off"}
ACTION_WORDS = {"block": "Block", "ask": "Ask", "allow": "Allow"}
REPLACED_KEYS = {"permissionMode": "Permission mode", "destructiveDefaults": "Destructive-action defaults"}  # a project's or chat's value replaces the global one


def build(cfg: dict[str, Any], tb: Any, kind: str, value: str, target: str, remove: bool) -> tuple[dict[str, Any], list[str]]:
    """(the permission keys to store, plain-language before -> after lines) for one requested change; ValueError says why not.
    The write is global: a line says so when this chat or its project sets the same key itself, which then still applies here."""
    out, lines = _build(cfg, tb, kind, value, target, remove)
    layers = [layer for layer in permissions.SCOPE.get() or () if layer]
    lines += [f"{label} is also set for this chat or its project, which keeps its own value here"
              for key, label in REPLACED_KEYS.items() if key in out and any(key in layer for layer in layers)]
    return out, lines


def _build(cfg: dict[str, Any], tb: Any, kind: str, value: str, target: str, remove: bool) -> tuple[dict[str, Any], list[str]]:
    value, target = str(value or "").strip(), str(target or "").strip()
    if kind == "mode":
        if value not in MODE_NAMES:
            raise ValueError("value must be 'auto', 'manual' or 'allow_all'")
        before = permissions.get(cfg, "permissionMode")
        return {"permissionMode": value}, [f"Permission mode: {MODE_NAMES.get(before, before)} -> {MODE_NAMES[value]}"]
    if kind == "destructive":
        if target not in permissions.DESTRUCTIVE_KINDS or value not in permissions.DESTRUCTIVE_ACTIONS:
            raise ValueError("target must be one of " + ", ".join(permissions.DESTRUCTIVE_KINDS) + " and value 'block', 'ask' or 'allow'")
        cur = {**permissions.DEFAULTS["destructiveDefaults"], **(permissions.get(cfg, "destructiveDefaults") or {})}
        return ({"destructiveDefaults": {**cur, target: value}},
                [f"{permissions.KIND_WORDS[target].capitalize()} (Manual mode): {ACTION_WORDS[cur[target]]} -> {ACTION_WORDS[value]}"])
    if kind == "tool":
        spec = tb.specs.get(target)
        if spec is None or value not in TOOL_MODE_WORDS:
            raise ValueError("target must be a built-in tool name and value 'on', 'ask' or 'off'")
        tools = dict(permissions.get(cfg, "tools") or {})
        was = _norm(tools.get(target)) or tb.default_mode(spec)
        now = tb.cap_modes({target: value})[target]  # an ask-locked tool tops out at ask
        return {"tools": {**tools, target: now}}, [f"{target}: {TOOL_MODE_WORDS[was]} -> {TOOL_MODE_WORDS[now]}"]
    if kind == "rule":
        if target not in ("allow", "ask", "deny") or not value:
            raise ValueError("target must be 'allow', 'ask' or 'deny' and value a rule such as Bash(git status *)")
        rules = {k: list((permissions.get(cfg, "permissionRules") or {}).get(k) or []) for k in ("allow", "ask", "deny")}
        if remove:
            if value not in rules[target]:
                raise ValueError(f"there is no {target} rule {value!r} to remove")
            rules[target].remove(value)
            return {"permissionRules": rules}, [f"Remove the {target} rule {value}"]
        if value not in rules[target]:
            rules[target].append(value)
        return {"permissionRules": rules}, [f"Add the {target} rule {value}"]
    raise ValueError("kind must be one of " + ", ".join(KINDS))


def _norm(v: Any) -> str | None:
    return v if v in TOOL_MODE_WORDS else None


def preview(tb: Any, args: dict[str, Any]) -> list[str] | None:
    """The card's before -> after lines, from the global settings as they are now (the write is global); None when the arguments would not apply."""
    token = permissions.SCOPE.set(None)
    try:
        cfg = tb.settings()
    finally:
        permissions.SCOPE.reset(token)
    try:
        return build(cfg, tb, str(args.get("kind") or ""), args.get("value"), args.get("target"), bool(args.get("remove")))[1]
    except (ValueError, TypeError):
        return None


def register(tb: Any) -> None:
    async def permissions_update(ctx: dict[str, Any], kind: str, value: str = "", target: str = "", remove: bool = False) -> Any:
        store: Callable[..., Any] | None = getattr(tb, "permission_store", None)
        if store is None or ctx.get("proposal_only"):
            return tool_error("Permission settings cannot be changed from here.")
        lines: list[str] = []

        def patch(cur: dict[str, Any]) -> dict[str, Any]:
            out, lines[:] = build(cur, tb, kind, value, target, remove)
            return out
        try:
            store(patch)
        except ValueError as e:
            return tool_error(f"permissions_update: {e}", alternative="tell the user which setting to change in Settings > Permissions")
        return {"ok": True, "changed": lines, "note": "Applied after the user's approval. Say plainly what changed."}

    tb.specs[NAME] = ToolSpec(
        NAME,
        "Propose a change to what needs the user's approval. Use it when the user is frustrated by prompts or asks to change what "
        "needs approval (\"stop asking me about calendar edits\", \"let it send mail\", \"switch to allow all\"). Propose the "
        "narrowest change that fixes it: one tool or one rule before a category, a category before the whole mode. The user always "
        "sees a card with the before and after and nothing changes until they approve it. kind=mode: value auto|manual|allow_all. "
        "kind=destructive: target shell|files|items|send, value block|ask|allow (applies in Manual mode). kind=tool: target a tool "
        "name, value on|ask|off. kind=rule: target allow|ask|deny, value a rule like Bash(git status *) or calendar_update; "
        "remove=true removes it. It cannot lift hard refusals or the destructive-command card.",
        _obj({"kind": {"type": "string", "enum": list(KINDS)}, "value": {"type": "string"},
              "target": {"type": "string"}, "remove": {"type": "boolean", "default": False}}, ["kind", "value"]),
        permissions_update, "permissions", "external",
        examples=[{"kind": "tool", "target": "calendar_update", "value": "on"},
                  {"kind": "destructive", "target": "send", "value": "ask"}, {"kind": "mode", "value": "allow_all"}])
    tb.specs[NAME].floor = lambda args, ctx: permissions.SETTINGS_KIND
    tb.specs[NAME].preview = lambda args: preview(tb, args)
