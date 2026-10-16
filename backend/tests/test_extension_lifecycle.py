"""Extension lifecycle events, settings namespace, migration ledger and export include list (design doc 3.1, 5.1).

Run: python -m pytest backend/tests/test_extension_lifecycle.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extlife-"))
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import backups, migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions import host as host_mod  # noqa: E402
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError  # noqa: E402

BASE = {"id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1}


def manifest(**over: Any) -> Manifest:
    return Manifest.from_dict({**BASE, **over})


def declared(*hooks: str, ext_id: str = "demo", **over: Any) -> Manifest:
    return manifest(id=ext_id, hooks={h: {} for h in hooks}, **over)


def client() -> TestClient:
    return TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def wait_for(cond: Any, seconds: float = 3.0) -> None:
    end = time.time() + seconds
    while not cond():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


# ---- app.start / app.stop ----
def test_app_start_runs_after_the_module_starts_and_app_stop_before_it_stops_in_reverse_order() -> None:
    log: list[str] = []

    def module(key: str) -> Any:
        class M:
            label = key

            def enabled(self) -> bool:
                return True

            async def start(self) -> None:
                log.append(f"start {key}")

            async def stop(self) -> None:
                log.append(f"stop {key}")
        M.key = key  # type: ignore[attr-defined]
        return M()

    host = ExtensionHost()
    for key in ("a", "b"):
        host._add(declared("app.start", "app.stop", ext_id=key), module(key))
        host.extensions[key].on("app.start", lambda _p, k=key: log.append(f"on_start {k}"))
        host.extensions[key].on("app.stop", lambda _p, k=key: log.append(f"on_stop {k}"))
    asyncio.run(host.start())
    asyncio.run(host.stop())
    assert log == ["start a", "on_start a", "start b", "on_start b", "on_stop b", "stop b", "on_stop a", "stop a"]


def test_a_failing_app_start_handler_does_not_stop_the_others_starting() -> None:
    host, seen = ExtensionHost(), []

    def boom(_p: Any) -> None:
        raise RuntimeError("no")
    for key, fn in (("a", boom), ("b", lambda _p: seen.append("b"))):
        host._add(declared("app.start", ext_id=key)).on("app.start", fn)
    asyncio.run(host.start())
    assert seen == ["b"]


def test_app_stop_handlers_get_a_budget_then_are_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_mod, "STOP_BUDGET_S", 0.05)
    host, state = ExtensionHost(), {"cancelled": False, "later": False}
    slow, later = declared("app.stop", ext_id="slow"), declared("app.stop", ext_id="later")

    async def hang(_p: Any) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
    host._add(later).on("app.stop", lambda _p: state.update(later=True))
    host._add(slow).on("app.stop", hang, timeout_ms=60_000)
    started = time.monotonic()
    asyncio.run(host.stop())
    assert time.monotonic() - started < 1
    assert state == {"cancelled": True, "later": True}


def test_stopping_disposes_the_subscriptions() -> None:
    host, seen = ExtensionHost(), []
    host._add(declared("app.start")).on("app.start", seen.append)
    asyncio.run(host.start())
    asyncio.run(host.stop())
    asyncio.run(host.bus.emit("app.start", {}))
    assert seen == [{}]


# ---- fire: emit from any thread without waiting ----
def test_fire_does_nothing_before_the_loop_runs() -> None:
    host, seen = ExtensionHost(), []
    host._add(declared("chat.created")).on("chat.created", seen.append)
    host.fire("chat.created", {"id": "c"})
    assert seen == []


def test_fire_from_a_thread_returns_at_once_and_a_slow_handler_finishes_later() -> None:
    host, done = ExtensionHost(), threading.Event()

    def slow(_p: Any) -> None:
        time.sleep(0.2)
        done.set()
    host._add(declared("chat.created")).on("chat.created", slow)

    async def main() -> float:
        async with host.lifespan(None):  # type: ignore[arg-type]
            t0 = time.monotonic()
            await asyncio.to_thread(host.fire, "chat.created", {"id": "c"})
            fired_in = time.monotonic() - t0
            assert not done.is_set()
            await asyncio.to_thread(done.wait, 3)
            return fired_in
    assert asyncio.run(main()) < 0.15
    assert done.is_set()


def test_pending_fired_tasks_are_cancelled_at_shutdown() -> None:
    host, state = ExtensionHost(), {"cancelled": False}

    async def hang(_p: Any) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
    host._add(declared("chat.created")).on("chat.created", hang, timeout_ms=60_000)

    async def main() -> None:
        async with host.lifespan(None):  # type: ignore[arg-type]
            host.fire("chat.created", {})
            await asyncio.sleep(0.05)
    asyncio.run(main())
    assert state["cancelled"]


# ---- ext.changed ----
def test_ext_changed_fires_once_per_enabled_flip() -> None:
    class M:
        key, label, on = "mod", "Mod", True

        def enabled(self) -> bool:
            return self.on
    host, seen, m = ExtensionHost(), [], M()
    host._add(declared("ext.changed", ext_id="watch")).on("ext.changed", seen.append)
    host.add_module(m)  # type: ignore[arg-type]

    async def main() -> None:
        async with host.lifespan(None):  # type: ignore[arg-type]
            host.refresh_enabled()
            m.on = False
            host.refresh_enabled()
            host.refresh_enabled()
            m.on = True
            host.refresh_enabled()
            await asyncio.sleep(0.1)
    asyncio.run(main())
    assert seen == [{"id": "mod", "enabled": False}, {"id": "mod", "enabled": True}]


# ---- settings namespace ----
def test_manifest_settings_and_data_are_validated() -> None:
    m = manifest(settings={"token": {"type": "secret"}, "limit": {"type": "integer"}}, data={"tables": ["demo_t"]})
    assert m.settings["token"] == {"type": "secret"} and m.data["tables"] == ["demo_t"]
    for bad in ({"settings": {"x": {"type": "object"}}}, {"settings": {"x": "string"}}, {"settings": {"a.b": {"type": "string"}}},
                {"settings": {"x": {"type": "string", "default": "y"}}}, {"data": {"tables": ["Bad Name"]}},
                {"data": {"migrations": 3}}, {"data": {"migrations": "../x"}}, {"data": {"migrations": "/etc"}}):
        with pytest.raises(ManifestError):
            manifest(**bad)


def test_check_setting_types() -> None:
    host = ExtensionHost()
    host._add(manifest(settings={"s": {"type": "string"}, "n": {"type": "number"}, "i": {"type": "integer"},
                                 "b": {"type": "boolean"}, "k": {"type": "secret"}}))
    assert host.setting_type("ext.demo.s") == "string"
    assert [host.setting_type(k) for k in ("ext.demo.nope", "ext.other.s", "ext.demo", "demo.s", "s")] == [None] * 5
    ok = {"s": "x", "n": 1.5, "i": 3, "b": False, "k": None}
    for name, value in ok.items():
        assert host.check_setting(f"ext.demo.{name}", value) == value
    for name, value in {"s": 1, "n": True, "i": 1.5, "b": 0, "k": 5, "n2": 1}.items():
        if name != "n2":
            with pytest.raises(ValueError):
                host.check_setting(f"ext.demo.{name}", value)
    with pytest.raises(ValueError):
        host.check_setting("ext.demo.n", float("inf"))
    assert host.secret_setting_keys() == ["ext.demo.k"]


@pytest.fixture()
def demo_extension():
    """An extension with settings and lifecycle subscriptions added to the app's own host for one test."""
    scope = appmod.host._add(declared("settings.changed", "chat.created", "chat.deleted", "project.created", "project.deleted",
                                      ext_id="demo", settings={"label": {"type": "string"}, "limit": {"type": "integer"},
                                                               "token": {"type": "secret"}}))
    events: list[tuple[str, Any]] = []
    for hook in ("settings.changed", "chat.created", "chat.deleted", "project.created", "project.deleted"):
        scope.on(hook, lambda p, h=hook: events.append((h, p)))
    yield events
    scope.disposables.dispose()
    del appmod.host.extensions["demo"]
    del appmod.host._enabled["demo"]
    appmod.db.secrets.delete("ext.demo.token")
    appmod.db.set_settings({"ext.demo.label": "", "ext.demo.limit": 0})


