"""opencode_run (opencode.py): any folder but the protected ones, an agent's own folder as a prompt hint (app._persona_folder).

The coding agent itself is replaced by a small script that prints the JSON events `opencode run --format json`
prints and writes one file, so the test pins the plumbing (sandbox, state dirs, config, summary) and not a model.
Where the Seatbelt sandbox is needed the test uses the real sandbox-exec and skips without it."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="opencodetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

from personal_os import opencode, sandbox, shell  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_servers import RESERVED_TOOL_NAMES  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

HAVE_SEATBELT = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
needs_seatbelt = pytest.mark.skipif(not HAVE_SEATBELT, reason="sandbox-exec is not available")

FAKE = """#!/bin/sh
# A stand-in for `opencode run --format json`: proves the cwd reaches the process and that opencode is
# left to its own config and state (it writes where a real opencode would, under the user's HOME).
echo '{"type":"step_start","sessionID":"ses_fake","part":{"type":"step-start"}}'
echo '{"type":"tool","sessionID":"ses_fake","part":{"type":"tool","tool":"write","state":{"title":"hello.txt"}}}'
printf 'hello from fake opencode\\n' > hello.txt || echo "write failed: $?"
mkdir -p "$HOME/.local/share/opencode" && echo "cfg=${OPENCODE_CONFIG_CONTENT-unset}" > "$HOME/.local/share/opencode/seen-config"
echo "{\\"type\\":\\"text\\",\\"sessionID\\":\\"ses_fake\\",\\"part\\":{\\"type\\":\\"text\\",\\"text\\":\\"Wrote hello.txt in $(pwd) with model $7\\"}}"
"""


class Box:
    def __init__(self, tmp: Path, **settings: Any):
        self.root = (tmp / "home" / "work").resolve()
        self.root.mkdir(parents=True)
        self.settings: dict[str, Any] = {"baseUrl": "http://localhost:4000",
                                         "defaultModel": "glm-5.3", "apiKey": "", **settings}
        self.db = Database(tmp / "data")
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)", ("c1", "t", "m", 0.0, 0.0))
        self.tb = Toolbox(None, None, None, lambda: self.settings, results=ToolResults(self.db))  # type: ignore[arg-type]
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "message_id": None, "settings": self.settings,
                                    "tainted": False, "taint_sources": []}

    def run(self, name: str, **args: Any) -> Any:
        return asyncio.run(self.tb.call(name, args, self.ctx))


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_registered_in_the_shell_group_asks_by_default_and_is_reserved(box: Box) -> None:
    spec = box.tb.specs["opencode_run"]
    assert (spec.group, spec.danger, box.tb.default_mode(spec)) == ("shell", "executes", "ask")
    assert "opencode_run" in RESERVED_TOOL_NAMES
    # a reply that read untrusted content cannot drive a networked coding agent without a card
    assert box.tb.gate("opencode_run", "on", {}, {"prompt": "x"}) == "on"
    assert box.tb.gate("opencode_run", "on", {"tainted": True}, {"prompt": "x"}) == "ask"


def test_missing_binary_is_an_actionable_error(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opencode, "binary", lambda: None)
    r = box.run("opencode_run", prompt="fix it")
    assert "brew install opencode" in r["error"]


def test_opencodes_own_config_and_state_are_left_alone(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    """Grain links to the installed opencode: no injected provider, no XDG redirection, no key of ours."""
    got: dict[str, Any] = {}

    async def fake_start(argv: list[str], **kw: Any) -> Any:
        got.update(kw, argv=argv)
        raise shell.ShellError("stop here")

    monkeypatch.setattr(opencode, "binary", lambda: "/bin/echo")
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    monkeypatch.setattr(box.tb.shell, "start", fake_start)
    box.run("opencode_run", prompt="x")
    env = got["env"]
    for k in ("OPENCODE_CONFIG_CONTENT", "GRAIN_MODEL_API_KEY", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
              "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert k not in env, k
    assert env["HOME"] == os.path.expanduser("~")  # so it finds ~/.config/opencode and its own sessions
    assert "-m" not in got["argv"]  # no model named: opencode's own default stands
    assert not any(a.startswith("grain/") for a in got["argv"])


def test_a_named_model_is_passed_through_unprefixed(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    async def fake_start(argv: list[str], **kw: Any) -> Any:
        got.update(argv=argv)
        raise shell.ShellError("stop here")

    monkeypatch.setattr(opencode, "binary", lambda: "/bin/echo")
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    monkeypatch.setattr(box.tb.shell, "start", fake_start)
    box.run("opencode_run", prompt="x", model="fireworks/ember-1")
    assert got["argv"][got["argv"].index("-m") + 1] == "fireworks/ember-1"


def test_sandbox_hosts_and_loopback() -> None:
    assert opencode.ALLOW_HOSTS == ["*:443"]
    p = sandbox.shell_profile(["/tmp"], allow_hosts=["localhost:4000", "*:443", "evil.example:443"], loopback=True)
    net = [l for l in p.splitlines() if "network" in l]
    assert '(allow network-outbound (remote tcp "localhost:4000"))' in net
    assert '(allow network-outbound (remote tcp "*:443"))' in net
    assert not any("evil" in l for l in net)  # only localhost or * are ever written
    assert any("network-bind" in l for l in net)
    assert not any("network-bind" in l for l in sandbox.shell_profile(["/tmp"]).splitlines())
    # A remote host allow needs the resolver socket with it, or every hostname fails ENOTFOUND inside.
    dns = f'(allow network-outbound (remote unix-socket (path-literal "{sandbox.MDNS_SOCKET}")))'
    assert dns in net
    assert dns not in sandbox.shell_profile(["/tmp"], allow_hosts=["localhost:4000"], loopback=True)
    assert dns not in sandbox.shell_profile(["/tmp"], proxy_port=4000)
    assert dns not in sandbox.shell_profile(["/tmp"])


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"), reason="needs macOS sandbox-exec")
def test_sandboxed_coding_agent_can_resolve_a_hostname() -> None:
    """The live check behind the rule above: `*:443` is useless while getaddrinfo cannot reach mDNSResponder."""
    code = "import socket; socket.getaddrinfo('api.fireworks.ai', 443)"
    if subprocess.run([sys.executable, "-c", code], capture_output=True).returncode:
        pytest.skip("no DNS on this machine")
    p = sandbox.shell_profile(["/tmp"], network=False, allow_hosts=opencode.ALLOW_HOSTS, loopback=True)
    r = subprocess.run(["sandbox-exec", "-p", p, sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]


def test_summary_keeps_text_and_tool_lines_and_the_session() -> None:
    raw = "\n".join([
        '{"type":"step_start","sessionID":"ses_1","part":{"type":"step-start"}}',
        '{"type":"tool","part":{"type":"tool","tool":"bash","state":{"title":"ls -la"}}}',
        '{"type":"text","part":{"type":"text","text":"Done."}}',
        "not json at all",
    ])
    text, session = opencode.summarize(raw)
    assert text == "[bash] ls -la\nDone.\nnot json at all" and session == "ses_1"


@needs_seatbelt
def test_runs_the_agent_sandboxed_in_the_working_folder_with_its_own_state(box: Box, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "bin" / "opencode"
    fake.parent.mkdir()
    fake.write_text(FAKE)
    fake.chmod(0o755)
    monkeypatch.setattr(opencode, "binary", lambda: str(fake))
    r = box.run("opencode_run", prompt="write hello", cwd=str(box.root))
    assert r.get("exit_code") == 0, r
    assert (box.root / "hello.txt").read_text() == "hello from fake opencode\n"
    assert r["session_id"] == "ses_fake" and r["model"] == "opencode default" and r["sandboxed"] is True
    assert "[write] hello.txt" in r["output"] and f"Wrote hello.txt in {box.root}" in r["output"]
    assert "write failed" not in r["output"]
    # the reply is tainted: a model with network access wrote the output
    assert box.ctx["tainted"] and "opencode_run:network" in box.ctx["taint_sources"]
    # no config is injected, and opencode's state stays where opencode puts it: the user's own home, not the data dir
    seen = Path(os.environ["HOME"]) / ".local" / "share" / "opencode" / "seen-config"
    assert seen.read_text().strip() == "cfg=unset"
    assert not (Path(box.db.data_dir) / "opencode").exists()


@needs_seatbelt
def test_launch_caches_packages_inside_the_per_launch_tmp_dir(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    async def fake_start(argv: list[str], **kw: Any) -> Any:
        got.update(kw)
        return object()

    monkeypatch.setattr(opencode, "binary", lambda: "/bin/echo")
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    monkeypatch.setattr(box.tb.shell, "start", fake_start)
    asyncio.run(opencode.launch(box.tb, box.ctx, "x", cwd=str(box.root), pool="coding", max_background=2))
    try:
        for k in ("npm_config_cache", "PIP_CACHE_DIR"):
            assert got["env"][k].startswith(got["tmp"] + os.sep), k
        assert (got["pool"], got["max_background"]) == ("coding", 2)
    finally:
        shutil.rmtree(got["tmp"], ignore_errors=True)


def test_cwd_is_anywhere_but_the_protected_places(box: Box, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    async def fake_start(argv: list[str], **kw: Any) -> Any:
        got.update(kw)
        raise shell.ShellError("stop here")

    monkeypatch.setattr(opencode, "binary", lambda: "/bin/echo")
    monkeypatch.setattr(shell, "sandbox_available", lambda: True)
    monkeypatch.setattr(box.tb.shell, "start", fake_start)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert "stop here" in box.run("opencode_run", prompt="x", cwd=str(elsewhere))["error"] and got["cwd"] == str(elsewhere)
    got.clear()
    box.run("opencode_run", prompt="x")  # no cwd: the home folder
    assert got["cwd"] == str(tmp_path / "home")
    data = Path(os.environ["PERSONAL_OS_DATA_DIR"])
    r = box.run("opencode_run", prompt="x", cwd=str(data))
    assert "off limits" in r["error"]
    r = box.run("opencode_run", prompt="x", cwd="/Applications/Grain.app")
    assert "off limits" in r["error"]


@needs_seatbelt
def test_profile_for_opencode_is_open_for_writes_and_keeps_the_protected_places(box: Box, tmp_path: Path) -> None:
    """opencode's own state lives in the user's home, which the profile already leaves writable; Grain's data
    folder and app stay denied."""
    data = Path(os.environ["PERSONAL_OS_DATA_DIR"]).resolve()
    p = sandbox.shell_profile(["/tmp/t"], network=False, allow_hosts=["*:443"], loopback=True)
    assert "(allow file-write*)\n" in p
    deny = p.index("(deny file-write* (subpath")
    assert f'(subpath "{data}")' in p[deny:deny + 400]
    assert '(subpath "/Applications/Grain.app")' in p and "LaunchAgents" in p and "(allow network-bind" in p


@needs_seatbelt
def test_opencodes_own_state_dirs_stay_writable_under_the_profile(tmp_path: Path) -> None:
    """The whole point of linking to the installed opencode: it must still reach its own config, sessions and auth."""
    import subprocess
    home = Path(os.path.expanduser("~"))
    p = sandbox.shell_profile([str(tmp_path)], network=False, allow_hosts=opencode.ALLOW_HOSTS, loopback=True)
    for d in (home / ".local" / "share" / "opencode", home / ".config" / "opencode"):
        probe = d / ".grain-sandbox-probe"
        r = subprocess.run(["sandbox-exec", "-p", p, "/bin/sh", "-c", f'mkdir -p {d!s:s} && : > "{probe}" && rm -f "{probe}"'],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{d} not writable under the profile: {r.stderr.strip()[:200]}"


# ---- an agent's own folder, and the retired per-chat working folder ----
def test_persona_folder_is_a_hint_only_for_a_folder_the_tools_may_reach(fake_home: Path) -> None:
    from types import SimpleNamespace
    from personal_os.app import _persona_folder
    good = fake_home / "repo"
    good.mkdir()
    assert _persona_folder(SimpleNamespace(workspace=str(good))) == str(good.resolve())
    assert _persona_folder(SimpleNamespace(workspace=str(good / "missing"))) is None
    assert _persona_folder(SimpleNamespace(workspace="")) is None and _persona_folder(SimpleNamespace(workspace=None)) is None
    assert _persona_folder(SimpleNamespace(workspace=os.environ["PERSONAL_OS_DATA_DIR"])) is None  # Grain's own folder
    assert _persona_folder(SimpleNamespace(workspace="/tmp")) == os.path.realpath("/tmp")           # outside home is fine


def test_patch_ignores_the_retired_working_folder(fake_home: Path) -> None:
    from fastapi.testclient import TestClient
    from personal_os.app import AUTH_TOKEN, app
    with TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN}) as client:
        cid = client.post("/conversations", json={}).json()["id"]
        repo = fake_home / "proj"
        repo.mkdir()
        for value in (str(repo), str(fake_home), str(repo / "nope"), ""):  # nothing to validate any more: no 422
            r = client.patch(f"/conversations/{cid}", json={"settings": {"workingFolder": value}})
            assert r.status_code == 200 and "workingFolder" not in r.json()["settings"]


# ---- a repo's main checkout gets its own worktree; the result says what changed ----
FAKE_EDIT = """#!/bin/sh
# Writes hello.txt unless the prompt says "nothing"; prints where it ran.
case "$*" in *nothing*) ;; *) printf 'hi\\n' > hello.txt ;; esac
echo "{\\"type\\":\\"text\\",\\"sessionID\\":\\"ses_g\\",\\"part\\":{\\"type\\":\\"text\\",\\"text\\":\\"ran in $(pwd)\\"}}"
"""


def _repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.txt").write_text("a\n")
    g = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run([*g, *args], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def fake_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "bin" / "opencode"
    fake.parent.mkdir()
    fake.write_text(FAKE_EDIT)
    fake.chmod(0o755)
    monkeypatch.setattr(opencode, "binary", lambda: str(fake))
    opencode._WORKTREES.clear()


def _branches(repo: Path) -> list[str]:
    return subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=repo, capture_output=True, text=True).stdout.split()


@needs_seatbelt
def test_main_checkout_runs_in_a_fresh_worktree_and_reports_the_change(box: Box, fake_edit: None) -> None:
    repo = _repo(box.root)
    r = box.run("opencode_run", prompt="write hello", cwd=str(repo / "src"))
    assert r.get("exit_code") == 0, r
    wt = Path(r["worktree"])
    assert wt.parent == repo / ".claude" / "worktrees" and r["branch"].startswith("grain/opencode-write-hello-")
    assert not (repo / "src" / "hello.txt").exists()                  # the checkout is untouched
    assert (wt / "src" / "hello.txt").read_text() == "hi\n"           # it ran in the same subfolder of the worktree
    assert f"ran in {wt / 'src'}" in r["output"]
    assert r["git"]["status"] == "?? src/hello.txt" and r["git"]["branch"] == r["branch"] and "removed" not in r["git"]
    assert r["git"]["base"] == subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    # a follow-up goes back to the same worktree
    r2 = box.run("opencode_run", prompt="write again", cwd=str(repo), continue_session=True)
    assert r2["worktree"] == str(wt) and f"ran in {wt}" in r2["output"]
    # a run started in the worktree itself stays there
    r3 = box.run("opencode_run", prompt="write more", cwd=str(wt))
    assert "worktree" not in r3 and r3["cwd"] == str(wt) and r3["git"]["status"] == "?? hello.txt\n?? src/hello.txt"


@needs_seatbelt
def test_a_worktree_left_without_changes_is_removed(box: Box, fake_edit: None) -> None:
    repo = _repo(box.root)
    r = box.run("opencode_run", prompt="change nothing", cwd=str(repo))
    assert r.get("exit_code") == 0, r
    assert r["git"]["removed"] is True and r["git"]["status"] == "clean" and "isolation" not in r
    assert not Path(r["worktree"]).exists() and _branches(repo) == ["main"]


@needs_seatbelt
def test_in_place_edits_the_checkout_and_still_reports(box: Box, fake_edit: None) -> None:
    repo = _repo(box.root)
    r = box.run("opencode_run", prompt="write hello", cwd=str(repo), in_place=True)
    assert r.get("exit_code") == 0, r
    assert (repo / "hello.txt").exists() and "worktree" not in r and r["git"]["status"] == "?? hello.txt"
    assert _branches(repo) == ["main"]


@needs_seatbelt
def test_outside_a_repo_there_is_no_worktree_and_no_git_report(box: Box, fake_edit: None) -> None:
    r = box.run("opencode_run", prompt="write hello", cwd=str(box.root))
    assert r.get("exit_code") == 0 and "worktree" not in r and "git" not in r


@needs_seatbelt
@pytest.mark.skipif(not shutil.which("opencode"), reason="needs the real opencode")
def test_killing_the_job_ends_opencodes_private_server_too(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    """`opencode run` starts `opencode serve --stdio` in a process group of its own, so the job's group kill does not
    reach it directly; it must still go when its parent does. No network: nothing reaches a provider."""
    monkeypatch.setattr(opencode, "binary", lambda: shutil.which("opencode"))
    monkeypatch.setattr(opencode, "ALLOW_HOSTS", [])

    def children(pid: int) -> list[int]:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
        return [int(l.split()[0]) for l in out.splitlines() if l.split()[1:2] == [str(pid)] and "serve" in l]

    async def go() -> list[int]:
        job, _ = await opencode.launch(box.tb, box.ctx, "say hi", cwd=str(box.root), background=True, timeout=120)
        kids: list[int] = []
        for _ in range(200):
            await asyncio.sleep(0.05)
            if kids := children(job.pid or 0):
                break
        await box.tb.shell.kill(job)
        await asyncio.sleep(3)
        return kids

    kids = asyncio.run(go())
    if not kids:
        pytest.skip("opencode did not start a private server in time")
    alive = [k for k in kids if subprocess.run(["ps", "-p", str(k)], capture_output=True).returncode == 0]
    for k in alive:
        os.kill(k, 9)
    assert not alive
