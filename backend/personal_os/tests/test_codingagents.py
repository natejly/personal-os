"""Coding sessions (extensions/coding/sessions.py) with a fake command runner and fake claude job files.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_codingagents -v
What matters here: the paths and branches a session may use, that a permission flag appears only when the caller
asked for one, that a follow-up never goes to a session that is still working, and how the drivers' states map.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from personal_os.extensions.coding import sessions as ca
from personal_os import migrations
from personal_os.db import Database

SID = "0b7e6f2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
START_OUT = "backgrounded · 45622d2f · grain-harden-probe\n  claude agents             list sessions\n  claude attach 45622d2f    open in this terminal\n"
COPY_OUT = ("note: session 45622d2f is already running in the background, so this started a copy as fffebd7b. `claude attach 45622d2f` "
            "opens the original.\nbackgrounded · fffebd7b\n  claude agents             list sessions\n")
WOKE_OUT = "note: woke session 45622d2f with its saved options (-n, --model).\nbackgrounded · 45622d2f · grain-harden-probe\n"


class Fake:
    """Answers each command by its first words (or last word); records every argv it was asked to run."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.out: dict[str, tuple[bool, str]] = {}

    async def __call__(self, argv: list[str], cwd: str, timeout: float, env: dict[str, str] | None = None) -> tuple[bool, str]:
        self.calls.append(argv)
        for key in (" ".join(argv[:3]), " ".join(argv[:2]), argv[-1]):
            if key in self.out:
                return self.out[key]
        if "--bg" in argv:
            return True, "started deadbeef\n"
        return True, "ok"


class CodingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="codingtest-")
        self.tmp = Path(self._dir.name).resolve()
        self.repo = self.tmp / "work" / "repo"
        (self.repo / ".git").mkdir(parents=True)
        self.home = self.tmp / "claude-jobs"
        self.home.mkdir()
        self.fake = Fake()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.jobs = SimpleNamespace(jobs={})
        db = Database(self.tmp / "data")
        with contextlib.closing(db.connect()) as c:  # the coding extension's own tables, as the host migrates them
            migrations.run_extension(c, "coding", migrations.sql_steps(Path(ca.__file__).parent / "migrations"))
        self.cs = ca.CodingSessions(db, self.jobs, self.fake, lambda e, d: self.events.append((e, d)),
                                    lambda: {}, claude_home=self.home)
        p = unittest.mock.patch.object(ca, "claude_binary", return_value="/bin/claude")
        p.start()
        self.addCleanup(p.stop)
        for q in (unittest.mock.patch.object(ca.opencode, "binary", return_value="/bin/opencode"),
                  unittest.mock.patch.object(ca.pi, "binary", return_value="/bin/pi"),
                  unittest.mock.patch.object(ca, "DAEMON_FILE", self.tmp / "no-daemon.json"),
                  unittest.mock.patch.object(ca, "login_path", return_value=None)):
            q.start()
            self.addCleanup(q.stop)
        self.addCleanup(self._dir.cleanup)

    def job_files(self, jid: str, state: str, detail: str = "", lines: list[dict[str, Any]] | None = None, **extra: Any) -> None:
        d = self.home / jid
        d.mkdir(exist_ok=True)
        (d / "state.json").write_text(json.dumps({"state": state, "detail": detail, "sessionId": SID, **extra}))
        (d / "timeline.jsonl").write_text("\n".join(json.dumps(x) for x in (lines or [])))

    def register(self, tb: Any) -> None:
        """Put the coding_session_* tools on `tb`, over this case's sessions."""
        tb.specs.update({s.name: s for s in ca.specs(SimpleNamespace(sessions=self.cs))})

    async def started(self, **kw: Any) -> dict[str, Any]:
        self.job_files("deadbeef", "working", "reading files")
        return await self.cs.start("claude", str(self.repo), "fix the bug", **kw)


