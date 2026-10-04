"""SKILL.md (agentskills.io) import and export, without a YAML dependency.

The frontmatter this accepts is the flat subset the spec uses: `key: value` lines and one level of
`metadata:` with indented `k: value` children. Importing only ever produces *fields*; the caller
turns them into a candidate through Skills.propose, so a pasted file can never approve itself.
`allowed-tools` is ignored on purpose: a skill never grants a permission here.
"""
from __future__ import annotations

import re
from typing import Any

from .learn import MAX_SKILL_DESCRIPTION, MAX_SKILL_NAME, MAX_SKILL_PROCEDURE

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPAT = 500
KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
BUNDLED_RE = re.compile(r"\b(?:scripts|references|assets)/", re.I)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if v[0] == '"' else inner.replace("''", "'")
    return v


def parse(text: str) -> dict[str, Any]:
    """-> {'frontmatter', 'body', 'errors', 'warnings'}. Errors mean the file cannot be imported."""
    errors: list[str] = []
    warnings: list[str] = []
    fm: dict[str, Any] = {}
    lines = (text or "").replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return {"frontmatter": {}, "body": (text or "").strip(), "errors": ["SKILL.md must start with a --- frontmatter block"], "warnings": []}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {"frontmatter": {}, "body": "", "errors": ["the frontmatter block is never closed with ---"], "warnings": []}
    current: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[0] in " \t"
        key, sep, val = raw.strip().partition(":")
        if not sep:
            errors.append(f"cannot read frontmatter line: {raw.strip()[:60]}")
            continue
        if indented and current == "metadata":
            if isinstance(fm.get("metadata"), dict):  # `metadata: x` then a child: the map check below reports it
                fm["metadata"][key.strip()] = _unquote(val)
            continue
        current = key.strip()
        if current == "metadata" and not val.strip():
            fm["metadata"] = {}
        else:
            fm[current] = _unquote(val)
    body = "\n".join(lines[end + 1:]).strip()

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name is required")
    elif len(name) > MAX_NAME or not NAME_RE.match(name):
        errors.append("name must be 1-64 characters of lowercase letters, digits and single hyphens, "
                      "with no leading, trailing or doubled hyphen")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc:
        errors.append("description is required")
    elif len(desc) > MAX_DESCRIPTION:
        errors.append(f"description is {len(desc)} characters; the limit is {MAX_DESCRIPTION}")
    if isinstance(fm.get("compatibility"), str) and len(fm["compatibility"]) > MAX_COMPAT:
        errors.append(f"compatibility is over {MAX_COMPAT} characters")
    if "metadata" in fm and not isinstance(fm["metadata"], dict):
        errors.append("metadata must be a map of strings")
    if "allowed-tools" in fm:
        warnings.append("allowed-tools ignored: skills never grant permissions here")
    for k in fm:
        if k not in KNOWN_KEYS:
            warnings.append(f"unknown frontmatter key '{k}' ignored")
    return {"frontmatter": fm, "body": body, "errors": errors, "warnings": warnings}


def to_skill_fields(parsed: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """(name, description, procedure, extra warnings). A body that is too long is an error, never truncated."""
    fm, body = parsed["frontmatter"], parsed["body"]
    warnings = list(parsed.get("warnings") or [])
    errors = list(parsed.get("errors") or [])
    name = str(fm.get("name") or "").replace("-", " ").strip().capitalize()[:MAX_SKILL_NAME]
    desc = str(fm.get("description") or "").strip()
    if len(desc) > MAX_SKILL_DESCRIPTION:
        desc = desc[:MAX_SKILL_DESCRIPTION]
        warnings.append(f"description trimmed to {MAX_SKILL_DESCRIPTION} characters")
    if len(body) > MAX_SKILL_PROCEDURE:
        errors.append(f"the body is {len(body)} characters; a procedure here holds at most {MAX_SKILL_PROCEDURE}")
    if BUNDLED_RE.search(body):
        warnings.append("bundled files are not imported: the body refers to scripts/, references/ or assets/")
    parsed["errors"] = errors
    return name, desc, body, warnings


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s[:MAX_NAME].strip("-")) or "skill"


def _quote(v: str) -> str:
    one = " ".join((v or "").split())
    return '"' + one.replace("\\", "\\\\").replace('"', '\\"') + '"' if re.search(r"[:#\"'{}\[\]]|^\s|\s$", one) or not one else one


def render(skill: dict[str, Any]) -> str:
    desc = " ".join((skill.get("description") or skill.get("name") or "").split())[:MAX_DESCRIPTION] or "A saved procedure"
    return (f"---\nname: {slug(skill.get('name') or '')}\ndescription: {_quote(desc)}\n"
            f"metadata:\n  source: grain\n  status: {skill.get('status') or 'candidate'}\n---\n\n"
            f"{(skill.get('procedure') or '').strip()}\n")


class ImportError_(ValueError):
    """The file cannot become a skill; `.errors` says why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def import_text(skills: Any, lint: Any, text: str, project_id: str | None = None,
                references: dict[str, str] | None = None) -> dict[str, Any]:
    """SKILL.md text -> a *candidate* row plus lint findings. Never approves: Skills.propose cannot."""
    parsed = parse(text)
    name, desc, body, warnings = to_skill_fields(parsed)
    if parsed["errors"]:
        raise ImportError_(parsed["errors"])
    refs = {k: v for k, v in (references or {}).items() if str(k).startswith("references/")}
    if len(refs) < len(references or {}):  # scripts/ and assets/ are never stored, so nothing can run them
        warnings.append("only references/ files are imported; scripts/ and assets/ files were dropped")
    row = skills.propose(name, desc, body, project_id=project_id, source="user", references=refs)
    return {"skill": row, "findings": lint(row["name"], row["description"], row["procedure"], row["id"]), "warnings": warnings}
