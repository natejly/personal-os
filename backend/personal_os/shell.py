"""A host shell for the agent, confined to a workspace by the OS.

`shell_run` runs one command in /bin/zsh on the user's machine under macOS Seatbelt (sandbox.shell_profile): the
whole disk is readable except secrets, writes land only in the folder the command runs in (a desk workspace or a
granted `workspaceRoots` entry) and a private temp dir, and the network is off unless `shellNetwork` is set. The
sandbox is the boundary, the approval card is the courtesy: nothing here relies on parsing the command.

If the sandbox is unavailable (no sandbox-exec, another OS, or the profile fails to apply) the call is refused. The
only way past that is `unsandboxed=true`, which Toolbox.gate turns into a forced approval no grant can buy off.

Background commands return a job id; shell_poll reads new output, shell_kill stops one. Jobs belong to the
conversation that started them, die with its run and with the app, and are never adopted after a restart: the
registry persists pid/pgid so a survivor shows up as `orphaned` with a kill, nothing more.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import redact, sandbox
from .db import new_id

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
BACKGROUND_TIMEOUT = 600
TERM_GRACE = 3.0                       # SIGTERM to the group, then SIGKILL after this long
TRUNC_LINES, TRUNC_BYTES = 2000, 50_000
FG_CAPTURE = 5_000_000                 # the most one foreground command's output keeps (the spill behind the handle)
BG_BUFFER = 200_000                    # a background job's rolling buffer
MAX_TRACKED = 64
FINISHED_KEEP_S = 30 * 60
SPILL_DAYS = 7
SAFE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
NO_ROOT = ("shell_run needs a folder to work in: there is no desk workspace and no workspace root. Ask the user to add "
           "a folder under Settings (Workspace roots), or run it from a desk.")


class ShellError(Exception):
    pass


# Credential shapes only. The entropy and card rules would also eat file paths, commit hashes and build ids, which are
# most of what a shell prints, and the "word that announces a secret" sweep would eat ordinary source code.
SHELL_REDACT = ("private_key", "aws_key", "jwt", "token")


def _scrub(text: str) -> str:
    return redact.scrub(text, SHELL_REDACT, secret_assign=False)


# ---- paths ----
def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _inside(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def granted_roots(settings: dict[str, Any], desk_root: Path | None) -> list[Path]:
    """Desk workspace first, then each workspaceRoots entry that exists as an absolute folder."""
    out: list[Path] = [_real(desk_root)] if desk_root else []
    for r in settings.get("workspaceRoots") or []:
        if isinstance(r, str) and r.strip() and os.path.isabs(os.path.expanduser(r.strip())):
            p = _real(r.strip())
            if p.is_dir() and p not in out:
                out.append(p)
    return out


def resolve_cwd(cwd: str | None, roots: list[Path]) -> tuple[Path, Path]:
    """(cwd, the granted root that contains it). Raises ShellError with a message the model can act on."""
    if not roots:
        raise ShellError(NO_ROOT)
    if not cwd or not str(cwd).strip():
        return roots[0], roots[0]
    raw = Path(os.path.expanduser(str(cwd).strip()))
    p = _real(raw if raw.is_absolute() else roots[0] / raw)  # a relative cwd is relative to the default root
    for r in roots:
        if _inside(p, r):
            if not p.is_dir():
                raise ShellError(f"{p} is not a folder.")
            return p, r
    raise ShellError(f"{p} is outside the folders this shell may work in ({', '.join(str(r) for r in roots)}). "
                     "Ask the user to add it under Settings (Workspace roots).")


# ---- environment and output shaping ----
def scrubbed_env(tmp: str) -> dict[str, str]:
    """An allowlist, never the app's environment: API keys and tokens live there."""
    return {"PATH": SAFE_PATH, "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8", "TERM": "dumb",
            "TMPDIR": tmp, "NO_COLOR": "1"}


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
        self.finished: float | None = None
        self.tmp: str | None = None
        self.pump: asyncio.Task | None = None
        self.watch: asyncio.Task | None = None
        self.notified = False

    @property
    def total(self) -> int:
        return self.base + len(self.buf)

    def append(self, text: str) -> None:
        self.buf += text
        if len(self.buf) > self.cap:
            drop = len(self.buf) - self.cap
            self.buf, self.base = self.buf[drop:], self.base + drop

    def live(self) -> bool:
        return self.status == "running"

    def info(self) -> dict[str, Any]:
        return {"job_id": self.id, "pid": self.pid, "pgid": self.pgid, "cwd": self.cwd, "run_id": self.run_id,
                "conversation_id": self.conversation_id, "command": self.command[:200], "status": self.status,
                "exit_code": self.exit_code, "started": self.started}


