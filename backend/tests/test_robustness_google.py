"""Failure cases in the Google sync paths: a changed sync target, a todo Google refuses,
a deleted mirror calendar, a flaky token refresh. No network; Google is a small fake.
"""
from __future__ import annotations

import base64
import datetime as dt
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import google as google_mod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.google_store import ReadStore  # noqa: E402
from personal_os.gtasks import TasksSync  # noqa: E402
from personal_os.todocal import TodoCalendarMirror  # noqa: E402
from personal_os.todos import Todos, clean_due  # noqa: E402


class _Settings:
    def __init__(self, **initial: Any) -> None:
        self.data: dict[str, Any] = dict(initial)

    def get(self) -> dict[str, Any]:
        return self.data

    def set(self, patch: dict[str, Any]) -> None:
        self.data.update(patch)


class _Tasks:
    """Google Tasks as one dict per task list."""

    def __init__(self) -> None:
        self.lists: dict[str, dict[str, dict[str, Any]]] = {}
        self.n = 0
        self.refuse_title: str | None = None

    def tasks_all(self, tasklist: str = "@default", updated_min: str | None = None) -> list[dict[str, Any]]:
        return [dict(t) for t in self.lists.setdefault(tasklist, {}).values()]

    def tasks_insert(self, body: dict[str, Any], tasklist: str = "@default") -> dict[str, Any]:
        if body.get("title") == self.refuse_title:
            raise RuntimeError("400 Bad Request")
        self.n += 1
        row = {"id": f"t{self.n}", "updated": f"2026-10-02T00:00:{self.n:02d}Z", **body}
        self.lists.setdefault(tasklist, {})[row["id"]] = row
        return dict(row)

    def tasks_update(self, task_id: str, patch: dict[str, Any], tasklist: str = "@default") -> dict[str, Any]:
        row = self.lists[tasklist][task_id]
        row.update(patch)
        return dict(row)

    def tasks_delete(self, task_id: str, tasklist: str = "@default") -> None:
        if task_id not in self.lists.get(tasklist, {}):
            raise RuntimeError("404 task not found")
        del self.lists[tasklist][task_id]


def _todos() -> Todos:
    return Todos(Database(tempfile.mkdtemp()))


# ---- todos ----
def test_due_is_a_date_or_nothing() -> None:
    assert clean_due("2026-10-14") == "2026-10-14"
    assert clean_due("2026-10-14T09:00:00Z") == "2026-10-14"
    assert clean_due("") is None and clean_due(None) is None
    for bad in ("tomorrow", "2026-13-45", "next friday"):
        with pytest.raises(ValueError):
            clean_due(bad)


def test_a_bad_due_is_refused_at_the_door() -> None:
    todos = _todos()
    with pytest.raises(ValueError):
        todos.create("Call", due="tomorrow")
    td = todos.create("Call", due="2026-10-14")
    with pytest.raises(ValueError):
        todos.update(td["id"], {"due": "soon"})
    assert todos.get(td["id"])["due"] == "2026-10-14"  # type: ignore[index]


