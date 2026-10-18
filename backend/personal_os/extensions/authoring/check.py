"""`grain ext check`: everything about an extension folder that can be known without running it (design doc section 6.2)."""
from __future__ import annotations

import ast
import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ...mcp_servers import RESERVED_TOOL_NAMES
from ...runtime import loop_fence
from ...skillbuild import _authority
from ..catalog import HOOKS
from ..manifest import Manifest, ManifestError, load_manifest

BUILTINS = Path(__file__).parents[1]  # the built-in extensions, one folder each
CORE_SERVICES = frozenset({"mcp", "google"})  # lent by the app through ExtensionContext.services
TOOL_NAME = re.compile(r"[a-z][a-z0-9_]*")
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?(\w+)", re.IGNORECASE)
CODE_EXCLUDED = {"tests", "dist", "node_modules", "__pycache__"}


@dataclass(frozen=True)
class Finding:
    level: str  # error | warn | note
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: [{self.code}] {self.message}"


def check(root: Path, previous: Path | None = None) -> list[Finding]:
    """All findings for the extension in `root`. `previous` is an earlier extension.json or extension.lock.json to diff
    the declared permissions against; the folder's own lock file is used when there is one."""
    try:
        manifest = load_manifest(root / "extension.json")
    except ManifestError as e:
        return [Finding("error", "manifest", str(e))]
    others = list(_builtin_manifests(root))
    out = [*_collisions(root, manifest, others), *_requires(manifest, others)]
    if manifest.kind == "profile":
        return out  # the manifest loader already refused any code, hooks or tools a profile carries
    out += _tools(root, manifest)
    out += _code(root, manifest)
    out += _data_tables(root, manifest)
    out += _authority_lint(root, manifest)
    if previous and not previous.is_file():
        return [*out, Finding("error", "permission-diff", f"{previous} is not a file to compare permissions against")]
    return [*out, *_permission_diff(manifest, previous or root / "extension.lock.json")]


def _builtin_manifests(root: Path) -> Iterator[Manifest]:
    for path in sorted(BUILTINS.glob("*/extension.json")):
        if path.parent.resolve() != root.resolve():
            yield load_manifest(path)


@functools.cache
def _reserved_tools() -> tuple[set[str], set[str]]:
    """(tool names a core Toolbox registers, the danger levels they use)."""
    from ...tools import Toolbox
    box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    return set(box.specs), {s.danger for s in box.specs.values()}


def _collisions(root: Path, manifest: Manifest, others: list[Manifest]) -> list[Finding]:
    out: list[Finding] = []
    if manifest.id in {m.id for m in others}:
        out.append(Finding("error", "id-collision", f"a built-in extension already has the id {manifest.id!r}"))
    taken = _reserved_tools()[0] | RESERVED_TOOL_NAMES | {t["name"] for m in others for t in m.contributes.get("tools", ())}
    if BUILTINS in root.resolve().parents:  # the reserved list names the built-in extensions' own tools too
        taken -= {t["name"] for t in manifest.contributes.get("tools", ())}
    seen: set[str] = set()
    for tool in manifest.contributes.get("tools", ()):
        name = tool["name"]
        if name in taken or name.startswith("mcp__"):
            out.append(Finding("error", "tool-collision", f"tool {name!r} is already taken by the app"))
        elif name in seen:
            out.append(Finding("error", "tool-collision", f"tool {name!r} is declared twice"))
        seen.add(name)
    return out


def _requires(manifest: Manifest, builtin: list[Manifest]) -> list[Finding]:
    out: list[Finding] = []
    for ext in manifest.requires_extensions:
        if ext not in {m.id for m in builtin}:
            out.append(Finding("error", "requires", f"required extension {ext!r} does not exist"))
    available = CORE_SERVICES | {s for m in builtin for s in m.provides_services}
    for service in manifest.requires_services:
        if service not in available:
            out.append(Finding("error", "requires", f"required service {service!r} is not provided by the app or a built-in extension"))
    return out


# ---- tools ----
def _tools(root: Path, manifest: Manifest) -> list[Finding]:
    out: list[Finding] = []
    _, dangers = _reserved_tools()
    for tool in manifest.contributes.get("tools", ()):
        name = tool["name"]
        if not TOOL_NAME.fullmatch(name):
            out.append(Finding("error", "tool-schema", f"tool {name!r}: the name must match [a-z][a-z0-9_]*"))
        if tool.get("danger") not in dangers:
            out.append(Finding("error", "tool-schema", f"tool {name!r}: danger must be one of {', '.join(sorted(dangers))}"))
        if not isinstance(tool.get("group"), str) or not tool["group"]:
            out.append(Finding("error", "tool-schema", f"tool {name!r}: declare its group"))
        if not tool.get("description"):
            out.append(Finding("warn", "tool-schema", f"tool {name!r}: no description"))
        if "schema" in tool:
            out += _tool_schema(root, name, tool["schema"])
    return out


