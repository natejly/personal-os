"""The default chat loop, pinned: for scripted-model chats (a tool round with a refused call and an approval card, a denied
card, a Stop, the repeat breaker, an unknown tool) the stable prefix, the offered tool names and their order, the model's
messages, the streaming event tape, the hook emissions and the executed_calls rows must equal what is stored in
golden/chat_loop.json. A change to the loop's behaviour shows up here as a diff; replace the golden only when it is intended.

Run: PYTHONPATH=<repo>/backend python -m pytest backend/tests/test_chat_loop_golden.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="chatloop-golden-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import limits, llm  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
GOLDEN = Path(__file__).parent / "golden" / "chat_loop.json"
ROUNDS: list[Any] = []
MODEL_CALLS: list[dict[str, Any]] = []
HOOKS: list[list[Any]] = []
NAMES = ("g_read", "g_write", "g_off", "g_hang")
VOLATILE = {"duration_ms", "created_at", "start", "end", "ttft_ms", "ms", "at", "auto_at", "finished_at", "elapsed", "date", "started_at"}
IDS = re.compile(r"[0-9a-f]{12,}")
TODAY = re.compile(r"## Today\n[^\n]*")  # the date block changes every day


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    MODEL_CALLS.append({"tools": [t["function"]["name"] for t in tools or []], "tool_choice": tool_choice, "messages": list(messages)})
    step = ROUNDS.pop(0) if ROUNDS else ["ok"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _read(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "read": 1}


async def _write(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "wrote": 1}


async def _hang(ctx: dict[str, Any]) -> dict[str, Any]:
    await asyncio.Event().wait()
    return {"ok": True}


def _spy(bus: Any) -> None:
    for kind in ("emit", "emit_sync", "collect", "pipe", "pipe_sync", "gate"):
        real = getattr(bus, kind)

        def make(kind: str, real: Callable[..., Any]) -> Callable[..., Any]:
            if asyncio.iscoroutinefunction(real):
                async def wrapped(hook: str, *a: Any, **kw: Any) -> Any:
                    HOOKS.append([kind, hook, digest(a[0] if a else None)])
                    return await real(hook, *a, **kw)
            else:
                def wrapped(hook: str, *a: Any, **kw: Any) -> Any:  # type: ignore[misc]
                    HOOKS.append([kind, hook, digest(a[0] if a else None)])
                    return real(hook, *a, **kw)
            return wrapped
        setattr(bus, kind, make(kind, real))


def norm(v: Any) -> Any:
    """Ids, clocks and durations out: what is left is the loop's behaviour."""
    if isinstance(v, dict):
        return {k: norm(x) for k, x in sorted(v.items()) if k not in VOLATILE}
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    if isinstance(v, float):
        return "<float>"
    if isinstance(v, str):
        return IDS.sub("<id>", TODAY.sub("## Today", v))
    return v


def digest(v: Any) -> str:
    if not isinstance(v, (dict, list, str, int, float, type(None))):
        return "<object>"  # turn.settled carries a live object, not data
    return hashlib.sha1(json.dumps(norm(json.loads(json.dumps(v, default=str))), sort_keys=True).encode()).hexdigest()[:12]


def shape(messages: list[dict[str, Any]]) -> list[Any]:
    """The context up to the user's message by digest (count and bytes), what the loop adds after it (tool turns, notes) in full."""
    head = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1) + 1
    return [{"context": [m["role"] for m in messages[:head]], "digest": digest(messages[:head])}, *norm(messages[head:])]


def keys(v: Any, depth: int = 2) -> Any:
    if isinstance(v, dict) and depth:
        return {k: keys(x, depth - 1) for k, x in sorted(v.items())}
    return None


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    obj = {"type": "object", "properties": {}}
    for name, fn, danger in (("g_read", _read, "safe"), ("g_write", _write, "writes"), ("g_off", _write, "writes"), ("g_hang", _hang, "safe")):
        appmod.toolbox.specs[name] = ToolSpec(name, name, obj, fn, "test", danger)
    appmod.db.set_settings({"toolDeferAbove": 0, "permissionMode": "manual", "autoLearn": False, "baseUrl": "", "codingRoute": True,
                                "hiddenViews": ["todos", "calendar", "mail", "health", "spaces", "claude-code", "memory-tree"]})  # what the golden was taken with: Lists, Calendar, Mail, Health, Canvases, Claude Code and History digest off; Coding on
    _spy(appmod.host.bus)
    with client:
        yield
    llm.stream_chat = real
    for n in NAMES:
        appmod.toolbox.specs.pop(n, None)


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    MODEL_CALLS.clear()
    HOOKS.clear()
    yield
    ROUNDS.clear()


