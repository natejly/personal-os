"""browser_* tools (browser.py) against a fake desktop bridge: an httpx MockTransport stands in for Electron main."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import mac, tools  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

TOKEN = "t" * 32


class Fake:
    """Records every /browser/<route> call and answers from `replies[route]` (a dict or a callable on the payload)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.replies: dict[str, Any] = {}
        self.auth: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        route = req.url.path.rsplit("/", 1)[-1]
        body = json.loads(req.content)
        self.auth.append(req.headers.get("authorization", ""))
        self.calls.append((route, body))
        r = self.replies.get(route)
        if callable(r):
            r = r(body)
        return httpx.Response(200, json=r if r is not None else page())

    def routes(self) -> list[str]:
        return [r for r, _ in self.calls]


def page(**kw: Any) -> dict[str, Any]:
    return {"ok": True, "url": "https://example.com/", "title": "Example", "tab": 1, "tabs": 1, "snapshotId": "s1",
            "snapshot": "url: https://example.com/\n<page id=\"p1\">\ne1 button \"Go\"\n</page>", "truncated": False, "notes": [], **kw}


def preview(risk: str = "none", **kw: Any) -> dict[str, Any]:
    return {"ok": True, "element": {"role": "button", "name": "Go", "tag": "button"}, "risk": risk, "url": "https://example.com/", **kw}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bridge = mac.PageBridge()
    bridge.register("http://127.0.0.1:9000", TOKEN, ["page", "browser"])
    fake = Fake()
    bridge.transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(mac, "page_bridge", bridge)

    async def no_dns(host: str) -> list[str]:
        return ["93.184.216.34"]
    monkeypatch.setattr(tools, "_resolve", no_dns)
    settings: dict[str, Any] = {"browserEnabled": True, "browserMaxTabs": 3, "browserIdleSeconds": 120, "browserAllowlist": [],
                                "fetchAllowlist": [], "workspaceRoots": []}
    ws = Workspace(tmp_path / "data")
    tb = Toolbox(None, None, None, lambda: settings, workspace=ws)  # type: ignore[arg-type]
    cards: list[dict[str, Any]] = []
    answers: list[bool] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        assert name == "browser" and forced is True
        cards.append(args)
        return answers.pop(0) if answers else True

    class E:
        pass
    e = E()
    e.bridge, e.fake, e.settings, e.tb, e.ws, e.cards, e.answers, e.approve = bridge, fake, settings, tb, ws, cards, answers, approve
    e.ctx = lambda **kw: {"conversation_id": "c1", "desk_id": "d1", "settings": settings, "tainted": False, "taint_sources": [],
                          "message_id": None, "approve": approve, **kw}
    e.run = lambda name, ctx=None, **a: asyncio.run(tb.call(name, a, ctx if ctx is not None else e.ctx()))
    return e


def test_availability(env) -> None:
    for n in ("browser_open", "browser_manage", "browser_click"):
        assert env.tb.available(n)
    env.settings["browserEnabled"] = False
    assert not env.tb.available("browser_open")
    env.settings["browserEnabled"] = True
    env.bridge.capabilities = {"page"}  # an older main: loader only
    assert not env.tb.available("browser_open") and env.tb.available("open_page")
    env.bridge.capabilities = {"page", "browser"}
    env.bridge.seen = time.time() - 100  # main stopped re-registering
    assert not env.tb.available("browser_open") and not env.tb.available("open_page")


def test_groups_and_dangers(env) -> None:
    for n in ("browser_open", "browser_snapshot", "browser_scroll", "browser_manage"):
        assert (env.tb.specs[n].group, env.tb.specs[n].danger, env.tb.specs[n].taints) == ("browser", "network", True)
    for n in ("browser_click", "browser_type", "browser_select", "browser_press"):
        assert (env.tb.specs[n].group, env.tb.specs[n].danger, env.tb.specs[n].taints) == ("browser", "executes", True)


