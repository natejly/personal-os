"""The interface of an agent loop (design doc section 10.10.2): what a loop receives, what it may call, and what it yields.

A loop module imports nothing from `personal_os` but this one (see `loop_fence`), so everything it can reach is declared here:
plain data in, narrow capabilities out. The capabilities are built by the core around the permission stack, the approval flow,
`dispatch_tool` and the model client, and none of them hands the loop the objects behind those.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable

LoopEvent = tuple[str, dict[str, Any]]  # (event name, payload) in the vocabulary the UI already reads
Messages = list[dict[str, Any]]
ToolCall = dict[str, Any]  # {"id", "name", "arguments"} as the model produced it
ToolMessage = dict[str, Any]  # {"role": "tool", "tool_call_id", "content"}: the content is already rendered and scrubbed

LOOP_EVENTS = frozenset({"delta", "plan", "span"})  # what a loop itself may emit; the tool and model objects emit the rest


class LimitReached(Exception):
    """A bound the core enforces was spent (a round bound the loop declared, or the turn's ceiling on model calls)."""


class TurnEnded(BaseException):
    """The core ended the reply under the loop (a card the user left unanswered was parked). A loop lets it pass."""


@dataclass(frozen=True)
class AttachmentRef:
    id: str
    name: str


@dataclass(frozen=True)
class TurnInput:
    """What the loop receives, all plain data."""
    conversation_id: str
    run_id: str | None
    text: str
    attachments: tuple[AttachmentRef, ...]
    origin: str | None
    resumed: bool
    model: str  # chosen by the model.router slot; the loop never chooses a provider
    effort: str
    cfg: Mapping[str, Any]  # the settings with secrets removed


class ContextBuilder(Protocol):
    def build(self, extra_tail: Sequence[str] = ()) -> Messages:
        """The turn's context from the contributor pipeline (stable, volatile, tail), then this run's own messages so far. `extra_tail`
        joins the tail, which is never cached. The list returned is the turn's own; the stable prefix in it is checked before every
        model call, and a call whose prefix was changed fails."""


class ModelStream(Protocol):
    def stream(self, messages: Messages, *, tools: bool) -> AsyncIterator[dict[str, Any]]:
        """One model call through the model.client slot, with the idle limit, Stop and steering. Yields `{"type": "delta", "text"}` and
        ends with one `{"type": "end", "text", "tool_calls", "finish_reason"}`. With `tools` false the model is told not to call any."""


class Tools(Protocol):
    async def run(self, calls: Sequence[ToolCall]) -> list[ToolMessage]:
        """Run the calls the last model call asked for, in order, with every check, card and journal row the chat gives them, and return
        the tool messages. The assistant message that asked for them is added to the turn's messages."""


class EventSink(Protocol):
    def emit(self, event: LoopEvent) -> None:
        """Publish a `delta`, `plan` or `span` event as the loop goes, not when it next yields."""


class LoopHooks(Protocol):
    async def emit(self, hook: str, payload: Mapping[str, Any]) -> None:
        """Fire `loop.plan` or `loop.verify`; no other hook can be fired from a loop."""


class TurnLimits(Protocol):
    @property
    def stopped(self) -> bool: ...
    def next_round(self, scope: str) -> int:
        """Count one more round in `scope` (a name in the loop's `bounds`) and return the count; `LimitReached` once the bound is spent."""
    def restart(self, scope: str) -> None:
        """The next `next_round(scope)` counts from one again."""
    async def take_steers(self) -> list[str]:
        """Fold in the messages the user wrote while the turn ran (the reply segment closes and a new one opens, as in any chat) and
        return their text."""


@dataclass(frozen=True)
class LoopHarness:
    """What the loop may call, each a narrow object."""
    context: ContextBuilder
    model: ModelStream
    tools: Tools
    events: EventSink
    hooks: LoopHooks
    limits: TurnLimits


@runtime_checkable
class AgentLoop(Protocol):
    id: str
    bounds: Mapping[str, int]  # rounds the loop may count per scope, read once by the core when the turn starts

    def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]: ...
