"""opencode_run (opencode.py) and the chat's working folder (app._working_folder, PATCH /conversations settings).

The coding agent itself is replaced by a small script that prints the JSON events `opencode run --format json`
prints and writes one file, so the test pins the plumbing (sandbox, state dirs, config, summary) and not a model.
Where the Seatbelt sandbox is needed the test uses the real sandbox-exec and skips without it."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="opencodetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")

from personal_os import opencode, sandbox  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_servers import RESERVED_TOOL_NAMES  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

HAVE_SEATBELT = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
needs_seatbelt = pytest.mark.skipif(not HAVE_SEATBELT, reason="sandbox-exec is not available")

FAKE = """#!/bin/sh
# A stand-in for `opencode run --format json`: proves the cwd, the state dirs and the config reach the process.
echo '{"type":"step_start","sessionID":"ses_fake","part":{"type":"step-start"}}'
echo '{"type":"tool","sessionID":"ses_fake","part":{"type":"tool","tool":"write","state":{"title":"hello.txt"}}}'
printf 'hello from fake opencode\\n' > hello.txt || echo "write failed: $?"
echo "cfg=$OPENCODE_CONFIG_CONTENT" > "$XDG_STATE_HOME/seen-config"
echo "{\\"type\\":\\"text\\",\\"sessionID\\":\\"ses_fake\\",\\"part\\":{\\"type\\":\\"text\\",\\"text\\":\\"Wrote hello.txt in $(pwd) with model $7\\"}}"
"""


class Box:
    def __init__(self, tmp: Path, **settings: Any):
        self.root = (tmp / "home" / "work").resolve()
        self.root.mkdir(parents=True)
        self.settings: dict[str, Any] = {"workspaceRoots": [str(self.root)], "baseUrl": "http://localhost:4000",
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


def test_config_points_at_grains_endpoint_and_allows_everything_inside_the_sandbox() -> None:
    cfg = json.loads(opencode.config("http://localhost:4000/v1", "glm-5.3", "KEYVAR"))
    assert cfg["model"] == "grain/glm-5.3"
    assert cfg["provider"]["grain"]["options"] == {"baseURL": "http://localhost:4000/v1", "apiKey": "{env:KEYVAR}"}
    assert cfg["permission"] == {"*": "allow"} and cfg["share"] == "disabled"
    assert opencode.endpoint({"baseUrl": "http://localhost:4000", "defaultModel": "m", "apiKey": ""}) == ("http://localhost:4000/v1", "none", "m")
    assert opencode.endpoint({"baseUrl": "", "defaultModel": "m"}) is None


def test_sandbox_hosts_and_loopback() -> None:
    assert opencode.allow_hosts("http://localhost:4000") == ["localhost:4000", "*:443"]
    assert opencode.allow_hosts("https://api.fireworks.ai/inference/v1") == ["*:443"]
    p = sandbox.shell_profile(["/tmp"], allow_hosts=["localhost:4000", "*:443", "evil.example:443"], loopback=True)
    net = [l for l in p.splitlines() if "network" in l]
    assert '(allow network-outbound (remote tcp "localhost:4000"))' in net
    assert '(allow network-outbound (remote tcp "*:443"))' in net
    assert not any("evil" in l for l in net)  # only localhost or * are ever written
    assert any("network-bind" in l for l in net)
    assert not any("network-bind" in l for l in sandbox.shell_profile(["/tmp"]).splitlines())


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
    r = box.run("opencode_run", prompt="write hello")
    assert r.get("exit_code") == 0, r
    assert (box.root / "hello.txt").read_text() == "hello from fake opencode\n"
    assert r["session_id"] == "ses_fake" and r["model"] == "grain/glm-5.3" and r["sandboxed"] is True
    assert "[write] hello.txt" in r["output"] and f"Wrote hello.txt in {box.root}" in r["output"]
    assert "write failed" not in r["output"]
    # the reply is tainted: a model with network access wrote the output
    assert box.ctx["tainted"] and "opencode_run:network" in box.ctx["taint_sources"]
    # state lives under the app data dir, keyed by the conversation, never in the home folder
    state = Path(box.db.data_dir) / "opencode" / "c1"
    assert (state / "state" / "seen-config").read_text().startswith('cfg={"$schema"')
    assert not list(Path(os.environ["HOME"]).glob(".local/share/opencode*"))


@needs_seatbelt
def test_outside_every_root_is_refused(box: Box, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opencode, "binary", lambda: "/bin/echo")
    r = box.run("opencode_run", prompt="x", cwd=str(tmp_path))
    assert "outside the folders" in r["error"]


# ---- the chat's working folder ----
def test_working_folder_is_granted_only_when_it_qualifies(fake_home: Path) -> None:
    from personal_os.app import _working_folder
    good = fake_home / "repo"
    good.mkdir()
    assert _working_folder({"workingFolder": str(good)}) == str(good.resolve())
    assert _working_folder({"workingFolder": str(fake_home)}) is None       # the whole home folder is never granted
    assert _working_folder({"workingFolder": str(good / "missing")}) is None
    assert _working_folder({}) is None and _working_folder({"workingFolder": ""}) is None


def test_patch_validates_and_resolves_the_working_folder(fake_home: Path) -> None:
    from fastapi.testclient import TestClient
    from personal_os.app import AUTH_TOKEN, app
    with TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN}) as client:
        cid = client.post("/conversations", json={}).json()["id"]
        repo = fake_home / "proj"
        repo.mkdir()
        r = client.patch(f"/conversations/{cid}", json={"settings": {"workingFolder": str(repo) + "/"}})
        assert r.status_code == 200 and r.json()["settings"]["workingFolder"] == str(repo.resolve())
        assert client.patch(f"/conversations/{cid}", json={"settings": {"workingFolder": str(fake_home)}}).status_code == 422
        assert client.patch(f"/conversations/{cid}", json={"settings": {"workingFolder": str(repo / "nope")}}).status_code == 422
        r = client.patch(f"/conversations/{cid}", json={"settings": {"workingFolder": ""}})
        assert r.status_code == 200 and r.json()["settings"]["workingFolder"] == ""
