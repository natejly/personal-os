"""`grain ext check --catalog`: the hook catalog against the code (design doc 10.9.2).

An AST pass finds every emit, emit_sync, fire, collect, pipe, pipe_sync and gate call on a bus, host or `hooks`
attribute, and checks it against catalog.HOOKS. The hook id must be a string literal or a module-level constant, or the
call carries a `# hook: <id>` comment; a call that forwards its own parameter (`def fire(self, hook, ...)`) is skipped,
because its callers are scanned instead.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import get_type_hints

from .. import hookgen
from ..catalog import CATALOG_VERSION, HOOKS
from ..hooks import CALL_KIND

PACKAGE = Path(__file__).parents[2]  # personal_os
GOLDEN = Path(__file__).parents[1] / "hooks.golden.json"
RECEIVERS = {"bus", "_bus", "hooks", "host"}
HOOK_COMMENT = re.compile(r"#\s*hook:\s*([\w.]+)")


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    method: str
    hook: str | None  # None: the id is neither a literal nor a constant, and the call has no `# hook:` comment
    payload: ast.expr | None

    def where(self) -> str:
        return f"{self.path}:{self.line}"


def _receiver(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in CALL_KIND:
        return False
    owner = func.value
    return (isinstance(owner, ast.Name) and owner.id in RECEIVERS) or (isinstance(owner, ast.Attribute) and owner.attr in RECEIVERS)


def _constants(tree: ast.Module) -> dict[str, str]:
    return {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str], constants: dict[str, str]) -> None:
        self.path, self.lines, self.constants = path, lines, constants
        self.params: list[set[str]] = []
        self.sites: list[Site] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.params.append({a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)})
        self.generic_visit(node)
        self.params.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        if not _receiver(node) or not node.args:
            return
        first = node.args[0]
        if isinstance(first, ast.Name) and self.params and first.id in self.params[-1]:
            return
        hook = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        if hook is None and isinstance(first, ast.Name):
            hook = self.constants.get(first.id)
        if hook is None:
            span = self.lines[node.lineno - 1:(node.end_lineno or node.lineno)]
            hook = next((m.group(1) for line in span if (m := HOOK_COMMENT.search(line))), None)
        payload = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg in ("payload", "value")), None)
        self.sites.append(Site(self.path, node.lineno, node.func.attr, hook, payload))  # type: ignore[attr-defined]


def scan(root: Path = PACKAGE) -> list[Site]:
    sites: list[Site] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text("utf-8")
        tree = ast.parse(source, str(path))
        visitor = _Visitor(path.relative_to(root).as_posix(), source.splitlines(), _constants(tree))
        visitor.visit(tree)
        sites += visitor.sites
    return sites


def _payload_problems(site: Site, spec_payload: type) -> list[str]:
    if not isinstance(site.payload, ast.Dict):
        return []
    allowed = set(get_type_hints(spec_payload))
    keys = {k.value for k in site.payload.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    spread = any(k is None or not isinstance(k, ast.Constant) for k in site.payload.keys)
    out = [f"{site.where()}: {site.hook} payload has the key {k!r}, which its TypedDict does not" for k in sorted(keys - allowed)]
    if not spread:
        out += [f"{site.where()}: {site.hook} payload lacks the required key {k!r}" for k in sorted(spec_payload.__required_keys__ - keys)]
    return out


def site_problems(sites: list[Site]) -> list[str]:
    out: list[str] = []
    for site in sites:
        if site.hook is None:
            out.append(f"{site.where()}: {site.method} names its hook with something other than a string literal or a module constant; "
                       "add a `# hook: <id>` comment to the call")
        elif (spec := HOOKS.get(site.hook)) is None:
            out.append(f"{site.where()}: {site.method} fires {site.hook!r}, which is not in the catalog")
        elif CALL_KIND[site.method] != spec.kind:
            out.append(f"{site.where()}: {site.hook} is a {spec.kind} hook, called with {site.method}")
        elif site.method.endswith("_sync") != spec.sync:
            out.append(f"{site.where()}: {site.hook} is {'' if spec.sync else 'not '}a sync hook, called with {site.method}")
        else:
            out += _payload_problems(site, spec.payload)
    return out


def dead_hooks(sites: list[Site]) -> list[str]:
    fired = {s.hook for s in sites}
    return [f"{hook}: in the catalog but fired nowhere" for hook in HOOKS if hook not in fired]


def golden_problems(golden: Path = GOLDEN) -> list[str]:
    """Hook ids are append-only: none may disappear, and a kind may change only on a deprecated hook in a newer catalog version."""
    recorded = json.loads(golden.read_text("utf-8"))
    out: list[str] = []
    for hook, kind, since in recorded["hooks"]:
        spec = HOOKS.get(hook)
        if spec is None:
            out.append(f"{hook}: removed from the catalog; retire a hook with `deprecated` instead")
        elif spec.kind != kind and not (spec.deprecated and CATALOG_VERSION != recorded["version"]):
            out.append(f"{hook}: kind changed from {kind} to {spec.kind} without `deprecated` and a CATALOG_VERSION bump")
        elif spec.since != since:
            out.append(f"{hook}: since changed from {since} to {spec.since}")
    known = {h for h, _, _ in recorded["hooks"]}
    out += [f'{h}: not in {golden.name}; add ["{h}", "{s.kind}", "{s.since}"]' for h, s in HOOKS.items() if h not in known]
    return out


def catalog_problems() -> list[str]:
    sites = scan()
    return [*site_problems(sites), *dead_hooks(sites), *golden_problems(), *hookgen.stale()]
