"""Coding goes to OpenCode / Claude Code (coding_route.py): Grain's own file writers and file-changing shell commands are
refused inside a git work tree under every permission mode and for every agent, with a message naming coding_session_start;
reads, tests and publishing stay allowed, edits outside a repo are untouched, the setting turns it off, and the Allow
everything floor still decides its own cases. Offline: a fake HOME in a tmpdir.

Run: cd backend && .venv/bin/python -m pytest -q tests/test_coding_route.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import coding_route, permissions, permrules, shell  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    (h / "repo" / ".git").mkdir(parents=True)
    (h / "repo" / "src").mkdir()
    (h / "repo" / "src" / "a.py").write_text("x = 1\n")
    (h / "notes").mkdir()
    (h / "notes" / "todo.md").write_text("- one\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("PERSONAL_OS_DATA_DIR", str(tmp_path / "data"))
    return h


def make(tmp_path: Path, **settings: Any) -> tuple[Toolbox, dict[str, Any]]:
    cfg: dict[str, Any] = {"permissionMode": "allow_all", **settings}
    tb = Toolbox(None, None, None, lambda: cfg, workspace=Workspace(tmp_path / "data"))  # type: ignore[arg-type]
    return tb, cfg


def call(tb: Toolbox, name: str, ctx: dict[str, Any], **args: Any) -> Any:
    return asyncio.run(tb.call(name, args, ctx))


def refused(out: Any) -> bool:
    return isinstance(out, dict) and "coding_session_start" in str(out.get("error") or "")


WORKER = {"conversation_id": "c-worker", "kind": "worker"}
CHAT = {"conversation_id": "c-chat"}

WRITES = [
    "sed -i '' 's/x = 1/x = 2/' src/a.py",
    "echo y = 2 > src/b.py",
    "echo y >> src/a.py",
    "cat src/a.py | tee src/c.py",
    "git commit -am wip",
    "git checkout -b feat/x origin/main",
    "git stash",
    "git switch main",
    "npm install left-pad",
    "rm src/a.py",
    "mkdir -p build",
    "perl -pi -e 's/1/2/' src/a.py",
    "python3 -c \"open('src/a.py','w').write('')\"",
    "ruff check --fix .",
    "git apply fix.patch",
]
READS = [
    "cat src/a.py",
    "rg 'x =' src",
    "ls -la",
    "git status",
    "git diff HEAD~1",
    "git log --oneline -5",
    "git fetch origin",
    "git branch --list",
    "npm test",
    "npm run typecheck 2>&1 | tail -20",
    "pytest -q tests/test_x.py",
    "python3 -m pytest -q",
    "npx tsc --noEmit",
    "eslint src",
    "npm ci",
    "git push -u origin feat/x",
    "gh pr create --fill",
    "gh pr merge 12 --squash",
    "cat src/a.py > /tmp/copy.py",
    "cp src/a.py /tmp/a.py",
]


@pytest.mark.parametrize("cmd", WRITES)
def test_shell_writes_in_a_repo_are_refused(home: Path, cmd: str) -> None:
    hit = coding_route._shell_refusal(cmd, str(home / "repo"))
    assert hit is not None and hit[1] == str(home / "repo"), cmd


@pytest.mark.parametrize("cmd", WRITES)
def test_cd_moves_the_judged_folder(home: Path, cmd: str) -> None:
    """Started from home (no repo) the command is allowed; a `cd repo &&` prefix makes it a repo write."""
    assert coding_route._shell_refusal(cmd, str(home)) is None, cmd
    assert coding_route._shell_refusal(f'cd "{home}/repo" && {cmd}', str(home)) is not None, cmd


def test_git_dash_c_and_absolute_paths(home: Path) -> None:
    repo = home / "repo"
    assert coding_route._shell_refusal(f"git -C {repo} commit -m x", str(home)) is not None
    assert coding_route._shell_refusal(f"git -C {repo} status", str(home)) is None
    assert coding_route._shell_refusal(f"echo hi > {repo}/src/new.py", str(home)) is not None
    assert coding_route._shell_refusal(f"sed -i '' s/a/b/ {repo}/src/a.py", "/tmp") is not None
    assert coding_route._shell_refusal(f"bash -c 'cd {repo} && git reset --hard'", str(home)) is not None


@pytest.mark.parametrize("cmd", READS)
def test_reads_tests_and_publishing_in_a_repo_are_allowed(home: Path, cmd: str) -> None:
    assert coding_route._shell_refusal(cmd, str(home / "repo")) is None, cmd


def test_edits_outside_a_repo_are_allowed(home: Path) -> None:
    for cmd in ("echo hi > todo.md", "sed -i '' s/one/two/ todo.md", "git init -q && git add -A", "rm todo.md"):
        assert coding_route._shell_refusal(cmd, str(home / "notes")) is None, cmd


def test_fs_edit_in_a_repo_is_refused_under_allow_all_for_chat_and_worker(home: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    for ctx in (dict(CHAT), dict(WORKER)):
        out = call(tb, "fs_edit", ctx, path=str(home / "repo/src/a.py"), old="x = 1", new="x = 2")
        assert refused(out), out
        assert refused(call(tb, "write_local_file", ctx, path="~/repo/src/new.py", content="y\n"))
        assert refused(call(tb, "fs_mkdir", ctx, path=str(home / "repo/build")))
        assert refused(call(tb, "move_local_file", ctx, path=str(home / "repo/src/a.py"), to=str(home / "notes/a.py")))
    assert (home / "repo/src/a.py").read_text() == "x = 1\n"
    assert not (home / "repo/src/new.py").exists()


def test_shell_run_in_a_repo_is_refused_before_it_runs(home: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    for cmd in (f"cd {home}/repo && echo y > src/a.py", f"cd {home}/repo && git commit -am x"):
        out = call(tb, "shell_run", dict(WORKER), command=cmd)
        assert refused(out), out
        assert "Route coding to OpenCode / Claude Code" in out["error"]
    assert (home / "repo/src/a.py").read_text() == "x = 1\n"
    # precheck refuses it the same way, before any card or reviewer
    assert refused(tb.precheck("shell_run", {"command": "echo y > src/a.py", "cwd": str(home / "repo")}, dict(CHAT)))
    assert tb.precheck("shell_run", {"command": "echo y > src/a.py", "cwd": str(home / "repo")}) is None  # no ctx: signature only


@pytest.mark.parametrize("mode", ["auto", "manual", "allow_all"])
def test_every_permission_mode(home: Path, tmp_path: Path, mode: str) -> None:
    tb, _ = make(tmp_path, permissionMode=mode)
    assert refused(call(tb, "fs_edit", dict(CHAT), path=str(home / "repo/src/a.py"), old="x = 1", new="x = 2"))


def test_edits_outside_a_repo_still_work(home: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    ctx = dict(CHAT)
    call(tb, "read_local_file", ctx, path=str(home / "notes/todo.md"))
    out = call(tb, "fs_edit", ctx, path=str(home / "notes/todo.md"), old="one", new="two")
    assert not refused(out), out
    assert (home / "notes/todo.md").read_text() == "- two\n"


def test_reading_a_repo_and_coding_session_tools_are_not_judged(home: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    assert coding_route.check(tb, "read_local_file", {"path": str(home / "repo/src/a.py")}, dict(CHAT)) is None
    assert coding_route.check(tb, "fs_grep", {"root": str(home / "repo"), "pattern": "x"}, dict(CHAT)) is None
    for name in ("coding_session_start", "coding_session_send", "opencode_run"):
        assert coding_route.check(tb, name, {"repo_path": str(home / "repo"), "cwd": str(home / "repo"),
                                             "prompt": "edit src/a.py"}, dict(CHAT)) is None


def test_setting_off_allows_direct_edits_again(home: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path, codingRoute=False)
    ctx = dict(CHAT)
    call(tb, "read_local_file", ctx, path=str(home / "repo/src/a.py"))
    out = call(tb, "fs_edit", ctx, path=str(home / "repo/src/a.py"), old="x = 1", new="x = 2")
    assert not refused(out), out
    assert (home / "repo/src/a.py").read_text() == "x = 2\n"
    assert coding_route.check(tb, "shell_run", {"command": "git commit -am x", "cwd": str(home / "repo")}, ctx) is None
    assert coding_route.hint({"codingRoute": False}) == ""


def test_refusal_and_hint_name_the_preferred_agent(home: Path, tmp_path: Path) -> None:
    for choice, needle in (("opencode", "agent='opencode'"), ("claude", "agent='claude'"), ("any", "agent='opencode' (OpenCode) or agent='claude'")):
        tb, cfg = make(tmp_path, codingAgent=choice)
        msg = coding_route.check(tb, "fs_edit", {"path": str(home / "repo/src/a.py")}, {"settings": cfg})
        assert msg and needle in msg and f"repo_path='{home / 'repo'}'" in msg
        assert needle in coding_route.hint(cfg)
    assert "coding_session_start" in coding_route.hint({})  # on by default


def test_settings_defaults_and_validation() -> None:
    assert permissions.DEFAULTS["codingRoute"] is True and permissions.DEFAULTS["codingAgent"] == "any"
    assert permissions.validate("codingAgent", "claude") == "claude"
    with pytest.raises(ValueError):
        permissions.validate("codingAgent", "cursor")
    with pytest.raises(ValueError):
        permissions.validate("codingRoute", "yes")


def test_allow_all_floor_still_decides_its_own_cases(home: Path, tmp_path: Path) -> None:
    """The route only adds refusals: the floor still cards a force-push and a permanent delete, and the hardline list
    still refuses, with the setting on."""
    tb, cfg = make(tmp_path)
    ctx = {"conversation_id": "c-floor", "settings": cfg}
    push = {"command": "git push --force origin main", "cwd": str(home / "repo")}
    assert coding_route.check(tb, "shell_run", push, ctx) is None  # publishing is not an edit ...
    rm = {"command": "rm -rf /opt/grain-test-not-a-repo/notes"}  # the floor treats temp folders (tmp_path) as scratch
    assert coding_route.check(tb, "shell_run", rm, ctx) is None
    assert permrules.resolve("shell_run", {"command": "rm -rf /"}, "on", False, rules={}).refusal


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_run_python_bridge_and_workflow_steps_refuse_before_any_card(home: Path, tmp_path: Path, mode: str) -> None:
    """A repo edit through run_python's tool bridge or a workflow tool step is refused before an approval card, the
    same as the model's own calls (Toolbox.precheck), rather than carded and then refused by Toolbox.call."""
    from types import SimpleNamespace

    from personal_os import toolbridge, workflows

    tb, cfg = make(tmp_path, permissionMode=mode)
    edit = {"path": str(home / "repo/src/a.py"), "old": "x = 1", "new": "x = 2"}
    cards: list[str] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        cards.append(name)
        return True

    b = toolbridge.Bridge(tb, {**CHAT, "settings": cfg}, ["fs_edit"], {"fs_edit": "ask"}, approve)
    out = asyncio.run(b.handle("fs_edit", dict(edit)))
    assert refused(out["result"]) and cards == [] and b.log[-1]["ok"] is False, out

    eng = workflows.Engine(SimpleNamespace(set_step=lambda *a, **k: None), tb, None, None, lambda: cfg)

    async def ask(*_a: Any, **_k: Any) -> bool:
        cards.append("workflow")
        return True

    eng._ask = ask  # type: ignore[method-assign]
    run = {"id": "r1", "definition": {"steps": [{"id": "s", "tool": "fs_edit"}]}}
    ctx = {**CHAT, "settings": cfg, "modes": {"fs_edit": "ask"}}
    step = {"id": "s", "tool": "fs_edit", "args": edit, "approval": "required"}
    for coro in (eng._approve_step(run, ctx, step, {}, {}, asyncio.Event()),
                 eng._tool_step(run, ctx, step, dict(edit), asyncio.Event())):
        with pytest.raises(workflows._StepFailed, match="coding_session_start"):
            asyncio.run(coro)
    assert cards == [] and (home / "repo/src/a.py").read_text() == "x = 1\n"


