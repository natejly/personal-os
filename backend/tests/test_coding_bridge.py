"""The hook bridge: Claude Code's own tool calls in a session Grain started reach the hook bus, and the bus can only narrow.

claude_hook (the hook program), floor_settings (what a session is started with), the /coding-hooks routes, and the
subscriber rules. Real PreToolUse and PostToolUse events recorded from the CLI are in fixtures/claude_cli_events.json.

Run: PYTHONPATH=backend python -m pytest -q backend/tests/test_coding_bridge.py
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="bridge-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import claude_hook, permrules  # noqa: E402
from personal_os.extensions.coding import sessions as ca  # noqa: E402
from personal_os.extensions.coding.bridge import PAYLOAD_CAP, STRING_CAP  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "claude_cli_events.json").read_text())
PRE, POST = FIXTURE["pre"], FIXTURE["post"]


def bash(command: str, **over: Any) -> dict[str, Any]:
    return {**PRE, "tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/work/repo", **over}


class App:
    """A stand-in for the app's /coding-hooks routes: records what the hook sent and answers as told."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any], str]] = []
        self.answer: dict[str, Any] | bytes = {"verdict": None, "reason": ""}
        self.delay = 0.0
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                app.seen.append((self.path, body, self.headers.get("X-Grain-Hook-Key", "")))
                time.sleep(app.delay)
                data = app.answer if isinstance(app.answer, bytes) else json.dumps(app.answer).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def args(self, *extra: str) -> list[str]:
        return ["--bridge", f"http://127.0.0.1:{self.port}", "--session", "sess1", *extra]

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(claude_hook.KEY_ENV, "the-key")
    monkeypatch.setattr(claude_hook, "GATE_TIMEOUT_S", 1)
    a = App()
    yield a
    a.close()


def run_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], event: dict[str, Any] | str, *args: str) -> dict[str, Any] | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(event if isinstance(event, str) else json.dumps(event)))
    assert claude_hook.main(list(args)) == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else None


def decision(out: dict[str, Any] | None) -> str | None:
    return out["permissionDecision"] if out else None


# ---- what a session is started with ----
def test_without_a_bridge_the_settings_are_what_they_always_were():
    for worktree in (None, "/work/tree"):
        old_hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": claude_hook.command(sys.executable, worktree, _data_dir()),
                                                                      "timeout": 10}]}]}
        assert ca.floor_settings(worktree) == json.dumps({**permrules.claude_code_floor(), "hooks": old_hooks}, separators=(",", ":"))
        assert ca.floor_settings(worktree, False, None) == ca.floor_settings(worktree)
    lifted = json.dumps({"permissions": {"deny": permrules.claude_code_floor()["permissions"]["deny"]}}, separators=(",", ":"))
    assert ca.floor_settings("/w", True) == lifted and ca.floor_settings("/w", True, None) == lifted


def _data_dir() -> str | None:
    from personal_os import mac
    d = mac._app_data_dir()
    return str(d.resolve()) if d else None


def hooks_of(settings: str) -> dict[str, Any]:
    return json.loads(settings).get("hooks", {})


def test_the_matcher_widens_only_when_something_needs_every_tool_and_post_appears_only_when_someone_listens():
    base = {"port": 4321, "session_id": "s1", "key": "k", "counter": "/c"}
    plain = hooks_of(ca.floor_settings("/w", False, {**base, "gate": False, "post": False}))
    assert plain["PreToolUse"][0]["matcher"] == "Bash" and "PostToolUse" not in plain  # nothing subscribed, no cap: the default install pays nothing
    gated = hooks_of(ca.floor_settings("/w", False, {**base, "gate": True, "post": False}))
    assert gated["PreToolUse"][0]["matcher"] == "*" and "PostToolUse" not in gated
    assert "--gate" in gated["PreToolUse"][0]["hooks"][0]["command"] and "GRAIN_HOOK_KEY=k" in gated["PreToolUse"][0]["hooks"][0]["command"]
    capped = hooks_of(ca.floor_settings("/w", False, {**base, "gate": False, "post": False, "cap": 7}))
    assert capped["PreToolUse"][0]["matcher"] == "*" and "--cap 7 --counter /c" in capped["PreToolUse"][0]["hooks"][0]["command"]
    watched = hooks_of(ca.floor_settings("/w", False, {**base, "gate": False, "post": True}))
    assert watched["PreToolUse"][0]["matcher"] == "Bash" and watched["PostToolUse"][0]["matcher"] == "*"
    assert watched["PostToolUse"][0]["hooks"][0]["command"].endswith("|| true")  # a PostToolUse hook has nothing to ask
    assert "--bridge http://127.0.0.1:4321 --session s1" in watched["PostToolUse"][0]["hooks"][0]["command"]


