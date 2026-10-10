"""Tool-call normalisation: argument repair, name resolution, and calls that never reach the gate.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_toolcalls.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolcalls-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
appmod.db.set_settings({"toolDeferAbove": 0})  # these tests drive their own tools; deferral is test_tool_search.py
from personal_os import llm  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402
from personal_os.toolcalls import parse_arguments, resolve_name  # noqa: E402

# ---- pure parts -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,want,repaired", [
    ('{"a": 1}', {"a": 1}, False),
    ("", {}, False),
    ("   ", {}, False),
    (json.dumps(json.dumps({"a": 1})), {"a": 1}, True),  # serialised twice
    ('```json\n{"a": 1}\n```', {"a": 1}, True),
    ('Here you go: {"a": 1} done', {"a": 1}, True),
    ('{"a": 1,}', {"a": 1}, True),
    ('{"a": [1, 2,],}', {"a": [1, 2]}, True),
    ('{"p": "C:\\Users\\me"}', {"p": "C:\\Users\\me"}, True),  # bare backslashes
    ('{"p": "a\\nb"}', {"p": "a\nb"}, False),  # a real escape stays
])
def test_parse_arguments_ladder(raw: str, want: dict[str, Any], repaired: bool) -> None:
    args, rep, problem = parse_arguments(raw)
    assert args == want and rep is repaired and problem is None


def test_trailing_comma_removal_leaves_string_values_alone() -> None:
    args, rep, _ = parse_arguments('{"text": "a, }", "b": "x,]",}')
    assert args == {"text": "a, }", "b": "x,]"} and rep is True


@pytest.mark.parametrize("raw", ['{"a": "abc', '{"a": 1', "[1, 2]", "5", '"just text"', "null", "{not json}"])
def test_parse_arguments_failures_are_never_completed(raw: str) -> None:
    args, rep, problem = parse_arguments(raw)
    assert args is None and rep is False and problem


def test_non_object_problem_names_the_type() -> None:
    assert "list" in (parse_arguments("[1]")[2] or "")


def test_resolve_name() -> None:
    known = {"search_memory", "todo_add", "web_fetch"}
    adv = ["search_memory", "todo_add", "web_fetch", "Todo_Add_Long"]
    assert resolve_name("todo_add", known, adv) == ("todo_add", False, None)
    assert resolve_name(" TODO_ADD ", known, adv) == ("todo_add", True, None)
    assert resolve_name("functions.todo_add", known, adv) == ("todo_add", True, None)
    # two advertised tools that differ only by case: ambiguous, so it does not resolve
    name, ok, problem = resolve_name("Foo", known, ["foo", "FOO", "bar"])
    assert not ok and problem and problem.startswith("Unknown tool Foo.") and "foo" in problem
    name, ok, problem = resolve_name("todo_ad", known, adv)
    assert not ok and "todo_add" in (problem or "")
    name, ok, problem = resolve_name("", known, adv)
    assert not ok and problem


def test_unknown_name_suggestions_are_padded_to_eight() -> None:
    adv = [f"tool_{i}" for i in range(12)]
    _, ok, problem = resolve_name("zzz", set(), adv)
    assert not ok and (problem or "").split("Did you mean: ")[1].count(",") == 7


def test_precheck_reports_what_call_would() -> None:
    async def fn(ctx: dict[str, Any], text: str) -> dict[str, Any]:
        return {"ok": True}

    tb = appmod.toolbox
    tb.specs["t_pre"] = ToolSpec("t_pre", "t", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                                 fn, "test", "safe", examples=[{"text": "hi"}])
    try:
        assert tb.precheck("t_pre", {"text": "x"}) is None
        bad = tb.precheck("t_pre", {})
        assert bad and "bad arguments" in bad["error"] and bad["expected"] == "required: text" and bad["example"] == {"text": "hi"}
        assert tb.precheck("t_pre", {"text": "x", "extra": 1})
        assert tb.precheck("no_such_tool", {}) is None  # a connector tool is not a spec
        out = asyncio.run(tb.call("t_pre", {}, {}))
        # the same shape from the backstop: only the TypeError's own wording differs
        assert out["error"].startswith("t_pre: bad arguments") and out["expected"] == bad["expected"] and out["example"] == bad["example"]
    finally:
        tb.specs.pop("t_pre", None)


# ---- the chat loop ------------------------------------------------------------------------------------------------

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []
SEEN: list[list[dict[str, Any]]] = []
RAN: list[dict[str, Any]] = []
NAMES = ("t_echo", "t_ask")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append(copy.deepcopy(messages))
    step = ROUNDS.pop(0) if ROUNDS else ["done"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": step.get("finish_reason", "tool_calls"), "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _echo(ctx: dict[str, Any], text: str) -> dict[str, Any]:
    RAN.append({"text": text})
    return {"ok": True, "text": text}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    obj = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    for name in NAMES:
        appmod.toolbox.specs[name] = ToolSpec(name, name, obj, _echo, "test", "safe", examples=[{"text": "hi"}])
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real
    for n in NAMES:
        appmod.toolbox.specs.pop(n, None)


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    SEEN.clear()
    RAN.clear()
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


def run_chat(modes: dict[str, str]) -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": modes}})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]
    wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "the run to finish")
    return cid, rid


def tape(rid: str) -> list[tuple[str, Any]]:
    return [(e, d) for _, e, d in store.events(rid)]


def call(name: str, arguments: str, cid: str = "c1") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": arguments}


def tool_msgs(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}


def replayed(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [tc["function"] for m in messages if m["role"] == "assistant" for tc in (m.get("tool_calls") or [])]


def test_malformed_call_never_opens_an_approval_and_echoes_empty_object() -> None:
    ROUNDS.append({"tool_calls": [call("t_ask", '{"text": "abc')]})
    cid, rid = run_chat({"t_ask": "ask"})
    assert store.approvals(status=None, run_id=rid) == []
    ev = tape(rid)
    tc = [d for e, d in ev if e == "tool_call"]
    tr = [d for e, d in ev if e == "tool_result"]
    assert len(tc) == 1 and tc[0]["needs_approval"] is False and tc[0]["arguments"] == {}
    assert len(tr) == 1 and tr[0]["invalid"] == "arguments" and tr[0]["error"]
    assert RAN == []
    assert len(SEEN) == 2
    assert [f["arguments"] for f in replayed(SEEN[1])] == ["{}"]
    res = tool_msgs(SEEN[1])["c1"]
    assert "not valid JSON" in res["error"] and res["sent"] == '{"text": "abc' and res["expected"] == "required: text"
    assert res["example"] == {"text": "hi"}


def test_repaired_call_runs_and_is_flagged() -> None:
    ROUNDS.append({"tool_calls": [call("t_echo", '{"text": "hi",}')]})
    cid, rid = run_chat({"t_echo": "on"})
    assert RAN == [{"text": "hi"}]
    tr = [d for e, d in tape(rid) if e == "tool_result"][0]
    assert tr["repaired"] is True and not tr["error"] and tr.get("invalid") is None
    assert [json.loads(f["arguments"]) for f in replayed(SEEN[1])] == [{"text": "hi"}]


def test_a_valid_call_replays_byte_for_byte() -> None:
    raw = '{"text":   "spaced"}'
    ROUNDS.append({"tool_calls": [call("t_echo", raw)]})
    run_chat({"t_echo": "on"})
    assert [f["arguments"] for f in replayed(SEEN[1])] == [raw]


def test_resolved_name_runs_under_its_canonical_name() -> None:
    ROUNDS.append({"tool_calls": [call("  T_ECHO ", '{"text": "x"}')]})
    cid, rid = run_chat({"t_echo": "on"})
    assert RAN == [{"text": "x"}]
    assert [f["name"] for f in replayed(SEEN[1])] == ["t_echo"]
    assert [d["name"] for e, d in tape(rid) if e == "tool_result"] == ["t_echo"]


def test_unknown_and_empty_names_get_a_precise_error_not_turned_off() -> None:
    ROUNDS.append({"tool_calls": [call("t_ech0", "{}", "a"), call("", "{}", "b")]})
    cid, rid = run_chat({"t_echo": "on"})
    res = tool_msgs(SEEN[1])
    assert res["a"]["error"].startswith("Unknown tool t_ech0.") and "t_echo" in res["a"]["error"]
    assert "turned off" not in json.dumps(res)
    assert "no name" in res["b"]["error"]
    assert all(f["name"] for f in replayed(SEEN[1])), "no empty function name goes back to the provider"
    assert {d["invalid"] for e, d in tape(rid) if e == "tool_result"} == {"name"}
    assert [m["tool_call_id"] for m in SEEN[1] if m["role"] == "tool"] == ["a", "b"], "one tool message per call id"


def test_known_tool_that_is_off_still_says_turned_off() -> None:
    ROUNDS.append({"tool_calls": [call("t_ask", '{"text": "x"}')]})
    run_chat({"t_ask": "off"})
    assert "turned off for this chat" in tool_msgs(SEEN[1])["c1"]["error"]


def test_signature_mismatch_is_refused_before_a_card() -> None:
    ROUNDS.append({"tool_calls": [call("t_ask", '{"nope": 1}')]})
    cid, rid = run_chat({"t_ask": "ask"})
    assert store.approvals(status=None, run_id=rid) == []
    tr = [d for e, d in tape(rid) if e == "tool_result"][0]
    assert tr["invalid"] == "schema" and "bad arguments" in tr["error"]
    assert RAN == []


def test_a_planning_desk_refuses_a_consequential_call_before_its_signature_is_checked() -> None:
    ROUNDS.append({"tool_calls": [call("todo_add", '{"nope": 1}')]})
    cid = j("POST", "/conversations", {})["id"]
    did = appmod.desks.create(conversation_id=cid, brief="go", autonomy="plan")["id"]
    appmod.convos.update(cid, {"settings": {"deskId": did, "tools": {"todo_add": "on"}}})  # deskId is not client-settable
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]
    wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "the run to finish")
    res = tool_msgs(SEEN[1])["c1"]
    assert "planning" in res["error"] and "bad arguments" not in res["error"], res
    assert [d.get("invalid") for e, d in tape(rid) if e == "tool_result"] == [None]


def test_a_call_cut_at_the_output_limit_is_reported_and_nothing_runs() -> None:
    ROUNDS.append({"finish_reason": "length", "tool_calls": [call("t_echo", '{"text": "a long bo')]})
    cid, rid = run_chat({"t_echo": "on"})
    assert RAN == []
    res = tool_msgs(SEEN[1])["c1"]
    assert "cut off" in res["error"] and "output limit" in res["error"] and "append" in res["error"]
    assert [f["arguments"] for f in replayed(SEEN[1])] == ["{}"]


def test_three_malformed_calls_disable_the_tool_for_the_reply() -> None:
    for i in range(4):
        ROUNDS.append({"tool_calls": [call("t_echo", '{"text": "x', f"c{i}")]})
    cid, rid = run_chat({"t_echo": "on"})
    last = tool_msgs(SEEN[-1])
    assert "disabled for the rest of this reply" in last["c3"]["error"]
    assert RAN == []
