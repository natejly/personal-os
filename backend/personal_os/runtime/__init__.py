"""Runtime slots: swappable providers for the core's seams (design doc section 10.7)."""
from __future__ import annotations

from .slots import Runtime

# The running app's registry. The app points it at its extension host's; until then (and in code that never builds the
# app) nothing is selected anywhere, so every slot resolves to its built-in default.
active: Runtime | None = None
