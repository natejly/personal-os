"""Authoring tools that need the running app: the dev panel's API (list, enable, disable, load, reload), a loaded folder's
tools and routes, grain_sdk.testing.Harness, and a sample extension built end to end by the CLI.

Run: python -m pytest backend/tests/test_extension_devtools.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="devtools-"))
os.environ["GRAIN_EXT_DEV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from grain_sdk import Deny  # noqa: E402
from grain_sdk.testing import Harness  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os.extensions import devapi  # noqa: E402
from personal_os.extensions.authoring import cli  # noqa: E402
from personal_os.extensions.authoring.scaffold import scaffold  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
CTX = {"run_id": None, "conversation_id": None, "project_id": None, "agent_id": None, "desk_id": None, "tainted": False}
HOOKED = '''
    from grain_sdk import Deny
    api.on("tool.pre", lambda p: Deny("no shell") if p["name"] == "shell_run" else None)
    api.on("model.select", lambda p: {**p, "effort": "high"})
    api.on("chat.created", lambda p: None)
'''


@pytest.fixture
def ext() -> Any:
    d = Path(tempfile.mkdtemp(prefix="devtools-ext-"))
    root = scaffold("sample", d)
    yield root
    appmod.host.failed.clear()
    if "sample" in appmod.host.extensions:
        import asyncio
        asyncio.run(appmod.host.unload("sample"))
    appmod.toolbox.drop_extension_tools(["sample_note"])
    shutil.rmtree(d, ignore_errors=True)


def edit(root: Path, name: str, fn: Any) -> None:
    (root / name).write_text(fn((root / name).read_text("utf-8")), "utf-8")


def declare(root: Path, *hooks: str, permissions: tuple[str, ...] = ()) -> None:
    m = json.loads((root / "extension.json").read_text("utf-8"))
    m["hooks"] |= {h: {} for h in hooks}
    m["permissions"] += permissions
    (root / "extension.json").write_text(json.dumps(m), "utf-8")


def listing() -> dict[str, Any]:
    return client.get("/dev/extensions").json()


def load(root: Path) -> Any:
    return client.post("/dev/extensions/load", json={"path": str(root)})


def load_on(root: Path) -> Any:
    """Load a folder and turn it on: a folder extension starts disabled."""
    got = load(root)
    client.post(f"/dev/extensions/{got.json()['id']}/enable")
    return got


# ---- the dev panel's API ----
def test_the_listing_shows_built_ins_with_their_registrations() -> None:
    out = listing()
    health = next(e for e in out["extensions"] if e["id"] == "health")
    assert out["live"] and health["tools"] == ["health_summary", "health_log", "health_delete_entry"] and not health["reloadable"]
    assert {h["hook"] for h in health["hooks"]} == {"app.start"} and health["permissions"] == ["db:own", "service:mcp"]
    assert health["contributes"]["routes"] == {"prefix": "/health"}


def test_the_listing_names_each_extensions_tier(ext: Path) -> None:
    assert next(e for e in listing()["extensions"] if e["id"] == "health")["tier"] == "builtin"
    assert load(ext).json()["tier"] == "user"


def test_a_view_event_wakes_an_extension_whose_code_waits_for_it(tmp_path: Path) -> None:
    root = tmp_path / "lazy"
    root.mkdir()
    (root / "extension.json").write_text(json.dumps({
        "id": "lazy", "name": "Lazy", "version": "1.0.0", "apiVersion": 1, "activation": ["onView:lazy"],
        "contributes": {"views": [{"id": "lazy"}], "commands": [{"name": "lazy-go", "hint": "go"}]}}), "utf-8")
    (root / "extension.py").write_text("def activate(api):\n    api.command('lazy-go', lambda args: 'went')\n", "utf-8")
    try:
        load_on(root)
        scope = appmod.host.extensions["lazy"]
        assert scope.dormant and {"name": "lazy-go", "hint": "go", "owner": "lazy"} in client.get("/commands/registered").json()
        assert client.post("/extensions/activate", json={"event": "onCommand:lazy-go"}).status_code == 422  # the renderer raises views only
        assert client.post("/extensions/activate", json={"event": "onView:chat"}).json() == {"ok": True} and scope.dormant
        assert client.post("/extensions/activate", json={"event": "onView:lazy"}).json() == {"ok": True}
        assert scope.dormant is None and appmod.host.slash.expand("lazy-go", "") == "went"
        assert client.get("/commands/registered").json().count({"name": "lazy-go", "hint": "go", "owner": "lazy"}) == 1
    finally:
        if "lazy" in appmod.host.extensions:
            import asyncio
            asyncio.run(appmod.host.unload("lazy"))
        appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings()["hiddenViews"] if v != "lazy"]})


def test_the_api_needs_the_token() -> None:
    assert TestClient(appmod.app).get("/dev/extensions").status_code == 401


def test_enable_and_disable_write_the_hidden_views_and_fire_ext_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    appmod.db.set_settings({"hiddenViews": sorted({*appmod.settings()["hiddenViews"], "health"})})  # Health starts off, as on a fresh install
    appmod.host.refresh_enabled()
    monkeypatch.setattr(appmod.host.bus, "fire", lambda hook, payload=None: seen.append((hook, payload)))
    try:
        on = client.post("/dev/extensions/health/enable").json()  # a fresh install has Health off
        assert on["enabled"] is True and "health" not in appmod.settings()["hiddenViews"] and appmod.toolbox.available("health_log")
        off = client.post("/dev/extensions/health/disable").json()
        assert off["enabled"] is False and "health" in appmod.settings()["hiddenViews"] and not appmod.toolbox.available("health_log")
    finally:
        appmod.db.set_settings({"hiddenViews": sorted({*appmod.settings()["hiddenViews"], "health"})})
    assert seen == [("ext.changed", {"id": "health", "enabled": True}), ("ext.changed", {"id": "health", "enabled": False})]
    assert client.post("/dev/extensions/ghost/enable").status_code == 404


def test_parallel_toggles_do_not_lose_hidden_views_updates() -> None:
    from concurrent.futures import ThreadPoolExecutor
    ids = [f"qa-{i}" for i in range(20)]
    before = appmod.settings()["hiddenViews"]
    try:
        with ThreadPoolExecutor(10) as pool:
            list(pool.map(lambda i: devapi.set_hidden_view(appmod.host, appmod._ext_ctx, i, True), ids))
        assert set(ids) <= set(appmod.settings()["hiddenViews"])
    finally:
        appmod.db.set_settings({"hiddenViews": before})


def test_loading_a_folder_registers_its_tools_and_hooks_and_reload_picks_up_edits(ext: Path) -> None:
    declare(ext, "tool.pre", permissions=("hooks:gate",))
    edit(ext, "extension.py", lambda t: t + '\n    api.on("tool.pre", lambda p: None)\n')
    got = load(ext).json()
    assert got["reloadable"] and got["tools"] == ["sample_note"] and got["folder"] == str(ext.resolve())
    assert {h["hook"] for h in got["hooks"]} == {"context.contribute", "tool.pre"}
    assert "sample_note" in appmod.toolbox.specs and appmod.toolbox.tool_owner["sample_note"] == "sample"
    dup = load(ext)
    assert dup.status_code == 400 and "already loaded" in dup.json()["detail"] and listing()["failed"] == []
    edit(ext, "extension.json", lambda t: t.replace("sample_note", "sample_jot"))
    edit(ext, "extension.py", lambda t: t.replace("sample_note", "sample_jot"))
    shutil.move(ext / "tools" / "sample_note.json", ext / "tools" / "sample_jot.json")
    again = client.post("/dev/extensions/sample/reload").json()
    assert again["tools"] == ["sample_jot"]
    assert "sample_jot" in appmod.toolbox.specs and "sample_note" not in appmod.toolbox.specs
    assert next(e for e in listing()["extensions"] if e["id"] == "sample")["tools"] == ["sample_jot"]
    appmod.toolbox.drop_extension_tools(["sample_jot"])


def test_a_folder_cannot_take_over_a_tool_the_app_already_has(ext: Path) -> None:
    core = "fs_glob"
    spec = appmod.toolbox.specs[core]
    for name in ("extension.json", "extension.py"):
        edit(ext, name, lambda t: t.replace("sample_note", core).replace(f"{core}s", "sample_notes"))
    shutil.move(ext / "tools" / "sample_note.json", ext / f"tools/{core}.json")
    r = load(ext)
    assert r.status_code == 400 and core in r.json()["detail"]
    assert "sample" not in appmod.host.extensions and appmod.toolbox.specs[core] is spec
    assert "sample" not in appmod.settings()["hiddenViews"]
    assert [f["path"] for f in client.get("/dev/extensions").json()["failed"]] == [str(ext.resolve())]


def test_a_misplaced_tool_leaves_none_of_the_extensions_tools_behind(ext: Path) -> None:
    m = json.loads((ext / "extension.json").read_text("utf-8"))
    m["contributes"]["tools"].append({**m["contributes"]["tools"][0], "name": "sample_two", "after": "nonexistent_tool"})
    (ext / "extension.json").write_text(json.dumps(m), "utf-8")
    shutil.copy(ext / "tools" / "sample_note.json", ext / "tools" / "sample_two.json")
    edit(ext, "extension.py", lambda t: t + '\n    api.tool(ToolSpec("sample_two", "Two.", schema, note, "sample", danger="writes"))\n')
    r = load(ext)
    assert r.status_code == 400 and "nonexistent_tool" in r.json()["detail"]
    assert "sample" not in appmod.host.extensions
    assert "sample_note" not in appmod.toolbox.specs and "sample_two" not in appmod.toolbox.specs
    assert "sample_note" not in appmod.toolbox.tool_owner


def test_a_profile_folder_loads_and_is_not_toggled_here(tmp_path: Path) -> None:
    root = scaffold("myprof", tmp_path, profile=True)
    try:
        got = load(root)
        assert got.status_code == 200 and got.json()["toggleable"] is False
        assert "myprof" in {p["id"] for p in client.get("/profiles").json()}
        assert client.post("/dev/extensions/myprof/disable").status_code == 400
        assert "myprof" not in appmod.settings()["hiddenViews"]
    finally:
        import asyncio
        asyncio.run(appmod.host.unload("myprof"))


def test_an_always_on_built_in_is_not_toggled_here() -> None:
    assert next(e for e in listing()["extensions"] if e["id"] == "google")["toggleable"] is False
    before = appmod.settings()["hiddenViews"]
    assert client.post("/dev/extensions/google/disable").status_code == 400 and appmod.settings()["hiddenViews"] == before


def test_a_loaded_folder_starts_disabled_whatever_its_manifest_says(ext: Path) -> None:
    edit(ext, "extension.json", lambda t: t.replace('"enabled": true', '"enabled": "always"'))
    assert load(ext).json()["enabled"] is False and not appmod.toolbox.available("sample_note")
    assert client.post("/dev/extensions/sample/enable").json()["enabled"] is True and appmod.toolbox.available("sample_note")


def test_disabling_a_loaded_extension_takes_its_tools_away(ext: Path) -> None:
    load_on(ext)
    assert appmod.toolbox.available("sample_note")
    client.post("/dev/extensions/sample/disable")
    try:
        assert not appmod.toolbox.available("sample_note")
    finally:
        client.post("/dev/extensions/sample/enable")
    assert appmod.toolbox.available("sample_note")


def test_a_broken_folder_is_reported_and_a_fixed_one_loads(ext: Path) -> None:
    edit(ext, "extension.py", lambda t: t + "\n    1 / 0\n")
    r = load(ext)
    assert r.status_code == 400 and "ZeroDivisionError" in r.json()["detail"]
    assert listing()["failed"] == [{"path": str(ext.resolve()), "error": "ZeroDivisionError: division by zero"}]
    assert "sample" not in appmod.settings()["hiddenViews"]
    edit(ext, "extension.py", lambda t: t.replace("\n    1 / 0\n", "\n"))
    assert load(ext).status_code == 200 and listing()["failed"] == []


def test_an_empty_path_is_refused_and_failed_loads_can_be_cleared() -> None:
    assert client.post("/dev/extensions/load", json={"path": "  "}).status_code == 400
    assert client.post("/dev/extensions/load", json={"path": "/nonexistent"}).status_code == 400
    assert [f["path"] for f in listing()["failed"]] == ["/nonexistent"]
    assert client.delete("/dev/extensions/failed").json() == {"ok": True}
    assert listing()["failed"] == []


def test_a_failed_reload_keeps_the_reason_and_drops_the_old_tools(ext: Path) -> None:
    load(ext)
    edit(ext, "extension.py", lambda t: t + "\n    raise RuntimeError('half done')\n")
    r = client.post("/dev/extensions/sample/reload")
    assert r.status_code == 400 and "half done" in r.json()["detail"]
    assert "sample_note" not in appmod.toolbox.specs and "sample" not in appmod.host.extensions
    assert "half done" in listing()["failed"][0]["error"]
    edit(ext, "extension.py", lambda t: t.replace("\n    raise RuntimeError('half done')\n", "\n"))
    assert load(ext).status_code == 200


def test_a_broken_edit_does_not_turn_an_enabled_extension_off(ext: Path) -> None:
    load_on(ext)
    edit(ext, "extension.py", lambda t: t + "\n    raise RuntimeError('half done')\n")
    assert client.post("/dev/extensions/sample/reload").status_code == 400
    edit(ext, "extension.py", lambda t: t.replace("\n    raise RuntimeError('half done')\n", "\n"))
    assert load(ext).json()["enabled"] is True  # `grain ext dev` falls back to a load once the reload is refused


def test_a_profile_folder_can_be_turned_on_and_off(tmp_path: Path) -> None:
    root = scaffold("myprof", tmp_path, profile=True)
    try:
        load(root)
        assert client.post("/profiles/myprof/enable", json={"confirm": True}).status_code == 200
        assert client.post("/profiles/myprof/disable").status_code == 200
    finally:
        import asyncio
        asyncio.run(appmod.host.unload("myprof"))


def test_a_failing_start_leaves_nothing_registered_and_a_fixed_folder_loads(ext: Path) -> None:
    edit(ext, "extension.py", lambda t: t + "\n    api.on('app.start', lambda p: 1 / 0)\n")
    declare(ext, "app.start")
    (ext / "migrations" / "0002_bad.sql").write_text("this is not sql;", "utf-8")
    r = load(ext)
    assert r.status_code == 400
    assert "sample" not in appmod.host.extensions and not appmod.host.bus.subscriptions("sample")
    assert "sample_note" not in appmod.toolbox.specs
    assert listing()["failed"][0]["path"] == str(ext.resolve())
    (ext / "migrations" / "0002_bad.sql").unlink()
    assert load(ext).status_code == 200


def test_a_built_in_cannot_be_reloaded_and_keeps_its_tools() -> None:
    tools = list(appmod.host.extensions["health"].tools)
    assert tools and all(t in appmod.toolbox.specs for t in tools)
    r = client.post("/dev/extensions/health/reload")
    assert r.status_code == 400 and "not loaded from a folder" in r.json()["detail"]
    assert all(t in appmod.toolbox.specs and appmod.toolbox.tool_owner[t] == "health" for t in tools)
    assert client.post("/dev/extensions/ghost/reload").status_code == 404


def test_loading_code_is_refused_unless_the_backend_was_started_for_it(ext: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAIN_EXT_DEV")
    app = FastAPI()
    app.include_router(devapi.router(appmod.host, appmod._ext_ctx, lambda: appmod.toolbox, appmod.prefix_watch.stats))
    c = TestClient(app)
    assert c.get("/dev/extensions").json()["live"] is False
    for r in (c.post("/dev/extensions/load", json={"path": str(ext)}), c.post("/dev/extensions/health/reload")):
        assert r.status_code == 403 and "GRAIN_EXT_DEV=1" in r.json()["detail"]
    assert "sample" not in appmod.host.extensions


def test_a_loaded_extensions_routes_sit_behind_the_token(ext: Path) -> None:
    edit(ext, "extension.py", lambda t: t.replace("import json", "import json\nfrom fastapi import APIRouter") + '''
    r = APIRouter()
    r.get("/hi")(lambda: {"say": "hi"})
    api.router(r)
''')
    load_on(ext)
    assert client.get("/ext/sample/hi").json() == {"say": "hi"}
    assert TestClient(appmod.app).get("/ext/sample/hi").status_code == 401


def test_the_panel_sees_handler_stats_and_a_muted_handler(ext: Path) -> None:
    declare(ext, "chat.created")
    edit(ext, "extension.py", lambda t: t + '\n    api.on("chat.created", lambda p: 1 / 0)\n')
    load_on(ext)
    for _ in range(5):
        appmod.host.bus.emit_sync("chat.created", {"id": "c", "project_id": None})
    hook = next(h for h in next(e for e in listing()["extensions"] if e["id"] == "sample")["hooks"] if h["hook"] == "chat.created")
    assert hook["muted"] and hook["calls"] == 5 and hook["errors"][-1].startswith("ZeroDivisionError") and hook["p95Ms"] is not None


def test_the_panel_shows_the_last_payloads_with_credentials_removed(ext: Path) -> None:
    declare(ext, "chat.created")
    edit(ext, "extension.py", lambda t: t + '\n    api.on("chat.created", lambda p: None)\n')
    load_on(ext)
    for i in range(7):
        appmod.host.bus.emit_sync("chat.created", {"id": f"c{i}", "project_id": "sk-abcdefghijklmnopqrstuvwxyz0123456789"})
    hook = next(h for h in next(e for e in listing()["extensions"] if e["id"] == "sample")["hooks"] if h["hook"] == "chat.created")
    assert len(hook["payloads"]) == 5 and "'id': 'c6'" in hook["payloads"][-1] and "'id': 'c2'" in hook["payloads"][0]
    assert not any("sk-abcdefghij" in p for p in hook["payloads"])


def test_the_panel_lists_hooks_in_the_order_they_were_subscribed(ext: Path) -> None:
    order = ["chat.created", "tool.pre", "app.start", "chat.created"]
    declare(ext, "chat.created", "tool.pre", "app.start", permissions=("hooks:gate",))
    edit(ext, "extension.py", lambda t: t + "".join(f'\n    api.on("{h}", lambda p: None)' for h in order) + "\n")
    load_on(ext)
    mine = next(e for e in listing()["extensions"] if e["id"] == "sample")
    assert [h["hook"] for h in mine["hooks"]] == ["context.contribute", *order]


# ---- grain_sdk.testing.Harness ----
def test_harness_drives_context_prefix_and_the_tool(ext: Path) -> None:
    with Harness(ext, settings={"permissionMode": "allow_all"}) as h:
        first = h.chat("hello")
        h.assert_context_contains("saved notes")
        assert first.text == "ok" and first.error is None and "tokens" not in first.context_used
        call = {"id": "c1", "name": "sample_note", "arguments": {"text": "buy milk"}}
        turn = h.chat("note it", model_script=[{"tool_calls": [call]}, "saved"])
        assert turn.text == "saved" and len(turn.requests) == 2 and "buy milk" in str(turn.requests[1])
        h.assert_prefix_stable()
        with pytest.raises(AssertionError, match="is not in the messages"):
            h.assert_context_contains("a sentence nobody sent")
    assert "sample" not in appmod.host.extensions and "sample_note" not in appmod.toolbox.specs


def test_harness_prefix_check_needs_two_turns_and_catches_a_moving_prefix(ext: Path) -> None:
    declare(ext, permissions=("prompt:inject",))
    edit(ext, "extension.py", lambda t: t.replace("slot=\"volatile\")", "slot=\"stable\")").replace("Contribution(f\"The user", "Contribution(f\"{count()}: The user"))
    with Harness(ext) as h:
        h.chat("one")
        with pytest.raises(AssertionError, match="at least two"):
            h.assert_prefix_stable()
        with appmod.db.tx() as c:
            c.execute("INSERT INTO sample_notes (text) VALUES ('x')")
        h.chat("two")
        with pytest.raises(AssertionError, match="system prompt changed on turn 2"):
            h.assert_prefix_stable()


def test_harness_fire_follows_the_hook_kind(ext: Path) -> None:
    declare(ext, "tool.pre", "model.select", "chat.created", permissions=("hooks:gate",))
    edit(ext, "extension.py", lambda t: t + HOOKED)
    with Harness(ext) as h:
        verdict = h.fire("tool.pre", {"name": "shell_run", "args": {}, "call_id": "c", "ctx": CTX})
        assert isinstance(verdict, Deny) and verdict.reason == "no shell"
        assert h.fire("tool.pre", {"name": "current_time", "args": {}, "call_id": "c", "ctx": CTX}) is None
        assert h.fire("model.select", {"model": "m", "effort": "low", "reason": "", "tier": "default"})["effort"] == "high"
        assert h.fire("chat.created", {"id": "c", "project_id": None}) is None
        [block] = h.fire("context.contribute", {"conversation_id": "c", "project_id": None})
        assert "saved notes" in block.text and block.slot == "volatile"


# ---- the whole path ----
def test_a_sample_extension_is_built_end_to_end_by_the_cli(tmp_path: Path, capfd: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(cli, "_token", lambda args: None)  # `new` would register with a dev backend; there is none to find
    assert cli.main(["ext", "new", "journal", "--dir", str(tmp_path)]) == 0
    root = tmp_path / "journal"
    assert cli.main(["ext", "check", str(root)]) == 0
    assert cli.main(["ext", "test", str(root)]) == 0
    assert cli.main(["ext", "pack", str(root)]) == 0
    assert cli.main(["ext", "check", str(root)]) == 0  # against its own lock: nothing changed
    assert "2 passed" in capfd.readouterr().out


def test_new_registers_the_absolute_folder_and_reports_an_unwritable_parent(tmp_path: Path, capfd: Any, monkeypatch: Any) -> None:
    sent: list[Path] = []
    monkeypatch.setattr(cli, "_token", lambda args: "t")
    monkeypatch.setattr(cli, "push", lambda client, root: sent.append(root) or "ok")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ext", "new", "rel"]) == 0
    assert sent == [tmp_path / "rel"]
    (tmp_path / "file").write_text("", "utf-8")
    assert cli.main(["ext", "new", "x", "--dir", str(tmp_path / "file" / "sub")]) == 1
    assert "error:" in capfd.readouterr().out


def test_ext_test_passes_pytest_options_through(tmp_path: Path, capfd: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(cli, "_token", lambda args: None)
    assert cli.main(["ext", "new", "opts", "--dir", str(tmp_path)]) == 0
    assert cli.main(["ext", "test", str(tmp_path / "opts"), "-k", "nothing_matches_this"]) == 5  # pytest: no tests selected
