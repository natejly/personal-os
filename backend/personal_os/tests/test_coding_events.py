"""The structured live feed of coding sessions (coding_events.py + its capture in codingagents.py + the events route).

Run: cd backend && .venv/bin/python -m pytest personal_os/tests/test_coding_events.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="codingevents-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from personal_os import coding_events as ce  # noqa: E402
from personal_os import codingagents as ca  # noqa: E402
from personal_os import shell  # noqa: E402
from personal_os.tests import test_codingagents as base  # noqa: E402


def lines(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def oc_tool(tool: str, title: str) -> dict:
    return {"type": "tool", "sessionID": "ses_1", "part": {"type": "tool", "tool": tool, "state": {"title": title}}}


def oc_text(text: str) -> dict:
    return {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": text}}


class Feed(base.CodingTestCase):
    def sess(self, agent: str = "claude", external: str | None = "deadbeef", status: str = "working") -> dict:
        with self.cs.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, name, prompt, status, "
                      "log_tail, created_at, updated_at) VALUES('s1',?,?,NULL,?,?,'n','p',?,'',1,1)",
                      (agent, external, str(self.repo), str(self.repo), status))
        return self.cs.get("s1")  # type: ignore[return-value]

    def feed(self, after: int = 0) -> list[dict]:
        return ce.list_after(self.cs.db, "s1", after, 2000)

    # ---- the store ----
    def test_append_assigns_seq_publishes_and_list_after_pages(self) -> None:
        seen: list = []
        for i in range(5):
            ev = ce.append(self.cs.db, "s1", "text", f"t{i}", data={"i": i}, publish=lambda t, e: seen.append((t, e)))
        ce.append(self.cs.db, "other", "text", "elsewhere")
        self.assertEqual([e["seq"] for e in self.feed()], [1, 2, 3, 4, 5])
        self.assertEqual(set(ev), {"id", "session_id", "seq", "ts", "kind", "text", "tool", "data"})
        self.assertRegex(ev["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual([t for t, _ in seen], ["coding_session_event"] * 5)
        self.assertEqual(seen[4][1], ev)
        self.assertEqual([e["seq"] for e in ce.list_after(self.cs.db, "s1", 2, 2)], [3, 4])
        self.assertEqual([e["text"] for e in ce.recent(self.cs.db, "s1", 2)], ["t3", "t4"])
        self.assertEqual(self.feed()[0]["data"], {"i": 0})
        self.assertEqual(ce.append(self.cs.db, "other", "text", "x")["seq"], 2)

    def test_text_is_truncated_and_cap_drops_the_oldest(self) -> None:
        self.assertEqual(len(ce.append(self.cs.db, "s1", "text", "x" * 5000)["text"]), ce.TEXT_MAX)
        with unittest.mock.patch.object(ce, "CAP", 3):
            for i in range(4):
                ce.append(self.cs.db, "s1", "text", f"n{i}")
            self.assertEqual([e["seq"] for e in ce.list_after(self.cs.db, "s1", 0, 100)], [3, 4, 5])
        self.assertEqual(ce.append(self.cs.db, "s1", "text", "next")["seq"], 6)

    # ---- opencode and pi ----
    async def test_start_and_send_record_the_prompt(self) -> None:
        row = await self.started()
        evs = ce.list_after(self.cs.db, row["id"])
        self.assertEqual([(e["kind"], e["text"]) for e in evs], [("user", "fix the bug"), ("status", "starting → working")])

    def test_opencode_output_becomes_events_once(self) -> None:
        row = self.sess("opencode", "j1")
        job = shell.Job("j1", "opencode", str(self.repo), None, None, True, False, 10_000)
        self.jobs.jobs["j1"] = job
        job.append(lines(oc_text("hello"), oc_tool("bash", "ls -la")))
        job.append(json.dumps(oc_text("half"))[:20])  # a line still being written
        row = self.cs.refresh(row)
        self.assertEqual([(e["kind"], e["text"], e["tool"]) for e in self.feed() if e["kind"] != "status"],
                         [("text", "hello", None), ("tool", "ls -la", "bash")])
        n = len(self.feed())
        self.cs.refresh(row)
        self.cs.seen.clear()  # force a re-parse of the same buffer
        self.cs.refresh(row)
        self.assertEqual(len(self.feed()), n)
        job.append(json.dumps(oc_text("half"))[20:] + "\n")
        job.append(lines({"type": "error", "error": {"data": {"message": "boom"}}}))
        job.status, job.exit_code = "exited", 1
        self.cs.refresh(row)
        tail = [(e["kind"], e["text"]) for e in self.feed()[n:]]
        self.assertEqual(tail[:3], [("text", "half"), ("error", "boom"), ("status", "working → failed")])
        self.assertEqual(len(tail), 3)

    def test_opencode_cursor_survives_the_job_buffer_rolling_over(self) -> None:
        row = self.sess("opencode", "j1")
        job = shell.Job("j1", "opencode", str(self.repo), None, None, True, False, 300)
        self.jobs.jobs["j1"] = job
        job.append(lines(oc_text("one")))
        self.cs.refresh(row)
        for i in range(2, 8):
            job.append(lines(oc_text(f"msg {i} " + "y" * 30)))
            self.cs.refresh(row)
        texts = [e["text"] for e in self.feed() if e["kind"] == "text"]
        self.assertEqual(texts[0], "one")
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(len(texts), 7)

    def test_pi_output_becomes_events_once(self) -> None:
        row = self.sess("pi", "j1")
        job = shell.Job("j1", "pi", str(self.repo), None, None, True, False, 10_000)
        self.jobs.jobs["j1"] = job
        msg = {"role": "assistant", "content": [{"type": "text", "text": "done it"}], "stopReason": "stop"}
        job.append(lines({"type": "session", "id": "5a1c"}, {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls"}},
                         {"type": "tool_execution_end", "toolName": "bash", "result": {}, "isError": False}))
        row = self.cs.refresh(row)
        self.assertEqual([(e["kind"], e["text"], e["tool"]) for e in self.feed() if e["kind"] != "status"], [("tool", "ls", "bash")])
        job.append(lines({"type": "message_end", "message": msg}))
        job.status, job.exit_code = "exited", 0
        self.cs.refresh(row)
        self.cs.refresh(row)
        got = [(e["kind"], e["text"]) for e in self.feed() if e["kind"] != "status"]
        self.assertEqual(got, [("tool", "ls"), ("text", "done it")])
        self.assertEqual(self.feed()[-1]["text"], "working → done")

    def test_pi_failure_and_tool_error(self) -> None:
        evs, session = ce.parse_pi(lines({"type": "session", "id": "abc"},
                                         {"type": "tool_execution_end", "toolName": "bash", "isError": True,
                                          "result": {"content": [{"type": "text", "text": "exit 2"}]}},
                                         {"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                                              "errorMessage": "no api key"}}))
        self.assertEqual(session, "abc")
        self.assertEqual([(k, t) for k, t, _, _ in evs], [("tool_result", "exit 2"), ("error", "no api key")])

    # ---- claude ----
    def test_claude_timeline_is_read_incrementally(self) -> None:
        row = self.sess()
        steps = [{"state": "working", "detail": "a", "text": ""}, {"state": "working", "detail": "a", "text": ""},
                 {"state": "working", "detail": "b", "text": "I fixed it"}, {"state": "working", "detail": "", "text": ""}]
        self.job_files("deadbeef", "working", "b", steps[:2])
        row = self.cs.refresh(row)
        self.assertEqual([(e["kind"], e["text"]) for e in self.feed()], [("tool", "a")])
        self.job_files("deadbeef", "working", "b", steps)
        self.cs.refresh(row)
        self.assertEqual([(e["kind"], e["text"], e["tool"]) for e in self.feed()], [("tool", "a", "step"), ("text", "I fixed it", None)])
        self.assertEqual(self.feed()[1]["data"], {"src": "deadbeef", "line": 3})

    def test_claude_events_do_not_repeat_after_a_restart(self) -> None:
        row = self.sess()
        steps = [{"detail": "a", "text": ""}, {"detail": "a", "text": ""}, {"detail": "b", "text": ""}]
        self.job_files("deadbeef", "working", "b", steps)
        self.cs.refresh(row)
        before = self.feed()
        self.cs.timelines.clear()  # a restart forgets the in-memory cursor
        self.cs.refresh(self.cs.get("s1"))
        self.assertEqual(self.feed(), before)
        self.job_files("deadbeef", "working", "c", [*steps, {"detail": "b", "text": ""}, {"detail": "c", "text": ""}])
        self.cs.timelines.clear()
        self.cs.refresh(self.cs.get("s1"))
        self.assertEqual([e["text"] for e in self.feed()], ["a", "b", "c"])

    def test_claude_partial_last_line_waits_and_status_change_is_recorded(self) -> None:
        row = self.sess()
        d = self.home / "deadbeef"
        d.mkdir()
        (d / "state.json").write_text(json.dumps({"state": "working", "detail": "", "sessionId": base.SID}))
        (d / "timeline.jsonl").write_text(json.dumps({"detail": "a", "text": ""}) + "\n" + '{"detail": "par')
        row = self.cs.refresh(row)
        self.assertEqual([e["text"] for e in self.feed()], ["a"])
        (d / "state.json").write_text(json.dumps({"state": "done", "detail": "", "sessionId": base.SID}))
        (d / "timeline.jsonl").write_text(json.dumps({"detail": "a", "text": ""}) + "\n" + json.dumps({"detail": "partial", "text": ""}) + "\n")
        self.cs.refresh(row)
        self.assertEqual([(e["kind"], e["text"]) for e in self.feed()],
                         [("tool", "a"), ("tool", "partial"), ("status", "working → done")])

    def test_claude_follow_up_copy_starts_its_own_cursor(self) -> None:
        evs, prev = ce.parse_claude([json.dumps({"detail": "x"})], 1, "aaaa1111")
        evs2, _ = ce.parse_claude([json.dumps({"detail": "x"})], 1, "bbbb2222", None)
        self.assertEqual(len(evs), len(evs2))
        self.assertNotEqual(evs[0][3]["src"], evs2[0][3]["src"])
        self.assertEqual(prev, ("tool", "x"))


# ---- the route ----
class Route(unittest.TestCase):
    def test_events_route(self) -> None:
        from fastapi.testclient import TestClient

        from personal_os.app import AUTH_TOKEN, app, db
        client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
        self.assertEqual(client.get("/coding-sessions/nope/events").status_code, 404)
        with db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, name, prompt, status, "
                      "log_tail, created_at, updated_at) VALUES('rt1','opencode',NULL,NULL,'/r','/r','n','p','done','',1,1)")
        for i in range(4):
            ce.append(db, "rt1", "text", f"m{i}")
        r = client.get("/coding-sessions/rt1/events")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([e["seq"] for e in r.json()["events"]], [1, 2, 3, 4])
        self.assertEqual([e["text"] for e in client.get("/coding-sessions/rt1/events?after=2&limit=1").json()["events"]], ["m2"])
        self.assertEqual(client.get("/coding-sessions/rt1/events?after=9").json(), {"events": []})
        self.assertEqual(client.get("/coding-sessions/rt1/events?limit=99999").status_code, 200)


if __name__ == "__main__":
    unittest.main()
