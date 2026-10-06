"""Two creates of the same memory at the same moment leave one row, whichever thread wins.

Run: backend/.venv/bin/python -m pytest backend/tests/test_memory_create_race.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="memrace-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.repos import Memories  # noqa: E402


def test_concurrent_identical_creates_dedupe_to_one_row() -> None:
    mem = Memories(Database(tempfile.mkdtemp(prefix="memrace-db-")))
    go = threading.Barrier(8)

    def add() -> None:
        go.wait()
        mem.create(None, "rapid fire memory")

    threads = [threading.Thread(target=add) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = [m for m in mem.list(None) if m["content"] == "rapid fire memory"]
    assert len(rows) == 1, [r["id"] for r in rows]
