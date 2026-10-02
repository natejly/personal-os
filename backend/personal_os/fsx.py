"""File tools over granted folders: glob, grep, exact-string edit, copy and mkdir.

The agent works on a folder cheaply and safely with these instead of rewriting whole files. Three ideas hold
the module together:

* Granted folders. Reads may go anywhere `mac.allowed_path` allows. A write must land inside the active desk's
  workspace (always fine), or inside a folder the user listed under the `workspaceRoots` setting. Anything else
  is not refused outright: `needs_approval` says so, the reply loop turns the call into an approval card, and the
  tool only proceeds when the loop sets `ctx["fs_outside_ok"]` after the user said yes. A tainted reply (it has
  read untrusted content) must ask even inside a granted folder, and an unattended background run may only
  write inside a desk workspace.
* Read before write. Every read that returns content records what was seen (file, mtime, character ranges) in
  `ReadLedger`. An edit needs the region it changes to have been read, an overwrite needs the whole file, and a
  file that changed since it was read is refused rather than clobbered.
* Credentials stay out. Names that carry secrets (.env files, key files, cloud and ssh folders) and /dev, /proc
  are refused for reads and skipped by glob and grep, whichever way a symlink reaches them.

Nothing here talks to the network or a shell. `register` adds the tools to a Toolbox.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import mac

SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"})
GLOB_CAP = 500
GLOB_SCAN_CAP = 200_000
GREP_CAP = 200
GREP_LINE_CHARS = 400
GREP_FILE_BYTES = 5_000_000
EDIT_MAX_BYTES = 2_000_000
COPY_MAX_FILES = 2_000
COPY_MAX_BYTES = 100_000_000
DIFF_MAX_CHARS = 8_000
BLOCK_SIMILARITY = 0.65
RESERVED_DESK_DIRS = (".baseline", ".trash")  # workspace bookkeeping, never a write target

SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".azure", "gcloud", ".docker"})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk"})
SENSITIVE_NAMES = frozenset({".netrc", ".npmrc", ".pgpass", ".git-credentials", ".pypirc", "credentials", "credentials.json",
                             "application_default_credentials.json"})
SENSITIVE_PREFIXES = ("/dev/", "/proc/")
KEY_FILE_RE = re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(?!.*\.pub$)")


class FsError(Exception):
    """A clean one-line failure for the tool error envelope."""


# ---------------------------------------------------------------- grants and paths
class Grants:
    def __init__(self, roots: list[Path], desk: Path | None) -> None:
        self.roots, self.desk = roots, desk

    def in_desk(self, p: Path) -> bool:
        return self.desk is not None and (p == self.desk or p.is_relative_to(self.desk))

    def in_roots(self, p: Path) -> bool:
        return mac.in_roots(p, self.roots)

    def default_root(self) -> Path | None:
        return self.desk or (self.roots[0] if self.roots else None)


def grants_for(box: Any, ctx: dict[str, Any]) -> Grants:
    roots: list[Path] = []
    raw = box.settings().get("workspaceRoots") or []
    for r in raw if isinstance(raw, list) else []:
        try:
            p = mac.allowed_path(str(r))
        except mac.LocalPathError:
            continue
        if p.is_dir() and p not in roots:
            roots.append(p)
    desk: Path | None = None
    did, ws = ctx.get("desk_id"), getattr(box, "workspace", None)
    if did and ws is not None:
        try:
            desk = ws.desk_root(str(did)).resolve()
        except Exception:  # noqa: BLE001 - a malformed id means "no desk", not a crash
            desk = None
    return Grants(roots, desk)


def sensitive_reason(*paths: str | Path) -> str | None:
    """Why a path may not be read, or None. Judge the spelled path AND the resolved one: a symlink named
    notes.txt that points at ~/.aws/credentials is refused by the second."""
    for raw in paths:
        s = str(raw)
        if s.startswith(SENSITIVE_PREFIXES) or s in ("/dev", "/proc"):
            return "device and process files are off limits"
        parts = Path(s).parts
        name = parts[-1] if parts else ""
        low = name.lower()
        if low == ".env" or low.startswith(".env.") or low == ".envrc":
            return "environment files hold secrets and are off limits"
        if KEY_FILE_RE.match(low) or Path(low).suffix in SENSITIVE_SUFFIXES:
            return "key files are off limits"
        if low in SENSITIVE_NAMES:
            return "credential files are off limits"
        if any(x in SENSITIVE_DIRS for x in parts[:-1]) or name in SENSITIVE_DIRS:
            return "credential folders are off limits"
    return None


def resolve_path(raw: Any, g: Grants, *, write: bool = False) -> Path:
    """Spell out a path the way the user meant it, resolve symlinks, and require it to be somewhere the agent
    may be: inside the desk workspace, or anywhere `mac.allowed_path` allows."""
    s = str(raw or "").strip()
    if not s:
        raise FsError("empty path")
    if "\x00" in s:
        raise FsError("path contains a NUL byte")
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = (g.desk or mac.home()) / p
    r = p.resolve()
    if g.in_desk(r):
        if r != g.desk and write and r.relative_to(g.desk).parts[0] in RESERVED_DESK_DIRS:
            raise FsError(f"{r.relative_to(g.desk).parts[0]} holds the workspace's own bookkeeping; write elsewhere in the workspace")
    else:
        try:
            r = mac.allowed_path(str(r))
        except mac.LocalPathError as e:
            raise FsError(str(e)) from None
    if write:
        bad = next((x.lower() for x in (Path(q).suffix for q in r.parts) if x.lower() in mac.BLOCKED_WRITE_SUFFIXES), None)
        if bad:
            raise FsError(f"{bad} files are off limits; write a document instead")
    return r


# Tools whose results are the user's own local files: reading them does not make a reply untrusted for this purpose.
LOCAL_SOURCES = frozenset({"read_local_file", "fs_grep", "fs_glob", "find_files"})


def untrusted(ctx: dict[str, Any]) -> bool:
    """The reply has read content from outside the user's own folders (web, mail, a connector). Reading a local file
    alone does not count: the user granted that folder to the agent."""
    if not ctx.get("tainted"):
        return False
    sources = ctx.get("taint_sources") or []
    return not sources or any(x not in LOCAL_SOURCES for x in sources)


def needs_approval(p: Path, ctx: dict[str, Any], g: Grants) -> bool:
    """True for a write the user has not pre-authorised: outside every granted folder, or inside a granted
    folder (not a desk) while the reply has read untrusted content."""
    if g.in_desk(p):
        return False
    return (not g.in_roots(p)) or untrusted(ctx)


def write_target(raw: Any, ctx: dict[str, Any], g: Grants) -> Path:
    p = resolve_path(raw, g, write=True)
    if g.in_desk(p):
        return p
    if ctx.get("proposal_only"):
        raise FsError("this is an unattended background run, which may only write inside a desk workspace")
    if needs_approval(p, ctx, g) and not ctx.get("fs_outside_ok"):
        raise FsError(f"{p} is not inside a folder the user granted, so it needs their approval; "
                      "tell them what you would change, or ask them to add the folder under Settings, Tools, Workspace folders")
    return p


# Which argument of each writing tool names a path the user must have granted.
WRITE_ARGS = {"fs_edit": ("path",), "fs_mkdir": ("path",), "fs_copy": ("dst",)}


def needs_ask(box: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """For the reply loop: should this call be shown to the user as an approval card? A path that does not
    resolve is not a reason to ask; the tool reports it."""
    keys = WRITE_ARGS.get(name)
    if not keys or not isinstance(args, dict):
        return False
    g = grants_for(box, ctx)
    for k in keys:
        try:
            if needs_approval(resolve_path(args.get(k), g, write=True), ctx, g):
                return True
        except FsError:
            continue
    return False


# ---------------------------------------------------------------- read ledger
class ReadLedger:
    """What this conversation has actually seen of each file, as character ranges against one mtime.

    In memory only: a restart forgets it, which costs the agent one re-read and nothing else. Also counts
    identical repeated reads, so a model that re-reads the same unchanged file in a loop is stopped.
    """

    def __init__(self, cap: int = 4000) -> None:
        self.cap = cap
        self._seen: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._repeat: OrderedDict[tuple[Any, ...], int] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _merge(ranges: list[list[int]]) -> list[list[int]]:
        out: list[list[int]] = []
        for a, b in sorted(ranges):
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    def note(self, conv: str, path: Path | str, mtime_ns: int, start: int, end: int, total: int) -> None:
        key = (conv or "", str(path))
        with self._lock:
            e = self._seen.pop(key, None)
            if not e or e["mtime"] != mtime_ns:
                e = {"mtime": mtime_ns, "ranges": []}
            e["total"] = total
            e["ranges"] = self._merge([*e["ranges"], [start, max(start, end)]])
            self._seen[key] = e
            while len(self._seen) > self.cap:
                self._seen.popitem(last=False)

    def note_full(self, conv: str, path: Path | str, mtime_ns: int, total: int) -> None:
        self.note(conv, path, mtime_ns, 0, total, total)

    def after_edit(self, conv: str, path: Path | str, new_mtime: int, new_total: int, first_start: int,
                   new_regions: list[tuple[int, int]]) -> None:
        """The agent just changed a file it had read. A whole-file read stays whole; otherwise it keeps what it saw
        before the first change (offsets there did not move) plus the text it wrote."""
        key = (conv or "", str(path))
        with self._lock:
            e = self._seen.pop(key, None)
        if e is None:
            return
        was_full = any(a <= 0 and b >= e["total"] for a, b in e["ranges"])
        if was_full:
            self.note_full(conv, path, new_mtime, new_total)
            return
        for a, b in e["ranges"]:
            if a < first_start:
                self.note(conv, path, new_mtime, a, min(b, first_start), new_total)
        for a, b in new_regions:
            self.note(conv, path, new_mtime, a, b, new_total)

    def forget(self, conv: str, path: Path | str) -> None:
        with self._lock:
            self._seen.pop((conv or "", str(path)), None)

    def check(self, conv: str, path: Path | str, mtime_ns: int, regions: list[tuple[int, int]] | None,
              in_desk: bool = False) -> str | None:
        """None when the file may be changed. `regions=None` demands a full read (an overwrite). `in_desk` names the
        reader that works on a workspace path: read_local_file refuses the workspace, so pointing there is a dead end."""
        with self._lock:
            e = self._seen.get((conv or "", str(path)))
        name = Path(path).name
        if e is None:
            how = "desk_read_file or fs_grep" if in_desk else "read_local_file or fs_grep"
            return f"{name} has not been read in this conversation; read it first ({how}) so the change is based on what is there"
        if e["mtime"] != mtime_ns:
            return f"{name} changed since you last read it; read it again before changing it"
        have = e["ranges"]
        if regions is None:
            if not (e["total"] == 0 and have) and not any(a <= 0 and b >= e["total"] for a, b in have):
                return f"{name} was only partly read; read all of it before overwriting it (or use fs_edit for a small change)"
            return None
        for s, t in regions:
            if not any(a <= s and b >= t for a, b in have):
                return f"the part of {name} you are changing was not in what you read; read that part first"
        return None

    def repeat(self, conv: str, path: Path | str, offset: int, length: int, mtime_ns: int) -> str:
        """'ok' for the first two identical reads of an unchanged file, 'stub' for the third, 'refuse' after."""
        key = (conv or "", str(path), offset, length, mtime_ns)
        with self._lock:
            n = self._repeat.pop(key, 0) + 1
            self._repeat[key] = n
            while len(self._repeat) > self.cap:
                self._repeat.popitem(last=False)
        return "ok" if n <= 2 else ("stub" if n == 3 else "refuse")


def line_ranges(text: str, lines: set[int]) -> list[tuple[int, int]]:
    """Character ranges of 1-based line numbers in `text` (line terminator included)."""
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    starts.append(len(text))
    out = []
    for n in sorted(lines):
        if 1 <= n < len(starts):
            out.append((starts[n - 1], starts[n]))
    return out


# ---------------------------------------------------------------- syntax check after a write
def syntax_check(path: Path | str, text: str) -> str | None:
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".py":
            compile(text, str(path), "exec")  # what py_compile does, without leaving a .pyc behind
        elif suffix == ".json":
            json.loads(text)
        elif suffix == ".toml":
            try:
                import tomllib as toml  # type: ignore[import-not-found]
            except ImportError:
                try:
                    import tomli as toml  # type: ignore[import-not-found,no-redef]
                except ImportError:
                    return None
            toml.loads(text)
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError:
                return None
            list(yaml.safe_load_all(text))
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    except Exception as e:  # noqa: BLE001 - every parser has its own error class
        return f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:300]
    return None


# ---------------------------------------------------------------- exact-string edit
class EditError(Exception):
    pass


def _eol_of(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")


def _fit_new(new: str, window_last: str, window_first: str) -> str:
    """Make `new` use the file's line ending and end the way the window it replaces ended."""
    eol = _eol_of(window_last) or _eol_of(window_first) or "\n"
    if eol == "\r\n":
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    if _eol_of(window_last) and not new.endswith(("\n", "\r\n")):
        new += eol
    return new


