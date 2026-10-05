"""The daily digest: assembly as a pure function, the once-a-day guard, and the quiet inbox row.

Run: backend/.venv/bin/python -m pytest backend/tests/test_digest.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="digest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import digest, job_history  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402

MIC = ("Microphone not granted, so meetings will not record.", {"label": "Meetings settings", "settings": "meetings"})


def at(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


def test_assemble_lists_meetings_activity_and_gaps_with_one_link_each() -> None:
    body, links = digest.assemble(recorded=2, notes_pending=1,
                                  apps={"Editor": 7200, "Mail": 1800, "(private)": 600, "Music": 60, "Notes": 30},
                                  gaps=[MIC])
    lines = body.split("\n\n")
    assert lines[0] == "**Meetings:** 2 recorded, 1 set of notes waiting for review."
    assert lines[1] == "**Activity:** 2.7h in apps. Most time: Editor 2.0h, Mail 30m, Music 1m."
    assert lines[2] == "**Setup:** " + MIC[0]
    assert links == [MIC[1]]


def test_nothing_to_say_is_an_empty_body() -> None:
    assert digest.assemble(recorded=0, notes_pending=0, apps={"Editor": 20}, gaps=[]) == ("", [])


def test_due_once_per_day_after_the_hour_and_never_on_a_relaunch() -> None:
    assert not digest.due(at(5, 7, 59), 8, None)
    assert digest.due(at(5, 8), 8, None)
    assert not digest.due(at(5, 18), 8, at(5, 8, 5))       # already written today
    assert not digest.due(at(6, 8), 8, at(5, 14))          # late yesterday: within the 20 h gap
    assert digest.due(at(6, 10), 8, at(5, 14))
    assert digest.due(at(6, 8, 10), 8, at(5, 8, 5))


def test_config_defaults_and_clamps() -> None:
    assert digest.config({}) == {"enabled": True, "hour": 8}
    assert digest.config({"digest": {"enabled": False, "hour": 99}}) == {"enabled": False, "hour": 23}


def test_written_row_reads_back_as_a_silent_finished_job_run() -> None:
    store = RunStore(Database(tempfile.mkdtemp(prefix="dg-")))
    rid = digest.write(store, "**Setup:** x", [MIC[1]])
    row = store.get(rid)
    assert row["status"] == "done" and row["kind"] == "job" and row["input"]["links"] == [MIC[1]]
    assert store.transcript(rid, rid)[0] == "**Setup:** x"
    assert digest.last_at(store, row["started_at"] + 1) == row["started_at"]
    summary = job_history.summarize_run(row, None, {})
    assert job_history.notify_events([summary], {}, [], since=0) == []  # never an OS notification


def test_inbox_lists_the_digest_once() -> None:
    from fastapi.testclient import TestClient

    from personal_os import app as appmod
    appmod.db.set_settings({"digest": {"enabled": True, "hour": 0}})
    appmod._digest_checked = 0.0
    orig = appmod._digest_gaps
    appmod._digest_gaps = lambda: [MIC]
    try:
        c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
        rows = [r for r in c.get("/inbox").json()["while_you_were_away"] if r["kind"] == "digest"]
        assert len(rows) == 1 and not rows[0]["seen"] and rows[0]["links"] == [MIC[1]]
        assert MIC[0] in rows[0]["summary"]
        appmod._digest_checked = 0.0
        again = [r for r in c.get("/inbox").json()["while_you_were_away"] if r["kind"] == "digest"]
        assert len(again) == 1
        assert c.get("/inbox/notify").json() == []
        appmod.db.set_settings({"digest": {"enabled": False, "hour": 0}})
    finally:
        appmod._digest_gaps = orig
