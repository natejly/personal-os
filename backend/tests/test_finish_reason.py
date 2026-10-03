"""How a reply ends: the model round is classified (content filter, output limit, incomplete stream, empty round)
and the reason lands on the messages row and on the final `done`. Tool-call ids stay unique across rounds.

Run: PYTHONPATH=backend python backend/tests/test_finish_reason.py
Same harness as test_runs.py: TestClient + a scripted llm.stream_chat.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="finishtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, EMPTY_NUDGE, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})

try:
    import pytest
except ImportError:  # standalone run
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        with client:
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
            yield

passed = 0
ROUNDS: list[dict[str, Any]] = []
SEEN: list[list[dict[str, Any]]] = []
SLOW = {"on": False}


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    step = ROUNDS.pop(0) if ROUNDS else {"text": "all done"}
    if step.get("text"):
        for part in (step["text"].split(" ") if SLOW["on"] else [step["text"]]):
            if SLOW["on"]:
                await asyncio.sleep(0.05)
            yield {"type": "delta", "text": part + (" " if SLOW["on"] else "")}
    yield {"type": "end", "finish_reason": step.get("finish", "stop"), "tool_calls": step.get("calls", []),
           "usage": None, "incomplete": step.get("incomplete", False)}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted_stream).parameters)
assert not _missing, f"_scripted_stream is missing {sorted(_missing)} from llm.stream_chat"
llm.stream_chat = _scripted_stream


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]}"
    return r.json()


def wait_until(pred: Callable[[], bool], label: str, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def events(text: str) -> list[tuple[str, Any]]:
    out = []
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


def reply(script: list[dict[str, Any]], slow: bool = False) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, Any]]]:
    """Run one chat turn over the scripted rounds; returns (final done payload, persisted row, all events)."""
    ROUNDS[:] = script
    SEEN.clear()
    SLOW["on"] = slow
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    wait_until(lambda: not [r for r in j("GET", "/runs") if r["conversation_id"] == cid], "the run to end")
    evs = events(client.get(f"/conversations/{cid}/stream?since=0").text)
    done = [d for e, d in evs if e == "done" and not d.get("segment")][-1]
    row = j("GET", f"/conversations/{cid}")["messages"][-1]
    row["_cid"] = cid
    return done, row, evs


def call(cid: str, name: str = "list_documents", args: str = "{}") -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": args}


def test_clean_reply_has_no_outcome() -> None:
    done, row, _ = reply([{"text": "fine"}])
    check(done["outcome"] is None and done["error_kind"] is None and done["error"] is None, "a clean done carries null outcome and error_kind")
    check(row["outcome"] is None and row["error_kind"] is None, "and so does the row")


def test_content_filter_is_an_error_with_a_kind() -> None:
    done, row, _ = reply([{"text": "I can", "finish": "content_filter", "calls": [call("c1")]}])
    check(done["error"] and "filtered" in done["error"], "content_filter ends with an error")
    check(done["error_kind"] == "content_filter" and done["outcome"] is None, "kind is content_filter, never an outcome")
    check(row["error_kind"] == "content_filter" and row["content"] == "I can", "kind persisted and the text that arrived is kept")
    check(not done["tool_events"], "no tool ran after a filtered round")


def test_length_with_text_is_an_outcome() -> None:
    done, row, _ = reply([{"text": "half an answ", "finish": "length"}])
    check(done["outcome"] == "length" and done["error"] is None, "length with text -> outcome length")
    check(row["outcome"] == "length" and row["content"] == "half an answ", "persisted with the text")


def test_length_with_nothing_is_an_error() -> None:
    done, row, _ = reply([{"text": "", "finish": "length"}])
    check(done["error"] and "output limit" in done["error"] and done["outcome"] is None, "an empty length reply is an error")
    check(row["error"] == done["error"], "persisted")


def test_incomplete_retries_once_then_answers() -> None:
    done, row, _ = reply([{"text": "", "incomplete": True}, {"text": "here it is"}])
    check(len(SEEN) == 2, f"one quiet retry, got {len(SEEN)} rounds")
    check(done["error"] is None and done["outcome"] is None and row["content"] == "here it is", "the retry answered")


def test_incomplete_with_text_is_an_outcome() -> None:
    done, row, _ = reply([{"text": "cut off mid", "incomplete": True}])
    check(len(SEEN) == 1, "text exists, so no retry")
    check(done["outcome"] == "incomplete" and row["outcome"] == "incomplete" and row["content"] == "cut off mid", "outcome incomplete")


def test_incomplete_twice_empty_is_an_error() -> None:
    done, _, _ = reply([{"text": "", "incomplete": True}, {"text": "", "incomplete": True}])
    check(len(SEEN) == 2, "never more than one silent retry")
    check(done["error"] and done["outcome"] is None, "nothing came back: an error, not an empty success")


def test_empty_round_is_nudged_once() -> None:
    done, row, _ = reply([{"text": ""}, {"text": "now I answer"}])
    check(len(SEEN) == 2 and any(m.get("content") == EMPTY_NUDGE for m in SEEN[1]), "the retry carries the nudge")
    check(done["error"] is None and row["content"] == "now I answer", "answered after the nudge")
    done, _, _ = reply([{"text": ""}, {"text": ""}])
    check(len(SEEN) == 2 and done["error"] and "empty reply" in done["error"], "a second empty round is an error")


def test_garbled_arguments_are_never_replayed() -> None:
    reply([{"text": "", "calls": [call("c1", args='{"q": "unfinished')]}, {"text": "ok"}])
    turn = next(m for m in SEEN[1] if m.get("role") == "assistant" and m.get("tool_calls"))
    check(turn["tool_calls"][0]["function"]["arguments"] == "{}", "the model's next round sees {} for a call that was not valid JSON")


def test_call_ids_are_unique_across_rounds() -> None:
    reply([{"text": "", "calls": [call("call_0")]}, {"text": "", "calls": [call("call_0")]}, {"text": "done"}])
    ids = [tc["id"] for m in SEEN[-1] if m.get("role") == "assistant" for tc in m.get("tool_calls") or []]
    check(len(ids) == 2 and len(set(ids)) == 2, f"two rounds that both said call_0 got distinct ids, got {ids}")
    tool_ids = [m["tool_call_id"] for m in SEEN[-1] if m.get("role") == "tool"]
    check(tool_ids == ids, "and each tool message answers its own call")


def test_stop_persists_stopped() -> None:
    ROUNDS[:] = [{"text": " ".join(f"w{i}" for i in range(60))}]
    SLOW["on"] = True
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": "hi"})
    wait_until(lambda: bool(j("GET", f"/conversations/{cid}")["messages"][-1:] and j("GET", f"/conversations/{cid}")["messages"][-1]["role"] == "assistant"), "the reply row")
    time.sleep(0.2)
    j("POST", f"/conversations/{cid}/stop")
    wait_until(lambda: not [r for r in j("GET", "/runs") if r["conversation_id"] == cid], "the run to end")
    SLOW["on"] = False
    evs = events(client.get(f"/conversations/{cid}/stream?since=0").text)
    done = [d for e, d in evs if e == "done"][-1]
    row = j("GET", f"/conversations/{cid}")["messages"][-1]
    check(done["stopped"] is True and done["outcome"] == "stopped", "done carries stopped and outcome stopped")
    check(row["outcome"] == "stopped" and row["error"] is None, "and the row persists it")


if __name__ == "__main__":
    failed = 0
    prev = llm.stream_chat
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        try:
            for t in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
                try:
                    t()
                    print(f"ok   {t.__name__}")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    import traceback
                    traceback.print_exc()
                    print(f"FAIL {t.__name__}: {e}")
        finally:
            llm.stream_chat = prev
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    sys.exit(1 if failed else 0)
