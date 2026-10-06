"""Usage rows carry a tag and round; spend alerts fire once per day and never block."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="utagtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.usage import Usage  # noqa: E402


def test_usage_tags() -> None:
    d = Path(tempfile.mkdtemp(prefix="utag-"))
    db = Database(d)
    u = Usage(db)
    kw = dict(kind="chat", prompt_tokens=10, completion_tokens=5, duration_ms=1, estimated=False, conversation_id=None, project_id=None)
    u.record(model="m", cost=0.4, **kw)
    u.record(model="m", cost=0.2, tag="job:j1", round=3, **kw)
    tags = {t["tag"]: t for t in u.report(1)["by_tag"]}
    assert set(tags) == {"untagged", "job:j1"} and tags["job:j1"]["calls"] == 1
    with db.tx() as c:
        row = c.execute("SELECT tag, round FROM usage_log WHERE tag='job:j1'").fetchone()
    assert (row["tag"], row["round"]) == ("job:j1", 3)

    assert not hasattr(u, "alert_state") and not hasattr(appmod, "_check_usage_alert"), "spend alerts are gone"


def test_old_db_migrates() -> None:
    d2 = Path(tempfile.mkdtemp(prefix="utag-old-"))
    Database(d2)
    con = sqlite3.connect(next(d2.glob("*.db")))
    con.executescript("ALTER TABLE usage_log DROP COLUMN tag; ALTER TABLE usage_log DROP COLUMN round;")
    con.execute("INSERT INTO usage_log(id,created_at,model,kind,prompt_tokens,completion_tokens,duration_ms,estimated) VALUES('a',1,'m','chat',1,1,1,0)")
    con.commit()
    con.close()
    with Database(d2).tx() as c:
        r = c.execute("SELECT tag, round FROM usage_log WHERE id='a'").fetchone()
    assert (r["tag"], r["round"]) == ("", 0)
