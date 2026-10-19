"""The input, offer, report, file and review hooks (design doc 5.2, 5.3, 5.8, 5.9, 5.10): turn.accept, turn.start, tools.offer,
worker.report, file.written and review.accepted (notify is in test_extension_api).

Run: python -m pytest backend/tests/test_planned_hooks.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="plannedhooks-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, workers as workers_mod  # noqa: E402
from personal_os.chat_files import ChatFiles  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.extensions import hooks as hooks_mod  # noqa: E402
from personal_os.extensions.hooks import Deny, HookBus  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SEEN_TOOLS: list[list[str]] = []


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    SEEN_TOOLS.append([t["function"]["name"] for t in tools or []])
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _app() -> Any:
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "autoTitle": False,
                                      "followUps": False, "learnStyle": False})
        yield


@pytest.fixture(autouse=True)
def _llm(monkeypatch: pytest.MonkeyPatch) -> None:
    SEEN_TOOLS.clear()
    monkeypatch.setattr(llm, "stream_chat", _fake)


def sub(hook: str, fn: Any, bus: HookBus | None = None, **kw: Any) -> Any:
    return (bus or appmod.host.bus).subscribe(hook, fn, owner="test", order=1, **kw)


def wait(pred: Callable[[], Any], what: str, timeout: float = 10.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def new_chat() -> str:
    return client.post("/conversations", json={"title": "t"}).json()["id"]


def send(cid: str, text: str, **extra: Any) -> Any:
    return client.post(f"/conversations/{cid}/chat", json={"content": text, **extra})


def settled(cid: str) -> list[dict[str, Any]]:
    wait(lambda: not appmod.bus.live(cid), "the reply to end")
    return appmod.convos.get(cid)["messages"]


# ---- turn.accept ----
def test_turn_accept_rewrites_the_message_the_run_stores() -> None:
    seen: list[Any] = []
    h = sub("turn.accept", lambda p: (seen.append(p), {**p, "content": p["content"].replace("secret", "[x]")})[1])
    try:
        cid = new_chat()
        assert send(cid, "the secret word", origin="telegram").status_code == 200
        user = settled(cid)[0]
    finally:
        h.dispose()
    assert user["content"] == "the [x] word"
    assert seen == [{"conversation_id": cid, "origin": "telegram", "content": "the secret word", "attachments": []}]


def test_turn_accept_deny_refuses_the_message_before_anything_is_stored() -> None:
    h = sub("turn.accept", lambda p: Deny("no card numbers") if "4111" in p["content"] else None)
    try:
        cid = new_chat()
        r = send(cid, "my card is 4111")
        assert r.status_code == 403 and r.json()["detail"] == "no card numbers"
        assert appmod.convos.get(cid)["messages"] == [] and not appmod.bus.live(cid)
        assert send(cid, "hello").status_code == 200
    finally:
        h.dispose()
    assert settled(cid)[0]["content"] == "hello"


def test_a_turn_accept_handler_returning_no_content_keeps_the_typed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks_mod, "STRICT", False)  # the contract check would stop a non-conforming handler first
    h = sub("turn.accept", lambda p: {"conversation_id": p["conversation_id"]})
    try:
        cid = new_chat()
        assert send(cid, "hello").status_code == 200
        user = settled(cid)[0]
    finally:
        h.dispose()
    assert user["content"] == "hello"


def test_a_rewrite_meets_the_length_limit_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appmod, "_message_too_long", lambda text, *_: "too long" if len(text) > 10 else None)
    h = sub("turn.accept", lambda p: {**p, "content": p["content"] * 3})
    try:
        cid = new_chat()
        r = send(cid, "abcd")
    finally:
        h.dispose()
    assert r.status_code == 413 and r.json()["detail"] == "too long" and appmod.convos.get(cid)["messages"] == []


def test_a_slow_turn_accept_handler_does_not_let_two_messages_start_two_runs() -> None:
    async def slow(payload: Any) -> None:
        await asyncio.sleep(0.3)
    h = sub("turn.accept", slow)
    try:
        cid = new_chat()
        codes: list[int] = []
        threads = [threading.Thread(target=lambda: codes.append(send(cid, "hi").status_code)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        messages = settled(cid)
    finally:
        h.dispose()
    assert sorted(codes) == [200, 409] and [m["role"] for m in messages] == ["user", "assistant"]


def test_an_internal_turn_is_not_offered_to_turn_accept() -> None:
    calls: list[Any] = []
    h = sub("turn.accept", lambda p: calls.append(p))
    try:
        body = appmod.ChatIn(content="wake text", kind="chat_in")
        row = appmod.convos.get(new_chat(), with_messages=False)
        assert asyncio.run(appmod._accept_turn(row, body, [])) is body
        assert asyncio.run(appmod._accept_turn(row, appmod.ChatIn(), [])).content is None
    finally:
        h.dispose()
    assert calls == []


# ---- turn.start ----
def test_turn_start_names_the_stored_user_row() -> None:
    got: list[Any] = []
    h = sub("turn.start", got.append)
    try:
        cid = new_chat()
        send(cid, "hi", origin="telegram")
        user = settled(cid)[0]
    finally:
        h.dispose()
    assert got == [{"conversation_id": cid, "message_id": user["id"], "origin": "telegram"}]


# ---- tools.offer ----
def test_tools_offer_hides_names_and_keeps_the_order() -> None:
    cid = new_chat()
    send(cid, "one")
    settled(cid)
    full = SEEN_TOOLS[-1]
    assert len(full) > 2
    hidden = full[1]
    h = sub("tools.offer", lambda p: {"names": [n for n in p["names"] if n != hidden]})
    try:
        send(cid, "two")
        settled(cid)
    finally:
        h.dispose()
    assert SEEN_TOOLS[-1] == [n for n in full if n != hidden]


def test_tools_offer_cannot_add_a_name() -> None:
    cid = new_chat()
    send(cid, "one")
    settled(cid)
    full = SEEN_TOOLS[-1]
    h = sub("tools.offer", lambda p: {"names": [*p["names"], "not_a_tool"]})
    try:
        send(cid, "two")
        settled(cid)
    finally:
        h.dispose()
    assert SEEN_TOOLS[-1] == full


# ---- worker.report ----
def test_worker_report_notes_reach_the_wake_text() -> None:
    seen: list[Any] = []

    def note(p: dict[str, Any]) -> dict[str, str]:
        seen.append(p)
        return {"text": "checked against the ticket"}
    h = sub("worker.report", note)
    try:
        reports = asyncio.run(appmod._noted_reports([{"id": "w1", "status": "done", "text": "found it " + "x" * 20_000}]))
    finally:
        h.dispose()
    assert reports[0]["notes"] == ["checked against the ticket"]
    assert seen[0]["worker"] == "w1" and len(seen[0]["summary"]) == workers_mod.WAKE_REPORT_CHARS
    text, _ = workers_mod.build_wake(reports)
    assert "Note from an extension: checked against the ticket" in text


def test_a_report_without_handlers_has_no_notes() -> None:
    (r,) = asyncio.run(appmod._noted_reports([{"id": "w1", "status": "done", "text": "ok"}]))
    assert r["notes"] == []
    assert "Note from an extension" not in workers_mod.build_wake([r])[0]


# ---- file.written ----
def run_with_loop(bus: HookBus, fn: Callable[[], Any]) -> None:
    async def go() -> None:
        bus.loop = asyncio.get_running_loop()
        fn()
        await asyncio.sleep(0.1)
    asyncio.run(go())


def test_file_written_fires_for_a_saved_output_and_an_edited_note(tmp_path: Path) -> None:
    bus, got = HookBus(), []
    sub("file.written", got.append, bus)
    files = ChatFiles(Database(tmp_path), tmp_path / "chats", bus)
    run_with_loop(bus, lambda: (files.record_output("c1", tmp_path / "a.txt"), files.record("c1", "note", "doc1", "Plan", "edited")))
    assert got == [{"path": str((tmp_path / "a.txt").resolve()), "owner": "c1", "kind": "output"},
                   {"path": "doc1", "owner": "c1", "kind": "note"}]


def test_file_written_skips_a_record_with_no_chat(tmp_path: Path) -> None:
    bus, got = HookBus(), []
    sub("file.written", got.append, bus)
    run_with_loop(bus, lambda: ChatFiles(Database(tmp_path), None, bus).record(None, "note", "d", "D", "edited"))
    assert got == []


# ---- review.accepted ----
def test_review_accepted_names_the_revision(tmp_path: Path) -> None:
    bus, got = HookBus(), []
    sub("review.accepted", got.append, bus)
    docs = Docs(Database(tmp_path), bus)
    d = docs.create("Plan", "one")
    rev = docs.propose(d["id"], "two")
    run_with_loop(bus, lambda: (docs.accept(rev["id"]), docs.accept(rev["id"])))
    assert got == [{"kind": "doc_revision", "id": rev["id"]}], "a revision is accepted once"

