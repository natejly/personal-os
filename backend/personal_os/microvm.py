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
  * network detached by default. settings sandboxNetwork is off | proxy | open:
    - proxy puts the container on its own `--internal` docker network whose only other
      member is a sidecar running egress.py (also attached to the bridge). That proxy is the
      only way out, so a program that ignores HTTP(S)_PROXY simply has no route. It admits
      the registry preset plus shellAllowedDomains, and results taint only once the sandbox
      has reached a host outside the registries (the sidecar's contacted report says so)
    - open attaches the bridge, and then every result that carries guest-produced bytes
      taints the run exactly like fetch_url
  * library text copied in with sandbox_put_document marks the sandbox on the host;
    later command output taints until that container is removed
  * capabilities dropped, no-new-privileges, memory / cpu / pids caps
  * commands run under coreutils `timeout` inside the guest, because killing the
    `docker exec` client does not kill the process it started in the container
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import egress, permissions
from .sandbox import IMAGE_EXT, MAX_IMAGE_BYTES, capped_run

WORKSPACE = "/workspace"
DESK_MOUNT = "/workspace/desk"  # where an active desk's own workspace appears inside its container
DEFAULT_IMAGE = permissions.DEFAULT_IMAGE
LABEL = "personal-os.sandbox"
CONV_LABEL = "personal-os.conv"  # which conversation a container belongs to; survives an app restart
MAX_SANDBOXES = 5           # LRU-reaped: a desktop should not quietly accumulate VMs
CKPT_REPO = "pos-sbx-ckpt"  # checkpoint images: pos-sbx-ckpt/<container suffix>:<label>
MAX_CKPTS = 3               # per conversation, oldest evicted
DEFAULT_KEEP_DAYS = 14      # a stopped sandbox nobody came back to is removed after this long
STDOUT_CAP = 20_000
STDERR_CAP = 8_000
MAX_EXEC_S = 600
AVAILABLE_TTL_S = 120
NET_PREFIX = "pos-sbx-net-"     # a proxy-mode sandbox's own internal network
PX_PREFIX = "pos-sbx-px-"       # and the sidecar that is its only way out
PROXY_LABEL = "personal-os.sandbox-proxy"
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")
PROXY_ALIAS = "egress"          # the sidecar's name on the internal network
PROXY_PORT = 3128
PROXY_LOG = "/tmp/egress.json"  # in the sidecar's own filesystem: survives stop/start, goes with the container


def net_mode(v: Any) -> str:
    """The sandboxNetwork setting as off | proxy | open. A stored true (the old on/off switch) means open."""
    if v is True:
        return "open"
    return v if v in ("proxy", "open") else "off"

Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


class SandboxError(Exception):
    """A clean, single-line failure for the tool error envelope."""


EXEC_HARD_CAP = 4_000_000   # a command that prints more than this is killed (`yes` with a 600s timeout)
READ_HARD_CAP = 8_000_000   # file reads: above the largest image sandbox_read_file will show


def _run(argv: list[str], *, input: bytes | None = None, timeout: float = 60,
         hard_cap: int = READ_HARD_CAP, keep: int | None = None) -> subprocess.CompletedProcess[bytes]:
    """subprocess.run, but the pipes are read through a byte cap instead of buffered whole."""
    r = capped_run(argv, input=input, timeout=timeout, hard_cap=hard_cap, keep=keep)
    if r.timed_out:
        raise subprocess.TimeoutExpired(argv, timeout, output=r.stdout, stderr=r.stderr)
    cp = subprocess.CompletedProcess(argv, r.returncode if r.returncode is not None else -1, r.stdout, r.stderr)
    cp.truncated = r.truncated  # type: ignore[attr-defined]
    return cp


def _line(b: bytes, cap: int = 300) -> str:
    lines = [ln for ln in b.decode(errors="replace").strip().splitlines() if ln.strip()]
    best = next((ln for ln in reversed(lines) if "rror" in ln), None)  # docker ends with a "--help" hint, not the error
    return (best or (lines[-1] if lines else ""))[:cap]


def ckpt_slug(label: str) -> str:
    """A docker-tag-safe checkpoint name; empty labels get a timestamp."""
    slug = re.sub(r"[^a-z0-9_.-]", "-", (label or "").strip().lower())[:40].lstrip(".-")
    return slug or f"ckpt-{int(time.time())}"


def _finished_at(raw: str) -> float | None:
    """Docker's RFC3339 FinishedAt (nanoseconds) as a unix time; None if it is not a real timestamp."""
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", raw.strip())
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = "+00:00" if m.group(3) in (None, "Z") else m.group(3)
    try:
        dt = datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")
    except ValueError:
        return None
    return dt.timestamp() if dt.year > 2000 else None  # 0001-01-01 = never started


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


EXPORT_MAX_BYTES = 10_000_000  # one file handed out of the sandbox
IDLE_STOP_S = 300       # a running container untouched this long is stopped (its files and installs survive)
REAP_EVERY_S = 60


class Sandboxes:
    """Names, creates, reuses and reaps one container per conversation."""

    def __init__(self, settings_fn: Callable[[], dict[str, Any]], runner: Runner | None = None,
                 import_dir: Path | None = None):
        self.settings = settings_fn
        self._run = runner or _run
        self._import_dir = Path(import_dir) if import_dir else None
        self._avail: tuple[float, bool] | None = None
        self._avail_reason = ""              # why available() said no, for the Settings status line
        self._lock = threading.Lock()   # ensure() can race between parallel runs
        self._last: dict[str, float] = {}    # container name -> last use, for LRU reaping
        self._shell: dict[str, str] = {}     # container name -> bash|sh
        self._net: dict[str, str] = {}       # container name -> off | proxy | open, as created
        self._reached: set[str] = set()      # proxy-mode containers that reached a non-registry host (sticky)
        self._seen: dict[str, dict[str, list[str]]] = {}  # last proxy report per container, for per-call deltas
        self._stale_checked = False          # _reap_stale runs once per app run
        # conversation id -> host path of that conversation's desk workspace, or None. Set by the app, which owns the desks.
        self.desk_workspace: Callable[[str], str | None] | None = None
        self._busy: dict[str, int] = {}      # container name -> calls in flight (never stopped as idle mid-command)
        self._reaper: threading.Thread | None = None
        self._reaper_stop = threading.Event()
        self._imported: set[str] = set()     # container names that hold library-file text

    def _cap_kw(self) -> dict[str, Any]:
        """exec's tighter output cap; an injected test runner takes no such arguments."""
        return {"hard_cap": EXEC_HARD_CAP, "keep": 4 * STDOUT_CAP} if self._run is _run else {}

    def _bin(self) -> str:
        return str(permissions.get(self.settings(), "sandboxRuntime") or "docker")

    # ---- availability: cheap enough for Toolbox.schemas() every round ----
    def available(self) -> bool:
        now = time.time()
        if self._avail and now - self._avail[0] < AVAILABLE_TTL_S:
            return self._avail[1]
        binary = self._bin()
        ok, reason = False, f"{binary} is not on PATH"
        if shutil.which(binary):
            try:
                p = self._run([binary, "info", "--format", "{{.ServerVersion}}"], timeout=4)
                ok = p.returncode == 0
                reason = "" if ok else f"`{binary} info` failed: {_line(p.stderr) or 'the daemon is not responding'}"
            except Exception as e:  # noqa: BLE001 - a hung daemon means "not available", not a crash
                reason = f"`{binary} info` failed: {type(e).__name__}"
        self._avail, self._avail_reason = (now, ok), reason
        return ok

    def status(self) -> dict[str, Any]:
        ok = self.available()
        return {"available": ok, "runtime": self._bin(), "reason": "" if ok else self._avail_reason}

    def list(self) -> list[dict[str, Any]]:
        """Every labeled container, stopped ones included, for the Settings view. Containers made before the
        conversation label existed report conversation_id None and can still be reset by name."""
        binary = self._bin()
        fmt = "{{.Names}}\t{{.State}}\t{{.CreatedAt}}\t{{.Label \"" + CONV_LABEL + "\"}}"
        p = self._run([binary, "ps", "-a", "--filter", f"label={LABEL}=1", "--format", fmt], timeout=10)
        if p.returncode != 0:
            raise SandboxError(f"could not list sandboxes: {_line(p.stderr)}")
        items = []
        for ln in p.stdout.decode(errors="replace").splitlines():
            name, state, created, conv = (ln.split("\t") + ["", "", ""])[:4]
            name = name.strip()
            if not name:
                continue
            items.append({"name": name, "conversation_id": conv.strip() or None, "status": state.strip(),
                          "created": created.strip(), "last_used": self._last.get(name), "networked": None if self._net.get(name) is None else self._net[name] != "off",
                          "holds_import": self._holds(name), "checkpoints": [t for t, _ in self._ckpt_tags(binary, name)]})
        return items

    def _name(self, conversation_id: str) -> str:
        return "pos-sbx-" + hashlib.sha1(conversation_id.encode()).hexdigest()[:12]

    # ---- lifecycle ----
    def ensure(self, conversation_id: str) -> str:
        """The conversation's container, running. Creates or restarts it as needed."""
        binary = self._bin()
        name = self._name(conversation_id)
        with self._lock:
            self._reap_stale(binary, keep=name)
            p = self._run([binary, "inspect", "-f", "{{.State.Running}}", name], timeout=10)
            if p.returncode == 0:
                woke = p.stdout.strip() != b"true"
                if woke:
                    s = self._run([binary, "start", name], timeout=30)
                    if s.returncode != 0:
                        raise SandboxError(f"sandbox would not restart: {_line(s.stderr)}")
                if name not in self._net:  # container survived an app restart: recover its facts
                    woke = True
                    n = self._run([binary, "inspect", "-f", "{{.HostConfig.NetworkMode}}", name], timeout=10)
                    mode = n.stdout.decode(errors="replace").strip() if n.returncode == 0 else "none"
                    self._net[name] = "off" if mode == "none" else "proxy" if mode.startswith(NET_PREFIX) else "open"
                    self._probe_shell(binary, name)
                if woke and self._net.get(name) == "proxy":
                    s = self._run([binary, "start", PX_PREFIX + self._sfx(name)], timeout=30)
                    if s.returncode != 0:
                        raise SandboxError(f"the sandbox's network proxy would not start ({_line(s.stderr)}); "
                                           "sandbox_reset starts a fresh sandbox")
                self._last[name] = time.time()
                self._start_reaper()
                return name
            self._reap(binary)
            cfg = self.settings()
            self._create(binary, name, str(permissions.get(cfg, "sandboxImage") or DEFAULT_IMAGE), net_mode(permissions.get(cfg, "sandboxNetwork")),
                         self._desk_mount(conversation_id), conversation_id)
            return name

    @staticmethod
    def _sfx(name: str) -> str:
        return name[len("pos-sbx-"):]

    def _desk_mount(self, conversation_id: str) -> str | None:
        """Host folder to bind at /workspace/desk: only a desk's own workspace, and only when sandboxMountDesk is on."""
        if self.desk_workspace is None or not self.settings().get("sandboxMountDesk", True):
            return None
        try:
            path = self.desk_workspace(conversation_id)
        except Exception:  # noqa: BLE001 - no desk lookup means no mount, not a failed sandbox
            return None
        return str(path) if path else None

    def _run_args(self, binary: str, name: str, image: str, net: Any, mount: str | None = None,
                  env: dict[str, str] | None = None, conv: str | None = None) -> list[str]:
        """One place for the isolation flags, shared by a fresh create and a checkpoint restore."""
        # --init: `sleep` as PID 1 ignores SIGTERM, so without it every stop waits out the whole grace period.
        args = [binary, "run", "-d", "--init", "--name", name, "--label", f"{LABEL}=1", "--hostname", "sandbox",
                "-w", WORKSPACE, "--memory", "1g", "--cpus", "2", "--pids-limit", "256",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        if conv:
            args += ["--label", f"{CONV_LABEL}={conv}"]
        mode = net_mode(net)
        if mode == "off":
            args += ["--network", "none"]
        elif mode == "proxy":  # internal-only: the sidecar is the one reachable address
            args += ["--network", NET_PREFIX + self._sfx(name)]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        if mount:  # the one bind mount there is: a desk's workspace, nothing else of the host
            args += ["-v", f"{mount}:{DESK_MOUNT}:rw"]
        return args + [image, "sleep", "infinity"]

    def _create(self, binary: str, name: str, image: str, net: Any, mount: str | None = None,
                conv: str | None = None) -> None:
        mode = net_mode(net)
        # Not proxy: blank the proxy variables, since a checkpoint committed in proxy mode carries them in its image
        # and they would point at a sidecar that no longer exists.
        env = self._start_proxy(binary, name) if mode == "proxy" else dict.fromkeys(PROXY_VARS, "")
        p = self._run(self._run_args(binary, name, image, mode, mount, env, conv), timeout=240)  # generous: the first run of an image pulls it
        if p.returncode != 0 and b"already in use" not in p.stderr:
            raise SandboxError(f"could not start the sandbox ({image}): {_line(p.stderr)}")
        self._net[name] = mode
        self._probe_shell(binary, name)
        self._last[name] = time.time()
        self._start_reaper()

    def _start_proxy(self, binary: str, name: str) -> dict[str, str]:
        """Create the internal network and the egress sidecar for a proxy-mode sandbox; returns the sandbox's proxy env.
        The token lives only in the two containers' environments. Any failure is a failed create: no proxy, no sandbox."""
        sfx = self._sfx(name)
        net, px = NET_PREFIX + sfx, PX_PREFIX + sfx
        token = secrets.token_urlsafe(18)
        allow = egress.allowed_set(True, permissions.get(self.settings(), "shellAllowedDomains"))
        self._run([binary, "rm", "-f", px], timeout=30)  # a leftover from a crash holds an old token
        n = self._run([binary, "network", "create", "--internal", "--label", f"{PROXY_LABEL}=1", net], timeout=30)
        if n.returncode != 0 and b"already exists" not in n.stderr:
            raise SandboxError(f"could not create the sandbox's network: {_line(n.stderr)}")
        src = Path(egress.__file__).read_text()
        r = self._run([binary, "run", "-d", "--init", "--name", px, "--label", f"{PROXY_LABEL}=1", "--hostname", "egress",
                       "--user", "65534:65534", "--memory", "128m", "--cpus", "0.5", "--pids-limit", "64",
                       "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                       "-e", f"GRAIN_PROXY_TOKEN={token}", "-e", "GRAIN_PROXY_ALLOW=" + ",".join(allow),
                       "-e", f"GRAIN_PROXY_PORT={PROXY_PORT}", "-e", f"GRAIN_PROXY_LOG={PROXY_LOG}",
                       DEFAULT_IMAGE, "python3", "-c", src], timeout=240)
        if r.returncode != 0:
            raise SandboxError(f"could not start the sandbox's network proxy: {_line(r.stderr)}")
        c = self._run([binary, "network", "connect", "--alias", PROXY_ALIAS, net, px], timeout=30)
        if c.returncode != 0:
            self._rm_proxy(binary, name)
            raise SandboxError(f"could not attach the sandbox's network proxy: {_line(c.stderr)}")
        return egress.proxy_env(PROXY_ALIAS, PROXY_PORT, token)

    def _rm_proxy(self, binary: str, name: str) -> None:
        self._run([binary, "rm", "-f", PX_PREFIX + self._sfx(name)], timeout=30)
        self._run([binary, "network", "rm", NET_PREFIX + self._sfx(name)], timeout=30)

    def _rm(self, binary: str, name: str) -> "subprocess.CompletedProcess[bytes]":
        """Remove a sandbox (and, in proxy mode, its sidecar and network). A sandbox whose mode this app run has not
        recovered yet is checked for a sidecar by name."""
        p = self._run([binary, "rm", "-f", name], timeout=30)
        mode = self._net.get(name)
        if mode == "proxy" or (mode is None and PX_PREFIX + self._sfx(name) in self._live(binary, label=PROXY_LABEL)):
            self._rm_proxy(binary, name)
        self._forget(name)
        return p

    def _proxy_seen(self, binary: str, name: str) -> dict[str, list[str]] | None:
        """The sidecar's cumulative contacted / blocked report; None when it cannot be read (callers then assume the worst)."""
        p = self._run([binary, "exec", PX_PREFIX + self._sfx(name), "cat", PROXY_LOG], timeout=10)
        if p.returncode != 0:
            return {"contacted": [], "blocked": []} if b"No such file" in (p.stderr or b"") else None
        try:
            d = json.loads(p.stdout)
            return {"contacted": [str(h) for h in d["contacted"]], "blocked": [str(h) for h in d["blocked"]]}
        except (ValueError, KeyError, TypeError):
            return None

    def _note_reach(self, name: str, seen: dict[str, list[str]] | None) -> bool:
        """True (and remembered) once a proxy-mode sandbox has reached a host outside the registry preset."""
        if name in self._reached:
            return True
        if seen is None or any(not egress.host_allowed(h, egress.REGISTRY_HOSTS) for h in seen["contacted"]):
            if seen is not None:
                self._reached.add(name)
            return True
        return False

    def _probe_shell(self, binary: str, name: str) -> None:
        p = self._run([binary, "exec", name, "sh", "-c", "command -v bash"], timeout=10)
        self._shell[name] = "bash" if p.returncode == 0 and p.stdout.strip() else "sh"

    def _live(self, binary: str, running_only: bool = False, label: str = LABEL) -> list[str]:
        p = self._run([binary, "ps", *([] if running_only else ["-a"]), "--filter", f"label={label}=1", "--format", "{{.Names}}"], timeout=10)
        return [n for n in p.stdout.decode(errors="replace").split() if n] if p.returncode == 0 else []

    def _reap(self, binary: str) -> None:
        names = self._live(binary)
        while len(names) >= MAX_SANDBOXES:
            oldest = min(names, key=lambda n: self._last.get(n, 0.0))
            self._rm(binary, oldest)
            names.remove(oldest)
            self._clear_import(oldest)

    def _reap_stale(self, binary: str, keep: str = "") -> None:
        """Remove stopped sandboxes untouched for sandboxKeepDays (stopping at quit keeps state; this bounds it)."""
        if self._stale_checked:
            return
        self._stale_checked = True
        days = float(self.settings().get("sandboxKeepDays", DEFAULT_KEEP_DAYS) or 0)
        cutoff = time.time() - days * 86400
        for n in self._live(binary) if days > 0 else []:
            if n == keep:
                continue
            p = self._run([binary, "inspect", "-f", "{{.State.Running}} {{.State.FinishedAt}}", n], timeout=10)
            if p.returncode != 0:
                continue
            running, _, fin = p.stdout.decode(errors="replace").strip().partition(" ")
            ts = _finished_at(fin)
            if running != "true" and ts is not None and ts < cutoff:
                self._rm(binary, n)
        live = set(self._live(binary))  # a sidecar whose sandbox is gone (removed while its mode was unknown) goes too
        for px in self._live(binary, label=PROXY_LABEL):
            if px.startswith(PX_PREFIX) and "pos-sbx-" + px[len(PX_PREFIX):] not in live:
                self._rm_proxy(binary, "pos-sbx-" + px[len(PX_PREFIX):])

    def stop_idle(self, now: float | None = None) -> list[str]:
        """Stop (not remove) running sandboxes idle for IDLE_STOP_S. ensure() restarts one on its next use, so this only
        frees the VM's memory and CPU; nothing a model wrote is lost. Returns the names stopped."""
        t = time.time() if now is None else now
        binary = self._bin()
        stopped: list[str] = []
        with self._lock:
            try:
                running = self._live(binary, running_only=True)
            except Exception:  # noqa: BLE001 - a reaper pass must never raise
                return stopped
            for n in running:
                if self._busy.get(n) or n not in self._last or t - self._last[n] < IDLE_STOP_S:
                    continue
                if self._run([binary, "stop", "-t", "3", n], timeout=30).returncode == 0:
                    stopped.append(n)
                    if self._net.get(n) == "proxy":
                        self._run([binary, "stop", "-t", "3", PX_PREFIX + self._sfx(n)], timeout=30)
        return stopped

    def _start_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper_stop.clear()

        def loop() -> None:
            while not self._reaper_stop.wait(REAP_EVERY_S):
                try:
                    self.stop_idle()
                except Exception:  # noqa: BLE001
                    pass
        self._reaper = threading.Thread(target=loop, name="sandbox-reaper", daemon=True)
        self._reaper.start()

    def _forget(self, name: str) -> None:
        self._last.pop(name, None)
        self._shell.pop(name, None)
        self._net.pop(name, None)
        self._reached.discard(name)
        self._seen.pop(name, None)

    def note_import(self, conversation_id: str) -> None:
        """Library text is now inside this sandbox. The guest cannot clear the mark."""
        name = self._name(conversation_id)
        self._imported.add(name)
        if self._import_dir is None:
            return
        self._import_dir.mkdir(parents=True, exist_ok=True)
        (self._import_dir / name).write_text("1")

    def holds_import(self, conversation_id: str) -> bool:
        return self._holds(self._name(conversation_id))

    def _holds(self, name: str) -> bool:
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
        return self.reset_name(self._name(conversation_id))

    def reset_name(self, name: str) -> dict[str, Any]:
        binary = self._bin()
        p = self._rm(binary, name)
        # Keep the mark if the container is still there: its files are still readable.
        if p.returncode == 0 or b"No such" in (p.stderr or b""):
            self._clear_import(name)
        for tag, _ in self._ckpt_tags(binary, name):  # "reset" keeps meaning "start clean"
            self._run([binary, "rmi", "-f", f"{CKPT_REPO}/{name[len('pos-sbx-'):]}:{tag}"], timeout=60)
        return {"reset": True, "note": "the next sandbox tool call starts from a fresh container"}

    def shutdown(self) -> None:
        """Stop (not remove) every running labeled container: /workspace and installs survive the next launch.
        _reap_stale removes the ones nobody comes back to."""
        self._reaper_stop.set()  # the reaper's job ends with the app; the loop below stops whatever is still running
        binary = self._bin()
        if not shutil.which(binary):
            return
        try:
            for name in self._live(binary, running_only=True):
                self._run([binary, "stop", "-t", "3", name], timeout=30)
                if self._net.get(name) == "proxy":
                    self._run([binary, "stop", "-t", "3", PX_PREFIX + self._sfx(name)], timeout=30)
        except Exception:  # noqa: BLE001 - shutdown must not fail the app
            pass

    # ---- checkpoints: filesystem snapshots as local images ----
    def _ckpt_tags(self, binary: str, name: str) -> list[tuple[str, str]]:
        """[(label, created)] newest first."""
        p = self._run([binary, "images", f"{CKPT_REPO}/{name[len('pos-sbx-'):]}", "--format", "{{.Tag}}\t{{.CreatedAt}}"], timeout=15)
        if p.returncode != 0:
            return []
        rows = []
        for ln in p.stdout.decode(errors="replace").splitlines():
            tag, _, created = ln.partition("\t")
            if tag.strip() and tag.strip() != "<none>":
                rows.append((tag.strip(), created.strip()))
        return sorted(rows, key=lambda r: r[1][:19], reverse=True)  # stable: docker's own order breaks ties

    def checkpoint(self, conversation_id: str, label: str = "") -> dict[str, Any]:
        name = self.ensure(conversation_id)
        binary = self._bin()
        slug = ckpt_slug(label)
        tag = f"{CKPT_REPO}/{name[len('pos-sbx-'):]}:{slug}"
        p = self._run([binary, "commit", "--pause=true", "-m", "pos checkpoint", name, tag], timeout=300)
        if p.returncode != 0:
            raise SandboxError(f"could not checkpoint the sandbox: {_line(p.stderr)}")
        tags = self._ckpt_tags(binary, name)
        for old, _ in [t for t in tags if t[0] != slug][max(0, MAX_CKPTS - 1):]:
            self._run([binary, "rmi", f"{CKPT_REPO}/{name[len('pos-sbx-'):]}:{old}"], timeout=60)
        kept = [slug] + [t for t, _ in tags if t != slug][: MAX_CKPTS - 1]
        return {"checkpoint": slug, "tag": tag, "kept": kept,
                "note": "filesystem only: running processes are not saved"}

    def checkpoints(self, conversation_id: str) -> dict[str, Any]:
        name = self._name(conversation_id)
        return {"checkpoints": [{"label": t, "created": c} for t, c in self._ckpt_tags(self._bin(), name)]}

    def restore(self, conversation_id: str, label: str) -> dict[str, Any]:
        binary = self._bin()
        name = self._name(conversation_id)
        have = [t for t, _ in self._ckpt_tags(binary, name)]
        slug = ckpt_slug(label) if (label or "").strip() else ""
        if slug not in have:
            raise SandboxError(f"no checkpoint named {label or '(empty)'}; have: {', '.join(have) or 'none'}")
        with self._lock:
            self._rm(binary, name)
            # Networking is decided now, from current settings: a checkpoint cannot re-enable it.
            self._create(binary, name, f"{CKPT_REPO}/{name[len('pos-sbx-'):]}:{slug}", net_mode(permissions.get(self.settings(), "sandboxNetwork")),
                         self._desk_mount(conversation_id), conversation_id)
        return {"restored": slug}

    def networked(self, conversation_id: str) -> bool:
        """True when what this sandbox returns may carry internet bytes: open network, or a proxy that let it reach a
        host outside the registry preset (or whose report cannot be read)."""
        name = self._name(conversation_id)
        mode = self._net.get(name)
        if mode == "proxy":
            return self._note_reach(name, None if name in self._reached else self._proxy_seen(self._bin(), name))
        return mode == "open"

    def reaches_out(self, conversation_id: str) -> bool:
        """True when the container was created with any route out (proxy or open), whatever it has reached so far."""
        return self._net.get(self._name(conversation_id), "off") != "off"

    def _egress_report(self, name: str, out: dict[str, Any]) -> None:
        """Fold the hosts the proxy saw since the previous call into a result, and taint-flag it when it must."""
        seen = self._proxy_seen(self._bin(), name)
        prev = self._seen.get(name) or {"contacted": [], "blocked": []}
        if seen is not None:
            self._seen[name] = seen
        new = {k: [h for h in (seen or {}).get(k, []) if h not in prev[k]] for k in ("contacted", "blocked")}
        out["egress"] = {"mode": "allowlist", **new}
        if new["blocked"]:
            out["note"] = (f"The sandbox's network proxy blocked: {', '.join(new['blocked'][:8])}. The user can allow a host "
                           "under Settings (shell allowed domains); a new sandbox (sandbox_reset) picks it up.")
        if self._note_reach(name, seen):
            out["network"] = True

    # ---- the tool surface ----
    def exec(self, conversation_id: str, command: str, timeout: int = 60) -> dict[str, Any]:
        name = self.ensure(conversation_id)
        t = max(1, min(int(timeout), MAX_EXEC_S))
        shell = self._shell.get(name, "sh")
        self._busy[name] = self._busy.get(name, 0) + 1
        try:
            p = self._run([self._bin(), "exec", "-i", "-w", WORKSPACE, name,
                           "timeout", "-k", "5", str(t), shell, "-c", command], timeout=t + 20,
                           **self._cap_kw())
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"Timed out after {t}s", "exit_code": -1, "timed_out": True}
        finally:
            self._busy[name] -= 1
            self._last[name] = time.time()  # a long command counts as use up to the moment it ended
        out = {"stdout": p.stdout.decode(errors="replace")[-STDOUT_CAP:],
               "stderr": p.stderr.decode(errors="replace")[-STDERR_CAP:],
               "exit_code": p.returncode, "timed_out": p.returncode == 124}
        if p.returncode == 124:
            out["stderr"] = (out["stderr"] + f"\nTimed out after {t}s").strip()
        if getattr(p, "truncated", False):
            out["truncated"] = True
            out["stderr"] = (out["stderr"] + f"\nOutput passed {EXEC_HARD_CAP // 1_000_000} MB and the command was "
                                             "stopped; only the end is shown.").strip()
        if self._net.get(name) == "proxy":
            self._egress_report(name, out)
        elif self._net.get(name) == "open":
            out["network"] = True
        from . import redact
        out["stdout"] = redact.scrub_command_output(out["stdout"])
        out["stderr"] = redact.scrub_command_output(out["stderr"])
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
            from . import redact
            out = {"path": gp, "total_bytes": total, "offset": off,
                   "text": redact.scrub_command_output(p.stdout.decode(errors="replace")),
                   "truncated": off + len(p.stdout) < total}
        if self.networked(conversation_id):
            out["network"] = True
        return out

    def export_file(self, conversation_id: str, path: str) -> tuple[str, bytes]:
        """The raw bytes of a file under /workspace, for handing to the user (capped at EXPORT_MAX_BYTES)."""
        name = self.ensure(conversation_id)
        gp = guest_path(path)
        if gp == WORKSPACE or not gp.startswith(WORKSPACE + "/"):
            raise SandboxError(f"{gp} is not a file under {WORKSPACE}; only files in /workspace can be exported")
        p = self._run([self._bin(), "exec", name, "sh", "-c", 'test -f "$1" && wc -c < "$1"', "sh", gp], timeout=30)
        if p.returncode != 0 or not p.stdout.split():
            raise SandboxError(f"{gp} is not a regular file in the sandbox")
        total = int(p.stdout.split()[0])
        if total > EXPORT_MAX_BYTES:
            raise SandboxError(f"{gp} is {total} bytes; the export limit is {EXPORT_MAX_BYTES}. Split or compress it first.")
        raw = self._run([self._bin(), "exec", name, "cat", gp], timeout=120,
                      **({"hard_cap": EXPORT_MAX_BYTES + 1024, "keep": EXPORT_MAX_BYTES + 1024} if self._run is _run else {}))
        if raw.returncode != 0 or len(raw.stdout) != total:
            raise SandboxError(f"could not read {gp}: {_line(raw.stderr) or 'size changed while reading'}")
        return gp, raw.stdout

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
        if self.networked(conversation_id):
            out["network"] = True
        return out
