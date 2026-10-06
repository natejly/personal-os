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

from personal_os import activity, codingagents, imessage, opencode, shell, system_access  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        system_access._version.cache_clear()
        self.cfg: dict = {}
        app = FastAPI()
        app.include_router(system_access.router(lambda: self.cfg, lambda: object()))
        self.c = TestClient(app)
        for target, val in [(activity, {"IS_MAC": True, "automation_status": lambda b: "granted",
                                        "input_monitoring_status": lambda: "unasked", "full_disk_access": lambda: False,
                                        "installed_browsers": lambda: ["Safari"]}),
                            (codingagents, {"claude_binary": lambda: "/bin/claude"}),
                            (opencode, {"binary": lambda: None}),
                            (imessage, {"_open_ro": lambda p: mock.Mock()})]:
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
        self.assertEqual(set(d), {"fullDisk", "inputMonitoring", "automation", "browsers", "scope", "clis"})
        self.assertEqual(set(d["automation"]), {"messages", "finder", "systemEvents", "contacts", "calendar", "reminders"})
        self.assertEqual(d["fullDisk"], "granted")  # chat.db probe succeeded
        self.assertEqual(d["inputMonitoring"], "unasked")
        self.assertEqual(d["browsers"], [{"name": "Safari", "state": "granted"}] if "Safari" in activity.BROWSER_BUNDLES
                         else d["browsers"])
        # the file scope: what is off limits and what asks first, as display strings
        self.assertEqual(set(d["scope"]), {"protected", "sensitive"})
        self.assertTrue(d["scope"]["protected"][0].startswith("Grain's data folder ("))
        self.assertIn("/Applications/Grain.app", d["scope"]["protected"])
        self.assertIn("~/.ssh", d["scope"]["sensitive"])
        self.assertIn("Browser cookies and saved passwords", d["scope"]["sensitive"])
        self.assertIn(".env files", d["scope"]["sensitive"])

    def test_probe_raises_is_unknown(self) -> None:
        with mock.patch.object(activity, "input_monitoring_status", side_effect=RuntimeError("x")):
            d = self.c.get("/system/access").json()
        self.assertEqual(d["inputMonitoring"], "unknown")
        self.assertEqual(d["automation"]["finder"], "granted")

    def test_fda_denied(self) -> None:
        with mock.patch.object(imessage, "_open_ro", side_effect=imessage.NeedsFullDiskAccess):
            self.assertEqual(self.c.get("/system/access").json()["fullDisk"], "denied")

    def test_fda_granted_without_probe(self) -> None:
        with mock.patch.object(activity, "full_disk_access", lambda: True), \
                mock.patch.object(imessage, "_open_ro", side_effect=imessage.NeedsFullDiskAccess):
            self.assertEqual(self.c.get("/system/access").json()["fullDisk"], "granted")

    def test_fda_unknown_off_mac(self) -> None:
        with mock.patch.object(activity, "IS_MAC", False):
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