def j(method: str, path: str, body: Any = None) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == 200, f"{method} {path} -> {r.status_code} {r.text[:300]}"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def drain(rid: str) -> None:
    wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval"), f"run {rid} to finish")
    time.sleep(0.2)  # the settle hooks run after the run is marked done


def call(name: str, cid: str) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps({})}


def record(rid: str) -> dict[str, Any]:
    tape = [{"event": e, "keys": keys(d), "data": norm(d) if e in ("tool_call", "tool_result", "tool_decision", "taint", "status", "plan") else None}
            for _, e, d in store.events(rid)]
    rows = [{k: norm(r.get(k)) for k in ("tool", "status", "args_digest", "attempts", "result")} for r in store.executed(rid)]
    prefix = hashlib.sha1(MODEL_CALLS[0]["messages"][0]["content"].encode()).hexdigest()
    return {"model_calls": [{"tools": c["tools"], "tool_choice": c["tool_choice"], "messages": shape(c["messages"])} for c in MODEL_CALLS],
            "prefix_sha1": prefix, "events": tape, "executed_calls": rows, "hooks": HOOKS[:]}


def scenario(rounds: list[Any], tools: dict[str, str], steps: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    ROUNDS.extend(rounds)
    cid = j("POST", "/conversations", {})["id"]
    j("PATCH", f"/conversations/{cid}", {"settings": {"tools": tools}})
    rid = j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]
    if steps:
        steps(cid, rid)
    drain(rid)
    return record(rid)


def answer(decision: str) -> Callable[[str, str], None]:
    def go(cid: str, rid: str) -> None:
        row = wait_until(lambda: store.approvals("pending", run_id=rid), "the approval card")[0]
        j("POST", f"/approvals/{row['call_id']}", {"decision": decision})
    return go


def allow_all_cards(cid: str, rid: str) -> None:
    """The doom-loop card a repeated call raises, answered as often as it comes."""
    while (r := store.get(rid)) and r["status"] in ("running", "awaiting_approval"):
        for row in store.approvals("pending", run_id=rid):
            j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
        time.sleep(0.02)


def stop(cid: str, rid: str) -> None:
    wait_until(lambda: any(e == "tool_call" for _, e, _ in store.events(rid)), "the tool_call event")
    time.sleep(0.1)
    j("POST", f"/conversations/{cid}/stop")


TOOLS = {"g_read": "on", "g_write": "ask", "g_off": "off"}


def test_chat_loop_matches_its_golden() -> None:
    got = {
        "round_card_allowed": scenario([{"tool_calls": [call("g_read", "a"), call("g_off", "b"), call("g_write", "c")]}, ["final ", "answer"]],
                                       TOOLS, answer("allow")),
        "round_card_denied": scenario([{"tool_calls": [call("g_write", "a"), call("g_read", "b")]}, ["declined"]], TOOLS, answer("deny")),
        "stopped_in_a_tool": scenario([{"tool_calls": [call("g_hang", "a"), call("g_write", "b")]}], {"g_hang": "on", "g_write": "on"}, stop),
        "repeat_breaker": scenario([{"tool_calls": [call("g_read", f"r{i}")]} for i in range(limits.REPEAT_LIMIT)] + [["enough"]], TOOLS, allow_all_cards),
        "unknown_tool": scenario([{"tool_calls": [call("no_such_tool", "a")]}, ["recovered"]], TOOLS),
    }
    got = json.loads(json.dumps(got, sort_keys=True))
    if not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    assert got == json.loads(GOLDEN.read_text())
