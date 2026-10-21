"""`grain ext pack`: build the renderer half and pin the extension's content (design doc section 6.2)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..manifest import load_manifest
from .check import CODE_EXCLUDED, declared

LOCK = "extension.lock.json"
BUNDLE = Path("dist") / "extension.js"
NOT_SHIPPED = CODE_EXCLUDED - {"dist"}  # the built bundle is what ships, so it is pinned
SHARED = ("react", "react/jsx-runtime", "@grain/ext-sdk")  # the app supplies these to a renderer half


def esbuild() -> str:
    """The esbuild to bundle with: $GRAIN_ESBUILD, one on PATH, or the app checkout's node_modules."""
    candidates = [os.environ.get("GRAIN_ESBUILD"), shutil.which("esbuild"), *(str(p / "node_modules/.bin/esbuild") for p in Path(__file__).resolve().parents)]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise FileNotFoundError("esbuild was not found: install it or set GRAIN_ESBUILD to its path")


def shipped(root: Path) -> list[Path]:
    """The files that ship (the tests, the lock and build leftovers do not)."""
    return [p for p in sorted(root.rglob("*")) if p.is_file() and p.name != LOCK and not NOT_SHIPPED & set(p.relative_to(root).parts)]


def content_hash(root: Path) -> dict[str, str]:
    """sha256 of every file that ships (the tests and build leftovers do not), by path."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in shipped(root)}


def digest(files: dict[str, str]) -> str:
    """The one hash of a `content_hash`: what the lock file and an installed extension's pin record."""
    return hashlib.sha256("".join(f"{path}\0{h}\n" for path, h in files.items()).encode()).hexdigest()


def pack(root: Path) -> Path:
    """Bundle extension.tsx into dist/extension.js (when there is one), then write the lock file and return its path."""
    manifest = load_manifest(root / "extension.json")
    lock = root / LOCK
    lock.unlink(missing_ok=True)  # a build that fails must not leave the last build's bundle and pin looking current
    (root / BUNDLE).unlink(missing_ok=True)
    if (root / "extension.tsx").is_file():
        subprocess.run([esbuild(), "extension.tsx", "--bundle", "--format=esm", "--platform=browser", "--jsx=automatic",
                        *(f"--external:{m}" for m in SHARED), f"--outfile={BUNDLE}", "--log-level=warning"],
                       cwd=root, check=True, capture_output=True, text=True)
    files = content_hash(root)
    lock.write_text(json.dumps({"id": manifest.id, "version": manifest.version, "hash": digest(files), "declared": declared(manifest), "files": files}, indent=2) + "\n", "utf-8")
    return lock