def _tool_schema(root: Path, tool: str, rel: Any) -> list[Finding]:
    path = root / rel if isinstance(rel, str) and ".." not in Path(rel).parts and not Path(rel).is_absolute() else None
    if path is None or not path.is_file():
        return [Finding("error", "tool-schema", f"tool {tool!r}: schema {rel!r} is not a file inside the extension")]
    try:
        schema = json.loads(path.read_text("utf-8"))
    except ValueError as e:
        return [Finding("error", "tool-schema", f"tool {tool!r}: {rel} is not valid JSON ({e})")]
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(props, dict):
        return [Finding("error", "tool-schema", f"tool {tool!r}: {rel} must be an object schema with properties")]
    out = [Finding("error", "tool-schema", f"tool {tool!r}: required {r!r} is not a property") for r in schema.get("required", ()) if r not in props]
    for key, sub in props.items():
        if not isinstance(sub, dict) or not ({"type", "enum", "anyOf"} & set(sub)):
            out.append(Finding("error", "tool-schema", f"tool {tool!r}: property {key!r} has no type"))
        elif not sub.get("description"):
            out.append(Finding("warn", "tool-schema", f"tool {tool!r}: property {key!r} has no description"))
    return out


# ---- code ----
def _python_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if not CODE_EXCLUDED & set(p.relative_to(root).parts)]


def _code(root: Path, manifest: Manifest) -> list[Finding]:
    out: list[Finding] = []
    entry = root / "extension.py"
    if not entry.is_file():
        return [Finding("error", "code", "extension.py is missing")]
    subscribed: set[str] = set()
    specs: list[dict[str, str | None]] = []
    uses_context = dynamic = False
    provides_loop = any(e["slot"] == "agent.loop" for e in manifest.contributes.get("runtime", ()))
    for path in _python_files(root):
        rel = path.relative_to(root)
        try:
            tree = ast.parse(path.read_text("utf-8"), str(path))
        except SyntaxError as e:
            out.append(Finding("error", "code", f"{rel}:{e.lineno}: {e.msg}"))
            continue
        out += _imports(rel, tree)
        if provides_loop:  # the agent loop import fence (design doc 10.10.3): what a loop may import is checked on every file of the extension
            out += [Finding("error", "loop-fence", f"{rel}: {problem}") for problem in loop_fence.violations(tree)]
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and _is_api(n.func.value)):
            if call.func.attr == "context":
                uses_context = True
            elif call.func.attr == "on":
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    subscribed.add(call.args[0].value)
                else:
                    dynamic = True
        specs += [spec for n in ast.walk(tree) if isinstance(n, ast.Call) and (spec := _tool_spec(n))]
        if path == entry:
            out += _activate(tree)
    out += _tool_mismatches(manifest, specs)
    for hook in sorted(subscribed):
        if hook not in HOOKS:
            out.append(Finding("error", "unknown-hook", f"api.on({hook!r}): no such hook"))
        elif hook not in manifest.hooks:
            out.append(Finding("error", "undeclared-hook", f"api.on({hook!r}) but extension.json does not declare it under hooks"))
    if uses_context and "context.contribute" not in manifest.hooks:
        out.append(Finding("error", "undeclared-hook", "api.context(...) needs extension.json to declare the context.contribute hook"))
    if dynamic:
        out.append(Finding("warn", "dynamic-hook", "an api.on call names its hook with something other than a string literal, so it cannot be checked"))
    else:
        used = subscribed | ({"context.contribute"} if uses_context else set())
        out += [Finding("warn", "unused-hook", f"extension.json declares {h!r} but the code never subscribes to it") for h in manifest.hooks if h not in used]
    return out


