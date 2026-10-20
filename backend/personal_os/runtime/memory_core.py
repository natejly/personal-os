"""The built-in memory system (`core/memory`) and the routing that picks the one a chat uses.

`CoreMemory` is an adapter: every call goes to code that already existed (`repos.Memories`, `memory_index`, `LearnWorker`, the
block makers in `context`). `MemoryRouting` resolves the slot for a chat (chat over project over profile over global) and fences a
selected provider: one that raises, times out or returns the wrong shape is replaced by the built-in one for that call, and the
user gets a card in the inbox.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import limits, memory_limits
from .memory_api import MemoryScope, MemorySystem
from .slots import Runtime, Scopes

if TYPE_CHECKING:
    from ..context import Block
    from ..settled import Settled

log = logging.getLogger(__name__)

SLOT = "memory.system"
DEFAULT_ID = "core/memory"
RECALL_TIMEOUT_S = 10.0
LEARN_TIMEOUT_S = 30.0


class CoreMemory:
    """Routes the memory system's calls to the repositories and workers the app already has. No logic of its own."""
    id = DEFAULT_ID

    def __init__(self, *, memories: Any, graph: Any, index: Any, learner: Any, skills: Any, settings: Callable[[], dict[str, Any]],
                 memory_hits: Callable[..., Awaitable[Any]], graph_hits: Callable[..., Awaitable[Any]],
                 query_vec: Callable[..., Awaitable[Any]]) -> None:
        self.memories, self.graph, self.index, self.learner, self.skills = memories, graph, index, learner, skills
        self._settings, self._memory_hits, self._graph_hits, self._query_vec = settings, memory_hits, graph_hits, query_vec

    async def learn(self, turn: Settled, scope: MemoryScope) -> None:
        from .. import learn  # local: learn imports the repositories, which import this package
        user_only = turn.tainted
        self.learner.submit(learn.LearnJob(
            conversation_id=turn.conversation_id, message_id=turn.message_id, project_id=turn.conv["project_id"],
            user_text="\n\n".join(turn.run_user_texts) if user_only else turn.user_text,
            assistant_text="" if user_only else turn.reply, model=turn.model, settings=turn.cfg,
            spans=list(turn.tracer.spans), tool_events=[] if user_only else list(turn.tool_events),
            skills_in_use=[] if user_only else learn.skills_seen(turn.used["skills"], turn.tool_events, self.skills, turn.conv["project_id"]),
            user_only=user_only, user_message_id=turn.user_message_id, learn_graph=bool(turn.conv["settings"].get("useGraph", True)),
        ))

    async def recall(self, query: str, scope: MemoryScope, budget_tokens: int, *, vec: Any = None) -> list[Block]:
        from ..context import profile_block, recalled_blocks
        cfg, cs = self._settings(), dict(scope.conv_settings)
        if vec is None:
            vec = await self._query_vec(query, cfg, cs)
        mem_hits = await self._memory_hits(scope.project_id, query, cfg, cs, vec)
        graph_hits = await self._graph_hits(scope.project_id, query, cfg, cs, vec)
        shares = limits.context_shares(budget_tokens)
        shown = {m["id"] for m in (profile_block(self.memories, scope.project_id, shares["profile"]) or _NONE).used.get("profile", ())}
        return recalled_blocks(self.memories, self.graph, scope.project_id, query, shares, cs, mem_hits, graph_hits, shown)

    def profile(self, scope: MemoryScope, budget_tokens: int) -> Block | None:
        from ..context import profile_block
        return profile_block(self.memories, scope.project_id, limits.context_shares(budget_tokens)["profile"])

    async def remember(self, content: str, kind: str, scope: MemoryScope, source: str) -> dict[str, Any] | None:
        return self.memories.create(scope.project_id, content, kind=kind or "fact", source=source)

    async def forget(self, memory_id: str) -> bool:
        return self.memories.invalidate(memory_id) is not None

    async def search(self, query: str, scope: MemoryScope, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            return self.memories.list(scope.project_id)[:limit]
        cfg = self._settings()
        return await self.index.search_reranked(scope.project_id, query, await self.index.query_vec(cfg, query), limit=limit, settings=cfg)

    def status(self, scope: MemoryScope) -> dict[str, Any]:
        return {"provider": self.id, "count": len(self.memories.list(scope.project_id))}


class _Empty:
    used: dict[str, Any] = {}


_NONE = _Empty()


class Fenced:
    """A selected memory system, with the built-in one standing in for any call it fails."""

    def __init__(self, routing: MemoryRouting, provider_id: str, impl: Any, scopes: Scopes) -> None:
        self.routing, self.id, self.impl, self.scopes = routing, provider_id, impl, scopes

    def _failed(self, what: str, why: BaseException | str) -> None:
        detail = f"{type(why).__name__}: {why}" if isinstance(why, BaseException) else why
        log.warning("memory system %s failed in %s: %s", self.id, what, detail)
        msg = f"The memory system '{self.id}' failed in {what} ({detail[:160]}), so the built-in memory was used instead."
        self.routing.rt.announce(SLOT, self.id, DEFAULT_ID, msg, False, self.scopes)
        if (self.id, what) not in self.routing.noted:
            self.routing.noted.add((self.id, what))
            self.routing.rt.note("A selected memory system failed", msg)

    async def _call(self, what: str, call: Callable[[Any], Awaitable[Any]], timeout: float, ok: Callable[[Any], bool] = lambda r: True) -> Any:
        try:
            result = await asyncio.wait_for(call(self.impl), timeout)
            if ok(result):
                return result
            self._failed(what, "it returned the wrong shape")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - any failure of the provider is the answer
            self._failed(what, e)
        return await call(self.routing.core)

    async def learn(self, turn: Settled, scope: MemoryScope) -> None:
        await self._call("learn", lambda m: m.learn(turn, scope), LEARN_TIMEOUT_S)

    async def recall(self, query: str, scope: MemoryScope, budget_tokens: int, *, vec: Any = None) -> list[Block]:
        from ..context import Block
        return await self._call("recall", lambda m: m.recall(query, scope, budget_tokens, vec=vec), RECALL_TIMEOUT_S,
                                lambda r: isinstance(r, list) and all(isinstance(b, Block) and b.slot == "volatile" and isinstance(b.text, str) for b in r))

    def profile(self, scope: MemoryScope, budget_tokens: int) -> Block | None:
        from ..context import Block
        try:
            block = self.impl.profile(scope, budget_tokens)
            if block is None or (isinstance(block, Block) and block.slot == "stable" and isinstance(block.text, str)):
                return block
            self._failed("profile", "it returned the wrong shape")
        except Exception as e:  # noqa: BLE001
            self._failed("profile", e)
        return self.routing.core.profile(scope, budget_tokens)

    async def remember(self, content: str, kind: str, scope: MemoryScope, source: str) -> dict[str, Any] | None:
        return await self._call("remember", lambda m: m.remember(content, kind, scope, source), RECALL_TIMEOUT_S)

    async def forget(self, memory_id: str) -> bool:
        return await self._call("forget", lambda m: m.forget(memory_id), RECALL_TIMEOUT_S)

    async def search(self, query: str, scope: MemoryScope, limit: int) -> list[dict[str, Any]]:
        return await self._call("search", lambda m: m.search(query, scope, limit), RECALL_TIMEOUT_S, lambda r: isinstance(r, list))

    def status(self, scope: MemoryScope) -> dict[str, Any]:
        try:
            out = self.impl.status(scope)
            if isinstance(out, dict):
                return out
            self._failed("status", "it returned the wrong shape")
        except Exception as e:  # noqa: BLE001
            self._failed("status", e)
        return self.routing.core.status(scope)


class MemoryRouting:
    """Which memory system a chat uses. `selected` is None while the built-in one serves, so a caller keeps its own path then."""

    def __init__(self, rt: Runtime, scopes: Callable[[str | None, str | None], Scopes], core: CoreMemory) -> None:
        self.rt, self._scopes, self.core = rt, scopes, core
        self.noted: set[tuple[str, str]] = set()  # (provider, what) the user was already told about

    def selected(self, conversation_id: str | None, project_id: str | None) -> Fenced | None:
        scopes = self._scopes(conversation_id, project_id)
        if not self.rt.has_alternatives(SLOT) and not any(layer.get(SLOT) for _, layer in scopes.layers()):
            return None
        res = self.rt.resolve(SLOT, scopes)  # a selection that is gone falls back here, with its card
        if res.provider == DEFAULT_ID or res.refused:
            return None
        provider = self.rt.provider(SLOT, res.provider)
        return Fenced(self, provider.id, provider.impl, scopes) if provider is not None and provider.impl is not None else None

    def system(self, conversation_id: str | None, project_id: str | None) -> MemorySystem:
        return self.selected(conversation_id, project_id) or self.core


def scope_of(conversation_id: str | None, project_id: str | None, conv_settings: Any = None) -> MemoryScope:
    return MemoryScope(project_id, conversation_id, dict(conv_settings or {}))
