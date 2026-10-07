"""Which message rows are the backend's own."""
from __future__ import annotations

from typing import Any


def is_internal(m: dict[str, Any]) -> bool:
    """A message row the model must see but the user never said: any non-NULL `kind` ('wake', 'nudge', 'continue',
    'resume', 'handoff', 'report'...). The SQL spelling of the same rule is `m.kind IS NULL`."""
    return bool(m.get("kind"))