def test_put_settings_accepts_declared_extension_keys_and_keeps_secrets_out_of_the_table(demo_extension: list) -> None:
    with client() as c:
        r = c.put("/settings", json={"ext.demo.label": "hi", "ext.demo.limit": 4, "ext.demo.token": "s3cret", "ext.demo.nope": "x",
                                     "ext.ghost.label": "x"})
        assert r.status_code == 200
        out = c.get("/settings").json()
        assert (out["ext.demo.label"], out["ext.demo.limit"]) == ("hi", 4)
        assert out["ext.demo.tokenSet"] is True and "ext.demo.token" not in out
        assert "ext.demo.nope" not in out and "ext.ghost.label" not in out
        assert appmod.db.secrets.get("ext.demo.token") == "s3cret"
        assert "ext.demo.token" not in appmod.db.get_settings()
        assert "s3cret" not in json.dumps(appmod.db.get_settings())

        c.put("/settings", json={"ext.demo.token": ""})  # blank = unchanged
        assert appmod.db.secrets.get("ext.demo.token") == "s3cret"
        c.put("/settings", json={"ext.demo.token": None})  # null clears
        assert not appmod.db.secrets.get("ext.demo.token")
        assert c.get("/settings").json()["ext.demo.tokenSet"] is False

        assert c.put("/settings", json={"ext.demo.limit": "many"}).status_code == 422
        assert c.put("/settings", json={"ext.demo.label": None}).status_code == 422


