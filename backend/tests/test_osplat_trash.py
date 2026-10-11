"""Moving to the Trash per platform: the freedesktop layout on Linux, gio across drives, the Recycle Bin, and macOS unchanged.
Run: pytest backend/tests/test_osplat_trash.py"""
from __future__ import annotations

import errno
import os
import re
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mac, osplat  # noqa: E402
from personal_os.osplat import trash as trashmod  # noqa: E402


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    (h / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_DATA_HOME", str(h / ".local" / "share"))
    return h


def _trash(h: Path) -> Path:
    return h / ".local" / "share" / "Trash"


def test_linux_file_and_folder(home: Path) -> None:
    f = home / "Desktop" / "my notes.txt"
    f.write_text("hi")
    d = home / "Desktop" / "Folder"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "a").write_text("a")
    with osplat.use("linux"):
        r = mac.trash_local(str(f))
        r2 = mac.trash_local(str(d))
    t = _trash(home)
    assert r["trashed_to"] == str(t / "files" / "my notes.txt") and not f.exists()
    assert "file manager's Trash" in r["note"]
    assert (t / "files" / "my notes.txt").read_text() == "hi"
    assert (t / "files" / "Folder" / "sub" / "a").read_text() == "a" and r2["trashed_to"] == str(t / "files" / "Folder")
    info = (t / "info" / "my notes.txt.trashinfo").read_text().splitlines()
    assert info[0] == "[Trash Info]"
    assert info[1] == "Path=" + urllib.parse.quote(str(f), safe="/") and "%20" in info[1]
    assert re.fullmatch(r"DeletionDate=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", info[2])
    assert (t / "info" / "Folder.trashinfo").is_file()
    for sub in ("", "files", "info"):
        assert (t / sub).stat().st_mode & 0o777 == 0o700


def test_linux_collisions_get_numbered_names(home: Path) -> None:
    got = []
    with osplat.use("linux"):
        for i in range(3):
            f = home / "Desktop" / "dupe.pdf"
            f.write_text(str(i))
            got.append(mac.trash_local(str(f))["trashed_to"])
    t = _trash(home)
    assert got == [str(t / "files" / n) for n in ("dupe.pdf", "dupe 2.pdf", "dupe 3.pdf")]
    assert (t / "info" / "dupe 3.pdf.trashinfo").is_file() and (t / "files" / "dupe 2.pdf").read_text() == "1"


def test_linux_stale_info_claims_the_name(home: Path) -> None:
    t = _trash(home)
    (t / "info").mkdir(parents=True)
    (t / "info" / "x.txt.trashinfo").write_text("[Trash Info]\n")
    f = home / "Desktop" / "x.txt"
    f.write_text("x")
    with osplat.use("linux"):
        assert mac.trash_local(str(f))["trashed_to"] == str(t / "files" / "x 2.txt")


def test_linux_symlinked_trash_is_refused(home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".local" / "share").mkdir(parents=True)
    _trash(home).symlink_to(elsewhere)
    f = home / "Desktop" / "keep.txt"
    f.write_text("k")
    with osplat.use("linux"), pytest.raises(mac.LocalPathError, match="symlink"):
        mac.trash_local(str(f))
    assert f.exists() and not list(elsewhere.iterdir())


def test_linux_symlinked_files_dir_is_refused(home: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _trash(home).mkdir(parents=True)
    (_trash(home) / "files").symlink_to(elsewhere)
    f = home / "Desktop" / "keep.txt"
    f.write_text("k")
    with osplat.use("linux"), pytest.raises(mac.LocalPathError, match="symlink"):
        mac.trash_local(str(f))
    assert f.exists() and not list(elsewhere.iterdir())


def _exdev(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.rename

    def fake(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if k.get("dst_dir_fd") is not None:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "rename", fake)


def test_linux_other_drive_uses_gio(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _exdev(monkeypatch)
    calls: list[list[str]] = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(trashmod.shutil, "which", lambda n: "/usr/bin/gio" if n == "gio" else None)
    monkeypatch.setattr(trashmod.subprocess, "run", lambda argv, **k: (calls.append(argv), R())[1])
    f = home / "Desktop" / "far.txt"
    f.write_text("f")
    with osplat.use("linux"):
        r = mac.trash_local(str(f))
    assert calls == [["/usr/bin/gio", "trash", str(f)]] and r["trashed_to"]
    assert not list((_trash(home) / "info").iterdir())  # the claimed name was released


def test_linux_other_drive_without_gio_refuses(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _exdev(monkeypatch)
    monkeypatch.setattr(trashmod.shutil, "which", lambda n: None)
    f = home / "Desktop" / "far.txt"
    f.write_text("f")
    with osplat.use("linux"), pytest.raises(mac.LocalPathError, match="across drives without gio"):
        mac.trash_local(str(f))
    assert f.exists() and not list((_trash(home) / "info").iterdir())


def test_windows_without_send2trash_refuses(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "send2trash", None)
    f = home / "Desktop" / "w.txt"
    f.write_text("w")
    with osplat.use("win32"), pytest.raises(mac.LocalPathError, match="send2trash"):
        mac.trash_local(str(f))
    assert f.exists()


def test_windows_with_send2trash(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import types
    seen: list[str] = []
    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=seen.append))
    f = home / "Desktop" / "w.txt"
    f.write_text("w")
    with osplat.use("win32"):
        r = mac.trash_local(str(f))
    assert seen == [str(f)] and "Recycle Bin" in r["note"]


def test_darwin_is_unchanged(home: Path) -> None:
    f = home / "Desktop" / "d.txt"
    f.write_text("d")
    with osplat.use("darwin"):
        r = mac.trash_local(str(f))
    assert r["trashed_to"] == str(home / ".Trash" / "d.txt") and (home / ".Trash" / "d.txt").read_text() == "d"
    assert r["note"] == "in the Trash; the user can put it back from the Finder"
    assert not (home / ".local").exists()


def test_undo_of_a_created_file_trashes_it_on_linux(home: Path) -> None:
    from personal_os.db import Database
    from personal_os.filesnap import FileSnapshots
    db = Database(str(home / "db"))
    fs = FileSnapshots(db, db.data_dir / "snapshots", lambda: {})
    ctx = {"conversation_id": "c1", "message_id": "m1"}
    f = home / "Desktop" / "new.md"
    snap = fs.capture("create", str(f), ctx)
    mac.write_local(str(f), "agent", "create")
    fs.finalize(snap["snapshot_id"], str(f))
    with osplat.use("linux"):
        fs.restore(snap["snapshot_id"])
    assert not f.exists() and (_trash(home) / "files" / "new.md").read_text() == "agent"
    assert (_trash(home) / "info" / "new.md.trashinfo").is_file()
