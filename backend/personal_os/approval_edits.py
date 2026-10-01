"""Human-edited arguments on an approval card.

A card may let the person fix what the model proposed (an email's recipient, an event's time) before
approving. The person authored those values, so they are allowed; but they are still re-validated here,
server-side, and everything downstream (the run's execution, the idempotency journal, the read-back
verifier, the outbox) must see the EDITED arguments, never the model's originals.

Shared contract: EDITABLE_TOOLS, register_validator(tool, fn), and `prepare`.
"""
from __future__ import annotations

from typing import Any, Callable

EDITABLE_TOOLS: set[str] = {
    "gmail_send", "gmail_draft",
    "calendar_create", "calendar_update", "calendar_delete", "calendar_propose",
    "google_tasks_add",
}

# A validator takes the candidate arguments and returns the cleaned ones, or raises ApprovalEditError.
Validator = Callable[[dict[str, Any]], dict[str, Any]]
_VALIDATORS: dict[str, Validator] = {}


class ApprovalEditError(ValueError):
    """The edited arguments are not acceptable. The message is shown to the person."""


def register_validator(tool: str, fn: Validator) -> None:
    _VALIDATORS[tool] = fn


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "boolean": (bool,), "integer": (int,), "number": (int, float),
    "array": (list,), "object": (dict,),
}


def check_schema(args: dict[str, Any], schema: dict[str, Any]) -> None:
    """The structural check against a tool's JSON-schema parameters: known keys, required keys, JSON types."""
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in args or args[key] is None:
            raise ApprovalEditError(f"'{key}' is required.")
    for key, val in args.items():
        if key not in props:
            raise ApprovalEditError(f"'{key}' is not an argument of this tool.")
        want = props[key].get("type")
        if val is None or not want:
            continue
        ok = _TYPES.get(want)
        if ok and (not isinstance(val, ok) or (want in ("integer", "number") and isinstance(val, bool))):
            raise ApprovalEditError(f"'{key}' must be a {want}.")


def prepare(tool: str, edited: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean edited arguments for `tool`. Raises PermissionError for a tool that is not editable."""
    if tool not in EDITABLE_TOOLS:
        raise PermissionError(f"{tool} does not take edited arguments.")
    if not isinstance(edited, dict):
        raise ApprovalEditError("arguments must be an object.")
    check_schema(edited, schema)
    fn = _VALIDATORS.get(tool)
    return fn(edited) if fn else edited
