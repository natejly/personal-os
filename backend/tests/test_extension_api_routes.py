"""What the app does with the notes, slash commands and trash kinds extensions register (design doc 3.4): the Agent inbox lists
an extension's cards and notifications, the composer's menu lists its commands, the trash lists its kind.

Run: python -m pytest backend/tests/test_extension_api_routes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extapi-routes-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, db, host  # noqa: E402
from personal_os.extensions.notes import ExtNotes, InboxItem  # noqa: E402
from personal_os.extensions.trashkinds import TrashHandler  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def test_an_inbox_card_counts_as_needing_you_until_dismissed() -> None:
    before = client.get("/inbox").json()["counts"]["needs_you"]
    note_id = ExtNotes(db).add("demo", InboxItem("Backup is stale", "Last ran 9 days ago"), inbox=True)
    box = client.get("/inbox").json()
    assert [n["id"] for n in box["needs_you"]["notes"]] == [note_id] and box["counts"]["needs_you"] == before + 1
    assert client.post(f"/inbox/notes/{note_id}/dismiss").json() == {"ok": True}
    box = client.get("/inbox").json()
    assert box["needs_you"]["notes"] == [] and box["counts"]["needs_you"] == before
    assert client.post(f"/inbox/notes/{note_id}/dismiss").status_code == 404


def test_a_notification_reaches_the_notify_feed_after_the_renderers_last_look() -> None:
    ExtNotes(db).add("demo", InboxItem("Sync finished", "42 items", "run:c1"), inbox=False)
    events = [e for e in client.get("/inbox/notify?since=0").json() if e["kind"] == "ext_note"]
    assert [(e["title"], e["body"], e["target"]) for e in events] == [("Sync finished", "42 items", "run:c1")]
    assert client.get(f"/inbox/notify?since={time.time() + 1}").json() == []


def test_a_registered_command_is_listed_for_the_composer_and_follows_its_extension() -> None:
    on = [True]
    registration = host.slash.register("demo", "zzdemo", lambda args: "x", "does a demo thing", lambda: on[0])
    try:
        assert {"name": "zzdemo", "hint": "does a demo thing", "owner": "demo"} in client.get("/commands/registered").json()
        on[0] = False
        assert all(c["name"] != "zzdemo" for c in client.get("/commands/registered").json())
    finally:
        registration.dispose()


def test_an_extensions_trash_kind_is_listed_restored_and_erased_through_the_routes() -> None:
    rows = {"r1": {"id": "r1", "title": "Demo row", "deleted_at": time.time()}, "r2": {"id": "r2", "title": "Other row", "deleted_at": time.time()}}
    registration = host.trash_kinds.register("demo:row", TrashHandler(
        "Demo rows", lambda: list(rows.values()), lambda i: rows.pop(i, None) is not None, lambda i: rows.pop(i, None) is not None), lambda: True)
    try:
        group = next(g for g in client.get("/trash").json()["extensions"] if g["kind"] == "demo:row")
        assert group["label"] == "Demo rows" and {i["id"] for i in group["items"]} == {"r1", "r2"}
        assert client.post("/trash/demo:row/r1/restore").json()["ok"]
        assert client.post("/trash/demo:row/r1/restore").status_code == 404
        assert client.delete("/trash/demo:row/r2").json() == {"ok": True}
        assert client.delete("/trash/demo:row/r2").status_code == 404
        assert client.delete("/trash/demo:nothing/x").status_code == 404
    finally:
        registration.dispose()


def test_recap_facts_list_the_calendar_before_the_unread_mail() -> None:
    ids = list(host.extensions)
    assert ids.index("calendar") < ids.index("mail")
