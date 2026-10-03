"""Tool-call bookkeeping that does not depend on the app: ids, and (later) argument normalisation."""
from __future__ import annotations

import uuid
from typing import Any


def ensure_unique_call_ids(calls: list[dict[str, Any]], seen: set[str]) -> int:
    """Give every call an id no earlier call in this reply used. A provider restarts its numbering each round (and
    a gateway may send none), but a tool message is matched to its call by id, and the journal, the approvals and
    the transcript key on it too. Rewrites in place, adds every id to `seen`, returns how many were replaced."""
    changed = 0
    for c in calls:
        cid = c.get("id") or ""
        if not cid or cid in seen:
            cid = "call_" + uuid.uuid4().hex[:12]
            c["id"] = cid
            changed += 1
        seen.add(cid)
    return changed
