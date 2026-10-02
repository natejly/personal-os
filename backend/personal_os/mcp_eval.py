"""Evaluation of a third-party MCP server, before its tools are offered to the model.

What this checks:
  * the server starts, handshakes and answers tools/list (the only live step)
  * every tool's parameters are well-formed JSON Schema
  * no tool name, description or schema string reads as an instruction to the model - a tool
    description is pasted into every request's tools array, so it is a prompt-injection vector
    whether or not the tool is ever called
  * a tool that claims to be read-only does not describe itself writing
  * no tool name collides with a built-in, and none tries to look namespaced

What this CANNOT do, stated here and in every report because the report is a trust signal:
**a static evaluation cannot show that a server is safe.** It reads what the server says about
itself. The server's code is not read, its filesystem and network activity are not observed, and
nothing forces a tool to behave like its description - it can behave differently on the one call
that matters, or only after a week. A clean report means nothing suspicious was found in what the
server advertises, and no more than that. A server that changes its tools after approval is a
separate problem, handled by the approved-shape rule in mcp_servers (a stale schema_hash decays
an 'on' grant back to 'ask').
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .mcp_servers import EVAL_STATUSES, RESERVED_PREFIX, RESERVED_TOOL_NAMES, McpServers

SEVERITIES = ("info", "warn", "fail")

# What a static pass cannot see. Carried in every report; the UI is expected to show it.
LIMITS = (
    "This is a static check of what the server advertises, not proof that it is safe.",
    "The server's code is not read and its filesystem or network activity is not observed.",
    "A tool can behave differently from its description, including only on the call that matters.",
    "A tool that hangs, crashes or leaks data is only found by running it; the client bounds the damage with timeouts, it does not prevent it.",
    "Injection detection is pattern matching. Novel phrasing gets through, and unusual wording is flagged that is not an attack.",
)

# Text in a tool name or description that is addressed to the model rather than describing a tool.
INJECTION_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("override_instructions", r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(previous|prior|above|earlier|all)\b[^.]{0,20}\b(instruction|prompt|rule|direction|system)",
     "fail", "tells the model to drop its instructions"),
    ("role_markup", r"<\s*/?\s*(system|assistant|user|human|tool_result|thinking)\s*>|\[/?(INST|SYS)\]|<\|[^|>]{1,40}\|>",
     "fail", "fakes a conversation-role or control marker"),
    ("developer_mode", r"\b(developer mode|jailbreak|dan mode|unrestricted mode|no restrictions apply)\b",
     "fail", "claims a privileged mode"),
    ("concealment", r"\b(do not|don't|never)\b[^.]{0,30}\b(tell|inform|mention|show|reveal|disclose)\b[^.]{0,20}\b(the )?(user|human|operator)",
     "fail", "asks the model to hide something from the user"),
    ("credential_interest", r"\b(id_rsa|\.ssh|private key|api[ _-]?key|password|secret token|credentials?|\.env)\b",
     "warn", "names credentials in text the model reads"),
    ("instruction_voice", r"(?:^|[.\s])(?:you must|you should always|always call|first call|before answering|after answering|from now on)\b",
     "warn", "gives the model an instruction"),
    ("role_framing", r"(?:^|\n)\s*(system|assistant|user)\s*:", "warn", "formats itself as a chat turn"),
    ("tool_redirection", r"\b(instead of|rather than)\b[^.]{0,30}\b(calling|using|asking)\b", "warn",
     "redirects the model away from another tool"),
    ("invisible_text", r"[​-‏‪-‮⁠﻿]", "fail",
     "contains zero-width or bidirectional-override characters"),
)

JSON_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
COMPOSITORS = ("anyOf", "oneOf", "allOf", "not", "$ref", "enum", "const")

WRITE_VERBS = ("delete", "deletes", "remove", "removes", "write", "writes", "overwrite", "create", "creates",
               "update", "updates", "modify", "modifies", "patch", "upload", "uploads", "send", "sends", "post",
               "drop", "truncate", "rename", "move", "install", "execute", "run", "kill", "purge", "wipe",
               "revoke", "grant", "pay", "charge", "transfer", "publish", "merge", "push")
READ_ONLY_CLAIMS = (r"\bread[- ]only\b", r"\bdoes not (modify|change|write|delete)\b", r"\bnever (modifies|writes|deletes)\b",
                    r"\bwithout (modifying|changing|writing)\b")

MAX_DESCRIPTION_CHARS = 2000   # a tool description longer than this is paying for prompt space it does not need
MAX_SCHEMA_BYTES = 20_000
MAX_SCHEMA_DEPTH = 8
MAX_NAME_CHARS = 64
EXCERPT_CHARS = 160

EVAL_MODEL = "static"  # no model is consulted; recorded so a report is never mistaken for a judged one


def _finding(code: str, severity: str, where: str, detail: str, excerpt: str = "") -> dict[str, Any]:
    return {"code": code, "severity": severity if severity in SEVERITIES else "warn", "where": where,
            "detail": detail, "excerpt": excerpt[:EXCERPT_CHARS]}


def scan_text(text: str, where: str) -> list[dict[str, Any]]:
    """Prompt-injection patterns in text that reaches the model. Pattern matching, nothing cleverer."""
    out: list[dict[str, Any]] = []
    if not text:
        return out
    for code, pattern, severity, detail in INJECTION_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 20)
            out.append(_finding(code, severity, where, detail, text[start:m.end() + 40]))
    if len(text) > MAX_DESCRIPTION_CHARS:
        out.append(_finding("oversized_text", "warn", where,
                            f"{len(text)} characters of text go into every request", text[:EXCERPT_CHARS]))
    return out


def _schema_strings(node: Any, depth: int = 0) -> Iterable[str]:
    """Every human-readable string in a schema: descriptions and titles reach the model too."""
    if depth > MAX_SCHEMA_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("description", "title", "$comment") and isinstance(value, str):
                yield value
            else:
                yield from _schema_strings(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _schema_strings(item, depth + 1)


def _depth(node: Any, depth: int = 0) -> int:
    if depth > MAX_SCHEMA_DEPTH + 1 or not isinstance(node, (dict, list)):
        return depth
    children = node.values() if isinstance(node, dict) else node
    return max((_depth(c, depth + 1) for c in children), default=depth)


def check_schema(parameters: Any, where: str) -> list[dict[str, Any]]:
    """Well-formedness of a tool's JSON Schema. A model cannot call what it cannot parse."""
    out: list[dict[str, Any]] = []
    if not isinstance(parameters, dict):
        return [_finding("schema_not_object", "fail", where,
                         f"parameters must be a JSON Schema object, got {type(parameters).__name__}")]
    if parameters.get("type") != "object":
        out.append(_finding("schema_root_not_object", "fail", where,
                            f"the root of a tool schema must be type 'object', got {parameters.get('type')!r}"))
    props = parameters.get("properties", {})
    if "properties" in parameters and not isinstance(props, dict):
        out.append(_finding("schema_properties_shape", "fail", where,
                            f"'properties' must be a map of name to schema, got {type(props).__name__}"))
        props = {}
    for name, sub in (props.items() if isinstance(props, dict) else ()):
        if not isinstance(sub, dict):
            out.append(_finding("schema_property_shape", "fail", f"{where}.{name}",
                                f"property schema must be an object, got {type(sub).__name__}"))
            continue
        t = sub.get("type")
        if t is None and not any(k in sub for k in COMPOSITORS):
            out.append(_finding("schema_property_untyped", "warn", f"{where}.{name}", "property declares no type"))
        elif isinstance(t, str) and t not in JSON_SCHEMA_TYPES:
            out.append(_finding("schema_unknown_type", "fail", f"{where}.{name}", f"{t!r} is not a JSON Schema type"))
        elif isinstance(t, list) and not set(t) <= JSON_SCHEMA_TYPES:
            out.append(_finding("schema_unknown_type", "fail", f"{where}.{name}", f"{t!r} contains unknown types"))
    required = parameters.get("required", [])
    if "required" in parameters and not isinstance(required, list):
        out.append(_finding("schema_required_shape", "fail", where,
                            f"'required' must be a list, got {type(required).__name__}"))
    else:
        for name in required:
            if not isinstance(name, str):
                out.append(_finding("schema_required_shape", "fail", where, f"required entry {name!r} is not a string"))
            elif isinstance(props, dict) and name not in props:
                out.append(_finding("schema_required_undeclared", "fail", where,
                                    f"required property {name!r} is not declared in 'properties'"))
    try:
        size = len(json.dumps(parameters, default=str))
    except (TypeError, ValueError):
        return out + [_finding("schema_not_json", "fail", where, "schema is not JSON-serialisable")]
    if size > MAX_SCHEMA_BYTES:
        out.append(_finding("schema_oversized", "warn", where, f"{size} bytes of schema go into every request"))
    if _depth(parameters) > MAX_SCHEMA_DEPTH:
        out.append(_finding("schema_too_deep", "warn", where, f"schema nests deeper than {MAX_SCHEMA_DEPTH} levels"))
    for text in _schema_strings(parameters):
        out.extend(scan_text(text, f"{where}.schema"))
    return out


