"""The user's view of what is running: shell jobs (/shell/jobs)."""
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

from personal_os import app as appmod, llm, shell  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

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
