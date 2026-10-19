"""The Claude Code extension: the headless streaming driver, the permission hook and its route, approvals as cards, fleets,
cost, worktrees and the surface (tools, routes, manifest). No real claude binary: a fake executable speaks stream-json and runs
the PermissionRequest hook command exactly as the --settings JSON gives it, against a real loopback server.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_claude_code.py
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="claudecode-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from personal_os import claude_permission_hook as perm_hook  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions.authoring.check import BUILTINS, check  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

PKG = "personal_os.extensions.claude-code"
driver = importlib.import_module(f"{PKG}.driver")
manager = importlib.import_module(f"{PKG}.manager")
store = importlib.import_module(f"{PKG}.store")
cc_tools = importlib.import_module(f"{PKG}.tools")

FAKE = textwrap.dedent('''\
    #!{python}
    """A stand-in for `claude -p --output-format stream-json --input-format stream-json`, scripted by scenario.json beside it."""
    import json, os, signal, subprocess, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    sc = json.load(open(os.path.join(here, "scenario.json")))
    argv = sys.argv[1:]
    n = len([f for f in os.listdir(here) if f.startswith("record-")])
    json.dump({{"argv": argv, "cwd": os.getcwd(), "env": dict(os.environ)}}, open(os.path.join(here, f"record-{{n}}.json"), "w"))

    def out(obj):
        sys.stdout.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\\n")
        sys.stdout.flush()

    def on_int(*_):
        open(os.path.join(here, "sigint"), "w").write("1")
        if not sc.get("ignore_sigint"):
            sys.exit(130)
    signal.signal(signal.SIGINT, on_int)

    if sc.get("crash"):
        sys.stderr.write("boom: cannot start\\n")
        sys.exit(3)
    first = sys.stdin.readline()
    out({{"type": "system", "subtype": "init", "session_id": "11111111-2222-3333-4444-555555555555", "model": "claude-test",
         "cwd": os.getcwd(), "tools": ["Bash"], "permissionMode": "default", "capabilities": ["x"]}})
    if sc.get("hang"):
        time.sleep(600)
    turn = 0

    def finish(text):
        global turn
        turn += 1
        cost = round(sc.get("cost_step", 0.5) * turn, 4)
        usage = {{"claude-test": {{"costUSD": cost, "inputTokens": 100 * turn, "outputTokens": 10 * turn}}}}
        sub = sc.get("subtype", "success")
        out({{"type": "result", "subtype": sub, "is_error": sub != "success", "result": text, "num_turns": turn, "total_cost_usd": cost,
             "modelUsage": usage, "permission_denials": sc.get("denials", []), "structured_output": sc.get("structured")}})

    def hook(tool, tool_input):
        settings = json.loads(argv[argv.index("--settings") + 1])
        cmd = settings["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
        event = {{"hook_event_name": "PermissionRequest", "tool_name": tool, "tool_input": tool_input, "session_id": "x", "cwd": os.getcwd()}}
        p = subprocess.run(cmd, shell=True, input=json.dumps(event), capture_output=True, text=True)
        return json.loads(p.stdout)["hookSpecificOutput"]["decision"]

    out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "working on it"}}]}}}})
    time.sleep(sc.get("delay", 0))
    if sc.get("garbage"):
        out("this is not json {{")
    for i, (tool, tool_input) in enumerate(sc.get("asks", [])):
        out({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": f"tu{{i}}", "name": tool, "input": tool_input}}]}}}})
        decision = hook(tool, tool_input)
        open(os.path.join(here, f"decision-{{i}}"), "w").write(json.dumps(decision))
        out({{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": f"tu{{i}}", "content": decision["behavior"]}}]}}}})
    finish(sc.get("final", "all done"))
    for line in sys.stdin:
        msg = json.loads(line)["message"]["content"]
        out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "echo: " + msg}}]}}}})
        finish("echo: " + msg)
''')


def sh(*args: str, cwd: Path) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


class Env:
    """A host with the coding and claude-code extensions over a fresh database, a fake claude, a git repo and a loopback server."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.ui: list[tuple[str, Any]] = []
        self.wakes: list[tuple[str, str, dict[str, Any]]] = []
        self.bin = tmp / "bin"
        self.bin.mkdir()
        exe = self.bin / "claude"
        exe.write_text(FAKE.format(python=sys.executable))
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.scenario({})
        monkeypatch.setattr(driver, "binary", lambda: str(exe))
        monkeypatch.setattr(driver, "child_env", lambda: {"PATH": os.environ["PATH"], "HOME": str(tmp)})
        self.repo = tmp / "repo"
        self.repo.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "a.txt").write_text("a\n")
        sh("git", "add", "a.txt", cwd=self.repo)
        sh("git", "commit", "-qm", "init", cwd=self.repo)
        self.cfg: dict[str, Any] = {"ext.claude-code.claudeCodeWorktreeRoot": str(tmp / "wt")}
        self.db = Database(str(tmp / "data"))
        self.host = ExtensionHost()
        ctx = ExtensionContext(db=self.db, hidden_views=lambda: self.cfg.get("hiddenViews") or (), services={}, settings=lambda: self.cfg,
                               set_settings=self.db.set_settings, sid=lambda p: p, wsid=lambda p: p,
                               publish=lambda n, d: self.ui.append((n, d)), owe_wake=lambda c, t, w: self.wakes.append((c, t, w)))
        self.coding_scope = self.host.load_builtin("coding", ctx)
        self.scope = self.host.load_builtin("claude-code", ctx)
        self.host.migrate(self.db)
        self.tb = Toolbox(None, None, None, lambda: self.cfg, bus=self.host.bus, prechecks=self.host.prechecks)  # type: ignore[arg-type]
        self.coding = self.coding_scope.app.services["coding"]()
        self.coding.bind(self.tb)
        self.tb.extensions.extend([self.coding_scope, self.scope])
        self.tb.set_extension_tools(self.coding_scope)
        self.tb.set_extension_tools(self.scope)
        self.cc: Any = None
        self.server: uvicorn.Server | None = None
        self.approvals: list[tuple[str, dict[str, Any], bool]] = []
        self.answers: asyncio.Queue[Any] | None = None

    def scenario(self, data: dict[str, Any]) -> None:
        (self.bin / "scenario.json").write_text(json.dumps(data))

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in sorted(self.bin.glob("record-*.json"), key=lambda p: int(p.stem.split("-")[1]))]

    async def start(self) -> None:
        # the extension's own instance owns the routes; reach it through its tools' closure instead of building a second one
        spec = self.scope.tools["claude_code_run"]
        self.cc = next(c.cell_contents for c in spec.fn.__closure__ if isinstance(c.cell_contents, manager.ClaudeCode))
        app = FastAPI()
        self.host.mount(app, self.scope)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", loop="asyncio"))
        self.server = server
        self.serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        self.port = server.servers[0].sockets[0].getsockname()[1]
        self.coding.sessions.bridge_port = self.port
        self.cc.recover()

    async def close(self) -> None:
        await self.cc.stop_all("test over", grace=0.3)
        if self.server:
            self.server.should_exit = True
            await self.serving

    def ctx(self, answer: Callable[[str, dict[str, Any], bool], Awaitable[bool]] | None = None, **extra: Any) -> dict[str, Any]:
        async def approve(name: str, args: dict[str, Any], forced: bool, wait: bool = False) -> bool:
            self.approvals.append((name, args, forced))
            return await answer(name, args, forced) if answer else True
        return {"run_id": "run1", "conversation_id": "conv1", "approve": approve, **extra}

    async def call(self, tool: str, ctx: dict[str, Any], **args: Any) -> Any:
        return await self.scope.tools[tool].fn(ctx, **args)

    async def settle(self, sid: str, timeout: float = 15) -> dict[str, Any]:
        out = await self.cc.wait([sid], "all", timeout)
        assert not out["timed_out"], self.cc.known(sid)
        return out["sessions"][0]


