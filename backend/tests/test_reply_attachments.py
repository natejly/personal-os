"""Files the agent makes during a Telegram turn ride on the reply without a send_files step (sendfiles.attach_made, the browser
screenshot, the ctx flag app.py sets). Everything external is faked: no network, no browser, no Telegram."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="replyatt-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, mac, sendfiles, tools  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.repos import Documents  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

TOKEN = "t" * 32


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


def doc(i: str, name: str = "a.png") -> dict[str, Any]:
    return {"id": i, "name": name, "mime": "image/png", "size": 3, "extra": "dropped"}


def test_attach_to_reply_dedups_by_id_and_keeps_order() -> None:
    ctx: dict[str, Any] = {}
    for i in ("b", "a", "b", "c", "a"):
        sendfiles.attach_to_reply(ctx, doc(i))
    assert [a["id"] for a in ctx["reply_attachments"]] == ["b", "a", "c"]
    assert set(ctx["reply_attachments"][0]) == {"id", "name", "mime", "size"}


@pytest.fixture
def box(tmp_path: Path):
    db = Database(tmp_path)
    ws = Workspace(tmp_path, sub="chats")
    tb = Toolbox(None, None, Documents(db), lambda: {}, docs=Docs(db), workspace=Workspace(tmp_path / "cowork-root"))  # type: ignore[arg-type]
    tb.chat_outputs = ws
    return tb, ws


def run_tool(tb: Toolbox, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
    return asyncio.run(tb.call(name, args, ctx))


def test_a_made_note_rides_on_the_reply_only_when_the_flag_is_on(box) -> None:
    tb, _ = box
    off: dict[str, Any] = {"conversation_id": "c1", "project_id": None}
    out = run_tool(tb, "doc_create", {"title": "Plan", "content": "# Plan\nalpha\n"}, off)
    assert out["doc_id"] and "reply_attachments" not in off
    on = {**off, "auto_attach": True}
    out = run_tool(tb, "doc_create", {"title": "Plan two", "content": "# Plan\nbeta\n"}, on)
    [att] = on["reply_attachments"]
    assert att["name"] == "Plan two.md" and att["mime"] == "text/markdown"
    assert Path(tb.documents.get(att["id"])["path"]).read_bytes() == b"# Plan\nbeta\n" and out["doc_id"]


def test_a_script_output_in_the_chat_files_rides_on_the_reply(box) -> None:
    tb, ws = box
    entry = ws.save_bytes("c1", "outputs/report.csv", b"a,b\n1,2\n")
    ctx: dict[str, Any] = {"conversation_id": "c1", "project_id": None, "auto_attach": True}
    asyncio.run(sendfiles.attach_made(tb, ctx, "run_python", {"outputs": [entry], "stdout": "ok"}))
    [att] = ctx["reply_attachments"]
    assert att["name"] == "report.csv" and att["mime"].startswith(("text/csv", "application/csv")) and att["size"] == 8
    # a chart the script drew inline rides along too, and an error result attaches nothing
    img = {"name": "plot.png", "mime": "image/png", "bytes": len(PNG), "data": "data:image/png;base64," + base64.b64encode(PNG).decode()}
    asyncio.run(sendfiles.attach_made(tb, ctx, "run_python", {"images": [img]}))
    asyncio.run(sendfiles.attach_made(tb, ctx, "run_python", {"error": "boom", "outputs": [entry]}))
    assert [a["name"] for a in ctx["reply_attachments"]] == ["report.csv", "plot.png"]


def test_a_users_own_upload_is_not_auto_attached(box) -> None:
    tb, _ = box
    mine = sendfiles.store_file(tb, {}, "photo.jpg", "image/jpeg", PNG + b"1")
    ctx: dict[str, Any] = {"project_id": None, "auto_attach": True, "user_attachments": [mine]}
    # a tool that merely reads or lists documents returns no `attachment`/`saved`/`outputs`, so nothing is made
    asyncio.run(sendfiles.attach_made(tb, ctx, "read_document", {"id": mine["id"], "text": "..."}))
    assert "reply_attachments" not in ctx


# ---- browser screenshot ----

class Fake:
    def __init__(self) -> None:
        self.reply: dict[str, Any] = {"ok": True, "pngBase64": base64.b64encode(PNG).decode(), "width": 8, "height": 8,
                                      "url": "https://example.com/", "title": "Example"}

    def handler(self, req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.reply)


@pytest.fixture
def browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bridge = mac.PageBridge()
    bridge.register("http://127.0.0.1:9000", TOKEN, ["page", "browser"])
    bridge.transport = httpx.MockTransport(Fake().handler)
    monkeypatch.setattr(mac, "page_bridge", bridge)
    settings: dict[str, Any] = {"browserEnabled": True, "browserAllowlist": [], "fetchAllowlist": [], "workspaceRoots": []}
    tb = Toolbox(None, None, Documents(Database(tmp_path)), lambda: settings, workspace=Workspace(tmp_path / "data"))  # type: ignore[arg-type]
    updates: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []

    async def hook(ctx: dict[str, Any], text: str, atts: list[dict[str, Any]]) -> bool:
        updates.append((ctx, text, atts))
        return True
    tb.user_update = hook
    ctx = lambda **kw: {"conversation_id": "c1", "settings": settings, "tainted": False, "taint_sources": [], "message_id": None, **kw}  # noqa: E731
    return tb, updates, ctx


def test_a_browser_screenshot_is_attached_and_sent_live_once_when_the_flag_is_on(browser) -> None:
    tb, updates, ctx = browser
    on = ctx(auto_attach=True)
    out = run_tool(tb, "browser_manage", {"action": "screenshot"}, on)
    att = out["attachment"]
    assert set(att) == {"id", "name", "mime", "size"} and att["mime"].startswith("image/") and att["size"] == len(PNG)
    assert Path(tb.documents.get(att["id"])["path"]).read_bytes() == PNG
    assert on["reply_attachments"] == [att]  # the central hook saw `attachment` again and added nothing
    assert len(updates) == 1 and updates[0][0] is on and updates[0][1] == "" and updates[0][2] == [att]
    run_tool(tb, "browser_manage", {"action": "screenshot"}, on)  # the same bytes: one Uploads row, one reply attachment
    assert on["reply_attachments"] == [att] and len(updates) == 2


def test_a_browser_screenshot_stays_off_the_reply_with_the_flag_off(browser) -> None:
    tb, updates, ctx = browser
    off = ctx()
    out = run_tool(tb, "browser_manage", {"action": "screenshot"}, off)
    assert "attachment" not in out and out["bytes"] == len(PNG) and "reply_attachments" not in off and updates == []


def test_a_failing_live_hook_does_not_fail_the_screenshot(browser) -> None:
    tb, _, ctx = browser

    async def boom(c: Any, t: str, a: Any) -> bool:
        raise RuntimeError("down")
    tb.user_update = boom
    on = ctx(auto_attach=True)
    out = run_tool(tb, "browser_manage", {"action": "screenshot"}, on)
    assert out["attachment"] and on["reply_attachments"] == [out["attachment"]]


# ---- the flag app.py builds ----

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SEEN_FLAGS: list[Any] = []
SCRIPT: list[str] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    if SCRIPT:
        SCRIPT.pop(0)
        yield {"type": "end", "finish_reason": "tool_calls", "usage": None,
               "tool_calls": [{"id": "call_t", "name": "current_time", "arguments": json.dumps({})}]}
        return
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _drive(cid: str) -> None:
    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="what time is it"), asyncio.Event()):
            pass
    asyncio.run(go())


def test_the_tool_ctx_flag_is_on_only_in_the_telegram_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "stream_chat", _scripted)
    monkeypatch.setattr(appmod.learner, "submit", lambda job: None)
    spec = appmod.toolbox.specs["current_time"]
    real = spec.fn

    async def spy(ctx: dict[str, Any]) -> Any:
        SEEN_FLAGS.append(ctx.get("auto_attach"))
        return await real(ctx)
    monkeypatch.setattr(spec, "fn", spy)
    with client:
        client.put("/settings", json={"autoLearn": False, "learnStyle": False, "baseUrl": ""})
        tg = appmod.convos.create(None, "texts", "claude-sonnet-5-5")["id"]
        plain = appmod.convos.create(None, "plain", "claude-sonnet-5-5")["id"]
        monkeypatch.setattr(appmod.telegram_bridge, "is_texts_conversation", lambda cid: cid == tg)
        for cid in (tg, plain):
            SCRIPT.append("tool")
            _drive(cid)
    assert SEEN_FLAGS == [True, False]