def test_a_cap_alone_needs_no_app_address_and_no_key():
    command = hooks_of(ca.floor_settings(None, False, {"cap": 5, "counter": "/c", "gate": False, "post": False}))["PreToolUse"][0]["hooks"][0]["command"]
    assert "--cap 5 --counter /c" in command and "--bridge" not in command and "GRAIN_HOOK_KEY" not in command


def test_a_lifted_session_keeps_no_floor_hook_but_may_have_the_bridge():
    base = {"port": 1, "session_id": "s", "key": "k", "counter": "/c", "post": False}
    assert "hooks" not in json.loads(ca.floor_settings("/w", True, {**base, "gate": False}))  # nothing needs it
    on = hooks_of(ca.floor_settings("/w", True, {**base, "gate": True}))
    command = on["PreToolUse"][0]["hooks"][0]["command"]
    assert on["PreToolUse"][0]["matcher"] == "*" and "--lifted" in command and "--gate" in command
    assert "permissions" in json.loads(ca.floor_settings("/w", True, {**base, "gate": True})) and "ask" not in json.loads(ca.floor_settings("/w", True, {**base, "gate": True}))["permissions"]


def test_tool_lists_go_before_another_flag_never_before_the_prompt():
    argv = ca.claude_argv("/bin/claude", "n", "do it", "m", None, None, False, None, ["Read", "Bash(npm test:*)"], ["WebFetch"])
    assert argv[:8] == ["/bin/claude", "--bg", "-n", "n", "--allowedTools", "Read", "Bash(npm test:*)", "--disallowedTools"]
    assert argv[8:10] == ["WebFetch", "--model"] and argv[-1] == "do it"
    resume = ca.resume_argv("/bin/claude", "uuid", "more", None, None, False, None, ["Read"], None)
    assert resume[:6] == ["/bin/claude", "--bg", "--resume", "uuid", "--allowedTools", "Read"] and resume[6] == "--settings"
    assert ca.claude_argv("/bin/claude", "n", "p")[2:4] == ["-n", "n"] and "--allowedTools" not in ca.claude_argv("/bin/claude", "n", "p")


# ---- the hook program ----
def test_a_real_pretooluse_event_passes_untouched_without_flags(monkeypatch, capsys):
    assert run_hook(monkeypatch, capsys, PRE) is None  # a Read: nobody has an opinion
    assert decision(run_hook(monkeypatch, capsys, bash("rm -rf /"))) == "deny"
    assert decision(run_hook(monkeypatch, capsys, bash("git push --force origin main"))) in (None, "ask")  # Grain's own floor is unchanged


def test_the_step_cap_counts_every_tool_and_denies_past_the_limit(monkeypatch, capsys, tmp_path):
    counter = str(tmp_path / "steps")
    flags = ["--cap", "3", "--counter", counter]
    assert [decision(run_hook(monkeypatch, capsys, PRE, *flags)) for _ in range(5)] == [None, None, None, "deny", "deny"]
    assert "step limit" in run_hook(monkeypatch, capsys, PRE, *flags)["permissionDecisionReason"]
    assert Path(counter).stat().st_size == 6


def test_a_cap_or_gate_that_cannot_be_checked_does_not_fall_silent(monkeypatch, capsys, tmp_path):
    broken = str(tmp_path / "missing" / "steps")  # the counter file cannot be created
    assert decision(run_hook(monkeypatch, capsys, PRE, "--cap", "5", "--counter", broken)) == "ask"
    assert decision(run_hook(monkeypatch, capsys, PRE, "--gate", "--cap", "5", "--counter", broken)) == "ask"
    assert decision(run_hook(monkeypatch, capsys, PRE, "--cap", "5", "--counter", broken, "--lifted")) == "deny"
    assert decision(run_hook(monkeypatch, capsys, PRE, "--cap", "many", "--counter", str(tmp_path / "steps"))) == "ask"