def aio(test: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    wants_patch = "monkeypatch" in test.__code__.co_varnames[:test.__code__.co_argcount]

    def wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = Env(tmp_path, monkeypatch)

        async def go() -> None:
            await env.start()
            try:
                await (test(env, monkeypatch) if wants_patch else test(env))
            finally:
                await env.close()
        asyncio.run(asyncio.wait_for(go(), 90))
    wrapper.__name__ = test.__name__
    return wrapper


def run_obj(run_id: str = "run1", stopped: bool = False) -> SimpleNamespace:
    """What run.after delivers: the run object, with every attribute the hook catalog names."""
    ev = asyncio.Event()
    if stopped:
        ev.set()
    return SimpleNamespace(run_id=run_id, conversation_id="conv1", kind="chat", desk_id=None, turn=1, status="done", error=None, message_id=None,
                           input={}, cost=0.0, rounds=1, stop=ev)


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# ---- the driver ----
def test_argv_has_the_streaming_flags_and_only_the_options_that_were_set():
    plain = driver.argv("/x/claude", settings_json="{}")
    assert plain == ["/x/claude", "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose", "--settings", "{}"]
    full = driver.argv("/x/claude", settings_json="{}", model="m", max_turns=5, budget_usd=1.5, permission_mode="acceptEdits",
                       allowed_tools=["Read", "Bash(git log:*)"], append_system_prompt="hi", json_schema={"type": "object"})
    assert flag(full, "--model") == "m" and flag(full, "--max-turns") == "5" and flag(full, "--max-budget-usd") == "1.5"
    assert flag(full, "--permission-mode") == "acceptEdits" and flag(full, "--append-system-prompt") == "hi"
    assert full[full.index("--allowedTools") + 1:full.index("--allowedTools") + 3] == ["Read", "Bash(git log:*)"]
    assert json.loads(flag(full, "--json-schema")) == {"type": "object"} and full[-2] == "--settings"
    assert "--permission-mode" not in driver.argv("/x", settings_json="{}", permission_mode="default")


def test_parse_turns_stream_lines_into_events_and_tolerates_junk():
    init = driver.parse({"type": "system", "subtype": "init", "session_id": "s", "model": "m", "tools": ["a"]})
    assert init[0][0] == "init" and init[0][3]["claude_session_id"] == "s"
    asst = driver.parse({"type": "assistant", "message": {"content": [{"type": "text", "text": " hi "}, {"type": "thinking", "thinking": "x"},
                                                                       {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls  -la"}}]}})
    assert [(e[0], e[1], e[2]) for e in asst] == [("assistant", "hi", None), ("tool", "ls -la", "Bash")]
    res = driver.parse({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}], "is_error": True}]}})
    assert res[0][0] == "tool_result" and res[0][1] == "ok" and res[0][3] == {"call_id": "t1", "is_error": True}
    for junk in (None, 3, [], {}, {"type": "assistant"}, {"type": "assistant", "message": {"content": 5}}, {"type": "user", "message": None}, {"type": "stream_event"}):
        driver.parse(junk)   # never raises
    assert driver.parse("plain text")[0][0] == "note"


@aio
async def test_start_runs_the_fake_cli_in_the_worktree_and_the_first_prompt_goes_over_stdin(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="fix the bug", name="fixer", max_turns=7, budget_usd=2,
                         model="claude-test", allowed_tools=["Read"])
    assert "error" not in out, out
    row = await env.settle(out["id"])
    assert row["status"] == "done" and row["result_subtype"] == "success"
    rec = env.records()[0]
    assert rec["cwd"] == os.path.realpath(row["worktree"]) and row["worktree"].startswith(str(env.tmp / "wt" / "repo"))
    a = rec["argv"]
    assert "-p" in a and flag(a, "--output-format") == "stream-json" and flag(a, "--input-format") == "stream-json" and "--verbose" in a
    assert flag(a, "--max-turns") == "7" and flag(a, "--max-budget-usd") == "2" and flag(a, "--model") == "claude-test"
    assert "ANTHROPIC_API_KEY" not in rec["env"]
    settings = json.loads(flag(a, "--settings"))
    assert "deny" in settings["permissions"] and "PreToolUse" in settings["hooks"]
    hook = settings["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert hook["timeout"] == 660 and "claude_permission_hook" in hook["command"] and "GRAIN_HOOK_KEY=" in hook["command"]
    evs = store.events_after(env.db, row["id"])
    kinds = [e["kind"] for e in evs]
    assert kinds[:2] == ["note", "init"] and "assistant" in kinds and kinds[-1] == "result"
    assert evs[0]["text"].startswith("Task: fix the bug")
    assert row["claude_session_id"] == "11111111-2222-3333-4444-555555555555" and row["model"] == "claude-test"
    assert row["branch"].startswith("grain/") and (Path(row["worktree"]) / "a.txt").exists()


@aio
async def test_a_malformed_line_is_noted_and_the_session_carries_on(env: Env) -> None:
    env.scenario({"garbage": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x")
    row = await env.settle(out["id"])
    assert row["status"] == "done"
    assert any(e["kind"] == "note" and "not json" in e["text"] for e in store.events_after(env.db, row["id"]))


@aio
async def test_events_are_pushed_to_the_ui_as_session_and_event(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x")
    await env.settle(out["id"])
    names = {n for n, _ in env.ui}
    assert {"ext:claude-code:session", "ext:claude-code:event"} <= names
    sess = [d for n, d in env.ui if n == "ext:claude-code:session"][-1]
    assert {"id", "name", "repo_path", "worktree", "branch", "status", "model", "cost_usd", "num_turns", "result_subtype", "fleet_id",
            "conversation_id", "started_at", "ended_at", "pending_approval", "last_text"} <= set(sess)
    ev = [d for n, d in env.ui if n == "ext:claude-code:event"][0]
    assert {"seq", "session_id", "kind", "text", "tool", "data", "at"} <= set(ev)


# ---- approvals ----
@aio
async def test_a_permission_request_becomes_a_card_and_allow_lets_the_tool_run(env: Env) -> None:
    env.scenario({"asks": [["Bash", {"command": "npm test"}]]})
    seen: dict[str, Any] = {}

    async def answer(name: str, args: dict[str, Any], forced: bool) -> bool:
        seen["status"] = env.cc.known(sid)["status"]
        seen["pending"] = env.cc.known(sid)["pending_approval"]
        return True
    out = await env.call("claude_code_run", env.ctx(answer), repo_path=str(env.repo), prompt="run tests")
    sid = out["id"]
    row = await env.settle(sid)
    assert row["status"] == "done"
    name, args, forced = env.approvals[0]
    assert name == "claude_code_permission" and forced is True
    assert args["tool"] == "Bash" and args["input"] == {"command": "npm test"} and args["session_id"] == sid
    assert seen == {"status": "waiting_approval", "pending": {"tool": "Bash", "summary": "npm test"}}
    assert json.loads((env.bin / "decision-0").read_text()) == {"behavior": "allow"}
    assert env.cc.known(sid)["pending_approval"] is None
    assert [e["data"]["decision"] for e in store.events_after(env.db, sid) if e["kind"] == "approval"] == ["pending", "allow"]


@aio
async def test_deny_reaches_the_cli_with_a_message(env: Env) -> None:
    env.scenario({"asks": [["Bash", {"command": "rm -rf build"}]]})

    async def no(*_a: Any) -> bool:
        return False
    out = await env.call("claude_code_run", env.ctx(no), repo_path=str(env.repo), prompt="clean")
    await env.settle(out["id"])
    d = json.loads((env.bin / "decision-0").read_text())
    assert d["behavior"] == "deny" and "did not allow" in d["message"]


@aio
async def test_a_card_that_never_gets_an_answer_times_out_as_a_deny(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "PERMISSION_WAIT_S", 0.3)
    env.scenario({"asks": [["Write", {"file_path": "x"}]]})

    async def never(*_a: Any) -> bool:
        await asyncio.sleep(60)
        return True
    out = await env.call("claude_code_run", env.ctx(never), repo_path=str(env.repo), prompt="w")
    await env.settle(out["id"])
    d = json.loads((env.bin / "decision-0").read_text())
    assert d["behavior"] == "deny" and "timed out" in d["message"]
    assert env.cc.known(out["id"])["status"] == "done"


@aio
async def test_without_a_live_approval_closure_the_answer_is_a_deny_that_says_why(env: Env) -> None:
    env.scenario({"asks": [["Bash", {"command": "ls"}]]})
    ctx = env.ctx()
    del ctx["approve"]   # an unattended run has nobody to ask
    out = await env.call("claude_code_run", ctx, repo_path=str(env.repo), prompt="u")
    await env.settle(out["id"])
    d = json.loads((env.bin / "decision-0").read_text())
    assert d["behavior"] == "deny" and "no card to ask on" in d["message"] and not env.approvals


@aio
async def test_when_the_reply_ends_the_closure_is_dropped_and_a_later_send_rebinds_it(env: Env) -> None:
    env.scenario({"asks": [["Bash", {"command": "ls"}]]})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="r")
    await env.settle(out["id"])
    assert len(env.approvals) == 1
    await env.host.bus.emit("run.after", run_obj())
    assert env.cc.handles[out["id"]].approve is None
    assert env.cc.known(out["id"])["status"] == "done"   # a finished reply leaves its sessions working
    new = env.ctx()
    new["run_id"] = "run2"
    sent = await env.call("claude_code_send", new, id=out["id"], message="again")
    assert sent["status"] == "running"
    assert env.cc.handles[out["id"]].approve is not None and env.cc.handles[out["id"]].approve_run == "run2"


@aio
async def test_a_proposal_only_run_cannot_start_a_session(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(proposal_only=True), repo_path=str(env.repo), prompt="x")
    assert "unattended" in out["error"]
    out = await env.call("claude_code_fleet", env.ctx(proposal_only=True), tasks=[{"repo_path": str(env.repo), "prompt": "x"}])
    assert "unattended" in out["error"]


@aio
async def test_the_permission_route_refuses_a_wrong_key_a_missing_session_and_a_stopped_one_with_the_same_403(env: Env) -> None:
    import httpx
    env.scenario({"hang": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="h")
    sid = out["id"]
    key = env.cc.handles[sid].row["hook_key"]
    body = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    base = f"http://127.0.0.1:{env.port}/coding-hooks/claude-code"
    async with httpx.AsyncClient() as c:
        bad = [await c.post(f"{base}/{sid}/permission", json=body, headers=h) for h in ({"x-grain-hook-key": "nope"}, {})]
        bad.append(await c.post(f"{base}/nosuch/permission", json=body, headers={"x-grain-hook-key": key}))
        assert [r.status_code for r in bad] == [403, 403, 403] and len({r.text for r in bad}) == 1
        ok = await c.post(f"{base}/{sid}/permission", json=body, headers={"x-grain-hook-key": key})
        assert ok.status_code == 200 and ok.json() == {"behavior": "allow"}
        await env.cc.stop(sid, grace=0.5)
        late = await c.post(f"{base}/{sid}/permission", json=body, headers={"x-grain-hook-key": key})
        assert late.status_code == 403


def test_the_hook_script_prints_deny_on_every_failure_and_only_the_apps_allow(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert perm_hook.main(["--bridge", "http://127.0.0.1:1", "--session", "s"]) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PermissionRequest" and out["decision"]["behavior"] == "deny"
    event = json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "x"}})
    for answer in (b'{"behavior":"maybe"}', b"not json", b"{}"):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *_a: Any) -> None:
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setattr(sys, "stdin", io.StringIO(event))
        perm_hook.main(["--bridge", f"http://127.0.0.1:{srv.server_address[1]}", "--session", "s"])
        assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
        srv.shutdown()


def test_the_hook_script_gives_up_at_its_own_ceiling_with_a_deny(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import io
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(1.5)

        def log_message(self, *_a: Any) -> None:
            pass
    srv = HTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {}})))
    t = time.monotonic()
    perm_hook.main(["--bridge", f"http://127.0.0.1:{srv.server_address[1]}", "--session", "s", "--ceiling", "0.3"])
    assert time.monotonic() - t < 1.2 and json.loads(capsys.readouterr().out)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    srv.shutdown()
    assert perm_hook.HOOK_TIMEOUT_S > perm_hook.WAIT_CEILING_S > manager.PERMISSION_WAIT_S


