"""find_files / read_local_file / shortcuts / open_page plumbing. Subprocesses and the Electron bridge are faked:
nothing here needs Spotlight, Shortcuts or a running app."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import mac, tools  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    for d in ("Desktop", "Documents", "Downloads", "Library/Keychains", ".ssh"):
        (h / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


class FakeStream:
    def __init__(self, lines: list[str]):
        self.lines = [ln.encode() + b"\n" for ln in lines]

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class FakeProc:
    def __init__(self, *, lines: list[str] | None = None, out: bytes = b"", err: bytes = b"", code: int = 0,
                 hang: bool = False, on_run: Any = None):
        self.stdout = FakeStream(lines or [])
        self.returncode: int | None = None
        self._out, self._err, self._code, self._hang, self._on_run = out, err, code, hang, on_run
        self.killed = False
        self.stdin_data: bytes | None = None

    async def communicate(self, data: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_data = data
        if self._hang:
            await asyncio.sleep(10)
        if self._on_run:
            self._on_run()
        self.returncode = self._code
        return self._out, self._err

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def fake_exec(monkeypatch: pytest.MonkeyPatch, proc: FakeProc | Any) -> list[list[str]]:
    calls: list[list[str]] = []

    async def _exec(*argv: str, **kw: Any) -> FakeProc:
        calls.append(list(argv))
        return proc(list(argv)) if callable(proc) and not isinstance(proc, FakeProc) else proc
    monkeypatch.setattr(mac.asyncio, "create_subprocess_exec", _exec)
    return calls


# ---- path policy ----
def test_allowed_path_policy(home: Path) -> None:
    assert mac.allowed_path("~/Documents") == home / "Documents"
    assert mac.allowed_path("Desktop") == home / "Desktop"  # relative = under home
    for bad in ("/etc/passwd", "~/Library/Keychains", "~/.ssh/id_ed25519", "~/Documents/../../outside", ""):
        with pytest.raises(mac.LocalPathError):
            mac.allowed_path(bad)


def test_symlink_out_of_home_is_refused(home: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("x")
    (home / "Desktop" / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(mac.LocalPathError):
        mac.allowed_path("~/Desktop/link.txt")


# ---- mdfind ----
def test_mdfind_scopes_to_desktop_and_documents(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = home / "Documents" / "lease.pdf"
    f.write_bytes(b"%PDF")
    calls = fake_exec(monkeypatch, FakeProc(lines=[str(f), str(home / "Documents" / ".hidden" / "x")]))
    out = asyncio.run(mac.mdfind("lease"))
    assert calls == [["mdfind", "-onlyin", str(home / "Desktop"), "-onlyin", str(home / "Documents"), "lease"]]
    assert out["count"] == 1 and out["folders"] == ["~/Desktop", "~/Documents"]
    row = out["results"][0]
    assert row["path"] == str(f) and row["kind"] == "pdf" and row["size"] == 4 and "modified" in row


def test_mdfind_name_mode_custom_folder_and_dash(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_exec(monkeypatch, FakeProc())
    asyncio.run(mac.mdfind("--resume", folders=["~/Downloads"], name_only=True))
    assert calls == [["mdfind", "-onlyin", str(home / "Downloads"), "-name", "resume"]]


def test_mdfind_caps_results_and_stops_the_process(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(lines=[f"/x/{i}" for i in range(50)])
    fake_exec(monkeypatch, proc)
    out = asyncio.run(mac.mdfind("x", limit=5))
    assert out["count"] == 5 and out["truncated"] is True and proc.killed


def test_mdfind_refuses_folders_outside_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_exec(monkeypatch, FakeProc())
    with pytest.raises(mac.LocalPathError):
        asyncio.run(mac.mdfind("x", folders=["/etc"]))
    with pytest.raises(ValueError):
        asyncio.run(mac.mdfind("  -- "))


# ---- read_local_file ----
def test_read_local_pages_text_and_lists_folders(home: Path) -> None:
    (home / "Desktop" / "notes.md").write_text("a" * 50 + "b" * 50)
    out = mac.read_local("~/Desktop/notes.md", offset=50, length=20)
    assert out["text"] == "b" * 20 and out["total_chars"] == 100 and out["has_more"]
    (home / "Desktop" / ".DS_Store").write_text("")
    listing = mac.read_local("~/Desktop")
    assert listing["kind"] == "folder" and listing["entries"] == ["notes.md"]


# ---- shortcuts ----
def test_list_shortcuts_parses_names(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_exec(monkeypatch, FakeProc(out=b"Log Water\nAdd to Reading List\n\n"))
    assert asyncio.run(mac.list_shortcuts()) == ["Log Water", "Add to Reading List"]
    assert calls == [["shortcuts", "list"]]


def test_run_shortcut_pipes_input_and_reads_output_file(monkeypatch: pytest.MonkeyPatch) -> None:
    holder: dict[str, Any] = {}

    def make(argv: list[str]) -> FakeProc:
        out_path = argv[argv.index("--output-path") + 1]
        p = FakeProc(on_run=lambda: Path(out_path).write_text("Saved."))
        holder["proc"] = p
        return p
    calls = fake_exec(monkeypatch, make)
    out = asyncio.run(mac.run_shortcut("Add to Reading List", "https://example.com"))
    assert calls[0][:3] == ["shortcuts", "run", "Add to Reading List"]
    assert holder["proc"].stdin_data == b"https://example.com"
    assert out["ok"] and out["output"] == "Saved." and out["exit_code"] == 0


def test_run_shortcut_times_out_and_kills(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(hang=True)
    fake_exec(monkeypatch, proc)
    out = asyncio.run(mac.run_shortcut("Slow", timeout=0.05))
    assert out["ok"] is False and out["timed_out"] and proc.killed


def test_run_shortcut_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_exec(monkeypatch, FakeProc(code=1, err=b"Couldn't find shortcut"))
    out = asyncio.run(mac.run_shortcut("Nope"))
    assert out["ok"] is False and "find shortcut" in out["stderr"]


# ---- page bridge ----
def test_bridge_only_accepts_loopback() -> None:
    b = mac.PageBridge()
    for bad in ("http://10.0.0.2:9000", "https://127.0.0.1:9000", "http://127.0.0.1", "http://evil.com:80"):
        with pytest.raises(ValueError):
            b.register(bad, "x" * 32)
    with pytest.raises(ValueError):
        b.register("http://127.0.0.1:9000", "short")
    b.register("http://127.0.0.1:9000", "x" * 32)
    assert b.connected and b.url == "http://127.0.0.1:9000"


def test_bridge_posts_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"], seen["body"], seen["url"] = req.headers["authorization"], json.loads(req.content), str(req.url)
        return httpx.Response(200, json={"url": "https://example.com/", "title": "Ex", "text": "y" * 5000, "truncated": False})
    real = httpx.AsyncClient
    monkeypatch.setattr(mac.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    b = mac.PageBridge()
    b.register("http://127.0.0.1:9000", "s" * 32)
    out = asyncio.run(b.open_page("https://example.com", max_chars=1000))
    assert seen["url"] == "http://127.0.0.1:9000/page" and seen["auth"] == "Bearer " + "s" * 32
    assert seen["body"]["maxChars"] == 1000 and len(out["text"]) == 1000 and out["truncated"] and out["title"] == "Ex"


# ---- Toolbox wiring ----
def make_toolbox() -> Toolbox:
    return Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]


def test_modes_and_reserved_names() -> None:
    from personal_os.mcp_servers import RESERVED_TOOL_NAMES

    tb = make_toolbox()
    modes = tb.effective({}, None, None)
    assert modes["run_shortcut"] == "ask"
    for n in ("find_files", "read_local_file", "list_shortcuts", "open_page"):
        assert modes[n] == "on", n
    assert set(tools.MAC_TOOLS) <= RESERVED_TOOL_NAMES
    assert tb.specs["open_page"].taints and tb.specs["read_local_file"].taints


def test_open_page_needs_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac, "page_bridge", mac.PageBridge())
    tb = make_toolbox()
    assert not tb.available("open_page")
    mac.page_bridge.register("http://127.0.0.1:9000", "t" * 32)
    assert tb.available("open_page")


def test_open_page_blocks_bad_urls_before_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def fake_open(url: str, **kw: Any) -> dict[str, Any]:
        called.append(url)
        return {"url": url, "title": "", "text": "ok"}

    async def no_dns(host: str) -> None:
        return None
    monkeypatch.setattr(mac.page_bridge, "open_page", fake_open)
    monkeypatch.setattr(tools, "_resolve", no_dns)
    tb = make_toolbox()
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "http://127.0.0.1:8798/settings", "http://192.168.1.1/"):
        out = asyncio.run(tb.call("open_page", {"url": bad}, {}))
        assert out.get("error"), bad
    assert called == []
    out = asyncio.run(tb.call("open_page", {"url": "https://example.com/a"}, {}))
    assert out["text"] == "ok" and called == ["https://example.com/a"]


def test_tool_errors_are_shaped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_exec(monkeypatch, FakeProc())
    tb = make_toolbox()
    out = asyncio.run(tb.call("read_local_file", {"path": "~/.ssh/id_ed25519"}, {}))
    assert "off limits" in out["error"] and out["try_instead"]
    out = asyncio.run(tb.call("find_files", {"query": "x", "folders": ["/etc"]}, {}))
    assert out["error"] and out["field"] == "folders"
