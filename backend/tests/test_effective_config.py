"""The effective configuration: config.assemble, the source of every value, the route, `grain config dump` and its read-only promise.

Run: python -m pytest backend/tests/test_effective_config.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="effective-"))
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import config, llm, migrations, permissions, provider_keys  # noqa: E402
from personal_os.db import SECRET_SETTINGS, Database  # noqa: E402
from personal_os.extensions.defaults import Defaults  # noqa: E402
from personal_os.extensions.manifest import Manifest  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SOURCES = ("default", "user", "project", "chat")


@pytest.fixture(autouse=True)
def fresh() -> Any:
    with appmod.db.tx() as c:
        c.execute("DELETE FROM settings WHERE key IN ('permissions', 'extDefaults')")
        c.execute("DELETE FROM setting_sources")
        c.execute("UPDATE projects SET permissions = '{}', tools = '{}'")
    yield


def dump(**params: Any) -> dict[str, Any]:
    r = client.get("/config/effective", params=params)
    assert r.status_code == 200
    return r.json()


def test_an_unknown_chat_or_project_is_a_404() -> None:
    assert client.get("/config/effective", params={"conversation_id": "nope"}).status_code == 404
    assert client.get("/config/effective", params={"project_id": "nope"}).status_code == 404


def source_ok(source: str) -> bool:
    return source in SOURCES or source.startswith(("profile:", "ext:", "preset:"))


# ---- assemble
def test_assemble_is_what_settings_returns_outside_a_turn() -> None:
    assert appmod.settings() == config.assemble(appmod.db)


def test_assemble_matches_its_golden_for_a_fixed_database(tmp_path: Path) -> None:
    db = Database(tmp_path)
    db.set_settings({"theme": "light", "hiddenViews": ["mail"], "providers": [{"id": "openrouter", "kind": "openrouter", "name": "OpenRouter", "baseUrl": "https://openrouter.ai/api/v1"}],
                     "defaultModel": "openrouter/some/model", "toolDeferAbove": 7, "sidebarHidden": ["health"], "ext.demo.note": "hi"})
    permissions.save(db, {"permissionMode": "manual", "alwaysAsk": ["gmail_send"], "tools": {"shell_run": "off"}})
    got = json.loads(json.dumps(config.assemble(db), sort_keys=True, default=str))
    golden = BACKEND / "tests" / "golden" / "settings_assemble.json"
    if not golden.exists():
        golden.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    assert got == json.loads(golden.read_text())


def test_a_turns_scope_overlays_the_settings_and_leaves_the_rest_alone() -> None:
    token = permissions.SCOPE.set(({"permissionMode": "manual"}, {"permissionRules": {"deny": ["x_tool"]}}))
    try:
        cfg = appmod.settings()
        assert permissions.get(cfg, "permissionMode") == "manual" and permissions.get(cfg, "permissionRules")["deny"] == ["x_tool"]
        assert cfg["permissions"]["permissionMode"] == "manual"
    finally:
        permissions.SCOPE.reset(token)
    assert permissions.get(appmod.settings(), "permissionMode") != "x" and appmod.settings() == config.assemble(appmod.db)


# ---- the route
def test_every_setting_the_permission_keys_and_the_tools_appear_with_a_source() -> None:
    d = dump()
    wanted = set(llm.DEFAULT_SETTINGS) | set(permissions.DEFAULTS)
    shown = set(d["values"])
    assert wanted - shown <= config.PRIVATE_SETTINGS | set(SECRET_SETTINGS)
    assert all(source_ok(e["source"]) and e["scope"] in ("global", "project", "chat") for e in d["values"].values())
    assert set(d["tools"]) == set(appmod.toolbox.specs) and all(source_ok(t["source"]) or t["source"] in ("global", "agent") for t in d["tools"].values())
    assert set(d["runtime"]) == set(appmod.host.runtime.resolve_all()) and d["scope"] == {"project_id": None, "conversation_id": None}


def test_a_default_a_user_edit_a_preset_a_profile_an_extension_a_project_and_a_chat_each_show_their_source() -> None:
    assert dump()["values"]["permissionMode"]["source"] == "default"
    assert dump()["values"]["toolDeferAbove"]["source"] == "default"
    assert client.put("/settings", json={"toolDeferAbove": 9, "permissionMode": "manual"}).status_code == 200
    v = dump()["values"]
    assert v["toolDeferAbove"]["source"] == "user" and v["toolDeferAbove"]["value"] == 9 and not v["toolDeferAbove"].get("legacy")
    assert v["permissionMode"]["source"] == "user" and v["permissionMode"]["shadowed"] == [{"source": "default", "value": "auto"}]

    assert client.post("/permissions/presets/builtin:auto/apply", json={}).status_code == 409  # auto from manual loosens
    r = client.post("/permissions/presets/builtin:auto/apply", json={}).json()
    client.post("/permissions/presets/builtin:auto/apply", json={"confirm": r["diff_hash"]})
    v = dump()["values"]["permissionMode"]
    assert v["source"] == "preset:builtin:auto" and v["value"] == "auto" and {"source": "previous", "value": "manual"} in v["shadowed"]

    profile = Manifest.from_dict({"id": "calm", "name": "Calm", "version": "1.0.0", "apiVersion": 1, "kind": "profile",
                                  "contributes": {"defaults": {"effort": "low", "permissions": "manual"}}})
    Defaults(appmod.db).apply(profile)
    v = dump()["values"]
    assert v["defaultEffort"]["source"] == "profile:calm" and v["defaultEffort"]["value"] == "low"
    assert v["defaultEffort"]["shadowed"][-1] == {"source": "previous", "value": "high"}
    assert v["permissionMode"]["source"] == "profile:calm"

    ext = Manifest.from_dict({"id": "tweaker", "name": "Tweaker", "version": "1.0.0", "apiVersion": 1, "contributes": {"defaults": {"theme": "light"}}})
    Defaults(appmod.db).apply(ext)
    assert dump()["values"]["theme"]["source"] == "ext:tweaker"

    project = appmod.projects.create("P")["id"]
    chat = appmod.convos.create(project, "t", "m")["id"]
    client.post("/permissions/presets", json={"name": "Chatty", "permissionMode": "allow_all", "tools": {"tools": {"fs_glob": "off"}}})
    r = client.post("/permissions/presets/user:chatty/apply", json={"scope": "project", "scope_id": project}).json()
    client.post("/permissions/presets/user:chatty/apply", json={"scope": "project", "scope_id": project, "confirm": r["diff_hash"]})
    d = dump(project_id=project)
    mode = d["values"]["permissionMode"]
    assert mode["value"] == "allow_all" and mode["scope"] == "project" and mode["source"] == "preset:user:chatty"
    assert {"source": "profile:calm", "value": "manual"} in mode["shadowed"] and d["tools"]["fs_glob"] == {"mode": "off", "source": "project"}
    appmod.projects.update(project, {"permissions": {"permissionMode": "allow_all"}})
    with appmod.db.tx() as c:
        c.execute("DELETE FROM setting_sources WHERE scope = 'project'")
    assert dump(project_id=project)["values"]["permissionMode"]["source"] == "project"
    appmod.convos.update(chat, {"settings": {"permissions": {"permissionMode": "manual"}, "tools": {"fs_glob": "ask"}}})
    d = dump(conversation_id=chat)  # the project comes from the chat
    assert d["values"]["permissionMode"]["source"] == "chat" and d["values"]["permissionMode"]["scope"] == "chat"
    assert d["tools"]["fs_glob"] == {"mode": "ask", "source": "chat"} and d["scope"]["conversation_id"] == chat


def test_a_value_stored_before_sources_were_recorded_is_the_users_and_flagged_legacy() -> None:
    appmod.db.set_settings({"toolDeferAbove": 11})
    e = dump()["values"]["toolDeferAbove"]
    assert e["source"] == "user" and e["legacy"] is True


# ---- secrets never leave
def test_a_planted_secret_appears_nowhere_in_the_output() -> None:
    sentinels = {k: f"SENTINEL-{k}-{i}" for i, k in enumerate(SECRET_SETTINGS)}
    appmod.db.set_settings(sentinels)
    appmod.db.secrets.set("ext.demo.token", "SENTINEL-ext-token")
    provider_keys.save(appmod.db.secrets, {"openrouter": "SENTINEL-provider-key"})
    appmod.db.set_settings({"providers": [{"id": "openrouter", "kind": "openrouter", "name": "OpenRouter", "baseUrl": "https://openrouter.ai/api/v1"}]})
    try:
        d = config.effective(appmod.db, appmod.toolbox, {"ext.demo.token": "secret"})
        d2 = dump()
        for blob in (json.dumps(d), json.dumps(d2), config.render(d), config.render(d2), json.dumps(client.get("/settings").json())):
            assert "SENTINEL" not in blob
        assert {d["secrets"][k] for k in SECRET_SETTINGS} == {"set"} and d["secrets"]["ext.demo.token"] == "set"
        assert d["secrets"]["providerKeys"] == {"openrouter": "set"}
        assert "apiKey" not in d["values"] and "ext.demo.token" not in d["values"]
    finally:
        appmod.db.set_settings({k: "" for k in SECRET_SETTINGS} | {"providers": []})
        appmod.db.secrets.delete("ext.demo.token")
        provider_keys.save(appmod.db.secrets, {})


def test_an_unset_secret_reads_unset() -> None:
    d = dump()
    assert d["secrets"]["braveApiKey"] == "unset" and d["secrets"]["providerKeys"] == {}


# ---- the CLI
def cli(*args: str, data_dir: Path | str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "GRAIN_SECRETS_BACKEND": "file"}
    env.pop("PERSONAL_OS_DATA_DIR", None)
    return subprocess.run([sys.executable, "-m", "personal_os.extensions.authoring.cli", "config", "dump", *args,
                           *(["--data-dir", str(data_dir)] if data_dir else [])], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)


def test_the_cli_and_the_route_return_the_same_data() -> None:
    appmod.db.set_settings({"toolDeferAbove": 5})
    client.put("/settings", json={"theme": "dark"})
    project = appmod.projects.create("Cli")["id"]
    appmod.projects.update(project, {"permissions": {"permissionMode": "manual"}, "tools": {"fs_glob": "off"}})
    out = cli("--json", "--project", project, data_dir=appmod.db.data_dir)
    assert out.returncode == 0, out.stderr
    got, want = json.loads(out.stdout), dump(project_id=project)
    assert got["values"] == want["values"] and got["secrets"] == want["secrets"] and got["scope"] == want["scope"]
    assert got["tools"] and all(want["tools"][n] == t for n, t in got["tools"].items())  # the CLI knows the core catalog
    text = cli("--project", project, data_dir=appmod.db.data_dir).stdout
    assert "[user]" in text and "toolDeferAbove" in text and "[tools]" in text and "[secrets]" in text


def test_the_cli_opens_the_database_read_only(tmp_path: Path) -> None:
    db = Database(tmp_path)
    db.set_settings({"theme": "light"})
    c = sqlite3.connect(db.path)
    c.execute(f"PRAGMA user_version = {migrations.latest() - 1}")  # one step pending: a normal start would migrate
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.commit()
    c.close()
    digest = lambda: hashlib.sha256(db.path.read_bytes()).hexdigest()  # noqa: E731
    listing = lambda: sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(("-wal", "-shm")))  # noqa: E731
    before, files = digest(), listing()
    out = cli("--json", data_dir=tmp_path)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["values"]["theme"]["value"] == "light"
    assert digest() == before and listing() == files
    c = sqlite3.connect(db.path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == migrations.latest() - 1
    c.close()


def test_the_cli_says_so_when_there_is_no_database(tmp_path: Path) -> None:
    out = cli(data_dir=tmp_path / "nowhere")
    assert out.returncode == 1 and "no Grain database" in out.stdout and not (tmp_path / "nowhere").exists()


def test_the_cli_refuses_an_unknown_project_or_chat_and_resolves_the_runtime_slots() -> None:
    for flag in ("--project", "--chat"):
        out = cli(flag, "nosuch", data_dir=appmod.db.data_dir)
        assert out.returncode == 1 and "no such" in out.stdout
    appmod.db.set_settings({"runtime": {"agent.loop": "core/plan-execute-verify"}})
    try:
        got = json.loads(cli("--json", data_dir=appmod.db.data_dir).stdout)["runtime"]
    finally:
        appmod.db.set_settings({"runtime": {}})
    assert set(got) == set(appmod.host.runtime.resolve_all())
    assert got["agent.loop"]["provider"] == "core/plan-execute-verify" and got["agent.loop"]["source"] == "global"
