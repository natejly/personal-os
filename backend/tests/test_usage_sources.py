"""Usage sources: proxy-alias pricing, migration 33 cost backfill, OpenCode import, by_source report."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="usagesrc-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.opencode_usage import import_usage  # noqa: E402
from personal_os.usage import Pricing, Usage  # noqa: E402

M = 1_000_000


def close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) < 1e-9


def test_aliases_price_under_every_prefix() -> None:
    p = Pricing()
    # (names, input, output) per million
    cases = [(("glm-5.3", "glm-5p3"), 1.40, 4.40), (("kimi-k3",), 3.00, 15.00), (("kimi-k3-fast",), 4.50, 22.50),
             (("ember-1",), 3.00, 15.00), (("deepseek-v4-flash", "deepseek-v4p1-flash"), 0.30, 1.20)]
    for names, i, o in cases:
        for n in names:
            for model in (n, f"accounts/fireworks/models/{n}", f"fireworks-ai/accounts/fireworks/models/{n}"):
                assert close(p.cost({}, model, M, M), i + o), model
    assert close(p.cost({}, "accounts/fireworks/routers/kimi-k3-fast", M, M), 4.50 + 22.50)
    assert p.cost({}, "no-such-model", M, M) is None


def _migrate_from_32(d: Path) -> sqlite3.Connection:
    con = sqlite3.connect(next(d.glob("*.db")))
    con.execute("PRAGMA user_version = 32")
    con.commit()
    return con


def _row(con, rid, model, pt, ct, cost, cached=0):
    con.execute("INSERT INTO usage_log(id,created_at,model,kind,prompt_tokens,completion_tokens,cost,cached_tokens) "
                "VALUES(?,?,?,?,?,?,?,?)", (rid, time.time(), model, "chat", pt, ct, cost, cached))


def test_backfill_migration() -> None:
    d = Path(tempfile.mkdtemp(prefix="usagesrc-mig-"))
    db = Database(d)
    db.set_settings({"modelPrices": {"my-model": {"input": 2.0, "output": 10.0}}})
    con = sqlite3.connect(next(d.glob("*.db")))
    _row(con, "ember", "ember-1", M, M, None, cached=600_000)
    _row(con, "glm", "glm-5.3", M, 0, 0)
    _row(con, "kept", "ember-1", M, M, 0.5)
    _row(con, "notok", "ember-1", 0, 0, None)
    _row(con, "unknown", "mystery", M, M, None)
    _row(con, "custom", "my-model", M, M, None)
    con.commit()
    con.execute("PRAGMA user_version = 32")
    con.commit()
    assert migrations.run(con)[0] >= 33
    assert [n for v, n, _ in migrations.MIGRATIONS if v == 33] == ["usage_sources_and_backfill"]

    def snap():
        return {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, cost, cost_source FROM usage_log")}

    s = snap()
    assert close(s["ember"][0], (400_000 * 3.00 + 600_000 * 0.30 + M * 15.00) / M) and s["ember"][1] == "backfill"
    assert close(s["glm"][0], 1.40) and s["glm"][1] == "backfill"
    assert s["kept"] == (0.5, None)
    assert s["notok"] == (None, None)
    assert s["unknown"] == (None, None)
    assert close(s["custom"][0], 12.0) and s["custom"][1] == "backfill"
    assert {r[0] for r in con.execute("SELECT source FROM usage_log")} == {"grain"}

    migrations._usage_sources_and_backfill(con)
    assert snap() == s
    con.close()


def _fake_opencode(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE session_message(id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER, "
                "time_created INTEGER, time_updated INTEGER, data TEXT)")
    for r in rows:
        _add(con, *r)
    con.commit()
    con.close()


def _add(con, mid, typ, created, updated, data) -> None:
    con.execute("INSERT OR REPLACE INTO session_message VALUES(?,?,?,?,?,?,?)", (mid, "s1", typ, 0, created, updated, json.dumps(data)))


def _asst(model="glm-5.3", cost=None, completed=True, inp=100, out=50, reasoning=10, read=20, write=5, created=1_000_000):
    d = {"model": {"providerID": "fireworks-ai", "id": model},
         "tokens": {"input": inp, "output": out, "reasoning": reasoning, "cache": {"read": read, "write": write}},
         "time": {"created": created, **({"completed": created + 1000} if completed else {})}}
    if cost is not None:
        d["cost"] = cost
    return d


def test_opencode_import() -> None:
    db = Database(Path(tempfile.mkdtemp(prefix="usagesrc-oc-")))
    pricing, path = Pricing(), Path(tempfile.mkdtemp(prefix="usagesrc-ocdb-")) / "opencode.db"
    assert import_usage(db, pricing, {}, path) == 0  # missing file
    _fake_opencode(path, [("m1", "assistant", 1_000_000, 1_000_000, _asst(cost=0.25)),
                          ("m2", "assistant", 2_000_000, 2_000_000, _asst(created=2_000_000)),
                          ("m3", "assistant", 3_000_000, 3_000_000, _asst(completed=False, created=3_000_000)),
                          ("u1", "user", 4_000_000, 4_000_000, {"text": "hi"})])

    def rows():
        with db.tx() as c:
            return {r["external_id"]: dict(r) for r in c.execute("SELECT * FROM usage_log WHERE source='opencode'")}

    assert import_usage(db, pricing, {}, path) == 2
    r = rows()
    assert set(r) == {"m1", "m2"}
    m1 = r["m1"]
    assert (m1["prompt_tokens"], m1["completion_tokens"], m1["cached_tokens"], m1["cache_write_tokens"], m1["reasoning_tokens"]) == (125, 60, 20, 5, 10)
    assert m1["cost"] == 0.25 and m1["cost_source"] == "opencode" and m1["model"] == "glm-5.3" and m1["created_at"] == 1000.0
    assert m1["duration_ms"] == 1000 and r["m2"]["cost_source"] is None
    assert close(r["m2"]["cost"], pricing.cost({}, "glm-5.3", 125, 60, 20, 5))
    total = sum(x["cost"] for x in r.values())
    assert import_usage(db, pricing, {}, path) == 0
    assert sum(x["cost"] for x in rows().values()) == total

    con = sqlite3.connect(path)
    _add(con, "m4", "assistant", 5_000_000, 5_000_000, _asst(created=5_000_000))
    _add(con, "m3", "assistant", 3_000_000, 6_000_000, _asst(created=3_000_000))  # finished late
    con.commit()
    con.close()
    assert import_usage(db, pricing, {}, path) == 2
    assert set(rows()) == {"m1", "m2", "m3", "m4"}
    assert import_usage(db, pricing, {}, path) == 0


def test_report_by_source_and_reprice() -> None:
    db = Database(Path(tempfile.mkdtemp(prefix="usagesrc-rep-")))
    u, pricing = Usage(db), Pricing()
    u.record(model="ember-1", kind="chat", prompt_tokens=M, completion_tokens=0, duration_ms=1, cost=3.0, estimated=False,
             conversation_id=None, project_id=None)
    with db.tx() as c:
        c.execute("INSERT INTO usage_log(id,created_at,model,kind,prompt_tokens,completion_tokens,cost,tag,source,cost_source,external_id) "
                  "VALUES('o1',?,?,?,?,?,?,?,?,?,?)", (time.time(), "glm-5.3", "opencode", M, 0, 7.77, "opencode", "opencode", "opencode", "x1"))
    rep = u.report(7)
    by = {b["source"]: b for b in rep["by_source"]}
    assert (by["grain"]["label"], by["opencode"]["label"]) == ("Grain", "OpenCode")
    assert (by["grain"]["calls"], by["grain"]["cost"]) == (1, 3.0)
    assert (by["opencode"]["calls"], by["opencode"]["cost"]) == (1, 7.77)
    assert rep["totals"]["calls"] == 2 and close(rep["totals"]["cost"], 10.77)
    u.reprice(pricing, {})
    with db.tx() as c:
        assert c.execute("SELECT cost FROM usage_log WHERE id='o1'").fetchone()[0] == 7.77