# ---- send, stop, result, cost ----
@aio
async def test_send_steers_the_open_process_and_flips_the_session_back_to_running(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="one")
    sid = out["id"]
    assert (await env.settle(sid))["num_turns"] == 1
    sent = await env.call("claude_code_send", env.ctx(), id=sid, message="two")
    assert sent["status"] == "running"
    for _ in range(100):
        if env.cc.known(sid)["num_turns"] == 2:
            break
        await asyncio.sleep(0.1)
    row = env.cc.known(sid)
    assert row["status"] == "done" and row["num_turns"] == 2 and row["last_text"] == "echo: two"
    assert any(e["kind"] == "note" and e["text"] == "You: two" for e in store.events_after(env.db, sid))


@aio
async def test_send_errors_once_the_process_is_gone_and_while_queued(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="one")
    sid = out["id"]
    await env.settle(sid)
    await env.cc.handles[sid].proc.close_stdin()
    for _ in range(50):
        if not env.cc.handles[sid].proc.alive:
            break
        await asyncio.sleep(0.1)
    gone = await env.call("claude_code_send", env.ctx(), id=sid, message="late")
    assert "exited" in gone["error"]
    assert "No Claude Code session" in (await env.call("claude_code_send", env.ctx(), id="nope", message="x"))["error"]


