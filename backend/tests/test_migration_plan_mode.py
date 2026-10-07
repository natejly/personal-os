"""Migration 32: the Plan toggle is gone. A chat that planned becomes a draft Plan-first desk; every planMode is dropped."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Conversations  # noqa: E402

V = next(v for v, name, _ in migrations.MIGRATIONS if name == "plan_mode_into_plan_first")


def _run(default: str | None, chats: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict], dict, Desks, dict[str, str]]:
    d = Path(tempfile.mkdtemp(prefix="mig-plan-"))
    db = Database(d)
    desks = Desks(db)
    convos = Conversations(db)
    ids = {name: convos.create(None, name, "m")["id"] for name in chats}
    con = sqlite3.connect(next(d.glob("*.db")))
    for name, s in chats.items():
        con.execute("UPDATE conversations SET settings=? WHERE id=?", (json.dumps(s), ids[name]))
    perms = {"version": 2, **({"planMode": default} if default else {})}
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('permissions', ?)", (json.dumps(perms),))
    con.execute(f"PRAGMA user_version = {V - 1}")
    con.commit()
    assert V in migrations.run(con)
    raw = {name: json.loads(con.execute("SELECT settings FROM conversations WHERE id=?", (ids[name],)).fetchone()[0]) for name in chats}
    rows = {name: dict(zip(("autonomy", "status", "id"), r)) for name in chats
            if (r := con.execute("SELECT autonomy, status, id FROM desks WHERE conversation_id=?", (ids[name],)).fetchone())}
    stored = json.loads(con.execute("SELECT value FROM settings WHERE key='permissions'").fetchone()[0])
    con.close()
    return raw, rows, stored, desks, ids


def test_planning_chats_become_plan_first_desks() -> None:
    raw, rows, stored, desks, ids = _run(None, {"always": {"planMode": "always", "effort": "high"}, "auto": {"planMode": "auto"},
                                               "off": {"planMode": "off"}, "none": {}, "job": {"planMode": "always", "job_id": "j"}})
    for name in ("always", "auto"):
        assert rows[name]["autonomy"] == "plan" and rows[name]["status"] == "draft"
        assert raw[name]["deskId"] == rows[name]["id"] and "planMode" not in raw[name]
        assert (desks.get(rows[name]["id"]) or {}).get("conversation_id") == ids[name]  # the app reads the row it wrote
    assert raw["always"]["effort"] == "high"
    assert "off" not in rows and "none" not in rows and "job" not in rows
    assert raw["off"] == {} and raw["none"] == {} and raw["job"] == {"job_id": "j"}
    assert "planMode" not in stored


def test_a_global_default_maps_the_chats_that_followed_it() -> None:
    raw, rows, stored, _, _ = _run("auto", {"follows": {}, "own_off": {"planMode": "off"}})
    assert rows["follows"]["autonomy"] == "plan" and raw["follows"]["deskId"] == rows["follows"]["id"]
    assert "own_off" not in rows and raw["own_off"] == {}
    assert stored == {"version": 2}


if __name__ == "__main__":
    test_planning_chats_become_plan_first_desks()
    test_a_global_default_maps_the_chats_that_followed_it()
    print("ok")
