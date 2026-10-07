"""Reach into the rest of the Mac the boring way: Spotlight, the file system, Shortcuts, and an offscreen page loader.

This module is the one place for path policy. Every path, read or written, goes through `allowed_path`: anywhere on
this Mac, symlinks resolved first, except what `protected_reason` names (Grain's own data folder, which holds the
database, the auth token and the secrets, and the Grain app itself): that is refused in every permission mode. The
credential stores (`sensitive_reason`: ssh and cloud keys, keychains, browser cookies and saved passwords, .env files)
are reachable but need the user's explicit approval, which fsx.py and the reply loops ask for. Both checks judge the
path as spelled AND as resolved, case-insensitively, so a symlink cannot walk around them. Writes never clobber
silently (`mode="create"` is the default) and nothing is deleted outright: `trash` moves items to ~/.Trash, where the
user can put them back.

Nothing here drives the screen. `mdfind` and `shortcuts` are plain subprocesses (argv, never a shell);
the page loader is an offscreen Electron window the main process owns, reached over a loopback bridge
that main registers with this backend (see src/main/pagefetch.ts). The agent itself holds no TCC
grants: Spotlight needs none, and a Shortcut runs with whatever the user granted it when they built it.
"""
from __future__ import annotations

import asyncio
import errno
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from .extract_text import extract_text

DEFAULT_ROOTS = ("~/Desktop", "~/Documents")
APP_BUNDLE = "/Applications/Grain.app"
MAX_READ_BYTES = 20 * 1024 * 1024
MAX_WRITE_CHARS = 400_000
# Suffixes macOS will run when the user double-clicks the file. The agent writes documents, not launchers.
BLOCKED_WRITE_SUFFIXES = frozenset({
    ".command", ".app", ".scpt", ".scptd", ".applescript", ".workflow", ".terminal", ".shortcut",
    ".inetloc", ".fileloc",  # a double-clicked internet location opens its URL with no quarantine prompt
})
WRITE_MODES = ("create", "overwrite", "append")
MAX_SHORTCUT_INPUT = 100_000
MAX_SHORTCUT_OUTPUT = 20_000
PAGE_MAX_CHARS = 60_000


class LocalPathError(Exception):
    pass


def is_mac() -> bool:
    return sys.platform == "darwin"


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def _app_data_dir() -> Path | None:
    """The folder that holds the app database, auth token and uploads. Same rule as db.data_dir_from_env."""
    try:
        from .db import data_dir_from_env
        return data_dir_from_env()
    except OSError:
        return None


def mcp_media_dir() -> Path | None:
    """Where pictures and files an MCP tool returned are saved, so view_image can look at them.

    Inside the app data folder, which allowed_path refuses on purpose: only view_image carves this one
    subfolder back out (vision.resolve), so the file read/write/move tools stay out of it."""
    data = _app_data_dir()
    return data / "mcp_media" if data is not None else None


def _under(path: Path, root: Path) -> bool:
    """True when `path` is `root` or a directory inside it.

    Component-wise and case-insensitive, so `GrainData` is the same folder as `graindata` and is not
    a prefix of `GrainData-backup`.
    """
    pp, rp = path.parts, root.parts
    if len(pp) < len(rp):
        return False
    return all(a.casefold() == b.casefold() for a, b in zip(rp, pp))


# ---- protected: refused outright, in every permission mode ----
def _bundle_of(p: Path) -> Path | None:
    """The `*.app` folder `p` sits in, or None."""
    for q in (p, *p.parents):
        if q.suffix.lower() == ".app":
            return q
    return None


def protected_paths() -> list[Path]:
    """The folders no file tool may touch: the app data folder (as configured and as resolved), /Applications/Grain.app
    and the app bundle this backend is running from, when that is a different one."""
    out: list[Path] = []

    def add(p: Path | None) -> None:
        if p is None:
            return
        for q in (p, p.resolve()):
            if q not in out:
                out.append(q)

    data = _app_data_dir()
    if data is not None:
        raw = Path(os.environ.get("PERSONAL_OS_DATA_DIR", "./data")).expanduser()
        add(raw if raw.is_absolute() else raw.absolute())
        add(data)
    add(Path(APP_BUNDLE))
    for here in (Path(sys.executable), Path(__file__)):
        add(_bundle_of(here.resolve()))
    return out


