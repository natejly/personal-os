"""Coding sessions: hand a coding task to Claude Code or OpenCode on a repo (or a fresh git worktree of it) and follow it.

Two drivers behind one row (`coding_sessions`, migration 11):

- claude: `claude --bg` starts a background session that outlives this app. It runs OUTSIDE the OS sandbox with the
  user's own account and tools, and prompts for permission inside its own session: a prompt shows as state "blocked",
  which becomes `needs_you` here, and the user answers it with `claude attach <id>`. Progress is read from the plain
  files the CLI keeps under ~/.claude/jobs/<id>/ (state.json, timeline.jsonl), never by following the session's
  output. Those files are untrusted text. A finished session stays open in the daemon, so a follow-up runs in a copy
  with a new id that the row then follows; a stopped one is woken under the same id. The session's `--permission-mode`
  follows Grain's own mode (Auto -> auto, Allow all -> bypassPermissions, Manual -> no flag, so it prompts and shows
  needs_you) unless the caller names a permission_mode (acceptEdits, auto, dontAsk or bypassPermissions) for that one
  session. Only an explicit bypassPermissions under Auto or Manual forces a card the reviewer cannot lift.
- opencode: `opencode.launch` under the OS sandbox as a background job in the shell registry (shell.ShellJobs). It
  has no prompt to answer: the sandbox is its boundary. It runs until it exits or Stop ends it (no time limit: app shutdown
  kills every shell job group, and a session a crash orphaned is recorded as `orphaned`); a follow-up
  `--continue`s the same opencode state folder.

Every change is saved and published on the app `events` topic as a `coding_session` event carrying `summary(row)`.
Nothing here removes a session, force-pushes, or runs git beyond `worktree add`, `status`, `diff` and `log`.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import mac, opencode, shell
from .db import new_id
from .limits import CODING_SESSION_MAX_CONCURRENT, LOGIN_SHELL_TIMEOUT_SECONDS
from .ship import BRANCH_RE, PROTECTED

AGENTS = ("claude", "opencode")
PERMISSION_MODES = (None, "acceptEdits", "auto", "dontAsk", "bypassPermissions")
LIVE = ("starting", "working", "needs_you")
ATTENTION = {"starting": "working", "working": "working", "needs_you": "needs_you", "blocked": "blocked"}
LOG_TAIL = 4000
READ_CAP = 256 * 1024
START_TIMEOUT, GIT_TIMEOUT, RESUME_GRACE = 180.0, 60.0, 15.0
STAT_CAP, DIFF_CAP = 8_000, 60_000
CLAUDE_BINS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")
CLAUDE_HINT = "Claude Code is not installed on this Mac (no `claude` in ~/.local/bin, /opt/homebrew/bin or /usr/local/bin)."
ID_RE = re.compile(r"\b[0-9a-f]{8}\b")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,99}")
CLAUDE_STATES = {"working": "working", "running": "working", "blocked": "needs_you", "done": "done", "stopped": "stopped", "failed": "failed"}
JOB_STATES = {"running": "working", "killed": "stopped", "timed_out": "failed", "orphaned": "blocked", "failed": "failed"}
GIT = ["git", "-c", "core.fsmonitor=false"]  # a repo's own config must not be able to run a program on `git status`

# (argv, cwd, timeout, env=None) -> (ok, output): the app's lambda over shell.run_fixed, unsandboxed (the user's own account)
Runner = Callable[..., Awaitable[tuple[bool, str]]]


class CodingError(ValueError):
    pass


def cli_permission_mode(grain_mode: str, explicit: str | None) -> str | None:
    """The `--permission-mode` for a Claude Code session: the caller's explicit one, else Grain's mode mapped over."""
    return explicit or {"auto": "auto", "allow_all": "bypassPermissions"}.get(grain_mode)


def own_session_taint(sid: str, sources: Any) -> bool:
    """True when the run's taint came only from this coding session's own labels (so a follow-up to it is not untrusted-driven)."""
    srcs = [str(x) for x in sources or []]
    return bool(srcs) and all(
        x in (f"coding_session:{k}:{sid}" for k in ("start", "send", "stop", "status", "diff"))
        or (x.startswith("coding_session:list:") and all(i == sid for i in x[20:].split(",") if i)) for x in srcs)


def claude_binary() -> str | None:
    for d in CLAUDE_BINS:
        p = os.path.join(os.path.expanduser(d), "claude")
        if os.access(p, os.X_OK):
            return p
    return shutil.which("claude")


DAEMON_FILE = Path.home() / ".claude" / "daemon.json"   # read-only; tests point this at a temp file


def daemon_running(home: Path | None = None) -> bool:
    """True when the claude daemon recorded in <home or ~/.claude>/daemon.json is alive (signal 0 on its pid)."""
    try:
        pid = json.loads((Path(home) / "daemon.json" if home else DAEMON_FILE).read_text()).get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError, AttributeError):
        return False
    return True


@functools.cache
def login_path() -> str | None:
    """PATH as the user's login shell sets it, or None. ponytail: the first call blocks up to LOGIN_SHELL_TIMEOUT_SECONDS
    once (cached afterwards); a PATH edited later needs an app restart."""
    try:
        r = subprocess.run([os.environ.get("SHELL") or "/bin/zsh", "-lic", 'printf %s "$PATH"'], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=LOGIN_SHELL_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    path = (r.stdout or "").rsplit("\n", 1)[-1].strip()  # interactive rc files may print before the PATH
    return path if path and all(os.path.isabs(d) for d in path.split(":")) else None


def claude_env() -> dict[str, str]:
    """Extra environment for the claude CLI: the user's own PATH (their tools and hooks), and a TMPDIR that outlives the
    launcher (the registry deletes its per-call tmp dir when the command exits, but the background session keeps running).
    A daemon started now inherits this environment for good, so with none running it gets the login shell's PATH."""
    fresh = not daemon_running()
    path = login_path() if fresh else None
    if not path:
        extra = "".join(os.path.expanduser(d) + ":" for d in CLAUDE_BINS) if fresh else ""
        path = os.path.expanduser("~/.local/bin") + ":" + (os.environ.get("PATH") or "") + ":" + extra + shell.SAFE_PATH
    return {"PATH": path, "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir()}


def parse_job_id(out: str) -> str | None:
    """The short job id `claude --bg` prints (8 hex chars), or None. The id after "backgrounded" wins: when the CLI
    starts a copy, its note names the original first."""
    m = re.search(r"backgrounded\W+([0-9a-f]{8})\b", out or "") or ID_RE.search(out or "")
    return m.group(m.lastindex or 0) if m else None


def _lead(text: str) -> str:
    """A positional argument that starts with a dash would be read as a flag."""
    return f"Task: {text}" if text.startswith("-") else text


def claude_argv(exe: str, name: str, prompt: str, model: str | None = None, permission_mode: str | None = None) -> list[str]:
    """The start command. A permission flag appears only when the caller asked for a mode."""
    return [exe, "--bg", "-n", name, *(["--model", model] if model else []),
            *(["--permission-mode", permission_mode] if permission_mode else []), _lead(prompt)]


def resume_argv(exe: str, session_id: str, message: str) -> list[str]:
    return [exe, "--bg", "--resume", session_id, _lead(message)]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30].strip("-") or "task"


