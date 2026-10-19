"""GET /extensions/catalog: installed, available (bundled and from a remote index) and services; the consent for a bundled extension.
The index is a local file: no network.

Run: python -m pytest backend/tests/test_extension_catalog.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extcatalog-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os.extensions import catalog_index  # noqa: E402
from personal_os.extensions.authoring.scaffold import scaffold  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
review = appmod.ext_review


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
    appmod.db.set_settings({"extensionCatalogUrl": appmod.llm.DEFAULT_SETTINGS["extensionCatalogUrl"],
                            "hiddenViews": [v for v in appmod.settings()["hiddenViews"] if not v.startswith("xc-")]})
    catalog_index.clear_cache()


def use_index(tmp: Path, entries: Any) -> str:
    path = tmp / "catalog.json"
    path.write_text(json.dumps(entries), "utf-8")
    client.put("/settings", json={"extensionCatalogUrl": str(path)})
    return str(path)


def ids(cards: list[dict[str, Any]]) -> list[str]:
    return [c["id"] for c in cards]


def test_the_default_catalog_url_is_a_setting() -> None:
    assert "examples/extensions/catalog.json" in client.get("/settings").json()["extensionCatalogUrl"]


def test_bundled_extensions_split_into_installed_available_and_services(tmp_path: Path) -> None:
    use_index(tmp_path, [])
    cat = client.get("/extensions/catalog").json()
    assert ids(cat["services"]) == ["google"] and "catalogError" not in cat
    everyone = ids(cat["installed"]) + ids(cat["available"])
    assert "google" not in everyone and "calendar" in everyone and "health" in everyone
    assert all(c["enabled"] for c in cat["installed"] if c["tier"] == "builtin") and not any(c["enabled"] for c in cat["available"])
    card = next(c for c in everyone_cards(cat) if c["id"] == "calendar")
    assert card["source"] == "bundled" and card["tier"] == "builtin" and card["requires"]["services"][0] == "google"
    assert {"tools", "views", "slots", "hooks", "commands", "settingsFields", "processes"} <= set(card["contributes"])
    assert card["permissionText"] and card["contextCostTokens"] > 0 and card["state"] in ("ready", "disabled-dependency")
    assert set(cat["services"][0]["requiredBy"]) >= {"calendar", "mail"}


def everyone_cards(cat: dict[str, Any]) -> list[dict[str, Any]]:
    return [*cat["installed"], *cat["available"]]


def test_a_remote_index_adds_available_entries_and_drops_bad_ones(tmp_path: Path) -> None:
    use_index(tmp_path, [
        {"id": "xc-reading", "name": "Reading list", "description": "Track reading.", "url": "https://example.com/r.git", "ref": "v1", "subdir": "ext",
         "author": "someone", "verified": False},
        {"id": "calendar", "name": "Fake", "url": "https://example.com/c.git"},  # collides with a bundled id
        {"id": "Bad Id", "url": "https://example.com/x.git"}, {"id": "xc-nourl"}, {"id": "xc-dash", "url": "-oProxyCommand=x"},
        {"id": "xc-http", "url": "http://example.com/x.git"}, {"id": "xc-up", "url": "https://e.com/x.git", "subdir": "../x"}, "junk", 3,
        {"id": "xc-reading", "url": "https://example.com/dup.git"}])
    cat = client.get("/extensions/catalog").json()
    entry = next(c for c in cat["available"] if c["id"] == "xc-reading")
    assert entry["source"] == "catalog" and entry["url"] == "https://example.com/r.git" and entry["ref"] == "v1" and entry["subdir"] == "ext"
    assert entry["verified"] is False and entry["author"] == "someone" and entry["enabled"] is False
    assert ids(cat["available"]).count("xc-reading") == 1 and "xc-up" in ids(cat["available"]) and "subdir" not in next(c for c in cat["available"] if c["id"] == "xc-up")
    assert not {"xc-nourl", "xc-dash", "xc-http", "Bad Id"} & set(ids(cat["available"]))
    assert next(c for c in everyone_cards(cat) if c["id"] == "calendar")["source"] == "bundled"


def test_an_index_in_an_object_and_a_file_url_both_load(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"extensions": [{"id": "xc-obj", "url": "https://example.com/o.git"}]}), "utf-8")
    client.put("/settings", json={"extensionCatalogUrl": path.as_uri()})
    assert "xc-obj" in ids(client.get("/extensions/catalog").json()["available"])


def test_a_failing_index_is_reported_not_raised(tmp_path: Path) -> None:
    client.put("/settings", json={"extensionCatalogUrl": str(tmp_path / "missing.json")})
    r = client.get("/extensions/catalog")
    assert r.status_code == 200 and "FileNotFoundError" in r.json()["catalogError"] and r.json()["available"]
    (tmp_path / "bad.json").write_text("{not json", "utf-8")
    client.put("/settings", json={"extensionCatalogUrl": str(tmp_path / "bad.json")})
    assert client.get("/extensions/catalog").json()["catalogError"]
    use_index(tmp_path, {"extensions": "nope"})
    assert "must be a list" in client.get("/extensions/catalog").json()["catalogError"]
    client.put("/settings", json={"extensionCatalogUrl": "ftp://example.com/c.json"})
    assert "unsupported" in client.get("/extensions/catalog").json()["catalogError"]


def test_a_remote_index_is_cached_for_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(url: str) -> Any:
        calls.append(url)
        return [{"id": "xc-cached", "url": "https://example.com/c.git"}]
    monkeypatch.setattr(catalog_index, "_read_index", fake)
    client.put("/settings", json={"extensionCatalogUrl": "https://catalog.example.com/c.json"})
    assert "xc-cached" in ids(client.get("/extensions/catalog").json()["available"])
    client.get("/extensions/catalog")
    assert calls == ["https://catalog.example.com/c.json"]
    monkeypatch.setattr(catalog_index.time, "monotonic", lambda: 1e12)
    client.get("/extensions/catalog")
    assert len(calls) == 2


def installed_folder(tmp: Path, ext_id: str = "xc-folder", **edit: Any) -> Path:
    src = scaffold(ext_id, tmp / "src")
    manifest = json.loads((src / "extension.json").read_text("utf-8"))
    manifest["contributes"] = {**manifest.get("contributes", {}), **edit.pop("contributes", {})}
    manifest.update(edit)
    (src / "extension.json").write_text(json.dumps(manifest), "utf-8")
    assert client.post("/extensions/install", json={"path": str(src)}).status_code == 200
    r = client.post(f"/extensions/install/{ext_id}/confirm")
    assert r.status_code == 200, r.text
    return review.drafts.installed / ext_id


def test_an_installed_folder_extension_is_listed_with_its_pin_and_the_ui_flag(tmp_path: Path) -> None:
    installed_folder(tmp_path, contributes={"views": [{"id": "xc-view", "label": "X"}]})
    cat = client.get("/extensions/catalog").json()
    card = next(c for c in cat["installed"] if c["id"] == "xc-folder")
    assert card["tier"] == "installed" and card["source"] == "folder" and card["enabled"] is False and card["pinned"]["sha256"]
    assert card["uiNotLoaded"] is True and card["contributes"]["views"] == ["xc-view"] and card["state"] == "ready"
    assert "xc-folder" not in ids(cat["available"])


def test_an_installed_extension_without_a_readable_manifest_is_invalid(tmp_path: Path) -> None:
    folder = installed_folder(tmp_path)
    (folder / "extension.json").write_text("{broken", "utf-8")
    card = next(c for c in client.get("/extensions/catalog").json()["installed"] if c["id"] == "xc-folder")
    assert card["state"] == "invalid" and card["reason"] and card["enabled"] is False


def test_an_enabled_extension_whose_required_extension_is_off_is_flagged(tmp_path: Path) -> None:
    installed_folder(tmp_path, requires={"extensions": ["todos"]})
    hidden = [v for v in appmod.settings()["hiddenViews"] if v != "todos"]
    appmod.db.set_settings({"hiddenViews": [*hidden, "todos"]})
    on_loop(review.enable("xc-folder", []))
    card = next(c for c in client.get("/extensions/catalog").json()["installed"] if c["id"] == "xc-folder")
    assert card["enabled"] is True and card["state"] == "disabled-dependency" and "todos" in card["reason"]


def test_the_context_cost_counts_tool_schemas_and_volatile_context() -> None:
    m = catalog_index.bundled_manifests()
    base = catalog_index.context_cost(m["calendar"], catalog_index.BUILTIN_ROOT / "calendar")
    assert base > 0
    from personal_os.extensions.manifest import Manifest
    plain = Manifest.from_dict({"id": "xc-cost", "name": "C", "version": "1.0.0", "apiVersion": 1})
    volatile = Manifest.from_dict({"id": "xc-cost", "name": "C", "version": "1.0.0", "apiVersion": 1, "permissions": ["context:volatile"]})
    assert catalog_index.context_cost(plain, None) == 0 and catalog_index.context_cost(volatile, None) == catalog_index.VOLATILE_CONTEXT_TOKENS


def test_the_consent_for_a_bundled_extension_is_not_quarantined() -> None:
    c = client.get("/extensions/calendar/consent").json()
    assert c["quarantined"] is False and c["source"] == {"kind": "bundled"} and c["card"]["id"] == "calendar" and c["commands"] == []
    assert c["typosquat"] is None and c["findings"] == [] and len(c["sha256"]) == 64 and c["card"]["permissionText"]
    assert client.get("/extensions/xc-none/consent").status_code == 404


def test_permission_text_is_plain_language() -> None:
    from personal_os.extensions.manifest import Manifest
    m = Manifest.from_dict({"id": "xc-text", "name": "T", "version": "1.0.0", "apiVersion": 1, "permissions": ["db:own", "hooks:gate", "service:mcp"],
                            "hooks": {"tool.permission": {}}})
    text = " ".join(catalog_index.permission_text(m))
    assert "own data" in text and "block a tool call" in text and "mcp service" in text and "tool.permission" in text
