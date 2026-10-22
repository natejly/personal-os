"""ext_propose: the assistant puts an extension it drafted up for the user's review (design doc 6.3).

The draft is written with the file tools into `<data>/ext-drafts/<id>/`. The call checks it and queues it in Review as a diff;
nothing is installed, and no tool enables an extension. Its card is always shown, whatever the permission mode.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .extensions.drafts import DraftError
from .tools import ToolSpec, _obj, tool_error


def register(tb: Any) -> None:
    async def ext_propose(ctx: dict[str, Any], id: str) -> Any:
        drafts = tb.ext_drafts
        if drafts is None:
            return tool_error("Extensions are not available.")
        try:
            proposal = await asyncio.to_thread(drafts.propose, str(id or "").strip(), tainted=bool(ctx.get("tainted")),
                                               taint_sources=list(dict.fromkeys(ctx.get("taint_sources") or [])))
        except DraftError as e:
            return {**tool_error(str(e), field="id"), "findings": e.findings}
        return {"ok": True, "id": proposal["id"], "status": "awaiting review", "warnings": proposal["findings"],
                "note": "The user reviews the diff in Settings > Advanced > Extensions. Accepting installs it disabled; only the user can enable it."}

    tb.specs["ext_propose"] = ToolSpec(
        "ext_propose", "Put an extension you drafted in <data>/ext-drafts/<id>/ up for the user's review: it is checked, and on success "
        "appears as a diff the user accepts or rejects. Accepting installs it disabled; you cannot enable it. Read the author-extension "
        "skill first.",
        _obj({"id": {"type": "string", "description": "The extension id: lowercase letters, digits and hyphens, the draft folder's name"}}, ["id"]),
        ext_propose, "extensions", "external", examples=[{"id": "reading-log"}])
