"""Human names for ids: the lookup, the write tools' echoed `item`, and the /labels route.

Run: PERSONAL_OS_TEST_CHILD=1 uv run pytest tests/test_labels.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="labels-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod, labels  # noqa: E402

EVENT = {"id": "evt1", "summary": "Robotics midterm study", "start": "2026-10-12T20:30:00", "end": "2026-10-12T22:00:00"}
MAIL = {"id": "m1", "subject": "Re: offer", "from": "Jack Smith <jack@x.com>", "date": "2026-10-11T09:00:00+00:00"}
WHEN = "Robotics midterm study · Mon Oct 12, 8:30 – 10:00 PM"


class _Reads:
    def get(self, ns: str, key: str) -> Any:
        return None


def _boom(*a: Any, **k: Any) -> Any:
    raise RuntimeError("not found")


def _tb() -> Any:
    g = SimpleNamespace(_reads=_Reads(), calendar_get=lambda e, c: EVENT if e == "evt1" else _boom(),
                        gmail_get=lambda m: MAIL if m == "m1" else _boom(),
                        tasks_get=lambda t, l: {"title": "Pay rent", "due": "2026-10-15T00:00:00.000Z"} if t == "t1" else _boom(),
                        calendars=lambda: [])
    return SimpleNamespace(google=g, modules=[], docs=SimpleNamespace(find=lambda k: {"title": "Notes"} if k == "d1" else None))


def test_known_ids_get_human_labels() -> None:
    tb = _tb()
    assert labels.lookup(tb, "event", "evt1") == WHEN
    assert labels.lookup(tb, "message", "m1") == "Re: offer · Jack Smith · Oct 11"
    assert labels.lookup(tb, "task", "t1") == "Pay rent · due Oct 15"
    assert labels.lookup(tb, "doc", "d1") == "Notes"


def test_unknown_ids_fall_back_to_none() -> None:
    tb = _tb()
    assert labels.lookup(tb, "event", "nope") is None and labels.lookup(tb, "doc", "zzz") is None
    assert labels.for_call(tb, "web_search", {"query": "x"}) is None
    assert labels.for_call(tb, "calendar_delete", {"event_id": "evt1"}) == WHEN


def test_write_tools_echo_the_item_they_touched() -> None:
    g = appmod.toolbox.google
    real = (g.calendar_get, g.calendar_delete)
    g.calendar_get = lambda e, c="primary": EVENT  # type: ignore[method-assign]
    g.calendar_delete = lambda *a, **k: {"deleted": "evt1"}  # type: ignore[method-assign]
    try:
        out = asyncio.run(appmod.toolbox.call("calendar_delete", {"event_id": "evt1"}, {"project_id": None}))
    finally:
        g.calendar_get, g.calendar_delete = real  # type: ignore[method-assign]
    assert out["item"] == WHEN


def test_the_labels_route_resolves_or_returns_null() -> None:
    from fastapi.testclient import TestClient
    g = appmod.toolbox.google
    real = g.calendar_get
    g.calendar_get = lambda e, c="primary": EVENT if e == "evt1" else _boom()  # type: ignore[method-assign]
    try:
        c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
        assert c.get("/labels", params={"kind": "event", "id": "evt1"}).json()["label"] == WHEN
        assert c.get("/labels", params={"kind": "event", "id": "nope"}).json()["label"] is None
    finally:
        g.calendar_get = real  # type: ignore[method-assign]
