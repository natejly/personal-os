"""pi as a coding agent (pi.py + the pi branch of codingagents.py): argv, `--mode json` parsing, detection, follow-ups."""
from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from personal_os import codingagents as ca
from personal_os import pi
from personal_os.tests.test_codingagents import CodingTestCase

HEADER = {"type": "session", "version": 3, "id": "5a1c0e22-aaaa-bbbb-cccc-1234567890ab", "timestamp": "t", "cwd": "/r"}


def stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def assistant(text: str, stop: str = "stop", err: str | None = None) -> dict:
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": stop}
    if err:
        msg["errorMessage"] = err
    return {"type": "message_end", "message": msg}


class Parsing(unittest.TestCase):
    def test_argv(self) -> None:
        self.assertEqual(pi.argv("/bin/pi", "fix it"), ["/bin/pi", "-p", "--mode", "json", "fix it"])
        self.assertEqual(pi.argv("/bin/pi", "more", "openai/gpt-4o", "5a1c"),
                         ["/bin/pi", "-p", "--mode", "json", "--model", "openai/gpt-4o", "--session", "5a1c", "more"])
        self.assertEqual(pi.argv("/bin/pi", "--help")[-1], "Task: --help")
        self.assertEqual(pi.argv("/bin/pi", "@x")[-1], "Task: @x")

    def test_summarize(self) -> None:
        raw = stream(HEADER, {"type": "agent_start"},
                     {"type": "tool_execution_start", "toolCallId": "1", "toolName": "bash", "args": {"command": "ls"}},
                     {"type": "tool_execution_end", "toolCallId": "1", "toolName": "bash", "result": {}, "isError": False},
                     assistant("Done."), {"type": "agent_end", "messages": []})
        self.assertEqual(pi.summarize(raw), ("[bash] ls\nDone.", HEADER["id"], None))
        text, _, err = pi.summarize("not json\n" + stream(assistant("", "error", "401 bad key")))
        self.assertEqual(err, "401 bad key")
        self.assertIn("not json", text)
        self.assertEqual(pi.summarize("[1, 2]\n{bad")[2], None)

    def test_binary_checks_the_usual_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "pi"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(0o755)
            with unittest.mock.patch.object(pi, "BIN_DIRS", (d,)):
                self.assertEqual(pi.binary(), str(exe))
            with unittest.mock.patch.object(pi, "BIN_DIRS", ()), unittest.mock.patch.object(pi.glob, "glob", return_value=[]), \
                    unittest.mock.patch.object(pi.shutil, "which", return_value=None):
                self.assertIsNone(pi.binary())


class PiDriver(CodingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cs.tb = SimpleNamespace()  # pi.launch is patched; the driver only needs a toolbox to exist

    def row(self, status: str, session: str | None) -> None:
        wt, t = str(self.repo), 1.0
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, name, prompt, status, "
                      "log_tail, created_at, updated_at) VALUES('p1','pi','j1',?,?,?,'n','p',?,'',?,?)",
                      (session, wt, wt, status, t, t))

    async def test_start_launches_pi_and_refresh_reads_the_session_and_a_silent_failure(self) -> None:
        launched = []

        async def launch(*a: object, **kw: object) -> SimpleNamespace:
            launched.append(kw)
            return SimpleNamespace(id="j9")

        with unittest.mock.patch.object(ca.pi, "binary", return_value="/bin/pi"), unittest.mock.patch.object(ca.pi, "launch", launch):
            row = await self.cs.start("pi", str(self.repo), "do it", new_worktree=False)
        self.assertEqual((row["status"], row["external_id"], launched[0]["session"]), ("working", "j9", None))
        job = SimpleNamespace(status="exited", exit_code=0, total=9, live=lambda: False,
                              buf=stream(HEADER, assistant("", "error", "no api key")))
        self.jobs.jobs["j9"] = job
        row = self.cs.refresh(row)
        self.assertEqual((row["status"], row["detail"], row["session_id"]), ("failed", "no api key", HEADER["id"]))

    async def test_permission_mode_is_refused_for_pi(self) -> None:
        with self.assertRaisesRegex(ca.CodingError, "Claude Code only"):
            await self.cs.start("pi", str(self.repo), "x", permission_mode="auto")

    async def test_follow_up_reopens_the_session_and_needs_its_id(self) -> None:
        self.row("done", None)
        with self.assertRaisesRegex(ca.CodingError, "session id"):
            await self.cs.send("p1", "more")
        with self.cs.db.tx() as c:
            c.execute("UPDATE coding_sessions SET session_id = 'abc' WHERE id = 'p1'")
        got = {}

        async def launch(*a: object, **kw: object) -> SimpleNamespace:
            got.update(kw, prompt=a[2])
            return SimpleNamespace(id="j2")

        with unittest.mock.patch.object(ca.pi, "launch", launch):
            row = await self.cs.send("p1", "more")
        self.assertEqual((got["session"], got["prompt"], row["external_id"], row["status"]), ("abc", "more", "j2", "working"))


if __name__ == "__main__":
    unittest.main()
