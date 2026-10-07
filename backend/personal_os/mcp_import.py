"""Bring MCP servers over from other apps' config files, or from pasted JSON.

Read-only and local: a fixed set of known files is read (never a path the client names), each size-capped and parsed
tolerantly, and every server found gets an opaque `ref`. The browser sees refs and names, never env or header values;
importing re-runs the discovery here and only acts on refs this module produced. A value that looks like a credential
goes to the secret store at import time, the rest stays in plain env.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from . import redact

MAX_FILE_BYTES = 2 * 1024 * 1024  # a config file is a few KB; this only stops a hostile or runaway one
MAX_PROJECTS = 200                # project folders of ~/.claude.json whose own .mcp.json is looked at
MAX_SERVERS = 200                 # per source
REF_CHARS = 16
REMOTE_TYPES = {"http": "http", "streamable-http": "http", "streamable_http": "http", "streamablehttp": "http", "sse": "sse"}
# A whole name segment that announces a credential, or a longer word ending in one (GITHUB_PERSONAL_ACCESS_TOKEN, DB_PASSWORD).
SECRET_WORDS = frozenset({"KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "PASSWD", "AUTH", "CREDENTIAL", "CREDENTIALS", "PAT", "COOKIE", "SESSION", "BEARER"})
SECRET_SUFFIXES = ("TOKEN", "SECRET", "PASSWORD", "APIKEY", "CREDENTIALS", "COOKIE")
# user:password@ in any scheme (a database URL), which redact's http(s)-only rule misses.
USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
VALUE_RULES = ("private_key", "url_userinfo", "url_secret_param", "token", "aws_key", "github_pat", "google_api", "google_oauth", "slack_webhook", "jwt", "entropy")


def sources() -> list[tuple[str, str, Path]]:
    """(id, label, file). Resolved per call so a test can point HOME elsewhere."""
    home = Path.home()
    app_support = home / "Library" / "Application Support"
    oc = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "opencode"
    return [("claude_desktop", "Claude Desktop", app_support / "Claude" / "claude_desktop_config.json"),
            ("claude_code", "Claude Code", home / ".claude.json"),
            ("cursor", "Cursor", home / ".cursor" / "mcp.json"),
            ("vscode", "VS Code", app_support / "Code" / "User" / "mcp.json"),
            ("windsurf", "Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            ("codex", "Codex", home / ".codex" / "config.toml"),
            ("opencode", "OpenCode", oc / "opencode.json" if (oc / "opencode.json").is_file() or not (oc / "opencode.jsonc").is_file()
             else oc / "opencode.jsonc")]


def looks_secret(key: str, value: str) -> bool:
    """Should this env value live in the secret store? By its name (whole segments, so PATH is not PAT) or by its shape."""
    words = [w.upper() for w in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])", str(key)) if w]
    if any(w in SECRET_WORDS or w.endswith(SECRET_SUFFIXES) for w in words) or str(key).upper().endswith(("APIKEY", "API_KEY")):
        return True
    text = str(value or "")
    return bool(text) and (bool(USERINFO.search(text)) or redact.scrub(text, VALUE_RULES, secret_assign=False) != text)


def _ref(source: str, origin: str, key: str) -> str:
    return hashlib.sha256(f"{source}|{origin}|{key}".encode()).hexdigest()[:REF_CHARS]


# Strings are matched first so a // inside a URL or a comma inside a value is left alone.
JSONC = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)


def parse_jsonc(text: str) -> Any:
    """json.loads that tolerates // and /* */ comments and trailing commas (VS Code and OpenCode config files)."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(JSONC.sub(lambda m: m.group(1) or "", text))


def _read_json(path: Path) -> tuple[Any, str | None]:
    """(parsed, error). A missing file is (None, None): not an error, just not there. .toml files are parsed as TOML."""
    try:
        if not path.is_file():
            return None, None
        with path.open("rb") as f:
            raw = f.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return None, f"could not read the file ({type(e).__name__})"
    if len(raw) > MAX_FILE_BYTES:
        return None, "file is too large to import from"
    try:
        text = raw.decode("utf-8-sig")
        return (tomllib.loads(text) if path.suffix == ".toml" else parse_jsonc(text)), None
    except (ValueError, UnicodeDecodeError):  # tomllib's decode error is a ValueError too
        return None, "not valid TOML" if path.suffix == ".toml" else "not valid JSON"