@aio
async def test_the_idle_timeout_closes_stdin_after_the_result(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "IDLE_S", 0.3)
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="one")
    sid = out["id"]
    await env.settle(sid)
    for _ in range(100):
        if not env.cc.handles[sid].proc.alive:
            break
        await asyncio.sleep(0.1)
    assert not env.cc.handles[sid].proc.alive and env.cc.known(sid)["status"] == "done"


@aio
async def test_stop_interrupts_with_sigint_and_never_needs_a_kill_when_it_exits(env: Env) -> None:
    env.scenario({"hang": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="h")
    sid = out["id"]
    for _ in range(100):
        if env.cc.known(sid)["status"] == "running":
            break
        await asyncio.sleep(0.05)
    stopped = await env.call("claude_code_stop", env.ctx(), id=sid)
    assert stopped["status"] == "stopped" and (env.bin / "sigint").exists()
    assert not env.cc.handles[sid].proc.alive
    assert env.wakes == []   # a stop Grain made is not news


@aio
async def test_stop_escalates_to_sigkill_when_sigint_is_ignored(env: Env) -> None:
    env.scenario({"hang": True, "ignore_sigint": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="h")
    sid = out["id"]
    for _ in range(100):
        if env.cc.known(sid)["status"] == "running":
            break
        await asyncio.sleep(0.05)
    t = time.monotonic()
    await env.cc.stop(sid, grace=0.4)
    assert (env.bin / "sigint").exists() and time.monotonic() - t < 5
    assert not env.cc.handles[sid].proc.alive and env.cc.handles[sid].proc.proc.returncode == -9


@aio
async def test_a_process_that_dies_without_a_result_fails_the_session_with_its_stderr(env: Env) -> None:
    env.scenario({"crash": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="c")
    for _ in range(100):
        if env.cc.known(out["id"])["status"] == "failed":
            break
        await asyncio.sleep(0.05)
    row = env.cc.known(out["id"])
    assert row["status"] == "failed" and "code 3" in row["detail"] and "boom" in row["detail"]


@aio
async def test_a_failed_result_subtype_marks_the_session_failed_and_keeps_it_sendable(env: Env) -> None:
    env.scenario({"subtype": "error_max_turns"})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="m")
    row = await env.settle(out["id"])
    assert row["status"] == "failed" and row["result_subtype"] == "error_max_turns"
    assert env.cc.view(row)["can_send"] is True


@aio
async def test_result_reports_structured_output_denials_cost_and_the_diff(env: Env) -> None:
    env.scenario({"structured": {"ok": True}, "denials": [{"tool_name": "Bash", "tool_use_id": "t", "tool_input": {"command": "ls"}}], "cost_step": 0.25})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="s", json_schema={"type": "object"})
    row = await env.settle(out["id"])
    (Path(row["worktree"]) / "new.txt").write_text("hello\n")
    res = await env.call("claude_code_result", env.ctx(), id=out["id"])
    assert res["structured_output"] == {"ok": True} and res["result_subtype"] == "success" and res["num_turns"] == 1
    assert res["cost_usd"] == 0.25 and res["by_model"]["claude-test"]["cost_usd"] == 0.25
    assert res["permission_denials"][0]["tool_name"] == "Bash"
    assert "new.txt" in res["diff"]["new_files"] and res["final_text"] == "all done"
    assert flag(env.records()[0]["argv"], "--json-schema") == '{"type":"object"}'


@aio
async def test_cost_is_the_latest_result_never_a_sum_over_one_sessions_results(env: Env) -> None:
    env.scenario({"cost_step": 0.5})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="a")
    sid = out["id"]
    await env.settle(sid)
    await env.call("claude_code_send", env.ctx(), id=sid, message="b")
    for _ in range(100):
        if env.cc.known(sid)["num_turns"] == 2:
            break
        await asyncio.sleep(0.1)
    row = env.cc.known(sid)
    assert row["cost_usd"] == 1.0   # the second result says 1.0 in total; 0.5 + 1.0 would double count
    assert store.cost_rollup([row]) == (1.0, {"claude-test": {"cost_usd": 1.0, "input_tokens": 200, "output_tokens": 20}})


