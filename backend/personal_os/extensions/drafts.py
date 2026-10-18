"""Extensions the assistant drafts (design doc sections 6.3 and 7.2).

The assistant stages a draft in `<data>/ext-drafts/<id>/` with the file tools (the one folder inside the data folder they
may write; `mac.EXT_DRAFTS`). `propose` snapshots it, so what the user reviews is what gets installed, and checks it. Accepting
the snapshot installs it into `<data>/extensions/<id>/` with `enabled: false` and pins its content hash. Enabling is a separate
step (`approve` says whether it may happen); no tool does it. The proposal and the pin live under `<data>/extensions/`, which
the file tools cannot write.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .. import mac
from .authoring.check import check
from .authoring.pack import content_hash, digest, shipped
from .catalog import HOOKS
from .manifest import Manifest, ManifestError, load_manifest

log = logging.getLogger(__name__)

INSTALLED = "extensions"
ID = re.compile(r"[a-z0-9-]+")
MAX_FILES = 200
MAX_BYTES = 2_000_000
TAINT_REFUSED = frozenset({"prompt:inject", "hooks:gate"})  # a session that read untrusted content may not write these in
GATE_PERMISSIONS = frozenset(p for spec in HOOKS.values() for p in spec.permission)  # the permissions that can narrow or change what the user did: granted by name at enable


class DraftError(ValueError):
    def __init__(self, message: str, findings: list[str] | None = None) -> None:
        super().__init__(message)
        self.findings = findings or []


def _card(manifest: Manifest, tainted: bool, sources: list[str]) -> dict[str, Any]:
    """What the user sees before approving: contributions, hooks, permissions, settings and the drafting session's provenance."""
    perms = set(manifest.permissions)
    return {"id": manifest.id, "name": manifest.name, "description": manifest.description, "version": manifest.version,
            "contributes": manifest.contributes, "hooks": sorted(manifest.hooks), "permissions": sorted(perms),
            "settings": sorted(manifest.settings), "tainted": tainted, "taintSources": sources,
            "refused": sorted(TAINT_REFUSED & perms) if tainted else [], "needsGrant": sorted(GATE_PERMISSIONS & perms)}


def _copy(src: Path, dst: Path) -> None:
    """The files of a draft that ship, into `dst`. A link could point the snapshot at a file outside the draft."""
    if any(p.is_symlink() for p in src.rglob("*")):
        raise DraftError("a draft may not contain symbolic links")
    files = shipped(src)
    for p in files:
        try:
            p.read_text("utf-8")
        except UnicodeDecodeError:
            raise DraftError(f"{p.relative_to(src).as_posix()} is not UTF-8 text: a draft is reviewed as text, so it holds nothing else") from None
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise DraftError(f"a draft may hold at most {MAX_FILES} files and {MAX_BYTES // 1_000_000} MB")
    for p in files:
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)


def _disable(manifest_path: Path) -> None:
    """An installed extension ships `enabled: false`."""
    if not manifest_path.is_file():
        raise DraftError("the draft folder has no extension.json: every extension needs one at its top level")
    try:
        raw = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise DraftError(f"extension.json cannot be read: {e}") from e
    if not isinstance(raw, dict):
        raise DraftError("extension.json must be a JSON object")
    manifest_path.write_text(json.dumps({**raw, "enabled": False}, indent=2) + "\n", "utf-8")


def _diff(old: Path | None, new: Path | None, rel: str) -> str:
    """The whole unified diff: a draft is capped in size and all text (`_copy`), so nothing the user approves is left unshown."""
    before = old.read_text("utf-8").splitlines(True) if old else []
    after = new.read_text("utf-8").splitlines(True) if new else []
    return "".join(difflib.unified_diff(before, after, f"a/{rel}", f"b/{rel}"))


def _changes(old: Path, new: Path) -> list[dict[str, str]]:
    """The files that differ between the installed version (if any) and the proposed one, each with its diff."""
    before = content_hash(old) if old.is_dir() else {}
    after = content_hash(new)
    return [{"path": rel, "status": "added" if rel not in before else "removed" if rel not in after else "changed",
             "diff": _diff(old / rel if rel in before else None, new / rel if rel in after else None, rel)}
            for rel in sorted(before.keys() | after.keys()) if before.get(rel) != after.get(rel)]


def _fingerprint(files: Path, meta: bytes) -> str:
    """Identifies a proposal: its files and its provenance. Accepting names the one the user was shown."""
    return digest({**content_hash(files), "\0proposal": hashlib.sha256(meta).hexdigest()})