def _entry(key: str, raw: Any) -> dict[str, Any] | None:
    """One server value as a normalised config, or None when it is neither a command nor a URL. Reads the shapes of every
    supported file: command/args/env, an OpenCode command array with `environment`, url with headers or http_headers."""
    if not isinstance(raw, dict):
        return None
    pairs = lambda v: {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}  # noqa: E731
    enabled = raw.get("enabled") is not False and raw.get("disabled") is not True
    cmd, args = raw.get("command"), raw.get("args") if isinstance(raw.get("args"), list) else []
    if isinstance(cmd, list) and cmd and all(isinstance(x, (str, int, float)) for x in cmd):  # OpenCode: the command is an array
        cmd, args = str(cmd[0]), [*map(str, cmd[1:]), *args]
    if isinstance(cmd, str) and cmd.strip():
        return {"key": key, "transport": "stdio", "command": cmd.strip(), "args": [str(a) for a in args], "url": "",
                "env": pairs(raw.get("env") or raw.get("environment")), "headers": {}, "enabled": enabled}
    url = raw.get("url") or raw.get("serverUrl")  # Windsurf names a remote server's address serverUrl
    if isinstance(url, str) and url.strip():
        kind = str(raw.get("type") or raw.get("transport") or "http").lower()
        return {"key": key, "transport": REMOTE_TYPES.get(kind, "http"), "command": "", "args": [], "url": url.strip(),
                "env": {}, "headers": pairs(raw.get("headers") or raw.get("http_headers")), "enabled": enabled}
    return None


# The key that holds the server map, by source. VS Code's mcp.json says `servers`, OpenCode `mcp`, Codex `mcp_servers`.
BLOCK_KEYS = {"vscode": "servers", "opencode": "mcp", "codex": "mcp_servers"}


def _servers_of(doc: Any, key: str = "mcpServers") -> dict[str, Any]:
    found = doc.get(key) if isinstance(doc, dict) else None
    return found if isinstance(found, dict) else {}


def _project_folders(home: Path) -> list[str]:
    """The project folders ~/.claude.json lists (the places a project-level config may live)."""
    doc, _err = _read_json(home / ".claude.json")
    projects = doc.get("projects") if isinstance(doc, dict) else None
    return [f for f in list(projects)[:MAX_PROJECTS] if isinstance(f, str) and f.startswith("/")] if isinstance(projects, dict) else []


