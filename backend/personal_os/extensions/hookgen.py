"""Generates everything derived from the hook catalog (catalog.HOOKS), design doc 10.9.3. Run as

    grain ext gen-hooks           # rewrite the files
    grain ext gen-hooks --check   # exit 1 if any is out of date

Outputs: the section 5 tables of docs/research/extensions-and-hooks.md (between `<!-- hooks:begin 5.x -->` markers),
docs/extension-hooks.md, backend/grain_sdk/hooks.py and src/shared/extensionHooks.ts.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints, is_typeddict

from .catalog import HOOKS, HookSpec
from .hookschema import SCALARS, members, type_name, typed_dicts

REPO = Path(__file__).parents[3]
RESEARCH = REPO / "docs" / "research" / "extensions-and-hooks.md"
FEATURE_DOC = REPO / "docs" / "extension-hooks.md"
PY_OUT = REPO / "backend" / "grain_sdk" / "hooks.py"
TS_OUT = REPO / "src" / "shared" / "extensionHooks.ts"
COMMAND = "grain ext gen-hooks"
SECTIONS = {
    "5.1": "App and session", "5.2": "Input", "5.3": "Context and prompt", "5.4": "Model request and response", "5.5": "Tool call",
    "5.6": "Turn and run end", "5.7": "Memory and compaction", "5.8": "Approvals", "5.9": "Background and schedule",
    "5.10": "Channels and I/O", "5.12": "Coding bridge", "5.13": "Runtime slots", "5.15": "Agent loop",
}
HEADER = "| Hook | Fires at | Payload | Returns | Order and failure | Who may subscribe |\n|---|---|---|---|---|---|\n"


def _typed_dicts() -> list[type]:
    found: list[type] = []
    for spec in HOOKS.values():
        for tp in [*typed_dicts(spec.payload), *(t for m in members(spec.returns) for t in typed_dicts(m))]:
            if tp not in found:
                found.append(tp)
    return found


def _return_classes() -> list[type]:
    """The non-TypedDict classes handlers return (Deny, Block, ...), for the imports of the generated Python."""
    found: list[type] = []
    for spec in HOOKS.values():
        for m in members(spec.returns):
            if isinstance(m, type) and not is_typeddict(m) and m not in found:
                found.append(m)
    return found


# ---- type expressions ----
def py_type(tp: Any) -> str:
    if tp is Any:
        return "Any"
    if tp is None or tp is type(None):
        return "None"
    if tp in SCALARS:
        return tp.__name__
    if len(members(tp)) > 1:
        return " | ".join(py_type(t) for t in members(tp))
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return f"Literal[{', '.join(json.dumps(a) for a in args)}]"
    if origin is list:
        return f"list[{py_type(args[0])}]"
    if origin is dict:
        return f"dict[str, {py_type(args[1])}]"
    return tp.__name__


def ts_type(tp: Any) -> str:
    if tp is Any:
        return "unknown"
    if tp is None or tp is type(None):
        return "null"
    if tp in SCALARS:
        return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}[SCALARS[tp]]
    if len(members(tp)) > 1:
        return " | ".join(ts_type(t) for t in members(tp))
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return " | ".join(json.dumps(a).replace('"', "'") for a in args)
    if origin is list:
        inner = ts_type(args[0])
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    if origin is dict:
        return f"Record<string, {ts_type(args[1])}>"
    return tp.__name__


def _doc(tp: type) -> str:
    return (tp.__doc__ or "").strip()


# ---- grain_sdk/hooks.py ----
def _py_class(tp: type) -> str:
    required = tp.__required_keys__  # type: ignore[attr-defined]
    fields = [f"    {k}: {py_type(t)}" if k in required else f"    {k}: NotRequired[{py_type(t)}]" for k, t in get_type_hints(tp).items()]
    doc = _doc(tp)
    return "\n".join([f"class {tp.__name__}(TypedDict):", *([f'    """{doc}"""'] if doc else []), *fields])


def _handler(spec: HookSpec) -> str:
    result = " | ".join(["None", *(py_type(m) for m in members(spec.returns))]) if spec.returns is not None else "Any"
    payload = spec.payload.__name__
    return f"Callable[[{payload}], {result} | Awaitable[{result}]] | Callable[[{payload}, HookCtx], {result} | Awaitable[{result}]]"


def _fire_returns(spec: HookSpec) -> str:
    match spec.kind:
        case "emit":
            return "None"
        case "collect":
            return f"list[{py_type(spec.returns)}]"
        case "pipe":
            return py_type(spec.returns)
        case _:
            return " | ".join(["None", *(py_type(m) for m in members(spec.returns))])


def render_py() -> str:
    classes = _return_classes()
    imports = {"personal_os.extensions.disposables": ["Disposable"], "personal_os.extensions.hooks": ["HookCtx"]}
    for cls in classes:
        imports.setdefault(cls.__module__, []).append(cls.__name__)
    ids = ", ".join(json.dumps(h) for h in HOOKS)
    on = "\n".join(
        f"    @overload\n    def __call__(self, hook: Literal[{json.dumps(h)}], fn: {_handler(s)}, /, *, priority: int | None = ..., "
        f"timeout_ms: int | None = ...) -> Disposable: ..." for h, s in HOOKS.items())
    fire = "\n".join(
        f"    @overload\n    def __call__(self, hook: Literal[{json.dumps(h)}], payload: {s.payload.__name__}, /) -> {_fire_returns(s)}: ..."
        for h, s in HOOKS.items())
    return "\n".join([
        f'"""Generated by `{COMMAND}` from personal_os.extensions.catalog. Do not edit."""',
        "from __future__ import annotations",
        "",
        "from typing import Any, Awaitable, Callable, Literal, NotRequired, Protocol, TypedDict, overload",
        "",
        *(f"from {module} import {', '.join(sorted(names))}" for module, names in sorted(imports.items())),
        "",
        "",
        "\n\n\n".join(_py_class(tp) for tp in _typed_dicts()),
        "",
        "",
        f"HookId = Literal[{ids}]",
        "",
        "",
        "class On(Protocol):",
        '    """`api.on`: the handler\'s argument and return are those of the hook; a second parameter named `ctx` receives a HookCtx."""',
        on,
        "",
        "",
        "class Fire(Protocol):",
        '    """`Harness.fire`: what the hook\'s handlers made of the payload."""',
        fire,
        "",
    ])


