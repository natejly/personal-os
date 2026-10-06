"""The connector catalog: a vetted list of MCP servers the user can install by filling in a few fields.

The data is `data/mcp_catalog.json`, shipped inside the package. This module loads it, checks it, and turns an entry plus
the user's field values into the arguments of McpServers.create_server. The rules it enforces exist for one reason: where a
value ends up decides who can read it. Arguments and URLs are stored in plain text and visible to `ps`; only env values and
headers can reach the secret store. So a field marked `secret` may be referenced from `install.env` / `install.headers`
and nowhere else, and `validate` fails the whole file if an entry breaks that.
"""
from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path
from typing import Any

from . import redact

log = logging.getLogger(__name__)

BUNDLED = Path(__file__).parent / "data" / "mcp_catalog.json"
VERSION = 1
CATEGORIES = ["Developer", "Productivity", "Data", "Search & Web", "Communication", "Design", "Cloud & Ops", "Finance", "Knowledge", "Utilities"]
TRANSPORTS = ("stdio", "http", "sse")
RUNTIMES = ("node", "python", "docker", "binary", "remote")
AUTHS = ("none", "api_key", "oauth", "env")
VERIFIED_SOURCES = ("npm", "pypi", "docker", "registry", "vendor")
MAX_DESCRIPTION = 200  # a card's blurb, not documentation
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ICON_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # a lucide-react icon name in kebab-case
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
# A literal credential in a template would ship to every user. Only the rules that identify credentials by shape.
CREDENTIAL_RULES = ("private_key", "url_userinfo", "aws_key", "github_pat", "google_api", "google_oauth", "slack_webhook", "jwt")
# Redact's generic `token` rule would flag an ordinary flag such as --api-endpoint-override, so the prefixes are spelled out here.
PREFIXED_CREDENTIAL = re.compile(r"\b(?:gh[pousr]|sk|xox[baprs])[-_][A-Za-z0-9_-]{20,}\b")


def _refs(text: Any) -> list[str]:
    return PLACEHOLDER.findall(text) if isinstance(text, str) else []


def _templates(install: dict[str, Any]) -> dict[str, list[str]]:
    """Every template string of an install block, by where it will end up."""
    return {"command": [install.get("command") or ""], "args": [str(a) for a in install.get("args") or []],
            "url": [install.get("url") or ""], "env": [str(v) for v in (install.get("env") or {}).values()],
            "headers": [str(v) for v in (install.get("headers") or {}).values()]}


def _looks_credential(text: str) -> bool:
    return bool(PREFIXED_CREDENTIAL.search(text)) or redact.scrub(text, CREDENTIAL_RULES, secret_assign=False) != text


def validate_entry(e: Any) -> list[str]:
    """Every rule violation in one entry, as readable strings. Empty means the entry is installable."""
    if not isinstance(e, dict):
        return ["entry is not an object"]
    eid = e.get("id")
    who = f"{eid}: " if isinstance(eid, str) else ""
    errs: list[str] = []

    def bad(msg: str) -> None:
        errs.append(who + msg)

    if not isinstance(eid, str) or not ID_RE.match(eid):
        bad("id must be lowercase letters, digits and hyphens")
    for k in ("name", "publisher"):
        if not isinstance(e.get(k), str) or not e[k].strip():
            bad(f"{k} is required")
    desc = e.get("description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > MAX_DESCRIPTION:
        bad(f"description must be 1-{MAX_DESCRIPTION} characters")
    if e.get("category") not in CATEGORIES:
        bad("category is not one of the known categories")
    if not isinstance(e.get("icon"), str) or not ICON_RE.match(e["icon"]):
        bad("icon must be a kebab-case icon name")
    if not isinstance(e.get("official"), bool):
        bad("official must be true or false")
    if not isinstance(e.get("docs"), str) or not e["docs"].startswith("https://"):
        bad("docs must be an https:// link")
    transport, runtime, auth = e.get("transport"), e.get("runtime"), e.get("auth")
    if transport not in TRANSPORTS:
        bad("transport must be stdio, http or sse")
    if runtime not in RUNTIMES:
        bad("runtime is not a known runtime")
    if auth not in AUTHS:
        bad("auth must be none, api_key, oauth or env")
    ver = e.get("verified")
    if not isinstance(ver, dict) or ver.get("source") not in VERIFIED_SOURCES or not ver.get("ref") or not ver.get("on"):
        bad("verified needs a source, a ref and a date")

    fields = e.get("fields") if isinstance(e.get("fields"), list) else None
    if fields is None:
        bad("fields must be a list")
        fields = []
    declared: dict[str, dict[str, Any]] = {}
    for f in fields:
        fid = f.get("id") if isinstance(f, dict) else None
        if not isinstance(fid, str) or not FIELD_ID_RE.match(fid):
            bad("a field has a missing or malformed id")
            continue
        if fid in declared:
            bad(f"field {fid} is declared twice")
        declared[fid] = f
        if not isinstance(f.get("label"), str) or not f["label"].strip():
            bad(f"field {fid} needs a label")
        for k in ("secret", "required", "multiple"):
            if k in f and not isinstance(f[k], bool):
                bad(f"field {fid}: {k} must be true or false")
        if f.get("secret") and f.get("default"):
            bad(f"field {fid} is secret and must not have a default")
        if f.get("secret") and f.get("multiple"):
            bad(f"field {fid} is secret and cannot be multiple")

    install = e.get("install")
    if not isinstance(install, dict):
        bad("install is required")
        return errs
    command, url = install.get("command") or "", install.get("url") or ""
    if not isinstance(install.get("args", []), list) or not isinstance(install.get("env", {}), dict) or not isinstance(install.get("headers", {}), dict):
        bad("install.args must be a list, install.env and install.headers objects")
        return errs
    if transport == "stdio":
        if not command.strip():
            bad("a stdio entry needs install.command")
        if url:
            bad("a stdio entry must not have install.url")
    elif transport in ("http", "sse"):
        if not url.startswith("https://"):
            bad("a remote entry needs an https:// install.url")
        if command:
            bad("a remote entry must not have install.command")
    if auth == "oauth" and transport not in ("http", "sse"):
        bad("oauth needs a remote (http or sse) transport")
    if auth == "none" and any(f.get("secret") for f in declared.values()):
        bad("auth none cannot have secret fields")

    used: set[str] = set()
    for where, items in _templates(install).items():
        for text in items:
            for name in _refs(text):
                used.add(name)
                if name not in declared:
                    bad(f"{{{name}}} in install.{where} is not a declared field")
                elif declared[name].get("secret") and where not in ("env", "headers"):
                    bad(f"secret field {name} appears in install.{where}; secrets may only go in env or headers")
        if any(_looks_credential(PLACEHOLDER.sub("", t)) for t in items):
            bad(f"install.{where} contains what looks like a literal credential")
    for fid, f in declared.items():
        if f.get("required") and fid not in used:
            bad(f"required field {fid} is not used anywhere in install")
        if f.get("multiple") and "{" + fid + "}" not in [str(a) for a in install.get("args") or []]:
            bad(f"multiple field {fid} must be exactly one whole argument")
        if f.get("multiple") and any(fid in _refs(t) for w, ts in _templates(install).items() if w != "args" for t in ts):
            bad(f"multiple field {fid} may only be used in args")
    return errs


def validate(doc: Any) -> list[str]:
    """Every problem in a catalog document. Empty means it can be served."""
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("entries"), list):
        return [f"catalog must be an object with version {VERSION} and an entries list"]
    errs: list[str] = []
    seen: set[str] = set()
    for e in doc["entries"]:
        errs.extend(validate_entry(e))
        eid = e.get("id") if isinstance(e, dict) else None
        if isinstance(eid, str):
            if eid in seen:
                errs.append(f"{eid}: duplicate id")
            seen.add(eid)
    return errs


