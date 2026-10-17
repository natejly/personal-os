"""The one store for global permission settings: a single `permissions` row in the settings table.

Shape: {"version": 2, <key>: <value>, ...} holding only what the user set; DEFAULTS fills the rest on read. Every
gate reads these values through `get(cfg, key)` (or `load()` for the whole set), and every writer goes through
`save()` / `update()`, which validate nothing themselves: PUT /settings calls `validate()` first.

Scopes narrower than global stay where they are (conversations.settings, projects.tools, agent_defs.tool_modes,
mcp_grants, desks.autonomy, jobs.allowed_tools, the in-memory session grants); Settings > Permissions lists them all.

Compatibility: app.settings() flattens these keys back to the top level of the settings dict, so a reader that
still does cfg.get("tools") sees the same value. A legacy top-level row written after the migration (an old code
path, a test) wins over the nested value for its key, and the next save folds it in: since every save deletes the
top-level rows it folds, a top-level row that exists is always the newest write for its key.

The three host allowlists are separate fields with separate jobs:
- fetchAllowlist: hosts fetch_url may still read once a reply touched untrusted content.
- browserAllowlist: the same, for the agent's browser opening a typed URL; the browser also honours fetchAllowlist
  (browser.py merges the two), fetch_url does not honour this one.
- shellAllowedDomains: hosts the shell's egress proxy lets through, beside the package registries.
"""
from __future__ import annotations

import json
import sqlite3
from contextvars import ContextVar
from typing import Any, Callable

from . import setting_sources


VERSION = 2  # 2: permissionMode added; migrate_mode stamps it
KEY = "permissions"

DEFAULTS: dict[str, Any] = {
    # tools: {tool_name: on|ask|off}; missing = the tool's default.
    "tools": {},
    # External and schedules tools that always show a card (tools.Toolbox.ask_locked): no map switches one on, no
    # card grants one whole-tool, and untrusted content in the reply forces its card. Every other tool that acts
    # outside the app runs on a plain yes. Sending mail and deleting things that are hard to get back stay here.
    # Auto and Manual honour this list; Allow everything lifts all of it.
    "alwaysAsk": ["gmail_send", "calendar_delete", "trash_local_file", "move_local_file", "run_shortcut",
                  "python_install", "schedule_task"],
    # Argument-pattern rules over the per-tool modes: {allow: [], ask: [], deny: []} of "Tool(pattern)" strings
    # (permrules.py). Deny beats ask beats allow; a forced approval is never lifted by one.
    "permissionRules": {"allow": [], "ask": [], "deny": []},
    # How calls that would run or ask are decided (autoreview.route): "auto" has a reviewer model read every call that is
    # not known safe; "manual" is the per-tool modes, grants and rules alone, with destructive actions refused by default
    # (destructiveDefaults); "allow_all" runs everything (unsandboxed shell and mail sends included) with no card, except a
    # destructive shell command and the user's own questions; denied calls (hard-deny paths included) are always refused.
    "permissionMode": "auto",
    # Manual mode only: what happens to a destructive call, by category (DESTRUCTIVE_KINDS). "block" refuses it, "ask" is a
    # card, "allow" leaves it to manual mode's usual gate. Auto and Allow everything ignore it, except that a destructive
    # shell command is a card in both.
    "destructiveDefaults": {"shell": "block", "files": "block", "items": "block", "send": "block"},
    # "deny": a job run that would have to ask is refused with a recorded reason instead of waiting for someone.
    "unattendedApprovals": "deny",
    "autoReviewModel": "",  # "" = the fast model, else the extraction model, else the chat model
    # Hosts fetch_url may still read once a reply has touched untrusted content (registrable-suffix match).
    "fetchAllowlist": [],
    # How doc_edit lands. "review" proposes a diff; "apply" writes it.
    "docEditMode": "review",
    # Host shell (shell.py): a networked shell run taints the reply, whatever it prints may be third-party text.
    "shellNetwork": False,
    # While shellNetwork is off, outbound connections go through a local allowlisting proxy (egress.py).
    # shellRegistryAccess admits the package registries; shellAllowedDomains adds hosts of the user's own.
    "shellRegistryAccess": True,
    "shellAllowedDomains": [],
    # In a desk, a sandboxed shell_run whose working folder is the desk's own workspace runs without a card.
    "deskShellAuto": True,
    # The agent's own browser (browser.py), and the hosts it may open by typed URL after untrusted content.
    "browserEnabled": True,
    "browserAllowlist": [],
    # desk_done is refused while the plan has open steps or a delivered file is missing or empty (deskgate.py),
    # and a read-only reviewer checks the result against the brief before the desk may finish.
    "deskDoneGate": True,
    "deskSelfReview": True,
    # Connections are unrestricted: the host allow-lists (fetch_url and browser after untrusted content, the shell and
    # sandbox egress proxy) admit any public hostname, and every MCP tool runs without a per-tool grant unless a grant says
    # "off". Private addresses, the MCP taint rule, deny rules and permissionMode are unchanged. migration 25 turns it on for
    # an install that already has user data; a fresh install starts off.
    "allowAllConnections": False,
    # Off by default. A reply that has read content from outside Grain (a web page, mail, a connector) otherwise asks
    # before a shell command that could reach the network and before a write outside the desk workspace, because what
    # it read could be steering it. On, exactly those two taint-forced cards are lifted: an unsandboxed shell command,
    # credential stores, Grain's own data folder and app, PROMPT_WRITES, ask_locked
    # tools, the network-tool taint rule and cancel_send all still ask, and every deny rule and off mode still wins.
    "trustExternalContent": False,
    # Coding goes to a coding agent (extensions/coding/route.py): Grain's own file writers and file-changing shell commands are refused
    # inside a git work tree under every permission mode, and the agents are told to use coding_session_start. codingAgent is
    # the default harness for coding_session_start when `agent` is omitted: "opencode", "claude", "pi", or "any" (the first
    # installed of claude, opencode, pi). A code default: nothing to migrate.
    "codingRoute": True,
    "codingAgent": "any",
}
KEYS = frozenset(DEFAULTS)
HOST_LISTS = frozenset({"fetchAllowlist", "shellAllowedDomains", "browserAllowlist"})
DESTRUCTIVE_KINDS = ("shell", "files", "items", "send")
DESTRUCTIVE_ACTIONS = ("block", "ask", "allow")
SETTINGS_KIND = "settings"  # a floor kind that is a card in every mode (permissions_update)
KIND_WORDS = {"shell": "destructive shell commands", "files": "deleting or overwriting files", "items": "deleting mail, events and tasks",
              "send": "sending email and messages"}


