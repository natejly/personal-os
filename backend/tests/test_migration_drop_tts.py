"""Migration 29 deletes the Read aloud and voice chat settings and leaves the rest."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="droptts-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm, migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402

KEYS = ("ttsVoice", "ttsRate", "voiceLoopMaxTurns")


def test_migration_drops_the_tts_settings() -> None:
    d = Path(tempfile.mkdtemp(prefix="droptts-mig-"))
    db = Database(d)
    db.set_settings({"ttsVoice": "com.apple.voice.x", "ttsRate": 1.3, "voiceLoopMaxTurns": 5, "uiZoom": 110})
    con = sqlite3.connect(next(d.glob("*.db")))
    con.execute("PRAGMA user_version = 28")
    con.commit()
    assert migrations.run(con)[0] == 29  # later steps may follow
    left = {r[0] for r in con.execute("SELECT key FROM settings")}
    con.close()
    assert not left & set(KEYS) and "uiZoom" in left
    assert [n for v, n, _ in migrations.MIGRATIONS if v == 29] == ["drop_tts_settings"]


def test_defaults_no_longer_carry_them() -> None:
    assert not set(KEYS) & set(llm.DEFAULT_SETTINGS)
