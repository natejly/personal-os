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


def test_structure() -> None:
    from datetime import date
    from personal_os import todo_rules
    with tempfile.TemporaryDirectory() as tmp:
        todos = Todos(Database(tmp))
        a = todos.create("Write report", tags=["Work", "#work", "q4"])
        assert a["tags"] == ["work", "q4"]
        b = todos.create("Outline", parent_id=a["id"])
        c = todos.create("Gather data", tags="work")
        d = todos.create("Unrelated")
        # tag filter
        assert {t["id"] for t in todos.list(tag="work")} == {a["id"], c["id"]}
        assert [t["id"] for t in todos.list(tag="work,q4")] == [a["id"]]
        # nesting: subtask follows parent; completing the parent leaves it intact
        order = [t["id"] for t in Todos.nest(todos.list())]
        assert order.index(b["id"]) == order.index(a["id"]) + 1
        todos.update(a["id"], {"done": True})
        assert todos.get(b["id"])["parent_id"] == a["id"]
        # parent cycles and self-parent rejected
        try:
            todos.update(a["id"], {"parent_id": b["id"]})
            raise AssertionError("cycle accepted")
        except ValueError:
            pass
        # dependencies
        todos.update(d["id"], {"depends_on": [c["id"]]})
        d2, c2 = todos.get(d["id"]), todos.get(c["id"])
        assert (d2["blocked_count"], c2["blocking_count"]) == (1, 1)
        today = date.today()
        assert todo_rules.urgency_of(d2, today) < todo_rules.urgency_of({**d2, "blocked_count": 0}, today)
        try:
            todos.update(c["id"], {"depends_on": [d["id"]]})
            raise AssertionError("dependency cycle accepted")
        except ValueError:
            pass
        todos.update(c["id"], {"done": True})  # blocker finished: no longer blocked
        assert todos.get(d["id"])["blocked_count"] == 0
        # saved filters
        f = todos.save_filter("Work", {"tag": "work", "junk": 1})
        assert todos.filters() == [f] and "junk" not in f
        todos.delete_filter(f["id"])
        assert todos.filters() == []


if __name__ == "__main__":
    test_calendar_fields()
    test_structure()
    print("ok")
