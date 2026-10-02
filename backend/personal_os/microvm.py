"""Persistent VM-isolated sandboxes for the sandbox_* tools.

Each conversation gets one sandbox: an OCI container driven through a CLI runtime
(`docker` by default, settings sandboxRuntime to override). On this project's macOS
setup the docker daemon lives inside a colima guest on Virtualization.framework, so
every container — and therefore every sandbox — executes inside a Linux VM with no
view of the host filesystem. That is a stronger boundary than the sandbox-exec
profile run_python uses, and it is why these tools may run arbitrary shell commands
and keep state between calls while run_python may not.

Security posture (same threat model as sandbox.py: the commands are model-written,
the model's context routinely holds untrusted text, and stdout flows straight back
into that context):
  * no bind mounts — the only files inside are ones the tools put there
  * network detached by default; settings sandboxNetwork attaches it, and then every
    result that carries guest-produced bytes taints the run exactly like fetch_url
  * library text copied in with sandbox_put_document marks the sandbox on the host;
    later command output taints until that container is removed
  * capabilities dropped, no-new-privileges, memory / cpu / pids caps
  * commands run under coreutils `timeout` inside the guest, because killing the
    `docker exec` client does not kill the process it started in the container
"""
from __future__ import annotations

import hashlib
import posixpath
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .sandbox import IMAGE_EXT, MAX_IMAGE_BYTES

WORKSPACE = "/workspace"
DEFAULT_IMAGE = "python:3.12-slim"
LABEL = "personal-os.sandbox"
MAX_SANDBOXES = 5           # LRU-reaped: a desktop should not quietly accumulate VMs
STDOUT_CAP = 20_000
STDERR_CAP = 8_000
MAX_EXEC_S = 600
AVAILABLE_TTL_S = 120

Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


class SandboxError(Exception):
    """A clean, single-line failure for the tool error envelope."""


def _run(argv: list[str], *, input: bytes | None = None, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, input=input, capture_output=True, timeout=timeout)


def _line(b: bytes, cap: int = 300) -> str:
    lines = [ln for ln in b.decode(errors="replace").strip().splitlines() if ln.strip()]
    best = next((ln for ln in reversed(lines) if "rror" in ln), None)  # docker ends with a "--help" hint, not the error
    return (best or (lines[-1] if lines else ""))[:cap]


def guest_path(path: str | None) -> str:
    """Resolve a tool-supplied path: relative paths live in /workspace, absolute ones are taken as-is.

    No confinement is attempted — sandbox_exec can reach any container path anyway, so
    rejecting ../ here would be theater. Normalisation only keeps the semantics tidy.
    """
    p = (path or "").strip().replace("\\", "/")
    if not p or p == ".":
        return WORKSPACE
    if not p.startswith("/"):
        p = posixpath.join(WORKSPACE, p)
    return posixpath.normpath(p)


