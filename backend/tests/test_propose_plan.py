"""propose_plan: one approval card for a set of calls, each bound to its argument digest.

What these cover, end to end through the chat loop: an approved step runs with no second modal; a step whose
arguments differ by one character falls back to the per-call gate; an approved step is single use; a `forced`
approval (untrusted content) always prompts even when a matching step is approved; a rejected plan blocks its
steps; and an edit authorises the edited arguments and not the ones the model proposed.

Run: uv run --project backend --with pytest pytest backend/tests/test_propose_plan.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="plantest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, plans as plansmod  # noqa: E402
from personal_os.runs import args_digest  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
plans = appmod.plans

ROUNDS: list[Any] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto") -> Any:
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
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    yield
    ROUNDS.clear()


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


def plan_call(steps: list[dict[str, Any]], title: str = "Do the things", cid: str = "p0") -> dict[str, Any]:
    return call("propose_plan", {"title": title, "steps": steps}, cid)


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


def answer_plan(run_id: str, decision: str = "allow", **body: Any) -> dict[str, Any]:
    """Wait for the plan card, answer it, and return the plan row."""
    a = wait_until(lambda: pending(run_id, "propose_plan"), "the plan card")[0]
    j("POST", f"/approvals/{a['call_id']}", {"decision": decision, **body})
    return plans.by_call(a["call_id"]) or {}


def todos_named(title: str) -> list[dict[str, Any]]:
    return [t for t in appmod.todos.list("__all__") if t["title"] == title]


def results(run_id: str) -> list[dict[str, Any]]:
    return [d for _, e, d in store.events(run_id) if e == "tool_result"]


# ---------------- the plan artifact ----------------
def test_a_plan_records_its_steps_with_the_approvals_digest() -> None:
    """One card, the real arguments, and a digest per step from the canonicalisation approvals already use."""
    args = {"title": "Buy milk", "priority": 1}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": args, "why": "the user asked"}])]})
    ROUNDS.append(["done"])
    cid, rid = start({"tools": {"todo_add": "ask"}})
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    assert a["status"] == "pending" and a["forced"] is False
    assert a["args"]["steps"][0]["arguments"] == args, "the card carries the real arguments, not a summary"

    plan = plans.by_call(a["call_id"])
    assert plan and plan["status"] == "pending" and plan["conversation_id"] == cid and plan["title"] == "Do the things"
    step = plan["steps"][0]
    assert step["tool"] == "todo_add" and step["args"] == args and step["why"] == "the user asked"
    assert step["status"] == "proposed" and step["edited"] is False
    assert step["args_digest"] == args_digest(args), "the plan reuses runs.args_digest, not a second hashing scheme"

    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert drain(rid)["status"] == "done"
    assert plans.by_call(a["call_id"])["status"] == "approved"
    assert j("GET", f"/runs/{rid}")["plans"][0]["plan_id"] == plan["plan_id"], "the run row carries its plans"
    res = results(rid)[0]
    assert res["name"] == "propose_plan" and res["error"] is None and "approved" in res["result_preview"]


def test_a_plan_the_user_cannot_act_on_never_reaches_them() -> None:
    """Validation is before the card: approving something that would fail anyway teaches the user to click through."""
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "no_such_tool", "arguments": {}}], cid="p0")]})
    ROUNDS.append({"tool_calls": [plan_call([], cid="p1")]})
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "propose_plan", "arguments": {}}], cid="p2")]})
    ROUNDS.append(["done"])
    _cid, rid = start()
    assert drain(rid)["status"] == "done"
    errs = [r["error"] for r in results(rid)]
    assert len(errs) == 3 and all(errs), errs
    assert "no tool called no_such_tool" in errs[0]
    assert "non-empty 'steps'" in errs[1]
    assert "cannot contain propose_plan" in errs[2]
    assert j("GET", f"/runs/{rid}")["plans"] == [], "nothing invalid was recorded as a plan"
    assert pending(rid) == [], "and nothing was put in front of the user"


def test_a_plan_cannot_authorise_a_tool_that_is_off() -> None:
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": "x"}}], cid="p0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "off"}})
    assert drain(rid)["status"] == "done"
    assert "turned off for this chat" in results(rid)[0]["error"]
    assert pending(rid) == [] and j("GET", f"/runs/{rid}")["plans"] == []


def test_propose_plan_always_asks_and_cannot_be_granted_away() -> None:
    """A plan is nothing but its card: 'on' must not skip it, and 'always' must not buy a standing grant."""
    first = {"title": f"grant {time.time()}"}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": first}], cid="p0")]})
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": "second"}}], cid="p1")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask", "propose_plan": "on"}})
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card even with propose_plan set to on")[0]
    j("POST", f"/approvals/{a['call_id']}", {"decision": "always_global"})
    b = wait_until(lambda: [x for x in pending(rid, "propose_plan") if x["call_id"] != a["call_id"]],
                   "the second plan card: 'always' cannot silence a plan")[0]
    j("POST", f"/approvals/{b['call_id']}", {"decision": "deny"})
    assert drain(rid)["status"] == "done"
    assert (appmod.settings().get("tools") or {}).get("propose_plan") != "on", "the grant was not written to settings"


# ---------------- digest binding ----------------
def test_an_approved_step_runs_without_a_second_approval() -> None:
    title = f"plan step {time.time()}"
    args = {"title": title, "priority": 1}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": args}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", args, "s0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    plan = answer_plan(rid)
    assert drain(rid)["status"] == "done"
    assert len(todos_named(title)) == 1, "the pre-authorised step ran"
    assert pending(rid, "todo_add") == [], "and it asked nothing"
    assert [a["tool"] for a in j("GET", f"/approvals?status=all&run_id={rid}")] == ["propose_plan"], "one card for the whole plan"
    step = plans.get(plan["plan_id"])["steps"][0]
    assert step["status"] == "consumed" and step["call_id"] and step["consumed_at"]
    ev = [r for r in results(rid) if r["name"] == "todo_add"][0]
    assert ev["approval"] == "plan" and ev["plan"]["plan_id"] == plan["plan_id"] and ev["plan"]["idx"] == 0


def test_one_different_character_falls_back_to_the_per_call_gate() -> None:
    title = f"plan drift {time.time()}"
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": title}}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title + "!"}, "s0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    plan = answer_plan(rid)
    a = wait_until(lambda: pending(rid, "todo_add"), "the per-call gate for the drifted arguments")[0]
    assert a["args"] == {"title": title + "!"} and a["args_digest"] != plan["steps"][0]["args_digest"]
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    assert drain(rid)["status"] == "done"
    assert todos_named(title + "!") == [] and todos_named(title) == []
    assert plans.get(plan["plan_id"])["steps"][0]["status"] == "approved", "the approved step was not spent on a near miss"


def test_an_approved_step_is_single_use() -> None:
    title = f"plan once {time.time()}"
    args = {"title": title}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": args}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", args, "s0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", args, "s1")]})  # the same call again, a round later
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    plan = answer_plan(rid)
    a = wait_until(lambda: pending(rid, "todo_add"), "the second identical call to re-prompt")[0]
    assert a["call_id"].endswith(":s1"), "the first call rode the plan; only the repeat asked"
    assert len(todos_named(title)) == 1
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert drain(rid)["status"] == "done"
    assert len(todos_named(title)) == 2, "the repeat ran because the user allowed it, not because of the plan"
    assert plans.get(plan["plan_id"])["steps"][0]["status"] == "consumed"


def test_an_extra_step_the_plan_never_mentioned_still_asks() -> None:
    planned, extra = f"planned {time.time()}", f"extra {time.time()}"
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": planned}}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": planned}, "s0"), call("todo_add", {"title": extra}, "s1")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    answer_plan(rid)
    a = wait_until(lambda: pending(rid, "todo_add"), "the unplanned call to ask")[0]
    assert a["args"] == {"title": extra}
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    assert drain(rid)["status"] == "done"
    assert len(todos_named(planned)) == 1 and todos_named(extra) == []


def test_key_order_is_not_data() -> None:
    """The digest is canonical, so the model re-emitting the same arguments in another order still matches."""
    title = f"plan order {time.time()}"
    proposed = {"title": title, "priority": 1, "notes": "n"}
    reordered = {"notes": "n", "priority": 1, "title": title}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": proposed}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", reordered, "s0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    answer_plan(rid)
    assert drain(rid)["status"] == "done"
    assert len(todos_named(title)) == 1 and pending(rid, "todo_add") == []


# ---------------- the escape hatches stay ----------------
def test_a_forced_approval_is_never_satisfied_by_a_plan() -> None:
    """Untrusted content in the reply upgrades the tool to ask; that prompt must always reach the user."""
    title = f"plan forced {time.time()}"
    args = {"title": title}
    spec = appmod.toolbox.specs["todo_add"]
    was = spec.danger
    spec.danger = "external"  # the only danger level taint upgrades; todo_add keeps a visible effect to assert on
    try:
        ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": args}], cid="p0")]})
        ROUNDS.append({"tool_calls": [call("search_documents", {"query": "anything"}, "t0")]})  # taints the reply
        ROUNDS.append({"tool_calls": [call("todo_add", args, "s0")]})
        ROUNDS.append(["done"])
        _cid, rid = start({"tools": {"todo_add": "on", "search_documents": "on"}})
        plan = answer_plan(rid)
        a = wait_until(lambda: pending(rid, "todo_add"), "the forced prompt, plan or no plan")[0]
        assert a["forced"] is True, "this is the taint upgrade, not an ordinary ask"
        assert a["args_digest"] == plan["steps"][0]["args_digest"], "and it matches an approved step exactly"
        j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
        assert drain(rid)["status"] == "done"
        assert todos_named(title) == []
        assert plans.get(plan["plan_id"])["steps"][0]["status"] == "approved", "the plan step was not consumed"
    finally:
        spec.danger = was


def test_rejecting_a_plan_blocks_its_steps() -> None:
    title = f"plan rejected {time.time()}"
    args = {"title": title}
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": args}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", args, "s0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    plan = answer_plan(rid, "deny", note="Not these people.")
    assert plan["status"] == "rejected" and plan["decided_by"] == "user"
    assert drain(rid)["status"] == "done"
    assert todos_named(title) == [], "the rejected step did not run"
    assert pending(rid, "todo_add") == [], "and the user was not asked about it again"
    res = [r for r in results(rid) if r["name"] == "todo_add"][0]
    assert res["error"] and "plan you rejected" in res["error"]
    plan_res = [r for r in results(rid) if r["name"] == "propose_plan"][0]
    assert plan_res["approval"] == "deny" and "rejected this plan" in plan_res["result_preview"]
    assert "Not these people." in plan_res["result_preview"], "the user's note goes back to the model"
    assert [s["status"] for s in plans.get(plan["plan_id"])["steps"]] == ["rejected"]


# ---------------- editing ----------------
def test_an_edit_authorises_the_edited_arguments_and_not_the_originals() -> None:
    proposed, edited = f"plan proposed {time.time()}", f"plan edited {time.time()}"
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": proposed}},
                                            {"tool": "todo_add", "arguments": {"title": "dropped"}}], cid="p0")]})
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": proposed}, "s0")]})   # what the model proposed
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": edited}, "s1")]})     # what the user authorised
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    plan = answer_plan(rid, "allow", steps=[{"idx": 0, "arguments": {"title": edited}}])
    step, gone = plan["steps"][0], plan["steps"][1]
    assert step["status"] == "approved" and step["edited"] is True and step["args"] == {"title": edited}
    assert step["args_digest"] == args_digest({"title": edited}), "the edit re-derived the digest"
    assert gone["status"] == "dropped", "a step the user removed is not authorised"

    a = wait_until(lambda: pending(rid, "todo_add"), "the original arguments to fall back to the gate")[0]
    assert a["args"] == {"title": proposed}
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"})
    assert drain(rid)["status"] == "done"
    assert todos_named(proposed) == [], "the arguments the user edited away are not authorised"
    assert len(todos_named(edited)) == 1, "the edited arguments ran without asking"
    assert [a["call_id"].rsplit(":", 1)[1] for a in j("GET", f"/approvals?status=all&run_id={rid}")] == ["p0", "s0"]
    res = [r for r in results(rid) if r["name"] == "propose_plan"][0]
    assert edited in res["result_preview"] and "dropped" in res["result_preview"]


# ---------------- the store on its own ----------------
def test_a_claim_is_atomic_so_two_calls_cannot_share_one_approval() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run_id = None
    args = {"to": "ana@example.com", "body": "once"}
    appmod.run_store.create("plan-claim-run", cid)
    run_id = "plan-claim-run"
    plan = plans.open("plan-claim:call", {"title": "t", "steps": [{"tool": "gmail_send", "arguments": args, "why": ""}]},
                      run_id=run_id, conversation_id=cid)
    plans.decide("plan-claim:call", "allow")
    first = plans.claim(run_id, "gmail_send", args, "c1")
    second = plans.claim(run_id, "gmail_send", args, "c2")
    assert first and first["idx"] == 0 and second is None, "single use"
    assert plans.claim(None, "gmail_send", args, "c3") is None, "a run-less call never claims"
    assert plans.claim("other-run", "gmail_send", args, "c4") is None, "a claim does not cross runs"
    assert plans.get(plan["plan_id"])["steps"][0]["call_id"] == "c1"
    # Deciding twice does not reopen anything: the first answer stands.
    again = plans.decide("plan-claim:call", "deny")
    assert again["status"] == "approved" and again["steps"][0]["status"] == "consumed"


def test_a_pending_plan_authorises_nothing() -> None:
    cid = j("POST", "/conversations", {})["id"]
    appmod.run_store.create("plan-pending-run", cid)
    args = {"title": "unanswered"}
    plans.open("plan-pending:call", {"title": "t", "steps": [{"tool": "todo_add", "arguments": args, "why": ""}]},
               run_id="plan-pending-run", conversation_id=cid)
    assert plans.claim("plan-pending-run", "todo_add", args, "c1") is None
    assert plans.rejected(cid, "todo_add", args) is None


def test_a_stop_rejects_the_plan_without_blocking_it_forever() -> None:
    """A stop means "stop", not "never": only the user's own no keeps blocking those arguments."""
    cid = j("POST", "/conversations", {})["id"]
    appmod.run_store.create("plan-stop-run", cid)
    args = {"title": "stopped"}
    plans.open("plan-stop:call", {"title": "t", "steps": [{"tool": "todo_add", "arguments": args, "why": ""}]},
               run_id="plan-stop-run", conversation_id=cid)
    p = plans.decide("plan-stop:call", "deny", by="stop")
    assert p["status"] == "rejected" and p["decided_by"] == "stop"
    assert plans.rejected(cid, "todo_add", args) is None


