"""A reply that stopped short keeps its reason: the message outcome survives a later write, and a legacy db gains the columns.

Run: PYTHONPATH=backend python backend/tests/test_partial_reply.py
Reuses the scripted-stream harness of test_finish_reason.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_finish_reason as h  # noqa: E402
from test_finish_reason import check  # noqa: E402


def test_a_recorded_reason_survives_a_later_write_and_a_legacy_db_gains_the_columns() -> None:
    import sqlite3
    import tempfile
    from pathlib import Path

    from personal_os.db import Database
    from personal_os.repos import Conversations

    with tempfile.TemporaryDirectory() as d:
        convos = Conversations(Database(d))
        cid = convos.create(None, "t", "m")["id"]
        am = convos.add_message(cid, "assistant", "", model="m")
        check(am["outcome"] is None and am["error_kind"] is None, "a fresh row has no outcome and no error kind")
        convos.finish_message(am["id"], "partway", None, None, outcome="length", error_kind=None)
        convos.finish_message(am["id"], "partway", None, None)  # recovery or a later rewrite: no reason given
        row = convos.get(cid)["messages"][-1]
        check(row["outcome"] == "length", "a reason, once written, is not erased by a write that gives none")
        # A database from before the columns existed: the additive migration adds them and old rows read back null.
        p = Path(d) / "personal-os.db"
        with sqlite3.connect(p) as c:
            c.execute("ALTER TABLE messages DROP COLUMN outcome")
            c.execute("ALTER TABLE messages DROP COLUMN error_kind")
        old = Conversations(Database(d)).get(cid)["messages"][-1]
        check(old["outcome"] is None and old["error_kind"] is None and old["content"] == "partway", "migrated: old rows read back with null reasons")


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
