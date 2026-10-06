"""Human edits on approval cards (approval_edits.py): the route, the run loop, the digest re-binding.

todo_add stands in for the real editable tools (gmail_send, calendar_create, ...): it needs no Google, has a visible
effect to assert on, and a schema (title required, priority integer). Tests add it to EDITABLE_TOOLS for their duration.

Run: uv run --project backend --with pytest pytest backend/tests/test_approval_edits.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="editstest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import approval_edits, llm  # noqa: E402
from personal_os.runs import Run, args_digest  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
plans = appmod.plans

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
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "delegationForce": False})  # the scripted rounds call tools past round 2
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    yield
    ROUNDS.clear()


@pytest.fixture()
def editable():  # type: ignore[no-untyped-def]
    approval_edits.EDITABLE_TOOLS.add("todo_add")
    yield
    approval_edits.EDITABLE_TOOLS.discard("todo_add")


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def call(name: str, args: dict[str, Any], cid: str = "call_0") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def plan_call(steps: list[dict[str, Any]], cid: str = "p0") -> dict[str, Any]:
    return call("propose_plan", {"title": "Do the things", "steps": steps}, cid)


def start(conv_settings: dict[str, Any] | None = None, content: str = "hi") -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    if conv_settings:
        j("PATCH", f"/conversations/{cid}", {"settings": conv_settings})
    return cid, j("POST", f"/conversations/{cid}/chat", {"content": content})["run_id"]


def drain(run_id: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(run_id)) and r["status"] not in ("running", "awaiting_approval") and r,
                      f"run {run_id} to finish")


def pending(run_id: str, tool: str | None = None) -> list[dict[str, Any]]:
    return [a for a in j("GET", "/approvals") if a["run_id"] == run_id and (tool is None or a["tool"] == tool)]


def asked(rid: str, tool: str = "todo_add") -> dict[str, Any]:
    return wait_until(lambda: pending(rid, tool), "the approval card")[0]


def todos_named(title: str) -> list[dict[str, Any]]:
    return [t for t in appmod.todos.list("__all__") if t["title"] == title]


def results(run_id: str) -> list[dict[str, Any]]:
    return [d for _, e, d in store.events(run_id) if e == "tool_result"]


def test_an_edit_runs_with_the_edited_arguments_and_records_both(editable) -> None:  # type: ignore[no-untyped-def]
    orig, new = f"model wrote {time.time()}", f"human wrote {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": orig})]})
    ROUNDS.append(["added"])
    cid, rid = start({"tools": {"todo_add": "ask"}})
    a = asked(rid)
    res = j("POST", f"/approvals/{a['call_id']}", {"decision": "allow", "arguments": {"title": new, "priority": 2}})
    assert res["status"] == "approved"
    assert drain(rid)["status"] == "done"
    assert todos_named(orig) == [] and len(todos_named(new)) == 1, "the edited arguments ran, the model's did not"
    row = store.approval(a["call_id"])
    assert row["args"] == {"title": orig}, "the original stays on the row"
    assert row["edited_args"] == {"title": new, "priority": 2} and row["edited_by"] == "user"
    assert row["args_digest"] == args_digest({"title": new, "priority": 2}), "the digest is re-bound to what ran"
    ev = [r for r in results(rid) if r["name"] == "todo_add"][0]
    assert ev["arguments"] == {"title": new, "priority": 2}
    assert ev["original_arguments"] == {"title": orig} and ev["edited_arguments"] == {"title": new, "priority": 2}
    assert ev["edited_by"] == "user" and ev["error"] is None
    # durable: the persisted message (what a reload renders from) carries the same fields
    msg = [m for m in j("GET", f"/conversations/{cid}")["messages"] if m["role"] == "assistant"][-1]
    pe = [t for t in msg["tool_events"] if t["id"] == a["call_id"]][0]
    assert pe["edited_by"] == "user" and pe["original_arguments"] == {"title": orig}
    # the idempotency journal is keyed by the edited arguments
    assert any(e["args_digest"] == args_digest({"title": new, "priority": 2}) for e in store.executed(rid))


def test_a_non_editable_tool_rejects_arguments() -> None:
    assert "todo_add" not in approval_edits.EDITABLE_TOOLS
    title = f"not editable {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title})]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    a = asked(rid)
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow", "arguments": {"title": "other"}}, expect=400)
    assert store.approval(a["call_id"])["status"] == "pending", "a rejected edit leaves the card pending"
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert drain(rid)["status"] == "done" and len(todos_named(title)) == 1


def test_invalid_edits_are_rejected_and_leave_the_card_pending(editable) -> None:  # type: ignore[no-untyped-def]
    title = f"invalid edit {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title})]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    a = asked(rid)
    p = f"/approvals/{a['call_id']}"
    j("POST", p, {"decision": "allow", "arguments": {"priority": 1}}, expect=400)               # required title missing
    j("POST", p, {"decision": "allow", "arguments": {"title": 5}}, expect=400)                   # wrong type
    j("POST", p, {"decision": "allow", "arguments": {"title": "x", "bogus": 1}}, expect=400)     # not an argument
    j("POST", p, {"decision": "allow", "arguments": {"title": "x", "priority": "high"}}, expect=400)
    j("POST", p, {"decision": "deny", "arguments": {"title": "x"}}, expect=400)                  # a no cannot edit
    assert store.approval(a["call_id"])["status"] == "pending"
    j("POST", p, {"decision": "allow"})                                                           # still answerable, unedited
    assert drain(rid)["status"] == "done" and len(todos_named(title)) == 1
    assert store.approval(a["call_id"])["edited_args"] is None
    assert "edited_by" not in [r for r in results(rid) if r["name"] == "todo_add"][0]


def test_registered_validators_run(editable) -> None:  # type: ignore[no-untyped-def]
    approval_edits.register_validator("todo_add", lambda a: "no shouting" if str(a.get("title", "")).isupper() else None)
    try:
        ROUNDS.append({"tool_calls": [call("todo_add", {"title": "quiet"})]})
        ROUNDS.append(["done"])
        _cid, rid = start({"tools": {"todo_add": "ask"}})
        a = asked(rid)
        r = client.post(f"/approvals/{a['call_id']}", json={"decision": "allow", "arguments": {"title": "LOUD"}})
        assert r.status_code == 400 and "no shouting" in r.text
        j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
        assert drain(rid)["status"] == "done"
    finally:
        approval_edits._validators["todo_add"].pop()  # noqa: SLF001


def test_the_builtin_gmail_validator() -> None:
    schema = appmod.toolbox.specs["gmail_send"].parameters
    ok = approval_edits.validate("gmail_send", {"to": "a@b.com", "subject": "s", "body": "hi"}, schema)
    assert ok["to"] == "a@b.com"
    for bad in ({"to": "nobody", "subject": "s", "body": "hi"}, {"to": "a@b.com", "subject": "s", "body": "  "},
                {"to": "a@b.com", "body": "x"}):
        with pytest.raises(approval_edits.EditError):
            approval_edits.validate("gmail_send", bad, schema)
    with pytest.raises(approval_edits.EditError):
        approval_edits.validate("gmail_outbox", {}, appmod.toolbox.specs["gmail_outbox"].parameters)


def test_a_model_cannot_smuggle_an_edit(editable) -> None:  # type: ignore[no-untyped-def]
    """edited_arguments / edited_by in the model's own call are not an edit path: unknown keys, never read back."""
    title, sneaky = f"model call {time.time()}", f"sneaky {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title, "edited_arguments": {"title": sneaky}, "edited_by": "user"})]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    assert drain(rid)["status"] == "done"
    # The signature rejects the stray keys before any card opens: there is nothing to approve, let alone edit.
    assert [a for a in store.approvals(status=None, run_id=rid)] == []
    assert todos_named(sneaky) == [] and todos_named(title) == []
    ev = [r for r in results(rid) if r["name"] == "todo_add"][0]
    assert ev["error"], "the stray keys fail like any bad argument"
    assert ev["invalid"] == "schema" and not ev["approval"]
    assert "edited_by" not in ev and "edited_arguments" not in ev