class Validation(CodingTestCase):
    async def test_folder_without_git_is_refused(self) -> None:
        plain = self.tmp / "work" / "plain"
        plain.mkdir()
        with self.assertRaises(ca.CodingError):
            await self.cs.start("claude", str(plain), "x")

    async def test_protected_and_malformed_branches_are_refused(self) -> None:
        for b in ("main", "master", "MAIN", "../x", "a..b", "refs/heads/x", "x.lock", "-x"):
            with self.assertRaises(ca.CodingError, msg=b):
                await self.cs.start("claude", str(self.repo), "x", new_worktree=True, branch=b)
        self.assertEqual(self.fake.calls, [])

    def test_worktree_path_shape(self) -> None:
        self.assertEqual(ca.worktree_path(self.repo, "grain/fix-bug-ab12"), self.repo / ".claude" / "worktrees" / "fix-bug-ab12")
        self.assertEqual(ca.worktree_path(self.repo, "plain"), self.repo / ".claude" / "worktrees" / "plain")

    async def test_new_worktree_runs_git_then_starts_inside_it(self) -> None:
        self.job_files("deadbeef", "working")
        row = await self.cs.start("claude", str(self.repo), "fix the bug", new_worktree=True, branch="grain/fix-1")
        wt = str(self.repo / ".claude" / "worktrees" / "fix-1")
        self.assertEqual(self.fake.calls[0][-5:], ["worktree", "add", "-b", "grain/fix-1", wt])
        self.assertEqual((row["worktree"], row["branch"], row["repo_path"]), (wt, "grain/fix-1", str(self.repo)))

    async def test_default_branch_is_a_grain_branch_and_existing_path_is_refused(self) -> None:
        self.job_files("deadbeef", "working")
        row = await self.cs.start("claude", str(self.repo), "Fix the bug!", new_worktree=True)
        self.assertRegex(row["branch"], r"^grain/fix-the-bug-[0-9a-f]{4}$")
        (self.repo / ".claude" / "worktrees" / "taken").mkdir(parents=True)
        with self.assertRaises(ca.CodingError):
            await self.cs.start("claude", str(self.repo), "x", new_worktree=True, branch="taken")

    async def test_either_agent_gets_a_worktree_on_a_main_checkout_unless_told_otherwise(self) -> None:
        launched: list[str] = []

        async def launch(row: dict[str, Any], *_a: Any) -> None:
            launched.append(row["worktree"])
            row["external_id"] = "j"

        main = unittest.mock.patch.object(ca.opencode, "repo_of", return_value=(self.repo, True))
        with main, unittest.mock.patch.object(ca.opencode, "binary", return_value="/bin/opencode"), \
                unittest.mock.patch.object(self.cs, "_launch_opencode", launch):
            row = await self.cs.start("opencode", str(self.repo), "Fix the bug")
            self.assertRegex(row["branch"], r"^grain/fix-the-bug-[0-9a-f]{4}$")
            self.assertEqual(launched[-1], row["worktree"])
            self.assertIn("worktree", self.fake.calls[-2])  # then rev-parse HEAD records the base
            n = len(self.fake.calls)
            row = await self.cs.start("opencode", str(self.repo), "in place", new_worktree=False)
            self.assertEqual((row["worktree"], row["branch"], len(self.fake.calls)), (str(self.repo), None, n + 1))  # only the base rev-parse: no worktree add
        with unittest.mock.patch.object(ca.opencode, "repo_of", return_value=(self.repo, False)), \
                unittest.mock.patch.object(self.cs, "_launch_opencode", launch):  # already a linked worktree
            row = await self.cs.start("opencode", str(self.repo), "x")
            self.assertEqual((row["worktree"], row["branch"]), (str(self.repo), None))
        self.job_files("deadbeef", "working")
        with unittest.mock.patch.object(ca.opencode, "repo_of", return_value=(self.repo, True)):
            row = await self.cs.start("claude", str(self.repo), "Fix it")  # Claude Code too
            wt = str(self.repo / ".claude" / "worktrees" / row["branch"].rsplit("/", 1)[-1])
            self.assertRegex(row["branch"], r"^grain/fix-it-[0-9a-f]{4}$")
            self.assertEqual((row["worktree"], self.fake.calls[-3][-5:-1]), (wt, ["worktree", "add", "-b", row["branch"]]))
            n = len(self.fake.calls)
            row = await self.cs.start("claude", str(self.repo), "in place", new_worktree=False)
            self.assertEqual((row["worktree"], row["branch"]), (str(self.repo), None))
            self.assertFalse(any("worktree" in c for c in self.fake.calls[n:]))

    async def test_permission_mode_and_agent_rules(self) -> None:
        with self.assertRaises(ca.CodingError):
            await self.cs.start("claude", str(self.repo), "x", permission_mode="plan")
        with self.assertRaises(ca.CodingError):
            await self.cs.start("opencode", str(self.repo), "x", permission_mode="acceptEdits")
        with self.assertRaises(ca.CodingError):
            await self.cs.start("nope", str(self.repo), "x")
        with self.assertRaises(ca.CodingError):
            await self.cs.start("claude", str(self.repo), "   ")