def _spelled(raw: str | Path) -> Path:
    """The path as given: `~` expanded, relative to home, `..` folded, symlinks not followed."""
    p = Path(os.path.expanduser(str(raw).strip()))
    if not p.is_absolute():
        p = home() / p
    return Path(os.path.normpath(p))


def protected_reason(*paths: str | Path) -> str | None:
    """Why a path is off limits to every agent file tool, or None. Judged on the spelled path and the resolved one."""
    prot = protected_paths()
    for raw in paths:
        if not str(raw).strip():
            continue
        spelled = _spelled(raw)
        try:
            resolved = spelled.resolve()
        except (OSError, RuntimeError):
            resolved = spelled
        for p in (spelled, resolved):
            for root in prot:
                if _under(p, root):
                    return "Grain's own data folder and app are off limits" if root.suffix.lower() != ".app" \
                        else "the Grain app is off limits"
    return None


# ---- sensitive: reachable, but only with the user's approval ----
SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".azure", "gcloud", ".docker"})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk"})
SENSITIVE_NAMES = frozenset({".netrc", ".npmrc", ".pgpass", ".git-credentials", ".pypirc", "credentials", "credentials.json",
                             "credentials.toml", ".vault-token", "application_default_credentials.json"})
SENSITIVE_PREFIXES = ("/dev/", "/proc/")
KEY_FILE_RE = re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(?!.*\.pub$)")
# Keychains and cookie jars: whole folders under the home folder (or /Library) the user has to approve.
KEYCHAIN_DIRS = ("Library/Keychains", "Library/Cookies", "Library/Containers/com.apple.Safari/Data/Library/Cookies")
SYSTEM_KEYCHAINS = ("/Library/Keychains",)
# Where the browsers keep a profile, relative to ~/Library/Application Support, and the files in it that hold sessions
# and saved passwords.
BROWSER_DIRS = ("Google/Chrome", "Arc", "BraveSoftware", "Microsoft Edge", "Chromium", "Vivaldi", "Firefox")
BROWSER_FILE_NAMES = ("Cookies", "Cookies-journal", "Login Data", "Login Data-journal", "Login Data For Account", "Web Data",
                      "Web Data-journal", "cookies.sqlite", "cookies.sqlite-wal", "key4.db", "logins.json")
BROWSER_FILES = frozenset(n.lower() for n in BROWSER_FILE_NAMES)
SENSITIVE_HOME_FILES = (".config/gh/hosts.yml", ".cargo/credentials.toml")
# What the shell sandbox denies for read and write: the same stores as `sensitive_reason`, as paths a profile can name.
CRED_HOME_DIRS = tuple(sorted(SENSITIVE_DIRS - {"gcloud"})) + (".config/gcloud",) + KEYCHAIN_DIRS
CRED_HOME_FILES = tuple(sorted(n for n in SENSITIVE_NAMES if n.startswith("."))) + SENSITIVE_HOME_FILES
# What the System access panel shows.
PROTECTED_LABELS = ("Grain's data folder", APP_BUNDLE)
SENSITIVE_LABELS = ("~/.ssh", "Keychains", "Browser cookies and saved passwords", "~/.aws, ~/.gnupg, ~/.netrc, gh tokens", ".env files")


def _holds_protected(p: Path) -> str | None:
    """A folder that contains a protected one: moving or trashing it would carry the data folder or the app along."""
    for root in protected_paths():
        if p != root and _under(root, p):
            return "that folder holds Grain's own data folder or app, which are off limits"
    return None


def _homes() -> list[Path]:
    out = [Path(os.path.expanduser("~")), home()]
    return list(dict.fromkeys(out))


def is_device(path: str | Path) -> bool:
    s = str(path)
    return s.startswith(SENSITIVE_PREFIXES) or s in ("/dev", "/proc")


