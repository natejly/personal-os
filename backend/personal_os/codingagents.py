"""Coding sessions: hand a coding task to Claude Code, OpenCode or pi on a repo (or a fresh git worktree of it) and follow it.

Three drivers behind one row (`coding_sessions`, migration 11):

- claude: `claude --bg` starts a background session that outlives this app. It runs OUTSIDE the OS sandbox with the
  user's own account and tools, and prompts for permission inside its own session: a prompt shows as state "blocked",
  which becomes `needs_you` here. A chat reply to it (`send`) stops the session and resumes it under the same id with the
  reply as the next message, in its worktree; that never grants the pending permission (the user approves it in the
  session itself, `claude attach <id>`, or restarts with a permission_mode). Progress is read from the plain
  files the CLI keeps under ~/.claude/jobs/<id>/ (state.json, timeline.jsonl), never by following the session's
  output. Those files are untrusted text. A follow-up to a done, failed or stopped session stops it first when it is
  still open in the daemon, so the resume wakes the same id; if the CLI starts a copy anyway, the row follows the copy. The session's `--permission-mode`
  follows Grain's own mode (Auto -> auto, Allow all -> bypassPermissions, Manual -> no flag, so it prompts and shows
  needs_you) unless the caller names a permission_mode (acceptEdits, auto, dontAsk or bypassPermissions) for that one
  session. Only an explicit bypassPermissions under Auto or Manual forces a card the reviewer cannot lift. Every start
  and follow-up also gets `--settings` with Allow everything's floor as Claude Code deny/ask rules
  (permrules.claude_code_floor), which apply in every mode, bypassPermissions included: Grain's own data folder and app
  and disk wipes are refused; credential stores, deletes that skip the Trash and force-pushes ask (needs_you). A Bash
  PreToolUse hook (claude_hook) applies the same floor through `sh -c`, `eval`, `sudo` and other wrappers, and lets
  deletes in temp folders and in the session's own Grain-made worktree run.
- opencode: `opencode.launch` under the OS sandbox as a background job in the shell registry (shell.ShellJobs). It
  has no prompt to answer: the sandbox is its boundary. It runs until it exits or Stop ends it (no time limit: app shutdown
  kills every shell job group, and a session a crash orphaned is recorded as `orphaned`); a follow-up
  `--continue`s the same opencode state folder.
- pi: `pi.launch` (`pi --mode json`) the same way as opencode, under the OS sandbox in the shell registry. pi has no
  permission prompts at all, so the sandbox is its only boundary; a follow-up reopens the same pi session by id
  (`--session`). pi exits 0 even when its model call failed, so a final error in its event stream marks the row failed.

On a repo's main checkout every agent gets its own worktree unless the caller passes new_worktree=false, so neither
edits a checkout the user or another agent is working in by default. One live session per worktree: start and send
refuse while another session is starting, working or needs_you there. Every tool takes the Grain id, the harness's
short `external_id` or the Claude Code `session_id` (`CodingSessions.get`).

A session belongs to the run that started it (`run_id`: the calling subagent or worker, else the chat or desk reply).
When that run is stopped, not when it finishes normally, its live sessions are stopped too (stop_owned, called from
subagents' _finish and the reply's teardown in app.py), with the reason on the row's detail. At app shutdown the
sessions of runs still going (halted workers, live replies and subagents) are stopped; ones whose run already finished
keep running in the daemon. Stopping never removes a worktree, so uncommitted or unmerged work stays.

Every change is saved and published on the app `events` topic as a `coding_session` event carrying `summary(row)`.
Nothing here removes a session, force-pushes, or runs git beyond `worktree add`, `status`, `diff` and `log`.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import claude_hook, coding_events, mac, opencode, osplat, permrules, pi, shell
from .coding_route import AGENT_NAMES
from .db import new_id
from .limits import CODING_SESSION_MAX_CONCURRENT, LOGIN_SHELL_TIMEOUT_SECONDS
from .ship import BRANCH_RE, PROTECTED

log = logging.getLogger(__name__)

AGENTS = ("claude", "opencode", "pi")
PERMISSION_MODES = (None, "acceptEdits", "auto", "dontAsk", "bypassPermissions")
LIVE = ("starting", "working", "needs_you")
SETTLED = ("done", "failed", "stopped", "needs_you", "blocked")  # a session in one of these has something to report
ATTENTION = {"starting": "working", "working": "working", "needs_you": "needs_you", "blocked": "blocked"}
LOG_TAIL = 4000
READ_CAP = 256 * 1024
START_TIMEOUT, GIT_TIMEOUT, RESUME_GRACE, STOP_WAIT = 180.0, 60.0, 15.0, 5.0
PERMISSION_RE = re.compile(r"permission|approve|allow|(?-i:\b[A-Z][A-Za-z]+\()", re.I)  # a waiting text that looks like a tool-permission prompt
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
    def __init__(self, msg: str, alt: str | None = None):
        super().__init__(msg)
        self.alt = alt   # what the caller can do instead, for the tool error's try_instead


def cli_permission_mode(grain_mode: str, explicit: str | None) -> str | None:
    """The `--permission-mode` for a Claude Code session: the caller's explicit one, else Grain's mode mapped over."""
    return explicit or {"auto": "auto", "allow_all": "bypassPermissions"}.get(grain_mode)


def own_session_taint(sid: str, sources: Any) -> bool:
    """True when the run's taint came only from this coding session's own labels (so a follow-up to it is not untrusted-driven)."""
    srcs = [str(x) for x in sources or []]
    return bool(srcs) and all(
        x in (f"coding_session:{k}:{sid}" for k in ("start", "send", "stop", "status", "summary", "diff"))
        or (x[:20] in ("coding_session:list:", "coding_session:wait:") and all(i == sid for i in x[20:].split(",") if i))
        for x in srcs)


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
        path = os.path.expanduser("~/.local/bin") + ":" + (os.environ.get("PATH") or "") + ":" + extra + shell.safe_path()
    return {"PATH": path, "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir()}


def parse_job_id(out: str) -> str | None:
    """The short job id `claude --bg` prints (8 hex chars), or None. The id after "backgrounded" wins: when the CLI
    starts a copy, its note names the original first."""
    m = re.search(r"backgrounded\W+([0-9a-f]{8})\b", out or "") or ID_RE.search(out or "")
    return m.group(m.lastindex or 0) if m else None


def _lead(text: str) -> str:
    """A positional argument that starts with a dash would be read as a flag."""
    return f"Task: {text}" if text.startswith("-") else text


# Two generic helpers every session may hand work to, inline (--agents): one writes, one checks.
CLAUDE_AGENTS = json.dumps({
    "implementer": {"description": "Makes a focused code change: reads the surrounding code, edits the files and runs what it touched.",
                    "prompt": "You implement one well-scoped change. Read the code around it first, keep the change small and in the "
                              "style of the file, run the relevant checks, and report exactly what you changed and what you verified.",
                    "model": "sonnet"},
    "tester-reviewer": {"description": "Tests and reviews a finished change: runs the tests, reads the diff and reports problems.",
                        "prompt": "You verify a change someone else made. Run the relevant tests, read the diff for bugs and missed "
                                  "cases, and report concrete problems with file and line, then what looks fine. Do not rewrite the change.",
                        "model": "sonnet"},
}, separators=(",", ":"))


def floor_settings(worktree: str | None = None, lifted: bool = False) -> str:
    """The `--settings` JSON every claude start and resume carries: the allow-all floor as permission rules plus the
    Bash PreToolUse hook (claude_hook) that judges what a command runs. `worktree` is a worktree Grain made for this
    session, where deleting files is the session's own work. Inline, so nothing on disk can be swapped under the
    session; the CLI merges these lists with the user's own settings. `lifted` (the session runs in bypassPermissions,
    which is Allow everything): keep the denies on Grain's own data and disk wipes, drop every ask and the delete hook."""
    if lifted:
        return json.dumps({"permissions": {"deny": permrules.claude_code_floor()["permissions"]["deny"]}}, separators=(",", ":"))
    return json.dumps({**permrules.claude_code_floor(), "hooks": claude_hook.settings_hooks(worktree)}, separators=(",", ":"))


