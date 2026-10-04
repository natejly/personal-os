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
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
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


def _paths() -> tuple[str, str, str]:
    """(home, repo root, app data dir): the places a sandbox must never read from."""
    home = os.path.expanduser("~")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from .db import data_dir_from_env  # lazy: avoids an import cycle
        data = str(data_dir_from_env())
    except Exception:
        data = os.path.join(root, "data")
    return home, root, data


def _sbpl_re(p: str) -> str:
    """A literal path inside an SBPL regex."""
    return re.sub(r'([.^$*+?()\[\]{}|\\"])', r"\\\1", p)


# Files that run code or configure tools the next time the user, git, an editor or an agent opens the folder. No shell
# write may touch them under any root, a desk workspace included: the block goes last, so it beats every allow above it.
_PROTECTED_WRITES = r"""(deny file-write* (regex #"/\.(zshrc|zprofile|zshenv|zlogin|bashrc|bash_profile|profile|envrc|gitconfig|mcp\.json)$")
                  (regex #"/\.git/(hooks|config|info)(/|$)") (regex #"/\.vscode/(tasks|settings)\.json$")
                  (regex #"/\.husky(/|$)") (regex #"/\.auth_token$") (regex #"/personal-os\.db"))
"""


def shell_profile(writable: list[str], network: bool = False, proxy_port: int | None = None) -> str:
    """Seatbelt profile for the host shell (shell.py): blanket deny, then what a shell needs, then targeted denies.

    Unlike run_python's allowlist, a shell has to run whatever the user's toolchain is, so reads are open and the
    *secrets* are the denylist: ssh/gpg/aws/gcloud/keychains, any .env, the app's own data dir and database. Writes
    are confined to `writable` (the folder the command runs in, a per-run tmp dir) and never reach the app's data dir
    or code, ~/Library, home dotfiles, or anything in `_PROTECTED_WRITES` (rc files, git hooks and config, editor tasks),
    where a write would run later outside the sandbox. Network is off unless `network`; with `proxy_port` (and
    not `network`) the one thing it may connect to is the allowlisting proxy on localhost at that port (egress.py).
    """
    home, root, data = _paths()
    w = " ".join(f"(subpath {_q(os.path.realpath(p))})" for p in writable)
    net = "(allow network*)" if network else "(deny network*)"
    if proxy_port and not network:
        net += f'\n(allow network-outbound (remote ip "localhost:{int(proxy_port)}"))'
    # The shared work venv lives under the app data dir, which is denied above; the shell has its bin first on PATH, so it must
    # be able to read it (pip, python). Appended after the deny so it wins; only that folder, never the database beside it.
    late = ""
    try:
        from . import envs
        wb = envs.work_bin()
    except Exception:  # noqa: BLE001
        wb = None
    if wb:
        late = f"(allow file-read* (subpath {_q(os.path.realpath(os.path.dirname(wb)))}))\n"
    # A desk workspace lives inside the app data dir, which the deny above covers. Re-allow only those
    # writable folders, then repeat the secret-name denies so a database or .env still loses.
    creds = " ".join([*(f"(subpath {_q(os.path.join(home, d))})" for d in (".docker", ".azure")),
                      *(f"(literal {_q(os.path.join(home, f))})" for f in (".netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass"))])
    data_real, home_real = os.path.realpath(data), os.path.realpath(home)
    inside = list(dict.fromkeys(rp for p in writable if (rp := os.path.realpath(p)).startswith(data_real + os.sep)))
    if inside:
        w_in = " ".join(f"(subpath {_q(p)})" for p in inside)
        late += f"(allow file-read* file-write* {w_in})\n"
        late += '(deny file-read* file-write* (regex #"/\\.env($|\\.)") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))\n'
    return f"""(version 1)
(deny default)
{net}
(allow sysctl-read)
(allow process-fork)
(allow process-exec)
(allow process-info*)
(allow signal (target same-sandbox))
(allow file-read*)
(allow file-write* {w})
(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr") (literal "/dev/tty") (literal "/dev/dtracehelper"))
(allow mach-lookup)
(allow ipc-posix-shm)
(allow pseudo-tty)
(deny appleevent-send)
(deny file-write* (subpath {_q(data)}) (subpath {_q(data_real)}) (literal {_q(os.path.join(root, ".env"))})
                  (subpath {_q(os.path.join(root, "backend", "personal_os"))})
                  (subpath {_q(os.path.join(home_real, "Library"))}) (regex #"^{_sbpl_re(home_real)}/\\.[^/]+"))
(deny file-read* (subpath {_q(data)}) (literal {_q(os.path.join(root, ".env"))})
                 (subpath {_q(os.path.join(root, "backend", "personal_os"))})
                 (subpath {_q(os.path.join(home, ".ssh"))}) (subpath {_q(os.path.join(home, ".gnupg"))})
                 (subpath {_q(os.path.join(home, ".aws"))}) (subpath {_q(os.path.join(home, ".config", "gcloud"))})
                 (subpath {_q(os.path.join(home, ".kube"))}) {creds}
                 (subpath {_q(os.path.join(home, "Library", "Keychains"))})
                 (regex #"/\\.env($|\\.)") (regex #"/\\.auth_token$") (regex #"/personal-os\\.db"))
{late}{_PROTECTED_WRITES}"""


