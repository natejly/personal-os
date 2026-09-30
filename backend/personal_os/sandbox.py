"""Sandboxed Python execution for the run_python tool.

Defense in depth, best effort for a single-user desktop app:
  * fresh temp working directory per run, deleted afterwards
  * `python -I` (isolated mode: no user site, no env vars like PYTHONPATH)
  * wall-clock timeout, plus CPU / process-count / file-size rlimits (and memory where the OS allows it:
    macOS refuses RLIMIT_AS, so there the timeout is the only thing bounding a runaway allocation)
  * on macOS, wrapped in `sandbox-exec` with a profile that denies network and
    restricts writes to the work directory (when available)
"""
from __future__ import annotations

import base64
import mimetypes
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from typing import Any

MAC_PROFILE = """(version 1)
(deny default)
(allow process-exec process-fork sysctl-read mach-lookup)
(allow file-read*)
(allow file-write* (subpath "{work}"))
(allow file-write* (subpath "/private/tmp") (subpath "/private/var/folders"))
(deny network*)
"""

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_IMAGE_BYTES = 3_000_000
MAX_IMAGES = 6

# matplotlib builds a font cache on first import; keep it between runs so plots don't pay ~3s each time.
MPL_CACHE = os.path.join(tempfile.gettempdir(), "personal-os-mplconfig")


def _collect_images(work: str, files: list[str]) -> list[dict[str, Any]]:
    """Image files the script wrote, as data URIs, so the UI can show them inline."""
    out: list[dict[str, Any]] = []
    for rel in files:
        if os.path.splitext(rel)[1].lower() not in IMAGE_EXT or len(out) >= MAX_IMAGES:
            continue
        path = os.path.join(work, rel)
        try:
            if os.path.getsize(path) > MAX_IMAGE_BYTES:
                continue
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        out.append({"name": rel, "mime": mime, "bytes": len(data), "data": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
    return out


RLIMITS = (("RLIMIT_CPU", 20), ("RLIMIT_AS", 1_500_000_000), ("RLIMIT_DATA", 1_500_000_000),
           ("RLIMIT_NPROC", 64), ("RLIMIT_FSIZE", 512_000_000), ("RLIMIT_CORE", 0))


def _limits() -> None:
    """Best-effort rlimits for one run, each applied on its own.

    They used to share a try block, which quietly cost us every limit after the first unsupported one: macOS refuses
    RLIMIT_AS with "current limit exceeds maximum limit", so RLIMIT_NPROC was never reached and a script could fork
    freely. Address space still cannot be capped on macOS (RLIMIT_DATA is refused too) - there the wall-clock timeout
    is what ends a runaway allocation - but both are still attempted because they do work on Linux.

    RLIMIT_NPROC counts processes per *user*, not per script, so on a desktop already running far more than 64 the
    effect is that the script cannot fork at all. That matches what run_python promises ("no subprocesses"), and it
    matters because subprocess.run's timeout kills only the direct child: forks it left behind would outlive the run.
    """
    for name, soft in RLIMITS:
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (soft, soft))
        except (ValueError, OSError):
            pass


def run_python(code: str, timeout: int = 30, python: str | None = None) -> dict[str, Any]:
    work = tempfile.mkdtemp(prefix="pos-sandbox-")
    script = os.path.join(work, "main.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    py = python or sys.executable
    cmd = [py, "-I", script]
    os.makedirs(MPL_CACHE, exist_ok=True)
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        cmd = ["sandbox-exec", "-p", MAC_PROFILE.format(work=os.path.realpath(work)), *cmd]
    try:
        p = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": MPL_CACHE},
            preexec_fn=_limits,
        )
        out = {"stdout": p.stdout[-20_000:], "stderr": p.stderr[-8_000:], "exit_code": p.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as e:
        out = {"stdout": (e.stdout or b"")[-20_000:].decode() if isinstance(e.stdout, bytes) else (e.stdout or "")[-20_000:],
               "stderr": "Timed out after %ss" % timeout, "exit_code": -1, "timed_out": True}
    finally:
        files = []
        for root, _dirs, names in os.walk(work):
            for n in names:
                if n != "main.py":
                    files.append(os.path.relpath(os.path.join(root, n), work))
        images = _collect_images(work, sorted(files))
        shutil.rmtree(work, ignore_errors=True)
    out["files_created"] = files[:50]
    if images:
        out["images"] = images
    return out
