"""Built-in tool deferral: past toolDeferAbove a reply is offered the core tools plus tool_search, and a search
loads more for the rest of the conversation.

Scripted model, no network. Run: python backend/tests/test_tool_search.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolsearch-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, mcp_search, tools  # noqa: E402

ROUNDS: list[list[dict[str, Any]]] = []
OFFERED: list[list[str]] = []  # tool names sent per model call
SIZES: list[int] = []
CALLED: list[str] = []


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    OFFERED.append([t["function"]["name"] for t in tools or []])
    SIZES.append(len(json.dumps(tools or [])))
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


async def _graph_search(ctx: dict[str, Any], query: str = "", **_: Any) -> Any:
    CALLED.append("graph_search")
    return {"results": []}


appmod.toolbox.specs["graph_search"].fn = _graph_search


def call(name: str, args: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return {"id": f"c{i}", "name": name, "arguments": json.dumps(args)}


def reply(cid: str, rounds: list[list[dict[str, Any]]], **settings: Any) -> list[dict[str, Any]]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workspaceRoots": [], "toolDeferAbove": 40, "planMode": "off",
                            **settings})
    ROUNDS[:] = rounds
    OFFERED.clear()
    SIZES.clear()
    CALLED.clear()
    out: list[dict[str, Any]] = []

    async def go() -> None:
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event()):
            if ev[0] in ("tool_result", "assistant_message"):
                out.append({"event": ev[0], **ev[1]})

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    return out


def new_conv() -> str:
    return appmod.convos.create(None, "t", "m")["id"]


def test_default_offers_core_plus_search() -> None:
    reply(new_conv(), [])
    names = OFFERED[0]
    assert "tool_search" in names and "graph_search" not in names and "trash_local_file" not in names
    assert all(n == "tool_search" or tools.is_core(appmod.toolbox.specs[n]) for n in names), names
    assert SIZES[0] < 28_000, SIZES[0]  # core set grew by doc_delete, show, agent_spawn text and deep_research on 2026-10-05


def test_search_loads_and_persists_per_conversation() -> None:
    cid = new_conv()
    ev = reply(cid, [[call("tool_search", {"query": "knowledge graph entities relations"})]])
    assert "graph_search" not in OFFERED[0] and "graph_search" in OFFERED[1]
    ctx = next(e for e in ev if e["event"] == "assistant_message")["context_used"]
    assert ctx["tools_deferred"] > 0 and "Also available via tool_search:" in ctx["system_prompt"]
    reply(cid, [])
    assert "graph_search" in OFFERED[0]  # the next reply in the same conversation keeps it
    reply(new_conv(), [])
    assert "graph_search" not in OFFERED[0]  # another conversation does not


def test_unloaded_tool_is_refused_not_run() -> None:
    ev = reply(new_conv(), [[call("graph_search", {"query": "x"})]])
    res = [e for e in ev if e["event"] == "tool_result"]
    assert not CALLED and res and "not loaded; call tool_search first" in json.dumps(res[0])


def test_plan_bound_call_loads_its_group() -> None:
    prev = appmod.plans.any_in
    appmod.plans.any_in = lambda cid: True  # a chat with a plan: its steps name tools it already settled on
    try:
        reply(new_conv(), [[call("graph_search", {"query": "x"})]])
    finally:
        appmod.plans.any_in = prev
    assert CALLED == ["graph_search"] and "graph_traverse" in OFFERED[1]


def test_history_reseeds_after_restart() -> None:
    cid = new_conv()
    appmod._tool_loaded[cid] = {"graph_search"}
    reply(cid, [[call("graph_search", {"query": "x"})]])
    assert CALLED == ["graph_search"]
    appmod._tool_loaded.clear()  # a restart forgets the cache
    reply(cid, [])
    assert "graph_search" in OFFERED[0]


def test_plan_mode_still_drops_loaded_writers() -> None:
    cid = new_conv()
    appmod._tool_loaded[cid] = {"graph_add", "graph_search"}
    reply(cid, [], planMode="always")
    names = OFFERED[0]
    assert "graph_add" not in names and "graph_search" in names
    for n in names:
        spec = appmod.toolbox.specs.get(n)
        assert n == "propose_plan" or spec is None or spec.danger in appmod.PLAN_SAFE_DANGER, n


def test_skill_view_loads_named_groups() -> None:
    async def _skill_view(ctx: dict[str, Any], **_: Any) -> Any:
        return {"body": "Step 1: call graph_traverse on the person."}
    prev = appmod.toolbox.specs["skill_view"].fn
    appmod.toolbox.specs["skill_view"].fn = _skill_view
    try:
        reply(new_conv(), [[call("skill_view", {"name": "x"})]])
    finally:
        appmod.toolbox.specs["skill_view"].fn = prev
    assert "graph_traverse" not in OFFERED[0] and {"graph_traverse", "graph_search", "graph_add"} <= set(OFFERED[1])


def test_zero_threshold_sends_everything() -> None:
    reply(new_conv(), [], toolDeferAbove=0)
    assert "tool_search" not in OFFERED[0] and "graph_search" in OFFERED[0] and len(OFFERED[0]) > 40


def test_group_hint() -> None:
    assert mcp_search.group_hint([("files", 10), ("mac", 1)]) == "Also available via tool_search: files (10 tools), mac (1 tool)."
    assert mcp_search.group_hint([]) == ""


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
