"""Terminals an agent drives (extensions/coding/terminals.py, terminal_tools.py): ownership, judging a typed line, what comes
back, waiting, limits and the bridge to the hub. The hub (Electron main) is replaced by a scripted FakeHub.

Run: PYTHONPATH=backend python -m pytest -q backend/tests/test_coding_terminals.py
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding_ext import FakeHub, call, coding_box, fake_terminals  # noqa: E402
from personal_os import logs, permrules, shell  # noqa: E402
from personal_os.extensions.coding import terminals as terms  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

CHAT = {"conversation_id": "c-chat"}


def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> tuple[Any, Any, FakeHub, Path, dict[str, Any]]:
    home = (tmp_path / "home").resolve()
    (home / "repo" / ".git").mkdir(parents=True)
    (home / "repo" / "src").mkdir()
    (home / "work").mkdir()
    monkeypatch.setenv("HOME", str(home))
    settings = {"permissionMode": "allow_all", **cfg}
    tb, coding, _ = coding_box(settings, workspace=Workspace(tmp_path / "data"))
    return tb, coding, fake_terminals(tb, coding, monkeypatch), home, settings


def ctx_of(settings: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {**CHAT, "settings": settings, "tainted": False, "taint_sources": [], **over}


def opened(tb: Any, ctx: dict[str, Any], cwd: str, **args: Any) -> str:
    out = call(tb, "terminal_open", ctx, cwd=cwd, **args)
    assert "terminal_id" in out, out
    return out["terminal_id"]


def test_open_builds_a_confined_login_shell_in_the_folder_and_records_the_row(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    out = call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"), name="dev server")
    assert re.fullmatch(r"tm_[0-9a-f]{16}", out["terminal_id"]) and out["cursor"] == 0 and out["sandboxed"] is True
    cmd = hub.opened[0]
    assert cmd["argv"][:3] == ["sandboxed", str(home / "work"), "--"] and cmd["argv"][-1] == "-l"  # the backend built it, the hub only runs it
    assert cmd["cwd"] == str(home / "work") and "NO_COLOR" not in cmd["env"]
    row = coding.terminals.row(out["terminal_id"])
    assert (row["state"], row["name"], row["user_attached"]) == ("open", "dev server", 0)


def test_a_sandbox_that_cannot_confine_refuses_the_terminal_and_leaves_no_scratch_dir(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    made: list[str] = []

    async def refuse(*a: Any, **k: Any) -> Any:
        raise shell.ShellError("terminal needs workspace-write confinement; the selected sandbox backend 'x/y' cannot enforce fs_write here (none). It was not run.")

    real = tempfile.mkdtemp
    monkeypatch.setattr(shell, "confine", refuse)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    out = call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"))
    assert "cannot enforce fs_write" in out["error"] and hub.opened == []
    assert made and not os.path.exists(made[0])


def test_a_terminal_in_a_session_worktree_belongs_to_that_session(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    wt = home / "repo" / ".claude" / "worktrees" / "feat"
    wt.mkdir(parents=True)
    with coding.sessions.db.tx() as c:
        c.execute("INSERT INTO coding_sessions(id, agent, repo_path, worktree, branch, name, prompt, status, created_at, updated_at) "
                  "VALUES('s1','claude',?,?,'grain/x','n','p','working',1,1)", (str(home / "repo"), str(wt)))
    sub = wt / "src"
    sub.mkdir()
    assert call(tb, "terminal_open", ctx_of(cfg), cwd=str(sub))["session_id"] == "s1"
    assert "session_id" not in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"))


def test_tools_are_offered_only_with_a_connected_desktop_app_and_a_sandbox(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    spec = tb.specs["terminal_open"]
    assert spec.available_fn() is True
    coding.terminals.bridge._seen = 0.0
    assert spec.available_fn() is False  # nobody has polled the bridge
    monkeypatch.setattr("personal_os.shell.sandbox_available", lambda: False)
    coding.terminals.bridge._seen = time.monotonic() + 1e9
    assert spec.available_fn() is False


# ---- ownership ----
@pytest.mark.parametrize("other", [
    {"conversation_id": "c-other"},                       # another chat
    {"conversation_id": "c-chat", "agent_run_id": "w1", "depth": 1},  # a subagent of this chat
    {"conversation_id": "c-chat", "workflow_run_id": "wf1"},
    {"conversation_id": "c-chat", "agent_id": "persona"},
])
def test_another_owner_gets_the_not_found_error_for_every_tool(tmp_path, monkeypatch, other):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg, agent_run_id="run-1"), str(home / "work"))
    stranger = ctx_of(cfg, **other)
    missing = call(tb, "terminal_read", ctx_of(cfg), terminal_id="tm_0000000000000000")["error"]
    assert missing.startswith("No such terminal")
    for name, args in (("terminal_read", {}), ("terminal_send", {"text": "ls"}), ("terminal_wait_for", {"timeout_s": 1, "literal": "x"}), ("terminal_close", {})):
        assert call(tb, name, stranger, terminal_id=tid, **args)["error"] == missing, name
    assert tid in hub.terms and not any(c["type"] in ("write", "close") for c in hub.calls)  # nothing reached the terminal
    assert "error" not in call(tb, "terminal_read", ctx_of(cfg, message_id="a-later-reply", agent_run_id="run-2"), terminal_id=tid)  # the same chat's later reply keeps it, whatever its run id


def test_limits_per_owner_and_overall(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ids = [opened(tb, ctx_of(cfg), str(home / "work")) for _ in range(terms.MAX_PER_OWNER)]
    assert "at most" in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"))["error"].lower()
    for n in range(2, 4):  # two more owners fill the agents' share of the hub
        for _ in range(terms.MAX_PER_OWNER):
            if coding.terminals._live() < terms.MAX_AGENT:
                opened(tb, ctx_of(cfg, conversation_id=f"c{n}"), str(home / "work"))
    assert coding.terminals._live() == terms.MAX_AGENT
    assert "error" in call(tb, "terminal_open", ctx_of(cfg, conversation_id="c9"), cwd=str(home / "work"))
    assert call(tb, "terminal_close", ctx_of(cfg), terminal_id=ids[0])["closed"] is True
    assert "terminal_id" in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"))  # a closed one frees its slot


def test_a_terminal_the_hub_lost_is_closed_and_frees_its_slot(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ids = [opened(tb, ctx_of(cfg), str(home / "work")) for _ in range(terms.MAX_PER_OWNER)]
    hub.terms.clear()  # the desktop app restarted: the hub has none of them
    assert call(tb, "terminal_read", ctx_of(cfg), terminal_id=ids[0])["error"].startswith("No such terminal")
    assert send(tb, cfg, ids[1], "ls")["error"].startswith("No such terminal")
    assert [t["id"] for t in asyncio.run(coding.terminals.live_status())] == []
    assert coding.terminals._live() == 0
    assert "terminal_id" in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"))


# ---- typing: each judging step, in order ----
def send(tb: Any, cfg: dict[str, Any], tid: str, text: str, **over: Any) -> Any:
    return call(tb, "terminal_send", ctx_of(cfg, **over), terminal_id=tid, text=text)


def test_a_line_is_typed_with_enter_and_the_cursor_comes_back(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert send(tb, cfg, tid, "echo hi") == {"ok": True, "cursor": 0}
    assert hub.terms[tid]["written"] == ["echo hi\r"]
    assert call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, key="ctrl-c")["ok"] is True
    assert hub.terms[tid]["written"][-1] == "\x03"


def test_keys_are_a_short_fixed_list_and_exactly_one_of_text_or_key(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert set(terms.KEYS) == {"ctrl-c", "ctrl-d", "escape", "up", "down", "left", "right", "tab"}  # no bare enter, backspace or ctrl-u/r/x
    assert "exactly one" in call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid)["error"]
    assert "exactly one" in call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, text="ls", key="tab")["error"]
    assert "key must be" in call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, key="enter")["error"]
    assert hub.terms[tid]["written"] == []


def test_empty_text_and_a_line_changed_by_a_key_are_refused_until_ctrl_c(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert "empty" in send(tb, cfg, tid, "")["error"]  # a bare Enter would run whatever the line editor holds
    assert call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, key="up")["ok"] is True
    assert "ctrl-c" in send(tb, cfg, tid, "ls")["error"]  # history recall changed the line, so it is no longer the judged text
    assert call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, key="ctrl-c")["ok"] is True
    assert send(tb, cfg, tid, "ls")["ok"] is True
    assert hub.terms[tid]["written"] == ["\x1b[A", "\x03", "ls\r"]


@pytest.mark.parametrize("line, why", [
    ("echo a\nrm -rf x", "one line"),
    ("echo \x1b[31mhi", "one line"),
    ("echo \x7f", "one line"),
    ("x" * 5000, "one line"),
    ("echo sk-abcdefghijklmnopqrstuvwxyz0123456789", "credential"),
    ("rm -rf /", "never allowed"),
])
def test_shape_secrets_and_the_hardline_are_refused_with_no_card(tmp_path, monkeypatch, line, why):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert why in send(tb, cfg, tid, line)["error"]
    assert hub.terms[tid]["written"] == []


def test_a_tab_is_allowed_and_a_registered_secret_value_is_refused(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert send(tb, cfg, tid, "echo\thi")["ok"] is True
    logs.register_secret("hunter2-the-secret")
    assert "credential" in send(tb, cfg, tid, "echo hunter2-the-secret")["error"]


def test_grains_own_folder_is_refused(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    from personal_os import mac
    own = str(mac._app_data_dir())
    assert "off limits" in send(tb, cfg, tid, f"cat {own}/personal-os.db")["error"]


def test_a_deny_rule_and_the_destructive_floor_apply_as_for_shell_run(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    rules = {"deny": ["Bash(git push *)"], "ask": ["Bash(npm publish*)"], "allow": ["Bash(make *)"]}
    args = {"terminal_id": tid, "text": "git push origin main"}
    assert permrules.resolve("terminal_send", args, "on", False, rules=rules).refusal == "blocked by your permission rule Bash(git push *)"
    assert permrules.resolve("terminal_send", {"terminal_id": tid, "text": "npm publish"}, "on", False, rules=rules).mode == "ask"
    assert permrules.resolve("terminal_send", {"terminal_id": tid, "text": "make test"}, "ask", False, rules=rules).mode == "on"
    # the open's command is the same subject
    assert permrules.resolve("terminal_open", {"command": "git push x"}, "on", False, rules=rules).refusal
    # a command that cannot be taken back is the shell floor: a card in Auto and Allow everything
    danger = {"terminal_id": tid, "text": "rm -rf /opt/grain-test-not-a-repo/notes"}  # temp folders (HOME here) count as scratch
    assert tb.floor_kind("terminal_send", danger, ctx_of(cfg)) == "shell"
    assert tb.floor_action("terminal_send", danger, ctx_of(cfg)) == "card"
    assert tb.floor_action("terminal_send", danger, ctx_of(cfg, permission_mode="auto")) == "card"
    assert tb.floor_kind("terminal_send", {"terminal_id": tid, "text": "ls"}, ctx_of(cfg)) is None


def test_coding_is_routed_to_a_session_inside_a_work_tree(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    inside = opened(tb, ctx_of(cfg), str(home / "repo"))
    outside = opened(tb, ctx_of(cfg), str(home / "work"))
    assert "coding_session_start" in send(tb, cfg, inside, "echo y > src/a.py")["error"]
    assert "coding_session_start" in send(tb, cfg, inside, "git commit -am x")["error"]
    for line in ("python3 script.py", "node build.js", "bash run.sh", "vim src/a.py", "less README.md", "cd src && python x.py"):
        assert "cannot be judged" in send(tb, cfg, inside, line)["error"], line
    for line in ("git status", "ls -la", "python3 -m pytest -q", "npm test", "git push origin x"):
        assert send(tb, cfg, inside, line)["ok"] is True, line
    for line in ("python3 script.py", "echo y > src/a.py", "vim notes.md"):
        assert send(tb, cfg, outside, line)["ok"] is True, line
    off = rig(tmp_path / "off", monkeypatch, codingRoute=False)
    tb2, hub2, home2, cfg2 = off[0], off[2], off[3], off[4]
    t2 = opened(tb2, ctx_of(cfg2), str(home2 / "repo"))
    assert send(tb2, cfg2, t2, "python3 script.py")["ok"] is True  # the setting is off: nothing is routed


def test_a_tainted_reply_that_can_reach_out_must_ask_and_trust_lifts_it(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch, shellNetwork=True)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    args = {"terminal_id": tid, "text": "ls"}
    assert tb.forces_ask("terminal_send", args, ctx_of(cfg, tainted=True)) is True
    assert tb.forces_ask("terminal_send", args, ctx_of(cfg)) is False
    assert tb.forces_ask("terminal_open", {"command": "ls"}, ctx_of(cfg, tainted=True)) is True
    lifted = {**cfg, "trustExternalContent": True}
    assert tb.forces_ask("terminal_send", args, ctx_of(lifted, tainted=True)) is False
    closed = {**cfg, "shellNetwork": False, "shellRegistryAccess": False, "shellAllowedDomains": []}
    assert tb.forces_ask("terminal_send", args, ctx_of(closed, tainted=True)) is False  # nothing could leave


def test_an_unattended_run_cannot_open_or_type(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    assert "unattended" in call(tb, "terminal_open", ctx_of(cfg, proposal_only=True))["error"]
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert "unattended" in send(tb, cfg, tid, "ls", proposal_only=True)["error"]


def test_the_open_command_is_judged_before_anything_is_spawned(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    assert "never allowed" in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"), command="rm -rf /")["error"]
    assert "coding_session_start" in call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "repo"), command="git commit -am x")["error"]
    assert hub.opened == []
    out = call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"), command="npm run dev")
    assert out["cursor"] == 0 and hub.terms[out["terminal_id"]]["written"] == ["npm run dev\r"]


def test_text_typed_without_enter_is_judged_with_what_follows_it(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, text="rm -rf ", submit=False)["ok"] is True
    assert "never runs" in send(tb, cfg, tid, "/")["error"]  # the whole line is what is judged


def test_the_send_rate_is_limited(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    for _ in range(terms.SENDS_PER_MIN):
        assert send(tb, cfg, tid, "ls")["ok"] is True
    assert "lines a minute" in send(tb, cfg, tid, "ls")["error"]


# ---- the user ----
def test_while_the_user_has_it_open_or_has_typed_the_agent_can_read_but_not_type(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.terms[tid]["attached"] = True
    read = call(tb, "terminal_read", ctx_of(cfg), terminal_id=tid)
    assert read["user_attached"] is True and "error" not in read
    assert "open in a window" in send(tb, cfg, tid, "ls")["error"]
    assert hub.terms[tid]["written"] == []
    assert [(t["user_attached"], t["user_dirty"]) for t in asyncio.run(coding.terminals.live_status())] == [(True, False)]
    hub.terms[tid].update(attached=False, dirty=True)
    call(tb, "terminal_read", ctx_of(cfg), terminal_id=tid)  # the hub's report reaches the row
    assert "typed into this terminal" in send(tb, cfg, tid, "ls")["error"]
    assert call(tb, "terminal_send", ctx_of(cfg), terminal_id=tid, key="ctrl-c")["ok"] is True  # clears it
    assert send(tb, cfg, tid, "ls")["ok"] is True


def test_a_race_with_the_user_is_caught_by_the_hub(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.terms[tid]["attached"] = True  # the row still says free: the hub refuses the write
    assert "using this terminal" in send(tb, cfg, tid, "ls")["error"]
    assert coding.terminals.row(tid)["user_attached"] == 1


# ---- what comes back ----
def test_output_is_stripped_scrubbed_cut_and_taints_the_run(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ctx = ctx_of(cfg)
    tid = opened(tb, ctx, str(home / "work"))
    logs.register_secret("topsecret-value-123")
    hub.feed(tid, "\x1b[31mred\x1b[0m \x1b]0;title\x07done\r\nprogress 10%\rprogress 100%\r\n"
                  "key sk-abcdefghijklmnopqrstuvwxyz0123456789 and topsecret-value-123\x00\x01\r\n")
    out = call(tb, "terminal_read", ctx, terminal_id=tid)
    assert out["text"] == "red done\nprogress 100%\nkey [token] and [redacted]\n"
    assert out["cursor"] > 0 and out["exited"] is False and out["dropped"] == 0
    assert f"terminal:{tid}" in ctx["taint_sources"] and ctx["tainted"]
    assert call(tb, "terminal_read", ctx, terminal_id=tid)["text"] == ""  # from where it last read


def test_a_long_output_is_cut_to_a_tail_and_says_how_much(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ctx = ctx_of(cfg)
    tid = opened(tb, ctx, str(home / "work"))
    hub.feed(tid, "a" * 40_000 + "TAIL")
    out = call(tb, "terminal_read", ctx, terminal_id=tid)
    assert len(out["text"]) == terms.READ_MAX and out["text"].endswith("TAIL") and out["dropped"] == 40_004 - terms.READ_MAX and out["truncated"]
    assert len(call(tb, "terminal_read", ctx, terminal_id=tid, cursor=0, max_bytes=100)["text"]) == 100  # the caller can ask for less


def test_screen_mode_reads_the_screen_not_the_stream(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid, "building...\r\n$ ")
    assert call(tb, "terminal_read", ctx_of(cfg), terminal_id=tid, mode="screen")["text"] == "$ "


def test_a_screen_read_leaves_the_unread_output_for_the_next_read(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ctx = ctx_of(cfg)
    tid = opened(tb, ctx, str(home / "work"))
    hub.feed(tid, "BUILD OK\r\n$ ")
    call(tb, "terminal_read", ctx, terminal_id=tid, mode="screen")
    assert "BUILD OK" in call(tb, "terminal_read", ctx, terminal_id=tid)["text"]


def test_an_exited_terminal_stays_readable_until_it_is_closed(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid, "bye\r\n")
    hub.exit(tid, 3)
    out = call(tb, "terminal_read", ctx_of(cfg), terminal_id=tid)
    assert (out["text"], out["exited"], out["exit_code"]) == ("bye\n", True, 3)
    assert "exited" in send(tb, cfg, tid, "ls")["error"]
    assert call(tb, "terminal_close", ctx_of(cfg), terminal_id=tid) == {"closed": True, "exit_code": 3}
    assert call(tb, "terminal_read", ctx_of(cfg), terminal_id=tid)["error"].startswith("No such terminal")


# ---- waiting ----
@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terms, "POLL_S", 0.02)
    monkeypatch.setattr(terms, "QUIET_PROMPT_S", 0.1)


def wait(tb: Any, cfg: dict[str, Any], tid: str, **args: Any) -> Any:
    return call(tb, "terminal_wait_for", ctx_of(cfg), terminal_id=tid, **args)


def test_wait_for_needs_a_timeout_in_range_and_one_matcher(tmp_path, monkeypatch, quick):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    assert "timeout_s" in wait(tb, cfg, tid, literal="x")["error"]  # missing: refused by the schema's bad-arguments error
    for bad in (0, 601, -1, True):
        assert "timeout_s is required" in wait(tb, cfg, tid, literal="x", timeout_s=bad)["error"]
    assert "not both" in wait(tb, cfg, tid, literal="x", pattern="y", timeout_s=1)["error"]
    assert "idle_ms" in wait(tb, cfg, tid, idle_ms=10, timeout_s=1)["error"]


def test_wait_for_keeps_only_a_bounded_tail_of_a_noisy_process(tmp_path, monkeypatch, quick):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid, "x" * (terms.MATCH_CAP * 2))
    out = wait(tb, cfg, tid, idle_ms=200, timeout_s=3)
    assert out["reason"] == "idle" and len(out["text"]) == terms.READ_MAX and out["dropped"] == terms.MATCH_CAP * 2 - terms.READ_MAX


def test_wait_for_reports_one_of_five_reasons(tmp_path, monkeypatch, quick):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid, "listening on :3000\r\n")
    out = wait(tb, cfg, tid, literal="listening", timeout_s=2)
    assert (out["reason"], out["match"]) == ("matched", "listening") and "listening on :3000" in out["text"]
    hub.exit(tid, 2)
    out = wait(tb, cfg, tid, literal="never", timeout_s=2, cursor=0)
    assert (out["reason"], out["exit_code"]) == ("exited", 2)
    tid2 = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid2, "compiling")
    out = wait(tb, cfg, tid2, idle_ms=200, timeout_s=3, cursor=0)
    assert out["reason"] == "idle" and "compiling" in out["text"]
    hub.feed(tid2, "\r\nContinue? [y/n] ")
    out = wait(tb, cfg, tid2, timeout_s=3)
    assert (out["reason"], out["confidence"]) == ("stdin_read", "heuristic")
    tid3 = opened(tb, ctx_of(cfg), str(home / "work"))
    hub.feed(tid3, "busy output without a prompt")
    started = time.monotonic()
    out = wait(tb, cfg, tid3, timeout_s=1)
    assert out["reason"] == "timeout" and time.monotonic() - started >= 1


def test_wait_for_regex_matches_new_output_only_and_scrubs_it(tmp_path, monkeypatch, quick):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    ctx = ctx_of(cfg)
    tid = opened(tb, ctx, str(home / "work"))
    hub.feed(tid, "ready on port 8080 with sk-abcdefghijklmnopqrstuvwxyz0123456789\r\nworking")
    out = call(tb, "terminal_wait_for", ctx, terminal_id=tid, pattern=r"port \d+", timeout_s=2)
    assert (out["reason"], out["match"]) == ("matched", "port 8080") and "sk-abc" not in out["text"]
    assert f"terminal:{tid}" in ctx["taint_sources"]
    again = call(tb, "terminal_wait_for", ctx, terminal_id=tid, pattern=r"port \d+", timeout_s=1)
    assert again["reason"] == "timeout"  # the cursor moved past it


def test_the_reason_follows_the_order_exited_matched_stdin_idle_timeout(tmp_path, monkeypatch, quick):
    """Property: whatever the script of output, exits and quiet, the reason is one of the five and the stronger one wins."""
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    rng = random.Random(7)
    for round_ in range(25):
        tid = opened(tb, ctx_of(cfg, conversation_id=f"c{round_}"), str(home / "work"))
        t = hub.terms[tid]
        matched, prompt, exited = rng.random() < .5, rng.random() < .5, rng.random() < .4
        hub.feed(tid, ("noise\r\n" if rng.random() < .5 else "") + ("MATCH\r\n" if matched else "") + ("Proceed? " if prompt else ""))
        if exited:
            hub.exit(tid, rng.randrange(0, 3))
        mine = ctx_of(cfg, conversation_id=f"c{round_}")
        out = call(tb, "terminal_wait_for", mine, terminal_id=tid, literal="MATCH", idle_ms=200, timeout_s=1, cursor=0)
        expected = "exited" if exited else "matched" if matched else "stdin_read" if prompt else None
        assert out["reason"] in ("matched", "exited", "idle", "stdin_read", "timeout")
        if expected:
            assert out["reason"] == expected, (matched, prompt, exited, out)
        else:
            assert out["reason"] in ("idle", "timeout")
        assert t is hub.terms[tid]
        call(tb, "terminal_close", mine, terminal_id=tid)


@pytest.mark.parametrize("pattern", [r"(a+)+b", r"(x*)*y", r"(a|a)*b", r"(a|ab)+c", r"(a{1,2})*b", r"(\w)\1", r"foo(?=bar)", r"(?<!a)b", "x" * 201, "(unclosed"])
def test_dangerous_or_broken_patterns_are_refused(pattern):
    with pytest.raises(terms.TerminalError):
        terms.safe_pattern(pattern)


@pytest.mark.parametrize("pattern", [r"ready", r"port \d+", r"(error|fail)ed?", r"^\$ $", r"[0-9]{1,3}\.[0-9]{1,3}", r"a+b+c*", r"(ab)+c", r"(?:error|fail)ed? at \d+"])
def test_ordinary_patterns_are_accepted(pattern):
    assert terms.safe_pattern(pattern).pattern == pattern


def test_the_number_of_waits_at_once_is_limited(tmp_path, monkeypatch, quick):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    coding.terminals.waiting[coding.terminals.owner_key(ctx_of(cfg))] = terms.WAITS_PER_OWNER
    assert "waits can run at once" in wait(tb, cfg, tid, literal="x", timeout_s=1)["error"]


# ---- closing ----
def test_closing_frees_the_scratch_folder_and_the_slot(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    tid = opened(tb, ctx_of(cfg), str(home / "work"))
    scratch = coding.terminals.scratch[tid][0]
    assert os.path.isdir(scratch)
    assert call(tb, "terminal_close", ctx_of(cfg), terminal_id=tid)["closed"] is True
    assert not os.path.exists(scratch) and coding.terminals.row(tid)["state"] == "closed" and tid not in hub.terms


def test_terminals_close_with_their_chat_their_child_their_desk_run_and_their_idle_time(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    chat = opened(tb, ctx_of(cfg, conversation_id="c-del"), str(home / "work"))
    child = opened(tb, ctx_of(cfg, agent_run_id="sa_child", depth=1), str(home / "work"))
    desk = opened(tb, ctx_of(cfg, conversation_id="c-desk", run_id="run-desk"), str(home / "work"))
    kept = opened(tb, ctx_of(cfg, conversation_id="c-kept", run_id="run-chat"), str(home / "work"))
    idle = opened(tb, ctx_of(cfg, conversation_id="c-idle"), str(home / "work"))

    async def go() -> None:
        await coding.chat_deleted({"id": "c-del"})
        await coding.child_ended({"child_id": "sa_child", "exit_reason": "completed", "error": None, "detached": False})
        stopped = type("R", (), {"stop": asyncio.Event(), "run_id": "run-desk", "desk_id": "d1", "kind": "chat"})()
        stopped.stop.set()
        await coding.run_ended(stopped)
        chat_run = type("R", (), {"stop": asyncio.Event(), "run_id": "run-chat", "desk_id": None, "kind": "chat"})()
        chat_run.stop.set()
        await coding.run_ended(chat_run)  # a stopped chat reply does not take its terminals with it
        with coding.terminals.db.tx() as c:
            c.execute("UPDATE coding_terminals SET last_used_at = ? WHERE id = ?", (time.time() - terms.IDLE_CLOSE_S - 5, idle))
        await coding.terminals.close_where("last_used_at < ?", time.time() - terms.IDLE_CLOSE_S)
    asyncio.run(go())
    state = {t: coding.terminals.row(t)["state"] for t in (chat, child, desk, kept, idle)}
    assert state == {chat: "closed", child: "closed", desk: "closed", kept: "open", idle: "closed"}


# ---- the bridge ----
def test_the_bridge_hands_a_command_to_the_poller_and_returns_its_answer():
    async def go() -> dict[str, Any]:
        bridge = terms.TerminalBridge()
        with pytest.raises(terms.TerminalError, match="desktop app"):
            await bridge.call({"type": "status", "terminal_id": "tm_x"})  # nobody polls yet
        async def poller() -> None:
            command = await bridge.next(hold=1)
            assert command and command["type"] == "status" and command["terminal_id"] == "tm_x"
            assert bridge.reply(command["id"], {"alive": True})
        task = asyncio.create_task(poller())
        await asyncio.sleep(0.05)
        assert bridge.connected
        out = await bridge.call({"type": "status", "terminal_id": "tm_x"}, timeout=2)
        await task
        return out
    assert asyncio.run(go()) == {"alive": True}


def test_the_bridge_raises_the_hubs_error_text_and_times_out():
    async def go() -> None:
        bridge = terms.TerminalBridge()
        async def poller() -> None:
            command = await bridge.next(hold=1)
            bridge.reply(command["id"], {"error": "No such terminal"})
        task = asyncio.create_task(poller())
        await asyncio.sleep(0.05)
        with pytest.raises(terms.TerminalError, match="No such terminal"):
            await bridge.call({"type": "close", "terminal_id": "tm_x"}, timeout=2)
        await task
        bridge._seen = time.monotonic() + 1e9
        with pytest.raises(terms.TerminalError, match="did not answer"):
            await bridge.call({"type": "close", "terminal_id": "tm_y"}, timeout=0.05)
        assert await bridge.next(hold=0.05) is None  # a command the caller gave up on is not delivered later
    asyncio.run(go())


def test_a_non_string_name_is_taken_as_text_and_leaves_no_terminal_behind(tmp_path, monkeypatch):
    tb, coding, hub, home, cfg = rig(tmp_path, monkeypatch)
    out = call(tb, "terminal_open", ctx_of(cfg), cwd=str(home / "work"), name=5)
    assert coding.terminals.row(out["terminal_id"])["name"] == "5" and len(hub.opened) == 1


def test_the_bridge_and_list_routes_need_the_app_token_and_a_live_extension():
    from fastapi.testclient import TestClient
    from personal_os.app import AUTH_TOKEN, app
    anonymous = TestClient(app)
    assert anonymous.post("/terminal-bridge/next").status_code == 401
    assert anonymous.post("/terminal-bridge/reply", json={"id": "x", "result": {}}).status_code == 401
    assert anonymous.get("/coding/terminals").status_code == 401
    authed = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    assert authed.get("/coding/terminals").json() == {"terminals": []}
    assert authed.post("/terminal-bridge/reply", json={"id": "nobody", "result": {}}).json() == {"ok": False}


def test_node_pty_is_imported_in_one_file_only():
    root = Path(__file__).resolve().parents[2] / "src"
    hits = [str(p.relative_to(root)) for p in root.rglob("*.ts*") if not p.name.endswith(".test.ts")
            and re.search(r"""(from|require\()\s*['"]node-pty['"]|import\(['"]node-pty['"]""", p.read_text("utf-8", "replace"))]
    assert hits == ["main/terminals.ts"]