def sensitive_reason(*paths: str | Path) -> str | None:
    """Why a path needs the user's explicit approval (or is a device file, which never does), or None. Judge the
    spelled path AND the resolved one: a symlink named notes.txt that points at ~/.aws/credentials is caught by the
    second. Folders are judged by their own spelling too, so listing ~/.ssh is as sensitive as reading a key in it."""
    for raw in paths:
        s = str(raw)
        if is_device(s):
            return "device and process files are off limits"
        parts = Path(s).parts
        name = parts[-1] if parts else ""
        low = name.lower()
        if low == ".env" or low.startswith(".env.") or low == ".envrc":
            return "environment files hold secrets"
        if KEY_FILE_RE.match(low) or Path(low).suffix in SENSITIVE_SUFFIXES:
            return "key files hold secrets"
        if low in SENSITIVE_NAMES:
            return "credential files hold secrets"
        if any(x.lower() in SENSITIVE_DIRS for x in parts[:-1]) or low in SENSITIVE_DIRS:
            return "credential folders hold secrets"
        p = Path(os.path.normpath(s))
        if any(_under(p, Path(k)) for k in SYSTEM_KEYCHAINS):
            return "keychains hold secrets"
        for h in _homes():
            if any(_under(p, h / k) for k in KEYCHAIN_DIRS):
                return "keychains and cookie jars hold secrets"
            if any(_under(p, h / f) for f in SENSITIVE_HOME_FILES):
                return "access tokens hold secrets"
            support = h / "Library" / "Application Support"
            if low in BROWSER_FILES and any(_under(p, support / d) for d in BROWSER_DIRS):
                return "browser cookies and saved passwords hold secrets"
    return None


def system_area(path: Path) -> bool:
    """True for a resolved path outside the home folder, the temp folders and /Volumes (/etc, /Library, /usr/local,
    /opt, /Applications...): a write there changes the machine, not the user's files, so it is a risky one."""
    temps = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders", tempfile.gettempdir(), os.environ.get("TMPDIR") or ""]
    safe = [*_homes(), Path("/Volumes")] + [Path(t).resolve() for t in temps if t] + [Path(t) for t in temps if t]
    return not any(_under(path, r) for r in safe)


def scope_summary() -> dict[str, list[str]]:
    """Display strings for the System access panel: what is off limits, and what asks first."""
    data = _app_data_dir()
    h = str(home())
    shown = str(data).replace(h, "~", 1) if data is not None else None
    return {"protected": [f"{PROTECTED_LABELS[0]} ({shown})" if shown else PROTECTED_LABELS[0], *PROTECTED_LABELS[1:]],
            "sensitive": list(SENSITIVE_LABELS)}


def allowed_path(raw: str) -> Path:
    """Resolve `raw` (symlinks included) and refuse only what `protected_reason` names: Grain's own data folder and app."""
    if not raw or not str(raw).strip():
        raise LocalPathError("empty path")
    p = _spelled(raw)
    r = p.resolve()
    if why := protected_reason(p, r):
        raise LocalPathError(why)
    return r


def readable_path(raw: str) -> Path:
    """`allowed_path` for a caller that shows a file's content with no approval card to lean on (the side panel, the
    chat's file list): a credential store is refused too."""
    p = allowed_path(raw)
    if why := sensitive_reason(_spelled(raw), p):
        raise LocalPathError(f"{p.name}: {why}")
    return p


