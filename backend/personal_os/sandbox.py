"""Sandboxed Python execution for the run_python tool.

Defense in depth, best effort for a single-user desktop app:
  * fresh temp working directory per run, deleted afterwards
  * `python -I` (isolated mode: no user site, no env vars like PYTHONPATH)
  * wall-clock timeout, CPU + memory rlimits
  * on macOS, wrapped in `sandbox-exec` with a profile that denies network, allowlists
    reads/exec/mach-lookup and restricts writes to the work directory (when available)
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

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_IMAGE_BYTES = 3_000_000
MAX_IMAGES = 6

# matplotlib builds a font cache on first import. It is kept between runs so plots don't pay ~3s each time, but the
# sandbox never writes here: the parent warms it from app-authored code, then copies it into each run's work dir.
MPL_CACHE = os.path.join(tempfile.gettempdir(), "personal-os-mplconfig")
MPL_DIR = ".mplconfig"
_mpl_warmed = False


def _warm_mpl(py: str) -> None:
    """Build the shared font cache once, unsandboxed, from our own code - so no sandboxed run can ever poison it."""
    global _mpl_warmed
    if _mpl_warmed:
        return
    _mpl_warmed = True
    try:
        os.makedirs(MPL_CACHE, exist_ok=True)
        if any(n.startswith("fontlist-") for n in os.listdir(MPL_CACHE)):
            return
        subprocess.run([py, "-I", "-c", "import matplotlib.font_manager"], capture_output=True, timeout=180,
                       env={"PATH": "/usr/bin:/bin", "HOME": MPL_CACHE, "MPLBACKEND": "Agg", "MPLCONFIGDIR": MPL_CACHE})
    except (OSError, subprocess.SubprocessError):
        pass  # worst case matplotlib rebuilds the cache inside the run


def _seed_mpl(work: str) -> str:
    """Private, writable MPLCONFIGDIR for one run, pre-seeded with the warm font cache."""
    dst = os.path.join(work, MPL_DIR)
    os.makedirs(dst, exist_ok=True)
    try:
        for n in os.listdir(MPL_CACHE):
            if n.startswith("fontlist-"):
                shutil.copy2(os.path.join(MPL_CACHE, n), os.path.join(dst, n))
    except OSError:
        pass
    return dst


def _q(p: str) -> str:
    """A path as a quoted SBPL literal (the repo path contains a space)."""
    return '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mac_profile(work: str, py: str) -> str:
    """Least-privilege sandbox-exec profile for one run_python call.

    Threat model: the script is model-written, the model's context routinely holds
    untrusted third-party text, and run_python stdout flows straight back into that
    context. Network is denied, so *reads are the exfiltration channel*: anything the
    script can open can be printed. Reads are therefore an allowlist (interpreter,
    site-packages, the work dir, system libs/fonts/tzdata) with
    the app's own secrets (.env, personal-os.db*, .auth_token), ssh/aws/gpg keys and
    browser profiles denied outright. process-exec and mach-lookup are allowlisted too,
    so the "executes" tier cannot shell out to osascript and silently become "external".
    Writes are confined to the run's own temp work dir, so no state survives to the next run.

    SBPL evaluates every rule and the last match wins: blanket deny, then the allows,
    then the targeted denies.
    """
    exe = os.path.realpath(py)
    base = os.path.dirname(os.path.dirname(exe))                    # interpreter + stdlib + lib-dynload
    venv = os.path.realpath(os.path.dirname(os.path.dirname(py)))   # site-packages: numpy, matplotlib, PIL/.dylibs
    work = os.path.realpath(work)
    home = os.path.expanduser("~")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from .db import data_dir_from_env  # lazy: avoids an import cycle
        data = str(data_dir_from_env())
    except Exception:
        data = os.path.join(root, "data")
    return f"""(version 1)
(deny default)
(deny network*)
(allow sysctl-read)
(allow process-fork)
(allow signal (target self))
(allow file-read-metadata)
(allow ipc-posix-shm-read* (ipc-posix-name "apple.shm.notification_center"))
(allow process-exec (literal {_q(py)}) (literal {_q(exe)}) (subpath {_q(base)}))
(allow file-read* (subpath {_q(base)}) (subpath {_q(venv)}) (subpath {_q(work)})
                  (subpath "/usr/lib") (subpath "/usr/share") (subpath "/System/Library")
                  (subpath "/Library/Fonts") (subpath "/private/var/db/timezone")
                  (literal "/private/etc/localtime") (literal "/dev/null") (literal "/dev/zero")
                  (literal "/dev/random") (literal "/dev/urandom") (subpath "/dev/fd")
                  (literal "/"))                    ; dyld stats the root dir; without it python aborts at startup
(allow file-write* (subpath {_q(work)}))
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))
(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.opendirectoryd.membership")
  (global-name "com.apple.system.notification_center")
  (global-name "com.apple.system.logger")
  (global-name "com.apple.logd")
  (global-name "com.apple.logd.events")
  (global-name "com.apple.diagnosticd")
  (global-name "com.apple.bsd.dirhelper"))
(deny appleevent-send)
(deny file-read* (literal {_q(os.path.join(root, ".env"))}) (subpath {_q(data)})
                 (subpath {_q(os.path.join(root, "backend", "personal_os"))})
                 (subpath {_q(os.path.join(home, ".ssh"))}) (subpath {_q(os.path.join(home, ".aws"))})
                 (subpath {_q(os.path.join(home, ".config"))}) (subpath {_q(os.path.join(home, ".gnupg"))})
                 (subpath {_q(os.path.join(home, ".kube"))})
                 (subpath {_q(os.path.join(home, "Library", "Keychains"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "Google", "Chrome"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "BraveSoftware"))})
                 (subpath {_q(os.path.join(home, "Library", "Application Support", "Firefox"))})
                 (subpath {_q(os.path.join(home, "Library", "Safari"))})
                 (regex #"/\\.env$") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))
"""


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


def _limits() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError):
        pass


def run_python(code: str, timeout: int = 30, python: str | None = None) -> dict[str, Any]:
    work = tempfile.mkdtemp(prefix="pos-sandbox-")
    script = os.path.join(work, "main.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    py = python or sys.executable
    cmd = [py, "-I", script]
    if any(k in code for k in ("matplotlib", "pyplot", "seaborn")):
        _warm_mpl(py)
    mplcfg = _seed_mpl(work)
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        cmd = ["sandbox-exec", "-p", _mac_profile(work, py), *cmd]
    try:
        p = subprocess.run(
            cmd, cwd=work, capture_output=True, text=True, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": mplcfg},
            preexec_fn=_limits,
        )
        out = {"stdout": p.stdout[-20_000:], "stderr": p.stderr[-8_000:], "exit_code": p.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as e:
        out = {"stdout": (e.stdout or b"")[-20_000:].decode() if isinstance(e.stdout, bytes) else (e.stdout or "")[-20_000:],
               "stderr": "Timed out after %ss" % timeout, "exit_code": -1, "timed_out": True}
    finally:
        files = []
        for root, dirs, names in os.walk(work):
            dirs[:] = [d for d in dirs if os.path.join(root, d) != os.path.join(work, MPL_DIR)]
            for n in names:
                if n != "main.py":
                    files.append(os.path.relpath(os.path.join(root, n), work))
        images = _collect_images(work, sorted(files))
        shutil.rmtree(work, ignore_errors=True)
    out["files_created"] = files[:50]
    if images:
        out["images"] = images
    return out
