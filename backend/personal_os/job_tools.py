"""Which tools a scheduled run may touch: a per-job allowlist, and the read-only set a dry run is limited to.

Both are expressed as a map of tool name -> 'off', written into the job conversation's `settings.tools`, which
`Toolbox.effective` already treats as the top-priority override. That has one useful property: the map can only
NARROW. A tool left out of it keeps whatever the user's global/project setting says, so a tool the user turned
off stays off even when a job lists it, and nothing here can turn a tool on or skip an approval.

`None` means "inherit": no map at all, so every job that predates this behaves exactly as before.
"""
from __future__ import annotations

from typing import Any, Iterable

# A job that can schedule jobs is a loop nobody asked for. These never appear in an allowlist (the tier is also
# proposal-only in Toolbox.call; this keeps the stored list honest about it).
NEVER_ALLOWED_DANGER = ("schedules",)
# The tier a dry run is allowed to use: reading and searching inside the app.
READ_ONLY_DANGER = ("safe",)
# Desk tools only exist inside a desk, and desk_done ends one; a preview has no business with either.
READ_ONLY_EXCLUDED_GROUPS = ("desk",)

DRY_RUN_HINT = ("This is a preview. Do not propose or perform any change; describe what you WOULD do and report "
                "what you found.")


def never_allowed(all_tools: Iterable[dict[str, Any]]) -> set[str]:
    return {t["name"] for t in all_tools if t.get("danger") in NEVER_ALLOWED_DANGER}


def read_only_names(all_tools: Iterable[dict[str, Any]]) -> set[str]:
    return {t["name"] for t in all_tools
            if t.get("danger") in READ_ONLY_DANGER and t.get("group") not in READ_ONLY_EXCLUDED_GROUPS}


def check(allowed: list[str], all_tools: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(unknown names, names no job may be given). Both empty means the list is storable."""
    known = {t["name"] for t in all_tools}
    banned = never_allowed(all_tools)
    return [n for n in allowed if n not in known], [n for n in allowed if n in banned]


def tool_modes(allowed: list[str] | None, all_tools: Iterable[dict[str, Any]]) -> dict[str, str]:
    """The `settings.tools` override for a job run: every tool outside the allowlist is off. {} when inheriting."""
    if allowed is None:
        return {}
    tools = list(all_tools)
    ok = set(allowed) - never_allowed(tools)
    return {t["name"]: "off" for t in tools if t["name"] not in ok}


def dry_run_modes(allowed: list[str] | None, all_tools: Iterable[dict[str, Any]]) -> dict[str, str]:
    """A preview: the job's own allowlist, and on top of it everything that is not read-only is off."""
    tools = list(all_tools)
    modes = tool_modes(allowed, tools)
    keep = read_only_names(tools)
    for t in tools:
        if t["name"] not in keep:
            modes[t["name"]] = "off"
    return modes
