"""propose_plan confidence: a low-confidence plan is refused with a pointer to ask_user; a high-confidence one goes
ahead on its own after planAutoApproveSeconds unless the user answers first, never in manual mode, and never for a
tool that always shows a card."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="planconf-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
plans = appmod.plans
ROUNDS: list[Any] = []
SENT: list[str] = []


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


async def _fake_send(ctx: dict[str, Any], to: str) -> Any:
    SENT.append(to)
    return {"ok": True, "sent": to}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _fresh():  # type: ignore[no-untyped-def]
    from personal_os.tools import ToolSpec, _obj
    tb = appmod.toolbox
    real = tb.specs["gmail_send"]
    tb.specs["gmail_send"] = ToolSpec("gmail_send", "fake send", _obj({"to": {"type": "string"}}, ["to"]), _fake_send, "test", "external")
    SENT.clear()
    ROUNDS.clear()
    j("PUT", "/settings", {"permissionMode": "auto", "planAutoApproveSeconds": 1})
    yield
    tb.specs["gmail_send"] = real
    j("PUT", "/settings", {"permissionMode": "auto", "planAutoApproveSeconds": 30})
    ROUNDS.clear()


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def call(name: str, args: dict[str, Any], cid: str) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def start() -> str:
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": {"gmail_send": "on"}}})
    return j("POST", f"/conversations/{cid}/chat", {"content": "reply"})["run_id"]


def pending(run_id: str, tool: str) -> list[dict[str, Any]]:
    return [a for a in j("GET", "/approvals") if a["run_id"] == run_id and a["tool"] == tool]


def finished(run_id: str) -> Any:
    return wait_until(lambda: (r := store.get(run_id)) and r["status"] not in ("running", "awaiting_approval") and r, "the run")


SEND = {"to": "a@example.com"}


def propose(**extra: Any) -> dict[str, Any]:
    args = {"title": "Reply", "steps": [{"tool": "gmail_send", "arguments": SEND}], **extra}
    return {"tool_calls": [call("propose_plan", args, "p0")]}


def test_low_confidence_is_refused_without_a_card() -> None:
    ROUNDS.extend([propose(confidence="low"), ["ok"]])
    rid = start()
    finished(rid)
    out = [d for _, e, d in store.events(rid) if e == "tool_result"]
    assert "ask_user" in json.dumps(out[0]) and not [a for a in j("GET", "/approvals") if a["run_id"] == rid]
    assert plans.for_run(rid) == []


def test_confidence_and_assumptions_are_stored_and_a_bad_rating_is_an_error() -> None:
    ROUNDS.extend([propose(confidence="medium", assumptions=["Ana is the Ana from Thursday", " "]), ["ok"]])
    rid = start()
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    plan = plans.by_call(a["call_id"])
    assert plan["confidence"] == "medium" and plan["assumptions"] == ["Ana is the Ana from Thursday"] and plan["auto_at"] is None
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    finished(rid)
    _, err = plansmod_normalize(confidence="certain")
    assert err and err["error"]


def plansmod_normalize(**extra: Any) -> tuple[dict[str, Any], Any]:
    from personal_os.plans import normalize_plan
    return normalize_plan({"steps": [{"tool": "todo_add", "arguments": {}}], **extra})


def test_high_confidence_goes_ahead_after_the_countdown_and_is_logged_as_automatic() -> None:
    ROUNDS.extend([propose(confidence="high"), ["done"]])
    rid = start()
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    assert plans.by_call(a["call_id"])["auto_at"] is not None
    finished(rid)
    plan = plans.by_call(a["call_id"])
    assert plan["status"] == "approved" and plan["decided_by"] == "auto"
    assert any("(auto)" in (r["note"] or "") for r in j("GET", "/approvals/history")["items"] if r["call_id"] == a["call_id"])


def test_holding_the_card_stops_the_countdown() -> None:
    ROUNDS.extend([propose(confidence="high"), ["ok"]])
    rid = start()
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    j("POST", f"/plans/{a['call_id']}/hold")
    time.sleep(2.6)
    assert plans.by_call(a["call_id"])["status"] == "pending" and plans.auto_at(a["call_id"]) is None
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    finished(rid)


def test_manual_mode_never_auto_approves() -> None:
    j("PUT", "/settings", {"permissionMode": "manual"})
    ROUNDS.extend([propose(confidence="high"), ["ok"]])
    rid = start()
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    time.sleep(2.6)
    plan = plans.by_call(a["call_id"])
    assert plan["auto_at"] is None and plan["status"] == "pending"
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    finished(rid)


def test_an_always_card_tool_still_cards_inside_an_auto_approved_plan() -> None:
    ROUNDS.extend([propose(confidence="high"), {"tool_calls": [call("gmail_send", SEND, "s0")]}, ["done"]])
    rid = start()
    card = wait_until(lambda: pending(rid, "gmail_send"), "the gmail_send card")[0]
    assert plans.for_run(rid)[0]["decided_by"] == "auto"
    assert SENT == [], "nothing was sent before the user answered the send card"
    j("POST", f"/approvals/{card['call_id']}", {"decision": "allow"})
    finished(rid)
    assert SENT == ["a@example.com"]
