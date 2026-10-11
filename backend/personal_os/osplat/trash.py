"""Moving a file or folder to the user's Trash, one implementation per platform.

Each function takes an absolute path that the caller has already validated and returns where the item ended up (a path
when the platform has one, otherwise a label). A refusal raises TrashRefused, an OSError whose message is meant for the user.
"""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from .base import Unsupported, home

MAC_NOTE = "in the Trash; the user can put it back from the Finder"
LINUX_NOTE = "in the Trash; the user can restore it from the file manager's Trash"
WINDOWS_NOTE = "in the Recycle Bin; the user can restore it from there"


class TrashRefused(Unsupported):
    """The item cannot be moved to the Trash; the message says why."""


def _dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


def _free_name(fd: int, src_name: str):
    """Candidate names in the file manager's order: `notes.txt`, `notes 2.txt`, ... (lazily, without following links)."""
    p = Path(src_name)
    yield src_name
    n = 2
    while True:
        yield f"{p.stem} {n}{p.suffix}"
        n += 1


# ---- macOS: ~/.Trash ----
def darwin_trash(src: str) -> str:
    trash = home() / ".Trash"
    if trash.is_symlink():
        raise TrashRefused("the Trash is a symlink and is off limits")
    if not trash.exists():
        trash.mkdir()
    elif not trash.is_dir():
        raise TrashRefused("the Trash is off limits")
    if trash.is_symlink():
        raise TrashRefused("the Trash is a symlink and is off limits")
    try:
        fd = os.open(trash, _dir_flags())
    except OSError as e:
        # Without Full Disk Access macOS refuses the open; the error says what to do rather than reading as a policy.
        raise TrashRefused("the Trash could not be opened: grant Grain Full Disk Access in System Settings > Privacy & Security, "
                           "or move the file somewhere else instead") from e
    try:
        name = next(n for n in _free_name(fd, Path(src).name) if not _exists(n, fd))
        try:
            os.rename(src, name, dst_dir_fd=fd)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise TrashRefused(f"could not move {Path(src).name} to the Trash") from e
            dest = trash / name
            if trash.is_symlink() or dest.is_symlink():
                raise TrashRefused("the Trash is a symlink and is off limits") from e
            shutil.move(src, os.fspath(dest))
    finally:
        os.close(fd)
    return str(trash / name)


def _exists(name: str, fd: int) -> bool:
    try:
        os.lstat(name, dir_fd=fd)
        return True
    except FileNotFoundError:
        return False


# ---- Linux: the freedesktop Trash ----
def linux_trash_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "Trash"


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise TrashRefused("the Trash is a symlink and is off limits")
    if path.exists():
        if not path.is_dir():
            raise TrashRefused("the Trash is off limits")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if path.is_symlink():
        raise TrashRefused("the Trash is a symlink and is off limits")


def _trashinfo(src: str) -> bytes:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    return f"[Trash Info]\nPath={urllib.parse.quote(src, safe='/')}\nDeletionDate={stamp}\n".encode()


def linux_trash(src: str) -> str:
    src = os.path.abspath(src)
    root = linux_trash_root()
    files, info = root / "files", root / "info"
    try:
        for d in (root, files, info):
            _private_dir(d)
        ffd = os.open(files, _dir_flags())
    except OSError as e:
        if isinstance(e, TrashRefused):
            raise
        raise TrashRefused(f"the Trash could not be opened: {e.strerror or e}") from e
    try:
        try:
            ifd = os.open(info, _dir_flags())
        except OSError as e:
            raise TrashRefused(f"the Trash could not be opened: {e.strerror or e}") from e
        try:
            name = _claim_name(ffd, ifd, Path(src).name, _trashinfo(src))
            try:
                os.rename(src, name, dst_dir_fd=ffd, src_dir_fd=None)
            except OSError as e:
                os.unlink(name + ".trashinfo", dir_fd=ifd)
                if e.errno != errno.EXDEV:
                    raise TrashRefused(f"could not move {Path(src).name} to the Trash: {e.strerror or e}") from e
                return _gio_trash(src)
        finally:
            os.close(ifd)
    finally:
        os.close(ffd)
    return str(files / name)


def _claim_name(ffd: int, ifd: int, base: str, content: bytes) -> str:
    """Create `<name>.trashinfo` with O_EXCL so two trashers never take the same name; the info file claims it."""
    for name in _free_name(ffd, base):
        if _exists(name, ffd):
            continue
        try:
            fd = os.open(name + ".trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=ifd)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        return name
    raise TrashRefused("no free name in the Trash")  # unreachable


def _gio_trash(src: str) -> str:
    """The item is on another drive: that drive's own Trash is the right place, and only `gio` knows how to use it."""
    gio = shutil.which("gio")
    if not gio:
        raise TrashRefused("cannot move to the Trash across drives without gio; install gio (libglib2.0-bin) or move the file elsewhere")
    try:
        r = subprocess.run([gio, "trash", src], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise TrashRefused(f"gio could not move the item to the Trash: {e}") from e
    if r.returncode != 0:
        why = (r.stderr or r.stdout or "").strip().splitlines()
        raise TrashRefused(f"gio could not move the item to the Trash: {why[0] if why else 'exit ' + str(r.returncode)}")
    top = Path(src)
    while not os.path.ismount(top) and top != top.parent:
        top = top.parent
    uid, name = os.getuid(), Path(src).name
    for cand in (top / f".Trash-{uid}" / "files" / name, top / ".Trash" / str(uid) / "files" / name):
        if os.path.lexists(cand):
            return str(cand)
    return f"the Trash on {top}"


# ---- Windows: the Recycle Bin ----
def win32_trash(src: str) -> str:
    try:
        from send2trash import send2trash  # type: ignore[import-not-found]
    except ImportError as e:
        raise TrashRefused("the Recycle Bin needs the send2trash package, which is not installed") from e
    try:
        send2trash(src)
    except OSError as e:
        raise TrashRefused(f"could not move {Path(src).name} to the Recycle Bin: {e.strerror or e}") from e
    return "the Recycle Bin"