def test_fleet_cost_sums_each_sessions_latest_and_breaks_down_by_model() -> None:
    def r(cost: float, **usage: dict[str, Any]) -> dict[str, Any]:
        return {"cost_usd": cost, "result": {"model_usage": usage}}
    total, by = store.cost_rollup([r(1.0, a={"costUSD": 0.6, "inputTokens": 5}, b={"costUSD": 0.4}), r(0.5, a={"costUSD": 0.5, "outputTokens": 3}), {"cost_usd": 0, "result": None}])
    assert total == 1.5 and by == {"a": {"cost_usd": 1.1, "input_tokens": 5, "output_tokens": 3}, "b": {"cost_usd": 0.4, "input_tokens": 0, "output_tokens": 0}}


# ---- wake ----
@aio
async def test_a_settled_session_owes_its_chat_a_wake_and_a_waiting_call_suppresses_it(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="w")
    await env.settle(out["id"])   # claude_code_wait style: reports itself
    assert env.wakes == []
    env.scenario({})
    out2 = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="w2")
    for _ in range(150):
        if env.wakes:
            break
        await asyncio.sleep(0.1)
    cid, text, record = env.wakes[0]
    assert cid == "conv1" and out2["id"] in text and "not instructions" in text and "<worker_report" in text
    assert record["tainted"] is True and record["ids"] == []


