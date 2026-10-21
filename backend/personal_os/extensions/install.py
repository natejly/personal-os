"""Installing an extension from outside the app: fetch it into quarantine, read it without running anything, show the user what it
asks for, and only on their confirmation move it into `<data>/extensions/<id>/` (disabled, with its content pinned).

Nothing from the fetched tree is ever imported or executed here: it is copied, hashed, parsed as JSON and read as text or as a
Python syntax tree (`grain ext check`). A git source is cloned with hooks, prompts, submodules and every protocol but the
needed one switched off, and the `.git` folder is dropped. A zip is extracted entry by entry with its names, link modes and
sizes checked. A folder is copied, refusing symbolic links.

Layout under the data directory:
    extensions-quarantine/<id>/            the fetched tree, untouched
    extensions-quarantine/.meta/<id>.json  where it came from, its hash and the findings the user was shown
    extensions/<id>/                       the installed copy (what proposals land in too; `extension.json` says enabled: false)
    extensions/.pins/<id>.json             the pin `Drafts.intact` checks at load, plus the source of the install
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import httpx

from ..egress import addr_ok
from .authoring.check import check, declared
from .authoring.pack import content_hash, digest
from .catalog_index import BUILTIN_ROOT, bundled_ids, card_for_manifest, read_pin, typosquat
from .drafts import Drafts, DraftError, _disable
from .manifest import ID, Manifest, ManifestError, load_manifest

QUARANTINE = "extensions-quarantine"
MAX_FILES = 1000
MAX_TREE_BYTES = 30_000_000
MAX_ZIP_BYTES = 20_000_000
MAX_UNZIPPED_BYTES = 50_000_000
GIT_TIMEOUT_S = 120
DOWNLOAD_TIMEOUT_S = 30.0
MAX_COMMANDS = 60
SCP_LIKE = re.compile(r"[\w.-]+@[\w.-]+:[\w./~-]+")
SHELL_LANGS = {"", "sh", "bash", "shell", "zsh", "console", "terminal", "text", "shell-session", "shellsession"}
SHELL_VERBS = ("pip", "pip3", "npm", "npx", "yarn", "pnpm", "brew", "apt", "apt-get", "curl", "wget", "git", "sudo", "python", "python3",
               "uv", "uvx", "cargo", "go", "make", "bash", "sh", "docker", "chmod", "cd", "export", "pipx", "gem", "bun")
DOC_NAMES = re.compile(r"(readme|setup|install)[^/]*", re.IGNORECASE)


class InstallError(Exception):
    def __init__(self, message: str, status: int = 400, findings: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.status, self.findings = status, findings or []


# ---- the tree ----
def tree_sha256(root: Path) -> str:
    """sha256 over the sorted tree: each file's relative path and the hash of its bytes."""
    h = hashlib.sha256()
    for rel, file_hash in sorted(_walk_hashes(root).items()):
        h.update(f"{rel}\0{file_hash}\n".encode())
    return h.hexdigest()


def _walk_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


