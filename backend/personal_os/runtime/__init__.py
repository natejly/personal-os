"""Runtime slots: swappable providers for the core's seams (design doc section 10.7)."""
from __future__ import annotations

from ..extensions.hooks import HookBus
from .slots import Runtime

# The running app's registry. The app points it at its extension host's; until then (and in code that never builds the
# app) it lists no provider, so nothing is selected anywhere and every consumer keeps its built-in default.
active: Runtime = Runtime(HookBus(), [])