class ShellJobs:
    """Every shell process this backend started: foreground ones for the length of the call, background ones until
    they finish, are killed, or age out."""

    def __init__(self, state_path: Path | None = None):
        self.jobs: dict[str, Job] = {}
        self.notes: dict[str, list[str]] = {}
        self.on_note: Any = None       # called with the conversation id when a completion note is queued (wakes an idle desk)
        self.sandbox_failed = False    # the OS refused to apply the profile once: unsandboxed may now be asked for
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

    def running_background(self) -> int:
        return sum(1 for j in self.jobs.values() if j.background and j.status in ("running", "orphaned"))

    def drain_notes(self, conversation_id: str | None) -> list[str]:
        return self.notes.pop(conversation_id or "", [])

    # -- starting and finishing --
    async def start(self, argv: list[str], *, command: str, cwd: str, env: dict[str, str], tmp: str | None,
                    conversation_id: str | None, run_id: str | None, background: bool, notify: bool,
                    timeout: float, max_background: int) -> Job:
        if background and self.running_background() >= max_background:
            raise ShellError(f"{max_background} background jobs are already running (shellMaxBackground). "
                             "shell_poll or shell_kill one first.")
        self._make_room()
        job = Job(new_id(), command, cwd, conversation_id, run_id, background, notify and background,
                  BG_BUFFER if background else FG_CAPTURE)
        job.tmp = tmp
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

    async def _watch(self, job: Job, timeout: float) -> None:
        assert job.proc
        timed_out = False
        try:
            await asyncio.wait_for(job.proc.wait(), timeout)
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
        if not job.background or timed_out:
            _signal_group(job.pgid, signal.SIGKILL)
        if job.pump and not job.pump.done():
            job.pump.cancel()
        job.exit_code = job.proc.returncode
        if job.status == "running":
            job.status = "timed_out" if timed_out else "exited"
        job.finished = time.time()
        self._cleanup(job)
        self._persist()
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

    async def _terminate(self, job: Job) -> None:
        """SIGTERM to the whole group, SIGKILL after TERM_GRACE."""
        _signal_group(job.pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(job.proc.wait(), TERM_GRACE)  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            pass
        _signal_group(job.pgid, signal.SIGKILL)
        try:
            await asyncio.wait_for(job.proc.wait(), 2.0)  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _cleanup(job: Job) -> None:
        if job.tmp:
            shutil.rmtree(job.tmp, ignore_errors=True)
            job.tmp = None

    async def wait(self, job: Job) -> None:
        if job.watch:
            await asyncio.shield(job.watch)

    # -- the model's verbs --
    async def kill(self, job: Job) -> str:
        if job.status == "orphaned":
            if _pgid_of(job.pid or 0) == job.pgid:
                _signal_group(job.pgid, signal.SIGTERM)
                await asyncio.sleep(TERM_GRACE if _pgid_of(job.pid or 0) == job.pgid else 0)
                _signal_group(job.pgid, signal.SIGKILL)
            job.status, job.finished = "killed", time.time()
            self._persist()
            return job.status
        if job.live():
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
        out: dict[str, Any] = {"job_id": job.id, "status": job.status, "exit_code": job.exit_code, "cwd": job.cwd}
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

    async def kill_conversation(self, conversation_id: str | None) -> int:
        """End every live job a conversation's run started. Called when its run ends."""
        mine = [j for j in self.jobs.values() if j.live() and j.conversation_id == conversation_id]
        for j in mine:
            await self.kill(j)
        return len(mine)

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

    def hint(ctx: dict[str, Any]) -> str:
        # Only point at a researcher when this caller may spawn one (a child at the depth cap may not).
        modes = ctx.get("modes")
        can_spawn = "agent_spawn" in tb.specs and (modes is None or modes.get("agent_spawn", "off") != "off")
        extra = ", or agent_spawn a researcher to read it" if can_spawn else ""
        return ("Output was cut to the last 2000 lines / 50 KB. Page through all of it with read_tool_result"
                f"(result_id, offset), search the files it wrote with fs_grep{extra}.")

    async def shell_run(ctx: dict[str, Any], command: str, cwd: str | None = None, timeout_s: int | None = None,
                        background: bool = False, notify_on_complete: bool = True, unsandboxed: bool = False) -> Any:
        s = cfg(ctx)
        command = str(command or "")
        if ctx.get("proposal_only"):
            return tool_error("shell_run runs commands on this Mac and this is an unattended background run, so it cannot "
                              "run here.", alternative="describe the command in your report for the user to run")
        if not command.strip():
            return tool_error("shell_run needs a command.", field="command", example={"command": "ls -la"})
        try:
            where, root = resolve_cwd(cwd, granted_roots(s, desk_root(ctx)))
        except ShellError as e:
            return tool_error(str(e))
        network = bool(s.get("shellNetwork"))
        usable = sandbox_available()
        if unsandboxed and usable and not jobs.sandbox_failed:
            return tool_error("The OS sandbox is available, so this runs sandboxed; unsandboxed is only for a machine where "
                              "it cannot start.", alternative="run it again without unsandboxed")
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
        argv = [shell_bin, "-c", command]
        if not unsandboxed:
            writable = [str(root), tmp]
            dr = desk_root(ctx)
            if dr:
                writable.append(str(dr))
            argv = ["sandbox-exec", "-p", sandbox.shell_profile(writable, network=network), *argv]
        if network or unsandboxed:
            # Whatever a networked or unconfined command prints may be third-party text.
            ctx["tainted"] = True
            src = "shell_run:unsandboxed" if unsandboxed else "shell_run:network"
            if src not in ctx.setdefault("taint_sources", []):
                ctx["taint_sources"].append(src)
        try:
            job = await jobs.start(argv, command=command, cwd=str(where), env=scrubbed_env(tmp), tmp=tmp,
                                   conversation_id=ctx.get("conversation_id"), run_id=ctx.get("run_id"),
                                   background=bool(background), notify=bool(notify_on_complete), timeout=timeout,
                                   max_background=int(s.get("shellMaxBackground") or 4))
        except ShellError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            return tool_error(str(e))
        base = {"cwd": str(where), "sandboxed": not unsandboxed, "network": bool(network or unsandboxed)}
        if background:
            return {"job_id": job.id, "pid": job.pid, "background": True, **base,
                    "note": "Running in the background. shell_poll(job_id) reads new output; shell_kill(job_id) stops it."
                            + (" You are told when it finishes." if job.notify else "")}
        t0 = time.time()
        try:
            await jobs.wait(job)
        except asyncio.CancelledError:  # the reply was stopped: do not leave the command running behind it
            await jobs.kill(job)
            raise
        text = _scrub(jobs.finished_output(job))
        if not unsandboxed and job.exit_code in (65, 71) and text.lstrip().startswith("sandbox-exec:"):
            jobs.sandbox_failed = True
            return tool_error("The OS sandbox refused to start (" + text.strip()[:200] + "), so nothing was run.",
                              alternative="retry with unsandboxed=true, which asks the user for approval on every call")
        shown, cut = truncate(text)
        out: dict[str, Any] = {"exit_code": job.exit_code, "output": shown, "truncated": cut,
                               "timed_out": job.status == "timed_out", "duration_s": round(time.time() - t0, 2), **base}
        if job.status == "timed_out":
            out["note"] = f"Killed after {timeout}s (the whole process group). Use background=true for long-running work."
        if cut:
            out["note"] = (out.get("note", "") + " " + hint(ctx)).strip()
            if getattr(tb, "results", None) is not None and ctx.get("conversation_id"):
                row = tb.results.store(ctx["conversation_id"], ctx.get("message_id"), "shell_run", text,
                                       {"type": "string", "chars": len(text)})
                out["result_id"] = row["id"]
        return out
    spec = ToolSpec("shell_run", "Run a shell command (zsh) on this Mac inside the working folder. It is sandboxed by the OS: the "
                    "disk is readable except secrets, files can be written only inside the working folder, and there is no "
                    "network unless the user enabled it. cwd must be inside the desk workspace or a workspace root (default: "
                    "that folder). Output is stdout and stderr together, cut to the last 2000 lines / 50 KB; the rest is "
                    "behind result_id. Default timeout 120s (max 600s), then the whole process group is killed. For "
                    "anything long-running pass background=true, then shell_poll and shell_kill with the job_id. "
                    "unsandboxed=true escapes the sandbox and always asks the user.",
                    _obj({"command": {"type": "string"}, "cwd": {"type": "string", "description": "A folder inside the working folder"},
                          "timeout_s": {"type": "integer", "default": 120}, "background": {"type": "boolean", "default": False},
                          "notify_on_complete": {"type": "boolean", "default": True},
                          "unsandboxed": {"type": "boolean", "default": False}}, ["command"]),
                    shell_run, "shell", "executes",
                    examples=[{"command": "ls -la"}, {"command": "python3 make_report.py && ls outputs"},
                              {"command": "npm test", "cwd": "app", "timeout_s": 300},
                              {"command": "python3 -m http.server 8000", "background": True}])
    spec.default = "ask"
    spec.force_ask = lambda args: bool(args.get("unsandboxed"))
    R("shell_run", spec)

    async def shell_poll(ctx: dict[str, Any], job_id: str) -> Any:
        job = jobs.get(str(job_id), ctx.get("conversation_id"))
        if not job:
            return tool_error(f"No shell job '{job_id}' in this conversation.", field="job_id",
                              alternative="start one with shell_run(background=true)")
        return jobs.poll(job)
    R("shell_poll", ToolSpec("shell_poll", "Read the new output of a background shell job and whether it is still running "
                             "(status running | exited | timed_out | killed | orphaned, and the exit code once it ends). "
                             "Each call returns only what is new since the last one.",
                             _obj({"job_id": {"type": "string"}}, ["job_id"]), shell_poll, "shell", "safe",
                             examples=[{"job_id": "a1b2c3"}]))

    async def shell_kill(ctx: dict[str, Any], job_id: str) -> Any:
        job = jobs.get(str(job_id), ctx.get("conversation_id"))
        if not job:
            return tool_error(f"No shell job '{job_id}' in this conversation.", field="job_id")
        status = await jobs.kill(job)
        return {"job_id": job.id, "status": status, "exit_code": job.exit_code}
    R("shell_kill", ToolSpec("shell_kill", "Stop a background shell job: SIGTERM to its whole process group, SIGKILL if it "
                             "has not exited after 3 seconds.", _obj({"job_id": {"type": "string"}}, ["job_id"]),
                             shell_kill, "shell", "writes", examples=[{"job_id": "a1b2c3"}]))
