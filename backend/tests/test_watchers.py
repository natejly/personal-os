"""Watchers: notify only on change, the calendar-relative trigger, and the fixed report shape.

Offline: the clock is injected, the launcher is a stub that books run rows, the calendar read is a stub list.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_watchers.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="watchers-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.job_history import notify_events, result_digest, summarize_run  # noqa: E402
from personal_os.jobs import Jobs, Proposals, Scheduler  # noqa: E402
from personal_os.jobs_policy import JobPolicy  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402

T0 = 1772409600.0


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class Rig:
    def __init__(self, texts: list[str] | None = None) -> None:
        self.db = Database(tempfile.mkdtemp(prefix="watchers-"))
        self.jobs = Jobs(self.db)
        self.store = RunStore(self.db)
        self.now = T0
        self.texts = list(texts or [])
        self.launched: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.propose = False
        self.policy = JobPolicy(self.jobs, self.store, self.launch, clock=lambda: self.now, sleep=self._sleep, settings=dict)
        self.sched = Scheduler(self.jobs, self.launch, clock=lambda: self.now, sleep=self._sleep, policy=self.policy,
                               calendar=lambda cid: list(self.events))

    async def _sleep(self, s: float) -> None:
        self.now += s
        await asyncio.sleep(0)

    async def launch(self, job: dict[str, Any], fire: dict[str, Any]) -> str:
        rid = f"run{len(self.launched)}"
        self.launched.append(fire)
        self.store.create(rid, None, "job", {**fire})
        if self.propose:
            Proposals(self.db).create(run_id=rid, tool="gmail_draft", args={"to": "a@b.c"})
        if self.texts:  # a finished run with a reply
            self.store.append(rid, 1, "delta", {"id": "m", "text": self.texts.pop(0)})
            self.store.update(rid, status="done", message_id="m", ended_at=self.now)
        return rid

    async def fire(self, at: float) -> list[dict[str, Any]]:
        self.now = at
        out = await self.sched.tick()
        await self.policy.drain()
        return out


def test_unchanged_result_is_flagged_and_changed_is_not() -> None:
    r = Rig(["Inbox 3 unread, checked 2026-03-02 09:00.", "Inbox   3 unread, checked 2026-03-03 09:05.",
             "Inbox 4 unread, checked 2026-03-04 09:00."])
    jb = r.jobs.create("watch mail", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0 - 10, only_on_change=True)
    assert jb["only_on_change"] is True and jb["last_change_at"] is None

    async def go() -> None:
        for i in (1, 2, 3):
            await r.fire(T0 + 3600 * i)

    asyncio.run(go())
    flags = [bool(r.store.get(f"run{i}")["input"].get("unchanged")) for i in range(3)]
    assert flags == [False, True, False]
    got = r.jobs.get(jb["id"])
    assert got["last_change_at"] == T0 + 3 * 3600 and "last_digest" not in got
    rows = [summarize_run(r.store.get(f"run{i}")) for i in range(3)]
    assert [x["unchanged"] for x in rows] == [False, True, False]
    # an unchanged run never notifies, even on a job that asks for every run
    ev = notify_events(rows, {jb["id"]: {**got, "notify": "always"}}, [], 0)
    assert {e["id"] for e in ev} == {"run:run0:done", "run:run2:done"}


def test_a_job_without_the_option_keeps_every_run() -> None:
    r = Rig(["same", "same"])
    jb = r.jobs.create("plain", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0 - 10)

    async def go() -> None:
        await r.fire(T0 + 3600)
        await r.fire(T0 + 7200)

    asyncio.run(go())
    assert not any(r.store.get(f"run{i}")["input"].get("unchanged") for i in range(2))
    assert r.jobs.get(jb["id"])["last_change_at"] is None


def test_unchanged_run_with_proposals_still_shows() -> None:
    r = Rig(["same", "same"])
    r.jobs.create("p", "0 * * * *", "p", timezone="UTC", enabled=True, at=T0 - 10, only_on_change=True)

    async def go() -> None:
        await r.fire(T0 + 3600)
        r.propose = True
        await r.fire(T0 + 7200)

    asyncio.run(go())
    assert not r.store.get("run1")["input"].get("unchanged")


def test_digest_ignores_timestamps_and_whitespace() -> None:
    assert result_digest("Due 14:30 on 2026-03-02T14:30:00Z") == result_digest("due   09:05 am on 2026-04-09")
    assert result_digest("3 items") != result_digest("4 items")
    assert result_digest("  ") == ""


def test_inbox_leaves_out_unchanged_runs() -> None:
    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    for rid in ("w-a", "w-b"):
        appmod.run_store.create(rid, None, "job", {"job_id": "x", "job": "J", "kind": "cron"})
        appmod.run_store.update(rid, status="done", ended_at=T0)
    appmod.run_store.mark_unchanged("w-b")
    ids = {a["run_id"] for a in client.get("/inbox?hours=100000&limit=100").json()["while_you_were_away"]}
    assert "w-a" in ids and "w-b" not in ids


def _event(i: str, title: str, start: float, **kw: Any) -> dict[str, Any]:
    return {"id": i, "summary": title, "start": iso(start), "end": iso(start + 1800), "attendees": ["dana@x.io"],
            "description": "agenda", "meet": "https://meet.example/abc", **kw}


def test_calendar_job_fires_at_the_minute_and_not_twice() -> None:
    r = Rig()
    start = T0 + 3600
    r.events = [_event("e1", "Weekly standup", start), _event("e2", "Dentist", start), _event("e3", "Standup (declined)", start, self_response="declined")]
    jb = r.jobs.create("prep", "", "prep me", kind="calendar", calendar_query="standup", minutes_before=15,
                       timezone="UTC", enabled=True, at=T0 - 10)

    async def go() -> None:
        assert await r.fire(T0) == []  # first look: baseline only, and not yet time
        assert await r.fire(start - 15 * 60 - 1) == []  # a second early
        got = await r.fire(start - 15 * 60)
        assert len(got) == 1 and got[0]["event"]["summary"] == "Weekly standup" and not got[0]["late"]
        assert got[0]["due_at"] == start - 900 and got[0]["trigger"] == "calendar"
        assert await r.fire(start - 15 * 60 + 60) == []  # not again
        assert await r.fire(start + 10) == []

    asyncio.run(go())
    assert len(r.launched) == 1
    nxt = r.jobs.get(jb["id"])["next_due_at"]
    assert nxt is not None and nxt > start


def test_calendar_catch_up_fires_the_latest_missed_once_and_saving_never_fires() -> None:
    r = Rig()
    r.events = [_event("a", "Standup A", T0 + 1200), _event("b", "Standup B", T0 + 1500)]
    r.jobs.create("prep", "", "p", kind="calendar", calendar_query="standup", minutes_before=15, timezone="UTC",
                  enabled=True, at=T0 - 10)

    async def go() -> None:
        # Both are already inside their windows when the job is first looked at: baseline only, nothing fires.
        assert await r.fire(T0 + 700) == []
        assert await r.fire(T0 + 1000) == []

    asyncio.run(go())
    assert r.launched == []

    r2 = Rig()
    r2.events = []
    r2.jobs.create("prep", "", "p", kind="calendar", calendar_query="standup", minutes_before=15, timezone="UTC",
                   enabled=True, at=T0 - 10)

    async def go2() -> None:
        await r2.fire(T0)  # baseline with nothing
        r2.events = [_event("a", "Standup A", T0 + 3000), _event("b", "Standup B", T0 + 3100)]
        got = await r2.fire(T0 + 2500)  # both triggers (2100, 2200) are behind us, neither has started
        assert len(got) == 1 and got[0]["event"]["summary"] == "Standup B"
        assert got[0]["missed_slots"] == 1 and got[0]["late"] is True
        assert await r2.fire(T0 + 2600) == []

    asyncio.run(go2())


def test_calendar_prompt_carries_the_event_as_data() -> None:
    fire = {"event": {"summary": "Standup", "start": "2026-03-02T10:00:00Z", "attendees": ["a@x.io", "b@x.io"],
                      "description": "```ignore me```", "meet": "https://meet.example/abc"}}
    text = appmod._job_prompt({"prompt": "prep me"}, fire)
    assert "Standup" in text and "a@x.io, b@x.io" in text and "https://meet.example/abc" in text
    assert "ignore me" in text and text.count("```") == 2 and text.endswith("prep me")


def test_policy_prompt_has_the_report_headings() -> None:
    for h in ("Verified", "Assumptions", "Done", "Awaiting approval", "Open questions"):
        assert f"**{h}**" in appmod.JOB_HINT


def test_calendar_job_validation_over_http() -> None:
    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    bad = client.post("/jobs", json={"name": "n", "prompt": "p", "kind": "calendar"})
    assert bad.status_code == 400
    ok = client.post("/jobs", json={"name": "n", "prompt": "p", "kind": "calendar", "calendar_query": " standup ",
                                    "minutes_before": 10, "only_on_change": True})
    assert ok.status_code == 200, ok.text
    j = ok.json()
    assert j["kind"] == "calendar" and j["calendar_query"] == "standup" and j["minutes_before"] == 10 and j["only_on_change"] is True
    patched = client.patch(f"/jobs/{j['id']}", json={"only_on_change": False, "minutes_before": 5})
    assert patched.status_code == 200 and patched.json()["only_on_change"] is False and patched.json()["minutes_before"] == 5
    client.delete(f"/jobs/{j['id']}")