def claude_argv(exe: str, name: str, prompt: str, model: str | None = None, permission_mode: str | None = None,
                worktree: str | None = None, lifted: bool | None = None) -> list[str]:
    """The start command, with the two inline sub-agents and the floor settings. A model flag appears only when the caller
    named one: the session runs on the user's own account, so their own CLI default is the one model they are known to
    have quota for (pinning one here started every session on it and failed the moment that model's limit was reached).
    A permission flag likewise appears only when the caller asked for a mode."""
    return [exe, "--bg", "-n", name, *(["--model", model] if model else []), "--agents", CLAUDE_AGENTS,
            "--settings", floor_settings(worktree, permission_mode == "bypassPermissions" if lifted is None else lifted), *(["--permission-mode", permission_mode] if permission_mode else []), _lead(prompt)]


def resume_argv(exe: str, session_id: str, message: str, worktree: str | None = None, permission_mode: str | None = None,
                lifted: bool | None = None) -> list[str]:
    """A follow-up is a new CLI process: it needs the floor settings again (the mode is the session's own)."""
    return [exe, "--bg", "--resume", session_id, "--settings",
            floor_settings(worktree, permission_mode == "bypassPermissions" if lifted is None else lifted), _lead(message)]


def own_worktree(row: dict[str, Any]) -> str | None:
    """The worktree Grain made for this session (a row with a branch), else None: a checkout the session was merely
    pointed at is not its own to delete in."""
    return row["worktree"] if row.get("branch") else None


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


def read_timeline(home: Path, job_id: str, cap: int = 8_000_000) -> list[str]:
    """The complete lines of a claude job's timeline.jsonl (the last line only when it is valid JSON, since the CLI may
    still be writing it). Same folder rules as read_claude."""
    if not ID_RE.fullmatch(job_id or ""):
        return []
    d = home / job_id
    try:
        if d.resolve().parent != home.resolve():
            return []
        text, cut = _tail(d / "timeline.jsonl", cap)
    except OSError:
        return []
    lines = text.splitlines()[1 if cut else 0:]
    if lines and not text.endswith("\n"):
        try:
            json.loads(lines[-1])
        except ValueError:
            lines.pop()
    return lines


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
    """(status, detail) for an opencode or pi job in the shell registry."""
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
           "worktree": row["worktree"], "branch": row.get("branch"), "conversation_id": row.get("conversation_id"), "external_id": row.get("external_id"),
           "session_id": row.get("session_id"), "model": row.get("model"), "permission_mode": row.get("permission_mode"), "created_at": row["created_at"],
           "updated_at": row["updated_at"], "ended_at": row.get("ended_at"),
           "log_tail": log if tail is None else (log[-tail:] if tail else "")}
    if row["agent"] == "claude" and row["status"] == "needs_you" and row.get("external_id"):
        out["attach_hint"] = f"claude attach {row['external_id']}"
    return out