class ClaudeDriver(CodingTestCase):
    def test_id_parsing(self) -> None:
        self.assertEqual(ca.parse_job_id("Started background session a1b2c3d4 (fix)\n"), "a1b2c3d4")
        self.assertIsNone(ca.parse_job_id("no id here, just words"))
        self.assertIsNone(ca.parse_job_id(""))
        self.assertEqual(ca.parse_job_id(START_OUT), "45622d2f")
        self.assertEqual(ca.parse_job_id(COPY_OUT), "fffebd7b")  # the note names the original first; the row follows the copy
        self.assertEqual(ca.parse_job_id(WOKE_OUT), "45622d2f")

    def test_map_claude(self) -> None:
        self.assertEqual(ca.map_claude({"state": "failed", "detail": "source session x not found"}), ("failed", "source session x not found"))
        self.assertEqual(ca.map_claude({"state": "blocked", "detail": "d", "needs": "API unavailable"}), ("needs_you", "API unavailable"))
        self.assertEqual(ca.map_claude({"state": "working", "detail": "d", "needs": "approve message"}), ("needs_you", "approve message"))
        self.assertEqual(ca.map_claude({"state": "working", "detail": "d", "needs": None}), ("working", "d"))
        self.assertEqual(ca.map_claude({"state": "running", "detail": "d"}), ("working", "d"))
        self.assertEqual(ca.map_claude({"state": "blocked", "detail": "d"}), ("needs_you", "d"))
        self.assertIsNone(ca.map_claude({"state": "weird"}))

    def test_read_claude_drops_consecutive_duplicate_lines(self) -> None:
        d = self.home / "abcd1234"
        d.mkdir()
        (d / "state.json").write_text('{"state": "working"}')
        evs = [{"detail": "a", "text": ""}, {"detail": "a", "text": ""}, {"detail": "b", "text": "said hi"}, {"detail": "b", "text": ""},
               {"detail": "a", "text": ""}, {"detail": "a", "text": ""}]
        (d / "timeline.jsonl").write_text("\n".join(json.dumps(e) for e in evs))
        self.assertEqual(ca.read_claude(self.home, "abcd1234")[1], ["a", "said hi", "b", "a"])

    def test_argv_has_no_permission_flag_unless_asked(self) -> None:
        plain = ca.claude_argv("/bin/claude", "n", "do it")
        # No --model either: without one the CLI uses the user's own default, the model they have quota for.
        self.assertEqual(plain, ["/bin/claude", "--bg", "-n", "n", "--agents", ca.CLAUDE_AGENTS, "--settings", ca.floor_settings(), "do it"])
        self.assertNotIn("--permission-mode", plain)
        self.assertNotIn("--model", plain)
        agents = json.loads(plain[plain.index("--agents") + 1])
        self.assertEqual(len(agents), 2)
        self.assertTrue(all(set(a) == {"description", "prompt", "model"} and a["model"] == "sonnet" for a in agents.values()))
        asked = ca.claude_argv("/bin/claude", "n", "do it", "opus", "acceptEdits")
        self.assertEqual(asked[asked.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(asked[asked.index("--model") + 1], "opus")
        dashed = ca.claude_argv("/bin/claude", "n", "-x")
        for argv in (plain, asked, ca.resume_argv("/bin/claude", SID, "go on"), dashed):
            self.assertFalse(any(a.startswith("--dangerously") for a in argv))
        self.assertEqual(dashed[-1], "Task: -x")

    def test_every_start_and_resume_carries_the_floor(self) -> None:
        """Allow everything's floor reaches Claude Code as deny/ask rules on every start and follow-up, whatever the mode."""
        for argv in (ca.claude_argv("/bin/claude", "n", "do it"), ca.claude_argv("/bin/claude", "n", "x", None, "auto"),
                     ca.resume_argv("/bin/claude", SID, "go on")):
            rules = json.loads(argv[argv.index("--settings") + 1])["permissions"]
            self.assertEqual(rules, ca.permrules.claude_code_floor()["permissions"])
            self.assertIn("Bash(git push --force*)", rules["ask"])

    def test_allow_all_sessions_carry_no_ask_rules_and_no_hook(self) -> None:
        """bypassPermissions is Allow everything: only the denies (Grain's data, disk wipes) remain on start and resume."""
        for argv in (ca.claude_argv("/bin/claude", "n", "x", None, "bypassPermissions"),
                     ca.resume_argv("/bin/claude", SID, "go on", None, "bypassPermissions")):
            s = json.loads(argv[argv.index("--settings") + 1])
            self.assertEqual(s, {"permissions": {"deny": ca.permrules.claude_code_floor()["permissions"]["deny"]}})

    def test_claude_env_carries_no_app_secrets(self) -> None:
        """The claude CLI gets the scrubbed allowlist plus PATH and TMPDIR, never the app's keys and tokens."""
        from personal_os import shell
        secrets = {"PERSONAL_OS_API_KEY": "fw_secret1", "FIREWORKS_API_KEY": "fw_secret2", "TELEGRAM_BOT_TOKEN": "123:abc",
                   "PERSONAL_OS_AUTH_TOKEN": "tok_secret3", "ANTHROPIC_API_KEY": "sk-ant-secret4"}
        with unittest.mock.patch.dict("os.environ", secrets), unittest.mock.patch.object(ca, "login_path", return_value="/usr/bin:/bin"):
            env = {**shell.scrubbed_env("/tmp/x"), **ca.claude_env()}
        self.assertFalse(set(secrets) & set(env))
        self.assertFalse([v for v in env.values() if any(x in v for x in secrets.values())])

    async def test_start_records_ids_and_maps_grain_mode_to_the_flag(self) -> None:
        row = await self.started()
        argv = next(a for a in self.fake.calls if "--bg" in a)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "auto")  # Grain's default mode is auto
        self.assertEqual((row["status"], row["external_id"], row["session_id"]), ("working", "deadbeef", SID))
        self.assertEqual(self.events[-1][0], "coding_session")
        self.assertEqual(self.events[-1][1]["id"], row["id"])

    async def test_start_without_an_id_falls_back_to_the_agents_list(self) -> None:
        self.fake.out["no id"] = (True, "no id")
        self.job_files("cafe0001", "working")
        row = await self.cs.start("claude", str(self.repo), "no id", name="mine")
        self.assertEqual(row["status"], "failed")  # the agents list said nothing either
        self.fake.out["/bin/claude agents --json"] = (True, json.dumps([
            {"id": "cafe0001", "cwd": str(self.repo), "name": "mine", "startedAt": "2026-01-01", "sessionId": SID}]))
        row = await self.cs.start("claude", str(self.repo), "no id", name="mine")
        self.assertEqual((row["status"], row["external_id"]), ("working", "cafe0001"))

    async def test_state_mapping(self) -> None:
        row = await self.started()
        cases = [("working", "working", "working"), ("blocked", "needs_you", "needs_you"), ("done", "done", "idle"),
                 ("stopped", "stopped", "idle")]
        for state, status, attention in cases:
            self.job_files("deadbeef", state, "step", [{"detail": "a"}, {"text": "b"}])
            row = self.cs.refresh(row)
            self.assertEqual(row["status"], status, state)
            self.assertEqual(ca.summary(row)["attention"], attention, state)
        self.job_files("deadbeef", "blocked", "x", needs="permission prompt")
        row = self.cs.refresh(row)
        s = ca.summary(row)
        self.assertEqual((row["detail"], s["attach_hint"]), ("permission prompt", "claude attach deadbeef"))
        self.assertNotIn("attach_hint", ca.summary({**row, "status": "working"}))

    async def test_missing_state_leaves_the_status(self) -> None:
        row = await self.started()
        (self.home / "deadbeef" / "state.json").unlink()
        row = self.cs.refresh(row)
        self.assertEqual((row["status"], row["detail"]), ("working", "state unavailable"))

    def test_job_folder_is_confined_to_the_jobs_home(self) -> None:
        self.assertEqual(ca.read_claude(self.home, "../../etc"), (None, []))
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "state.json").write_text('{"state": "done"}')
        (self.home / "abcd1234").symlink_to(outside)
        self.assertEqual(ca.read_claude(self.home, "abcd1234"), (None, []))

    async def test_send_is_refused_while_working(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "working")
        with self.assertRaises(ca.CodingError):
            await self.cs.send(row["id"], "more")
        self.assertFalse(any("--resume" in a for a in self.fake.calls))
        self.job_files("deadbeef", "done")
        row = await self.cs.send(row["id"], "more")
        argv = next(a for a in self.fake.calls if "--resume" in a)
        self.assertEqual(argv[argv.index("--resume") + 1], SID)  # the full id, never the short one
        self.assertEqual((row["status"], row["external_id"]), ("working", "deadbeef"))

    async def test_a_failed_session_can_be_followed_up(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "failed", "API error")
        self.assertEqual(self.cs.refresh(self.cs.get(row["id"]))["status"], "failed")
        row = await self.cs.send(row["id"], "carry on")
        self.assertEqual(row["status"], "working")
        self.assertEqual(self.cs.refresh(self.cs.get(row["id"]))["status"], "working")  # the stale "failed" is not read back

    async def test_a_settled_session_is_not_reread_until_its_state_changes(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "done", "finished")
        self.cs.refresh(self.cs.get(row["id"]))
        self.cs.refresh(self.cs.get(row["id"]))  # the first look at it settled records the state file's mtime
        with unittest.mock.patch.object(self.cs, "_refresh_claude", side_effect=AssertionError("re-read")):
            self.cs.refresh(self.cs.get(row["id"]))
        self.job_files("deadbeef", "working", "resumed in the terminal")
        self.assertEqual(self.cs.refresh(self.cs.get(row["id"]))["status"], "working")

    async def test_a_copy_started_by_the_cli_is_recorded(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "done")
        self.fake.out["resumed"] = (True, "started 0badf00d")
        self.job_files("0badf00d", "working")
        row = await self.cs.send(row["id"], "resumed")
        self.assertEqual(row["external_id"], "0badf00d")
        self.assertIn("copy", row["detail"])

    async def test_a_follow_up_on_an_open_session_follows_the_copy(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "done")
        self.fake.out["copy it"] = (True, COPY_OUT)
        self.job_files("fffebd7b", "working")
        row = await self.cs.send(row["id"], "copy it")
        self.assertEqual((row["external_id"], row["status"]), ("fffebd7b", "working"))
        self.assertIn("copy (fffebd7b)", row["detail"])
        self.fake.out["wake it"] = (True, WOKE_OUT.replace("45622d2f", "fffebd7b"))
        self.job_files("fffebd7b", "stopped")
        self.cs.resumed.clear()  # the grace window after a follow-up would hide "stopped"
        row = self.cs.refresh(row)
        row = await self.cs.send(row["id"], "wake it")
        self.assertEqual((row["external_id"], row["detail"]), ("fffebd7b", "follow-up sent"))

    async def test_stop_calls_claude_stop_and_never_rm(self) -> None:
        row = await self.started()
        row = await self.cs.stop(row["id"])
        self.assertEqual(row["status"], "stopped")
        self.assertIsNotNone(row["ended_at"])
        self.assertIn(["/bin/claude", "stop", "deadbeef"], self.fake.calls)
        self.assertFalse(any("rm" in a for a in self.fake.calls))

    async def test_recover_rereads_live_claude_sessions(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "done")
        again = ca.CodingSessions(self.cs.db, self.jobs, self.fake, lambda e, d: None, lambda: {},
                                  claude_home=self.home)
        self.assertEqual(again.get(row["id"])["status"], "done")