def test_settings_changed_lists_the_changed_keys_and_never_a_secret_value(demo_extension: list) -> None:
    with client() as c:
        c.put("/settings", json={"responseStyle": "default"})
        wait_for(lambda: demo_extension)
        demo_extension.clear()
        c.put("/settings", json={"ext.demo.label": "one", "ext.demo.token": "s3cret", "responseStyle": "default"})
        wait_for(lambda: demo_extension)
        (hook, payload), = demo_extension
        assert hook == "settings.changed"
        assert set(payload["keys"]) == {"ext.demo.label", "ext.demo.token"}  # responseStyle did not change
        assert payload["new"] == {"ext.demo.label": "one"} and payload["old"]["ext.demo.label"] in ("", None)
        assert "s3cret" not in json.dumps(payload)
        demo_extension.clear()
        c.put("/settings", json={"ext.demo.label": "one"})  # nothing changed: nothing fires
        time.sleep(0.2)
        assert demo_extension == []


def test_settings_changed_lists_each_key_once_and_reports_a_permissions_change(demo_extension: list) -> None:
    with client() as c:
        c.put("/settings", json={"braveApiKey": "k-one"})
        wait_for(lambda: demo_extension)
        (_, payload), = demo_extension
        assert payload["keys"] == ["braveApiKey"]
        demo_extension.clear()
        mode = c.get("/settings").json()["permissionMode"]
        other = "manual" if mode != "manual" else "auto"
        c.put("/settings", json={"permissionMode": other})
        wait_for(lambda: demo_extension)
        (_, payload), = demo_extension
        assert payload["keys"] == ["permissions"]
        c.put("/settings", json={"permissionMode": mode})
        appmod.db.secrets.delete("braveApiKey")