def brief(row: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """The cheap view: identity, status, when it last moved and (waiting) what it waits on or (else) its detail and last log line."""
    t = row["updated_at"]
    out = {k: row.get(k) for k in ("id", "agent", "name", "status")}
    out.update(attention=ATTENTION.get(row["status"], "idle"), branch=row.get("branch"), worktree=row["worktree"], repo_path=row["repo_path"],
              external_id=row.get("external_id"), session_id=row.get("session_id"),
              last_activity=time.strftime("%-I:%M %p", time.localtime(t)), idle_s=max(0, int((now or time.time()) - t)))
    if row["status"] == "needs_you":
        text = row.get("detail") or ""
        out["waiting_on"] = {"kind": "permission" if PERMISSION_RE.search(text) else "question", "text": text}
    else:
        out["detail"] = row.get("detail")
        out["last_log"] = next((ln.strip()[:300] for ln in reversed((row.get("log_tail") or "").splitlines()) if ln.strip()), "")
    return out


class CodingSessions:
    def __init__(self, db: Any, jobs: shell.ShellJobs, run: Runner, publish: Callable[[str, Any], None],
                 settings: Callable[[], dict[str, Any]], tb: Any = None, claude_home: Path | None = None):
        self.db, self.jobs, self.run, self.publish, self.settings = db, jobs, run, publish, settings
        self.tb = tb                    # the Toolbox: opencode.launch needs its model settings, data dir and workspace
        self.claude_home = claude_home or Path.home() / ".claude" / "jobs"
        self.resumed: dict[str, float] = {}   # id -> when a follow-up started: a stale "done" in state.json is ignored for a moment
        self.cursors: dict[str, tuple[str, int]] = {}  # id -> (job id, absolute output offset) already turned into events (opencode, pi)
        self.timelines: dict[tuple[str, str], tuple[int, tuple[str, str] | None]] = {}  # (id, claude job id) -> (lines read, last event)
        self.seen: dict[str, tuple[int, str]] = {}   # id -> (output size, job status) at the last opencode refresh
        self.mtimes: dict[str, int | None] = {}  # id -> state.json mtime at the last look at a settled claude session
        # ponytail: one lock for every session's refresh (the watcher thread vs wait/status/list); per-id locks if it ever contends
        self._refresh_lock = threading.RLock()
        self.on_finish: Callable[[dict[str, Any]], None] | None = None  # a session settled: wake the chat or run that owns it
        self.waiting: dict[str, int] = {}   # id -> coding_session_wait calls blocked on it (they report it themselves, no wake)
        self.stopping: set[str] = set()     # ids Grain is stopping itself: that stop is not news
        self.closing = False  # set at app shutdown, after the halted workers' sessions are stopped (see stop_owned)
        self._recover()
        self._prev_on_change = getattr(jobs, "on_change", None)
        jobs.on_change = self._job_changed

    def _job_changed(self) -> None:
        """A shell job started or ended: a live OpenCode or pi session publishes its new state now, not at the next poll."""
        if self._prev_on_change:
            self._prev_on_change()
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_sessions WHERE agent != 'claude' AND external_id IS NOT NULL "
                                               f"AND status IN ({','.join('?' * len(LIVE))})", LIVE)]
        for row in rows:
            self.refresh(row)

    # ---- rows ----
    def get(self, sid: str) -> dict[str, Any] | None:
        """A row by Grain id, else by the harness's own id (`external_id`, the Claude Code short id) or `session_id` (its UUID)."""
        sid = str(sid or "")
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM coding_sessions WHERE id = ?1 OR external_id = ?1 OR session_id = ?1 "
                          "ORDER BY id = ?1 DESC, created_at DESC, rowid DESC LIMIT 1", (sid,)).fetchone()
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

    def _live(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_sessions WHERE status IN ({','.join('?' * len(LIVE))})", LIVE)]
        return [r for r in map(self.refresh, rows) if r["status"] in LIVE]  # a session that ended unseen must not hold a slot

    def _check_capacity(self) -> None:
        n = len(self._live())
        if n >= self._max_concurrent():
            raise CodingError(f"{n} coding sessions are already running (codingSessionMaxConcurrent); wait for one to finish or stop one.",
                              "coding_session_wait for one, or coding_session_stop one, then try again")

    def _check_free(self, worktree: str, own: str | None = None) -> None:
        """One live session per worktree: refuse when a session other than `own` is starting, working or needs_you there."""
        here = os.path.realpath(worktree)
        for r in self._live():
            if r["id"] != own and os.path.realpath(r["worktree"]) == here:
                raise CodingError(f"{worktree} is in use by session {r['id']} ('{r['name']}', {r['agent']}, {r['status'].replace('_', ' ')}).",
                                  "wait for it (coding_session_wait), answer it (coding_session_send if it needs_you), stop it "
                                  "(coding_session_stop), or start in a new worktree (new_worktree=true)")

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

    def event(self, sid: str, kind: str, text: str, tool: str | None = None, data: dict[str, Any] | None = None) -> None:
        """Add an event to a session's live feed and publish it. Never raises: the feed must not break a session."""
        try:
            coding_events.append(self.db, sid, kind, text, tool, data, self.publish)
        except Exception:  # noqa: BLE001
            log.warning("coding session event failed", exc_info=True)

    def _events(self, sid: str, evs: list[coding_events.Ev]) -> None:
        for kind, text, tool, data in evs:
            self.event(sid, kind, text, tool, data)

    def _apply(self, row: dict[str, Any], **kw: Any) -> dict[str, Any]:
        """Set fields and save, but only when something changed (a poll that sees nothing new publishes nothing)."""
        changed = {k: v for k, v in kw.items() if row.get(k) != v}
        if changed:
            old = row.get("status")
            row.update(changed)
            if "status" in changed:
                self.event(row["id"], "status", f"{old} → {row['status']}")
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
        """Re-read the driver's state and save when it changed. Reads files and the in-memory job registry only. Safe from
        a worker thread: refreshes take turns, and each diffs against the saved row, not the caller's possibly stale copy."""
        with self._refresh_lock:
            row.update(self.get(row["id"]) or {})  # in place: callers read the row they passed in
            prev = row["status"]
            if row["agent"] == "claude":
                if row.get("external_id") and row["status"] != "failed" and (row["status"] in LIVE or self._state_moved(row)):
                    row = self._refresh_claude(row)
            else:
                row = self._refresh_opencode(row) if row["status"] in LIVE else row
            # Settling, or moving on from waiting on the user (needs_you -> done): someone is owed the result.
            if prev in LIVE and row["status"] in SETTLED and row["status"] != prev and self.on_finish:
                if not (self.waiting.get(row["id"]) or row["id"] in self.stopping):
                    try:
                        self.on_finish(row)
                    except Exception:  # noqa: BLE001 - a wake-up that fails must not break the refresh
                        log.warning("coding session wake failed", exc_info=True)
            return row

    def _state_moved(self, row: dict[str, Any]) -> bool:
        """For a settled claude session: has its state.json changed since the last look (a resume from the terminal)?
        An unchanged one is not re-read, so listing finished sessions does not re-read their timelines."""
        try:
            m: int | None = (self.claude_home / row["external_id"] / "state.json").stat().st_mtime_ns
        except OSError:
            m = None
        if self.mtimes.get(row["id"], -1) == m:
            return False
        self.mtimes[row["id"]] = m
        return True

    async def watch(self, every: float = 5.0, fast: float = 2.0) -> None:
        """Claude Code sessions have no process of ours to hear from: re-read the live ones so a finish is noticed (and woken
        for) without anyone calling status."""
        rows: list[dict[str, Any]] = []
        while True:
            await asyncio.sleep(fast if rows else every)  # a working session is polled faster so its feed reads live
            try:
                with self.db.tx() as c:
                    rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_sessions WHERE agent = 'claude' AND status IN "
                                                       f"({','.join('?' * len(LIVE))})", LIVE)]  # needs_you too: its finish is news
                for row in rows:
                    await asyncio.to_thread(self.refresh, row)
            except Exception:  # noqa: BLE001 - the watcher outlives any one bad row
                log.warning("coding session watcher failed", exc_info=True)

    async def wait(self, ids: list[str], mode: str = "all", timeout_s: float = 120) -> dict[str, Any]:
        """Block (asynchronously) until the sessions have settled (all of them, or any one) or the timeout passes."""
        ids = [str(i) for i in ids][:20]
        if not ids:
            raise CodingError("ids is empty.")
        ids = [self._known(i)["id"] for i in ids]  # Grain ids: the settle and wake bookkeeping is keyed by them
        try:
            deadline = time.monotonic() + max(1.0, min(float(timeout_s or 120), 600.0))
        except (TypeError, ValueError):
            deadline = time.monotonic() + 120
        for i in ids:
            self.waiting[i] = self.waiting.get(i, 0) + 1
        try:
            while True:
                rows = await asyncio.to_thread(lambda: [self.refresh(self._known(i)) for i in ids])  # file reads, off the loop
                settled = [r for r in rows if r["status"] not in ("starting", "working")]
                if (settled if mode == "any" else len(settled) == len(rows)) or time.monotonic() >= deadline:
                    return {"sessions": rows, "settled": [r["id"] for r in settled], "timed_out": not (settled if mode == "any" else len(settled) == len(rows))}
                await asyncio.sleep(min(2.0, max(0.05, deadline - time.monotonic())))
        finally:
            for i in ids:
                self.waiting[i] -= 1

    def _refresh_claude(self, row: dict[str, Any]) -> dict[str, Any]:
        state, lines = read_claude(self.claude_home, row["external_id"])
        if state is None:
            return self._apply(row, detail="state unavailable") if row["status"] in LIVE else row
        mapped = map_claude(state)
        if mapped is None:
            return row
        status, detail = mapped
        if status in ("done", "stopped", "failed") and time.monotonic() - self.resumed.get(row["id"], -1e9) < RESUME_GRACE:
            return row  # a follow-up was just sent and state.json has not caught up
        sid = state.get("sessionId")
        log = shell._scrub("\n".join(lines))[-LOG_TAIL:]
        self._claude_events(row)
        if status == row["status"] == "stopped":
            detail = row.get("detail") or detail  # the recorded stop reason, not the CLI's own word for it
        return self._apply(row, status=status, detail=detail or row.get("detail"), log_tail=log or row.get("log_tail") or "",
                           session_id=sid if isinstance(sid, str) and UUID_RE.fullmatch(sid) else row.get("session_id"))

    def _claude_events(self, row: dict[str, Any]) -> None:
        """Turn the timeline lines not yet seen into events. The cursor is a line count per claude job id; after a
        restart it is found again from the newest event of that job (coding_events.last_from), so nothing repeats."""
        key = (row["id"], row["external_id"])
        try:
            raw = read_timeline(self.claude_home, row["external_id"])
            if key not in self.timelines:
                last = coding_events.last_from(self.db, row["id"], row["external_id"])
                if last:
                    self.timelines[key] = (int((last["data"] or {}).get("line") or 0),
                                           (("tool" if last["kind"] == "tool" else "text"), last["text"]))
                else:
                    self.timelines[key] = (0, None)
            done, prev = self.timelines[key]
            evs, prev = coding_events.parse_claude([shell._scrub(ln) for ln in raw[done:]], done + 1, row["external_id"], prev)
            self.timelines[key] = (len(raw), prev)
            self._events(row["id"], evs)
        except Exception:  # noqa: BLE001
            log.warning("claude timeline events failed", exc_info=True)

    def _job_events(self, row: dict[str, Any], job: Any) -> None:
        """Turn the job output not yet seen into events. The cursor is an absolute offset into the job's output; only
        complete lines are taken while the job runs."""
        try:
            jid, pos = self.cursors.get(row["id"], (None, 0))
            if jid != row.get("external_id"):
                pos = 0
            base = getattr(job, "base", 0)
            start = max(pos - base, 0)
            raw = job.buf[start:]
            if job.live() and not raw.endswith("\n"):
                raw = raw[:raw.rfind("\n") + 1]
            self.cursors[row["id"]] = (row.get("external_id") or "", base + start + len(raw))
            if raw:
                parse = coding_events.parse_pi if row["agent"] == "pi" else coding_events.parse_opencode
                self._events(row["id"], parse(shell._scrub(raw))[0])
        except Exception:  # noqa: BLE001
            log.warning("coding job events failed", exc_info=True)

    def _refresh_opencode(self, row: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs.jobs.get(row.get("external_id") or "")
        if job is None:
            return self._apply(row, status="blocked", detail="the job record is gone (the app restarted, or it expired from the job registry); Stop clears it")
        if self.seen.get(row["id"]) == (job.total, job.status):
            return row  # nothing new since the last look: skip re-parsing the buffer
        self.seen[row["id"]] = (job.total, job.status)
        status, detail = map_job(job)
        self._job_events(row, job)
        raw = job.buf
        if job.live() and not raw.endswith("\n"):
            raw = raw[:raw.rfind("\n") + 1]  # the last line is still being written
        if status == "failed" and osplat.current().sandbox.failed_to_start(job.exit_code, raw):
            detail = "the OS sandbox refused to start (" + shell._scrub(raw.strip())[:200] + "), so nothing was run"
        if row["agent"] == "pi":
            text, session, err = pi.summarize(shell._scrub(raw))
            if err and status == "done":
                status, detail = "failed", err
        else:
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

    @staticmethod
    def installed_agents() -> list[str]:
        """The harnesses whose binary is present on this Mac, in preference order."""
        return [n for n, b in (("claude", claude_binary), ("opencode", opencode.binary), ("pi", pi.binary)) if b()]

    def default_agent(self) -> str | None:
        """The user's default harness: the `codingAgent` setting when it names one, else the first installed (claude, opencode, pi)."""
        from . import coding_route
        choice = coding_route.agent_choice(self.settings())
        if choice != "any":
            return choice
        have = self.installed_agents()
        return have[0] if have else None

    def resolve_agent(self, agent: str | None) -> str:
        """An explicit agent must be installed; with none given the default is used. Raises CodingError otherwise."""
        have = self.installed_agents()
        listed = ", ".join(have) or "none"
        agent = (str(agent).strip() or None) if agent else None
        if agent is None:
            agent = self.default_agent()
            if agent is None:
                raise CodingError("No coding agent is installed on this Mac (looked for claude, opencode and pi).")
            if agent not in have:
                raise CodingError(f"The default coding agent '{agent}' (Settings) is not installed. Installed here: {listed}. "
                                  "Pass agent explicitly or change the default.")
            return agent
        if agent not in AGENTS:
            raise CodingError(f"agent must be one of {', '.join(AGENTS)}.")
        if agent not in have:
            raise CodingError(f"'{agent}' is not installed on this Mac. Installed here: {listed}.")
        return agent

    async def start(self, agent: str | None, repo_path: str, prompt: str, *, new_worktree: bool | None = None, branch: str | None = None,
                    model: str | None = None, permission_mode: str | None = None, name: str | None = None,
                    ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = ctx if ctx is not None else {}
        agent = self.resolve_agent(agent)
        prompt = self._text(prompt, "prompt")
        permission_mode = permission_mode or None
        if permission_mode not in PERMISSION_MODES:
            raise CodingError("permission_mode must be acceptEdits, auto, dontAsk or bypassPermissions, or left out to follow Grain's permission mode.")
        if permission_mode and agent != "claude":
            raise CodingError("permission_mode is for Claude Code only: OpenCode and pi already run with every permission "
                              "allowed inside the OS sandbox.")
        model = (str(model).strip() or None) if model else None
        if model and not MODEL_RE.fullmatch(model):
            raise CodingError(f"'{model}' is not a model id this tool accepts.")
        repo = self.check_repo(repo_path, opencode._desk_root(self.tb, ctx))
        if new_worktree is None:  # every agent stays off a main checkout unless asked
            found = await asyncio.to_thread(opencode.repo_of, repo)
            new_worktree = bool(found and found[1])
        self._check_capacity()
        if not new_worktree:
            self._check_free(str(repo))
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
            if agent == "pi" and not pi.binary():
                raise CodingError(pi.INSTALL_HINT)
            wt = worktree_path(repo, branch)
            if wt.exists():
                raise CodingError(f"{wt} already exists. Pick another branch name.")
            ok, out = await self.run([*GIT, "worktree", "add", "-b", branch, str(wt)], str(repo), GIT_TIMEOUT)
            if not ok:
                raise CodingError(f"git could not create the worktree: {out.strip()[-300:]}")
        ok, head = await self.run([*GIT, "rev-parse", "HEAD"], str(wt), GIT_TIMEOUT)
        base = head.strip() if ok and re.fullmatch(r"[0-9a-f]{40}", head.strip()) else None  # what diff and the commit list compare to
        t = time.time()
        row = {"id": sid, "agent": agent, "external_id": None, "session_id": None, "repo_path": str(repo), "worktree": str(wt),
               "branch": branch, "conversation_id": ctx.get("conversation_id"), "desk_id": ctx.get("desk_id"),
               # the owner: the subagent or worker that called (agent_run_id), else the chat or desk reply (stop_owned)
               "run_id": ctx.get("agent_run_id") or ctx.get("run_id"), "name": name, "prompt": prompt, "model": model, "permission_mode": permission_mode,
               "status": "starting", "detail": None, "log_tail": "", "created_at": t, "updated_at": t, "ended_at": None,
               "base_sha": base}
        with self.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, branch, conversation_id, "
                      "desk_id, run_id, name, prompt, model, permission_mode, status, detail, log_tail, created_at, updated_at, "
                      "ended_at, base_sha) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, agent, None, None, row["repo_path"], row["worktree"], branch, row["conversation_id"], row["desk_id"],
                       row["run_id"], name, prompt, model, permission_mode, "starting", None, "", t, t, None, base))
        self.publish("coding_session", summary(row, None))
        self.event(sid, "user", prompt)
        shell.taint(ctx, f"coding_session:start:{sid}")  # the agent's text comes back through status, logs and diff
        try:
            if agent == "claude":
                await self._start_claude(row, ctx)
            elif agent == "pi":
                await self._launch_pi(row, ctx, prompt)
            else:
                await self._launch_opencode(row, ctx, prompt, False)
        except (CodingError, shell.ShellError) as e:
            return self._apply(row, status="failed", detail=shell._scrub(str(e))[:300])
        return self._apply(row, status="working", detail="started")

    def _lifted(self, row: dict[str, Any], ctx: dict[str, Any] | None) -> bool:
        """The floor's asks and delete hook are dropped only when Grain itself is in Allow everything: an explicit
        bypassPermissions the user approved under Auto or Manual keeps every gate."""
        from . import autoreview
        ctx = ctx or {}
        return row["permission_mode"] == "bypassPermissions" and (
            ctx.get("permission_mode") or autoreview.mode_of(ctx.get("settings") or self.settings())) == "allow_all"

    async def _start_claude(self, row: dict[str, Any], ctx: dict[str, Any] | None = None) -> None:
        exe = claude_binary()
        if not exe:
            raise CodingError(CLAUDE_HINT)
        ok, out = await self.run(claude_argv(exe, row["name"], row["prompt"], row["model"], row["permission_mode"], own_worktree(row),
                                           self._lifted(row, ctx)),
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
        job, _base = await opencode.launch(self.tb, ctx, prompt, cwd=row["worktree"],
                                           continue_session=continue_session, session=row.get("session_id") if continue_session else None,
                                           model=row["model"], background=True,
                                           no_timeout=True, conversation_id=f"coding:{row['id']}",
                                           run_id=row.get("run_id"), notify=False, on_timeout="kill",
                                           pool="coding", max_background=self._max_concurrent())
        row["external_id"] = job.id

    async def _launch_pi(self, row: dict[str, Any], ctx: dict[str, Any], prompt: str) -> None:
        if self.tb is None:
            raise CodingError("pi is not wired into this backend.")
        job = await pi.launch(self.tb, ctx, prompt, cwd=row["worktree"], session=row.get("session_id"), model=row["model"],
                              conversation_id=f"coding:{row['id']}", run_id=row.get("run_id"),
                              max_background=self._max_concurrent())
        row["external_id"] = job.id

    async def _quiesce(self, row: dict[str, Any], exe: str) -> None:
        """Stop a live Claude Code session and wait (bounded) until its state.json no longer reads blocked or working, so the
        resume wakes this id instead of starting a copy. Stopping cancels whatever it was waiting on; nothing is granted."""
        self.stopping.add(row["id"])
        try:
            await self._stop(row, "stopped to take the user's reply")
        except CodingError as e:
            raise CodingError(f"Could not stop the waiting session to deliver the reply: {e}",
                              f"open it with `claude attach {row['external_id']}` and answer there, or coding_session_stop it and send again") from e
        finally:
            self.stopping.discard(row["id"])
        end = time.monotonic() + STOP_WAIT
        while True:
            mapped = map_claude(read_claude(self.claude_home, row["external_id"])[0] or {})
            if mapped is None or mapped[0] not in ("needs_you", "working"):
                return
            if time.monotonic() >= end:
                raise CodingError("The session did not stop in time, so the reply was not delivered.",
                                  f"open it with `claude attach {row['external_id']}` and answer there, or try coding_session_send again")
            await asyncio.sleep(0.25)

    async def send(self, sid: str, message: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = ctx if ctx is not None else {}
        row = self._known(sid)
        message = self._text(message, "message")
        if not os.path.isdir(row["worktree"]):
            raise CodingError(f"{row['worktree']} no longer exists, so the session cannot continue there.", "start a new session")
        self.refresh(row)
        shell.taint(ctx, f"coding_session:send:{row['id']}")
        note = None
        if row["agent"] == "claude":
            if row["status"] not in ("done", "failed", "stopped", "needs_you"):
                raise CodingError(f"This session is {row['status'].replace('_', ' ')}; it cannot take a message now.",
                                  "coding_session_wait until it settles, or coding_session_stop it, then send")
            self._check_free(row["worktree"], row["id"])
            if not (row.get("session_id") and ID_RE.fullmatch(row.get("external_id") or "")):
                raise CodingError("This session's Claude Code id is not known, so it cannot be resumed.",
                                  "start a new session with the context in the prompt" + (f", or open it with `claude attach {row['external_id']}`" if row.get("external_id") else ""))
            exe = claude_binary()
            if not exe:
                raise CodingError(CLAUDE_HINT, "install Claude Code, or start the task with another harness")
            waiting = row["status"] == "needs_you"
            if waiting:  # it holds a slot itself: count the slots once it is stopped
                perm = bool(PERMISSION_RE.search(row.get("detail") or ""))
                self._check_capacity()
                await self._quiesce(row, exe)
            else:
                self._check_capacity()
            if row["status"] == "done":  # still open in the daemon: resuming it would start a copy, so close it first
                await self.run([exe, "stop", row["external_id"]], row["worktree"], GIT_TIMEOUT, await asyncio.to_thread(claude_env))
            ok, out = await self.run(resume_argv(exe, row["session_id"], message, own_worktree(row), row["permission_mode"], self._lifted(row, ctx)), row["worktree"], START_TIMEOUT,
                                     await asyncio.to_thread(claude_env))
            if not ok:
                if waiting:  # the quiesce already stopped it: say so instead of leaving a stale question
                    self._apply(row, detail="reply not delivered: claude did not resume; the session is stopped")
                raise CodingError(f"claude did not resume: {out.strip()[-300:]}",
                                  f"the session is now stopped: coding_session_send can retry, or `claude attach {row['external_id']}` opens it" if waiting
                                  else f"open it with `claude attach {row['external_id']}`, or start a new session")
            new = parse_job_id(out)
            detail = "reply sent" if waiting else "follow-up sent"
            if new and new != row["external_id"]:  # the CLI started a copy instead of continuing this one
                row["external_id"], row["session_id"] = new, None
                detail = f"the original session was still open, so Claude Code continued in a copy ({new}); this session now follows the copy"
                state, _ = read_claude(self.claude_home, new)
                sess = (state or {}).get("sessionId")
                if isinstance(sess, str) and UUID_RE.fullmatch(sess):
                    row["session_id"] = sess
            self.resumed[row["id"]] = time.monotonic()
            if waiting and perm:
                note = ("The reply was delivered as a message. The pending permission was NOT granted: a chat reply cannot approve a "
                        "Claude Code permission prompt. If the agent still needs it, it will ask again and the user approves it in the "
                        f"session itself (`claude attach {row['external_id']}`), or restart the session with a permission_mode the user chooses.")
        else:
            name = AGENT_NAMES[row["agent"]]
            job = self.jobs.jobs.get(row.get("external_id") or "")
            if (job is not None and job.status in ("running", "orphaned")) or row["status"] in LIVE:
                raise CodingError(f"{name} is still working on this session; it cannot take a message now.",
                                  "coding_session_wait until it finishes, or coding_session_stop it, then send")
            if row["status"] == "blocked":  # the job record was lost; the process may still run on the same state folder
                raise CodingError(f"{name} may still be running on this session (its job record was lost); Stop it first, then send the follow-up.",
                                  "coding_session_stop it, then send")
            if row["agent"] == "pi" and not row.get("session_id"):
                raise CodingError("pi's session id was not seen yet, so it cannot be reopened for a follow-up.", "start a new session")
            self._check_free(row["worktree"], row["id"])
            self._check_capacity()
            try:
                if row["agent"] == "pi":
                    await self._launch_pi(row, ctx, message)
                else:
                    await self._launch_opencode(row, ctx, message, True)
            except shell.ShellError as e:
                raise CodingError(shell._scrub(str(e)), "start a new session") from e
            detail = "follow-up sent"
        self.event(row["id"], "user", message)
        out = self._apply(row, status="working", detail=detail, log_tail="", ended_at=None)
        return {**out, "note": note} if note else out

    async def stop(self, sid: str, reason: str = "stopped by the user") -> dict[str, Any]:
        row = self._known(sid)
        if row["status"] not in (*LIVE, "blocked"):
            return row
        self.stopping.add(row["id"])
        try:
            return await self._stop(row, reason)
        finally:
            self.stopping.discard(row["id"])

    async def _stop(self, row: dict[str, Any], reason: str) -> dict[str, Any]:
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
        return self._apply(row, status="stopped", detail=reason)

    async def stop_owned(self, run_id: str | None, why: str) -> list[dict[str, Any]]:
        """Stop the live sessions `run_id` started, because that run was stopped (a user or assistant Stop, a parent
        stopping, a watchdog or breaker, a worker halted at shutdown). Never called on a normal finish: a session the run
        left working in the background keeps going. Stopping ends processes only (OpenCode's job group, `claude stop`);
        the worktree, its branch and the session files stay, and a worktree with work in it is named in the reason.
        A no-op once the app is closing (`closing`): Claude Code sessions outlive the app by design, and every OpenCode
        job ends with the shell registry anyway."""
        if not run_id or self.closing:
            return []
        live = (*LIVE, "blocked")
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute(f"SELECT * FROM coding_sessions WHERE run_id = ? AND status IN ({','.join('?' * len(live))})",
                                               (run_id, *live))]
        out = []
        for row in rows:
            reason = f"stopped: {why}{await self._kept(row)}"
            try:
                out.append(await self.stop(row["id"], reason))
            except (CodingError, shell.ShellError) as e:  # still running: say so on the row rather than claim it stopped
                out.append(self._apply(row, detail=shell._scrub(f"could not stop it ({why}): {e}")[:300]))
        return out

    async def _kept(self, row: dict[str, Any]) -> str:
        """Where the session's work stays, for a stop reason: its own worktree and branch, or uncommitted changes in it."""
        wt, branch = row["worktree"], row.get("branch")
        if not os.path.isdir(wt):
            return ""
        ok, st = await self.run([*GIT, "status", "--porcelain"], wt, GIT_TIMEOUT)
        where = wt + (f" (branch {branch})" if branch else "")
        if ok and st.strip():
            return f"; its uncommitted changes are kept in {where}"
        return f"; its worktree stays at {where}" if branch else ""

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

        base = row.get("base_sha") or "HEAD"  # sessions from before the base was recorded: working tree vs HEAD
        _, status = await git("status", "--short")
        _, stat = await git("diff", base, "--stat", "--no-ext-diff", "--no-textconv")
        _, new = await git("ls-files", "--others", "--exclude-standard")  # new files the diff against a commit cannot show
        new_files = new.splitlines()[:200]
        if row.get("base_sha"):
            _, log = await git("log", "--oneline", "--stat", "-n", "20", f"{base}..HEAD")
        else:
            ok, log = await git("log", "--oneline", "--stat", "-n", "20", "origin/main..HEAD")
            if not ok:
                _, log = await git("log", "--oneline", "--stat", "-n", "5")
        out: dict[str, Any] = {"worktree": wt, "branch": row.get("branch"), "base": row.get("base_sha"), "status": status,
                               "diff_stat": stat, "new_files": new_files, "log": log}
        if full:
            _, out["diff"] = await git("diff", base, "--no-ext-diff", "--no-textconv", cap=DIFF_CAP)
            for f in new_files:  # git diff --no-index exits 1 when the files differ, so the output is read whatever the code
                if len(out["diff"]) >= DIFF_CAP:
                    break
                _, patch = await git("diff", "--no-index", "--no-ext-diff", "--no-textconv", "--", "/dev/null", f, cap=DIFF_CAP)
                out["diff"] += patch
            out["diff"] = out["diff"][:DIFF_CAP]
        out["truncated"] = cut
        return out


def recent_events(db: Any, sid: str, n: int) -> list[Any]:
    """The last n structured events of a session, or [] while coding_events (or its `recent`) is not there."""
    try:
        from . import coding_events
        return list(coding_events.recent(db, sid, n))
    except (ImportError, AttributeError, TypeError):
        return []


def register(tb: Any, sessions: CodingSessions) -> None:
    """The coding_session_* tools (group `shell`). Starting and following up are external: they run an agent that edits
    a repo on this Mac, so an unattended run only proposes them and a chat asks first."""
    from .tools import ToolSpec, _obj, tool_error
    tb.coding = sessions

    def available() -> bool:
        return claude_binary() is not None or opencode.binary() is not None or pi.binary() is not None

    def installed() -> str:
        return ", ".join(CodingSessions.installed_agents()) or "none"

    def harnesses() -> list[dict[str, Any]]:
        have, default = CodingSessions.installed_agents(), None
        try:
            default = sessions.default_agent()
        except Exception:
            pass
        return [{"id": n, "name": AGENT_NAMES[n], "installed": n in have, "default": n == default} for n in AGENTS]

    def unattended(name: str) -> dict[str, Any]:
        return tool_error(f"{name} starts or steers a coding agent on this Mac and this is an unattended background run, "
                          "so it cannot run here.", alternative="describe the task in your report for the user")

    def view(sid: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        row = sessions.get(str(sid or ""))
        return (sessions.refresh(row), None) if row else (None, tool_error(f"No coding session '{sid}'.", field="id", alternative="coding_session_list shows the ids"))

    async def coding_session_start(ctx: dict[str, Any], repo_path: str, prompt: str, agent: str | None = None,
                                   new_worktree: bool | None = None, branch: str | None = None, model: str | None = None,
                                   permission_mode: str | None = None, name: str | None = None) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_session_start")
        try:
            row = await sessions.start(agent, repo_path, prompt, new_worktree=None if new_worktree is None else bool(new_worktree),
                                       branch=branch, model=model,
                                       permission_mode=permission_mode, name=name, ctx=ctx)
        except (CodingError, shell.ShellError) as e:
            return tool_error(shell._scrub(str(e)))
        agent = row["agent"]
        if row["status"] == "failed":
            return tool_error(row.get("detail") or "The session did not start.",
                              alternative="fix the cause named above, or run the task another way")
        note = ("Started. It works in the background; coding_session_status(id) reads progress. "
                + ("If it needs a permission answer its status becomes needs_you; coding_session_send answers a question, but a permission is approved by the user in the session (`claude attach <external_id>`)."
                   if agent == "claude" else f"{AGENT_NAMES[agent]} runs until it finishes or coding_session_stop; send a follow-up to continue."))
        return {**summary(row), "note": note}

    spec = ToolSpec("coding_session_start",
                    "Start a coding agent on a repo and let it work in the background. Leave `agent` out to use the user's default "
                    "harness (the Coding agent setting; if it is 'any', the first installed of claude, opencode, pi); pass it only when the "
                    f"user asked for a specific one: 'claude' (Claude Code), 'opencode' or 'pi' (installed here: {installed()}). "
                    "Returns at once with a session id; use coding_session_wait to block until it settles. repo_path must be a git repo anywhere on this Mac; new_worktree=true gives "
                    "the agent its own branch and git worktree under <repo>/.claude/worktrees so the repo's checkout stays "
                    "untouched (on a repo's main checkout every agent gets one unless new_worktree=false). Give a complete, self-contained task: it does not see this conversation. Claude Code runs OUTSIDE "
                    "the OS sandbox with the user's own account and tools, and asks for permission inside its own session "
                    "(status needs_you; coding_session_send answers a question, but only the user approves a permission, with `claude attach <id>`); in every mode it is refused Grain's own data folder and app "
                    "and asks before credential stores, deletes, force-pushes and sending mail. OpenCode and pi run inside the OS sandbox (Grain's own data folder and app, credential stores and the "
                    "files that run code later are off limits); pi has no permission prompts of its own, so the sandbox is all that limits it. permission_mode ('acceptEdits', 'auto', 'dontAsk' or 'bypassPermissions', "
                    "Claude Code only) overrides the mode this session runs in (default follows Grain's permission mode); only 'bypassPermissions' "
                    "(under Auto or Manual) asks the user first. Leave it out unless the user asked. Follow progress with coding_session_wait (blocks) or coding_session_status (one look) and review with coding_session_diff.",
                    _obj({"agent": {"type": "string", "enum": list(AGENTS), "description": "Optional: omit to use the user's default harness"},
                          "repo_path": {"type": "string", "description": "Absolute path of a git repo on this Mac"},
                          "prompt": {"type": "string", "description": "The task, with the files or folders it concerns"},
                          "new_worktree": {"type": "boolean", "description": "Default: true on a repo's main checkout, else false"},
                          "branch": {"type": "string", "description": "Branch for the new worktree (default grain/<task>-<hex>); never main/master"},
                          "model": {"type": "string"},
                          "permission_mode": {"type": "string", "enum": ["acceptEdits", "auto", "dontAsk", "bypassPermissions"]},
                          "name": {"type": "string", "description": "A short label for the session"}},
                         ["repo_path", "prompt"]),
                    coding_session_start, "shell", "external",
                    examples=[{"repo_path": "/Users/me/code/app", "prompt": "Add a --dry-run flag to cli.py and cover it in tests",
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
        return {"harnesses": harnesses(), "sessions": rows}

    async def coding_session_status(ctx: dict[str, Any], id: str, lines: int = 30, events: int = 0) -> Any:
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
        try:
            want = max(0, min(int(events or 0), 100))
        except (TypeError, ValueError):
            want = 0
        if want:
            out["events"] = recent_events(sessions.db, row["id"], want)
        return out

    async def coding_session_summary(ctx: dict[str, Any], id: str) -> Any:
        row, err = view(id)
        if err:
            return err
        shell.taint(ctx, f"coding_session:summary:{row['id']}")  # detail and the waiting text were written by a coding agent
        return brief(row)

    async def coding_session_wait(ctx: dict[str, Any], ids: list[str], mode: str = "all", timeout_s: int = 120) -> Any:
        try:
            out = await sessions.wait(list(ids or []), "any" if mode == "any" else "all", timeout_s)
        except CodingError as e:
            return tool_error(shell._scrub(str(e)), field="ids")
        shell.taint(ctx, "coding_session:wait:" + ",".join(r["id"] for r in out["sessions"]))  # logs and details are agent-written
        return {**out, "sessions": [summary(r, 1500) for r in out["sessions"]]}

    async def coding_session_send(ctx: dict[str, Any], id: str, message: str) -> Any:
        if ctx.get("proposal_only"):
            return unattended("coding_session_send")
        try:
            row = await sessions.send(str(id), message, ctx)
        except CodingError as e:
            return tool_error(shell._scrub(str(e)), alternative=e.alt)
        except shell.ShellError as e:
            return tool_error(shell._scrub(str(e)), alternative="start a new session")
        return {**summary(row), "note": row["note"]} if row.get("note") else summary(row)

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
        shell.taint(ctx, f"coding_session:diff:{sessions.get(str(id))['id']}")  # agent-written content
        return out

    sid = {"id": {"type": "string"}}
    for name, desc, params, fn, danger in (
        ("coding_session_list", "List coding sessions (newest first) with their status, plus `harnesses`: each supported coding agent with "
         "installed and default flags. needs_you means the session waits for an answer (a question or a permission prompt).", _obj({}, []), coding_session_list, "safe"),
        ("coding_session_status", "One non-blocking look at a coding session: status, current step and the end of its log (refreshed first). "
         "Use it to inspect (coding_session_summary is the cheaper look); to wait for a session to settle use coding_session_wait. events=N adds the last N structured "
         "events (tool calls, messages) when recorded.",
         _obj({**sid, "lines": {"type": "integer", "default": 30, "description": "Log lines to return (1-200)"},
               "events": {"type": "integer", "default": 0, "description": "Also return the last N structured events (0 = none, max 100)"}}, ["id"]), coding_session_status, "safe"),
        ("coding_session_wait", "Wait (without polling) until the named coding sessions have finished, stopped, failed or need the user: "
         "mode 'all' (default) or 'any' of them; returns their summaries, or timed_out=true after timeout_s (default 120, max 600): "
         "call it again to keep waiting. Prefer this to repeated coding_session_status calls.",
         _obj({"ids": {"type": "array", "items": {"type": "string"}}, "mode": {"type": "string", "enum": ["all", "any"], "default": "all"},
               "timeout_s": {"type": "integer", "default": 120}}, ["ids"]), coding_session_wait, "safe"),
        ("coding_session_summary", "Cheap look at one session: status, branch, worktree, ids, last activity (time and idle seconds) and, when "
         "it needs_you, what it waits on (waiting_on: kind permission|question, text), else its detail and last log line. No log tail.",
         _obj(sid, ["id"]), coding_session_summary, "safe"),
        ("coding_session_send", "Send a message to a coding session: a follow-up to one that is done, failed or stopped (to fix or extend; it keeps "
         "its worktree and context, so do not start a new session for it), or the answer to a Claude Code session that needs_you "
         "(Claude Code only: OpenCode and pi run in the sandbox with no prompts and never wait for one; for Claude Code the session is stopped "
         "and resumed under the same id). A reply NEVER approves a pending permission prompt: the result's note says so, and the user approves it "
         "in the session (`claude attach <id>`). A working session cannot take one: wait or stop it first. Takes the Grain id, external_id or session_id. "
         "Refused while another session is live in the same worktree. A resumed Claude Code session may continue as a copy with a new external_id. "
         "Asks the user in Manual mode, and whenever the run read other untrusted content.",
         _obj({**sid, "message": {"type": "string"}}, ["id", "message"]), coding_session_send, "external"),
        ("coding_session_stop", "Stop a running coding session. It keeps its files and can be continued with "
         "coding_session_send. Never removes a session.", _obj(sid, ["id"]), coding_session_stop, "executes"),
        ("coding_session_diff", "What a coding session changed in its worktree: git status, diff stat and new files and the commits "
         "since the session started; full=true adds the whole working-tree patch (cut at 60 KB).",
         _obj({**sid, "full": {"type": "boolean", "default": False}}, ["id"]), coding_session_diff, "safe"),
    ):
        s = ToolSpec(name, desc, params, fn, "shell", danger, examples=[{"id": "a1b2c3d4e5f60718"}] if name not in ("coding_session_list", "coding_session_wait") else [])
        s.available_fn = available
        if name == "coding_session_send":
            s.default = "ask"
            s.taint_ok = lambda args, ctx: (not ctx.get("taint_unsourced")
                                            and (r := sessions.get(str(args.get("id") or ""))) is not None
                                            and own_session_taint(r["id"], ctx.get("taint_sources")))
            s.force_ask = lambda args, ctx, ok=s.taint_ok: bool(ctx.get("tainted")) and not ok(args, ctx)
        tb.specs[name] = s
