"""A host shell for the agent, kept off the secrets by the OS.

`shell_run` runs one command in /bin/zsh on the user's machine under macOS Seatbelt (sandbox.shell_profile): it may run
in any folder and read and write anywhere the user could, except Grain's own data folder and app (never written), the
credential stores (never read or written) and the files that run code later (rc files, git hooks, launch agents). The
default folder is the desk workspace in a desk, else the home folder. The network has three modes: open (`shellNetwork`), off, or
-- the default when a registry preset or allowed domains apply -- one allowlisting proxy on localhost (egress.py) and
nothing else. The sandbox is the boundary, the approval card is the courtesy: nothing here relies on parsing the command.

If the sandbox is unavailable (no sandbox-exec, another OS, or the profile fails to apply) the call is refused. The
only way past that is `unsandboxed=true`, which Toolbox.gate turns into a forced approval no grant can buy off
(Allow everything runs it without one). `unsandboxed=true` is also the per-call exception for one action the sandbox
blocks (binding a port, launching an app), and a blocked call names the rule that caused it (`wall_note`).

Background commands return a job id; shell_poll reads new output (wait_s blocks until there is some, so an agent never
sleeps in a shell to wait), shell_kill stops one. Jobs belong to the conversation and the run that started them (a
worker's own run id, so a worker's jobs outlive the reply that started it and die when the worker ends), die with that
run and with the app, and are never adopted after a restart: the registry persists pid/pgid so a survivor shows up as
`orphaned` with a kill, nothing more (a worker's survivors are killed at startup: no one can ever poll them).
"""
from __future__ import annotations

import asyncio
import codecs
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import egress, mac, permissions, redact, sandbox
from .db import new_id

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
BACKGROUND_TIMEOUT = 600
TERM_GRACE = 3.0                       # SIGTERM to the group, then SIGKILL after this long
TRUNC_LINES, TRUNC_BYTES = 2000, 50_000
CWD_FILE = "end-cwd"                   # in the run's private tmp dir: where the command's shell ended
FG_CAPTURE = 5_000_000                 # the most one foreground command's output keeps (the spill behind the handle)
BG_BUFFER = 200_000                    # a background job's rolling buffer
POLL_WAIT_MAX = 300                    # the longest one shell_poll(wait_s) blocks
MAX_TRACKED = 64
FINISHED_KEEP_S = 30 * 60
SPILL_DAYS = 7
SAFE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class ShellError(Exception):
    pass


def taint(ctx: dict[str, Any], src: str) -> None:
    """Mark the reply as having read untrusted text, with the source named so an approved plan can predict it."""
    ctx["tainted"] = True
    # Appended even when already listed: the chat loop fences a result when this list grew during the call.
    ctx.setdefault("taint_sources", []).append(src)


def net_blocked_note(hosts: list[str]) -> str:
    return (f"The network proxy blocked: {', '.join(hosts[:8])}. The user can allow a host under Settings (shell allowed "
            "domains), or ask them with desk_ask.")


def _scrub(text: str) -> str:
    return redact.scrub_command_output(text)


