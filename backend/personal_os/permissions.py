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
- shellAllowedDomains: hosts the shell's and sandbox's egress proxy lets through, beside the package registries.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable


VERSION = 2  # 2: permissionMode added; migrate_mode stamps it
DEFAULT_IMAGE = "python:3.12-slim"  # microvm.DEFAULT_IMAGE
KEY = "permissions"

DEFAULTS: dict[str, Any] = {
    # tools: {tool_name: on|ask|off}; missing = the tool's default.
    "tools": {},
    # External and schedules tools that always show a card (tools.Toolbox.ask_locked): no map switches one on, no
    # card grants one whole-tool, and untrusted content in the reply forces its card. Every other tool that acts
    # outside the app runs on a plain yes. Sending mail and deleting things that are hard to get back stay here.
    "alwaysAsk": ["gmail_send", "calendar_delete", "trash_local_file", "move_local_file", "run_shortcut",
                  "python_install", "schedule_task"],
    # Argument-pattern rules over the per-tool modes: {allow: [], ask: [], deny: []} of "Tool(pattern)" strings
    # (permrules.py). Deny beats ask beats allow; a forced approval is never lifted by one.
    "permissionRules": {"allow": [], "ask": [], "deny": []},
    # How calls that would run or ask are decided (autoreview.route): "auto" has a reviewer model read every call that is
    # not known safe; "manual" is the per-tool modes, grants and rules alone; "allow_all" runs everything but denied
    # calls and writes outside the workspace folders.
    "permissionMode": "auto",
    # Legacy, kept so stored values load and PUT keeps accepting them. skipPermissions and autoReview are no longer read
    # by any gate (migrate_mode folds an existing install into "auto"); unattendedApprovals is read only in manual mode.
    "skipPermissions": False,
    # "deny": a job run that would have to ask is refused with a recorded reason instead of waiting for someone.
    "unattendedApprovals": "deny",
    "autoReview": "off",
    "autoReviewModel": "",  # "" = the fast model, else the extraction model, else the chat model
    # Hosts fetch_url may still read once a reply has touched untrusted content (registrable-suffix match).
    "fetchAllowlist": [],
    # How doc_edit lands. "review" proposes a diff; "apply" writes it.
    "docEditMode": "review",
    # Folders (absolute paths inside the home folder) where fs_edit / fs_copy / fs_mkdir run without asking. A desk's
    # own workspace is always granted; anywhere else those tools ask first.
    "workspaceRoots": [],
    # Plan mode for ordinary chats when the chat has no setting of its own: off | auto | always.
    "planMode": "off",
    # The Linux sandbox's network: "off" (none), "proxy" (an internal-only network whose one way out is an allowlisting
    # proxy: package registries plus shellAllowedDomains), "open" (every result taints). A stored true reads as "open".
    "sandboxNetwork": "off",
    # The sandbox_* containers: the image a fresh one starts from, and the CLI that drives them.
    "sandboxImage": DEFAULT_IMAGE,
    "sandboxRuntime": "docker",
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
}
KEYS = frozenset(DEFAULTS)
# An image reference as an argv word: no leading dash (it would read as a flag), no spaces or shell characters.
IMAGE_REF = re.compile(r"^[a-z0-9][A-Za-z0-9._/:-]{0,254}(@sha256:[a-f0-9]{64})?$")
HOST_LISTS = frozenset({"fetchAllowlist", "shellAllowedDomains", "browserAllowlist"})
SANDBOX_RUNTIMES = ("docker", "podman", "nerdctl")
CHOICES: dict[str, tuple[str, ...]] = {
    "permissionMode": ("auto", "manual", "allow_all"),
    "unattendedApprovals": ("ask", "deny"),
    "autoReview": ("off", "risky", "all-writes"),
    "sandboxRuntime": SANDBOX_RUNTIMES,
    "sandboxNetwork": ("off", "proxy", "open"),
    "docEditMode": ("review", "apply"),
    "planMode": ("off", "auto", "always"),
}
CHOICE_ERRORS = {
    "permissionMode": "permissionMode must be 'auto', 'manual' or 'allow_all'",
    "unattendedApprovals": "unattendedApprovals must be 'ask' or 'deny'",
    "autoReview": "autoReview must be 'off', 'risky' or 'all-writes'",
    "sandboxRuntime": f"sandboxRuntime must be one of {', '.join(SANDBOX_RUNTIMES)}",
    "sandboxNetwork": "sandboxNetwork must be 'off', 'proxy' or 'open'",
    "docEditMode": "docEditMode must be 'review' or 'apply'",
    "planMode": "planMode must be 'off', 'auto' or 'always'",
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
    skipPermissions / autoReview said. _write keeps every other key (workspaceRoots included) and stamps VERSION."""
    row = c.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    cur = _json(row[0]) if row else None
    if isinstance(cur, dict) and (cur.get("version") or 0) >= 2 and cur.get("permissionMode") in CHOICES["permissionMode"]:
        return
    _write(c, {"permissionMode": "auto"})


def update(db: Any, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Read-modify-write under one write lock: `fn` gets every current value and returns the keys to change.
    For a grant that merges into a map or list (an approval card's always / allow host) so a concurrent save is not lost."""
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        stored = {r[0]: _json(r[1]) for r in c.execute(
            f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * (len(KEYS) + 1))})", (KEY, *KEYS)).fetchall()}
        return _write(c, fn(load(stored)))


def save(db: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Write already-validated values (see validate)."""
    return update(db, lambda _cur: patch)


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
    if key == "sandboxImage" and v and not IMAGE_REF.match(v):  # empty = the default image
        raise ValueError("sandboxImage must be an image reference such as python:3.12-slim")
    if key == "workspaceRoots":
        if not all(isinstance(x, str) for x in v):
            raise ValueError("workspaceRoots must be a list of folders")
        # The file tools only work inside the home folder and outside hidden folders and ~/Library. A root they
        # would refuse is rejected here rather than stored and then silently ignored. The home folder itself is refused too.
        from . import mac
        for root in v:
            try:
                mac.allowed_root(root)
            except mac.LocalPathError as e:
                raise ValueError(f"{root} cannot be a workspace folder: {e}") from e
    return v