# ---- fleets ----
@aio
async def test_a_fleet_runs_up_to_the_cap_queues_the_rest_and_starts_them_as_slots_free(env: Env) -> None:
    env.cfg["ext.claude-code.claudeCodeMaxConcurrent"] = 2
    env.scenario({"cost_step": 0.1, "delay": 1.0})
    tasks = [{"repo_path": str(env.repo), "prompt": f"task {i}", "name": f"t{i}"} for i in range(4)]
    out = await env.call("claude_code_fleet", env.ctx(), tasks=tasks)
    assert "error" not in out, out
    statuses = [s["status"] for s in out["sessions"]]
    assert statuses.count("queued") == 2 and "queued" in out["note"]
    ids = [s["id"] for s in out["sessions"]]
    waited = await env.call("claude_code_wait", env.ctx(), ids=ids, mode="all", timeout=40)
    assert not waited["timed_out"] and {s["status"] for s in waited["sessions"]} == {"done"}
    fleets = env.cc.fleets()
    assert fleets[0]["id"] == out["fleet_id"] and fleets[0]["counts"] == {"done": 4} and fleets[0]["cost_usd"] == 0.4
    assert fleets[0]["by_model"]["claude-test"]["cost_usd"] == 0.4 and fleets[0]["session_ids"] == ids
    assert len({r["worktree"] for r in env.cc.sessions()}) == 4   # each in its own worktree


@aio
async def test_max_parallel_bounds_a_fleet_and_a_wake_comes_once_when_all_settle(env: Env) -> None:
    env.scenario({"delay": 0.5})
    tasks = [{"repo_path": str(env.repo), "prompt": f"p{i}"} for i in range(3)]
    out = await env.call("claude_code_fleet", env.ctx(), tasks=tasks, max_parallel=1)
    assert [s["status"] for s in out["sessions"]].count("queued") == 2
    for _ in range(300):
        if env.wakes:
            break
        await asyncio.sleep(0.1)
    assert len(env.wakes) == 1 and all(s["id"] in env.wakes[0][1] for s in out["sessions"])
    await asyncio.sleep(0.5)
    assert len(env.wakes) == 1 and {s["status"] for s in env.cc.sessions()} == {"done"}


@aio
async def test_the_coding_extensions_live_sessions_count_toward_the_same_cap(env: Env) -> None:
    env.cfg["codingSessionMaxConcurrent"] = 1
    env.coding.sessions.live_count = lambda: 1   # type: ignore[method-assign]
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x")
    assert "codingSessionMaxConcurrent" in out["error"]
    fleet = await env.call("claude_code_fleet", env.ctx(), tasks=[{"repo_path": str(env.repo), "prompt": "x"}])
    assert "error" in fleet


@aio
async def test_run_refuses_past_its_own_cap_with_a_way_forward(env: Env) -> None:
    env.cfg["ext.claude-code.claudeCodeMaxConcurrent"] = 1
    env.scenario({"hang": True})
    first = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="a")
    assert "error" not in first
    second = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="b")
    assert "claudeCodeMaxConcurrent" in second["error"] and "claude_code_fleet" in second["try_instead"]
    assert (await env.call("claude_code_fleet", env.ctx(), tasks=[]))["error"].startswith("tasks")


@aio
async def test_a_bad_task_in_a_fleet_starts_nothing(env: Env) -> None:
    out = await env.call("claude_code_fleet", env.ctx(), tasks=[{"repo_path": str(env.repo), "prompt": "ok"}, {"repo_path": "/nonexistent", "prompt": "x"}])
    assert out["error"].startswith("tasks[1]") and env.cc.sessions() == []


