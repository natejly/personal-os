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
    assert mac.allowed_path("~") == home                      # the home folder itself is fine
    # the whole Mac is in scope: outside the home folder, ~/Library and dot-folders included
    for ok in ("/etc/passwd", "/tmp", "/usr/local", "/Volumes/Backup/notes.txt", "~/Library/Keychains", "~/.ssh/id_ed25519",
               "~/Documents/../../outside"):
        assert mac.allowed_path(ok) == Path(os.path.realpath(os.path.expanduser(ok))), ok
    with pytest.raises(mac.LocalPathError):
        mac.allowed_path("")


def test_a_symlink_is_judged_where_it_points(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "secret.txt").write_text("x")
    (home / "Desktop" / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert mac.allowed_path("~/Desktop/link.txt") == tmp_path / "secret.txt"  # outside home is fine now
    data = tmp_path / "data"
    data.mkdir()
    (data / "personal-os.db").write_text("sentinel")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    (home / "Desktop" / "db-link").symlink_to(data / "personal-os.db")
    (home / "Desktop" / "data-link").symlink_to(data, target_is_directory=True)
    for raw in ("~/Desktop/db-link", "~/Desktop/data-link", "~/Desktop/data-link/personal-os.db"):
        with pytest.raises(mac.LocalPathError):
            mac.allowed_path(raw)
        assert mac.protected_reason(raw)


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


def test_credential_stores_are_sensitive_whatever_the_case(home: Path) -> None:
    """APFS is case-insensitive and resolve() keeps the caller's spelling, so ~/library is ~/Library."""
    for raw in ("~/library/Keychains/login.keychain-db", "~/LIBRARY/Keychains/login.keychain-db",
                "~/LiBrArY/Keychains/login.keychain-db", "~/Desktop/../library/Keychains/login.keychain-db",
                "/Library/Keychains/System.keychain", "~/.SSH/config", "~/Library/Cookies/Cookies.binarycookies",
                "~/Library/Application Support/Google/Chrome/Default/Login Data",
                "~/Library/Application Support/Arc/User Data/Default/Cookies",
                "~/Library/Application Support/Firefox/Profiles/x.default/key4.db",
                "~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies",
                "~/.config/gh/hosts.yml", "~/Documents/.env", "~/Documents/id_rsa",
                "~/.cargo/credentials.toml", "~/.vault-token", "~/Documents/credentials.toml"):
        assert mac.sensitive_reason(os.path.normpath(os.path.expanduser(raw))), raw
    for raw in ("~/Library-backup/x", "~/Desktop/notes.txt", "~/Library/Application Support/Google/Chrome/Default/History",
                "~/.config/gh/config.yml", "~/Documents/id_rsa.pub"):
        assert mac.sensitive_reason(os.path.normpath(os.path.expanduser(raw))) is None, raw
    assert mac.allowed_path("~/Library-backup") == home / "Library-backup"


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


def test_outside_home_is_written_like_anywhere_else(home: Path) -> None:
    """A sibling of the home folder is just a folder; only the protected places refuse."""
    sibling = Path(str(home) + "-secret")
    sibling.mkdir()
    out = mac.write_local(str(sibling / "note.txt"), "x")
    assert out["created"] and (sibling / "note.txt").read_text() == "x"
    assert mac.read_local(str(sibling / "note.txt"))["text"] == "x"


def test_symlink_dir_cannot_reach_the_data_folder(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    (data / "personal-os.db").write_text("sentinel")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    (home / "Desktop" / "dirlink").symlink_to(data, target_is_directory=True)
    (home / "Desktop" / "movable.txt").write_text("stay")
    for raw in ("~/Desktop/dirlink", "~/Desktop/dirlink/personal-os.db", "~/Desktop/dirlink/new.txt"):
        with pytest.raises(mac.LocalPathError):
            mac.read_local(raw)
        with pytest.raises(mac.LocalPathError):
            mac.write_local(raw, "x")
    with pytest.raises(mac.LocalPathError):
        mac.move_local("~/Desktop/movable.txt", "~/Desktop/dirlink/movable.txt")
    with pytest.raises(mac.LocalPathError):
        mac.move_local("~/Desktop/dirlink/personal-os.db", "~/Desktop/stolen.db")
    assert (home / "Desktop" / "movable.txt").read_text() == "stay"
    assert not (data / "new.txt").exists() and not (data / "movable.txt").exists()
    assert (data / "personal-os.db").read_text() == "sentinel"


def test_a_folder_holding_the_data_folder_cannot_be_moved_or_trashed(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = home / "Desktop" / "Holder" / "GrainData"
    data.mkdir(parents=True)
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    with pytest.raises(mac.LocalPathError):
        mac.trash_local("~/Desktop/Holder")
    with pytest.raises(mac.LocalPathError):
        mac.move_local("~/Desktop/Holder", "~/Documents/Holder")
    assert data.is_dir()


def test_protected_paths_are_refused_for_every_path_tool(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The data folder (and its database), the Grain app and a symlink into either are refused, in any spelling."""
    data = home / "Desktop" / "GrainData"
    (data / "uploads").mkdir(parents=True)
    (data / "personal-os.db").write_text("sentinel")
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    (home / "Documents" / "db-link").symlink_to(data / "personal-os.db")
    for raw in (str(data), str(data / "personal-os.db"), "~/Desktop/GrainData/uploads/x.txt", "~/Desktop/GRAINDATA/personal-os.db",
                "~/Desktop/../Desktop/graindata", "~/Documents/db-link", "/Applications/Grain.app",
                "/Applications/Grain.app/Contents/MacOS/Grain", "/applications/grain.app/Contents/Info.plist"):
        assert mac.protected_reason(raw), raw
        with pytest.raises(mac.LocalPathError):
            mac.allowed_path(raw)
    assert mac.protected_reason(str(home / "Desktop" / "GrainData-backup")) is None
    assert mac.protected_reason("/Applications/Grain.app.bak/x") is None
    assert mac.protected_reason("~/Documents") is None
    assert (data / "personal-os.db").read_text() == "sentinel"


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
        str(db), "/Applications/Grain.app/Contents/Info.plist", str(home / "Library" / "Keychains" / "login.keychain-db"),
        str(home / ".ssh" / "id_rsa"), "/etc/passwd", str(ok),
    ]))
    out = asyncio.run(mac.mdfind("lease"))
    assert [r["path"] for r in out["results"]] == ["/etc/passwd", str(ok)]


def test_mdfind_refuses_the_data_folder_and_searches_elsewhere(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = home / "Desktop" / "GrainData"
    data.mkdir()
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    calls = fake_exec(monkeypatch, FakeProc())
    with pytest.raises(mac.LocalPathError):
        asyncio.run(mac.mdfind("x", folders=[str(data)]))
    asyncio.run(mac.mdfind("x", folders=["/tmp"]))  # outside home is searchable
    assert calls and calls[0][:2] == ["mdfind", "-onlyin"]
    with pytest.raises(ValueError):
        asyncio.run(mac.mdfind("  -- "))


# ---- read_local_file ----
def test_a_token_in_a_local_file_is_stripped_for_the_model(home: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    (home / "Desktop" / "secret.md").write_text(f"key {pat}\n")
    tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    out = asyncio.run(tb.call("read_local_file", {"path": "~/Desktop/secret.md"}, {}))
    assert pat not in out["text"] and "[github-pat]" in out["text"]
    assert (home / "Desktop" / "secret.md").read_text() == f"key {pat}\n"


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


def test_write_local_refuses_bad_paths_modes_and_launchers(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = home / "Desktop" / "GrainData"
    data.mkdir()
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    for bad in (str(data / "x.txt"), "/Applications/Grain.app/x.txt", "~/Desktop/run.command", "~/Desktop/Thing.app",
                "~/Desktop/Evil.app/Contents/MacOS/run", "~/Desktop/link.inetloc", "~/Desktop/open.fileloc", "/dev/null"):
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
                     ("~/Downloads/lease.pdf", "/Applications/Grain.app/x.pdf"),  # the Grain app
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


def test_a_token_in_a_local_path_error_is_stripped(home: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb = make_toolbox()
    out = asyncio.run(tb.call("trash_local_file", {"path": f"~/Desktop/missing-{pat}.pdf"}, {}))
    assert "error" in out
    assert pat not in out["error"] and "[github-pat]" in out["error"]
    assert not (home / "Desktop" / f"missing-{pat}.pdf").exists()
    missing = asyncio.run(tb.call("read_local_file", {"path": f"~/Desktop/missing-{pat}.pdf"}, {}))
    assert pat not in missing["error"] and "[github-pat]" in missing["error"]


def test_a_token_in_a_local_path_is_stripped(home: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb = make_toolbox()
    name = f"note-{pat}.md"
    wrote = asyncio.run(tb.call("write_local_file", {"path": f"~/Desktop/{name}", "content": "hi"}, {}))
    assert pat not in wrote["path"] and "[github-pat]" in wrote["path"]
    assert (home / "Desktop" / name).read_text() == "hi"
    moved = asyncio.run(tb.call("move_local_file", {"path": f"~/Desktop/{name}", "to": f"~/Documents/{name}"}, {}))
    assert pat not in moved["from"] and pat not in moved["path"]
    assert (home / "Documents" / name).is_file()
    trashed = asyncio.run(tb.call("trash_local_file", {"path": f"~/Documents/{name}"}, {}))
    assert pat not in trashed["path"] and pat not in trashed["trashed_to"]
    assert "[github-pat]" in trashed["path"]


def test_a_token_in_a_local_write_error_is_stripped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError(f"cannot write {pat}")

    monkeypatch.setattr(mac, "write_local", boom)
    monkeypatch.setattr(mac, "trash_local", boom)
    tb = make_toolbox()
    wrote = asyncio.run(tb.call("write_local_file", {"path": "~/Desktop/note.md", "content": "hi"}, {}))
    assert pat not in str(wrote) and "[github-pat]" in wrote["error"]
    assert not (home / "Desktop" / "note.md").exists()
    trashed = asyncio.run(tb.call("trash_local_file", {"path": "~/Desktop/note.md"}, {}))
    assert pat not in str(trashed) and "[github-pat]" in trashed["error"]


def test_file_writes_ask_first_and_errors_are_shaped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tb = make_toolbox()
    modes = tb.effective({}, None, None)
    for n in ("write_local_file", "move_local_file", "trash_local_file"):
        # Moving and trashing are under alwaysAsk by default; a write runs, and still asks for a credential store.
        assert modes[n] == ("on" if n == "write_local_file" else "ask"), n
        assert tb.available(n), n            # plain file work, no Mac-only binary
        assert tb.specs[n].group == "files"
    data = home / "Desktop" / "GrainData"
    data.mkdir()
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    out = asyncio.run(tb.call("write_local_file", {"path": str(data / "x.txt"), "content": "x"}, {}))
    assert out["error"] and out["field"] == "path" and out["try_instead"]
    out = asyncio.run(tb.call("move_local_file", {"path": "~/Desktop/nope.md", "to": "~/Desktop/b.md"}, {}))
    assert out["error"] and out["field"] == "path"
    out = asyncio.run(tb.call("write_local_file", {"path": "~/Desktop/ok.md", "content": "hi"}, {}))
    assert out["created"] and (home / "Desktop" / "ok.md").read_text() == "hi"
    # outside the home folder and in a dot-folder are just paths now
    out = asyncio.run(tb.call("write_local_file", {"path": "~/.config/tool/x.toml", "content": "a = 1"}, {}))
    assert out["created"]
    # a credential store needs the user's yes: refused without it, written with it
    assert tb.fs_needs_ask("write_local_file", {"path": "~/.ssh/config", "content": "x"}, {})
    out = asyncio.run(tb.call("write_local_file", {"path": "~/.ssh/config", "content": "Host x"}, {}))
    assert "approval" in out["error"] and not (home / ".ssh" / "config").exists()
    out = asyncio.run(tb.call("write_local_file", {"path": "~/.ssh/config", "content": "Host x"}, {"fs_outside_ok": True}))
    assert out["created"] and (home / ".ssh" / "config").read_text() == "Host x"
    (home / "Desktop" / "t.txt").write_text("x")
    assert tb.fs_needs_ask("move_local_file", {"path": "~/Desktop/t.txt", "to": "~/.ssh/t.txt"}, {})
    assert tb.fs_needs_ask("trash_local_file", {"path": "~/.ssh/config"}, {})
    assert not tb.fs_needs_ask("trash_local_file", {"path": "~/Desktop/t.txt"}, {})
    # a reply that read untrusted content asks before any write, a credential store or not
    assert tb.fs_needs_ask("write_local_file", {"path": "~/Desktop/ok.md", "content": "x"}, {"tainted": True, "taint_sources": ["fetch_url"]})
    # a write into a system area is a soft force; the home folder, temp folders and /Volumes are not
    assert tb.forces_ask("write_local_file", {"path": "/etc/hosts.grain", "content": "x"}, {})
    assert tb.forces_ask("fs_edit", {"path": "/Library/Preferences/x.plist", "old": "a", "new": "b"}, {})
    assert tb.forces_ask("move_local_file", {"path": "~/Desktop/t.txt", "to": "/usr/local/t.txt"}, {})
    for ok in ("~/Desktop/x.md", "/tmp/x.md", "/private/tmp/x.md", "/Volumes/Backup/x.md", str(Path(os.environ.get("TMPDIR", "/tmp")) / "x.md")):
        assert not tb.forces_ask("write_local_file", {"path": ok, "content": "x"}, {}), ok


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


def test_bridge_links_and_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"url": "https://e.com/", "title": "", "text": "t",
                                         "links": [{"text": "a", "href": f"https://e.com/{i}"} for i in range(60)]})
    real = httpx.AsyncClient
    monkeypatch.setattr(mac.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    b = mac.PageBridge()
    b.register("http://127.0.0.1:9000", "s" * 32)
    out = asyncio.run(b.open_page("https://e.com", wait_for=" #app ", links=True))
    assert seen[0]["waitForSelector"] == "#app" and seen[0]["links"] is True and len(out["links"]) == 40
    out = asyncio.run(b.open_page("https://e.com"))
    assert "links" not in out and "waitForSelector" not in seen[1] and "links" not in seen[1]


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


def test_a_token_in_shortcut_output_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    async def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"shortcut": "Export", "ok": True, "output": f"saved {pat}", "error": ""}

    monkeypatch.setattr(mac, "run_shortcut", fake)
    tb = make_toolbox()
    out = asyncio.run(tb.specs["run_shortcut"].fn({}, name="Export"))
    assert pat not in out["output"] and "[github-pat]" in out["output"]


def test_a_token_in_a_shortcut_name_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    seen: list[str] = []

    async def fake(name: str, text_input: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
        seen.append(name)
        return {"shortcut": name, "ok": True, "output": "done", "error": ""}

    async def bad_list(folder: str | None = None) -> list[str]:
        raise ValueError(f"no folder {pat}")

    monkeypatch.setattr(mac, "run_shortcut", fake)
    monkeypatch.setattr(mac, "list_shortcuts", bad_list)
    tb = make_toolbox()
    out = asyncio.run(tb.specs["run_shortcut"].fn({}, name=pat))
    assert seen == [pat]
    assert pat not in str(out) and out["shortcut"] == "[github-pat]" and out["output"] == "done"
    err = asyncio.run(tb.call("list_shortcuts", {"folder": pat}, {}))
    assert pat not in str(err) and "[github-pat]" in err["error"]


def test_a_token_in_a_spotlight_hit_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"

    async def fake(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"query": "x", "results": [{"path": f"/Users/me/Desktop/{pat}.txt", "name": f"{pat}.txt"}], "count": 1}

    monkeypatch.setattr(mac, "mdfind", fake)
    out = asyncio.run(make_toolbox().call("find_files", {"query": "x"}, {}))
    hit = out["results"][0]
    assert pat not in hit["path"] and pat not in hit["name"]
    assert "[github-pat]" in hit["path"] and "[github-pat]" in hit["name"]


def test_a_token_in_a_file_search_query_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    seen: list[str] = []

    async def fake(query: str, **_kw: Any) -> dict[str, Any]:
        seen.append(query)
        return {"query": query, "folders": [f"~/Desktop/{pat}"], "results": [], "count": 0}

    monkeypatch.setattr(mac, "mdfind", fake)
    out = asyncio.run(make_toolbox().call("find_files", {"query": pat, "folders": [f"~/Desktop/{pat}"]}, {}))
    assert seen == [pat]
    assert pat not in str(out)
    assert out["query"] == "[github-pat]" and "[github-pat]" in out["folders"][0]


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


def test_a_token_in_an_opened_page_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    called: list[str] = []

    async def fake_open(url: str, **_kw: Any) -> dict[str, Any]:
        called.append(url)
        return {"url": url, "title": "Ex", "text": "hello",
                "links": [{"text": "docs", "href": f"https://example.com/{pat}"}]}

    async def no_dns(_host: str) -> None:
        return None

    monkeypatch.setattr(mac.page_bridge, "open_page", fake_open)
    monkeypatch.setattr(tools, "_resolve", no_dns)
    tb = make_toolbox()
    url = f"https://example.com/{pat}"
    out = asyncio.run(tb.call("open_page", {"url": url}, {}))
    assert called == [url]
    assert pat not in str(out)
    assert "[github-pat]" in out["url"] and "[github-pat]" in out["links"][0]["href"]
    assert out["text"] == "hello"
    err = asyncio.run(tb.call("open_page", {"url": url}, {"tainted": True, "allowed_urls": set()}))
    assert called == [url]
    assert pat not in str(err) and "restricted" in err["error"] and "[github-pat]" in err["error"]


def test_tool_errors_are_shaped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_exec(monkeypatch, FakeProc())
    tb = make_toolbox()
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE")
    assert tb.fs_needs_ask("read_local_file", {"path": "~/.ssh/id_ed25519"}, {})
    out = asyncio.run(tb.call("read_local_file", {"path": "~/.ssh/id_ed25519"}, {}))
    assert "approval" in out["error"] and out["try_instead"] and "PRIVATE" not in str(out)
    out = asyncio.run(tb.call("read_local_file", {"path": "~/.ssh/id_ed25519"}, {"fs_outside_ok": True}))
    assert out["text"] == "PRIVATE"  # the user said yes
    out = asyncio.run(tb.call("read_local_file", {"path": "/dev/null"}, {"fs_outside_ok": True}))
    assert "off limits" in out["error"]  # a device file is never approvable
    assert not tb.fs_needs_ask("read_local_file", {"path": "~/Documents/x.txt"}, {})
    data = home / "Desktop" / "GrainData"
    data.mkdir()
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    out = asyncio.run(tb.call("find_files", {"query": "x", "folders": [str(data)]}, {}))
    assert out["error"] and out["field"] == "folders"
    out = asyncio.run(tb.call("read_local_file", {"path": str(data)}, {"fs_outside_ok": True}))
    assert "off limits" in out["error"]  # no approval buys the protected places