class Ownership(CodingTestCase):
    """A session belongs to the run that started it and stops when that run is stopped, never when it finishes."""

    async def owned(self, owner: str, **ctx: Any) -> dict[str, Any]:
        self.job_files("deadbeef", "working", "reading files")
        return await self.cs.start("claude", str(self.repo), "fix the bug", new_worktree=True, ctx={"agent_run_id": owner, **ctx})

    async def test_the_calling_worker_owns_the_session_else_the_reply(self) -> None:
        row = await self.owned("worker-1", run_id="reply-1")
        self.assertEqual(row["run_id"], "worker-1")
        self.job_files("deadbeef", "working")
        row = await self.cs.start("claude", str(self.repo), "x", new_worktree=True, ctx={"run_id": "reply-2"})
        self.assertEqual(row["run_id"], "reply-2")

    async def test_stopping_the_owner_stops_its_live_sessions_with_the_reason(self) -> None:
        mine = await self.owned("worker-1")
        self.job_files("deadbeef", "working")
        other = await self.owned("worker-2")
        Path(mine["worktree"]).mkdir(parents=True, exist_ok=True)
        self.fake.out["--porcelain"] = (True, " M src/a.py\n")
        out = await self.cs.stop_owned("worker-1", "the worker that started it was stopped")
        self.assertEqual([r["id"] for r in out], [mine["id"]])
        row = self.cs.get(mine["id"])
        self.assertEqual(row["status"], "stopped")
        self.assertIn("the worker that started it was stopped", row["detail"])
        self.assertIn("uncommitted changes are kept in " + row["worktree"], row["detail"])
        self.assertEqual(self.cs.get(other["id"])["status"], "working")
        self.assertFalse(any("remove" in a or "rm" in a for a in self.fake.calls), "the worktree is never removed")
        # the CLI's own "stopped" state must not overwrite why it was stopped
        self.job_files("deadbeef", "stopped", "stopped")
        self.assertIn("worker that started it", self.cs.refresh(row)["detail"])

    async def test_finished_sessions_unknown_owners_and_closing_are_left_alone(self) -> None:
        row = await self.owned("worker-1")
        self.cs._apply(row, status="done")
        self.assertEqual(await self.cs.stop_owned("worker-1", "x"), [])
        self.assertEqual(await self.cs.stop_owned(None, "x"), [])
        self.job_files("deadbeef", "working")
        live = await self.owned("worker-3")
        self.cs.closing = True
        self.assertEqual(await self.cs.stop_owned("worker-3", "x"), [])
        self.assertEqual(self.cs.get(live["id"])["status"], "working")

    async def test_a_stop_that_fails_says_so_instead_of_claiming_stopped(self) -> None:
        row = await self.owned("worker-1")
        self.fake.out["/bin/claude stop deadbeef"] = (False, "daemon not reachable")
        out = await self.cs.stop_owned("worker-1", "the worker was stopped")
        self.assertNotEqual(out[0]["status"], "stopped")
        self.assertIn("could not stop it", out[0]["detail"])


