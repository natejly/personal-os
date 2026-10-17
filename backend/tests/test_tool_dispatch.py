"""The tool-path seam (dispatch_tool): tool.pre, tool.result, tool.error, tool.post and tool.taint fire for a chat call,
a connector call from the chat, a worker call, a worker's connector call and a workflow step, and for a journaled call
once.

Run: python -m pytest backend/tests/test_tool_dispatch.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="dispatch-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.dispatch import dispatch_tool  # noqa: E402
from personal_os.extensions.hooks import Ask, Deny, HookBus  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

CTX = {"project_id": "p", "conversation_id": "c", "run_id": "r", "agent_id": "a", "tainted": False, "taint_sources": []}


def fake_toolbox(result: Any = None, *, taints: bool = False, boom: bool = False) -> SimpleNamespace:
    async def call(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
        if boom:
            raise RuntimeError("kaput")
        if taints:
            ctx["tainted"] = True
            ctx.setdefault("taint_sources", []).append(name)
        return {"ok": True} if result is None else result
    return SimpleNamespace(bus=HookBus(), call=call)


def go(box: Any, name: str = "t", args: dict[str, Any] | None = None, ctx: dict[str, Any] | None = None, **kw: Any) -> Any:
    return asyncio.run(dispatch_tool(box, dict(CTX) if ctx is None else ctx, name, args or {"a": 1}, call_id="id1", **kw))


def on(box: Any, hook: str, fn: Any, **kw: Any) -> None:
    box.bus.subscribe(hook, fn, owner=kw.pop("owner", "x"), order=0, **kw)


# ---- the mechanism, on a stand-in toolbox ----
def test_without_subscribers_the_call_passes_through() -> None:
    assert go(fake_toolbox({"v": 1})) == {"v": 1}


def test_pre_sees_the_call_and_a_deny_or_ask_stops_it() -> None:
    box = fake_toolbox()
    seen: list[Any] = []
    on(box, "tool.pre", lambda ev: seen.append(ev) or Deny("not today"))
    out = go(box)
    assert "not today" in out["error"] and "blocked by an extension" in out["error"]
    assert seen[0] == {"name": "t", "args": {"a": 1}, "call_id": "id1",
                       "ctx": {"run_id": "r", "conversation_id": "c", "project_id": "p", "agent_id": "a", "desk_id": None, "tainted": False}}
    ask = fake_toolbox()
    on(ask, "tool.pre", lambda ev: Ask("spend over limit"))
    assert "needs the user's approval" in go(ask)["error"] and "spend over limit" in go(ask)["error"]


def test_a_denied_call_never_reaches_the_body_or_the_after_hooks() -> None:
    box = fake_toolbox()
    ran: list[str] = []
    box.call = lambda *a: ran.append("body")  # type: ignore[assignment]
    after: list[str] = []
    on(box, "tool.pre", lambda ev: Deny("no"))
    on(box, "tool.post", lambda ev: after.append("post"))
    go(box)
    assert ran == [] and after == []


def test_a_gate_that_fails_or_times_out_asks_and_never_allows() -> None:
    def broken(ev: Any) -> Any:
        raise RuntimeError("x")

    for fn in (broken, lambda ev: time.sleep(0.3)):
        box = fake_toolbox()
        on(box, "tool.pre", fn, timeout_ms=50)
        assert "did not answer" in go(box)["error"]
    box = fake_toolbox()
    on(box, "tool.pre", broken, on_failure="deny")
    assert "blocked by an extension" in go(box)["error"]


def test_pre_handlers_run_in_priority_order_and_the_first_objection_wins() -> None:
    box = fake_toolbox()
    order: list[str] = []
    on(box, "tool.pre", lambda ev: order.append("late") or Deny("late"), priority=70)
    on(box, "tool.pre", lambda ev: order.append("early") or Deny("early"), priority=10)
    assert "early" in go(box)["error"] and order == ["early"]


def test_result_is_piped_over_every_channel_the_loops_split_out() -> None:
    full = {"text": "key sk-123", "images": [{"name": "a.png"}], "show": {"kind": "html"}, "research": {"t": 1}, "dashboard": {"d": 1}}
    box = fake_toolbox(full)
    payloads: list[Any] = []

    def redact(ev: dict[str, Any]) -> dict[str, Any]:
        payloads.append(ev)
        r = ev["result"]
        return {**ev, "result": {k: ("[x]" if k == "text" else None if k != "images" else []) for k in r}}

    on(box, "tool.result", redact)
    out = go(box)
    assert out == {"text": "[x]", "images": [], "show": None, "research": None, "dashboard": None}
    assert payloads[0]["name"] == "t" and payloads[0]["tainted"] is False and payloads[0]["result"] == full


def test_result_handlers_chain_in_order_and_a_failure_leaves_the_value() -> None:
    box = fake_toolbox({"n": "a"})

    def boom(ev: Any) -> Any:
        raise RuntimeError("x")

    on(box, "tool.result", lambda ev: {**ev, "result": {"n": ev["result"]["n"] + "b"}}, priority=10)
    on(box, "tool.result", boom, priority=20)
    on(box, "tool.result", lambda ev: {**ev, "result": {"n": ev["result"]["n"] + "c"}}, priority=30)
    assert go(box) == {"n": "abc"}


def test_result_handlers_work_on_a_copy_not_the_tools_own_object() -> None:
    own = {"n": "a"}
    box = fake_toolbox(own)
    on(box, "tool.result", lambda ev: ev["result"].update(n="mutated"))
    go(box)
    assert own == {"n": "a"}


def test_a_slow_result_handler_times_out_and_the_result_is_untouched() -> None:
    box = fake_toolbox({"n": "a"})

    async def slow(ev: Any) -> Any:
        await asyncio.sleep(1)
        return {**ev, "result": {"n": "late"}}

    on(box, "tool.result", slow, timeout_ms=30)
    assert go(box) == {"n": "a"}


def test_post_sees_the_piped_result_and_error_fires_for_an_error_result() -> None:
    box = fake_toolbox({"error": "disk full sk-ant-abcdefghijklmnop"})
    posts: list[Any] = []
    errors: list[Any] = []
    on(box, "tool.post", posts.append)
    on(box, "tool.error", errors.append)
    on(box, "tool.result", lambda ev: {**ev, "result": {"error": "disk full"}})
    go(box)
    assert posts[0]["name"] == "t" and posts[0]["args"] == {"a": 1} and posts[0]["error"] == "disk full"
    assert "disk full" in posts[0]["result_preview"] and posts[0]["duration_ms"] >= 0
    assert errors == [{"name": "t", "call_id": "id1", "error": "disk full"}]


def test_a_raised_exception_fires_error_and_post_and_still_raises() -> None:
    box = fake_toolbox(boom=True)
    seen: list[str] = []
    on(box, "tool.error", lambda ev: seen.append(ev["error"]))
    on(box, "tool.post", lambda ev: seen.append("post:" + ev["error"]))
    with pytest.raises(RuntimeError):
        go(box)
    assert seen == ["RuntimeError: kaput", "post:RuntimeError: kaput"]


def test_a_failing_observer_does_not_break_the_call() -> None:
    box = fake_toolbox({"v": 1})

    def boom(ev: Any) -> None:
        raise RuntimeError("x")

    for hook in ("tool.post", "tool.error", "tool.taint"):
        on(box, hook, boom)
    assert go(box) == {"v": 1}


def test_taint_fires_for_each_new_source() -> None:
    box = fake_toolbox(taints=True)
    seen: list[Any] = []
    on(box, "tool.taint", seen.append)
    go(box, ctx={"taint_sources": ["web"], "tainted": True})
    assert seen == [{"name": "t", "source": "t"}]


def test_a_connector_call_taints_under_its_own_name() -> None:
    box = fake_toolbox()
    seen: list[Any] = []
    on(box, "tool.taint", seen.append)

    async def conn() -> Any:
        return {"content": "x"}

    assert go(box, name="mcp__s__t", connector=conn) == {"content": "x"}
    assert seen == [{"name": "mcp__s__t", "source": "mcp__s__t"}]


def test_a_clean_call_fires_no_taint() -> None:
    box = fake_toolbox()
    seen: list[Any] = []
    on(box, "tool.taint", seen.append)
    go(box)
    assert seen == []


def test_disposing_a_subscription_stops_its_hook() -> None:
    box = fake_toolbox()
    d = box.bus.subscribe("tool.pre", lambda ev: Deny("no"), owner="x", order=0)
    assert "error" in go(box)
    d.dispose()
    assert go(box) == {"ok": True}


# ---- the loops: each one reaches the hooks ----
HOOKS = ("tool.pre", "tool.result", "tool.post", "tool.error", "tool.taint")


class Tap:
    """Subscribes to every tool hook on the real bus and records the names it saw."""
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.subs = [appmod.toolbox.bus.subscribe(h, self._fn(h), owner="tap", order=0) for h in HOOKS]

    def _fn(self, hook: str) -> Any:
        def fn(ev: dict[str, Any]) -> Any:
            self.seen.append((hook, ev["name"]))
            return {**ev} if hook == "tool.result" else None
        return fn

    def hooks_for(self, name: str) -> set[str]:
        return {h for h, n in self.seen if n == name}

    def close(self) -> None:
        for s in self.subs:
            s.dispose()


@pytest.fixture
def tap() -> Any:
    t = Tap()
    yield t
    t.close()


async def stub_ok(ctx: dict[str, Any]) -> Any:
    return {"ok": True}


async def stub_bad(ctx: dict[str, Any]) -> Any:
    return {"error": "nope"}


appmod.toolbox.specs["disp_ok"] = ToolSpec("disp_ok", "test", _obj({}, []), stub_ok, "test", "safe", taints=True)
appmod.toolbox.specs["disp_bad"] = ToolSpec("disp_bad", "test", _obj({}, []), stub_bad, "test", "safe")
appmod.toolbox.specs["disp_write"] = ToolSpec("disp_write", "test", _obj({}, []), stub_ok, "test", "writes")
EXPECTED = {"tool.pre", "tool.result", "tool.post"}

_sid = appmod.mcp_store.create_server("dispatchsrv", command="x")["id"]
appmod.mcp_store.sync_tools(_sid, [{"name": "read", "description": "read", "parameters": _obj({}, []),
                                    "annotations": {"readOnlyHint": True}}])
SLUG = next(t["slug"] for t in appmod.mcp_store.tools() if t["slug"].startswith("mcp__dispatchsrv__"))
appmod.mcp_store.set_grant(SLUG, "on", "global")


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []

    async def fake(slug: str, args: dict[str, Any]) -> Any:
        called.append(slug)
        return {"content": "third-party text"}

    monkeypatch.setattr(appmod, "_mcp_call", fake)
    monkeypatch.setattr(appmod.mcp, "ready_slugs", lambda: [SLUG])
    return called


def scripted(rounds: list[list[dict[str, Any]]]) -> Any:
    async def stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
        calls = rounds.pop(0) if rounds else []
        yield {"type": "delta", "text": "ok" if not calls else ""}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}
    return stream


def chat(name: str, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "permissionMode": "allow_all",
                            "permissionRules": {"allow": [], "ask": [], "deny": []}})
    cid = appmod.convos.create(None, "t", "m")["id"]
    monkeypatch.setattr(llm, "stream_chat", scripted([[{"id": "c0", "name": name, "arguments": "{}"}]]))
    events: list[dict[str, Any]] = []

    async def run() -> None:
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event()):
            if ev[0] == "tool_result":
                events.append(ev[1])

    asyncio.run(run())
    return events


def test_a_chat_call_fires_the_tool_hooks(tap: Tap, monkeypatch: pytest.MonkeyPatch) -> None:
    events = chat("disp_ok", monkeypatch)
    assert events and tap.hooks_for("disp_ok") == EXPECTED | {"tool.taint"}
    chat("disp_bad", monkeypatch)
    assert tap.hooks_for("disp_bad") == EXPECTED | {"tool.error"}


def test_a_chat_connector_call_fires_the_tool_hooks(tap: Tap, connector: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    chat(SLUG, monkeypatch)
    assert connector == [SLUG] and tap.hooks_for(SLUG) == EXPECTED | {"tool.taint"}


def test_a_chat_tool_pre_deny_keeps_a_connector_from_being_contacted(connector: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    d = appmod.toolbox.bus.subscribe("tool.pre", lambda ev: Deny("off limits") if ev["name"] == SLUG else None, owner="t", order=0)
    try:
        events = chat(SLUG, monkeypatch)
    finally:
        d.dispose()
    assert connector == [] and "off limits" in events[0]["error"]


def child_ctx(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    cid = appmod.convos.create(None, "t", "m")["id"]
    mcp_modes, mcp_schemas = appmod._mcp_tooling(None, cid)
    return {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": False, "taint_sources": [],
            "allowed_urls": set(), "settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None),
            "mcp_modes": mcp_modes, "mcp_schemas": {s["function"]["name"]: s for s in mcp_schemas}, "depth": 0, "agent_run_id": "",
            "model": "m", "stop": asyncio.Event(), "meter": appmod.RunMeter(),
            "run": SimpleNamespace(live=True, run_id="run_fake", publish=lambda *a: None, set_status=lambda s: None), **(extra or {})}


def worker(name: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0, "permissionMode": "allow_all",
                            "permissionRules": {"allow": [], "ask": [], "deny": []}})
    monkeypatch.setattr(llm, "stream_chat", scripted([[{"id": "c0", "name": name, "arguments": "{}"}]]))
    return asyncio.run(asyncio.wait_for(appmod.toolbox.call("agent_spawn", {"task": "t", "role": "general"}, child_ctx()), 10))


def test_a_worker_call_fires_the_tool_hooks(tap: Tap, monkeypatch: pytest.MonkeyPatch) -> None:
    assert worker("disp_ok", monkeypatch)["state"] == "completed"
    assert tap.hooks_for("disp_ok") == EXPECTED | {"tool.taint"}


def test_a_worker_connector_call_fires_the_tool_hooks(tap: Tap, connector: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    worker(SLUG, monkeypatch)
    assert connector == [SLUG] and tap.hooks_for(SLUG) == EXPECTED | {"tool.taint"}


def test_a_workflow_step_fires_the_tool_hooks(tap: Tap, monkeypatch: pytest.MonkeyPatch) -> None:
    store, engine = appmod.workflow_store, appmod.workflow_engine
    appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
    w = store.save(json.dumps({"name": "dispatch-flow", "steps": [{"id": "one", "tool": "disp_ok"}, {"id": "two", "tool": "disp_bad"}]}))

    async def go_() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        for _ in range(400):
            if store.get_run(r["id"])["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.02)

    asyncio.run(go_())
    assert tap.hooks_for("disp_ok") >= EXPECTED and tap.hooks_for("disp_bad") >= {"tool.pre", "tool.error"}


def journaled_run() -> Any:
    run_id = f"disp-{time.time_ns()}"
    appmod.run_store.create(run_id, None)
    return SimpleNamespace(run_id=run_id, store=appmod.run_store, desk_id=None, input={}, kind="chat")


def test_a_journaled_call_runs_the_hooks_once_and_a_replay_returns_the_piped_result() -> None:
    n = {"calls": 0}

    async def counting(ctx: dict[str, Any]) -> Any:
        n["calls"] += 1
        return {"value": "secret"}

    appmod.toolbox.specs["disp_journal"] = ToolSpec("disp_journal", "test", _obj({}, []), counting, "test", "writes")
    d = appmod.toolbox.bus.subscribe("tool.result", lambda ev: {**ev, "result": {"value": "[redacted]"}}
                                     if ev["name"] == "disp_journal" else None, owner="t", order=0)
    posts: list[str] = []
    p = appmod.toolbox.bus.subscribe("tool.post", lambda ev: posts.append(ev["name"]), owner="t", order=1)
    run = journaled_run()
    try:
        first = asyncio.run(appmod._call_tool(run, 1, "disp_journal", {}, dict(CTX), "u1"))
        again = asyncio.run(appmod._call_tool(run, 1, "disp_journal", {}, dict(CTX), "u1"))
    finally:
        d.dispose()
        p.dispose()
    assert first == {"value": "[redacted]"} and again == {"value": "[redacted]", "replayed": True}
    assert n["calls"] == 1 and posts.count("disp_journal") == 1


def test_a_pre_deny_inside_the_journal_is_recorded_as_the_calls_result() -> None:
    n = {"calls": 0}

    async def counting(ctx: dict[str, Any]) -> Any:
        n["calls"] += 1
        return {"ok": True}

    appmod.toolbox.specs["disp_journal2"] = ToolSpec("disp_journal2", "test", _obj({}, []), counting, "test", "writes")
    d = appmod.toolbox.bus.subscribe("tool.pre", lambda ev: Deny("frozen") if ev["name"] == "disp_journal2" else None, owner="t", order=0)
    run = journaled_run()
    try:
        out = asyncio.run(appmod._call_tool(run, 1, "disp_journal2", {}, dict(CTX), "u1"))
    finally:
        d.dispose()
    row = appmod.run_store.executed(run.run_id)[0]
    assert "frozen" in out["error"] and row["result"] == out and n["calls"] == 0


def test_a_script_call_through_the_tool_bridge_passes_the_gate(tmp_path: Path) -> None:
    """A worker or workflow run has no bridge_call, so the bridge's own fallback must go through the seam."""
    from personal_os import toolbridge
    d = appmod.toolbox.bus.subscribe("tool.pre", lambda ev: Deny("off limits") if ev["name"] == "fs_glob" else None, owner="t", order=0)
    try:
        b = toolbridge.Bridge(appmod.toolbox, child_ctx(), ["fs_glob"], {"fs_glob": "on"}, None)
        out = asyncio.run(b.handle("fs_glob", {"pattern": "*", "root": str(tmp_path)}))
    finally:
        d.dispose()
    assert "off limits" in out["result"]["error"]


