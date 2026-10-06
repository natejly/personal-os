"""Meeting slots into a Gmail draft (event only after confirm) and local mail snooze. Offline.

Run: uv run --project backend --with pytest pytest backend/tests/test_mail_slots_snooze.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="slots-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, llm, mailwatch as mw, tools  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.modules import ModuleContext  # noqa: E402
from personal_os.modules.mailwatch import MailWatchModule  # noqa: E402

NY = "America/New_York"
# Friday 2026-10-02 09:00 New York (EDT, UTC-4)
NOW = dt.datetime(2026, 10, 2, 13, 0, tzinfo=dt.timezone.utc)


class _FrozenDt:
    """tools.dt with datetime.now() pinned; everything else is the real module."""
    class datetime(dt.datetime):  # noqa: N801
        @classmethod
        def now(cls, tz: Any = None) -> dt.datetime:
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    def __getattr__(self, name: str) -> Any:
        return getattr(dt, name)


def _setup() -> dict[str, mock.Mock]:
    g = appmod.toolbox.google
    busy = [{"id": "b1", "summary": "Busy", "start": "2026-10-02T14:00:00Z", "end": "2026-10-02T15:00:00Z", "all_day": False, "transparency": "opaque"}]
    m = {k: mock.Mock(return_value=v) for k, v in {
        "calendar_events": busy, "gmail_send": {"sent": "x"}, "gmail_modify": {}, "calendar_create": {"id": "ev1"},
        "gmail_draft": {"draft_id": "d1"}}.items()}
    for k, v in m.items():
        setattr(g, k, v)
    return m


def _call(**kw: Any) -> Any:
    args = {"to": "a@x.com", "subject": "Catch up", "duration_minutes": 30, "window_start": "2026-10-02", "window_end": "2026-10-02",
            "timezone": NY, **kw}
    with mock.patch.object(tools, "dt", _FrozenDt()):
        return asyncio.run(appmod.toolbox.specs["propose_times_draft"].fn({}, **args))


def test_slots_fill_a_draft_and_nothing_else_happens() -> None:
    m = _setup()
    out = _call()
    assert out["slots"]
    body = m["gmail_draft"].call_args[0][2]
    busy0, busy1 = dt.datetime(2026, 10, 2, 10), dt.datetime(2026, 10, 2, 11)
    for s in out["slots"]:
        a, b = dt.datetime.fromisoformat(s["start"]).replace(tzinfo=None), dt.datetime.fromisoformat(s["end"]).replace(tzinfo=None)
        assert dt.time(9) <= a.time() and b.time() <= dt.time(18) and a.weekday() < 5
        assert b <= busy0 or a >= busy1, "no overlap with the 10-11 busy block"
        assert s["start"].replace("T", " ") in body
    assert m["gmail_send"].call_count == 0 and m["calendar_create"].call_count == 0 and "event" not in out


def test_confirm_creates_one_event_and_still_never_sends() -> None:
    first = _call()["slots"][0]
    m = _setup()
    out = _call(confirm=True, chosen_start=first["start"])
    assert m["calendar_create"].call_count == 1 and out["event"]
    fields = m["calendar_create"].call_args[0][0]
    assert str(fields["start"]).startswith(first["start"][:16]) and str(fields["end"]).startswith(first["end"][:16])
    assert m["gmail_send"].call_count == 0


def test_snoozed_thread_is_hidden_until_its_time() -> None:
    m = _setup()
    clock = {"now": dt.datetime(2026, 10, 2, 19, 0, tzinfo=dt.timezone.utc)}  # 15:00 New York
    ctx = ModuleContext(db=Database(tempfile.mkdtemp()), settings=lambda: dict(llm.DEFAULT_SETTINGS), set_settings=lambda _p: None,
                        google=None, sid=lambda p: p, wsid=lambda p: p)
    mod = MailWatchModule(ctx, clock=lambda: clock["now"])
    rows = []
    for tid in ("a", "b"):
        t = {"thread_id": tid, "subject": tid, "messages": [{"id": tid, "from": "al@y.com", "to": "me@x.com", "date": "2026-10-01T10:00:00+00:00",
                                                              "snippet": "Can you?", "labels": []}]}
        rows.append((t, mw.classify(t, "me@x.com", clock["now"])))
    mod.store.refresh(rows)
    app = FastAPI()
    app.include_router(mod.router())
    c = TestClient(app)
    assert c.post("/mail/threads/a/snooze", json={"until": "2026-10-03T08:00:00-04:00"}).status_code == 200
    ids = lambda: sorted(t["thread_id"] for t in c.get("/mail/watch").json()["threads"])  # noqa: E731
    assert ids() == ["b"] and c.get("/mail/snoozed").json()["thread_ids"] == ["a"]
    clock["now"] = dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.timezone.utc)  # 08:00 New York
    assert ids() == ["a", "b"] and c.get("/mail/snoozed").json()["thread_ids"] == []
    assert all(m[k].call_count == 0 for k in ("gmail_send", "gmail_modify", "calendar_create"))
