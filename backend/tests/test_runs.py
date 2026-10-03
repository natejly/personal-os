"""The run bus against the real app, with llm.stream_chat scripted.

Run: PERSONAL_OS_DATA_DIR=/tmp/runstest python backend/tests/test_runs.py
The whole point of the slice is that a run outlives its viewers, so that is tested explicitly.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="runstest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as app_mod  # noqa: E402
from personal_os import learn as learn_mod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, bus  # noqa: E402
from personal_os.runs import QUEUE_MAX, RING, Run  # noqa: E402

# The ChatEvent union in src/shared/types.ts. Nothing may leave the bus that is not one of these.
CHAT_EVENTS = {"user_message", "assistant_message", "removed_message", "restored_message", "title", "delta", "reasoning", "tool_call",
               "tool_result", "span", "done", "learned", "learn_error", "error", "taint", "plan",
                "plan_card", "plan_decision", "parked", "desk_status", "desk_handoff"}

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})

try:
    import pytest
except ImportError:  # standalone `python tests/test_runs.py` run
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        """One portal for the whole module, so every request shares the event loop the run tasks live on."""
        with client:
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
            yield
passed = 0
SCRIPT: dict[str, Any] = {"chunks": ["Hello", " ", "world"], "delay": 0.0}


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    for chunk in SCRIPT["chunks"]:
        if SCRIPT["delay"]:
            await asyncio.sleep(SCRIPT["delay"])
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


# app.py calls stream_chat with keywords, so a parameter added there must land on the stub too or every
# run dies before its first delta.
_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted_stream).parameters)
assert not _missing, f"_scripted_stream is missing {sorted(_missing)} from llm.stream_chat"

_real_stream = llm.stream_chat
llm.stream_chat = _scripted_stream


def script(n: int, delay: float = 0.0) -> str:
    """Arm the stub with n numbered deltas and return the text they add up to."""
    SCRIPT["chunks"] = [f"w{i} " for i in range(n)]
    SCRIPT["delay"] = delay
    return "".join(SCRIPT["chunks"]).strip()


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def new_conv() -> str:
    return j("POST", "/conversations", {})["id"]


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {label}")


def events(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        event, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event and data:
            out.append((event, json.loads(data)))
    return out


def read_streams(paths: list[str], timeout: float = 20.0) -> list[str]:
    """TestClient buffers a response, so one GET /stream blocks until that run ends: one thread each."""
    out: dict[int, str] = {}

    def pull(i: int, path: str) -> None:
        out[i] = client.get(path).text

    threads = [threading.Thread(target=pull, args=(i, p), daemon=True) for i, p in enumerate(paths)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert len(out) == len(paths), f"only {len(out)}/{len(paths)} streams finished within {timeout}s"
    return [out[i] for i in range(len(paths))]


def text_of(evs: list[tuple[str, Any]]) -> str:
    """The deltas concatenated, stripped the way the run strips before persisting."""
    return "".join(d["text"] for e, d in evs if e == "delta").strip()


def message(conv_id: str) -> dict[str, Any]:
    msgs = j("GET", f"/conversations/{conv_id}")["messages"]
    return msgs[-1] if msgs else {}


def run_info(conv_id: str) -> dict[str, Any]:
    return next((r for r in j("GET", "/runs") if r["conversation_id"] == conv_id), {})


def drain(conv_id: str) -> None:
    wait_until(lambda: not run_info(conv_id), f"the run on {conv_id} to end")


def test_post_starts_a_background_run() -> None:
    full = script(6, 0.05)
    cid = new_conv()
    started = j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    check(isinstance(started["run_id"], str) and started["run_id"], "POST /chat returns a run_id")
    check(started["seq"] == 0, f"a fresh run has produced no events yet, got seq {started['seq']}")

    live = [r for r in j("GET", "/runs") if r["conversation_id"] == cid]
    check(len(live) == 1, "GET /runs lists the run started by POST")
    check(live[0]["run_id"] == started["run_id"] and live[0]["live"] is True, "the listed run is this one, and live")
    wait_until(lambda: bool(run_info(cid).get("message_id")), "the assistant message to exist")
    info = run_info(cid)
    check(info["message_id"] == message(cid)["id"], "RunInfo carries the message_id a new window paints its ring from")
    check(info["seq"] > 0 and isinstance(info["started_at"], float), "RunInfo carries a growing seq and a start time")

    drain(cid)
    check(j("GET", "/runs") == [], "a finished run is no longer listed")
    check(message(cid)["content"] == full, "the reply is persisted in full")
    check(j("POST", f"/conversations/{cid}/chat", {"content": "again"})["seq"] == 0, "the conversation takes a new run afterwards")
    drain(cid)


def test_second_post_conflicts() -> None:
    script(40, 0.05)
    cid = new_conv()
    started = j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    detail = client.post(f"/conversations/{cid}/chat", json={"content": "again"})
    check(detail.status_code == 409, f"a second POST while a run is live 409s, got {detail.status_code}")
    conflict = detail.json()["detail"]
    check(conflict["run_id"] == started["run_id"], "the 409 carries the live run_id the renderer attaches to")
    check(isinstance(conflict["seq"], int) and isinstance(conflict["message"], str), "the 409 detail is a RunConflict")
    check(j("POST", f"/conversations/{cid}/stop")["ok"] is True, "stop ends it")
    drain(cid)
    j("POST", "/conversations/nope/chat", {"content": "hi"}, expect=404)


def test_two_clients_see_the_same_events() -> None:
    full = script(12, 0.04)
    cid = new_conv()
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    url = f"/conversations/{cid}/stream?since=0"
    bodies: list[str] = []
    threads = [threading.Thread(target=lambda: bodies.append(client.get(url).text), daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    wait_until(lambda: len(bus.get(cid)._subs) == 2, "both clients to attach while the run is live")  # noqa: SLF001
    check(bus.get(cid).live, "the run is still live with two subscribers attached")
    for t in threads:
        t.join(20)
    check(len(bodies) == 2, "both streams ended when the run did")
    check(bodies[0] == bodies[1], "two clients on one conversation get byte-identical events")
    a = events(bodies[0])
    check(text_of(a) == full, "each client sees every delta")
    names = [e for e, _ in a]
    check(names[0] == "user_message" and names.index("assistant_message") < names.index("delta"), f"ordered from the user message, got {names[:4]}")
    check(a[-1][0] == "done" and a[-1][1]["error"] is None, "the sequence ends with a clean done")


def test_late_client_replays_from_the_ring() -> None:
    full = script(9)
    cid = new_conv()
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    drain(cid)
    replay = events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])
    check(text_of(replay) == full, "a client attaching after the run ended still replays the whole reply")
    check(replay[-1][0] == "done", "the replay includes done")
    tail = events(read_streams([f"/conversations/{cid}/stream?since={len(replay) - 1}"])[0])
    check([e for e, _ in tail] == ["done"], f"since=N skips what the client already has, got {[e for e, _ in tail]}")
    check(read_streams([f"/conversations/{new_conv()}/stream"])[0] == "", "a conversation with no run streams an empty body")


def test_event_names_are_the_chatevent_union() -> None:
    script(4)
    cid = new_conv()
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    drain(cid)
    evs = events(read_streams([f"/conversations/{cid}/stream"])[0])
    names = {e for e, _ in evs}
    check(names <= CHAT_EVENTS, f"every event name is in the ChatEvent union, got extras {names - CHAT_EVENTS}")
    check({"user_message", "assistant_message", "span", "delta", "done"} <= names, f"the core events all fired, got {names}")
    by = {e: d for e, d in evs}
    check(by["user_message"]["role"] == "user" and by["user_message"]["content"] == "hi", "user_message shape")
    check(by["assistant_message"]["id"] and "context_used" in by["assistant_message"], "assistant_message shape")
    check(set(by["delta"]) == {"id", "text"}, f"delta shape is {{id, text}}, got {set(by['delta'])}")
    check(set(by["done"]) == {"id", "error", "context_used", "tool_events", "trace", "stopped",
                              "partial", "segment", "tainted", "taint_sources", "reasoning", "outcome", "error_kind"}, f"done shape, got {set(by['done'])}")
    check(by["done"]["segment"] is False, "the last done ends the run; a steered segment's says True")
    check(by["done"]["stopped"] is False, "an uninterrupted run reports stopped false")
    check(set(by["span"]) == {"message_id", "span"}, "span shape")


def test_stop_ends_the_run() -> None:
    full = script(80, 0.05)
    cid = new_conv()
    started = j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    wait_until(lambda: run_info(cid).get("seq", 0) >= 7, "a few deltas to be produced")
    check(j("POST", f"/conversations/{cid}/stop", None)["ok"] is True, "POST /stop reports it stopped a run")
    drain(cid)
    stopped = message(cid)
    check(0 < len(stopped["content"]) < len(full), f"the partial reply is persisted, got {len(stopped['content'])} of {len(full)} chars")
    done = dict(events(read_streams([f"/conversations/{cid}/stream"])[0]))["done"]
    check(done["stopped"] is True, "done reports stopped")
    check(j("POST", f"/conversations/{cid}/stop")["ok"] is False, "stopping a conversation with no live run is ok false")
    check(j("POST", f"/conversations/{cid}/chat", {"content": "hi"})["run_id"] != started["run_id"], "a stopped conversation starts a fresh run")
    check(j("POST", f"/conversations/{cid}/stop?run_id=someoneelse")["ok"] is False, "a run_id that does not match is ok false")
    check(j("POST", f"/conversations/{cid}/stop?run_id={j('GET', '/runs')[0]['run_id']}")["ok"] is True, "a matching run_id stops it")
    drain(cid)

    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    wait_until(lambda: bool(run_info(cid).get("message_id")), "the assistant message to exist")
    check(j("POST", f"/messages/{run_info(cid)['message_id']}/stop")["ok"] is True, "the per-message stop route still reaches the run")
    drain(cid)


def test_steer_folds_into_the_live_run() -> None:
    full = script(40, 0.05)
    cid = new_conv()
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    wait_until(lambda: run_info(cid).get("seq", 0) >= 5, "a few deltas to be produced")
    steered = j("POST", f"/conversations/{cid}/steer", {"content": "also do this"})
    check(steered["ok"] is True and steered["message"]["role"] == "user", "steer persists and returns a user message")
    drain(cid)
    evs = events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])
    names = [e for e, _ in evs]
    check(names.count("done") == 2, f"the steer closes one segment and a second one answers, got {names}")
    check(names.count("assistant_message") == 2, "a fresh assistant message opens after the steer")
    check(all(d["error"] is None for e, d in evs if e == "done"), "both segments end clean")
    msgs = j("GET", f"/conversations/{cid}")["messages"]
    roles = [m["role"] for m in msgs]
    check(roles == ["user", "assistant", "user", "assistant"], f"transcript holds both turns in order, got {roles}")
    check(msgs[2]["content"] == "also do this", "the steered message is the second user turn")
    check(0 < len(msgs[1]["content"]) < len(full), f"the steer cuts the segment in progress, got {len(msgs[1]['content'])} of {len(full)}")
    check(msgs[3]["content"] == full, "the follow-up is a whole reply")
    r = client.post(f"/conversations/{cid}/steer", json={"content": "late"})
    check(r.status_code == 409, f"steering with no live run 409s, got {r.status_code}")
    j("POST", "/conversations/nope/steer", {"content": "x"}, expect=404)


def test_the_learn_tail_does_not_hold_the_conversation() -> None:
    """Auto-learn no longer runs inside the run: the exchange goes to learn.LearnWorker and the run
    ends at its `done` (see test_learn_async.py). So a held-up extraction must not keep the
    conversation locked — the reply is persisted, and the next message starts its own run at once.
    """
    full = script(4)
    gate = threading.Event()

    async def _gated_learn(**kw: Any) -> dict[str, list[Any]]:
        await asyncio.get_running_loop().run_in_executor(None, gate.wait)
        return {"memories": [], "nodes": [], "edges": []}

    real, learn_mod.learn_from_exchange = learn_mod.learn_from_exchange, _gated_learn
    client.put("/settings", json={"autoLearn": True, "baseUrl": ""})
    cid = new_conv()
    try:
        j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
        drain(cid)
        check(message(cid)["content"] == full, "the whole reply is persisted before the tail starts")
        late = client.post(f"/conversations/{cid}/steer", json={"content": "late"})
        check(late.status_code == 409, f"a steer after the reply is refused, not swallowed, got {late.status_code}")
        check(len(j("GET", f"/conversations/{cid}")["messages"]) == 2, "and it left no unanswered message behind")

        nxt = client.post(f"/conversations/{cid}/chat", json={"content": "next"})
        check(nxt.status_code == 200, f"a new message starts its own run instead of a 409, got {nxt.status_code}")
        check(bool(nxt.json()["run_id"]), "and it is a run of its own, not the first one resumed")
        drain(cid)
    finally:
        gate.set()
        learn_mod.learn_from_exchange = real
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
    drain(cid)


async def _detach_mid_run() -> tuple[str, str, str]:
    """Attach two clients, drop both mid-reply, and let the run finish alone."""
    full = script(30, 0.03)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Personal-OS-Token": AUTH_TOKEN}) as ac:
        cid = (await ac.post("/conversations", json={})).json()["id"]
        await ac.post(f"/conversations/{cid}/chat", json={"content": "hi"})
        watchers = [asyncio.create_task(ac.get(f"/conversations/{cid}/stream")) for _ in range(2)]
        while len(bus.get(cid)._subs) < 2:  # noqa: SLF001
            await asyncio.sleep(0.01)
        seq_at_detach = bus.get(cid).seq
        for w in watchers:
            w.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        assert not bus.get(cid)._subs, "the subscriber set did not empty"  # noqa: SLF001
        assert bus.get(cid).live, "the run died with its last viewer"
        while bus.get(cid).live:
            await asyncio.sleep(0.02)
        assert bus.get(cid).seq > seq_at_detach, "the run stopped producing events once nobody watched"
        body = (await ac.get(f"/conversations/{cid}/stream?since=0")).text
        reply = (await ac.get(f"/conversations/{cid}")).json()["messages"][-1]
    assert reply["error"] is None, f"the unwatched run errored: {reply['error']}"
    return reply["content"], full, body


def test_run_survives_every_subscriber_leaving() -> None:
    content, full, body = asyncio.run(_detach_mid_run())
    check(content == full, f"the reply completed and persisted with nobody watching, got {len(content)} of {len(full)} chars")
    check(text_of(events(body)) == full, "and the whole reply is still replayable from the ring afterwards")


async def _shutdown_mid_run() -> tuple[str, str, int]:
    """What the shutdown hook does before the tmp-dir rmtree: cancel live runs and wait for them."""
    full = script(60, 0.03)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Personal-OS-Token": AUTH_TOKEN}) as ac:
        cid = (await ac.post("/conversations", json={})).json()["id"]
        await ac.post(f"/conversations/{cid}/chat", json={"content": "hi"})
        while bus.get(cid) is None or bus.get(cid).seq < 7:
            await asyncio.sleep(0.01)
        await bus.shutdown()
        assert bus.get(cid) is None, "shutdown left the run behind"
        reply = (await ac.get(f"/conversations/{cid}")).json()["messages"][-1]
        return reply["content"], full, len((await ac.get("/runs")).json())


def test_shutdown_cancels_a_live_run_and_keeps_its_text() -> None:
    content, full, live = asyncio.run(_shutdown_mid_run())
    check(live == 0, "no run survives the shutdown")
    check(0 < len(content) < len(full), f"a cancelled run still persists its partial text, got {len(content)} of {len(full)} chars")


async def _overflow_then_reconnect() -> tuple[int, bool, list[str]]:
    """A subscriber that stops reading overflows its queue; the ring is what it reconnects from."""
    run = Run("overflow")
    run.publish("delta", {"id": "m", "text": "1"})
    gen = run.subscribe(0)
    first = await gen.__anext__()
    assert json.loads(first.split("data: ")[1])["text"] == "1", "the ring replayed the first event"
    for i in range(2, QUEUE_MAX + 300):
        run.publish("delta", {"id": "m", "text": str(i)})
    overflowed = all(sub.overflow for sub in run._subs)  # noqa: SLF001
    await gen.aclose()
    run.end()
    tail = [json.loads(block.split("data: ")[1])["text"] async for block in run.subscribe(1)]
    return run.seq, overflowed, tail


def test_an_overflowed_subscriber_reconnects_without_a_gap() -> None:
    check(QUEUE_MAX < RING, "the invariant itself: a subscriber can never overflow past the ring")
    seq, overflowed, tail = asyncio.run(_overflow_then_reconnect())
    check(overflowed, f"a subscriber that stopped reading overflowed at {QUEUE_MAX} queued events")
    check(tail == [str(i) for i in range(2, seq + 1)], "reconnecting at its last seq replays every event it missed, in order")


def test_stop_and_steer_cut_a_blocked_provider_read() -> None:
    """No next token is coming. Stop and steer still have to end the read, not wait it out."""
    prev = llm.stream_chat
    # Hold only until the first cancel. The steered follow-up must finish on its own, or it waits out the timeout.
    state = {"hold": True}

    async def _blocked(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                       effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                       cancel: asyncio.Event | None = None) -> Any:
        yield {"type": "delta", "text": "partial "}
        if state["hold"] and cancel is not None:
            try:
                await asyncio.wait_for(cancel.wait(), 2)
            except asyncio.TimeoutError:
                yield {"type": "delta", "text": "TOO-LATE "}
            else:
                state["hold"] = False
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    llm.stream_chat = _blocked
    try:
        cid = new_conv()
        j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
        wait_until(lambda: run_info(cid).get("seq", 0) >= 4, "the first token to be published")
        check(j("POST", f"/conversations/{cid}/stop")["ok"] is True, "stop reaches a blocked stream")
        drain(cid)
        stopped = message(cid)["content"]
        check(stopped == "partial", f"stop cut the stream before the rest, got {stopped!r}")

        state["hold"] = True
        cid = new_conv()
        j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
        wait_until(lambda: run_info(cid).get("seq", 0) >= 4, "the first token to be published")
        steered = j("POST", f"/conversations/{cid}/steer", {"content": "turn left"})
        check(steered["ok"] is True, "steer reaches a blocked stream")
        drain(cid)
        msgs = j("GET", f"/conversations/{cid}")["messages"]
        check([m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"], f"steer split the reply, got {[m['role'] for m in msgs]}")
        check(msgs[1]["content"] == "partial", f"the first segment was cut, got {msgs[1]['content']!r}")
        check(msgs[2]["content"] == "turn left", "the steer is the next user turn")
        check(msgs[3]["content"] == "partial", f"the follow-up started and was not the abandoned tail, got {msgs[3]['content']!r}")
        check("TOO-LATE" not in "".join(m["content"] for m in msgs), "neither segment waited out the blocked read")
    finally:
        llm.stream_chat = prev


def test_reasoning_stays_out_of_the_reply() -> None:
    """A reasoning model's chain-of-thought is shown and stored, and is not the answer."""
    prev = llm.stream_chat

    async def _think(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                     effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                     cancel: asyncio.Event | None = None) -> Any:
        yield {"type": "reasoning", "text": "weighing it"}
        yield {"type": "delta", "text": "the answer"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    llm.stream_chat = _think
    try:
        cid = new_conv()
        j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
        drain(cid)
        evs = events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])
        names = [e for e, _ in evs]
        check(names.index("reasoning") < names.index("delta"), f"thinking arrives before the answer, got {names}")
        done = next(d for e, d in evs if e == "done")
        check(done["reasoning"] == "weighing it", f"done carries the thought, got {done.get('reasoning')!r}")
        m = message(cid)
        check(m["content"] == "the answer", f"the reply is only the answer, got {m['content']!r}")
        check(m["reasoning"] == "weighing it", f"the thought is stored on the message, got {m.get('reasoning')!r}")
        check("weighing it" not in m["content"], "the thought did not leak into the reply")
    finally:
        llm.stream_chat = prev