def test_hiding_a_module_view_fires_ext_changed() -> None:
    mod = next(m for m in appmod.modules if m.view)
    seen: list[Any] = []
    scope = appmod.host._add(declared("ext.changed", ext_id="watch"))
    scope.on("ext.changed", seen.append)
    try:
        with client() as c:
            hidden = list(appmod.settings().get("hiddenViews") or ())
            now_hidden = [v for v in hidden if v != mod.view] if mod.view in hidden else [*hidden, mod.view]
            c.put("/settings", json={"hiddenViews": now_hidden})
            wait_for(lambda: seen)
            assert seen == [{"id": mod.key, "enabled": mod.view not in now_hidden}]
            c.put("/settings", json={"hiddenViews": hidden})
    finally:
        scope.disposables.dispose()
        del appmod.host.extensions["watch"]
        del appmod.host._enabled["watch"]


# ---- chat / project events ----
def test_chat_and_project_routes_fire_their_events(demo_extension: list) -> None:
    with client() as c:
        pid = c.post("/projects", json={"name": "P"}).json()["id"]
        cid = c.post("/conversations", json={"project_id": pid, "title": "t"}).json()["id"]
        c.delete(f"/conversations/{cid}")
        c.delete(f"/conversations/{cid}")  # already gone: no second event
        c.delete(f"/projects/{pid}")
        c.delete("/projects/no-such-project")  # never existed: no event
        wait_for(lambda: len(demo_extension) >= 4)
        time.sleep(0.1)
    assert sorted(demo_extension, key=lambda e: e[0]) == sorted([
        ("project.created", {"id": pid}), ("chat.created", {"id": cid, "project_id": pid}),
        ("chat.deleted", {"id": cid, "project_id": pid}), ("project.deleted", {"id": pid})], key=lambda e: e[0])


# ---- migration ledger ----
def ledger(c: sqlite3.Connection) -> list[tuple[Any, str]]:
    return [tuple(r) for r in c.execute("SELECT version, name FROM schema_migrations ORDER BY typeof(version), version")]


def test_a_ledger_from_before_extensions_is_rebuilt_with_its_rows(tmp_path: Path) -> None:
    Database(tmp_path)
    c = sqlite3.connect(tmp_path / "personal-os.db")
    before = ledger(c)
    c.execute("DROP TABLE schema_migrations")
    c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)")
    c.executemany("INSERT INTO schema_migrations VALUES(?, ?, 0)", before)
    c.commit()
    migrations.run(c)
    assert ledger(c) == before
    assert migrations.drift(c) == []
    migrations.run_extension(c, "demo", [(1, "init", lambda cn: cn.execute("CREATE TABLE demo_t(x)"))])
    assert ledger(c)[-1] == ("ext:demo:1", "init")
    c.close()


def test_extension_steps_are_ledgered_once_in_order_and_leave_user_version_alone(tmp_path: Path) -> None:
    db = Database(tmp_path)
    steps = [(2, "second", lambda c: c.execute("INSERT INTO demo_t VALUES('b')")),
             (1, "init", lambda c: c.execute("CREATE TABLE demo_t(x)"))]
    with db.tx() as c:
        version = migrations.current(c)
    conn = db.connect()
    assert migrations.run_extension(conn, "demo", steps) == [1, 2]
    assert migrations.run_extension(conn, "demo", steps) == []
    assert migrations.current(conn) == version
    assert [r for r in ledger(conn) if str(r[0]).startswith("ext:")] == [("ext:demo:1", "init"), ("ext:demo:2", "second")]
    assert migrations.run_extension(conn, "other", [(1, "init", lambda c: c.execute("CREATE TABLE other_t(x)"))]) == [1]
    assert migrations.drift(conn) == []
    conn.close()
    assert Database(tmp_path).schema_drift == []