def test_session_id_and_open_payload(env) -> None:
    out = env.run("browser_open", url="https://example.com/a")
    route, body = env.fake.calls[0]
    assert route == "open" and body["session"] == "desk:d1" and body["maxTabs"] == 3 and body["idleSeconds"] == 120
    assert body["downloadDir"] == str(env.ws.desk_root("d1") / "work" / "downloads") and body["newTab"] is False
    assert env.fake.auth[0] == f"Bearer {TOKEN}"
    assert set(out) == {"url", "title", "tab", "tabs", "snapshot", "notes"} and "snapshotId" not in out
    assert "<page" in out["snapshot"]
    env.run("browser_snapshot", ctx=env.ctx(desk_id=None), query="go", full=True)
    route, body = env.fake.calls[-1]
    assert route == "snapshot" and body["session"] == "conv:c1" and body["query"] == "go" and body["full"] is True
    env.run("browser_open", ctx=env.ctx(desk_id=None), url="https://example.com/b", new_tab=True)
    # A plain chat downloads into its own files, not a desk's.
    chat_dl = env.ws.root.parent / "chats" / "c1" / "outputs" / "downloads"
    assert env.fake.calls[-1][1]["downloadDir"] == str(chat_dl) and env.fake.calls[-1][1]["newTab"] is True
    # With no conversation there is nowhere to put a download: none is offered.
    env.run("browser_open", ctx=env.ctx(desk_id=None, conversation_id=None), url="https://example.com/c")
    assert "downloadDir" not in env.fake.calls[-1][1]


def test_scroll_payload(env) -> None:
    env.run("browser_scroll", direction="up", amount=2, ref="e4")
    assert env.fake.calls[-1] == ("act", {"session": "desk:d1", "action": "scroll", "direction": "up", "amount": 2.0, "ref": "e4"})
    assert env.run("browser_scroll", direction="sideways").get("error")


@pytest.mark.parametrize("code,needle", [("stale_ref", "new snapshot"), ("dialog_open", "action='dialog'"), ("busy", "wait"),
                                         ("timeout", "browser_snapshot"), ("blocked_host", "cannot be opened"),
                                         ("too_many_tabs", "close_tab"), ("too_many_sessions", "desk"), ("no_session", "browser_open")])
def test_error_codes_map_to_ways_forward(env, code: str, needle: str) -> None:
    env.fake.replies["act"] = {"ok": False, "error": "boom", "code": code}
    out = env.run("browser_scroll")
    assert "boom" in out["error"] and needle in out["try_instead"]


def test_bridge_failures(env) -> None:
    env.bridge.transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "no"}))
    out = env.run("browser_snapshot")
    assert "credentials" in out["error"]

    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")
    env.bridge.transport = httpx.MockTransport(boom)
    assert "could not be reached" in env.run("browser_snapshot")["error"]


def test_open_rules(env) -> None:
    # clean run: no card
    env.run("browser_open", url="https://example.com/x")
    assert env.cards == [] and env.fake.routes() == ["open"]
    # private host: refused before the bridge
    n = len(env.fake.calls)
    for bad in ("http://127.0.0.1:8798/", "http://192.168.1.1/", "file:///etc/passwd"):
        assert env.run("browser_open", url=bad).get("error"), bad
    assert len(env.fake.calls) == n and env.cards == []
    # tainted + unknown URL: asks, proceeds on yes
    t = env.ctx(tainted=True)
    env.run("browser_open", ctx=t, url="https://other.example/p?q=1")
    assert env.cards[-1] == {"action": "open", "url": "https://other.example/p?q=1",
                             "why": "this address was not typed by you or returned by a search"}
    assert env.fake.routes()[-1] == "open"
    # declined: refused, bridge untouched
    n = len(env.fake.calls)
    env.answers.append(False)
    assert "browser_open refused" in env.run("browser_open", ctx=t, url="https://third.example/")["error"]
    assert len(env.fake.calls) == n
    # no approve in ctx: refused
    assert env.run("browser_open", ctx=env.ctx(tainted=True, approve=None), url="https://third.example/")["error"]
    assert len(env.fake.calls) == n
    # allowlisted host (browserAllowlist) and a URL a search returned: no card
    env.cards.clear()
    env.settings["browserAllowlist"] = ["docs.example.org"]
    env.run("browser_open", ctx=t, url="https://docs.example.org/guide")
    env.run("browser_open", ctx=env.ctx(tainted=True, allowed_urls={"https://found.example/a"}), url="https://found.example/a")
    assert env.cards == []
    # a private host stays refused even on the allowlist
    env.settings["browserAllowlist"] = ["127.0.0.1"]
    assert env.run("browser_open", ctx=t, url="http://127.0.0.1:1/").get("error")


