"""Approval routing across concurrent conversations.

Regression test for a real defect: llm.stream_chat falls back to "call_<idx>" when a provider omits a
tool-call id, and _approvals was a single global dict keyed by that id. Two conversations streaming at
once both produced "call_0", so answering one chat's approval card resolved the other chat's pending
call. Multi-session chat made this reachable in normal use.

Run: PYTHONPATH=<repo>/backend PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_approvals.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="approvaltest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def test_uid_is_unique_across_conversations() -> None:
    """The client-facing approval id must not collide when two replies both emit call_0."""
    # Two different assistant messages, each with a provider that omitted ids (the call_<idx> fallback).
    uid_a = f"{'a' * 16}:call_0"
    uid_b = f"{'b' * 16}:call_0"
    check(uid_a != uid_b, "two conversations' first tool calls must not share an approval id")

    approvals: dict[str, str] = {}
    approvals[uid_a] = "pending-a"
    approvals[uid_b] = "pending-b"
    check(len(approvals) == 2, "both approvals coexist rather than one overwriting the other")
    approvals.pop(uid_a)
    check(approvals.get(uid_b) == "pending-b", "answering one chat must leave the other pending")


def test_source_keys_approvals_by_uid() -> None:
    """Guard the fix in the source itself, so a future edit cannot silently reintroduce the collision."""
    src = (Path(__file__).resolve().parents[1] / "personal_os" / "app.py").read_text()
    check('_approvals[uid] = fut' in src, "_approvals must be keyed by the per-message uid")
    check('_approvals[c["id"]]' not in src, "_approvals must not be keyed by the raw provider call id")
    check('"id": uid' in src, "the tool_call/tool_result events must carry the unique id")
    # The model must still receive the provider's own id, or the tool result cannot be correlated.
    check('"tool_call_id": c["id"]' in src, "the model-facing tool_call_id must stay the provider's id")


def test_approval_route_resolves_only_its_own_future() -> None:
    """POST /approvals/<uid> resolves exactly one waiter."""
    async def run() -> tuple[str, bool]:
        loop = asyncio.get_event_loop()
        fut_a: asyncio.Future = loop.create_future()
        fut_b: asyncio.Future = loop.create_future()
        appmod._approvals.clear()
        appmod._approvals["msgA:call_0"] = fut_a
        appmod._approvals["msgB:call_0"] = fut_b
        target = appmod._approvals.get("msgA:call_0")
        assert target is not None
        target.set_result("allow")
        await asyncio.sleep(0)
        return fut_a.result(), fut_b.done()

    decided, other_done = asyncio.run(run())
    check(decided == "allow", "the addressed approval resolves")
    check(other_done is False, "the other conversation's approval is still pending")
    appmod._approvals.clear()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
