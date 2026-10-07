"""The Telegram chat inside Grain: the Texts conversation is flagged (settings.telegram) and exists from pairing, a message typed
in it from Grain runs as a Telegram turn and is mirrored to the phone ("From Grain: ..."), and a finished worker's reply lands
in it too. Everything goes to the owner's chat and nowhere else; internal turns are never mirrored.

Telegram is a fake API caller installed on the bridge, so nothing touches the network; the model is scripted.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="telegramchat-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
bridge = appmod.telegram_bridge
TOKEN = "123456789:" + "AbC-dEf_GhI" * 3 + "xyz"
OWNER, BOT = 4242, "grain_test_bot"
STREAM = {"slow": False}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (30, 40, 50)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.queue: list[list[dict[str, Any]]] = []
        self._n = 0

    async def __call__(self, token: str, method: str, params: dict[str, Any], timeout: float | None = None, files: Any = None) -> Any:
        if method == "getMe":
            return {"id": 99, "username": BOT, "is_bot": True}
        self.calls.append((method, params))
        if method == "getUpdates":
            if self.queue:
                return self.queue.pop(0)
            await asyncio.sleep(0.02)
            return []
        return True

    def push(self, *updates: dict[str, Any]) -> None:
        self.queue.append(list(updates))

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in list(self.calls) if m == method]

    def sent(self) -> list[str]:
        return [p["text"] for p in self.of("sendMessage")]

    def outbound(self) -> list[tuple[str, dict[str, Any]]]:
        return [(m, p) for m, p in list(self.calls) if m not in ("getUpdates", "sendChatAction")]

    def update(self, text: str, chat: int = OWNER) -> dict[str, Any]:
        self._n += 1
        return {"update_id": 3000 + self._n, "message": {"message_id": self._n, "chat": {"id": chat, "type": "private"},
                                                         "from": {"id": chat, "first_name": "Nate"}, "date": int(time.time()) + 1, "text": text}}


fake = FakeTelegram()


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    if STREAM["slow"]:
        for i in range(30):
            await asyncio.sleep(0.05)
            yield {"type": "delta", "text": "." if i < 29 else "done"}
    yield {"type": "delta", "text": "Hello **there**"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat, appmod.learner.submit, bridge.deps.api
    llm.stream_chat = _scripted
    appmod.learner.submit = lambda job: None  # type: ignore[method-assign]
    bridge.deps.api = fake
    with client:
        client.put("/settings", json={"autoLearn": False, "learnStyle": False, "baseUrl": "", "permissionMode": "manual"})
        yield
    llm.stream_chat, appmod.learner.submit, bridge.deps.api = real


@pytest.fixture(autouse=True)
def _reset():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    fake.calls.clear()
    fake.queue.clear()
    STREAM["slow"] = False
    j("DELETE", "/telegram/token")
    j("PUT", "/settings", {"telegramNotifyLongRuns": False, "telegramPushWorkerResults": False})
    yield
    j("DELETE", "/telegram/token")


def pair() -> str:
    st = j("PUT", "/telegram/token", {"token": TOKEN})
    fake.push(fake.update(f"/start {st['pairing']['code']}"))
    wait_until(lambda: j("GET", "/telegram/status")["paired"], "pairing")
    wait_until(lambda: "Paired. Text me anything, or /help." in fake.sent(), "the pairing reply")
    fake.calls.clear()
    return target()


def target() -> str:
    return appmod.db.get_settings()["telegramState"]["textsConversationId"]


def flag(cid: str) -> Any:
    return j("GET", f"/conversations/{cid}")["settings"].get("telegram")


def doc() -> dict[str, Any]:
    return appmod._store_upload(None, "shot.png", "image/png", PNG)


def owner_only() -> None:
    for m, p in fake.outbound():
        assert p.get("chat_id") == OWNER, f"{m} went to {p.get('chat_id')}"


def messages(cid: str) -> list[dict[str, Any]]:
    return j("GET", f"/conversations/{cid}")["messages"]


# ---------------------------------------------------------------- the flag

def test_pairing_creates_the_flagged_telegram_chat_and_lists_it() -> None:
    published: list[tuple[str, Any]] = []
    real = appmod.events.publish
    appmod.events.publish = lambda kind, payload=None, *a, **k: (published.append((kind, payload)), real(kind, payload, *a, **k))[1]  # type: ignore[method-assign]
    try:
        cid = pair()
    finally:
        appmod.events.publish = real  # type: ignore[method-assign]
    assert ("conversation_changed", {"id": cid, "reload": True}) in published  # the sidebar re-reads it
    listed = {c["id"]: c for c in j("GET", "/conversations")}
    assert cid in listed and listed[cid]["settings"]["telegram"] is True and listed[cid]["title"] == "Telegram"


def test_new_moves_the_flag_to_the_new_conversation() -> None:
    old = pair()
    fake.push(fake.update("/new"))
    wait_until(lambda: "Started a new conversation." in fake.sent(), "/new")
    new = target()
    assert new != old and flag(new) is True and flag(old) is False
    assert j("GET", f"/conversations/{old}")["id"] == old  # the old one stays as an ordinary chat


def test_unpair_and_removing_the_token_clear_the_flag_and_pairing_again_sets_it() -> None:
    cid = pair()
    j("POST", "/telegram/unpair")
    assert flag(cid) is False
    code = j("GET", "/telegram/status")["pairing"]["code"]
    fake.push(fake.update(f"/start {code}"))
    wait_until(lambda: j("GET", "/telegram/status")["paired"], "pairing again")
    wait_until(lambda: flag(cid) is True, "the flag back")
    j("DELETE", "/telegram/token")
    assert flag(cid) is False


def test_starting_the_poller_backfills_a_chat_made_before_the_flag_existed() -> None:
    cid = pair()
    j("POST", "/telegram/enabled", {"enabled": False})
    wait_until(lambda: bridge._task is None, "the poller to stop")
    appmod.convos.update(cid, {"settings": {"telegram": False}})
    j("POST", "/telegram/enabled", {"enabled": True})
    wait_until(lambda: flag(cid) is True, "the backfill")


# ---------------------------------------------------------------- typed in Grain

def test_a_message_typed_in_grain_is_mirrored_and_its_reply_goes_to_the_phone() -> None:
    cid = pair()
    run = j("POST", f"/conversations/{cid}/chat", {"content": "hi from the desk"})
    assert run["run_id"] in bridge._runs.seen
    wait_until(lambda: "Hello <b>there</b>" in fake.sent(), "the reply on the phone")
    assert fake.sent() == ["From Grain: hi from the desk", "Hello <b>there</b>"]  # one mirror, then the reply
    owner_only()
    assert [m["content"] for m in messages(cid) if m["role"] == "user"] == ["hi from the desk"]  # no copy stored: it is the chat's own message


def test_a_file_typed_in_grain_goes_to_the_phone_with_the_text_as_caption() -> None:
    cid = pair()
    a = doc()
    j("POST", f"/conversations/{cid}/chat", {"content": "look", "attachments": [a["id"]]})
    wait_until(lambda: "Hello <b>there</b>" in fake.sent(), "the reply")
    photo = wait_until(lambda: fake.of("sendPhoto"), "the photo")
    assert photo == [{"chat_id": OWNER, "caption": "From Grain: look"}] and "From Grain: look" not in fake.sent()
    owner_only()


def test_from_app_with_a_file_and_no_text_sends_the_file_alone_and_with_nothing_sends_nothing() -> None:
    cid = pair()
    a = doc()
    bridge.from_app(cid, "run-files", "", [a])
    wait_until(lambda: fake.of("sendPhoto"), "the photo")
    assert fake.of("sendPhoto")[0]["caption"] == "From Grain:"
    fake.calls.clear()
    bridge.from_app(cid, "run-empty", "", [])
    time.sleep(0.2)
    assert fake.outbound() == []
    owner_only()


def test_a_message_in_another_conversation_sends_nothing() -> None:
    pair()
    other = j("POST", "/conversations", {"title": "Plain"})["id"]
    j("POST", f"/conversations/{other}/chat", {"content": "just here"})
    wait_until(lambda: any(m["role"] == "assistant" and m["content"] for m in messages(other)), "the reply in Grain")
    time.sleep(0.3)
    assert fake.outbound() == []


def test_unpaired_the_turn_still_runs_and_nothing_is_sent() -> None:
    cid = pair()
    j("POST", "/telegram/unpair")
    fake.calls.clear()
    run = j("POST", f"/conversations/{cid}/chat", {"content": "nobody listening"})
    assert run["run_id"] not in bridge._runs.seen
    wait_until(lambda: any(m["role"] == "assistant" and m["content"] for m in messages(cid)), "the reply in Grain")
    time.sleep(0.3)
    assert fake.outbound() == []


def test_a_turn_that_came_from_telegram_is_not_echoed() -> None:
    cid = pair()
    seen: list[Any] = []
    real = bridge.from_app
    bridge.from_app = lambda *a, **k: seen.append(a)  # type: ignore[method-assign]
    try:
        j("POST", f"/conversations/{cid}/chat", {"content": "from the phone", "origin": "telegram"})
        wait_until(lambda: any(m["role"] == "assistant" and m["content"] for m in messages(cid)), "the reply")
    finally:
        bridge.from_app = real  # type: ignore[method-assign]
    assert seen == [] and not any(t.startswith("From Grain") for t in fake.sent())


def test_a_text_from_the_phone_is_not_echoed_back() -> None:
    pair()
    fake.push(fake.update("ping"))
    wait_until(lambda: "Hello <b>there</b>" in fake.sent(), "the reply")
    time.sleep(0.3)  # a late echo would land after the reply
    assert fake.sent() == ["Hello <b>there</b>"]
    owner_only()


def test_a_steer_typed_in_grain_is_mirrored_once_and_a_phone_steer_is_not() -> None:
    cid = pair()
    STREAM["slow"] = True
    j("POST", f"/conversations/{cid}/chat", {"content": "first"})
    wait_until(lambda: appmod.bus.answering(cid), "the run to be answering")
    j("POST", f"/conversations/{cid}/steer", {"content": "and this"})
    wait_until(lambda: "From Grain: and this" in fake.sent(), "the mirrored steer")
    fake.push(fake.update("and from the phone"))
    wait_until(lambda: any(m["content"] == "and from the phone" for m in messages(cid)), "the phone steer stored")
    wait_until(lambda: any(t.endswith("Hello <b>there</b>") for t in fake.sent()), "the one reply")
    time.sleep(0.2)
    assert [t for t in fake.sent() if t.startswith("From Grain")] == ["From Grain: first", "From Grain: and this"]
    assert not any("and from the phone" in t for t in fake.sent())
    owner_only()


def test_internal_turns_are_never_mirrored() -> None:
    cid = pair()
    seen: list[Any] = []
    real = bridge.from_app
    bridge.from_app = lambda *a, **k: seen.append(a)  # type: ignore[method-assign]
    try:
        j("POST", f"/conversations/{cid}/chat", {"content": "keep going", "wake": {"ids": ["w1"]}}, expect=400)
        j("POST", f"/conversations/{cid}/chat", {"content": "internal note", "kind": "nudge"})
        wait_until(lambda: not appmod.bus.answering(cid), "the run to end")
    finally:
        bridge.from_app = real  # type: ignore[method-assign]
    assert seen == []
    time.sleep(0.2)
    assert not any(t.startswith("From Grain") for t in fake.sent())


# ---------------------------------------------------------------- worker results

def _kinds(cid: str) -> list[Any]:
    with appmod.db.tx() as c:
        return [r["kind"] for r in c.execute("SELECT kind FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)).fetchall()]


def test_a_worker_reply_from_another_chat_lands_in_the_telegram_chat_and_the_phone_once() -> None:
    texts = pair()
    j("PUT", "/settings", {"telegramPushWorkerResults": True})
    src = j("POST", "/conversations", {"title": "Research"})["id"]
    a = doc()
    published: list[tuple[str, Any]] = []
    real = appmod.events.publish
    appmod.events.publish = lambda kind, payload=None, *a_, **k: (published.append((kind, payload)), real(kind, payload, *a_, **k))[1]  # type: ignore[method-assign]
    try:
        appmod._push_wake_reply("Found 3 papers", [a], src)
        msg = wait_until(lambda: next((m for m in messages(texts) if m["role"] == "assistant"), None), "the update in the chat")
        wait_until(lambda: ("conversation_changed", {"id": texts, "reload": True}) in published, "conversation_changed")
    finally:
        appmod.events.publish = real  # type: ignore[method-assign]
    assert msg["content"] == "Update from “Research”:\n\nFound 3 papers" and [x["id"] for x in msg["attachments"]] == [a["id"]]
    assert _kinds(texts) == [None]  # visible, not an internal row
    wait_until(lambda: fake.of("sendPhoto"), "the phone push")
    assert fake.of("sendPhoto") == [{"chat_id": OWNER, "caption": "Found 3 papers"}] and fake.sent() == []  # once, not twice
    owner_only()


def test_a_worker_reply_in_the_telegram_chat_itself_is_not_added_twice_and_off_means_off() -> None:
    texts = pair()
    j("PUT", "/settings", {"telegramPushWorkerResults": True})
    appmod._push_wake_reply("same chat", None, texts)
    wait_until(lambda: "same chat" in fake.sent(), "the phone push")
    assert messages(texts) == []  # the reply row is the wake turn's own
    j("PUT", "/settings", {"telegramPushWorkerResults": False})
    src = j("POST", "/conversations", {"title": "Other"})["id"]
    fake.calls.clear()
    appmod._push_wake_reply("quiet", None, src)
    time.sleep(0.3)
    assert messages(texts) == [] and fake.outbound() == []