def _apply(text: str, spans: list[tuple[int, int, str]]) -> tuple[str, list[tuple[int, int]]]:
    out, pos, n, new_regions = [], 0, 0, []
    for s, e, rep in sorted(spans):
        out.append(text[pos:s])
        n += s - pos
        out.append(rep)
        new_regions.append((n, n + len(rep)))
        n += len(rep)
        pos = e
    out.append(text[pos:])
    return "".join(out), new_regions


def _reindent(new: str, old_indent: str, file_indent: str) -> str:
    """The model often gives a block without the file's indentation. Move it to where the matched text sits."""
    if old_indent == file_indent:
        return new
    out = []
    for ln in new.split("\n"):
        if not ln.strip():
            out.append(ln)
        elif ln.startswith(old_indent):
            out.append(file_indent + ln[len(old_indent):])
        else:
            out.append(ln)
    return "\n".join(out)


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def replace_text(text: str, old: str, new: str, replace_all: bool = False) -> tuple[str, str, int, list[tuple[int, int]], list[tuple[int, int]]]:
    """Replace `old` with `new`: exact first, then ignoring leading/trailing whitespace per line, then by the
    first and last line when the lines between them are similar enough. Returns (text, matcher, count, regions
    changed in the ORIGINAL text, the same regions in the NEW text). Several matches are an error unless `replace_all`."""
    if not old:
        raise EditError("old is empty; give the exact text to replace")
    if old == new:
        raise EditError("old and new are identical; nothing to change")

    def finish(matcher: str, spans: list[tuple[int, int, str]]) -> tuple[str, str, int, list[tuple[int, int]], list[tuple[int, int]]]:
        if len(spans) > 1 and not replace_all:
            raise EditError(f"{len(spans)} places match ({matcher}); include more surrounding lines in old to pick one, or pass replace_all")
        out, new_regions = _apply(text, spans)
        return out, matcher, len(spans), [(s, e) for s, e, _ in sorted(spans)], new_regions

    # 1. exact
    spans, i = [], text.find(old)
    while i != -1:
        spans.append((i, i + len(old), new))
        i = text.find(old, i + len(old))
    if spans:
        return finish("exact", spans)

    lines = text.splitlines(keepends=True)
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))
    olines = old.replace("\r\n", "\n").split("\n")
    while olines and not olines[-1].strip():
        olines.pop()
    while olines and not olines[0].strip():
        olines.pop(0)
    if not olines:
        raise EditError("old is only whitespace")
    stripped = [ln.strip() for ln in lines]
    ostrip = [ln.strip() for ln in olines]
    k = len(olines)

    # 2. line-trimmed
    spans = []
    i = 0
    while i + k <= len(lines):
        if stripped[i:i + k] == ostrip:
            fit = _fit_new(_reindent(new, _indent(old.replace("\r\n", "\n").lstrip("\n").split("\n")[0]), _indent(lines[i])),
                           lines[i + k - 1], lines[i])
            spans.append((offs[i], offs[i + k], fit))
            i += k
        else:
            i += 1
    if spans:
        return finish("line-trimmed", spans)

    # 3. block anchors: same first and last line, middle similar
    if k >= 3 and ostrip[0] and ostrip[-1]:
        mid_old = "\n".join(ostrip[1:-1])
        cands: list[tuple[float, int, int]] = []
        for i in range(len(lines)):
            if stripped[i] != ostrip[0]:
                continue
            best: tuple[float, int] | None = None
            for j in range(i + 2, min(len(lines), i + 2 * k + 2)):
                if stripped[j] != ostrip[-1]:
                    continue
                sim = difflib.SequenceMatcher(None, mid_old, "\n".join(stripped[i + 1:j])).ratio()
                if sim >= BLOCK_SIMILARITY and (best is None or sim > best[0]):
                    best = (sim, j)
            if best:
                cands.append((best[0], i, best[1]))
        # overlapping candidates are one block seen twice: keep the better one
        kept: list[tuple[float, int, int]] = []
        for c in sorted(cands, key=lambda c: -c[0]):
            if all(c[2] < o[1] or c[1] > o[2] for o in kept):
                kept.append(c)
        oi = _indent(old.replace("\r\n", "\n").lstrip("\n").split("\n")[0])
        spans = [(offs[i], offs[j + 1], _fit_new(_reindent(new, oi, _indent(lines[i])), lines[j], lines[i])) for _, i, j in kept]
        if spans:
            return finish("block-anchor", spans)
    raise EditError("old was not found. Read the file again and copy the text exactly, with its indentation")


