"""Coding as a built-in extension: it keeps every tool and route it had, the repo-write refusal holds at every door and fails
closed, and turning the extension off takes its surface with it.

Run: PYTHONPATH=backend python -m pytest -q backend/tests/test_coding_extension.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="codingext-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding_ext import call, coding_box, coding_scope  # noqa: E402
from personal_os import backups, dispatch, migrations, permissions  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions.coding import extension as ext  # noqa: E402
from personal_os.extensions import hooks  # noqa: E402
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "personal_os"
GOLDEN = Path(__file__).parent / "golden" / "tools.txt"
NEW_TOOLS = ["terminal_open", "terminal_send", "terminal_read", "terminal_wait_for", "terminal_close", "coding_recipe_list", "coding_recipe_show",
             "coding_recipe_save", "coding_recipe_run", "coding_recipe_status", "coding_run_structured"]
SESSION_TOOLS = ["coding_session_start", "coding_session_list", "coding_session_status", "coding_session_wait", "coding_session_summary",
                 "coding_session_send", "coding_session_stop", "coding_session_diff"]


def _digest(spec: ToolSpec) -> str:
    blob = json.dumps([spec.description, spec.parameters, spec.group, spec.danger, spec.default, spec.examples, spec.taints], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---- nothing moved, nothing renamed ----
def test_the_tool_list_is_byte_for_byte_what_the_parent_offered_with_the_new_tools_last():
    from personal_os.app import toolbox
    golden = [line.split() for line in GOLDEN.read_text().splitlines()]
    actual = [(n, _digest(s)) for n, s in toolbox.specs.items()]
    assert [list(a) for a in actual[:len(golden)]] == golden  # names, order, descriptions, schemas, danger, group and default mode
    names = [n for n, _ in actual]
    assert names.index("opencode_run") == names.index("shell_kill") + 1  # mid-list, where the shell tools end
    assert names[len(golden) - len(SESSION_TOOLS):len(golden)] == SESSION_TOOLS  # the eight coding_session_* tools last
    assert names[len(golden):] == NEW_TOOLS  # what this step adds comes after, in the manifest's order
    assert all(toolbox.specs[n].group == "shell" for n in [*SESSION_TOOLS, "opencode_run", *NEW_TOOLS])


def test_the_extension_owns_its_tools_routes_tables_and_service():
    from personal_os.app import host, toolbox
    scope = host.extensions["coding"]
    assert scope.manifest.enabled is True and scope.manifest.provides_services == ("coding",)
    declared = [t["name"] for t in scope.manifest.contributes["tools"]]
    assert set(declared) == {"opencode_run", *SESSION_TOOLS, *NEW_TOOLS} and all(toolbox.tool_owner[n] == "coding" for n in declared)
    assert set(scope.manifest.data["tables"]) == {"coding_sessions", "coding_session_events", "coding_recipes", "coding_recipe_versions",
                                                  "coding_recipe_runs", "coding_terminals"}
    assert set(scope.manifest.permissions) == {"db:own", "hooks:gate", "terminal:control"}
    assert {"tool.pre", "tool.result", "tool.error", "tool.post", "run.after", "subagent.stop", "app.start"} <= set(scope.manifest.hooks)


def test_no_module_outside_the_package_names_the_old_ones():
    old = re.compile(r"coding_route|codingagents|coding_events")
    leaks = []
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("extensions/coding/") or "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            continue
        leaks += [f"{rel}:{n}" for n, line in enumerate(path.read_text("utf-8").splitlines(), 1) if old.search(line)]
    assert leaks == []
    for gone in ("codingagents", "coding_events", "coding_route", "opencode", "opencode_mcp", "opencode_usage", "pi"):
        assert not (PACKAGE / f"{gone}.py").exists(), gone
    app = (PACKAGE / "app.py").read_text()
    assert not re.search(r'@app\.(get|post|put|delete)\("/coding-sessions', app)
    assert not re.search(r"_coding_(owed|waiting|finished|run|row|do|watch)|_wake_coding|_schedule_coding", app)  # the generic owe_wake is all that is left


def test_the_catalogs_opencode_server_points_at_the_moved_module():
    catalog = (PACKAGE / "data" / "mcp_catalog.json").read_text()
    assert "personal_os.extensions.coding.opencode_mcp" in catalog and "personal_os.opencode_mcp" not in catalog
    import importlib.util
    assert importlib.util.find_spec("personal_os.extensions.coding.opencode_mcp")


# ---- the refusal, at every door ----
CTXS = {
    "chat": {"conversation_id": "c-chat"},
    "desk": {"conversation_id": "c-desk", "desk_id": "d1"},
    "worker": {"conversation_id": "c-chat", "kind": "worker", "agent_run_id": "w1"},
    "workflow step": {"conversation_id": "c-chat", "workflow_run_id": "wf1", "agent_run_id": "wf1"},
}


@pytest.fixture
def repo_rig(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    (home / "repo" / ".git").mkdir(parents=True)
    (home / "repo" / "src").mkdir()
    target = home / "repo" / "src" / "a.py"
    target.write_text("x = 1\n")
    monkeypatch.setenv("HOME", str(home))
    cfg = {"permissionMode": "allow_all"}
    tb, coding, host = coding_box(cfg, workspace=Workspace(tmp_path / "data"))
    return SimpleNamespace(tb=tb, coding=coding, host=host, home=home, target=target, cfg=cfg)


@pytest.mark.parametrize("who", list(CTXS))
def test_a_repo_edit_is_refused_before_a_card_and_again_at_the_gate_if_the_precheck_were_skipped(repo_rig, who):
    r = repo_rig
    ctx = {**CTXS[who], "settings": r.cfg}
    args = {"path": str(r.target), "old": "x = 1", "new": "x = 2"}
    refused = r.tb.precheck("fs_edit", dict(args), ctx)  # what every loop asks before it opens a card
    assert refused and "coding_session_start" in refused["error"] and r.tb.prechecks.refusal("fs_edit", dict(args), ctx)
    assert r.tb.precheck("fs_edit", dict(args)) is None  # without a context there is nothing to judge
    out = call(r.tb, "fs_edit", ctx, **args)  # ... and with the precheck bypassed, the gate below the approval wait says the same
    assert "blocked by an extension" in out["error"] and "coding_session_start" in out["error"]
    assert r.target.read_text() == "x = 1\n"


def test_the_subagent_loop_asks_the_same_door(repo_rig):
    r = repo_rig
    from personal_os.subagents import Subagents
    source = (PACKAGE / "subagents.py").read_text()
    assert "self.toolbox.prechecks.refusal(name, args, ch.ctx)" in source and "coding_route" not in source
    assert Subagents.__init__.__code__.co_varnames.count("directives") == 1
    assert r.tb.prechecks.refusal("shell_run", {"command": "echo y > src/b.py", "cwd": str(r.home / "repo")}, {"conversation_id": "w"})


def test_a_script_that_writes_into_a_repo_is_reverted_and_refused_after_the_fact(repo_rig):
    r = repo_rig
    seen: list[str] = []

    async def script(ctx: dict[str, Any], command: str, cwd: str | None = None) -> Any:
        seen.append(command)
        (Path(cwd or r.home) / "src" / "a.py").write_text("x = 99\n")
        (Path(cwd or r.home) / "src" / "new.py").write_text("z = 1\n")
        return {"exit_code": 0, "output": "wrote"}
    subprocess.run(["git", "init", "-q", str(r.home / "repo")], check=True)
    git = ["git", "-C", str(r.home / "repo"), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    r.tb.specs["shell_run"] = ToolSpec("shell_run", "x", {"type": "object", "properties": {}}, script, "shell", "executes")
    out = call(r.tb, "shell_run", {"conversation_id": "c1"}, command="python3 w.py", cwd=str(r.home / "repo"))  # the text check cannot see what w.py does
    assert "coding_session_start" in out["error"] and out["try_instead"] == "coding_session_start"
    assert "src/a.py" in out["error"] and "reverted" in out["error"]
    assert (r.home / "repo" / "src" / "a.py").read_text() == "x = 1\n" and not (r.home / "repo" / "src" / "new.py").exists()
    assert r.coding.snapshots == {}  # the snapshot is gone with the call


def test_snapshots_are_per_run_and_go_with_the_run_that_left_them(repo_rig):
    import asyncio
    import types
    r = repo_rig
    r.coding.snapshots[("r1", "call_0")] = {"a": 1}
    r.coding.snapshots[("r2", "call_0")] = {"b": 2}  # another run reusing the same provider call id
    asyncio.run(r.coding.drop_snapshot({"run_id": "r1", "call_id": "call_0"}))
    assert list(r.coding.snapshots) == [("r2", "call_0")]
    r.coding.snapshots[("r1", "call_1")] = {"c": 3}  # left by a later tool.pre refusal
    asyncio.run(r.coding.run_ended(types.SimpleNamespace(run_id="r1", stop=types.SimpleNamespace(is_set=lambda: False))))
    assert list(r.coding.snapshots) == [("r2", "call_0")]


def test_a_snapshot_is_dropped_when_the_call_fails(repo_rig):
    r = repo_rig
    subprocess.run(["git", "init", "-q", str(r.home / "repo")], check=True)

    async def boom(ctx: dict[str, Any], command: str, cwd: str | None = None) -> Any:
        assert len(r.coding.snapshots) == 1  # taken at the gate, kept while the command runs
        raise RuntimeError("killed")
    r.tb.specs["shell_run"] = ToolSpec("shell_run", "x", {"type": "object", "properties": {}}, boom, "shell", "executes")
    out = call(r.tb, "shell_run", {"conversation_id": "c1"}, command="python3 w.py", cwd=str(r.home / "repo"))
    assert "killed" in out["error"] and r.coding.snapshots == {}


def swap_gate(r: Any, fn: Any) -> None:
    """Replace what the extension's tool.pre subscription runs, keeping its place on the bus."""
    sub = next(s for s in r.host.bus._subs["tool.pre"] if s.owner == "coding")
    sub.fn = fn


