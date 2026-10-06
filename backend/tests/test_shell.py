"""shell_run / shell_poll / shell_kill: the host shell under Seatbelt. Offline; the Seatbelt assertions are skipped with a
message when sandbox-exec is absent (non-macOS)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import sandbox, shell  # noqa: E402
from personal_os.db import Database, now  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

needs_seatbelt = pytest.mark.skipif(not shell.sandbox_available(), reason="sandbox-exec is not available here")
if not shell.sandbox_available():
    print("NOTE: sandbox-exec is absent; Seatbelt assertions are skipped")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    data = tmp_path / "data"
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(data))
    db = Database(data)
    with db.tx() as c:
        c.execute("INSERT INTO conversations(id, project_id, title, model, settings, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                  ("c1", None, "t", "m", "{}", now(), now()))
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    cfg: dict[str, Any] = {"workspaceRoots": [str(root)]}
    tb = Toolbox(None, None, None, lambda: cfg, results=ToolResults(db))  # type: ignore[arg-type]
    return {"tb": tb, "root": root, "cfg": cfg, "tmp": tmp_path.resolve(), "db": db,
            "ctx": lambda **kw: {"conversation_id": "c1", "message_id": None, "settings": cfg, **kw}}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def sh(env: dict[str, Any], command: str, ctx: dict[str, Any] | None = None, **args: Any) -> Any:
    ctx = ctx if ctx is not None else env["ctx"]()
    return run(env["tb"].call("shell_run", {"command": command, **args}, ctx))


# ---- registration and gating ----
def test_registered_as_executes_tier_default_ask(env: dict[str, Any]) -> None:
    tb = env["tb"]
    spec = tb.specs["shell_run"]
    assert spec.danger == "executes" and spec.group == "shell" and spec.default_mode == "ask"
    assert tb.effective({}, None, None)["shell_run"] == "ask"
    assert tb.specs["shell_poll"].default_mode == "on"
    # an explicit user setting still wins over the tool's own default
    assert tb.effective({"shell_run": "on"}, None, None)["shell_run"] == "on"


def test_unsandboxed_is_a_forced_ask_no_grant_removes(env: dict[str, Any]) -> None:
    tb = env["tb"]
    assert tb.gate("shell_run", "on", {}, {"command": "ls"}) == "on"
    assert tb.gate("shell_run", "on", {}, {"command": "ls", "unsandboxed": True}) == "ask"
    assert tb.gate("shell_run", "off", {}, {"command": "ls", "unsandboxed": True}) == "off"
    assert tb.gate("shell_run", "on", {}) == "on"  # callers that pass no args are unchanged


def test_settings_defaults() -> None:
    from personal_os import llm
    assert llm.DEFAULT_SETTINGS["shellNetwork"] is False
    assert llm.DEFAULT_SETTINGS["shellTimeoutSec"] == 120
    assert llm.DEFAULT_SETTINGS["shellMaxBackground"] == 4


# ---- where it may run ----
def test_cwd_outside_roots_refused(env: dict[str, Any]) -> None:
    out = sh(env, "echo hi", cwd=str(env["tmp"]))
    assert "outside" in out["error"] and str(env["root"]) in out["error"]
    out = sh(env, "echo hi", cwd="/")
    assert "outside" in out["error"]
    # a symlink inside the root that points out is resolved before the check
    (env["root"] / "link").symlink_to(env["tmp"])
    assert "outside" in sh(env, "echo hi", cwd=str(env["root"] / "link"))["error"]


def test_no_root_no_desk_refuses_with_instruction(env: dict[str, Any]) -> None:
    env["cfg"]["workspaceRoots"] = []
    out = sh(env, "echo hi")
    assert "Settings" in out["error"] and "workspace" in out["error"].lower()


def test_empty_command(env: dict[str, Any]) -> None:
    assert "needs a command" in sh(env, "  ")["error"]


@needs_seatbelt
def test_default_cwd_and_subfolder(env: dict[str, Any]) -> None:
    (env["root"] / "sub").mkdir()
    out = sh(env, "pwd")
    assert out["exit_code"] == 0 and out["output"].strip() == str(env["root"]) and out["sandboxed"] is True
    assert sh(env, "pwd", cwd="sub")["output"].strip() == str(env["root"] / "sub")


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root

    def ensure(self, desk_id: str) -> Path:
        p = self.root / desk_id
        (p / "outputs").mkdir(parents=True, exist_ok=True)
        return p


@needs_seatbelt
def test_desk_workspace_is_the_default_and_writable(env: dict[str, Any]) -> None:
    env["cfg"]["workspaceRoots"] = []
    env["tb"].workspace = FakeWorkspace(env["tmp"] / "desks")
    ctx = env["ctx"](desk_id="d1")
    out = sh(env, "python3 -c \"open('outputs/report.txt','w').write('ok')\" && ls outputs", ctx)
    assert out["exit_code"] == 0 and "report.txt" in out["output"]
    assert (env["tmp"] / "desks" / "d1" / "outputs" / "report.txt").read_text() == "ok"
    victim = env["tmp"] / "elsewhere" / "x"
    victim.parent.mkdir()
    assert sh(env, f"touch {victim}", ctx)["exit_code"] != 0 and not victim.exists()


# ---- the sandbox itself ----
@needs_seatbelt
def test_write_inside_ok_outside_fails(env: dict[str, Any]) -> None:
    outside = env["tmp"] / "outside"
    outside.mkdir()
    out = sh(env, f"echo yes > inside.txt && touch {outside}/x; echo rc=$?")
    assert (env["root"] / "inside.txt").read_text().strip() == "yes"
    assert "rc=1" in out["output"] and "not permitted" in out["output"].lower()
    assert not (outside / "x").exists()


@needs_seatbelt
def test_per_run_tmp_is_writable_and_removed(env: dict[str, Any]) -> None:
    out = sh(env, 'echo data > "$TMPDIR/scratch" && echo $TMPDIR')
    tmpdir = out["output"].strip()
    assert out["exit_code"] == 0 and "pos-shell-" in tmpdir and not os.path.exists(tmpdir)


@needs_seatbelt
def test_git_hooks_and_config_are_not_writable(env: dict[str, Any]) -> None:
    (env["root"] / ".git" / "hooks").mkdir(parents=True)
    (env["root"] / ".git" / "config").write_text("[core]\n")
    out = sh(env, "echo x > .git/hooks/pre-commit; echo h=$?; echo x >> .git/config; echo c=$?; echo ok > .git/other; echo o=$?")
    assert "h=1" in out["output"] and "c=1" in out["output"] and "o=0" in out["output"]
    assert not (env["root"] / ".git" / "hooks" / "pre-commit").exists()
    assert (env["root"] / ".git" / "config").read_text() == "[core]\n"


@needs_seatbelt
def test_secrets_are_unreadable(env: dict[str, Any]) -> None:
    (env["root"] / ".env").write_text("API_KEY=hunter2hunter2\n")
    (env["root"] / ".env.local").write_text("API_KEY=hunter2hunter2\n")
    (env["root"] / "notes.txt").write_text("fine\n")
    out = sh(env, "cat notes.txt; cat .env; cat .env.local; ls ~/.ssh; ls ~/Library/Keychains")
    assert "fine" in out["output"] and "hunter2" not in out["output"]
    assert out["output"].lower().count("not permitted") >= 4
    # the data dir (database, token) is unreadable too
    (env["db"].data_dir / "secret.txt").write_text("tok")
    assert "tok" not in sh(env, f"cat {env['db'].data_dir}/secret.txt")["output"]


@needs_seatbelt
def test_network_is_off_by_default(env: dict[str, Any]) -> None:
    code = "import socket; socket.create_connection(('127.0.0.1', 9), timeout=2)"
    out = sh(env, f'python3 -c "{code}"')
    assert out["exit_code"] != 0 and "not permitted" in out["output"].lower() and out["network"] is False
    # curl cannot even resolve a name
    assert sh(env, "curl -sS -m 3 http://example.com")["exit_code"] != 0


@needs_seatbelt
def test_network_setting_allows_and_taints(env: dict[str, Any]) -> None:
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    env["cfg"]["shellNetwork"] = True
    ctx = env["ctx"]()
    out = sh(env, f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}), timeout=2); print('connected')\"", ctx)
    srv.close()
    assert "connected" in out["output"] and out["network"] is True
    assert ctx["tainted"] is True and "shell_run:network" in ctx["taint_sources"]


def test_env_is_scrubbed(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAIN_PLANTED_SECRET", "s3cr3t-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-planted")
    out = sh(env, "env")
    assert "s3cr3t-value" not in out["output"] and "OPENAI_API_KEY" not in out["output"]
    assert "TERM=dumb" in out["output"] and "TMPDIR=" in out["output"] and "PATH=" in out["output"]


# ---- timeouts, output ----
@needs_seatbelt
def test_timeout_kills_the_whole_process_group(env: dict[str, Any]) -> None:
    t0 = time.time()
    out = sh(env, "sleep 100 & echo $! > child.pid; sleep 100", timeout_s=1)
    assert out["timed_out"] is True and time.time() - t0 < 10
    assert "process group" in out["note"]
    pid = int((env["root"] / "child.pid").read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@needs_seatbelt
def test_timeout_is_clamped_to_ten_minutes(env: dict[str, Any]) -> None:
    seen: dict[str, Any] = {}
    real = env["tb"].shell.start

    async def spy(*a: Any, **kw: Any) -> Any:
        seen.update(kw)
        return await real(*a, **kw)
    env["tb"].shell.start = spy
    sh(env, "true", timeout_s=99999)
    assert seen["timeout"] == 600
    sh(env, "true")
    assert seen["timeout"] == 120
    env["cfg"]["shellTimeoutSec"] = 45
    sh(env, "true")
    assert seen["timeout"] == 45


def test_truncate_by_lines_and_bytes() -> None:
    text = "\n".join(str(i) for i in range(5000))
    out, cut = shell.truncate(text)
    assert cut and out.split("\n")[-1] == "4999" and len(out.split("\n")) <= shell.TRUNC_LINES
    wide = "x" * 200_000
    out, cut = shell.truncate(wide)
    assert cut and len(out.encode()) <= shell.TRUNC_BYTES
    assert shell.truncate("short") == ("short", False)


@needs_seatbelt
def test_long_output_is_truncated_with_a_handle(env: dict[str, Any]) -> None:
    out = sh(env, "seq 1 6000")
    assert out["truncated"] is True and out["output"].rstrip().endswith("6000")
    assert len(out["output"].split("\n")) <= shell.TRUNC_LINES
    assert "fs_grep" in out["note"] and "read_tool_result" in out["note"] and "agent_spawn" not in out["note"]
    full = env["tb"].results.read("c1", out["result_id"], 0, 20000)
    assert full["total_chars"] > len(out["output"]) and full["text"].startswith("1\n2\n3")


@needs_seatbelt
def test_hint_names_agent_spawn_when_the_tool_exists(env: dict[str, Any]) -> None:
    env["tb"].specs["agent_spawn"] = env["tb"].specs["shell_poll"]
    assert "agent_spawn" in sh(env, "seq 1 6000")["note"]


@needs_seatbelt
def test_output_is_redacted(env: dict[str, Any]) -> None:
    out = sh(env, "echo AKIAIOSFODNN7EXAMPLE; echo ghp_abcdefghijklmnop1234; pwd; echo 9f2c1d7e8a3b4c5d6e7f8091a2b3c4d5e6f70812")
    assert "AKIAIOSFODNN7EXAMPLE" not in out["output"] and "ghp_abcdefghij" not in out["output"]
    # paths and commit-hash-like ids survive: redaction is for credential shapes, not for everything long
    assert str(env["root"]) in out["output"] and "9f2c1d7e8a3b4c5d6e7f8091a2b3c4d5e6f70812" in out["output"]


@needs_seatbelt
def test_stderr_is_interleaved_and_exit_code_kept(env: dict[str, Any]) -> None:
    out = sh(env, "echo out; echo err 1>&2; exit 3")
    assert out["exit_code"] == 3 and "out" in out["output"] and "err" in out["output"]


def test_spill_sweep_drops_old_outputs_only(env: dict[str, Any]) -> None:
    tr, db = env["tb"].results, env["db"]
    old = tr.store("c1", None, "shell_run", "old", {})["id"]
    new = tr.store("c1", None, "shell_run", "new", {})["id"]
    other = tr.store("c1", None, "fetch_url", "keep", {})["id"]
    with db.tx() as c:
        c.execute("UPDATE tool_results SET created_at=? WHERE id IN (?,?)", (time.time() - 8 * 86400, old, other))
    assert env["tb"].shell.sweep_spills(db) == 1
    assert tr.get(old) is None and tr.get(new) and tr.get(other)
    assert env["tb"].shell.sweep_spills(db) == 0  # at most once an hour


# ---- background jobs ----
@needs_seatbelt
def test_background_poll_and_kill(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb, ctx = env["tb"], env["ctx"]()
        job = await tb.call("shell_run", {"command": "echo started; sleep 60; echo never", "background": True}, ctx)
        assert job["background"] is True and job["job_id"]
        jid = job["job_id"]
        seen = ""
        for _ in range(50):
            p = await tb.call("shell_poll", {"job_id": jid}, ctx)
            seen += p.get("output", "")
            if "started" in seen:
                break
            await asyncio.sleep(0.1)
        assert "started" in seen and p["status"] == "running"
        again = await tb.call("shell_poll", {"job_id": jid}, ctx)
        assert again["output"] == ""  # only what is new
        k = await tb.call("shell_kill", {"job_id": jid}, ctx)
        assert k["status"] == "killed"
        assert (await tb.call("shell_poll", {"job_id": jid}, ctx))["status"] == "killed"
        assert tb.shell.get(jid, "c1").proc.returncode is not None
    run(go())


@needs_seatbelt
def test_jobs_are_private_to_their_conversation(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb = env["tb"]
        job = await tb.call("shell_run", {"command": "sleep 30", "background": True}, env["ctx"]())
        other = {**env["ctx"](), "conversation_id": "c2"}
        assert "No shell job" in (await tb.call("shell_poll", {"job_id": job["job_id"]}, other))["error"]
        assert "No shell job" in (await tb.call("shell_kill", {"job_id": job["job_id"]}, other))["error"]
        assert await tb.shell.kill_conversation("c1") == 1
    run(go())


@needs_seatbelt
def test_background_cap(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb, ctx = env["tb"], env["ctx"]()
        env["cfg"]["shellMaxBackground"] = 2
        ids = [(await tb.call("shell_run", {"command": "sleep 30", "background": True}, ctx))["job_id"] for _ in range(2)]
        third = await tb.call("shell_run", {"command": "sleep 30", "background": True}, ctx)
        assert "already running" in third["error"]
        await tb.call("shell_kill", {"job_id": ids[0]}, ctx)
        assert "job_id" in await tb.call("shell_run", {"command": "sleep 30", "background": True}, ctx)
        await tb.shell.shutdown()
    run(go())


@needs_seatbelt
def test_notify_on_complete_is_queued_for_the_next_round(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb, ctx = env["tb"], env["ctx"]()
        job = await tb.call("shell_run", {"command": "echo built; exit 2", "background": True}, ctx)
        await tb.shell.wait(tb.shell.get(job["job_id"], "c1"))
        notes = tb.shell.drain_notes("c1")
        assert len(notes) == 1 and "exit code 2" in notes[0] and "built" in notes[0] and job["job_id"] in notes[0]
        assert tb.shell.drain_notes("c1") == []
        quiet = await tb.call("shell_run", {"command": "echo x", "background": True, "notify_on_complete": False}, ctx)
        await tb.shell.wait(tb.shell.get(quiet["job_id"], "c1"))
        assert tb.shell.drain_notes("c1") == []
    run(go())


@needs_seatbelt
def test_shutdown_kills_live_jobs(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb, ctx = env["tb"], env["ctx"]()
        job = await tb.call("shell_run", {"command": "sleep 60", "background": True}, ctx)
        j = tb.shell.get(job["job_id"], "c1")
        await tb.shell.shutdown()
        await asyncio.wait_for(j.proc.wait(), 5)
        assert j.proc.returncode is not None
    run(go())


def test_registry_prunes_finished_jobs_and_caps_tracking() -> None:
    jobs = shell.ShellJobs()
    for i in range(shell.MAX_TRACKED):
        j = shell.Job(f"j{i}", "x", "/", "c", None, True, False, 10)
        j.status, j.finished = "exited", time.time() - 100 + i
        jobs.jobs[j.id] = j
    jobs._make_room()
    assert len(jobs.jobs) == shell.MAX_TRACKED - 1 and "j0" not in jobs.jobs  # least recently finished goes first
    old = shell.Job("old", "x", "/", "c", None, True, False, 10)
    old.status, old.finished = "exited", time.time() - shell.FINISHED_KEEP_S - 5
    jobs.jobs["old"] = old
    jobs.prune()
    assert "old" not in jobs.jobs
    for i in range(shell.MAX_TRACKED):
        jobs.jobs[f"r{i}"] = shell.Job(f"r{i}", "x", "/", "c", None, True, False, 10)
    for j in jobs.jobs.values():
        j.status = "running"
    with pytest.raises(shell.ShellError):
        jobs._make_room()  # nothing finished to evict


def test_rolling_buffer_and_poll_reports_dropped_chars() -> None:
    j = shell.Job("j", "x", "/", "c", None, True, False, 100)
    j.append("a" * 80)
    j.append("b" * 80)
    assert len(j.buf) == 100 and j.base == 60
    out = shell.ShellJobs().poll(j)
    assert out["dropped_chars"] == 60 and out["output"] == "a" * 20 + "b" * 80 and out["more"] is False


def test_survivors_are_orphaned_not_adopted(tmp_path: Path) -> None:
    state = tmp_path / "shell_jobs.json"
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        state.write_text(json.dumps([
            {"job_id": "live1", "pid": p.pid, "pgid": p.pid, "cwd": "/x", "run_id": "r", "conversation_id": "c1", "command": "sleep 60"},
            {"job_id": "dead1", "pid": 999999, "pgid": 999999, "cwd": "/x", "run_id": "r", "conversation_id": "c1", "command": "x"},
            {"job_id": "recycled", "pid": p.pid, "pgid": p.pid + 12345, "cwd": "/x", "run_id": "r", "conversation_id": "c1", "command": "x"}]))
        jobs = shell.ShellJobs(state)
        assert set(jobs.jobs) == {"live1"} and jobs.jobs["live1"].status == "orphaned"
        poll = jobs.poll(jobs.jobs["live1"])
        assert poll["status"] == "orphaned" and "cannot be adopted" in poll["note"]
        assert asyncio.run(jobs.kill(jobs.jobs["live1"])) == "killed"
        p.wait(timeout=10)
        assert p.returncode is not None
        assert json.loads(state.read_text()) == []
    finally:
        if p.poll() is None:
            p.kill()


@needs_seatbelt
def test_running_jobs_are_persisted_for_orphan_detection(env: dict[str, Any]) -> None:
    async def go() -> None:
        tb, ctx = env["tb"], env["ctx"]()
        job = await tb.call("shell_run", {"command": "sleep 30", "background": True}, ctx)
        rows = json.loads((env["db"].data_dir / "shell_jobs.json").read_text())
        assert [r["job_id"] for r in rows] == [job["job_id"]] and rows[0]["pgid"] == rows[0]["pid"]
        assert set(rows[0]) >= {"job_id", "pid", "pgid", "cwd", "run_id"}
        await tb.shell.shutdown()
        assert json.loads((env["db"].data_dir / "shell_jobs.json").read_text()) == []
    run(go())


# ---- when the sandbox is not there ----
def test_missing_sandbox_refuses_and_names_the_escape(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "sandbox_available", lambda: False)
    out = sh(env, "echo hi")
    assert "not available" in out["error"] and "unsandboxed=true" in out["try_instead"]


def test_unsandboxed_runs_when_asked_and_taints(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "sandbox_available", lambda: False)
    ctx = env["ctx"]()
    out = sh(env, "echo escaped", ctx, unsandboxed=True)
    assert out["exit_code"] == 0 and "escaped" in out["output"] and out["sandboxed"] is False
    assert ctx["tainted"] and "shell_run:unsandboxed" in ctx["taint_sources"]
    # still confined to the granted folders by cwd, and still has the scrubbed environment
    assert "outside" in sh(env, "echo hi", cwd="/", unsandboxed=True)["error"]


@needs_seatbelt
def test_profile_failing_to_apply_is_refused_not_downgraded(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "shell_profile", lambda *a, **k: "(version 1) (this is not valid")
    out = sh(env, "echo hi > leak.txt")
    assert "sandbox refused to start" in out["error"] and "unsandboxed=true" in out["try_instead"]
    assert not (env["root"] / "leak.txt").exists()


# ---- the profile builder both users share ----
def test_shell_profile_shape(tmp_path: Path) -> None:
    p = sandbox.shell_profile([str(tmp_path)])
    assert "(deny default)" in p and "(deny network*)" in p and "(allow network*)" not in p
    assert str(tmp_path.resolve()) in p and "/.ssh" in p and ".git/hooks" in p
    assert "(allow network*)" in sandbox.shell_profile([str(tmp_path)], network=True)
    py = sandbox._mac_profile(str(tmp_path), sys.executable)
    assert "(deny default)" in py and "/.ssh" in py and "network-outbound" not in py
    assert "network-outbound" in sandbox._mac_profile(str(tmp_path), sys.executable, (str(tmp_path / "s.sock"),))


@needs_seatbelt
def test_run_python_still_sandboxed() -> None:
    out = sandbox.run_python("import os\nprint(os.getcwd() != '')\nopen('/tmp/pos_should_fail','w')", 20)
    assert "True" in out["stdout"] and out["exit_code"] != 0 and not os.path.exists("/tmp/pos_should_fail")
