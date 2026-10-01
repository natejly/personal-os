"""Reach into the rest of the Mac the boring way: Spotlight, Shortcuts, and an offscreen page loader.

Nothing here drives the screen. `mdfind` and `shortcuts` are plain subprocesses (argv, never a shell);
the page loader is an offscreen Electron window the main process owns, reached over a loopback bridge
that main registers with this backend (see src/main/pagefetch.ts). The agent itself holds no TCC
grants: Spotlight needs none, and a Shortcut runs with whatever the user granted it when they built it.
"""
from __future__ import annotations

import asyncio
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
MAX_READ_BYTES = 20 * 1024 * 1024
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


def allowed_path(raw: str) -> Path:
    """Resolve `raw` (symlinks included) and require it to sit under the home folder, outside dot-folders and ~/Library."""
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
    if parts and parts[0] in BLOCKED_UNDER_HOME:
        raise LocalPathError(f"~/{parts[0]} is off limits")
    if any(x.startswith(".") for x in parts):
        raise LocalPathError("hidden files and folders are off limits")
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
    argv = ["shortcuts", "list"] + (["--folder-name", folder] if folder else [])
    code, out, err, timed_out = await _run(argv, stdin=None, timeout=timeout)
    if timed_out:
        raise TimeoutError("shortcuts list timed out")
    if code != 0:
        raise RuntimeError((err.decode("utf-8", "replace").strip() or f"shortcuts exited with {code}")[:300])
    return [s.strip() for s in out.decode("utf-8", "replace").splitlines() if s.strip()]


async def run_shortcut(name: str, text_input: str | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """`shortcuts run <name> -o <tmp>`, with the optional text piped to stdin (Shortcut Input)."""
    if not (name or "").strip():
        raise ValueError("name is empty")
    if text_input is not None and len(text_input) > MAX_SHORTCUT_INPUT:
        raise ValueError(f"input is longer than {MAX_SHORTCUT_INPUT} characters")
    with tempfile.TemporaryDirectory(prefix="grain-shortcut-") as tmp:
        out_path = os.path.join(tmp, "output")
        argv = ["shortcuts", "run", name.strip(), "--output-path", out_path]
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
class PageBridge:
    """Where the Electron main process is listening. Main POSTs this on startup and every so often after."""

    def __init__(self) -> None:
        self.url = ""
        self.token = ""
        self.seen = 0.0

    def register(self, url: str, token: str) -> None:
        u = urllib.parse.urlsplit(url.strip())
        if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost") or not u.port:
            raise ValueError("the page bridge must be http://127.0.0.1:<port>")
        if len(token.strip()) < 16:
            raise ValueError("the page bridge token is too short")
        self.url, self.token, self.seen = f"http://127.0.0.1:{u.port}", token.strip(), time.time()

    @property
    def connected(self) -> bool:
        return bool(self.url and self.token)

    async def open_page(self, url: str, *, max_chars: int = 20000, timeout: float = 20.0) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("the desktop app's page loader is not connected")
        n = max(1000, min(int(max_chars), PAGE_MAX_CHARS))
        t = max(3.0, min(float(timeout), 45.0))
        async with httpx.AsyncClient(timeout=t + 10, trust_env=False) as c:
            r = await c.post(f"{self.url}/page", json={"url": url, "maxChars": n, "timeoutMs": int(t * 1000)},
                             headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code == 401:
            raise RuntimeError("the page loader rejected this backend's credentials")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:300]}
        if r.status_code != 200 or body.get("error"):
            raise RuntimeError(str(body.get("error") or f"HTTP {r.status_code}")[:300])
        text = str(body.get("text") or "")
        return {"url": body.get("url") or url, "title": body.get("title") or "", "text": text[:n],
                "truncated": bool(body.get("truncated")) or len(text) > n, "timed_out": bool(body.get("timedOut"))}


page_bridge = PageBridge()