def test_a_gate_verdict_and_the_local_floor_combine_strictest_first(monkeypatch, capsys, app):
    gate = app.args("--gate")
    app.answer = {"verdict": "ask", "reason": "an extension wants a look"}
    out = run_hook(monkeypatch, capsys, PRE, *gate)
    assert (out["permissionDecision"], out["permissionDecisionReason"]) == ("ask", "an extension wants a look")
    path, body, key = app.seen[-1]
    assert path == "/coding-hooks/sess1/pre" and key == "the-key"
    assert body["tool"] == "Read" and body["call_id"] == PRE["tool_use_id"] and body["permission_mode"] == "dontAsk" and body["lifted"] is False
    assert body["input"] == PRE["tool_input"] and body["cwd"] == PRE["cwd"]
    app.answer = {"verdict": "deny", "reason": "no"}
    assert decision(run_hook(monkeypatch, capsys, PRE, *gate)) == "deny"
    app.answer = {"verdict": None, "reason": ""}
    assert run_hook(monkeypatch, capsys, PRE, *gate) is None
    assert decision(run_hook(monkeypatch, capsys, bash("rm -rf /"), *gate)) == "deny"  # the local floor still speaks
    app.answer = {"verdict": "ask", "reason": "x"}
    assert decision(run_hook(monkeypatch, capsys, bash("rm -rf /"), *gate)) == "deny"  # and the stricter one wins
    assert decision(run_hook(monkeypatch, capsys, bash("rm -rf ~/x"), *gate)) == "ask"
    app.answer = {"verdict": "deny", "reason": "x"}
    assert decision(run_hook(monkeypatch, capsys, bash("rm -rf ~/x"), *gate)) == "deny"


def test_a_gate_that_does_not_answer_properly_asks_and_the_bridge_without_gate_is_silent(monkeypatch, capsys, app):
    gate = app.args("--gate")
    for junk in (b"not json", b"[]", b"", {"verdict": "allow"}, {"verdict": "yes"}, {"verdict": 5}, {"updatedInput": {"command": "ls"}}):
        app.answer = junk
        out = run_hook(monkeypatch, capsys, PRE, *gate)
        assert decision(out) in ("ask", None), junk  # never allow
        if isinstance(junk, dict) and junk.get("verdict") in ("allow", "yes", 5):
            assert decision(out) == "ask", junk
    app.answer = {"verdict": "deny", "reason": "x"}
    app.delay = 1.6  # slower than the hook's socket timeout
    out = run_hook(monkeypatch, capsys, PRE, *gate)
    assert out["permissionDecision"] == "ask" and "did not answer" in out["permissionDecisionReason"]
    app.delay = 0
    assert run_hook(monkeypatch, capsys, PRE, *app.args()) is None  # no --gate: the bridge is not asked, so it cannot block
    app.close()
    assert decision(run_hook(monkeypatch, capsys, PRE, *gate)) == "ask"  # --gate and the app gone: ask
    assert run_hook(monkeypatch, capsys, PRE, *app.args()) is None


def test_a_lifted_session_honors_a_deny_but_has_no_asks_and_no_local_floor(monkeypatch, capsys, app):
    gate = app.args("--gate", "--lifted")
    assert run_hook(monkeypatch, capsys, bash("rm -rf ~/x"), *gate) is None  # Allow everything: the delete hook stays dropped
    app.answer = {"verdict": "ask", "reason": "x"}
    assert run_hook(monkeypatch, capsys, PRE, *gate) is None
    app.answer = {"verdict": "deny", "reason": "no"}
    assert decision(run_hook(monkeypatch, capsys, PRE, *gate)) == "deny"
    assert app.seen[-1][1]["lifted"] is True
    app.close()
    assert run_hook(monkeypatch, capsys, PRE, *gate) is None  # a failed bridge does nothing there


def test_a_finished_call_is_reported_and_never_changes_anything(monkeypatch, capsys, app):
    assert run_hook(monkeypatch, capsys, POST, *app.args()) is None
    path, body, key = app.seen[-1]
    assert path == "/coding-hooks/sess1/post" and key == "the-key"
    assert body["tool"] == "Read" and body["duration_ms"] == POST["duration_ms"] and "hello" in body["result_preview"]
    app.close()
    assert run_hook(monkeypatch, capsys, POST, *app.args()) is None  # the app gone: still silent, exit 0
    assert run_hook(monkeypatch, capsys, POST) is None  # no bridge flags at all