class OpencodeDriver(CodingTestCase):
    def test_job_status_mapping(self) -> None:
        def job(status: str, code: int | None = None) -> Any:
            return SimpleNamespace(status=status, exit_code=code)
        self.assertEqual(ca.map_job(job("running"))[0], "working")
        self.assertEqual(ca.map_job(job("exited", 0))[0], "done")
        self.assertEqual(ca.map_job(job("exited", 2))[0], "failed")
        self.assertEqual(ca.map_job(job("killed"))[0], "stopped")
        self.assertEqual(ca.map_job(job("timed_out"))[0], "failed")
        self.assertEqual(ca.map_job(job("orphaned"))[0], "blocked")

    async def test_follow_up_resumes_this_session_not_the_last_one_in_the_folder(self) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, name, prompt, status, "
                      "log_tail, created_at, updated_at) VALUES('s5','opencode','j5','ses_mine',?,?,'n','p','done','',?,?)", (wt, wt, t, t))
        self.jobs.jobs["j5"] = SimpleNamespace(status="exited", exit_code=0, buf="", total=0, live=lambda: False)
        got: dict[str, Any] = {}

        async def launch(tb: Any, ctx: Any, prompt: str, **kw: Any) -> Any:
            got.update(kw)
            return SimpleNamespace(id="j6"), {}
        self.cs.tb = object()
        with unittest.mock.patch.object(ca.opencode, "launch", launch):
            await self.cs.send("s5", "more")
        self.assertEqual((got["continue_session"], got["session"]), (True, "ses_mine"))

    async def test_send_refused_when_the_job_record_was_lost(self) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s2','opencode','gone',?,?,'n','p','blocked','',?,?)", (wt, wt, t, t))
        with self.assertRaisesRegex(ca.CodingError, "Stop it first"):
            await self.cs.send("s2", "more")

    def test_a_sandbox_that_refused_to_start_says_so(self) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s9','pi','j9',?,?,'n','p','working','',?,?)", (wt, wt, t, t))
        self.jobs.jobs["j9"] = SimpleNamespace(status="exited", exit_code=65, buf="sandbox-exec: profile error\n", total=30,
                                               live=lambda: False)
        with unittest.mock.patch.object(ca.osplat.current().sandbox, "failed_to_start", lambda code, out: code == 65):
            row = self.cs.refresh(self.cs.get("s9"))
        self.assertEqual(row["status"], "failed")
        self.assertIn("OS sandbox refused to start", row["detail"])

    async def test_send_refused_while_the_job_runs_and_a_gone_job_is_blocked(self) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s1','opencode','j1',?,?,'n','p','working','',?,?)", (wt, wt, t, t))
        job = SimpleNamespace(status="running", exit_code=None, buf='{"type":"text","part":{"type":"text","text":"hi"}}\n', total=50,
                              live=lambda: True)
        self.jobs.jobs["j1"] = job
        with self.assertRaises(ca.CodingError):
            await self.cs.send("s1", "more")
        row = self.cs.refresh(self.cs.get("s1"))
        self.assertEqual((row["status"], row["log_tail"]), ("working", "hi"))
        job.status, job.exit_code = "exited", 0
        self.assertEqual(self.cs.refresh(row)["status"], "done")
        with self.cs.db.tx() as c:  # refresh diffs against the saved row
            c.execute("UPDATE coding_sessions SET status = 'working' WHERE id = 's1'")
        self.jobs.jobs.clear()
        self.assertEqual(self.cs.refresh(row)["status"], "blocked")

    async def test_a_job_ending_publishes_the_session_at_once(self) -> None:
        """The job registry's change hook refreshes live OpenCode sessions, so the chat card sees "done" without a poll;
        the hook that was there before still runs."""
        before: list[int] = []
        jobs = SimpleNamespace(jobs={}, on_change=lambda: before.append(1))
        cs = ca.CodingSessions(self.cs.db, jobs, self.fake, lambda e, d: self.events.append((e, d)), lambda: {})
        wt, t = str(self.repo), 1.0
        with cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s3','opencode','j3',?,?,'n','p','working','',?,?)", (wt, wt, t, t))
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s4','opencode',NULL,?,?,'n','p','starting','',?,?)", (wt, wt, t, t))
        jobs.jobs["j3"] = SimpleNamespace(status="exited", exit_code=0, buf="", total=0, live=lambda: False)
        self.events.clear()
        jobs.on_change()
        self.assertEqual(before, [1])
        self.assertEqual([(d["id"], d["status"]) for e, d in self.events if e == "coding_session"], [("s3", "done")])
        self.assertEqual(cs.get("s4")["status"], "starting")  # still launching: not mistaken for a lost job


class Wake(CodingTestCase):
    async def test_a_session_settling_wakes_its_owner_once_and_a_waiter_reports_it_itself(self) -> None:
        woken: list[str] = []
        self.cs.on_finish = lambda r: woken.append(r["status"])
        row = await self.started()
        self.cs.refresh(self.cs.get(row["id"]))
        self.assertEqual(woken, [])  # still working
        self.job_files("deadbeef", "done", "finished")
        self.cs.refresh(self.cs.get(row["id"]))
        self.cs.refresh(self.cs.get(row["id"]))
        self.assertEqual(woken, ["done"])  # once

    async def test_a_finish_after_needs_you_wakes_too(self) -> None:
        woken: list[str] = []
        self.cs.on_finish = lambda r: woken.append(r["status"])
        row = await self.started()
        self.job_files("deadbeef", "blocked", "x", needs="permission prompt")
        self.cs.refresh(self.cs.get(row["id"]))
        self.job_files("deadbeef", "done", "finished")  # the user answered in the terminal
        self.cs.refresh(self.cs.get(row["id"]))
        self.assertEqual(woken, ["needs_you", "done"])

    async def test_a_stale_copy_of_the_row_does_not_settle_it_twice(self) -> None:
        woken: list[str] = []
        self.cs.on_finish = lambda r: woken.append(r["status"])
        row = await self.started()
        stale = self.cs.get(row["id"])  # the watcher's snapshot, taken while it was working
        self.job_files("deadbeef", "done", "finished")
        self.cs.refresh(self.cs.get(row["id"]))
        self.cs.refresh(stale)
        self.assertEqual(woken, ["done"])
        self.assertEqual(sum(1 for e, d in self.events if e == "coding_session_event" and "→ done" in str(d)), 1)

    async def test_wait_returns_when_all_settled_or_on_timeout_and_does_not_wake(self) -> None:
        woken: list[str] = []
        self.cs.on_finish = lambda r: woken.append(r["id"])
        row = await self.started()
        out = await self.cs.wait([row["id"]], "all", 1)
        self.assertTrue(out["timed_out"])
        self.assertEqual(self.cs.waiting[row["id"]], 0)

        async def finish() -> None:
            await asyncio.sleep(0.2)
            self.job_files("deadbeef", "done", "finished")
        t = asyncio.ensure_future(finish())
        out = await self.cs.wait([row["id"]], "any", 20)
        await t
        self.assertEqual((out["timed_out"], out["settled"], out["sessions"][0]["status"]), (False, [row["id"]], "done"))
        self.assertEqual(woken, [])  # the waiter was told directly

    async def test_our_own_stop_is_not_news(self) -> None:
        woken: list[str] = []
        self.cs.on_finish = lambda r: woken.append(r["id"])
        row = await self.started()
        self.job_files("deadbeef", "stopped")
        await self.cs.stop(row["id"])
        self.assertEqual(woken, [])