def check_branch(branch: str) -> None:
    """A plain branch name for the new worktree, never main/master."""
    if not BRANCH_RE.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith((".lock", "/", ".")) \
            or branch.startswith("refs/"):
        raise CodingError(f"'{branch}' is not a branch name a coding session accepts.")
    if branch.lower() in PROTECTED:
        raise CodingError(f"A coding session never works on {branch} directly: give a feature branch name.")


def worktree_path(repo: Path, branch: str) -> Path:
    return repo / ".claude" / "worktrees" / branch.rsplit("/", 1)[-1]


def _tail(path: Path, cap: int = READ_CAP) -> tuple[str, bool]:
    """The last `cap` bytes of a file as text, and whether the start was cut off."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - cap))
        data = f.read(cap)
    return data.decode("utf-8", "replace"), size > cap


def read_claude(home: Path, job_id: str, lines: int = 30) -> tuple[dict[str, Any] | None, list[str]]:
    """(state.json as a dict, the last `lines` timeline texts) for a claude job, or (None, []) when unreadable. Only the
    one folder named by an 8-hex id inside `home` is read, and symlinks that leave it are not followed."""
    if not ID_RE.fullmatch(job_id or ""):
        return None, []
    d = home / job_id
    try:
        if d.resolve().parent != home.resolve():
            return None, []
        text, cut = _tail(d / "state.json")
        state = None if cut else json.loads(text)
    except (OSError, ValueError):
        return None, []
    if not isinstance(state, dict):
        return None, []
    out: list[str] = []
    try:
        text, cut = _tail(d / "timeline.jsonl")
        raw = text.splitlines()[1 if cut else 0:][-lines:]
    except OSError:
        raw = []
    for line in raw:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("text") or ev.get("detail") if isinstance(ev, dict) else None
        if isinstance(t, str) and t.strip() and (not out or out[-1] != t.strip()):  # the CLI repeats the same detail many times in a row
            out.append(t.strip())
    return state, out


def map_claude(state: dict[str, Any]) -> tuple[str, str] | None:
    """(status, detail) for a claude state.json, or None for a state this code does not know."""
    status = CLAUDE_STATES.get(str(state.get("state")))
    if not status:
        return None
    needs = state.get("needs")  # set while "working" too (e.g. an approval pending), or with "blocked"
    needs = needs.strip() if isinstance(needs, str) else ""
    if needs and status == "working":
        status = "needs_you"
    detail = (needs if status == "needs_you" else None) or state.get("detail") or ""
    return status, shell._scrub(str(detail))[:300]


def map_job(job: Any) -> tuple[str, str]:
    """(status, detail) for an opencode job in the shell registry."""
    st = job.status
    if st == "exited":
        return ("done", "") if job.exit_code == 0 else ("failed", f"exited with code {job.exit_code}")
    if st == "orphaned":
        return "blocked", "the app restarted while it ran; Stop ends it"
    return JOB_STATES.get(st, "working"), ""


def summary(row: dict[str, Any], tail: int | None = 1500) -> dict[str, Any]:
    """The tool and REST view. `tail` is how many log chars to include (None = all)."""
    log = row.get("log_tail") or ""
    out = {"id": row["id"], "agent": row["agent"], "name": row["name"], "status": row["status"],
           "attention": ATTENTION.get(row["status"], "idle"), "detail": row.get("detail"), "repo_path": row["repo_path"],
           "worktree": row["worktree"], "branch": row.get("branch"), "external_id": row.get("external_id"),
           "model": row.get("model"), "permission_mode": row.get("permission_mode"), "created_at": row["created_at"],
           "updated_at": row["updated_at"], "ended_at": row.get("ended_at"),
           "log_tail": log if tail is None else (log[-tail:] if tail else "")}
    if row["agent"] == "claude" and row["status"] == "needs_you" and row.get("external_id"):
        out["attach_hint"] = f"claude attach {row['external_id']}"
    return out


class CodingSessions:
    def __init__(self, db: Any, jobs: shell.ShellJobs, run: Runner, publish: Callable[[str, Any], None],
                 settings: Callable[[], dict[str, Any]], tb: Any = None, claude_home: Path | None = None):
        self.db, self.jobs, self.run, self.publish, self.settings = db, jobs, run, publish, settings
        self.tb = tb                    # the Toolbox: opencode.launch needs its model settings, data dir and workspace
        self.claude_home = claude_home or Path.home() / ".claude" / "jobs"
        self.resumed: dict[str, float] = {}   # id -> when a follow-up started: a stale "done" in state.json is ignored for a moment
        self.seen: dict[str, tuple[int, str]] = {}   # id -> (output size, job status) at the last opencode refresh
        self._recover()

    # ---- rows ----
    def get(self, sid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM coding_sessions WHERE id = ?", (sid,)).fetchone()
        return dict(r) if r else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM coding_sessions ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                               (max(1, int(limit)),)).fetchall()]

    def _max_concurrent(self) -> int:
        try:
            return max(1, int(self.settings().get("codingSessionMaxConcurrent") or CODING_SESSION_MAX_CONCURRENT))
        except (TypeError, ValueError):
            return CODING_SESSION_MAX_CONCURRENT

    def _check_capacity(self) -> None:
        limit = self._max_concurrent()
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_sessions WHERE status IN ({','.join('?' * len(LIVE))})", LIVE)]
        n = sum(1 for r in rows if self.refresh(r)["status"] in LIVE)  # a session that ended unseen must not hold a slot
        if n >= limit:
            raise CodingError(f"{n} coding sessions are already running (codingSessionMaxConcurrent); wait for one to finish or stop one.")

    def _known(self, sid: str) -> dict[str, Any]:
        row = self.get(str(sid))
        if row is None:
            raise CodingError(f"No coding session '{sid}'.")
        return row

    def _save(self, row: dict[str, Any]) -> None:
        row["updated_at"] = time.time()
        with self.db.tx() as c:
            c.execute("UPDATE coding_sessions SET external_id=?, session_id=?, status=?, detail=?, log_tail=?, updated_at=?, "
                      "ended_at=? WHERE id=?", (row.get("external_id"), row.get("session_id"), row["status"], row.get("detail"),
                                                row.get("log_tail") or "", row["updated_at"], row.get("ended_at"), row["id"]))
        self.publish("coding_session", summary(row, None))

    def _apply(self, row: dict[str, Any], **kw: Any) -> dict[str, Any]:
        """Set fields and save, but only when something changed (a poll that sees nothing new publishes nothing)."""
        changed = {k: v for k, v in kw.items() if row.get(k) != v}
        if changed:
            row.update(changed)
            if "status" in changed:
                row["ended_at"] = None if row["status"] in LIVE else (row.get("ended_at") or time.time())
            self._save(row)
        return row

    def _recover(self) -> None:
        """After a restart: claude sessions re-read their files (they outlive the app); an opencode job that is gone
        is reported as blocked, because the registry never adopts a process."""
        for row in self.list(200):
            if row["status"] not in LIVE:
                continue
            if row["status"] == "starting" and not row.get("external_id"):
                self._apply(row, status="failed", detail="the app stopped while this session was starting")
            else:
                self.refresh(row)

    # ---- progress ----
    def refresh(self, row: dict[str, Any]) -> dict[str, Any]:
        """Re-read the driver's state and save when it changed. Reads files and the in-memory job registry only."""
        if row["agent"] == "claude":
            return self._refresh_claude(row) if row.get("external_id") and row["status"] != "failed" else row
        return self._refresh_opencode(row) if row["status"] in LIVE else row

    def _refresh_claude(self, row: dict[str, Any]) -> dict[str, Any]:
        state, lines = read_claude(self.claude_home, row["external_id"])
        if state is None:
            return self._apply(row, detail="state unavailable") if row["status"] in LIVE else row
        mapped = map_claude(state)
        if mapped is None:
            return row
        status, detail = mapped
        if status in ("done", "stopped") and time.monotonic() - self.resumed.get(row["id"], -1e9) < RESUME_GRACE:
            return row  # a follow-up was just sent and state.json has not caught up
        sid = state.get("sessionId")
        log = shell._scrub("\n".join(lines))[-LOG_TAIL:]
        return self._apply(row, status=status, detail=detail or row.get("detail"), log_tail=log or row.get("log_tail") or "",
                           session_id=sid if isinstance(sid, str) and UUID_RE.fullmatch(sid) else row.get("session_id"))

    def _refresh_opencode(self, row: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs.jobs.get(row.get("external_id") or "")
        if job is None:
            return self._apply(row, status="blocked", detail="the job record is gone (the app restarted, or it expired from the job registry); Stop clears it")
        if self.seen.get(row["id"]) == (job.total, job.status):
            return row  # nothing new since the last look: skip re-parsing the buffer
        self.seen[row["id"]] = (job.total, job.status)
        status, detail = map_job(job)
        raw = job.buf
        if job.live() and not raw.endswith("\n"):
            raw = raw[:raw.rfind("\n") + 1]  # the last line is still being written
        text, session = opencode.summarize(shell._scrub(raw))
        lines = text.splitlines()
        step = next((ln for ln in reversed(lines) if ln.startswith("[")), lines[-1] if lines else "")
        return self._apply(row, status=status, detail=(detail or step)[:300] or None, log_tail=text[-LOG_TAIL:],
                           session_id=row.get("session_id") or session)

    # ---- verbs ----
    def check_repo(self, repo_path: str, desk: Path | None = None) -> Path:
        p = str(repo_path or "").strip()
        if not p or not os.path.isabs(os.path.expanduser(p)):
            raise CodingError("repo_path must be an absolute path to the repository folder.")
        try:
            where = shell.resolve_cwd(p, desk or mac.home(), desk)
        except shell.ShellError as e:
            raise CodingError(shell._scrub(str(e))) from e
        if not (where / ".git").exists():
            raise CodingError(f"{where} is not a git repository (no .git).")
        return where

    @staticmethod
    def _text(value: Any, what: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise CodingError(f"{what} is empty.")
        if len(text) > opencode.MAX_PROMPT:
            raise CodingError(f"The {what} is over {opencode.MAX_PROMPT} characters; point the agent at a file instead.")
        return text

    async def start(self, agent: str, repo_path: str, prompt: str, *, new_worktree: bool = False, branch: str | None = None,
                    model: str | None = None, permission_mode: str | None = None, name: str | None = None,
                    ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = ctx if ctx is not None else {}
        if agent not in AGENTS:
            raise CodingError(f"agent must be one of {', '.join(AGENTS)}.")
        prompt = self._text(prompt, "prompt")
        permission_mode = permission_mode or None
        if permission_mode not in PERMISSION_MODES:
            raise CodingError("permission_mode must be acceptEdits, auto, dontAsk or bypassPermissions, or left out to follow Grain's permission mode.")
        if permission_mode and agent != "claude":
            raise CodingError("permission_mode is for Claude Code only: OpenCode already runs with every permission allowed "
                              "inside the OS sandbox.")
        model = (str(model).strip() or None) if model else None
        if model and not MODEL_RE.fullmatch(model):
            raise CodingError(f"'{model}' is not a model id this tool accepts.")
        repo = self.check_repo(repo_path, opencode._desk_root(self.tb, ctx))
        self._check_capacity()
        name = " ".join(str(name or prompt).split())[:60].lstrip("- ") or "coding session"
        sid, wt, want = new_id(), repo, branch
        branch = None
        if agent == "claude":  # the stored mode is what the CLI gets and what the UI shows
            from . import autoreview
            permission_mode = cli_permission_mode(ctx.get("permission_mode") or autoreview.mode_of(ctx.get("settings") or self.settings()),
                                                  permission_mode)
        if new_worktree:
            branch = str(want or "").strip() or f"grain/{_slug(name)}-{secrets.token_hex(2)}"
            check_branch(branch)
            if agent == "claude" and not claude_binary():  # fail before a worktree is created for nothing
                raise CodingError(CLAUDE_HINT)
            if agent == "opencode" and not opencode.binary():
                raise CodingError(opencode.INSTALL_HINT)
            wt = worktree_path(repo, branch)
            if wt.exists():
                raise CodingError(f"{wt} already exists. Pick another branch name.")
            ok, out = await self.run([*GIT, "worktree", "add", "-b", branch, str(wt)], str(repo), GIT_TIMEOUT)
            if not ok:
                raise CodingError(f"git could not create the worktree: {out.strip()[-300:]}")
        t = time.time()
        row = {"id": sid, "agent": agent, "external_id": None, "session_id": None, "repo_path": str(repo), "worktree": str(wt),
               "branch": branch, "conversation_id": ctx.get("conversation_id"), "desk_id": ctx.get("desk_id"),
               "run_id": ctx.get("run_id"), "name": name, "prompt": prompt, "model": model, "permission_mode": permission_mode,
               "status": "starting", "detail": None, "log_tail": "", "created_at": t, "updated_at": t, "ended_at": None}
        with self.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, branch, conversation_id, "
                      "desk_id, run_id, name, prompt, model, permission_mode, status, detail, log_tail, created_at, updated_at, "
                      "ended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, agent, None, None, row["repo_path"], row["worktree"], branch, row["conversation_id"], row["desk_id"],
                       row["run_id"], name, prompt, model, permission_mode, "starting", None, "", t, t, None))
        self.publish("coding_session", summary(row, None))
        shell.taint(ctx, f"coding_session:start:{sid}")  # the agent's text comes back through status, logs and diff
        try:
            if agent == "claude":
                await self._start_claude(row)
            else:
                await self._launch_opencode(row, ctx, prompt, False)
        except (CodingError, shell.ShellError) as e:
            return self._apply(row, status="failed", detail=shell._scrub(str(e))[:300])
        return self._apply(row, status="working", detail="started")

    async def _start_claude(self, row: dict[str, Any]) -> None:
        exe = claude_binary()
        if not exe:
            raise CodingError(CLAUDE_HINT)
        ok, out = await self.run(claude_argv(exe, row["name"], row["prompt"], row["model"], row["permission_mode"]),
                                 row["worktree"], START_TIMEOUT, await asyncio.to_thread(claude_env))
        if not ok:
            raise CodingError(f"claude did not start: {out.strip()[-300:]}")
        jid, sess = parse_job_id(out), None
        if not jid:  # no id in the output: look for the session this start created
            jid, sess = await self._find_agent(exe, row)
        if not jid:
            raise CodingError(f"claude started but printed no session id: {out.strip()[-300:]}")
        state, _ = read_claude(self.claude_home, jid)
        if state is None:  # the printed id has no job folder: prefer the id `claude agents` lists for this start
            jid = (await self._find_agent(exe, row))[0] or jid
            state, _ = read_claude(self.claude_home, jid)
        sess = (state or {}).get("sessionId") or sess
        if not (isinstance(sess, str) and UUID_RE.fullmatch(sess)):
            _, sess = await self._find_agent(exe, row, jid)
        row["external_id"], row["session_id"] = jid, sess if isinstance(sess, str) and UUID_RE.fullmatch(sess) else None

    async def _find_agent(self, exe: str, row: dict[str, Any], jid: str | None = None) -> tuple[str | None, str | None]:
        """(short id, sessionId) from `claude agents --json --all` (completed sessions too): the entry with this id, else
        the newest one for this worktree and name."""
        ok, out = await self.run([exe, "agents", "--json", "--all"], row["worktree"], GIT_TIMEOUT, await asyncio.to_thread(claude_env))
        try:
            agents = json.loads(out[out.index("["):]) if ok and "[" in out else []
        except ValueError:
            agents = []
        mine = [a for a in agents if isinstance(a, dict) and ID_RE.fullmatch(str(a.get("id") or "")[:8])
                and ((str(a["id"])[:8] == jid) if jid else (a.get("cwd") == row["worktree"] and a.get("name") == row["name"]))]
        if not mine:
            return jid, None
        a = max(mine, key=lambda a: str(a.get("startedAt") or ""))
        short = str(a["id"])[:8]
        return (short if ID_RE.fullmatch(short) else jid), a.get("sessionId")

    async def _launch_opencode(self, row: dict[str, Any], ctx: dict[str, Any], prompt: str, continue_session: bool) -> None:
        if self.tb is None:
            raise CodingError("OpenCode is not wired into this backend.")
        ctx = {**ctx, "taint_sources": []}  # opencode's own "network" label stays off the caller's ctx; the session's label covers it
        job, _base = await opencode.launch(self.tb, ctx, prompt, cwd=row["worktree"], state_key=f"coding-{row['id']}",
                                           continue_session=continue_session, model=row["model"], background=True,
                                           no_timeout=True, conversation_id=f"coding:{row['id']}",
                                           run_id=row.get("run_id"), notify=False, on_timeout="kill",
                                           pool="coding", max_background=self._max_concurrent())
        row["external_id"] = job.id

    async def send(self, sid: str, message: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = ctx if ctx is not None else {}
        row = self._known(sid)
        message = self._text(message, "message")
        if not os.path.isdir(row["worktree"]):
            raise CodingError(f"{row['worktree']} no longer exists.")
        self.refresh(row)
        shell.taint(ctx, f"coding_session:send:{sid}")
        if row["agent"] == "claude":
            if row["status"] not in ("done", "stopped"):
                hint = " It is waiting for the user: `claude attach <id>` answers it." if row["status"] == "needs_you" else ""
                raise CodingError(f"This session is {row['status'].replace('_', ' ')}; wait for it to finish or stop it first.{hint}")
            if not row.get("session_id"):
                raise CodingError("This session's id is not known yet, so it cannot be resumed.")
            self._check_capacity()
            exe = claude_binary()
            if not exe:
                raise CodingError(CLAUDE_HINT)
            ok, out = await self.run(resume_argv(exe, row["session_id"], message), row["worktree"], START_TIMEOUT,
                                     await asyncio.to_thread(claude_env))
            if not ok:
                raise CodingError(f"claude did not resume: {out.strip()[-300:]}")
            new = parse_job_id(out)
            detail = "follow-up sent"
            if new and new != row["external_id"]:  # the CLI started a copy instead of continuing this one
                row["external_id"], row["session_id"] = new, None
                detail = f"the original session was still open, so Claude Code continued in a copy ({new}); this session now follows the copy"
                state, _ = read_claude(self.claude_home, new)
                sess = (state or {}).get("sessionId")
                if isinstance(sess, str) and UUID_RE.fullmatch(sess):
                    row["session_id"] = sess
            self.resumed[row["id"]] = time.monotonic()
        else:
            job = self.jobs.jobs.get(row.get("external_id") or "")
            if (job is not None and job.status in ("running", "orphaned")) or row["status"] in LIVE:
                raise CodingError("OpenCode is still working on this session; wait for it to finish or stop it first.")
            if row["status"] == "blocked":  # the job record was lost; the process may still run on the same state folder
                raise CodingError("OpenCode may still be running on this session (its job record was lost); Stop it first, then send the follow-up.")
            self._check_capacity()
            try:
                await self._launch_opencode(row, ctx, message, True)
            except shell.ShellError as e:
                raise CodingError(shell._scrub(str(e))) from e
            detail = "follow-up sent"
        return self._apply(row, status="working", detail=detail, log_tail="", ended_at=None)

    async def stop(self, sid: str) -> dict[str, Any]:
        row = self._known(sid)
        if row["status"] not in (*LIVE, "blocked"):
            return row
        if row["agent"] == "claude":
            exe = claude_binary()
            if not (exe and ID_RE.fullmatch(row.get("external_id") or "")):
                raise CodingError("This session has no Claude Code job id to stop.")
            ok, out = await self.run([exe, "stop", row["external_id"]], row["worktree"], GIT_TIMEOUT,
                                     await asyncio.to_thread(claude_env))
            if not ok:
                raise CodingError(f"claude stop failed: {out.strip()[-300:]}")
        else:
            job = self.jobs.jobs.get(row.get("external_id") or "")
            if job is not None:
                await self.jobs.kill(job)
        return self._apply(row, status="stopped", detail="stopped by the user")

    async def diff(self, sid: str, full: bool = False) -> dict[str, Any]:
        row = self._known(sid)
        wt = row["worktree"]
        if not os.path.isdir(wt):
            raise CodingError(f"{wt} no longer exists.")
        cut = False

        async def git(*args: str, cap: int = STAT_CAP) -> tuple[bool, str]:
            nonlocal cut
            ok, out = await self.run([*GIT, *args], wt, GIT_TIMEOUT)
            if len(out) > cap:
                out, cut = out[:cap], True
            return ok, out

        _, status = await git("status", "--short")
        _, stat = await git("diff", "HEAD", "--stat", "--no-ext-diff", "--no-textconv")
        ok, log = await git("log", "--oneline", "--stat", "-n", "20", "origin/main..HEAD")
        if not ok:
            _, log = await git("log", "--oneline", "--stat", "-n", "5")
        out: dict[str, Any] = {"worktree": wt, "branch": row.get("branch"), "status": status, "diff_stat": stat, "log": log}
        if full:
            _, out["diff"] = await git("diff", "HEAD", "--no-ext-diff", "--no-textconv", cap=DIFF_CAP)
        out["truncated"] = cut
        return out


def register(tb: Any, sessions: CodingSessions) -> None:
    """The coding_session_* tools (group `shell`). Starting and following up are external: they run an agent that edits
    a repo on this Mac, so an unattended run only proposes them and a chat asks first."""
    from .tools import ToolSpec, _obj, tool_error
    tb.coding = sessions

    def available() -> bool:
        return claude_binary() is not None or opencode.binary() is not None

    def installed() -> str:
        have = [n for n, b in (("claude", claude_binary()), ("opencode", opencode.binary())) if b]
        return ", ".join(have) or "none"

    def unattended(name: str) -> dict[str, Any]:
        return tool_error(f"{name} starts or steers a coding agent on this Mac and this is an unattended background run, "
                          "so it cannot run here.", alternative="describe the task in your report for the user")

    def view(sid: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        row = sessions.get(str(sid or ""))
        return (sessions.refresh(row), None) if row else (None, tool_error(f"No coding session '{sid}'.", field="id"))

    async def coding_session_start(ctx: dict[str, Any], agent: str, repo_path: str, prompt: str, new_worktree: bool = False,
                                   branch: str | None = None, model: str | None = None, permission_mode: str | None = None,
                                   name: str | None = None) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_session_start")
        try:
            row = await sessions.start(agent, repo_path, prompt, new_worktree=bool(new_worktree), branch=branch, model=model,
                                       permission_mode=permission_mode, name=name, ctx=ctx)
        except (CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)))
        if row["status"] == "failed":
            return tool_error(row.get("detail") or "The session did not start.",
                              alternative="fix the cause named above, or run the task another way")
        note = ("Started. It works in the background; coding_session_status(id) reads progress. "
                + ("If it needs a permission answer its status becomes needs_you and the user runs `claude attach <external_id>`."
                   if agent == "claude" else "OpenCode runs until it finishes or coding_session_stop; send a follow-up to continue."))
        return {**summary(row), "note": note}

    spec = ToolSpec("coding_session_start",
                    "Start a coding agent on a repo and let it work in the background: agent 'claude' (Claude Code) or 'opencode' "
                    f"(installed here: {installed()}). repo_path must be a git repo anywhere on this Mac; new_worktree=true gives "
                    "the agent its own branch and git worktree under <repo>/.claude/worktrees so the repo's checkout stays "
                    "untouched. Give a complete, self-contained task: it does not see this conversation. Claude Code runs OUTSIDE "
                    "the OS sandbox with the user's own account and tools, and asks for permission inside its own session "
                    "(status needs_you; the user answers with `claude attach <id>`). OpenCode runs inside the OS sandbox (Grain's own data folder and app, credential stores and the "
                    "files that run code later are off limits). permission_mode ('acceptEdits', 'auto', 'dontAsk' or 'bypassPermissions', "
                    "Claude Code only) overrides the mode this session runs in (default follows Grain's permission mode); only 'bypassPermissions' "
                    "(under Auto or Manual) asks the user first. Leave it out unless the user asked. Follow progress with coding_session_status and review with coding_session_diff.",
                    _obj({"agent": {"type": "string", "enum": list(AGENTS)}, "repo_path": {"type": "string"},
                          "prompt": {"type": "string", "description": "The task, with the files or folders it concerns"},
                          "new_worktree": {"type": "boolean", "default": False},
                          "branch": {"type": "string", "description": "Branch for the new worktree (default grain/<task>-<hex>); never main/master"},
                          "model": {"type": "string"},
                          "permission_mode": {"type": "string", "enum": ["acceptEdits", "auto", "dontAsk", "bypassPermissions"]},
                          "name": {"type": "string", "description": "A short label for the session"}},
                         ["agent", "repo_path", "prompt"]),
                    coding_session_start, "shell", "external",
                    examples=[{"agent": "claude", "repo_path": "/Users/me/code/app", "prompt": "Add a --dry-run flag to cli.py and cover it in tests",
                               "new_worktree": True}])
    spec.default = "ask"
    def _bypass(args: dict[str, Any], ctx: dict[str, Any]) -> bool:  # an explicit bypass that Allow all did not itself choose
        from . import autoreview
        gm = ctx.get("permission_mode") or autoreview.mode_of(ctx.get("settings") or sessions.settings())
        return args.get("permission_mode") == "bypassPermissions" and gm != "allow_all"

    spec.force_card = _bypass  # a hard card: no grant, and not the reviewer, buys it off
    spec.force_ask = lambda args, ctx: bool(ctx.get("tainted")) or _bypass(args, ctx)
    spec.available_fn = available
    tb.specs["coding_session_start"] = spec

    async def coding_session_list(ctx: dict[str, Any]) -> Any:
        rows = [summary(sessions.refresh(r), 0) for r in sessions.list(20)]
        shell.taint(ctx, "coding_session:list:" + ",".join(r["id"] for r in rows))  # each row's detail was written by a coding agent
        return {"sessions": rows}

    async def coding_session_status(ctx: dict[str, Any], id: str, lines: int = 30) -> Any:
        row, err = view(id)
        if err:
            return err
        shell.taint(ctx, f"coding_session:status:{row['id']}")  # the log and detail were written by a coding agent
        try:
            n = max(1, min(int(lines or 30), 200))
        except (TypeError, ValueError):
            n = 30
        out = summary(row, None)  # type: ignore[arg-type]
        out["log_tail"] = "\n".join(out["log_tail"].splitlines()[-n:])[-LOG_TAIL:]
        return out

    async def coding_session_send(ctx: dict[str, Any], id: str, message: str) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_session_send")
        try:
            return summary(await sessions.send(str(id), message, ctx))
        except (CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)))

    async def coding_session_stop(ctx: dict[str, Any], id: str) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_session_stop")
        try:
            row = await sessions.stop(str(id))
        except (CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)))
        shell.taint(ctx, f"coding_session:stop:{row['id']}")  # the row's detail and log may be agent-written
        return summary(row)

    async def coding_session_diff(ctx: dict[str, Any], id: str, full: bool = False) -> Any:
        try:
            out = await sessions.diff(str(id), bool(full))
        except (CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)))
        shell.taint(ctx, f"coding_session:diff:{str(id)}")  # agent-written content
        return out

    sid = {"id": {"type": "string"}}
    for name, desc, params, fn, danger in (
        ("coding_session_list", "List coding sessions (newest first) with their status. needs_you means the session is waiting "
         "for the user to answer a permission prompt in its own window.", _obj({}, []), coding_session_list, "safe"),
        ("coding_session_status", "Read one coding session's status, current step and the end of its log. Refreshes it first.",
         _obj({**sid, "lines": {"type": "integer", "default": 30}}, ["id"]), coding_session_status, "safe"),
        ("coding_session_send", "Send a follow-up message to a coding session that has finished or been stopped. A session "
         "that is still working or waiting for the user cannot take one: wait, or stop it first. A finished Claude Code session "
         "may continue as a copy with a new external_id. Asks the user in Manual mode, and whenever the run read other untrusted content.",
         _obj({**sid, "message": {"type": "string"}}, ["id", "message"]), coding_session_send, "external"),
        ("coding_session_stop", "Stop a running coding session. It keeps its files and can be continued with "
         "coding_session_send. Never removes a session.", _obj(sid, ["id"]), coding_session_stop, "executes"),
        ("coding_session_diff", "What a coding session changed in its worktree: git status, diff stat and the commits "
         "ahead of origin/main; full=true adds the whole working-tree patch (cut at 60 KB).",
         _obj({**sid, "full": {"type": "boolean", "default": False}}, ["id"]), coding_session_diff, "safe"),
    ):
        s = ToolSpec(name, desc, params, fn, "shell", danger, examples=[{"id": "a1b2c3d4e5f60718"}] if name != "coding_session_list" else [])
        s.available_fn = available
        if name == "coding_session_send":
            s.default = "ask"
            s.taint_ok = lambda args, ctx: (not ctx.get("taint_unsourced")
                                            and own_session_taint(str(args.get("id") or ""), ctx.get("taint_sources")))
            s.force_ask = lambda args, ctx, ok=s.taint_ok: bool(ctx.get("tainted")) and not ok(args, ctx)
        tb.specs[name] = s
