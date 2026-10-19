"""Permission presets: the schema, the built-ins, apply at global, project and chat scope, the loosening gate, scoped overrides,
profile presets and the routes.

Run: python -m pytest backend/tests/test_permission_presets.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="presets-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import guards, llm, permissions, presets as presets_mod  # noqa: E402
from personal_os.extensions.defaults import Defaults  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
P = appmod.presets
RAN: list[str] = []


async def probe(ctx: dict[str, Any], tag: str = "", destroy: bool = False) -> Any:
    RAN.append(tag)
    return {"ok": True}


_spec = ToolSpec("preset_probe", "test", _obj({"tag": {"type": "string"}, "destroy": {"type": "boolean"}}, []), probe, "presetgroup", "writes")
_spec.floor = lambda args, ctx: "files" if args.get("destroy") else None
appmod.toolbox.specs["preset_probe"] = _spec


@pytest.fixture(autouse=True)
def fresh() -> Any:
    RAN.clear()
    with appmod.db.tx() as c:
        c.execute("DELETE FROM settings WHERE key = 'permissions'")
        c.execute("DELETE FROM setting_sources")
        c.execute("DELETE FROM permission_presets")
        c.execute("UPDATE projects SET permissions = '{}', tools = '{}'")
    appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
    yield


def stored() -> dict[str, Any]:
    return permissions.load(appmod.db.get_settings())


def user_preset(**body: Any) -> str:
    return P.save({"name": body.pop("name", "Mine"), **body})["id"]


def apply(preset_id: str, scope: str = "global", scope_id: str = "", confirm: str | None = None) -> Any:
    return client.post(f"/permissions/presets/{preset_id}/apply", json={"scope": scope, "scope_id": scope_id, "confirm": confirm})


# ---- the schema
def test_the_schema_rejects_what_it_should() -> None:
    bad = [{"name": "x", "colour": "red"}, {"name": "x", "permissionMode": "yolo"}, {"name": "x", "permissionRules": {"deny": ["Bash(unclosed"]}},
           {"name": "x", "permissionRules": {"bogus": []}},
           {"name": "x", "connectors": {"srv": "on"}}, {"name": "x", "floor": {"shell": "allow"}}, {"name": "x", "hardline": []},
           {"name": "x", "protectedPaths": []}, {"name": "x", "trustExternalContent": True}, {"name": "x", "allowAllConnections": True},
           {"name": "x", "network": {"trustExternalContent": True}}, {"name": "x", "tools": {"danger": {"bogus": "off"}}},
           {"name": "x", "tools": {"groups": {"mail": "maybe"}}}, {"name": "x", "schema": "grain.preset/2"}, {"description": "no name"},
           {"name": "x", "description": "d" * 10_000}, {"name": "x", "alwaysAsk": ["a" * 70_000]}, {"name": "x", "id": "User:Bad"}]
    for doc in bad:
        with pytest.raises(presets_mod.PresetError):
            presets_mod.validate(doc)


def test_the_same_validator_runs_on_create_edit_import_and_apply() -> None:
    doc = {"name": "x", "colour": "red"}
    assert client.post("/permissions/presets", json=doc).status_code == 422
    assert client.post("/permissions/presets/import", json=doc).status_code == 422
    pid = user_preset()
    assert client.put(f"/permissions/presets/{pid}", json=doc).status_code == 422
    with appmod.db.tx() as c:  # a stored body that no longer validates is refused when applied
        c.execute("UPDATE permission_presets SET body = ? WHERE id = ?", (json.dumps({"name": "x", "permissionMode": "yolo"}), pid))
    assert apply(pid).status_code == 422


def test_a_rule_naming_no_tool_is_refused_on_save() -> None:
    assert client.post("/permissions/presets", json={"name": "x", "permissionRules": {"deny": ["no_such_tool"]}}).status_code == 422


def test_a_pattern_on_a_tool_that_never_matches_one_is_refused_on_save() -> None:
    r = client.post("/permissions/presets", json={"name": "x", "permissionRules": {"deny": ["shell_run(rm *)"]}})
    assert r.status_code == 422 and "never matches" in r.text
    assert client.post("/permissions/presets", json={"name": "x", "permissionRules": {"deny": ["shell_run", "Bash(rm *)"]}}).status_code == 200


# ---- the built-ins
def test_auto_on_an_untouched_install_changes_nothing() -> None:
    before = appmod.db.get_settings()
    r = apply("builtin:auto")
    assert r.status_code == 200 and r.json()["changes"] == {} and r.json()["loosened"] == []
    assert appmod.db.get_settings().get(permissions.KEY) == before.get(permissions.KEY)
    with appmod.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM setting_sources").fetchone()[0] == 0


def test_the_built_ins_store_the_values_they_name() -> None:
    assert apply("builtin:manual").status_code == 200 and stored()["permissionMode"] == "manual"
    assert apply("builtin:auto").status_code == 409 and stored()["permissionMode"] == "manual"  # auto is looser than manual
    r = apply("builtin:allow-all")
    assert r.status_code == 409  # always loosening from the other two
    assert apply("builtin:allow-all", confirm=r.json()["diff_hash"]).status_code == 200 and stored()["permissionMode"] == "allow_all"
    assert apply("builtin:read-only").status_code == 200
    assert stored()["permissionMode"] == "manual"
    tools = stored()["tools"]
    assert tools["shell_run"] == "off" and tools["gmail_send"] == "off" and tools["fs_edit"] == "off"
    assert "fs_glob" not in tools  # a safe tool is left alone


def test_read_only_covers_a_tool_added_after_the_preset_was_written() -> None:
    pid = user_preset(name="Locked", tools={"danger": {"writes": "off"}})
    appmod.toolbox.specs["added_later"] = ToolSpec("added_later", "test", _obj({}, []), probe, "test", "writes")
    try:
        assert apply(pid).status_code == 200
        assert stored()["tools"]["added_later"] == "off"
    finally:
        del appmod.toolbox.specs["added_later"]


def test_tools_resolve_danger_then_group_then_name() -> None:
    modes = presets_mod.tool_modes({"danger": {"writes": "off"}, "groups": {"presetgroup": "ask"}}, appmod.toolbox)
    assert modes["preset_probe"] == "ask" and modes["fs_edit"] == "off"  # the group beats the tier
    modes = presets_mod.tool_modes({"danger": {"writes": "off"}, "groups": {"presetgroup": "ask"}, "tools": {"preset_probe": "off"}}, appmod.toolbox)
    assert modes["preset_probe"] == "off"  # the name beats the group
    assert apply(user_preset(tools={"groups": {"presetgroup": "ask"}})).status_code == 200
    assert stored()["tools"] == {"preset_probe": "ask"}  # only what differs from today is written


# ---- the loosening gate
def set_state(**values: Any) -> None:
    permissions.save(appmod.db, values)


LOOSENERS = [
    ("a mode raise", {"permissionMode": "manual"}, {"permissionMode": "auto"}),
    ("a removed deny rule", {"permissionRules": {"allow": [], "ask": [], "deny": ["preset_probe"]}}, {"permissionRules": {"allow": [], "ask": [], "deny": []}}),
    ("an added allow rule", {}, {"permissionRules": {"allow": ["preset_probe"], "ask": [], "deny": []}}),
    ("a tool off to on", {"tools": {"preset_probe": "off"}}, {"tools": {"tools": {"preset_probe": "on"}}}),
    ("a removed alwaysAsk entry", {}, {"alwaysAsk": ["schedule_task"]}),
    ("a raised destructiveDefaults", {}, {"destructiveDefaults": {"files": "allow"}}),
    ("a raised unattendedApprovals", {}, {"unattendedApprovals": "ask"}),
    ("shell network opened", {}, {"network": {"shellNetwork": True}}),
]


@pytest.mark.parametrize("label,state,body", LOOSENERS, ids=[x[0] for x in LOOSENERS])
def test_a_looser_preset_is_never_applied_without_its_diff_hash(label: str, state: dict[str, Any], body: dict[str, Any]) -> None:
    set_state(**state)
    pid = user_preset(**body)
    before = appmod.db.get_settings().get(permissions.KEY)
    r = apply(pid)
    assert r.status_code == 409, label
    diff = r.json()
    assert diff["loosened"] and diff["diff_hash"] and all("old" in c and "new" in c for c in diff["changes"].values())
    assert appmod.db.get_settings().get(permissions.KEY) == before
    assert apply(pid, confirm="not-the-hash").status_code == 409
    assert appmod.db.get_settings().get(permissions.KEY) == before
    assert apply(pid, confirm=diff["diff_hash"]).status_code == 200
    assert appmod.db.get_settings().get(permissions.KEY) != before


def test_a_confirm_for_an_earlier_state_is_stale() -> None:
    pid = user_preset(permissionMode="allow_all")
    stale = apply(pid).json()["diff_hash"]
    set_state(permissionMode="manual")  # the world moved: the diff the user saw is gone
    assert apply(pid, confirm=stale).status_code == 409


def test_applying_to_an_unknown_project_or_chat_is_a_404() -> None:
    assert apply("builtin:read-only", "project", "nope").status_code == 404
    assert apply("builtin:read-only", "chat", "nope").status_code == 404


def test_a_stricter_preset_applies_without_a_click() -> None:
    set_state(permissionMode="allow_all", alwaysAsk=[])
    pid = user_preset(permissionMode="manual", alwaysAsk=["gmail_send"], destructiveDefaults={"files": "block"}, permissionRules={"allow": [], "ask": [], "deny": ["preset_probe"]})
    r = apply(pid)
    assert r.status_code == 200 and r.json()["loosened"] == []
    assert stored()["permissionMode"] == "manual" and stored()["alwaysAsk"] == ["gmail_send"]


def test_importing_never_applies() -> None:
    before = stored()["permissionMode"]
    r = client.post("/permissions/presets/import", json={"name": "Wild", "permissionMode": "allow_all"})
    assert r.status_code == 200 and r.json()["source"] == "import" and r.json()["id"].startswith("user:")
    assert stored()["permissionMode"] == before


def test_the_comparator_table_names_every_permission_key() -> None:
    assert set(permissions.DEFAULTS) <= set(permissions.COMPARATORS) | permissions.NEUTRAL
    assert not set(permissions.COMPARATORS) & permissions.NEUTRAL


def test_loosening_reads_each_key_by_what_it_opens() -> None:
    assert permissions.loosening({"permissionMode": "allow_all"}, {"permissionMode": "manual"}) == []
    assert permissions.loosening({}, {"fetchAllowlist": ["a.com"]}) == ["fetchAllowlist"]
    assert permissions.loosening({"fetchAllowlist": ["a.com", "b.com"]}, {"fetchAllowlist": ["a.com"]}) == []
    assert permissions.loosening({}, {"deskDoneGate": False}) == ["deskDoneGate"]
    assert permissions.loosening({"tools": {"x": "on"}}, {"tools": {"x": "ask"}}) == []


# ---- the floor and the hardline list stay on top
@pytest.mark.parametrize("builtin", ["builtin:auto", "builtin:manual", "builtin:allow-all", "builtin:read-only"])
def test_the_floor_and_the_hardline_list_hold_under_every_preset(builtin: str) -> None:
    r = apply(builtin)
    if r.status_code == 409:
        assert apply(builtin, confirm=r.json()["diff_hash"]).status_code == 200
    ctx = {"settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None)}
    assert guards.run_core_guards(appmod.toolbox, ctx, "shell_run", {"command": "rm -rf /"}).guard == "hardline"
    assert guards.run_core_guards(appmod.toolbox, ctx, "read_local_file", {"path": str(appmod.db.data_dir / "x")}).guard == "protected_paths"
    assert appmod.toolbox.floor_action("shell_run", {"command": "rm -rf ~/x"}, {"settings": appmod.settings()}, kind="shell") in ("card", "block")


# ---- scopes
def make_project_and_chat() -> tuple[str, str]:
    pid = appmod.projects.create("Scoped")["id"]
    return pid, appmod.convos.create(pid, "t", "m")["id"]


def test_a_project_preset_unions_its_rules_and_cannot_remove_a_global_deny() -> None:
    set_state(permissionRules={"allow": [], "ask": [], "deny": ["preset_probe"]})
    project, _ = make_project_and_chat()
    pid = user_preset(permissionRules={"allow": ["preset_probe"], "ask": [], "deny": []})
    r = apply(pid, "project", project)
    assert r.status_code == 409 and r.json()["loosened"] == ["permissionRules"]  # an added allow rule is a loosening, whatever else wins
    assert apply(pid, "project", project, confirm=r.json()["diff_hash"]).status_code == 200
    rules = permissions.get(permissions.overlay(appmod.settings(), appmod.projects.get(project)["permissions"]), "permissionRules")
    assert rules["deny"] == ["preset_probe"] and rules["allow"] == ["preset_probe"]
    assert stored()["permissionRules"]["allow"] == []  # the global store is untouched
    ctx = {"settings": permissions.overlay(appmod.settings(), appmod.projects.get(project)["permissions"]), "modes": {"preset_probe": "on"}}
    assert guards.run_core_guards(appmod.toolbox, ctx, "preset_probe", {}).guard == "deny_rules"


def test_a_chat_scope_mode_changes_the_gate_for_that_chat_only(monkeypatch: pytest.MonkeyPatch) -> None:
    set_state(permissionMode="allow_all", destructiveDefaults={"shell": "block", "files": "block", "items": "block", "send": "block"})
    project, chat = make_project_and_chat()
    other = appmod.convos.create(project, "t", "m")["id"]
    pid = user_preset(permissionMode="manual")
    assert apply(pid, "chat", chat).status_code == 200
    assert appmod.convos.get(chat, with_messages=False)["settings"]["permissions"] == {"permissionMode": "manual"}
    assert "permissions" not in appmod.convos.get(other, with_messages=False)["settings"]

    def say(conv: str) -> list[dict[str, Any]]:
        rounds = [[{"id": "c0", "name": "preset_probe", "arguments": json.dumps({"tag": conv, "destroy": True})}]]

        async def stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
            calls = rounds.pop(0) if rounds else []
            yield {"type": "delta", "text": "ok" if not calls else ""}
            yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}

        monkeypatch.setattr(llm, "stream_chat", stream)
        events: list[dict[str, Any]] = []

        async def run() -> None:
            async for ev in appmod._chat_stream(conv, appmod.ChatIn(content="go"), asyncio.Event()):
                if ev[0] == "tool_result":
                    events.append(ev[1])

        asyncio.run(run())
        return events

    assert "blocked by the user's settings" in say(chat)[0]["error"] and chat not in RAN
    assert say(other)[0]["error"] is None and other in RAN


def test_global_only_keys_are_listed_as_not_applied_at_a_narrower_scope() -> None:
    project, _ = make_project_and_chat()
    pid = user_preset(permissionMode="manual", network={"shellNetwork": False, "fetchAllowlist": []})
    diff = apply(pid, "project", project).json()
    assert diff["not_applied"] == ["shellNetwork", "fetchAllowlist"]
    assert "shellNetwork" not in diff["changes"] and stored()["shellNetwork"] is False


def test_scoped_tool_modes_are_written_to_the_scopes_own_map() -> None:
    project, chat = make_project_and_chat()
    pid = user_preset(tools={"tools": {"fs_glob": "off"}})
    assert apply(pid, "project", project).status_code == 200
    assert appmod.projects.get(project)["tools"]["fs_glob"] == "off" and "fs_glob" not in stored()["tools"]
    other = user_preset(name="Other", tools={"tools": {"fs_grep": "off"}})
    assert apply(other, "chat", chat).status_code == 200
    assert appmod.convos.get(chat, with_messages=False)["settings"]["tools"] == {"fs_grep": "off"}


def test_a_patch_cannot_set_a_chats_permissions() -> None:
    _, chat = make_project_and_chat()
    client.patch(f"/conversations/{chat}", json={"settings": {"permissions": {"permissionMode": "allow_all"}}})
    assert "permissions" not in appmod.convos.get(chat, with_messages=False)["settings"]


def test_every_gate_reads_permissions_through_the_settings() -> None:
    """The overlay reaches a gate because it reads permissions.get(cfg, key); only the writers read the stored rows directly."""
    root = Path(appmod.__file__).parent
    readers = {str(p.relative_to(root)) for p in root.rglob("*.py") if "tests" not in p.parts and "permissions.load(" in p.read_text()}
    assert readers == {"config.py", "presets.py", "extensions/defaults.py"}


# ---- operations
def test_duplicate_rename_edit_delete_export_and_import_round_trip() -> None:
    first = P.save({"name": "Careful", "permissionMode": "manual", "alwaysAsk": ["gmail_send"]})
    assert first["id"] == "user:careful" and first["source"] == "user" and not first["readonly"]
    copy = client.post(f"/permissions/presets/{first['id']}/duplicate").json()
    assert copy["id"] == "user:careful-copy" and copy["body"]["permissionMode"] == "manual"
    renamed = client.put(f"/permissions/presets/{copy['id']}", json={**copy["body"], "name": "Renamed", "permissionMode": "auto"}).json()
    assert renamed["name"] == "Renamed" and renamed["id"] == copy["id"] and renamed["body"]["permissionMode"] == "auto"
    exported = client.get(f"/permissions/presets/{first['id']}/export").json()
    assert "id" not in exported and exported["schema"] == presets_mod.SCHEMA
    imported = client.post("/permissions/presets/import", json=exported).json()
    assert imported["id"] == "user:careful-2" and imported["source"] == "import" and imported["body"]["alwaysAsk"] == ["gmail_send"]
    assert client.delete(f"/permissions/presets/{copy['id']}").status_code == 200
    assert copy["id"] not in {r["id"] for r in client.get("/permissions/presets").json()}


def test_duplicating_a_preset_whose_name_fills_the_limit_cuts_the_copy_name_to_fit() -> None:
    long = client.post("/permissions/presets", json={"name": "n" * 80}).json()
    copy = client.post(f"/permissions/presets/{long['id']}/duplicate")
    assert copy.status_code == 200 and copy.json()["name"] == "n" * 75 + " copy"


def test_built_ins_cannot_be_edited_or_deleted_but_can_be_duplicated() -> None:
    assert client.put("/permissions/presets/builtin:auto", json={"name": "x"}).status_code == 422
    assert client.delete("/permissions/presets/builtin:manual").status_code == 422
    assert client.post("/permissions/presets/builtin:read-only/duplicate").json()["body"]["tools"]["danger"]["writes"] == "off"
    assert client.get("/permissions/presets/nope").status_code == 404
    listed = client.get("/permissions/presets").json()
    assert [r["id"] for r in listed[:4]] == ["builtin:auto", "builtin:manual", "builtin:allow-all", "builtin:read-only"]


# ---- profiles
PROFILE = {"id": "cautious", "name": "Cautious", "version": "1.0.0", "apiVersion": 1, "kind": "profile",
           "contributes": {"presets": [{"id": "careful", "name": "Careful", "permissionMode": "manual"},
                                       {"id": "loose", "name": "Loose", "permissionMode": "allow_all"}],
                           "defaults": {"permissionPreset": "profile:cautious:careful"}}}


def test_a_profile_ships_read_only_presets() -> None:
    manifest = Manifest.from_dict(PROFILE)
    scope = type("S", (), {"manifest": manifest})()
    appmod.host.extensions["cautious"] = scope
    try:
        row = next(r for r in client.get("/permissions/presets").json() if r["id"] == "profile:cautious:careful")
        assert row["readonly"] and row["owner"] == "Cautious" and row["source"] == "profile"
        assert client.delete("/permissions/presets/profile:cautious:careful").status_code == 422
        assert apply("profile:cautious:careful").status_code == 200 and stored()["permissionMode"] == "manual"
    finally:
        del appmod.host.extensions["cautious"]


def test_a_manifest_preset_is_validated_with_the_preset_schema() -> None:
    for bad in ({"id": "x", "name": "x", "colour": "red"}, {"id": "X Y", "name": "x"}, {"id": "x", "name": "x", "connectors": {"s": "on"}}):
        with pytest.raises(ManifestError):
            Manifest.from_dict({**PROFILE, "contributes": {"presets": [bad]}})
    with pytest.raises(ManifestError):
        Manifest.from_dict({**PROFILE, "contributes": {"defaults": {"permissionPreset": "profile:cautious:ghost"}}})
    with pytest.raises(ManifestError):
        Manifest.from_dict({**PROFILE, "contributes": {"presets": [PROFILE["contributes"]["presets"][0], PROFILE["contributes"]["presets"][0]]}})


def shipped(manifest: Manifest) -> None:
    appmod.host.extensions[manifest.id] = type("S", (), {"manifest": manifest})()


def test_defaults_permission_preset_obeys_the_floor_rule() -> None:
    shipped(Manifest.from_dict(PROFILE))
    loose = Manifest.from_dict({**PROFILE, "id": "loosey", "contributes": {"presets": PROFILE["contributes"]["presets"], "defaults": {"permissionPreset": "builtin:allow-all"}}})
    try:
        d = Defaults(appmod.db, P.keys)
        set_state(permissionMode="auto")
        held = d.apply(loose)
        assert [c.key for c in held] == ["perm:permissionMode"] and stored()["permissionMode"] == "auto"
        d.revert("loosey")
        assert d.apply(Manifest.from_dict(PROFILE)) == [] and stored()["permissionMode"] == "manual"
        assert appmod.db.get_settings()["extDefaults"]["cautious"]["perm:permissionMode"] == {"was": "auto", "set": "manual"}
        d.revert("cautious")
        assert stored()["permissionMode"] == "manual"  # a revert never loosens: going back to auto is the user's click
    finally:
        appmod.host.extensions.pop("cautious", None)


def test_a_confirmed_defaults_preset_applies_the_looser_value() -> None:
    manifest = Manifest.from_dict({**PROFILE, "id": "loosey", "contributes": {"defaults": {"permissionPreset": "builtin:allow-all"}}})
    d = Defaults(appmod.db, P.keys)
    assert d.apply(manifest, confirmed=True) == [] and stored()["permissionMode"] == "allow_all"
    d.revert("loosey")
    assert stored()["permissionMode"] == "auto"  # a revert may tighten


# ---- the hook and the provenance
def test_preset_applied_fires_with_key_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(P.bus, "fire", lambda hook, payload: seen.append(payload) if hook == "preset.applied" else None)
    pid = user_preset(permissionMode="manual", alwaysAsk=[*permissions.DEFAULTS["alwaysAsk"], "preset_probe"])
    assert apply(pid).status_code == 200
    assert len(seen) == 1 and seen[0]["preset_id"] == pid and seen[0]["scope"] == "global" and seen[0]["by"] == "user"
    assert seen[0]["changed_keys"] == ["alwaysAsk", "permissionMode"] and seen[0]["loosened"] == []
    assert "manual" not in json.dumps(seen[0])


def test_an_apply_stamps_the_source_and_the_previous_value() -> None:
    set_state(permissionMode="auto")
    apply("builtin:manual")
    from personal_os import setting_sources
    with appmod.db.tx() as c:
        rows = setting_sources.read(c)
    assert rows[("permissionMode", "global", "")]["source"] == "preset:builtin:manual"
    assert rows[("permissionMode", "global", "")]["prev"] == "auto"
