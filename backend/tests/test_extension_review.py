"""Agent-authored extensions (design doc 6.3, 7.2): ext_propose, the Review flow, the disabled install, the gates on enabling
and the content-hash pin; the author-extension skill.

Run: python -m pytest backend/tests/test_extension_review.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extreview-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import autoreview, mac, permrules, subagents, tools  # noqa: E402
from personal_os.extensions.authoring import skill  # noqa: E402
from personal_os.extensions.authoring.scaffold import scaffold  # noqa: E402
from personal_os.extensions.drafts import DraftError  # noqa: E402
from personal_os.mcp_servers import RESERVED_TOOL_NAMES  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
review = appmod.ext_review
DATA = appmod.db.data_dir


def on_loop(coro: Any) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, appmod._loop).result(timeout=30)  # type: ignore[arg-type]


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    with client:
        yield


@pytest.fixture(autouse=True)
def _clean():  # type: ignore[no-untyped-def]
    yield
    for ext_id in review.drafts.installed_ids():
        on_loop(review.stand_down(ext_id))
    for folder in (review.drafts.installed, review.drafts.drafts):
        shutil.rmtree(folder, ignore_errors=True)
    appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings()["hiddenViews"] if not v.startswith("rl-")]})


def draft(ext_id: str = "rl-notes", *, permissions: tuple[str, ...] = (), hooks: dict[str, Any] | None = None, code: str = "") -> Path:
    """A valid draft in the staging folder, as the assistant would leave it (the scaffold's tool is `<ident>_note`)."""
    root = scaffold(ext_id, review.drafts.drafts)
    manifest = json.loads((root / "extension.json").read_text("utf-8"))
    manifest["permissions"] += permissions
    manifest["hooks"] |= hooks or {}
    (root / "extension.json").write_text(json.dumps(manifest), "utf-8")
    if code:
        (root / "extension.py").write_text((root / "extension.py").read_text("utf-8") + code, "utf-8")
    return root


def propose(ext_id: str = "rl-notes", **ctx: Any) -> Any:
    return on_loop(appmod.toolbox.call("ext_propose", {"id": ext_id}, {"conversation_id": None, "project_id": None, "taint_sources": [], **ctx}))


def accept(ext_id: str = "rl-notes", digest: str | None = None) -> Any:
    """Accepts the proposal as listed, unless a digest says which one the user reviewed."""
    if digest is None:
        digest = next((p["digest"] for p in client.get("/extensions").json()["proposals"] if p["id"] == ext_id), "")
    return client.post(f"/extensions/{ext_id}/accept", json={"digest": digest})


def enable(ext_id: str = "rl-notes", grants: list[str] | None = None) -> Any:
    return client.post(f"/extensions/{ext_id}/enable", json={"grants": grants or []})


def installed(ext_id: str = "rl-notes") -> dict[str, Any]:
    return next(e for e in client.get("/extensions").json()["installed"] if e["id"] == ext_id)


def test_a_failing_propose_keeps_the_proposal_under_review() -> None:
    root = draft()
    propose()
    before = client.get("/extensions").json()["proposals"]
    assert [p["id"] for p in before] == ["rl-notes"]
    (root / "extension.py").unlink()
    assert "found errors" in str(propose())
    assert client.get("/extensions").json()["proposals"] == before


# ---- the staging folder ----
def test_the_file_tools_may_write_the_staging_folder_and_nothing_else_in_the_data_folder(tmp_path: Path) -> None:
    assert mac.protected_reason(DATA / "ext-drafts" / "a" / "extension.py") is None
    assert mac.protected_reason(DATA / "extensions" / "a" / "extension.py")
    assert mac.protected_reason(DATA / "ext-drafts" / ".." / ".auth_token")
    link = DATA / "ext-drafts" / "link"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(DATA / ".auth_token")
    try:
        assert mac.protected_reason(link)
    finally:
        link.unlink()


# ---- ext_propose ----
def test_ext_propose_always_cards_and_no_child_or_workflow_gets_it() -> None:
    spec = appmod.toolbox.specs["ext_propose"]
    assert "ext_propose" in tools.ALWAYS_CARD and appmod.toolbox.ask_locked(spec) and "ext_propose" in RESERVED_TOOL_NAMES
    mode = appmod.toolbox.effective({"ext_propose": "on"}, None, {"ext_propose": "on"})["ext_propose"]
    assert mode == "ask" and "ext_propose" in permrules.STILL_ASK
    assert autoreview.route("allow_all", mode=mode, danger=spec.danger, question=True) == "card"
    assert "ext_propose" in subagents.CHILD_BLOCK
    assert not [n for n in appmod.toolbox.specs if "enable" in n and "ext" in n]


def test_a_missing_draft_is_refused_and_names_the_folder() -> None:
    out = propose("rl-ghost")
    assert "error" in out and str(review.drafts.drafts / "rl-ghost") in out["error"]
    assert propose("../escape")["error"].startswith("an extension id")


def test_a_draft_without_a_manifest_says_so_and_leaves_no_snapshot() -> None:
    (review.drafts.drafts / "rl-empty").mkdir(parents=True, exist_ok=True)
    out = propose("rl-empty")
    assert "no extension.json" in out["error"] and ".review" not in out["error"]
    assert review.drafts.proposal("rl-empty") is None


def test_a_checked_draft_is_queued_as_a_diff_and_nothing_is_installed() -> None:
    draft()
    out = propose()
    assert out["ok"] and out["status"] == "awaiting review"
    listing = client.get("/extensions").json()
    [proposal] = listing["proposals"]
    assert proposal["id"] == "rl-notes" and proposal["update"] is False and listing["installed"] == []
    changes = {c["path"]: c for c in proposal["changes"]}
    assert changes["extension.json"]["status"] == "added" and "+  \"id\": \"rl-notes\"" in changes["extension.json"]["diff"]
    assert "tests/test_extension.py" not in changes
    assert proposal["permissions"] == ["context:volatile", "db:own"] and proposal["contributes"]["tools"][0]["danger"] == "writes"
    assert not (review.drafts.installed / "rl-notes").exists() and not appmod.toolbox.available("rl_notes_note")


def test_the_whole_diff_is_shown_however_long_the_file() -> None:
    draft(code="\n" * 30_000 + "PAYLOAD = 1\n")
    propose()
    [proposal] = client.get("/extensions").json()["proposals"]
    assert "+PAYLOAD = 1" in next(c for c in proposal["changes"] if c["path"] == "extension.py")["diff"]


def test_a_draft_with_a_file_that_is_not_text_is_refused() -> None:
    (draft() / "blob.bin").write_bytes(b"\xff\xfe\x00")
    out = propose()
    assert "blob.bin" in out["error"] and client.get("/extensions").json()["proposals"] == []


def test_a_draft_that_fails_the_check_is_not_queued() -> None:
    root = draft(code="    import personal_os.app\n")
    out = propose()
    assert "error" in out and any("forbidden-import" in f for f in out["findings"])
    assert client.get("/extensions").json()["proposals"] == []
    (root / "extension.py").write_text((root / "extension.py").read_text("utf-8").replace("    import personal_os.app\n", ""), "utf-8")
    assert propose()["ok"]


def test_a_draft_with_a_link_or_the_wrong_id_is_refused() -> None:
    root = draft()
    (root / "leak.txt").symlink_to(DATA / ".auth_token")
    assert "symbolic links" in propose()["error"]
    (root / "leak.txt").unlink()
    manifest = json.loads((root / "extension.json").read_text("utf-8"))
    (root / "extension.json").write_text(json.dumps({**manifest, "id": "rl-other"}), "utf-8")
    assert "must have the id" in propose()["error"]
    assert client.get("/extensions").json()["proposals"] == []


# ---- accepting ----
def test_accepting_installs_the_reviewed_snapshot_disabled_and_runs_nothing() -> None:
    root = draft()
    propose()
    (root / "extension.py").write_text("raise SystemExit('changed after the review')\n", "utf-8")
    assert accept().status_code == 200
    folder = review.drafts.installed / "rl-notes"
    assert "changed after the review" not in (folder / "extension.py").read_text("utf-8")
    assert json.loads((folder / "extension.json").read_text("utf-8"))["enabled"] is False
    assert "rl-notes" in appmod.settings()["hiddenViews"] and "rl-notes" not in appmod.host.extensions
    assert not appmod.toolbox.available("rl_notes_note")
    card = installed()
    assert card["enabled"] is False and card["changed"] is False and client.get("/extensions").json()["proposals"] == []
    assert accept().status_code == 404


def test_accepting_refuses_a_proposal_that_replaced_the_one_reviewed() -> None:
    root = draft()
    propose()
    reviewed = client.get("/extensions").json()["proposals"][0]["digest"]
    (root / "extension.py").write_text((root / "extension.py").read_text("utf-8") + "\n# second version\n", "utf-8")
    propose()
    refused = accept(digest=reviewed)
    assert refused.status_code == 409 and "changed after you reviewed" in refused.json()["detail"]
    assert review.drafts.installed_ids() == [] and len(client.get("/extensions").json()["proposals"]) == 1
    assert accept().status_code == 200


def test_the_snapshot_is_checked_against_the_digest_as_it_is_copied() -> None:
    draft()
    propose()
    reviewed = client.get("/extensions").json()["proposals"][0]["digest"]
    (review.drafts._review / "rl-notes" / "files" / "extension.py").write_text("# swapped\n", "utf-8")
    with pytest.raises(DraftError, match="changed after you reviewed"):
        review.drafts.accept("rl-notes", reviewed)
    assert review.drafts.installed_ids() == [] and not (review.drafts.installed / ".staging-rl-notes").exists()


def test_rejecting_drops_the_proposal() -> None:
    draft()
    propose()
    assert client.post("/extensions/rl-notes/reject").json()["proposals"] == []
    assert accept().status_code == 404 and client.post("/extensions/rl-notes/reject").status_code == 404
    assert review.drafts.installed_ids() == []


# ---- enabling is the user's, through the API ----
def test_enabling_by_hand_loads_it_and_offers_its_tool_and_disabling_unloads_it() -> None:
    draft()
    propose()
    accept()
    assert enable().status_code == 200
    assert installed()["enabled"] is True and "rl-notes" not in appmod.settings()["hiddenViews"]
    assert appmod.toolbox.available("rl_notes_note")
    assert client.post("/extensions/rl-notes/disable").status_code == 200
    assert installed()["enabled"] is False and "rl-notes" not in appmod.host.extensions and not appmod.toolbox.available("rl_notes_note")
    assert client.post("/dev/extensions/rl-notes/enable").status_code == 404  # the dev panel cannot bring it back past the gates
    assert enable().status_code == 200 and appmod.toolbox.available("rl_notes_note")


def test_an_unknown_extension_cannot_be_enabled() -> None:
    assert enable("rl-ghost").status_code == 404 and client.post("/extensions/rl-ghost/disable").status_code == 404


def test_a_dev_loaded_or_built_in_id_that_is_not_installed_cannot_be_enabled() -> None:
    assert enable("todos").status_code == 404


def test_a_draft_id_follows_the_same_rule_as_ext_new() -> None:
    assert "starts with a letter" in propose("1abc")["error"]


# ---- the drafting session's provenance ----
def test_a_source_that_tainted_the_session_twice_is_listed_once() -> None:
    draft()
    propose(tainted=True, taint_sources=["fs_grep", "fs_grep", "fetch_url"])
    accept()
    assert installed()["taintSources"] == ["fs_grep", "fetch_url"]


def test_a_tainted_session_may_not_hold_prompt_inject_or_hooks_gate() -> None:
    draft(permissions=("prompt:inject",))
    propose(tainted=True, taint_sources=["fetch_url"])
    accept()
    card = installed()
    assert card["tainted"] is True and card["taintSources"] == ["fetch_url"] and card["refused"] == ["prompt:inject"]
    refused = enable()
    assert refused.status_code == 409 and "fetch_url" in refused.json()["detail"] and "prompt:inject" in refused.json()["detail"]
    assert "rl-notes" not in appmod.host.extensions


def test_a_clean_session_may_hold_prompt_inject() -> None:
    draft(permissions=("prompt:inject",))
    propose()
    accept()
    assert installed()["tainted"] is False and installed()["refused"] == [] and installed()["tier"] == "user"
    assert enable().status_code == 200


def test_a_gating_hook_needs_a_named_grant_even_from_a_clean_session() -> None:
    draft(permissions=("hooks:gate",), hooks={"tool.pre": {}}, code='    api.on("tool.pre", lambda p: None)\n')
    propose()
    accept()
    assert installed()["needsGrant"] == ["hooks:gate"]
    assert enable().status_code == 409 and enable(grants=["memory:gate"]).status_code == 409
    assert "rl-notes" not in appmod.host.extensions
    assert enable(grants=["hooks:gate"]).status_code == 200
    assert "tool.pre" in {s.hook for s in appmod.host.bus.subscriptions("rl-notes")}


def test_a_tainted_session_with_a_gating_hook_is_refused_even_with_the_grant() -> None:
    draft(permissions=("hooks:gate",), hooks={"tool.pre": {}}, code='    api.on("tool.pre", lambda p: None)\n')
    propose(tainted=True, taint_sources=["gmail_read"])
    accept()
    assert enable(grants=["hooks:gate"]).status_code == 409 and "rl-notes" not in appmod.host.extensions


# ---- the content pin ----
def test_a_changed_installed_file_cannot_be_enabled() -> None:
    draft()
    propose()
    accept()
    (review.drafts.installed / "rl-notes" / "extension.py").write_text("# edited by hand\n", "utf-8")
    assert installed()["changed"] is True
    refused = enable()
    assert refused.status_code == 409 and "not the ones you accepted" in refused.json()["detail"]


def test_startup_loads_what_was_enabled_and_drops_a_changed_extension_back_to_disabled() -> None:
    for ext_id in ("rl-kept", "rl-edited"):
        draft(ext_id)
        propose(ext_id)
        accept(ext_id)
        assert enable(ext_id).status_code == 200
        on_loop(appmod.host.unload(ext_id))  # as after a restart: enabled in settings, nothing loaded
        appmod.toolbox.drop_extension_tools([f"{ext_id.replace('-', '_')}_note"])
    (review.drafts.installed / "rl-edited" / "extension.py").write_text("# edited by hand\n", "utf-8")
    on_loop(review.load_installed())
    assert "rl-kept" in appmod.host.extensions and appmod.host.extensions["rl-kept"].enabled()
    assert "rl-edited" not in appmod.host.extensions and "rl-edited" in appmod.settings()["hiddenViews"]


def test_a_new_proposal_for_an_enabled_extension_drops_it_to_disabled_until_the_user_enables_it_again() -> None:
    root = draft()
    propose()
    accept()
    enable()
    (root / "extension.json").write_text(json.dumps({**json.loads((root / "extension.json").read_text("utf-8")), "version": "0.2.0"}), "utf-8")
    propose()
    [proposal] = client.get("/extensions").json()["proposals"]
    assert proposal["update"] is True and [c["path"] for c in proposal["changes"]] == ["extension.json"]
    assert "-  \"version\": \"0.1.0\"" in proposal["changes"][0]["diff"]
    accept()
    assert "rl-notes" not in appmod.host.extensions and not appmod.toolbox.available("rl_notes_note")
    assert installed()["version"] == "0.2.0" and installed()["enabled"] is False
    assert enable().status_code == 200


def test_drafts_error_carries_findings() -> None:
    assert DraftError("x", ["a"]).findings == ["a"] and DraftError("x").findings == []


# ---- the skill ----
def test_the_author_extension_skill_waits_for_the_users_approval_with_the_sdk_reference_and_is_created_once() -> None:
    [row] = [s for s in appmod.skills.list() if s["name"] == skill.NAME]
    assert row["status"] == "candidate" and skill.NAME not in appmod.skills.approved_block() and "references/sdk.md" in row["references"] and "ext_propose" in row["procedure"]
    assert str(review.drafts.drafts) in row["procedure"] and "DRAFTS" not in row["procedure"]
    skill.ensure(appmod.skills, review.drafts.drafts)
    assert len([s for s in appmod.skills.list() if s["name"] == skill.NAME]) == 1


def test_a_deleted_author_extension_skill_is_not_offered_again() -> None:
    [row] = [s for s in appmod.skills.list() if s["name"] == skill.NAME]
    appmod.skills.delete(row["id"])
    skill.ensure(appmod.skills, review.drafts.drafts)
    assert not [s for s in appmod.skills.list() if s["name"] == skill.NAME]


def test_an_installed_extensions_widget_kinds_are_known_while_it_is_disabled_so_its_windows_survive_the_start_sweep() -> None:
    root = draft()
    manifest = json.loads((root / "extension.json").read_text("utf-8"))
    manifest["contributes"]["slots"] = {"canvas.widget": [{"id": "ext:rl-notes:board"}]}
    (root / "extension.json").write_text(json.dumps(manifest), "utf-8")
    propose()
    assert accept().status_code == 200
    assert "rl-notes" not in appmod.host.extensions
    assert "ext:rl-notes:board" in appmod._ext_ctx.widget_kinds()


def test_startup_does_not_load_an_installed_extension_whose_session_is_refused_its_permissions() -> None:
    draft(permissions=("prompt:inject",))
    propose(tainted=True, taint_sources=["fetch_url"])
    accept()
    assert enable().status_code == 409
    appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings()["hiddenViews"] if v != "rl-notes"]})  # a write that omits it
    on_loop(review.load_installed())
    assert "rl-notes" not in appmod.host.extensions and "rl-notes" in appmod.settings()["hiddenViews"]
    assert installed()["enabled"] is False