def test_click_preview_gate(env) -> None:
    env.fake.replies["preview"] = preview("none")
    env.run("browser_click", ref="e1", double=True)
    assert env.cards == [] and env.fake.routes() == ["preview", "act"]
    assert env.fake.calls[1][1] == {"session": "desk:d1", "action": "click", "ref": "e1", "double": True}
    # submit: card with the readable fields
    env.fake.calls.clear()
    env.fake.replies["preview"] = preview("submit", formAction="https://example.com/buy", href="https://example.com/h")
    env.run("browser_click", ref="e1")
    c = env.cards[-1]
    assert c["action"] == "click" and c["risk"] == "submit" and c["form_action"] == "https://example.com/buy"
    assert c["href"] == "https://example.com/h" and "Go" in c["element"] and c["url"] == "https://example.com/"
    assert env.fake.routes() == ["preview", "act"]
    # declined: error and act never called
    env.fake.calls.clear()
    env.answers.append(False)
    out = env.run("browser_click", ref="e1")
    assert "did not allow" in out["error"] and "handoff" in out["try_instead"] and env.fake.routes() == ["preview"]
    # no approve: refused
    env.fake.calls.clear()
    out = env.run("browser_click", ctx=env.ctx(approve=None), ref="e1")
    assert out["error"] and env.fake.routes() == ["preview"]
    # download: allowed after approval
    env.fake.calls.clear()
    env.fake.replies["preview"] = preview("download")
    env.run("browser_click", ref="e1")
    assert env.fake.calls[1][1]["allowDownload"] is True


def test_select_and_press_are_previewed(env) -> None:
    env.fake.replies["preview"] = preview("none")
    env.run("browser_select", ref="e2", values=["Large"])
    env.run("browser_press", key="Enter", ref="e2")
    assert env.fake.routes() == ["preview", "act", "preview", "act"]
    assert env.fake.calls[1][1]["values"] == ["Large"] and env.fake.calls[3][1] == {"session": "desk:d1", "action": "press", "key": "Enter", "ref": "e2"}
    env.fake.replies["preview"] = preview("submit")
    env.run("browser_press", key="Enter")
    assert env.cards[-1]["risk"] == "submit"


def test_type_text_and_password(env) -> None:
    env.fake.replies["preview"] = preview("none")
    env.run("browser_type", ref="e3", text="hello", clear=False)
    assert env.cards == [] and env.fake.calls[1][1] == {"session": "desk:d1", "action": "type", "ref": "e3", "text": "hello", "clear": False, "submit": False}
    env.fake.replies["preview"] = preview("submit")
    env.run("browser_type", ref="e3", text="hello", submit=True)
    assert env.cards[-1]["text"] == "hello"
    env.fake.replies["preview"] = {**preview("password"), "element": {"role": "textbox", "name": "Password", "tag": "input", "inputType": "password"}}
    env.run("browser_type", ref="e4", text="hunter2")  # no submit: still asks
    assert env.cards[-1]["text"] == "<hidden>" and "hunter2" not in json.dumps(env.cards[-1])
    env.fake.calls.clear()
    env.answers.append(False)
    assert env.run("browser_type", ref="e4", text="hunter2")["error"] and env.fake.routes() == ["preview"]


def test_upload_containment_and_ask(env, tmp_path: Path) -> None:
    root = env.ws.ensure("d1")
    (root / "outputs" / "r.txt").write_text("x")
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    out = env.run("browser_manage", action="upload", ref="e9", paths=["outputs/r.txt"])
    assert env.cards[-1]["action"] == "upload" and env.cards[-1]["files"] == ["r.txt"]
    assert env.fake.calls[-1][0] == "act" and env.fake.calls[-1][1]["paths"] == [str(root / "outputs" / "r.txt")] and "error" not in out
    n = len(env.fake.calls)
    for bad in (str(outside), "../../../secret.txt", "outputs/missing.txt"):
        assert env.run("browser_manage", action="upload", ref="e9", paths=[bad]).get("error"), bad
    assert len(env.fake.calls) == n
    env.answers.append(False)
    assert env.run("browser_manage", action="upload", ref="e9", paths=["outputs/r.txt"])["error"] and len(env.fake.calls) == n
    assert env.run("browser_manage", ctx=env.ctx(approve=None), action="upload", ref="e9", paths=["outputs/r.txt"])["error"]
    assert env.run("browser_manage", action="upload", paths=["outputs/r.txt"])["error"]


def test_screenshot_saved_not_returned(env) -> None:
    png = b"\x89PNG\r\n\x1a\nfake"
    env.fake.replies["manage"] = page(pngBase64=base64.b64encode(png).decode(), width=800, height=600)
    out = env.run("browser_manage", action="screenshot", full_page=True)
    assert env.fake.calls[-1][1] == {"session": "desk:d1", "action": "screenshot", "fullPage": True}
    rel = out["path"]
    assert rel.startswith("work/screens/") and rel.endswith(".png") and (env.ws.desk_root("d1") / rel).read_bytes() == png
    assert "view_image" in out["note"] and base64.b64encode(png).decode() not in json.dumps(out)
    out = env.run("browser_manage", ctx=env.ctx(desk_id=None), action="screenshot")
    assert Path(out["path"]).read_bytes() == png


