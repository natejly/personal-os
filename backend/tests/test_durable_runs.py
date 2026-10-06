"""Durable runs: a run is a row, its stream a tail on run_events, approvals are rows, side effects are journaled.

Run: uv run --project backend --with pytest pytest backend/tests/test_durable_runs.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="durabletest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.runs import RING, Run, args_digest, idempotency_key  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store

# Each entry is one LLM round: a list of text chunks, or {"tool_calls": [...]}. Past the end, the model answers "ok".
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
    """One portal for the module, so requests share the event loop the run tasks live on."""
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted  # another module may have swapped in its own stub at import time
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


def events(text: str) -> list[tuple[int | None, str, Any]]:
    out = []
    for block in text.split("\n\n"):
        seq, event, data = None, "", ""
        for line in block.split("\n"):
            if line.startswith("id:"):
                seq = int(line[3:].strip())
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event and data:
            out.append((seq, event, json.loads(data)))
    return out


def stream(path: str, timeout: float = 20.0) -> str:
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(client.get(path).text), daemon=True)
    t.start()
    t.join(timeout)
    assert out, f"{path} did not finish within {timeout}s"
    return out[0]


def start(conv_settings: dict[str, Any] | None = None, content: str = "hi") -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    if conv_settings:
        j("PATCH", f"/conversations/{cid}", {"settings": conv_settings})
    return cid, j("POST", f"/conversations/{cid}/chat", {"content": content})["run_id"]


def drain(run_id: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(run_id)) and r["status"] not in ("running", "awaiting_approval") and r,
                      f"run {run_id} to finish")


def call(name: str, args: dict[str, Any], cid: str = "call_0") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


# ---------------- the tape ----------------
def test_events_are_persisted_and_tailable_with_since() -> None:
    ROUNDS.append(["Hello", " ", "durable", " ", "world"])
    cid, rid = start()
    row = drain(rid)
    assert row["status"] == "done" and row["conversation_id"] == cid and row["ended_at"]
    assert row["message_id"], "the assistant message id is on the row"
    # What was asked is on the row. Matched by field, not whole-dict: the chat body grows new
    # optional fields (page_context, ...) and that is not a change to what a run records.
    assert row["input"]["content"] == "hi" and row["input"]["model"] is None, row["input"]
    tape = store.events(rid)
    assert [s for s, _, _ in tape] == list(range(1, len(tape) + 1)), "seq is dense from 1"
    assert row["last_seq"] == len(tape)
    assert tape[-1][1] == "done"
    assert "".join(d["text"] for _, e, d in tape if e == "delta").strip() == "Hello durable world"

    live = events(stream(f"/conversations/{cid}/stream?since=0"))
    assert [(s, e) for s, e, _ in live] == [(s, e) for s, e, _ in tape], "the in-memory replay and the table agree, seq for seq"

    # Forget the run in memory, as a restart would: the stream is now served from run_events alone.
    appmod.bus._runs.pop(cid)  # noqa: SLF001
    k = 3
    tail = events(stream(f"/conversations/{cid}/stream?since={k}"))
    assert [s for s, _, _ in tail] == list(range(k + 1, len(tape) + 1)), "since=k replays exactly what came after k"
    assert tail == [(s, e, d) for s, e, d in tape if s > k]
    pinned = events(stream(f"/conversations/{cid}/stream?since=0&run_id={rid}"))
    assert len(pinned) == len(tape)
    assert stream(f"/conversations/{cid}/stream?since=0&run_id=nope") == "", "an unknown run_id streams nothing"

    assert all(r["run_id"] != rid for r in j("GET", "/runs")), "a finished run is not in the active list"
    hist = j("GET", f"/runs?status=all&conversation_id={cid}")
    assert [r["run_id"] for r in hist] == [rid] and hist[0]["status"] == "done" and hist[0]["live"] is False
    assert j("GET", f"/runs?status=done&conversation_id={cid}")[0]["run_id"] == rid
    j("GET", "/runs?status=bogus", expect=400)
    one = j("GET", f"/runs/{rid}")
    assert one["status"] == "done" and one["seq"] == len(tape) and one["approvals"] == [] and one["executed_calls"] == []
    j("GET", "/runs/nope", expect=404)


def test_a_subscriber_older_than_the_ring_fills_the_gap_from_the_table() -> None:
    """A long run outgrows the ring: a late subscriber gets the head from the table, then the ring."""
    cid = j("POST", "/conversations", {})["id"]

    async def go() -> tuple[int, list[int]]:
        run = Run(cid, store)
        for i in range(RING + 50):
            run.publish("delta", {"id": "m", "text": str(i)})
        run.end()
        assert store.get(run.run_id)["status"] == "done"
        return run.seq, [int(block.split("\n", 1)[0][4:]) async for block in run.subscribe(10)]

    seq, seqs = asyncio.run(go())
    assert seqs == list(range(11, seq + 1)), "no gap and no duplicate between the table and the ring"


# ---------------- approvals ----------------
def test_approval_row_lifecycle() -> None:
    title = f"durable approval {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title})]})
    ROUNDS.append(["added"])
    cid, rid = start({"tools": {"todo_add": "ask"}})
    pending = wait_until(lambda: [a for a in j("GET", "/approvals") if a["run_id"] == rid], "the approval row")
    a = pending[0]
    assert a["status"] == "pending" and a["tool"] == "todo_add" and a["args"] == {"title": title} and a["live"] is True
    assert a["args_digest"] == args_digest({"title": title}) and a["conversation_id"] == cid and a["message_id"]
    assert a["decision"] is None and a["decided_at"] is None
    active = [r for r in j("GET", "/runs") if r["run_id"] == rid]
    assert active and active[0]["status"] == "awaiting_approval" and active[0]["live"] is True
    assert store.get(rid)["budget"]["max_rounds"] >= 0, "the budget snapshot is stored with the status change"

    j("POST", f"/approvals/{a['call_id']}", {"decision": "sideways"}, expect=400)
    res = j("POST", f"/approvals/{a['call_id']}", {"decision": "allow"})
    assert res == {"ok": True, "live": True, "resumed": False, "queued": False, "status": "approved"}
    row = drain(rid)
    assert row["status"] == "done"
    decided = store.approval(a["call_id"])
    assert decided["status"] == "approved" and decided["decision"] == "allow" and decided["decided_by"] == "user" and decided["decided_at"]
    assert [t for t in appmod.todos.list("__all__") if t["title"] == title], "the approved call ran"
    j("POST", f"/approvals/{a['call_id']}", {"decision": "deny"}, expect=404)  # first decision wins
    assert j("GET", f"/approvals?status=approved&run_id={rid}")[0]["call_id"] == a["call_id"]


def test_a_pending_approval_does_not_auto_deny() -> None:
    src = (Path(__file__).resolve().parents[1] / "personal_os" / "app.py").read_text()
    assert "waited >= 600" not in src, "the 10-minute auto-deny is gone"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": "never answered"})]})
    cid, rid = start({"tools": {"todo_add": "ask"}})
    a = wait_until(lambda: [a for a in j("GET", "/approvals") if a["run_id"] == rid], "the approval row")[0]
    time.sleep(2.5)  # past one poll of the wait loop
    assert store.approval(a["call_id"])["status"] == "pending" and store.get(rid)["status"] == "awaiting_approval"
    assert j("POST", f"/conversations/{cid}/stop")["ok"] is True
    assert drain(rid)["status"] == "done"
    stopped = store.approval(a["call_id"])
    assert stopped["status"] == "denied" and stopped["decided_by"] == "stop"


# ---------------- idempotency ----------------
def test_idempotent_replay_returns_the_recorded_result_without_calling_again() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run = Run(cid, store)
    calls: list[int] = []

    async def send() -> dict[str, Any]:
        calls.append(1)
        return {"sent": True, "id": f"msg-{len(calls)}"}

    async def go() -> list[tuple[Any, bool]]:
        args = {"to": "a@example.com", "subject": "hi", "body": "x"}
        first = await store.call_once(run.run_id, 1, "gmail_send", args, send, call_id="m:c0")
        again = await store.call_once(run.run_id, 1, "gmail_send", dict(reversed(list(args.items()))), send)  # key order is not data
        other_step = await store.call_once(run.run_id, 2, "gmail_send", args, send)
        return [first, again, other_step]

    (r1, rep1), (r2, rep2), (r3, rep3) = asyncio.run(go())
    assert (r1, rep1) == ({"sent": True, "id": "msg-1"}, False)
    assert (r2, rep2) == ({"sent": True, "id": "msg-1"}, True), "the replay is the recorded result"
    assert rep3 is False and r3["id"] == "msg-2", "a different step is a different key"
    assert len(calls) == 2
    rows = store.executed(run.run_id)
    assert [(r["step"], r["status"], r["attempts"]) for r in rows] == [(1, "done", 1), (2, "done", 1)]
    assert rows[0]["key"] == idempotency_key(run.run_id, 1, "gmail_send", args_digest({"to": "a@example.com", "subject": "hi", "body": "x"}))


def test_idempotency_failed_and_unknown_outcomes() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run = Run(cid, store)
    calls: list[str] = []

    async def failing() -> dict[str, Any]:
        calls.append("fail")
        return {"error": "HTTP 503"}

    async def ok() -> dict[str, Any]:
        calls.append("ok")
        return {"ok": True}

    async def go() -> tuple[Any, Any, Any]:
        a = await store.call_once(run.run_id, 1, "calendar_create", {"t": 1}, failing)
        b = await store.call_once(run.run_id, 1, "calendar_create", {"t": 1}, ok)  # a clean failure may be retried
        # A call that started and never recorded an outcome (the process died mid-call) is not run again.
        key = idempotency_key(run.run_id, 5, "gmail_send", args_digest({"x": 1}))
        store._exec("INSERT INTO executed_calls(key, run_id, step, tool, args_digest, status, created_at) VALUES(?,?,?,?,?,'started',?)",  # noqa: SLF001
                    (key, run.run_id, 5, "gmail_send", args_digest({"x": 1}), time.time()))
        c = await store.call_once(run.run_id, 5, "gmail_send", {"x": 1}, ok)
        return a, b, c

    a, b, c = asyncio.run(go())
    assert a == ({"error": "HTTP 503"}, False)
    assert b == ({"ok": True}, False)
    assert c[1] is True and c[0]["unknown_outcome"] is True and "error" in c[0]
    assert calls == ["fail", "ok"], "the unknown-outcome call never reached the tool"
    assert [r["attempts"] for r in store.executed(run.run_id) if r["step"] == 1] == [2]


def test_an_unverified_write_replays_instead_of_writing_again() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run = Run(cid, store)
    calls: list[str] = []
    unverified = {"error": "UNVERIFIED: could not read it back", "verification": {"status": "unverified"}}

    async def send() -> dict[str, Any]:
        calls.append("send")
        return unverified

    async def go() -> tuple[Any, Any]:
        a = await store.call_once(run.run_id, 1, "gmail_send", {"to": "x"}, send)
        b = await store.call_once(run.run_id, 1, "gmail_send", {"to": "x"}, send)
        return a, b

    a, b = asyncio.run(go())
    assert a == (unverified, False) and b == (unverified, True)
    assert calls == ["send"], "the write may have landed, so it never runs twice"
    assert [r["status"] for r in store.executed(run.run_id)] == ["done"]


def test_recovery_parks_a_desks_pending_card_so_one_answer_is_enough() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run = Run(cid, store)
    args = {"to": "x@example.com"}
    store.open_approval(f"{run.run_id}:d", run.run_id, "gmail_send", args, conversation_id=cid, desk_id="desk_recover")
    store.open_approval(f"{run.run_id}:c", run.run_id, "gmail_send", args, conversation_id=cid)
    store.recover()
    desk_card, chat_card = store.approval(f"{run.run_id}:d"), store.approval(f"{run.run_id}:c")
    assert desk_card["status"] == "pending" and desk_card["parked_at"], "still answerable, and no run holds it"
    assert not chat_card["parked_at"], "a chat card is not a desk's"
    store.decide(f"{run.run_id}:d", "allow")
    assert store.claim_parked("desk_recover", "gmail_send", args, "next:call_0"), "the desk's next turn runs it with no second card"


def test_an_unverified_write_is_replayed_never_written_again() -> None:
    cid = j("POST", "/conversations", {})["id"]
    run, resumed = Run(cid, store), Run(cid, store)
    calls: list[int] = []

    async def create() -> dict[str, Any]:
        calls.append(1)
        return {"id": "ev1", "verification": {"status": "unverified"}, "error": "UNVERIFIED - do not retry"}

    async def go() -> tuple[Any, Any]:
        await store.call_once(run.run_id, 1, "calendar_create", {"t": 1}, create)
        again = await store.call_once(run.run_id, 1, "calendar_create", {"t": 1}, create)
        resume = await store.call_once(resumed.run_id, 1, "calendar_create", {"t": 1}, create, inherit=run.run_id)
        return again, resume

    again, resume = asyncio.run(go())
    assert again[1] is True and resume[1] is True and calls == [1]


def test_a_duplicate_write_in_one_round_runs_once_end_to_end() -> None:
    title = f"durable dup {time.time()}"
    ROUNDS.append({"tool_calls": [call("todo_add", {"title": title}, "call_0"), call("todo_add", {"title": title}, "call_1")]})
    ROUNDS.append(["done"])
    _cid, rid = start({"tools": {"todo_add": "on"}})
    assert drain(rid)["status"] == "done"
    assert len([t for t in appmod.todos.list("__all__") if t["title"] == title]) == 1, "the second identical write was a replay"
    results = [d for _, e, d in store.events(rid) if e == "tool_result"]
    assert len(results) == 2 and results[0]["error"] is None
    assert "replayed" in results[1]["result_preview"]
    journal = j("GET", f"/runs/{rid}")["executed_calls"]
    assert len(journal) == 1 and journal[0]["tool"] == "todo_add" and journal[0]["status"] == "done" and journal[0]["step"] == 1


def test_chat_notify_is_a_known_setting_that_round_trips() -> None:
    assert llm.DEFAULT_SETTINGS["chatNotify"] is True
    assert j("GET", "/settings")["chatNotify"] is True
    assert j("PUT", "/settings", {"chatNotify": False})["chatNotify"] is False
    assert j("GET", "/settings")["chatNotify"] is False
    assert j("PUT", "/settings", {"chatNotify": True})["chatNotify"] is True


def test_read_only_tools_skip_the_journal() -> None:
    ROUNDS.append({"tool_calls": [call("current_time", {})]})
    _cid, rid = start({"tools": {"current_time": "on"}})
    assert drain(rid)["status"] == "done"
    assert store.executed(rid) == []


# ---------------- startup recovery ----------------
def test_startup_marks_orphaned_runs_interrupted_and_keeps_the_approval_answerable() -> None:
    cid = j("POST", "/conversations", {})["id"]
    am = appmod.convos.add_message(cid, "assistant", "")
    # A run the previous process left mid-reply, blocked on an approval.
    run = Run(cid, store)
    run.publish("assistant_message", {**am, "context_used": None})
    run.publish("delta", {"id": am["id"], "text": "Let me send "})
    run.publish("delta", {"id": am["id"], "text": "that."})
    uid = f"{am['id']}:call_0"
    store.open_approval(uid, run.run_id, "gmail_send", {"to": "x@example.com"}, conversation_id=cid, message_id=am["id"])
    run.set_status("awaiting_approval")
    plain = Run(cid, store)  # a second orphan that was simply running
    assert store.get(plain.run_id)["status"] == "running"

    asyncio.run(appmod._recover_runs())  # noqa: SLF001 - what startup runs

    row = store.get(run.run_id)
    assert row["status"] == "interrupted" and row["ended_at"] and uid in row["error"] and "gmail_send" in row["error"]
    last = store.events(run.run_id)[-1]
    assert last[1] == "error" and last[2]["interrupted"] is True and last[2]["pending_approvals"] == [uid] and last[0] == row["last_seq"]
    assert store.get(plain.run_id)["status"] == "interrupted"
    assert any(r["run_id"] == run.run_id for r in j("GET", "/runs?status=interrupted"))
    assert all(r["run_id"] != run.run_id for r in j("GET", "/runs")), "an interrupted run is not active"

    msg = j("GET", f"/conversations/{cid}")["messages"][-1]
    assert msg["content"] == "Let me send that.", "the reply is salvaged from the tape"
    assert msg["error"] and "Interrupted" in msg["error"]
    card = [t for t in msg["tool_events"] if t["id"] == uid][0]
    assert card["pending"] is True and card["needs_approval"] is True, "the UI still has an approval card to show"

    pend = [a for a in j("GET", "/approvals") if a["call_id"] == uid]
    assert pend and pend[0]["status"] == "pending" and pend[0]["live"] is False
    res = j("POST", f"/approvals/{uid}", {"decision": "allow"})
    # `resumed` is the desk half of the answer: a chat approval never resumes anything.
    assert res == {"ok": True, "live": False, "resumed": False, "queued": False, "status": "approved"}
    assert store.approval(uid)["decision"] == "allow"
    card = [t for t in j("GET", f"/conversations/{cid}")["messages"][-1]["tool_events"] if t["id"] == uid][0]
    assert card["pending"] is False and card["needs_approval"] is False and card["approval"] == "allow"
    j("POST", f"/approvals/{uid}", {"decision": "allow"}, expect=404)

    # A run alive in this process is never marked interrupted.
    async def live_one() -> str:
        r = Run(cid, store)
        appmod.bus._runs["__probe__"] = r  # noqa: SLF001
        try:
            await appmod._recover_runs()  # noqa: SLF001
            return store.get(r.run_id)["status"]
        finally:
            appmod.bus._runs.pop("__probe__", None)  # noqa: SLF001
            r.end()

    assert asyncio.run(live_one()) == "running"
