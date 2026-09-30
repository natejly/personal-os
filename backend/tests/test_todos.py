"""Todo calendar link fields persist through create/update."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.todos import Todos  # noqa: E402


def test_calendar_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        todos = Todos(Database(tmp))
        t = todos.create("Call the dentist", due="2026-10-02")
        assert t["calendar_event_id"] is None
        assert t["calendar_link"] is None
        updated = todos.update(t["id"], {
            "calendar_event_id": "evt_1",
            "calendar_link": "https://calendar.google.com/event?eid=evt_1",
        })
        assert updated is not None
        assert updated["calendar_event_id"] == "evt_1"
        assert "calendar.google.com" in updated["calendar_link"]
        listed = todos.list()
        assert listed[0]["calendar_event_id"] == "evt_1"


if __name__ == "__main__":
    test_calendar_fields()
    print("ok")