# ---- worktrees ----
@aio
async def test_the_default_worktree_root_is_under_the_users_grain_folder_and_expands_tilde(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    del env.cfg["ext.claude-code.claudeCodeWorktreeRoot"]
    monkeypatch.setenv("HOME", str(env.tmp / "home"))
    assert env.cc.worktree_root() == Path(os.path.realpath(env.tmp / "home" / "Grain" / "worktrees"))
    env.cfg["ext.claude-code.claudeCodeWorktreeRoot"] = "~/wts"
    assert env.cc.worktree_root() == Path(os.path.realpath(env.tmp / "home" / "wts"))


@aio
async def test_a_root_inside_a_dot_claude_worktrees_folder_is_refused_before_anything_is_made(env: Env) -> None:
    for root in (str(env.tmp / ".claude" / "worktrees"), str(env.tmp / "x" / ".claude" / "worktrees" / "deeper")):
        env.cfg["ext.claude-code.claudeCodeWorktreeRoot"] = root
        out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x")
        assert ".claude/worktrees" in out["error"]
    assert env.cc.sessions() == [] and not (env.tmp / ".claude").exists()
    link = env.tmp / "link"
    (env.tmp / ".claude" / "worktrees").mkdir(parents=True)
    link.symlink_to(env.tmp / ".claude" / "worktrees")
    env.cfg["ext.claude-code.claudeCodeWorktreeRoot"] = str(link)
    assert ".claude/worktrees" in (await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x"))["error"]
    env.cfg["ext.claude-code.claudeCodeWorktreeRoot"] = "relative/dir"
    assert "absolute" in (await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x"))["error"]


@aio
async def test_repo_path_must_be_a_git_repo_and_branches_and_paths_do_not_collide(env: Env) -> None:
    plain = env.tmp / "plain"
    plain.mkdir()
    assert "not a git repository" in (await env.call("claude_code_run", env.ctx(), repo_path=str(plain), prompt="x"))["error"]
    assert "never works on main" in (await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="x", branch="main"))["error"]
    a = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="same task", name="same task")
    b = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="same task", name="same task")
    assert a["branch"] == "grain/same-task" and b["branch"] != a["branch"] and b["worktree"] != a["worktree"]
    taken = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="y", branch=a["branch"])
    assert "already exists" in taken["error"]
    env.scenario({"hang": True})
    in_place = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="z", new_worktree=False)
    assert "error" not in in_place and in_place["worktree"] == str(env.repo) and in_place["branch"] is None
    again = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="z2", new_worktree=False)
    assert "in use" in again["error"]


# ---- lifecycle and ownership ----
@aio
async def test_a_stopped_reply_stops_its_sessions_and_a_finished_one_does_not(env: Env) -> None:
    env.scenario({"hang": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="h")
    sid = out["id"]
    await env.host.bus.emit("run.after", run_obj())
    assert env.cc.handles[sid].proc.alive
    await env.host.bus.emit("run.after", run_obj(stopped=True))
    row = env.cc.known(sid)
    assert row["status"] == "stopped" and "reply that started it was stopped" in row["detail"]


@aio
async def test_a_stopped_subagent_takes_its_sessions_with_it(env: Env) -> None:
    env.scenario({"hang": True})
    out = await env.call("claude_code_run", env.ctx(agent_run_id="child1"), repo_path=str(env.repo), prompt="h")
    await env.host.bus.emit("subagent.stop", {"child_id": "child1", "role": "r", "parent": None, "exit_reason": "completed", "error": None, "detached": False})
    assert env.cc.handles[out["id"]].proc.alive
    await env.host.bus.emit("subagent.stop", {"child_id": "child1", "role": "r", "parent": None, "exit_reason": "stopped", "error": None, "detached": False})
    assert env.cc.known(out["id"])["status"] == "stopped"


@aio
async def test_app_stop_and_disabling_coding_stop_the_processes(env: Env) -> None:
    env.scenario({"hang": True})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="h")
    await env.host.bus.emit("ext.changed", {"id": "coding", "enabled": False})
    assert env.cc.known(out["id"])["status"] == "stopped" and not env.cc.handles[out["id"]].proc.alive


@aio
async def test_a_restart_closes_rows_still_marked_live(env: Env) -> None:
    now = time.time()
    store.insert(env.db, {"id": "old", "name": "n", "repo_path": "/r", "worktree": "/r", "status": "running", "prompt": "p", "hook_key": "k", "created_at": now,
                          "own_worktree": 0, "cost_usd": 0, "num_turns": 0, "last_text": ""})
    env.cc.recover()
    row = store.get(env.db, "old")
    assert row["status"] == "failed" and "app stopped" in row["detail"]


@aio
async def test_it_refuses_with_a_reason_while_the_coding_extension_is_off(env: Env) -> None:
    env.cfg["hiddenViews"] = ["coding"]
    for tool, args in (("claude_code_run", {"repo_path": str(env.repo), "prompt": "x"}), ("claude_code_status", {}), ("claude_code_wait", {"ids": ["x"]})):
        out = await env.call(tool, env.ctx(), **args)
        assert "Coding extension" in out["error"] and "Settings" in out["try_instead"], tool


@aio
async def test_the_coding_tool_post_hook_is_emitted_for_each_finished_tool_call(env: Env) -> None:
    seen: list[dict[str, Any]] = []

    async def watcher(ev: dict[str, Any]) -> None:
        seen.append(ev)
    env.host.bus.subscribe("coding.tool.post", watcher, owner="test", order=99)
    env.scenario({"asks": [["Bash", {"command": "ls -la"}]]})
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="p")
    await env.settle(out["id"])
    assert len(seen) == 1
    p = seen[0]
    assert p["agent"] == "claude" and p["tool"] == "Bash" and p["input"] == {"command": "ls -la"} and p["session_id"] == out["id"]
    assert p["call_id"] == "tu0" and p["conversation_id"] == "conv1" and p["result_preview"] == "allow"