def _files(source: str, path: Path, doc: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every (origin, server map) a source contributes; the origin keeps one name in two projects from sharing a ref. Claude Code spreads them over projects and their .mcp.json."""
    out = [(str(path), _servers_of(doc, BLOCK_KEYS.get(source, "mcpServers")))]
    home = Path.home()
    if source == "claude_code":
        settings, _err = _read_json(home / ".claude" / "settings.json")
        out.append((str(home / ".claude" / "settings.json"), _servers_of(settings)))
        if isinstance(doc, dict) and isinstance(doc.get("projects"), dict):
            for i, (folder, proj) in enumerate(doc["projects"].items()):
                if i >= MAX_PROJECTS:
                    break
                out.append((f"{path}#{folder}", _servers_of(proj)))
                if isinstance(folder, str) and folder.startswith("/"):
                    project_file = Path(folder) / ".mcp.json"
                    inner, _err = _read_json(project_file)
                    if inner is not None:
                        out.append((str(project_file), _servers_of(inner)))
    elif source == "vscode":
        settings, _err = _read_json(path.with_name("settings.json"))
        nested = settings.get("mcp") if isinstance(settings, dict) else None  # {"mcp": {"servers": ...}} or a flat "mcp.servers"
        out.append((str(path.with_name("settings.json")), _servers_of(nested, "servers") or _servers_of(settings, "mcp.servers")))
    elif source == "opencode":
        for extra in ({path.with_suffix(".jsonc"), path.with_suffix(".json")} - {path}):
            inner, _err = _read_json(extra)
            if inner is not None:
                out.append((str(extra), _servers_of(inner, "mcp")))
        for folder in _project_folders(home):
            for name in ("opencode.json", ".opencode.json", "opencode.jsonc"):
                inner, _err = _read_json(Path(folder) / name)
                if inner is not None:
                    out.append((str(Path(folder) / name), _servers_of(inner, "mcp")))
    return out


def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The same server already configured: one stdio command and args, or one URL."""
    if a["transport"] != b["transport"]:
        return False
    return a["url"] == b["url"] if a["transport"] != "stdio" else (a["command"], a["args"]) == (b["command"], b["args"])


def _public(ref: str, cfg: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any]:
    secret_keys = sorted(k for k, v in cfg["env"].items() if looks_secret(k, v))
    return {"ref": ref, "key": cfg["key"], "name": cfg["key"], "transport": cfg["transport"], "command": cfg["command"],
            "args": [redact.scrub_command_output(a) for a in cfg["args"]],
            "url": (redact.sanitize_url(cfg["url"]) or redact.scrub_command_output(cfg["url"])) if cfg["url"] else "",
            "env_keys": sorted(cfg["env"]), "header_keys": sorted(cfg["headers"]), "secret_keys": secret_keys,
            "enabled": cfg["enabled"], "installed": any(same(cfg, e) for e in existing)}


def scan(existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(what the UI may see, ref -> full config for importing). `existing` is McpServers.servers().
    A server an earlier source already lists is shown with `duplicate_of` (that source's label) and cannot be imported."""
    view: list[dict[str, Any]] = []
    configs: dict[str, dict[str, Any]] = {}
    earlier: list[tuple[dict[str, Any], str]] = []
    for sid, label, path in sources():
        doc, err = _read_json(path)
        servers: list[dict[str, Any]] = []
        seen: list[dict[str, Any]] = []
        for origin, block in _files(sid, path, doc):
            for key, raw in block.items():
                cfg = _entry(str(key), raw)
                if cfg is None or any(same(cfg, s) and s["key"] == cfg["key"] for s in seen) or len(servers) >= MAX_SERVERS:
                    continue  # the same server listed under several projects is shown once
                seen.append(cfg)
                ref = _ref(sid, origin, cfg["key"])
                pub = _public(ref, cfg, existing)
                dup = next((lbl for e, lbl in earlier if same(cfg, e)), None)
                if dup:
                    pub["duplicate_of"] = dup
                else:
                    configs[ref] = cfg
                servers.append(pub)
        earlier.extend((s, label) for s in seen if not any(same(s, e) for e, _l in earlier))
        view.append({"id": sid, "label": label, "path": str(path), "found": doc is not None or err is not None,
                     "error": err, "servers": servers})
    return view, configs


def parse_pasted(text: str) -> list[dict[str, Any]]:
    """Configs from pasted JSON: {"mcpServers"|"servers"|"mcp": {...}}, a bare {name: {...}} map, or one server object.
    Raises ValueError with a fixed message; the pasted text is never quoted back."""
    if len(text) > MAX_FILE_BYTES:
        raise ValueError("The pasted text is too large")
    try:
        doc = parse_jsonc(text)
    except ValueError:
        raise ValueError("That is not valid JSON") from None
    if not isinstance(doc, dict):
        raise ValueError("Expected a JSON object")
    if _entry("", doc):  # one server object: named by its own "name", else its command or host
        host = re.sub(r"^https?://", "", str(doc.get("url") or "")).split("/")[0]
        name = str(doc.get("name") or Path(str(doc.get("command") or "")).name or host or "MCP server")
        return [{**_entry(name, doc), "key": name}]  # type: ignore[dict-item]
    block = next((doc[k] for k in ("mcpServers", "servers", "mcp_servers", "mcp") if isinstance(doc.get(k), dict)), doc)
    out = [c for k, v in block.items() if (c := _entry(str(k), v)) is not None][:MAX_SERVERS]
    if not out:
        raise ValueError("No MCP servers found: each needs a command or a url")
    return out


def create_kwargs(cfg: dict[str, Any]) -> dict[str, Any]:
    """McpServers.create_server arguments for one scanned config: credential-looking env values to `secrets`."""
    secrets = {k: v for k, v in cfg["env"].items() if looks_secret(k, v)}
    return {"name": cfg["key"], "transport": cfg["transport"], "command": cfg["command"], "args": list(cfg["args"]),
            "env": {k: v for k, v in cfg["env"].items() if k not in secrets}, "secrets": secrets,
            "url": cfg["url"], "headers": dict(cfg["headers"]), "enabled": cfg.get("enabled", True)}
