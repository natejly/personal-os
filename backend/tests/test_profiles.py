"""Profiles and contributes.defaults: the validator, apply and revert, the permission floor, the routes.

Run: python backend/tests/test_profiles.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import permissions  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.extensions import profiles  # noqa: E402
from personal_os.extensions.defaults import Defaults  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost, builtin_manifest  # noqa: E402
from personal_os.extensions.manifest import Manifest, ManifestError, load_manifest  # noqa: E402

BASE = {"id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1}


def profile(ext_id: str = "demo", **contributes: Any) -> Manifest:
    return Manifest.from_dict({**BASE, "id": ext_id, "kind": "profile", "contributes": {"defaults": contributes}})


class ValidatorTests(unittest.TestCase):
    def test_bundled_profiles_are_valid_profiles(self) -> None:
        for pid in ("lean", "focus"):
            self.assertEqual(builtin_manifest(pid).kind, "profile")

    def test_a_profile_declares_no_code(self) -> None:
        for extra in ({"hooks": {"app.start": {}}}, {"permissions": ["db:own"]}, {"requires": {"services": ["mcp"]}},
                      {"provides": {"services": ["x"]}}, {"settings": {"a": {"type": "string"}}}, {"data": {"tables": ["t"]}},
                      {"activation": ["onSchedule"]}, {"contributes": {"tools": [{"name": "demo_x"}]}},
                      {"contributes": {"routes": {"prefix": "/demo"}}}, {"contributes": {"layout": {}}}):
            with self.assertRaises(ManifestError, msg=extra):
                Manifest.from_dict({**BASE, "kind": "profile", **extra})

    def test_a_profile_folder_holds_only_its_manifest(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="profile-"))
        self.addCleanup(shutil.rmtree, folder)
        (folder / "extension.json").write_text(json.dumps({**BASE, "kind": "profile"}))
        self.assertEqual(load_manifest(folder / "extension.json").kind, "profile")
        (folder / "extension.py").write_text("print('hi')\n")
        with self.assertRaisesRegex(ManifestError, "extension.py"):
            load_manifest(folder / "extension.json")

    def test_defaults_are_checked(self) -> None:
        for bad in ({"views": {"mail": "no"}}, {"views": ["mail"]}, {"model": ""}, {"effort": "huge"}, {"theme": "blue"},
                    {"permissions": "yolo"}, {"colour": "red"}):
            with self.assertRaises(ManifestError, msg=bad):
                profile(**bad)
        profile(views={"mail": False}, model="m", effort="low", theme="light", permissions="manual")

    def test_theme_tokens_are_plain_values_over_custom_properties(self) -> None:
        def theme(t: Any) -> Manifest:
            return Manifest.from_dict({**BASE, "kind": "profile", "contributes": {"theme": t}})
        theme({"dark": {"--bg": "#101010", "--radius": "6px"}, "light": {"--accent": "rgb(1, 2, 3)", "--edge": "calc(var(--r) * 2)"}})
        for bad in ({"sepia": {}}, {"dark": {"bg": "#000"}}, {"dark": {"--bg": "red; color: blue"}}, {"dark": {"--bg": "url(http://x)"}},
                    {"dark": {"--bg": "}body{display:none"}}, {"dark": {"--bg": "@import x"}}, {"dark": {"--bg": 3}}, {"dark": ["--bg"]},
                    {"dark": {"--bg": "x/*"}}, {"dark": {"--bg": 'image-set("https://h/x.png" 1x)'}}, {"dark": {"--bg": "src(x)"}}):
            with self.assertRaises(ManifestError, msg=bad):
                theme(bad)


class DefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="defaults-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.db = Database(self.dir)
        self.defaults = Defaults(self.db)

    def hidden(self) -> list[str]:
        return self.db.get_settings().get("hiddenViews") or []

    def test_apply_sets_the_values_and_revert_restores_them(self) -> None:
        self.db.set_settings({"hiddenViews": ["library"], "theme": "dark", "defaultModel": "a/b"})
        m = profile(views={"mail": False, "library": True}, model="c/d", effort="low", theme="light")
        self.assertEqual(self.defaults.apply(m), [])
        s = self.db.get_settings()
        self.assertEqual((s["hiddenViews"], s["theme"], s["defaultModel"], s["defaultEffort"]), (["mail"], "light", "c/d", "low"))
        self.assertTrue(self.defaults.applied("demo"))
        self.defaults.revert("demo")
        s = self.db.get_settings()
        self.assertEqual((s["hiddenViews"], s["theme"], s["defaultModel"], s["defaultEffort"]), (["library"], "dark", "a/b", "high"))
        self.assertFalse(self.defaults.applied("demo"))

    def test_revert_leaves_what_the_user_changed_since(self) -> None:
        self.defaults.apply(profile(views={"mail": False, "todos": False}, theme="light", effort="low"))
        self.db.set_settings({"theme": "system", "defaultEffort": "max"})  # the user picked their own theme and effort
        self.defaults.revert("demo")
        s = self.db.get_settings()
        self.assertEqual((s["hiddenViews"], s["theme"], s["defaultEffort"]), ([], "system", "max"))

    def test_defaults_apply_once_and_a_later_edit_survives_a_second_apply(self) -> None:
        m = profile(views={"mail": False})
        self.defaults.apply(m)
        self.db.set_settings({"hiddenViews": []})
        self.defaults.apply(m)
        self.assertEqual(self.hidden(), [])

    def test_a_value_that_already_holds_is_not_recorded_or_reverted(self) -> None:
        self.db.set_settings({"hiddenViews": ["mail"]})
        self.defaults.apply(profile(views={"mail": False}))
        self.defaults.revert("demo")
        self.assertEqual(self.hidden(), ["mail"])

    def test_overlapping_profiles_keep_what_the_other_still_wants(self) -> None:
        wide, narrow = profile("wide", views={"todos": False, "mail": False}), profile("narrow", views={"mail": False})
        self.defaults.apply(wide)
        self.defaults.apply(narrow)  # mail is already hidden: it still counts as wanted by this profile
        self.defaults.revert("wide")
        self.assertEqual(self.hidden(), ["mail"])
        self.defaults.revert("narrow")
        self.assertEqual(self.hidden(), [])

    def test_the_profile_turned_on_second_inherits_the_way_back(self) -> None:
        narrow, wide = profile("narrow", views={"mail": False}), profile("wide", views={"todos": False, "mail": False})
        self.defaults.apply(narrow)
        self.defaults.apply(wide)
        self.defaults.revert("narrow")
        self.assertEqual(self.hidden(), ["mail", "todos"])
        self.defaults.revert("wide")
        self.assertEqual(self.hidden(), [])

    def test_revert_of_a_never_applied_extension_changes_nothing(self) -> None:
        self.defaults.revert("demo")
        self.assertNotIn("extDefaults", self.db.get_settings())

    def test_a_stricter_permission_preset_is_applied_and_reverted(self) -> None:
        self.assertEqual(self.defaults.apply(profile(permissions="manual")), [])
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "manual")
        self.defaults.revert("demo")
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "auto")

    def test_a_looser_permission_preset_is_held_until_confirmed(self) -> None:
        m = profile(permissions="allow_all", effort="low")
        held = self.defaults.apply(m)
        self.assertEqual([c.to_dict() for c in held], [{"key": "permissions", "was": "auto", "set": "allow_all"}])
        s = self.db.get_settings()
        self.assertEqual((permissions.load(s)["permissionMode"], s["defaultEffort"]), ("auto", "low"))  # the rest still applied
        self.defaults.revert("demo")
        self.assertEqual(self.defaults.apply(m, confirmed=True), [])
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "allow_all")
        self.defaults.revert("demo")
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "auto")


class RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="profileroutes-")
        self.addCleanup(shutil.rmtree, self.dir)
        self.db = Database(self.dir)
        ctx = ExtensionContext(db=self.db, hidden_views=lambda: self.db.get_settings().get("hiddenViews") or (), services={},
                               settings=self.db.get_settings, set_settings=self.db.set_settings, sid=lambda p: p, wsid=lambda p: p)
        self.host, self.changes = ExtensionHost(), []
        self.host.load_builtin("lean", ctx)
        self.host.load_builtin("focus", ctx)
        self.host._add(Manifest.from_dict({**BASE, "id": "wide", "kind": "profile", "contributes": {"defaults": {"permissions": "allow_all"}}}), ctx=ctx)
        self.host._add(Manifest.from_dict({**BASE, "id": "plain"}), ctx=ctx)
        app = FastAPI()
        app.include_router(profiles.router(self.host, self.changes.append))
        self.client = TestClient(app)

    def listed(self) -> dict[str, Any]:
        return {p["id"]: p for p in self.client.get("/profiles").json()}

    def test_lists_only_profiles_with_their_state_and_theme(self) -> None:
        self.assertEqual(set(self.listed()), {"lean", "focus", "wide"})
        self.assertEqual({k: v["enabled"] for k, v in self.listed().items()}, {"lean": False, "focus": False, "wide": False})

    def test_enable_applies_and_disable_reverts(self) -> None:
        self.assertEqual(self.client.post("/profiles/focus/enable").json(), {"held": []})
        self.assertTrue(self.listed()["focus"]["enabled"])
        s = self.db.get_settings()
        self.assertEqual((s["hiddenViews"], s["defaultEffort"]), (["todos", "mail"], "low"))
        self.assertEqual(self.client.post("/profiles/focus/disable").status_code, 200)
        s = self.db.get_settings()
        self.assertEqual((s["hiddenViews"], s["defaultEffort"], self.listed()["focus"]["enabled"]), ([], "high", False))
        self.assertEqual(len(self.changes), 2)

    def test_enable_reports_ext_changed_for_the_profile_and_the_views_it_flips(self) -> None:
        fired: list[Any] = []
        self.host.fire = lambda hook, payload=None: fired.append((hook, payload))  # type: ignore[method-assign]
        self.client.post("/profiles/lean/enable")
        self.assertIn(("ext.changed", {"id": "lean", "enabled": True}), fired)

    def test_a_looser_preset_needs_an_explicit_confirm(self) -> None:
        held = self.client.post("/profiles/wide/enable").json()["held"]
        self.assertEqual(held, [{"key": "permissions", "was": "auto", "set": "allow_all"}])
        self.assertFalse(self.listed()["wide"]["enabled"])
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "auto")
        self.assertEqual(self.client.post("/profiles/wide/enable", json={"confirm": True}).json(), {"held": []})
        self.assertTrue(self.listed()["wide"]["enabled"])
        self.assertEqual(permissions.load(self.db.get_settings())["permissionMode"], "allow_all")

    def test_enabling_an_enabled_profile_changes_nothing(self) -> None:
        self.client.post("/profiles/lean/enable")
        self.db.set_settings({"hiddenViews": []})
        self.client.post("/profiles/lean/enable")
        self.assertEqual(self.db.get_settings()["hiddenViews"], [])

    def test_unknown_and_non_profile_ids_are_404(self) -> None:
        for pid in ("nope", "plain"):
            self.assertEqual(self.client.post(f"/profiles/{pid}/enable").status_code, 404)
            self.assertEqual(self.client.post(f"/profiles/{pid}/disable").status_code, 404)


class ExtensionDefaultsTests(unittest.TestCase):
    def test_an_extensions_defaults_follow_its_enabled_state(self) -> None:
        folder = tempfile.mkdtemp(prefix="extdefaults-")
        self.addCleanup(shutil.rmtree, folder)
        db = Database(folder)
        db.set_settings({"hiddenViews": ["demo"]})
        ctx = ExtensionContext(db=db, hidden_views=lambda: db.get_settings().get("hiddenViews") or (), services={},
                               settings=db.get_settings, set_settings=db.set_settings, sid=lambda p: p, wsid=lambda p: p)
        host = ExtensionHost()
        host._add(Manifest.from_dict({**BASE, "contributes": {"defaults": {"effort": "low", "permissions": "allow_all"}}}), ctx=ctx)
        db.set_settings({"hiddenViews": []})
        host.refresh_enabled()  # turned on: the effort applies, the looser preset is skipped without a click
        s = db.get_settings()
        self.assertEqual((s["defaultEffort"], permissions.load(s)["permissionMode"]), ("low", "auto"))
        db.set_settings({"hiddenViews": ["demo"]})
        host.refresh_enabled()
        self.assertEqual(db.get_settings()["defaultEffort"], "high")


if __name__ == "__main__":
    unittest.main()