def scan_tree(root: Path) -> None:
    """Refuse a tree with symbolic links, special files, too many files or too many bytes."""
    files = size = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            p = Path(base) / name
            if p.is_symlink():
                raise InstallError(f"{p.relative_to(root).as_posix()} is a symbolic link: an extension may not contain links")
        for name in names:
            p = Path(base) / name
            if not stat.S_ISREG(p.lstat().st_mode):
                raise InstallError(f"{p.relative_to(root).as_posix()} is not a regular file")
            files += 1
            size += p.stat().st_size
        if files > MAX_FILES or size > MAX_TREE_BYTES:
            raise InstallError(f"the extension is larger than {MAX_FILES} files or {MAX_TREE_BYTES // 1_000_000} MB")


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a folder, leaving out `.git` and refusing any symbolic link."""
    files = size = 0
    dst.mkdir(parents=True, exist_ok=True)
    for base, dirs, names in os.walk(src, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel = Path(base).relative_to(src)
        for name in [*dirs, *names]:
            if (Path(base) / name).is_symlink():
                raise InstallError(f"{(rel / name).as_posix()} is a symbolic link: an extension may not contain links")
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in names:
            p = Path(base) / name
            if not stat.S_ISREG(p.lstat().st_mode):
                raise InstallError(f"{(rel / name).as_posix()} is not a regular file")
            files += 1
            size += p.stat().st_size
            if files > MAX_FILES or size > MAX_TREE_BYTES:
                raise InstallError(f"the extension is larger than {MAX_FILES} files or {MAX_TREE_BYTES // 1_000_000} MB")
            shutil.copyfile(p, dst / rel / name)


# ---- sources ----
def _git_env(local: bool) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"), "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_ASKPASS": "true", "SSH_ASKPASS": "true", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes",
            "GIT_ALLOW_PROTOCOL": "file" if local else "https:ssh"}


def _git(args: list[str], *, local: bool, cwd: Path | None = None) -> str:
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false",
           "-c", "protocol.allow=never", "-c", f"protocol.{'file' if local else 'https'}.allow=always", *([] if local else ["-c", "protocol.ssh.allow=always"]), *args]
    try:
        r = subprocess.run(cmd, cwd=cwd, env=_git_env(local), capture_output=True, text=True, timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        raise InstallError("git is not installed, so a git source cannot be fetched") from None
    except subprocess.TimeoutExpired:
        raise InstallError(f"git took longer than {GIT_TIMEOUT_S} seconds") from None
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["unknown error"])[-1][:300]
        raise InstallError(f"git failed: {tail}")
    return r.stdout.strip()


def _fetch_git(location: str, dest: Path, ref: str | None, local_path: Path | None) -> str:
    """Shallow clone `location` (a repository path or an https or ssh URL) into `dest` and return the commit."""
    local = local_path is not None
    url = f"file://{local_path.resolve().as_posix()}" if local_path else location
    clone = ["clone", "--depth", "1", "--no-tags", "--single-branch", *(["--branch", ref] if ref else []), "--", url, str(dest)]
    try:
        _git(clone, local=local)
    except InstallError:
        if not ref:
            raise
        shutil.rmtree(dest, ignore_errors=True)  # a commit id is not a branch: fetch it by name
        dest.mkdir(parents=True)
        _git(["init", "-q", str(dest)], local=local)
        _git(["remote", "add", "origin", url], local=local, cwd=dest)
        _git(["fetch", "-q", "--depth", "1", "origin", ref], local=local, cwd=dest)
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], local=local, cwd=dest)
    commit = _git(["rev-parse", "HEAD"], local=local, cwd=dest)
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return commit


def _public_https(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        raise InstallError("that address is not a public host")
    try:
        import ipaddress
        ipaddress.ip_address(host)
    except ValueError:
        return
    if not addr_ok(host):
        raise InstallError("that address is not a public host")


def _download(url: str, dest: Path) -> None:
    _public_https(url)
    size = 0
    try:
        with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as r:
            r.raise_for_status()
            if r.url.scheme != "https":
                raise InstallError("the download was redirected away from https")
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise InstallError(f"the download is larger than {MAX_ZIP_BYTES // 1_000_000} MB")
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise InstallError(f"could not download it: {type(e).__name__}: {e}") from e


def _extract_zip(archive: Path, dest: Path) -> None:
    """Extract entry by entry. A name that leaves `dest`, a link, a special file or more bytes than allowed refuses the whole archive."""
    if archive.stat().st_size > MAX_ZIP_BYTES:
        raise InstallError(f"the zip is larger than {MAX_ZIP_BYTES // 1_000_000} MB")
    base = dest.resolve()
    total = count = 0
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise InstallError("that is not a valid zip file") from None
    with zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("__MACOSX/"):
                continue
            parts = PurePosixPath(name).parts
            if (not name or name.startswith(("/", "\\")) or "\\" in name or "\0" in name or ".." in parts or (parts and re.match(r"^[A-Za-z]:", parts[0]))):
                raise InstallError(f"the zip holds an unsafe path: {name!r}")
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise InstallError(f"the zip holds a link or special file: {name!r}")
            target = (base / name).resolve()
            if target != base and base not in target.parents:
                raise InstallError(f"the zip holds an unsafe path: {name!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            count += 1
            if count > MAX_FILES:
                raise InstallError(f"the zip holds more than {MAX_FILES} files")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(65536):
                    total += len(chunk)
                    if total > MAX_UNZIPPED_BYTES:
                        raise InstallError(f"the zip expands to more than {MAX_UNZIPPED_BYTES // 1_000_000} MB")
                    out.write(chunk)


def _resolve(src: str) -> tuple[str, Path | None, str]:
    """(kind, local path, location) for a source string: kind is git, zip, zipurl or folder."""
    if not src or src != src.strip() or src.startswith("-") or "\0" in src:
        raise InstallError("that is not a valid source")
    parts = urllib.parse.urlsplit(src)
    if parts.scheme == "file":
        path: Path | None = Path(urllib.parse.unquote(parts.path))
    elif parts.scheme == "" and not SCP_LIKE.fullmatch(src):
        path = Path(src).expanduser()
    else:
        path = None
    if path is not None:
        if not path.exists():
            raise InstallError(f"{src} does not exist")
        if path.is_dir():
            return ("git" if (path / ".git").exists() else "folder"), path, src
        if path.is_file() and zipfile.is_zipfile(path):
            return "zip", path, src
        raise InstallError(f"{src} is neither a folder nor a zip file")
    if parts.scheme == "https":
        return ("zipurl" if parts.path.lower().endswith(".zip") else "git"), None, src
    if parts.scheme == "ssh" or SCP_LIKE.fullmatch(src):
        return "git", None, src
    raise InstallError("a source is an https or ssh git URL, an https zip URL, or a path on this computer")


# ---- text facts ----
def find_commands(root: Path) -> list[str]:
    """Shell commands from the README, setup and install text at the top of the tree, verbatim: lines of a shell code fence,
    lines that start with `$ `, and lines that start with a common shell command."""
    out: list[str] = []
    for doc in sorted(p for p in root.iterdir() if p.is_file() and not p.is_symlink() and DOC_NAMES.fullmatch(p.name)) if root.is_dir() else []:
        try:
            text = doc.read_text("utf-8")[:200_000]
        except (OSError, UnicodeDecodeError):
            continue
        fence: str | None = None
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = None if fence is not None else stripped.lstrip("`~").strip().split()[0].lower() if stripped.lstrip("`~").strip() else ""
                continue
            if not stripped:
                continue
            if fence is not None:
                if fence in SHELL_LANGS:
                    out.append(stripped[:300])
            elif stripped.startswith("$ ") or stripped.split(" ", 1)[0] in SHELL_VERBS and " " in stripped and not stripped.endswith((".", ":")):
                out.append(stripped[:300])
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))][:MAX_COMMANDS]


# ---- validation ----
def read_manifest(root: Path) -> Manifest:
    path = root / "extension.json"
    if not path.is_file():
        raise InstallError("it has no extension.json at its top level (use the subdir field when the extension is inside a larger repository)")
    try:
        return load_manifest(path)
    except ManifestError as e:
        raise InstallError(f"extension.json is not valid: {e}") from e


def findings_for(root: Path) -> list[dict[str, str]]:
    level = {"error": "error", "warn": "warn", "note": "info"}
    return [{"level": level.get(f.level, "info"), "message": f"[{f.code}] {f.message}"} for f in check(root)]


def tree_state(drafts: Drafts, ext_id: str) -> tuple[str, str | None]:
    """('ready', None), or ('needs-review', why) when the installed files are not the ones that were pinned. Such an extension does not load."""
    try:
        if drafts.intact(ext_id):
            return "ready", None
    except (OSError, ValueError):
        pass
    return "needs-review", "its files are not the ones you approved: install or update it again to review them"


def changes_between(old: Manifest, new: Manifest) -> dict[str, list[str]]:
    a, b = declared(old), declared(new)
    return {"permissionsAdded": sorted(set(b["permissions"]) - set(a["permissions"])), "hooksAdded": sorted(set(b["hooks"]) - set(a["hooks"])),
            "toolsAdded": sorted(set(b["tools"]) - set(a["tools"]))}


# ---- the installer ----
class Installer:
    def __init__(self, data_dir: Path, drafts: Drafts, known_ids: Callable[[], set[str]] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.drafts = drafts
        self.quarantine = self.data_dir / QUARANTINE
        self._meta = self.quarantine / ".meta"
        self._known_ids = known_ids or (lambda: set())

    # -- staging --
    def stage(self, *, url: str | None = None, path: str | None = None, ref: str | None = None, subdir: str | None = None,
              update_of: str | None = None) -> dict[str, Any]:
        """Fetch into quarantine and return the consent facts. Raises InstallError (HTTP 4xx) on a refusal."""
        if bool(url) == bool(path):
            raise InstallError("give either a url or a path")
        src = (url or path or "").strip()
        if ref is not None and (not isinstance(ref, str) or ref.startswith("-") or "\0" in ref):
            raise InstallError("that ref is not valid")
        if subdir:
            p = Path(subdir)
            if p.is_absolute() or ".." in p.parts or "\\" in subdir or "\0" in subdir:
                raise InstallError("the subdir must be a relative folder inside the source")
        kind, local, location = _resolve(src)
        self.quarantine.mkdir(parents=True, exist_ok=True)
        work = self.quarantine / f".tmp-{secrets.token_hex(6)}"
        try:
            work.mkdir()
            tree, commit = work / "tree", None
            if kind == "git":
                commit = _fetch_git(location, tree, ref, local)
            elif kind == "folder":
                if ref:
                    raise InstallError("a ref only applies to a git source")
                _copy_tree(local, tree)  # type: ignore[arg-type]
            else:
                archive = local
                if kind == "zipurl":
                    archive = work / "download.zip"
                    _download(location, archive)
                tree.mkdir()
                _extract_zip(archive, tree)  # type: ignore[arg-type]
                if not (tree / "extension.json").is_file():
                    inner = [p for p in tree.iterdir() if p.name != "__MACOSX"]
                    if len(inner) == 1 and inner[0].is_dir() and not subdir:
                        tree = inner[0]
            root = tree / subdir if subdir else tree
            if subdir and not (root.resolve().is_relative_to(tree.resolve()) and root.is_dir()):
                raise InstallError("the subdir is not a folder inside the source")
            scan_tree(root)
            manifest = read_manifest(root)
            self._refuse_id(manifest.id, update_of)
            findings = findings_for(root)
            sha = tree_sha256(root)
            old = None
            if update_of:
                try:
                    old = load_manifest(self.drafts.installed / update_of / "extension.json")
                except ManifestError:
                    old = None
            known = (bundled_ids() | self._known_ids()) - {manifest.id}
            squat = typosquat(manifest.id, known)
            dest = self.quarantine / manifest.id
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(root), str(dest))
            source = {"kind": "git" if kind == "git" else "folder" if kind == "folder" else "zip", "url": src,
                      **({"ref": ref} if ref else {}), **({"subdir": subdir} if subdir else {}), **({"commit": commit} if commit else {})}
            changes = changes_between(old, manifest) if old else None
            meta = {"id": manifest.id, "source": source, "sha256": sha, "findings": findings, "typosquat": squat,
                    "update": bool(update_of), "commands": find_commands(dest)}
            self._meta.mkdir(parents=True, exist_ok=True)
            (self._meta / f"{manifest.id}.json").write_text(json.dumps(meta), "utf-8")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return self._consent(manifest, dest, meta, changes)

    def _refuse_id(self, ext_id: str, update_of: str | None) -> None:
        if not ID.fullmatch(ext_id):
            raise InstallError("the extension id is not valid")
        if ext_id in bundled_ids():
            raise InstallError(f"{ext_id!r} is the id of an extension that ships with Grain: an installed one cannot take its place", 409)
        if update_of and ext_id != update_of:
            raise InstallError(f"the source now declares the id {ext_id!r}, not {update_of!r}", 409)
        if not update_of and (self.drafts.installed / ext_id).exists():
            raise InstallError(f"{ext_id!r} is already installed: update it instead", 409)

    def _consent(self, manifest: Manifest, root: Path, meta: dict[str, Any], changes: dict[str, list[str]] | None) -> dict[str, Any]:
        src = meta["source"]
        card = card_for_manifest(manifest, root=root, tier="installed", source=src["kind"], enabled=False, url=src["url"], ref=src.get("ref"),
                                 subdir=src.get("subdir"), updateAvailable=True if meta["update"] else None)
        consent: dict[str, Any] = {"id": manifest.id, "card": card, "commands": meta["commands"], "typosquat": meta["typosquat"],
                                   "findings": meta["findings"], "sha256": meta["sha256"], "source": src, "quarantined": True}
        if changes is not None:
            consent["changes"] = changes
        return consent

    def staged(self, ext_id: str) -> tuple[Path, dict[str, Any]]:
        meta_path = self._meta / f"{ext_id}.json"
        root = self.quarantine / ext_id
        if not ID.fullmatch(ext_id) or not root.is_dir() or not meta_path.is_file():
            raise KeyError(ext_id)
        return root, json.loads(meta_path.read_text("utf-8"))

    def discard(self, ext_id: str) -> None:
        self.staged(ext_id)
        shutil.rmtree(self.quarantine / ext_id, ignore_errors=True)
        (self._meta / f"{ext_id}.json").unlink(missing_ok=True)

    # -- update --
    def update(self, ext_id: str) -> dict[str, Any]:
        """Fetch the recorded source again. A tree identical to the installed one is not left in quarantine."""
        pin = read_pin(self.drafts.installed, ext_id)
        if not ID.fullmatch(ext_id) or not (self.drafts.installed / ext_id).is_dir():
            raise KeyError(ext_id)
        src = (pin or {}).get("source")
        if not isinstance(src, dict) or not isinstance(src.get("url"), str):
            raise InstallError("it was not installed from a source that can be fetched again", 409)
        consent = self.stage(url=src["url"], ref=src.get("ref"), subdir=src.get("subdir"), update_of=ext_id)
        if consent["sha256"] == (pin or {}).get("sourceSha256"):
            self.discard(ext_id)
            consent["card"]["updateAvailable"] = False
            consent["quarantined"] = False
            consent["findings"] = [{"level": "info", "message": "Already up to date"}]
            consent.pop("changes", None)
        return consent

    # -- confirm --
    def commit(self, ext_id: str) -> dict[str, Any]:
        """Replace the installed copy with the quarantined tree, disabled, and pin it. The caller has stood the old version down.
        Returns {manifest, update, grew}: whether permissions or hooks grew, which is when the extension must stay disabled."""
        root, meta = self.staged(ext_id)
        if errors := [f for f in meta["findings"] if f["level"] == "error"]:
            raise InstallError("it has problems that must be fixed first", 409, errors)
        if tree_sha256(root) != meta["sha256"]:
            raise InstallError("the files changed after you reviewed them; fetch them again", 409)
        scan_tree(root)
        manifest = read_manifest(root)
        shutil.rmtree(self.quarantine / ".tmp-confirm", ignore_errors=True)
        installed = self.drafts.installed / ext_id
        old: Manifest | None = None
        if installed.is_dir():
            try:
                old = load_manifest(installed / "extension.json")
            except ManifestError:
                old = None
        grew = bool(old is None or any(changes_between(old, manifest)[k] for k in ("permissionsAdded", "hooksAdded")))
        staging = self.drafts.installed / f".staging-{ext_id}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            shutil.copytree(root, staging, symlinks=True)
            try:
                _disable(staging / "extension.json")
            except DraftError as e:
                raise InstallError(str(e)) from e
            shutil.rmtree(installed, ignore_errors=True)
            staging.replace(installed)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        pins = self.drafts.installed / ".pins"
        pins.mkdir(parents=True, exist_ok=True)
        (pins / f"{ext_id}.json").write_text(json.dumps({
            "hash": digest(content_hash(installed)), "tainted": False, "taintSources": [], "sourceSha256": meta["sha256"],
            "source": meta["source"], **({"commit": meta["source"]["commit"]} if meta["source"].get("commit") else {}),
            "version": manifest.version, "declared": declared(manifest),
            "installedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}), "utf-8")
        self.discard(ext_id)
        return {"manifest": manifest, "update": old is not None, "grew": grew}

    # -- remove --
    def remove_files(self, ext_id: str) -> None:
        if not ID.fullmatch(ext_id) or ext_id in bundled_ids() or not (self.drafts.installed / ext_id).is_dir():
            raise KeyError(ext_id)
        shutil.rmtree(self.drafts.installed / ext_id, ignore_errors=True)
        (self.drafts.installed / ".pins" / f"{ext_id}.json").unlink(missing_ok=True)


__all__ = ["BUILTIN_ROOT", "InstallError", "Installer", "find_commands", "scan_tree", "tree_sha256", "tree_state"]
