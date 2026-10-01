"""Auto-learn runs off the reply's critical path, against the real app with both LLM calls scripted.

Run: python backend/tests/test_learn_async.py
The point of the slice: the run ends when the reply does, even though extraction is still going, so
the conversation takes its next message immediately and the result arrives on the app topic instead.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="learntest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import learn, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.app import events as topic  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0

try:
    import pytest
except ImportError:  # standalone `python tests/test_learn_async.py` run
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        saved = _install_stubs()
        try:
            with client:
                client.put("/settings", json={"autoLearn": True, "baseUrl": ""})
                yield
        finally:
            _restore_stubs(saved)
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    yield {"type": "delta", "text": "hello"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


# The extraction stub, and the gate the test holds it on. `calls` is read from the test thread while
# the stub runs on the app's loop thread; every field is a plain assignment, so that is safe enough.
GATE = {"open": False}
calls: dict[str, int] = {"started": 0, "finished": 0}


async def _scripted_learn(**kw: Any) -> dict[str, Any]:
    calls["started"] += 1
    while not GATE["open"]:
        await asyncio.sleep(0.01)
    calls["finished"] += 1
    return {"memories": [{"id": f"m{calls['finished']}", "content": "User likes tests"}], "nodes": [], "edges": []}


def _install_stubs() -> tuple[Any, Any]:
    """Patch both LLM calls, for this module only.

    Scoped rather than left in place: pytest imports every test module before running any test, so a
    stub installed at import time would still be in place for other modules. test_learn.py exercises
    the real extraction and would hang here forever, waiting on a GATE only these tests open.
    """
    saved = (llm.stream_chat, learn.learn_from_exchange)
    llm.stream_chat = _scripted_stream
    learn.learn_from_exchange = _scripted_learn
    return saved


def _restore_stubs(saved: tuple[Any, Any]) -> None:
    llm.stream_chat, learn.learn_from_exchange = saved


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {label}")


def run_live(conv_id: str) -> bool:
    return any(r["conversation_id"] == conv_id for r in j("GET", "/runs"))


def last_message(conv_id: str) -> dict[str, Any]:
    msgs = j("GET", f"/conversations/{conv_id}")["messages"]
    return msgs[-1] if msgs else {}


def read_events(since: int = 0) -> list[tuple[int, str, Any]]:
    """Replay the app topic's ring, as a window reconnecting at `since` would see it.

    Over HTTP rather than over the object would be better, but TestClient buffers a response whole
    and this stream never ends, so a GET /events never returns. The endpoint is `Topic.subscribe`
    and nothing else, which is what this drives — including the SSE text it writes.
    """
    async def pull() -> list[str]:
        gen = topic.subscribe(since)
        blocks: list[str] = []
        try:
            while True:  # the ring comes out with no awaiting; past it, the wait means it is drained
                blocks.append(await asyncio.wait_for(gen.__anext__(), timeout=0.5))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()
        return blocks

    out: list[tuple[int, str, Any]] = []
    for block in asyncio.run(pull()):
        seq, event, data = 0, "", ""
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


def test_the_run_ends_while_extraction_is_still_going() -> None:
    GATE["open"] = False
    before = calls["started"]
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": "remember this"})
    wait_until(lambda: not run_live(cid), "the run to end")

    check(last_message(cid)["content"] == "hello", "the reply is persisted before auto-learn is done")
    wait_until(lambda: calls["started"] > before, "the worker to pick the job up")
    check(calls["finished"] == 0, "the run ended with extraction still in flight, not after it")

    # The whole point: the conversation is free again, so the next message is taken, not 409'd.
    second = client.post(f"/conversations/{cid}/chat", json={"content": "and this"})
    check(second.status_code == 200, f"a second message lands while auto-learn runs, got {second.status_code}")
    wait_until(lambda: not run_live(cid), "the second run to end")
    check(calls["finished"] == 0, "both replies finished without ever waiting on extraction")

    GATE["open"] = True
    wait_until(lambda: calls["finished"] >= 2, "both queued jobs to drain")


def test_results_arrive_on_the_app_topic() -> None:
    GATE["open"] = True
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": "learn something"})
    wait_until(lambda: not run_live(cid), "the run to end")
    wait_until(lambda: any(s["kind"] == "learn" for s in last_message(cid).get("trace") or []),
               "the learn span to be written back to the message")

    mid = last_message(cid)["id"]
    evs = read_events()
    mine = [(seq, e, d) for seq, e, d in evs if e == "learned" and d.get("message_id") == mid]
    check(mine, f"the app topic carries this message's `learned`, got {[e for _, e, _ in evs]}")
    seq, _, data = mine[-1]
    check(seq > 0, "every topic event carries its seq as the SSE id, so a reconnect can resume")
    check(data["conversation_id"] == cid, "the event names the conversation it came from")
    check(len(data["memories"]) == 1, "the extracted memories ride along for the toast")
    check(all(s > seq for s, _, _ in read_events(since=seq)),
          "a window reconnecting at its last seq replays only what came after it")

    span = next(s for s in last_message(cid)["trace"] if s["kind"] == "learn")
    check(span["end"] and not span["error"], "the learn span is closed and clean in the stored trace")
    check(len(last_message(cid)["trace"]) > 1, "it was appended to the reply's own spans, not written over them")


def test_a_failing_extraction_reports_and_leaves_the_chat_alone() -> None:
    async def _boom(**kw: Any) -> dict[str, Any]:
        raise RuntimeError("extraction exploded")

    saved, learn.learn_from_exchange = learn.learn_from_exchange, _boom
    try:
        cid = j("POST", "/conversations", {})["id"]
        j("POST", f"/conversations/{cid}/chat", {"content": "boom"})
        wait_until(lambda: not run_live(cid), "the run to end")
        wait_until(lambda: any(s["kind"] == "learn" for s in last_message(cid).get("trace") or []),
                   "the failed learn span to be written back")
        mid = last_message(cid)["id"]
        errs = [d for _, e, d in read_events() if e == "learn_error" and d.get("message_id") == mid]
        check(errs and "exploded" in errs[-1]["message"], "the failure is reported on the topic, not swallowed")
        check(last_message(cid)["content"] == "hello", "the reply itself is untouched by its failed extraction")
        check(client.post(f"/conversations/{cid}/chat", json={"content": "again"}).status_code == 200,
              "and the conversation still takes the next message")
        wait_until(lambda: not run_live(cid), "the follow-up run to end")
    finally:
        learn.learn_from_exchange = saved


TESTS = [test_the_run_ends_while_extraction_is_still_going, test_results_arrive_on_the_app_topic,
         test_a_failing_extraction_reports_and_leaves_the_chat_alone]

if __name__ == "__main__":
    failures = 0
    _install_stubs()
    with client:  # one portal, so every request shares the loop the run and worker tasks live on
        client.put("/settings", json={"autoLearn": True, "baseUrl": ""})
        for t in TESTS:
            try:
                t()
                print(f"ok   {t.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