def test_a_job_no_extension_serves_can_still_be_renamed_and_disabled_but_not_enabled() -> None:
    job = client.post("/jobs", json={"name": "orphan", "prompt": "p", "cron": "0 8 * * *", "enabled": True}).json()
    with appmod.db.tx() as c:
        c.execute("UPDATE jobs SET kind='gone', cron='' WHERE id=?", (job["id"],))
    try:
        assert client.patch(f"/jobs/{job['id']}", json={"name": "renamed"}).json()["name"] == "renamed"
        assert client.patch(f"/jobs/{job['id']}", json={"enabled": False}).json()["enabled"] is False
        assert client.patch(f"/jobs/{job['id']}", json={"enabled": True}).status_code == 400
    finally:
        client.delete(f"/jobs/{job['id']}")


def test_a_draft_folder_that_is_itself_a_link_is_refused(tmp_path: Path) -> None:
    root = draft()
    outside = tmp_path / "outside"
    shutil.copytree(root, outside)
    shutil.rmtree(root)
    root.symlink_to(outside)
    try:
        assert "symbolic links" in propose()["error"]
    finally:
        root.unlink()


def test_disabling_an_installed_profile_puts_back_the_defaults_it_applied() -> None:
    root = review.drafts.drafts / "rl-look"
    root.mkdir(parents=True)
    (root / "extension.json").write_text(json.dumps({
        "id": "rl-look", "name": "Look", "version": "0.1.0", "apiVersion": 1, "kind": "profile",
        "contributes": {"defaults": {"effort": "low"}}}), "utf-8")
    before = appmod.settings()
    propose("rl-look")
    assert accept("rl-look").status_code == 200 and enable("rl-look").status_code == 200
    assert client.post("/profiles/rl-look/enable").status_code == 200
    assert appmod.settings()["defaultEffort"] == "low"
    assert client.post("/extensions/rl-look/disable").status_code == 200
    after = appmod.settings()
    assert after["defaultEffort"] == before["defaultEffort"]
    assert "rl-look" not in (after.get("extDefaults") or {})