def test_long_strings_are_cut_before_they_leave_the_hook(monkeypatch, capsys, app):
    run_hook(monkeypatch, capsys, bash("echo " + "x" * 5000), *app.args("--gate"))
    assert len(app.seen[-1][1]["input"]["command"]) == claude_hook.STRING_CAP == 2000


def test_the_hook_never_prints_allow_or_a_rewritten_input(monkeypatch, capsys, app):
    """Property: whatever the event, flags and answer, the output is nothing or a deny or an ask."""
    rng = random.Random(3)
    answers: list[Any] = [{"verdict": v, "reason": "r", **extra} for v in ("deny", "ask", None, "allow", "bypass", 3)
                          for extra in ({}, {"updatedInput": {"command": "ls"}}, {"permissionDecision": "allow"})] + [b"junk", b""]
    events = [PRE, POST, bash("ls"), bash("rm -rf ~/x"), bash("rm -rf /"), bash("git push -f"), "not json", "{}", "[1]", json.dumps({"tool_name": 7})]
    flag_sets = [[], ["--lifted"], ["--gate"], ["--gate", "--lifted"], ["--cap", "1", "--counter", "{counter}"], ["--gate", "--cap", "2", "--counter", "{counter}"]]
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(120):
            flags = [f.replace("{counter}", f"{tmp}/c{i}") for f in rng.choice(flag_sets)]
            app.answer = rng.choice(answers)
            event = rng.choice(events)
            monkeypatch.setattr(sys, "stdin", io.StringIO(event if isinstance(event, str) else json.dumps(event)))
            claude_hook.main([*app.args(), *flags] if rng.random() < .8 else flags)
            out = capsys.readouterr().out
            if out:
                parsed = json.loads(out)
                assert set(parsed) == {"hookSpecificOutput"} and set(parsed["hookSpecificOutput"]) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}
                assert parsed["hookSpecificOutput"]["permissionDecision"] in ("deny", "ask")


def test_the_recorded_cli_events_have_the_fields_the_hook_reads():
    assert {"session_id", "cwd", "permission_mode", "hook_event_name", "tool_name", "tool_input", "tool_use_id"} <= set(PRE)
    assert {"tool_response", "duration_ms"} <= set(POST) and POST["hook_event_name"] == "PostToolUse"
    assert claude_hook._event_body(PRE, False)["call_id"] == PRE["tool_use_id"]
    assert FIXTURE["result"]["structured_output"] == {"n": 7} and FIXTURE["result"]["subtype"] == "success"


# ---- the app's routes ----
@pytest.fixture(scope="module")
def routes() -> Any:
    from fastapi.testclient import TestClient
    from personal_os import app as appmod
    from personal_os.extensions.hooks import Ask, Deny
    local = TestClient(appmod.app, client=("127.0.0.1", 50000))  # no app token: the session's key is the only credential
    remote = TestClient(appmod.app, client=("10.0.0.5", 50000))
    return type("R", (), {"app": appmod, "local": local, "remote": remote, "Deny": Deny, "Ask": Ask})


