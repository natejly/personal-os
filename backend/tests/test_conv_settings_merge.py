"""Conversations.update merges settings under the write lock, so concurrent writers with disjoint keys both land.

Runs under pytest, or directly: python backend/tests/test_conv_settings_merge.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations  # noqa: E402


def _race(convos: Conversations, cid: str, a: dict, b: dict) -> list[BaseException]:
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(patch: dict) -> None:
        try:
            barrier.wait()
            convos.update(cid, {"settings": patch})
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run, args=(p,)) for p in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return errors


def test_concurrent_disjoint_keys_both_survive() -> None:
    with tempfile.TemporaryDirectory() as d:
        convos = Conversations(Database(d))
        cid = convos.create(None, "hi", "m")["id"]
        for i in range(100):
            errors = _race(convos, cid, {"effort": "high", "fast": bool(i % 2)}, {"tainted": True, "taint_sources": ["email"]})
            assert not errors, errors
            s = convos.get(cid, with_messages=False)["settings"]
            assert s["effort"] == "high" and s["tainted"] is True and s["taint_sources"] == ["email"]
            convos.update(cid, {"settings": {"tainted": False, "taint_sources": []}})


def test_model_and_settings_apply_together() -> None:
    with tempfile.TemporaryDirectory() as d:
        convos = Conversations(Database(d))
        cid = convos.create(None, "hi", "m")["id"]
        row = convos.update(cid, {"model": "m2", "settings": {"effort": "high"}})
        assert row["model"] == "m2" and row["settings"]["effort"] == "high"


def test_merge_stays_shallow() -> None:
    with tempfile.TemporaryDirectory() as d:
        convos = Conversations(Database(d))
        cid = convos.create(None, "hi", "m")["id"]
        convos.update(cid, {"settings": {"tools": {"web": True}}})
        assert convos.get(cid, with_messages=False)["settings"]["tools"] == {"web": True}
        convos.update(cid, {"settings": {"tools": {}}})
        assert convos.get(cid, with_messages=False)["settings"]["tools"] == {}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