@pytest.mark.parametrize("gate", ["raises", "hangs", "allows", "returns junk"])
def test_a_gate_that_fails_never_lets_a_routed_call_through(repo_rig, gate, monkeypatch):
    r = repo_rig
    monkeypatch.setitem(hooks.TIMEOUT_S, "gate", 0.2)
    sub = next(s for s in r.host.bus._subs["tool.pre"] if s.owner == "coding")
    sub.timeout_s = None

    def raises(_ev: Any) -> None:
        raise RuntimeError("bug in the gate")

    def hangs(_ev: Any) -> None:
        time.sleep(2)
    swap_gate(r, {"raises": raises, "hangs": hangs, "allows": lambda ev: "allow", "returns junk": lambda ev: {"verdict": "allow"}}[gate])
    out = call(r.tb, "fs_edit", {"conversation_id": "c1"}, path=str(r.target), old="x = 1", new="x = 2")
    assert "needs the user's approval" in out["error"] and "did not answer" in out["error"]  # an ask, never the edit
    assert r.target.read_text() == "x = 1\n"


def test_the_gate_is_inert_while_the_extension_is_off(repo_rig):
    r = repo_rig
    r.coding.api._ctx.hidden_views = lambda: ["coding"]
    r.cfg["hiddenViews"] = ["coding"]  # what the Toolbox reads
    ctx = {"conversation_id": "c1", "settings": r.cfg}
    assert r.tb.precheck("fs_edit", {"path": str(r.target), "old": "x = 1", "new": "x = 2"}, ctx) is None
    assert "error" not in call(r.tb, "fs_mkdir", ctx, path=str(r.home / "repo" / "build"))  # nothing routes it: the same as codingRoute off
    assert (r.home / "repo" / "build").is_dir()
    assert r.host.directives.text({}) == ""
    assert not r.tb.available("coding_session_start", google_ok=True) and not r.tb.available("opencode_run", google_ok=True)