def test_edit_payloads_are_checked_before_anything_is_decided() -> None:
    for bad in ([{"arguments": {}}], [{"idx": "0"}], [{"idx": 0, "arguments": [1]}], "nope"):
        with pytest.raises(ValueError):
            plansmod.parse_plan_edits(bad)
    assert plansmod.parse_plan_edits(None) is None
    assert plansmod.parse_plan_edits([{"idx": 2}]) == {2: None}

    title = f"plan badedit {time.time()}"
    ROUNDS.append({"tool_calls": [plan_call([{"tool": "todo_add", "arguments": {"title": title}}], cid="p0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    a = wait_until(lambda: pending(rid, "propose_plan"), "the plan card")[0]
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow", "steps": [{"arguments": {}}]}, expect=400)
    assert store.approval(a["call_id"])["status"] == "pending", "a rejected payload leaves the approval pending"
    assert plans.by_call(a["call_id"])["status"] == "pending"
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert drain(rid)["status"] == "done"


def test_only_a_plan_approval_may_carry_edited_steps() -> None:
    title = f"plan notaplan {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title}, "s0")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "ask"}})
    a = wait_until(lambda: pending(rid, "todo_add"), "an ordinary approval")[0]
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow", "steps": [{"idx": 0, "arguments": {"title": "x"}}]}, expect=400)
    j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert drain(rid)["status"] == "done"
    assert len(todos_named(title)) == 1


def test_a_direct_toolbox_call_records_no_plan() -> None:
    """The plan is its approval card, so the tool function itself authorises nothing."""
    out = asyncio.run(appmod.toolbox.call("propose_plan", {"steps": [{"tool": "todo_add", "arguments": {"title": "x"}}]},
                                          {"project_id": None}))
    assert out["error"] and "approval gate" in out["error"]
