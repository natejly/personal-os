"""The import fence around an agent loop (design doc section 10.10.3, mechanism 2).

A module that defines an `AgentLoop` may import from `personal_os` only `personal_os.runtime.loop_api`, and from the standard
library only what cannot be used to find the objects behind the harness (`gc`, `inspect`, `sys`, `importlib`, `builtins`,
`ctypes`, `__import__`). The scan reads the module's source, so it holds for what the file says and not only for what it imported
when it was loaded. It runs when a provider is registered, in `grain ext check`, and over the loops the core ships.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
from typing import Any

API = "personal_os.runtime.loop_api"
INTROSPECTION = frozenset({"gc", "inspect", "sys", "importlib", "builtins", "ctypes"})


def violations(tree: ast.AST, package: str = "") -> list[str]:
    """What in a parsed module breaks the fence, one line each. `package` resolves its relative imports."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
            names = [base] if base == API else [f"{base}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.append(f"line {node.lineno}: __import__ loads modules the fence cannot see")
            continue
        else:
            continue
        for name in names:
            if (name == "personal_os" or name.startswith("personal_os.")) and name != API:
                found.append(f"line {node.lineno}: imports {name}; a loop imports only {API} from personal_os")
            elif name.split(".")[0] in INTROSPECTION:
                found.append(f"line {node.lineno}: imports {name}, which reaches objects outside the harness")
    return found


def check(impl: Any) -> None:
    """Refuse a loop whose module breaks the fence, or whose source cannot be read to prove it does not."""
    module = sys.modules.get(type(impl).__module__)
    try:
        tree = ast.parse(inspect.getsource(module)) if module is not None else None
    except (OSError, TypeError, SyntaxError):
        tree = None
    if tree is None:
        raise ValueError(f"the source of {type(impl).__module__} cannot be read, so its imports cannot be checked")
    if found := violations(tree, module.__package__ or ""):
        raise ValueError(f"{type(impl).__module__} breaks the agent loop import fence: " + "; ".join(found))