# ---- the whole app: off takes everything away, on brings it back ----
def test_turning_the_extension_off_hides_its_tools_routes_and_rule_and_on_restores_them():
    from fastapi.testclient import TestClient
    from personal_os import app as appmod
    c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    names = [*SESSION_TOOLS, "opencode_run", *NEW_TOOLS]
    try:
        appmod.db.set_settings({"hiddenViews": []})
        assert [n for n in names if not appmod.toolbox.tool_owner.get(n) == "coding"] == []
        assert c.get("/coding-sessions").status_code == 200 and c.get("/coding/recipes").status_code == 200
        assert "Coding goes to a coding agent" in appmod.host.directives.text(appmod.settings())
        appmod.db.set_settings({"hiddenViews": ["coding"]})
        assert not any(appmod.toolbox.available(n) for n in names)
        assert c.get("/coding-sessions").status_code == 404 and c.get("/coding/recipes").status_code == 404 and c.get("/coding/terminals").status_code == 404
        assert appmod.host.directives.text(appmod.settings()) == ""
        assert appmod.toolbox.prechecks.refusal("fs_edit", {"path": os.path.expanduser("~/x.py"), "old": "a", "new": "b"}, {"conversation_id": "c"}) is None
        modes = {n: "on" for n in appmod.toolbox.specs}
        assert not {"coding_session_start", "opencode_run", "coding_recipe_run"} & {t["function"]["name"] for t in appmod.toolbox.schemas(modes)}
    finally:
        appmod.db.set_settings({"hiddenViews": []})
    assert c.get("/coding-sessions").status_code == 200
    assert {"coding_session_start", "coding_recipe_list"} <= {t["function"]["name"] for t in appmod.toolbox.schemas({n: "on" for n in appmod.toolbox.specs})
                                                              if t["function"]["name"] in names}


