"""The user's view of what is running: shell jobs (/shell/jobs) and sandbox containers (/sandboxes), plus the sandbox
settings' validation. No docker is needed: the sandbox manager gets the in-memory simulator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="runningviews-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, llm, microvm, shell  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

from test_microvm_checkpoints import FakeDocker, cp  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


@pytest.fixture
def orphan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A `sleep` in its own process group, recorded in a state file the way an earlier app run left it."""
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    state = tmp_path / "shell_jobs.json"
    state.write_text(json.dumps([{"job_id": "orph1", "pid": p.pid, "pgid": p.pid, "cwd": "/tmp", "run_id": None,
                                  "conversation_id": "c1", "command": "sleep 30", "started": time.time() - 60}]))
    jobs = shell.ShellJobs(state)
    monkeypatch.setattr(appmod.toolbox, "shell", jobs)
    yield p
    p.kill()
    p.wait()


def test_orphaned_job_is_listed_tailed_and_killable(orphan: Any) -> None:
    rows = client.get("/shell/jobs").json()["jobs"]
    assert [(r["job_id"], r["status"], r["command"]) for r in rows] == [("orph1", "orphaned", "sleep 30")]
    t = client.get("/shell/jobs/orph1/tail").json()
    assert t["output"] == "" and "earlier run" in t["note"]
    r = client.post("/shell/jobs/orph1/kill")
    assert r.status_code == 200 and r.json()["status"] == "killed"
    assert orphan.wait(timeout=5) is not None  # the process group is gone
    assert client.get("/shell/jobs").json()["jobs"][0]["status"] == "killed"


def test_unknown_job_is_404(orphan: Any) -> None:
    assert client.get("/shell/jobs/nope/tail").status_code == 404
    assert client.post("/shell/jobs/nope/kill").status_code == 404


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    d = FakeDocker()
    sb = microvm.Sandboxes(lambda: {"sandboxNetwork": False}, runner=d)
    monkeypatch.setattr(microvm.shutil, "which", lambda b: "/usr/local/bin/" + b)
    monkeypatch.setattr(appmod, "sandboxes", sb)
    return d


def test_sandboxes_list_with_titles_and_reset_through_the_route(docker: FakeDocker) -> None:
    conv = client.post("/conversations", json={"title": "Data crunch"}).json()
    sb = appmod.sandboxes
    sb.ensure(conv["id"])
    sb.checkpoint(conv["id"], "clean")
    body = client.get("/sandboxes").json()
    assert body["available"] is True and body["runtime"] == "docker" and body["reason"] == ""
    (item,) = body["items"]
    assert item["conversation_id"] == conv["id"] and item["title"] == "Data crunch" and item["checkpoints"] == ["clean"]
    r = client.post(f"/sandboxes/{conv['id']}/reset")
    assert r.status_code == 200 and r.json()["reset"] is True
    assert docker.containers == {} and docker.images == {}
    assert client.get("/sandboxes").json()["items"] == []
    assert client.post("/sandboxes/pos-sbx-;rm/reset").status_code == 400


def test_sandboxes_report_why_they_are_unavailable(docker: FakeDocker) -> None:
    appmod.sandboxes._run = lambda argv, **kw: cp(1, "", "Cannot connect to the Docker daemon")  # type: ignore[assignment]
    body = client.get("/sandboxes").json()
    assert body["available"] is False and body["items"] == [] and "Cannot connect" in body["reason"]
    assert client.post("/sandboxes/x/reset").status_code == 409


def test_settings_validate_the_sandbox_image_and_runtime() -> None:
    try:
        for k, bad in (("sandboxImage", "; rm -rf"), ("sandboxImage", "--privileged"), ("sandboxImage", "a b"),
                       ("sandboxRuntime", "bash"), ("sandboxRuntime", "/bin/sh"), ("sandboxNetwork", "yes"),
                       ("sandboxKeepDays", -1)):
            assert client.put("/settings", json={k: bad}).status_code == 422, (k, bad)
        r = client.put("/settings", json={"sandboxImage": "ghcr.io/acme/tools:1.2", "sandboxRuntime": "podman",
                                          "sandboxNetwork": "proxy", "sandboxKeepDays": 30})
        assert r.status_code == 200
        got = r.json()
        assert (got["sandboxImage"], got["sandboxRuntime"], got["sandboxNetwork"], got["sandboxKeepDays"]) == \
            ("ghcr.io/acme/tools:1.2", "podman", "proxy", 30)
    finally:
        client.put("/settings", json={k: llm.DEFAULT_SETTINGS[k] for k in
                                      ("sandboxImage", "sandboxRuntime", "sandboxNetwork", "sandboxKeepDays")})
