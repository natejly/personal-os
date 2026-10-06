"""Ship checklist: tests -> push -> pull request -> merge for one branch of a repo, as a job's deliverable.

Steps run in that order, each only after the one before it is green; a red step stops the run and the rest are skipped.
Merge never runs on its own: it waits in `awaiting_confirm` until the user confirms (POST /ship/{id}/confirm), and a
retry asks again. Nothing here can force-push or push to main/master: the push argv is built here from a validated
branch name, and every argv is checked once more right before it runs.

Commands go through the shell's job registry (shell.run_fixed): the test command in the shell sandbox, git and gh with
the user's own network and credentials. Every change is saved and published on the app `events` topic as a
`ship_checklist` event carrying the whole row.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import mac
from .db import new_id

STEPS = ("tests", "push", "pr", "merge")
PROTECTED = frozenset({"main", "master"})
LOG_TAIL = 4000
TEST_TIMEOUT, GIT_TIMEOUT = 1800.0, 180.0
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
PR_URL_RE = re.compile(r"https://\S+/pull/\d+")
FORCE_FLAGS = ("-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "-d")

# (argv, cwd, sandboxed, timeout) -> (ok, output)
Runner = Callable[[list[str], str, bool, float], Awaitable[tuple[bool, str]]]


class ShipError(ValueError):
    pass


def check_branch(branch: str, base: str) -> None:
    """A plain branch name each, never a refspec, and never pushing to main/master or straight onto the base."""
    for label, b in (("branch", branch), ("base", base)):
        if not BRANCH_RE.fullmatch(b) or ".." in b or "//" in b or b.endswith((".lock", "/", ".")) or b.startswith("refs/"):
            raise ShipError(f"'{b}' is not a branch name this checklist accepts ({label}).")
    if branch.lower() in PROTECTED:
        raise ShipError(f"The checklist never pushes to {branch}. Ship from a feature branch and merge through the pull request.")
    if branch == base:
        raise ShipError("branch and base are the same branch: there would be nothing to merge.")


def refuse_force(argv: list[str]) -> None:
    """The last line of defence before a git/gh command runs: no force flag, no +refspec, no push onto main/master."""
    if argv[:1] != ["git"] or "push" not in argv:
        return
    for a in argv[argv.index("push") + 1:]:
        if a in FORCE_FLAGS or a.startswith("--force") or a.startswith("+"):
            raise ShipError(f"Refused: '{a}' would rewrite or delete remote history. The checklist never force-pushes.")
        dest = a.split(":", 1)[-1].removeprefix("refs/heads/")
        if dest.lower() in PROTECTED:
            raise ShipError(f"Refused: the checklist never pushes to {dest}.")


def check_test_command(cmd: str | None) -> None:
    if cmd and (re.search(r"\bgit\b[^;&|]*\bpush\b", cmd) or "--force" in cmd):
        raise ShipError("The test command may not push: pushing is the checklist's own step, and it never forces.")


def detect_test_command(repo: Path) -> str | None:
    """npm test when package.json has a real test script, else pytest when the repo looks like a Python project."""
    try:
        scripts = json.loads((repo / "package.json").read_text()).get("scripts") or {}
        t = scripts.get("test") if isinstance(scripts, dict) else None
        if isinstance(t, str) and t.strip() and "no test specified" not in t:
            return "npm test"
    except (OSError, ValueError, AttributeError):
        pass
    if any((repo / f).exists() for f in ("pytest.ini", "pyproject.toml", "setup.cfg", "tests")):
        return "python3 -m pytest"
    return None


def _fresh_steps() -> list[dict[str, Any]]:
    return [{"name": n, "status": "pending", "started_at": None, "ended_at": None, "log_tail": "", "link": None} for n in STEPS]


def _reset(step: dict[str, Any]) -> None:
    step.update(status="pending", started_at=None, ended_at=None, log_tail="", link=None)


class Ship:
    def __init__(self, db: Any, run: Runner, publish: Callable[[str, Any], None],
                 job_of: Callable[[str | None], str | None] | None = None):
        self.db, self.run, self.publish = db, run, publish
        self.job_of = job_of or (lambda _cid: None)   # conversation id -> the job it belongs to
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._recover()

    # ---- rows ----
    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["steps"] = json.loads(d.pop("steps_json"))
        return d

    def get(self, cid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM ship_checklists WHERE id = ?", (cid,)).fetchone()
        return self._row(r) if r else None

    def latest(self, job_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM ship_checklists WHERE job_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                          (job_id,)).fetchone()
        return self._row(r) if r else None

    def _save(self, row: dict[str, Any]) -> None:
        row["updated_at"] = time.time()
        with self.db.tx() as c:
            c.execute("UPDATE ship_checklists SET status=?, steps_json=?, pr_url=?, merged_sha=?, updated_at=? WHERE id=?",
                      (row["status"], json.dumps(row["steps"]), row.get("pr_url"), row.get("merged_sha"), row["updated_at"], row["id"]))
        self.publish("ship_checklist", row)

    def _recover(self) -> None:
        """A checklist that was mid-step when the app stopped: that step is red, the rest skipped. Retry resumes it.
        One waiting for a merge confirmation keeps waiting: nothing was running."""
        with self.db.tx() as c:
            rows = [self._row(r) for r in c.execute("SELECT * FROM ship_checklists WHERE status = 'running'").fetchall()]
        for row in rows:
            for s in row["steps"]:
                if s["status"] == "running":
                    s.update(status="red", ended_at=time.time(), log_tail=(s["log_tail"] + "\nInterrupted: the app stopped while this step ran.").strip())
            self._fail(row)

    # ---- the user's and the agent's verbs ----
    def create(self, *, repo_path: str, branch: str, base: str = "main", test_command: str | None = None,
               job_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        branch, base = str(branch or "").strip(), str(base or "main").strip()
        test_command = (str(test_command).strip() or None) if test_command else None
        check_branch(branch, base)
        check_test_command(test_command)
        repo = Path(str(repo_path or "")).expanduser()
        if not repo.is_absolute():
            raise ShipError("repo_path must be an absolute path to the repository folder.")
        repo = repo.resolve()
        if why := mac.protected_reason(repo):
            raise ShipError(f"{repo}: {why}.")
        if not (repo / ".git").exists():
            raise ShipError(f"{repo} is not a git repository (no .git).")
        t = time.time()
        row = {"id": new_id(), "job_id": job_id, "run_id": run_id, "repo_path": str(repo), "branch": branch, "base": base,
               "test_command": test_command, "status": "running", "steps": _fresh_steps(), "pr_url": None,
               "merged_sha": None, "created_at": t, "updated_at": t}
        with self.db.tx() as c:
            c.execute("INSERT INTO ship_checklists(id, job_id, run_id, repo_path, branch, base, test_command, status, steps_json, "
                      "pr_url, merged_sha, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (row["id"], job_id, run_id, row["repo_path"], branch, base, test_command, row["status"],
                       json.dumps(row["steps"]), None, None, t, t))
        self.publish("ship_checklist", row)
        return row

    def start(self, cid: str) -> None:
        self.tasks[cid] = asyncio.create_task(self._drive(cid))

    def confirm(self, cid: str) -> dict[str, Any]:
        """The user's go for the merge. Checked and flipped to running without an await in between, so a double click
        or a second window cannot merge twice."""
        row = self._known(cid)
        merge = row["steps"][-1]
        if row["status"] != "awaiting_confirm" or merge["status"] != "awaiting_confirm" or cid in self.tasks:
            raise ShipError("Nothing is waiting for a merge confirmation on this checklist.")
        merge["status"], row["status"] = "running", "running"
        self._save(row)
        self.tasks[cid] = asyncio.create_task(self._drive(cid, merge_confirmed=True))
        return row

    def retry(self, cid: str) -> dict[str, Any]:
        """Re-run from the first step that is not green. A merge asks for confirmation again."""
        row = self._known(cid)
        if cid in self.tasks or row["status"] not in ("failed", "cancelled"):
            raise ShipError("Only a failed or cancelled checklist can be retried.")
        for s in row["steps"]:
            if s["status"] != "green":
                _reset(s)
        row["status"] = "running"
        self._save(row)
        self.start(cid)
        return row

    async def cancel(self, cid: str) -> dict[str, Any]:
        row = self._known(cid)
        task = self.tasks.pop(cid, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            row = self._known(cid)
        if row["status"] in ("done", "cancelled"):
            return row
        for s in row["steps"]:
            if s["status"] == "running":
                s.update(status="red", ended_at=time.time(), log_tail=(s["log_tail"] + "\nCancelled.").strip())
            elif s["status"] in ("pending", "awaiting_confirm"):
                s["status"] = "skipped"
        row["status"] = "cancelled"
        self._save(row)
        return row

    def _known(self, cid: str) -> dict[str, Any]:
        row = self.get(cid)
        if row is None:
            raise KeyError(cid)
        return row

    # ---- the runner ----
    def _fail(self, row: dict[str, Any]) -> None:
        for s in row["steps"]:
            if s["status"] in ("pending", "awaiting_confirm"):
                s["status"] = "skipped"
        row["status"] = "failed"
        self._save(row)

    async def _drive(self, cid: str, merge_confirmed: bool = False) -> None:
        try:
            row = self._known(cid)
            for step in row["steps"]:
                if step["status"] == "green":
                    continue
                if step["name"] == "merge" and not merge_confirmed:
                    step["status"], row["status"] = "awaiting_confirm", "awaiting_confirm"
                    self._save(row)
                    return
                await self._run_step(row, step)
                if step["status"] != "green":
                    self._fail(row)
                    return
            row["status"] = "done"
            self._save(row)
        finally:
            if self.tasks.get(cid) is asyncio.current_task():
                self.tasks.pop(cid, None)

    async def _run_step(self, row: dict[str, Any], step: dict[str, Any]) -> None:
        step.update(status="running", started_at=time.time(), ended_at=None, log_tail="", link=None)
        self._save(row)
        try:
            ok, out, link = await self._exec(row, step["name"])
        except ShipError as e:
            ok, out, link = False, str(e), None
        except Exception as e:  # noqa: BLE001 - a step that blows up is a red step, not a stuck checklist
            ok, out, link = False, f"{type(e).__name__}: {e}", None
        step.update(status="green" if ok else "red", ended_at=time.time(), log_tail=out[-LOG_TAIL:], link=link)
        if step["name"] == "pr" and link:
            row["pr_url"] = link
        self._save(row)

    async def _cmd(self, row: dict[str, Any], argv: list[str], *, sandboxed: bool = False,
                   timeout: float = GIT_TIMEOUT) -> tuple[bool, str]:
        refuse_force(argv)
        return await self.run(argv, row["repo_path"], sandboxed, timeout)

    async def _exec(self, row: dict[str, Any], name: str) -> tuple[bool, str, str | None]:
        branch, base = row["branch"], row["base"]
        check_branch(branch, base)  # again: the row could have been edited behind the API's back
        if name == "tests":
            cmd = row.get("test_command") or detect_test_command(Path(row["repo_path"]))
            if not cmd:
                return False, "No test command found (no package.json test script, no Python project). Give test_command.", None
            check_test_command(cmd)
            ok, out = await self._cmd(row, ["/bin/zsh", "-c", cmd], sandboxed=True, timeout=TEST_TIMEOUT)
            return ok, f"$ {cmd}\n{out}", None
        if name == "push":
            ok, out = await self._cmd(row, ["git", "push", "-u", "origin", f"refs/heads/{branch}:refs/heads/{branch}"])
            return ok, out, None
        if name == "pr":
            ok, out = await self._cmd(row, ["gh", "pr", "list", "--head", branch, "--base", base, "--state", "open",
                                            "--json", "url", "-q", ".[0].url"])
            if ok and (m := PR_URL_RE.search(out)):
                return True, f"Reusing the open pull request {m.group(0)}", m.group(0)
            ok, out = await self._cmd(row, ["gh", "pr", "create", "--fill", "--base", base, "--head", branch])
            m = PR_URL_RE.search(out)
            return ok and bool(m), out, m.group(0) if m else None
        if name == "merge":
            url = row.get("pr_url")
            if not url:
                return False, "There is no pull request to merge.", None
            ok, out = await self._cmd(row, ["gh", "pr", "merge", "--merge", url])
            if ok:
                ok2, sha = await self._cmd(row, ["gh", "pr", "view", url, "--json", "mergeCommit", "-q", ".mergeCommit.oid"])
                if ok2 and re.fullmatch(r"[0-9a-f]{7,64}", sha.strip()):
                    row["merged_sha"] = sha.strip()
            return ok, out, url
        raise ShipError(f"unknown step {name}")


def summary(row: dict[str, Any]) -> dict[str, Any]:
    """The tool-facing view: no log tails beyond the failing step's."""
    return {"ship_checklist_id": row["id"], "status": row["status"], "repo_path": row["repo_path"], "branch": row["branch"],
            "base": row["base"], "pr_url": row.get("pr_url"), "merged_sha": row.get("merged_sha"),
            "steps": [{"name": s["name"], "status": s["status"], **({"log_tail": s["log_tail"][-1500:]} if s["status"] == "red" else {})}
                      for s in row["steps"]]}