# ---- src/shared/extensionHooks.ts ----
def _ts_interface(tp: type) -> str:
    required = tp.__required_keys__  # type: ignore[attr-defined]
    doc = f"/** {_doc(tp)} */\n" if _doc(tp) else ""
    hints = get_type_hints(tp)
    if not hints:
        return f"{doc}export type {tp.__name__} = Record<string, never>"
    fields = [f"  {k}{'' if k in required else '?'}: {ts_type(t)}" for k, t in hints.items()]
    return "\n".join([f"{doc}export interface {tp.__name__} {{", *fields, "}"])


def render_ts() -> str:
    ids = " | ".join(f"'{h}'" for h in HOOKS)
    payloads = "".join(f"  '{h}': {s.payload.__name__}\n" for h, s in HOOKS.items())
    kinds = "".join(f"  '{h}': '{s.kind}'\n" for h, s in HOOKS.items())
    return "\n".join([
        f"// Generated by `{COMMAND}` from backend/personal_os/extensions/catalog.py. Do not edit.",
        "",
        "\n\n".join(_ts_interface(tp) for tp in _typed_dicts()),
        "",
        f"export type HookId = {ids}",
        "",
        "/** The payload each hook's handlers receive. */",
        f"export interface HookPayloads {{\n{payloads}}}",
        "",
        "/** How the host calls the handlers: emit and collect pass the payload, pipe and gate the current value. */",
        f"export interface HookKinds {{\n{kinds}}}",
        "",
    ])


# ---- the tables ----
def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _payload_cell(spec: HookSpec) -> str:
    required = spec.payload.__required_keys__  # type: ignore[attr-defined]
    keys = ", ".join(k if k in required else f"{k}?" for k in get_type_hints(spec.payload))
    return _cell(f"`{{{keys}}}`" + (f". {_doc(spec.payload)}" if _doc(spec.payload) else ""))


def _returns_cell(spec: HookSpec) -> str:
    return "none" if spec.returns is None else " or ".join(f"`{type_name(m)}`" for m in members(spec.returns))


def _who_cell(spec: HookSpec) -> str:
    match spec.subscribers:
        case "any":
            return "any enabled extension"
        case "builtin":
            return "built-in extensions only"
        case _:
            return "built-in extensions, or one that declares " + " and ".join(f"`{p}`" for p in spec.permission)


def table(section: str) -> str:
    rows = [f"| `{s.id}` | {_cell(s.fires_at)} | {_payload_cell(s)} | {_returns_cell(s)} | {_cell(s.order_failure)} | {_who_cell(s)} |"
            for s in HOOKS.values() if s.section == section]
    return HEADER + "\n".join(rows) + "\n"


def render_feature_doc() -> str:
    parts = [
        "# Extension hooks",
        "",
        f"Generated by `{COMMAND}` from `backend/personal_os/extensions/catalog.py`; edit the catalog, not this file.",
        "",
        "A hook is a named point where an extension may observe, add, transform or narrow. Four call shapes: `emit` (concurrent, "
        "results ignored), `collect` (concurrent, results merged in priority order), `pipe` (sequential, each handler sees the previous "
        "value) and `gate` (sequential, the first opinion wins; a handler can only `Deny` or `Ask`, never allow).",
        "",
    ]
    for section in SECTIONS:
        if any(s.section == section for s in HOOKS.values()):
            parts += [f"## {SECTIONS[section]}", "", table(section)]
    return "\n".join(parts)


def splice(text: str, section: str) -> str:
    """`text` with the table between the markers of `section` replaced by the generated one."""
    pattern = re.compile(rf"(<!-- hooks:begin {re.escape(section)} -->\n).*?(<!-- hooks:end {re.escape(section)} -->)", re.S)
    if not pattern.search(text):
        raise ValueError(f"hook section {section} has no `<!-- hooks:begin {section} -->` table in {RESEARCH.name}")
    return pattern.sub(lambda m: m.group(1) + table(section) + m.group(2), text)


def render_research(text: str) -> str:
    for section in dict.fromkeys(s.section for s in HOOKS.values()):
        text = splice(text, section)
    return text


def outputs() -> dict[Path, str]:
    return {RESEARCH: render_research(RESEARCH.read_text("utf-8")), FEATURE_DOC: render_feature_doc(), PY_OUT: render_py(), TS_OUT: render_ts()}


def stale() -> list[str]:
    try:
        wanted = outputs()
    except ValueError as e:
        return [str(e)]
    return [f"{path.name} is out of date: run `{COMMAND}`" for path, content in wanted.items()
            if not path.exists() or path.read_text("utf-8") != content]


def main(argv: list[str]) -> int:
    if "--check" in argv:
        problems = stale()
        print("\n".join(problems))
        return 1 if problems else 0
    for path, content in outputs().items():
        path.write_text(content, "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