class Sandboxes:
    """Names, creates, reuses and reaps one container per conversation."""

    def __init__(self, settings_fn: Callable[[], dict[str, Any]], runner: Runner | None = None,
                 import_dir: Path | None = None):
        self.settings = settings_fn
        self._run = runner or _run
        self._import_dir = Path(import_dir) if import_dir else None
        self._avail: tuple[float, bool] | None = None
        self._lock = threading.Lock()   # ensure() can race between parallel runs
        self._last: dict[str, float] = {}    # container name -> last use, for LRU reaping
        self._shell: dict[str, str] = {}     # container name -> bash|sh
        self._net: dict[str, bool] = {}      # container name -> created with network
        self._imported: set[str] = set()     # container names that hold library-file text

    def _bin(self) -> str:
        return str(self.settings().get("sandboxRuntime") or "docker")

    # ---- availability: cheap enough for Toolbox.schemas() every round ----
    def available(self) -> bool:
        now = time.time()
        if self._avail and now - self._avail[0] < AVAILABLE_TTL_S:
            return self._avail[1]
        binary = self._bin()
        ok = False
        if shutil.which(binary):
            try:
                ok = self._run([binary, "info", "--format", "{{.ServerVersion}}"], timeout=4).returncode == 0
            except Exception:  # noqa: BLE001 - a hung daemon means "not available", not a crash
                ok = False
        self._avail = (now, ok)
        return ok

    def _name(self, conversation_id: str) -> str:
        return "pos-sbx-" + hashlib.sha1(conversation_id.encode()).hexdigest()[:12]

    # ---- lifecycle ----
    def ensure(self, conversation_id: str) -> str:
        """The conversation's container, running. Creates or restarts it as needed."""
        binary = self._bin()
        name = self._name(conversation_id)
        with self._lock:
            p = self._run([binary, "inspect", "-f", "{{.State.Running}}", name], timeout=10)
            if p.returncode == 0:
                if p.stdout.strip() != b"true":
                    s = self._run([binary, "start", name], timeout=30)
                    if s.returncode != 0:
                        raise SandboxError(f"sandbox would not restart: {_line(s.stderr)}")
                if name not in self._net:  # container survived an app restart: recover its facts
                    n = self._run([binary, "inspect", "-f", "{{.HostConfig.NetworkMode}}", name], timeout=10)
                    self._net[name] = n.returncode == 0 and n.stdout.strip() != b"none"
                    self._probe_shell(binary, name)
                self._last[name] = time.time()
                return name
            self._reap(binary)
            cfg = self.settings()
            image = str(cfg.get("sandboxImage") or DEFAULT_IMAGE)
            net = bool(cfg.get("sandboxNetwork"))
            args = [binary, "run", "-d", "--name", name, "--label", f"{LABEL}=1", "--hostname", "sandbox",
                    "-w", WORKSPACE, "--memory", "1g", "--cpus", "2", "--pids-limit", "256",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
            if not net:
                args += ["--network", "none"]
            args += [image, "sleep", "infinity"]
            p = self._run(args, timeout=240)  # generous: the first run of an image pulls it
            if p.returncode != 0 and b"already in use" not in p.stderr:
                raise SandboxError(f"could not start the sandbox ({image}): {_line(p.stderr)}")
            self._net[name] = net
            self._probe_shell(binary, name)
            self._last[name] = time.time()
            return name

    def _probe_shell(self, binary: str, name: str) -> None:
        p = self._run([binary, "exec", name, "sh", "-c", "command -v bash"], timeout=10)
        self._shell[name] = "bash" if p.returncode == 0 and p.stdout.strip() else "sh"

    def _live(self, binary: str) -> list[str]:
        p = self._run([binary, "ps", "-a", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}"], timeout=10)
        return [n for n in p.stdout.decode(errors="replace").split() if n] if p.returncode == 0 else []

    def _reap(self, binary: str) -> None:
        names = self._live(binary)
        while len(names) >= MAX_SANDBOXES:
            oldest = min(names, key=lambda n: self._last.get(n, 0.0))
            self._run([binary, "rm", "-f", oldest], timeout=30)
            names.remove(oldest)
            self._forget(oldest)
            self._clear_import(oldest)

    def _forget(self, name: str) -> None:
        self._last.pop(name, None)
        self._shell.pop(name, None)
        self._net.pop(name, None)

    def note_import(self, conversation_id: str) -> None:
        """Library text is now inside this sandbox. The guest cannot clear the mark."""
        name = self._name(conversation_id)
        self._imported.add(name)
        if self._import_dir is None:
            return
        self._import_dir.mkdir(parents=True, exist_ok=True)
        (self._import_dir / name).write_text("1")

    def holds_import(self, conversation_id: str) -> bool:
        name = self._name(conversation_id)
        if name in self._imported:
            return True
        if self._import_dir and (self._import_dir / name).is_file():
            self._imported.add(name)
            return True
        return False

    def _clear_import(self, name: str) -> None:
        self._imported.discard(name)
        if self._import_dir:
            (self._import_dir / name).unlink(missing_ok=True)

    def reset(self, conversation_id: str) -> dict[str, Any]:
        name = self._name(conversation_id)
        p = self._run([self._bin(), "rm", "-f", name], timeout=30)
        self._forget(name)
        # Keep the mark if the container is still there: its files are still readable.
        if p.returncode == 0 or b"No such" in (p.stderr or b""):
            self._clear_import(name)
        return {"reset": True, "note": "the next sandbox tool call starts from a fresh container"}

    def shutdown(self) -> None:
        """Remove every labeled container, ours or orphaned from an earlier app run."""
        binary = self._bin()
        if not shutil.which(binary):
            return
        try:
            for name in self._live(binary):
                self._run([binary, "rm", "-f", name], timeout=30)
        except Exception:  # noqa: BLE001 - shutdown must not fail the app
            pass

    def networked(self, conversation_id: str) -> bool:
        return bool(self._net.get(self._name(conversation_id)))

    # ---- the tool surface ----
    def exec(self, conversation_id: str, command: str, timeout: int = 60) -> dict[str, Any]:
        name = self.ensure(conversation_id)
        t = max(1, min(int(timeout), MAX_EXEC_S))
        shell = self._shell.get(name, "sh")
        try:
            p = self._run([self._bin(), "exec", "-i", "-w", WORKSPACE, name,
                           "timeout", "-k", "5", str(t), shell, "-c", command], timeout=t + 20)
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"Timed out after {t}s", "exit_code": -1, "timed_out": True}
        out = {"stdout": p.stdout.decode(errors="replace")[-STDOUT_CAP:],
               "stderr": p.stderr.decode(errors="replace")[-STDERR_CAP:],
               "exit_code": p.returncode, "timed_out": p.returncode == 124}
        if p.returncode == 124:
            out["stderr"] = (out["stderr"] + f"\nTimed out after {t}s").strip()
        if self._net.get(name):
            out["network"] = True
        return out

    def write_file(self, conversation_id: str, path: str, content: str, append: bool = False) -> dict[str, Any]:
        name = self.ensure(conversation_id)
        gp = guest_path(path)
        if gp == WORKSPACE:
            raise SandboxError("path must name a file, not the workspace directory")
        redir = ">>" if append else ">"
        # the path travels as an argv word ($1), never through shell interpolation
        p = self._run([self._bin(), "exec", "-i", name, "sh", "-c",
                       f'mkdir -p -- "$(dirname -- "$1")" && cat {redir} "$1"', "sh", gp],
                      input=content.encode(), timeout=60)
        if p.returncode != 0:
            raise SandboxError(f"could not write {gp}: {_line(p.stderr)}")
        return {"written": gp, "bytes": len(content.encode()), "appended": append}

    def read_file(self, conversation_id: str, path: str, offset: int = 0, length: int = 6000) -> dict[str, Any]:
        name = self.ensure(conversation_id)
        binary = self._bin()
        gp = guest_path(path)
        off = max(0, int(offset))
        ln = max(1, min(int(length), 200_000))
        p = self._run([binary, "exec", name, "sh", "-c", 'wc -c < "$1"', "sh", gp], timeout=30)
        if p.returncode != 0:
            raise SandboxError(f"could not read {gp}: {_line(p.stderr)}")
        total = int(p.stdout.split()[0] or 0) if p.stdout.split() else 0
        ext = posixpath.splitext(gp)[1].lower()
        if ext in IMAGE_EXT and off == 0:
            if total > MAX_IMAGE_BYTES:
                raise SandboxError(f"{gp} is an image of {total} bytes; only images up to {MAX_IMAGE_BYTES} can be shown")
            raw = self._run([binary, "exec", name, "cat", gp], timeout=60)
            if raw.returncode != 0:
                raise SandboxError(f"could not read {gp}: {_line(raw.stderr)}")
            import base64
            import mimetypes
            mime = mimetypes.guess_type(gp)[0] or "application/octet-stream"
            out: dict[str, Any] = {"path": gp, "total_bytes": total, "shown_inline": True,
                                   "images": [{"name": posixpath.basename(gp), "mime": mime, "bytes": total,
                                               "data": f"data:{mime};base64,{base64.b64encode(raw.stdout).decode()}"}]}
        else:
            p = self._run([binary, "exec", name, "sh", "-c", f'tail -c +{off + 1} -- "$1" | head -c {ln}', "sh", gp], timeout=60)
            if p.returncode != 0:
                raise SandboxError(f"could not read {gp}: {_line(p.stderr)}")
            out = {"path": gp, "total_bytes": total, "offset": off,
                   "text": p.stdout.decode(errors="replace"), "truncated": off + len(p.stdout) < total}
        if self._net.get(name):
            out["network"] = True
        return out

    def list_files(self, conversation_id: str, path: str | None = None) -> dict[str, Any]:
        name = self.ensure(conversation_id)
        gp = guest_path(path)
        p = self._run([self._bin(), "exec", name, "sh", "-c",
                       'find "$1" -maxdepth 3 -printf "%y\\t%s\\t%p\\n" 2>/dev/null | head -400', "sh", gp], timeout=30)
        if p.returncode != 0:
            raise SandboxError(f"could not list {gp}: {_line(p.stderr)}")
        entries = []
        for row in p.stdout.decode(errors="replace").splitlines():
            parts = row.split("\t", 2)
            if len(parts) == 3 and parts[2] != gp:
                entries.append({"type": "dir" if parts[0] == "d" else "file", "bytes": int(parts[1] or 0), "path": parts[2]})
        out: dict[str, Any] = {"path": gp, "entries": entries, "total": len(entries), "truncated": len(entries) >= 399}
        if self._net.get(name):
            out["network"] = True
        return out
