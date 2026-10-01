"""Per-desk workspace directories: the boundary of a desk agent's free autonomy.

A cowork desk gets one directory under `<data_dir>/cowork/<desk_id>/` holding `outputs/` (what the
user reviews), `work/` (scratch), `.baseline/` (each file as it was on the desk's first write, so
Files can show a real diff) and `.trash/` (nothing in a workspace is ever unlinked). Writing inside
it is danger `writes`, not `external`, precisely because the directory is the boundary — which is
only true if the boundary holds, so `resolve_in` is the one chokepoint every `desk_*` tool goes
through and the paranoid part of this module.

Containment is resolve-then-contain, not check-then-resolve: a relative path with a clean `..`-free
spelling can still land outside through a symlink the agent planted inside its own workspace, so
both the desk root and the candidate are `Path.resolve()`d and only then compared. The cheap string
checks (absolute, `..`, `~`, NUL) run first because they give a better error, not because they are
the defence.

Quotas are checked *before* the write and the refusal carries current usage, so a looping agent
fills a quota and is told what it filled rather than filling the disk. Usage counts everything under
the desk root including `.trash/` and `.baseline/`: trashing does not free quota, which is the
honest consequence of never unlinking.

No SQL here. `desks.workspace` stores the relative `cowork/<id>` (see `rel_root`), never an absolute
path, so moving the data directory does not strand every desk.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILE_CHARS = 400_000
MAX_FILES = 500
MAX_TOTAL_BYTES = 200_000_000
MAX_PREVIEW = 200_000
BLOCKED_SUFFIXES = (".command", ".app", ".scpt", ".applescript", ".workflow", ".terminal", ".shortcut")

BASELINE_DIR = ".baseline"
TRASH_DIR = ".trash"
SUBDIRS = ("outputs", "work", BASELINE_DIR, TRASH_DIR)
# Bookkeeping, not content: a write into either would make diff() lie or lose a trashed version.
RESERVED_DIRS = (BASELINE_DIR, TRASH_DIR)

# Desk ids are new_id() hex, but the id is a path segment, so it is validated as one.
DESK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SNIFF_BYTES = 8192
WRITE_MODES = ("create", "overwrite", "append")


class WorkspaceError(Exception):
    """A clean, single-line failure for the tool error envelope, carrying usage when a quota bit."""

    def __init__(self, message: str, *, usage: dict[str, int] | None = None):
        super().__init__(message)
        self.usage = usage or {"files": 0, "bytes": 0}


def _is_text(path: Path) -> bool:
    """A NUL byte in the first few KB is the usual, cheap "this is not prose" signal."""
    try:
        with path.open("rb") as fh:
            return b"\x00" not in fh.read(SNIFF_BYTES)
    except OSError:
        return False


def _blocked(parts: tuple[str, ...]) -> str | None:
    """A blocked suffix anywhere in the path, not just on the leaf: `Thing.app/run.sh` is a bundle."""
    for part in parts:
        suffix = PurePosixPath(part).suffix.lower()
        if suffix in BLOCKED_SUFFIXES:
            return suffix
    return None


def _oserror(rel: str, what: str, e: OSError, hint: str = "") -> WorkspaceError:
    """An OSError's text carries the absolute data-dir path and the message is handed to the model,
    so every filesystem failure is re-raised against the workspace-relative path instead."""
    tail = f"; {hint}" if hint else ""
    return WorkspaceError(f"{rel} could not be {what} ({e.__class__.__name__}){tail}")


def _unique(dest: Path) -> Path:
    """Finder's rule: `draft.md`, `draft 2.md`, `draft 3.md`. Never overwrite what is already there."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} {n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class Workspace:
    """Path-safe file access scoped to one desk. `max_*` exist so tests can hit a quota cheaply."""

    def __init__(self, data_dir: str | Path, *, max_files: int = MAX_FILES,
                 max_total_bytes: int = MAX_TOTAL_BYTES) -> None:
        self.root = Path(data_dir) / "cowork"
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes

    # ---- layout ----
    def desk_root(self, desk_id: str) -> Path:
        if not isinstance(desk_id, str) or not DESK_ID_RE.match(desk_id):
            raise WorkspaceError(f"{desk_id!r} is not a desk id")
        return self.root / desk_id

    def rel_root(self, desk_id: str) -> str:
        """What `desks.workspace` stores: relative to the data dir, so the dir can move."""
        return f"cowork/{self.desk_root(desk_id).name}"

    def ensure(self, desk_id: str) -> Path:
        root = self.desk_root(desk_id)
        for sub in SUBDIRS:
            try:
                (root / sub).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise _oserror(sub, "created", e) from e
        return root

    # ---- layer 1: containment ----
    def resolve_in(self, desk_id: str, rel: str) -> Path:
        """The one chokepoint. Reject the obvious spellings, then resolve both sides and contain.

        Checking the string and resolving afterwards is what a symlink defeats, so the containment
        test runs on the resolved path: every parent component is followed, including one the agent
        created itself, and including the case where the leaf does not exist yet.
        """
        if not isinstance(rel, str):
            raise WorkspaceError("path must be a string")
        if "\x00" in rel:
            raise WorkspaceError("path contains a NUL byte")
        if rel.startswith("/") or rel.startswith("~") or PurePosixPath(rel).is_absolute():
            raise WorkspaceError(f"{rel!r} is an absolute path; desk paths are relative to the workspace")
        parts = PurePosixPath(rel).parts
        for part in parts:
            if part == "..":
                raise WorkspaceError(f"{rel!r} contains a '..' segment; desk paths may not walk upwards")
            if part.startswith("~"):
                raise WorkspaceError(f"{rel!r} starts a segment with '~'")
            if not part.strip():
                raise WorkspaceError(f"{rel!r} contains a blank path segment")

        root = self.desk_root(desk_id).resolve()
        target = (root / PurePosixPath(*parts)).resolve() if parts else root
        if target != root and not target.is_relative_to(root):
            raise WorkspaceError(f"{rel!r} resolves outside the desk workspace")
        return target

    def _rel_of(self, desk_id: str, path: Path) -> str:
        return path.relative_to(self.desk_root(desk_id).resolve()).as_posix()

    def _baseline_of(self, desk_id: str, rel: str) -> Path:
        root = self.desk_root(desk_id).resolve()
        return root / BASELINE_DIR / PurePosixPath(rel)

    # ---- reads ----
    def read(self, desk_id: str, rel: str, offset: int = 0, length: int = 6000) -> dict[str, Any]:
        """A character window snapped back to a line boundary, with `next_offset` to continue.

        Paging rather than one big read is also the mitigation for the flat tool-result cap.
        """
        p = self.resolve_in(desk_id, rel)
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory, not a file")
        if not p.is_file():
            raise WorkspaceError(f"{rel} does not exist in this workspace")
        try:
            data = p.read_bytes()
        except OSError as e:
            raise _oserror(rel, "read", e) from e
        if b"\x00" in data[:SNIFF_BYTES]:
            raise WorkspaceError(f"{rel} is not a text file ({len(data)} bytes)")
        text = data.decode("utf-8", errors="replace")
        off = max(0, min(int(offset), len(text)))
        ln = max(1, min(int(length), MAX_PREVIEW))
        end = off + ln
        if end < len(text):
            cut = text.rfind("\n", off, end)
            if cut > off:
                end = cut + 1
        out: dict[str, Any] = {"path": rel, "text": text[off:end], "offset": off, "chars": len(text),
                               "bytes": len(data), "truncated": end < len(text)}
        if out["truncated"]:
            out["next_offset"] = end
        return out

    def state(self, desk_id: str, rel: str) -> str:
        """new | modified | unchanged, against `.baseline/`. No baseline means the desk made it."""
        p = self.resolve_in(desk_id, rel)
        base = self._baseline_of(desk_id, self._rel_of(desk_id, p))
        if not base.is_file():
            return "new"
        if not p.is_file():
            return "modified"
        return "unchanged" if base.read_bytes() == p.read_bytes() else "modified"

    def tree(self, desk_id: str, sub: str = "") -> list[dict[str, Any]]:
        root = self.desk_root(desk_id).resolve()
        base = self.resolve_in(desk_id, sub)
        if not base.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            if here == root:
                dirnames[:] = [d for d in dirnames if d not in RESERVED_DIRS]
            dirnames.sort()
            for name in dirnames + sorted(filenames):
                p = here / name
                if p.is_symlink():  # a symlink is never content: resolve_in would refuse most of them
                    continue
                rel = p.relative_to(root).as_posix()
                is_dir = p.is_dir()
                try:
                    st = p.stat()
                except OSError:
                    continue
                entries.append({
                    "path": rel,
                    "bytes": 0 if is_dir else st.st_size,
                    "modified": st.st_mtime,
                    "is_dir": is_dir,
                    "is_text": False if is_dir else _is_text(p),
                    "state": "unchanged" if is_dir else self.state(desk_id, rel),
                })
                if len(entries) >= self.max_files:
                    return sorted(entries, key=lambda e: e["path"])
        return sorted(entries, key=lambda e: e["path"])

    def diff(self, desk_id: str, rel: str) -> dict[str, Any]:
        """A real unified diff of `.baseline/<rel>` against the file now; a new file is all additions."""
        p = self.resolve_in(desk_id, rel)
        rel = self._rel_of(desk_id, p)
        base = self._baseline_of(desk_id, rel)
        before = base.read_text(encoding="utf-8", errors="replace") if base.is_file() else ""
        after = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        text = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                            fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
        added = removed = 0
        for line in difflib.ndiff(before.splitlines(), after.splitlines()):
            if line.startswith("+ "):
                added += 1
            elif line.startswith("- "):
                removed += 1
        return {"path": rel, "state": self.state(desk_id, rel), "diff": text[:MAX_PREVIEW],
                "truncated": len(text) > MAX_PREVIEW, "added": added, "removed": removed,
                "has_baseline": base.is_file()}

    def sha(self, desk_id: str, rel: str) -> str:
        p = self.resolve_in(desk_id, rel)
        if not p.is_file():
            raise WorkspaceError(f"{rel} does not exist in this workspace")
        h = hashlib.sha256()
        try:
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError as e:
            raise _oserror(rel, "hashed", e) from e
        return h.hexdigest()

    def usage(self, desk_id: str) -> dict[str, int]:
        """Everything under the desk root, `.trash/` and `.baseline/` included — that is the disk."""
        root = self.desk_root(desk_id)
        files = total = 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
                files += 1
        return {"files": files, "bytes": total}

    # ---- writes ----
    def write(self, desk_id: str, rel: str, content: str, mode: str = "create") -> dict[str, Any]:
        """create | overwrite | append. Quotas and suffixes are checked before anything is written."""
        if mode not in WRITE_MODES:
            raise WorkspaceError(f"mode must be one of {', '.join(WRITE_MODES)}, not {mode!r}")
        if not isinstance(content, str):
            raise WorkspaceError("content must be a string")
        root = self.ensure(desk_id).resolve()
        p = self.resolve_in(desk_id, rel)
        if p == root:
            raise WorkspaceError("path must name a file, not the workspace root")
        rel = self._rel_of(desk_id, p)
        parts = PurePosixPath(rel).parts
        if parts[0] in RESERVED_DIRS:
            raise WorkspaceError(f"{parts[0]}/ is reserved for the workspace itself and is not writable")
        suffix = _blocked(parts)
        if suffix:
            raise WorkspaceError(f"{suffix} files cannot be written to a workspace", usage=self.usage(desk_id))
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory, not a file")
        if len(content) > MAX_FILE_CHARS:
            raise WorkspaceError(
                f"{rel} would be {len(content)} characters; the limit for one write is {MAX_FILE_CHARS}. "
                "Split it across files or write less.", usage=self.usage(desk_id))
        exists = p.is_file()
        if exists and mode == "create":
            raise WorkspaceError(
                f"{rel} already exists; pass mode='overwrite' to replace it or mode='append' to add to it")
        if not exists and mode == "append":
            mode = "create"

        old_bytes = p.stat().st_size if exists else 0
        base = self._baseline_of(desk_id, rel)
        snapshot = exists and not base.exists()
        new_bytes = len(content.encode("utf-8"))
        added_bytes = (new_bytes if mode == "append" else new_bytes - old_bytes) + (old_bytes if snapshot else 0)
        added_files = (0 if exists else 1) + (1 if snapshot else 0)

        use = self.usage(desk_id)
        if use["files"] + added_files > self.max_files:
            raise WorkspaceError(
                f"this workspace already holds {use['files']} files and the limit is {self.max_files}. "
                "Trash a file or write into one you already have.", usage=use)
        if use["bytes"] + added_bytes > self.max_total_bytes:
            raise WorkspaceError(
                f"this workspace already holds {use['bytes']} bytes and the limit is {self.max_total_bytes}; "
                f"that write adds {added_bytes}.", usage=use)

        try:
            if snapshot:
                base.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, base)
            # mkdir raises FileExistsError/NotADirectoryError when an ancestor is a regular file.
            p.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with p.open("a", encoding="utf-8") as fh:
                    fh.write(content)
            else:
                # Write beside the target and rename, so an interrupted write cannot leave a half file.
                tmp = p.with_name(f".{p.name}.tmp")
                tmp.write_text(content, encoding="utf-8")
                os.replace(tmp, p)
        except OSError as e:
            raise _oserror(rel, "written", e, "check that no parent of that path is already a file") from e
        return {"path": rel, "mode": mode, "created": not exists, "bytes": p.stat().st_size,
                "state": self.state(desk_id, rel), "usage": self.usage(desk_id)}

    def trash(self, desk_id: str, rel: str) -> dict[str, Any]:
        """Move into `.trash/`, keeping the relative path and suffixing the name. Never unlink."""
        root = self.ensure(desk_id).resolve()
        p = self.resolve_in(desk_id, rel)
        if p == root:
            raise WorkspaceError("the workspace root cannot be trashed")
        rel = self._rel_of(desk_id, p)
        head = PurePosixPath(rel).parts[0]
        if head == TRASH_DIR:
            raise WorkspaceError(f"{rel} is already in the trash")
        if head in RESERVED_DIRS:
            # write() refuses these for the same reason: moving .baseline/ away would destroy the
            # only before-copy the review diff is computed against.
            raise WorkspaceError(f"{head}/ is reserved for the workspace itself and cannot be trashed")
        if not p.exists():
            raise WorkspaceError(f"{rel} does not exist in this workspace")
        dest = _unique(root / TRASH_DIR / PurePosixPath(rel))
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
        except OSError as e:
            raise _oserror(rel, "trashed", e) from e
        return {"path": rel, "trashed_to": dest.relative_to(root).as_posix(),
                "usage": self.usage(desk_id)}

    def purge(self, desk_id: str) -> None:
        """Only ever called behind an explicit `?purge=true`: a desk's files are unregenerable."""
        shutil.rmtree(self.desk_root(desk_id), ignore_errors=True)
