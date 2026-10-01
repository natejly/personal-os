"""shell_run / shell_poll / shell_kill (shell.py) against a real /bin/zsh. Where the Seatbelt sandbox is needed the tests
use the real sandbox-exec and skip with a message when it is absent; nothing here touches the network or the model."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import sandbox, shell  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

HAVE_SEATBELT = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
needs_seatbelt = pytest.mark.skipif(not HAVE_SEATBELT, reason="sandbox-exec is not available: Seatbelt assertions skipped")


class Box:
    def __init__(self, tmp: Path, **settings: Any):
        self.root = (tmp / "work").resolve()
        self.root.mkdir()
        self.settings: dict[str, Any] = {"workspaceRoots": [str(self.root)], **settings}
        self.db = Database(tmp / "data")
        with self.db.tx() as c:  # stored tool results hang off a conversation row
            for cid in ("c1", "c2"):
                c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)",
                          (cid, "t", "m", 0.0, 0.0))
        self.tb = Toolbox(None, None, None, lambda: self.settings, results=ToolResults(self.db))  # type: ignore[arg-type]
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "message_id": None, "settings": self.settings,
                                    "tainted": False, "taint_sources": []}

    def run(self, name: str, **args: Any) -> Any:
        return asyncio.run(self.tb.call(name, args, self.ctx))

    async def arun(self, name: str, **args: Any) -> Any:
        return await self.tb.call(name, args, self.ctx)


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_registered_ask_by_default_and_never_unsandboxed_unasked(box: Box) -> None:
    spec = box.tb.specs["shell_run"]
    assert (spec.group, spec.danger, spec.default_mode) == ("shell", "executes", "ask")
    assert box.tb.specs["shell_poll"].default_mode == "on"
    # unsandboxed is a forced approval: an 'on' mode is upgraded, and the call is flagged as forced
    assert box.tb.gate("shell_run", "on", {}, {"command": "ls"}) == "on"
    assert box.tb.gate("shell_run", "on", {}, {"command": "ls", "unsandboxed": True}) == "ask"
    assert box.tb.forces_ask("shell_run", {"command": "ls", "unsandboxed": True})
    assert not box.tb.forces_ask("shell_run", {"command": "ls"})
    from personal_os import llm
    assert llm.DEFAULT_SETTINGS["workspaceRoots"] == [] and llm.DEFAULT_SETTINGS["shellNetwork"] is False
    assert (llm.DEFAULT_SETTINGS["shellTimeoutSec"], llm.DEFAULT_SETTINGS["shellMaxBackground"]) == (120, 4)


def test_cwd_must_be_inside_a_granted_root(tmp_path: Path, box: Box) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    (box.root / "sub").mkdir()
    roots = shell.granted_roots(box.settings, None)
    assert shell.resolve_cwd(None, roots)[0] == box.root
    assert shell.resolve_cwd("sub", roots)[0] == box.root / "sub"
    with pytest.raises(shell.ShellError, match="outside"):
        shell.resolve_cwd(str(other), roots)
    with pytest.raises(shell.ShellError, match="outside"):
        shell.resolve_cwd("../elsewhere", roots)
    (box.root / "link").symlink_to(other)  # a symlink out of the root is resolved first, then refused
    with pytest.raises(shell.ShellError, match="outside"):
        shell.resolve_cwd("link", roots)
    with pytest.raises(shell.ShellError, match="Settings"):
        shell.resolve_cwd(None, [])
    r = box.run("shell_run", command="pwd", cwd=str(other))
    assert r["error"] and "outside" in r["error"]
    box.settings["workspaceRoots"] = []
    assert "Workspace roots" in box.run("shell_run", command="pwd")["error"]


def test_desk_workspace_is_the_default_root(tmp_path: Path) -> None:
    b = Box(tmp_path, workspaceRoots=[])
    desk = (tmp_path / "desk").resolve()
    desk.mkdir()

    class Ws:
        def ensure(self, desk_id: str) -> Path:
            assert desk_id == "d1"
            return desk
    b.tb.workspace = Ws()
    b.ctx["desk_id"] = "d1"
    if not HAVE_SEATBELT:
        pytest.skip("sandbox-exec is not available")
    r = b.run("shell_run", command="pwd && touch made.txt")
    assert r["exit_code"] == 0 and str(desk) in r["output"] and (desk / "made.txt").exists()


@needs_seatbelt
def test_writes_inside_succeed_and_outside_fail(tmp_path: Path, box: Box) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    r = box.run("shell_run", command=f"python3 -c \"open('report.txt','w').write('x')\" && ls && touch {outside}/nope")
    assert r["exit_code"] != 0 and "report.txt" in r["output"]
    assert (box.root / "report.txt").exists()
    assert not (outside / "nope").exists()
    assert r["sandboxed"] is True and r["network"] is False
    # the per-run tmp dir is writable, and gone afterwards
    t = box.run("shell_run", command="echo hi > $TMPDIR/t && cat $TMPDIR/t && echo $TMPDIR")
    assert t["exit_code"] == 0 and "hi" in t["output"]
    assert not os.path.exists(t["output"].split()[-1])


@needs_seatbelt
def test_repo_hooks_and_config_are_not_writable(box: Box) -> None:
    (box.root / ".git" / "hooks").mkdir(parents=True)
    (box.root / ".git" / "config").write_text("[core]\n")
    r = box.run("shell_run", command="echo x > .git/hooks/pre-commit; echo y >> .git/config; echo z > .git/HEAD; echo done")
    assert "done" in r["output"]
    assert not (box.root / ".git" / "hooks" / "pre-commit").exists()
    assert (box.root / ".git" / "config").read_text() == "[core]\n"
    assert (box.root / ".git" / "HEAD").read_text().strip() == "z"  # the rest of .git is ordinary workspace


@needs_seatbelt
def test_secrets_are_unreadable(tmp_path: Path, box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("PRIVATE")
    monkeypatch.setenv("HOME", str(home))
    (box.root / ".env").write_text("API_KEY=abc")
    (box.root / "notes.txt").write_text("fine")
    r = box.run("shell_run", command=f"cat {home}/.ssh/id_rsa; cat .env; cat notes.txt")
    assert "PRIVATE" not in r["output"] and "API_KEY" not in r["output"] and "fine" in r["output"]


@needs_seatbelt
def test_network_is_off(tmp_path: Path, box: Box) -> None:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"reachable")

        def log_message(self, *a: Any) -> None:
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        r = box.run("shell_run", command=f"curl -sS -m 5 {url}")
        assert r["exit_code"] != 0 and "reachable" not in r["output"]
        assert box.ctx["tainted"] is False
        # With shellNetwork on, the same command works and the reply is tainted
        box.settings["shellNetwork"] = True
        r2 = box.run("shell_run", command=f"curl -sS -m 5 {url}")
        assert r2["exit_code"] == 0 and "reachable" in r2["output"] and r2["network"] is True
        assert box.ctx["tainted"] is True and "shell_run:network" in box.ctx["taint_sources"]
    finally:
        srv.shutdown()


def test_env_is_scrubbed(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    if not HAVE_SEATBELT:
        pytest.skip("sandbox-exec is not available")
    monkeypatch.setenv("GRAIN_PLANTED_SECRET", "hunter2-planted")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-planted-1234567890abcdef")
    r = box.run("shell_run", command="env")
    assert "planted" not in r["output"]
    assert "TERM=dumb" in r["output"] and "TMPDIR=" in r["output"]


def test_scrub_removes_credentials_but_not_ordinary_output() -> None:
    assert "AKIAABCDEFGHIJKLMNOP" not in shell._scrub("key AKIAABCDEFGHIJKLMNOP end")
    assert "-----BEGIN RSA PRIVATE KEY-----" not in shell._scrub("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    keep = "commit 3f2a91c7d8e0b1a2 src/app/main.py:42 password = os.environ['X']"
    assert shell._scrub(keep) == keep


@needs_seatbelt
def test_output_is_redacted_before_the_model_and_the_handle(box: Box) -> None:
    r = box.run("shell_run", command="echo AKIAABCDEFGHIJKLMNOP; seq 1 4000")
    assert "AKIAABCDEFGHIJKLMNOP" not in r["output"] and r["truncated"]
    stored = box.tb.results.get(r["result_id"])
    assert "AKIAABCDEFGHIJKLMNOP" not in stored["content"] and "[aws-key]" in stored["content"]


def test_truncate_by_lines_and_bytes() -> None:
    text = "\n".join(str(i) for i in range(5000))
    out, cut = shell.truncate(text)
    assert cut and out.splitlines()[-1] == "4999" and len(out.splitlines()) <= shell.TRUNC_LINES
    wide = "x" * 200 + "\n"
    out, cut = shell.truncate(wide * 1000)  # 1000 lines, 200 KB
    assert cut and len(out.encode()) <= shell.TRUNC_BYTES
    assert shell.truncate("short") == ("short", False)


@needs_seatbelt
def test_long_output_is_truncated_with_a_handle_and_hint(box: Box) -> None:
    r = box.run("shell_run", command="seq 1 5000")
    assert r["truncated"] and r["output"].splitlines()[-1] == "5000"
    assert len(r["output"].splitlines()) <= 2000
    assert "fs_grep" in r["note"] and "read_tool_result" in r["note"] and "agent_spawn" not in r["note"]
    page = box.tb.results.read("c1", r["result_id"], offset=0, limit=50)
    assert page["text"].startswith("1\n2\n3") and page["total_chars"] > 20000
    box.tb.specs["agent_spawn"] = box.tb.specs["shell_run"]  # the hint names it only when the agent has it
    assert "agent_spawn" in box.run("shell_run", command="seq 1 5000")["note"]


@needs_seatbelt
def test_timeout_kills_the_whole_process_group(tmp_path: Path, box: Box) -> None:
    pidfile = tmp_path / "pid"
    t0 = time.time()
    r = box.run("shell_run", command=f"sleep 100 & echo $! > {pidfile}; wait", timeout_s=1)
    assert time.time() - t0 < 10
    assert r["timed_out"] is True and "process group" in r["note"]
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    if pid is None:  # the pidfile is outside the root, so the sandbox may have refused it: use the root instead
        r = box.run("shell_run", command="sleep 100 & echo $! > pid; wait", timeout_s=1)
        pid = int((box.root / "pid").read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@needs_seatbelt
def test_term_then_kill_when_term_is_ignored(box: Box) -> None:
    t0 = time.time()
    r = box.run("shell_run", command="trap '' TERM; sleep 100", timeout_s=1)
    took = time.time() - t0
    assert r["timed_out"] and 3.0 <= took < 12  # SIGTERM ignored, SIGKILL after the 3 s grace


@needs_seatbelt
def test_background_poll_and_kill(box: Box) -> None:
    async def go() -> None:
        r = await box.arun("shell_run", command="echo one; sleep 30", background=True)
        jid = r["job_id"]
        assert r["background"] and r["pid"]
        for _ in range(40):
            p = await box.arun("shell_poll", job_id=jid)
            if "one" in p.get("output", ""):
                break
            await asyncio.sleep(0.1)
        assert p["status"] == "running" and "one" in p["output"]
        again = await box.arun("shell_poll", job_id=jid)
        assert again["output"] == ""  # only what is new
        k = await box.arun("shell_kill", job_id=jid)
        assert k["status"] == "killed"
        assert (await box.arun("shell_poll", job_id=jid))["status"] == "killed"
        assert "No shell job" in (await box.arun("shell_poll", job_id="nope"))["error"]
    asyncio.run(go())


@needs_seatbelt
def test_background_cap_and_completion_note(box: Box) -> None:
    box.settings["shellMaxBackground"] = 1

    async def go() -> None:
        a = await box.arun("shell_run", command="sleep 30", background=True)
        b = await box.arun("shell_run", command="echo hi", background=True)
        assert "already running" in b["error"]
        await box.arun("shell_kill", job_id=a["job_id"])
        c = await box.arun("shell_run", command="echo finished-ok; exit 3", background=True)
        for _ in range(50):
            if box.tb.shell.notes.get("c1"):
                break
            await asyncio.sleep(0.1)
        notes = box.tb.shell.drain_notes("c1")
        assert len(notes) == 1 and c["job_id"] in notes[0] and "exit code 3" in notes[0] and "finished-ok" in notes[0]
        assert box.tb.shell.drain_notes("c1") == []
        # opt out of the notification
        d = await box.arun("shell_run", command="echo quiet", background=True, notify_on_complete=False)
        await asyncio.sleep(1.0)
        assert box.tb.shell.drain_notes("c1") == [] and (await box.arun("shell_poll", job_id=d["job_id"]))["status"] == "exited"
    asyncio.run(go())


@needs_seatbelt
def test_jobs_belong_to_their_conversation_and_die_with_its_run(box: Box) -> None:
    async def go() -> None:
        a = await box.arun("shell_run", command="sleep 30", background=True)
        other = dict(box.ctx, conversation_id="c2")
        assert "No shell job" in (await box.tb.call("shell_poll", {"job_id": a["job_id"]}, other))["error"]
        assert await box.tb.shell.kill_conversation("c1") == 1
        assert box.tb.shell.jobs[a["job_id"]].status == "killed"
    asyncio.run(go())


@needs_seatbelt
def test_shutdown_kills_every_live_job(box: Box) -> None:
    async def go() -> None:
        a = await box.arun("shell_run", command="sleep 30", background=True)
        pid = a["pid"]
        await box.tb.shell.shutdown()
        await asyncio.sleep(0.5)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(go())


def test_registry_prunes_finished_jobs_lru_and_refuses_when_all_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "MAX_TRACKED", 3)
    jobs = shell.ShellJobs()
    for i, fin in enumerate((300.0, 100.0, 200.0)):
        j = shell.Job(f"j{i}", "x", "/", "c", None, True, False, 1000)
        j.status, j.finished = "exited", time.time() - fin
        jobs.jobs[j.id] = j
    jobs._make_room()
    assert set(jobs.jobs) == {"j1", "j2"}  # j0 finished longest ago, so it went first
    for k in list(jobs.jobs):
        jobs.jobs[k].status = "running"
    while len(jobs.jobs) < 3:
        jobs.jobs[f"x{len(jobs.jobs)}"] = shell.Job(f"x{len(jobs.jobs)}", "x", "/", "c", None, True, False, 10)
    with pytest.raises(shell.ShellError, match="still running"):
        jobs._make_room()


def test_finished_jobs_age_out_after_30_minutes() -> None:
    jobs = shell.ShellJobs()
    old = shell.Job("old", "x", "/", "c", None, True, False, 10)
    old.status, old.finished = "exited", time.time() - 31 * 60
    new = shell.Job("new", "x", "/", "c", None, True, False, 10)
    new.status, new.finished = "exited", time.time() - 60
    jobs.jobs.update(old=old, new=new)
    jobs.prune()
    assert set(jobs.jobs) == {"new"}


def test_rolling_buffer_keeps_the_newest_200k_and_polls_report_the_drop() -> None:
    j = shell.Job("j", "x", "/", "c", None, True, False, shell.BG_BUFFER)
    j.append("a" * 150_000)
    j.append("b" * 150_000)
    assert len(j.buf) == shell.BG_BUFFER and j.buf.endswith("b") and j.base == 100_000
    out = shell.ShellJobs().poll(j, limit=10)
    assert out["dropped_chars"] == 100_000 and out["more"] is True


@needs_seatbelt
def test_restart_marks_survivors_orphaned_and_never_adopts_them(tmp_path: Path, box: Box) -> None:
    async def go() -> None:
        a = await box.arun("shell_run", command="sleep 60", background=True)
        job = box.tb.shell.jobs[a["job_id"]]
        state = json.loads((box.db.data_dir / "shell_jobs.json").read_text())
        assert state[0]["pid"] == job.pid and state[0]["pgid"] == job.pgid and state[0]["run_id"] is None
        assert {"job_id", "cwd", "run_id"} <= set(state[0])
        # a "restarted" registry reads the file: the process is still alive, so it is listed, never adopted
        fresh = shell.ShellJobs(box.db.data_dir / "shell_jobs.json")
        o = fresh.jobs[a["job_id"]]
        assert o.status == "orphaned" and o.proc is None and o.live() is False
        assert "cannot be adopted" in fresh.poll(o)["note"]
        assert fresh.running_background() == 1  # it still counts against the cap
        assert await fresh.kill(o) == "killed"
        await asyncio.sleep(0.3)
        with pytest.raises(ProcessLookupError):
            os.kill(job.pid, 0)
        await box.tb.shell.shutdown()
    asyncio.run(go())


def test_dead_or_recycled_pids_are_not_listed_as_orphans(tmp_path: Path) -> None:
    state = tmp_path / "s.json"
    state.write_text(json.dumps([{"job_id": "gone", "pid": 2_999_999, "pgid": 2_999_999, "cwd": "/", "run_id": None},
                                 {"job_id": "recycled", "pid": os.getpid(), "pgid": 1, "cwd": "/", "run_id": None}]))
    assert shell.ShellJobs(state).jobs == {}


def test_unsandboxed_is_refused_while_the_sandbox_works(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    r = box.run("shell_run", command="echo hi", unsandboxed=True)
    assert "sandbox is available" in r["error"]


def test_no_sandbox_means_refused_until_the_forced_unsandboxed_path(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "sandbox_available", lambda: False)
    r = box.run("shell_run", command="echo hi > made.txt")
    assert "not available" in r["error"] and not (box.root / "made.txt").exists()  # never silently downgraded
    ok = box.run("shell_run", command="echo hi > made.txt && echo ran", unsandboxed=True)
    assert ok["exit_code"] == 0 and ok["sandboxed"] is False and (box.root / "made.txt").exists()
    assert box.ctx["tainted"] is True and "shell_run:unsandboxed" in box.ctx["taint_sources"]


@needs_seatbelt
def test_a_profile_that_fails_to_apply_is_refused_then_unsandboxed_may_be_asked_for(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "shell_profile", lambda writable, network=False: "(version 1) (this is not sbpl")
    r = box.run("shell_run", command="echo hi")
    assert "sandbox refused to start" in r["error"] and box.tb.shell.sandbox_failed
    assert "sandbox is available" not in box.run("shell_run", command="echo ran", unsandboxed=True).get("error", "")


def test_unattended_runs_cannot_use_the_shell(box: Box) -> None:
    box.ctx["proposal_only"] = True
    assert "unattended" in box.run("shell_run", command="ls")["error"]


def test_empty_command_is_a_shaped_error(box: Box) -> None:
    assert box.run("shell_run", command="  ")["error"]


def test_shell_profile_shape() -> None:
    p = sandbox.shell_profile(["/tmp/w"], network=False)
    assert "(deny network*)" in p and "(allow network*)" not in p and "(deny default)" in p
    assert ".ssh" in p and "Keychains" in p and "gcloud" in p and r"\.env" in p
    assert "hooks" in p and "/tmp/w" in p
    assert "(allow network*)" in sandbox.shell_profile(["/tmp/w"], network=True)


def test_spilled_outputs_older_than_a_week_are_swept(box: Box) -> None:
    old = box.tb.results.store("c1", None, "shell_run", "old output", {})
    new = box.tb.results.store("c1", None, "shell_run", "new output", {})
    other = box.tb.results.store("c1", None, "read_local_file", "not shell", {})
    with box.db.tx() as c:
        c.execute("UPDATE tool_results SET created_at=? WHERE id IN (?,?)", (time.time() - 8 * 86400, old["id"], other["id"]))
    assert box.tb.shell.sweep_spills(box.db) == 1
    assert box.tb.results.get(old["id"]) is None and box.tb.results.get(new["id"]) and box.tb.results.get(other["id"])
    assert box.tb.shell.sweep_spills(box.db) == 0  # at most once an hour


# ---- idle VM sandboxes are stopped by a reaper (microvm.py) ----
def _vm():
    from test_microvm_checkpoints import make
    sb, d = make()
    sb._reaper_stop.set()  # no background thread in a unit test: stop_idle() is called by hand
    return sb, d


def test_idle_sandbox_is_stopped_after_300s_and_restarts_on_next_use() -> None:
    sb, d = _vm()
    name = sb.ensure("c1")
    t = sb._last[name]
    assert sb.stop_idle(now=t + 299) == [] and d.containers[name]["running"] is True
    assert sb.stop_idle(now=t + 301) == [name] and d.containers[name]["running"] is False
    assert sb.stop_idle(now=t + 900) == []  # already stopped: nothing to do
    assert sb.ensure("c1") == name and d.containers[name]["running"] is True  # state kept, restarted on use
    assert name in d.containers  # stopped, never removed


def test_a_command_in_flight_is_never_reaped_and_use_resets_the_clock() -> None:
    sb, d = _vm()
    name = sb.ensure("c1")
    t = sb._last[name]
    sb._busy[name] = 1
    assert sb.stop_idle(now=t + 5000) == []
    sb._busy[name] = 0
    sb.exec("c1", "true")
    assert sb._last[name] >= t and sb.stop_idle(now=sb._last[name] + 10) == []


def test_reaper_thread_starts_once_and_shutdown_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_microvm_checkpoints import make
    sb, d = make()
    sb.ensure("c1")
    th = sb._reaper
    assert th is not None and th.is_alive() and th.daemon
    sb.ensure("c1")
    assert sb._reaper is th
    monkeypatch.setattr(shutil, "which", lambda b: "/usr/bin/docker")
    sb.shutdown()
    th.join(2)
    assert not th.is_alive() and d.containers[sb._name("c1")]["running"] is False
