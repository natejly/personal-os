"""Sandboxed Python execution for the run_python tool.

Defense in depth, best effort for a single-user desktop app:
  * fresh temp working directory per run, deleted afterwards
  * `python -I` (isolated mode: no user site, no env vars like PYTHONPATH)
  * wall-clock timeout, plus CPU / process-count / file-size rlimits (and memory where the OS allows it:
    macOS refuses RLIMIT_AS, so there the timeout is the only thing bounding a runaway allocation)
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


def app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    try:
        from .db import data_dir_from_env  # lazy: avoids an import cycle
        return str(data_dir_from_env())
    except Exception:
        return os.path.join(app_root(), "data")


# Fixed fragments shared by every profile built here (run_python and the host shell). SBPL evaluates every rule and
# the last match wins, so a builder emits: blanket deny, its allows, then the targeted denies.
HEADER = """(version 1)
(deny default)
(deny network*)
(allow sysctl-read)
(allow process-fork)
(allow signal (target self))
(allow file-read-metadata)
(allow ipc-posix-shm-read* (ipc-posix-name "apple.shm.notification_center"))
"""
MACH = """(allow mach-lookup
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.opendirectoryd.membership")
  (global-name "com.apple.system.notification_center")
  (global-name "com.apple.system.logger")
  (global-name "com.apple.logd")
  (global-name "com.apple.logd.events")
  (global-name "com.apple.diagnosticd")
  (global-name "com.apple.bsd.dirhelper"))
"""


def secret_read_denies(*, strict: bool = True) -> str:
    """The `(deny file-read* ...)` block for the app's own secrets and the user's keys, browser profiles and tokens.

    `strict=False` (the host shell) leaves ~/.config readable except gcloud, and the app source readable: a shell works
    in the user's own project folders, which may be this repo, and needs its tools' config. run_python needs neither.
    """
    home = os.path.expanduser("~")
    root = app_root()
    cfg = os.path.join(home, ".config") if strict else os.path.join(home, ".config", "gcloud")
    app_support = os.path.join(home, "Library", "Application Support")
    subs = [data_dir(), *([os.path.join(root, "backend", "personal_os")] if strict else []),
            os.path.join(home, ".ssh"), os.path.join(home, ".aws"), cfg, os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"), os.path.join(home, "Library", "Keychains"),
            os.path.join(app_support, "Google", "Chrome"), os.path.join(app_support, "BraveSoftware"),
            os.path.join(app_support, "Firefox"), os.path.join(home, "Library", "Safari")]
    return (f"(deny file-read* (literal {_q(os.path.join(root, '.env'))}) "
            + " ".join(f"(subpath {_q(x)})" for x in subs)
            + ' (regex #"/\\.env$") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))\n')


def _mac_profile(work: str, py: str, sockets: tuple[str, ...] = ()) -> str:
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
    `sockets` are unix-socket paths the script may connect to (the tool bridge); no other network opens.

    SBPL evaluates every rule and the last match wins: blanket deny, then the allows,
    then the targeted denies.
    """
    exe = os.path.realpath(py)
    base = os.path.dirname(os.path.dirname(exe))                    # interpreter + stdlib + lib-dynload
    venv = os.path.realpath(os.path.dirname(os.path.dirname(py)))   # site-packages: numpy, matplotlib, PIL/.dylibs
    work = os.path.realpath(work)
    sock = "".join(f"(allow network-outbound (literal {_q(os.path.realpath(p))}))\n" for p in sockets)
    return (HEADER + f"""(allow process-exec (literal {_q(py)}) (literal {_q(exe)}) (subpath {_q(base)}))
(allow file-read* (subpath {_q(base)}) (subpath {_q(venv)}) (subpath {_q(work)})
                  (subpath "/usr/lib") (subpath "/usr/share") (subpath "/System/Library")
                  (subpath "/Library/Fonts") (subpath "/private/var/db/timezone")
                  (literal "/private/etc/localtime") (literal "/dev/null") (literal "/dev/zero")
                  (literal "/dev/random") (literal "/dev/urandom") (subpath "/dev/fd")
                  (literal "/"))                    ; dyld stats the root dir; without it python aborts at startup
(allow file-write* (subpath {_q(work)}))
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))
""" + MACH + "(deny appleevent-send)\n" + sock + secret_read_denies())


def shell_profile(writable: list[str], network: bool = False) -> str:
    """sandbox-exec profile for the host shell (shell.py): any program may run and read, writes only under `writable`.

    Reads are open except the secrets in secret_read_denies. A writable root that sits inside the (denied) data dir, such
    as a desk workspace, is re-allowed after that deny; the app's own files in there stay denied by name. `.git/hooks`
    and `.git/config` are never writable, so a command cannot plant something the user's own git would run later.
    """
    rw = [os.path.realpath(p) for p in writable]
    out = [HEADER, MACH, "(allow process-exec*)\n(allow signal (target same-sandbox))\n(allow file-read*)\n"]
    if network:
        out.append("(allow network*)\n")
    out.append("(allow file-write* " + " ".join(f"(subpath {_q(p)})" for p in rw) + ")\n")
    out.append('(allow file-write-data (literal "/dev/null") (literal "/dev/tty") (literal "/dev/dtracehelper")'
               ' (literal "/dev/stdout") (literal "/dev/stderr"))\n')
    out.append('(deny file-write* (regex #"/\\.git/hooks(/|$)") (regex #"/\\.git/config$"))\n')
    out.append("(deny appleevent-send)\n")
    data = os.path.realpath(data_dir())
    inside = [p for p in rw if p == data or p.startswith(data + os.sep)]  # never re-allow a root that contains ~/.ssh
    out.append(f"(deny file-write* (subpath {_q(data)}))\n")  # a project root that contains the data dir must not write the db
    out.append(secret_read_denies(strict=False))
    if inside:
        sub = " ".join(f"(subpath {_q(p)})" for p in inside)
        out.append(f"(allow file-read* {sub})\n(allow file-write* {sub})\n")
        out.append('(deny file-write* (regex #"/\\.git/hooks(/|$)") (regex #"/\\.git/config$"))\n')
    out.append('(deny file-read* (regex #"/\\.env$") (regex #"/\\.env\\.[^/]*$") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))\n')
    return "".join(out)


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
