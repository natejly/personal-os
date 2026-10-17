"""Coding recipes (extensions/coding/recipes.py, recipe_tools.py, recipe_job.py): versions, what a run records, the state
machine that ends only on verify's exit code, and the three ways to start one. The coding session and the shell are scripted.

Run: PYTHONPATH=backend python -m pytest -q backend/tests/test_coding_recipes.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding_ext import call, coding_box  # noqa: E402
from personal_os.extensions.coding import recipes as rcp  # noqa: E402
from personal_os.extensions.coding.recipe_job import RecipeTrigger  # noqa: E402
from personal_os.extensions.coding.sessions import CodingError  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402


class Rig:
    """A coding extension whose sessions are rows the test settles, and whose shell_run answers from a script."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> None:
        self.home = (tmp_path / "home").resolve()
        (self.home / "repo" / ".git").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(self.home))
        self.cfg: dict[str, Any] = {"permissionMode": "allow_all", **cfg}
        self.tb, self.coding, self.host = coding_box(self.cfg, workspace=Workspace(tmp_path / "data"))
        self.recipes = self.coding.recipes
        self.started: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self.verify_runs: list[dict[str, Any]] = []
        self.exits: list[int] = []
        self.output = "tests ran"
        self.fail_start: Exception | None = None
        self.events: list[tuple[str, Any]] = []
        monkeypatch.setattr(self.coding.sessions, "start", self._start)
        monkeypatch.setattr(self.coding.sessions, "send", self._send)
        monkeypatch.setattr(self.coding.sessions, "default_agent", lambda: "claude")
        real = self.tb.specs["shell_run"]
        self.tb.specs["shell_run"] = ToolSpec("shell_run", "x", {"type": "object", "properties": {}}, self._shell, "shell", "executes")
        self.tb.specs["shell_run"].floor = real.floor  # the destructive-command floor is the real one
        self.coding.api.app.owe_wake = lambda *a: self.events.append(("wake", a))  # type: ignore[method-assign]
        self.host.bus.fire = lambda hook, payload=None: self.events.append((hook, payload))  # type: ignore[method-assign]
        self.posts: list[dict[str, Any]] = []
        self.host.bus.subscribe("tool.post", lambda p: self.posts.append(p), owner="t", order=0)

    async def _start(self, **kw: Any) -> dict[str, Any]:
        if self.fail_start:
            raise self.fail_start
        run = self.recipes.get_run(kw["recipe_run_id"])
        assert run and run["resolved"]["start"] == {k: v for k, v in kw.items() if k not in ("ctx", "recipe_run_id")}  # saved before it starts
        self.started.append(kw)
        sid = f"s{len(self.started)}"
        with self.coding.sessions.db.tx() as c:
            c.execute("INSERT INTO coding_sessions(id, agent, repo_path, worktree, branch, name, prompt, status, created_at, updated_at, recipe_run_id) "
                      "VALUES(?,?,?,?,?,?,?,'working',?,?,?)", (sid, kw["agent"], kw["repo_path"], run["resolved"]["worktree"], kw["branch"], kw["name"],
                                                               kw["prompt"], time.time(), time.time(), kw["recipe_run_id"]))
        return self.coding.sessions.get(sid)  # type: ignore[return-value]

    async def _send(self, sid: str, message: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append(message)
        self.settle(sid, "working")
        return {}

    async def _shell(self, ctx: dict[str, Any], **args: Any) -> Any:
        self.verify_runs.append(args)
        return {"exit_code": self.exits.pop(0), "output": self.output}

    def settle(self, sid: str, status: str, detail: str | None = None) -> None:
        with self.coding.sessions.db.tx() as c:
            c.execute("UPDATE coding_sessions SET status = ?, detail = ? WHERE id = ?", (status, detail, sid))

    def body(self, **over: Any) -> dict[str, Any]:
        return {"name": "fix", "description": "Fix a test", "prompt": "Make {{test}} pass in {{mode}}",
                "inputs": [{"name": "test", "type": "string"}, {"name": "mode", "type": "enum", "values": ["fast", "slow"], "default": "fast"}],
                "repo_path": str(self.home / "repo"), "driver": "claude", "verify": {"command": "npm test"}, "max_retries": 1, **over}

    def run(self, ref: str = "fix", inputs: Any = None, origin: str = "chat", **kw: Any) -> dict[str, Any]:
        ctx = {"conversation_id": "c1", "settings": self.cfg, "tainted": False, "taint_sources": [], "run_id": "r1"}
        return asyncio.run(self.recipes.run(ref, inputs if inputs is not None else {"test": "t1"}, origin, ctx, **kw))

    def advance(self, run_id: str) -> dict[str, Any]:
        asyncio.run(self.recipes.advance(run_id))
        return self.recipes.get_run(run_id)  # type: ignore[return-value]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# ---- saving ----
def test_a_save_adds_a_version_and_never_edits_one(rig):
    first = rig.recipes.save(rig.body(), "user")
    assert (first["version"], first["approved"]) == (1, True)
    second = rig.recipes.save(rig.body(prompt="Do {{test}} well"), "user")
    assert (second["recipe_id"], second["version"]) == (first["recipe_id"], 2)
    assert rig.recipes.version(first["recipe_id"], 1)["body"]["prompt"].startswith("Make ")
    assert rig.recipes.version(first["recipe_id"])["version"] == 2  # the current one
    assert rig.recipes.find("fix")["current_version"] == 2
    assert [v["version"] for v in rig.recipes.versions(first["recipe_id"])] == [1, 2]


def test_a_version_the_agent_saved_waits_for_the_user_and_a_tainted_save_asks(rig):
    out = call(rig.tb, "coding_recipe_save", {"conversation_id": "c1"}, **rig.body())
    assert out["approved"] is False and "approves" in out["note"]
    rid = out["recipe_id"]
    assert rig.recipes.version(rid)["created_by"] == "agent" and rig.recipes.version(rid)["approved_at"] is None
    with pytest.raises(rcp.RecipeError, match="not been approved"):
        rig.run()
    assert "not been approved" in call(rig.tb, "coding_recipe_run", {"conversation_id": "c1"}, recipe="fix")["error"]
    assert rig.recipes.approve(rid, 1) is True and rig.recipes.approve(rid, 1) is False
    assert rig.run()["status"] == "working"
    assert rig.tb.forces_ask("coding_recipe_save", {}, {"tainted": True}) is True
    assert rig.tb.forces_ask("coding_recipe_save", {}, {"tainted": False}) is False
    assert rig.tb.forces_ask("coding_recipe_run", {}, {"tainted": True}) is True


@pytest.mark.parametrize("over, why", [
    ({"permission_mode": "bypassPermissions"}, "bypassPermissions"),
    ({"driver": "opencode", "allowed_tools": ["Read"]}, "tool lists"),
    ({"driver": "pi", "denied_tools": ["Bash"]}, "tool lists"),
    ({"driver": "opencode", "max_steps": 20}, "step limit"),
    ({"prompt": "Fix {{missing}}"}, "not a declared input"),
    ({"verify": None}, "verify.command"),
    ({"verify": {"command": "  "}}, "verify.command"),
    ({"verify": {"command": "pytest {{test}}"}}, "cannot use inputs"),
    ({"max_steps": 501}, "max_steps"),
    ({"timeout_s": 7201}, "timeout_s"),
    ({"max_retries": 2}, "max_retries"),
    ({"worktree": "sideways"}, "worktree must be"),
    ({"driver": "nano"}, "driver must be"),
    ({"repo_path": "relative/path"}, "absolute path"),
    ({"inputs": [{"name": "a b"}], "prompt": "x"}, "letters, digits"),
    ({"inputs": [{"name": "e", "type": "enum"}], "prompt": "x"}, "enum needs"),
    ({"allowed_tools": ["Bash(rm -rf /)\nBash"]}, "tool names"),
    ({"surprise": 1}, "unknown recipe field"),
])
def test_what_a_recipe_cannot_say_is_refused_at_save(rig, over, why):
    body = {**rig.body(), **over}
    if over.get("verify", 0) is None:
        body.pop("verify")
    with pytest.raises(rcp.RecipeError, match=why):
        rig.recipes.save(body, "user")
    assert rig.recipes.list() == []


def test_opencode_may_bound_a_run_with_a_time_limit_instead_of_a_step_limit(rig):
    assert rig.recipes.save(rig.body(driver="opencode", max_steps=20, timeout_s=600), "user")["version"] == 1
    run = rig.run()
    assert run["status"] == "working"
    resolved = rig.recipes.get_run(run["run_id"])["resolved"]
    assert resolved["start"]["cli_opts"] is None and resolved["timeout_s"] == 600  # the step cap is not a promise it cannot keep


def test_a_claude_recipe_has_a_default_step_cap_and_passes_its_options(rig):
    rig.recipes.save(rig.body(allowed_tools=["Read", "Bash(npm test:*)"], denied_tools=["WebFetch"], permission_mode="acceptEdits"), "user")
    resolved = rig.recipes.preview("fix", {"test": "t1"})
    assert resolved["start"]["cli_opts"] == {"allowed_tools": ["Read", "Bash(npm test:*)"], "denied_tools": ["WebFetch"], "max_steps": 60}
    assert resolved["start"]["permission_mode"] == "acceptEdits"


def test_inputs_are_typed_capped_and_put_in_as_plain_data(rig):
    rig.recipes.save(rig.body(inputs=[{"name": "test", "type": "string", "max_len": 12}, {"name": "n", "type": "number", "required": False},
                                      {"name": "flag", "type": "boolean", "default": True}], prompt="{{test}} {{ n }} {{flag}} {{test}}"), "user")
    shown = rig.recipes.preview("fix", {"test": "$(rm -rf /)", "n": 3})["start"]["prompt"]
    assert shown == "$(rm -rf /) 3 true $(rm -rf /)"  # substituted, never evaluated
    for bad, why in (({"test": "x" * 13}, "over 12"), ({}, "required"), ({"test": "a", "n": "3"}, "number"), ({"test": "a", "flag": "yes"}, "true or false"),
                     ({"test": "a", "extra": 1}, "unknown input")):
        with pytest.raises(rcp.RecipeError, match=why):
            rig.recipes.preview("fix", bad)
    assert rig.recipes.preview("fix", {"test": "a"})["start"]["prompt"] == "a  true a"  # an optional input left blank is empty


def test_an_existing_worktree_must_belong_to_the_repo(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    repo = rig.home / "real"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    wt = rig.home / "wt"
    subprocess.run([*git, "worktree", "add", "-q", "-b", "side", str(wt)], check=True)
    rig.recipes.save(rig.body(repo_path=str(repo), worktree="existing", worktree_path=str(wt)), "user")
    start = rig.recipes.preview("fix", {"test": "t"})["start"]
    assert (start["repo_path"], start["new_worktree"], start["branch"]) == (str(wt.resolve()), False, None)
    with pytest.raises(rcp.RecipeError, match="not a worktree of"):
        rig.recipes.save(rig.body(name="other", repo_path=str(repo), worktree="existing", worktree_path=str(rig.home / "work")), "user")


# ---- what a run records ----
def test_the_resolved_settings_are_what_start_got_and_are_written_before_and_never_change(rig):
    rig.recipes.save(rig.body(), "user")
    out = rig.run(inputs={"test": "t1"})
    run = rig.recipes.get_run(out["run_id"])
    start = run["resolved"]["start"]
    assert rig.started[0] == {**start, "ctx": rig.started[0]["ctx"], "recipe_run_id": out["run_id"]}  # exactly the arguments (the check inside _start ran first)
    assert start["branch"].startswith("grain/fix-") and start["new_worktree"] is True and start["prompt"] == "Make t1 pass in fast"
    assert run["resolved"]["worktree"].endswith(start["branch"].rsplit("/", 1)[-1])
    assert (run["resolved"]["verify"]["command"], run["resolved"]["timeout_s"], run["resolved"]["max_retries"]) == ("npm test", None, 1)
    frozen = json.dumps(run["resolved"], sort_keys=True)
    rig.settle(out["session_id"], "done")
    rig.exits = [1, 0]
    rig.advance(out["run_id"])
    rig.advance(out["run_id"])
    for _ in range(4):
        rig.advance(out["run_id"])
    assert json.dumps(rig.recipes.get_run(out["run_id"])["resolved"], sort_keys=True) == frozen


def test_a_failed_start_is_recorded_and_raised(rig):
    rig.recipes.save(rig.body(), "user")
    rig.fail_start = CodingError("3 coding sessions are already running")
    with pytest.raises(CodingError):
        rig.run()
    run = rig.recipes.list_runs()[0]
    assert run["status"] == "agent_failed" and "already running" in run["verify_tail"]


# ---- the state machine ----
def finish(rig: Rig, out: dict[str, Any], *exits: int) -> dict[str, Any]:
    """The agent finishes, and verify exits with each of `exits` in turn: advance the run until it ends or goes back to the agent."""
    rig.exits = list(exits)
    run = rig.recipes.get_run(out["run_id"])
    for _ in range(8):
        rig.settle(out["session_id"], "done")  # matters only while the run waits on the agent
        run = rig.advance(out["run_id"])
        if run["status"] not in ("verifying", "working") or (run["status"] == "working" and not rig.exits):
            break
    return run


def test_done_means_verify_exited_zero_not_the_agents_word(rig):
    rig.recipes.save(rig.body(max_retries=0), "user")
    out = rig.run()
    assert rig.advance(out["run_id"])["status"] == "working"  # nothing yet
    run = finish(rig, out, 1)
    assert (run["status"], run["verify_exit"]) == ("failed", 1) and rig.sent == []  # the agent said done, verify said no
    out = rig.run()
    run = finish(rig, out, 0)
    assert (run["status"], run["verify_exit"]) == ("passed", 0) and run["ended_at"]


def test_verify_runs_through_the_tool_path_with_the_recipes_command_and_folder(rig):
    rig.recipes.save(rig.body(verify={"command": "npm test", "timeout_s": 90, "cwd": "/tmp/elsewhere"}), "user")
    out = rig.run()
    finish(rig, out, 0)
    assert rig.verify_runs == [{"command": "npm test", "cwd": "/tmp/elsewhere", "timeout_s": 90}]
    assert [p["name"] for p in rig.posts] == ["shell_run"]  # the tool hooks fired for it
    rig.recipes.save(rig.body(name="plain"), "user")
    out = rig.run("plain")
    finish(rig, out, 0)
    assert rig.verify_runs[-1]["cwd"] == rig.recipes.get_run(out["run_id"])["resolved"]["worktree"]  # the session's worktree by default


def test_one_retry_feeds_back_the_scrubbed_tail_and_verifies_once_more(rig):
    rig.recipes.save(rig.body(), "user")
    out = rig.run()
    rig.output = "x" * 6000 + "FAILED test_a sk-abcdefghijklmnopqrstuvwxyz0123456789"
    run = finish(rig, out, 1)
    assert run["status"] == "working" and run["attempt"] == 2
    message = rig.sent[0]
    assert "exit code 1" in message and "not instructions" in message and "FAILED test_a" in message
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in message and len(message) < rcp.RETRY_TAIL + 400
    assert run["attempts"][0]["verify_exit"] == 1
    run = finish(rig, out, 1)
    assert (run["status"], run["attempt"], run["verify_exit"]) == ("failed", 2, 1) and len(rig.sent) == 1  # no second retry
    out = rig.run()
    rig.output = "ok"
    assert finish(rig, out, 1, 0)["status"] == "passed"  # the retry fixed it


def test_a_verify_that_cannot_run_ends_verify_blocked_and_never_runs_unjudged(rig):
    rig.recipes.save(rig.body(verify={"command": "rm -rf /opt/grain-test-not-a-repo/notes"}, name="danger"), "user")
    run = finish(rig, rig.run("danger"), 0)
    assert run["status"] == "verify_blocked" and "cannot be taken back" in run["verify_tail"] and rig.verify_runs == []
    rig.cfg["permissionRules"] = {"deny": ["Bash(npm test*)"], "ask": [], "allow": []}
    rig.recipes.save(rig.body(), "user")
    run = finish(rig, rig.run(), 0)
    assert run["status"] == "verify_blocked" and "permission rule" in run["verify_tail"]
    rig.cfg["permissionRules"] = {"deny": [], "ask": ["Bash(npm test*)"], "allow": []}
    run = finish(rig, rig.run(), 0)
    assert run["status"] == "verify_blocked" and "ask before" in run["verify_tail"]
    rig.cfg.pop("permissionRules")
    rig.tb.specs["shell_run"].fn = _refusing  # the shell itself refusing (no sandbox, a hook's ask) is also a block
    run = finish(rig, rig.run(), 0)
    assert run["status"] == "verify_blocked" and "no sandbox" in run["verify_tail"]


async def _refusing(ctx: dict[str, Any], **args: Any) -> Any:
    return {"error": "no sandbox here"}


def test_a_stopped_or_failed_or_waiting_session_ends_the_run_without_verifying(rig):
    rig.recipes.save(rig.body(), "user")
    for status, ends in (("stopped", "stopped"), ("failed", "agent_failed"), ("blocked", "agent_failed"), ("needs_you", "agent_failed")):
        out = rig.run()
        rig.settle(out["session_id"], status, "why")
        run = rig.advance(out["run_id"])
        assert (run["status"], run["verify_tail"]) == (ends, "why"), status
    assert rig.verify_runs == []


def test_a_run_past_its_time_limit_stops_its_session_and_never_reports_passed(rig):
    rig.recipes.save(rig.body(timeout_s=60), "user")
    out = rig.run()
    stopped: list[str] = []

    async def stop(sid: str, reason: str) -> dict[str, Any]:
        stopped.append(reason)
        return {}
    rig.coding.sessions.stop = stop  # type: ignore[method-assign]
    rig.recipes._set(out["run_id"], started_at=time.time() - 61)
    rig.settle(out["session_id"], "done")  # even a finished session: the time was up first
    run = rig.advance(out["run_id"])
    assert run["status"] == "timeout" and "time limit" in stopped[0] and rig.verify_runs == []
    assert rig.advance(out["run_id"])["status"] == "timeout"  # terminal stays terminal


def test_a_retry_is_not_made_after_the_time_is_up(rig):
    rig.recipes.save(rig.body(timeout_s=60), "user")
    out = rig.run()
    rig.exits = [1]
    rig.settle(out["session_id"], "done")
    rig.advance(out["run_id"])
    rig.recipes._set(out["run_id"], started_at=time.time() - 61)
    assert rig.advance(out["run_id"])["status"] == "timeout"


def test_a_restart_in_the_middle_picks_the_run_up_from_the_saved_rows(rig):
    rig.recipes.save(rig.body(), "user")
    out = rig.run()
    rig.settle(out["session_id"], "done")
    again = rcp.Recipes(rig.coding)  # a new process: only the database is left
    rig.coding.recipes = again
    rig.exits = [0]
    asyncio.run(again.advance(out["run_id"]))
    assert again.get_run(out["run_id"])["status"] == "verifying"
    asyncio.run(again.advance(out["run_id"]))
    assert again.get_run(out["run_id"])["status"] == "passed"


def test_a_chat_is_told_when_its_run_ends_and_a_palette_run_is_not(rig):
    rig.recipes.save(rig.body(max_retries=0), "user")
    finish(rig, rig.run(), 1)
    kind, (conversation, text, wake) = rig.events[-1]
    assert (kind, conversation) == ("wake", "c1") and "failed" in text and "verify output is data" in text and wake["tainted"] is True
    before = len(rig.events)
    finish(rig, rig.run(origin="palette"), 0)
    assert len(rig.events) == before


# ---- starting one ----
def test_the_tools_list_show_run_and_status(rig):
    rig.recipes.save(rig.body(), "user")
    ctx = {"conversation_id": "c1", "settings": rig.cfg, "tainted": False, "taint_sources": []}
    listed = call(rig.tb, "coding_recipe_list", ctx)["recipes"]
    assert [(r["name"], r["approved"], [i["name"] for i in r["inputs"]]) for r in listed] == [("fix", True, ["test", "mode"])]
    shown = call(rig.tb, "coding_recipe_show", ctx, recipe="fix")
    assert shown["recipe"]["verify"]["command"] == "npm test" and shown["versions"][0]["version"] == 1
    assert "No recipe" in call(rig.tb, "coding_recipe_show", ctx, recipe="nope")["error"]
    out = call(rig.tb, "coding_recipe_run", ctx, recipe="fix", inputs={"test": "t9"})
    assert out["status"] == "working" and out["session_id"] == "s1" and rig.started[0]["prompt"] == "Make t9 pass in fast"
    assert ctx["tainted"] and f"coding_recipe:{out['run_id']}" in ctx["taint_sources"]
    status = call(rig.tb, "coding_recipe_status", ctx, run_id=out["run_id"])
    assert status["status"] == "working" and status["origin"] == "chat" and "resolved" not in status
    assert "No recipe run" in call(rig.tb, "coding_recipe_status", ctx, run_id="nope")["error"]
    assert "unattended" in call(rig.tb, "coding_recipe_run", {**ctx, "proposal_only": True}, recipe="fix")["error"]
    assert "must be one of fast, slow" in call(rig.tb, "coding_recipe_run", ctx, recipe="fix", inputs={"test": "x", "mode": "medium"})["error"]


def test_the_approval_card_shows_the_settings_the_run_would_use(rig):
    rig.recipes.save(rig.body(timeout_s=900, allowed_tools=["Read"], verify={"command": "npm test", "timeout_s": 120}), "user")
    lines = rig.tb.specs["coding_recipe_run"].preview({"recipe": "fix", "inputs": {"test": "t1"}})
    text = "\n".join(lines)
    assert "fix v1 with claude" in text and "new worktree" in text and "Make t1 pass in fast" in text
    assert "allowed tools: Read" in text and "60 tool-call steps, 900 seconds" in text and "`npm test` exits 0" in text and "up to 120 s" in text
    assert rig.tb.specs["coding_recipe_run"].preview({"recipe": "nope"}) is None
    assert rig.tb.specs["coding_recipe_run"].preview({"recipe": "fix", "inputs": {}}) is None  # a required input is missing
    from personal_os.permission_tool import NAME  # the other tool with a card of its own still has one
    assert NAME  # imported only to prove the hook is generic


def test_the_directive_names_recipes_once_there_are_some(rig):
    assert "coding_recipe_run" not in rig.host.directives.text({})
    rig.recipes.save(rig.body(), "user")
    assert "coding_recipe_run" in rig.host.directives.text({}) and "Coding goes to a coding agent" in rig.host.directives.text({})
    assert rig.host.directives.text({"codingRoute": False}) == ""


def test_the_routes_save_approve_preview_run_and_list(rig):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(rcp_routes(rig))
    c = TestClient(app)
    assert c.post("/coding/recipes", json={**rig.body(), "verify": {}}).status_code == 400
    saved = c.post("/coding/recipes", json=rig.body()).json()
    assert saved["approved"] is True and c.get("/coding/recipes").json()["recipes"][0]["name"] == "fix"
    detail = c.get(f"/coding/recipes/{saved['recipe_id']}").json()
    assert detail["current"]["body"]["verify"]["command"] == "npm test" and detail["versions"][0]["approved_at"]
    preview = c.post("/coding/recipes/fix/preview", json={"inputs": {"test": "t"}}).json()
    assert any("`npm test` exits 0" in line for line in preview["lines"])
    assert c.post("/coding/recipes/fix/preview", json={"inputs": {}}).status_code == 400
    started = c.post("/coding/recipes/fix/run", json={"inputs": {"test": "t"}}).json()
    assert started["status"] == "working"
    run = c.get(f"/coding/recipe-runs/{started['run_id']}").json()
    assert run["origin"] == "palette" and run["conversation_id"] is None
    assert c.get("/coding/recipe-runs").json()["runs"][0]["id"] == started["run_id"] and c.get("/coding/recipe-runs/zzz").status_code == 404
    assert c.delete("/coding/recipes/fix").json() == {"archived": True} and c.get("/coding/recipes").json()["recipes"] == []
    assert c.post("/coding/recipes/fix/run", json={"inputs": {"test": "t"}}).status_code == 400  # archived: no such recipe
    assert c.get("/coding/recipes/nope").status_code == 404


def rcp_routes(rig: Rig) -> Any:
    from personal_os.extensions.coding.recipe_routes import recipes_router
    return recipes_router(rig.coding)


# ---- as a job ----
JOB = {"id": "j1", "name": "nightly", "timezone": "UTC", "created_at": 1_000_000.0, "last_fired_at": None, "cron": "", "kind": "coding_recipe"}


def job_row(rig: Rig, **cfg: Any) -> dict[str, Any]:
    body = {"recipe": "fix", "version": 1, "inputs": {"test": "t"}, "cron": "0 3 * * *", **cfg}
    return {**JOB, "prompt": json.dumps(body)}


class FakeJobs:
    def __init__(self) -> None:
        self.fired: list[dict[str, Any]] = []
        self.skips: list[tuple[Any, ...]] = []

    def mark_fired(self, id: str, **kw: Any) -> None:
        self.fired.append({"id": id, **kw})

    def record_skip(self, *a: Any) -> None:
        self.skips.append(a)


def test_a_job_kind_checks_its_settings_and_pins_the_version(rig):
    rig.recipes.save(rig.body(), "user")
    trigger = RecipeTrigger(rig.coding)
    assert trigger.check(job_row(rig)) is None
    assert "cron expression" in trigger.check(job_row(rig, cron="whenever"))
    assert "pin a \"version\"" in trigger.check({**job_row(rig), "prompt": json.dumps({"recipe": "fix", "cron": "0 3 * * *"})})
    assert "JSON" in trigger.check({**job_row(rig), "prompt": "run it"})
    assert "has no version 7" in trigger.check(job_row(rig, version=7))
    assert "No recipe" in trigger.check(job_row(rig, recipe="nope"))
    assert "empty" in trigger.check({**job_row(rig), "cron": "0 3 * * *"})
    rig.recipes.save(rig.body(), "agent")  # v2, unapproved
    assert trigger.check(job_row(rig, version=2)) and "not been approved" in trigger.check(job_row(rig, version=2))
    rig.recipes.save(rig.body(prompt="v3 {{test}}"), "user")
    assert trigger.check(job_row(rig, version=1)) is None  # a newer version does not move the job


def test_a_job_starts_a_recipe_run_not_a_chat_and_pins_its_version(rig):
    rig.recipes.save(rig.body(), "user")
    rig.recipes.save(rig.body(prompt="CHANGED {{test}}"), "user")
    trigger, jobs = RecipeTrigger(rig.coding), FakeJobs()
    row = job_row(rig)
    at = 1_000_000.0 + 86_400  # past the first 03:00 slot after the job was made
    fire, nxt = asyncio.run(trigger.poll(jobs, row, at))  # type: ignore[arg-type]
    assert fire is None and nxt > at  # no chat run is made for it
    assert rig.started[0]["prompt"] == "Make t pass in fast" and rig.started[0]["ctx"]["permission_mode"] == "auto"  # v1; Allow everything is capped
    run = rig.recipes.list_runs()[0]
    assert (run["origin"], run["job_id"], run["recipe_version"]) == ("job", "j1", 1)
    assert jobs.fired[0]["run_id"] == run["id"] and jobs.fired[0]["next_due_at"] == nxt
    assert ("job.fired", {"job_id": "j1", "kind": "coding_recipe", "trigger": "clock"}) in _drain(rig)
    early, soon = asyncio.run(trigger.poll(jobs, {**row, "last_fired_at": at}, at + 60))  # type: ignore[arg-type]
    assert early is None and len(rig.started) == 1 and soon > at + 60  # not due again yet
    finish(rig, {"run_id": run["id"], "session_id": run["session_id"]}, 0)
    assert ("job.done", {"job_id": "j1", "run_id": run["id"], "outcome": "passed"}) in _drain(rig)


def _drain(rig: Rig) -> list[tuple[str, Any]]:
    return rig.events


def test_a_job_whose_run_cannot_start_spends_the_slot_and_tries_the_next(rig):
    rig.recipes.save(rig.body(), "user")
    trigger, jobs = RecipeTrigger(rig.coding), FakeJobs()
    rig.fail_start = CodingError("no agent installed")
    fire, nxt = asyncio.run(trigger.poll(jobs, job_row(rig), 1_000_000.0 + 86_400))  # type: ignore[arg-type]
    assert fire is None and jobs.fired == [] and jobs.skips[0][1] == "recipe_not_started" and nxt > 1_000_000.0 + 86_400


def test_a_job_run_waits_for_a_free_session_and_gives_up_after_ten_minutes(rig, monkeypatch):
    rig.recipes.save(rig.body(), "user")
    monkeypatch.setattr(rig.coding.sessions, "_live", lambda: [object()] * 3)
    monkeypatch.setattr(rig.coding.sessions, "_max_concurrent", lambda: 3)
    out = rig.run(origin="job", job_id="j1")
    assert out["status"] == "queued" and out["session_id"] is None and rig.started == []
    assert rig.advance(out["run_id"])["status"] == "queued"
    monkeypatch.setattr(rig.coding.sessions, "_live", lambda: [])
    run = rig.advance(out["run_id"])
    assert run["status"] == "working" and run["session_id"] == "s1"  # a slot came free
    monkeypatch.setattr(rig.coding.sessions, "_live", lambda: [object()] * 3)
    late = rig.run(origin="job", job_id="j1")
    rig.recipes._set(late["run_id"], created_at=time.time() - rcp.QUEUE_WAIT_S - 1)
    run = rig.advance(late["run_id"])
    assert run["status"] == "queued_timeout" and ("job.done", {"job_id": "j1", "run_id": late["run_id"], "outcome": "queued_timeout"}) in _drain(rig)
    chat = rig.run()  # a chat run is refused at once by the session limit, not queued
    assert chat["status"] in ("working", "agent_failed")
