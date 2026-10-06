"""The promotion ladder: a reply becomes a five-heading skill candidate, a test run of a job is labelled and leaves the
schedule alone, and a job keeps its newest 20 run records.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p03_ladder.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ladder-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, learn  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
HEADINGS = ("Steps", "Decision rules", "Failure handling", "Output", "Boundaries")


def _job() -> dict[str, Any]:
    return appmod.jobs.create("Weekly brief", "0 9 * * 1", "Summarise the week.", enabled=False)


def test_induced_procedure_always_has_the_five_headings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*_a: Any, **_k: Any) -> str:  # the model forgot every heading but the steps
        return '{"name": "Weekly brief", "description": "When asked for a brief", "procedure": "Steps:\\n1. Read the inbox.\\n2. Write the brief down."}'

    monkeypatch.setattr(learn.llm, "complete", fake)
    conv = appmod.convos.create(None, "chat", "m")
    appmod.convos.add_message(conv["id"], "user", "Write me a brief of the week from my inbox please")
    reply = appmod.convos.add_message(conv["id"], "assistant", "Here is your brief: three threads need an answer.")
    r = client.post(f"/conversations/{conv['id']}/skills/induce", json={"message_id": reply["id"]})
    cand = r.json()["candidate"]
    assert cand["status"] == "candidate"
    for h in HEADINGS:
        assert h in cand["procedure"], h
    assert learn.with_headings("Just do it, then 1. stop.").startswith("Steps:")


def test_test_run_is_labelled_and_leaves_the_schedule_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job()
    fires: list[dict[str, Any]] = []

    async def launch(j: dict[str, Any], fire: dict[str, Any]) -> str | None:
        fires.append(fire)
        rid = f"run-{len(fires)}"
        appmod.run_store.create(rid, None, "job", fire)
        appmod.run_store.update(rid, status="done", ended_at=time.time())
        return rid

    monkeypatch.setattr(appmod, "_launch_job", launch)
    assert client.post(f"/jobs/{job['id']}/run?test=1").json()["ok"]
    assert client.post(f"/jobs/{job['id']}/run").json()["ok"]
    assert fires[0]["test"] and fires[0]["manual"] and "test" not in fires[1]
    after = appmod.jobs.get(job["id"])
    assert after["next_due_at"] == job["next_due_at"] and after["consecutive_failures"] == 0 and not after["enabled"]
    runs = {r["run_id"]: r for r in client.get(f"/jobs/{job['id']}/runs").json()}
    assert runs["run-1"]["test"] is True and runs["run-2"]["test"] is False


def test_a_job_keeps_its_newest_20_runs() -> None:
    job, other = _job(), _job()
    for i in range(25):
        rid = f"keep-{i}"
        appmod.run_store.create(rid, None, "job", {"job_id": job["id"]})
        appmod.run_store.update(rid, status="done", ended_at=time.time())
    appmod.run_store.create("other-1", None, "job", {"job_id": other["id"]})
    runs = appmod.run_store.of_job(job["id"], 200)
    assert len(runs) == 20
    assert {r["run_id"] for r in runs} == {f"keep-{i}" for i in range(5, 25)}
    assert len(appmod.run_store.of_job(other["id"], 200)) == 1
