"""`opencode_run`: hand a coding task to the opencode CLI in any folder on this Mac.

opencode is a terminal coding agent installed separately on this Mac (`brew install opencode`); its headless mode
(`opencode run "<prompt>"`) reads and edits files, runs commands and reports back. Grain only links to that
installation: it does not bundle opencode, configure its provider or relocate its state.

It runs the way shell_run does: under the OS sandbox (sandbox.shell_profile: it may write anywhere but Grain's own
data folder and app, the credential stores and the files that run code later), starting in the desk workspace in a
desk, else the home folder, under the same job registry (shell.ShellJobs) so timeouts, background promotion,
shell_poll and shell_kill all apply. A kill or timeout signals the job's whole process group; `opencode run` puts its
private `opencode serve --stdio` in a group of its own, but that server exits as soon as its parent's pipe closes.

Isolation: a folder inside a git repository's main checkout (where the user, or another agent, may be working) is not
edited in place. The run gets a fresh worktree on a new branch off the checkout's HEAD, at
<repo>/.claude/worktrees/opencode-<task>-<hex> (the coding sessions' layout), and starts in the same subfolder there.
Uncommitted changes in the checkout are not in it. in_place=true opts out; a folder that already is a linked worktree
runs in place. continue_session=true reuses the worktree this chat's last run in that repo used. When a run ends in a
repo the result carries a git report (worktree, branch, base, `git status --short`, diff stat and commits against the
base, each capped); a worktree it created that ended with no change and no commit is removed again, never one with
changes. The worktree is created and inspected by Grain itself with fixed git argv (no hooks, no fsmonitor); the
sandbox profile is unchanged (the worktree and the repo's .git are already writable, .git/hooks and .git/config not).

What it keeps from opencode's own install:

- Model and auth: whatever `opencode auth login` and the user's own config (~/.config/opencode) set up. Grain passes
  `-m` only when the caller names a model, and otherwise lets opencode pick its default. Nothing of Grain's provider
  setup is injected, so a model id here is opencode's own, not a Grain alias.
- State: sessions, caches and auth stay in the user's XDG dirs, so `opencode` in a terminal and opencode under Grain
  share one history and `continue_session=true` resumes the last session for that folder.

Whatever opencode prints came from a model with network access, so the reply is marked tainted like a networked
shell_run, and a reply that already read untrusted content must ask before it can call this.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import mac, sandbox, shell

# Where the binary usually lands: Homebrew, the official installer (~/.opencode/bin), npm -g, ~/.local/bin.
BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.opencode/bin", "~/.local/bin", "~/.bun/bin")
INSTALL_HINT = ("opencode is not installed on this Mac. The user can install it with `brew install opencode` "
                "(or `npm i -g opencode-ai`), then try again.")
MAX_PROMPT = 20_000
DEFAULT_MODEL = "fireworks-ai/accounts/fireworks/models/deepseek-v4p1-flash"  # opencode provider/model spelling; a caller model wins
GIT_TIMEOUT, REPORT_CAP = 60, 4_000
# (conversation id, repo top folder) -> the worktree its last run used: continue_session goes back to it. In memory: after
# a restart the caller passes the worktree as cwd (the result named it).
_WORKTREES: dict[tuple[str, str], dict[str, str]] = {}


def binary() -> str | None:
    for d in BIN_DIRS:
        p = os.path.join(os.path.expanduser(d), "opencode")
        if os.access(p, os.X_OK):
            return p
    return shutil.which("opencode")


# opencode reaches its own provider (whatever the user configured) and its catalog, both over https. loopback is
# granted separately: `opencode run` talks to a private server of its own, and a local proxy is a common endpoint.
ALLOW_HOSTS = ["*:443"]


def summarize(raw: str) -> tuple[str, str | None]:
    """(what to show the model, session id) from `--format json` output: the assistant's text parts and one line per
    tool call, in order. Lines that are not events are kept as they are (a crash message, an install notice)."""
    out: list[str] = []
    session: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            if line:
                out.append(line)
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        session = session or ev.get("sessionID") or (ev.get("part") or {}).get("sessionID")
        part = ev.get("part") or ev
        kind = part.get("type") or ev.get("type")
        if kind == "text" and part.get("text"):
            out.append(str(part["text"]).rstrip())
        elif kind == "tool":
            state = part.get("state") or {}
            title = state.get("title") or (state.get("input") or {}).get("command") or (state.get("input") or {}).get("filePath") or ""
            out.append(f"[{part.get('tool', 'tool')}] {str(title)[:200]}".rstrip())
        elif kind == "error" or ev.get("error"):
            err = ev.get("error") or part.get("error") or {}
            msg = err.get("data", {}).get("message") if isinstance(err, dict) else str(err)
            out.append(f"error: {msg or err}")
    return "\n".join(out).strip(), session


def _git(cwd: str | Path, *args: str) -> tuple[bool, str]:
    """A fixed git command run by Grain itself (not the agent): (exit 0, stdout + stderr)."""
    from .codingagents import GIT
    try:
        r = subprocess.run([*GIT, "-c", "core.hooksPath=/dev/null", *args], cwd=str(cwd), capture_output=True, text=True,
                           timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError) as e:
        return False, type(e).__name__
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def repo_of(path: Path) -> tuple[Path, bool] | None:
    """(the repo's top folder, whether `path` is in its main checkout rather than a linked worktree), or None outside a
    repository's working tree."""
    ok, out = _git(path, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir")
    lines = out.splitlines()
    if not ok or len(lines) != 3:
        return None
    top, gdir, common = lines
    return Path(os.path.realpath(top)), os.path.realpath(gdir) == os.path.realpath(os.path.join(path, common))


def isolate(where: Path, conversation_id: str | None, prompt: str, continue_session: bool) -> dict[str, Any] | None:
    """The worktree a run in `where` should use ({worktree, branch, base, repo, cwd}), or None when `where` is not in a
    repo's main checkout. Raises Refused when git cannot make one."""
    from .codingagents import _slug, check_branch, worktree_path
    found = repo_of(where)
    if not found or not found[1]:
        return None
    top = found[0]
    key = (conversation_id or "", str(top))
    wt = _WORKTREES.get(key) if continue_session else None
    if not (wt and os.path.isdir(wt["worktree"])):
        ok, base = _git(top, "rev-parse", "HEAD")
        if not ok:
            raise Refused(f"{top} has no commit yet to branch a worktree from, so nothing was run.",
                          "in_place=true to let opencode work in the checkout itself")
        branch = f"grain/opencode-{_slug(prompt)[:24].strip('-')}-{secrets.token_hex(2)}"
        check_branch(branch)
        path = worktree_path(top, branch)
        ok, out = _git(top, "worktree", "add", "-b", branch, str(path), base)
        if not ok:
            raise Refused(shell._scrub(f"git could not create a worktree for this run: {out[-300:]}"),
                          "in_place=true to let opencode work in the checkout itself")
        wt = _WORKTREES[key] = {"worktree": str(path), "branch": branch, "base": base, "repo": str(top)}
    try:
        sub = Path(wt["worktree"]) / where.relative_to(top)
    except ValueError:
        sub = Path(wt["worktree"])
    return {**wt, "cwd": str(sub if sub.is_dir() else wt["worktree"])}


def git_report(where: str, base: str | None, owned: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a finished run left in its repo: status, diff stat and commits against `base`, each capped. A worktree this
    module created (`owned`) that has neither a change nor a commit is removed with its branch (git refuses either if
    anything is there)."""
    def cap(text: str) -> str:
        text = shell._scrub(text)
        return text if len(text) <= REPORT_CAP else text[:REPORT_CAP] + "\n[cut]"

    _, status = _git(where, "status", "--short")
    _, stat = _git(where, "diff", base or "HEAD", "--stat", "--no-ext-diff", "--no-textconv")
    commits = _git(where, "log", "--oneline", f"{base}..HEAD")[1] if base else ""
    out: dict[str, Any] = {"status": cap(status) or "clean", "diff_stat": cap(stat), "commits": cap(commits)}
    if owned:
        out = {k: owned[k] for k in ("worktree", "branch", "base", "repo")} | out
        if not status and not commits and _git(owned["repo"], "worktree", "remove", owned["worktree"])[0]:
            _git(owned["repo"], "branch", "-d", owned["branch"])
            for k, v in list(_WORKTREES.items()):
                if v["worktree"] == owned["worktree"]:
                    _WORKTREES.pop(k, None)
            out["removed"] = True
    return out


class Refused(shell.ShellError):
    """A launch that cannot start: the message is for the model, `alternative` is what to do instead."""

    def __init__(self, message: str, alternative: str | None = None):
        super().__init__(message)
        self.alternative = alternative


def _desk_root(tb: Any, ctx: dict[str, Any]) -> Path | None:
    did = str(ctx.get("desk_id") or "")
    if did and getattr(tb, "workspace", None) is not None:
        try:
            return tb.workspace.ensure(did)
        except Exception:  # noqa: BLE001
            return None
    return None


def default_timeout(settings: dict[str, Any], timeout_s: Any, background: bool) -> int:
    default_t = shell.BACKGROUND_TIMEOUT if background else max(int(settings.get("shellTimeoutSec") or shell.DEFAULT_TIMEOUT), 300)
    try:
        return max(1, min(int(timeout_s or default_t), shell.MAX_TIMEOUT))
    except (TypeError, ValueError):
        return default_t


async def launch(tb: Any, ctx: dict[str, Any], prompt: str, *, cwd: str | None, continue_session: bool = False,
                 model: str | None = None, background: bool = False, timeout: int | None = None,
                 conversation_id: str | None = None, run_id: str | None = None, notify: bool = True,
                 on_timeout: str | None = None, pool: str = "shell",
                 max_background: int | None = None, no_timeout: bool = False) -> tuple[shell.Job, dict[str, Any]]:
    """Start `opencode run` under the OS sandbox as a tracked job and return (job, {cwd, model, sandboxed}).
    Raises Refused (a ShellError) when it cannot start. The caller owns waiting for the job and reading its output.
    `conversation_id` defaults to the chat's; a caller that must outlive the chat's reply passes its own."""
    s = ctx.get("settings") or tb.settings()
    exe = binary()
    if not exe:
        raise Refused(INSTALL_HINT, "shell_run and fs_edit for the change yourself")
    if not shell.sandbox_available():
        raise Refused("The OS sandbox opencode runs in is not available here (it needs macOS sandbox-exec), so nothing "
                      "was run.", "fs_edit and shell_run for the change yourself")
    use_model = str(model or "").strip() or DEFAULT_MODEL
    dr = _desk_root(tb, ctx)
    where = shell.resolve_cwd(cwd, dr or mac.home(), dr)
    timeout = None if no_timeout else timeout or default_timeout(s, None, background)
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="pos-opencode-"))
    env = shell.scrubbed_env(tmp)
    env["PATH"] = f"{os.path.dirname(exe)}:{env['PATH']}"
    # ~/.npm and ~/.cache/pip are outside the sandbox's writable paths, so package installs use the per-launch tmp dir
    env["npm_config_cache"], env["PIP_CACHE_DIR"] = os.path.join(tmp, "npm-cache"), os.path.join(tmp, "pip-cache")
    # No XDG overrides and no injected config: opencode reads its own ~/.config/opencode and writes its own state,
    # so a session started here is the same session the user sees from a terminal.
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    argv = [exe, "run", "--standalone", "--format", "json"]  # standalone: no shared daemon outside the sandbox
    if use_model:  # otherwise opencode's own default model stands
        argv += ["-m", use_model]
    if continue_session:
        argv.append("--continue")
    argv.append(prompt)
    writable = [tmp]
    if dr:
        writable.append(str(dr))
    # loopback: `opencode run` spawns a private server on a random local port and talks to it
    profile = sandbox.shell_profile(writable, network=False, allow_hosts=ALLOW_HOSTS, loopback=True)
    shell.taint(ctx, "opencode_run:network")  # a model with network access wrote whatever comes back
    shown_cmd = f"opencode run {prompt[:160]!r}"
    try:
        job = await tb.shell.start(["sandbox-exec", "-p", profile, *argv], command=shown_cmd, cwd=str(where), env=env, tmp=tmp,
                                   conversation_id=conversation_id if conversation_id is not None else ctx.get("conversation_id"),
                                   run_id=run_id if run_id is not None else ctx.get("run_id"),
                                   background=bool(background), timeout=timeout, notify=notify,
                                   max_background=max_background or int(s.get("shellMaxBackground") or 4), pool=pool,
                                   on_timeout=on_timeout or ("background" if ctx.get("desk_id") else "kill"))
    except shell.ShellError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return job, {"cwd": shell._scrub(str(where)), "model": use_model or "opencode default", "sandboxed": True}


def register(tb: Any) -> None:
    """Add opencode_run to a Toolbox (group `shell`). shell.register must have run first (it owns tb.shell)."""
    from .tools import ToolSpec, _obj, tool_error

    jobs: shell.ShellJobs = tb.shell

    def cfg(ctx: dict[str, Any]) -> dict[str, Any]:
        return ctx.get("settings") or tb.settings()

    async def opencode_run(ctx: dict[str, Any], prompt: str, cwd: str | None = None, timeout_s: int | None = None,
                           background: bool = False, continue_session: bool = False, model: str | None = None,
                           in_place: bool = False) -> Any:
        s = cfg(ctx)
        prompt = str(prompt or "").strip()
        if ctx.get("proposal_only"):
            return tool_error("opencode_run edits files and runs commands on this Mac and this is an unattended background "
                              "run, so it cannot run here.", alternative="describe the task in your report for the user")
        if not prompt:
            return tool_error("opencode_run needs a prompt.", field="prompt", example={"prompt": "add a --dry-run flag to cli.py"})
        if len(prompt) > MAX_PROMPT:
            return tool_error(f"The prompt is over {MAX_PROMPT} characters; point opencode at a file instead.", field="prompt")
        timeout = default_timeout(s, timeout_s, bool(background))
        wt: dict[str, Any] | None = None
        base: str | None = None
        if binary() and shell.sandbox_available():  # launch() names what is missing otherwise
            dr = _desk_root(tb, ctx)
            try:
                where = shell.resolve_cwd(cwd, dr or mac.home(), dr)
                wt = None if in_place else await asyncio.to_thread(isolate, where, ctx.get("conversation_id"), prompt,
                                                                   bool(continue_session))
            except shell.ShellError as e:
                return tool_error(shell._scrub(str(e)), alternative=getattr(e, "alternative", None))
            if wt:
                cwd = wt["cwd"]
            elif await asyncio.to_thread(repo_of, where):  # in place in a repo: report against where HEAD is now
                base = (await asyncio.to_thread(_git, where, "rev-parse", "HEAD"))[1] or None
        try:
            job, info = await launch(tb, ctx, prompt, cwd=cwd, continue_session=continue_session, model=model,
                                     background=bool(background), timeout=timeout)
        except shell.ShellError as e:
            if wt:  # nothing ran in it: do not leave an empty worktree behind
                await asyncio.to_thread(git_report, wt["worktree"], wt["base"], wt)
            return tool_error(shell._scrub(str(e)), alternative=getattr(e, "alternative", None))
        if wt:
            info.update(worktree=wt["worktree"], branch=wt["branch"], base=wt["base"],
                        isolation=f"Working in its own git worktree on branch {wt['branch']}, off {wt['base'][:12]} of "
                                  f"{wt['repo']}; the checkout itself is untouched. Follow up with continue_session=true "
                                  f"(or cwd={wt['worktree']}).")
        poll = (f"shell_poll(job_id='{job.id}', wait_s=300) blocks until it has new raw JSON events or ends (do not sleep "
                f"in a shell to wait) and shell_kill(job_id) stops it; you are told when it finishes" + (f", then review it with shell_run `git status` / `git diff {wt['base'][:12]}` in "
                                        f"{wt['worktree']}." if wt else "."))
        if background:
            return {"job_id": job.id, "background": True, **info, "note": f"opencode is working in the background. {poll}"}
        t0 = time.time()
        try:
            await jobs.wait(job)
        except BaseException:  # the reply was stopped: do not leave opencode editing behind it
            await jobs.kill(job)
            raise
        raw = shell._scrub(jobs.finished_output(job))
        if job.background and job.live():
            job.read_pos = job.total
            text, session = summarize(raw)
            shown, cut = shell.truncate(text)
            return {"output": shown, "truncated": cut, "still_running": True, "job_id": job.id, "session_id": session,
                    "duration_s": round(time.time() - t0, 2), **info,
                    "note": f"Still running after {timeout}s, so it carries on in the background (job_id {job.id}). {poll}"}
        if job.exit_code in (65, 71) and raw.lstrip().startswith("sandbox-exec:"):
            return tool_error(shell._scrub("The OS sandbox refused to start (" + raw.strip()[:200] + "), so nothing was run."))
        text, session = summarize(raw)
        shown, cut = shell.truncate(text)
        out: dict[str, Any] = {"exit_code": job.exit_code, "output": shown, "truncated": cut, "session_id": session,
                               "timed_out": job.status == "timed_out", "duration_s": round(time.time() - t0, 2), **info}
        if wt or base:
            out["git"] = await asyncio.to_thread(git_report, wt["worktree"] if wt else str(job.cwd), wt["base"] if wt else base, wt)
            if out["git"].get("removed"):
                out.pop("isolation", None)
                out["git"]["note"] = "opencode changed nothing and committed nothing, so its worktree and branch were removed."
        if job.status == "timed_out":
            out["note"] = f"Killed after {timeout}s. Use background=true for a long task."
        if cut and getattr(tb, "results", None) is not None and ctx.get("conversation_id"):
            row = tb.results.store(ctx["conversation_id"], ctx.get("message_id"), "opencode_run", raw,
                                   {"type": "string", "chars": len(raw)})
            out["result_id"] = row["id"]
        return out

    spec = ToolSpec("opencode_run",
                    "Hand a coding task to opencode, a terminal coding agent installed on this Mac, in a folder on this Mac (default: the desk "
                    "workspace in a desk, else the home folder). It reads and edits files and runs commands, all under the OS sandbox (Grain's own data "
                    "folder and app, credential stores and the files that run code later are off limits), using opencode's own model and sign-in, not this app's. Give it a complete, self-contained task "
                    "with the files or folder it concerns; it does not see this conversation. continue_session=true carries on "
                    "its last session in that folder, the same history the opencode CLI shows. In a git repo's main checkout it works in a fresh "
                    "worktree on a new branch off HEAD (<repo>/.claude/worktrees/opencode-...), so the checkout and anyone working in it are "
                    "untouched; in_place=true edits the checkout itself (only when the user asked for that). The result is its narration and "
                    "final answer plus a git report (worktree, branch, status, diff stat, commits); verify the change yourself (shell_run "
                    "`git diff <base>` in the worktree) before reporting it done. Default timeout 300s (max 600s), then in a desk it carries "
                    "on as a background job you follow with shell_poll(job_id, wait_s=...); background=true starts it that way.",
                    _obj({"prompt": {"type": "string", "description": "The task, with the files or folder it concerns"},
                          "cwd": {"type": "string", "description": "The folder to work in, usually a repo; relative to the default folder"},
                          "timeout_s": {"type": "integer", "default": 300}, "background": {"type": "boolean", "default": False},
                          "continue_session": {"type": "boolean", "default": False},
                          "in_place": {"type": "boolean", "default": False,
                                       "description": "Edit a repo's checkout directly instead of a fresh worktree"},
                          "model": {"type": "string", "description": "An opencode model id (provider/model), overriding its default"}},
                         ["prompt"]),
                    opencode_run, "shell", "executes",
                    examples=[{"prompt": "Add a --dry-run flag to cli.py and cover it in tests/test_cli.py", "cwd": "work/repo"},
                              {"prompt": "Fix the failing test in tests/test_parse.py", "continue_session": True}])
    spec.default = "ask"
    spec.force_ask = lambda _args, ctx: bool(ctx.get("tainted"))  # it reaches the network, so untrusted input must not drive it unasked
    spec.available_fn = lambda: binary() is not None
    tb.specs["opencode_run"] = spec