def _meta(path: str) -> dict[str, Any]:
    row: dict[str, Any] = {"path": path, "name": os.path.basename(path)}
    try:
        st = os.stat(path)
    except OSError:
        return row
    row["kind"] = "folder" if os.path.isdir(path) else (Path(path).suffix.lower().lstrip(".") or "file")
    if not os.path.isdir(path):
        row["size"] = st.st_size
    row["modified"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
    return row


async def mdfind(query: str, *, folders: list[str] | None = None, name_only: bool = False, limit: int = 20,
                 timeout: float = 10.0) -> dict[str, Any]:
    """Spotlight search scoped with -onlyin. Reads at most `limit` + 1 paths, then stops mdfind."""
    q = (query or "").strip().lstrip("-").strip()  # mdfind has no `--`: a leading dash would parse as a flag
    if not q:
        raise ValueError("query is empty")
    roots = [allowed_path(f) for f in (folders or DEFAULT_ROOTS)]
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        raise LocalPathError("none of the folders to search exist")
    lim = max(1, min(int(limit), 100))
    argv = ["mdfind"]
    for r in roots:
        argv += ["-onlyin", str(r)]
    argv += ["-name", q] if name_only else [q]
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    paths: list[str] = []
    truncated = timed_out = False
    try:
        async def _read() -> None:
            nonlocal truncated
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                p = line.decode("utf-8", "replace").rstrip("\n")
                if not p:
                    continue
                if any(seg.startswith(".") for seg in Path(p).parts):  # Spotlight indexes some dot-folders
                    continue
                try:
                    allowed_path(p)  # mdfind's -onlyin is a hint; the result still has to pass the file policy
                except LocalPathError:
                    continue
                if sensitive_reason(p):  # a credential store is not something to list in a search result
                    continue
                if len(paths) >= lim:
                    truncated = True
                    return
                paths.append(p)
        await asyncio.wait_for(_read(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
    home_s = str(home())
    return {"query": q, "mode": "name" if name_only else "content",
            "folders": [str(r).replace(home_s, "~", 1) for r in roots],
            "results": [_meta(p) for p in paths], "count": len(paths), "truncated": truncated, "timed_out": timed_out}


def read_local(path: str, offset: int = 0, length: int = 8000) -> dict[str, Any]:
    p = allowed_path(path)
    if not p.exists():
        raise LocalPathError(f"{p} does not exist")
    if p.is_dir():
        entries = sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir() if not e.name.startswith("."))
        return {"path": str(p), "kind": "folder", "entries": entries[:200], "total_entries": len(entries)}
    size = p.stat().st_size
    if size > MAX_READ_BYTES:
        raise LocalPathError(f"{p.name} is {size // (1024 * 1024)} MB; the limit is {MAX_READ_BYTES // (1024 * 1024)} MB")
    text = extract_text(p.name, p.read_bytes())
    off = max(0, int(offset))
    n = max(1, min(int(length), 30000))
    return {"path": str(p), "total_chars": len(text), "offset": off, "text": text[off: off + n],
            "has_more": off + n < len(text)}


def _launcher_suffix(path: Path) -> str | None:
    """A blocked suffix on the file or any parent (Evil.app/Contents/MacOS/run is still an app bundle)."""
    for part in path.parts:
        suf = Path(part).suffix.lower()
        if suf in BLOCKED_WRITE_SUFFIXES:
            return suf
    return None


def _stated_path(raw: str) -> Path:
    """The path as given, expanded but not resolved, so a symlink is still a symlink."""
    if not raw or not str(raw).strip():
        raise LocalPathError("empty path")
    p = Path(os.path.expanduser(str(raw).strip()))
    if not p.is_absolute():
        p = home() / p
    return p


def _refuse_leaf_symlink(raw: str, verb: str) -> None:
    """`allowed_path` follows links. For a write, that would edit the target and call it the link."""
    if _stated_path(raw).is_symlink():
        raise LocalPathError(f"refusing to {verb} through a symlink")


def _writable_path(raw: str) -> Path:
    """`allowed_path` plus the rules that only matter when we are about to create or replace a file."""
    p = allowed_path(raw)
    if is_device(p):
        raise LocalPathError("device and process files are off limits")
    if suf := _launcher_suffix(p):
        raise LocalPathError(f"{suf} files are off limits; write a document instead")
    return p


def _write_nofollow(path: Path, text: str, mode: str) -> None:
    """Write `path` without following a symlink planted at the final component after the check. An overwrite goes to
    a temp file in the same folder and is renamed over the original, so a write that fails leaves it as it was."""
    data = text.encode("utf-8")  # text that cannot be encoded fails here, before anything is opened or truncated
    parent = path.parent
    flags = os.O_RDONLY | os.O_DIRECTORY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags |= nofollow
    try:
        dirfd = os.open(parent, flags)
    except OSError as e:
        raise LocalPathError(f"could not open {parent.name}") from e
    try:
        if mode == "overwrite":
            _replace_at(dirfd, path.name, data, nofollow)
            return
        oflags = os.O_WRONLY | os.O_CREAT | nofollow
        oflags |= os.O_APPEND if mode == "append" else os.O_TRUNC
        if mode == "create":
            oflags |= os.O_EXCL  # the exists() check before this can lose a race; the kernel cannot
        try:
            fd = os.open(path.name, oflags, 0o644, dir_fd=dirfd)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise LocalPathError(f"{path.name} already exists; pass mode='overwrite' to replace it or mode='append' to add to it") from e
            if e.errno == errno.ELOOP:
                raise LocalPathError("refusing to write through a symlink") from e
            raise
        with os.fdopen(fd, "ab" if mode == "append" else "wb") as f:
            f.write(data)
    finally:
        os.close(dirfd)


def _replace_at(dirfd: int, name: str, data: bytes, nofollow: int) -> None:
    """Write `data` to a temp file in the folder `dirfd`, then rename it over `name`. A rename replaces a symlink
    planted at `name` instead of writing through it."""
    try:
        perm = os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode & 0o777
    except FileNotFoundError:
        perm = 0o644
    tmp = f".grain-write-{os.urandom(6).hex()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=dirfd)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            os.fchmod(f.fileno(), perm)
        os.replace(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise


def write_local(path: str, content: str, mode: str = "create") -> dict[str, Any]:
    """Write a text file. 'create' refuses to replace a file that is already there."""
    if mode not in WRITE_MODES:
        raise ValueError(f"mode must be one of {', '.join(WRITE_MODES)}")
    if not isinstance(content, str):  # str(None) would silently overwrite the file with the word "None"
        raise ValueError("content must be a string")
    text = content
    if len(text) > MAX_WRITE_CHARS:
        raise ValueError(f"content is {len(text)} characters; the limit is {MAX_WRITE_CHARS}")
    _refuse_leaf_symlink(path, "write")
    p = _writable_path(path)
    if p.is_dir():
        raise LocalPathError(f"{p} is a folder")
    existed = p.exists()
    if existed and mode == "create":
        raise LocalPathError(f"{p.name} already exists; pass mode='overwrite' to replace it or mode='append' to add to it")
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_nofollow(p, text, mode)
    return {"path": str(p), "mode": mode, "created": not existed, "chars_written": len(text), "size": p.stat().st_size}


def move_local(path: str, to: str) -> dict[str, Any]:
    """Move or rename a file or folder. Never replaces something that already exists."""
    _refuse_leaf_symlink(path, "move")
    _refuse_leaf_symlink(to, "move")
    src = allowed_path(path)
    if not src.exists():
        raise LocalPathError(f"{src} does not exist")
    if why := _holds_protected(src):
        raise LocalPathError(why)
    dst = _writable_path(to)
    into_folder = to.rstrip().endswith(("/", os.sep))  # Path drops the slash; it still means "this folder"
    if into_folder and not dst.exists():
        dst.mkdir(parents=True)
    if into_folder and not dst.is_dir():
        raise LocalPathError(f"{dst} is a file, not a folder")
    if dst.is_dir():
        dst = dst / src.name
        if dst.suffix.lower() in BLOCKED_WRITE_SUFFIXES:
            raise LocalPathError(f"{dst.suffix} files are off limits")
    if dst == src:
        raise LocalPathError("the source and the destination are the same path")
    if dst.exists():
        raise LocalPathError(f"{dst} already exists; pick another name")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"from": str(src), "path": str(dst), "kind": "folder" if dst.is_dir() else "file"}


def _open_trash() -> tuple[int, Path]:
    """A directory fd for ~/.Trash.

    `shutil.move` follows a directory symlink, so a `~/.Trash` that points elsewhere
    would drop the file there while the returned path still said `~/.Trash/...`. Open the directory
    itself (`O_NOFOLLOW`) and rename into that fd.
    """
    trash = home() / ".Trash"
    if trash.is_symlink():
        raise LocalPathError("the Trash is a symlink and is off limits")
    if not trash.exists():
        trash.mkdir()
    elif not trash.is_dir():
        raise LocalPathError("the Trash is off limits")
    if trash.is_symlink():
        raise LocalPathError("the Trash is a symlink and is off limits")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(trash, flags)
    except OSError as e:
        # Without Full Disk Access macOS refuses the open; the error says what to do rather than reading as a policy.
        raise LocalPathError("the Trash could not be opened: grant Grain Full Disk Access in System Settings > Privacy & Security, "
                             "or move the file somewhere else instead") from e
    return fd, trash


def _free_trash_name(fd: int, src: Path) -> str:
    """The Finder's naming: `notes.txt`, then `notes 2.txt`, without following a symlink in the Trash."""
    name = src.name
    n = 2
    while True:
        try:
            os.lstat(name, dir_fd=fd)
        except FileNotFoundError:
            return name
        name = f"{src.stem} {n}{src.suffix}"
        n += 1


def trash_local(path: str) -> dict[str, Any]:
    """Move a file or folder to ~/.Trash, so the user can get it back from the Finder. Nothing is erased here."""
    _refuse_leaf_symlink(path, "trash")
    p = allowed_path(path)
    if not p.exists():
        raise LocalPathError(f"{p} does not exist")
    if p == home() or p.parent == p:
        raise LocalPathError("the home folder itself cannot be trashed")
    if why := _holds_protected(p):
        raise LocalPathError(why)
    fd, trash = _open_trash()
    try:
        name = _free_trash_name(fd, p)
        try:
            os.rename(os.fspath(p), name, dst_dir_fd=fd)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise LocalPathError(f"could not move {p.name} to the Trash") from e
            dest = trash / name
            if trash.is_symlink() or dest.is_symlink():
                raise LocalPathError("the Trash is a symlink and is off limits") from e
            shutil.move(os.fspath(p), os.fspath(dest))
    finally:
        os.close(fd)
    return {"path": str(p), "trashed_to": str(trash / name), "note": "in the Trash; the user can put it back from the Finder"}


# ---- Shortcuts ----
async def _run(argv: list[str], *, stdin: bytes | None, timeout: float) -> tuple[int | None, bytes, bytes, bool]:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
        return proc.returncode, out, err, False
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None, b"", b"", True


async def list_shortcuts(folder: str | None = None, timeout: float = 15.0) -> list[str]:
    if folder and str(folder).strip().startswith("-"):
        raise ValueError("folder cannot start with '-'")
    argv = ["shortcuts", "list"] + (["--folder-name", folder] if folder else [])
    code, out, err, timed_out = await _run(argv, stdin=None, timeout=timeout)
    if timed_out:
        raise TimeoutError("shortcuts list timed out")
    if code != 0:
        raise RuntimeError((err.decode("utf-8", "replace").strip() or f"shortcuts exited with {code}")[:300])
    return [s.strip() for s in out.decode("utf-8", "replace").splitlines() if s.strip()]


async def run_shortcut(name: str, text_input: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """`shortcuts run <name> -o <tmp>`, with the optional text piped to stdin (Shortcut Input)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name is empty")
    if any(c in name for c in "\x00\r\n"):
        raise ValueError("name is not a shortcut name")
    if text_input is not None and len(text_input) > MAX_SHORTCUT_INPUT:
        raise ValueError(f"input is longer than {MAX_SHORTCUT_INPUT} characters")
    with tempfile.TemporaryDirectory(prefix="grain-shortcut-") as tmp:
        out_path = os.path.join(tmp, "output")
        # `--` ends option parsing, so a name like `--output-path=/tmp/x` is the shortcut, not a flag.
        argv = ["shortcuts", "run", "--output-path", out_path, "--", name]
        started = time.monotonic()
        code, out, err, timed_out = await _run(argv, stdin=text_input.encode() if text_input is not None else None, timeout=timeout)
        elapsed = round(time.monotonic() - started, 2)
        result: dict[str, Any] = {"shortcut": name.strip(), "seconds": elapsed}
        if timed_out:
            return {**result, "ok": False, "timed_out": True, "error": f"the shortcut did not finish within {int(timeout)}s"}
        output = ""
        if os.path.exists(out_path):
            try:
                output = extract_text("output", Path(out_path).read_bytes()[: MAX_SHORTCUT_OUTPUT * 4])
            except ValueError:
                output = f"(binary output, {os.path.getsize(out_path)} bytes)"
        output = output or out.decode("utf-8", "replace")
        errs = err.decode("utf-8", "replace").strip()
        result.update({"ok": code == 0, "exit_code": code, "output": output[:MAX_SHORTCUT_OUTPUT],
                       "output_truncated": len(output) > MAX_SHORTCUT_OUTPUT})
        if errs:
            result["stderr"] = errs[:2000]
        return result


# ---- offscreen page loader (Electron main) ----
REGISTRATION_TTL = 90.0  # seconds a registration stays good (main re-sends every 30)


class PageBridge:
    """Where the Electron main process is listening. Main POSTs this on startup and every so often after."""

    def __init__(self) -> None:
        self.url = ""
        self.token = ""
        self.seen = 0.0
        self.capabilities: set[str] = {"page"}  # what main says it can do; an older main sends nothing and only has the loader
        self.transport: httpx.AsyncBaseTransport | None = None  # tests inject a fake main here

    def register(self, url: str, token: str, capabilities: list[str] | None = None) -> None:
        u = urllib.parse.urlsplit(url.strip())
        if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or not u.port:
            raise ValueError("the page bridge must be http://127.0.0.1:<port>")
        if len(token.strip()) < 16:
            raise ValueError("the page bridge token is too short")
        self.url, self.token, self.seen = f"http://127.0.0.1:{u.port}", token.strip(), time.time()
        self.capabilities = {str(c) for c in capabilities} if capabilities is not None else {"page"}

    @property
    def connected(self) -> bool:
        # Main re-registers every 30 s. A backend that outlives the desktop app would otherwise offer these tools forever.
        return bool(self.url and self.token) and time.time() - self.seen <= REGISTRATION_TTL

    def has(self, cap: str) -> bool:
        return self.connected and cap in self.capabilities

    async def browser(self, route: str, payload: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
        """POST one /browser/* call to main. Always returns the parsed body; a transport failure or a rejected
        credential comes back as {ok: False, error, code} so callers have one shape to handle."""
        if not self.connected:
            return {"ok": False, "code": "bridge", "error": "the desktop app's browser is not connected"}
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False, transport=self.transport) as c:
                r = await c.post(f"{self.url}/browser/{route.strip('/')}", json=payload,
                                 headers={"Authorization": f"Bearer {self.token}"})
        except httpx.TimeoutException:
            return {"ok": False, "code": "timeout", "error": f"the desktop app's browser did not answer within {int(timeout)}s"}
        except httpx.HTTPError as e:
            return {"ok": False, "code": "bridge", "error": f"the desktop app's browser could not be reached ({type(e).__name__})"}
        if r.status_code == 401:
            return {"ok": False, "code": "bridge", "error": "the desktop app's browser rejected this backend's credentials"}
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return {"ok": False, "code": "bridge", "error": f"the desktop app's browser answered HTTP {r.status_code} with no usable body"}
        return body

    async def open_page(self, url: str, *, max_chars: int = 20000, timeout: float = 20.0,
                        wait_for: str = "", links: bool = False) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("the desktop app's page loader is not connected")
        n = max(1000, min(int(max_chars), PAGE_MAX_CHARS))
        t = max(3.0, min(float(timeout), 45.0))
        async with httpx.AsyncClient(timeout=t + 10, trust_env=False) as c:
            payload: dict[str, Any] = {"url": url, "maxChars": n, "timeoutMs": int(t * 1000)}
            if wait_for.strip():
                payload["waitForSelector"] = wait_for.strip()[:200]
            if links:
                payload["links"] = True
            r = await c.post(f"{self.url}/page", json=payload,
                             headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code == 401:
            raise RuntimeError("the page loader rejected this backend's credentials")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:300]}
        if r.status_code != 200 or body.get("error"):
            raise RuntimeError(str(body.get("error") or f"HTTP {r.status_code}")[:300])
        text = str(body.get("text") or "")
        out: dict[str, Any] = {"url": body.get("url") or url, "title": body.get("title") or "", "text": text[:n],
                               "truncated": bool(body.get("truncated")) or len(text) > n, "timed_out": bool(body.get("timedOut"))}
        if links:  # page content, not allow-listed: a tainted run cannot follow these
            out["links"] = [{"text": str(l.get("text") or "")[:120], "href": str(l["href"])} for l in (body.get("links") or [])
                            if isinstance(l, dict) and l.get("href")][:40]
        return out


page_bridge = PageBridge()
