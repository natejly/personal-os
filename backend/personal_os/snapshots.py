"""Whole-folder snapshots, so a reply's effect on a granted folder can be taken back, shell side effects included.

Per-file pre-images (filesnap.py) cannot undo what a command did to a tree. Here one private object store
(`<data>/snapshots/store`, a bare repo the user never sees) holds a snapshot of each granted root, always
driven as `git --git-dir=<store> --work-tree=<root>` with its own index file per root, so the user's own
`.git` in a folder is never read as ours and never written. Only the folders the user granted (the setting
`workspaceRoots`) and desk workspaces are ever snapshotted, never all of home.

A run snapshots a root at most once, before its first mutating call (`before`), and once more when the run
ends (`finish`). Both tree hashes and a ledger of what changed (path, blob before, blob after) go on a
`run_snapshots` row. Undo reverts only the paths whose current content still equals what the run left; the
rest are reported as edited since, never clobbered. Redo is the mirror image. Undo and redo are routes the
user clicks; there is no tool for either.

Per snapshot: files over 2 MB, `node_modules`, `.venv` and `.git` are skipped, a root's `.gitignore` and
`.git/info/exclude` are honoured, and a root with more than 50,000 files is not snapshotted (and says so).
Each root keeps its newest 20 snapshots as refs under `refs/grain/<hash16>/`, the store is held to 500 MB by
evicting the oldest, and unreachable objects are collected after 7 days. Without a `git` binary the feature
reports itself unavailable and nothing else changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .db import Database

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 50_000
KEEP_PER_ROOT = 20
MAX_STORE_BYTES = 500 * 1024 * 1024
GC_PRUNE = "7.days"
SKIP_DIRS = frozenset({"node_modules", ".venv", ".git"})
SKIP_ITEMS_REPORTED = 20

# Tools that can change files in a granted root. shell_run is judged by its command (see read_only_shell).
FILE_TOOLS = frozenset({"write_local_file", "move_local_file", "trash_local_file", "fs_edit", "fs_copy", "fs_mkdir"})
# run_python and desk_fetch_file can write the workspace too (run_python only inside a desk: roots_for_call needs a desk id).
DESK_TOOLS = frozenset({"desk_write_file", "desk_trash_file", "desk_import_sandbox", "run_python", "desk_fetch_file", "sandbox_export_file"})
SHELL_TOOLS = frozenset({"shell_run", "opencode_run"})
PATH_KEYS = ("path", "to", "from", "src", "dst", "dest", "destination", "source")

READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "pwd", "echo", "grep", "egrep", "fgrep", "rg", "wc", "stat", "file", "which",
    "du", "df", "date", "whoami", "cut", "tr", "basename", "dirname", "realpath", "diff", "cmp",
    "md5", "shasum", "sha256sum", "true", "printf", "type", "uname", "id", "hostname", "more", "nl", "column",
})
READ_ONLY_GIT = frozenset({"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame", "remote", "describe", "shortlog"})
# find and sed can write, so they are read-only only without these flags; anything else with them is mutating.
FIND_WRITES = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"})
SHELL_META = re.compile(r"[;&<>`\n]|\$\(|\|\|")


class SnapshotError(Exception):
    pass


def available() -> bool:
    return shutil.which("git") is not None


def read_only_shell(command: str) -> bool:
    """True when every stage of a pipeline is a known read-only command. Anything with redirects, chaining,
    substitution or an unknown program is treated as mutating, because snapshotting needlessly is cheap and
    missing a write is not."""
    if not isinstance(command, str) or not command.strip() or SHELL_META.search(command):
        return False
    for stage in command.split("|"):
        try:
            argv = shlex.split(stage)
        except ValueError:
            return False
        if not argv:
            return False
        prog = os.path.basename(argv[0])
        if prog == "git":
            if any(a.startswith(("--output", "-o")) for a in argv[1:]):  # log/diff/show --output=FILE writes it
                return False
            rest = [a for a in argv[1:] if not a.startswith("-")]
            if not rest or rest[0] not in READ_ONLY_GIT or (rest[0] in ("branch", "remote") and len(rest) > 1):
                return False
        elif prog == "find":
            if any(a in FIND_WRITES for a in argv[1:]):
                return False
        elif prog not in READ_ONLY_COMMANDS:
            return False
    return True


def _h16(root: Path) -> str:
    return hashlib.sha1(str(root).encode()).hexdigest()[:16]


def _pattern_escape(rel: str) -> str:
    """A gitignore line matching exactly one path (anchored, special characters escaped)."""
    out = re.sub(r"([\\*?\[\]#!\s])", r"\\\1", rel)
    return "/" + out


class Snapshots:
    def __init__(self, db: Database, base: Path, settings_fn: Callable[[], dict[str, Any]],
                 desk_root_fn: Callable[[str], Path] | None = None) -> None:
        self.db = db
        self.base = Path(base)
        self.store = self.base / "store"
        self.settings = settings_fn
        self.desk_root_fn = desk_root_fn
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ---- git plumbing ----
    def _env(self, h: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update({"GIT_INDEX_FILE": str(self.store / f"index-{h}"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                    "GIT_AUTHOR_NAME": "grain", "GIT_AUTHOR_EMAIL": "grain@localhost",
                    "GIT_COMMITTER_NAME": "grain", "GIT_COMMITTER_EMAIL": "grain@localhost", "GIT_TERMINAL_PROMPT": "0"})
        for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
            env.pop(k, None)
        return env

    def _git(self, root: Path | None, *args: str, h: str = "x", input: bytes | None = None, check: bool = True) -> bytes:
        cmd = ["git", "--literal-pathspecs", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
               "-c", "gc.auto=0", f"--git-dir={self.store}"]
        if root is not None:
            cmd.append(f"--work-tree={root}")
        cmd.extend(args)
        p = subprocess.run(cmd, input=input, capture_output=True, env=self._env(h), cwd=str(root or self.store), timeout=300)
        if check and p.returncode != 0:
            raise SnapshotError(f"git {args[0]} failed: {p.stderr.decode(errors='replace').strip()[:300]}")
        return p.stdout

    def _ensure_store(self) -> None:
        if (self.store / "HEAD").exists():
            return
        self.base.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-q", str(self.store)], check=True, capture_output=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})

    def _lock(self, h: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(h, threading.Lock())

    # ---- roots ----
    def enabled(self) -> bool:
        return bool(self.settings().get("snapshotsEnabled", True)) and available()

    def roots(self, desk_id: str | None = None, settings: dict[str, Any] | None = None) -> list[Path]:
        """Granted roots that exist, plus the desk's workspace. Nothing else is ever snapshotted. `settings` is the
        run's own when it has one (a chat bound to a working folder lists it there)."""
        out: list[Path] = []
        for r in (settings or self.settings()).get("workspaceRoots", []) or []:
            try:
                p = Path(str(r)).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if p.is_dir() and p != Path.home().resolve() and p != Path(p.anchor) and p not in out:
                out.append(p)
        if desk_id and self.desk_root_fn is not None:
            try:
                d = self.desk_root_fn(desk_id).resolve()
                if d.is_dir() and d not in out:
                    out.append(d)
            except Exception:  # noqa: BLE001 - a bad desk id just means no desk root
                pass
        return out

    def roots_for_call(self, name: str, args: dict[str, Any], desk_id: str | None,
                       settings: dict[str, Any] | None = None) -> list[Path]:
        """Which roots a call can touch. File tools: the roots containing their path arguments. Desk tools:
        the desk workspace. A shell command that is not read-only: its cwd's root, else every root."""
        if name in DESK_TOOLS:
            return self.roots(desk_id, settings)[-1:] if desk_id and self.desk_root_fn else []
        if name in SHELL_TOOLS:
            if read_only_shell(str(args.get("command") or args.get("cmd") or "")):
                return []
            allr = self.roots(desk_id, settings)
            cwd = args.get("cwd")
            if cwd:
                hit = self._containing(allr, str(cwd))
                if hit:
                    return hit
            return allr
        if name in FILE_TOOLS:
            allr = self.roots(desk_id, settings)
            hits: list[Path] = []
            for k in PATH_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v:
                    for r in self._containing(allr, v):
                        if r not in hits:
                            hits.append(r)
            return hits
        return []

    @staticmethod
    def _containing(roots: list[Path], path: str) -> list[Path]:
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return []
        return [r for r in roots if p == r or r in p.parents]

    def wants(self, name: str, args: dict[str, Any], desk_id: str | None, settings: dict[str, Any] | None = None) -> bool:
        return (name in FILE_TOOLS or name in DESK_TOOLS or name in SHELL_TOOLS) and self.enabled() \
            and bool(self.roots_for_call(name, args, desk_id, settings))

    # ---- tracking ----
    def _walk(self, root: Path) -> tuple[list[str], list[str] | None]:
        """(over-size relative paths, None) or ([], too-many-files marker). Prunes the skipped directories."""
        big: list[str] = []
        n = 0
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                n += 1
                if n > MAX_FILES:
                    return [], [f"more than {MAX_FILES} files"]
                fp = os.path.join(dirpath, f)
                try:
                    if not os.path.islink(fp) and os.path.getsize(fp) > MAX_FILE_BYTES:
                        big.append(os.path.relpath(fp, root))
                except OSError:
                    continue
        return big, None

    def _excludes(self, root: Path, h: str, big: list[str]) -> Path:
        lines = ["node_modules/", ".venv/", ".git/"]
        info = root / ".git" / "info" / "exclude"
        try:
            if info.is_file():
                lines.extend(info.read_text(errors="replace").splitlines())
        except OSError:
            pass
        lines.extend(_pattern_escape(b) for b in big)
        f = self.store / f"exclude-{h}"
        f.write_text("\n".join(lines) + "\n")
        return f

    def track(self, root: Path) -> dict[str, Any]:
        """Snapshot a root: add -A + write-tree. Returns {tree, skipped} (tree None when it was too big)."""
        root = Path(root).resolve()
        h = _h16(root)
        with self._lock(h):
            self._ensure_store()
            big, toomany = self._walk(root)
            if toomany:
                return {"tree": None, "skipped": toomany}
            ex = self._excludes(root, h, big)
            self._git(root, "-c", f"core.excludesFile={ex}", "add", "-A", "--ignore-errors", h=h, check=False)
            tree = self._git(root, "write-tree", h=h).decode().strip()
            self._retain(root, h, tree)
            return {"tree": tree, "skipped": [f"{b} (over 2 MB)" for b in big[:SKIP_ITEMS_REPORTED]]}

    def _refs(self, h: str) -> list[str]:
        out = self._git(None, "for-each-ref", "--sort=refname", "--format=%(refname) %(tree)", f"refs/grain/{h}/", check=False).decode()
        return [ln for ln in out.splitlines() if ln]

    def _retain(self, root: Path, h: str, tree: str) -> None:
        """Keep the tree reachable through a commit under refs/grain/<h>/, newest KEEP_PER_ROOT only."""
        refs = self._refs(h)
        if refs and refs[-1].split()[1] == tree:
            return
        commit = self._git(None, "commit-tree", tree, "-m", f"snapshot {root}", h=h).decode().strip()
        self._git(None, "update-ref", f"refs/grain/{h}/{time.time_ns():020d}", commit, h=h)
        for ln in self._refs(h)[:-KEEP_PER_ROOT]:
            self._git(None, "update-ref", "-d", ln.split()[0], h=h, check=False)

    def changes(self, a: str, b: str) -> list[dict[str, Any]]:
        """Paths that differ between two trees: [{status A|M|D, path, before, after}] with blob ids."""
        out = self._git(None, "diff-tree", "-r", "-z", "--no-renames", "--raw", a, b).split(b"\0")
        res: list[dict[str, Any]] = []
        i = 0
        while i + 1 < len(out) and out[i].startswith(b":"):
            meta = out[i][1:].decode().split()
            path = out[i + 1].decode(errors="surrogateescape")
            zero = "0" * len(meta[2])
            res.append({"status": meta[4][0], "path": path,
                        "before": None if meta[2] == zero else meta[2], "after": None if meta[3] == zero else meta[3]})
            i += 2
        return res

    # ---- per-run bookkeeping ----
    def before(self, run_id: str, roots: list[Path]) -> None:
        """Snapshot each root not yet snapshotted by this run. Called before a mutating call; never raises."""
        for root in roots:
            try:
                with self.db.tx() as c:
                    if c.execute("SELECT 1 FROM run_snapshots WHERE run_id=? AND root=?", (run_id, str(root))).fetchone():
                        continue
                t = self.track(root)
                with self.db.tx() as c:
                    c.execute("INSERT OR IGNORE INTO run_snapshots(run_id, root, before_tree, skipped, created_at) VALUES (?,?,?,?,?)",
                              (run_id, str(root), t["tree"], _j(t["skipped"]), time.time()))
            except Exception as e:  # noqa: BLE001 - a snapshot must never break the call it guards
                log.warning("snapshot before %s failed: %s", root, e)

    def finish(self, run_id: str) -> None:
        """After the run: snapshot again each root it had snapshotted, and write the ledger. Never raises."""
        if not available():
            return
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM run_snapshots WHERE run_id=? AND after_tree IS NULL", (run_id,))]
        for r in rows:
            if not r["before_tree"]:
                continue
            try:
                t = self.track(Path(r["root"]))
                if not t["tree"]:
                    continue
                files = self.changes(r["before_tree"], t["tree"])
                with self.db.tx() as c:
                    c.execute("UPDATE run_snapshots SET after_tree=?, files=? WHERE run_id=? AND root=?",
                              (t["tree"], _j(files), run_id, r["root"]))
            except Exception as e:  # noqa: BLE001
                log.warning("snapshot after %s failed: %s", r["root"], e)

    def rows(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rs = [dict(r) for r in c.execute("SELECT * FROM run_snapshots WHERE run_id=? ORDER BY root", (run_id,))]
        for r in rs:
            r["files"] = _l(r.get("files"))
            r["skipped"] = _l(r.get("skipped"))
        return rs

    def summary(self, run_id: str) -> dict[str, Any]:
        """What the footer needs: how many files the run changed and whether undo/redo is on offer."""
        rs = [r for r in self.rows(run_id) if r["after_tree"]]
        files = [{"root": r["root"], **f} for r in rs for f in r["files"]]
        undone = bool(rs) and all(r["state"] == "undone" for r in rs if r["files"])
        return {"available": available(), "count": len(files), "state": "undone" if undone else "applied",
                "files": files, "skipped": [s for r in rs for s in r["skipped"]]}

    # ---- undo / redo ----
    def _current_blob(self, root: Path, rel: str, h: str) -> str | None:
        p = root / rel
        if os.path.islink(p):
            return self._git(None, "hash-object", "--stdin", h=h, input=os.readlink(p).encode()).decode().strip()
        if not p.is_file():
            return None
        return self._git(None, "hash-object", "--", str(p), h=h).decode().strip()

    def _apply(self, run_id: str, direction: str) -> dict[str, Any]:
        undo = direction == "undo"
        want_state, new_state = ("applied", "undone") if undo else ("undone", "applied")
        reverted: list[str] = []
        edited: list[str] = []
        rows = [r for r in self.rows(run_id) if r["after_tree"] and r["state"] == want_state]
        for r in rows:  # retention or gc may have dropped the trees: refuse up front instead of half-applying and calling it done
            try:
                self._git(None, "cat-file", "-e", f'{r["before_tree"] if undo else r["after_tree"]}^{{tree}}', h=_h16(Path(r["root"])))
            except SnapshotError:
                raise SnapshotError(f"this run's snapshot has expired, so it can no longer be {'undone' if undo else 'redone'}") from None
        done = False
        for r in rows:
            root = Path(r["root"])
            h = _h16(root)
            src_tree = r["before_tree"] if undo else r["after_tree"]
            with self._lock(h):
                for f in r["files"]:
                    expect, target = (f["after"], f["before"]) if undo else (f["before"], f["after"])
                    if self._current_blob(root, f["path"], h) != expect:
                        edited.append(f["path"])
                        continue
                    try:
                        if target is None:
                            self._remove(root, f["path"])
                        else:
                            self._git(root, "checkout", src_tree, "--", f["path"], h=h)
                        reverted.append(f["path"])
                    except (SnapshotError, OSError) as e:
                        log.warning("%s of %s failed: %s", direction, f["path"], e)
                        edited.append(f["path"])
            with self.db.tx() as c:
                c.execute("UPDATE run_snapshots SET state=? WHERE run_id=? AND root=?", (new_state, run_id, r["root"]))
            done = True
        if not done:
            raise SnapshotError(f"nothing to {direction}")
        return {"ok": True, "direction": direction, "reverted": reverted, "edited_since": edited}

    @staticmethod
    def _remove(root: Path, rel: str) -> None:
        p = root / rel
        if p.is_symlink() or p.is_file():
            p.unlink()
        parent = p.parent
        while parent != root and root in parent.parents:  # drop directories the run created and left empty
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def undo(self, run_id: str) -> dict[str, Any]:
        return self._apply(run_id, "undo")

    def redo(self, run_id: str) -> dict[str, Any]:
        return self._apply(run_id, "redo")

    # ---- housekeeping ----
    def _store_bytes(self) -> int:
        n = 0
        for dp, _d, fs in os.walk(self.store):
            for f in fs:
                try:
                    n += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return n

    def prune(self, max_bytes: int = MAX_STORE_BYTES) -> int:
        """Daily: collect objects nothing reaches any more, then evict the oldest snapshots until the store fits."""
        if not available() or not (self.store / "HEAD").exists():
            return 0
        evicted = 0
        stamp = self.base / "gc-stamp"
        if not stamp.exists() or time.time() - stamp.stat().st_mtime > 86400:
            self._git(None, "gc", f"--prune={GC_PRUNE}", "-q", check=False)
            stamp.touch()
        while self._store_bytes() > max_bytes:
            refs = sorted((ln.split()[0] for h in os.listdir(self.store / "refs" / "grain") for ln in self._refs(h)),
                          key=lambda r: r.rsplit("/", 1)[-1]) if (self.store / "refs" / "grain").is_dir() else []
            if not refs:
                break
            for r in refs[:max(1, len(refs) // 4)]:
                self._git(None, "update-ref", "-d", r, check=False)
                evicted += 1
            self._git(None, "gc", "--prune=now", "-q", check=False)
        return evicted


def _j(v: Any) -> str:
    import json
    return json.dumps(v)


def _l(s: Any) -> list[Any]:
    import json
    try:
        v = json.loads(s) if s else []
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def router(snaps: Snapshots, run_exists: Callable[[str], bool]) -> Any:
    """The user-only routes: what a run changed, and undo / redo of it."""
    from fastapi import APIRouter, HTTPException

    r = APIRouter(tags=["snapshots"])

    def _settle(run_id: str) -> None:
        """The after-snapshot is normally taken when the run closes, which is after its auto-learn tail and so can
        be minutes after the reply finished. Once the reply has said `done` it can change nothing more, so take
        the snapshot now rather than showing "no changes" for that long. Finishing is idempotent."""
        with snaps.db.tx() as c:
            pend = c.execute("SELECT 1 FROM run_snapshots WHERE run_id=? AND after_tree IS NULL", (run_id,)).fetchone()
            evs = c.execute("SELECT data FROM run_events WHERE run_id=? AND type='done'", (run_id,)).fetchall() if pend else []
        for ev in evs:  # a `done` that only closes a steered segment does not end the reply
            try:
                d = json.loads(ev["data"])
            except (TypeError, ValueError):
                continue
            if isinstance(d, dict) and not d.get("segment"):
                snaps.finish(run_id)
                break

    @r.get("/messages/{message_id}/changes")
    def message_changes(message_id: str) -> dict[str, Any]:
        """The same summary, found from the reply it belongs to (the footer only knows the message)."""
        with snaps.db.tx() as c:
            runs = [x["run_id"] for x in c.execute("SELECT run_id FROM agent_runs WHERE message_id=?", (message_id,))]
        for rid in runs:
            _settle(rid)
        with snaps.db.tx() as c:
            row = c.execute("SELECT s.run_id FROM run_snapshots s JOIN agent_runs a ON a.run_id=s.run_id "
                            "WHERE a.message_id=? AND s.after_tree IS NOT NULL ORDER BY a.started_at DESC LIMIT 1",
                            (message_id,)).fetchone()
        if not row:
            return {"available": available(), "count": 0, "state": "applied", "files": [], "skipped": []}
        return {"run_id": row["run_id"], **snaps.summary(row["run_id"])}

    def _check(run_id: str) -> None:
        if not run_exists(run_id):
            raise HTTPException(404, "No such run")
        if not available():
            raise HTTPException(503, "git is not installed, so snapshots are unavailable")

    @r.get("/runs/{run_id}/changes")
    def changes(run_id: str) -> dict[str, Any]:
        if not run_exists(run_id):
            raise HTTPException(404, "No such run")
        _settle(run_id)
        return snaps.summary(run_id)

    @r.post("/runs/{run_id}/undo")
    def undo(run_id: str) -> dict[str, Any]:
        _check(run_id)
        _settle(run_id)
        try:
            return snaps.undo(run_id)
        except SnapshotError as e:
            raise HTTPException(409, str(e)) from None

    @r.post("/runs/{run_id}/redo")
    def redo(run_id: str) -> dict[str, Any]:
        _check(run_id)
        try:
            return snaps.redo(run_id)
        except SnapshotError as e:
            raise HTTPException(409, str(e)) from None

    return r
