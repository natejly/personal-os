"""find_files / the local file tools / shortcuts / open_page plumbing. Subprocesses and the Electron bridge are faked:
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


def test_app_data_dir_is_off_limits_even_under_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev data dir lives on the Desktop, which the home-folder rule would otherwise allow."""
    data = home / "Desktop" / "GrainData"
    (data / "uploads").mkdir(parents=True)
    (data / "personal-os.db").write_text("sentinel")
    (data / "uploads" / "note.txt").write_text("hello")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    for raw in (str(data / "personal-os.db"), "~/Desktop/GrainData/uploads/note.txt", "~/Desktop/GrainData"):
        with pytest.raises(mac.LocalPathError):
            mac.allowed_path(raw)
    link = home / "Documents" / "notes.db"
    link.symlink_to(data / "personal-os.db")
    with pytest.raises(mac.LocalPathError):
        mac.read_local("~/Documents/notes.db")
    (home / "Documents" / "ok.txt").write_text("hi")
    assert mac.read_local("~/Documents/ok.txt")["text"] == "hi"


def test_library_case_is_off_limits(home: Path) -> None:
    """APFS is case-insensitive and resolve() keeps the caller's spelling, so ~/library is ~/Library."""
    secret = home / "Library" / "Keychains" / "login.keychain-db"
    secret.write_text("sentinel")
    for raw in ("~/library/Keychains/login.keychain-db", "~/LIBRARY/Keychains/login.keychain-db",
                "~/LiBrArY/Keychains/login.keychain-db", "~/Desktop/../library/Keychains/login.keychain-db"):
        with pytest.raises(mac.LocalPathError):
            mac.allowed_path(raw)
        with pytest.raises(mac.LocalPathError):
            mac.read_local(raw)
        with pytest.raises(mac.LocalPathError):
            mac.write_local(raw, "x", "overwrite")
    (home / "Desktop" / "note.txt").write_text("hi")
    with pytest.raises(mac.LocalPathError):
        mac.move_local("~/Desktop/note.txt", "~/library/Keychains/note.txt")
    assert secret.read_text() == "sentinel"
    assert (home / "Desktop" / "note.txt").read_text() == "hi"
    (home / "Library-backup").mkdir()
    assert mac.allowed_path("~/Library-backup") == home / "Library-backup"  # prefix of the name, not the folder