def _atomic_write(p: Path, data: bytes) -> None:
    mode = (p.stat().st_mode & 0o777) if p.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".grain-edit-")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def unified(before: str, after: str, name: str) -> str:
    d = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{name}", f"b/{name}", n=2))
    return d if len(d) <= DIFF_MAX_CHARS else d[:DIFF_MAX_CHARS] + "\n... diff truncated"


# ---------------------------------------------------------------- glob
def glob_regex(pattern: str) -> re.Pattern[str]:
    """`**` crosses folders, `*` and `?` do not. A pattern with no slash matches at any depth."""
    pat = pattern.replace("\\", "/")
    if pat.startswith("./"):
        pat = pat[2:]
    if "/" not in pat:
        pat = "**/" + pat
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pat[i + 1:j]
                body = "^" + body[1:] if body.startswith("!") else body
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _walk(base: Path):
    """(dir, dirnames, filenames) with dot-folders and dependency folders pruned, symlinks not followed."""
    for d, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS)
        yield d, dirs, sorted(files)


def glob_files(base: Path, pattern: str) -> tuple[list[dict[str, Any]], bool]:
    rx = glob_regex(pattern)
    hits: list[tuple[float, dict[str, Any]]] = []
    scanned = 0
    for d, dirs, files in _walk(base):
        for name, is_dir in [(x, True) for x in dirs] + [(x, False) for x in files]:
            scanned += 1
            if scanned > GLOB_SCAN_CAP:
                return _finish_glob(hits), True
            full = Path(d) / name
            rel = full.relative_to(base).as_posix()
            if not rx.match(rel):
                continue
            if sensitive_reason(full):
                continue
            try:
                st = full.lstat()
                if full.is_symlink():
                    tgt = full.resolve()
                    if not (tgt == base or tgt.is_relative_to(base.resolve())) or sensitive_reason(tgt):
                        continue
            except OSError:
                continue
            hits.append((st.st_mtime, {"path": str(full), "rel": rel, "kind": "folder" if is_dir else "file",
                                       **({} if is_dir else {"size": st.st_size})}))
    return _finish_glob(hits), False