# ---- Google Tasks sync ----
def test_changing_the_task_list_does_not_delete_local_todos() -> None:
    todos, g = _todos(), _Tasks()
    settings = _Settings(googleTasksSync={"enabled": True}, googleToken={"email": "me@example.com"})
    sync = TasksSync(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    for title in ("One", "Two", "Three"):
        todos.create(title)
    assert sync.sync_once()["created_remote"] == 3

    sync.set_config({"tasklist": "other"})
    counts = sync.sync_once()

    assert counts["deleted_local"] == 0
    assert sorted(t["title"] for t in todos.list()) == ["One", "Three", "Two"]
    assert len(g.lists["other"]) == 3  # they moved to the new list rather than vanishing


def test_switching_account_does_not_delete_local_todos() -> None:
    todos, g = _todos(), _Tasks()
    settings = _Settings(googleTasksSync={"enabled": True}, googleToken={"email": "me@example.com"})
    sync = TasksSync(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    todos.create("Keep me")
    sync.sync_once()

    g.lists.clear()  # another account: none of the old task ids exist there
    settings.data["googleToken"] = {"email": "other@example.com"}
    counts = sync.sync_once()

    assert counts["deleted_local"] == 0
    assert [t["title"] for t in todos.list()] == ["Keep me"]


def test_same_target_still_deletes_a_todo_whose_task_was_removed() -> None:
    todos, g = _todos(), _Tasks()
    settings = _Settings(googleTasksSync={"enabled": True}, googleToken={"email": "me@example.com"})
    sync = TasksSync(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    todos.create("Done elsewhere")
    sync.sync_once()
    g.lists["@default"].clear()

    assert sync.sync_once()["deleted_local"] == 1
    assert todos.list() == []


def test_one_refused_todo_does_not_block_the_rest() -> None:
    todos, g = _todos(), _Tasks()
    settings = _Settings(googleTasksSync={"enabled": True})
    sync = TasksSync(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    todos.create("Bad")
    todos.create("Good")
    g.refuse_title = "Bad"

    counts = sync.sync_once()

    assert counts["created_remote"] == 1
    assert [t["title"] for t in g.lists["@default"].values()] == ["Good"]
    assert sync.last_error and "Bad" in sync.last_error


def test_a_todo_deleted_during_the_insert_is_not_resurrected() -> None:
    todos, g = _todos(), _Tasks()
    settings = _Settings(googleTasksSync={"enabled": True})
    sync = TasksSync(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    td = todos.create("Changed my mind")
    insert = g.tasks_insert

    def insert_then_user_deletes(body: dict[str, Any], tasklist: str = "@default") -> dict[str, Any]:
        row = insert(body, tasklist)
        todos.delete(td["id"], notify=False)
        return row

    g.tasks_insert = insert_then_user_deletes  # type: ignore[method-assign]
    sync.sync_once()

    assert g.lists["@default"] == {}
    g.tasks_insert = insert  # type: ignore[method-assign]
    assert sync.sync_once()["created_local"] == 0


# ---- calendar mirror ----
class _Calendar:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.calendars = {"cal-1"}
        self.n = 0

    def calendar_ensure(self, summary: str) -> dict[str, Any]:
        self.n += 1
        cid = f"cal-{self.n + 1}"
        self.calendars.add(cid)
        return {"id": cid, "summary": summary, "created": True}

    def calendar_create(self, event: dict[str, Any], calendar_id: str = "primary") -> dict[str, Any]:
        if calendar_id not in self.calendars:
            raise RuntimeError("404 Not Found")
        if event["summary"] == "Refused":
            raise RuntimeError("400 Bad Request")
        self.n += 1
        eid = f"e{self.n}"
        self.events[eid] = {"id": eid, "calendar_id": calendar_id, **event}
        return {**self.events[eid], "link": f"https://cal/{eid}"}

    def calendar_delete(self, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
        self.events.pop(event_id, None)
        return {"deleted": event_id}


def test_one_refused_event_does_not_block_the_mirror() -> None:
    todos, g = _todos(), _Calendar()
    settings = _Settings(googleTodoCalendar={"enabled": True, "calendarId": "cal-1"})
    mirror = TodoCalendarMirror(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    todos.create("Refused", due="2026-10-05")
    todos.create("Fine", due="2026-10-06")

    counts = mirror.sync_once()

    assert counts["created"] == 1
    assert [e["summary"] for e in g.events.values()] == ["Fine"]
    assert mirror.last_error and "Refused" in mirror.last_error


def test_a_deleted_mirror_calendar_is_recreated() -> None:
    todos, g = _todos(), _Calendar()
    settings = _Settings(googleTodoCalendar={"enabled": True, "calendarId": "cal-gone"})
    mirror = TodoCalendarMirror(todos, g, settings.get, settings.set)  # type: ignore[arg-type]
    todos.create("Dentist", due="2026-10-05")

    counts = mirror.sync_once()

    new_id = settings.data["googleTodoCalendar"]["calendarId"]
    assert new_id and new_id != "cal-gone"
    assert counts["created"] == 1
    assert [e["calendar_id"] for e in g.events.values()] == [new_id]


# ---- google.py helpers ----
def test_recipient_names_are_encoded_without_swallowing_the_address() -> None:
    raw = base64.urlsafe_b64decode(google_mod._raw_message("Zoë <a@b.com>, plain@y.com", "Hi", "body")).decode()
    to = next(line for line in raw.splitlines() if line.lower().startswith("to:"))
    assert "<a@b.com>" in to and "plain@y.com" in to
    assert google_mod._address_header("Bob <b@c.com>") == "Bob <b@c.com>"


def test_saved_bodies_are_capped() -> None:
    store = ReadStore(None)
    for i in range(5):
        store.put("gmail-body", f"m{i}", {"id": f"m{i}"}, cap=3)
    assert store.get("gmail-body", "m0") is None and store.get("gmail-body", "m1") is None
    assert store.get("gmail-body", "m4") == {"id": "m4"}
    store.put("gmail-body", "m2", {"id": "m2"}, cap=3)  # re-saved: now the youngest
    store.put("gmail-body", "m5", {"id": "m5"}, cap=3)
    assert store.get("gmail-body", "m2") is not None and store.get("gmail-body", "m3") is None


def test_only_a_refused_refresh_token_asks_for_a_new_sign_in() -> None:
    from google.auth.exceptions import RefreshError, TransportError

    assert google_mod._refresh_rejected(RefreshError("invalid_grant: Token has been expired or revoked."))
    assert not google_mod._refresh_rejected(TransportError("Failed to resolve 'oauth2.googleapis.com'"))
    assert not google_mod._refresh_rejected(RefreshError("Service Unavailable", retryable=True))
    assert not google_mod._refresh_rejected(OSError("network is down"))


class _Get:
    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event

    def get(self, **_kw: Any) -> "_Get":
        return self

    def execute(self) -> dict[str, Any]:
        return self.event


def test_a_new_start_keeps_the_event_length() -> None:
    timed = _Get({"start": {"dateTime": "2026-10-05T14:00:00-04:00"}, "end": {"dateTime": "2026-10-05T17:00:00-04:00"}})
    assert google_mod._end_keeping_length(timed, "primary", "e1", "2026-10-07T09:00:00") == "2026-10-07T12:00:00"
    days = _Get({"start": {"date": "2026-10-05"}, "end": {"date": "2026-10-08"}})
    assert google_mod._end_keeping_length(days, "primary", "e1", "2026-10-10") == "2026-10-13"
    # all-day -> timed (or the reverse) has no length to carry over
    assert google_mod._end_keeping_length(days, "primary", "e1", "2026-10-10T09:00:00") is None
    assert google_mod._end_keeping_length(timed, "primary", "e1", "2026-10-10") is None


def test_a_write_drops_the_saved_copy_of_the_event() -> None:
    g = google_mod.Google.__new__(google_mod.Google)
    g._reads = ReadStore(None)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    g._reads.put("calendar", "primary", {"start": now, "end": now, "synced_at": now, "events": {
        "e1": {"id": "e1"}, "series": {"id": "series"}, "series_20261005T140000Z": {"id": "series_20261005T140000Z"},
    }})

    g._drop_saved_event("primary", "series")
    assert set(g._reads.get("calendar", "primary")["events"]) == {"e1"}
    g._drop_saved_event("primary", "missing")  # nothing to drop is not an error
    g._drop_saved_event("other", "e1")  # nor is a calendar with no snapshot
    assert set(g._reads.get("calendar", "primary")["events"]) == {"e1"}
