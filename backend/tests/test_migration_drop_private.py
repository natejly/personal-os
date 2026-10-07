"""Migration 30: private chats are gone, so the flag is cleared and those chats behave like any other."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "drop_private_chats")


def test_the_flag_is_cleared_and_the_chat_reads_and_learns_again() -> None:
    d = Path(tempfile.mkdtemp(prefix="mig-private-"))
    db = Database(d)
    convos = Conversations(db)
    plain = convos.create(None, "plain", "m")
    was = convos.create(None, "was private", "m")
    con = sqlite3.connect(next(d.glob("*.db")))
    con.execute("UPDATE conversations SET settings=? WHERE id=?", (json.dumps({"private": True, "effort": "high"}), was["id"]))
    con.execute(f"PRAGMA user_version = {V - 1}")
    con.commit()
    assert V in migrations.run(con)
    raw = {r[0]: json.loads(r[1]) for r in con.execute("SELECT id, settings FROM conversations")}
    con.close()
    assert "private" not in raw[was["id"]] and raw[was["id"]]["effort"] == "high"  # only the flag goes
    assert raw[plain["id"]] == {}
    got = convos.get(was["id"], with_messages=False)
    assert got["settings"]["useMemory"] is True and got["settings"]["autoLearn"] is True


def test_private_is_no_longer_special() -> None:
    db = Database(Path(tempfile.mkdtemp(prefix="mig-private-")))
    convos = Conversations(db)
    c = convos.create(None, "c", "m")
    out = convos.update(c["id"], {"settings": {"private": True}})  # an old client sending it gets no special behaviour
    assert out["settings"]["useMemory"] is True and out["settings"]["autoLearn"] is True