def _finish_glob(hits: list[tuple[float, dict[str, Any]]]) -> list[dict[str, Any]]:
    hits.sort(key=lambda h: -h[0])
    return [h[1] for h in hits]


# ---------------------------------------------------------------- grep
def _is_binary(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def _grep_files(base: Path, glob: str | None):
    rx = glob_regex(glob) if glob else None
    if base.is_file():
        yield base, base.name
        return
    for d, _dirs, files in _walk(base):
        for name in files:
            full = Path(d) / name
            rel = full.relative_to(base).as_posix()
            if rx and not rx.match(rel):
                continue
            yield full, rel


def grep_python(base: Path, pattern: str, glob: str | None, context: int, ignore_case: bool) -> tuple[list[dict[str, Any]], bool]:
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise FsError(f"bad regular expression: {e}") from None
    out: list[dict[str, Any]] = []
    for full, _rel in _grep_files(base, glob):
        if sensitive_reason(full):
            continue
        try:
            if full.is_symlink():
                tgt = full.resolve()
                if sensitive_reason(tgt) or (base.is_dir() and not tgt.is_relative_to(base.resolve())):
                    continue
            if full.stat().st_size > GREP_FILE_BYTES or _is_binary(full):
                continue
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        shown: set[int] = set()
        for n, ln in enumerate(lines, 1):
            if not rx.search(ln):
                continue
            for k in range(max(1, n - context), min(len(lines), n + context) + 1):
                if k in shown:
                    continue
                shown.add(k)
                row: dict[str, Any] = {"path": str(full), "line": k, "text": lines[k - 1][:GREP_LINE_CHARS]}
                if k != n and not rx.search(lines[k - 1]):
                    row["context"] = True
                out.append(row)
            if sum(1 for r in out if not r.get("context")) >= GREP_CAP:
                return out, True
    return out, False


def grep_rg(rg: str, base: Path, pattern: str, glob: str | None, context: int, ignore_case: bool) -> tuple[list[dict[str, Any]], bool]:
    argv = [rg, "--json", "--no-config", "-n", "--max-filesize", str(GREP_FILE_BYTES), "-g", "!node_modules", "-g", "!.git",
            "-g", "!.env*", "-g", "!*.pem", "-g", "!*.key", "-g", "!id_rsa*"]
    if ignore_case:
        argv.append("-i")
    if context:
        argv += ["-C", str(context)]
    if glob:
        argv += ["-g", glob if "/" in glob else glob]
    argv += ["-e", pattern, "--", str(base)]
    p = subprocess.run(argv, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    if p.returncode not in (0, 1):
        msg = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FsError(f"bad pattern or search failure: {msg[0] if msg else 'rg failed'}")
    out: list[dict[str, Any]] = []
    matches = 0
    for raw in p.stdout.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind not in ("match", "context"):
            continue
        d = ev["data"]
        path = (d.get("path") or {}).get("text")
        text = (d.get("lines") or {}).get("text")
        if not path or text is None:
            continue
        pp = Path(path)
        if sensitive_reason(pp):
            continue
        try:
            if pp.is_symlink() and (sensitive_reason(pp.resolve()) or (base.is_dir() and not pp.resolve().is_relative_to(base.resolve()))):
                continue
        except OSError:
            continue
        row: dict[str, Any] = {"path": path, "line": d.get("line_number"), "text": text.rstrip("\r\n")[:GREP_LINE_CHARS]}
        if kind == "context":
            row["context"] = True
        else:
            matches += 1
            if matches > GREP_CAP:
                return out, True
        out.append(row)
    return out, False


# ---------------------------------------------------------------- hooks for read_local_file / write_local_file
def pre_read(box: Any, ctx: dict[str, Any], path: str, offset: int, length: int) -> dict[str, Any] | None:
    """Run before read_local_file. Raises mac.LocalPathError for a secret path; returns a result to send back
    instead of reading (a stub, or a refusal) when the same unchanged slice is requested again and again."""
    p = mac.allowed_path(path)
    s = os.path.expanduser(str(path).strip())
    spelled = s if os.path.isabs(s) else str(mac.home() / s)
    why = sensitive_reason(os.path.normpath(spelled), p)
    if why:
        raise mac.LocalPathError(f"{p.name}: {why}")
    if not p.is_file():
        return None
    verdict = box.fs_reads.repeat(str(ctx.get("conversation_id") or ""), p, int(offset), int(length), p.stat().st_mtime_ns)
    if verdict == "stub":
        return {"path": str(p), "unchanged": True, "offset": int(offset),
                "note": "you already read exactly this slice and the file has not changed; use what you have, or read a different offset"}
    if verdict == "refuse":
        return {"error": f"read_local_file: {p.name} was read this way several times and has not changed",
                "try_instead": "work from the text you already have, or read a different offset"}
    return None


def post_read(box: Any, ctx: dict[str, Any], path: str, result: Any) -> None:
    """Record what a successful read_local_file showed."""
    if not isinstance(result, dict) or "text" not in result or result.get("error"):
        return
    try:
        p = mac.allowed_path(path)
        box.fs_reads.note(str(ctx.get("conversation_id") or ""), p, p.stat().st_mtime_ns, int(result.get("offset") or 0),
                          int(result.get("offset") or 0) + len(result["text"]), int(result.get("total_chars") or 0))
    except (mac.LocalPathError, OSError):
        pass


def pre_write(box: Any, ctx: dict[str, Any], path: str, mode: str) -> str | None:
    """Run before write_local_file: an overwrite needs the whole existing file to have been read."""
    if mode != "overwrite" or not box.settings().get("requireReadBeforeWrite", True):
        return None
    p = mac.allowed_path(path)
    if not p.is_file():
        return None
    return box.fs_reads.check(str(ctx.get("conversation_id") or ""), p, p.stat().st_mtime_ns, None)


def post_write(box: Any, ctx: dict[str, Any], path: str, content: str, result: Any) -> None:
    """After a write the agent knows the whole file; a .py/.json/.toml/.yaml that no longer parses is reported."""
    if not isinstance(result, dict) or result.get("error"):
        return
    try:
        p = mac.allowed_path(path)
        if result.get("mode") != "append":
            box.fs_reads.note_full(str(ctx.get("conversation_id") or ""), p, p.stat().st_mtime_ns, len(content))
            err = syntax_check(p, content)
        else:
            err = syntax_check(p, p.read_text(encoding="utf-8", errors="replace"))
    except (mac.LocalPathError, OSError):
        return
    if err:
        result["syntax_error"] = err
        result["note"] = "the file was written, but it does not parse; fix it with fs_edit"


# ---------------------------------------------------------------- tools
def register(box: Any) -> None:
    from .tools import ALTERNATIVE, ToolSpec, _obj, tool_error

    R = box.specs.__setitem__
    ledger: ReadLedger = box.fs_reads

    def conv_of(ctx: dict[str, Any]) -> str:
        return str(ctx.get("conversation_id") or "")

    def need_reads() -> bool:
        return bool(box.settings().get("requireReadBeforeWrite", True))

    def fail(name: str, e: Exception, **kw: Any) -> dict[str, Any]:
        return tool_error(f"{name}: {e}", alternative=ALTERNATIVE.get(name), **kw)

    ALTERNATIVE.update({
        "fs_glob": "find_files for a Spotlight search, or read_local_file on a folder to list it",
        "fs_grep": "read_local_file on the file and look through the text",
        "fs_edit": "write_local_file with mode overwrite after reading the whole file",
        "fs_copy": "read_local_file, then write_local_file to the new path",
        "fs_mkdir": "write_local_file creates missing parent folders by itself",
    })

    def base_for(root: Any, ctx: dict[str, Any]) -> tuple[Grants, Path]:
        g = grants_for(box, ctx)
        if root:
            base = resolve_path(root, g)
        else:
            base = g.default_root()  # type: ignore[assignment]
            if base is None:
                raise FsError("no folder to search: pass root, or add a workspace folder under Settings, Tools")
            if not base.exists() and g.in_desk(base):
                base.mkdir(parents=True, exist_ok=True)
        if sensitive_reason(base):
            raise FsError(sensitive_reason(base) or "off limits")
        if not base.exists():
            raise FsError(f"{base} does not exist")
        return g, base

    async def fs_glob(ctx: dict[str, Any], pattern: str, root: str | None = None) -> Any:
        try:
            if not isinstance(pattern, str) or not pattern.strip() or pattern.startswith("/") or ".." in Path(pattern).parts:
                raise FsError("pattern must be relative to the root, without '..', e.g. **/*.py")
            _g, base = base_for(root, ctx)
            if not base.is_dir():
                raise FsError(f"{base} is not a folder")
            rows, truncated = await asyncio.to_thread(glob_files, base, pattern.strip())
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_glob", e, field="pattern", example={"pattern": "**/*.md"})
        total = len(rows)
        return {"root": str(base), "pattern": pattern, "files": rows[:GLOB_CAP], "total": total,
                "truncated": truncated or total > GLOB_CAP, "note": "newest first; dot-folders, node_modules and .git are skipped"}
    R("fs_glob", ToolSpec("fs_glob", "List files and folders under a root that match a glob (`**` crosses folders; a pattern with no slash matches at any depth), newest first, at most 500. Skips dot-folders, node_modules and .git. Root defaults to the desk workspace or the first workspace folder.",
        _obj({"pattern": {"type": "string", "description": "e.g. **/*.py or src/*.ts"},
              "root": {"type": "string", "description": "Folder to search; absolute or ~/ path"}}, ["pattern"]), fs_glob, "files",
        examples=[{"pattern": "**/*.md", "root": "~/Documents/notes"}, {"pattern": "*.csv"}]))

    def record_grep(conv: str, rows: list[dict[str, Any]]) -> None:
        by_path: dict[str, set[int]] = {}
        for r in rows:
            if isinstance(r.get("line"), int):
                by_path.setdefault(r["path"], set()).add(r["line"])
        for path, nums in by_path.items():
            try:
                p = Path(path)
                st = p.stat()
                if st.st_size > GREP_FILE_BYTES:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                for s, e in line_ranges(text, nums):
                    ledger.note(conv, p.resolve(), st.st_mtime_ns, s, e, len(text))
            except OSError:
                continue

    async def fs_grep(ctx: dict[str, Any], pattern: str, root: str | None = None, glob: str | None = None,
                      context: int = 0, ignore_case: bool = False) -> Any:
        try:
            if not isinstance(pattern, str) or not pattern:
                raise FsError("pattern is empty")
            _g, base = base_for(root, ctx)
            ctxn = max(0, min(int(context or 0), 5))
            rg = shutil.which("rg")
            if rg:
                try:
                    rows, truncated = await asyncio.to_thread(grep_rg, rg, base, pattern, glob, ctxn, bool(ignore_case))
                except (subprocess.TimeoutExpired, OSError):
                    rows, truncated = await asyncio.to_thread(grep_python, base, pattern, glob, ctxn, bool(ignore_case))
            else:
                rows, truncated = await asyncio.to_thread(grep_python, base, pattern, glob, ctxn, bool(ignore_case))
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_grep", e, field="pattern", example={"pattern": "TODO", "glob": "**/*.py"})
        await asyncio.to_thread(record_grep, conv_of(ctx), rows)
        hits = sum(1 for r in rows if not r.get("context"))
        return {"root": str(base), "pattern": pattern, "matches": rows, "count": hits, "truncated": truncated,
                **({"note": f"stopped at {GREP_CAP} matches; narrow the pattern or pass glob"} if truncated else {})}
    R("fs_grep", ToolSpec("fs_grep", "Search file contents under a root with a regular expression. Returns path, line number and the line (cut at 400 characters), at most 200 matches; binary files and secret files are skipped. glob narrows the files (e.g. **/*.py); context adds lines around each match. Read a match's file with read_local_file, change it with fs_edit.",
        _obj({"pattern": {"type": "string", "description": "Regular expression"},
              "root": {"type": "string", "description": "Folder or file to search"},
              "glob": {"type": "string", "description": "Only files matching this glob"},
              "context": {"type": "integer", "default": 0, "description": "Lines of context, max 5"},
              "ignore_case": {"type": "boolean", "default": False}}, ["pattern"]), fs_grep, "files",
        examples=[{"pattern": "TODO", "root": "~/Documents/project", "glob": "**/*.py"}, {"pattern": "def main", "context": 2}], taints=True))

    def snapshot(ctx: dict[str, Any], p: Path) -> dict[str, Any] | None:
        """Pre-image for undo. Desk workspaces sit outside the home folder rules and keep their own baseline."""
        fs = box.filesnap
        if fs is None:
            return None
        try:
            return fs.capture("overwrite", str(p), ctx)
        except mac.LocalPathError:
            return None

    async def fs_edit(ctx: dict[str, Any], path: str, old: str, new: str, replace_all: bool = False) -> Any:
        snap: dict[str, Any] | None = None
        try:
            g = grants_for(box, ctx)
            p = write_target(path, ctx, g)
            if not p.is_file():
                raise FsError(f"{p} is not an existing file; use write_local_file to create one")
            st = p.stat()
            if st.st_size > EDIT_MAX_BYTES:
                raise FsError(f"{p.name} is larger than {EDIT_MAX_BYTES // 1_000_000} MB")
            try:
                before = p.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                raise FsError(f"{p.name} is not UTF-8 text") from None
            try:
                after, matcher, n, regions, new_regions = replace_text(before, str(old), str(new), bool(replace_all))
            except EditError as e:
                raise FsError(str(e)) from None
            conv = conv_of(ctx)
            if need_reads():
                why = ledger.check(conv, p, st.st_mtime_ns, regions, in_desk=g.in_desk(p))
                if why:
                    raise FsError(why)
            snap = await asyncio.to_thread(snapshot, ctx, p)
            await asyncio.to_thread(_atomic_write, p, after.encode("utf-8"))
        except FsError as e:
            return fail("fs_edit", e, field="path")
        except OSError as e:
            if snap and snap.get("snapshot_id"):
                await asyncio.to_thread(box.filesnap.discard, snap["snapshot_id"])
            return fail("fs_edit", e.strerror or e, field="path")
        out: dict[str, Any] = {"path": str(p), "matcher": matcher, "replacements": n, "diff": unified(before, after, p.name)}
        if snap is not None:
            sid = snap.get("snapshot_id")
            if sid:
                await asyncio.to_thread(box.filesnap.finalize, sid, str(p))
            out["undo"] = {"snapshot_id": sid, **({"reason": snap["reason"]} if snap.get("reason") else {})}
        # the agent knows this content now: it just wrote it
        ledger.after_edit(conv, p, p.stat().st_mtime_ns, len(after), regions[0][0], new_regions)
        err = syntax_check(p, after)
        if err:
            out["syntax_error"] = err
            out["note"] = "the file was written, but it does not parse; fix it with another fs_edit"
        return out
    R("fs_edit", ToolSpec("fs_edit", "Change a file by replacing exact text: old must match once (or pass replace_all). If the exact text is not found it also tries matching ignoring each line's indentation, then matching by a block's first and last line. Returns a unified diff and which matcher applied. Read the part you are changing first. Python, JSON, TOML and YAML files are syntax-checked afterwards. Outside the desk workspace and the user's workspace folders it asks first.",
        _obj({"path": {"type": "string"}, "old": {"type": "string", "description": "Text to replace, copied exactly"},
              "new": {"type": "string", "description": "Replacement text"},
              "replace_all": {"type": "boolean", "default": False}}, ["path", "old", "new"]), fs_edit, "files", "writes",
        examples=[{"path": "~/Documents/project/main.py", "old": "retries = 2", "new": "retries = 5"}]))

    def _copy_tree(src: Path, dst: Path) -> int:
        n = total = 0
        for d, dirs, files in os.walk(src, followlinks=False):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS]
            rel = Path(d).relative_to(src)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for f in files:
                s = Path(d) / f
                if s.is_symlink() or sensitive_reason(s) or s.suffix.lower() in mac.BLOCKED_WRITE_SUFFIXES:
                    continue
                n += 1
                total += s.stat().st_size
                if n > COPY_MAX_FILES or total > COPY_MAX_BYTES:
                    raise FsError(f"folder is larger than {COPY_MAX_FILES} files or {COPY_MAX_BYTES // 1_000_000} MB")
                shutil.copy2(s, dst / rel / f)
        return n

    async def fs_copy(ctx: dict[str, Any], src: str, dst: str) -> Any:
        snap: dict[str, Any] | None = None
        try:
            g = grants_for(box, ctx)
            s = resolve_path(src, g)
            if not s.exists():
                raise FsError(f"{s} does not exist")
            why = sensitive_reason(Path(os.path.expanduser(str(src))), s)
            if why:
                raise FsError(why)
            d = write_target(dst, ctx, g)
            if d.is_dir() and not (s.is_dir() and not any(d.iterdir())):
                d = write_target(str(d / s.name), ctx, g)
            if d.exists():
                raise FsError(f"{d} already exists; copy never overwrites, pick another name")
            if d == s or d.is_relative_to(s):
                raise FsError("cannot copy a folder into itself")
            d.parent.mkdir(parents=True, exist_ok=True)
            if box.filesnap is not None and s.is_file():  # undoing a copy trashes the new file
                try:
                    snap = await asyncio.to_thread(box.filesnap.capture, "create", str(d), ctx)
                except mac.LocalPathError:
                    snap = None  # a desk workspace sits outside the home rules and keeps its own baseline
            if s.is_dir():
                count = await asyncio.to_thread(_copy_tree, s, d)
            else:
                await asyncio.to_thread(shutil.copy2, s, d)
                count = 1
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_copy", e, field="dst")
        except OSError as e:
            return fail("fs_copy", e.strerror or e, field="dst")
        out: dict[str, Any] = {"from": str(s), "path": str(d), "files": count}
        if snap and snap.get("snapshot_id"):
            await asyncio.to_thread(box.filesnap.finalize, snap["snapshot_id"], str(d))
            out["undo"] = {"snapshot_id": snap["snapshot_id"]}
        return out
    R("fs_copy", ToolSpec("fs_copy", "Copy a file or folder to a new path. Never overwrites: the destination must not exist (a destination folder that exists receives the copy under the same name). Secret files are not copied. Writing outside the desk workspace and the user's workspace folders asks first.",
        _obj({"src": {"type": "string"}, "dst": {"type": "string"}}, ["src", "dst"]), fs_copy, "files", "writes",
        examples=[{"src": "~/Documents/project/config.json", "dst": "~/Documents/project/config.backup.json"}]))

    async def fs_mkdir(ctx: dict[str, Any], path: str) -> Any:
        try:
            p = write_target(path, ctx, grants_for(box, ctx))
            if p.exists() and not p.is_dir():
                raise FsError(f"{p} exists and is a file")
            existed = p.is_dir()
            await asyncio.to_thread(p.mkdir, 0o755, True, True)
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_mkdir", e, field="path")
        except OSError as e:
            return fail("fs_mkdir", e.strerror or e, field="path")
        return {"path": str(p), "created": not existed}
    R("fs_mkdir", ToolSpec("fs_mkdir", "Create a folder, with any missing parents. Succeeds quietly if it already exists. Outside the desk workspace and the user's workspace folders it asks first.",
        _obj({"path": {"type": "string"}}, ["path"]), fs_mkdir, "files", "writes",
        examples=[{"path": "~/Documents/project/reports/2026"}]))
