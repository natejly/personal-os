"""System access panel: shape, probe failures, CLI lookup, shell self-check. Everything platform-specific is mocked."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="sysacc-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import codingagents, macos, opencode, shell, system_access  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        system_access._version.cache_clear()
        self.cfg: dict = {}
        app = FastAPI()
        app.include_router(system_access.router(lambda: self.cfg, lambda: object()))
        self.c = TestClient(app)
        for target, val in [(macos, {"IS_MAC": True, "automation_status": lambda b: "granted", "full_disk_access": lambda: True,
                                     "installed_browsers": lambda: ["Safari"]}),
                            (codingagents, {"claude_binary": lambda: "/bin/claude"}),
                            (opencode, {"binary": lambda: None})]:
            for k, v in val.items():
                p = mock.patch.object(target, k, v)
                p.start()
                self.addCleanup(p.stop)
        p = mock.patch.object(system_access.subprocess, "run",
                              return_value=mock.Mock(returncode=0, stdout="1.2.3 (Claude)\nmore\n", stderr=""))
        self.run_mock = p.start()
        self.addCleanup(p.stop)


class AccessTests(_Base):
    def test_shape(self) -> None:
        d = self.c.get("/system/access").json()
        self.assertEqual(set(d), {"fullDisk", "automation", "browsers", "scope", "clis"})
        self.assertEqual(set(d["automation"]), {"finder", "systemEvents", "contacts", "calendar", "reminders"})
        self.assertEqual(d["fullDisk"], "granted")
        self.assertEqual(d["browsers"], [{"name": "Safari", "state": "granted"}] if "Safari" in macos.BROWSER_BUNDLES
                         else d["browsers"])
        # the file scope: what is off limits and what asks first, as display strings
        self.assertEqual(set(d["scope"]), {"protected", "sensitive"})
        self.assertTrue(d["scope"]["protected"][0].startswith("Grain's data folder ("))
        self.assertIn("/Applications/Grain.app", d["scope"]["protected"])
        self.assertIn("~/.ssh", d["scope"]["sensitive"])
        self.assertIn("Browser cookies and saved passwords", d["scope"]["sensitive"])
        self.assertIn(".env files", d["scope"]["sensitive"])

    def test_probe_raises_is_unknown(self) -> None:
        with mock.patch.object(macos, "automation_status", side_effect=RuntimeError("x")):
            d = self.c.get("/system/access").json()
        self.assertEqual(d["automation"]["finder"], "unknown")

    def test_fda_denied(self) -> None:
        with mock.patch.object(macos, "full_disk_access", lambda: False):
            self.assertEqual(self.c.get("/system/access").json()["fullDisk"], "denied")

    def test_fda_unknown_off_mac(self) -> None:
        with mock.patch.object(macos, "IS_MAC", False):
            self.assertEqual(self.c.get("/system/access").json()["fullDisk"], "unknown")

    def test_cli_missing_has_hint(self) -> None:
        d = self.c.get("/system/access").json()["clis"]
        self.assertEqual(d["opencode"], {"path": None, "version": None, "hint": opencode.INSTALL_HINT})
        self.assertEqual(d["claude"]["path"], "/bin/claude")
        self.assertEqual(d["claude"]["version"], "1.2.3 (Claude)")
        self.assertIn("npm i -g", d["claude"]["hint"])

    def test_version_cached(self) -> None:
        self.c.get("/system/access")
        self.c.get("/system/access")
        self.assertEqual(self.run_mock.call_count, 1)

    def test_workspace_roots_are_not_reported(self) -> None:
        self.cfg = {"workspaceRoots": ["/x"]}
        self.assertNotIn("roots", self.c.get("/system/access").json())


class PermissionRouteTests(_Base):
    def test_request_passes_the_id_and_browser_through(self) -> None:
        with mock.patch.object(macos, "request_permission", return_value={"id": "automation", "state": "granted"}) as req:
            d = self.c.post("/system/permissions/request", json={"id": "automation", "browser": "Safari"}).json()
        req.assert_called_once_with("automation", "Safari")
        self.assertEqual(d, {"result": {"id": "automation", "state": "granted"}})

    def test_request_for_an_unknown_id_is_a_note_not_an_error(self) -> None:
        d = self.c.post("/system/permissions/request", json={"id": "input_monitoring"}).json()["result"]
        self.assertEqual((d["id"], d["state"], d["prompted"]), ("input_monitoring", "unknown", False))

    def test_open_reports_whether_a_pane_was_opened(self) -> None:
        with mock.patch.object(macos.subprocess, "run") as run:
            self.assertEqual(self.c.post("/system/permissions/open", json={"id": "microphone"}).json(), {"ok": True})
            self.assertIn("Privacy_Microphone", run.call_args[0][0][1])
            self.assertEqual(self.c.post("/system/permissions/open", json={"id": "input_monitoring"}).json(), {"ok": False})

    def test_permissions_list_never_prompts(self) -> None:
        with mock.patch.object(macos, "permission_state", return_value="unasked"):
            rows = macos.permissions()
        self.assertEqual({r["id"] for r in rows},
                         {"accessibility", "screen_recording", "microphone", "speech_recognition", "automation", "full_disk"})
        self.assertTrue(all(r["state"] == "unasked" for r in rows))


class ShellCheckTests(_Base):
    def test_ok(self) -> None:
        async def fake(jobs, argv, cwd, cfg, **kw):
            self.assertTrue(kw["sandboxed"])
            self.assertEqual(kw["timeout"], 10)
            return True, "ok\n"
        with mock.patch.object(shell, "run_fixed", fake):
            d = self.c.post("/system/shell-check").json()
        self.assertEqual(d["ok"], True)
        self.assertEqual(d["cwd"], os.path.realpath(os.path.expanduser("~")))  # the check runs in the home folder
        self.assertIsNone(d["error"])

    def test_nonzero_exit(self) -> None:
        async def fake(*a, **kw):
            return False, "boom"
        with mock.patch.object(shell, "run_fixed", fake):
            d = self.c.post("/system/shell-check").json()
        self.assertFalse(d["ok"])
        self.assertEqual(d["output"], "boom")

    def test_shell_error(self) -> None:
        async def fake(*a, **kw):
            raise shell.ShellError("sandbox unavailable")
        with mock.patch.object(shell, "run_fixed", fake):
            d = self.c.post("/system/shell-check").json()
        self.assertEqual((d["ok"], d["error"]), (False, "sandbox unavailable"))


if __name__ == "__main__":
    unittest.main()