def register(tb: Any, ship: Ship) -> None:
    """ship_checklist / ship_status. External: it pushes code and opens a pull request, so a scheduled job's call
    becomes a proposal the user accepts, and in a chat it asks first (like python_install)."""
    from .tools import ToolSpec, _obj, tool_error
    tb.ship = ship

    async def ship_checklist(ctx: dict[str, Any], repo_path: str, branch: str, base: str = "main",
                             test_command: str | None = None) -> Any:
        if ctx.get("proposal_only"):  # _call_tool records it as a proposal first; this is the second gate
            return tool_error("ship_checklist pushes code and opens a pull request, so an unattended run only proposes it.")
        job_id = (ctx.get("conv_settings") or {}).get("job_id") or ship.job_of(ctx.get("conversation_id"))
        try:
            row = ship.create(repo_path=repo_path, branch=branch, base=base, test_command=test_command,
                              job_id=job_id, run_id=ctx.get("run_id"))
        except ShipError as e:
            return tool_error(str(e))
        ship.start(row["id"])
        return {**summary(row), "note": "Started: tests, then push, then the pull request. The merge waits for the user to "
                                        "confirm it on the checklist card; you cannot merge. ship_status(id) reads progress."}

    spec = ToolSpec("ship_checklist", "Ship a branch: run the repo's tests, then push the branch to origin, then open (or reuse) "
                    "a pull request into base. Each step runs only after the previous one passed. The merge is never "
                    "automatic: it waits for the user to confirm it. Never pushes main/master and never force-pushes. "
                    "repo_path must be a git repository folder on this Mac. test_command defaults to npm test or pytest.",
                    _obj({"repo_path": {"type": "string"}, "branch": {"type": "string"},
                          "base": {"type": "string", "default": "main"}, "test_command": {"type": "string"}},
                         ["repo_path", "branch"]),
                    ship_checklist, "shell", "external",
                    examples=[{"repo_path": "/Users/me/code/app", "branch": "fix-login", "base": "main"}])
    spec.default = "ask"
    tb.specs["ship_checklist"] = spec

    async def ship_status(ctx: dict[str, Any], id: str) -> Any:
        row = ship.get(str(id))
        return summary(row) if row else tool_error(f"No ship checklist '{id}'.", field="id")
    tb.specs["ship_status"] = ToolSpec("ship_status", "Read a ship checklist's progress: each step's status, the pull "
                                       "request link, and the log of a failed step.", _obj({"id": {"type": "string"}}, ["id"]),
                                       ship_status, "shell", "safe", examples=[{"id": "a1b2c3d4"}])
