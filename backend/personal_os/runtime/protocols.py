"""What a runtime slot provider implements (design doc section 10.7.2). Each interface is small on purpose: a provider
returns data, and the checks, the journal and the approvals around it stay in the core."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

from ..osplat.base import SandboxBackend  # noqa: F401 - the sandbox.backend protocol lives beside the platform interfaces


@dataclass(frozen=True)
class ModelRequest:
    """The argument list of `llm.stream_chat` without the settings: a client never sees them or any provider's key."""
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    kind: str = "chat"
    effort: str = "default"
    tool_choice: str = "auto"
    fast: bool = False


@dataclass(frozen=True)
class ModelReply:
    text: str


@runtime_checkable
class ModelClient(Protocol):
    id: str
    kinds: frozenset[str]                       # which entry points it serves: "chat" (stream_chat) and "complete"
    locality: Literal["local", "remote"]        # where the prompt goes
    def stream(self, req: ModelRequest) -> AsyncIterator[dict[str, Any]]: ...
    async def complete(self, req: ModelRequest) -> ModelReply: ...


class RouteChoice(TypedDict):
    model: str
    effort: str
    reason: str
    no_think: bool


@runtime_checkable
class ModelRouter(Protocol):
    id: str
    def route(self, turn: dict[str, Any]) -> RouteChoice: ...   # sync, 50 ms budget; `turn` is router.route's keyword arguments


class ToolDecl(TypedDict):
    name: str
    description: str
    schema: dict[str, Any]  # the JSON Schema of the arguments
    danger: str
    group: str


@runtime_checkable
class ToolSource(Protocol):
    id: str
    def list(self) -> list[ToolDecl]: ...
    async def call(self, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class AgentLoop(Protocol):
    id: str
