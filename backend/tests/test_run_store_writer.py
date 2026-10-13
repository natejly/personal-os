"""RunStore.append only queues: a held write lock elsewhere no longer stalls the caller, and reads still see every event."""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402


def test_append_does_not_wait_for_a_held_write_lock(tmp_path: Path) -> None:
    store = RunStore(Database(tmp_path))
    store.create("r1", None)
    other = sqlite3.connect(tmp_path / "personal-os.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    t = time.monotonic()
    for i in range(1, 51):
        store.append("r1", i, "delta", {"text": str(i)})
    assert time.monotonic() - t < 0.2, "50 appends behind a held lock returned at once"
    other.execute("COMMIT")
    other.close()
    assert [s for s, _, _ in store.events("r1")] == list(range(1, 51)), "a read drains the queue first"


def test_a_bad_row_does_not_drop_its_batch(tmp_path: Path) -> None:
    store = RunStore(Database(tmp_path))
    store.create("r1", None)
    store.append("r1", 1, "delta", {"text": "a"})
    store.append("gone", 1, "delta", {"text": "b"})  # no such run: the foreign key refuses it
    store.append("r1", 2, "delta", {"text": "c"})
    assert [d["text"] for _, _, d in store.events("r1")] == ["a", "c"]
