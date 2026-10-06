"""File tools for anywhere on this Mac: glob, grep, exact-string edit, copy and mkdir.

The agent works on a folder cheaply and safely with these instead of rewriting whole files. Three ideas hold
the module together:

* Scope and approval. The tools reach anywhere `mac.allowed_path` allows, which is the whole Mac minus Grain's own data
  folder and app (`mac.protected_reason`). A call is not refused outright for being risky: `needs_approval` /
  `needs_ask` say when it needs the user, the reply loop turns the call into an approval card, and the tool only
  proceeds when the loop sets `ctx["fs_outside_ok"]` after the user said yes. That is the case for a credential
  store (`mac.sensitive_reason`) read or written directly, and for any write outside the active desk's workspace
  while the reply has read untrusted content. An unattended background run may only write inside a desk workspace.
* Read before write. Every read that returns content records what was seen (file, mtime, character ranges) in
  `ReadLedger`. An edit needs the region it changes to have been read, an overwrite needs the whole file, and a
  file that changed since it was read is refused rather than clobbered.
* Credentials stay out of scans. glob and grep skip credential files inside a tree, whichever way a symlink reaches
  them; device files (/dev, /proc) are refused outright.

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

from . import mac, permissions, redact

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
RESERVED_DESK_DIRS = (".baseline", ".trash", "inputs")  # bookkeeping and the user's input snapshots, never a write target


class FsError(Exception):
    """A clean one-line failure for the tool error envelope."""


# ---------------------------------------------------------------- grants and paths
class Grants:
    """What a run is sure to be allowed to touch: its desk's workspace (which sits in the protected data folder)."""

    def __init__(self, desk: Path | None) -> None:
        self.desk = desk

    def in_desk(self, p: Path) -> bool:
        return self.desk is not None and (p == self.desk or p.is_relative_to(self.desk))

    def default_root(self) -> Path:
        return self.desk or mac.home()


def carry_desk_copies(box: Any, ctx: dict[str, Any], g: Grants, paths: list[Path]) -> None:
    """A download copied or edited into another workspace path stays a download."""
    did = str(ctx.get("desk_id") or "")
    ws = getattr(box, "workspace", None)
    if not did or ws is None or g.desk is None:
        return
    rels: list[str] = []
    for raw in paths:
        try:
            p = raw.resolve()
        except OSError:
            continue
        if not p.is_file() or not g.in_desk(p) or p == g.desk:
            continue
        rels.append(p.relative_to(g.desk).as_posix())
    if rels:
        try:
            ws.carry_fetch_copies(did, rels)
        except Exception:  # noqa: BLE001 - recording a copy must not fail the write that already landed
            return


def grants_for(box: Any, ctx: dict[str, Any]) -> Grants:
    desk: Path | None = None
    did, ws = ctx.get("desk_id"), getattr(box, "workspace", None)
    if did and ws is not None:
        try:
            desk = ws.desk_root(str(did)).resolve()
        except Exception:  # noqa: BLE001 - a malformed id means "no desk", not a crash
            desk = None
    return Grants(desk)


def sensitive_reason(*paths: str | Path) -> str | None:
    """`mac.sensitive_reason`: why a path is a credential store (or a device file). Kept here for the callers that
    refuse such a path outright (mail attachments, uploads, the browser)."""
    return mac.sensitive_reason(*paths)


def resolve_path(raw: Any, g: Grants, *, write: bool = False) -> Path:
    """Spell out a path the way the user meant it, resolve symlinks, and require it to be somewhere the agent
    may be: inside the desk workspace, or anywhere `mac.allowed_path` allows (the whole Mac but Grain's own folder and app)."""
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
        if r != g.desk and write and r.relative_to(g.desk).parts[0].casefold() in RESERVED_DESK_DIRS:
            raise FsError(f"{r.relative_to(g.desk).parts[0]} holds the workspace's own bookkeeping or the user's inputs and is "
                          "read-only; write elsewhere in the workspace")
    else:
        try:
            r = mac.allowed_path(str(r))
        except mac.LocalPathError as e:
            raise FsError(str(e)) from None
        if mac.is_device(r):
            raise FsError("device and process files are off limits")
    if write:
        bad = next((x.lower() for x in (Path(q).suffix for q in r.parts) if x.lower() in mac.BLOCKED_WRITE_SUFFIXES), None)
        if bad:
            raise FsError(f"{bad} files are off limits; write a document instead")
    return r


