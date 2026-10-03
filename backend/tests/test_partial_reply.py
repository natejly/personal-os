"""A reply that hits its time limit: kept text stays, tool work gets one closing round, the reason is persisted.

Run: PYTHONPATH=backend python backend/tests/test_partial_reply.py
Reuses the scripted-stream harness of test_finish_reason.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_finish_reason as h  # noqa: E402
from test_finish_reason import call, check, reply  # noqa: E402

from personal_os.app import TIME_STOP  # noqa: E402


def test_timeout_after_tool_work_runs_one_closing_round() -> None:
    done, row, _ = reply([
        {"text": "", "calls": [call("c1")]},
        {"text": "", "finish": "timeout"},
        {"text": "Here is what I found."},
    ])
    check(len(h.SEEN) == 3, f"tool round, timed-out round, one closing round; got {len(h.SEEN)}")
    check(any(m.get("content") == TIME_STOP for m in h.SEEN[2]), "the closing round was told it is out of time")
    check(done["partial"] == "time" and done["outcome"] == "time" and done["error"] is None, "done says time, not an error")
    check(row["outcome"] == "time" and row["content"] == "Here is what I found.", "persisted with the closing text")


def test_timeout_after_text_keeps_the_text() -> None:
    done, row, _ = reply([{"text": "Half of the answ", "finish": "timeout"}])
    check(len(h.SEEN) == 1, "no closing round: it would restart the answer after half a sentence")
    check(done["outcome"] == "time" and row["outcome"] == "time" and row["content"] == "Half of the answ", "the text stays, outcome time")


def test_timeout_with_nothing_still_raises() -> None:
    done, row, _ = reply([{"text": "", "finish": "timeout"}])
    check(done["error"] and "time limit" in done["error"] and done["outcome"] is None, "nothing at all: today's error")


if __name__ == "__main__":
    failed = 0
    prev = h.llm.stream_chat
    with h.client:
        h.client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        try:
            for t in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
                try:
                    t()
                    print(f"ok   {t.__name__}")
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    import traceback
                    traceback.print_exc()
                    print(f"FAIL {t.__name__}: {e}")
        finally:
            h.llm.stream_chat = prev
    print(f"\n{h.passed} assertions passed, {failed} test(s) failed")
    sys.exit(1 if failed else 0)
