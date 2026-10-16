"""Post-turn and observer hooks (design doc 5.4, 5.6-5.9): turn.settled, reply.complete, run.after, model.select,
model.request / model.response, subagent.start / stop, approval.requested / resolved, job.fired / done, memory.* and
compaction.before / after, plus the synchronous firing the repositories and the model layer use (emit_sync, pipe_sync).

Run: python -m pytest backend/tests/test_post_turn_hooks.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="postturn-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction, limits, llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions import hooks as hooks_mod  # noqa: E402
from personal_os.extensions.hooks import Deny, HookBus  # noqa: E402
from personal_os.repos import Conversations, Memories, MemoryRefused  # noqa: E402
from personal_os.runs import RunBus, RunStore  # noqa: E402

PROSE = ("I have been thinking about how I want the weekly review to go this year. Most weeks I rush it, and then I regret "
         "that I did not look at what actually happened across the whole seven days. I would like a calmer routine that "
         "starts on Friday afternoon, gathers the loose notes, and ends with two or three plain sentences about what I "
         "want to carry into the following week without any fuss at all.")


def sub(bus: HookBus, hook: str, fn: Any, **kw: Any) -> Any:
    return bus.subscribe(hook, fn, owner=kw.pop("owner", "test"), order=kw.pop("order", 1), **kw)


# ---- emit_sync and pipe_sync ----
def test_emit_sync_runs_sync_handlers_before_it_returns_in_order() -> None:
    bus, seen = HookBus(), []
    sub(bus, "memory.written", lambda p: seen.append(("b", p)), priority=60)
    sub(bus, "memory.written", lambda p: seen.append(("a", p)), priority=40)
    bus.emit_sync("memory.written", 1)
    assert seen == [("a", 1), ("b", 1)]


def test_emit_sync_starts_an_async_handler_on_the_loop_without_waiting() -> None:
    bus, seen = HookBus(), []

    async def handler(p: Any) -> None:
        await asyncio.sleep(0.01)
        seen.append(p)

    async def go() -> None:
        bus.loop = asyncio.get_running_loop()
        bus.emit_sync("subagent.start", "x")
        assert seen == []
        await asyncio.sleep(0.1)
        assert seen == ["x"]
    sub(bus, "subagent.start", handler)
    asyncio.run(go())


def test_emit_sync_drops_an_async_handler_when_no_loop_is_bound() -> None:
    bus = HookBus()

    async def handler(p: Any) -> None:
        raise AssertionError("must not run")
    sub(bus, "subagent.start", handler)
    bus.emit_sync("subagent.start", 1)


def test_emit_sync_failing_handler_is_logged_and_muted_after_five() -> None:
    bus, calls = HookBus(), []

    def bad(p: Any) -> None:
        calls.append(p)
        raise RuntimeError("boom")
    sub(bus, "memory.written", bad)
    sub(bus, "memory.written", lambda p: calls.append("good"))
    for _ in range(hooks_mod.MUTE_AFTER + 2):
        bus.emit_sync("memory.written", "p")
    assert calls.count("p") == hooks_mod.MUTE_AFTER and calls.count("good") == hooks_mod.MUTE_AFTER + 2


def test_close_cancels_the_async_handlers_it_started() -> None:
    bus, state = HookBus(), []

    async def handler(p: Any) -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    async def go() -> None:
        bus.loop = asyncio.get_running_loop()
        bus.emit_sync("subagent.stop", 1)
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.sleep(0.05)
    sub(bus, "subagent.stop", handler)
    asyncio.run(go())
    assert state == ["cancelled"] and bus.loop is None


def test_pipe_sync_chains_returns_deny_as_is_and_skips_async_handlers() -> None:
    bus = HookBus()
    sub(bus, "memory.before_write", lambda v: {**v, "n": v["n"] + 1}, priority=10)

    async def later(v: Any) -> Any:
        return {"n": 100}
    sub(bus, "memory.before_write", later, priority=20)
    sub(bus, "memory.before_write", lambda v: None, priority=30)
    assert bus.pipe_sync("memory.before_write", {"n": 0}) == {"n": 1}
    sub(bus, "memory.before_write", lambda v: Deny("no"), priority=40)
    sub(bus, "memory.before_write", lambda v: {"n": 7}, priority=50)
    assert bus.pipe_sync("memory.before_write", {"n": 0}) == Deny("no")


def test_a_core_handler_is_never_muted_and_has_no_default_timeout() -> None:
    bus, calls = HookBus(), []

    def bad(p: Any) -> None:
        calls.append(p)
        raise RuntimeError("boom")
    sub(bus, "memory.written", bad, owner="core")
    sub(bus, "memory.before_write", bad, owner="core")

    async def slow(p: Any) -> None:
        await asyncio.sleep(0.05)
        calls.append("slow")
    sub(bus, "turn.settled", slow, owner="core")

    async def go() -> None:
        await bus.emit("turn.settled", 1)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(hooks_mod.TIMEOUT_S, "emit", 0.001)
        asyncio.run(go())
    for _ in range(hooks_mod.MUTE_AFTER + 2):
        bus.emit_sync("memory.written", "p")
        bus.pipe_sync("memory.before_write", "v")
    assert calls.count("p") == hooks_mod.MUTE_AFTER + 2 and calls.count("v") == hooks_mod.MUTE_AFTER + 2 and "slow" in calls


def test_pipe_sync_mutes_a_handler_that_keeps_failing() -> None:
    bus, calls = HookBus(), []

    def bad(v: Any) -> None:
        calls.append(v)
        raise RuntimeError("boom")
    sub(bus, "memory.before_write", bad)
    for _ in range(hooks_mod.MUTE_AFTER + 2):
        assert bus.pipe_sync("memory.before_write", "v") == "v"
    assert len(calls) == hooks_mod.MUTE_AFTER


def test_emit_sync_times_out_an_async_handler_and_counts_it_as_a_failure() -> None:
    bus, started = HookBus(), []

    async def hang(p: Any) -> None:
        started.append(p)
        await asyncio.sleep(5)

    async def go() -> None:
        bus.loop = asyncio.get_running_loop()
        for _ in range(hooks_mod.MUTE_AFTER + 1):
            bus.emit_sync("subagent.start", "x")
            await asyncio.sleep(0.05)
    handle = sub(bus, "subagent.start", hang, timeout_ms=10)
    asyncio.run(go())
    assert len(started) == hooks_mod.MUTE_AFTER and bus._subs["subagent.start"][0].muted
    handle.dispose()


# ---- turn.settled and reply.complete through a real chat turn ----
async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    yield {"type": "delta", "text": "ok then"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


class Spy:
    def __init__(self, monkeypatch: Any) -> None:
        self.learn: list[Any] = []
        self.followups: list[Any] = []
        self.titles: list[Any] = []
        self.relearn: list[Any] = []
        monkeypatch.setattr(appmod.learner, "submit", self.learn.append)
        monkeypatch.setattr(appmod.followup_jobs, "spawn", lambda *a: self.followups.append(a))
        monkeypatch.setattr(appmod.title_jobs, "spawn", lambda *a: self.titles.append(a))
        monkeypatch.setattr(appmod.learner, "submit_style", lambda *a: self.relearn.append(a))


def turn(monkeypatch: Any, text: str = PROSE, **settings: Any) -> tuple[str, list[tuple[str, Any]]]:
    appmod.db.set_settings({"autoLearn": True, "learnStyle": True, "autoTitle": True, "followUps": True,
                            "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom", "permissionMode": "manual", **settings})
    monkeypatch.setattr(llm, "stream_chat", _fake)
    cid = appmod.convos.create(None, "New chat", "test-model")["id"]
    body = appmod.ChatIn(content=text)

    async def go() -> Any:
        run = appmod.bus.start(cid, lambda r: appmod._run_chat(r, body), input=body.model_dump())
        await run.task
        return run
    run = asyncio.run(go())
    return cid, [(t, d) for _, t, d in appmod.run_store.events(run.run_id)]


def test_a_finished_reply_sets_all_four_post_turn_jobs_going(monkeypatch: Any) -> None:
    spy = Spy(monkeypatch)
    cid, events = turn(monkeypatch)
    assert len(spy.learn) == 1 and spy.learn[0].conversation_id == cid and spy.learn[0].assistant_text == "ok then"
    assert len(spy.followups) == 1 and spy.followups[0][0] == cid
    assert len(spy.titles) == 1 and spy.titles[0][0] == cid
    assert len(spy.relearn) == 1
    assert any(e == "style_learned" and d["profile"] is None for e, d in events)


def test_the_style_span_is_in_the_trace_the_learner_continues(monkeypatch: Any) -> None:
    spy = Spy(monkeypatch)
    cid, events = turn(monkeypatch)
    spans = [d["span"] for e, d in events if e == "span" and d["span"]["kind"] == "style"]
    assert spans and spans[-1]["end"] is not None and spans[-1]["error"] is None
    assert any(s["kind"] == "style" and s["end"] is not None for s in spy.learn[0].spans)
    stored = appmod.convos.get(cid)["messages"][-1]["trace"]
    assert any(s["kind"] == "style" for s in stored)


def test_each_job_keeps_its_own_gate(monkeypatch: Any) -> None:
    spy = Spy(monkeypatch)
    turn(monkeypatch, autoLearn=False, learnStyle=False, followUps=False, autoTitle=False)
    assert spy.learn == [] and spy.followups == [] and spy.titles == [] and spy.relearn == []
    spy2 = Spy(monkeypatch)
    turn(monkeypatch, PROSE.split(".")[0] + ".", learnStyle=True)
    assert spy2.relearn == [] and len(spy2.learn) == 1, "a short instruction is not banked as style, but the exchange is still learned"


def test_a_subscriber_receives_the_settled_record_and_can_be_disposed(monkeypatch: Any) -> None:
    Spy(monkeypatch)
    got: list[Any] = []
    handle = sub(appmod.host.bus, "turn.settled", got.append)
    cid, _ = turn(monkeypatch)
    assert len(got) == 1
    s = got[0]
    assert (s.conversation_id, s.reply, s.error, s.stopped, s.gone, s.internal, s.tainted) == (cid, "ok then", None, False, False, False, False)
    assert s.user_text == PROSE and s.model == "test-model" and s.message_id
    handle.dispose()
    turn(monkeypatch)
    assert len(got) == 1


def test_an_extension_subscriber_gets_a_scrubbed_copy_not_the_live_record(monkeypatch: Any) -> None:
    spy = Spy(monkeypatch)
    got: list[Any] = []
    def tamper(s: Any) -> None:
        got.append(s)
        s.cfg["autoTitle"] = False

    handle = sub(appmod.host.bus, "turn.settled", tamper)
    try:
        turn(monkeypatch, apiKey="sk-live-secret-key", githubToken="gh-secret")
    finally:
        handle.dispose()
    (s,) = got
    assert not hasattr(s, "tracer") and not hasattr(s, "publish")
    assert "sk-live-secret-key" not in repr(s.cfg) and "gh-secret" not in repr(s.cfg)
    assert len(spy.titles) == 1, "the handler's change to its copy of the settings did not reach the built-in title subscriber"


def test_without_a_run_the_style_events_are_not_published_but_the_trace_is_stored(monkeypatch: Any) -> None:
    Spy(monkeypatch)
    appmod.db.set_settings({"autoLearn": True, "learnStyle": True, "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom"})
    monkeypatch.setattr(llm, "stream_chat", _fake)
    cid = appmod.convos.create(None, "New chat", "test-model")["id"]

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content=PROSE), asyncio.Event(), None)]
    events = asyncio.run(go())
    assert not any(e == "span" and d["span"]["kind"] == "style" for e, d in events) and not any(e == "style_learned" for e, _ in events)
    assert any(sp["kind"] == "style" for sp in appmod.convos.get(cid)["messages"][-1]["trace"])


def test_a_failing_subscriber_does_not_stop_the_others(monkeypatch: Any) -> None:
    spy = Spy(monkeypatch)

    def bad(s: Any) -> None:
        raise RuntimeError("boom")
    handle = sub(appmod.host.bus, "turn.settled", bad)
    try:
        turn(monkeypatch)
    finally:
        handle.dispose()
    assert len(spy.learn) == 1 and len(spy.titles) == 1


def test_reply_complete_carries_the_finished_reply(monkeypatch: Any) -> None:
    Spy(monkeypatch)
    got: list[Any] = []
    handle = sub(appmod.host.bus, "reply.complete", got.append)
    try:
        _, events = turn(monkeypatch)
    finally:
        handle.dispose()
    done = next(d for e, d in events if e == "done")
    assert len(got) == 1
    p = got[0]
    assert p["message_id"] == done["id"] and p["text"] == "ok then" and p["outcome"] is None
    assert set(p) == {"message_id", "text", "tool_events", "usage", "trace", "taint", "outcome"}
    assert p["taint"] == {"tainted": False, "sources": []} and set(p["usage"]) == {"tokens", "cost", "rounds"}


def test_reply_complete_is_a_copy_a_handler_cannot_change(monkeypatch: Any) -> None:
    Spy(monkeypatch)

    def meddle(p: Any) -> None:
        p["trace"].clear()
        p["text"] = "changed"
    handle = sub(appmod.host.bus, "reply.complete", meddle)
    try:
        cid, events = turn(monkeypatch)
    finally:
        handle.dispose()
    done = next(d for e, d in events if e == "done")
    assert done["trace"] and appmod.convos.get(cid)["messages"][-1]["content"] == "ok then"


# ---- run.after ----
def test_run_after_fires_before_the_run_is_closed_and_a_failure_does_not_block_it() -> None:
    hooks = HookBus()
    bus = RunBus(None, hooks)
    seen: list[Any] = []

    def bad(run: Any) -> None:
        raise RuntimeError("boom")
    sub(hooks, "run.after", bad)
    sub(hooks, "run.after", lambda run: seen.append((run.run_id, run.live)))

    async def runner(run: Any) -> None:
        run.publish("delta", {"text": "x"})

    async def go() -> Any:
        run = bus.start("c1", runner)
        await run.task
        return run
    run = asyncio.run(go())
    assert seen == [(run.run_id, True)] and not run.live


def test_the_snapshot_and_child_cleanup_subscribers_are_built_in() -> None:
    subs = appmod.host.bus._subs["run.after"]
    assert [s.owner for s in subs if s.owner == "core"] == ["core", "core"]
    assert any(s.timeout_s == limits.SNAPSHOT_AFTER_MS / 1000 for s in subs)


# ---- model.select ----
def select_turn(monkeypatch: Any, handler: Any, **settings: Any) -> list[dict[str, Any]]:
    Spy(monkeypatch)
    seen: list[dict[str, Any]] = []

    async def fake(settings_: dict[str, Any], model: str, messages: Any, tools: Any = None, **kw: Any) -> Any:
        seen.append({"model": model, "effort": kw.get("effort")})
        yield {"type": "delta", "text": "ok"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}
    handle = sub(appmod.host.bus, "model.select", handler)
    try:
        turn(monkeypatch, "hi", **settings)
        monkeypatch.setattr(llm, "stream_chat", fake)
        cid = appmod.convos.create(None, "t", "chat-model")["id"]

        async def go() -> None:
            async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="hi"), asyncio.Event()):
                pass
        asyncio.run(go())
    finally:
        handle.dispose()
    return seen


def test_model_select_can_swap_the_model_and_effort(monkeypatch: Any) -> None:
    payloads: list[Any] = []

    def pick(v: dict[str, Any]) -> dict[str, Any]:
        payloads.append(dict(v))
        return {**v, "model": "other-model", "effort": "low"}
    seen = select_turn(monkeypatch, pick)
    assert seen == [{"model": "other-model", "effort": "low"}]
    assert payloads[-1]["model"] == "chat-model" and set(payloads[-1]) == {"model", "effort", "reason", "tier"} and payloads[-1]["reason"] == ""


def test_model_select_ignores_a_model_no_provider_serves_and_nonsense(monkeypatch: Any) -> None:
    providers = [{"id": "custom-1", "kind": "custom", "name": "P", "baseUrl": "http://x/v1"}]
    appmod.db.set_settings({"providers": providers})
    try:
        cfg = appmod.settings()
        model, effort = asyncio.run(appmod._select_model(cfg, "custom-1/a", "high", ""))
        assert (model, effort) == ("custom-1/a", "high")
        sub_ = sub(appmod.host.bus, "model.select", lambda v: {**v, "model": "custom-9/zzz", "effort": ""})
        try:
            assert asyncio.run(appmod._select_model(cfg, "custom-1/a", "high", "")) == ("custom-1/a", "high")
        finally:
            sub_.dispose()
        sub_ = sub(appmod.host.bus, "model.select", lambda v: "not a dict")
        try:
            assert asyncio.run(appmod._select_model(cfg, "custom-1/a", "high", "")) == ("custom-1/a", "high")
        finally:
            sub_.dispose()
    finally:
        appmod.db.set_settings({"providers": []})


def test_model_select_failure_keeps_the_routers_choice(monkeypatch: Any) -> None:
    def bad(v: Any) -> None:
        raise RuntimeError("boom")
    seen = select_turn(monkeypatch, bad)
    assert seen[0]["model"] == "chat-model"


# ---- model.request and model.response ----
def test_complete_fires_request_then_response_and_usage_is_a_subscriber(monkeypatch: Any) -> None:
    bus = HookBus()
    monkeypatch.setattr(llm, "bus", bus)
    log: list[tuple[str, Any]] = []
    sub(bus, "model.request", lambda p: log.append(("request", p)))
    sub(bus, "model.response", lambda p: log.append(("response", p)))

    async def fake_send(*a: Any, **k: Any) -> Any:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}), 0, 0
    monkeypatch.setattr(llm, "_send_with_retry", fake_send)
    assert asyncio.run(llm.complete({}, "m", [{"role": "user", "content": "hello"}], "helper")) == "hi"
    assert [k for k, _ in log] == ["request", "response"]
    assert log[0][1] == {"model": "m", "message_count": 1, "tool_names": [], "tokens_est": log[0][1]["tokens_est"]}
    assert log[1][1]["model"] == "m" and log[1][1]["kind"] == "helper" and log[1][1]["prompt_tokens"] == 5


def test_model_request_lists_the_offered_tools(monkeypatch: Any) -> None:
    bus = HookBus()
    monkeypatch.setattr(llm, "bus", bus)
    got: list[Any] = []
    sub(bus, "model.request", got.append)
    llm._emit_request("m", [{"role": "user", "content": "x"}], [{"function": {"name": "a"}}, {"function": {"name": "b"}}])
    assert got[0]["tool_names"] == ["a", "b"] and got[0]["message_count"] == 1


def test_the_usage_row_is_written_by_the_model_response_subscriber() -> None:
    assert llm.bus is appmod.host.bus
    llm._emit_usage("post-turn-m", "chat", {"prompt_tokens": 11, "completion_tokens": 3}, 5, 0, 0)
    with appmod.db.tx() as c:
        row = c.execute("SELECT prompt_tokens, completion_tokens FROM usage_log WHERE model='post-turn-m'").fetchone()
    assert (row["prompt_tokens"], row["completion_tokens"]) == (11, 3)


# ---- subagent.start / stop ----
def test_a_subagent_fires_start_then_stop(monkeypatch: Any) -> None:
    log: list[tuple[str, Any]] = []
    handles = [sub(appmod.host.bus, "subagent.start", lambda p: log.append(("start", p))),
               sub(appmod.host.bus, "subagent.stop", lambda p: log.append(("stop", p)))]

    async def child(settings: Any, model: str, messages: Any, tools: Any = None, **kw: Any) -> Any:
        yield {"type": "delta", "text": "done"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}
    monkeypatch.setattr(llm, "stream_chat", child)
    cid = appmod.convos.create(None, "t", "m")["id"]
    ctx = {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": False, "taint_sources": [],
           "allowed_urls": set(), "settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None),
           "depth": 0, "agent_run_id": "", "model": "m", "stop": asyncio.Event(), "meter": appmod.RunMeter(), "run": None}
    try:
        out = asyncio.run(appmod.toolbox.call("agent_spawn", {"task": "look", "role": "researcher"}, ctx))
    finally:
        for h in handles:
            h.dispose()
    assert out["state"] == "completed"
    assert [k for k, _ in log] == ["start", "stop"]
    assert log[0][1] == log[1][1] and log[0][1]["role"] == "researcher" and log[0][1]["child_id"].startswith("sa_") and log[0][1]["parent"] == ""


# ---- approval.requested / resolved ----
def test_approvals_fire_once_each_and_never_decide(tmp_path: Path) -> None:
    hooks = HookBus()
    store = RunStore(Database(tmp_path), hooks)
    log: list[tuple[str, Any]] = []
    sub(hooks, "approval.requested", lambda p: log.append(("requested", p)))
    sub(hooks, "approval.resolved", lambda p: log.append(("resolved", p)))
    row = store.open_approval("call1", None, "write_local_file", {"path": "/x"})
    store.open_approval("call1", None, "write_local_file", {"path": "/x"})
    assert log == [("requested", {"call_id": "call1", "tool": "write_local_file", "args_digest": row["args_digest"], "run_id": None})]
    assert store.decide("call1", "approve", by="user")["status"] == "approved"
    assert store.decide("call1", "deny", by="telegram") is None, "first decision wins"
    assert log[1:] == [("resolved", {"call_id": "call1", "decision": "approve", "by": "user"})]


def test_a_handler_cannot_change_an_approval(tmp_path: Path) -> None:
    hooks = HookBus()
    store = RunStore(Database(tmp_path), hooks)
    sub(hooks, "approval.resolved", lambda p: store.decide(p["call_id"], "deny", by="hook"))
    store.open_approval("call2", None, "write_local_file", {"path": "/x"})
    assert store.decide("call2", "approve")["status"] == "approved"
    assert store.approval("call2")["status"] == "approved" and store.approval("call2")["decision"] == "approve"


# ---- job.fired / job.done ----
async def _broken(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    raise RuntimeError("model down")
    yield  # pragma: no cover - makes this an async generator


def fire_job(monkeypatch: Any, stream: Any) -> tuple[list[tuple[str, Any]], dict[str, Any], str | None]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
    monkeypatch.setattr(llm, "stream_chat", stream)
    log: list[tuple[str, Any]] = []
    handles = [sub(appmod.host.bus, "job.fired", lambda p: log.append(("fired", p))),
               sub(appmod.host.bus, "job.done", lambda p: log.append(("done", p)))]
    job = appmod.jobs.create(f"hooked {time.time()}", "0 9 * * *", "report", timezone="UTC", enabled=False, at=1772409600.0)
    fire = {"job_id": job["id"], "job": job["name"], "kind": job["kind"], "cron": job["cron"], "timezone": "UTC", "due_at": 1772409600.0,
            "fired_at": 1772409600.0, "late_seconds": 0.0, "missed_slots": 0, "late": False}

    async def go() -> str | None:
        appmod.host._loop = asyncio.get_running_loop()
        try:
            run_id = await appmod._launch_job(job, fire)
            run = next(r for r in appmod.bus._runs.values() if r.run_id == run_id)
            await run.task
            await asyncio.sleep(0.1)
            return run_id
        finally:
            appmod.host._loop = None
    try:
        run_id = asyncio.run(go())
    finally:
        for h in handles:
            h.dispose()
    return log, job, run_id


def test_a_fired_job_reports_kind_and_trigger_and_done_reports_the_outcome(monkeypatch: Any) -> None:
    log, job, run_id = fire_job(monkeypatch, _fake)
    assert log[0] == ("fired", {"job_id": job["id"], "kind": job["kind"], "trigger": "clock"})
    assert log[1] == ("done", {"job_id": job["id"], "run_id": run_id, "outcome": "done"})


def test_a_job_that_fails_reports_fired_first_and_done_with_its_outcome(monkeypatch: Any) -> None:
    log, job, run_id = fire_job(monkeypatch, _broken)
    assert [name for name, _ in log] == ["fired", "done"]
    assert log[1][1]["run_id"] == run_id and log[1][1]["outcome"] != "done"


def test_a_manual_or_triggered_fire_names_its_trigger() -> None:
    got: list[Any] = []
    handle = sub(appmod.host.bus, "job.fired", got.append)
    try:
        asyncio.run(appmod._job_fired({"id": "j", "kind": "mail"}, {"manual": True}))
        asyncio.run(appmod._job_fired({"id": "j", "kind": "mail"}, {"trigger": "mail"}))
    finally:
        handle.dispose()
    assert [g["trigger"] for g in got] == ["manual", "mail"]


# ---- memory.* ----
@pytest.fixture
def mem(tmp_path: Path) -> tuple[Memories, HookBus]:
    hooks = HookBus()
    return Memories(Database(tmp_path), hooks), hooks


def test_before_write_can_rewrite_and_written_reports_the_row(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    written: list[Any] = []
    sub(hooks, "memory.before_write", lambda p: {**p, "content": p["content"].replace("secret", "[x]")})
    sub(hooks, "memory.written", written.append)
    m = memories.create(None, "the secret is blue", kind="fact", source="user")
    assert m["content"] == "the [x] is blue" and written[0]["memory"]["id"] == m["id"] and written[0]["source"] == "user"


def test_before_write_payload_names_content_kind_source_and_project(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    seen: list[Any] = []
    sub(hooks, "memory.before_write", seen.append)
    memories.create(None, "a fact", kind="note", source="auto")
    assert seen == [{"content": "a fact", "kind": "note", "source": "auto", "project_id": None}]


def test_a_denied_write_stores_nothing_and_fires_no_written(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    written: list[Any] = []
    deny = sub(hooks, "memory.before_write", lambda p: Deny("no pii") if "ssn" in p["content"] else None)
    sub(hooks, "memory.written", written.append)
    with pytest.raises(MemoryRefused, match="no pii"):
        memories.create(None, "my ssn is 1", kind="fact", source="user")
    assert memories.list(None) == [] and written == []
    ok = memories.create(None, "fine", kind="fact", source="user")
    with pytest.raises(MemoryRefused):
        memories.supersede(ok["id"], "now my ssn is 2")
    with pytest.raises(MemoryRefused):
        memories.update(ok["id"], {"content": "ssn again"})
    assert memories.get(ok["id"])["content"] == "fine" and len(written) == 1
    deny.dispose()
    assert memories.update(ok["id"], {"content": "ssn again"})["content"] == "ssn again"


def test_supersede_and_update_fire_written_for_the_new_row(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    written: list[Any] = []
    sub(hooks, "memory.written", lambda p: written.append(p["memory"]["content"]))
    a = memories.create(None, "one", kind="fact", source="user")
    b = memories.supersede(a["id"], "two", source="auto")
    memories.update(b["id"], {"pinned": True})
    assert written == ["one", "two", "two"]


def test_a_duplicate_create_is_not_a_new_write(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    written: list[Any] = []
    sub(hooks, "memory.written", written.append)
    memories.create(None, "same", kind="fact", source="user")
    memories.create(None, "SAME", kind="fact", source="user")
    assert len(written) == 1


def test_deleted_fires_for_a_delete_and_a_forget(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    gone: list[Any] = []
    sub(hooks, "memory.deleted", lambda p: gone.append(p["memory"]["content"]))
    a = memories.create(None, "alpha", kind="fact", source="user")
    b = memories.create(None, "beta", kind="fact", source="user")
    memories.invalidate(a["id"])
    memories.delete(b["id"])
    memories.delete("missing")
    assert gone == ["alpha", "beta"]


def test_a_handler_cannot_change_the_row_the_caller_holds(mem: tuple[Memories, HookBus]) -> None:
    memories, hooks = mem
    sub(hooks, "memory.written", lambda p: p["memory"].update(content="tampered"))
    assert memories.create(None, "intact", kind="fact", source="user")["content"] == "intact"


def test_the_memory_route_answers_403_when_a_handler_refuses(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    handle = sub(appmod.host.bus, "memory.before_write", lambda p: Deny("policy"))
    try:
        with TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN}) as client:
            r = client.post("/memories", json={"content": "something to keep", "kind": "fact"})
    finally:
        handle.dispose()
    assert r.status_code == 403 and "policy" in r.json()["detail"]


# ---- compaction.before / after ----
def compaction_fixture(tmp_path: Path) -> tuple[compaction.Compactor, Conversations, str]:
    db = Database(tmp_path)
    hooks = HookBus()
    convos = Conversations(db)
    cid = convos.create(None, "t", "m")["id"]
    for i in range(12):
        convos.add_message(cid, "user", f"question {i}")
        am = convos.add_message(cid, "assistant", "", model="m")
        convos.finish_message(am["id"], f"answer {i}", None, None, None)
    return compaction.Compactor(db, hooks), convos, cid



def test_compaction_before_can_supply_the_summary_and_after_reports_it(tmp_path: Path) -> None:
    compactor, convos, cid = compaction_fixture(tmp_path)
    after: list[Any] = []
    payloads: list[Any] = []

    def supply(p: dict[str, Any]) -> dict[str, Any]:
        payloads.append(p)
        return {**p, "summary": "  my own summary  "}
    sub(compactor.hooks, "compaction.before", supply)
    sub(compactor.hooks, "compaction.after", after.append)

    async def never(*a: Any, **k: Any) -> str:
        raise AssertionError("the built-in summarizer must not run")
    res = asyncio.run(compactor.compact({"compactKeepRecent": 4}, "m", cid, convos.history_rows(cid), complete=never))
    assert res["compacted"] and compactor.get(cid)["summary"] == "my own summary"
    assert after == [{"summary": "my own summary"}]
    assert set(payloads[0]) == {"messages", "window", "summary"} and payloads[0]["summary"] is None
    assert payloads[0]["messages"][0] == {"role": "user", "content": "question 0"} and payloads[0]["window"] > 0


def test_compaction_falls_back_to_the_built_in_summarizer(tmp_path: Path) -> None:
    compactor, convos, cid = compaction_fixture(tmp_path)
    after: list[Any] = []

    def bad(p: Any) -> None:
        raise RuntimeError("boom")
    sub(compactor.hooks, "compaction.before", bad)
    sub(compactor.hooks, "compaction.before", lambda p: {**p, "summary": ""}, priority=60)
    sub(compactor.hooks, "compaction.after", after.append)

    async def built_in(cfg: Any, model: str, messages: Any, kind: str = "compact") -> str:
        return "built-in summary"
    asyncio.run(compactor.compact({"compactKeepRecent": 4}, "m", cid, convos.history_rows(cid), complete=built_in))
    assert compactor.get(cid)["summary"] == "built-in summary" and after == [{"summary": "built-in summary"}]


def test_compaction_without_subscribers_is_unchanged(tmp_path: Path) -> None:
    compactor, convos, cid = compaction_fixture(tmp_path)

    async def built_in(cfg: Any, model: str, messages: Any, kind: str = "compact") -> str:
        return "S"
    res = asyncio.run(compactor.compact({"compactKeepRecent": 4}, "m", cid, convos.history_rows(cid), complete=built_in))
    assert res["compacted"] and compactor.get(cid)["summary"] == "S"