class Diff(CodingTestCase):
    async def test_diff_is_against_the_recorded_base_and_lists_new_files(self) -> None:
        sha = "a" * 40
        self.fake.out["HEAD"] = (True, sha + "\n")
        self.fake.out["--exclude-standard"] = (True, "new.py\nsub/two.py\n")
        row = await self.started()
        self.assertEqual(self.cs.get(row["id"])["base_sha"], sha)
        self.fake.calls.clear()
        out = await self.cs.diff(row["id"], full=True)
        self.assertEqual((out["base"], out["new_files"]), (sha, ["new.py", "sub/two.py"]))
        flat = [" ".join(a) for a in self.fake.calls]
        self.assertTrue(any(c.endswith(f"diff {sha} --stat --no-ext-diff --no-textconv") for c in flat), flat)
        self.assertTrue(any(c.endswith(f"log --oneline --stat -n 20 {sha}..HEAD") for c in flat), flat)
        self.assertFalse(any("origin/main" in c for c in flat))
        self.assertEqual(sum("--no-index" in c and c.endswith("/dev/null new.py") for c in flat), 1)

    async def test_outputs_are_capped_and_flagged(self) -> None:
        row = await self.started()
        self.fake.out["git -c core.fsmonitor=false"] = (True, "x" * 100_000)
        out = await self.cs.diff(row["id"], full=True)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["status"]), ca.STAT_CAP)
        self.assertEqual(len(out["diff"]), ca.DIFF_CAP)
        self.assertNotIn("diff", await self.cs.diff(row["id"]))

    async def test_log_falls_back_when_origin_main_is_missing(self) -> None:
        row = await self.started()
        self.fake.calls.clear()
        real = self.fake.__call__

        async def run(argv: list[str], cwd: str, timeout: float) -> tuple[bool, str]:
            if "origin/main..HEAD" in argv:
                self.fake.calls.append(argv)
                return False, "bad revision"
            return await real(argv, cwd, timeout)
        self.cs.run = run
        out = await self.cs.diff(row["id"])
        self.assertEqual(out["log"], "ok")
        self.assertTrue(any("log" in a and "5" in a for a in self.fake.calls))

    def test_summary_shape(self) -> None:
        row = {"id": "i", "agent": "opencode", "name": "n", "status": "needs_you", "detail": None, "repo_path": "r", "worktree": "w",
               "branch": None, "external_id": None, "model": None, "permission_mode": None, "created_at": 1.0, "updated_at": 2.0,
               "ended_at": None, "log_tail": "z" * 3000}
        s = ca.summary(row)
        self.assertEqual((len(s["log_tail"]), s["attention"]), (1500, "needs_you"))
        self.assertEqual(len(ca.summary(row, None)["log_tail"]), 3000)
        self.assertNotIn("attach_hint", s)  # only claude has a window to attach to

