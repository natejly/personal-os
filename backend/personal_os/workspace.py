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
# What the user handed the desk: snapshot copies, read-only to every desk writer (write, trash, downloads, fs_*).
# A shell or script can still change them, so each also gets a baseline copy and a change shows as `modified`.
INPUTS_DIR = "inputs"
INPUTS_MANIFEST = "MANIFEST.md"

# Desk ids are new_id() hex, but the id is a path segment, so it is validated as one.
DESK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SNIFF_BYTES = 8192
WRITE_MODES = ("create", "overwrite", "append")
# Formats whose extracted text, written out under a new name, is still the download.
_EXTRACT_SUFFIXES = frozenset({".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".rtf"})
# A shorter shared opening is too common to treat as the same download.
_CARRY_PREFIX = 80


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


def _read_only(rel: str) -> WorkspaceError | None:
    head = PurePosixPath(rel).parts[0] if rel else ""
    if head.casefold() == INPUTS_DIR:
        return WorkspaceError(f"{INPUTS_DIR}/ holds the inputs the user handed this desk and is read-only; "
                              "write your own copy under work/ instead")
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

    def _fetch_ledger(self, desk_id: str) -> Path:
        """Outside the workspace, so a desk cannot delete the record of a file it downloaded."""
        name = self.desk_root(desk_id).name
        return self.root.parent / "cowork-fetched" / name

    def _fetch_rows(self, desk_id: str) -> list[tuple[str, str]]:
        """Ledger lines are `path` or `path\\tsha256`. A hash lets a rename be recognized after the old path is gone."""
        path = self._fetch_ledger(desk_id)
        if not path.is_file():
            return []
        rows: list[tuple[str, str]] = []
        for ln in path.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            rel, _, digest = ln.partition("\t")
            rows.append((rel.strip(), digest.strip()))
        return rows

    def note_fetch(self, desk_id: str, rel: str) -> None:
        rel = self._rel_of(desk_id, self.resolve_in(desk_id, rel))
        digest = ""
        try:
            src = self.resolve_in(desk_id, rel)
            if src.is_file():
                data = src.read_bytes()
                if data:
                    digest = hashlib.sha256(data).hexdigest()
        except OSError:
            digest = ""
        path = self._fetch_ledger(desk_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [(r, d) for r, d in self._fetch_rows(desk_id) if r != rel]
        rows.append((rel, digest))
        path.write_text("".join(f"{r}\t{d}\n" if d else f"{r}\n" for r, d in sorted(rows)))

    def was_fetched(self, desk_id: str, rel: str) -> bool:
        try:
            rel = self._rel_of(desk_id, self.resolve_in(desk_id, rel))
        except WorkspaceError:
            return False
        return any(r == rel for r, _ in self._fetch_rows(desk_id))

    def fetched_paths(self, desk_id: str) -> list[str]:
        """Relative paths this desk downloaded or copied out of a networked sandbox."""
        return [r for r, _ in self._fetch_rows(desk_id)]

    def carry_fetch_copies(self, desk_id: str, rels: list[str] | None = None) -> list[str]:
        """Mark workspace paths that now hold a download's bytes, or the text extracted from one.

        `desk_write_file` is not the only way a file appears: a script, a copy, an edit, or a rename
        can put the same bytes under a new name. `rels` is those paths; None walks the workspace.
        A missing ledger path widens the walk, because a rename keeps the file's timestamp."""
        noted: list[str] = []
        rows = self._fetch_rows(desk_id)
        if not rows:
            return noted
        digests = {d for _, d in rows if d}
        missing = False
        blobs: list[tuple[str, bytes]] = []
        for src_rel, _digest in rows:
            try:
                src = self.resolve_in(desk_id, src_rel)
            except WorkspaceError:
                missing = True
                continue
            if not src.is_file():
                missing = True
                continue
            try:
                blobs.append((src_rel, src.read_bytes()))
            except OSError:
                missing = True
                continue
        if missing:
            rels = None
        if not blobs and not digests:
            return noted
        if rels is None:
            rels = [e["path"] for e in self.tree(desk_id) if not e.get("is_dir")]
        extract = None
        for rel in rels:
            try:
                if self.was_fetched(desk_id, rel):
                    continue
                dest = self.resolve_in(desk_id, rel)
            except WorkspaceError:
                continue
            if not dest.is_file():
                continue
            try:
                data = dest.read_bytes()
            except OSError:
                continue
            if not data:
                continue
            if hashlib.sha256(data).hexdigest() in digests:
                self.note_fetch(desk_id, rel)
                noted.append(rel)
                continue
            if any(raw == data and src_rel != rel for src_rel, raw in blobs):
                self.note_fetch(desk_id, rel)
                noted.append(rel)
                continue
            if any(src_rel != rel and len(raw) >= _CARRY_PREFIX and (data.startswith(raw) or (len(data) >= _CARRY_PREFIX and raw.startswith(data)))
                   for src_rel, raw in blobs):
                self.note_fetch(desk_id, rel)
                noted.append(rel)
                continue
            for src_rel, raw in blobs:
                if src_rel == rel or PurePosixPath(src_rel).suffix.lower() not in _EXTRACT_SUFFIXES:
                    continue
                if extract is None:
                    from . import extract_text as xt
                    extract = xt.extract_text
                try:
                    shown = extract(PurePosixPath(src_rel).name, raw)
                except Exception:  # noqa: BLE001 - a reader failure must not fail the write
                    continue
                if shown and shown.encode("utf-8") == data:
                    self.note_fetch(desk_id, rel)
                    noted.append(rel)
                    break
        return noted

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

    def read_bytes(self, desk_id: str, rel: str, max_bytes: int = 50_000_000) -> bytes:
        """The raw bytes of a workspace file, for the readers that handle PDFs, office files and pictures.
        `read` refuses a NUL-bearing file on purpose (it returns text); this one leaves the judgement to the caller."""
        p = self.resolve_in(desk_id, rel)
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory, not a file")
        if not p.is_file():
            raise WorkspaceError(f"{rel} does not exist in this workspace")
        try:
            if p.stat().st_size > max_bytes:
                raise WorkspaceError(f"{rel} is {p.stat().st_size} bytes; the limit for reading one file is {max_bytes}")
            return p.read_bytes()
        except OSError as e:
            raise _oserror(rel, "read", e) from e

    def reserve_file(self, desk_id: str, rel: str) -> tuple[Path, int]:
        """Where a binary download may land, and how many bytes the quota still has room for.

        `write` is text-only, so a downloader streams into a file of its own; this is the same gate in front of it:
        reserved dirs, blocked suffixes, the file-count and byte quotas. An existing name is never overwritten
        (Finder's `name 2.ext` rule), so a second download of the same URL cannot destroy the first.
        """
        root = self.ensure(desk_id).resolve()
        p = self.resolve_in(desk_id, rel)
        if p == root:
            raise WorkspaceError("path must name a file, not the workspace root")
        parts = PurePosixPath(self._rel_of(desk_id, p)).parts
        if parts[0] in RESERVED_DIRS:
            raise WorkspaceError(f"{parts[0]}/ is reserved for the workspace itself and is not writable")
        if err := _read_only(parts[0]):
            raise err
        suffix = _blocked(parts)
        if suffix:
            raise WorkspaceError(f"{suffix} files cannot be written to a workspace", usage=self.usage(desk_id))
        if p.is_dir():
            raise WorkspaceError(f"{rel} is a directory, not a file")
        use = self.usage(desk_id)
        if use["files"] + 1 > self.max_files:
            raise WorkspaceError(f"this workspace already holds {use['files']} files and the limit is {self.max_files}. "
                                 "Trash a file you no longer need.", usage=use)
        room = self.max_total_bytes - use["bytes"]
        if room <= 0:
            raise WorkspaceError(f"this workspace already holds {use['bytes']} bytes and the limit is {self.max_total_bytes}.",
                                 usage=use)
        return _unique(p), room

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
                dirnames[:] = [d for d in dirnames if d.casefold() not in RESERVED_DIRS]
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
        if parts[0].casefold() in RESERVED_DIRS:  # APFS is case-insensitive: .BASELINE is the same folder
            raise WorkspaceError(f"{parts[0]}/ is reserved for the workspace itself and is not writable")
        if err := _read_only(rel):
            raise err
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
        if head.casefold() == TRASH_DIR:
            raise WorkspaceError(f"{rel} is already in the trash")
        if head.casefold() in RESERVED_DIRS:
            # write() refuses these for the same reason: moving .baseline/ away would destroy the
            # only before-copy the review diff is computed against.
            raise WorkspaceError(f"{head}/ is reserved for the workspace itself and cannot be trashed")
        if err := _read_only(rel):
            raise err
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

    # ---- inputs ----
    def add_inputs(self, desk_id: str, items: list[tuple[str, bytes, str]]) -> list[dict[str, Any]]:
        """Copy (name, bytes, source) snapshots into `inputs/`, each with a baseline copy and a MANIFEST.md line.

        The only writer of `inputs/`. The quota is checked for the whole batch first, so a refused batch lands
        nothing; an existing name gets Finder's `name 2.ext` rather than being replaced."""
        root = self.ensure(desk_id).resolve()
        need_files = 2 * len(items) + 1
        need_bytes = 2 * sum(len(data) for _, data, _ in items) + 300 * len(items)
        use = self.usage(desk_id)
        if use["files"] + need_files > self.max_files:
            raise WorkspaceError(f"this workspace already holds {use['files']} files and the limit is {self.max_files}", usage=use)
        if use["bytes"] + need_bytes > self.max_total_bytes:
            raise WorkspaceError(f"those inputs need {need_bytes} bytes (a copy and a baseline each); this workspace holds "
                                 f"{use['bytes']} and the limit is {self.max_total_bytes}", usage=use)
        out: list[dict[str, Any]] = []
        manifest = root / INPUTS_DIR / INPUTS_MANIFEST
        for name, data, source in items:
            name = re.sub(r"[\\/\x00:]+", "-", str(name or "")).strip().lstrip(".~ ") or "input"
            if name.casefold() == INPUTS_MANIFEST.casefold():
                name = f"input-{name}"
            suffix = _blocked((name,))
            if suffix:
                raise WorkspaceError(f"{suffix} files cannot be copied into a workspace")
            dest = _unique(self.resolve_in(desk_id, f"{INPUTS_DIR}/{name}"))
            rel = self._rel_of(desk_id, dest)
            base = self._baseline_of(desk_id, rel)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                base.parent.mkdir(parents=True, exist_ok=True)
                base.write_bytes(data)
                new = not manifest.exists()
                with manifest.open("a", encoding="utf-8") as fh:
                    if new:
                        fh.write("# Inputs\n\nSnapshot copies the user handed this desk. Read them; do not edit them.\n\n")
                    fh.write(f"- `{rel}`: {' '.join(str(source).split())[:200]} ({len(data)} bytes, "
                             f"sha256 {hashlib.sha256(data).hexdigest()[:12]})\n")
            except OSError as e:
                raise _oserror(rel, "copied in", e) from e
            out.append({"path": rel, "bytes": len(data), "source": source})
        return out

    def inputs(self, desk_id: str) -> list[dict[str, Any]]:
        """The inputs as the tree sees them, with `state`: `modified` means one changed after it was handed in."""
        skip = f"{INPUTS_DIR}/{INPUTS_MANIFEST}"
        return [e for e in self.tree(desk_id, INPUTS_DIR) if not e["is_dir"] and e["path"] != skip]

    def purge(self, desk_id: str) -> None:
        """Only ever called behind an explicit `?purge=true`: a desk's files are unregenerable."""
        shutil.rmtree(self.desk_root(desk_id), ignore_errors=True)
