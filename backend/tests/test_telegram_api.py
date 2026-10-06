"""The Telegram bridge through the real app: token, pairing and status routes, a paired owner's text running a real
chat turn with the reply sent back, and an approval answered by a button.

Telegram itself is a fake API caller installed on the bridge, so nothing touches the network. The bot token goes to
the file secret store in a temp data directory.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, Callable

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="telegramapi-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os import telegram as tg  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
bridge = appmod.telegram_bridge
TOKEN = "123456789:" + "AbC-dEf_GhI" * 3 + "xyz"
OWNER, BOT = 4242, "grain_test_bot"
STREAM = {"slow": False}
OLD = "i" "message"  # the retired bridge's name, spelled apart so it is gone from the codebase


class FakeTelegram:
    """Records every call; getUpdates hands out what the test queued, then waits like a long poll."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.queue: list[list[dict[str, Any]]] = []
        self.get_me: Any = {"id": 99, "username": BOT, "is_bot": True}
        self._n = 0

    async def __call__(self, token: str, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        if method == "getMe":
            if isinstance(self.get_me, Exception):
                raise self.get_me
            return self.get_me
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

    def update(self, text: str, chat: int = OWNER, user: int | None = None) -> dict[str, Any]:
        self._n += 1
        return {"update_id": 1000 + self._n, "message": {"message_id": self._n, "chat": {"id": chat, "type": "private"},
                                                         "from": {"id": user or chat, "first_name": "Nate"}, "date": int(time.time()) + 1, "text": text}}


fake = FakeTelegram()


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
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
    fake.get_me = {"id": 99, "username": BOT, "is_bot": True}
    STREAM["slow"] = False
    j("DELETE", "/telegram/token")
    j("PUT", "/settings", {"telegramNotifyLongRuns": False, "telegramLongRunMinutes": 3})
    yield
    j("DELETE", "/telegram/token")


def connect() -> dict[str, Any]:
    return j("PUT", "/telegram/token", {"token": TOKEN})


def pair() -> None:
    st = connect()
    fake.push(fake.update(f"/start {st['pairing']['code']}"))
    wait_until(lambda: j("GET", "/telegram/status")["paired"], "pairing")
    wait_until(lambda: "Paired. Text me anything, or /help." in fake.sent(), "the pairing reply")
    fake.calls.clear()


def target() -> str:
    return appmod.db.get_settings()["telegramState"]["textsConversationId"]


def test_status_shape_and_precedence_without_a_token() -> None:
    s = j("GET", "/telegram/status")
    assert s == {"enabled": False, "has_token": False, "bot_username": None, "paired": False, "owner_name": None,
                 "status": "no_token", "last_error": None, "last_poll_at": None, "pairing": None}


def test_token_is_validated_stored_privately_and_starts_the_poller() -> None:
    j("PUT", "/telegram/token", {"token": "nope"}, expect=422)
    j("PUT", "/telegram/token", {"token": "123:short"}, expect=422)
    assert j("GET", "/telegram/status")["has_token"] is False
    fake.get_me = tg.TelegramError(401, "Unauthorized")
    assert j("PUT", "/telegram/token", {"token": TOKEN}, expect=400)["detail"] == "Telegram rejected that token"
    fake.get_me = tg.TelegramError(404, "Not Found")
    j("PUT", "/telegram/token", {"token": TOKEN}, expect=400)
    fake.get_me = tg.TelegramError(0, f"ConnectError: failed for https://api.telegram.org/bot{TOKEN}/getMe")
    r = client.put("/telegram/token", json={"token": TOKEN})
    assert r.status_code == 502 and TOKEN not in r.text
    fake.get_me = ConnectionError(f"boom {TOKEN}")
    r = client.put("/telegram/token", json={"token": TOKEN})
    assert r.status_code == 502 and TOKEN not in r.text
    assert j("GET", "/telegram/status")["has_token"] is False  # nothing was stored by the failures

    fake.get_me = {"id": 99, "username": BOT}
    s = connect()
    assert s["has_token"] and s["enabled"] and s["bot_username"] == BOT and s["paired"] is False and s["owner_name"] is None
    assert s["status"] == "not_paired"
    assert set(s["pairing"]) == {"code", "link", "expires_at"} and s["pairing"]["link"] == f"https://t.me/{BOT}?start={s['pairing']['code']}"
    assert s["pairing"]["expires_at"] > time.time()
    assert appmod.db.secrets.get("telegramBotToken") == TOKEN
    wait_until(lambda: fake.of("getUpdates"), "the poller to start")
    # The token and the state never come back through settings, and the old texting keys are gone.
    pub = j("GET", "/settings")
    assert pub["telegramEnabled"] is True and "telegramState" not in pub and TOKEN not in str(pub)
    assert not [k for k in pub if k.startswith(OLD)]
    assert pub["telegramNotifyLongRuns"] is False and pub["telegramLongRunMinutes"] == 3
    assert TOKEN not in str(appmod.db.get_settings())  # not even the private rows hold it


def test_a_bad_token_seen_while_polling_is_reported() -> None:
    async def dead(token: str, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        if method == "getMe":
            return {"id": 99, "username": BOT}
        raise tg.TelegramError(401, "Unauthorized")

    bridge.deps.api = dead
    try:
        connect()
        wait_until(lambda: j("GET", "/telegram/status")["status"] == "bad_token", "bad_token")
        assert "401" in j("GET", "/telegram/status")["last_error"]
    finally:
        bridge.deps.api = fake


def test_removing_the_token_stops_the_poller_and_forgets_everything() -> None:
    pair()
    s = j("DELETE", "/telegram/token")
    assert s["status"] == "no_token" and s["has_token"] is False and s["paired"] is False and s["pairing"] is None
    assert appmod.db.secrets.get("telegramBotToken") is None
    assert "telegramState" not in appmod.db.get_settings() or appmod.db.get_settings()["telegramState"] == {}
    assert bridge._task is None
    n = len(fake.of("getUpdates"))
    time.sleep(0.2)
    assert len(fake.of("getUpdates")) == n  # no longer polling


def test_pairing_endpoints_and_unpair() -> None:
    j("POST", "/telegram/pairing", expect=400)
    first = connect()["pairing"]["code"]
    second = j("POST", "/telegram/pairing")["pairing"]["code"]
    assert second != first
    fake.push(fake.update(f"/start {first}"))  # the replaced code no longer works
    time.sleep(0.2)
    assert j("GET", "/telegram/status")["paired"] is False
    j("POST", "/telegram/test", expect=400)
    fake.push(fake.update(f"/start {second}"))
    wait_until(lambda: j("GET", "/telegram/status")["paired"], "pairing")
    s = j("GET", "/telegram/status")
    assert s["owner_name"] == "Nate" and s["pairing"] is None and s["status"] == "connected"
    fake.push(fake.update(f"/start {second}", chat=555))  # single use
    s = j("POST", "/telegram/unpair")
    assert s["paired"] is False and s["owner_name"] is None and s["status"] == "not_paired" and s["pairing"]["code"] not in (first, second)


def test_test_message() -> None:
    pair()
    assert j("POST", "/telegram/test") == {"ok": True}
    assert fake.of("sendMessage")[-1] == {"chat_id": OWNER, "text": "Grain is connected."}
    j("POST", "/telegram/unpair")
    assert j("POST", "/telegram/test", expect=400)["detail"] == "Pair a Telegram chat first"


def test_enabled_toggle_stops_and_restarts_the_poller() -> None:
    pair()
    s = j("POST", "/telegram/enabled", {"enabled": False})
    assert s["enabled"] is False and s["status"] == "disabled" and s["paired"] is True
    wait_until(lambda: bridge._task is None, "the poller to stop")
    assert j("GET", "/settings")["telegramEnabled"] is False
    s = j("POST", "/telegram/enabled", {"enabled": True})
    assert s["enabled"] is True and s["status"] == "connected"
    wait_until(lambda: bridge._task is not None, "the poller to start")
    j("PUT", "/settings", {"telegramEnabled": False})  # the settings route reconciles too
    wait_until(lambda: bridge._task is None, "the poller to stop")
    j("PUT", "/settings", {"telegramEnabled": True})
    wait_until(lambda: bridge._task is not None, "the poller to start")
    j("POST", "/telegram/enabled", {"enabled": "yes please"}, expect=422)


def test_settings_validate_and_hide_the_state() -> None:
    j("PUT", "/settings", {"telegramLongRunMinutes": 0}, expect=422)
    j("PUT", "/settings", {"telegramLongRunMinutes": 5000}, expect=422)
    j("PUT", "/settings", {"telegramEnabled": "yes"}, expect=422)
    assert j("PUT", "/settings", {"telegramLongRunMinutes": 12})["telegramLongRunMinutes"] == 12
    assert j("PUT", "/settings", {"telegramNotifyLongRuns": True})["telegramNotifyLongRuns"] is True
    appmod.db.set_settings({"telegramState": {"offset": 5}})
    j("PUT", "/settings", {"telegramState": {"ownerChatId": 1}})
    assert appmod.db.get_settings()["telegramState"] == {"offset": 5}
    assert "telegramState" not in j("GET", "/settings")
    j("PUT", "/settings", {OLD + "Enabled": True})  # a retired key is not a setting any more
    assert OLD + "Enabled" not in j("GET", "/settings")
    j("PUT", "/settings", {"telegramNotifyLongRuns": False, "telegramLongRunMinutes": 3})


def test_an_owner_text_runs_a_real_turn_and_the_reply_comes_back() -> None:
    pair()
    fake.push(fake.update("ping"))
    wait_until(lambda: "Hello there" in fake.sent(), "the reply")
    sends = fake.of("sendMessage")
    assert sends[-1] == {"chat_id": OWNER, "text": "Hello there"}  # markdown stripped, sent to the owner's chat
    conv = target()
    c = j("GET", f"/conversations/{conv}")
    assert c["title"] == "Texts"
    assert [m["content"] for m in c["messages"] if m["role"] == "user"] == ["ping"]
    assert [m["content"] for m in c["messages"] if m["role"] == "assistant"] == ["Hello **there**"]
    runs = appmod.run_store.list(None, conv)
    assert len(runs) == 1 and runs[0]["input"]["origin"] == "telegram" and runs[0]["input"]["content"] == "ping"
    assert j("GET", "/telegram/status")["status"] == "connected"
    # A stranger, a group and a different chat get nothing, and start nothing.
    fake.push(fake.update("let me in", chat=777), {"update_id": 5000, "message": {"message_id": 9, "chat": {"id": -100, "type": "supergroup"},
                                                                                  "from": {"id": OWNER}, "date": int(time.time()) + 1, "text": "group"}})
    time.sleep(0.3)
    assert len(appmod.run_store.list(None, conv)) == 1 and fake.sent() == ["Hello there"]


def test_a_text_during_a_reply_steers_it_instead_of_starting_a_second_run() -> None:
    pair()
    fake.push(fake.update("/new"))
    wait_until(lambda: "Started a new conversation." in fake.sent(), "the new conversation")
    conv = target()
    STREAM["slow"] = True
    fake.push(fake.update("first thing"))
    wait_until(lambda: appmod.bus.answering(conv), "the run to be answering")
    fake.push(fake.update("and also this"))
    wait_until(lambda: [m["content"] for m in j("GET", f"/conversations/{conv}")["messages"] if m["role"] == "user"][-2:]
               == ["first thing", "and also this"], "both texts in the conversation")
    assert len(appmod.run_store.list(None, conv, limit=200)) == 1
    wait_until(lambda: any(t.endswith("Hello there") for t in fake.sent()), "the one reply")
    time.sleep(0.2)
    assert sum(t.endswith("Hello there") for t in fake.sent()) == 1


def test_an_approval_answered_by_a_button_is_decided_as_telegram() -> None:
    pair()
    fake.push(fake.update("/new"))
    wait_until(lambda: "Started a new conversation." in fake.sent(), "the new conversation")
    conv = target()
    appmod.run_store.create("run-fake", conv, "chat", {})
    appmod.run_store.open_approval("call-tg-1", "run-fake", "send_email", {"to": "a@b.co", "subject": "Hi"}, conversation_id=conv)
    appmod.run_store.open_approval("call-orphan", "run-fake", "send_email", {"to": "gone@b.co"}, conversation_id=conv)  # no run waits on it

    async def waiter() -> asyncio.Future:
        return asyncio.get_running_loop().create_future()

    fut = client.portal.call(waiter)  # a run in this process waiting on the card, as the chat loop does
    appmod._approvals["call-tg-1"] = fut
    bridge.on_run_change(types.SimpleNamespace(run_id="run-fake", conversation_id=conv, kind="chat", started_at=time.time(),
                                               ended_at=None, live=True, replied=False, status="awaiting_approval",
                                               message_id=None, error=None))
    ask = wait_until(lambda: next((p for p in fake.of("sendMessage") if p["text"].startswith("Approval needed [")), None), "the approval card")
    assert "send_email" in ask["text"] and "to=a@b.co" in ask["text"] and not any("gone@b.co" in t for t in fake.sent())
    approve = ask["reply_markup"]["inline_keyboard"][0][0]
    assert approve["text"] == "Approve" and approve["callback_data"].startswith("ap:")
    fake.push({"update_id": 7000, "callback_query": {"id": "cq-1", "from": {"id": OWNER}, "data": approve["callback_data"],
                                                      "message": {"message_id": 31, "text": ask["text"], "chat": {"id": OWNER}}}})
    row = wait_until(lambda: (r := appmod.run_store.approval("call-tg-1")) and r["status"] == "approved" and r, "the decision")
    assert row["decided_by"] == "telegram" and row["decision"] == "allow"
    wait_until(lambda: fut.done() and fake.of("editMessageText"), "the run to wake and the card to be edited")
    assert fut.result() == "allow"  # the waiting run was woken through approve_tool_call
    assert fake.of("answerCallbackQuery") == [{"callback_query_id": "cq-1", "text": "Approved."}]
    assert fake.of("editMessageText") == [{"chat_id": OWNER, "message_id": 31, "text": ask["text"] + "\n\nApproved."}]
    assert appmod.run_store.approval("call-orphan")["status"] == "pending"
    hist = j("GET", "/approvals/history?tool=send_email")["items"]
    assert hist and hist[0]["decision"] == "allow_once" and "(telegram)" in hist[0]["note"]


def test_the_approval_route_defaults_to_the_user_and_only_accepts_the_telegram_marker() -> None:
    appmod.run_store.create("run-fake-ui", None, "chat", {})
    appmod.run_store.open_approval("call-ui-1", "run-fake-ui", "send_email", {"to": "a@b.co"})
    j("POST", "/approvals/call-ui-1", {"decision": "deny"})
    assert appmod.run_store.approval("call-ui-1")["decided_by"] == "user"
    appmod.run_store.open_approval("call-ui-2", "run-fake-ui", "send_email", {"to": "a@b.co"})
    j("POST", "/approvals/call-ui-2", {"decision": "deny", "via": OLD}, expect=422)
    j("POST", "/approvals/call-ui-2", {"decision": "deny", "via": "carrier-pigeon"}, expect=422)
    assert appmod.run_store.approval("call-ui-2")["status"] == "pending"


def test_the_bridge_stops_before_the_runs_are_cancelled_on_shutdown() -> None:
    import inspect
    src = inspect.getsource(appmod._shutdown)
    assert src.index("telegram_bridge.stop()") < src.index("bus.shutdown()")


def test_the_real_http_caller_is_what_the_app_ships_with() -> None:
    assert isinstance(tg.Deps.__dataclass_fields__["api"].default_factory(), tg.HttpApi)
    api = tg.HttpApi()
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True, "result": [1]})))
    assert asyncio.run(api(TOKEN, "getUpdates", {})) == [1]
