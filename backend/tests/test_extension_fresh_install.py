"""Built-in extensions start off on a fresh install; an install that predates the rule keeps every state it had.

The manifest `enabled` of a built-in is only the default for an id this install has not seen: the ids seen are kept in
extensions-seeded.json in the data directory. An install without the file that already has chats is a legacy install.

FRESH_INSTALL = True

Run: python -m pytest backend/tests/test_extension_fresh_install.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extfresh-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions.host import SEEDED_FILE, BUILTIN_ROOT, ExtensionContext, ExtensionHost, builtin_manifest  # noqa: E402

USER_FACING = ("coding", "claude-code", "telegram", "spaces", "todos", "calendar", "mail", "health")


def test_every_manifest_default_is_off_except_the_google_service() -> None:
    manifests = {p.parent.name: builtin_manifest(p.parent.name) for p in BUILTIN_ROOT.glob("*/extension.json")}
    assert manifests["google"].enabled == "always"
    assert {i for i, m in manifests.items() if m.enabled is not False} == {"google"}


def test_a_fresh_install_has_nothing_on_but_the_service() -> None:
    host = appmod.host
    assert all(not host.extensions[i].enabled() for i in USER_FACING)
    assert host.extensions["google"].enabled()
    assert set(USER_FACING) <= set(appmod.settings()["hiddenViews"])
    assert "extDefaults" not in appmod.db.get_settings() or not appmod.db.get_settings()["extDefaults"]  # no extension default was applied


def test_a_fresh_install_has_no_coding_tools_and_hides_the_sidebar_rows() -> None:
    names = {t["function"]["name"] for t in appmod.toolbox.schemas({n: "on" for n in appmod.toolbox.specs})}
    assert not names & {"todo_list", "calendar_events", "gmail_search", "health_summary", "space_list"}


def test_the_seen_ids_are_recorded_and_the_enable_route_turns_one_on() -> None:
    seen = json.loads((appmod.db.data_dir / SEEDED_FILE).read_text())
    assert set(USER_FACING) <= set(seen)
    from fastapi.testclient import TestClient
    c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    assert c.post("/extensions/todos/enable").json()["enabled"] == ["todos"]
    assert "todos" not in appmod.settings()["hiddenViews"] and appmod.host.extensions["todos"].enabled()
    c.post("/extensions/todos/disable")


def _ctx(db: Database) -> ExtensionContext:
    return ExtensionContext(db=db, hidden_views=lambda: db.get_settings().get("hiddenViews") or (), services={"mcp": lambda: None},
                            settings=db.get_settings, set_settings=db.set_settings, sid=lambda p: p, wsid=lambda p: p, publish=lambda _n, _d: None)


def _db(chats: bool) -> Database:
    db = Database(tempfile.mkdtemp(prefix="extseed-"))
    if chats:
        with db.tx() as c:
            c.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES ('c1', 'old', 'm', 0, 0)")
    return db


def test_an_install_with_chats_and_no_file_keeps_every_built_in_as_it_was() -> None:
    db = _db(chats=True)
    host, ctx = ExtensionHost(), _ctx(db)
    db.set_settings({"hiddenViews": ["health"]})
    for i in ("spaces", "health"):
        host.load_builtin(i, ctx)
    assert db.get_settings()["hiddenViews"] == ["health"]  # nothing added: spaces, on before, stays on
    assert host.extensions["spaces"].enabled() and not host.extensions["health"].enabled()
    assert set(json.loads((db.data_dir / SEEDED_FILE).read_text())) == {"spaces", "health"}


def test_a_built_in_added_after_the_install_was_seeded_starts_off() -> None:
    db = _db(chats=True)
    (db.data_dir / SEEDED_FILE).write_text(json.dumps(["health"]))
    host, ctx = ExtensionHost(), _ctx(db)
    host.load_builtin("health", ctx)
    host.load_builtin("spaces", ctx)  # new to this install
    assert host.extensions["health"].enabled() and not host.extensions["spaces"].enabled()
    assert db.get_settings()["hiddenViews"] == ["spaces"]


def test_an_empty_install_without_the_file_is_fresh() -> None:
    db = _db(chats=False)
    host, ctx = ExtensionHost(), _ctx(db)
    host.load_builtin("health", ctx)
    assert not host.extensions["health"].enabled()
    host.load_builtin("google", ctx)
    assert host.extensions["google"].enabled() and "google" not in db.get_settings()["hiddenViews"]


def test_a_user_who_turned_one_on_keeps_it_across_starts() -> None:
    db = _db(chats=False)
    ctx = _ctx(db)
    ExtensionHost().load_builtin("health", ctx)
    db.set_settings({"hiddenViews": []})  # the user turned it on
    again = ExtensionHost()
    again.load_builtin("health", ctx)
    assert again.extensions["health"].enabled()


def test_a_fresh_database_hides_claude_code_and_coding_and_a_stored_list_is_kept() -> None:
    fresh = _db(chats=False)
    host, ctx = ExtensionHost(), _ctx(fresh)
    for i in ("coding", "claude-code"):
        host.load_builtin(i, ctx)
    assert {"coding", "claude-code"} <= set(fresh.get_settings()["hiddenViews"])
    assert not host.extensions["coding"].enabled() and not host.extensions["claude-code"].enabled()
    stored = _db(chats=False)
    stored.set_settings({"hiddenViews": ["health"]})
    (stored.data_dir / SEEDED_FILE).write_text(json.dumps(["coding", "claude-code"]))  # an install that has seen both: its list wins
    again, ctx2 = ExtensionHost(), _ctx(stored)
    for i in ("coding", "claude-code"):
        again.load_builtin(i, ctx2)
    assert stored.get_settings()["hiddenViews"] == ["health"]
    assert again.extensions["coding"].enabled() and again.extensions["claude-code"].enabled()