def test_a_denied_worker_connector_call_does_not_taint_the_run(connector: list[str]) -> None:
    ctx = child_ctx()
    appmod.db.set_settings({"permissionMode": "allow_all", "permissionRules": {"allow": [], "ask": [], "deny": []}})
    d = appmod.toolbox.bus.subscribe("tool.pre", lambda ev: Deny("off limits"), owner="t", order=0)
    try:
        ch = SimpleNamespace(ctx=ctx, id="ch", conversation_id=ctx["conversation_id"], message_id=None, desk_id=None, kind="general",
                             touch=lambda **kw: None)
        decision, result, ran = asyncio.run(appmod.subagent_mgr._exec_mcp(ch, "u1", SLUG, {}, "on"))
    finally:
        d.dispose()
    assert connector == [] and "off limits" in result["error"] and not ctx["tainted"] and ctx["taint_sources"] == []


def test_tool_post_args_have_credentials_scrubbed() -> None:
    box = fake_toolbox()
    seen: list[Any] = []
    on(box, "tool.post", seen.append)
    go(box, args={"url": "https://u:hunter2@example.com/x"})
    assert "hunter2" not in json.dumps(seen[0]["args"])


def test_tool_post_error_is_scrubbed_like_tool_error() -> None:
    box = fake_toolbox({"error": "401 for Authorization: Bearer sk-ant-abcdefghijklmnopqrstuv"})
    seen: list[Any] = []
    on(box, "tool.post", seen.append)
    go(box)
    assert "sk-ant-abcdefghijklmnopqrstuv" not in json.dumps(seen[0]["error"])
