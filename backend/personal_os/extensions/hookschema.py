"""JSON Schema for a hook payload, derived from its TypedDict (design doc 10.9.1), and the check that a value matches.

Supported annotations: str, int, float, bool, None, `X | Y`, `list[T]`, `dict[str, T]`, `Literal`, `NotRequired`, a nested
TypedDict and `Any`. Anything else raises at generation time, so a payload cannot use a type the generated SDK files
could not express.
"""
from __future__ import annotations

import functools
import types
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

import jsonschema

SCALARS: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}


def _union(tp: Any) -> bool:
    return get_origin(tp) in (Union, types.UnionType)


def schema_of(tp: Any) -> dict[str, Any]:
    if tp is Any:
        return {}
    if tp is None:
        tp = type(None)
    if tp in SCALARS:
        return {"type": SCALARS[tp]}
    if _union(tp):
        return {"anyOf": [schema_of(t) for t in get_args(tp)]}
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return {"enum": list(args)}
    if origin is list and len(args) == 1:
        return {"type": "array", "items": schema_of(args[0])}
    if origin is dict and len(args) == 2 and args[0] is str:
        return {"type": "object", "additionalProperties": schema_of(args[1])}
    if is_typeddict(tp):
        hints = get_type_hints(tp)
        out: dict[str, Any] = {"type": "object", "properties": {k: schema_of(t) for k, t in hints.items()},
                               "required": sorted(tp.__required_keys__), "additionalProperties": False}
        if tp.__doc__:
            out["description"] = tp.__doc__.strip()
        return out
    raise TypeError(f"{tp!r} has no JSON Schema form")


def typed_dicts(tp: Any) -> list[type]:
    """The TypedDicts a type refers to, itself included, each after the ones it contains."""
    found: list[type] = []

    def walk(t: Any) -> None:
        if is_typeddict(t):
            for field in get_type_hints(t).values():
                walk(field)
            if t not in found:
                found.append(t)
        else:
            for arg in get_args(t):
                walk(arg)
    walk(tp)
    return found


@functools.cache
def _validator(tp: type) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(schema_of(tp))


def problem(tp: Any, value: Any) -> str | None:
    """Why `value` is not a `tp` (a TypedDict, a class, or a union of them), or None when it is."""
    if is_typeddict(tp):
        error = next(iter(_validator(tp).iter_errors(value)), None)
        return None if error is None else f"{error.message} at {'/'.join(map(str, error.absolute_path)) or 'the top level'}"
    if _union(tp):
        reasons = [problem(t, value) for t in get_args(tp)]
        return None if None in reasons else "; ".join(r for r in reasons if r)
    return None if isinstance(value, tp) else f"{type(value).__name__} is not a {getattr(tp, '__name__', tp)}"


def members(tp: Any) -> tuple[Any, ...]:
    """The types a union is made of; a single type is its own member."""
    return get_args(tp) if _union(tp) else (tp,)


def type_name(tp: Any) -> str:
    return " | ".join(getattr(t, "__name__", str(t)) for t in members(tp))

