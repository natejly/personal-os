"""The memory.system runtime slot: a provider receives learn / recall / profile, a chat's selection changes what it recalls while
another chat keeps the built-in memory, and a failing provider falls back to the built-in one with a card.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_memory_system_slot.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memslot-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import settled  # noqa: E402
from personal_os.context import Block, MemoryBlocks, build_context  # noqa: E402
from personal_os.runtime.memory_api import MemoryScope, MemorySystem  # noqa: E402
from personal_os.runtime.slots import SLOTS, Provider, Scopes  # noqa: E402
from personal_os.trace import Tracer  # noqa: E402

rt = appmod.host.runtime
WINDOW = 200_000
HEADERS = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}


class Scripted:
    """A memory system that answers from a script and records what it was asked."""
    id = "scripted"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.stored: list[dict[str, Any]] = []

    async def learn(self, turn: Any, scope: MemoryScope) -> None:
        self.calls.append(("learn", (turn.reply, scope.project_id, scope.conversation_id)))

    async def recall(self, query: str, scope: MemoryScope, budget_tokens: int, *, vec: Any = None) -> list[Block]:
        self.calls.append(("recall", (query, budget_tokens)))
        return [Block("## Scripted recall\n- the user keeps bees", "volatile", id="memory:recalled", label="Scripted recall", kind="memory", source="s1",
                      used={"memories": [{"id": "s1", "content": "the user keeps bees", "project_id": None}]})]

    def profile(self, scope: MemoryScope, budget_tokens: int) -> Block | None:
        self.calls.append(("profile", budget_tokens))
        return Block("## Scripted standing preferences\n- answer briefly", "stable", id="memory:profile", label="Standing preferences", kind="memory")

    async def remember(self, content: str, kind: str, scope: MemoryScope, source: str) -> dict[str, Any] | None:
        self.calls.append(("remember", (content, kind, source)))
        row = {"id": f"s{len(self.stored) + 2}", "content": content, "kind": kind, "project_id": scope.project_id}
        self.stored.append(row)
        return row

    async def forget(self, memory_id: str) -> bool:
        self.calls.append(("forget", memory_id))
        return memory_id == "s1"

    async def search(self, query: str, scope: MemoryScope, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("search", (query, limit)))
        return [{"id": "s1", "content": "the user keeps bees", "kind": "fact", "project_id": None, "source_conversation_id": None,
                 "invalid_at": None, "valid_from": 1.0, "created_at": 1.0}]

    def status(self, scope: MemoryScope) -> dict[str, Any]:
        return {"provider": self.id, "count": 1}


class Broken(Scripted):
    id = "broken"

    async def recall(self, *a: Any, **k: Any) -> list[Block]:
        raise RuntimeError("index offline")

    def profile(self, *a: Any, **k: Any) -> Block | None:
        raise RuntimeError("profile offline")

    async def learn(self, *a: Any, **k: Any) -> None:
        raise RuntimeError("learn offline")


class WrongShape(Scripted):
    id = "wrong"

    async def recall(self, *a: Any, **k: Any) -> Any:
        return ["not a block"]


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    made, disposables = {}, []
    for cls in (Scripted, Broken, WrongShape):
        impl = cls()
        made[impl.id] = impl
        disposables.append(rt.declare(Provider(f"test/{impl.id}", "memory.system", impl.id.title(), extension="test")))
        disposables.append(rt.attach("memory.system", f"test/{impl.id}", impl))
    notes: list[tuple[str, str]] = []
    announced: list[tuple[Any, ...]] = []
    monkeypatch.setattr(rt, "note", lambda title, body: notes.append((title, body)))
    real = rt.announce
    monkeypatch.setattr(rt, "announce", lambda *a, **k: (announced.append(a), real(*a, **k))[1])
    appmod.memory_routing.noted.clear()
    made["notes"], made["announced"] = notes, announced
    yield made
    for d in reversed(disposables):
        d.dispose()


def chat(provider: str | None = None, project_id: str | None = None, **settings: Any) -> str:
    cid = appmod.convos.create(project_id, "Mem", "test-model")["id"]
    runtime = {"memory.system": f"test/{provider}"} if provider else {}
    appmod.convos.update(cid, {"settings": {"runtime": runtime, **settings}})
    return cid


def assemble(cid: str, query: str = "what do I keep") -> tuple[str, dict[str, Any]]:
    """What a turn of this chat puts in its context, through the same gather the chat uses."""
    conv = appmod.convos.get(cid, with_messages=False)
    cs = {"useMemory": True, "useGraph": True, **conv["settings"]}
    hits, _, blocks = asyncio.run(appmod._gather_context(query, None, conv["project_id"], cid, cs, WINDOW))
    return build_context(memories=appmod.memories, graph=appmod.graph, documents=appmod.documents, project=None, project_id=conv["project_id"],
                         query=query, settings=appmod.settings(), conv_settings=cs, global_system_prompt="", memory_hits=hits["memory"],
                         graph_hits=hits["graph"], doc_hits=hits["docs"], memory=blocks, window=WINDOW)


def turn(cid: str, reply: str = "Noted.") -> settled.Settled:
    conv = appmod.convos.get(cid, with_messages=False)
    return settled.Settled(
        conversation_id=cid, message_id="m1", user_message_id="u1", conv=conv, cfg={"autoLearn": True}, model="test-model", user_text="hi",
        run_user_texts=["hi"], reply=reply, error=None, stopped=False, gone=False, internal=False, in_side=False, proposal_only=False,
        desk_id=None, tainted=False, tool_events=[], used={"skills": []}, placeholder_title=None, tracer=Tracer(), publish=lambda e, d: None)


def settle(cid: str) -> None:
    asyncio.run(appmod.host.bus.emit("turn.settled", turn(cid), public=lambda s: settled.view_of(s, lambda: {})))


# ---- the slot ----
def test_the_slot_is_declared_with_the_built_in_memory_as_default() -> None:
    d = SLOTS["memory.system"]
    assert (d.protocol, d.default_id, d.permission, d.noun) == (MemorySystem, "core/memory", "runtime:memory", "memory system")
    assert rt.provider("memory.system", "core/memory").core and isinstance(appmod.core_memory, MemorySystem)
    assert rt.resolve("memory.system", Scopes()).default


def test_the_runtime_picker_lists_it(providers: dict[str, Any]) -> None:
    client = TestClient(appmod.app, headers=HEADERS)
    if True:
        row = next(s for s in client.get("/runtime").json()["slots"] if s["id"] == "memory.system")
        assert row["default_id"] == "core/memory" and {"core/memory", "test/scripted"} <= {p["id"] for p in row["providers"]}
        cid = chat()
        r = client.put("/runtime", json={"slot": "memory.system", "scope": "chat", "scope_id": cid, "value": "test/scripted"})
        assert r.status_code == 200 and r.json()["memory.system"]["provider"] == "test/scripted"


def test_a_user_extension_may_not_provide_it() -> None:
    with pytest.raises(PermissionError):
        rt.declare(Provider("mine/mem", "memory.system", "Mine", tier="user", extension="mine"), granted=True)



def test_the_scripted_provider_passes_the_contract_suite(providers: dict[str, Any]) -> None:
    assert asyncio.run(rt.test("memory.system", "test/scripted")) == {"ok": True, "detail": "The provider passed the contract suite."}
    assert asyncio.run(rt.test("memory.system", "test/broken"))["ok"] is False


# ---- context assembly ----
def test_the_selected_provider_supplies_the_profile_and_recalled_blocks_for_that_chat_only(providers: dict[str, Any]) -> None:
    appmod.memories.create(None, "User keeps llamas", kind="preference", pinned=True)
    mine, other = chat("scripted"), chat()
    system, used = assemble(mine)
    assert "Scripted recall" in system and "Scripted standing preferences" in system and "llamas" not in system
    assert used["memories"] == [{"id": "s1", "content": "the user keeps bees", "project_id": None}]
    ids = {e["id"]: e for e in used["ledger"].entries}
    assert ids["memory:recalled"]["slot"] == "volatile" and ids["memory:profile"]["slot"] == "stable" and ids["memory:recalled"]["owner"] == "test"
    assert ("recall", ("what do I keep", WINDOW)) in providers["scripted"].calls and ("profile", WINDOW) in providers["scripted"].calls
    system2, _ = assemble(other)  # another chat still gets the built-in memory
    assert "Scripted" not in system2 and "User keeps llamas" in system2


def test_a_project_selection_applies_and_a_chat_selection_overrides_it(providers: dict[str, Any]) -> None:
    project = appmod.projects.create("Bees")["id"]
    appmod.projects.update(project, {"runtime": {"memory.system": "test/scripted"}})
    inherits, overrides = chat(project_id=project), chat(project_id=project)
    appmod.convos.update(overrides, {"settings": {"runtime": {"memory.system": "core/memory"}}})
    assert "Scripted recall" in assemble(inherits)[0]
    assert "Scripted" not in assemble(overrides)[0]


def test_the_chats_memory_switches_stay_in_front_of_the_provider(providers: dict[str, Any]) -> None:
    system, _ = assemble(chat("scripted", useMemory=False, useGraph=False))
    assert "Scripted" not in system and providers["scripted"].calls == []
    system, _ = assemble(chat("scripted", useMemory=False))  # graph still on: the provider is asked, but its memory blocks and profile are not used
    assert "Scripted" not in system and [c for c, _ in providers["scripted"].calls] == ["recall"]


def test_the_default_provider_makes_exactly_the_blocks_context_makes_itself() -> None:
    appmod.memories.create(None, "User drinks oolong", kind="preference", pinned=True)
    appmod.memories.create(None, "User drinks oolong in the morning")
    cs = {"useMemory": True, "useGraph": True}
    common = dict(memories=appmod.memories, graph=appmod.graph, documents=appmod.documents, project=None, project_id=None, query="oolong",
                  settings=appmod.settings(), conv_settings=cs, global_system_prompt="", window=WINDOW)
    plain, _ = build_context(**common)
    scope = MemoryScope(None, None, cs)
    blocks = MemoryBlocks(appmod.core_memory.profile(scope, WINDOW), asyncio.run(appmod.core_memory.recall("oolong", scope, WINDOW)))
    via, _ = build_context(**common, memory=blocks)
    assert via == plain and "oolong" in plain


# ---- learning ----
def test_the_post_turn_learner_calls_the_chats_provider(providers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[Any] = []
    monkeypatch.setattr(appmod.learner, "submit", lambda job: queued.append(job) or True)
    mine, other = chat("scripted"), chat()
    settle(mine)
    assert providers["scripted"].calls == [("learn", ("Noted.", None, mine))] and queued == []
    settle(other)  # the built-in memory queues an extraction job
    assert len(queued) == 1 and queued[0].conversation_id == other


def test_a_chat_with_auto_learn_off_never_reaches_the_provider(providers: dict[str, Any]) -> None:
    settle(chat("scripted", autoLearn=False))
    assert providers["scripted"].calls == []


# ---- failures fall back ----
def test_a_failing_recall_and_profile_fall_back_to_the_built_in_memory_with_a_card(providers: dict[str, Any]) -> None:
    appmod.memories.create(None, "User likes jasmine tea", kind="preference", pinned=True)
    system, _ = assemble(chat("broken"), "jasmine")
    assert "jasmine tea" in system  # the built-in blocks stood in
    assert [t for t, _ in providers["notes"]].count("A selected memory system failed") == 2  # recall and profile, each said once
    wanted = {a[1:3] for a in providers["announced"] if a[0] == "memory.system"}
    assert ("test/broken", "core/memory") in wanted or ("broken", "core/memory") in wanted
    assemble(chat("broken"), "jasmine")
    assert len(providers["notes"]) == 2  # not repeated


def test_a_provider_that_returns_the_wrong_shape_is_replaced_for_that_call(providers: dict[str, Any]) -> None:
    appmod.memories.create(None, "User likes rooibos")
    system, _ = assemble(chat("wrong"), "rooibos")
    assert "rooibos" in system and providers["notes"]


def test_a_failing_learner_falls_back_to_the_built_in_extraction(providers: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[Any] = []
    monkeypatch.setattr(appmod.learner, "submit", lambda job: queued.append(job) or True)
    settle(chat("broken"))
    assert len(queued) == 1 and providers["notes"]


def test_a_selection_that_is_gone_uses_the_built_in_memory(providers: dict[str, Any]) -> None:
    cid = chat()
    appmod.convos.update(cid, {"settings": {"runtime": {"memory.system": "gone/memory"}}})
    assert appmod.memory_routing.selected(cid, None) is None and providers["notes"]


# ---- tools ----
def test_memory_tools_go_to_the_chats_provider(providers: dict[str, Any]) -> None:
    cid = chat("scripted")
    ctx = {"project_id": None, "conversation_id": cid, "message_id": "m1", "conv_settings": {"useMemory": True}}
    found = asyncio.run(appmod.toolbox.specs["search_memory"].fn(ctx, query="bees"))
    assert found["memories"][0]["id"] == "s1" and ("search", ("bees", 100)) in providers["scripted"].calls
    saved = asyncio.run(appmod.toolbox.specs["save_memory"].fn(ctx, content="User keeps bees", kind="fact"))
    assert saved["saved"] == "s2" and ("remember", ("User keeps bees", "fact", "auto")) in providers["scripted"].calls
    assert asyncio.run(appmod.toolbox.specs["save_memory"].fn(ctx, replaces="s1", forget=True)) == {"forgotten": "s1"}
    assert not any(m["content"] == "User keeps bees" for m in appmod.memories.list(None))  # nothing reached the core tables