def test_an_edit_cannot_ride_a_plan_and_a_forced_card_stays_forced(editable) -> None:  # type: ignore[no-untyped-def]
    """Plan step X is approved. A tainted reply calls X: forced card (the plan is not consulted). The user edits it to Y.
    Y runs; X stays unconsumed; Y is not authorised by the plan, so the model calling Y again asks again."""
    x, y = f"plan X {time.time()}", f"edited Y {time.time()}"
    spec = appmod.toolbox.specs["todo_add"]
    was = spec.danger
    spec.danger = "external"
    try:
        ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": x}}], cid="p0")]})
        ROUNDS.append({"tool_calls": [call("search_documents", {"query": "anything"}, "t0")]})
        ROUNDS.append({"tool_calls": [call("todo_add", {"title": x}, "s0")]})
        ROUNDS.append({"tool_calls": [call("todo_add", {"title": y}, "s1")]})
        ROUNDS.append(["done"])
        _cid, rid = start({"tools": {"todo_add": "on", "search_documents": "on"}})
        pa = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
        j("POST", f"/approvals/{pa['call_id']}", {"decision": "allow"})
        plan = plans.by_call(pa["call_id"])
        a = asked(rid)
        assert a["forced"] is True and a["args_digest"] == plan["steps"][0]["args_digest"]
        j("POST", f"/approvals/{a['call_id']}", {"decision": "allow", "arguments": {"title": y}})
        b = wait_until(lambda: [p for p in pending(rid, "todo_add") if p["call_id"] != a["call_id"]], "a second card for Y")[0]
        assert b["forced"] is True and b["edited_args"] is None, "Y is a new, forced, unedited ask: the edit did not authorise it"
        j("POST", f"/approvals/{b['call_id']}", {"decision": "deny"})
        assert drain(rid)["status"] == "done"
        assert todos_named(x) == [] and len(todos_named(y)) == 1
        assert plans.get(plan["plan_id"])["steps"][0]["status"] == "approved", "the approved step X was not consumed by the edit"
        assert store.approval(a["call_id"])["args_digest"] == args_digest({"title": y})
    finally:
        spec.danger = was


