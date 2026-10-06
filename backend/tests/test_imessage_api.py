"""The iMessage bridge through the real app: settings, status and test routes, a text running a real chat turn,
and an approval answered by text.

The Messages database is a fixture file named by GRAIN_IMESSAGE_CHAT_DB and every send goes to a fake runner
installed on the bridge, so no real Messages database is read and no script runs.
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

_DATA = tempfile.mkdtemp(prefix="imessageapi-")
os.environ.setdefault("PERSONAL_OS_DATA_DIR", _DATA)
os.environ["GRAIN_IMESSAGE_CHAT_DB"] = str(Path(_DATA) / "fixture-chat.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_imessage import BODY, MARK, ME, SELF_GUID, STRANGER, ChatDB  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import imessage as im  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
bridge = appmod.imessage_bridge
chat_db = ChatDB(Path(os.environ["GRAIN_IMESSAGE_CHAT_DB"]))
SENT: list[list[str]] = []
OPENED: list[int] = []
STREAM = {"slow": False}


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    if STREAM["slow"]:
        for i in range(30):
            await asyncio.sleep(0.05)
            yield {"type": "delta", "text": "." if i < 29 else "done"}
    yield {"type": "delta", "text": "Hello **there**"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _fake_runner(argv: list[str]) -> tuple[int, str]:
    SENT.append(argv)
    return 0, ""


def texts() -> list[str]:
    return [a[4].removeprefix(MARK) for a in SENT]


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
    real = llm.stream_chat, appmod.learner.submit, im.open_full_disk_access
    llm.stream_chat = _scripted
    appmod.learner.submit = lambda job: None  # type: ignore[method-assign]
    im.open_full_disk_access = lambda: OPENED.append(1) or True  # type: ignore[assignment]
    bridge.deps.runner = _fake_runner
    bridge.poll_seconds, bridge.send_gap = 0.05, 0
    bridge.record_delays = (0.0,)
    with client:
        client.put("/settings", json={"autoLearn": False, "learnStyle": False, "baseUrl": ""})
        yield
    llm.stream_chat, appmod.learner.submit, im.open_full_disk_access = real


@pytest.fixture(autouse=True)
def _reset():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    SENT.clear()
    OPENED.clear()
    appmod.db.set_settings({"imessageSelfChatGuid": None, "imessageReplyMarker": MARK})
    STREAM["slow"] = False
    yield


def enable() -> None:
    j("PUT", "/settings", {"imessageEnabled": True, "imessageHandles": ["(555) 123-4567"]})
    wait_until(lambda: bridge.status()["status"] == "running" and (appmod.db.get_settings().get("imessageState") or {}).get("cursor") is not None,
               "the poller's first poll")


def target() -> str:
    return bridge.status()["target_conversation"]["id"]


def test_settings_validate_normalize_and_hide_the_state() -> None:
    s = j("PUT", "/settings", {"imessageHandles": ["(555) 123-4567", "+15551234567", " Nate@Example.COM ", "chat123"]})
    assert s["imessageHandles"] == ["+15551234567", "nate@example.com", "chat123"]
    assert s["imessageEnabled"] is False and s["imessageConversationId"] is None and s["imessageLongRunMinutes"] == 3
    assert s["imessageNotifyLongRuns"] is False
    bad = j("PUT", "/settings", {"imessageHandles": [ME, "not a number"]}, expect=422)
    assert "not a number" in bad["detail"]
    j("PUT", "/settings", {"imessageHandles": "+15551234567"}, expect=422)
    j("PUT", "/settings", {"imessageHandles": [5]}, expect=422)
    j("PUT", "/settings", {"imessageConversationId": 7}, expect=422)
    j("PUT", "/settings", {"imessageLongRunMinutes": 0}, expect=422)
    j("PUT", "/settings", {"imessageLongRunMinutes": 5000}, expect=422)
    assert j("GET", "/settings")["imessageHandles"] == ["+15551234567", "nate@example.com", "chat123"]  # a rejected save changed nothing
    assert j("PUT", "/settings", {"imessageConversationId": None})["imessageConversationId"] is None
    assert j("PUT", "/settings", {"imessageLongRunMinutes": 12})["imessageLongRunMinutes"] == 12
    j("PUT", "/settings", {"imessageLongRunMinutes": 3})
    appmod.db.set_settings({"imessageState": {"cursor": 5}})
    j("PUT", "/settings", {"imessageState": {"cursor": 999}})
    assert appmod.db.get_settings()["imessageState"] == {"cursor": 5}
    assert "imessageState" not in j("GET", "/settings") and "imessageState" not in s


def test_status_shape() -> None:
    s = j("GET", "/imessage/status")
    assert set(s) == {"enabled", "running", "status", "fda_ok", "last_poll_at", "last_error", "ignored_count", "last_ignored_at",
                      "target_conversation", "self_chat"}
    assert s["enabled"] is False and s["status"] == "off" and s["running"] is False
    assert s["self_chat"] == {"guid": None, "handle": None}


def test_test_message_only_goes_to_allowlisted_handles() -> None:
    j("PUT", "/settings", {"imessageHandles": [ME]})
    j("POST", "/imessage/test", {"handle": STRANGER}, expect=400)
    assert SENT == []
    assert j("POST", "/imessage/test", {}) == {"ok": True, "to": "handle"}
    assert SENT[-1][2] == im.SCRIPT_PARTICIPANT and SENT[-1][3] == ME and SENT[-1][4] == MARK + "Grain is connected ✅"
    assert j("POST", "/imessage/test", {"handle": "(555) 123-4567"}) == {"ok": True, "to": "handle"}
    j("PUT", "/settings", {"imessageHandles": []})
    assert "note-to-self" in j("POST", "/imessage/test", {}, expect=400)["detail"]


def test_the_self_chat_and_marker_settings_validate() -> None:
    s = j("GET", "/settings")
    assert s["imessageSelfChatGuid"] is None and s["imessageReplyMarker"] == MARK
    assert j("PUT", "/settings", {"imessageSelfChatGuid": f"  {SELF_GUID} "})["imessageSelfChatGuid"] == SELF_GUID
    assert j("PUT", "/settings", {"imessageSelfChatGuid": "  "})["imessageSelfChatGuid"] is None
    assert j("PUT", "/settings", {"imessageSelfChatGuid": SELF_GUID})["imessageSelfChatGuid"] == SELF_GUID
    assert j("PUT", "/settings", {"imessageSelfChatGuid": None})["imessageSelfChatGuid"] is None
    j("PUT", "/settings", {"imessageSelfChatGuid": 7}, expect=422)
    j("PUT", "/settings", {"imessageSelfChatGuid": "x" * 201}, expect=422)
    assert j("PUT", "/settings", {"imessageSelfChatGuid": "x" * 200})["imessageSelfChatGuid"] == "x" * 200
    assert j("PUT", "/settings", {"imessageReplyMarker": "🤖 "})["imessageReplyMarker"] == "🤖 "
    assert j("PUT", "/settings", {"imessageReplyMarker": ""})["imessageReplyMarker"] == ""  # stored; the bridge falls back
    assert j("PUT", "/settings", {"imessageReplyMarker": "x" * 16})["imessageReplyMarker"] == "x" * 16
    j("PUT", "/settings", {"imessageReplyMarker": "x" * 17}, expect=422)
    j("PUT", "/settings", {"imessageReplyMarker": 5}, expect=422)
    j("PUT", "/settings", {"imessageReplyMarker": None}, expect=422)
    assert j("GET", "/settings")["imessageReplyMarker"] == "x" * 16  # a rejected save changed nothing
    j("PUT", "/settings", {"imessageSelfChatGuid": None, "imessageReplyMarker": MARK})


def test_test_message_goes_to_the_self_chat_when_one_is_set() -> None:
    j("PUT", "/settings", {"imessageHandles": [ME], "imessageSelfChatGuid": SELF_GUID})
    assert j("POST", "/imessage/test", {}) == {"ok": True, "to": "self_chat"}
    assert SENT[-1][2] == im.SCRIPT_CHAT and SENT[-1][3] == SELF_GUID and SENT[-1][4] == MARK + "Grain is connected ✅"
    assert j("GET", "/imessage/status")["self_chat"] == {"guid": SELF_GUID, "handle": im.mask_handle(ME)}
    j("PUT", "/settings", {"imessageHandles": []})
    assert j("POST", "/imessage/test", {}) == {"ok": True, "to": "self_chat"}  # the chat is enough; no allowlist needed to send there


def test_self_chats_lists_candidates_without_any_message_text() -> None:
    j("PUT", "/settings", {"imessageHandles": [ME]})
    chat_db.add(ME, BODY, from_me=1)
    chat_db.add(STRANGER, BODY, from_me=1)
    res = j("GET", "/imessage/self-chats")
    assert set(res) == {"chats"} and [c["guid"] for c in res["chats"]] == [SELF_GUID]
    assert set(res["chats"][0]) == {"guid", "handle", "last_activity", "source", "best"}
    assert res["chats"][0]["best"] is True and res["chats"][0]["source"] == "allowlist" and res["chats"][0]["handle"] == "…4567"
    assert "ZEBRA" not in str(res)
    j("PUT", "/settings", {"imessageHandles": []})
    assert j("GET", "/imessage/self-chats") == {"chats": []}


def test_self_chats_needs_full_disk_access_when_the_database_is_unreadable() -> None:
    real = bridge.deps.chat_db_path
    bridge.deps.chat_db_path = str(Path(_DATA) / "nowhere" / "chat.db")
    try:
        assert j("GET", "/imessage/self-chats", expect=409) == {"detail": "needs_full_disk_access"}
    finally:
        bridge.deps.chat_db_path = real


def test_open_fda_route_uses_the_hook() -> None:
    assert j("POST", "/imessage/open-fda") == {"ok": True} and OPENED == [1]


def test_a_text_runs_a_real_turn_and_the_reply_comes_back() -> None:
    enable()
    chat_db.add(ME, "ping")
    wait_until(lambda: "Hello there" in texts(), "the texted reply")
    assert [a[3] for a in SENT] == [f"iMessage;-;{ME}"] and SENT[0][2] == im.SCRIPT_CHAT  # markdown stripped, sent to the same chat
    conv = target()
    c = j("GET", f"/conversations/{conv}")
    assert c["title"] == "Texts"
    assert [m["content"] for m in c["messages"] if m["role"] == "user"] == ["ping"]
    assert [m["content"] for m in c["messages"] if m["role"] == "assistant"] == ["Hello **there**"]
    runs = appmod.run_store.list(None, conv)
    assert len(runs) == 1 and runs[0]["input"]["origin"] == "imessage" and runs[0]["input"]["content"] == "ping"
    st = j("GET", "/imessage/status")
    assert st["enabled"] and st["running"] and st["status"] == "running" and st["fda_ok"] is True
    assert st["target_conversation"]["title"] == "Texts"
    chat_db.add(STRANGER, "let me in")
    wait_until(lambda: j("GET", "/imessage/status")["ignored_count"] >= 1, "the ignored counter")
    assert not any("let me in" in t for t in texts()) and len(appmod.run_store.list(None, conv)) == 1


def test_a_text_during_a_reply_steers_it_instead_of_starting_a_second_run() -> None:
    enable()
    conv = target()
    STREAM["slow"] = True
    before = len(appmod.run_store.list(None, conv, limit=200))
    chat_db.add(ME, "first thing")
    wait_until(lambda: appmod.bus.answering(conv), "the run to be answering")
    chat_db.add(ME, "and also this")
    wait_until(lambda: [m["content"] for m in j("GET", f"/conversations/{conv}")["messages"] if m["role"] == "user"][-2:]
               == ["first thing", "and also this"], "both texts in the conversation")
    assert len(appmod.run_store.list(None, conv, limit=200)) == before + 1
    wait_until(lambda: any(t.endswith("Hello there") for t in texts()), "the one reply")
    assert sum(t.endswith("Hello there") for t in texts()) == 1


def test_an_approval_answered_by_text_is_decided_as_imessage() -> None:
    enable()
    conv = target()
    appmod.run_store.create("run-fake", conv, "chat", {})
    appmod.run_store.open_approval("call-text-1", "run-fake", "send_email", {"to": "a@b.co", "subject": "Hi"}, conversation_id=conv)
    appmod.run_store.open_approval("call-orphan", "run-fake", "send_email", {"to": "gone@b.co"}, conversation_id=conv)  # no run waits on it

    async def waiter() -> asyncio.Future:
        return asyncio.get_running_loop().create_future()

    fut = client.portal.call(waiter)  # a run in this process waiting on the card, as the chat loop does
    appmod._approvals["call-text-1"] = fut
    bridge.on_run_change(types.SimpleNamespace(run_id="run-fake", conversation_id=conv, kind="chat", started_at=time.time(),
                                               ended_at=None, live=True, replied=False, status="awaiting_approval",
                                               message_id=None, error=None))
    wait_until(lambda: any(t.startswith("Approval needed [") for t in texts()), "the approval text")
    ask = next(t for t in texts() if t.startswith("Approval needed ["))
    assert "send_email" in ask and "to=a@b.co" in ask and ask.endswith("Reply yes or no")
    assert not any("gone@b.co" in t for t in texts())  # a card nobody is waiting on is never offered
    chat_db.add(ME, "yes")
    row = wait_until(lambda: (r := appmod.run_store.approval("call-text-1")) and r["status"] == "approved" and r, "the decision")
    assert row["decided_by"] == "imessage" and row["decision"] == "allow"
    wait_until(lambda: fut.done() and "Approved." in texts(), "the run to wake and the confirmation text")  # a beat after the row is written
    assert fut.result() == "allow"  # the waiting run was woken through approve_tool_call
    assert appmod.run_store.approval("call-orphan")["status"] == "pending"
    hist = j("GET", "/approvals/history?tool=send_email")["items"]
    assert hist and hist[0]["decision"] == "allow_once" and "(imessage)" in hist[0]["note"]


def test_the_approval_route_defaults_to_the_user_and_only_accepts_the_imessage_marker() -> None:
    appmod.run_store.create("run-fake-ui", None, "chat", {})
    appmod.run_store.open_approval("call-ui-1", "run-fake-ui", "send_email", {"to": "a@b.co"})
    j("POST", "/approvals/call-ui-1", {"decision": "deny"})
    assert appmod.run_store.approval("call-ui-1")["decided_by"] == "user"
    appmod.run_store.open_approval("call-ui-2", "run-fake-ui", "send_email", {"to": "a@b.co"})
    j("POST", "/approvals/call-ui-2", {"decision": "deny", "via": "carrier-pigeon"}, expect=422)
    assert appmod.run_store.approval("call-ui-2")["status"] == "pending"


def test_turning_it_off_stops_the_poller_and_a_new_enable_skips_history() -> None:
    enable()
    j("PUT", "/settings", {"imessageEnabled": False})
    wait_until(lambda: bridge.status()["status"] == "off", "the poller to stop")
    last = chat_db.add(ME, "sent while it was off")
    j("PUT", "/settings", {"imessageEnabled": True})
    wait_until(lambda: (appmod.db.get_settings().get("imessageState") or {}).get("cursor") == last, "the cursor to jump to the newest row")
    time.sleep(0.3)
    assert not any("sent while it was off" in c for c in [m["content"] for m in j("GET", f"/conversations/{target()}")["messages"]])
    j("PUT", "/settings", {"imessageEnabled": False})
    wait_until(lambda: bridge.status()["status"] == "off", "the poller to stop")


def test_the_bridge_stops_before_the_runs_are_cancelled_on_shutdown() -> None:
    import inspect
    src = inspect.getsource(appmod._shutdown)
    assert src.index("imessage_bridge.stop()") < src.index("bus.shutdown()")
    assert not hasattr(appmod, "_imessage_shutdown")
