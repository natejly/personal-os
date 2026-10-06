"""Stuck detector: unit patterns (stuck.py) and the loop wiring in app._chat_stream.

Run: python backend/tests/test_stuck.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="stucktest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os.stuck import WINDOW, StuckDetector  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def feed(d: StuckDetector, seq: list[tuple[str, dict, Any]]) -> Any:
    for t, a, r in seq:
        d.observe(t, a, r)
    return d.check()


OK = {"ok": True}


def test_same_result() -> None:
    check(feed(StuckDetector(), [("a", {"q": 1}, {"v": 1, "duration_ms": i}) for i in range(4)]).pattern == "same_result",
          "same call, same result x4 (volatile duration_ms ignored)")
    check(feed(StuckDetector(), [("a", {"q": 1}, {"v": 1})] * 3) is None, "3 is not enough")
    check(feed(StuckDetector(), [("a", {"offset": i}, {"v": i}) for i in range(8)]) is None, "paging never flags")
    check(feed(StuckDetector(), [("a", {"q": 1}, {"v": i}) for i in range(8)]) is None, "same args, changing results never flags")


def test_error_cycle_and_storm() -> None:
    check(feed(StuckDetector(), [("a", {"q": 1}, {"error": f"e{i}"}) for i in range(3)]).pattern == "error_cycle", "same call erroring x3")
    s = feed(StuckDetector(), [("a", {"q": i}, {"error": "bad"}) for i in range(4)])
    check(s is not None and s.pattern == "error_storm", "one tool erroring with different args x4")
    check(feed(StuckDetector(), [("a", {"q": i}, {"error": "bad"}) for i in range(3)]) is None, "3 varied errors is not a storm")
    check(feed(StuckDetector(), [("a", {"q": i}, {"error": "bad"}) for i in range(3)] + [("b", {}, {"error": "x"})]) is None,
          "storm needs one tool")


def test_alternating() -> None:
    A, B = ("a", {"x": 1}, {"r": "A"}), ("b", {"y": 2}, {"r": "B"})
    check(feed(StuckDetector(), [A, B] * 3).pattern == "alternating", "A,B x3 with stable results")
    check(feed(StuckDetector(), [A, B, A, ("b", {"y": 2}, {"r": "B2"}), A, B]) is None, "a changed result in the middle clears it")
    check(feed(StuckDetector(), [A, B] * 2) is None, "two cycles is not enough")


def test_window_bounded() -> None:
    d = StuckDetector()
    for i in range(100):
        d.observe("a", {"i": i}, OK)
    check(len(d.obs) == WINDOW == 24, "deque bounded at 24")


def test_not_run_streak() -> None:
    d = StuckDetector()
    for i in range(3):
        d.skip(f"t{i}")
    check(d.check() is None, "3 calls that never ran is not yet a streak")
    d.skip("t3")
    s = d.check()
    check(s is not None and s.pattern == "not_run", "4 calls that never ran in a row")
    d.observe("a", {}, OK)
    check(d.check() is None, "an executed call ends the streak")
    for i in range(4):
        d.skip("t")
    d.reset()
    check(d.check() is None, "reset clears the streak")


# ---------------- integration ----------------
SEEN: list[list[dict[str, Any]]] = []
ROUNDS: list[dict[str, Any]] = []


async def _scripted_stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                           tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                           effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    step = ROUNDS.pop(0) if ROUNDS else {"text": "all done", "calls": []}
    yield {"type": "delta", "text": step["text"]}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": step["calls"], "usage": None}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted_stream).parameters)
assert not _missing, f"_scripted_stream is missing {sorted(_missing)}"


def call(cid: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "name": name, "arguments": json.dumps(args)}


def run_chat(conv_id: str) -> list[tuple[str, Any]]:
    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(conv_id, appmod.ChatIn(content="go"), asyncio.Event())]

    prev = llm.stream_chat
    llm.stream_chat = _scripted_stream
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat = prev


def pingpong(n: int) -> list[dict[str, Any]]:
    rounds = []
    for i in range(n):
        c = call(f"c{i}", "list_documents", {}) if i % 2 == 0 else call(f"c{i}", "search_memory", {"query": "zzz"})
        rounds.append({"text": "", "calls": [c]})
    return rounds


def breakers_of(events: list[tuple[str, Any]]) -> list[Any]:
    return [d.get("breaker") for e, d in events if e == "tool_result"]


def test_pingpong_nudged_then_stopped() -> None:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "stuckDetection": True, "delegationForce": False})
    cid = appmod.convos.create(None, "t", "m")["id"]
    SEEN.clear()
    ROUNDS[:] = pingpong(24)
    events = run_chat(cid)
    breakers = breakers_of(events)
    check("stuck_nudge" in breakers, "first detection nudges")
    check("stuck" in breakers, "second detection stops")
    check(breakers.index("stuck_nudge") < breakers.index("stuck"), "nudge precedes stop")
    check(len(breakers) < 25, f"ended well before maxToolRounds, ran {len(breakers)} calls")
    notices = [m for r in SEEN for m in r if m["role"] == "tool" and "[stuck_notice]" in (m.get("content") or "")]
    check(len(notices) >= 1 and "stuck" in notices[0]["content"], "the model saw the notice in a tool message")
    check(len(SEEN) == len(breakers) + 1, "one tool-free final round followed the stop")


def _never_runs(rounds: list[dict[str, Any]]) -> None:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "delegationForce": False, "permissionMode": "manual"})
    cid = appmod.convos.create(None, "t", "m")["id"]
    SEEN.clear()
    ROUNDS[:] = rounds
    breakers = breakers_of(run_chat(cid))
    check("stuck_nudge" in breakers and "stuck" in breakers and breakers.index("stuck_nudge") < breakers.index("stuck"),
          "nudged, then stopped")
    check(len(breakers) <= 12, f"ended within the threshold, ran {len(breakers)} calls")
    check(len(SEEN) == len(breakers) + 1, "one tool-free final round followed the stop")


def test_a_disabled_tool_called_with_varied_args_ends_stuck() -> None:
    """doc_read fails three times, is disabled for the reply, and the model keeps calling it with new arguments."""
    _never_runs([{"text": "", "calls": [call(f"d{i}", "doc_read", {"doc_id": f"nope{i}"})]} for i in range(60)])


def test_a_tool_called_with_arguments_it_cannot_take_ends_stuck() -> None:
    """The call is refused before it reaches the tool, and the arguments differ every time."""
    _never_runs([{"text": "", "calls": [call(f"u{i}", "doc_read", {f"bogus{i}": i})]} for i in range(60)])


def test_legacy_off_setting_is_ignored() -> None:
    """Stuck detection is always on: a stored stuckDetection=False (the old switch) no longer disables it."""
    appmod.db.set_settings({"stuckDetection": False})
    try:
        cid = appmod.convos.create(None, "t", "m")["id"]
        SEEN.clear()
        ROUNDS[:] = pingpong(24)
        breakers = breakers_of(run_chat(cid))
        check("stuck_nudge" in breakers and "stuck" in breakers, "still nudged and stopped with the old key off")
    finally:
        appmod.db.set_settings({"stuckDetection": True})


def test_paging_not_flagged() -> None:
    cid = appmod.convos.create(None, "t", "m")["id"]
    ROUNDS[:] = [{"text": "", "calls": [call(f"p{i}", "list_documents", {"offset": i})]} for i in range(3)] + [{"text": "done", "calls": []}]
    check(not [b for b in breakers_of(run_chat(cid)) if b], "short varied sequence is untouched")


if __name__ == "__main__":
    failed = 0
    for t in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    sys.exit(1 if failed else 0)