def _tool_spec(call: ast.Call) -> dict[str, str | None] | None:
    """The name, group and danger a `ToolSpec(...)` call gives its tool; None for a field that is not a string literal."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "ToolSpec" or isinstance(call.func, ast.Attribute) and call.func.attr == "ToolSpec"):
        return None
    given = {**dict(zip(("name", "description", "parameters", "fn", "group", "danger"), call.args)), **{k.arg: k.value for k in call.keywords if k.arg}}
    return {"name": _literal(given.get("name")), "group": _literal(given.get("group")),
            "danger": _literal(given["danger"]) if "danger" in given else "safe"}


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _tool_mismatches(manifest: Manifest, specs: list[dict[str, str | None]]) -> list[Finding]:
    """The tools the code registers against the ones extension.json declares: the host refuses to load an extension where they differ."""
    declared = {t["name"]: t for t in manifest.contributes.get("tools", ())}
    out: list[Finding] = []
    for spec in specs:
        name = spec["name"]
        if name is None:
            continue
        if name not in declared:
            out.append(Finding("error", "tool-mismatch", f"the code registers tool {name!r} but extension.json does not declare it"))
            continue
        for field in ("group", "danger"):
            if spec[field] is not None and spec[field] != declared[name].get(field):
                out.append(Finding("error", "tool-mismatch", f"tool {name!r}: the code says {field} {spec[field]!r}, extension.json says {declared[name].get(field)!r}"))
    if all(spec["name"] for spec in specs):
        registered = {spec["name"] for spec in specs}
        out += [Finding("error", "tool-mismatch", f"extension.json declares tool {name!r} but the code never registers it") for name in declared if name not in registered]
    return out


def _data_tables(root: Path, manifest: Manifest) -> list[Finding]:
    """With its own migrations folder, every table data.tables names must be created by one of them (a built-in's tables come from the app's own schema)."""
    folder = manifest.data.get("migrations")
    if not folder:
        return []
    created = {m.group(1) for sql in (root / folder).glob("*.sql") for m in CREATE_TABLE.finditer(sql.read_text("utf-8"))}
    return [Finding("error", "data-tables", f"data.tables names {t!r} but no migration creates it") for t in manifest.data.get("tables", ()) if t not in created]


def _is_api(node: ast.expr) -> bool:
    """`api`, `self.api` and the like: the extension object, whatever the class around it."""
    return (isinstance(node, ast.Name) and node.id == "api") or (isinstance(node, ast.Attribute) and node.attr == "api")


def _imports(rel: Path, tree: ast.AST) -> list[Finding]:
    out: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            if node.level > 1:
                out.append(Finding("error", "forbidden-import", f"{rel}:{node.lineno}: a relative import may not leave the extension folder"))
        else:
            continue
        for name in names:
            if name == "personal_os" or name.startswith("personal_os."):
                out.append(Finding("error", "forbidden-import", f"{rel}:{node.lineno}: import {name}: an extension imports from grain_sdk, not from personal_os"))
    return out


def _activate(tree: ast.Module) -> list[Finding]:
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "activate"]
    if not fns:
        return [Finding("error", "code", "extension.py defines no activate(api)")]
    args = fns[0].args
    if len(args.posonlyargs) + len(args.args) != 1:
        return [Finding("error", "code", "activate must take exactly one argument, the api")]
    return []


# ---- text the model will read ----
def _authority_lint(root: Path, manifest: Manifest) -> list[Finding]:
    """Static prompt text must not claim authority (the check a skill's procedure gets): descriptions, tool schemas and
    the literal text handed to Contribution(...) or ToolSpec(...)."""
    texts = {"extension.json": [manifest.name, manifest.description]}
    for tool in manifest.contributes.get("tools", ()):
        texts[f"tool {tool['name']}"] = [tool.get("description", "")]
        path = root / tool["schema"] if isinstance(tool.get("schema"), str) else None
        if path and path.is_file():
            try:
                texts[f"tool {tool['name']}"] += _descriptions(json.loads(path.read_text("utf-8")))
            except ValueError:
                pass  # reported by the tool-schema lint
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except SyntaxError:
            continue
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("Contribution", "ToolSpec")):
            literals = [a.value for a in call.args[:2] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            texts.setdefault(f"{path.relative_to(root)}", []).extend(literals)
    return [Finding("error", "authority", f"{where}: text that claims a permission or tells the assistant not to ask ({f['excerpt']})")
            for where, parts in texts.items() for text in parts if text for f in _authority(text, where)]


def _descriptions(schema: Any) -> list[str]:
    if isinstance(schema, dict):
        return [v for k, v in schema.items() if k == "description" and isinstance(v, str)] + [d for v in schema.values() for d in _descriptions(v)]
    if isinstance(schema, list):
        return [d for v in schema for d in _descriptions(v)]
    return []


# ---- permissions ----
def declared(manifest: Manifest) -> dict[str, list[str]]:
    """The part of a manifest a later version is diffed against; pack stores it in the lock file."""
    return {"permissions": sorted(manifest.permissions), "hooks": sorted(manifest.hooks),
            "tools": sorted(t["name"] for t in manifest.contributes.get("tools", ()))}


def _permission_diff(manifest: Manifest, previous: Path) -> list[Finding]:
    """What this version declares beyond the last one, which is what the user would have to approve again."""
    if not previous.is_file():
        return []
    try:
        raw = json.loads(previous.read_text("utf-8"))
        before = {k: set(v) for k, v in (raw["declared"] if "declared" in raw else declared(Manifest.from_dict(raw))).items()}
    except (ValueError, KeyError, TypeError, ManifestError) as e:
        return [Finding("warn", "permission-diff", f"cannot read {previous.name} to compare against: {e}")]
    out: list[Finding] = []
    for kind, names in declared(manifest).items():
        now = set(names)
        out += [Finding("warn" if kind == "permissions" else "note", "permission-diff", f"{kind} added since {previous.name}: {name}") for name in sorted(now - before[kind])]
        out += [Finding("note", "permission-diff", f"{kind} removed since {previous.name}: {name}") for name in sorted(before[kind] - now)]
    return out

