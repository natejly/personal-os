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
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Any

from . import osplat, runtime
from .osplat.base import SandboxRequest, shortfalls
from .runtime.slots import Refused

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_IMAGE_BYTES = 3_000_000
MAX_IMAGES = 6

# matplotlib builds a font cache on first import. It is kept between runs so plots don't pay ~3s each time, but the
# sandbox never writes here: the parent warms it from app-authored code, then copies it into each run's work dir.
MPL_CACHE = os.path.join(tempfile.gettempdir(), "personal-os-mplconfig")
MPL_DIR = ".mplconfig"
_mpl_warmed = False


def _warm_mpl(py: str) -> None:
    """Build the shared font cache once, from our own code, before any run - so no model-written script can poison it.
    Still under Seatbelt: the work venv's site-packages (python_install) run at import, and its .pth files with them."""
    global _mpl_warmed
    if _mpl_warmed:
        return
    _mpl_warmed = True
    try:
        os.makedirs(MPL_CACHE, exist_ok=True)
        if any(n.startswith("fontlist-") for n in os.listdir(MPL_CACHE)):
            return
        cmd = [py, "-I", "-B", "-c", "import matplotlib.font_manager"]
        if osplat.current().sandbox.python_available():
            cmd = osplat.current().sandbox.wrap(cmd, _mac_profile(MPL_CACHE, py))
        subprocess.run(cmd, capture_output=True, timeout=180,
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


# The Seatbelt profile builders live in osplat/seatbelt.py (pure text); these names stay for callers and tests.
from .osplat.seatbelt import MDNS_SOCKET, _paths, _q, shell_profile  # noqa: E402,F401
from .osplat.seatbelt import python_profile as _mac_profile  # noqa: E402


# ---- sandbox.backend slot (design doc section 10.7.2) ----
class SandboxRefused(Exception):
    """A confined request the selected sandbox backend cannot enforce. Nothing was run, and nothing is retried unconfined."""


class OsDefault:
    """core/os-default: the platform sandbox (Seatbelt on macOS) behind the sandbox.backend interface. Seatbelt's profile
    already encodes the places the request's `deny` lists, so it is built here from the request as it always was."""
    id = "core/os-default"

    def support(self) -> dict[str, dict[str, str]]:
        # Nothing asks for read-only today and the Seatbelt profiles have no read-only form, so it is not claimed.
        return {"workspace-write": dict.fromkeys(("fs_write", "fs_read_deny", "network", "process"), "full")} \
            if osplat.current().sandbox.shell_available() else {}

    def wrap(self, req: SandboxRequest, argv: list[str]) -> list[str]:
        if req.purpose == "python":
            profile = _mac_profile(req.cwd, req.native["py"], req.native.get("socket_path"), req.native.get("workspace"))
        else:
            profile = shell_profile(list(req.writable), network=req.network == "open", proxy_port=req.proxy_port,
                                    allow_hosts=list(req.allow_hosts) or None, **req.native)
        return osplat.current().sandbox.wrap(argv, profile)

    def failed_to_start(self, exit_code: int | None, output: str) -> bool:
        return osplat.current().sandbox.failed_to_start(exit_code, output)


_OS_DEFAULT = OsDefault()


def _denied() -> tuple[str, ...]:
    """The places no confined process may touch: Grain's data folder, its app bundle and code, and the credential stores."""
    from . import mac
    home, _, data = _paths()
    return tuple(dict.fromkeys([data, *map(str, mac.protected_paths()), *(os.path.join(home, d) for d in mac.CRED_HOME_DIRS)]))


def shell_request(cwd: str, writable: list[str], *, network: str = "none", proxy_port: int | None = None,
                  allow_hosts: tuple[str, ...] = (), purpose: str = "shell", **native: Any) -> SandboxRequest:
    """A workspace-write request for a shell command, an agent terminal or a coding agent. `native` carries the Seatbelt knobs
    (confine, loopback) only the OS-default backend reads."""
    return SandboxRequest("workspace-write", cwd, tuple(writable), network, proxy_port, tuple(allow_hosts),  # type: ignore[arg-type]
                          _denied(), purpose, native)  # type: ignore[arg-type]


def python_request(work: str, py: str, socket_path: str | None, workspace: str | None) -> SandboxRequest:
    """The workspace-write request of one run_python call: it writes its work dir (and a desk's workspace) and has no network."""
    return SandboxRequest("workspace-write", work, (work, *([workspace] if workspace else [])), "none", None, (), _denied(), "python",
                          {"py": py, "socket_path": socket_path, "workspace": workspace})


def _current_backend() -> tuple[str, Any]:
    chosen = runtime.active.alternative("sandbox.backend") if runtime.active is not None else None
    return (chosen.id, chosen.impl) if chosen is not None else (_OS_DEFAULT.id, _OS_DEFAULT)


def _refuse(req: SandboxRequest, backend: str, why: str, dimension: str, enforcement: dict[str, Any]) -> SandboxRefused:
    if runtime.active is not None:
        scopes = runtime.active.scopes()
        runtime.active.bus.emit_sync("sandbox.refused", {"tool": req.purpose, "mode": req.mode, "backend": backend, "dimension": dimension,
                                                         "enforcement": enforcement, "run_id": scopes.run_id})
    return SandboxRefused(why)


def sandbox_wrap(req: SandboxRequest, argv: list[str]) -> list[str]:
    """The one place a confined request becomes a wrapped argv. It fails closed: when the selected backend cannot enforce the
    request's mode (or only partly, and the user has not accepted that dimension), the call is refused, never run unconfined,
    on another backend or on the default one. `full` is not a backend capability: it is the explicit unsandboxed request, which
    never comes here."""
    if req.mode == "full":
        raise ValueError("a full request asks for no confinement and is never wrapped")
    try:
        backend_id, backend = _current_backend()
    except Refused as e:
        raise _refuse(req, "", f"{req.purpose} needs {req.mode} confinement; {e}", "setup", {}) from None
    support = _support_of(backend)
    accepted = runtime.active.accepted(backend_id) if runtime.active is not None else frozenset()
    for dimension, level in shortfalls(support, req.mode, req.network):
        if level != "partial" or dimension not in accepted:
            raise _refuse(req, backend_id, f"{req.purpose} needs {req.mode} confinement; the selected sandbox backend '{backend_id}' "
                          f"cannot enforce {dimension} here ({level}). It was not run.", dimension, support.get(req.mode, {}))
    try:
        return backend.wrap(req, argv)
    except Exception as e:  # noqa: BLE001 - a backend that cannot set up its confinement must not leave the command unconfined
        raise _refuse(req, backend_id, f"{req.purpose} needs {req.mode} confinement; the selected sandbox backend '{backend_id}' "
                      f"failed to set it up ({type(e).__name__}: {str(e)[:120]}). It was not run.", "setup", support.get(req.mode, {})) from e


def _support_of(backend: Any) -> dict[str, Any]:
    try:
        return backend.support()
    except Exception:  # noqa: BLE001 - a backend that cannot say what it enforces enforces nothing we can rely on
        return {}


def failed_to_start(exit_code: int | None, output: str) -> bool:
    """Did the selected backend's confinement itself refuse to start (as opposed to the confined command failing)?"""
    try:
        _, backend = _current_backend()
    except Refused:
        backend = _OS_DEFAULT
    return bool(backend.failed_to_start(exit_code, output))


# ---- declared limits: the walls, stated before the work ----
# The profiles above are the enforcement; these strings are what the assistant is told up front, so it plans around
# the wall instead of discovering it mid-task. The run_python description and GET /sandbox/limits both read them, so the
# text cannot drift from the profile without this file changing too.
_DENIAL = re.compile(r"sandbox-exec:|operation not permitted|\[Errno 1\]|(?:errno|error) -54", re.IGNORECASE)


def declared_limits() -> dict[str, dict[str, Any]]:
    """What the run_python sandbox blocks, and how to ask for an exception."""
    return {
        "run_python": {
            "blocks": [
                "network access of any kind, including binding or listening on a local port",
                "subprocesses and launching other apps (osascript, open, a child process)",
                "writes outside the run's own temp working directory (a desk workspace is also writable)",
                "reads outside an allowlist (the interpreter, its packages, the work dir, system libraries); credential "
                "stores (~/.ssh, ~/.aws, keychains, browser profiles, .env files) are denied",
            ],
            "enforced_by": "macOS Seatbelt (sandbox-exec), with per-run rlimits and a wall-clock timeout",
            "escalation": "a task that needs one of these uses shell_run with unsandboxed=true, which asks the user for "
                          "that one command",
        },
    }


def limits_text(limits: dict[str, Any]) -> str:
    """One `Declared limits: ...` sentence for a tool description, from an entry of declared_limits()."""
    blocks = "; ".join(limits.get("blocks") or [])
    return f" Declared limits: {blocks}." if blocks else ""


def profile_failed(exit_code: int | None, stderr: str) -> bool:
    """True when sandbox-exec itself refused to start, so the run never happened (not a failure of the code it wraps)."""
    return exit_code in (65, 71) and str(stderr or "").lstrip().startswith("sandbox-exec:")


def blocked_note(stderr: str, stdout: str = "") -> str | None:
    """When the OS blocked an operation, a sentence naming the wall and the way to ask for an exception. The OS error
    itself stays verbatim in stderr/stdout; this only explains it, so a blocked call is never a silent or generic failure."""
    if not _DENIAL.search(f"{stderr or ''}\n{stdout or ''}"):
        return None
    return ("The OS sandbox blocked an operation: the error above is the operating system's own refusal, not a bug in "
            "the code. Do not retry it unchanged. Report what was impossible, and if the task genuinely needs it, "
            "shell_run with unsandboxed=true asks the user for that one command.")


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
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **osplat.current().proc.new_group_kwargs(), **popen)

    def kill() -> None:
        osplat.current().proc.kill_tree(proc.pid, proc)

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
    cmd = [py, "-I", "-B", script]
    env = {"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
    sock = None
    if bridge is not None:
        from . import toolbridge
        with open(os.path.join(work, GRAIN_TOOLS_MODULE), "w", encoding="utf-8") as f:
            f.write(toolbridge.CLIENT_SOURCE)
        sock = bridge.socket_path
        env["GRAIN_TOOLS_SOCK"] = sock
        # -I keeps the script's own folder off sys.path, so put it back for the client module and run main.py by path.
        cmd = [py, "-I", "-B", "-c", "import sys, runpy; sys.path.insert(0, %r); runpy.run_path(%r, run_name='__main__')" % (work, script)]
    if any(k in code for k in ("matplotlib", "pyplot", "seaborn")):
        _warm_mpl(py)
    env["MPLCONFIGDIR"] = _seed_mpl(work)
    if osplat.current().sandbox.python_available():
        try:
            cmd = sandbox_wrap(python_request(work, py, sock, workspace), cmd)
        except SandboxRefused:
            shutil.rmtree(work, ignore_errors=True)
            raise
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
                osplat.current().proc.kill_tree(p.pid, p)
                stdout, stderr = p.communicate()
                break
    return bridge.shape_stdout(stdout or ""), stderr or "", (-1 if timed_out else p.returncode), timed_out
