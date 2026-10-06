"""Bring MCP servers over from other apps' config files.

Read-only and local: three known files are read (never a path the client names), each size-capped and parsed
tolerantly, and every server found gets an opaque `ref`. The browser sees refs and names, never env or header values;
importing re-runs the discovery here and only acts on refs this module produced. A value that looks like a credential
goes to the secret store at import time, the rest stays in plain env.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    return [("claude_desktop", "Claude Desktop", home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
            ("claude_code", "Claude Code", home / ".claude.json"),
            ("cursor", "Cursor", home / ".cursor" / "mcp.json")]


def looks_secret(key: str, value: str) -> bool:
    """Should this env value live in the secret store? By its name (whole segments, so PATH is not PAT) or by its shape."""
    words = [w.upper() for w in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])", str(key)) if w]
    if any(w in SECRET_WORDS or w.endswith(SECRET_SUFFIXES) for w in words) or str(key).upper().endswith(("APIKEY", "API_KEY")):
        return True
    text = str(value or "")
    return bool(text) and (bool(USERINFO.search(text)) or redact.scrub(text, VALUE_RULES, secret_assign=False) != text)


def _ref(source: str, origin: str, key: str) -> str:
    return hashlib.sha256(f"{source}|{origin}|{key}".encode()).hexdigest()[:REF_CHARS]


def _read_json(path: Path) -> tuple[Any, str | None]:
    """(parsed, error). A missing file is (None, None): not an error, just not there."""
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
        return json.loads(raw.decode("utf-8-sig")), None
    except ValueError:
        return None, "not valid JSON"


def _entry(key: str, raw: Any) -> dict[str, Any] | None:
    """One `mcpServers` value as a normalised config, or None when it is neither a command nor a URL."""
    if not isinstance(raw, dict):
        return None
    pairs = lambda v: {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}  # noqa: E731
    if isinstance(raw.get("command"), str) and raw["command"].strip():
        args = raw.get("args") if isinstance(raw.get("args"), list) else []
        return {"key": key, "transport": "stdio", "command": raw["command"].strip(), "args": [str(a) for a in args],
                "url": "", "env": pairs(raw.get("env")), "headers": {}}
    if isinstance(raw.get("url"), str) and raw["url"].strip():
        kind = str(raw.get("type") or raw.get("transport") or "http").lower()
        return {"key": key, "transport": REMOTE_TYPES.get(kind, "http"), "command": "", "args": [], "url": raw["url"].strip(),
                "env": {}, "headers": pairs(raw.get("headers"))}
    return None


def _servers_of(doc: Any) -> dict[str, Any]:
    found = doc.get("mcpServers") if isinstance(doc, dict) else None
    return found if isinstance(found, dict) else {}


def _files(source: str, path: Path, doc: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every (origin, mcpServers) a source contributes; the origin keeps one name in two projects from sharing a ref. Claude Code spreads them over projects and their .mcp.json."""
    out = [(str(path), _servers_of(doc))]
    if source == "claude_code" and isinstance(doc, dict) and isinstance(doc.get("projects"), dict):
        for i, (folder, proj) in enumerate(doc["projects"].items()):
            if i >= MAX_PROJECTS:
                break
            out.append((f"{path}#{folder}", _servers_of(proj)))
            if isinstance(folder, str) and folder.startswith("/"):
                project_file = Path(folder) / ".mcp.json"
                inner, _err = _read_json(project_file)
                if inner is not None:
                    out.append((str(project_file), _servers_of(inner)))
    return out


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
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
            "installed": any(_same(cfg, e) for e in existing)}


def scan(existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(what the UI may see, ref -> full config for importing). `existing` is McpServers.servers()."""
    view: list[dict[str, Any]] = []
    configs: dict[str, dict[str, Any]] = {}
    for sid, label, path in sources():
        doc, err = _read_json(path)
        servers: list[dict[str, Any]] = []
        seen: list[dict[str, Any]] = []
        for origin, block in _files(sid, path, doc):
            for key, raw in block.items():
                cfg = _entry(str(key), raw)
                if cfg is None or any(_same(cfg, s) and s["key"] == cfg["key"] for s in seen) or len(servers) >= MAX_SERVERS:
                    continue  # the same server listed under several projects is shown once
                seen.append(cfg)
                ref = _ref(sid, origin, cfg["key"])
                configs[ref] = cfg
                servers.append(_public(ref, cfg, existing))
        view.append({"id": sid, "label": label, "path": str(path), "found": doc is not None or err is not None,
                     "error": err, "servers": servers})
    return view, configs


def create_kwargs(cfg: dict[str, Any]) -> dict[str, Any]:
    """McpServers.create_server arguments for one scanned config: credential-looking env values to `secrets`."""
    secrets = {k: v for k, v in cfg["env"].items() if looks_secret(k, v)}
    return {"name": cfg["key"], "transport": cfg["transport"], "command": cfg["command"], "args": list(cfg["args"]),
            "env": {k: v for k, v in cfg["env"].items() if k not in secrets}, "secrets": secrets,
            "url": cfg["url"], "headers": dict(cfg["headers"])}
