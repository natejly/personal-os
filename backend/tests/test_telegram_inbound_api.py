"""Photos through the real app: a photo sent to the bot is downloaded, stored as an upload and attached to the user message
in the Texts conversation; a vision-capable chat model gets the picture as an image_url part and a text-only one does not; and
a file the agent sends with send_files lands on its reply row, the done event and the phone.

Telegram is a fake API caller (and a fake download) installed on the bridge, so nothing touches the network.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="telegraminbound-"))
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
SEEN: list[list[dict[str, Any]]] = []   # the messages of every model request
SCRIPT: list[str] = []                  # per request: 'send' (a send_files call) or 'ok'
STATE: dict[str, Any] = {"send": {}}


def _png() -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (60, 40), (30, 40, 50))
    im.paste((250, 10, 10), (0, 0, 30, 40))
    im.save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
        self.queue: list[list[dict[str, Any]]] = []
        self._n = 0
        self.downloads: list[str] = []

    async def __call__(self, token: str, method: str, params: dict[str, Any], timeout: float | None = None, files: Any = None) -> Any:
        if method == "getMe":
            return {"id": 99, "username": BOT, "is_bot": True}
        self.calls.append((method, params, files))
        if method == "getUpdates":
            if self.queue:
                return self.queue.pop(0)
            await asyncio.sleep(0.02)
            return []
        if method == "getFile":
            return {"file_path": "photos/file_1.jpg", "file_size": len(PNG)}
        return True

    async def download(self, token: str, file_path: str) -> bytes:
        self.downloads.append(file_path)
        return PNG

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p, _ in list(self.calls) if m == method]

    def sent(self) -> list[str]:
        return [p["text"] for p in self.of("sendMessage")]

    def photo(self, caption: str | None = None, chat: int = OWNER, size: int | None = None) -> dict[str, Any]:
        self._n += 1
        msg: dict[str, Any] = {"message_id": self._n, "chat": {"id": chat, "type": "private"}, "from": {"id": chat, "first_name": "Nate"},
                               "date": int(time.time()) + 1,
                               "photo": [{"file_id": "small", "width": 90, "height": 60}, {"file_id": "big", "width": 600, "height": 400, "file_size": size or len(PNG)}]}
        if caption is not None:
            msg["caption"] = caption
        return {"update_id": 2000 + self._n, "message": msg}

    def text(self, text: str) -> dict[str, Any]:
        self._n += 1
        return {"update_id": 2000 + self._n, "message": {"message_id": self._n, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER, "first_name": "Nate"},
                                                         "date": int(time.time()) + 1, "text": text}}


fake = FakeTelegram()


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    if (SCRIPT.pop(0) if SCRIPT else "ok") == "send":
        yield {"type": "end", "finish_reason": "tool_calls", "usage": None,
               "tool_calls": [{"id": "call_send", "name": "send_files", "arguments": json.dumps(STATE["send"])}]}
        return
    yield {"type": "delta", "text": "All done"}
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
    real = llm.stream_chat, appmod.learner.submit, bridge.deps.api, bridge.deps.download
    llm.stream_chat = _scripted
    appmod.learner.submit = lambda job: None  # type: ignore[method-assign]
    bridge.deps.api, bridge.deps.download = fake, fake.download
    with client:
        client.put("/settings", json={"autoLearn": False, "learnStyle": False, "baseUrl": "", "permissionMode": "manual"})
        yield
    llm.stream_chat, appmod.learner.submit, bridge.deps.api, bridge.deps.download = real


@pytest.fixture(autouse=True)
def _reset():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    fake.calls.clear()
    fake.queue.clear()
    fake.downloads.clear()
    SEEN.clear()
    SCRIPT.clear()
    llm._VISION_FLAGS.clear()
    j("DELETE", "/telegram/token")
    j("PUT", "/settings", {"telegramNotifyLongRuns": False, "defaultModel": "claude-sonnet-5-5", "visionModel": ""})
    yield
    j("DELETE", "/telegram/token")


def pair() -> None:
    st = j("PUT", "/telegram/token", {"token": TOKEN})
    fake.queue.append([{"update_id": 1, "message": {"message_id": 1, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER, "first_name": "Nate"},
                                                    "date": int(time.time()) + 1, "text": f"/start {st['pairing']['code']}"}}])
    wait_until(lambda: j("GET", "/telegram/status")["paired"], "pairing")
    wait_until(lambda: "Paired. Text me anything, or /help." in fake.sent(), "the pairing reply")
    fake.calls.clear()


def target() -> str:
    return appmod.db.get_settings()["telegramState"]["textsConversationId"]


def messages(role: str) -> list[dict[str, Any]]:
    return [m for m in j("GET", f"/conversations/{target()}")["messages"] if m["role"] == role]


def test_a_photo_with_a_caption_becomes_an_attachment_the_model_can_see() -> None:
    pair()
    fake.queue.append([fake.photo("what is this?")])
    wait_until(lambda: "All done" in fake.sent(), "the reply")
    assert fake.downloads == ["photos/file_1.jpg"] and fake.of("getFile") == [{"file_id": "big"}]
    user = messages("user")[0]
    assert user["content"] == "what is this?" and len(user["attachments"]) == 1
    att = user["attachments"][0]
    doc = appmod.documents.get(att["id"])
    assert att["mime"] == "image/jpeg" and att["name"].startswith("photo-") and att["name"].endswith(".jpg")
    assert Path(doc["path"]).read_bytes() == PNG and appmod.db.data_dir in Path(doc["path"]).parents
    # The vision-capable chat model's request carries the picture next to the text; earlier turns would stay text.
    content = [m for m in SEEN[0] if m["role"] == "user"][-1]["content"]
    assert isinstance(content, list) and content[0]["type"] == "text" and f'id="{att["id"]}"' in content[0]["text"]
    assert [p["type"] for p in content[1:]] == ["image_url"] and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    run = appmod.run_store.list(None, target())[0]
    assert run["input"]["origin"] == "telegram" and run["input"]["attachments"] == [att["id"]]


def test_a_photo_without_a_caption_is_a_turn_and_a_text_only_model_gets_no_picture() -> None:
    j("PUT", "/settings", {"defaultModel": "accounts/fireworks/models/gpt-oss-120b"})
    pair()
    fake.queue.append([fake.photo()])
    wait_until(lambda: "All done" in fake.sent(), "the reply")
    user = messages("user")[0]
    assert user["content"] == "" and len(user["attachments"]) == 1
    last = [m for m in SEEN[0] if m["role"] == "user"][-1]["content"]
    assert isinstance(last, str) and "<attached_file" in last  # the marker text only: this model cannot read pictures


def test_a_stranger_or_an_oversize_photo_starts_nothing() -> None:
    pair()
    fake.queue.append([fake.photo("hi", chat=777)])
    time.sleep(0.3)
    assert fake.downloads == [] and all(m == "getUpdates" for m, _, _ in fake.calls)
    fake.queue.append([fake.photo("big one", size=25 * 1024 * 1024)])
    wait_until(lambda: any("20 MB" in t for t in fake.sent()), "the refusal")
    assert fake.downloads == [] and not appmod.convos.get(appmod.db.get_settings()["telegramState"]["textsConversationId"])["messages"]  # no turn: the chat exists from pairing, empty


def test_a_file_the_agent_sends_reaches_the_reply_row_the_done_event_and_the_phone() -> None:
    pair()
    shot = PNG + b"\0"  # trailing bytes: a different hash from the photo above, so the upload is its own document
    doc = appmod._store_upload(None, "shot.png", "image/png", shot)
    STATE["send"] = {"files": [doc["id"]], "text": "Here is the screen."}
    SCRIPT.append("send")
    fake.queue.append([fake.text("show me")])
    wait_until(lambda: "All done" in fake.sent(), "the final reply")
    att = {"id": doc["id"], "name": "shot.png", "mime": "image/png", "size": len(shot)}
    assert messages("assistant")[-1]["attachments"] == [att]
    # Delivered once, as the progress update; the final reply does not send the same file again.
    photos = fake.of("sendPhoto")
    assert len(photos) == 1 and photos[0]["chat_id"] == OWNER and photos[0]["caption"] == "Here is the screen."
    assert fake.of("sendDocument") == [] and fake.of("sendMediaGroup") == []
    ev = next(e for e in messages("assistant")[-1]["tool_events"] if e["name"] == "send_files")
    assert ev["images"] and "telegram" in ev["result_preview"]


def _drive(cid: str, body: Any) -> list[tuple[str, Any]]:
    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, body, asyncio.Event())]
    return asyncio.run(go())


def test_the_done_event_and_the_row_carry_the_reply_attachments() -> None:
    cid = appmod.convos.create(None, "plain", "claude-sonnet-5-5")["id"]
    doc = appmod._store_upload(None, "chart.png", "image/png", PNG + b"\0\0")
    STATE["send"] = {"files": [doc["id"]]}
    SCRIPT.append("send")
    events = _drive(cid, appmod.ChatIn(content="draw it"))
    att = [{"id": doc["id"], "name": "chart.png", "mime": "image/png", "size": doc["size"]}]
    done = next(d for e, d in events if e == "done")
    assert done["attachments"] == att and appmod.convos.get(cid)["messages"][-1]["attachments"] == att
    events = _drive(cid, appmod.ChatIn(content="thanks"))  # a reply that sent nothing carries nothing
    assert next(d for e, d in events if e == "done")["attachments"] is None


def test_a_wake_reply_carries_the_workers_files_and_pushes_them(monkeypatch: pytest.MonkeyPatch) -> None:
    cid = appmod.convos.create(None, "plain", "claude-sonnet-5-5")["id"]
    doc = appmod._store_upload(None, "worker.png", "image/png", PNG + b"\0\0\0")
    att = {"id": doc["id"], "name": "worker.png", "mime": "image/png", "size": doc["size"]}
    pushed: list[tuple[str, Any]] = []
    monkeypatch.setattr(appmod, "_push_wake_reply", lambda text, attachments=None, conv_id=None: pushed.append((text, attachments)))
    events = _drive(cid, appmod.ChatIn(content="Worker finished.", wake={"ids": [], "tainted": False, "title": "t", "attachments": [att]}))
    assert next(d for e, d in events if e == "done")["attachments"] == [att]
    assert appmod.convos.get(cid)["messages"][-1]["attachments"] == [att] and pushed == [("All done", [att])]


def test_build_wake_folds_the_workers_files_once() -> None:
    from personal_os import workers
    a, b = {"id": "d1", "name": "a.png", "mime": "image/png", "size": 1}, {"id": "d2", "name": "b.pdf", "mime": "application/pdf", "size": 2}
    _, wake = workers.build_wake([{"id": "w1", "status": "done", "text": "x", "attachments": [a]},
                                  {"id": "w2", "status": "done", "text": "y", "attachments": [a, b]}, {"id": "w3", "status": "done", "text": "z"}])
    assert wake["attachments"] == [a, b]
