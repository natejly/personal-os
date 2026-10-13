"""Workers and subagents hold the parent's connector tools, and every connector call a child makes goes through the same
checks the chat loop applies (deny rule, taint turning an 'on' grant into a card, the permission mode, live grants).
An agent's tool list narrows connectors too. Scripted model, stubbed connector call. Run: python backend/tests/test_child_connectors.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="childmcp-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.tools import allows_slug  # noqa: E402

mgr = appmod.subagent_mgr
CALLED: list[str] = []
OFFERED: list[list[str]] = []
SCRIPT: list[dict[str, Any]] = []


async def _stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    OFFERED.append([t["function"]["name"] for t in (tools or [])])
    step = SCRIPT.pop(0) if SCRIPT else {"text": "done", "calls": []}
    yield {"type": "delta", "text": step["text"]}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": step.get("calls", []), "usage": None}


async def _fake_call(slug: str, args: dict[str, Any]) -> Any:
    CALLED.append(slug)
    return {"content": "third-party text"}


class FakeRun:
    live = True
    run_id = "run_fake"

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, event: str, data: Any) -> None:
        self.events.append((event, data))

    def set_status(self, s: str) -> None:
        pass


appmod._mcp_call = _fake_call
_sid = appmod.mcp_store.create_server("beta", command="x")["id"]
_empty = {"type": "object", "properties": {}}
appmod.mcp_store.sync_tools(_sid, [{"name": "read", "description": "read", "parameters": _empty},
                                   {"name": "send", "description": "send", "parameters": _empty}])
SLUGS = {t["name"]: t["slug"] for t in appmod.mcp_store.tools()}
appmod.mcp.ready_slugs = lambda: sorted(SLUGS.values())  # type: ignore[method-assign]
for _s in SLUGS.values():
    appmod.mcp_store.set_grant(_s, "on", "global")


def child_run(calls: list[str], *, role: str = "general", tainted: bool = False, rules: dict | None = None,
              pmode: str = "manual", decision: str | None = None, card: str = "c0") -> tuple[dict, FakeRun]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0, "permissionMode": pmode,
                            "permissionRules": rules or {"allow": [], "ask": [], "deny": []}})
    CALLED.clear()
    OFFERED.clear()
    SCRIPT[:] = [{"text": "", "calls": [{"id": f"c{i}", "name": SLUGS[n], "arguments": json.dumps({})}]} for i, n in enumerate(calls)]
    cid = appmod.convos.create(None, "t", "m")["id"]
    mcp_modes, mcp_schemas = appmod._mcp_tooling(None, cid)
    fr = FakeRun()
    ctx = {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": tainted, "taint_sources": [],
           "allowed_urls": set(), "settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None),
           "mcp_modes": mcp_modes, "mcp_schemas": {s["function"]["name"]: s for s in mcp_schemas}, "depth": 0, "agent_run_id": "",
           "model": "m", "stop": asyncio.Event(), "meter": appmod.RunMeter(), "run": fr}

    async def go() -> dict:
        task = asyncio.create_task(appmod.toolbox.call("agent_spawn", {"task": "t", "role": role}, ctx))
        if decision:
            for _ in range(200):
                if fr_card := [k for k in appmod._approvals if k.endswith(f":{card}")]:
                    appmod.run_store.decide(fr_card[0], decision)
                    appmod._approvals[fr_card[0]].set_result(decision)
                    break
                await asyncio.sleep(0.02)
        return await asyncio.wait_for(task, 10)

    prev = llm.stream_chat
    llm.stream_chat = _stream
    try:
        return asyncio.run(go()), fr
    finally:
        llm.stream_chat = prev


def test_child_modes_carry_connectors_and_respect_allowlists() -> None:
    pm = appmod.toolbox.effective({}, None, None)
    cm = {SLUGS["read"]: "on", SLUGS["send"]: "ask"}
    assert set(cm) <= set(mgr.child_modes(pm, mgr.role_for("general"), None, 1, cm))
    assert not set(cm) & set(mgr.child_modes(pm, mgr.role_for("researcher"), None, 1, cm))  # a read-only role names no connector
    assert set(cm) <= set(mgr.child_modes(pm, mgr.role_for("worker"), None, 1, cm))
    got = mgr.child_modes(pm, mgr.role_for("general"), [SLUGS["read"]], 1, cm)
    assert SLUGS["read"] in got and SLUGS["send"] not in got  # agent_spawn's tools list narrows connectors
    assert allows_slug(["mcp__beta__*"], SLUGS["read"]) and not allows_slug([], SLUGS["read"]) and not allows_slug(["web_search"], SLUGS["read"])


def test_child_is_offered_and_runs_a_granted_connector() -> None:
    out, _ = child_run(["read"])
    assert SLUGS["read"] in OFFERED[0] and CALLED == [SLUGS["read"]] and out["state"] == "completed"


def test_deny_rule_refuses_a_child_connector_call() -> None:
    child_run(["read"], rules={"allow": [], "ask": [], "deny": [SLUGS["read"]]})
    assert CALLED == []


def test_tainted_child_asks_before_a_connector_call() -> None:
    fr = child_run(["read"], tainted=True, decision="deny")[1]
    cards = [d for e, d in fr.events if e == "tool_call" and d.get("needs_approval")]
    assert cards and cards[0]["name"] == SLUGS["read"] and CALLED == []
    child_run(["read"], tainted=True, decision="allow")
    assert CALLED == [SLUGS["read"]]


def test_a_connector_result_taints_the_child() -> None:
    # the second call is an 'on' grant, but the first result taints the child, so it now asks (denied here)
    fr = child_run(["read", "send"], decision="deny", card="c1")[1]
    assert CALLED == [SLUGS["read"]] and any(d.get("needs_approval") for e, d in fr.events if e == "tool_call")


def test_revoked_grant_is_honoured_live() -> None:
    appmod.mcp_store.set_grant(SLUGS["read"], "off", "global")
    try:
        child_run(["read"])
        assert CALLED == []
    finally:
        appmod.mcp_store.set_grant(SLUGS["read"], "on", "global")


def main() -> int:
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"{n} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