def _mac_profile(work: str, py: str, socket_path: str | None = None, workspace: str | None = None) -> str:
    """Least-privilege sandbox-exec profile for one run_python call.

    Threat model: the script is model-written, the model's context routinely holds
    untrusted third-party text, and run_python stdout flows straight back into that
    context. Network is denied, so *reads are the exfiltration channel*: anything the
    script can open can be printed. Reads are therefore an allowlist (interpreter,
    site-packages, the work dir, system libs/fonts/tzdata) with
    the app's own secrets (.env, personal-os.db*, .auth_token), ssh/aws/gpg keys and
    browser profiles denied outright. process-exec and mach-lookup are allowlisted too,
    so the "executes" tier cannot shell out to osascript and silently become "external".
    Writes are confined to the run's own temp work dir, so no state survives to the next run - except in a cowork desk,
    where `workspace` (the desk's own folder, symlink-resolved) is readable and writable too: that is what the desk is
    for. The app data dir is denied below, and the workspace and the shared work venv both live inside it, so their
    allows come after that deny; the secret-name denies are repeated after them and still win.

    SBPL evaluates every rule and the last match wins: blanket deny, then the allows,
    then the targeted denies.
    """
    exe = os.path.realpath(py)
    base = os.path.dirname(os.path.dirname(exe))                    # interpreter + stdlib + lib-dynload
    venv = os.path.realpath(os.path.dirname(os.path.dirname(py)))   # site-packages: numpy, matplotlib, PIL/.dylibs
    work = os.path.realpath(work)
    home, root, data = _paths()
    # The tool bridge's Unix socket (toolbridge.py) is the one network the script gets: connect to that path, nothing else.
    names = r'(regex #"/\.env$") (regex #"/\.auth_token$") (regex #"/personal-os\.db")'
    late = ""
    if workspace:
        wsp = os.path.realpath(workspace)
        late = f"(allow file-read* file-write* (subpath {_q(wsp)}))\n"
    if os.path.realpath(venv).startswith(os.path.realpath(data) + os.sep):  # the work env under <data>/envs
        late += f"(allow file-read* (subpath {_q(os.path.realpath(venv))}))\n"
    sock = (f'(allow network-outbound (remote unix-socket (path-literal {_q(os.path.realpath(socket_path))})))\n'
            f'(allow file-read* file-write* (literal {_q(os.path.realpath(socket_path))}))\n') if socket_path else ""
    return f"""(version 1)
(deny default)
(deny network*)
{sock}(allow sysctl-read)
(allow process-fork)
(allow signal (target self))
(allow file-read-metadata)
(allow ipc-posix-shm-read* (ipc-posix-name "apple.shm.notification_center"))
(allow process-exec (literal {_q(py)}) (literal {_q(exe)}) (subpath {_q(base)}))
(allow file-read* (subpath {_q(base)}) (subpath {_q(venv)}) (subpath {_q(work)})
                  (subpath "/usr/lib") (subpath "/usr/share") (subpath "/System/Library")
                  (subpath "/Library/Fonts") (subpath "/private/var/db/timezone")
                  (literal "/private/etc/localtime") (literal "/private/etc/mime.types") (literal "/private/etc/apache2/mime.types")  ; openpyxl & co. build a MimeTypes() at import
                  (literal "/dev/null") (literal "/dev/zero")
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
{late}(deny file-read* {names})
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


GRAIN_TOOLS_MODULE = "grain_tools.py"


WORKSPACE_REPORT_CAP = 50
WORKSPACE_SKIP = (".baseline", ".trash")  # workspace bookkeeping, never reported as something the script made


def _workspace_changes(root: str, since_ns: int) -> list[str]:
    """Files under a desk workspace written at or after `since_ns`, relative and sorted, at most WORKSPACE_REPORT_CAP."""
    found: list[str] = []
    for dirpath, dirs, names in os.walk(root):
        if dirpath == root:
            dirs[:] = [d for d in dirs if d not in WORKSPACE_SKIP]
        for n in names:
            full = os.path.join(dirpath, n)
            try:
                if not os.path.islink(full) and os.stat(full).st_mtime_ns >= since_ns:
                    found.append(os.path.relpath(full, root))
            except OSError:
                continue
    return sorted(found)[:WORKSPACE_REPORT_CAP]


def _limits_for(cpu_seconds: int):  # type: ignore[no-untyped-def]
    """`_limits` with the CPU ceiling lifted to the wall-clock timeout: a desk run may legitimately compute for a while."""
    def apply() -> None:
        for name, soft in RLIMITS:
            limit = getattr(resource, name, None)
            if limit is None:
                continue
            try:
                resource.setrlimit(limit, (max(soft, cpu_seconds) if name == "RLIMIT_CPU" else soft,) * 2)
            except (ValueError, OSError):
                pass
    return apply


def run_python(code: str, timeout: int = 30, python: str | None = None, bridge: Any = None,
               workspace: str | None = None, keep: Any = None) -> dict[str, Any]:
    """Run `code` in the sandbox. With `workspace` (a cowork desk's folder) the script runs *in* that folder, may read
    and write it, and the result lists what it created or changed there (`workspace_files`); the script itself and the
    scratch HOME/TMPDIR stay in the per-run temp dir, which is still deleted afterwards. `bridge` (toolbridge.Bridge) lets the script call app tools over its Unix socket:
    its client module is dropped next to the script, the socket is the one network path the profile allows, and the
    wall clock stops while the bridge is waiting on an approval card (`bridge.paused_for()`). `keep` (outside a desk) is
    called with the temp dir's `outputs/` before the temp dir is deleted, and what it returns is the result's `outputs`."""
    work = tempfile.mkdtemp(prefix="pos-sandbox-")
    script = os.path.join(work, "main.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)
    py = python or sys.executable
    started_ns = time.time_ns()
    cwd = os.path.realpath(workspace) if workspace else work
    pre = _limits_for(timeout) if workspace else _limits
    cmd = [py, "-I", script]
    env = {"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
    sock = None
    if bridge is not None:
        from . import toolbridge
        with open(os.path.join(work, GRAIN_TOOLS_MODULE), "w", encoding="utf-8") as f:
            f.write(toolbridge.CLIENT_SOURCE)
        sock = bridge.socket_path
        env["GRAIN_TOOLS_SOCK"] = sock
        # -I keeps the script's own folder off sys.path, so put it back for the client module and run main.py by path.
        cmd = [py, "-I", "-c", "import sys, runpy; sys.path.insert(0, %r); runpy.run_path(%r, run_name='__main__')" % (work, script)]
    if any(k in code for k in ("matplotlib", "pyplot", "seaborn")):
        _warm_mpl(py)
    env["MPLCONFIGDIR"] = _seed_mpl(work)
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        cmd = ["sandbox-exec", "-p", _mac_profile(work, py, sock, workspace), *cmd]
    try:
        if bridge is None:
            # Byte-capped: `yes` must not grow the backend by gigabytes (capped_run).
            p = capped_run(cmd, timeout=timeout, keep=TAIL_KEEP, cwd=cwd, env=env, preexec_fn=pre)
            stdout = p.stdout.decode("utf-8", errors="replace")[-20_000:]
            stderr = p.stderr.decode("utf-8", errors="replace")[-8_000:]
            if p.timed_out:
                out = {"stdout": stdout, "stderr": "Timed out after %ss" % timeout, "exit_code": -1, "timed_out": True}
            else:
                out = {"stdout": stdout, "stderr": stderr, "exit_code": p.returncode, "timed_out": False}
            if p.truncated:
                out["truncated"] = True
                out["stderr"] = (out["stderr"] + "\nOutput passed %d MB and the script was stopped; only the end is shown." % (HARD_CAP // 1_000_000)).strip()
        else:
            stdout, stderr, rc, timed_out = _run_paused(cmd, cwd, env, timeout, bridge, pre)
            out = {"stdout": stdout, "stderr": "Timed out after %ss" % timeout if timed_out else stderr[-bridge.stderr_cap:],
                   "exit_code": rc, "timed_out": timed_out}
    except subprocess.TimeoutExpired as e:
        out = {"stdout": (e.stdout or b"")[-20_000:].decode() if isinstance(e.stdout, bytes) else (e.stdout or "")[-20_000:],
               "stderr": "Timed out after %ss" % timeout, "exit_code": -1, "timed_out": True}
    finally:
        files = []
        for root, dirs, names in os.walk(work):
            dirs[:] = [d for d in dirs if os.path.join(root, d) != os.path.join(work, MPL_DIR)]
            for n in names:
                if n not in ("main.py", GRAIN_TOOLS_MODULE):
                    files.append(os.path.relpath(os.path.join(root, n), work))
        images = _collect_images(work, sorted(files))
        wfiles = _workspace_changes(cwd, started_ns) if workspace else []
        if wfiles:
            images += _collect_images(cwd, wfiles)[: max(0, MAX_IMAGES - len(images))]
        kept = keep(os.path.join(work, "outputs")) if keep is not None and not workspace else None
        shutil.rmtree(work, ignore_errors=True)
    if kept:
        out = {"outputs": kept, **out}  # first, so a card still finds it when a long stdout cuts the stored preview
    out["files_created"] = files[:50]
    if workspace:
        out["workspace_files"] = wfiles
    if images:
        out["images"] = images
    from . import redact
    out["stdout"] = redact.scrub_command_output(str(out.get("stdout") or ""))
    out["stderr"] = redact.scrub_command_output(str(out.get("stderr") or ""))
    return out


def _run_paused(cmd: list[str], work: str, env: dict[str, str], timeout: float, bridge: Any,
                pre: Any = None) -> tuple[str, str, int, bool]:
    """subprocess.run with a clock that does not tick while the bridge waits on the user. Returns
    (stdout, stderr, exit_code, timed_out); the child is killed with its whole group on timeout."""
    p = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         preexec_fn=pre or _limits, start_new_session=True)
    t0, timed_out = time.monotonic(), False
    while True:
        try:
            stdout, stderr = p.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - t0 - bridge.paused_for() > timeout:
                timed_out = True
                try:
                    os.killpg(p.pid, 9)
                except (ProcessLookupError, PermissionError):
                    p.kill()
                stdout, stderr = p.communicate()
                break
    return bridge.shape_stdout(stdout or ""), stderr or "", (-1 if timed_out else p.returncode), timed_out
