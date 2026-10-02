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
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
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


HARD_CAP = 4_000_000   # bytes of output after which a run is killed: `yes` must not grow the backend by gigabytes
TAIL_KEEP = 64_000     # bytes kept per stream; the callers show the last ~20k characters


class CappedRun:
    """What capped_run saw: raw bytes (the tail of each stream), the exit code and why it stopped."""
    def __init__(self) -> None:
        self.stdout = b""
        self.stderr = b""
        self.returncode: int | None = None
        self.timed_out = False
        self.truncated = False   # output went past the cap and the child was killed


def capped_run(cmd: list[str], *, input: bytes | None = None, timeout: float, hard_cap: int = HARD_CAP,
               keep: int | None = None, **popen: Any) -> CappedRun:
    """Run `cmd`, reading both pipes through a byte-capped ring buffer instead of buffering everything.

    Only the last `keep` bytes of each stream are held. Once the two streams together pass `hard_cap` the child
    (and its process group) is killed and `truncated` is set. On timeout the child is killed too and `timed_out`
    is set. Never decodes: callers decode with errors="replace".
    """
    keep = hard_cap if keep is None else keep
    res = CappedRun()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, **popen)

    def kill() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    lock = threading.Lock()
    total = [0]
    rings: dict[str, deque[bytes]] = {"out": deque(), "err": deque()}
    sizes = {"out": 0, "err": 0}

    def pump(name: str, stream: Any) -> None:
        fd = stream.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            with lock:
                ring = rings[name]
                ring.append(chunk)
                sizes[name] += len(chunk)
                while sizes[name] - len(ring[0]) >= keep:  # drop whole old chunks; the slice below trims the rest
                    sizes[name] -= len(ring.popleft())
                total[0] += len(chunk)
                over = total[0] > hard_cap
                if over:
                    res.truncated = True
            if over:
                kill()
                # keep draining so the child is not blocked on a full pipe while it dies

    def feed() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(input or b"")
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=pump, args=("out", proc.stdout), daemon=True),
               threading.Thread(target=pump, args=("err", proc.stderr), daemon=True)]
    if input is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        res.timed_out = True
        kill()
        proc.wait()
    # A grandchild that kept the pipe open must not hold us here once the leader is gone.
    for t in threads:
        t.join(5)
    for s_ in (proc.stdout, proc.stderr):
        try:
            s_.close()
        except OSError:
            pass
    res.returncode = proc.returncode
    res.stdout = b"".join(rings["out"])[-keep:]
    res.stderr = b"".join(rings["err"])[-keep:]
    return res


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
        p = capped_run(cmd, timeout=timeout, keep=TAIL_KEEP, cwd=work,
                       env={"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": mplcfg},
                       preexec_fn=_limits)
        stdout = p.stdout.decode("utf-8", errors="replace")[-20_000:]
        stderr = p.stderr.decode("utf-8", errors="replace")[-8_000:]
        if p.timed_out:
            out = {"stdout": stdout, "stderr": "Timed out after %ss" % timeout, "exit_code": -1, "timed_out": True}
        else:
            out = {"stdout": stdout, "stderr": stderr, "exit_code": p.returncode, "timed_out": False}
        if p.truncated:
            out["truncated"] = True
            out["stderr"] = (out["stderr"] + "\nOutput passed %d MB and the script was stopped; only the end is shown." % (HARD_CAP // 1_000_000)).strip()
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
