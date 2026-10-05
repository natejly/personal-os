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
    assert r["sandboxed"] is True and r["network"]["mode"] == "allowlist" and r["network"]["contacted"] == []  # the default: proxy only
    # the per-run tmp dir is writable, and gone afterwards
    t = box.run("shell_run", command="echo hi > $TMPDIR/t && cat $TMPDIR/t && echo $TMPDIR")
    assert t["exit_code"] == 0 and "hi" in t["output"]
    assert not os.path.exists(t["output"].split()[-1])


@needs_seatbelt
def test_here_documents_work_inside_the_sandbox(box: Box) -> None:
    """zsh writes a here-document to a temp file under TMPPREFIX; it must land in the writable per-run tmp dir."""
    r = box.run("shell_run", command="cat > made.txt <<'EOF'\nhello heredoc\nEOF\ncat made.txt")
    assert r["exit_code"] == 0 and "hello heredoc" in r["output"], r["output"]
    assert (box.root / "made.txt").read_text().strip() == "hello heredoc"


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
    box.settings["shellRegistryAccess"] = False  # no registry preset and no allowed domains: the "no network" mode
    assert box.run("shell_run", command="true")["network"] is False

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
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    gkey = "AIzaSyA1234567890abcdefGHIJKLMNOPQRSTUV"
    oauth = "ya29.a0AfH6SMCabcdefghijklmnopqrstuvwxyz"
    assert pat not in shell._scrub(f"token {pat}")
    assert gkey not in shell._scrub(gkey) and oauth not in shell._scrub(oauth)
    assert "hunter2" not in shell._scrub("https://user:hunter2@example.com/x")
    assert "secretvalue" not in shell._scrub("https://example.com/a?token=secretvalue")
    keep = "commit 3f2a91c7d8e0b1a2 src/app/main.py:42 password = os.environ['X']"
    assert shell._scrub(keep) == keep


def test_run_python_strips_a_printed_token() -> None:
    from personal_os.sandbox import run_python
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    out = run_python(f"print({pat!r})")
    assert out["exit_code"] == 0
    assert pat not in out["stdout"] and "[github-pat]" in out["stdout"]


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
    assert "fs_grep" in r["note"] and "read_tool_result" in r["note"]
    # agent_spawn is suggested only to a caller that may use it
    assert ("agent_spawn" in r["note"]) == ("agent_spawn" in box.tb.specs)
    box.ctx["modes"] = {"agent_spawn": "off"}
    try:
        r2 = box.run("shell_run", command="seq 1 5000")
    finally:
        box.ctx.pop("modes")
    assert "agent_spawn" not in r2["note"]
    page = box.tb.results.read("c1", r["result_id"], offset=0, limit=50)
    assert page["text"].startswith("1\n2\n3") and page["total_chars"] > 20000
    box.tb.specs["agent_spawn"] = box.tb.specs["shell_run"]  # the hint names it only when the agent has it
    assert "agent_spawn" in box.run("shell_run", command="seq 1 5000")["note"]


