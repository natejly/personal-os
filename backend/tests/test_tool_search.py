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
TOOL_MSGS: list[str] = []  # tool-role message contents the model saw


async def _scripted(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    OFFERED.append([t["function"]["name"] for t in tools or []])
    TOOL_MSGS[:] = [str(m["content"]) for m in messages if m["role"] == "tool"]
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


def reply(cid: str, rounds: list[list[dict[str, Any]]], text: str = "go", **settings: Any) -> list[dict[str, Any]]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workspaceRoots": [], "toolDeferAbove": 40,
                            **settings})
    ROUNDS[:] = rounds
    OFFERED.clear()
    SIZES.clear()
    CALLED.clear()
    out: list[dict[str, Any]] = []

    async def go() -> None:
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content=text), asyncio.Event()):
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
    # core set grew by doc_delete, show, agent_spawn text and deep_research on 2026-10-05; fetch_url/web_search Firecrawl
    # wording on 2026-10-06; the worker tools (delegate, message/check/stop/resume_worker) and send_files on 2026-10-06/07
    # 2026-10-11: terse descriptions, and examples sent only for doubly nested shapes (flat ones ride in the bad-arguments
    # error); 22.8k measured. scripts/tool_schema_size.py prints the per-tool breakdown.
    assert SIZES[0] < 14_500, SIZES[0]  # 2026-10-12: core cut to 18 tools (tools.CORE_ORDER), from 47 and 22.8k
    assert len(tools.CORE_ORDER) <= 18 and len(names) <= len(tools.CORE_ORDER) + 1, names
    assert names[:len(tools.CORE_ORDER)] == [n for n in ["tool_search", *tools.CORE_ORDER[1:]] if n in names], "core is sent in its fixed order"


def test_a_plain_request_loads_the_obvious_tool_before_round_one() -> None:
    reply(new_conv(), [], text="move this file to the trash")
    base = OFFERED[0]
    assert "trash_local_file" in base and "graph_search" not in base
    reply(new_conv(), [], text="hey how are you")
    assert "trash_local_file" not in OFFERED[0] and len(OFFERED[0]) <= len(tools.CORE_ORDER)
    assert base[:len(OFFERED[0])] == OFFERED[0], "the loaded tool only appends to the core"


def test_a_load_only_appends_to_the_tools_array() -> None:
    cid = new_conv()
    reply(cid, [[call("tool_search", {"query": "knowledge graph entities relations"})]])
    assert OFFERED[1][:len(OFFERED[0])] == OFFERED[0] and len(OFFERED[1]) > len(OFFERED[0])
    reply(cid, [[call("tool_search", {"query": "move a file to the trash"})]])
    assert OFFERED[1][:len(OFFERED[0])] == OFFERED[0], "a second load leaves the first one's place alone"


def test_search_loads_and_persists_per_conversation() -> None:
    cid = new_conv()
    ev = reply(cid, [[call("tool_search", {"query": "knowledge graph entities relations"})]])
    assert "graph_search" not in OFFERED[0] and "graph_search" in OFFERED[1]
    ctx = next(e for e in ev if e["event"] == "assistant_message")["context_used"]
    assert ctx["tools_deferred"] > 0 and "graph_search \u2014 " in ctx["system_prompt"]
    reply(cid, [])
    assert "graph_search" in OFFERED[0]  # the next reply in the same conversation keeps it
    reply(new_conv(), [])
    assert "graph_search" not in OFFERED[0]  # another conversation does not


def test_first_use_loads_and_runs_in_the_same_round() -> None:
    cid = new_conv()
    ev = reply(cid, [[call("graph_search", {"query": "x"})]])
    assert CALLED == ["graph_search"] and "graph_search" not in OFFERED[0] and "graph_search" in OFFERED[1]
    assert "graph_search" in appmod._tool_loaded[cid]
    assert not any("not loaded" in json.dumps(e) for e in ev)


def test_first_use_bad_arguments_return_the_schema() -> None:
    ev = reply(new_conv(), [[call("graph_traverse", {"nope": 1})]])
    res = [e for e in ev if e["event"] == "tool_result"]
    assert not CALLED and res and res[0]["error"]
    assert "graph_traverse" in OFFERED[1]  # loaded anyway, so the retry is a plain call
    assert TOOL_MSGS and '"schema"' in TOOL_MSGS[-1] and "required" in TOOL_MSGS[-1]


def test_index_is_static_and_lists_every_deferrable_tool() -> None:
    cid = new_conv()
    ev = reply(cid, [[call("tool_search", {"query": "knowledge graph entities relations"})]])
    sys1 = next(e for e in ev if e["event"] == "assistant_message")["context_used"]["system_prompt"]
    ev = reply(cid, [])
    sys2 = next(e for e in ev if e["event"] == "assistant_message")["context_used"]["system_prompt"]
    assert sys1 == sys2 and "More tools, loaded when you first call one:" in sys1
    assert "graph_search \u2014 " in sys1 and "Call any tool listed above directly by name" in sys1
    assert "Also available via tool_search" not in sys1


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


def test_a_planning_desk_still_drops_loaded_writers() -> None:
    cid = new_conv()
    did = appmod.desks.create(conversation_id=cid, brief="t", autonomy="plan")["id"]
    appmod.convos.update(cid, {"settings": {"deskId": did}})
    appmod._tool_loaded[cid] = {"graph_add", "graph_search"}
    reply(cid, [])
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


def test_gist_and_index() -> None:
    assert mcp_search.gist("Create a custom agent: a named role with a brief that later chats run as.") == "Create a custom agent"
    assert mcp_search.gist("Wait for background subagents and return their reports in full detail") == "Wait for background subagents and return"
    idx = mcp_search.tool_index([("b", "z_tool", "Do z."), ("a", "y_tool", "Do y: more"), ("a", "x_tool", "")])
    assert idx.splitlines()[1:3] == ["a: x_tool, y_tool \u2014 Do y", "b: z_tool \u2014 Do z"] and mcp_search.tool_index([]) == ""


def test_error_prone_tools_carry_a_short_example_in_their_schema() -> None:
    assert tools.EXAMPLE_IN_SCHEMA
    for name in sorted(tools.EXAMPLE_IN_SCHEMA):
        spec = appmod.toolbox.specs[name]
        assert "\nExample: " in spec.schema()["function"]["description"], name
        assert len(json.dumps(spec.examples[0], ensure_ascii=False)) < 150, name


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
