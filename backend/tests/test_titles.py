"""Chat titles: word-boundary placeholder, then a detached model title that never overwrites the user's.

Run: python backend/tests/test_titles.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="titletest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm, titles  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.app import events as topic  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0

try:
    import pytest
except ImportError:
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        saved = _install_stubs()
        try:
            with client:
                client.put("/settings", json={"autoLearn": False, "autoTitle": True, "baseUrl": ""})
                yield
        finally:
            _restore_stubs(saved)
            client.put("/settings", json={"autoTitle": True, "baseUrl": ""})


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    yield {"type": "delta", "text": "hello"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


GATE = {"open": True}
DEFAULT_REPLY = "<think>hm</think>\n\"Trip planning for Lisbon.\"\nextra"
STATE: dict[str, Any] = {"calls": 0, "prompts": [], "ctx": [], "raise": False, "reply": DEFAULT_REPLY}


async def _scripted_complete(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "learn", **kw: Any) -> str:
    STATE["calls"] += 1
    STATE["prompts"].append((kind, json.dumps(messages), kw.get("effort"), kw.get("deadline") is not None))
    STATE["ctx"].append(dict(llm.usage_context.get()))
    while not GATE["open"]:
        await asyncio.sleep(0.01)
    if STATE["raise"]:
        raise RuntimeError("model down")
    return STATE["reply"]


def _install_stubs() -> tuple[Any, Any]:
    saved = (llm.stream_chat, llm.complete)
    llm.stream_chat = _scripted_stream
    llm.complete = _scripted_complete
    return saved


def _restore_stubs(saved: tuple[Any, Any]) -> None:
    llm.stream_chat, llm.complete = saved


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


def run_live(cid: str) -> bool:
    return any(r["conversation_id"] == cid for r in j("GET", "/runs"))


def conv(cid: str) -> dict[str, Any]:
    return j("GET", f"/conversations/{cid}")


def changed_events(cid: str) -> list[dict[str, Any]]:
    async def pull() -> list[str]:
        gen = topic.subscribe(0)
        blocks: list[str] = []
        try:
            while True:
                blocks.append(await asyncio.wait_for(gen.__anext__(), timeout=0.5))
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            await gen.aclose()
        return blocks

    out = []
    for block in asyncio.run(pull()):
        ev, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if ev == "conversation_changed":
            d = json.loads(data)
            if d["id"] == cid:
                out.append(d)
    return out


def reset() -> None:
    GATE["open"] = True
    STATE.update(calls=0, prompts=[], ctx=[], reply=DEFAULT_REPLY)
    STATE["raise"] = False


LONG = "Help me plan a long weekend trip to Lisbon with two kids and a tight budget please"


def test_placeholder_and_clean() -> None:
    check(titles.placeholder("short one") == "short one", "short text unchanged")
    p = titles.placeholder(LONG)
    check(p.endswith("…") and len(p) <= 49 and not p[:-1].endswith(" "), f"cut at a word: {p!r}")
    check(LONG.startswith(p[:-1]) and LONG[len(p[:-1])] == " ", "the cut falls on a space")
    check(titles.placeholder("x" * 80) == "x" * 48 + "…", "an unbroken token hard-cuts")
    check(titles.placeholder("   ") == "New chat", "empty is New chat")
    check(titles.clean("<think>a</think>\n\"Title: Hello there.\"\nmore") == "Hello there", "think, quotes, label, period")
    check(titles.clean("**Plan**\nsecond") == "Plan", "first line, markdown stripped")
    check(len(titles.clean("word " * 40)) <= 60, "clamped")
    check(titles.clean("<think>only</think>") == "", "nothing usable is empty")


def test_a_token_in_a_message_is_stripped_before_the_title() -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    seen: list[str] = []

    async def fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "learn", **kw: Any) -> str:
        seen.append(messages[-1]["content"])
        return "Notes"

    real = llm.complete
    llm.complete = fake  # type: ignore[assignment]
    try:
        out = asyncio.run(titles.generate({}, "m", [f"please look at {pat}"]))
    finally:
        llm.complete = real  # type: ignore[assignment]
    check(out == "Notes", "the title still comes back")
    check(pat not in seen[0] and "[github-pat]" in seen[0], "a token in the message is stripped before the title model")


def test_happy_path() -> None:
    reset()
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": LONG})
    wait_until(lambda: conv(cid)["settings"].get("titleSource") == "auto", "the model title")
    c = conv(cid)
    check(c["title"] == "Trip planning for Lisbon", f"cleaned model title, got {c['title']!r}")
    check(changed_events(cid)[-1]["title"] == c["title"], "conversation_changed was published")
    kind, prompt, effort, has_deadline = STATE["prompts"][0]
    check(kind == "title" and effort == "low" and has_deadline, "kind title, low effort, deadline")
    check("hello" not in prompt.replace("Help me", ""), "no assistant text in the prompt")
    check(STATE["ctx"][0].get("conversation_id") == cid, "usage context set inside the task")


def test_detached() -> None:
    reset()
    GATE["open"] = False
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": LONG})
    wait_until(lambda: not run_live(cid), "the run to end")
    wait_until(lambda: STATE["calls"] == 1, "the title call to start")
    r = client.post(f"/conversations/{cid}/chat", json={"content": "and another"})
    check(r.status_code == 200, f"second message accepted while the title call is pending, got {r.status_code}")
    GATE["open"] = True
    wait_until(lambda: conv(cid)["settings"].get("titleSource") == "auto", "the title")


def test_rename_race() -> None:
    reset()
    GATE["open"] = False
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": LONG})
    wait_until(lambda: STATE["calls"] == 1, "the title call to start")
    wait_until(lambda: not run_live(cid), "the run to end")
    j("PATCH", f"/conversations/{cid}", {"title": "My own name"})
    GATE["open"] = True
    time.sleep(0.5)
    c = conv(cid)
    check(c["title"] == "My own name" and c["settings"]["titleSource"] == "user", "the user's title survives")
    cid2 = j("POST", "/conversations", {})["id"]
    t = conv(cid2)["title"]
    j("PATCH", f"/conversations/{cid2}", {"title": t})
    check("titleSource" not in conv(cid2)["settings"], "unchanged title is not the user's")


def test_failure_and_off() -> None:
    reset()
    STATE["raise"] = True
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": LONG})
    wait_until(lambda: STATE["calls"] == 1, "the title call")
    wait_until(lambda: not run_live(cid), "the run to end")
    time.sleep(0.3)
    check(conv(cid)["title"] == titles.placeholder(LONG), "placeholder stays on failure")
    reset()
    j("POST", f"/conversations/{cid}/chat", {"content": "and a second turn"})
    wait_until(lambda: conv(cid)["settings"].get("titleSource") == "auto", "a later turn retries the failed title")
    check(conv(cid)["title"] == "Trip planning for Lisbon", "retried title replaces the placeholder")
    reset()
    client.put("/settings", json={"autoTitle": False})
    try:
        cid = j("POST", "/conversations", {})["id"]
        j("POST", f"/conversations/{cid}/chat", {"content": LONG})
        wait_until(lambda: not run_live(cid), "the run to end")
        time.sleep(0.3)
        check(STATE["calls"] == 0, "no title call with autoTitle off")
    finally:
        client.put("/settings", json={"autoTitle": True})


def test_regenerate() -> None:
    reset()
    cid = j("POST", "/conversations", {})["id"]
    j("POST", f"/conversations/{cid}/chat", {"content": LONG})
    wait_until(lambda: conv(cid)["settings"].get("titleSource") == "auto", "auto title")
    j("PATCH", f"/conversations/{cid}", {"title": "typed"})
    STATE["prompts"].clear()
    STATE["reply"] = "Fresh title"
    out = j("POST", f"/conversations/{cid}/title")
    check(out["title"] == "Fresh title" and out["settings"]["titleSource"] == "auto", "regenerate replaces and resets marker")
    check("hello" not in STATE["prompts"][0][1], "user messages only")
    STATE["raise"] = True
    j("POST", f"/conversations/{cid}/title", expect=502)
    check(conv(cid)["title"] == "Fresh title", "title intact after failure")
    j("POST", "/conversations/nope/title", expect=404)


TESTS = [test_placeholder_and_clean, test_a_token_in_a_message_is_stripped_before_the_title,
         test_happy_path, test_detached, test_rename_race, test_failure_and_off, test_regenerate]

if __name__ == "__main__":
    failures = 0
    _install_stubs()
    with client:
        client.put("/settings", json={"autoLearn": False, "autoTitle": True, "baseUrl": ""})
        for t in TESTS:
            try:
                t()
                print(f"ok   {t.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed")
    sys.exit(1 if failures else 0)