@needs_seatbelt
def test_timeout_kills_the_whole_process_group(tmp_path: Path, box: Box) -> None:
    pidfile = tmp_path / "pid"
    t0 = time.time()
    r = box.run("shell_run", command=f"sleep 100 & echo $! > {pidfile}; wait", timeout_s=1, on_timeout="kill")
    assert time.time() - t0 < 10
    assert r["timed_out"] is True and "process group" in r["note"]
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    if pid is None:  # the pidfile is outside the root, so the sandbox may have refused it: use the root instead
        r = box.run("shell_run", command="sleep 100 & echo $! > pid; wait", timeout_s=1, on_timeout="kill")
        pid = int((box.root / "pid").read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@needs_seatbelt
def test_term_then_kill_when_term_is_ignored(box: Box) -> None:
    t0 = time.time()
    r = box.run("shell_run", command="trap '' TERM; sleep 100", timeout_s=1, on_timeout="kill")
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
    monkeypatch.setattr(sandbox, "shell_profile", lambda writable, network=False, **kw: "(version 1) (this is not sbpl")
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


# ---- network modes, auto-approval, cwd persistence, timeout promotion (cowork shell slice) ----
def test_profile_allows_only_the_proxy_port_in_allowlist_mode() -> None:
    p = sandbox.shell_profile(["/tmp/w"], network=False, proxy_port=48123)
    assert "(deny network*)" in p and "(allow network*)" not in p
    assert p.count("network-outbound") == 1 and 'localhost:48123' in p
    assert "network-outbound" not in sandbox.shell_profile(["/tmp/w"])
    assert "localhost" not in sandbox.shell_profile(["/tmp/w"], network=True, proxy_port=1)  # open network needs no proxy rule


@needs_seatbelt
def test_allowlist_mode_env_and_only_the_proxy_is_reachable(box: Box) -> None:
    r = box.run("shell_run", command="env | grep -i proxy | sort; echo ALL=${ALL_PROXY-unset}")
    assert r["exit_code"] == 0
    for k in ("HTTP_PROXY=http://", "HTTPS_PROXY=http://", "http_proxy=http://", "https_proxy=http://", "NO_PROXY="):
        assert k in r["output"], k
    assert "ALL=unset" in r["output"] and r["network"]["mode"] == "allowlist"
    # a host that is not allowed: the proxy answers 403 and records it; the reply is not tainted, the note tells the way forward
    r = box.run("shell_run", command="curl -sS -m 8 https://not-allowed.example.org/ -o /dev/null -w '%{http_code}' ; true")
    assert r["network"]["blocked"] == ["not-allowed.example.org"] and "allow a host" in r["note"]
    assert box.ctx["tainted"] is False
    # a direct connection that skips the proxy is denied by the sandbox itself
    r = box.run("shell_run", command="curl -sS -m 5 --noproxy '*' http://93.184.216.34/ ; echo rc=$?")
    assert "rc=0" not in r["output"]


@needs_seatbelt
def test_an_allowed_host_that_cannot_be_reached_is_not_counted_as_contacted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os import egress

    async def fake_resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def fake_connect(addr: str, port: int) -> Any:
        raise OSError("no internet in tests")
    monkeypatch.setattr(egress, "resolve", fake_resolve)
    monkeypatch.setattr(egress, "connect", fake_connect)
    box.settings["shellAllowedDomains"] = ["example.com"]
    r = box.run("shell_run", command="curl -sS -m 8 https://api.example.com/ ; true")
    assert r["network"]["blocked"] == [] and r["network"]["contacted"] == []
    assert box.ctx["tainted"] is False


def test_contacted_hosts_taint_the_reply_like_open_network(box: Box) -> None:
    ctx: dict[str, Any] = {"tainted": False, "taint_sources": []}
    shell.taint(ctx, "shell_run:network")
    shell.taint(ctx, "shell_run:network")
    assert ctx["tainted"] is True and ctx["taint_sources"] == ["shell_run:network"]
    assert "Settings" in shell.net_blocked_note(["a.com"]) and "desk_ask" in shell.net_blocked_note(["a.com"])


def test_auto_ok_truth_table(tmp_path: Path) -> None:
    desk = (tmp_path / "desk").resolve()
    (desk / "work").mkdir(parents=True)
    elsewhere = (tmp_path / "else").resolve()
    elsewhere.mkdir()
    ctx: dict[str, Any] = {"desk_id": "d1", "conversation_id": "cA", "tainted": False}
    st: dict[str, Any] = {"deskShellAuto": True}

    def ok(args: dict[str, Any] | None = None, c: dict[str, Any] | None = None, s: dict[str, Any] | None = None,
           roots: list[Any] | None = None) -> bool:
        return shell.auto_ok(args or {"command": "ls"}, c if c is not None else ctx, s if s is not None else st,
                             [desk] if roots is None else roots)
    assert ok()
    assert ok({"command": "ls", "cwd": "work"}) and ok({"command": "ls", "cwd": str(desk / "work")})
    assert not ok({"command": "ls", "cwd": str(elsewhere)}) and not ok({"command": "ls", "cwd": ".."})
    assert not ok(c={**ctx, "desk_id": None}) and not ok(roots=[])                 # outside a desk
    assert not ok({"command": "ls", "unsandboxed": True})
    assert not ok(s={"deskShellAuto": False})                                       # setting off
    assert ok(s={})                                                                 # the setting defaults on
    assert not ok(s={**st, "tools": {"shell_run": "ask"}}) and not ok(s={**st, "tools": {"shell_run": False}})
    assert not ok(c={**ctx, "tool_overrides": {"shell_run": "ask"}})                # a chat/project choice stands
    assert not ok(s={**st, "shellNetwork": True})                                   # open network: the sandbox protects nothing
    assert not ok(c={**ctx, "proposal_only": True})
    # a reply that read untrusted text may not get a way out through the proxy without a card; with no network it may
    assert not ok(c={**ctx, "tainted": True})
    assert ok(c={**ctx, "tainted": True}, s={**st, "shellRegistryAccess": False})
    # where the last command ended counts as the default folder
    shell._remember_cwd("cA", str(desk / "work"))
    assert ok()
    shell._remember_cwd("cA", str(elsewhere))
    assert not ok()


def test_a_token_in_a_shell_path_error_is_stripped(tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb = Toolbox(None, None, None, lambda: {"workspaceRoots": [str(tmp_path)]})  # type: ignore[arg-type]
    out = asyncio.run(tb.specs["shell_run"].fn(
        {"conversation_id": "c1", "settings": {"workspaceRoots": [str(tmp_path)]}},
        command="ls", cwd=f"/tmp/{pat}"))
    assert pat not in str(out) and "[github-pat]" in out["error"]


def test_a_token_in_a_shell_cwd_is_stripped() -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    jobs = shell.ShellJobs()
    job = shell.Job("j1", "pwd", f"/tmp/note-{pat}", "c1", None, True, False, 1000)
    job.status = "exited"
    job.exit_code = 0
    out = jobs.poll(job)
    assert pat not in out["cwd"] and "[github-pat]" in out["cwd"]
    assert pat in job.cwd


@needs_seatbelt
def test_cd_persists_between_calls_and_an_explicit_cwd_wins(box: Box) -> None:
    (box.root / "a" / "b").mkdir(parents=True)
    r = box.run("shell_run", command="cd a/b && echo in")
    assert r["exit_code"] == 0 and r["cwd"] == str(box.root / "a" / "b")
    r = box.run("shell_run", command="pwd")
    assert r["output"].strip() == str(box.root / "a" / "b") and r["cwd"] == str(box.root / "a" / "b")
    r = box.run("shell_run", command="pwd", cwd="a")
    assert r["output"].strip() == str(box.root / "a")
    # exit code and output are untouched by the wrapper, including when the command exits by itself
    r = box.run("shell_run", command="echo x; exit 7")
    assert r["exit_code"] == 7 and r["output"].strip() == "x"
    # another conversation starts in the default folder; a cd outside every root is forgotten
    box.ctx["conversation_id"] = "c2"
    assert box.run("shell_run", command="pwd")["output"].strip() == str(box.root)
    r = box.run("shell_run", command="cd /tmp && true")
    assert r["cwd"] == str(box.root) and shell.remembered_cwd("c2") is None
    # a remembered folder that has since gone is not used
    box.ctx["conversation_id"] = "c1"
    shell._remember_cwd("c1", str(box.root / "a"))
    shutil.rmtree(box.root / "a")
    assert box.run("shell_run", command="pwd")["output"].strip() == str(box.root)


@needs_seatbelt
def test_timeout_moves_the_command_to_the_background_by_default(box: Box) -> None:
    async def go() -> None:  # one loop: the job's watcher lives on it
        t0 = time.time()
        r = await box.arun("shell_run", command="echo started; sleep 3; echo finished", timeout_s=1)
        assert time.time() - t0 < 3 and r["still_running"] is True and r["exit_code"] is None and r["timed_out"] is False
        assert "started" in r["output"] and "finished" not in r["output"] and r["job_id"] and "shell_poll" in r["note"]
        job = box.tb.shell.jobs[r["job_id"]]
        assert job.background and job.live()
        await asyncio.sleep(3.5)
        p = await box.arun("shell_poll", job_id=r["job_id"])
        assert p["status"] == "exited" and "finished" in p["output"] and "started" not in p["output"]
        assert "finished" in " ".join(box.tb.shell.drain_notes("c1"))  # completion notice, like any background job
    asyncio.run(go())


@needs_seatbelt
def test_timeout_kills_when_asked_or_when_no_slot_is_free(box: Box) -> None:
    r = box.run("shell_run", command="sleep 30", timeout_s=1, on_timeout="kill")
    assert r["timed_out"] is True and "still_running" not in r
    box.settings["shellMaxBackground"] = 1
    first = box.run("shell_run", command="sleep 30", background=True)
    r = box.run("shell_run", command="sleep 30", timeout_s=1)
    assert r["timed_out"] is True and "process group" in r["note"]
    box.run("shell_kill", job_id=first["job_id"])
    assert "on_timeout" in box.run("shell_run", command="true", on_timeout="later")["error"]


def test_the_tool_describes_its_network_modes_and_timeout_choice(box: Box) -> None:
    spec = box.tb.specs["shell_run"]
    d = spec.description
    assert "proxy" in d and "on_timeout" in d and "environment variables do not" in d
    assert spec.parameters["properties"]["on_timeout"]["enum"] == ["background", "kill"]
    assert spec.default_mode == "ask"


def test_package_tool_caches_live_in_the_run_dir() -> None:
    """HOME is read-only inside the sandbox: pip's default cache would warn on every call and uv would fail."""
    env = shell.scrubbed_env("/tmp/run1")
    assert env["PIP_CACHE_DIR"] == "/tmp/run1/pip-cache" and env["UV_CACHE_DIR"] == "/tmp/run1/uv-cache"


@needs_seatbelt
def test_shell_profile_lets_the_shell_read_the_work_venv_but_not_the_rest_of_the_data_dir(tmp_path: Path) -> None:
    from personal_os import envs
    env = envs.WorkEnv(tmp_path, lambda: {})
    (env.dir / "bin").mkdir(parents=True)
    (env.dir / "bin" / "python").write_text("")
    (env.dir / envs.MARKER).write_text('{"packages": []}')
    prof = sandbox.shell_profile([str(tmp_path)])
    assert f'(allow file-read* (subpath "{os.path.realpath(env.dir)}"))' in prof
    assert prof.index("(deny file-read*") < prof.index("(allow file-read* (subpath")  # last match wins: the allow comes after the deny
    envs._active = None
    assert "envs/work" not in sandbox.shell_profile([str(tmp_path)])


def test_run_python_profile_allows_the_mime_tables_python_reads_at_import() -> None:
    """openpyxl builds a MimeTypes() on import, which reads /etc/apache2/mime.types; without the allow every office library
    died under the profile with PermissionError."""
    prof = sandbox._mac_profile("/tmp/w", sys.executable)
    assert '(literal "/private/etc/apache2/mime.types")' in prof and '(literal "/private/etc/mime.types")' in prof