def _read(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("MCP catalog %s is missing or unreadable", path)
        return {"version": VERSION, "entries": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        return {"version": VERSION, "entries": []}
    # An entry that breaks a rule is withheld, not served: a secret in an argument would be stored in plain text.
    good = []
    for e in doc["entries"]:
        errs = validate_entry(e)
        if errs:
            log.warning("MCP catalog entry withheld: %s", "; ".join(errs))
        else:
            good.append(e)
    return {**doc, "entries": good}


@functools.lru_cache(maxsize=1)
def _bundled() -> dict[str, Any]:
    return _read(BUNDLED)


def load(path: Path | str | None = None) -> dict[str, Any]:
    """The bundled catalog (cached for the process), or the one at `path` (read fresh: for tests)."""
    return _bundled() if path is None else _read(Path(path))


def get(entry_id: str, path: Path | str | None = None) -> dict[str, Any] | None:
    return next((e for e in load(path)["entries"] if e["id"] == entry_id), None)


def _fill(template: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), template)


def render_install(entry: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """The create_server arguments for `entry` with the user's `values`.

    Raises ValueError naming the first required field that is empty. Values never appear in an error. An env/header
    template that mentions a secret field goes to `secrets` / `headers` (the secret store); the rest to plain `env`.
    """
    fields = {f["id"]: f for f in entry.get("fields") or []}
    vals: dict[str, str] = {}
    for fid, f in fields.items():
        v = str((values or {}).get(fid) or "").strip() or str(f.get("default") or "")
        if not v and f.get("required"):
            raise ValueError(f"Missing required field: {fid}")
        vals[fid] = v
    install = entry.get("install") or {}
    args: list[str] = []
    for a in (str(x) for x in install.get("args") or []):
        ref = PLACEHOLDER.fullmatch(a)
        f = fields.get(ref.group(1)) if ref else None
        if f and f.get("multiple"):
            args.extend(p.strip() for p in re.split(r"[\n,]", vals[f["id"]]) if p.strip())
        elif f and not vals[f["id"]] and not f.get("required"):
            continue  # an optional argument left empty is dropped, not passed as ""
        else:
            args.append(_fill(a, vals))
    env: dict[str, str] = {}
    secrets: dict[str, str] = {}
    for k, tpl in (install.get("env") or {}).items():
        v = _fill(str(tpl), vals)
        if not v.strip():
            continue
        (secrets if any(fields.get(r, {}).get("secret") for r in _refs(tpl)) else env)[k] = v
    headers = {k: v for k, tpl in (install.get("headers") or {}).items() if (v := _fill(str(tpl), vals)).strip()}
    return {"name": entry["name"], "transport": entry["transport"], "command": install.get("command") or "", "args": args,
            "env": env, "secrets": secrets, "url": _fill(install.get("url") or "", vals), "headers": headers,
            "description": entry.get("description") or "", "catalog_id": entry["id"]}