# ---- after the fact: a script the text check cannot read is still caught and put back ----
@pytest.fixture
def gitrepo(home: Path) -> Path:
    import subprocess
    r = home / "proj"
    (r / "src").mkdir(parents=True)
    (r / "src" / "a.py").write_text("x = 1\n")
    (r / "src" / "b.py").write_text("y = 1\n")
    (r / ".gitignore").write_text("node_modules/\ndist/\n")
    g = ["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*g[:3], "init", "-q"], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "init"], check=True)
    (r / "src" / "b.py").write_text("y = 2  # the user's own edit\n")  # dirty before any command
    (home / "notes" / "w.py").write_text(
        "import pathlib, sys\nroot = pathlib.Path(sys.argv[1])\n"
        "(root / 'src/a.py').write_text('x = 99\\n')\n(root / 'src/new.py').write_text('z = 1\\n')\n"
        "(root / 'node_modules').mkdir(exist_ok=True)\n(root / 'node_modules/pkg.js').write_text('1')\n")
    return r


def test_script_writing_a_repo_is_reverted_and_refused(home: Path, gitrepo: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    cmd = f"python3 {home}/notes/w.py {gitrepo}"
    assert coding_route._shell_refusal(cmd, str(gitrepo)) is None  # the text check cannot see it
    out = call(tb, "shell_run", {"conversation_id": "c-guard"}, command=cmd, cwd=str(gitrepo))
    assert refused(out), out
    assert "src/a.py" in out["error"] and "reverted" in out["error"]
    assert (gitrepo / "src/a.py").read_text() == "x = 1\n"
    assert not (gitrepo / "src/new.py").exists()
    assert (gitrepo / "src/b.py").read_text() == "y = 2  # the user's own edit\n"  # already dirty: never touched
    assert (gitrepo / "node_modules/pkg.js").exists()  # gitignored: not a source edit


def test_heredoc_from_outside_the_repo_is_caught(home: Path, gitrepo: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    cmd = f"cd {gitrepo} && python3 - <<'PY'\nimport pathlib\npathlib.Path('src/a.py').write_text('')\nPY"
    out = call(tb, "shell_run", {"conversation_id": "c-guard2"}, command=cmd, cwd=str(home / "notes"))
    assert refused(out), out
    assert (gitrepo / "src/a.py").read_text() == "x = 1\n"


def test_guard_skips_reads_tests_builds_and_setting_off(home: Path, gitrepo: Path, tmp_path: Path) -> None:
    tb, _ = make(tmp_path)
    ctx = {"conversation_id": "c-guard3"}
    for cmd in ("npm test", "npx vitest run x.test.ts", "cd web && npm ci", "python3 -m pytest -q | tail -5", "make build",
                "uv run pytest -q", "cargo test"):
        assert coding_route.guard_start(tb, {"command": cmd, "cwd": str(gitrepo)}, ctx) is None, cmd
    assert coding_route.guard_start(tb, {"command": "python3 w.py", "cwd": str(gitrepo)}, ctx) is not None
    assert coding_route.guard_start(tb, {"command": "python3 w.py", "cwd": str(home / "notes")}, ctx) is None  # no repo
    off, _ = make(tmp_path, codingRoute=False)
    assert coding_route.guard_start(off, {"command": "python3 w.py", "cwd": str(gitrepo)}, ctx) is None
    out = call(tb, "shell_run", ctx, command="cat src/a.py && ls", cwd=str(gitrepo))
    assert not refused(out) and "x = 1" in out["output"], out
