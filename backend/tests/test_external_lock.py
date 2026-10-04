"""Actions outside the app, and booking unattended work, always ask: no Settings, Project or Chat map can switch an
external or schedules tool to 'on', and a card's 'Always' does not grant one whole-tool. 'off' is still honoured."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extlock-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
tb = appmod.toolbox
ROUNDS: list[Any] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        # These tests drive their own tool calls; deferral is test_tool_search.py.
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0})
        yield
    llm.stream_chat = real


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def test_effective_caps_external_and_schedules_at_ask_at_every_level() -> None:
    assert tb.specs["gmail_send"].danger == "external" and tb.specs["schedule_task"].danger == "schedules"
    for name in ("gmail_send", "calendar_create", "schedule_task"):
        on = {name: "on"}
        assert tb.effective(on, None, None)[name] == "ask"
        assert tb.effective({}, on, None)[name] == "ask"
        assert tb.effective({}, None, on)[name] == "ask"
        assert tb.effective({name: True}, None, None)[name] == "ask", "a legacy boolean grant is capped too"
        assert tb.effective(on, None, {name: "off"})[name] == "off", "off still wins"
        assert tb.effective({}, {name: "off"}, None)[name] == "off"
    assert tb.effective({"todo_add": "on"}, None, None)["todo_add"] == "on", "in-app tools are untouched"
    assert tb.effective({}, None, {"web_search": "on"})["web_search"] == "on"


def test_saving_on_for_an_external_tool_stores_ask() -> None:
    out = j("PUT", "/settings", {"tools": {"gmail_send": "on", "schedule_task": "on", "todo_add": "on", "mcp__x__y": "on"}})
    assert out["tools"]["gmail_send"] == "ask" and out["tools"]["schedule_task"] == "ask"
    assert out["tools"]["todo_add"] == "on" and out["tools"]["mcp__x__y"] == "on", "only built-in locked tools are coerced"
    j("PUT", "/settings", {"tools": {}})

    pid = j("POST", "/projects", {"name": "Lock"})["id"]
    proj = j("PUT", f"/projects/{pid}", {"tools": {"calendar_create": "on", "gmail_send": "off"}})
    assert proj["tools"] == {"calendar_create": "ask", "gmail_send": "off"}

    cid = j("POST", "/conversations", {})["id"]
    conv = j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"gmail_send": "on", "todo_add": "off"}}})
    assert conv["settings"]["tools"] == {"gmail_send": "ask", "todo_add": "off"}


def _wait(pred: Any, label: str) -> Any:
    deadline = time.time() + 15
    while time.time() < deadline:
        if (v := pred()):
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def test_a_legacy_stored_on_still_shows_a_card_and_always_grants_nothing() -> None:
    cid = j("POST", "/conversations", {})["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"schedule_task": "on"}}})  # past the route, as an older build stored it
    args = {"name": "Chase", "prompt": "Did they reply?", "in_minutes": 30}
    ROUNDS.append({"tool_calls": [{"id": "call_0", "name": "schedule_task", "arguments": json.dumps(args)}]})
    ROUNDS.append(["booked"])
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "remind me"})["run_id"]
    card = _wait(lambda: [a for a in j("GET", "/approvals") if a["run_id"] == rid], "the approval card")[0]
    assert card["tool"] == "schedule_task"
    j("POST", f"/approvals/{card['call_id']}", {"decision": "always_global"})
    _wait(lambda: (r := appmod.run_store.get(rid)) and r["status"] not in ("running", "awaiting_approval"), "the run")
    assert "schedule_task" not in (appmod.settings().get("tools") or {}), "'Always' on a schedules card is one-shot"


# ---- an approved plan step still stands in for the card, now that external tools top out at ask ----
SENT: list[str] = []


async def _fake_send(ctx: dict[str, Any], to: str) -> Any:
    SENT.append(to)
    return {"ok": True, "sent": to}


@pytest.fixture()
def fake_send():  # type: ignore[no-untyped-def]
    from personal_os.tools import ToolSpec, _obj
    real = tb.specs["gmail_send"]
    # Not in the "google" group, which is only offered once Google is connected.
    tb.specs["gmail_send"] = ToolSpec("gmail_send", "fake send", _obj({"to": {"type": "string"}}, ["to"]), _fake_send, "test", "external")
    SENT.clear()
    ROUNDS.clear()
    # A desk here runs its plan and stops: no completion gate, no reviewer turn, no parked card.
    j("PUT", "/settings", {"deskDoneGate": False, "deskSelfReview": False, "parkAfterSeconds": 0, "planMode": "off"})
    yield
    tb.specs["gmail_send"] = real
    ROUNDS.clear()


def _call(name: str, args: dict[str, Any], cid: str) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def _plan_run(steps: list[dict[str, Any]], then: list[dict[str, Any]], desk: bool = False) -> tuple[dict[str, str], dict[str, Any]]:
    """propose_plan, approve it, then make `then` one call per round. Returns the approvals filter and the plan row.

    Taint a plan expected is only honoured where the plan stays active across the run, which is a desk: in a chat
    any taint voids the pre-approval (plans.taint_expected with no active plan)."""
    ROUNDS.append({"tool_calls": [_call("propose_plan", {"title": "Reply", "steps": steps}, "p0")]})
    ROUNDS.extend({"tool_calls": [c]} for c in then)
    ROUNDS.append(["done"])
    if desk:
        scope = {"desk_id": j("POST", "/cowork/desks", {"brief": "reply to them", "start": True})["desk"]["id"]}
    else:
        cid = j("POST", "/conversations", {})["id"]
        j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"gmail_send": "on", "search_documents": "on"}}})
        scope = {"run_id": j("POST", f"/conversations/{cid}/chat", {"content": "reply to them"})["run_id"]}
    card = _card(scope, "propose_plan")
    j("POST", f"/approvals/{card['call_id']}", {"decision": "allow"})
    return scope, appmod.plans.by_call(card["call_id"]) or {}


def _rows(scope: dict[str, str], status: str = "pending") -> list[dict[str, Any]]:
    k, v = next(iter(scope.items()))
    return [a for a in j("GET", f"/approvals?status={status}&{k}={v}") if a.get(k) == v]


def _card(scope: dict[str, str], tool: str) -> dict[str, Any]:
    return _wait(lambda: [a for a in _rows(scope) if a["tool"] == tool], f"the {tool} card")[0]


def _done(scope: dict[str, str]) -> None:
    if "run_id" in scope:
        _wait(lambda: (r := appmod.run_store.get(scope["run_id"])) and r["status"] not in ("running", "awaiting_approval"), "the run")
    else:
        _wait(lambda: not j("GET", f"/runs?desk_id={scope['desk_id']}"), "the desk's runs")


def _sends(scope: dict[str, str]) -> list[dict[str, Any]]:
    return [a for a in _rows(scope, "all") if a["tool"] == "gmail_send"]


def test_an_approved_gmail_send_step_runs_without_a_card(fake_send: None) -> None:
    args = {"to": "a@example.com"}
    scope, plan = _plan_run([{"tool": "gmail_send", "arguments": args}], [_call("gmail_send", args, "s0")])
    _done(scope)
    assert SENT == ["a@example.com"] and _sends(scope) == [], "the capped 'ask' is what the plan card answered"
    assert appmod.plans.get(plan["plan_id"])["steps"][0]["status"] == "done"


def test_taint_the_plan_expected_still_claims_its_gmail_send_step(fake_send: None) -> None:
    query, args = {"query": "their last message"}, {"to": "b@example.com"}
    scope, plan = _plan_run([{"tool": "search_documents", "arguments": query}, {"tool": "gmail_send", "arguments": args}],
                            [_call("search_documents", query, "t0"), _call("gmail_send", args, "s0")], desk=True)
    assert plan["expected_taint"] == ["search_documents"], "the plan card named the read"
    _done(scope)
    assert SENT == ["b@example.com"] and _sends(scope) == [], "the user already approved this send after this read"
    assert [st["status"] for st in appmod.plans.get(plan["plan_id"])["steps"]] == ["done", "done"]


@pytest.mark.parametrize("desk", [False, True])
def test_taint_the_plan_did_not_expect_still_asks_and_is_forced(fake_send: None, desk: bool) -> None:
    args = {"to": "c@example.com"}
    scope, plan = _plan_run([{"tool": "gmail_send", "arguments": args}],
                            [_call("search_documents", {"query": "anything"}, "t0"), _call("gmail_send", args, "s0")], desk=desk)
    card = _card(scope, "gmail_send")
    assert card["forced"] is True
    j("POST", f"/approvals/{card['call_id']}", {"decision": "deny"})
    _done(scope)
    assert SENT == []
    assert appmod.plans.get(plan["plan_id"])["steps"][0]["status"] == "approved", "the step was not spent"
