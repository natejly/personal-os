"""Installing an extension from outside the app: quarantine, consent facts, refusals, the pin, update and delete.
No network: sources are a local git repository, a local zip and a local folder.

Run: python -m pytest backend/tests/test_extension_install.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extinstall-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.extensions import install  # noqa: E402
from personal_os.extensions.authoring.scaffold import scaffold  # noqa: E402
from personal_os.extensions.catalog_index import typosquat  # noqa: E402
from personal_os.extensions.drafts import Drafts  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
review = appmod.ext_review
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


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
    shutil.rmtree(review.drafts.installed, ignore_errors=True)
    shutil.rmtree(appmod.db.data_dir / install.QUARANTINE, ignore_errors=True)
    appmod.db.set_settings({"hiddenViews": [v for v in appmod.settings()["hiddenViews"] if not v.startswith("xi-")]})


def make(tmp: Path, ext_id: str = "xi-notes", *, permissions: tuple[str, ...] = (), hooks: dict[str, Any] | None = None, readme: str = "") -> Path:
    root = scaffold(ext_id, tmp)
    manifest = json.loads((root / "extension.json").read_text("utf-8"))
    manifest["permissions"] += permissions
    manifest["hooks"] |= hooks or {}
    (root / "extension.json").write_text(json.dumps(manifest), "utf-8")
    if readme:
        (root / "README.md").write_text(readme, "utf-8")
    return root


def git_repo(root: Path) -> str:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, env=GIT_ENV, check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return run("rev-parse", "HEAD")


def zip_of(folder: Path, out: Path, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(out, "w") as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder).as_posix())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return out


# ---- sources ----
def test_a_local_git_repository_is_cloned_into_quarantine_with_its_commit(tmp_path: Path) -> None:
    src = make(tmp_path / "src", readme="# Notes\n\n```bash\npip install requests\n```\n\n$ make setup\nplain prose here\n")
    commit = git_repo(src)
    r = client.post("/extensions/install", json={"path": str(src)})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["id"] == "xi-notes" and c["quarantined"] is True and c["source"]["kind"] == "git" and c["source"]["commit"] == commit
    assert c["card"]["enabled"] is False and c["card"]["tier"] == "installed" and len(c["sha256"]) == 64
    assert c["commands"] == ["pip install requests", "$ make setup"] and c["typosquat"] is None
    assert not (appmod.db.data_dir / "extensions" / "xi-notes").exists()  # nothing is installed before confirm
    assert not (appmod.db.data_dir / install.QUARANTINE / "xi-notes" / ".git").exists()
    assert any("permission" in t.lower() or "tool" in t.lower() for t in c["card"]["permissionText"])


def test_a_git_ref_pins_the_clone(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    first = git_repo(src)
    (src / "extra.txt").write_text("later", "utf-8")
    subprocess.run(["git", "add", "-A"], cwd=src, env=GIT_ENV, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "two"], cwd=src, env=GIT_ENV, check=True)
    c = client.post("/extensions/install", json={"path": str(src), "ref": first}).json()
    assert c["source"]["commit"] == first and not (appmod.db.data_dir / install.QUARANTINE / "xi-notes" / "extra.txt").exists()


def test_a_zip_is_extracted_and_a_single_top_folder_is_unwrapped(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    archive = tmp_path / "wrapped.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for p in src.rglob("*"):
            if p.is_file():
                z.write(p, "xi-notes-main/" + p.relative_to(src).as_posix())
    c = client.post("/extensions/install", json={"path": str(archive)}).json()
    assert c["id"] == "xi-notes" and c["source"]["kind"] == "zip"


def test_a_subdir_of_a_repository_is_installed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make(repo / "exts")
    (repo / "README.md").write_text("top", "utf-8")
    git_repo(repo)
    c = client.post("/extensions/install", json={"path": str(repo), "subdir": "exts/xi-notes"}).json()
    assert c["id"] == "xi-notes" and c["source"]["subdir"] == "exts/xi-notes"
    assert client.post("/extensions/install", json={"path": str(repo), "subdir": "../etc"}).status_code == 400


def test_a_folder_is_copied_and_a_symlink_is_refused(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    assert client.post("/extensions/install", json={"path": str(src)}).json()["source"]["kind"] == "folder"
    (tmp_path / "secret.txt").write_text("x", "utf-8")
    (src / "link.txt").symlink_to(tmp_path / "secret.txt")
    r = client.post("/extensions/install", json={"path": str(src)})
    assert r.status_code == 400 and "link" in r.json()["detail"]


# ---- refusals ----
def test_a_zip_with_a_path_that_leaves_the_folder_is_refused(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    bad = zip_of(src, tmp_path / "bad.zip", {"../escape.txt": b"x"})
    r = client.post("/extensions/install", json={"path": str(bad)})
    assert r.status_code == 400 and "unsafe path" in r.json()["detail"]
    assert not (tmp_path / "escape.txt").exists()
    absolute = zip_of(src, tmp_path / "abs.zip", {"/tmp/xi-abs.txt": b"x"})
    assert client.post("/extensions/install", json={"path": str(absolute)}).status_code == 400


def test_a_zip_with_a_symlink_entry_is_refused(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(src / "extension.json", "extension.json")
        info = zipfile.ZipInfo("evil")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    r = client.post("/extensions/install", json={"path": str(archive)})
    assert r.status_code == 400 and "link" in r.json()["detail"]


def test_a_zip_that_expands_past_the_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make(tmp_path / "src")
    big = zip_of(src, tmp_path / "big.zip", {"blob.bin": b"\0" * 5_000_000})
    monkeypatch.setattr(install, "MAX_UNZIPPED_BYTES", 1_000_000)
    r = client.post("/extensions/install", json={"path": str(big)})
    assert r.status_code == 400 and "expands" in r.json()["detail"]


def test_an_id_that_a_built_in_extension_has_is_refused(tmp_path: Path) -> None:
    src = make(tmp_path / "src", "xi-notes")
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    (src / "extension.json").write_text(json.dumps({**manifest, "id": "calendar"}), "utf-8")
    r = client.post("/extensions/install", json={"path": str(src)})
    assert r.status_code == 409 and "ships with Grain" in r.json()["detail"]
    assert not (appmod.db.data_dir / install.QUARANTINE / "calendar").exists()


def test_a_manifest_that_does_not_validate_is_refused(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    manifest["bogus"] = 1
    (src / "extension.json").write_text(json.dumps(manifest), "utf-8")
    r = client.post("/extensions/install", json={"path": str(src)})
    assert r.status_code == 400 and "not valid" in r.json()["detail"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert client.post("/extensions/install", json={"path": str(empty)}).status_code == 400


def test_bad_sources_are_refused(tmp_path: Path) -> None:
    for body in ({}, {"url": "http://example.com/x.git"}, {"url": "ext::sh -c id"}, {"url": "-oProxyCommand=x"}, {"path": str(tmp_path / "missing")},
                 {"url": "a", "path": "b"}, {"path": str(tmp_path), "ref": "-x"}):
        assert client.post("/extensions/install", json=body).status_code == 400, body


def test_findings_from_the_check_come_with_the_consent(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    (src / "extension.py").write_text((src / "extension.py").read_text("utf-8") + "\nimport personal_os.db\n", "utf-8")
    c = client.post("/extensions/install", json={"path": str(src)}).json()
    assert any(f["level"] == "error" and "forbidden-import" in f["message"] for f in c["findings"])
    r = client.post("/extensions/install/xi-notes/confirm")
    assert r.status_code == 409 and not (review.drafts.installed / "xi-notes").exists()


def test_typosquats_are_within_two_edits_of_a_known_id() -> None:
    assert typosquat("calender", {"calendar", "mail"}) == {"similarTo": "calendar"}
    assert typosquat("calendar", {"calendar"}) is None
    assert typosquat("zz", {"ab"}) is None and typosquat("elephant", {"calendar"}) is None


def test_the_consent_names_a_look_alike_of_a_built_in(tmp_path: Path) -> None:
    src = make(tmp_path / "src", "xi-calendar")
    assert client.post("/extensions/install", json={"path": str(src)}).json()["typosquat"] is None
    other = make(tmp_path / "other", "calender")
    assert client.post("/extensions/install", json={"path": str(other)}).json()["typosquat"] == {"similarTo": "calendar"}


def test_discarding_removes_the_quarantine(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    client.post("/extensions/install", json={"path": str(src)})
    assert client.delete("/extensions/install/xi-notes").json() == {"ok": True}
    assert not (appmod.db.data_dir / install.QUARANTINE / "xi-notes").exists()
    assert client.delete("/extensions/install/xi-notes").status_code == 404


# ---- confirm, pin, load ----
def confirm(src: Path) -> dict[str, Any]:
    assert client.post("/extensions/install", json={"path": str(src)}).status_code == 200
    r = client.post("/extensions/install/xi-notes/confirm")
    assert r.status_code == 200, r.text
    return r.json()["card"]


def test_confirm_installs_disabled_with_a_pin_and_it_is_not_a_proposal(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    git_repo(src)
    card = confirm(src)
    assert card["id"] == "xi-notes" and card["enabled"] is False and card["state"] == "ready" and card["source"] == "git"
    assert card["pinned"]["sha256"] and card["pinned"]["sha"]
    assert json.loads((review.drafts.installed / "xi-notes" / "extension.json").read_text("utf-8"))["enabled"] is False
    assert "xi-notes" in appmod.settings()["hiddenViews"] and "xi-notes" not in appmod.host.extensions
    listing = client.get("/extensions").json()
    assert listing["proposals"] == [] and [c["id"] for c in listing["installed"]] == ["xi-notes"]
    assert review.drafts.intact("xi-notes") and not (appmod.db.data_dir / install.QUARANTINE / "xi-notes").exists()
    pin = json.loads((review.drafts.installed / ".pins" / "xi-notes.json").read_text("utf-8"))
    assert pin["source"]["url"] == str(src) and pin["installedAt"] and pin["sourceSha256"] == card["pinned"]["sha256"]


def test_a_changed_tree_is_needs_review_and_does_not_load(tmp_path: Path) -> None:
    confirm(make(tmp_path / "src"))
    (review.drafts.installed / "xi-notes" / "extension.py").write_text("# tampered\n", "utf-8")
    assert install.tree_state(review.drafts, "xi-notes")[0] == "needs-review"
    cat = client.get("/extensions/catalog").json()
    card = next(c for c in cat["installed"] if c["id"] == "xi-notes")
    assert card["state"] == "needs-review" and card["enabled"] is False and card["reason"]
    assert client.post("/extensions/xi-notes/enable").status_code == 409  # approve() refuses files that are not the pinned ones


def test_the_tree_changing_in_quarantine_refuses_confirm(tmp_path: Path) -> None:
    client.post("/extensions/install", json={"path": str(make(tmp_path / "src"))})
    (appmod.db.data_dir / install.QUARANTINE / "xi-notes" / "extension.py").write_text("# swapped\n", "utf-8")
    assert client.post("/extensions/install/xi-notes/confirm").status_code == 409
    assert client.post("/extensions/install/nope/confirm").status_code == 404


def test_installing_an_installed_id_again_points_at_update(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    confirm(src)
    assert client.post("/extensions/install", json={"path": str(src)}).status_code == 409


# ---- update ----
def test_update_with_the_same_tree_says_up_to_date(tmp_path: Path) -> None:
    confirm(make(tmp_path / "src"))
    c = client.post("/extensions/xi-notes/update").json()
    assert c["card"]["updateAvailable"] is False and c["quarantined"] is False and c["findings"][0]["message"] == "Already up to date"


def test_update_that_adds_a_permission_needs_consent_and_stays_disabled(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    confirm(src)
    on_loop(review.enable("xi-notes", []))
    assert client.get("/extensions/catalog").json()["installed"][0]["enabled"] is True
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    manifest["permissions"] += ["conversation:read"]
    manifest["version"] = "1.1.0"
    (src / "extension.json").write_text(json.dumps(manifest), "utf-8")
    c = client.post("/extensions/xi-notes/update").json()
    assert c["quarantined"] is True and c["card"]["updateAvailable"] is True and c["changes"]["permissionsAdded"] == ["conversation:read"]
    # nothing changes until the user confirms
    assert json.loads((review.drafts.installed / "xi-notes" / "extension.json").read_text("utf-8"))["version"] == "0.1.0"
    assert client.get("/extensions/catalog").json()["installed"][0]["enabled"] is True
    card = client.post("/extensions/install/xi-notes/confirm").json()["card"]
    assert card["version"] == "1.1.0" and card["enabled"] is False and "conversation:read" in card["permissions"]


def test_update_that_keeps_the_permissions_keeps_the_enabled_state(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    confirm(src)
    on_loop(review.enable("xi-notes", []))
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    manifest["version"] = "1.0.1"
    (src / "extension.json").write_text(json.dumps(manifest), "utf-8")
    c = client.post("/extensions/xi-notes/update").json()
    assert c["changes"] == {"permissionsAdded": [], "hooksAdded": [], "toolsAdded": []}
    card = client.post("/extensions/install/xi-notes/confirm").json()["card"]
    assert card["version"] == "1.0.1" and card["enabled"] is True


def test_update_needs_a_recorded_source() -> None:
    assert client.post("/extensions/nothere/update").status_code == 404


# ---- delete ----
def test_delete_removes_the_folder_the_data_and_the_owned_tables(tmp_path: Path) -> None:
    src = make(tmp_path / "src")
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    manifest["data"] = {"tables": ["xi_notes_rows", "messages"]}
    (src / "extension.json").write_text(json.dumps(manifest), "utf-8")
    (src / "migrations").mkdir(exist_ok=True)
    confirm(src)
    data = appmod.db.data_dir / "ext" / "xi-notes"
    data.mkdir(parents=True, exist_ok=True)
    (data / "f.txt").write_text("x", "utf-8")
    with appmod.db.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS xi_notes_rows(id INTEGER)")
    kept = client.delete("/extensions/xi-notes?keep_data=true").json()
    assert kept["ok"] and kept["tablesKept"] == ["xi_notes_rows", "messages"] and not (review.drafts.installed / "xi-notes").exists() and data.exists()
    confirm(src)
    gone = client.delete("/extensions/xi-notes?keep_data=false").json()
    assert gone["tablesDropped"] == ["xi_notes_rows"] and gone["tablesKept"] == ["messages"] and not data.exists()
    with appmod.db.tx() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'xi_notes_rows'").fetchall()
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages'").fetchall()


def test_a_bundled_extension_cannot_be_deleted_and_an_unknown_one_is_404() -> None:
    assert client.delete("/extensions/calendar").status_code == 400
    assert client.delete("/extensions/xi-none").status_code == 404


def test_find_commands_skips_prose_and_other_languages(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Install it:\n\n```json\n{\"a\": 1}\n```\n\n```sh\nnpm install\n# comment\n```\n\nnpm is great.\ngit clone x\n", "utf-8")
    (tmp_path / "notes.md").write_text("$ ignored\n", "utf-8")
    assert install.find_commands(tmp_path) == ["npm install", "# comment", "git clone x"]


def test_a_drafts_instance_on_another_folder_reads_the_same_pin(tmp_path: Path) -> None:
    confirm(make(tmp_path / "src"))
    assert Drafts(appmod.db.data_dir).intact("xi-notes")
