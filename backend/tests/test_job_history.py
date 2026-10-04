"""Per-job run history, stats, CSV export and the OS-notification feed. Rows only; no model calls.

Run: backend/.venv/bin/python -m pytest backend/tests/test_job_history.py
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="jobhist-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import job_history  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
NOW = time.time()
SECRET = "Quarterly numbers from alice@example.com: 4.2M"


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    with client:
        task = getattr(appmod.app.state, "jobs_task", None)
        if task is not None:
            task.get_loop().call_soon_threadsafe(task.cancel)
        yield


def add_run(job_id: str, status: str, *, start: float, dur: float | None = 10.0, budget: dict[str, Any] | None = None,
            error: str | None = None, extra: dict[str, Any] | None = None, kind: str = "job") -> str:
    rid = f"{job_id[:6]}-{start}-{status}"
    store.create(rid, None, kind, {"job_id": job_id, "job": "J", **(extra or {})})
    with appmod.db.tx() as c:
        c.execute("UPDATE agent_runs SET started_at=? WHERE run_id=?", (start, rid))
    store.update(rid, status=status, error=error, budget=budget, ended_at=None if dur is None else start + dur)
    return rid


def mkjob(name: str = "hist") -> dict[str, Any]:
    return appmod.jobs.create(f"{name}{time.time()}", "0 * * * *", "p", timezone="UTC", enabled=True)


def test_of_job_filters_orders_and_limits() -> None:
    a, b = mkjob(), mkjob()
    for i in range(5):
        add_run(a["id"], "done", start=NOW - 1000 + i)
    add_run(b["id"], "done", start=NOW - 500)
    add_run(a["id"], "done", start=NOW - 400, kind="chat")  # not a job run
    rows = store.of_job(a["id"], 3)
    assert len(rows) == 3 and all(r["input"]["job_id"] == a["id"] for r in rows)
    assert [r["started_at"] for r in rows] == sorted((r["started_at"] for r in rows), reverse=True)
    assert len(store.of_job(a["id"])) == 5 and len(store.of_job(b["id"])) == 1
    assert len(store.of_job(a["id"], 9999)) == 5  # clamped, not an error


def test_summarize_run_duration_timed_out_and_proposals() -> None:
    j = mkjob()
    rid = add_run(j["id"], "done", start=NOW - 300, dur=240.0,
                  budget={"max_seconds": 240, "seconds": 240.0, "cost": 0.12, "max_cost": 0.2},
                  extra={"attempt": 2, "retry_of": "x", "late": True, "missed_slots": 3})
    p = appmod.proposals.create(run_id=rid, tool="gmail_send", args={"to": "a"}, job_id=j["id"])
    appmod.proposals.create(run_id=rid, tool="gmail_send", args={"to": "b"}, job_id=j["id"])
    appmod.proposals.reject(p["id"])
    row = store.get(rid)
    s = job_history.summarize_run(row, {"tool_result": 4}, appmod.proposals.counts([rid])[rid], "x" * 1000)
    assert s["status"] == "timed_out" and s["duration_s"] == 240.0 and s["cost"] == 0.12
    assert s["attempt"] == 2 and s["retry_of"] == "x" and s["late"] and s["missed_slots"] == 3
    assert s["proposals"] == {"pending": 1, "accepted": 0, "rejected": 1} and s["tool_calls"] == 4
    assert len(s["summary"]) == 400
    # a normal fast run stays 'done'; a live one is 'running' with no duration
    ok = job_history.summarize_run(store.get(add_run(j["id"], "done", start=NOW - 90, budget={"max_seconds": 240, "seconds": 9})))
    live = job_history.summarize_run(store.get(add_run(j["id"], "running", start=NOW - 80, dur=None)))
    assert ok["status"] == "done" and live["status"] == "running" and live["duration_s"] is None and live["cost"] is None


def test_stats_success_rate_and_empty() -> None:
    empty = job_history.stats([])
    assert empty["runs"] == 0 and empty["success_rate"] is None and empty["median_duration_s"] is None
    j = mkjob()
    runs = [add_run(j["id"], st, start=NOW - 100 * (i + 1), dur=d, budget={"cost": 0.1})
            for i, (st, d) in enumerate([("done", 10), ("done", 30), ("error", 20), ("done", 20), ("running", None)])]
    rows = [job_history.summarize_run(store.get(r)) for r in runs]
    st = job_history.stats(rows)
    assert st["runs"] == 4 and st["ok"] == 3 and st["failed"] == 1 and st["success_rate"] == 0.75
    assert st["median_duration_s"] == 20 and st["total_cost"] == pytest.approx(0.4) and st["last_ok_at"] is not None
    body = client.get(f"/jobs/{j['id']}/stats").json()
    assert body["runs"] == 4 and body["success_rate"] == 0.75


def test_runs_route_and_404s() -> None:
    j = mkjob()
    for i in range(3):
        add_run(j["id"], "done", start=NOW - 50 + i)
    body = client.get(f"/jobs/{j['id']}/runs?limit=2").json()
    assert len(body) == 2 and body[0]["started_at"] > body[1]["started_at"]
    for path in ("runs", "stats", "runs.csv"):
        assert client.get(f"/jobs/nope/{path}").status_code == 404


def test_csv_export_escapes_and_has_header() -> None:
    j = mkjob("csv, \"job\"")
    add_run(j["id"], "error", start=NOW - 20, error='bad, "thing"\nsecond line')
    add_run(j["id"], "error", start=NOW - 10, error="=HYPERLINK(\"http://x\")")
    r = client.get(f"/jobs/{j['id']}/runs.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and '"' not in r.headers["content-disposition"].split("filename=")[1].strip('"')
    rows = list(csv.reader(io.StringIO(r.text)))
    assert tuple(rows[0]) == job_history.CSV_COLUMNS and len(rows) == 3
    errs = {row[-1] for row in rows[1:]}
    assert 'bad, "thing"\nsecond line' in errs
    assert "'=HYPERLINK(\"http://x\")" in errs  # no formula injection


def test_notify_filters_redacts_and_caps() -> None:
    j = mkjob("notify")
    appmod.jobs.update(j["id"], {"max_retries": 0})
    t = time.time()
    failed = add_run(j["id"], "error", start=t - 100, error=SECRET)
    done = add_run(j["id"], "done", start=t - 90, extra={"attempt": 1})
    appmod.proposals.create(run_id=done, tool="gmail_send", args={"body": SECRET}, job_id=j["id"])
    old = add_run(j["id"], "error", start=t - 5000, error="old")
    ev = client.get("/inbox/notify", params={"since": t - 3600}).json()
    mine = [e for e in ev if e["id"] in (f"run:{failed}:error", f"run:{done}:done", f"run:{old}:error")]
    kinds = {e["kind"] for e in mine}
    assert kinds == {"job_failed", "job_done_with_proposals"}  # the old one is filtered by `since`
    blob = repr(ev)
    assert SECRET not in blob and "alice" not in blob and "4.2M" not in blob
    # a proposal already announced by its run's 'done' is not announced a second time
    assert not [e for e in ev if e["kind"] == "proposal_pending" and j["name"] in e["title"]]
    # since in the future filters everything
    assert client.get("/inbox/notify", params={"since": t + 3600}).json() == []
    # cap
    k = mkjob("many")
    appmod.jobs.update(k["id"], {"max_retries": 0})
    for i in range(30):
        add_run(k["id"], "error", start=t - 60 + i * 0.01, error="x")
    assert len(client.get("/inbox/notify", params={"since": t - 3600}).json()) == 20


def test_failed_run_that_will_retry_is_not_news_and_pause_is() -> None:
    j = mkjob("retrying")  # max_retries defaults to 1, run is attempt 1 -> a retry is coming
    t = time.time()
    rid = add_run(j["id"], "error", start=t - 30, error="e")
    ev = client.get("/inbox/notify", params={"since": t - 3600}).json()
    assert f"run:{rid}:error" not in [e["id"] for e in ev]
    appmod.jobs.pause(j["id"], "paused after 3 failed runs: e")
    ev = client.get("/inbox/notify", params={"since": t - 3600}).json()
    assert any(e["kind"] == "job_paused" and e["id"].startswith(f"job:{j['id']}:") for e in ev)
    x = mkjob("expiring")
    appmod.jobs.pause(x["id"], "expired")
    ev = client.get("/inbox/notify", params={"since": t - 3600}).json()
    assert [e["body"] for e in ev if e["id"].startswith(f"job:{x['id']}:")] == ["Its schedule expired. Switch it back on to keep it running."]


def test_notify_setting_round_trips() -> None:
    assert client.get("/settings").json()["notifyJobs"] is True
    assert client.put("/settings", json={"notifyJobs": False}).status_code == 200
    assert client.get("/settings").json()["notifyJobs"] is False
    client.put("/settings", json={"notifyJobs": True})
