"""The built-in provider of each slot: what runs when nothing else is selected."""
from __future__ import annotations

from .slots import Provider


def core_providers() -> list[Provider]:
    from .. import llm, router, sandbox  # imported here: they import this package
    return [
        Provider("core/openai-compatible", "model.client", "OpenAI-compatible endpoint", impl=llm.default_client, core=True),
        Provider("core/heuristic", "model.router", "Heuristic router", impl=router.HEURISTIC, core=True),
        Provider("core/builtin", "tools.source", "Built-in tools", core=True),
        Provider("core/mcp", "tools.source", "Connectors", core=True),
        Provider("core/os-default", "sandbox.backend", "OS sandbox", impl=sandbox.OS_DEFAULT, core=True),
        Provider("core/chat-loop", "agent.loop", "Chat loop", core=True),
    ]
