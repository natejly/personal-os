"""Coding sessions follow Grain's permission mode, and a follow-up to a session's own taint is not untrusted-driven.

Run: PYTHONPATH=backend backend/.venv/bin/python -m unittest personal_os.tests.test_codingagents_modes -v
"""
from __future__ import annotations

import unittest

from personal_os import autoreview, codingagents as ca
from personal_os.tests.test_codingagents import CodingTestCase
from personal_os.tools import Toolbox


class Mapping(unittest.TestCase):
    def test_cli_permission_mode_table(self) -> None:
        self.assertEqual([ca.cli_permission_mode(m, None) for m in ("auto", "manual", "allow_all")], ["auto", None, "bypassPermissions"])
        for m in ("auto", "manual", "allow_all"):
            self.assertEqual(ca.cli_permission_mode(m, "acceptEdits"), "acceptEdits")
            self.assertEqual(ca.cli_permission_mode(m, "dontAsk"), "dontAsk")
        self.assertEqual(ca.PERMISSION_MODES, (None, "acceptEdits", "auto", "dontAsk", "bypassPermissions"))

    def test_own_session_taint(self) -> None:
        own = ca.own_session_taint
        for src in ("start", "send", "stop", "status", "diff"):
            self.assertTrue(own("a1", [f"coding_session:{src}:a1"]), src)
            self.assertFalse(own("a1", [f"coding_session:{src}:b2"]), src)
        self.assertTrue(own("a1", ["coding_session:list:a1"]))
        self.assertTrue(own("a1", ["coding_session:list:"]))
        self.assertFalse(own("a1", ["coding_session:list:a1,b2"]))
        self.assertFalse(own("a1", ["coding_session:status:a1", "fetch_url"]))
        self.assertFalse(own("a1", ["coding_session:status:a1", "opencode_run:network"]))
        self.assertFalse(own("a1", []))
        self.assertFalse(own("a1", None))


class Start(CodingTestCase):
    def argv(self) -> list[str]:
        return next(a for a in self.fake.calls if "--bg" in a)

    async def test_flag_follows_grain_mode(self) -> None:
        for mode, want in (("auto", "auto"), ("manual", None), ("allow_all", "bypassPermissions")):
            self.fake.calls.clear()
            self.cs.settings = lambda m=mode: {"permissionMode": m}
            row = await self.started()
            argv = self.argv()
            self.assertEqual(row["permission_mode"], want, mode)
            self.assertEqual(argv[argv.index("--permission-mode") + 1] if want else None, want, mode)
            self.assertEqual("--permission-mode" in argv, want is not None, mode)

    async def test_ctx_override_and_explicit_win(self) -> None:
        self.cs.settings = lambda: {"permissionMode": "manual"}
        row = await self.started(ctx={"permission_mode": "allow_all"})
        self.assertEqual(row["permission_mode"], "bypassPermissions")
        row = await self.started(permission_mode="dontAsk", ctx={"permission_mode": "allow_all"})
        self.assertEqual(row["permission_mode"], "dontAsk")

    async def test_opencode_never_maps(self) -> None:
        self.cs.settings = lambda: {"permissionMode": "allow_all"}
        with self.assertRaises(ca.CodingError):
            await self.cs.start("opencode", str(self.repo), "x", permission_mode="auto")


class Gate(CodingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.box = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
        ca.register(self.box, self.cs)

    def test_start_hard_card_only_for_explicit_bypass_outside_allow_all(self) -> None:
        for mode, pm, want in (("auto", "bypassPermissions", True), ("manual", "bypassPermissions", True),
                               ("allow_all", "bypassPermissions", False), ("auto", "acceptEdits", False),
                               ("auto", "auto", False), ("manual", "dontAsk", False), ("auto", None, False)):
            ctx = {"settings": {"permissionMode": mode}}
            args = {"permission_mode": pm} if pm else {}
            self.assertEqual(self.box.forces_card("coding_session_start", args, ctx), want, (mode, pm))
            self.assertEqual(self.box.forces_ask("coding_session_start", args, ctx), want, (mode, pm))
        ctx = {"settings": {"permissionMode": "auto"}, "permission_mode": "allow_all"}  # a subagent override
        self.assertFalse(self.box.forces_card("coding_session_start", {"permission_mode": "bypassPermissions"}, ctx))

    def test_start_has_no_own_session_exemption(self) -> None:
        ctx = {"tainted": True, "taint_sources": ["coding_session:status:a1"]}
        self.assertTrue(self.box.forces_ask("coding_session_start", {}, ctx))
        self.assertFalse(self.box.forces_ask("coding_session_start", {}, {}))

    def test_send_exempts_only_its_own_session(self) -> None:
        a = {"id": "a1", "message": "go"}
        own = {"tainted": True, "taint_sources": ["coding_session:status:a1", "coding_session:diff:a1"]}
        self.assertFalse(self.box.tainted_for("coding_session_send", a, own))
        self.assertFalse(self.box.forces_ask("coding_session_send", a, own))
        self.assertTrue(self.box.tainted_for("coding_session_send", {"id": "b2"}, own))
        mixed = {"tainted": True, "taint_sources": ["coding_session:status:a1", "fetch_url"]}
        self.assertTrue(self.box.tainted_for("coding_session_send", a, mixed))
        self.assertTrue(self.box.forces_ask("coding_session_send", a, mixed))
        self.assertFalse(self.box.tainted_for("coding_session_send", a, {}))
        self.assertTrue(self.box.tainted_for("fetch_url", {}, own))  # other tools still see the conversation's taint
        self.assertTrue(self.box.tainted_for("coding_session_send", a, {**own, "taint_unsourced": True}))  # a legacy unexplained taint
        self.assertTrue(self.box.forces_ask("coding_session_send", a, {**own, "taint_unsourced": True}))

    def test_route_sends_untainted_start_and_send_to_the_reviewer(self) -> None:
        r = autoreview.route
        self.assertEqual(r("auto", mode="ask", danger="external"), "review")
        self.assertEqual(r("auto", mode="ask", danger="external", hard_forced=True), "card")
        self.assertEqual(r("allow_all", mode="ask", danger="external", hard_forced=True), "run")
        self.assertEqual(r("manual", mode="ask", danger="external"), "card")


if __name__ == "__main__":
    unittest.main()
