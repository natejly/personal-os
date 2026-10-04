"""Reach into the rest of the Mac the boring way: Spotlight, the file system, Shortcuts, and an offscreen page loader.

Every path, read or written, goes through `allowed_path`: inside the home folder, outside ~/Library and
dot-folders, symlinks resolved first. `Library` is matched case-insensitively, because the home volume
is case-insensitive and `Path.resolve()` keeps the spelling the caller used. Writes never clobber
silently (`mode="create"` is the default) and nothing is deleted outright — `trash` moves items to
~/.Trash, where the user can put them back.

Nothing here drives the screen. `mdfind` and `shortcuts` are plain subprocesses (argv, never a shell);
the page loader is an offscreen Electron window the main process owns, reached over a loopback bridge
that main registers with this backend (see src/main/pagefetch.ts). The agent itself holds no TCC
grants: Spotlight needs none, and a Shortcut runs with whatever the user granted it when they built it.
"""
from __future__ import annotations

import asyncio
import errno
import os
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
BLOCKED_UNDER_HOME = ("Library",)  # app data, mail, keychains, browser profiles
_BLOCKED_TOP = frozenset(name.casefold() for name in BLOCKED_UNDER_HOME)
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


def allowed_path(raw: str) -> Path:
    """Resolve `raw` (symlinks included) and require it to sit under the home folder, outside dot-folders, ~/Library and the app's own data folder."""
    if not raw or not str(raw).strip():
        raise LocalPathError("empty path")
    p = Path(os.path.expanduser(str(raw).strip()))
    if not p.is_absolute():
        p = home() / p
    p = p.resolve()
    h = home()
    try:
        rel = p.relative_to(h)
    except ValueError:
        raise LocalPathError(f"{p} is outside your home folder") from None
    parts = rel.parts
    if parts and parts[0].casefold() in _BLOCKED_TOP:
        raise LocalPathError(f"~/{parts[0]} is off limits")
    if any(x.startswith(".") for x in parts):
        raise LocalPathError("hidden files and folders are off limits")
    data = _app_data_dir()
    if data is not None and _under(p, data):
        raise LocalPathError("the app's own data folder is off limits")
    return p


def in_roots(path: str | Path, roots: list[Path]) -> bool:
    """True when `path` (symlinks resolved) sits inside one of the granted folders. A symlink in a root that
    points outside it is therefore not inside."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    return any(p == r or p.is_relative_to(r) for r in roots)


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
    """Write a text file under the home folder. 'create' refuses to replace a file that is already there."""
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
    """Move or rename a file or folder inside the home folder. Never replaces something that already exists."""
    _refuse_leaf_symlink(path, "move")
    _refuse_leaf_symlink(to, "move")
    src = allowed_path(path)
    if not src.exists():
        raise LocalPathError(f"{src} does not exist")
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

    `shutil.move` follows a directory symlink, so a `~/.Trash` that points outside the home folder
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
        raise LocalPathError("the Trash is off limits") from e
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
    if p == home():
        raise LocalPathError("the home folder itself cannot be trashed")
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