# ---- paths ----
def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _inside(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


# Where the last foreground command of a conversation ended (`cd` sticks). Per conversation, in memory: a restart goes back
# to the default folder, which is the safe place to start from.
_LAST_CWD: dict[str, str] = {}
_LAST_CWD_MAX = 256


def remembered_cwd(conversation_id: str | None) -> str | None:
    return _LAST_CWD.get(conversation_id or "")


def _remember_cwd(conversation_id: str | None, p: str) -> None:
    key = conversation_id or ""
    _LAST_CWD.pop(key, None)
    _LAST_CWD[key] = p
    while len(_LAST_CWD) > _LAST_CWD_MAX:
        _LAST_CWD.pop(next(iter(_LAST_CWD)))


def resolve_cwd(cwd: str | None, default: Path, desk: Path | None = None) -> Path:
    """The folder a command runs in: any existing folder except Grain's own data folder and app (the active desk's
    workspace, which lives in there, is fine). A relative cwd is relative to `default`. Raises ShellError with a message
    the model can act on."""
    if not cwd or not str(cwd).strip():
        return _real(default)
    raw = Path(os.path.expanduser(str(cwd).strip()))
    p = _real(raw if raw.is_absolute() else default / raw)
    if not (desk and _inside(p, _real(desk))) and (why := mac.protected_reason(raw if raw.is_absolute() else default / raw, p)):
        raise ShellError(f"{p}: {why}.")
    if not p.is_dir():
        raise ShellError(f"{p} is not a folder.")
    return p


def reaches_out(settings: dict[str, Any]) -> bool:
    """True when a sandboxed command could send something off this Mac: open network, or a proxy with any allowed host."""
    return bool(permissions.get(settings, "shellNetwork")) or bool(
        egress.allowed_set(permissions.get(settings, "shellRegistryAccess"), permissions.get(settings, "shellAllowedDomains"),
                           permissions.get(settings, "allowAllConnections")))


def auto_ok(args: dict[str, Any], ctx: dict[str, Any], settings: dict[str, Any], roots: list[Any]) -> bool:
    """True when this shell_run call may skip its card: it is the default `ask` of the tool (the user never set one), it runs
    in a desk, sandboxed, with its working folder inside that desk's workspace (`roots`), and `deskShellAuto` is on.

    Only ever turns a card OFF for the spec default; every stricter rule (permission rules, the hardline list, doom-loop
    cards, desk autonomy `ask`, plan mode, forced approvals) is applied by the caller afterwards and still wins. Two more
    refusals of its own: open network (the sandbox then protects nothing from leaving) and a reply that has read untrusted
    content while a proxy would let a command out (a card is cheap there, an exfiltration is not)."""
    from .tools import Toolbox
    if not ctx.get("desk_id") or not roots or ctx.get("proposal_only") or not permissions.get(settings, "deskShellAuto"):
        return False
    if args.get("unsandboxed") or permissions.get(settings, "shellNetwork"):
        return False
    layers = [permissions.get(settings, "tools") or {}, ctx.get("tool_overrides") or {}]
    if any(Toolbox._norm(layer.get("shell_run")) is not None for layer in layers if isinstance(layer, dict)):
        return False  # the user set a mode for this tool somewhere: that choice stands
    if ctx.get("tainted") and reaches_out(settings):
        return False
    real = [_real(r) for r in roots]
    raw = str(args.get("cwd") or "").strip() or remembered_cwd(ctx.get("conversation_id")) or None
    try:
        where = resolve_cwd(raw, real[0], real[0])
    except ShellError:
        return False
    return any(_inside(where, r) for r in real)


def perm_where(tb: Any, ctx: dict[str, Any]) -> tuple[list[str], str]:
    """(roots, cwd) for permrules on a shell_run: the desk workspace, whose own folder a command may name without it counting
    as Grain's data, and where the command starts before its own `cwd` argument: where the last command in this
    conversation ended, else the desk workspace, else home."""
    desk: Path | None = None
    did = str(ctx.get("desk_id") or "")
    if did and getattr(tb, "workspace", None) is not None:
        try:
            desk = Path(tb.workspace.desk_root(did))
        except Exception:  # noqa: BLE001 - no desk folder: judge from home
            desk = None
    base = str(desk) if desk else str(mac.home())
    last = os.path.expanduser(remembered_cwd(ctx.get("conversation_id")) or "")
    return ([str(desk)] if desk else []), (os.path.join(base, last) if last else base)


# What the shell sandbox blocks, as (pattern in the command's own error output, the sandbox rule behind it). Stated up
# front in the tool description and quoted back with the OS error when a call hits one, so a wall is never silent.
SANDBOX_LIMITS = ("it cannot bind or listen on a network port (even 127.0.0.1) unless the user enabled open network, cannot "
                  "send Apple events or launch/drive apps, cannot write Grain's data folder or app, cannot touch credential "
                  "stores, and cannot write shell rc files, git hooks or launch agents")
_DENIED = re.compile(r"Operation not permitted|Permission denied|Errno 1\b|EPERM|error -54|-10810|-1743|Read-only file system", re.I)
_WALLS = (  # (words near the denial, sandbox rule, plain reason); the first that matches names the wall
    (re.compile(r"bind|listen|socket|connect", re.I), "network*",
     "the sandbox denies network access, binding or listening on a port included, unless open network is enabled"),
    (re.compile(r"osascript|apple ?event|LSOpen|-10810|-1743", re.I), "appleevent-send / app launch",
     "the sandbox denies Apple events and launching or driving apps"),
    (re.compile(r"."), "file-write* / file-read*",
     "the sandbox denies this path (Grain's data folder and app, credential stores, rc files, git hooks, launch agents)"),
)


def wall_note(output: str, network: bool = False) -> dict[str, str] | None:
    """The sandbox rule behind a failed call, the OS error quoted: None unless the output holds a denial. With open
    network the network rule is not what blocked it."""
    line = next((ln.strip()[:300] for ln in output.splitlines() if _DENIED.search(ln)), None)
    if line is None:
        return None
    for pat, rule, why in _WALLS:
        if pat.search(output) and not (network and rule.startswith("network")):
            return {"rule": rule, "error": line, "why": why, "retry": "unsandboxed=true asks the user for this one command"}
    return None


# ---- environment and output shaping ----
def scrubbed_env(tmp: str) -> dict[str, str]:
    """An allowlist, never the app's environment: API keys and tokens live there."""
    from . import envs
    # The shared work environment first, once it exists, so `python` and `pip` in a command are the ones with the
    # document and data libraries rather than the system's.
    path = f"{work}:{SAFE_PATH}" if (work := envs.work_bin()) else SAFE_PATH
    return {"PATH": path, "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8", "TERM": "dumb",
            "TMPDIR": tmp, "TMPPREFIX": f"{tmp}/zsh", "NO_COLOR": "1",  # zsh puts here-document temp files at TMPPREFIX, not TMPDIR
            # HOME is the real one (read-only here), so the package tools' default caches are unwritable: pip prints a warning on
            # every call and uv fails outright. Point them at the run's own dir instead.
            "PIP_CACHE_DIR": f"{tmp}/pip-cache", "UV_CACHE_DIR": f"{tmp}/uv-cache", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}


def truncate(text: str) -> tuple[str, bool]:
    """Keep the tail (that is where a failure is) within TRUNC_LINES lines and TRUNC_BYTES bytes."""
    lines = text.split("\n")
    cut = False
    if len(lines) > TRUNC_LINES:
        lines, cut = lines[-TRUNC_LINES:], True
    out = "\n".join(lines)
    raw = out.encode("utf-8", "replace")
    if len(raw) > TRUNC_BYTES:
        out, cut = raw[-TRUNC_BYTES:].decode("utf-8", "ignore"), True
    return out, cut


def sandbox_available() -> bool:
    return sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))


