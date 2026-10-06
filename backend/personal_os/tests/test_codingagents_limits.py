"""Coding-session limits and the claude daemon environment (codingagents.py, shell.py pools).

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_codingagents_limits -v
What matters here: the OpenCode timeout comes from the setting, coding sessions have their own concurrency pool,
and claude's PATH comes from the login shell only when no daemon runs. No process is started and the real
~/.claude/daemon.json is never read: every case points at a temp dir or mocks the call."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from personal_os import codingagents as ca
from personal_os import limits, llm, shell
from personal_os.tests.test_codingagents import SID, CodingTestCase


class Settings(unittest.TestCase):
    def test_new_keys_have_defaults_and_ranges(self) -> None:
        for k, v in (("codingSessionTimeoutMinutes", 30), ("codingSessionMaxConcurrent", 3)):
            self.assertEqual(llm.DEFAULT_SETTINGS[k], v)
        self.assertEqual(limits.RANGES["codingSessionTimeoutMinutes"], (1, 1440))
        self.assertEqual(limits.RANGES["codingSessionMaxConcurrent"], (1, 20))


class Timeout(CodingTestCase):
    def row(self) -> dict:
        return {"id": "s1", "worktree": str(self.repo), "model": None, "run_id": None}

    async def launched(self, settings: dict) -> dict:
        self.cs.settings, self.cs.tb = lambda: settings, object()
        launch = unittest.mock.AsyncMock(return_value=(SimpleNamespace(id="j1"), {}))
        with unittest.mock.patch.object(ca.opencode, "launch", launch):
            await self.cs._launch_opencode(self.row(), {}, "go", False)
        return launch.call_args.kwargs

    async def test_setting_minutes_become_seconds_and_the_coding_pool(self) -> None:
        kw = await self.launched({"codingSessionTimeoutMinutes": 45, "codingSessionMaxConcurrent": 2})
        self.assertEqual((kw["timeout"], kw["pool"], kw["max_background"]), (45 * 60, "coding", 2))

    async def test_junk_falls_back_to_the_constant(self) -> None:
        kw = await self.launched({"codingSessionTimeoutMinutes": "soon", "codingSessionMaxConcurrent": None})
        self.assertEqual(kw["timeout"], limits.CODING_SESSION_TIMEOUT_MINUTES * 60)
        self.assertEqual(kw["max_background"], limits.CODING_SESSION_MAX_CONCURRENT)

    def test_timed_out_text_names_the_minutes(self) -> None:
        _, detail = ca.map_job(SimpleNamespace(status="timed_out"), 45)
        self.assertIn("45 minutes", detail)
        self.assertNotIn("600", detail)


class Concurrency(CodingTestCase):
    async def test_start_refuses_at_the_limit_and_launches_nothing(self) -> None:
        self.cs.settings = lambda: {"codingSessionMaxConcurrent": 1}
        await self.started()
        calls = len(self.fake.calls)
        with self.assertRaises(ca.CodingError) as e:
            await self.cs.start("claude", str(self.repo), "another", new_worktree=True)
        self.assertIn("1 coding sessions are already running (codingSessionMaxConcurrent)", str(e.exception))
        self.assertEqual(len(self.fake.calls), calls)  # no worktree add, no second launch

    async def test_send_refuses_at_the_limit_and_relaunches_nothing(self) -> None:
        done = await self.started()
        self.cs._apply(done, status="done", session_id=SID)
        live = await self.started()  # a second, live session on its own job id (the fake always prints deadbeef)
        self.cs._apply(live, external_id="cafebabe")
        self.job_files("cafebabe", "working")
        self.job_files("deadbeef", "done")
        self.cs.settings = lambda: {"codingSessionMaxConcurrent": 1}
        calls = len(self.fake.calls)
        with self.assertRaises(ca.CodingError) as e:
            await self.cs.send(done["id"], "more")
        self.assertIn("codingSessionMaxConcurrent", str(e.exception))
        self.assertEqual(len(self.fake.calls), calls)

    async def test_a_session_that_ended_unseen_frees_its_slot(self) -> None:
        await self.started()
        self.job_files("deadbeef", "done")  # finished while nobody refreshed its row
        self.cs.settings = lambda: {"codingSessionMaxConcurrent": 1}
        self.assertEqual((await self.cs.start("claude", str(self.repo), "another"))["status"], "working")

    async def test_below_the_limit_starts(self) -> None:
        self.cs.settings = lambda: {"codingSessionMaxConcurrent": 2}
        await self.started()
        row = await self.started()
        self.assertIn(row["status"], ca.LIVE)


class Pools(unittest.TestCase):
    def test_running_background_counts_each_pool_apart(self) -> None:
        reg = shell.ShellJobs.__new__(shell.ShellJobs)
        reg.jobs = {}
        for i, (pool, bg, st) in enumerate([("shell", True, "running"), ("coding", True, "running"), ("coding", True, "orphaned"),
                                            ("coding", True, "exited"), ("shell", False, "running")]):
            j = shell.Job(f"j{i}", "c", "/", None, None, bg, False, 10)
            j.pool, j.status = pool, st
            reg.jobs[j.id] = j
        self.assertEqual((reg.running_background(), reg.running_background("coding")), (1, 2))


class Daemon(unittest.TestCase):
    def setUp(self) -> None:
        self._d = tempfile.TemporaryDirectory(prefix="daemontest-")
        self.home = Path(self._d.name)
        self.addCleanup(self._d.cleanup)

    def write(self, text: str) -> None:
        (self.home / "daemon.json").write_text(text)

    def test_missing_garbage_and_bad_pid_are_not_running(self) -> None:
        self.assertFalse(ca.daemon_running(self.home))
        for text in ("{not json", "[]", "{}", '{"pid": "1"}', '{"pid": true}', '{"pid": -5}', '{"pid": 0}'):
            self.write(text)
            self.assertFalse(ca.daemon_running(self.home), text)

    def test_own_pid_is_alive(self) -> None:
        self.write(json.dumps({"pid": os.getpid()}))
        self.assertTrue(ca.daemon_running(self.home))

    def test_a_finished_process_is_dead(self) -> None:
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.write(json.dumps({"pid": p.pid}))
        self.assertFalse(ca.daemon_running(self.home))

    def test_permission_error_means_alive(self) -> None:
        self.write('{"pid": 4242}')
        with unittest.mock.patch.object(ca.os, "kill", side_effect=PermissionError):
            self.assertTrue(ca.daemon_running(self.home))

    def test_default_path_is_the_module_constant(self) -> None:
        self.write(json.dumps({"pid": os.getpid()}))
        with unittest.mock.patch.object(ca, "DAEMON_FILE", self.home / "daemon.json"):
            self.assertTrue(ca.daemon_running())


def run_result(out: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout=out, stderr="")


class LoginPath(unittest.TestCase):
    def setUp(self) -> None:
        ca.login_path.cache_clear()
        self.addCleanup(ca.login_path.cache_clear)

    def path_from(self, **kw: object) -> str | None:
        ca.login_path.cache_clear()
        with unittest.mock.patch.object(ca.subprocess, "run", **kw) as run:
            out = ca.login_path()
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], limits.LOGIN_SHELL_TIMEOUT_SECONDS)
        return out

    def test_takes_the_last_line_after_rc_noise(self) -> None:
        self.assertEqual(self.path_from(return_value=run_result("welcome back\nmotd\n/opt/x/bin:/usr/bin")), "/opt/x/bin:/usr/bin")

    def test_timeout_and_missing_shell_give_none(self) -> None:
        self.assertIsNone(self.path_from(side_effect=subprocess.TimeoutExpired("sh", 5)))
        self.assertIsNone(self.path_from(side_effect=FileNotFoundError()))

    def test_relative_or_empty_entries_are_refused(self) -> None:
        self.assertIsNone(self.path_from(return_value=run_result("/usr/bin:bin")))
        self.assertIsNone(self.path_from(return_value=run_result("/usr/bin::/bin")))
        self.assertIsNone(self.path_from(return_value=run_result("")))

    def test_result_is_cached(self) -> None:
        with unittest.mock.patch.object(ca.subprocess, "run", return_value=run_result("/a:/b")) as run:
            ca.login_path()
            ca.login_path()
        self.assertEqual(run.call_count, 1)


class ClaudeEnv(unittest.TestCase):
    def env(self, daemon: bool, login: str | None) -> dict:
        with unittest.mock.patch.object(ca, "daemon_running", return_value=daemon), \
                unittest.mock.patch.object(ca, "login_path", return_value=login):
            return ca.claude_env()

    def test_login_path_when_no_daemon_runs(self) -> None:
        self.assertEqual(self.env(False, "/opt/x/bin:/usr/bin")["PATH"], "/opt/x/bin:/usr/bin")

    def test_fallback_when_the_login_shell_gave_nothing(self) -> None:
        path = self.env(False, None)["PATH"]
        self.assertTrue(path.startswith(os.path.expanduser("~/.local/bin") + ":"))
        self.assertIn(shell.SAFE_PATH, path)
        self.assertIn("/opt/homebrew/bin", path)

    def test_running_daemon_keeps_todays_path_and_skips_the_login_shell(self) -> None:
        with unittest.mock.patch.object(ca, "daemon_running", return_value=True), \
                unittest.mock.patch.object(ca, "login_path") as lp:
            path = ca.claude_env()["PATH"]
        lp.assert_not_called()
        self.assertTrue(path.startswith(os.path.expanduser("~/.local/bin") + ":"))


if __name__ == "__main__":
    unittest.main()