def blocked_reason(kind: str | None) -> str:
    """The reason a destructive call of `kind` is refused in Manual mode; callers hand it to tools.denied."""
    return (f"blocked by the user's settings, which refuse {KIND_WORDS.get(kind or '', 'this destructive action')} while permissions are "
            "on Manual. Do not work around it with another tool or a shell command; tell the user what you wanted to do so they can "
            "do it themselves or change the setting")


def floor_action(pmode: str, kind: str | None, defaults: Any = None) -> str | None:
    """card | block | None for a call of destructive `kind` (Toolbox.floor_kind); None leaves it to the usual gate.
    A destructive shell command is a card in auto and Allow everything and follows destructiveDefaults in manual; the other
    kinds follow destructiveDefaults in manual only; SETTINGS_KIND is a card in every mode."""
    if kind == SETTINGS_KIND:
        return "card"
    if kind is None:
        return None
    if pmode != "manual":
        return "card" if kind == "shell" else None
    act = {**DEFAULTS["destructiveDefaults"], **(defaults if isinstance(defaults, dict) else {})}.get(kind)
    return {"block": "block", "ask": "card"}.get(act)


CHOICES: dict[str, tuple[str, ...]] = {
    "permissionMode": ("auto", "manual", "allow_all"),
    "unattendedApprovals": ("ask", "deny"),
    "docEditMode": ("review", "apply"),
    "codingAgent": ("any", "opencode", "claude", "pi"),
}
CHOICE_ERRORS = {
    "permissionMode": "permissionMode must be 'auto', 'manual' or 'allow_all'",
    "unattendedApprovals": "unattendedApprovals must be 'ask' or 'deny'",
    "docEditMode": "docEditMode must be 'review' or 'apply'",
    "codingAgent": "codingAgent must be 'any', 'opencode', 'claude' or 'pi'",
}


def get(cfg: dict[str, Any] | None, key: str) -> Any:
    """One permission value from a settings dict (app.settings() carries the flattened view), else its default."""
    cfg = cfg or {}
    if key in cfg:
        return cfg[key]
    nested = cfg.get(KEY)
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    return DEFAULTS[key]


# ---------------------------------------------------------------- scopes narrower than global
# The keys a project or a chat may override (projects.permissions and conversations.settings.permissions).
SCOPED_KEYS = ("permissionMode", "permissionRules", "alwaysAsk", "destructiveDefaults", "unattendedApprovals")
# The project's and the chat's overrides for the turn running on this task. settings() overlays them, so every gate that
# reads a permission value from the settings sees the value in force for the turn without being changed.
SCOPE: ContextVar[tuple[dict[str, Any] | None, dict[str, Any] | None] | None] = ContextVar("permission_scope", default=None)