class Control(CodingTestCase):
    """Answering a waiting session, the cheap summary, lookup by any id, one live session per worktree, actionable errors."""

    async def asyncSetUp(self) -> None:
        inner = self.fake.__call__

        async def run(argv: list[str], cwd: str, timeout: float, env: Any = None) -> tuple[bool, str]:
            out = await inner(argv, cwd, timeout, env)
            if argv[1:2] == ["stop"]:  # a stopped session's state.json reads stopped
                self.job_files(argv[2], "stopped")
            return out
        self.cs.run = run
        p = unittest.mock.patch.object(ca, "STOP_WAIT", 0.3)
        p.start()
        self.addCleanup(p.stop)

    def resumes(self) -> list[list[str]]:
        return [a for a in self.fake.calls if "--resume" in a]

    async def waiting(self, needs: str) -> dict[str, Any]:
        row = await self.started()
        self.job_files("deadbeef", "blocked", needs=needs)
        return self.cs.refresh(row)

    async def test_answering_a_question_stops_then_resumes_the_same_id(self) -> None:
        row = await self.waiting("Which database should I use?")
        self.assertEqual(row["status"], "needs_you")
        self.fake.out["postgres"] = (True, WOKE_OUT.replace("45622d2f", "deadbeef"))
        out = await self.cs.send(row["id"], "postgres")
        self.assertEqual((out["status"], out["external_id"], out["detail"]), ("working", "deadbeef", "reply sent"))
        self.assertNotIn("note", out)
        stop = self.fake.calls.index(["/bin/claude", "stop", "deadbeef"])
        self.assertLess(stop, self.fake.calls.index(self.resumes()[0]))
        self.assertEqual(self.resumes()[0][self.resumes()[0].index("--resume") + 1], SID)
        self.assertIn("--settings", self.resumes()[0])
        self.assertNotIn("--permission-mode", self.resumes()[0])
        self.assertIn(("user", "postgres"), [(e["kind"], e["text"]) for e in ca.recent_events(self.cs.db, row["id"], 10)])
        self.assertIn(row["id"], self.cs.resumed)

    async def test_a_reply_to_a_permission_prompt_does_not_grant_it(self) -> None:
        row = await self.waiting("Allow Bash(rm -rf build)? Needs permission")
        out = await self.cs.send(row["id"], "yes, approve it")
        self.assertIn("NOT granted", out["note"])
        self.assertIn("claude attach deadbeef", out["note"])
        self.assertEqual(out["status"], "working")
        self.assertFalse(any("--allowedTools" in a or "--permission-mode" in a for a in self.resumes()))

    async def test_the_tool_returns_the_note(self) -> None:
        row = await self.waiting("Allow Edit(/x)?")
        tb = SimpleNamespace(specs={})
        self.register(tb)
        out = await tb.specs["coding_session_send"].fn({}, row["id"], "ok")
        self.assertIn("NOT granted", out["note"])
        self.assertEqual(out["status"], "working")

    def test_summary_when_waiting(self) -> None:
        t = 1_700_000_000.0
        row = {"id": "a", "agent": "claude", "name": "n", "status": "needs_you", "detail": "Allow Bash(ls)?", "repo_path": "/r",
               "worktree": "/w", "updated_at": t, "log_tail": "x"}
        out = ca.brief(row, t + 90)
        self.assertEqual(out["waiting_on"], {"kind": "permission", "text": "Allow Bash(ls)?"})
        self.assertEqual((out["idle_s"], out["attention"]), (90, "needs_you"))
        self.assertRegex(out["last_activity"], r"^(1[0-2]|[1-9]):[0-5]\d (AM|PM)$")
        self.assertNotIn("last_log", out)
        out = ca.brief({**row, "detail": "Which database?"}, t)
        self.assertEqual(out["waiting_on"]["kind"], "question")

    async def test_summary_tool_when_not_waiting(self) -> None:
        row = await self.started()
        self.cs.refresh(row)
        with self.cs.db.tx() as c:
            c.execute("UPDATE coding_sessions SET log_tail=? WHERE id=?", ("first\n" + "z" * 500 + "\n", row["id"]))
        tb = SimpleNamespace(specs={})
        self.register(tb)
        ctx: dict[str, Any] = {}
        out = await tb.specs["coding_session_summary"].fn(ctx, "deadbeef")  # by external id
        self.assertEqual((out["id"], out["status"], out["detail"], len(out["last_log"])), (row["id"], "working", "reading files", 300))
        self.assertNotIn("waiting_on", out)
        self.assertNotIn("log_tail", out)
        self.assertEqual(tb.specs["coding_session_summary"].danger, "safe")
        self.assertTrue(ctx.get("tainted") or ctx.get("taint_sources"))

    async def test_resume_of_a_stopped_session_keeps_the_id(self) -> None:
        row = await self.started()
        row = await self.cs.stop(row["id"])
        self.fake.out["again"] = (True, WOKE_OUT.replace("45622d2f", "deadbeef"))
        out = await self.cs.send(row["id"], "again")
        self.assertEqual((out["status"], out["external_id"], out["detail"]), ("working", "deadbeef", "follow-up sent"))
        self.assertEqual(len(self.resumes()), 1)

    async def test_resume_of_a_done_session_stops_it_first_and_keeps_the_id(self) -> None:
        row = await self.started()
        self.job_files("deadbeef", "done")
        self.fake.out["more"] = (True, WOKE_OUT.replace("45622d2f", "deadbeef"))
        out = await self.cs.send(row["id"], "more")
        self.assertLess(self.fake.calls.index(["/bin/claude", "stop", "deadbeef"]), self.fake.calls.index(self.resumes()[0]))
        self.assertEqual((out["external_id"], out["detail"]), ("deadbeef", "follow-up sent"))

    async def test_own_session_taint_exemption_holds_for_external_ids(self) -> None:
        row = await self.started()
        tb = SimpleNamespace(specs={})
        self.register(tb)
        ctx: dict[str, Any] = {}
        await tb.specs["coding_session_summary"].fn(ctx, "deadbeef")
        spec = tb.specs["coding_session_send"]
        self.assertTrue(spec.taint_ok({"id": "deadbeef"}, ctx))
        self.assertTrue(spec.taint_ok({"id": row["id"]}, ctx))
        ctx["taint_sources"] = [*ctx["taint_sources"], "web:x"]
        self.assertFalse(spec.taint_ok({"id": "deadbeef"}, ctx))
        self.assertFalse(spec.taint_ok({"id": "nope"}, {"taint_sources": []}))

    async def test_failed_resume_after_stop_records_that_the_reply_was_not_delivered(self) -> None:
        row = await self.waiting("Which database should I use?")
        self.fake.out["postgres"] = (False, "boom")
        tb = SimpleNamespace(specs={})
        self.register(tb)
        out = await tb.specs["coding_session_send"].fn({}, row["id"], "postgres")
        self.assertIn("did not resume", str(out))
        self.assertIn("coding_session_send can retry", str(out))
        now = self.cs.get(row["id"])
        self.assertEqual(now["status"], "stopped")
        self.assertIn("reply not delivered", now["detail"])

    def test_permission_re_tool_call_is_case_sensitive(self) -> None:
        base = {"id": "a", "agent": "claude", "name": "n", "status": "needs_you", "repo_path": "/r", "worktree": "/w", "updated_at": 1.0, "log_tail": ""}
        self.assertEqual(ca.brief({**base, "detail": "Should I use option (a)?"}, 1.0)["waiting_on"]["kind"], "question")
        self.assertEqual(ca.brief({**base, "detail": "Bash(rm -rf build)"}, 1.0)["waiting_on"]["kind"], "permission")

    async def test_a_failed_session_can_be_resumed(self) -> None:
        row = await self.started()
        self.cs._apply(row, status="failed", detail="boom")
        out = await self.cs.send(row["id"], "retry")
        self.assertEqual(out["status"], "working")

    async def test_lookup_by_external_id_and_session_id(self) -> None:
        row = await self.started()
        for key in (row["id"], "deadbeef", SID):
            self.assertEqual(self.cs.get(key)["id"], row["id"])
        self.assertEqual((await self.cs.wait(["deadbeef"], timeout_s=1))["settled"], [])
        self.assertIsNone(self.cs.get("nope"))
        self.job_files("deadbeef", "done")
        self.assertEqual((await self.cs.diff(SID))["worktree"], row["worktree"])

    async def test_a_second_session_in_an_occupied_worktree_is_refused(self) -> None:
        first = await self.started()
        with self.assertRaises(ca.CodingError) as cm:
            await self.cs.start("claude", str(self.repo), "other", new_worktree=False)
        msg = str(cm.exception)
        for part in (first["id"], "claude", "working"):
            self.assertIn(part, msg)
        self.assertIn("new_worktree=true", cm.exception.alt)
        self.job_files("deadbeef", "done")  # once it settles the worktree is free again
        self.job_files("cafe0001", "working")
        self.fake.out["other"] = (True, "backgrounded · cafe0001\n")
        await self.cs.start("claude", str(self.repo), "other", new_worktree=False)

    async def test_a_resume_is_refused_while_a_different_session_holds_the_worktree(self) -> None:
        first = await self.started()
        self.job_files("deadbeef", "done")
        self.cs.refresh(first)
        self.job_files("cafe0001", "working")
        self.fake.out["second"] = (True, "backgrounded · cafe0001\n")
        second = await self.cs.start("claude", str(self.repo), "second", new_worktree=False)
        with self.assertRaises(ca.CodingError) as cm:
            await self.cs.send(first["id"], "more")
        self.assertIn(second["id"], str(cm.exception))
        self.assertEqual(self.resumes(), [])

    async def test_a_failed_delivery_says_what_to_do(self) -> None:
        row = await self.waiting("Which one?")
        self.fake.out["answer"] = (False, "daemon not running")
        tb = SimpleNamespace(specs={})
        self.register(tb)
        out = await tb.specs["coding_session_send"].fn({}, row["id"], "answer")
        self.assertIn("did not resume", out["error"])
        self.assertIn("claude attach deadbeef", out["try_instead"])

    async def test_a_session_that_cannot_be_stopped_says_so(self) -> None:
        row = await self.waiting("Which one?")
        self.fake.out["/bin/claude stop deadbeef"] = (False, "no daemon")
        with self.assertRaises(ca.CodingError) as cm:
            await self.cs.send(row["id"], "answer")
        self.assertIn("claude attach", cm.exception.alt)