def test_an_install_that_had_coding_on_keeps_it_on_and_a_fresh_one_starts_with_it_on():
    from personal_os import app as appmod
    assert appmod.host.extensions["coding"].enabled() is True
    assert "coding" not in appmod.settings().get("hiddenViews", [])
    assert permissions.get({}, "codingRoute") is True and permissions.get({}, "codingAgent") == "any"  # the settings did not move


# ---- data ----
def test_the_baseline_step_changes_nothing_on_an_install_that_has_the_tables_and_the_rest_add_to_them(tmp_path):
    db = Database(tmp_path)
    with db.tx() as c:
        c.execute("INSERT INTO coding_sessions(id, agent, repo_path, worktree, name, prompt, status, created_at, updated_at) "
                  "VALUES('keep','claude','/r','/w','n','p','done',1,1)")
        c.execute("INSERT INTO coding_session_events(session_id, seq, ts, kind, text) VALUES('keep',1,'t','user','hello')")
    host = ExtensionHost()
    scope = coding_scope(host, db)
    host.migrate(db)  # a second look finds nothing left to do
    with db.tx() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(coding_sessions)")}
        assert {"base_sha", "hook_key", "hook_gate", "recipe_run_id", "cli_opts"} <= cols
        assert c.execute("SELECT id, status FROM coding_sessions").fetchall()[0][:] == ("keep", "done")
        assert c.execute("SELECT text FROM coding_session_events").fetchone()[0] == "hello"
        done = {r[0] for r in c.execute("SELECT version FROM schema_migrations WHERE typeof(version) = 'text' AND version LIKE 'ext:coding:%'")}
    assert done == {f"ext:coding:{n}" for n in (1, 2, 3, 4)}
    import contextlib
    with contextlib.closing(db.connect()) as c:
        steps = migrations.sql_steps(scope.root / "migrations")
        assert migrations.pending_extension(c, "coding", steps) == []
        baseline = steps[0][2]
        c.execute("BEGIN")
        baseline(c)  # run again: IF NOT EXISTS all the way
        c.rollback()


def test_a_fresh_database_gets_the_same_final_shape_from_the_core_steps_and_the_extension(tmp_path):
    db = Database(tmp_path)
    scope = coding_scope(ExtensionHost(), db)
    with db.tx() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(coding_sessions)")]
        assert cols[:21] == ["id", "agent", "external_id", "session_id", "repo_path", "worktree", "branch", "conversation_id", "desk_id", "run_id", "name",
                            "prompt", "model", "permission_mode", "status", "detail", "log_tail", "created_at", "updated_at", "ended_at", "base_sha"]
        assert cols[21:] == ["hook_key", "hook_gate", "recipe_run_id", "cli_opts"]
        for table in scope.manifest.data["tables"]:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone(), table


def test_the_readable_export_carries_the_tables(tmp_path):
    from personal_os import app as appmod
    out = tmp_path / "export.zip"
    backups.export_zip(appmod.db.data_dir, out, appmod.host.data_tables())
    names = set(zipfile.ZipFile(out).namelist())
    assert {f"export/extensions/{t}.json" for t in ("coding_sessions", "coding_recipes", "coding_terminals")} <= names


# ---- the pieces around it ----
def test_a_wake_owed_to_a_chat_is_a_session_row_as_text_and_several_fold_into_one_turn():
    from personal_os import app as appmod
    row = {"id": "s1", "name": "fix", "agent": "claude", "status": "done", "detail": "all green", "branch": "grain/x", "worktree": "/w", "log_tail": "ok"}
    text, wake = ext.build_coding_wake(row)
    assert text.startswith(ext.CODING_WAKE_HEADER) and "Coding session s1 (fix, claude): done. all green Branch grain/x in /w." in text
    assert wake == {"ids": [], "tainted": True, "title": "a coding session", "attachments": []}
    other = ext.build_coding_wake({**row, "id": "s2"})
    merged_text, merged = appmod._merge_wakes([(text, wake), other])
    assert merged_text == text + "\n\n" + other[0] and merged["tainted"] is True and merged["title"] == "a coding session" and merged["ids"] == []