def _pgid_of(pid: int) -> int | None:
    try:
        r = subprocess.run(["ps", "-o", "pgid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


async def _exited(proc: asyncio.subprocess.Process) -> None:
    """Until the leader exits. proc.wait() also waits for its pipes to close (Python 3.12+), and a leftover child holding
    stdout keeps them open, so the group kill that ends that child would never run."""
    while proc.returncode is None:
        await asyncio.sleep(0.05)


def _signal_group(pgid: int | None, sig: int) -> None:
    if not pgid or pgid <= 1:
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


# ---- jobs ----
class Job:
    def __init__(self, job_id: str, command: str, cwd: str, conversation_id: str | None, run_id: str | None,
                 background: bool, notify: bool, cap: int):
        self.id, self.command, self.cwd = job_id, command, cwd
        self.conversation_id, self.run_id = conversation_id, run_id
        self.background, self.notify, self.cap = background, notify, cap
        self.proc: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.pgid: int | None = None
        self.buf = ""                  # rolling text; absolute offset of buf[0] is `base`
        self.base = 0
        self.read_pos = 0
        self.status = "running"        # running | exited | killed | timed_out | orphaned | failed
        self.exit_code: int | None = None
        self.started = time.time()
        self.out_at = self.started    # when it last printed anything: a waiting agent whose job prints is not stalled
        self.finished: float | None = None
        self.tmp: str | None = None
        self.pump: asyncio.Task | None = None
        self.watch: asyncio.Task | None = None
        self.notified = False
        self.want_notify = notify      # notify is only honoured for background jobs; a promoted foreground job takes it up
        self.on_timeout = "kill"       # background | kill: what a foreground timeout does
        self.max_background = 4
        self.pool = "shell"            # concurrency pool a background job counts against ("coding" for coding sessions)
        self.promoted = asyncio.Event()  # set when a foreground job that hit its timeout carries on in the background
        self.egress_token: str | None = None
        self.net: dict[str, list[str]] | None = None   # what the proxy saw, frozen when the job ends
        self.end_cwd: str | None = None

    @property
    def total(self) -> int:
        return self.base + len(self.buf)

    def append(self, text: str) -> None:
        if text:
            self.out_at = time.time()
        self.buf += text
        if len(self.buf) > self.cap:
            drop = len(self.buf) - self.cap
            self.buf, self.base = self.buf[drop:], self.base + drop

    def live(self) -> bool:
        return self.status == "running"

    def info(self) -> dict[str, Any]:
        return {"job_id": self.id, "pid": self.pid, "pgid": self.pgid, "cwd": self.cwd, "run_id": self.run_id,
                "conversation_id": self.conversation_id, "command": self.command[:200], "status": self.status,
                "exit_code": self.exit_code, "started": self.started, "finished": self.finished,
                "background": self.background, "total": self.total}


class ShellJobs:
    """Every shell process this backend started: foreground ones for the length of the call, background ones until
    they finish, are killed, or age out."""

    def __init__(self, state_path: Path | None = None):
        self.jobs: dict[str, Job] = {}
        self.notes: dict[str, list[str]] = {}
        self.on_note: Any = None       # called with the conversation id when a completion note is queued (wakes an idle desk)
        self.on_change: Any = None     # called with no arguments when a job starts, ends or is killed (the Running list)
        self.sandbox_failed = False    # the OS refused to apply the profile once: unsandboxed may now be asked for
        self.egress = egress.Egress()  # the allowlisting proxy; binds its port on first use
        self.state_path = state_path
        self._last_sweep = 0.0
        self._load_orphans()

    # -- persistence: never adopt, only report --
    def _load_orphans(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            rows = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            rows = []
        for r in rows if isinstance(rows, list) else []:
            try:
                pid, pgid = int(r["pid"]), int(r["pgid"])
                os.kill(pid, 0)
            except (KeyError, TypeError, ValueError, ProcessLookupError):
                continue
            except PermissionError:
                pass
            if _pgid_of(pid) != pgid:   # pid recycled by something that is not our process group
                continue
            j = Job(str(r.get("job_id") or new_id()), str(r.get("command") or ""), str(r.get("cwd") or ""),
                    r.get("conversation_id"), r.get("run_id"), True, False, BG_BUFFER)
            j.pid, j.pgid, j.status, j.started = pid, pgid, "orphaned", float(r.get("started") or time.time())
            self.jobs[j.id] = j
        self._persist()

    def _persist(self) -> None:
        if not self.state_path:
            return
        rows = [{"job_id": j.id, "pid": j.pid, "pgid": j.pgid, "cwd": j.cwd, "run_id": j.run_id,
                 "conversation_id": j.conversation_id, "command": j.command[:200], "started": j.started}
                for j in self.jobs.values() if j.status in ("running", "orphaned") and j.pid and j.pgid]
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rows))
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # -- registry --
    def prune(self) -> None:
        t = time.time()
        for j in [j for j in self.jobs.values() if not j.live() and j.status != "orphaned" and j.finished
                  and t - j.finished > FINISHED_KEEP_S]:
            self.jobs.pop(j.id, None)

    def _make_room(self) -> None:
        self.prune()
        while len(self.jobs) >= MAX_TRACKED:
            done = sorted((j for j in self.jobs.values() if not j.live() and j.status != "orphaned"),
                          key=lambda j: j.finished or 0)
            if not done:
                raise ShellError(f"{MAX_TRACKED} shell jobs are already tracked and all are still running. "
                                 "Kill one with shell_kill first.")
            self.jobs.pop(done[0].id, None)

    def get(self, job_id: str, conversation_id: str | None) -> Job | None:
        j = self.jobs.get(job_id)
        if j and conversation_id is not None and j.conversation_id not in (None, conversation_id):
            return None  # one chat cannot poll or kill another chat's job
        return j

    def list(self) -> list[dict[str, Any]]:
        """Every tracked job for the user's Running view: live and orphaned first, newest first within each."""
        return [j.info() for j in sorted(self.jobs.values(),
                                         key=lambda j: (j.status not in ("running", "orphaned"), -j.started))]

    def tail(self, job: Job, limit: int = 4000) -> dict[str, Any]:
        """The last `limit` chars of a job's output. Reads the buffer directly, so the model's poll cursor stays put."""
        out = {**job.info(), "output": _scrub(job.buf[-limit:]) if limit > 0 else ""}
        if job.status == "orphaned":
            out["note"] = "Started by an earlier run of the app: its output is gone. Kill stops it."
        return out

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a UI refresh must never fail a shell job
                pass

    def running_background(self, pool: str = "shell") -> int:
        return sum(1 for j in self.jobs.values() if j.background and j.pool == pool and j.status in ("running", "orphaned"))

    def drain_notes(self, conversation_id: str | None) -> list[str]:
        return self.notes.pop(conversation_id or "", [])

    # -- starting and finishing --
    async def start(self, argv: list[str], *, command: str, cwd: str, env: dict[str, str], tmp: str | None,
                    conversation_id: str | None, run_id: str | None, background: bool, notify: bool,
                    timeout: float | None, max_background: int, on_timeout: str = "kill", egress_token: str | None = None,
                    pool: str = "shell") -> Job:
        if background and self.running_background(pool) >= max_background:
            if pool != "shell":
                raise ShellError(f"{max_background} {pool} jobs are already running; wait for one to finish or stop one.")
            raise ShellError(f"{max_background} background jobs are already running (shellMaxBackground). "
                             "shell_poll or shell_kill one first.")
        self._make_room()
        job = Job(new_id(), command, cwd, conversation_id, run_id, background, notify and background,
                  BG_BUFFER if background else FG_CAPTURE)
        job.tmp = tmp
        job.want_notify = notify   # a foreground job that is later promoted announces its end like a background one
        job.on_timeout, job.max_background, job.egress_token, job.pool = on_timeout, max_background, egress_token, pool
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=1 << 20)
        except OSError as e:
            self._cleanup(job)
            raise ShellError(f"could not start the shell: {e}") from e
        job.pid = job.proc.pid
        job.pgid = job.pid  # start_new_session makes the child its own group leader
        self.jobs[job.id] = job
        self._persist()
        self._changed()
        job.pump = asyncio.create_task(self._pump(job))
        job.watch = asyncio.create_task(self._watch(job, timeout))
        return job

    async def _pump(self, job: Job) -> None:
        assert job.proc and job.proc.stdout
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = await job.proc.stdout.read(65536)
                if not chunk:
                    job.append(dec.decode(b"", final=True))
                    break
                job.append(dec.decode(chunk))
        except (asyncio.CancelledError, ValueError):
            pass

    async def _watch(self, job: Job, timeout: float | None) -> None:
        """`timeout` None waits for the process to exit, however long that takes (a coding session)."""
        assert job.proc
        timed_out = False
        try:
            try:
                await asyncio.wait_for(_exited(job.proc), timeout)
            except asyncio.TimeoutError:
                if not self._promote(job):
                    raise
                # Out of foreground time with the work unfinished: it carries on as a background job for the usual
                # background lifetime instead of losing everything it did.
                await asyncio.wait_for(_exited(job.proc), BACKGROUND_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(job)
        except asyncio.CancelledError:
            raise
        # The leader is gone; drain what it wrote, then end any stragglers it left in the group.
        try:
            await asyncio.wait_for(asyncio.shield(job.pump), 1.0)  # type: ignore[arg-type]
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        _signal_group(job.pgid, signal.SIGKILL)  # background too: once the leader exits nothing could reach a leftover child
        if job.pump and not job.pump.done():
            job.pump.cancel()
        job.exit_code = job.proc.returncode
        if job.status == "running":
            job.status = "timed_out" if timed_out else "exited"
        job.finished = time.time()
        self._cleanup(job)
        self._persist()
        self._changed()
        job.promoted.set()
        if job.notify and not job.notified and job.status != "killed":
            job.notified = True
            tail, _ = truncate(job.buf[-2000:])
            self.notes.setdefault(job.conversation_id or "", []).append(
                f"Background shell job {job.id} finished ({job.status}, exit code {job.exit_code}). "
                f"Last output:\n{_scrub(tail)}")
            if self.on_note:
                try:
                    self.on_note(job.conversation_id)
                except Exception:  # noqa: BLE001 - a wake-up that fails must not lose the note
                    pass

    def _promote(self, job: Job) -> bool:
        """Turn a foreground job that hit its timeout into a background one, if the caller allowed it and a slot is free."""
        if job.background or job.on_timeout != "background" or self.running_background(job.pool) >= job.max_background:
            return False
        job.background, job.notify, job.cap = True, job.want_notify, BG_BUFFER
        job.promoted.set()
        return True

    async def _terminate(self, job: Job) -> None:
        """SIGTERM to the whole group, SIGKILL after TERM_GRACE."""
        _signal_group(job.pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(_exited(job.proc), TERM_GRACE)  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            pass
        _signal_group(job.pgid, signal.SIGKILL)
        try:
            await asyncio.wait_for(_exited(job.proc), 2.0)  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            pass

    def _cleanup(self, job: Job) -> None:
        if job.tmp:
            try:  # the wrapper (shell_run) leaves the folder the command ended in here; read it before the folder goes
                job.end_cwd = (Path(job.tmp) / CWD_FILE).read_text().strip() or None
            except OSError:
                pass
            shutil.rmtree(job.tmp, ignore_errors=True)
            job.tmp = None
        if job.egress_token:  # the run is over: its proxy credentials stop working, and what it did is kept
            job.net = self.egress.revoke(job.egress_token)
            job.egress_token = None

    def net_view(self, job: Job) -> dict[str, list[str]] | None:
        if job.net is not None:
            return job.net
        return self.egress.stats(job.egress_token) if job.egress_token else None

    async def wait(self, job: Job) -> None:
        """Until the job ends, or until it is promoted to the background (then the caller reports it as running)."""
        if not job.watch:
            return
        gate = asyncio.ensure_future(job.promoted.wait())
        try:
            await asyncio.wait([job.watch, gate], return_when=asyncio.FIRST_COMPLETED)
        finally:
            gate.cancel()

    # -- the model's verbs --
    async def kill(self, job: Job) -> str:
        if job.status == "orphaned":
            if _pgid_of(job.pid or 0) == job.pgid:
                _signal_group(job.pgid, signal.SIGTERM)
                await asyncio.sleep(TERM_GRACE if _pgid_of(job.pid or 0) == job.pgid else 0)
                _signal_group(job.pgid, signal.SIGKILL)
            job.status, job.finished = "killed", time.time()
            self._persist()
            self._changed()
            return job.status
        if job.live():  # _watch publishes the end
            job.status = "killed"
            await self._terminate(job)
            if job.watch:
                try:
                    await asyncio.wait_for(asyncio.shield(job.watch), 5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            job.status = "killed"
        return job.status

    def poll(self, job: Job, limit: int = TRUNC_BYTES) -> dict[str, Any]:
        """Output since the last poll, at most `limit` chars of it; `more` says whether a poll would return more."""
        out: dict[str, Any] = {"job_id": job.id, "status": job.status, "exit_code": job.exit_code,
                               "cwd": _scrub(str(job.cwd or ""))}
        if job.status == "orphaned":
            out["note"] = ("This job was started by an earlier run of the app. Its output is gone and it cannot be adopted; "
                           "shell_kill stops it.")
            return out
        if job.read_pos < job.base:
            out["dropped_chars"] = job.base - job.read_pos
            job.read_pos = job.base
        start = job.read_pos - job.base
        text = job.buf[start:start + limit]
        job.read_pos += len(text)
        out["output"] = _scrub(text)
        out["more"] = job.read_pos < job.total
        return out

    def finished_output(self, job: Job) -> str:
        return job.buf

    async def kill_conversation(self, conversation_id: str | None, run_id: str | None = None) -> int:
        """End every live job a conversation's run started. Called when its run ends. With `run_id`, a job another run owns
        (a worker's, which outlives the reply) is left alone; one with no owner recorded still ends."""
        mine = [j for j in self.jobs.values() if j.live() and j.conversation_id == conversation_id
                and (run_id is None or j.run_id in (None, run_id))]
        for j in mine:
            await self.kill(j)
        return len(mine)

    async def kill_run(self, run_id: str) -> int:
        """End every live job one run started (a worker that ended, however it ended), together. A coding session's
        OpenCode job (conversation `coding:<id>`) is not one of them: it outlives a normal finish, and a stop ends it
        through codingagents.CodingSessions.stop_owned, which records why."""
        mine = [j for j in self.jobs.values() if run_id and j.live() and j.run_id == run_id
                and not (j.conversation_id or "").startswith("coding:")]
        await asyncio.gather(*(self.kill(j) for j in mine), return_exceptions=True)
        return len(mine)

    async def wait_output(self, job: Job, wait_s: float, stop: Any = None) -> None:
        """Until the job has output the model has not read, or ends, or `wait_s` passes (or `stop` is set)."""
        deadline = time.monotonic() + max(0.0, min(float(wait_s), POLL_WAIT_MAX))
        while job.live() and job.read_pos >= job.total and time.monotonic() < deadline and not (stop is not None and stop.is_set()):
            await asyncio.sleep(0.25)

    async def shutdown(self) -> None:
        for j in list(self.jobs.values()):
            if j.live():
                j.status = "killed"
                _signal_group(j.pgid, signal.SIGTERM)
        await asyncio.sleep(0.2)
        for j in list(self.jobs.values()):
            if j.status == "killed":
                _signal_group(j.pgid, signal.SIGKILL)
            self._cleanup(j)
        await self.egress.close()
        self._persist()

    def sweep_spills(self, db: Any) -> int:
        """Full outputs older than SPILL_DAYS leave the tool_results table. At most once an hour."""
        t = time.time()
        if t - self._last_sweep < 3600:
            return 0
        self._last_sweep = t
        try:
            with db.tx() as c:
                return c.execute("DELETE FROM tool_results WHERE tool='shell_run' AND created_at < ?",
                                 (t - SPILL_DAYS * 86400,)).rowcount
        except Exception:  # noqa: BLE001 - housekeeping must never fail a command
            return 0


# ---- the tools ----
def register(tb: Any) -> None:
    """Add shell_run / shell_poll / shell_kill to a Toolbox (group `shell`)."""
    from .tools import ToolSpec, _obj, tool_error

    state = None
    data_dir = getattr(getattr(getattr(tb, "results", None), "db", None), "data_dir", None)
    if data_dir:
        state = Path(data_dir) / "shell_jobs.json"
    tb.shell = ShellJobs(state)
    jobs: ShellJobs = tb.shell
    R = tb.specs.__setitem__

    def cfg(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("settings") or tb.settings()

    def desk_root(ctx: dict[str, Any]) -> Path | None:
        did = str(ctx.get("desk_id") or "")
        if did and getattr(tb, "workspace", None) is not None:
            try:
                return tb.workspace.ensure(did)
            except Exception:  # noqa: BLE001
                return None
        return None

    def _note_shell_copies(ctx: dict[str, Any], since_ns: int) -> None:
        """A download copied by a shell command stays a download, same as a script or fs_copy."""
        did = str(ctx.get("desk_id") or "")
        ws = getattr(tb, "workspace", None)
        if not did or ws is None or since_ns <= 0:
            return
        try:
            if not ws.fetched_paths(did):
                return
            dr = desk_root(ctx)
            if dr is None:
                return
            from .sandbox import WORKSPACE_REPORT_CAP, _workspace_changes
            rels = _workspace_changes(str(dr), since_ns)
            # A rename keeps the file's mtime, so `rels` can be empty while the download's old path is gone.
            ws.carry_fetch_copies(did, None if len(rels) >= WORKSPACE_REPORT_CAP else rels)
        except Exception:  # noqa: BLE001 - recording a copy must not fail the command that already ran
            return

    def hint(ctx: dict[str, Any]) -> str:
        # Only point at a researcher when this caller may spawn one (a child at the depth cap may not).
        modes = ctx.get("modes")
        can_spawn = "agent_spawn" in tb.specs and (modes is None or modes.get("agent_spawn", "off") != "off")
        extra = ", or agent_spawn a researcher to read it" if can_spawn else ""
        return ("Output was cut to the last 2000 lines / 50 KB. Page through all of it with read_tool_result"
                f"(result_id, offset), search the files it wrote with fs_grep{extra}.")

    async def shell_run(ctx: dict[str, Any], command: str, cwd: str | None = None, timeout_s: int | None = None,
                        background: bool = False, notify_on_complete: bool = True, unsandboxed: bool = False,
                        on_timeout: str = "background") -> Any:
        s = cfg(ctx)
        command = str(command or "")
        if ctx.get("proposal_only"):
            return tool_error("shell_run runs commands on this Mac and this is an unattended background run, so it cannot "
                              "run here.", alternative="describe the command in your report for the user to run")
        if not command.strip():
            return tool_error("shell_run needs a command.", field="command", example={"command": "ls -la"})
        if on_timeout not in ("background", "kill"):
            return tool_error("on_timeout must be 'background' or 'kill'.", field="on_timeout", example={"on_timeout": "kill"})
        # A chat's background jobs end with its reply (only a desk's outlive a turn), so in a chat a timeout kills.
        in_desk = bool(ctx.get("desk_id"))
        if not in_desk:
            on_timeout = "kill"
        # Where it runs: an explicit cwd wins; otherwise where the last command in this conversation ended, when that
        # folder is still there, otherwise the default folder (the desk workspace, else the home folder).
        dr = desk_root(ctx)
        default = dr or mac.home()
        try:
            where = resolve_cwd(cwd, default, dr)
            if not (cwd and str(cwd).strip()) and remembered_cwd(ctx.get("conversation_id")):
                try:
                    where = resolve_cwd(remembered_cwd(ctx.get("conversation_id")), default, dr)
                except ShellError:
                    pass
        except ShellError as e:
            return tool_error(_scrub(str(e)))
        network = bool(permissions.get(s, "shellNetwork"))
        usable = sandbox_available()
        if not unsandboxed and not usable:
            return tool_error("The OS sandbox this shell runs in is not available here (it needs macOS sandbox-exec), so "
                              "nothing was run.", alternative="run_python for a calculation, or retry with "
                              "unsandboxed=true, which asks the user for approval on every call")
        default_t = BACKGROUND_TIMEOUT if background else int(s.get("shellTimeoutSec") or DEFAULT_TIMEOUT)
        try:
            timeout = max(1, min(int(timeout_s or default_t), MAX_TIMEOUT))
        except (TypeError, ValueError):
            timeout = default_t
        if getattr(tb, "results", None) is not None:
            jobs.sweep_spills(tb.results.db)
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="pos-shell-"))
        shell_bin = "/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/sh"
        # An EXIT trap records where the shell ended without touching the exit code or the output, even when the command
        # itself calls `exit`; the next call starts there. Nothing is parsed or rewritten in the command.
        wrapped = f"trap {shlex.quote('pwd -P >' + shlex.quote(os.path.join(tmp, CWD_FILE)) + ' 2>/dev/null')} EXIT\n{command}"
        argv = [shell_bin, "-c", wrapped]
        env = scrubbed_env(tmp)
        allowed = egress.allowed_set(permissions.get(s, "shellRegistryAccess"), permissions.get(s, "shellAllowedDomains"),
                                     permissions.get(s, "allowAllConnections"))
        proxied = bool(allowed) and not network and not unsandboxed   # the third network mode: only the proxy is reachable
        token: str | None = None
        port: int | None = None
        if proxied:
            try:
                port = await jobs.egress.ensure()
            except OSError as e:
                log.warning("egress proxy could not start: %s", e)
                proxied = False  # no proxy means no network, which is the safe fallback
            else:
                token = jobs.egress.new_run(allowed)
                env.update(jobs.egress.env(token))
        if not unsandboxed:
            writable = [tmp]  # the profile lets a command write anywhere but the protected places; this is the run's own scratch dir
            if dr:
                writable.append(str(dr))  # a desk workspace sits inside the protected data folder and is let back in
            argv = ["sandbox-exec", "-p", sandbox.shell_profile(writable, network=network, proxy_port=port if proxied else None), *argv]
        if unsandboxed:  # the approval log has the call; this line is for the backend log (no card under Allow everything)
            log.warning("shell_run unsandboxed in %s (conversation %s): %s", _scrub(str(where)), ctx.get("conversation_id"),
                        _scrub(command)[:500])
        if network or unsandboxed:
            # Whatever a networked or unconfined command prints may be third-party text.
            taint(ctx, "shell_run:unsandboxed" if unsandboxed else "shell_run:network")
        since_ns = time.time_ns()
        try:
            job = await jobs.start(argv, command=command, cwd=str(where), env=env, tmp=tmp,
                                   conversation_id=ctx.get("conversation_id"), run_id=ctx.get("run_id"),
                                   background=bool(background), notify=bool(notify_on_complete), timeout=timeout,
                                   max_background=int(s.get("shellMaxBackground") or 4), on_timeout=on_timeout, egress_token=token)
            job.since_ns = since_ns
        except ShellError as e:
            jobs.egress.revoke(token)
            shutil.rmtree(tmp, ignore_errors=True)
            return tool_error(_scrub(str(e)))
        base: dict[str, Any] = {"cwd": _scrub(str(where)), "sandboxed": not unsandboxed, "network": bool(network or unsandboxed)}
        if proxied:
            base["network"] = {"mode": "allowlist", "contacted": [], "blocked": []}

        def net_report(res: dict[str, Any]) -> None:
            """Fold what the proxy saw into the result: contacted hosts taint the reply like open network does; blocked ones
            get a note saying how to allow them."""
            if not proxied:
                return
            seen = jobs.net_view(job) or {"contacted": [], "blocked": []}
            res["network"] = {"mode": "allowlist", **seen}
            if seen["contacted"]:
                taint(ctx, "shell_run:network")
            if seen["blocked"]:
                res["note"] = (str(res.get("note", "")) + " " + net_blocked_note(seen["blocked"])).strip()

        if background:
            return {"job_id": job.id, "pid": job.pid, "background": True, **base,
                    "note": "Running in the background. shell_poll(job_id) reads new output (wait_s=120 waits for some instead of "
                            "sleeping); shell_kill(job_id) stops it."
                            + (" You are told when it finishes." if job.notify and in_desk else "")
                            + ("" if in_desk else " It is stopped when this reply ends, so finish with it before replying.")}
        t0 = time.time()
        try:
            await jobs.wait(job)
        except asyncio.CancelledError:  # the reply was stopped: do not leave the command running behind it
            await jobs.kill(job)
            raise
        text = _scrub(jobs.finished_output(job))
        if job.background and job.live():
            # Hit its timeout and carries on as a background job: hand back what it printed so far and how to follow it.
            job.read_pos = job.total
            shown, cut = truncate(text)
            res: dict[str, Any] = {"exit_code": None, "output": shown, "truncated": cut, "timed_out": False, "still_running": True,
                                   "job_id": job.id, "pid": job.pid, "background": True, "duration_s": round(time.time() - t0, 2),
                                   **base,
                                   "note": f"Still running after {timeout}s, so it carries on in the background (job_id {job.id}). "
                                           "shell_poll(job_id, wait_s=120) waits for new output (never sleep in a shell to wait), "
                                           "shell_kill(job_id) stops it"
                                           + ("; you are told when it finishes." if job.notify else ".")}
            net_report(res)
            _note_shell_copies(ctx, since_ns)
            return res
        if not unsandboxed and job.exit_code in (65, 71) and text.lstrip().startswith("sandbox-exec:"):
            jobs.sandbox_failed = True
            return tool_error(_scrub("The OS sandbox refused to start (" + text.strip()[:200] + "), so nothing was run."),
                              alternative="retry with unsandboxed=true, which asks the user for approval on every call")
        shown, cut = truncate(text)
        wall = None if unsandboxed else wall_note(text, network)
        if job.end_cwd:
            try:
                ended = resolve_cwd(job.end_cwd, default, dr)
            except ShellError:
                ended = None  # it ended somewhere it may not run (or the folder is gone): the next call starts from the default folder again
            if ended:
                _remember_cwd(ctx.get("conversation_id"), str(ended))
                base["cwd"] = _scrub(str(ended))
            else:
                _LAST_CWD.pop(ctx.get("conversation_id") or "", None)
        out: dict[str, Any] = {"exit_code": job.exit_code, "output": shown, "truncated": cut,
                               "timed_out": job.status == "timed_out", "duration_s": round(time.time() - t0, 2), **base}
        if job.status == "timed_out":
            out["note"] = f"Killed after {timeout}s (the whole process group). Use background=true for long-running work."
        if wall:
            out["sandbox_blocked"] = wall
        net_report(out)
        if cut:
            out["note"] = (out.get("note", "") + " " + hint(ctx)).strip()
            if getattr(tb, "results", None) is not None and ctx.get("conversation_id"):
                row = tb.results.store(ctx["conversation_id"], ctx.get("message_id"), "shell_run", text,
                                       {"type": "string", "chars": len(text)})
                out["result_id"] = row["id"]
        _note_shell_copies(ctx, since_ns)
        return out
    spec = ToolSpec("shell_run", "Run a shell command (zsh) on this Mac. It is sandboxed by the OS: it can read and write anywhere "
                    "on this Mac except Grain's own data folder and app, credential stores (~/.ssh, keychains, browser cookies and "
                    "passwords, .env files) and the files that run code later (shell rc files, git hooks, launch agents); the network is "
                    "open only if the user enabled it, otherwise limited to package registries and the user's allowed domains "
                    "through a proxy (anything else is blocked), or off. macOS-protected folders (Desktop, Documents, Downloads...) need "
                    "Full Disk Access in System Settings. cwd is any folder on this Mac; by default it is where the last command in this "
                    "conversation ended (cd persists, environment variables do not), else the desk workspace in a desk, else the home folder. Output is stdout and stderr together, cut to the last 2000 lines / 50 KB; the rest is "
                    "behind result_id. Default timeout 120s (max 600s); then the command keeps running as a background job "
                    "(on_timeout=background, the default; poll it with shell_poll) or, with on_timeout=kill, the whole process "
                    "group is killed. For anything long-running pass background=true, then shell_poll and shell_kill with the job_id. "
                    "Outside a desk, background jobs are stopped when the reply ends and a timeout always kills. "
                    "Sandbox limits: " + SANDBOX_LIMITS + ". When a command needs one of those, plan around it or pass unsandboxed=true for "
                    "that one command: it escapes the sandbox and asks the user first (Allow everything runs it without asking). "
                    "A blocked command returns sandbox_blocked with the OS error and the rule that caused it; report that wall to "
                    "the user instead of retrying blindly.",
                    _obj({"command": {"type": "string"}, "cwd": {"type": "string", "description": "Any folder on this Mac; relative to the default folder"},
                          "timeout_s": {"type": "integer", "default": 120}, "background": {"type": "boolean", "default": False},
                          "notify_on_complete": {"type": "boolean", "default": True},
                          "unsandboxed": {"type": "boolean", "default": False},
                          "on_timeout": {"type": "string", "enum": ["background", "kill"], "default": "background"}}, ["command"]),
                    shell_run, "shell", "executes",
                    examples=[{"command": "ls -la"}, {"command": "python3 make_report.py && ls outputs"},
                              {"command": "npm test", "cwd": "app", "timeout_s": 300},
                              {"command": "python3 -m http.server 8000", "background": True}])
    spec.default = "ask"
    # Unsandboxed asks (Allow everything lifts it, autoreview.route); so does a reply that read untrusted content while a command could reach out, and no
    # standing grant, session grant or allow rule buys that card off (it is forced). trustExternalContent lifts only the
    # taint half: an unsandboxed command still asks.
    spec.force_ask = lambda args, ctx: bool(args.get("unsandboxed")) or (
        bool(ctx.get("tainted")) and reaches_out(cfg(ctx))
        and not permissions.get(cfg(ctx), "trustExternalContent"))
    spec.force_card = lambda args, ctx: bool(args.get("unsandboxed"))  # a sandbox escape is a hard card: Auto's reviewer may not lift it
    R("shell_run", spec)

    async def shell_poll(ctx: dict[str, Any], job_id: str, wait_s: float = 0) -> Any:
        job = jobs.get(str(job_id), ctx.get("conversation_id"))
        if not job:
            return tool_error(_scrub(f"No shell job '{job_id}' in this conversation."), field="job_id",
                              alternative="start one with shell_run(background=true)")
        try:
            wait = float(wait_s or 0)
        except (TypeError, ValueError):
            return tool_error("wait_s must be a number of seconds.", field="wait_s", example={"job_id": job.id, "wait_s": 120})
        if wait > 0:
            await jobs.wait_output(job, wait, ctx.get("stop"))
        out = jobs.poll(job)
        seen = jobs.net_view(job)
        if seen is not None:
            out["network"] = {"mode": "allowlist", **seen}
            if seen["contacted"]:
                taint(ctx, "shell_run:network")
            if seen["blocked"]:
                out["note"] = (str(out.get("note", "")) + " " + net_blocked_note(seen["blocked"])).strip()
        _note_shell_copies(ctx, int(getattr(job, "since_ns", 0) or 0))
        return out
    R("shell_poll", ToolSpec("shell_poll", "Read the new output of a background shell job and whether it is still running "
                             "(status running | exited | timed_out | killed | orphaned, and the exit code once it ends). "
                             "Each call returns only what is new since the last one. To wait for a job, pass wait_s (at most "
                             f"{POLL_WAIT_MAX}): the call returns as soon as there is new output or the job ends. Never run "
                             "`sleep` in shell_run to wait for a job.",
                             _obj({"job_id": {"type": "string"},
                                   "wait_s": {"type": "number", "default": 0,
                                              "description": f"Wait up to this many seconds (max {POLL_WAIT_MAX}) for new output or the end"}},
                                  ["job_id"]), shell_poll, "shell", "safe",
                             examples=[{"job_id": "a1b2c3"}, {"job_id": "a1b2c3", "wait_s": 120}]))

    async def shell_kill(ctx: dict[str, Any], job_id: str) -> Any:
        job = jobs.get(str(job_id), ctx.get("conversation_id"))
        if not job:
            return tool_error(_scrub(f"No shell job '{job_id}' in this conversation."), field="job_id")
        status = await jobs.kill(job)
        _note_shell_copies(ctx, int(getattr(job, "since_ns", 0) or 0))
        return {"job_id": job.id, "status": status, "exit_code": job.exit_code}
    R("shell_kill", ToolSpec("shell_kill", "Stop a background shell job: SIGTERM to its whole process group, SIGKILL if it "
                             "has not exited after 3 seconds.", _obj({"job_id": {"type": "string"}}, ["job_id"]),
                             shell_kill, "shell", "writes", examples=[{"job_id": "a1b2c3"}]))


# ---- fixed commands with no model in the loop (ship.py, codingagents.py; `label` names the caller in the job registry) ----
async def run_fixed(jobs: ShellJobs, argv: list[str], cwd: str, settings: dict[str, Any], *, sandboxed: bool,
                    timeout: float, label: str = "ship", extra_env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Run one argv through the shell job registry and wait for it: (exit code 0, scrubbed output).

    sandboxed=True is shell_run's sandbox: writes only in `cwd` and a private tmp dir, network only when shellNetwork is
    on (no allowlist proxy here). sandboxed=False is for the caller's own fixed git/gh argv: they need the user's network
    and credentials. The scrubbed environment still applies, plus the ssh agent socket and no interactive prompts. The
    repo's git hooks and config cannot have been planted by the agent: the sandbox denies writes to .git/hooks,
    .git/config and .husky."""
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="pos-ship-"))
    env = scrubbed_env(tmp)
    if sandboxed:
        if not sandbox_available():
            shutil.rmtree(tmp, ignore_errors=True)
            raise ShellError("The OS sandbox is not available here (it needs macOS sandbox-exec), so nothing was run.")
        net = bool(permissions.get(settings, "shellNetwork"))
        argv = ["sandbox-exec", "-p", sandbox.shell_profile([cwd, tmp], network=net), *argv]
    else:
        env.update(GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
        if os.environ.get("SSH_AUTH_SOCK"):
            env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    if extra_env:
        env.update(extra_env)
    job = await jobs.start(argv, command=shlex.join(argv[-3:] if sandboxed else argv), cwd=cwd, env=env, tmp=tmp,
                           conversation_id=label, run_id=None, background=False, notify=False, timeout=timeout,
                           max_background=int(settings.get("shellMaxBackground") or 4), on_timeout="kill")
    try:
        await jobs.wait(job)
    except asyncio.CancelledError:  # the checklist was cancelled: do not leave the command running behind it
        await jobs.kill(job)
        raise
    out = _scrub(jobs.finished_output(job))
    if job.status == "timed_out":
        out += f"\n[killed after {int(timeout)}s]"
    return job.status == "exited" and job.exit_code == 0, out
