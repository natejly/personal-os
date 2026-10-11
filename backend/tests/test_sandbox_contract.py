"""The sandbox's declared limits and its error passthrough.

Item 1: what a sandbox blocks is stated up front, in the tool descriptions the assistant reads and at GET /sandbox/limits.
Item 3: a blocked call comes back as the operating system's own error, quoted, never an empty result or generic text.

No Database (its file is named personal-os.db, which a host sandbox denies) and no real Seatbelt: the profile's
refusals are exercised through sandbox.profile_failed / sandbox.blocked_note, and run_python runs with the profile
disabled so its stdout/stderr passthrough is what is under test.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import sandbox  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402


def _tb() -> Toolbox:
    # Descriptions are built at registration; nothing here opens the database or the container runtime.
    return Toolbox(None, None, None, lambda: {})


def test_declared_limits_name_every_wall() -> None:
    limits = sandbox.declared_limits()
    assert set(limits) == {"run_python"}
    py = " ".join(limits["run_python"]["blocks"]).lower()
    for wall in ("network", "bind", "subprocess", "launch", "writes outside", "credential"):
        assert wall in py, wall
    assert "unsandboxed" in limits["run_python"]["escalation"]
    text = sandbox.limits_text(limits["run_python"])
    assert text.startswith(" Declared limits:") and "network" in text


def test_run_python_description_carries_the_declared_limits() -> None:
    tb = _tb()
    from personal_os.tools import python_unsandboxed
    carried = sandbox.limits_text(sandbox.declared_limits()["run_python"]) in tb.specs["run_python"].description
    assert carried == (not python_unsandboxed())  # no limits are declared where nothing confines the script


def test_profile_failed_only_for_a_sandbox_that_never_started() -> None:
    assert sandbox.profile_failed(71, "sandbox-exec: sandbox_apply: Operation not permitted\n")
    assert sandbox.profile_failed(65, "  sandbox-exec: invalid profile")
    assert not sandbox.profile_failed(1, "Traceback (most recent call last)")
    assert not sandbox.profile_failed(0, "sandbox-exec: not actually a failure")
    assert not sandbox.profile_failed(None, "sandbox-exec: x")


def test_blocked_note_quotes_the_os_error_and_names_the_way_out() -> None:
    for err in ("PermissionError: [Errno 1] Operation not permitted",
                "Traceback (most recent call last):\nPermissionError: [Errno 1] Operation not permitted: '/x'",
                "zsh:1: operation not permitted: .zshrc",
                "open('/Users/x/.ssh/id_rsa'): error -54",
                "sandbox-exec: sandbox_apply: Operation not permitted"):
        note = sandbox.blocked_note(err)
        assert note and "operating system" in note and "unsandboxed" in note, err
    assert sandbox.blocked_note("hello, all good") is None


def test_run_python_returns_the_os_error_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)  # run the interpreter directly: the script's own error
    out = sandbox.run_python("raise PermissionError(1, 'Operation not permitted')")
    assert out["exit_code"] != 0
    assert "PermissionError" in out["stderr"]
    assert "[Errno 1] Operation not permitted" in out["stderr"]  # verbatim, not paraphrased


def test_run_python_tool_refuses_a_sandbox_that_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = _tb()
    refused = {"stdout": "", "stderr": "sandbox-exec: sandbox_apply: Operation not permitted\n", "exit_code": 71,
               "timed_out": False}
    monkeypatch.setattr("personal_os.tools.run_python", lambda *a, **k: dict(refused))
    out = asyncio.run(tb.specs["run_python"].fn({"conversation_id": None, "settings": {}}, code="print(1)"))
    assert "sandbox refused to start" in out["error"]
    assert "sandbox_apply: Operation not permitted" in out["error"]  # the OS message, quoted
    assert "unsandboxed" in out["try_instead"]


def test_run_python_tool_keeps_the_os_error_and_adds_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = _tb()
    blocked = {"stdout": "", "stderr": "PermissionError: [Errno 1] Operation not permitted\n", "exit_code": 1,
               "timed_out": False}
    monkeypatch.setattr("personal_os.tools.run_python", lambda *a, **k: dict(blocked))
    out = asyncio.run(tb.specs["run_python"].fn({"conversation_id": None, "settings": {}}, code="print(1)"))
    assert out["stderr"] == blocked["stderr"]  # the OS error is untouched
    assert "unsandboxed" in out["note"]        # the wall and the way out are named alongside it
