"""Default harness resolution for coding_session_start (codingagents.CodingSessions.resolve_agent) and the codingAgent setting."""
from __future__ import annotations

import unittest
import unittest.mock
from typing import Any

from personal_os import codingagents as ca
from personal_os import permissions
from personal_os.tests.test_codingagents import CodingTestCase


class DefaultHarness(CodingTestCase):
    def have(self, *names: str) -> None:
        bins = {"claude": (ca, "claude_binary"), "opencode": (ca.opencode, "binary"), "pi": (ca.pi, "binary")}
        for n, (mod, attr) in bins.items():
            p = unittest.mock.patch.object(mod, attr, return_value=f"/bin/{n}" if n in names else None)
            p.start()
            self.addCleanup(p.stop)

    def setting(self, value: str | None) -> None:
        self.cs.settings = lambda: {} if value is None else {"codingAgent": value}

    def test_setting_wins_when_installed(self) -> None:
        self.have("claude", "opencode", "pi")
        self.setting("pi")
        self.assertEqual(self.cs.resolve_agent(None), "pi")

    def test_any_picks_first_installed_in_preference_order(self) -> None:
        for installed, want in ((("claude", "opencode", "pi"), "claude"), (("opencode", "pi"), "opencode"), (("pi",), "pi")):
            with self.subTest(installed=installed):
                self.have(*installed)
                self.setting("any")
                self.assertEqual(self.cs.resolve_agent(None), want)

    def test_unset_setting_behaves_like_any(self) -> None:
        self.have("opencode")
        self.setting(None)
        self.assertEqual(self.cs.resolve_agent(""), "opencode")

    def test_nothing_installed(self) -> None:
        self.have()
        self.setting("any")
        with self.assertRaisesRegex(ca.CodingError, "No coding agent is installed"):
            self.cs.resolve_agent(None)

    def test_default_not_installed_lists_installed(self) -> None:
        self.have("claude")
        self.setting("pi")
        with self.assertRaisesRegex(ca.CodingError, "default coding agent 'pi'.*Installed here: claude"):
            self.cs.resolve_agent(None)

    def test_explicit_agent_overrides_setting(self) -> None:
        self.have("claude", "opencode")
        self.setting("claude")
        self.assertEqual(self.cs.resolve_agent("opencode"), "opencode")

    def test_explicit_uninstalled_agent_errors(self) -> None:
        self.have("claude", "opencode")
        with self.assertRaisesRegex(ca.CodingError, "'pi' is not installed.*Installed here: claude, opencode"):
            self.cs.resolve_agent("pi")
        with self.assertRaisesRegex(ca.CodingError, "agent must be one of"):
            self.cs.resolve_agent("nope")

    async def test_start_without_agent_uses_default(self) -> None:
        self.have("claude", "opencode")
        self.setting("any")
        self.job_files("deadbeef", "working", "reading files")
        row: dict[str, Any] = await self.cs.start(None, str(self.repo), "fix the bug")
        self.assertEqual(row["agent"], "claude")


class Setting(unittest.TestCase):
    def test_validation(self) -> None:
        for ok in ("any", "claude", "opencode", "pi"):
            self.assertEqual(permissions.validate("codingAgent", ok), ok)
        with self.assertRaises(ValueError):
            permissions.validate("codingAgent", "nonsense")
        self.assertEqual(permissions.DEFAULTS["codingAgent"], "any")


if __name__ == "__main__":
    unittest.main()