def narrowed(key: str, wider: Any, own: Any) -> Any:
    """A narrower scope's value on top of the wider one: rules and always-ask entries are added to the wider scope's (a
    narrower scope cannot remove a global deny); the other keys replace it."""
    if key == "permissionRules":
        return {k: list(dict.fromkeys([*((wider or {}).get(k) or []), *((own or {}).get(k) or [])])) for k in ("allow", "ask", "deny")}
    if key == "alwaysAsk":
        return list(dict.fromkeys([*(wider or []), *(own or [])]))
    return own


def overlay(cfg: dict[str, Any], *scopes: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of the settings with each scope's overrides applied, widest scope first (project, then chat)."""
    out = {**cfg, KEY: dict(cfg.get(KEY) or {})}
    for scope in scopes:
        for key in SCOPED_KEYS:
            if scope and key in scope:
                out[key] = out[KEY][key] = narrowed(key, get(out, key), scope[key])
    return out


# ---------------------------------------------------------------- what loosens
MODE_RANK = {"off": 0, "ask": 1, "on": 2}  # a tool's mode, by how much runs without a card
_ORDERS = {"permissionMode": ("manual", "auto", "allow_all"), "unattendedApprovals": ("deny", "ask"),
           "docEditMode": ("review", "apply"), "destructive": ("block", "ask", "allow")}


def _raised(order: str) -> Callable[[Any, Any], bool]:
    return lambda old, new: _ORDERS[order].index(new) > _ORDERS[order].index(old)


def _rules_loosen(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """A removed deny or ask rule, or an added allow rule."""
    def side(d: dict[str, Any], k: str) -> set[str]:
        return set((d or {}).get(k) or ())
    return bool(side(old, "deny") - side(new, "deny") or side(old, "ask") - side(new, "ask") or side(new, "allow") - side(old, "allow"))


def _destructive_loosen(old: dict[str, Any], new: dict[str, Any]) -> bool:
    old, new = {**DEFAULTS["destructiveDefaults"], **old}, {**DEFAULTS["destructiveDefaults"], **new}
    return any(_raised("destructive")(old[k], new[k]) for k in DESTRUCTIVE_KINDS)


def _tools_loosen(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Any tool whose mode rises; a tool the old map does not mention counts as off, so a rise is never missed."""
    return any(MODE_RANK.get(m, 0) > MODE_RANK.get(old.get(n), 0) for n, m in new.items())


def _opens(old: bool, new: bool) -> bool:
    return bool(new) and not old


def _closes(old: bool, new: bool) -> bool:
    return bool(old) and not new


def _grows(old: list[str], new: list[str]) -> bool:
    return bool(set(new) - set(old))


# Per key: does going from the old value to the new one let more run without a card, or more reach out? A key with no
# comparator must be named in NEUTRAL (a test enforces it), so a new permission key cannot be forgotten here.
COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "tools": _tools_loosen, "alwaysAsk": lambda old, new: bool(set(old) - set(new)), "permissionRules": _rules_loosen,
    "permissionMode": _raised("permissionMode"), "destructiveDefaults": _destructive_loosen,
    "unattendedApprovals": _raised("unattendedApprovals"), "fetchAllowlist": _grows, "docEditMode": _raised("docEditMode"),
    "shellNetwork": _opens, "shellRegistryAccess": _opens, "shellAllowedDomains": _grows, "deskShellAuto": _opens,
    "browserEnabled": _opens, "browserAllowlist": _grows, "deskDoneGate": _closes, "deskSelfReview": _closes,
    "allowAllConnections": _opens, "trustExternalContent": _opens, "codingRoute": _closes,
}
NEUTRAL = frozenset({"autoReviewModel", "codingAgent"})  # a model name and a harness choice open nothing


def loosening(current: dict[str, Any], proposed: dict[str, Any]) -> list[str]:
    """The keys of `proposed` that open more than `current` does. Keys `current` lacks read as their defaults."""
    return [k for k, new in proposed.items() if k in COMPARATORS and COMPARATORS[k](current.get(k, DEFAULTS[k]), new)]


def _json(raw: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _nested(raw: Any) -> dict[str, Any]:
    try:
        v = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return {}
    return {k: x for k, x in v.items() if k in KEYS} if isinstance(v, dict) else {}


def stored_keys(stored: dict[str, Any]) -> set[str]:
    """The permission keys the settings rows hold a value for, as opposed to the ones that read as their default."""
    return set(_nested(stored.get(KEY))) | {k for k in KEYS if k in stored}


def load(stored: dict[str, Any]) -> dict[str, Any]:
    """Every permission value from the raw settings rows (db.get_settings()): defaults < the store < a newer legacy row."""
    return {**DEFAULTS, **_nested(stored.get(KEY)), **{k: stored[k] for k in KEYS if k in stored}}


def _write(c: sqlite3.Connection, patch: dict[str, Any]) -> dict[str, Any]:
    """Fold any top-level legacy rows and `patch` into the store, delete those rows; returns what is stored."""
    row = c.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    cur = _nested(row[0]) if row else {}
    marks = ",".join("?" * len(KEYS))
    for k, v in c.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", tuple(KEYS)).fetchall():
        try:
            cur[k] = json.loads(v)
        except ValueError:
            pass  # unreadable legacy row: the default applies, as it did when the app read it
    cur.update({k: v for k, v in patch.items() if k in KEYS})
    c.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps({"version": VERSION, **cur})))
    c.execute(f"DELETE FROM settings WHERE key IN ({marks})", tuple(KEYS))
    return cur


def migrate(c: sqlite3.Connection) -> None:
    """Migration 5: the legacy top-level permission rows become the one `permissions` row."""
    _write(c, {})


def migrate_mode(c: sqlite3.Connection) -> None:
    """One-time, idempotent: a store older than version 2, or without a permissionMode, becomes "auto" whatever
    an older install had stored. _write keeps every other key and stamps VERSION."""
    row = c.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    cur = _json(row[0]) if row else None
    if isinstance(cur, dict) and (cur.get("version") or 0) >= 2 and cur.get("permissionMode") in CHOICES["permissionMode"]:
        return
    _write(c, {"permissionMode": "auto"})


def update(db: Any, fn: Callable[[dict[str, Any]], dict[str, Any]], *, source: str = "user",
           prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-modify-write under one write lock: `fn` gets every current value and returns the keys to change.
    For a grant that merges into a map or list (an approval card's always / allow host) so a concurrent save is not lost.
    The keys it writes are recorded as coming from `source` (setting_sources.py); `prev` maps a key to the value it replaced."""
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        stored = {r[0]: _json(r[1]) for r in c.execute(
            f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * (len(KEYS) + 1))})", (KEY, *KEYS)).fetchall()}
        patch = fn(load(stored))
        out = _write(c, patch)
        setting_sources.stamp(c, [k for k in patch if k in KEYS], source, prev=prev)
        return out


def save(db: Any, patch: dict[str, Any], *, source: str = "user", prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write already-validated values (see validate)."""
    return update(db, lambda _cur: patch, source=source, prev=prev)


def validate(key: str, v: Any, *, cap_modes: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
             rule_check: Callable[[Any], None] | None = None) -> Any:
    """A value as it may be stored, or ValueError with the reason. `cap_modes` caps ask-locked tools in a tool map;
    `rule_check` refuses a rule naming no known tool (both live with the toolbox, so the caller passes them)."""
    d = DEFAULTS[key]
    if isinstance(d, (dict, list, str, bool)) and not isinstance(v, type(d)):
        # Stored as given, a wrong-typed value (tools: "x") 500s every route that reads it.
        raise ValueError(f"{key} must be a {type(d).__name__}")
    if key in CHOICES and v not in CHOICES[key]:
        raise ValueError(CHOICE_ERRORS[key])
    if key == "destructiveDefaults":
        if bad := [k for k in v if k not in DESTRUCTIVE_KINDS]:
            raise ValueError(f"destructiveDefaults has no category {bad[0]!r}; use {', '.join(DESTRUCTIVE_KINDS)}")
        if bad := [k for k, a in v.items() if a not in DESTRUCTIVE_ACTIONS]:
            raise ValueError(f"destructiveDefaults.{bad[0]} must be 'block', 'ask' or 'allow'")
        return {**DEFAULTS[key], **v}
    if key == "permissionRules":
        from . import permrules
        out: dict[str, list[str]] = {}
        for k in ("allow", "ask", "deny"):
            items = v.get(k) or []
            if not isinstance(items, list):
                raise ValueError(f"permissionRules.{k} must be a list")
            parsed = [permrules.parse_rule(str(t)) for t in items]
            for r in parsed:
                if rule_check:
                    rule_check(r)
            out[k] = list(dict.fromkeys(r.text for r in parsed))
        return out
    if key == "tools":
        # Coerced, not refused: a legacy stored 'on' comes back in every later save of the whole map.
        return cap_modes(v) if cap_modes else v
    if key == "alwaysAsk":
        if not all(isinstance(x, str) for x in v):
            raise ValueError("alwaysAsk must be a list of tool names")
        return list(dict.fromkeys(v))
    if key in HOST_LISTS:
        # Bare hostnames only, the rule the egress proxy matches by: a URL, wildcard, IP or lone TLD stored
        # here would be ignored at best and widen an allowlist at worst.
        from . import egress
        hosts: list[str] = []
        for e in v:
            h = egress.normalize_entry(e.strip().lstrip(".") if isinstance(e, str) else e)  # ".x.com" = "x.com"
            if h is None:
                raise ValueError(f"{key}: {e!r} is not a hostname (no scheme, path, wildcard or IP address)")
            if h not in hosts:
                hosts.append(h)
        return hosts
    return v
