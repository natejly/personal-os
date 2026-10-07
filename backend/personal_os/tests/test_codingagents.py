"""Coding sessions (codingagents.py) with a fake command runner and fake claude job files.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_codingagents -v
What matters here: the paths and branches a session may use, that a permission flag appears only when the caller
asked for one, that a follow-up never goes to a session that is still working, and how the drivers' states map.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from personal_os import codingagents as ca
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
        self.cs = ca.CodingSessions(Database(self.tmp / "data"), self.jobs, self.fake, lambda e, d: self.events.append((e, d)),
                                    lambda: {}, claude_home=self.home)
        p = unittest.mock.patch.object(ca, "claude_binary", return_value="/bin/claude")
        p.start()
        self.addCleanup(p.stop)
        for q in (unittest.mock.patch.object(ca, "DAEMON_FILE", self.tmp / "no-daemon.json"),
                  unittest.mock.patch.object(ca, "login_path", return_value=None)):
            q.start()
            self.addCleanup(q.stop)
        self.addCleanup(self._dir.cleanup)

    def job_files(self, jid: str, state: str, detail: str = "", lines: list[dict[str, Any]] | None = None, **extra: Any) -> None:
        d = self.home / jid
        d.mkdir(exist_ok=True)
        (d / "state.json").write_text(json.dumps({"state": state, "detail": detail, "sessionId": SID, **extra}))
        (d / "timeline.jsonl").write_text("\n".join(json.dumps(x) for x in (lines or [])))

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
        self.assertEqual(plain, ["/bin/claude", "--bg", "-n", "n", "--agents", ca.CLAUDE_AGENTS, "do it"])
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

    async def test_send_is_refused_unless_done_or_stopped(self) -> None:
        row = await self.started()
        for state in ("working", "blocked"):
            self.job_files("deadbeef", state)
            with self.assertRaises(ca.CodingError, msg=state):
                await self.cs.send(row["id"], "more")
        self.assertFalse(any("--resume" in a for a in self.fake.calls))
        self.job_files("deadbeef", "done")
        row = await self.cs.send(row["id"], "more")
        argv = next(a for a in self.fake.calls if "--resume" in a)
        self.assertEqual(argv[argv.index("--resume") + 1], SID)  # the full id, never the short one
        self.assertEqual((row["status"], row["external_id"]), ("working", "deadbeef"))

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

    async def test_send_refused_when_the_job_record_was_lost(self) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, repo_path, worktree, name, prompt, status, log_tail, "
                      "created_at, updated_at) VALUES('s2','opencode','gone',?,?,'n','p','blocked','',?,?)", (wt, wt, t, t))
        with self.assertRaisesRegex(ca.CodingError, "Stop it first"):
            await self.cs.send("s2", "more")

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
        row["status"] = "working"
        self.jobs.jobs.clear()
        self.assertEqual(self.cs.refresh(row)["status"], "blocked")


class Diff(CodingTestCase):
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


if __name__ == "__main__":
    unittest.main()