def test_an_edit_on_an_orphaned_card_is_recorded_not_run(editable) -> None:  # type: ignore[no-untyped-def]
    """A run that died while waiting: the edit is validated and recorded on the event, nothing executes."""
    cid = j("POST", "/conversations", {})["id"]
    am = appmod.convos.add_message(cid, "assistant", "")
    run = Run(cid, store)
    uid = f"{am['id']}:call_0"
    store.open_approval(uid, run.run_id, "todo_add", {"title": "orig"}, conversation_id=cid, message_id=am["id"])
    with appmod.db.tx() as c:
        c.execute("UPDATE messages SET tool_events=? WHERE id=?", (json.dumps([{"id": uid, "name": "todo_add", "arguments": {"title": "orig"},
                                                                               "pending": True, "needs_approval": True}]), am["id"]))
    j("POST", f"/approvals/{uid}", {"decision": "allow", "arguments": {"title": "fixed"}})
    ev = [m for m in j("GET", f"/conversations/{cid}")["messages"] if m["id"] == am["id"]][0]["tool_events"][0]
    assert ev["pending"] is False and ev["edited_by"] == "user" and ev["arguments"] == {"title": "fixed"} and ev["original_arguments"] == {"title": "orig"}
    assert todos_named("fixed") == []


def test_a_parked_card_refuses_edits_and_stays_pending(editable) -> None:  # type: ignore[no-untyped-def]
    """No run reads a parked card's edit back, so it must not be accepted (the desk would run the original)."""
    cid = j("POST", "/conversations", {})["id"]
    am = appmod.convos.add_message(cid, "assistant", "")
    run = Run(cid, store)
    uid = f"{am['id']}:call_0"
    store.open_approval(uid, run.run_id, "todo_add", {"title": "orig"}, conversation_id=cid, message_id=am["id"])
    store.park(uid)
    j("POST", f"/approvals/{uid}", {"decision": "allow", "arguments": {"title": "fixed"}}, expect=400)
    assert store.approval(uid)["status"] == "pending"


def test_calendar_propose_validator_follows_contract() -> None:
    """Registered validators return an error string or None; a valid proposal must not read as an error."""
    fns = approval_edits._validators["calendar_propose"]
    ok = {"changes": [{"op": "create", "summary": "Focus", "start": "2026-10-02T09:00", "end": "2026-10-02T10:00"}]}
    assert fns and all(fn(ok) is None for fn in fns)
    assert any(isinstance(fn({"changes": [{"op": "teleport"}]}), str) for fn in fns)