def test_a_stopped_run_or_child_stops_the_sessions_it_started_and_a_finished_one_does_not(repo_rig):
    r = repo_rig
    stopped: list[tuple[str, str]] = []

    async def stop_owned(run_id: str, why: str) -> list[Any]:
        stopped.append((run_id, why))
        return []
    r.coding.sessions.stop_owned = stop_owned  # type: ignore[method-assign]
    done = SimpleNamespace(stop=asyncio.Event(), run_id="r1", desk_id=None, kind="chat")
    asyncio.run(r.coding.run_ended(done))
    stopped_run = SimpleNamespace(stop=asyncio.Event(), run_id="r2", desk_id="d1", kind="chat")
    stopped_run.stop.set()
    asyncio.run(r.coding.run_ended(stopped_run))
    for reason, error in (("completed", None), ("error", "x"), ("stopped", "Stopped by the user."), ("stale", None), ("interrupted", None), ("shutdown", None)):
        asyncio.run(r.coding.child_ended({"child_id": f"c-{reason}", "exit_reason": reason, "error": error, "detached": reason == "stale"}))
    assert stopped == [("r2", "the desk run that started it was stopped"),
                       ("c-stopped", "the subagent that started it was stopped by the user"),
                       ("c-stale", "the worker that started it was stopped by the hang watchdog"),
                       ("c-interrupted", "the subagent that started it was stopped with the reply or agent that started it"),
                       ("c-shutdown", "the subagent that started it was halted at app shutdown")]


def test_shutdown_stops_the_sessions_of_runs_still_going_then_leaves_the_rest(repo_rig):
    r = repo_rig
    stopped: list[str] = []

    async def stop_owned(run_id: str, why: str) -> list[Any]:
        stopped.append(why)
        return []
    r.coding.sessions.stop_owned = stop_owned  # type: ignore[method-assign]
    asyncio.run(r.coding.shutdown({"a", "b"}))
    assert stopped == ["Grain was shut down while it was running"] * 2 and r.coding.sessions.closing is True


def test_the_system_panel_learns_the_installed_harnesses_from_the_service():
    from personal_os.app import _ext_ctx
    service = _ext_ctx.services["coding"]()
    assert set(service.clis()) == {"claude", "opencode", "pi"} and all(callable(find) and hint for find, hint in service.clis().values())
    assert service.clis()["claude"][1].startswith("Install the claude CLI")


def test_a_tool_with_a_preview_shows_its_lines_on_the_approval_card(tmp_path):
    from personal_os import app as appmod, permrules
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    saved = appmod.coding.recipes.save({"name": "card-test", "prompt": "do it", "repo_path": str(repo), "driver": "claude", "verify": {"command": "make test"}}, "user")
    try:
        perm = permrules.resolve("coding_recipe_run", {}, "ask", False, rules=None)
        card = appmod._permission_card(perm, "external", "coding_recipe_run", {"recipe": "card-test"})
        assert card and any("`make test` exits 0" in line for line in card["change"])
        assert "change" not in (appmod._permission_card(perm, "external", "coding_recipe_run", {"recipe": "nope"}) or {})  # nothing to show for a call that would not run
        change = appmod._permission_card(perm, "danger", "permissions_update", {"kind": "mode", "value": "manual"})
        assert change and "change" in change  # the settings card still has its before and after
    finally:
        appmod.coding.recipes.archive(saved["recipe_id"])


def test_a_dispatched_call_carries_its_id_to_the_result_error_and_post_hooks():
    seen: dict[str, Any] = {}
    host = ExtensionHost()
    for hook in ("tool.result", "tool.error", "tool.post"):
        host.bus.subscribe(hook, lambda p, h=hook: seen.setdefault(h, p) and None, owner="t", order=0)

    async def boom(ctx: dict[str, Any]) -> Any:
        raise RuntimeError("x")
    tb = SimpleNamespace(bus=host.bus, call=lambda *_a: boom({}))
    with pytest.raises(RuntimeError):
        asyncio.run(dispatch.dispatch_tool(tb, {"desk_id": "d1"}, "t", {}, call_id="id9"))  # type: ignore[arg-type]
    assert seen["tool.error"]["call_id"] == "id9" and seen["tool.post"]["call_id"] == "id9"


def test_the_directive_is_part_of_the_prefix_hash_by_its_version(repo_rig):
    from personal_os.context import prefix_hash
    from personal_os.extensions.directives import Directives
    r = repo_rig
    assert r.host.directives.versions({}) == ["coding@1"] and r.host.directives.versions({"codingRoute": False}) == []
    newer = Directives()
    newer.register("coding", lambda cfg: "same text", 2, lambda: True)
    older = Directives()
    older.register("coding", lambda cfg: "same text", 1, lambda: True)
    assert older.text({}) == newer.text({}) and prefix_hash("p", older.versions({})) != prefix_hash("p", newer.versions({}))