def test_cancel_closes_the_provider_socket() -> None:
    """stream_chat itself drops the connection; the read must not sit until the server sends more."""
    async def go() -> None:
        disconnected = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = b""
            while b"\r\n\r\n" not in data:
                data += await reader.read(4096)
            head, rest = data.split(b"\r\n\r\n", 1)
            need = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    need = int(line.split(b":", 1)[1].strip())
            while len(rest) < need:
                rest += await reader.read(4096)
            payload = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 1000000\r\n\r\n" + payload
            )
            await writer.drain()
            try:
                await reader.read()
            finally:
                disconnected.set()
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        cancel = asyncio.Event()
        agen = _real_stream({"baseUrl": f"http://127.0.0.1:{port}", "apiKey": ""}, "m",
                            [{"role": "user", "content": "hi"}], cancel=cancel)
        try:
            first = await asyncio.wait_for(agen.__anext__(), 2)
            check(first == {"type": "delta", "text": "Hi"}, f"the first token arrived, got {first!r}")
            cancel.set()
            started = time.time()
            end = await asyncio.wait_for(agen.__anext__(), 2)
            check(time.time() - started < 1.5, "cancel returned without waiting on the server")
            check(end["type"] == "end" and end["finish_reason"] == "cancelled" and end["tool_calls"] == [],
                  f"the end event is a cancellation, got {end!r}")
            await asyncio.wait_for(disconnected.wait(), 1.5)
        finally:
            await agen.aclose()
            server.close()
            await server.wait_closed()

    asyncio.run(go())


TESTS = [test_post_starts_a_background_run, test_second_post_conflicts, test_two_clients_see_the_same_events,
         test_late_client_replays_from_the_ring, test_event_names_are_the_chatevent_union, test_stop_ends_the_run,
         test_steer_folds_into_the_live_run, test_stop_and_steer_cut_a_blocked_provider_read,
         test_reasoning_stays_out_of_the_reply,
         test_cancel_closes_the_provider_socket, test_the_learn_tail_does_not_hold_the_conversation,
         test_run_survives_every_subscriber_leaving, test_an_overflowed_subscriber_reconnects_without_a_gap,
         test_shutdown_cancels_a_live_run_and_keeps_its_text]

if __name__ == "__main__":
    failures = 0
    # One portal, so every request shares the event loop the run tasks live on.
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        for t in TESTS:
            try:
                t()
                print(f"ok   {t.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