def test_app_data_dir_case_is_off_limits(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = home / "Desktop" / "GrainData"
    (data / "uploads").mkdir(parents=True)
    (data / "personal-os.db").write_text("sentinel")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    for raw in ("~/Desktop/graindata/personal-os.db", "~/Desktop/GRAINDATA/uploads/note.txt",
                str(home / "Desktop" / "graindata" / "personal-os.db")):
        with pytest.raises(mac.LocalPathError):
            mac.read_local(raw)
        with pytest.raises(mac.LocalPathError):
            mac.write_local(raw, "x", "overwrite")
    neighbor = home / "Desktop" / "GrainData-backup"
    neighbor.mkdir()
    (neighbor / "notes.txt").write_text("hi")
    assert mac.read_local("~/Desktop/GrainData-backup/notes.txt")["text"] == "hi"
    assert (data / "personal-os.db").read_text() == "sentinel"


def test_home_prefix_is_not_a_string_prefix(home: Path) -> None:
    """`/home-secret` starts with `/home` and is still outside the home folder."""
    sibling = Path(str(home) + "-secret")
    sibling.mkdir()
    (sibling / "secret.txt").write_text("nope")
    assert str(sibling).startswith(str(home))
    with pytest.raises(mac.LocalPathError):
        mac.allowed_path(str(sibling / "secret.txt"))
    with pytest.raises(mac.LocalPathError):
        mac.write_local(str(sibling / "secret.txt"), "x")
    assert (sibling / "secret.txt").read_text() == "nope"


def test_symlink_dir_cannot_read_write_move_or_escape(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    (home / "Desktop" / "dirlink").symlink_to(outside, target_is_directory=True)
    (home / "Desktop" / "movable.txt").write_text("stay")
    for raw in ("~/Desktop/dirlink", "~/Desktop/dirlink/secret.txt", "~/Desktop/dirlink/new.txt"):
        with pytest.raises(mac.LocalPathError):
            mac.read_local(raw)
        with pytest.raises(mac.LocalPathError):
            mac.write_local(raw, "x")
    with pytest.raises(mac.LocalPathError):
        mac.move_local("~/Desktop/movable.txt", "~/Desktop/dirlink/movable.txt")
    assert (home / "Desktop" / "movable.txt").read_text() == "stay"
    assert not (outside / "new.txt").exists() and not (outside / "movable.txt").exists()


def test_trash_refuses_a_symlink_outside_home(home: Path, tmp_path: Path) -> None:
    stolen = tmp_path / "stolen"
    stolen.mkdir()
    (home / ".Trash").symlink_to(stolen, target_is_directory=True)
    (home / "Desktop" / "victim.txt").write_text("keep")
    with pytest.raises(mac.LocalPathError):
        mac.trash_local("~/Desktop/victim.txt")
    assert (home / "Desktop" / "victim.txt").read_text() == "keep"
    assert list(stolen.iterdir()) == []


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
    proc = FakeProc(lines=[str(home / "Documents" / f"{i}.txt") for i in range(50)])
    fake_exec(monkeypatch, proc)
    out = asyncio.run(mac.mdfind("x", limit=5))
    assert out["count"] == 5 and out["truncated"] is True and proc.killed


def test_mdfind_drops_paths_the_file_tools_refuse(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = home / "Desktop" / "GrainData"
    data.mkdir()
    db = data / "personal-os.db"
    db.write_text("x")
    ok = home / "Documents" / "lease.pdf"
    ok.write_bytes(b"%PDF")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    fake_exec(monkeypatch, FakeProc(lines=[
        str(db), "/etc/passwd", str(home / "Library" / "Keychains" / "login.keychain-db"), str(ok),
    ]))
    out = asyncio.run(mac.mdfind("lease"))
    assert [r["path"] for r in out["results"]] == [str(ok)]


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


# ---- write_local_file / move_local_file / trash_local_file ----
def test_write_local_creates_without_clobbering(home: Path) -> None:
    out = mac.write_local("~/Desktop/Trips/packing.md", "- passport\n")
    assert out["created"] and out["mode"] == "create"
    assert (home / "Desktop" / "Trips" / "packing.md").read_text() == "- passport\n"  # parents created

    with pytest.raises(mac.LocalPathError) as e:  # a second create refuses rather than replacing
        mac.write_local("~/Desktop/Trips/packing.md", "gone")
    assert "already exists" in str(e.value)

    assert mac.write_local("~/Desktop/Trips/packing.md", "- charger\n", "append")["created"] is False
    assert (home / "Desktop" / "Trips" / "packing.md").read_text() == "- passport\n- charger\n"
    mac.write_local("~/Desktop/Trips/packing.md", "fresh", "overwrite")
    assert (home / "Desktop" / "Trips" / "packing.md").read_text() == "fresh"


def test_write_local_refuses_bad_paths_modes_and_launchers(home: Path) -> None:
    for bad in ("/etc/hosts", "~/Library/x.txt", "~/.ssh/key", "~/Desktop/run.command", "~/Desktop/Thing.app",
                "~/Desktop/Evil.app/Contents/MacOS/run", "~/Desktop/link.inetloc", "~/Desktop/open.fileloc"):
        with pytest.raises(mac.LocalPathError):
            mac.write_local(bad, "x")
    (home / "Desktop" / "folder").mkdir()
    with pytest.raises(mac.LocalPathError):
        mac.write_local("~/Desktop/folder", "x")
    with pytest.raises(ValueError):
        mac.write_local("~/Desktop/a.txt", "x", "replace")
    with pytest.raises(ValueError):
        mac.write_local("~/Desktop/a.txt", "x" * (mac.MAX_WRITE_CHARS + 1))
    assert not (home / "Desktop" / "a.txt").exists()


def test_write_local_refuses_a_symlink_out_of_home(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    (home / "Desktop" / "link.txt").symlink_to(outside)
    with pytest.raises(mac.LocalPathError):
        mac.write_local("~/Desktop/link.txt", "overwritten", "overwrite")
    assert outside.read_text() == "original"


def test_write_move_and_trash_do_not_follow_a_symlink_inside_home(home: Path) -> None:
    """Resolving the link first would edit, move or trash the target and report the link's path."""
    target = home / "Documents" / "important.txt"
    target.write_text("keep")
    link = home / "Desktop" / "link.txt"
    link.symlink_to(target)
    for act in (
        lambda: mac.write_local("~/Desktop/link.txt", "overwritten", "overwrite"),
        lambda: mac.move_local("~/Desktop/link.txt", "~/Desktop/renamed.txt"),
        lambda: mac.trash_local("~/Desktop/link.txt"),
    ):
        with pytest.raises(mac.LocalPathError):
            act()
    assert target.read_text() == "keep"
    assert link.is_symlink()
    assert not (home / "Desktop" / "renamed.txt").exists()
    assert not (home / ".Trash" / "important.txt").exists()


def test_move_local_renames_moves_and_never_replaces(home: Path) -> None:
    (home / "Downloads" / "scan.pdf").write_text("pdf")
    (home / "Documents" / "Receipts").mkdir()
    out = mac.move_local("~/Downloads/scan.pdf", "~/Documents/Receipts/")  # folder destination keeps the name
    assert out["path"] == str(home / "Documents" / "Receipts" / "scan.pdf")
    assert not (home / "Downloads" / "scan.pdf").exists()

    mac.move_local("~/Documents/Receipts/scan.pdf", "~/Documents/Receipts/lease.pdf")  # rename
    assert (home / "Documents" / "Receipts" / "lease.pdf").read_text() == "pdf"

    (home / "Downloads" / "lease.pdf").write_text("other")
    for src, dst in (("~/Downloads/lease.pdf", "~/Documents/Receipts/"),       # the name is taken
                     ("~/Downloads/lease.pdf", "~/Downloads/lease.pdf"),       # onto itself
                     ("~/Downloads/missing.pdf", "~/Desktop/x.pdf"),           # no source
                     ("~/Downloads/lease.pdf", "/tmp/out.pdf"),                # outside home
                     ("~/Downloads/lease.pdf", "~/Desktop/open.command")):     # launcher
        with pytest.raises(mac.LocalPathError):
            mac.move_local(src, dst)
    assert (home / "Downloads" / "lease.pdf").read_text() == "other"


def test_trash_local_moves_to_the_trash_and_dedupes(home: Path) -> None:
    (home / "Desktop" / "dupe.pdf").write_text("one")
    first = mac.trash_local("~/Desktop/dupe.pdf")
    assert first["trashed_to"] == str(home / ".Trash" / "dupe.pdf")
    assert (home / ".Trash" / "dupe.pdf").read_text() == "one"

    (home / "Desktop" / "dupe.pdf").write_text("two")
    second = mac.trash_local("~/Desktop/dupe.pdf")
    assert second["trashed_to"] == str(home / ".Trash" / "dupe 2.pdf")  # the Finder's naming
    assert (home / ".Trash" / "dupe 2.pdf").read_text() == "two"

    (home / "Documents" / "Folder").mkdir()
    assert mac.trash_local("~/Documents/Folder")["trashed_to"] == str(home / ".Trash" / "Folder")
    for bad in ("~", "/etc/hosts", "~/Desktop/missing.pdf"):
        with pytest.raises(mac.LocalPathError):
            mac.trash_local(bad)


def test_file_writes_ask_first_and_errors_are_shaped(home: Path) -> None:
    tb = make_toolbox()
    modes = tb.effective({}, None, None)
    for n in ("write_local_file", "move_local_file", "trash_local_file"):
        assert modes[n] == "ask", n          # danger "external": never silent
        assert tb.available(n), n            # plain file work, no Mac-only binary
        assert tb.specs[n].group == "files"
    out = asyncio.run(tb.call("write_local_file", {"path": "~/Library/x.txt", "content": "x"}, {}))
    assert out["error"] and out["field"] == "path" and out["try_instead"]
    out = asyncio.run(tb.call("move_local_file", {"path": "~/Desktop/nope.md", "to": "~/Desktop/b.md"}, {}))
    assert out["error"] and out["field"] == "path"
    out = asyncio.run(tb.call("write_local_file", {"path": "~/Desktop/ok.md", "content": "hi"}, {}))
    assert out["created"] and (home / "Desktop" / "ok.md").read_text() == "hi"


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
    argv = calls[0]
    assert argv[:2] == ["shortcuts", "run"]
    assert argv[argv.index("--") + 1] == "Add to Reading List"
    assert argv[argv.index("--output-path") + 1] != "Add to Reading List"
    assert holder["proc"].stdin_data == b"https://example.com"
    assert out["ok"] and out["output"] == "Saved." and out["exit_code"] == 0


def test_run_shortcut_name_is_not_a_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_exec(monkeypatch, FakeProc())
    asyncio.run(mac.run_shortcut("--output-path=/tmp/pwned"))
    argv = calls[0]
    assert argv[argv.index("--") + 1] == "--output-path=/tmp/pwned"
    assert "--output-path=/tmp/pwned" not in argv[:argv.index("--")]
    with pytest.raises(ValueError):
        asyncio.run(mac.run_shortcut("name\n--output-path=/tmp/pwned"))
    with pytest.raises(ValueError):
        asyncio.run(mac.list_shortcuts("--folders"))


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
    assert tb.specs["run_shortcut"].taints  # a Shortcut's output is whatever that Shortcut returns


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