def test_a_failing_extension_step_rolls_back_and_is_not_recorded(tmp_path: Path) -> None:
    conn = Database(tmp_path).connect()

    def bad(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE half(x)")
        raise ValueError("nope")
    steps = [(1, "ok", lambda c: c.execute("CREATE TABLE fine(x)")), (2, "bad", bad)]
    with pytest.raises(RuntimeError, match="Migration 2 .*demo.*nope"):
        migrations.run_extension(conn, "demo", steps)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "fine" in names and "half" not in names
    assert [r for r in ledger(conn) if str(r[0]).startswith("ext:")] == [("ext:demo:1", "ok")]
    conn.close()


def test_sql_files_become_numbered_steps_run_statement_by_statement(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0002_seed.sql").write_text("INSERT INTO demo_t VALUES ('a;b');\nINSERT INTO demo_t VALUES ('c');\n")
    (folder / "0001_init.sql").write_text("CREATE TABLE demo_t(x);\nCREATE TRIGGER demo_trg AFTER INSERT ON demo_t\nBEGIN\n  SELECT 1;\nEND;\n")
    steps = migrations.sql_steps(folder)
    assert [(n, name) for n, name, _ in steps] == [(1, "init"), (2, "seed")]
    conn = Database(tmp_path / "data").connect()
    migrations.run_extension(conn, "demo", steps)
    assert [r[0] for r in conn.execute("SELECT x FROM demo_t ORDER BY rowid")] == ["a;b", "c"]
    conn.close()


def test_a_malformed_sql_migration_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_init.sql").write_text("CREATE TABLE demo_t(x);\nINSERT INTO demo_t VALUES (1)")
    conn = Database(tmp_path / "data").connect()
    with pytest.raises(RuntimeError, match="no terminating semicolon"):
        migrations.run_extension(conn, "demo", migrations.sql_steps(folder))
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'demo_t'").fetchone() is None  # rolled back, not recorded
    (folder / "readme.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="NNNN_name.sql"):
        migrations.sql_steps(folder)
    conn.close()


def test_host_migrate_backs_up_once_then_applies_each_extensions_steps(tmp_path: Path) -> None:
    db = Database(tmp_path / "data")
    root = tmp_path / "demo"
    (root / "migrations").mkdir(parents=True)
    (root / "migrations" / "0001_init.sql").write_text("CREATE TABLE demo_t(x);\n")
    host = ExtensionHost()
    host._add(manifest(data={"tables": ["demo_t"], "migrations": "migrations/"}), root=root)
    host._add(manifest(id="plain"))  # no migrations folder: nothing to do
    host.migrate(db)
    conn = db.connect()
    assert ("ext:demo:1", "init") in ledger(conn)
    conn.close()
    assert [b["kind"] for b in backups.list_backups(db.data_dir)] == ["premigrate"]
    host.migrate(db)  # applied: no second backup
    assert len(backups.list_backups(db.data_dir)) == 1


def test_host_migrate_opens_no_connection_when_nothing_declares_migrations(tmp_path: Path) -> None:
    class NoConnect:
        data_dir = tmp_path

        def connect(self) -> None:
            raise AssertionError("opened a connection")

    host = ExtensionHost()
    host._add(manifest(id="plain"))
    host.migrate(NoConnect())  # type: ignore[arg-type]


# ---- export include list ----
def test_export_includes_the_declared_extension_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "data")
    with db.tx() as c:
        c.execute("CREATE TABLE demo_t(x TEXT, y BLOB)")
        c.execute("INSERT INTO demo_t VALUES('a', X'00')")
    host = ExtensionHost()
    host._add(manifest(data={"tables": ["demo_t", "not_created_yet"]}))
    assert host.data_tables() == ["demo_t", "not_created_yet"]
    dest = tmp_path / "out.zip"
    backups.export_zip(db.data_dir, dest, host.data_tables())
    with zipfile.ZipFile(dest) as z:
        names = set(z.namelist())
        assert "export/extensions/demo_t.json" in names and "export/extensions/not_created_yet.json" not in names
        assert json.loads(z.read("export/extensions/demo_t.json"))[0]["x"] == "a"
    backups.export_zip(db.data_dir, dest)
    with zipfile.ZipFile(dest) as z:
        assert not [n for n in z.namelist() if n.startswith("export/extensions/")]