def test_manage_validation(env) -> None:
    for a in ({"action": "nope"}, {"action": "switch_tab"}, {"action": "close_tab"}, {"action": "wait", "ms": 0}):
        out = env.run("browser_manage", **a)
        assert out.get("error"), a
    assert env.fake.calls == []
    env.run("browser_manage", action="switch_tab", tab=2)
    env.run("browser_manage", action="wait", text="Done")
    env.run("browser_manage", action="dialog", accept=False, prompt_text="x")
    env.run("browser_manage", action="tabs")
    assert [c[1]["action"] for c in env.fake.calls] == ["switch_tab", "wait", "dialog", "tabs"]
    assert env.fake.calls[0][1]["tab"] == 2 and env.fake.calls[2][1]["promptText"] == "x"
    env.fake.calls.clear()
    assert env.run("browser_manage", action="close") == {"closed": True} and env.fake.calls == [("close", {"session": "desk:d1"})]


def test_tab_list_passthrough(env) -> None:
    env.fake.replies["manage"] = page(tabList=[{"tab": 1, "url": "https://example.com/"}])
    assert env.run("browser_manage", action="tabs")["tab_list"][0]["tab"] == 1


def test_handoff_order(env) -> None:
    env.fake.replies["manage"] = page()
    out = env.run("browser_manage", action="handoff", reason="sign in")
    assert env.fake.routes() == ["manage", "manage", "snapshot"]
    assert [c[1].get("action") for c in env.fake.calls[:2]] == ["show", "hide"]
    assert env.cards == [{"action": "handoff", "reason": "sign in", "url": "https://example.com/"}]
    assert "error" not in out and "snapshot" in out
    # declined or unanswerable: window hidden again, no snapshot
    env.fake.calls.clear()
    env.answers.append(False)
    assert env.run("browser_manage", action="handoff", reason="x")["error"]
    assert [c[1].get("action") for c in env.fake.calls] == ["show", "hide"]
    env.fake.calls.clear()
    assert env.run("browser_manage", ctx=env.ctx(approve=None), action="handoff")["error"] and env.fake.calls == []


def test_redaction_and_cap(env) -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    env.fake.replies["snapshot"] = page(snapshot=f"e1 text \"{key}\"\n" + "x " * 10000)
    out = env.run("browser_snapshot")
    assert key not in out["snapshot"] and "[aws-key]" in out["snapshot"]
    assert len(out["snapshot"]) <= 12000 and any("cut" in n for n in out["notes"])


def test_downloads_reported_relative(env) -> None:
    root = env.ws.ensure("d1")
    env.fake.replies["act"] = page(notes=[f"downloaded: {root / 'work' / 'downloads' / 'a.pdf'}"])
    env.fake.replies["preview"] = preview("none")
    assert env.run("browser_click", ref="e1")["downloaded"] == ["work/downloads/a.pdf"]


def test_chat_download_needs_approval_and_lands_in_chat_files(env) -> None:
    ctx = env.ctx(desk_id=None)
    env.run("browser_open", ctx=ctx, url="https://example.com/a")
    dl = Path(env.fake.calls[-1][1]["downloadDir"])
    assert dl == env.tb.chat_outputs.desk_root("c1") / "outputs" / "downloads"
    (dl / "a.pdf").write_bytes(b"%PDF-1")
    env.fake.replies["preview"] = preview("download")
    env.fake.replies["act"] = page(notes=[f"downloaded: {dl / 'a.pdf'}"])
    env.answers.append(False)  # declined: the act is never sent
    env.fake.calls.clear()
    out = env.run("browser_click", ctx=ctx, ref="e1")
    assert "error" in out and env.fake.routes() == ["preview"] and env.cards[-1]["action"] == "click"
    env.fake.calls.clear()
    out = env.run("browser_click", ctx=env.ctx(desk_id=None), ref="e1")
    assert env.fake.calls[1][1]["allowDownload"] is True
    assert out["downloaded"] == ["outputs/downloads/a.pdf"]
    assert out["outputs"] == [{"name": "a.pdf", "size": 6, "path": "outputs/downloads/a.pdf"}]


def test_register_route_accepts_capabilities() -> None:
    b = mac.PageBridge()
    b.register("http://127.0.0.1:9000", TOKEN)
    assert b.has("page") and not b.has("browser")
    b.register("http://127.0.0.1:9000", TOKEN, ["page", "browser"])
    assert b.has("browser")
    from personal_os.app import PageBridgeIn
    assert PageBridgeIn(url="u", token="t", capabilities=["page", "browser"]).capabilities == ["page", "browser"]
    assert PageBridgeIn(url="u", token="t").capabilities is None