# Tools whose results are the user's own local files: reading them does not make a reply untrusted for this purpose.
LOCAL_SOURCES = frozenset({"read_local_file", "fs_grep", "fs_glob", "find_files"})


def untrusted(ctx: dict[str, Any]) -> bool:
    """The reply has read content from outside the user's own folders (web, mail, a connector). Reading a local file
    alone does not count: they are the user's own files."""
    if not ctx.get("tainted"):
        return False
    sources = ctx.get("taint_sources") or []
    return not sources or any(x not in LOCAL_SOURCES for x in sources)


def approval_reason(p: Path, ctx: dict[str, Any], g: Grants, *, write: bool = True, raw: Any = None) -> str | None:
    """Why this access needs the user's OK, or None. Inside the desk workspace never. Elsewhere a credential store
    (the spelled path or the resolved one) always does, for a read as well as a write, and a write also does while
    the reply has read untrusted content."""
    if g.in_desk(p):
        return None
    spelled = mac._spelled(raw) if isinstance(raw, (str, Path)) and str(raw).strip() else p
    if why := mac.sensitive_reason(spelled, p):
        return why
    if write and untrusted(ctx):
        return "this reply has read untrusted content, so a write outside the desk workspace needs a yes"
    return None


def needs_approval(p: Path, ctx: dict[str, Any], g: Grants, raw: Any = None) -> bool:
    """True for a write the user has not pre-authorised: a credential store, or any write outside a desk workspace
    while the reply has read untrusted content."""
    return approval_reason(p, ctx, g, raw=raw) is not None


def write_target(raw: Any, ctx: dict[str, Any], g: Grants) -> Path:
    p = resolve_path(raw, g, write=True)
    if g.in_desk(p):
        return p
    if ctx.get("proposal_only"):
        raise FsError("this is an unattended background run, which may only write inside a desk workspace")
    if (why := approval_reason(p, ctx, g, raw=raw)) and not ctx.get("fs_outside_ok"):
        raise FsError(f"{p.name}: {why}; it needs the user's approval, so tell them what you would change")
    return p


# Which argument of each tool names a path the user may have to approve: writes (a credential store, or any path
# after untrusted content) and reads of a credential store.
WRITE_ARGS = {"fs_edit": ("path",), "fs_mkdir": ("path",), "fs_copy": ("dst",), "write_local_file": ("path",),
              "move_local_file": ("path", "to"), "trash_local_file": ("path",)}
LOCAL_TOOLS = frozenset({"read_local_file", "write_local_file", "move_local_file", "trash_local_file"})
READ_ARGS = {"read_local_file": ("path",), "fs_grep": ("root",), "fs_glob": ("root",), "fs_copy": ("src",)}