class Drafts:
    def __init__(self, data_dir: Path) -> None:
        self.drafts = Path(data_dir) / mac.EXT_DRAFTS
        self.installed = Path(data_dir) / INSTALLED
        self._review = self.installed / ".review"  # <id>/files (the snapshot) and <id>/proposal.json
        self._pins = self.installed / ".pins"  # <id>.json

    # ---- proposals ----
    def propose(self, ext_id: str, *, tainted: bool, taint_sources: list[str]) -> dict[str, Any]:
        """Snapshot and check the draft `<ext-drafts>/<id>/` and put it up for review. Raises DraftError with the findings when it fails."""
        if not ID.fullmatch(ext_id):
            raise DraftError("an extension id is lowercase letters, digits and hyphens")
        src = self.drafts / ext_id
        if not src.is_dir():
            raise DraftError(f"there is no draft for {ext_id!r}: write its files under {src}, then call ext_propose again")
        snap = self._review / ext_id
        staging = self.installed / ".review-new" / ext_id  # a draft that fails leaves the proposal under review as it was
        shutil.rmtree(staging, ignore_errors=True)
        try:
            _copy(src, staging / "files")
            _disable(staging / "files" / "extension.json")
            warnings = self._check(ext_id, staging / "files")
            (staging / "proposal.json").write_text(json.dumps({"tainted": tainted, "taintSources": taint_sources, "findings": warnings}), "utf-8")
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(snap, ignore_errors=True)
        snap.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(snap)
        return self._view(ext_id)

    def _check(self, ext_id: str, files: Path) -> list[str]:
        """The non-error findings of `grain ext check` on the snapshot; DraftError when there is an error or the id is not the folder's."""
        installed = self.installed / ext_id / "extension.json"
        found = check(files, installed if installed.is_file() else None)
        if errors := [str(f) for f in found if f.level == "error"]:
            raise DraftError("grain ext check found errors", errors)
        if load_manifest(files / "extension.json").id != ext_id:
            raise DraftError(f"extension.json must have the id {ext_id!r}, the draft folder's name")
        return [str(f) for f in found]

    def proposal(self, ext_id: str) -> dict[str, Any] | None:
        return self._view(ext_id) if (self._review / ext_id / "proposal.json").is_file() else None

    def _view(self, ext_id: str) -> dict[str, Any]:
        raw = (self._review / ext_id / "proposal.json").read_bytes()
        meta = json.loads(raw)
        files = self._review / ext_id / "files"
        return {**_card(load_manifest(files / "extension.json"), meta["tainted"], meta["taintSources"]),
                "findings": meta["findings"], "digest": _fingerprint(files, raw), "update": (self.installed / ext_id).is_dir(),
                "changes": _changes(self.installed / ext_id, files)}

    def proposals(self) -> list[dict[str, Any]]:
        ids = sorted(p.name for p in self._review.iterdir()) if self._review.is_dir() else []
        return [p for ext_id in ids if (p := self.proposal(ext_id))]

    def reject(self, ext_id: str) -> None:
        if self.proposal(ext_id) is None:
            raise KeyError(ext_id)
        shutil.rmtree(self._review / ext_id)

    def accept(self, ext_id: str, reviewed: str) -> None:
        """Install the proposed snapshot, replacing any earlier version, and pin its hash. The caller has stood the old version down.
        Raises DraftError unless the snapshot is the one with the `reviewed` digest: a new proposal may have replaced it since."""
        meta_path = self._review / ext_id / "proposal.json"
        if not meta_path.is_file():
            raise KeyError(ext_id)
        raw = meta_path.read_bytes()
        meta = json.loads(raw)
        staging = self.installed / f".staging-{ext_id}"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(self._review / ext_id / "files", staging)
        if _fingerprint(staging, raw) != reviewed:
            shutil.rmtree(staging, ignore_errors=True)
            raise DraftError("the proposal changed after you reviewed it; review it again")
        shutil.rmtree(self.installed / ext_id, ignore_errors=True)
        staging.replace(self.installed / ext_id)
        self._pins.mkdir(exist_ok=True)
        (self._pins / f"{ext_id}.json").write_text(json.dumps({"hash": digest(content_hash(self.installed / ext_id)), "tainted": meta["tainted"],
                                                              "taintSources": meta["taintSources"]}), "utf-8")
        shutil.rmtree(self._review / ext_id)

    # ---- installed ----
    def installed_ids(self) -> list[str]:
        return sorted(p.name for p in self.installed.iterdir() if p.is_dir() and not p.name.startswith(".")) if self.installed.is_dir() else []

    def widget_kinds(self) -> tuple[str, ...]:
        """The canvas widget kinds the installed extensions declare, loaded or not: a turned-off one keeps its windows."""
        kinds: list[str] = []
        for ext_id in self.installed_ids():
            try:
                manifest = load_manifest(self.installed / ext_id / "extension.json")
            except ManifestError:
                continue
            kinds += (i["id"] for i in manifest.contributes.get("slots", {}).get("canvas.widget", ()))
        return tuple(kinds)

    def intact(self, ext_id: str) -> bool:
        """The folder still has the content that was accepted."""
        pin = self._pins / f"{ext_id}.json"
        return pin.is_file() and json.loads(pin.read_text("utf-8"))["hash"] == digest(content_hash(self.installed / ext_id))

    def card(self, ext_id: str) -> dict[str, Any] | None:
        """The enable card of an installed extension, or None when its manifest cannot be read."""
        pin = self._pins / f"{ext_id}.json"
        meta = json.loads(pin.read_text("utf-8")) if pin.is_file() else {"tainted": True, "taintSources": []}
        try:
            manifest = load_manifest(self.installed / ext_id / "extension.json")
        except ManifestError as e:
            log.warning("installed extension %s: %s", ext_id, e)
            return None
        return {**_card(manifest, meta["tainted"], meta["taintSources"]), "changed": not self.intact(ext_id)}

    def approve(self, ext_id: str, grants: list[str]) -> None:
        """Raise DraftError unless this installed extension may be enabled now: its files are the accepted ones, the drafting
        session's taint allows its permissions, and each permission that changes a gate was granted by name."""
        card = self.card(ext_id)
        if card is None:
            raise KeyError(ext_id)
        if card["changed"]:
            raise DraftError("its files are not the ones you accepted; have the assistant propose it again to review the new version")
        if card["refused"]:
            raise DraftError(f"the session that drafted it read untrusted content ({', '.join(card['taintSources']) or 'unknown source'}), "
                             f"so it may not hold {', '.join(card['refused'])}")
        if missing := sorted(set(card["needsGrant"]) - set(grants)):
            raise DraftError(f"it can change what you approved or refused; enable it again granting {', '.join(missing)}")