def make_session(r: Any, sid: str, *, key: str | bytes | None = "the-session-key", status: str = "working", ended_at: float | None = None) -> None:
    now = time.time()
    with r.app.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO coding_sessions(id, agent, repo_path, worktree, branch, conversation_id, run_id, name, prompt, status, "
                  "created_at, updated_at, ended_at, hook_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sid, "claude", "/r", "/r/wt", "grain/x", "conv1", "run1", "n", "p", status, now, now, ended_at, key))


def hook_post(client: Any, sid: str, path: str, key: str | bytes | None = "the-session-key", **body: Any) -> Any:
    return client.post(f"/coding-hooks/{sid}/{path}", json={"tool": "Bash", "input": {"command": "ls"}, "call_id": "tu1", **body},
                       headers={"X-Grain-Hook-Key": key} if key is not None else {})


def subscribe(r: Any, hook: str, fn: Any) -> Any:
    return r.app.host.bus.subscribe(hook, fn, owner="probe", order=99)


def test_the_route_opens_for_the_sessions_key_only(routes):
    make_session(routes, "sess-live")
    ok = hook_post(routes.local, "sess-live", "pre")
    assert ok.status_code == 200 and ok.json() == {"verdict": None, "reason": ""}
    assert hook_post(routes.local, "sess-live", "pre", key="wrong").status_code == 403
    assert hook_post(routes.local, "sess-live", "pre", key=None).status_code == 403
    assert hook_post(routes.local, "sess-live", "pre", key="caf\u00e9".encode("latin-1")).status_code == 403  # not a 500 that tells a real id from a made-up one
    assert hook_post(routes.local, "no-such-session", "pre").status_code == 403  # the same answer: ids cannot be probed
    make_session(routes, "sess-nokey", key=None)
    assert hook_post(routes.local, "sess-nokey", "pre", key="").status_code == 403
    assert hook_post(routes.remote, "sess-live", "pre").status_code == 403  # loopback only
    other = routes.local.post("/coding-sessions/sess-live/stop")
    assert other.status_code == 401  # the key opens nothing else
    assert routes.local.get("/coding/recipes").status_code == 401 and routes.local.post("/terminal-bridge/next").status_code == 401


def test_a_session_that_ended_no_longer_opens_it_unless_it_is_being_resumed(routes):
    make_session(routes, "sess-done", status="done", ended_at=time.time() - ca.RESUME_GRACE - 5)
    assert hook_post(routes.local, "sess-done", "pre").status_code == 403
    make_session(routes, "sess-resuming", status="done", ended_at=time.time() - 1)
    assert hook_post(routes.local, "sess-resuming", "pre").status_code == 200  # a follow-up starts before the row says working
    make_session(routes, "sess-wait", status="needs_you")
    assert hook_post(routes.local, "sess-wait", "post").status_code == 200


def test_a_gate_subscriber_answers_deny_or_ask_and_nothing_else_gets_through(routes, monkeypatch):
    from personal_os.extensions import hooks
    monkeypatch.setattr(hooks, "STRICT", False)  # the bad returns below are the point; the strict bus would raise on them
    make_session(routes, "sess-gate")
    seen: list[dict[str, Any]] = []
    answers: list[Any] = []
    sub = subscribe(routes, "coding.tool.pre", lambda p: (seen.append(p), answers[0])[1])
    try:
        for given, verdict in ((routes.Deny("no thanks"), "deny"), (routes.Ask("look first"), "ask")):
            answers[:] = [given]
            out = hook_post(routes.local, "sess-gate", "pre").json()
            assert out == {"verdict": verdict, "reason": given.reason}
        for bad in ("allow", True, {"decision": "allow"}, 42, routes.Deny):
            answers[:] = [bad]
            assert hook_post(routes.local, "sess-gate", "pre").json()["verdict"] == "ask"  # a bad return fails toward asking
        answers[:] = [None]
        assert hook_post(routes.local, "sess-gate", "pre").json()["verdict"] is None
    finally:
        sub.dispose()
    p = seen[0]
    assert (p["session_id"], p["agent"], p["tool"], p["worktree"], p["conversation_id"], p["run_id"], p["call_id"]) == \
        ("sess-gate", "claude", "Bash", "/r/wt", "conv1", "run1", "tu1")
    assert set(p) == {"session_id", "agent", "tool", "input", "cwd", "worktree", "permission_mode", "lifted", "call_id", "conversation_id", "run_id"}


def test_a_gate_that_raises_or_hangs_asks(routes, monkeypatch):
    make_session(routes, "sess-bad")

    def boom(_p: Any) -> None:
        raise RuntimeError("bug")
    sub = subscribe(routes, "coding.tool.pre", boom)
    try:
        out = hook_post(routes.local, "sess-bad", "pre").json()
        assert out["verdict"] == "ask" and "did not answer" in out["reason"]
    finally:
        sub.dispose()
    from personal_os.extensions import hooks
    monkeypatch.setitem(hooks.TIMEOUT_S, "gate", 0.1)
    sub = subscribe(routes, "coding.tool.pre", lambda _p: time.sleep(1))
    try:
        assert hook_post(routes.local, "sess-bad", "pre").json()["verdict"] == "ask"
    finally:
        sub.dispose()


def test_the_payload_is_scrubbed_and_capped(routes):
    make_session(routes, "sess-cap")
    seen: list[dict[str, Any]] = []
    sub = subscribe(routes, "coding.tool.pre", lambda p: seen.append(p))
    try:
        hook_post(routes.local, "sess-cap", "pre", input={"command": "curl -H 'x: sk-abcdefghijklmnopqrstuvwxyz0123456789' " + "a" * 9000, "file_path": "/f"})
        hook_post(routes.local, "sess-cap", "pre", input={f"k{i}": "v" * 3000 for i in range(40)})
    finally:
        sub.dispose()
    first, second = seen
    assert "sk-abcdef" not in json.dumps(first) and len(first["input"]["command"]) <= STRING_CAP
    assert len(json.dumps(second)) <= PAYLOAD_CAP  # forty big strings are cut shorter until the whole fits


def test_a_finished_call_is_emitted_once_and_added_to_the_feed_unless_the_timeline_has_it(routes):
    make_session(routes, "sess-post")
    seen: list[dict[str, Any]] = []
    sub = subscribe(routes, "coding.tool.post", lambda p: seen.append(p))
    try:
        r = hook_post(routes.local, "sess-post", "post", input={"command": "npm test"}, result_preview="ok sk-abcdefghijklmnopqrstuvwxyz0123456789", duration_ms=12)
        assert r.status_code == 200 and r.json() == {}
        hook_post(routes.local, "sess-post", "post", input={"command": "npm test"}, call_id="tu1")  # the same call reported twice
        with routes.app.db.tx() as c:
            c.execute("INSERT INTO coding_session_events(session_id, seq, ts, kind, text, tool) VALUES('sess-post', 9, 't', 'tool', 'Running ls -la', 'step')")
        hook_post(routes.local, "sess-post", "post", input={"command": "ls -la"}, call_id="tu2")  # the timeline already saw this one
    finally:
        sub.dispose()
    assert len(seen) == 3 and "sk-abcdef" not in seen[0]["result_preview"] and seen[0]["duration_ms"] == 12
    from personal_os.extensions.coding import events as feed
    events = [e for e in feed.list_after(routes.app.db, "sess-post") if (e["data"] or {}).get("hook")]
    assert [e["text"] for e in events] == ["npm test"] and events[0]["tool"] == "Bash"


def test_a_call_without_an_id_is_added_even_when_earlier_events_have_none(routes):
    make_session(routes, "sess-noid")
    with routes.app.db.tx() as c:
        c.execute("INSERT INTO coding_session_events(session_id, seq, ts, kind, text) VALUES('sess-noid', 1, 't', 'user', 'run the build')")
    hook_post(routes.local, "sess-noid", "post", input={"command": "make build"}, call_id=None)
    from personal_os.extensions.coding import events as feed
    assert [e["text"] for e in feed.list_after(routes.app.db, "sess-noid") if (e["data"] or {}).get("hook")] == ["make build"]


def test_subscribing_needs_the_permissions(routes):
    from personal_os.extensions.hooks import HookBus
    from personal_os.extensions.host import ExtensionHost
    from personal_os.extensions.manifest import Manifest, ManifestError
    host = ExtensionHost()

    def scope(perms: list[str], hook: str = "coding.tool.pre") -> Any:
        manifest = Manifest.from_dict({"id": f"probe{len(host.extensions)}", "name": "p", "version": "1.0.0", "apiVersion": 1, "permissions": perms,
                                       "hooks": {hook: {}}})
        return host._add(manifest)
    for missing in ([], ["hooks:gate"], ["coding:read"]):
        with pytest.raises(ManifestError, match="needs the permission"):
            scope(missing)
    scope(["hooks:gate", "coding:read"]).on("coding.tool.pre", lambda p: None)
    with pytest.raises(ManifestError, match="coding:read"):
        scope([], "coding.tool.post")
    scope(["coding:read"], "coding.tool.post").on("coding.tool.post", lambda p: None)
    assert isinstance(host.bus, HookBus)


def test_the_route_adds_little_latency_with_no_subscriber(routes):
    make_session(routes, "sess-fast")
    times = []
    for _ in range(60):
        t = time.perf_counter()
        assert hook_post(routes.local, "sess-fast", "pre").status_code == 200
        times.append(time.perf_counter() - t)
    times.sort()
    assert times[int(len(times) * .95)] < 0.15  # p95 under the 150 ms the design allows