def needs_ask(box: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """For the reply loop: should this call be shown to the user as an approval card? A path that does not
    resolve is not a reason to ask; the tool reports it."""
    if not isinstance(args, dict) or (name not in WRITE_ARGS and name not in READ_ARGS):
        return False
    g = Grants(None) if name in LOCAL_TOOLS else grants_for(box, ctx)  # the local-file tools never target a desk workspace
    for write, keys in ((True, WRITE_ARGS.get(name, ())), (False, READ_ARGS.get(name, ()))):
        for k in keys:
            if not str(args.get(k) or "").strip():
                continue
            try:
                if needs_approval_for(args[k], ctx, g, write):
                    return True
            except FsError:
                continue
    return False


def needs_approval_for(raw: Any, ctx: dict[str, Any], g: Grants, write: bool) -> bool:
    p = resolve_path(raw, g, write=write)
    return approval_reason(p, ctx, g, write=write, raw=raw) is not None


def system_write(box: Any, name: str, args: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """True when a write this call makes lands in a system area (mac.system_area): a soft force, so manual mode shows a
    card, auto mode sends it to strict review and allow-all runs it."""
    if not isinstance(args, dict):
        return False
    g = Grants(None) if name in LOCAL_TOOLS else grants_for(box, ctx)
    for k in WRITE_ARGS.get(name, ()):
        if not str(args.get(k) or "").strip():
            continue
        try:
            p = resolve_path(args[k], g, write=True)
        except FsError:
            continue
        if not g.in_desk(p) and mac.system_area(p):
            return True
    return False


def guard_local(box: Any, ctx: dict[str, Any], name: str, args: dict[str, Any]) -> None:
    """Run before write_local_file / move_local_file / trash_local_file: raises mac.LocalPathError when the call needs the
    user's OK (a credential store, or a write after untrusted content) and the reply loop has not got it."""
    if ctx.get("fs_outside_ok"):
        return
    g = Grants(None)
    for k in WRITE_ARGS.get(name, ()):
        raw = args.get(k)
        if not str(raw or "").strip():
            continue
        try:
            p = resolve_path(raw, g, write=True)
        except FsError:
            continue  # the tool reports a path it cannot use
        if why := approval_reason(p, ctx, g, raw=raw):
            raise mac.LocalPathError(f"{p.name}: {why}; it needs the user's approval, so tell them what you would change")


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


def glob_files(base: Path, pattern: str, allow_sensitive: bool = False) -> tuple[list[dict[str, Any]], bool]:
    """`allow_sensitive`: the user approved a credential folder as the root, so what is inside it is listed."""
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
            if not allow_sensitive and sensitive_reason(full):
                continue
            try:
                st = full.lstat()
                if full.is_symlink():
                    tgt = full.resolve()
                    if not (tgt == base or tgt.is_relative_to(base.resolve())) or (not allow_sensitive and sensitive_reason(tgt)):
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


def grep_python(base: Path, pattern: str, glob: str | None, context: int, ignore_case: bool,
                allow_sensitive: bool = False) -> tuple[list[dict[str, Any]], bool]:
    try:
        rx = re.compile(pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise FsError(f"bad regular expression: {e}") from None
    out: list[dict[str, Any]] = []
    for full, _rel in _grep_files(base, glob):
        if not allow_sensitive and sensitive_reason(full):
            continue
        try:
            if full.is_symlink():
                tgt = full.resolve()
                if (not allow_sensitive and sensitive_reason(tgt)) or (base.is_dir() and not tgt.is_relative_to(base.resolve())):
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


def grep_rg(rg: str, base: Path, pattern: str, glob: str | None, context: int, ignore_case: bool,
            allow_sensitive: bool = False) -> tuple[list[dict[str, Any]], bool]:
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
        if not allow_sensitive and sensitive_reason(pp):
            continue
        try:
            if pp.is_symlink() and ((not allow_sensitive and sensitive_reason(pp.resolve())) or (base.is_dir() and not pp.resolve().is_relative_to(base.resolve()))):
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
    """Run before read_local_file. Raises mac.LocalPathError for a device file, or for a credential store the user has not
    approved (`ctx["fs_outside_ok"]`, set by the reply loop after a yes); returns a result to send back instead of reading
    (a stub, or a refusal) when the same unchanged slice is requested again and again."""
    p = mac.allowed_path(path)
    why = sensitive_reason(mac._spelled(path), p)
    if why and (mac.is_device(p) or not ctx.get("fs_outside_ok")):
        raise mac.LocalPathError(f"{p.name}: {why}" + ("" if mac.is_device(p) else "; it needs the user's approval"))
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
        out = tool_error(f"{name}: {e}", alternative=ALTERNATIVE.get(name), **kw)
        if isinstance(out.get("error"), str):
            out["error"] = redact.scrub_command_output(out["error"])
        return out

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
            base = g.default_root()
            if not base.exists() and g.in_desk(base):
                base.mkdir(parents=True, exist_ok=True)
        why = None if g.in_desk(base) else sensitive_reason(mac._spelled(root) if root else base, base)
        if why and (mac.is_device(base) or not ctx.get("fs_outside_ok")):
            raise FsError(f"{base.name}: {why}" + ("" if mac.is_device(base) else "; it needs the user's approval"))
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
            rows, truncated = await asyncio.to_thread(glob_files, base, pattern.strip(), bool(sensitive_reason(base)))
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_glob", e, field="pattern", example={"pattern": "**/*.md"})
        total = len(rows)
        shown = []
        for r in rows[:GLOB_CAP]:
            item = dict(r)
            for key in ("path", "rel"):
                if isinstance(item.get(key), str):
                    item[key] = redact.scrub_command_output(item[key])
            shown.append(item)
        return {"root": redact.scrub_command_output(str(base)), "pattern": pattern, "files": shown, "total": total,
                "truncated": truncated or total > GLOB_CAP, "note": "newest first; dot-folders, node_modules and .git are skipped"}
    R("fs_glob", ToolSpec("fs_glob", "List files and folders under a root that match a glob (`**` crosses folders; a pattern with no slash matches at any depth), newest first, at most 500. Skips dot-folders, node_modules and .git. Root defaults to the desk workspace, else the home folder.",
        _obj({"pattern": {"type": "string", "description": "e.g. **/*.py or src/*.ts"},
              "root": {"type": "string", "description": "Folder to search; absolute or ~/ path, anywhere on this Mac"}}, ["pattern"]), fs_glob, "files",
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
            approved = bool(sensitive_reason(base))  # base_for let a credential root through: the user said yes
            rg = shutil.which("rg")
            if rg:
                try:
                    rows, truncated = await asyncio.to_thread(grep_rg, rg, base, pattern, glob, ctxn, bool(ignore_case), approved)
                except (subprocess.TimeoutExpired, OSError):
                    rows, truncated = await asyncio.to_thread(grep_python, base, pattern, glob, ctxn, bool(ignore_case), approved)
            else:
                rows, truncated = await asyncio.to_thread(grep_python, base, pattern, glob, ctxn, bool(ignore_case), approved)
        except (FsError, mac.LocalPathError) as e:
            return fail("fs_grep", e, field="pattern", example={"pattern": "TODO", "glob": "**/*.py"})
        await asyncio.to_thread(record_grep, conv_of(ctx), rows)
        hits = sum(1 for r in rows if not r.get("context"))
        shown = []
        for r in rows:
            item = dict(r)
            if isinstance(item.get("text"), str):
                item["text"] = redact.scrub_command_output(item["text"])
            if isinstance(item.get("path"), str):
                item["path"] = redact.scrub_command_output(item["path"])
            shown.append(item)
        return {"root": redact.scrub_command_output(str(base)), "pattern": pattern, "matches": shown, "count": hits,
                "truncated": truncated,
                **({"note": f"stopped at {GREP_CAP} matches; narrow the pattern or pass glob"} if truncated else {})}
    R("fs_grep", ToolSpec("fs_grep", "Search file contents under a root with a regular expression. Returns path, line number and the line (cut at 400 characters), at most 200 matches; binary files and secret files are skipped. glob narrows the files (e.g. **/*.py); context adds lines around each match. Read a match's file with read_local_file, change it with fs_edit.",
        _obj({"pattern": {"type": "string", "description": "Regular expression"},
              "root": {"type": "string", "description": "Folder or file to search"},
              "glob": {"type": "string", "description": "Only files matching this glob"},
              "context": {"type": "integer", "default": 0, "description": "Lines of context, max 5"},
              "ignore_case": {"type": "boolean", "default": False}}, ["pattern"]), fs_grep, "files",
        examples=[{"pattern": "TODO", "root": "~/Documents/project", "glob": "**/*.py"}, {"pattern": "def main", "context": 2}], taints=True))

    def snapshot(ctx: dict[str, Any], p: Path) -> dict[str, Any] | None:
        """Pre-image for undo. A desk workspace is in Grain's own data folder, which is off limits here, and keeps its own baseline."""
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
        carry_desk_copies(box, ctx, g, [p])
        out["path"] = redact.scrub_command_output(out["path"])
        out["diff"] = redact.scrub_command_output(out["diff"])
        if isinstance(out.get("syntax_error"), str):
            out["syntax_error"] = redact.scrub_command_output(out["syntax_error"])
        return out
    R("fs_edit", ToolSpec("fs_edit", "Change a file by replacing exact text: old must match once (or pass replace_all). If the exact text is not found it also tries matching ignoring each line's indentation, then matching by a block's first and last line. Returns a unified diff and which matcher applied. Read the part you are changing first. Python, JSON, TOML and YAML files are syntax-checked afterwards. A credential store (~/.ssh, keychains, browser cookies, .env files) needs the user's approval.",
        _obj({"path": {"type": "string"}, "old": {"type": "string", "description": "Text to replace, copied exactly"},
              "new": {"type": "string", "description": "Replacement text"},
              "replace_all": {"type": "boolean", "default": False}}, ["path", "old", "new"]), fs_edit, "files", "writes",
        examples=[{"path": "~/Documents/project/main.py", "old": "retries = 2", "new": "retries = 5"}]))

    def _copy_tree(src: Path, dst: Path, skipped: list[str]) -> int:
        n = total = 0
        for d, dirs, files in os.walk(src, followlinks=False):
            rel = Path(d).relative_to(src)
            skipped += [str(rel / x) + "/" for x in dirs if x.startswith(".") or x in SKIP_DIRS]
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS]
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for f in files:
                s = Path(d) / f
                if s.is_symlink() or sensitive_reason(s) or s.suffix.lower() in mac.BLOCKED_WRITE_SUFFIXES:
                    skipped.append(str(rel / f))
                    continue
                n += 1
                total += s.stat().st_size
                if n > COPY_MAX_FILES or total > COPY_MAX_BYTES:
                    raise FsError(f"folder is larger than {COPY_MAX_FILES} files or {COPY_MAX_BYTES // 1_000_000} MB")
                shutil.copy2(s, dst / rel / f)
        return n

    async def fs_copy(ctx: dict[str, Any], src: str, dst: str) -> Any:
        snap: dict[str, Any] | None = None
        skipped: list[str] = []
        made: Path | None = None
        try:
            g = grants_for(box, ctx)
            s = resolve_path(src, g)
            if not s.exists():
                raise FsError(f"{s} does not exist")
            why = None if g.in_desk(s) else sensitive_reason(mac._spelled(src), s)
            if why and (mac.is_device(s) or not ctx.get("fs_outside_ok")):
                raise FsError(f"{s.name}: {why}" + ("" if mac.is_device(s) else "; it needs the user's approval"))
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
                made = d  # it did not exist (checked above): a copy that fails part way removes it again
                count = await asyncio.to_thread(_copy_tree, s, d, skipped)
            else:
                await asyncio.to_thread(shutil.copy2, s, d)
                count = 1
        except (FsError, mac.LocalPathError, OSError) as e:
            if made is not None:
                await asyncio.to_thread(shutil.rmtree, made, True)
            return fail("fs_copy", (e.strerror or e) if isinstance(e, OSError) else e, field="dst")
        out: dict[str, Any] = {"from": str(s), "path": str(d), "files": count}
        if skipped:  # hidden folders, dependency folders, symlinks, secrets and launchers are left out on purpose
            out["skipped"] = len(skipped)
            out["skipped_sample"] = skipped[:10]
        if snap and snap.get("snapshot_id"):
            await asyncio.to_thread(box.filesnap.finalize, snap["snapshot_id"], str(d))
            out["undo"] = {"snapshot_id": snap["snapshot_id"]}
        copied = [d]
        if d.is_dir():
            copied = [Path(dirpath) / name for dirpath, _dirs, names in os.walk(d) for name in names]
        carry_desk_copies(box, ctx, g, copied)
        for key in ("from", "path"):
            if isinstance(out.get(key), str):
                out[key] = redact.scrub_command_output(out[key])
        return out
    R("fs_copy", ToolSpec("fs_copy", "Copy a file or folder to a new path. Never overwrites: the destination must not exist (a destination folder that exists receives the copy under the same name). Secret files, symlinks, hidden and dependency folders are not copied (counted in skipped). A credential store needs the user's approval.",
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
        return {"path": redact.scrub_command_output(str(p)), "created": not existed}
    R("fs_mkdir", ToolSpec("fs_mkdir", "Create a folder, with any missing parents. Succeeds quietly if it already exists. A credential store (~/.ssh, keychains, browser cookies, .env files) needs the user's approval.",
        _obj({"path": {"type": "string"}}, ["path"]), fs_mkdir, "files", "writes",
        examples=[{"path": "~/Documents/project/reports/2026"}]))

    for tool in WRITE_ARGS:  # a write into a system area is a soft force, on top of any force the tool already has
        if spec := box.specs.get(tool):
            spec.force_ask = (lambda a, c, n=tool, prev=spec.force_ask: bool(prev and prev(a, c)) or system_write(box, n, a, c))