# ---- the surface ----
@aio
async def test_the_ui_routes_list_sessions_events_fleets_and_take_send_and_stop(env: Env) -> None:
    import httpx
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="ui")
    sid = out["id"]
    await env.settle(sid)
    base = f"http://127.0.0.1:{env.port}/claude-code"
    async with httpx.AsyncClient() as c:
        sessions = (await c.get(f"{base}/sessions")).json()["sessions"]
        assert sessions[0]["id"] == sid and sessions[0]["status"] == "done" and sessions[0]["pending_approval"] is None
        evs = (await c.get(f"{base}/sessions/{sid}/events")).json()["events"]
        assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
        later = (await c.get(f"{base}/sessions/{sid}/events", params={"after": evs[1]["seq"]})).json()["events"]
        assert later[0]["seq"] == evs[1]["seq"] + 1
        sent = await c.post(f"{base}/sessions/{sid}/send", json={"message": "more"})
        assert sent.status_code == 200 and sent.json()["status"] == "running"
        assert (await c.get(f"{base}/fleets")).json() == {"fleets": []}
        assert (await c.get(f"{base}/sessions/nosuch")).status_code == 404
        stopped = await c.post(f"{base}/sessions/{sid}/stop")
        assert stopped.status_code == 200 and stopped.json()["status"] == "stopped"
        assert (await c.post(f"{base}/sessions/{sid}/send", json={"message": "x"})).status_code == 409


@aio
async def test_events_are_capped_per_session(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "EVENT_CAP", 5)
    for i in range(12):
        store.append_event(env.db, "s", "note", f"n{i}")
    evs = store.events_after(env.db, "s", 0, 100)
    assert [e["text"] for e in evs] == [f"n{i}" for i in range(7, 12)]


@aio
async def test_tools_are_registered_with_the_right_danger_and_the_card_name_is_hidden(env: Env) -> None:
    specs = env.tb.specs
    danger = {n: specs[n].danger for n in specs if n.startswith("claude_code_")}
    assert danger == {"claude_code_run": "external", "claude_code_fleet": "external", "claude_code_send": "external", "claude_code_stop": "executes",
                      "claude_code_status": "safe", "claude_code_result": "safe", "claude_code_wait": "safe", "claude_code_permission": "external"}
    assert all(specs[n].default == "ask" for n in ("claude_code_run", "claude_code_fleet", "claude_code_send"))
    assert specs["claude_code_permission"].available_fn() is False   # never offered to the model


@aio
async def test_bypass_permissions_is_a_hard_card_unless_allow_everything_chose_it(env: Env) -> None:
    run = env.tb.specs["claude_code_run"]
    assert run.force_card({"permission_mode": "bypassPermissions"}, {"permission_mode": "auto"}) is True
    assert run.force_card({"permission_mode": "bypassPermissions"}, {"permission_mode": "allow_all"}) is False
    assert run.force_card({"permission_mode": "acceptEdits"}, {"permission_mode": "auto"}) is False
    fleet = env.tb.specs["claude_code_fleet"]
    assert fleet.force_card({"tasks": [{"permission_mode": "bypassPermissions"}]}, {"permission_mode": "manual"}) is True
    # lifted only under Allow everything: no PermissionRequest hook is installed then
    env.cfg["permissionMode"] = "allow_all"
    out = await env.call("claude_code_run", env.ctx(permission_mode="allow_all"), repo_path=str(env.repo), prompt="x", permission_mode="bypassPermissions")
    await env.settle(out["id"])
    a = env.records()[0]["argv"]
    assert flag(a, "--permission-mode") == "bypassPermissions" and "PermissionRequest" not in json.loads(flag(a, "--settings")).get("hooks", {})


@aio
async def test_sending_is_a_card_again_when_the_run_read_other_untrusted_text(env: Env) -> None:
    out = await env.call("claude_code_run", env.ctx(), repo_path=str(env.repo), prompt="t")
    sid = out["id"]
    send = env.tb.specs["claude_code_send"]
    own = {"tainted": True, "taint_sources": [f"claude_code:run:{sid}", f"claude_code:wait:{sid}"]}
    foreign = {"tainted": True, "taint_sources": [f"claude_code:run:{sid}", "web"]}
    assert not send.force_ask({"id": sid}, own) and send.force_ask({"id": sid}, foreign) and not send.force_ask({"id": sid}, {})


def test_settings_are_declared_with_the_documented_defaults() -> None:
    from personal_os.extensions.manifest import load_manifest
    m = load_manifest(BUILTINS / "claude-code" / "extension.json")
    s = m.settings
    assert s["claudeCodeMaxConcurrent"] == {**s["claudeCodeMaxConcurrent"], "default": 3, "minimum": 1, "maximum": 16}
    assert s["claudeCodeDefaultPermissionMode"]["default"] == "default" and "bypassPermissions" not in s["claudeCodeDefaultPermissionMode"]["enum"]
    assert s["claudeCodeBudgetUsd"]["default"] == 0 and s["claudeCodeMaxTurns"]["default"] == 0
    assert s["claudeCodeDefaultModel"]["default"] == "" and s["claudeCodeWorktreeRoot"]["default"] == "~/Grain/worktrees"
    assert m.enabled is False and m.requires_extensions == ("coding",) and m.id == "claude-code"


def test_the_manifest_loads_and_the_authoring_check_finds_nothing_but_the_builtin_import_style() -> None:
    findings = check(BUILTINS / "claude-code")
    assert [str(f) for f in findings if f.code != "forbidden-import"] == []   # the same imports of the app's own modules every built-in makes