def claims_read_only(tool: dict[str, Any]) -> bool:
    annotations = tool.get("annotations") or {}
    if annotations.get("read_only_hint") or annotations.get("readOnlyHint"):
        return True
    description = str(tool.get("description") or "")
    return any(re.search(p, description, re.IGNORECASE) for p in READ_ONLY_CLAIMS)


def check_read_only(tool: dict[str, Any], where: str) -> list[dict[str, Any]]:
    """A read-only claim contradicted by the tool's own words. Keyword-level, and it says so."""
    if not claims_read_only(tool):
        return []
    text = f"{tool.get('name') or ''} {tool.get('description') or ''}".lower()
    hits = sorted({v for v in WRITE_VERBS if re.search(rf"\b{v}\b", text)})
    if not hits:
        return []
    return [_finding("read_only_contradicted", "warn", where,
                     f"declares itself read-only but describes writing ({', '.join(hits)}); "
                     "a read-only claim is the server's own word either way",
                     str(tool.get("description") or ""))]


def check_name(name: str, where: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lowered = (name or "").lower()
    if lowered in RESERVED_TOOL_NAMES:
        out.append(_finding("shadows_builtin", "warn", where,
                            f"exports a tool named {name!r}, which is a built-in tool name; the call is "
                            "namespaced to mcp__<server>__<tool> so the built-in cannot be shadowed, but a "
                            "server with no reason to pick that name picked it"))
    if lowered.startswith(RESERVED_PREFIX):
        out.append(_finding("fake_namespace", "warn", where,
                            f"{name!r} starts with {RESERVED_PREFIX}, the prefix this app adds itself"))
    if not name:
        out.append(_finding("nameless_tool", "fail", where, "tool has no name"))
    elif len(name) > MAX_NAME_CHARS:
        out.append(_finding("name_too_long", "warn", where, f"name is {len(name)} characters"))
    if name and not all(c.isascii() for c in name):
        out.append(_finding("non_ascii_name", "warn", where, "name contains non-ASCII characters (homoglyph risk)", name))
    return out


# Words a tool might be called without meaning another tool: a bare-name mention of these is just English.
COMMON_NAMES = frozenset({
    "search", "create", "delete", "update", "write", "fetch", "query", "email", "message", "messages", "files",
    "issue", "issues", "browse", "upload", "download", "status", "report", "export", "import", "lookup",
    "execute", "convert", "summary", "document", "content", "project", "comment", "record", "records",
})
MIN_BARE_NAME = 5
# What turns a mention of another tool into steering: the instruction patterns above, plus ordering words.
_DIRECTIVE = re.compile(
    r"(?:^|[.\s])(?:you must|you should always|always (?:call|use)|first call|before (?:calling|using|invoking|running)|"
    r"after (?:calling|using)|from now on|instead of|rather than|do not use|don't use|never use)\b", re.I)


def _mentions(text: str, needle: str) -> bool:
    return bool(needle) and re.search(r"(?<![a-z0-9_])" + re.escape(needle.lower()) + r"(?![a-z0-9_])", text.lower()) is not None


def check_shadowing(tool: dict[str, Any], other_tools: list[dict[str, Any]], where: str) -> list[dict[str, Any]]:
    """Text in one server's tool that names another server's tool, or tells the model what to do about it.

    A mention alone is a warning (`references_other_tool`); a mention inside an instruction is a fail
    (`shadows_other_tool`), because that is how one connector steers calls to another. Same-server
    mentions are ignored. `tool` needs `slug`/`server_slug`; others need `slug`, `name`, `server_slug`.
    """
    mine = str(tool.get("server_slug") or (str(tool.get("slug") or "").split("__") + ["", ""])[1])
    texts = [("description", str(tool.get("description") or ""))]
    props = (tool.get("parameters") or {}).get("properties") if isinstance(tool.get("parameters"), dict) else None
    for arg, spec in (props or {}).items():
        if isinstance(spec, dict) and spec.get("description"):
            texts.append((f"arguments.{arg}", str(spec["description"])))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for other in other_tools:
        if str(other.get("server_slug") or "") == mine or other.get("slug") == tool.get("slug"):
            continue
        names = [str(other.get("slug") or "")]
        bare = str(other.get("name") or "")
        if len(bare) >= MIN_BARE_NAME and bare.lower() not in COMMON_NAMES:
            names.append(bare)
        srv = str(other.get("server_slug") or "")
        if len(srv) >= MIN_BARE_NAME and srv.lower() not in COMMON_NAMES:
            names.append(srv)
        for field, text in texts:
            hit = next((n for n in names if _mentions(text, n)), None)
            key = (str(other.get("slug")), field)
            if not hit or key in seen:
                continue
            seen.add(key)
            m = re.search(re.escape(hit), text, re.I)
            excerpt = text[max(0, m.start() - 40):m.end() + 60] if m else text
            if _DIRECTIVE.search(text):
                out.append(_finding("shadows_other_tool", "fail", f"{where}.{field}",
                                    f"tells the model what to do about {other.get('slug')}, a tool from another server", excerpt))
            else:
                out.append(_finding("references_other_tool", "warn", f"{where}.{field}",
                                    f"mentions {other.get('slug')}, a tool from another server", excerpt))
    return out


def status_for(findings: Iterable[dict[str, Any]]) -> str:
    severities = {f.get("severity") for f in findings}
    if "fail" in severities:
        return "fail"
    if "warn" in severities:
        return "warn"
    return "pass"


def evaluate_tool(tool: dict[str, Any], slug: str = "") -> dict[str, Any]:
    """Static verdict on one advertised tool."""
    name = str(tool.get("name") or "")
    where = slug or name or "tool"
    findings = check_name(name, where)
    findings += scan_text(name, f"{where}.name")
    findings += scan_text(str(tool.get("description") or ""), f"{where}.description")
    findings += check_schema(tool.get("parameters", tool.get("inputSchema")), where)
    findings += check_read_only(tool, where)
    return {"name": name, "slug": slug, "status": status_for(findings), "findings": findings}


def evaluate_tools(tools: list[dict[str, Any]], slugs: dict[str, str] | None = None) -> dict[str, Any]:
    """Static verdict on a server's whole advertised surface."""
    results = [evaluate_tool(t, (slugs or {}).get(str(t.get("name") or ""), "")) for t in tools]
    findings = [f for r in results for f in r["findings"]]
    status = status_for(findings)
    return {"status": status, "tools": results, "findings": findings, "limits": list(LIMITS),
            "summary": summarize(status, len(tools), findings), "model": EVAL_MODEL}


def summarize(status: str, tool_count: int, findings: list[dict[str, Any]]) -> str:
    fails = sum(1 for f in findings if f["severity"] == "fail")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    counted = f"{tool_count} tool{'s' if tool_count != 1 else ''}"
    if status == "pass":
        return f"{counted} advertised, nothing suspicious in what the server says about itself. That is not a safety guarantee."
    if status == "warn":
        return f"{counted} advertised, {warns} thing{'s' if warns != 1 else ''} worth a look before trusting it."
    return f"{counted} advertised, {fails} serious problem{'s' if fails != 1 else ''} and {warns} warning{'s' if warns != 1 else ''}."


async def evaluate_config(client: Any, config: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
    """Evaluate a launch config that was never saved, so the decision to trust can come first."""
    probe = await client.probe(config, timeout=timeout)
    if not probe["ok"]:
        return {"status": "error", "tools": [], "findings": [
            _finding("connect_failed", "fail", "server", probe["error"] or "the server did not connect")],
            "limits": list(LIMITS), "summary": f"Did not connect: {probe['error'] or 'unknown error'}",
            "model": EVAL_MODEL, "server_info": probe["server_info"], "stderr": probe["stderr"]}
    report = evaluate_tools(probe["tools"])
    report["server_info"] = probe["server_info"]
    report["stderr"] = probe["stderr"]
    return report


async def evaluate_server(client: Any, store: McpServers, server_id: str, *, record: bool = True,
                          timeout: float | None = None) -> dict[str, Any]:
    """Probe a server, evaluate what it advertises, and (by default) file the report.

    The probe is a throwaway connection, so this can be run on a server that has never been
    enabled - which is the point: the decision to trust it comes before the decision to use it.
    """
    if store.server(server_id) is None:
        return {"status": "error", "tools": [], "findings": [_finding("no_such_server", "fail", "server", "no such server")],
                "limits": list(LIMITS), "summary": "No such server.", "model": EVAL_MODEL, "server_info": {},
                "stderr": [], "server_id": server_id}
    probe = await client.probe_server(server_id, timeout=timeout)
    if not probe["ok"]:
        report = {"status": "error", "tools": [], "findings": [
            _finding("connect_failed", "fail", "server", probe["error"] or "the server did not connect")],
            "limits": list(LIMITS), "summary": f"Did not connect: {probe['error'] or 'unknown error'}",
            "model": EVAL_MODEL, "server_info": probe["server_info"], "stderr": probe["stderr"]}
    else:
        slugs = {t["name"]: t["slug"] for t in store.tools(server_id, include_missing=True)}
        report = evaluate_tools(probe["tools"], slugs)
        report["server_info"] = probe["server_info"]
        report["stderr"] = probe["stderr"]
        instructions = str(probe["server_info"].get("instructions") or "")
        extra = scan_text(instructions, "server.instructions")
        if extra:  # a server's `instructions` string is prepended to the system prompt by some clients
            report["findings"] = report["findings"] + extra
            report["status"] = status_for(report["findings"])
            report["summary"] = summarize(report["status"], len(probe["tools"]), report["findings"])
    report["server_id"] = server_id
    if record:
        status = report["status"] if report["status"] in EVAL_STATUSES else "error"
        report["eval"] = store.record_eval(server_id, status, report["summary"], report["findings"], model=EVAL_MODEL)
    return report
