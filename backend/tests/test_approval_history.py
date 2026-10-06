"""approval_log.py: the decision history. Card answers log in RunStore.decide, standing-grant and reviewed calls log
from the chat loop (record), and the desk reviewer's verdict logs from deskgate. A real Database in a temp dir."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import approval_log, deskgate, migrations  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "data")
    with d.tx() as c:
        c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES('c1','t','m',0,0)")
    return d


def rows(db: Database) -> list[dict]:
    return approval_log.history(db, limit=200)["items"]


def test_migration_adds_the_log_and_the_review_column(db: Database) -> None:
    with db.tx() as c:
        assert migrations.current(c) == migrations.latest()
        assert c.execute("SELECT COUNT(*) FROM approval_log").fetchone()[0] == 0
        assert "review" in {r[1] for r in c.execute("PRAGMA table_info(approvals)")}
        migrations._approval_history(c)  # idempotent: a second run changes nothing and does not raise


def test_every_card_answer_is_logged_with_its_scope(db: Database) -> None:
    store = RunStore(db)
    store.create("r1", "c1", kind="job")
    review = {"verdict": "ask", "reason": "sends mail outward", "model": "rev", "ms": 40}
    for i, (decision, by) in enumerate([("allow", "user"), ("always_global", "user"), ("always_rule", "user"),
                                        ("always_chat", "user"), ("deny", "stop")]):
        store.open_approval(f"m:{i}", "r1", "gmail_send", {"to": "a@b.c", "password": "hunter2"}, conversation_id="c1",
                            review=review if i == 1 else None)
        store.decide(f"m:{i}", decision, by=by, rules=["gmail_send(to:a@b.c)"] if decision == "always_rule" else None)
    got = {r["call_id"]: r for r in rows(db)}
    assert [got[f"m:{i}"]["decision"] for i in range(5)] == ["allow_once", "always", "always", "always", "deny"]
    assert [got[f"m:{i}"]["scope"] for i in range(5)] == ["once", "global", "rule", "conversation", None]
    assert got["m:1"]["reviewer_verdict"] == "ask" and got["m:1"]["reviewer_reason"] == "sends mail outward"
    assert got["m:1"]["reviewer_model"] == "rev" and got["m:1"]["reviewer_ms"] == 40
    assert got["m:2"]["rule"] == ["gmail_send(to:a@b.c)"]
    assert got["m:4"]["note"] == "(stop)"
    assert all(r["agent"] == "job" and r["conversation_id"] == "c1" for r in got.values())
    assert "hunter2" not in got["m:0"]["args_summary"] and "a@b.c" in got["m:0"]["args_summary"]
    # First decision wins, and a second answer logs nothing.
    assert store.decide("m:0", "deny") is None
    assert len(rows(db)) == 5


def test_an_edited_approval_logs_what_ran(db: Database) -> None:
    store = RunStore(db)
    store.open_approval("e", None, "gmail_send", {"to": "x@y.z"})
    store.decide("e", "allow", edited_args={"to": "fixed@y.z"})
    (r,) = rows(db)
    assert r["decision"] == "edited" and "fixed@y.z" in r["args_summary"] and r["agent"] == "chat"


def test_grant_auto_allow_and_reviewed_calls(db: Database) -> None:
    approval_log.record(db, tool="shell_run", args={"command": "ls"}, decision="always", scope="rule", rule="shell_run(ls *)")
    approval_log.record(db, tool="calendar_create", args={"title": "x"}, decision="auto",
                        review={"verdict": "allow", "reason": "asked for it", "model": "m", "ms": 9})
    by = {r["tool"]: r for r in rows(db)}
    assert by["shell_run"]["scope"] == "rule" and by["shell_run"]["rule"] == "shell_run(ls *)"
    assert by["calendar_create"]["reviewer_verdict"] == "allow" and by["calendar_create"]["decision"] == "auto"


def test_history_filters_and_pages(db: Database) -> None:
    for i in range(5):
        approval_log.record(db, tool="a" if i % 2 else "b", args={"n": i}, decision="deny" if i == 4 else "allow_once",
                            note="needle" if i == 3 else None)
    assert {r["tool"] for r in approval_log.history(db, tool="a")["items"]} == {"a"}
    assert [r["decision"] for r in approval_log.history(db, decision="deny")["items"]] == ["deny"]
    assert len(approval_log.history(db, q="needle")["items"]) == 1
    first = approval_log.history(db, limit=2)
    assert len(first["items"]) == 2 and first["more"]
    last = approval_log.history(db, limit=2, offset=4)
    assert len(last["items"]) == 1 and not last["more"]
    assert first["items"][0]["ts"] >= first["items"][1]["ts"]  # newest first


def test_summary_is_short_and_redacted() -> None:
    s = approval_log.summarize({"body": "x" * 1000, "api_key": "sk-abc", "nested": {"token": "t0k"}})
    assert len(s) <= approval_log.SUMMARY_CHARS and "sk-abc" not in s and "t0k" not in s


def test_desk_reviewer_verdict_is_recorded(db: Database, tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    desks = Desks(db, ws)
    desk = desks.create(conversation_id="c1", brief="b")
    deskgate.record_review(SimpleNamespace(desks=desks), {"conversation_id": "c1", "run_id": None}, desk["id"], "fail", "1. only two tiers")
    (r,) = rows(db)
    assert r["decision"] == "review" and r["desk_id"] == desk["id"] and r["reviewer_verdict"] == "fail"
    assert "only two tiers" in r["reviewer_reason"]
    assert any(e["body"].startswith("Reviewed: fail") for e in desks.events(desk["id"]) if e["kind"] == "note")
    # A broken toolbox never raises out of the gate.
    deskgate.record_review(SimpleNamespace(), {}, desk["id"], "pass", "")