class Bridge(CodingTestCase):
    """What a start and a follow-up carry for the hook bridge and a recipe's options (claude_hook, extensions/coding/bridge.py)."""

    def settings_of(self, argv: list[str]) -> dict[str, Any]:
        return json.loads(argv[argv.index("--settings") + 1])

    def starts(self) -> list[list[str]]:
        return [a for a in self.fake.calls if "--bg" in a]

    async def test_with_nobody_listening_a_start_is_what_it_always_was(self) -> None:
        self.cs.bridge_port = 4321
        row = await self.started()
        argv = self.starts()[0]
        self.assertEqual(argv[argv.index("--settings") + 1], ca.floor_settings(None))
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(self.cs.get(row["id"])["hook_gate"], 0)

    async def test_subscribers_and_a_step_cap_put_the_bridge_in_the_start_and_the_follow_up(self) -> None:
        self.cs.bridge_port = 4321
        self.cs.subscribers = lambda: (True, True)
        row = await self.started(cli_opts={"allowed_tools": ["Read"], "denied_tools": ["WebFetch"], "max_steps": 5}, recipe_run_id="rr1")
        saved = self.cs.get(row["id"])
        self.assertEqual((saved["recipe_run_id"], saved["hook_gate"]), ("rr1", 1))
        self.assertTrue(saved["hook_key"] and json.loads(saved["cli_opts"])["max_steps"] == 5)
        argv = self.starts()[0]
        self.assertEqual(argv[4:8], ["--allowedTools", "Read", "--disallowedTools", "WebFetch"])
        hooks = self.settings_of(argv)["hooks"]
        command = hooks["PreToolUse"][0]["hooks"][0]["command"]
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "*")
        self.assertIn(f"GRAIN_HOOK_KEY={saved['hook_key']}", command)
        self.assertIn(f"--session {row['id']} --gate --cap 5 --counter ", command)
        self.assertIn(str(self.cs.db.data_dir / "coding-counters" / row["id"]), command)
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "*")
        self.assertNotIn("hook_key", ca.summary(saved))  # the key never leaves through the tools or the routes
        self.assertNotIn(saved["hook_key"], json.dumps(ca.brief(saved)))
        self.job_files("deadbeef", "done")
        self.cs.refresh(self.cs.get(row["id"]))
        self.cs.subscribers = lambda: (False, True)  # the gate subscriber is gone: the next resume is not gated
        await self.cs.send(row["id"], "more")
        resume = [a for a in self.fake.calls if "--resume" in a][0]
        self.assertEqual(resume[4:7], ["--allowedTools", "Read", "--disallowedTools"])
        resumed = self.settings_of(resume)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn(f"GRAIN_HOOK_KEY={saved['hook_key']}", resumed)
        self.assertNotIn("--gate", resumed)
        self.assertEqual(self.cs.get(row["id"])["hook_gate"], 0)  # the row records what it ran with

    async def test_a_session_from_before_the_bridge_gets_its_key_when_it_is_first_needed(self) -> None:
        row = await self.started()
        with self.cs.db.tx() as c:
            c.execute("UPDATE coding_sessions SET hook_key = NULL WHERE id = ?", (row["id"],))
        self.cs.bridge_port, self.cs.subscribers = 4321, lambda: (True, False)
        self.job_files("deadbeef", "done")
        self.cs.refresh(self.cs.get(row["id"]))
        await self.cs.send(row["id"], "more")
        self.assertTrue(self.cs.get(row["id"])["hook_key"])

    async def test_without_a_known_port_there_is_nothing_to_report_to(self) -> None:
        self.cs.bridge_port, self.cs.subscribers = None, lambda: (True, True)
        await self.started()
        self.assertNotIn("--gate", json.dumps(self.settings_of(self.starts()[0])))  # no known address: nothing to report to

    async def test_options_that_only_claude_has_are_refused_for_the_others(self) -> None:
        with self.assertRaises(ca.CodingError):
            await self.cs.start("opencode", str(self.repo), "x", cli_opts={"max_steps": 5})
