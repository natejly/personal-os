"""Human edits to a pending tool approval.

An approval card for an outward-facing call (an email, a calendar event, a task) may let the user fix the
arguments before pressing Approve. POST /approvals/{call_id} then carries `arguments`; this module decides
whether that is allowed and whether the edit is well-formed.

Trust model (read this before changing anything):

* The ONLY way to edit is that HTTP body. There is no tool, no tool argument and no model-visible field that
  reaches it: `edited_arguments` / `edited_by` in a model's own tool call are just unknown keys (rejected by the
  tool's schema like any other) and are never read back from the call. The run loop takes the effective
  arguments from the approval *row*, and the row's edit columns are written by `RunStore.decide(...)` only
  from the route. That is the "explicit path": a human authored these values, so they replace the model's.
* An edit never changes *whether* a call asks. The gate (mode, taint, forced, plan mode, propose-only) is
  decided before the card exists; an edit is only meaningful on a row that is still pending, i.e. a card the
  user is looking at. A forced approval stays forced, and a tainted run still asks.
* A plan step is consumed by `plans.claim`, which matches the *model's* arguments by digest and skips the
  card entirely. So an edited card is never a claimed step, and an edit cannot make a different call ride
  on a plan's approval. The edit re-binds the approval row's `args_digest` to the edited arguments, and the
  executed_calls journal, the read-back verification and the 90s outbox all run on the edited arguments,
  because they are handed the effective arguments and nothing else.
* The server re-validates: the edit must fit the tool's parameter schema and any per-tool validator
  registered here, and a deny cannot carry an edit.
"""
from __future__ import annotations

from typing import Any, Callable

# Tools whose arguments a person can sensibly rewrite on the card. calendar_propose is the calendar
# workstream's "show me the change first" tool; listing a name that is not registered is harmless.
EDITABLE_TOOLS: set[str] = {
    "gmail_send", "gmail_draft",
    "calendar_create", "calendar_update", "calendar_delete", "calendar_propose",
    "google_tasks_add", "write_local_file",
}

# A validator returns an error string (rejected), None (fine as is), or a dict: the same arguments normalised.
Validator = Callable[[dict[str, Any]], "str | dict[str, Any] | None"]
_validators: dict[str, list[Validator]] = {}


class EditError(ValueError):
    """The edit is not acceptable. The message is the HTTP 400 detail."""


def register_validator(tool: str, fn: Validator) -> None:
    """Add a per-tool check: fn(edited_args) returns an error string, None when the edit is fine, or the cleaned dict."""
    _validators.setdefault(tool, []).append(fn)


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "boolean": (bool,), "object": (dict,), "array": (list,),
    "integer": (int,), "number": (int, float), "null": (type(None),),
}


def _check_value(path: str, v: Any, schema: dict[str, Any]) -> str | None:
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for name in types:
            py = _TYPES.get(name)
            if py is None:
                ok = True
            elif isinstance(v, py) and not (isinstance(v, bool) and name in ("integer", "number")):
                ok = True
        if not ok:
            return f"{path} must be {' or '.join(types)}"
    if "enum" in schema and v not in schema["enum"]:
        return f"{path} must be one of {', '.join(map(str, schema['enum']))}"
    if isinstance(v, dict) and isinstance(schema.get("properties"), dict):
        return _check_object(path, v, schema, strict=False)
    if isinstance(v, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(v):
            if (e := _check_value(f"{path}[{i}]", item, schema["items"])):
                return e
    return None


def _check_object(path: str, args: dict[str, Any], schema: dict[str, Any], strict: bool) -> str | None:
    props = schema.get("properties") or {}
    pre = path + "." if path else ""
    for req in schema.get("required") or []:
        if req not in args or args[req] is None:
            return f"{pre}{req} is required"
    for k, v in args.items():
        if k not in props:
            if strict:
                return f"{k} is not an argument of this tool"
            continue
        if v is None:  # an optional argument cleared in the editor
            continue
        if (e := _check_value(f"{pre}{k}", v, props[k])):
            return e
    return None


def validate(tool: str, args: Any, parameters: dict[str, Any] | None) -> dict[str, Any]:
    """The edited arguments, cleaned (None values dropped), or EditError. `parameters` is the tool's JSON schema."""
    if tool not in EDITABLE_TOOLS:
        raise EditError(f"{tool} cannot be edited on its approval card")
    if not isinstance(args, dict):
        raise EditError("arguments must be an object")
    if parameters is None:
        raise EditError(f"{tool} is not available, so its arguments cannot be checked")
    if (e := _check_object("", args, parameters, strict=True)):
        raise EditError(e)
    clean = {k: v for k, v in args.items() if v is not None}
    for fn in _validators.get(tool, []):
        r = fn(clean)
        if isinstance(r, dict):
            clean = r
        elif r:
            raise EditError(r)
    return clean


# ---- built-in validators ----
def _gmail(args: dict[str, Any]) -> str | None:
    parts = [a.strip() for a in str(args.get("to", "")).replace(";", ",").split(",") if a.strip()]
    if not parts or not all("@" in p for p in parts):
        return "to must be one or more email addresses"
    if not str(args.get("body", "")).strip():
        return "body must not be empty"
    return None


register_validator("gmail_send", _gmail)
register_validator("gmail_draft", _gmail)


def _task(args: dict[str, Any]) -> str | None:
    return None if str(args.get("title", "")).strip() else "title must not be empty"


register_validator("google_tasks_add", _task)
