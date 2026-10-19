"""The interface of a memory system (the `memory.system` slot): what a provider does with a finished reply, what it recalls into
a turn, and how memories are written, found and removed.

Everything is plain data or an existing core type (`context.Block`, `settled.Settled`). The checks around a provider stay in the core:
the `useMemory` / `useGraph` switches of a chat are applied before a provider is asked, the blocks it returns are placed by the
context assembler (a profile block is `stable`, a recalled block is `volatile`), and a provider that raises or returns the wrong
shape is replaced by the built-in one for that call, with a card in the inbox.

Sizing: `budget_tokens` is the model's context window in tokens. A provider sizes each block it returns as a share of it
(`limits.context_shares(window)` names the shares the built-in one uses), so one number serves every block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:  # type names only: the context assembler and the settled-turn record import the runtime package
    from ..context import Block
    from ..settled import Settled as SettledTurn


@dataclass(frozen=True)
class MemoryScope:
    """Whose memory a call is about: a project (`project_id`) or the personal scope (None), and the chat it comes from."""
    project_id: str | None = None
    conversation_id: str | None = None
    conv_settings: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemorySystem(Protocol):
    id: str

    async def learn(self, turn: SettledTurn, scope: MemoryScope) -> None:
        """What auto-learn does after a reply. The core has already decided the turn may be learned from (no error, not an unattended
        run, memory and auto-learn on for the chat); `turn.tainted` says the reply read someone else's text."""

    async def recall(self, query: str, scope: MemoryScope, budget_tokens: int, *, vec: Any = None) -> list[Block]:
        """Volatile block(s) for this turn, relevance-ordered. `vec` is the turn's query embedding when one was made."""

    def profile(self, scope: MemoryScope, budget_tokens: int) -> Block | None:
        """The stable standing-preferences block, or None. Sync, and a pure function of the stored memory: it sits in the cached prefix."""

    async def remember(self, content: str, kind: str, scope: MemoryScope, source: str) -> dict[str, Any] | None:
        """Store a memory (`source` is "user" or "auto"); returns the stored row, or None when it was refused."""

    async def forget(self, memory_id: str) -> bool:
        """Remove a memory; False when it is not there."""

    async def search(self, query: str, scope: MemoryScope, limit: int) -> list[dict[str, Any]]:
        """Memories matching `query` for the UI and the memory_search tool: rows with at least `id`, `content`, `kind`, `project_id`."""

    def status(self, scope: MemoryScope) -> dict[str, Any]:
        """A small summary for Settings: at least `{"provider": id, "count": int}`."""
