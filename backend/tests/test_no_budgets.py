"""Nothing caps a reply, a job, a subagent or a desk turn: only stuck detection, idle detection and per-request
timeouts stop work. Also: max_tokens goes out only where the provider requires it, context blocks are shares of the
model's window, and the stored budget settings are dropped.

Run: uv run --project backend --with pytest pytest backend/tests/test_no_budgets.py
Scripted llm.stream_chat throughout; the permission mode is pinned to manual (Auto waits on a reviewer).
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="nobudgets-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import job_history, limits, llm, migrations  # noqa: E402
from personal_os import subagents as sa  # noqa: E402
from personal_os.context import build_context, estimate_tokens  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents, Graph, Memories  # noqa: E402
from personal_os.usage import Pricing  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
REAL_STREAM = llm.stream_chat  # the autouse fixture swaps llm.stream_chat for the scripted one
store = appmod.run_store
CALLS = 150          # far past the old 100-round hard cap
CHILD_CALLS = 60     # past the old 12-round child cap
PROMPT, COMPLETION = 3000, 100   # per round: 150 rounds is 465k tokens, past the old 200k per-reply cap
CLOCK_STEP = 5.0     # seconds each fake round adds to time.monotonic: 150 rounds is 750 s, past the old 300 s cap

STATE: dict[str, Any] = {"n": 0, "child_n": 0, "hang": False, "tokens": 0, "offset": 0.0, "calls": CALLS, "tool": "search_memory",
                         "same": False, "seen": []}


def call(cid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                cancel: asyncio.Event | None = None) -> Any:
    """Every round asks for one more distinct call, STATE['calls'] times, then answers in text."""
    if STATE["hang"]:
        await asyncio.sleep(3600)
    is_child = messages[0]["role"] == "system" and "You are a subagent" in messages[0]["content"]
    key, total = ("child_n", CHILD_CALLS) if is_child else ("n", STATE["calls"])
    i = STATE[key]
    STATE["seen"] = [dict(m) for m in messages]
    STATE["offset"] += CLOCK_STEP
    STATE["tokens"] += PROMPT + COMPLETION
    usage = {"prompt_tokens": PROMPT, "completion_tokens": COMPLETION}
    if tool_choice == "none" or i >= total:
        yield {"type": "delta", "text": "finished"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": usage}
        return
    STATE[key] = i + 1
    c = call(f"c{i}", STATE["tool"], {"query": "same" if STATE["same"] else f"distinct {i}"})
    yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": [c], "usage": usage}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "delegationForce": False})  # forced delegation would refuse the scripted tool calls after round 2
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    STATE.update(n=0, child_n=0, hang=False, tokens=0, offset=0.0, calls=CALLS, tool="search_memory", same=False, seen=[])
    monkeypatch.setattr(llm, "stream_chat", _fake)
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + STATE["offset"])
    appmod.db.set_settings({"permissionMode": "manual", "workspaceRoots": [],
                            "permissionRules": {"allow": [], "ask": [], "deny": []}})
    yield


# ---------------- the main reply loop ----------------
def run_chat(content: str = "go") -> list[tuple[str, Any]]:
    cid = appmod.convos.create(None, "t", "m")["id"]

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content=content), asyncio.Event())]

    return asyncio.run(go())


def final_done(events: list[tuple[str, Any]]) -> dict[str, Any]:
    return [d for e, d in events if e == "done" and not d.get("segment")][-1]


def test_a_long_reply_is_never_cut_by_rounds_tokens_or_time() -> None:
    events = run_chat()
    done = final_done(events)
    results = [d for e, d in events if e == "tool_result"]
    assert len(results) == CALLS, f"all {CALLS} distinct calls ran, got {len(results)}"
    assert done["partial"] is None and done["error"] is None and done["outcome"] is None
    assert STATE["tokens"] > 300_000, "past the old token cap"
    assert STATE["offset"] > 600, "past the old wall-clock cap"
    assert not [r for r in results if r.get("breaker")], "no breaker fired on distinct calls"


def test_repeated_refusals_get_the_hard_stop_note() -> None:
    appmod.db.set_settings({"permissionRules": {"allow": [], "ask": [], "deny": ["search_memory"]}})
    STATE["calls"] = 6
    events = run_chat()
    assert "Three calls in a row were refused" in json.dumps(STATE["seen"]), "after three refusals the result tells the model to stop varying them"
    assert final_done(events)["error"] is None


# ---------------- unattended jobs ----------------
def new_job(name: str) -> str:
    made = client.post("/jobs", json={"name": f"{name} {time.time()}", "cron": "0 6 * * *", "prompt": "report", "timezone": "UTC"})
    assert made.status_code == 200, made.text
    return made.json()["id"]


def wait_run(run_id: str, timeout: float = 120.0) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        row = store.get(run_id)
        if row and row["status"] not in ("running", "awaiting_approval"):
            return row
        time.sleep(0.05)
    raise AssertionError("the run did not end")


def test_identical_calls_are_still_stopped_in_a_job() -> None:
    """Nobody can answer the card the third identical call raises, so it is refused; the fifth ends tool use."""
    STATE.update(same=True, calls=50)
    row = wait_run(client.post(f"/jobs/{new_job('same')}/run").json()["run_id"])
    assert row["status"] == "done", row
    msg = client.get(f"/conversations/{row['conversation_id']}").json()["messages"][-1]
    assert msg["outcome"] == "loop", msg["outcome"]
    assert STATE["n"] <= limits.REPEAT_LIMIT + 1, f"ended at the repeat limit, ran {STATE['n']} rounds"


def test_a_long_job_run_completes() -> None:
    run_id = client.post(f"/jobs/{new_job('long')}/run").json()["run_id"]
    row = wait_run(run_id)
    assert row["status"] == "done" and not row.get("error"), row
    assert row["budget"]["tokens"] > 300_000 and row["budget"]["rounds"] >= CALLS
    assert not [k for k in row["budget"] if k.startswith("max_")], "the snapshot is usage only"
    msg = client.get(f"/conversations/{row['conversation_id']}").json()["messages"][-1]
    assert msg["outcome"] is None and msg["content"].strip() == "finished"


def test_the_idle_watchdog_stops_a_hung_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appmod, "JOB_IDLE_SECONDS", 0.4)
    STATE["hang"] = True
    t0 = time.time()
    row = wait_run(client.post(f"/jobs/{new_job('hang')}/run").json()["run_id"], timeout=30)
    assert time.time() - t0 < 20
    assert row["status"] == "error" and "no model or tool activity" in (row.get("error") or ""), row
    assert job_history.summarize_run(row)["status"] == "timed_out", "the history reads the idle stop as a timeout"


def test_old_wall_clock_rows_still_read_as_timed_out() -> None:
    legacy = {"run_id": "r", "started_at": 1.0, "ended_at": 241.0, "status": "done",
              "budget": {"max_seconds": 240, "seconds": 240.0}, "error": None}
    assert job_history.summarize_run(legacy)["status"] == "timed_out"


# ---------------- subagents ----------------
class FakeRun:
    live = True
    kind = "chat"
    run_id = "fake"

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, event: str, data: Any) -> None:
        self.events.append((event, data))

    def set_status(self, status: str) -> None:
        pass


def child_ctx() -> dict[str, Any]:
    cid = appmod.convos.create(None, "Test chat", "test-model")["id"]
    cfg = appmod.settings()
    return {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": False, "taint_sources": [],
            "allowed_urls": set(), "settings": cfg, "modes": appmod.toolbox.effective({}, None, None), "depth": 0,
            "agent_run_id": "", "model": "test-model", "stop": asyncio.Event(), "meter": appmod.RunMeter(), "run": FakeRun()}


def spawn(task: str, ctx: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(appmod.toolbox.call("agent_spawn", {"task": task}, ctx))


def test_a_child_with_many_distinct_calls_completes() -> None:
    ctx = child_ctx()
    out = spawn("a long errand", ctx)
    assert out["state"] == "completed" and out["exit_reason"] == "completed", out
    assert out["rounds"] == CHILD_CALLS + 1
    assert ctx["meter"].tokens > 0, "its usage still rolls up to the parent for display"


def test_a_child_that_repeats_one_call_is_stopped() -> None:
    STATE.update(same=True)
    ctx = child_ctx()
    ctx["proposal_only"] = True   # the third identical call raises a card; nobody can answer it
    out = spawn("loop forever", ctx)
    assert out["state"] == "partial" and out["exit_reason"] == "stuck", out
    assert STATE["child_n"] == limits.REPEAT_LIMIT


def test_a_child_told_no_over_and_over_is_warned() -> None:
    appmod.db.set_settings({"permissionRules": {"allow": [], "ask": [], "deny": ["search_memory"]}})
    ctx = child_ctx()
    ctx["settings"] = appmod.settings()
    out = spawn("keep asking", ctx)
    assert out["state"] == "completed"
    assert "Three calls in a row were refused" in json.dumps(STATE["seen"]), "the 4th result carried the stop-varying note"


def test_an_agent_definition_with_steps_is_accepted_and_the_field_ignored() -> None:
    f = sa.parse_def("---\nname: tidy-up\ndescription: x\nsteps: 999\n---\nDo it.")
    assert f["name"] == "tidy-up" and "steps" not in f


# ---------------- desks ----------------
def test_a_desk_has_no_turn_cap_only_the_single_nudge() -> None:
    from personal_os.runs import Run

    run = Run("unused", input={"content": "go"})
    run.partial = None
    try:
        row = {"status": "working", "turn": 500, "cost": 1e6, "plan_id": None, "budget": {"maxTurns": 1}}
        assert appmod._chain_kind(row, run) == "nudge"
        run.input["content"] = appmod.DESK_NUDGE
        assert appmod._chain_kind(row, run) is None, "a nudged turn that just ends settles"
    finally:
        run.end()


# ---------------- max_tokens ----------------
def capture(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if bodies[-1].get("stream"):
            sse = 'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        return httpx.Response(200, json=reply or {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    return bodies


async def stream_once(settings: dict[str, Any]) -> None:
    async for _ in REAL_STREAM(settings, "m", [{"role": "user", "content": "x"}]):
        pass


@pytest.mark.parametrize("base, caps, expect", [
    ("http://localhost:4000", {"max_output_tokens": 64000}, None),            # a proxy: never sent, even when known
    ("https://api.openai.com/v1", {"max_output_tokens": 64000}, None),
    ("https://api.anthropic.com/v1/", {"max_output_tokens": 64000}, 64000),   # required there, and the model's own maximum
    ("https://api.anthropic.com/v1/", {}, None),                              # unknown maximum: nothing of ours is invented
])
def test_max_tokens_only_where_the_provider_requires_it(monkeypatch: pytest.MonkeyPatch, base: str, caps: dict[str, Any], expect: int | None) -> None:
    monkeypatch.setattr(llm, "caps_lookup", lambda m: caps)
    bodies = capture(monkeypatch)
    cfg = {"baseUrl": base, "apiKey": "k"}
    asyncio.run(stream_once(cfg))
    asyncio.run(llm.complete(cfg, "m", [{"role": "user", "content": "x"}]))
    assert len(bodies) == 2
    for b in bodies:
        assert b.get("max_tokens") == expect, (base, b)
        assert "max_completion_tokens" not in b


def test_the_pricing_cache_learns_output_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    real = httpx.AsyncClient
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-x", "max_input_tokens": 200000, "max_tokens": 64000}]})
        return httpx.Response(200, json={"data": [{"model_name": "p", "model_info": {"max_input_tokens": 1000, "max_output_tokens": 500}}]})

    from personal_os import usage as usage_mod
    monkeypatch.setattr(usage_mod.httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    a = Pricing()
    asyncio.run(a.refresh({"baseUrl": "https://api.anthropic.com/v1", "apiKey": "k"}, force=True))
    assert a.caps("claude-x") == {"max_input_tokens": 200000, "max_output_tokens": 64000}
    b = Pricing()
    asyncio.run(b.refresh({"baseUrl": "http://localhost:4000", "apiKey": "k"}, force=True))
    assert b.caps("p")["max_output_tokens"] == 500 and any(u.endswith("/model/info") for u in seen)


# ---------------- context shares ----------------
def test_context_shares_scale_with_the_window() -> None:
    small, big = limits.context_shares(8_000), limits.context_shares(1_000_000)
    assert all(small[k] < big[k] for k in limits.CONTEXT_SHARES)
    assert big["memories"] == int(1_000_000 * limits.CONTEXT_SHARES["memories"])


def test_injected_memory_and_profile_follow_the_window() -> None:
    d = Path(tempfile.mkdtemp(prefix="nobudgets-ctx-"))
    db = Database(d)
    memories, graph = Memories(db), Graph(db)
    for i in range(40):
        memories.create(None, f"Preference {i} " + "x" * 200, kind="preference")
    hits = [{"id": str(i), "content": f"note {i} " + "y" * 200, "project_id": None} for i in range(200)]

    def build(window: int) -> dict[str, Any]:
        return build_context(memories=memories, graph=graph, documents=Documents(db), project=None, project_id=None, query="alpha",
                             settings={}, conv_settings={}, global_system_prompt="", memory_hits=hits, window=window)[1]

    small, large = build(8_000), build(1_000_000)
    assert len(small["memories"]) < len(large["memories"]) == 200
    assert len(small["profile"]) < len(large["profile"]) == 40
    assert small["trimmed"]["memories"] > 0 and "memories" not in large["trimmed"]
    block = "## What you remember about the user" + small["volatile_blocks"][0].split("## What you remember about the user")[1]
    assert estimate_tokens(block) <= limits.context_shares(8_000)["memories"] + 40


# ---------------- settings ----------------
OLD_KEYS = ("maxToolRounds", "maxRunTokens", "maxRunSeconds", "subagentMaxRounds", "deskMaxTurns",
            "codingSessionTimeoutMinutes", "contextBudget", "skillsInlineBudget", "usageAlerts")


def test_migration_drops_the_budget_settings() -> None:
    d = Path(tempfile.mkdtemp(prefix="nobudgets-mig-"))
    db = Database(d)
    db.set_settings({**{k: 5 for k in OLD_KEYS}, "uiZoom": 110})
    path = next(d.glob("*.db"))
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 19")
    con.commit()
    assert migrations.run(con) == list(range(20, migrations.latest() + 1))
    left = {r[0] for r in con.execute("SELECT key FROM settings")}
    con.close()
    assert not left & set(OLD_KEYS) and "uiZoom" in left
    assert [n for v, n, _ in migrations.MIGRATIONS if v == 20] == ["drop_budget_settings"]


def test_put_settings_accepts_old_budget_keys_and_stores_nothing() -> None:
    r = client.put("/settings", json={**{k: 5 for k in OLD_KEYS}, "uiZoom": 105})
    assert r.status_code == 200 and r.json()["uiZoom"] == 105
    stored = appmod.db.get_settings()
    assert not set(OLD_KEYS) & set(stored)
    client.put("/settings", json={"uiZoom": 100})
