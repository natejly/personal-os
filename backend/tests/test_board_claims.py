"""Card claims, the append-only event log, checker-only completion, idempotent proposals. Offline, injected clock.

Run: uv run --project backend --with pytest pytest backend/tests/test_board_claims.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="boardclaims-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os.boards import Boards  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.jobs import Proposals  # noqa: E402

T = 1_000_000.0


@pytest.fixture()
def env():
    db = Database(Path(tempfile.mkdtemp(prefix="bc-")) / "t.db")
    b = Boards(db)
    board = b.create("B")
    card = b.add_card(board["id"], None, "Task")
    return db, b, board, card


def kinds(b, cid):
    return [(e["kind"], e["payload"].get("reason")) for e in b.events(cid)]


def test_a_token_in_a_missing_board_is_stripped() -> None:
    from personal_os.tools import Toolbox
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    db = Database(Path(tempfile.mkdtemp(prefix="bc-")) / "t.db")
    boards = Boards(db)
    boards.create(pat)
    box = Toolbox(None, None, None, lambda: {}, boards=boards)  # type: ignore[arg-type]
    hinted = asyncio.run(box.call("board_list", {"board": "Nope"}, {"project_id": None}))
    assert pat not in str(hinted)
    assert "Nope" in hinted["error"] and hinted["example"]["board"] == "[github-pat]"
    assert boards.list()[0]["name"] == pat
    missed = asyncio.run(box.call("board_move_card", {"board": pat, "card": pat, "column": "Done"}, {"project_id": None}))
    assert pat not in str(missed)
    assert "[github-pat]" in missed["error"] and missed["example"]["card"] == "[github-pat]"
    assert boards.list()[0]["name"] == pat


def test_a_token_in_a_card_title_is_stripped() -> None:
    from personal_os.tools import Toolbox
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    db = Database(Path(tempfile.mkdtemp(prefix="bc-")) / "t.db")
    boards = Boards(db)
    board = boards.create("Work")
    boards.add_card(board["id"], None, f"Send {pat}")
    box = Toolbox(None, None, None, lambda: {}, boards=boards)  # type: ignore[arg-type]
    out = asyncio.run(box.call("board_list", {"board": "Work"}, {"project_id": None}))
    assert pat not in out["cards"][0]["title"]
    assert "[github-pat]" in out["cards"][0]["title"]
    assert pat in boards.find_board("Work")["cards"][0]["title"]


def test_a_token_in_a_new_card_title_is_stripped() -> None:
    from personal_os.tools import Toolbox
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    db = Database(Path(tempfile.mkdtemp(prefix="bc-")) / "t.db")
    boards = Boards(db)
    boards.create("Work")
    box = Toolbox(None, None, None, lambda: {}, boards=boards)  # type: ignore[arg-type]
    added = asyncio.run(box.call("board_add_card", {"board": "Work", "title": f"Send {pat}"}, {"project_id": None}))
    assert pat not in added["added"] and "[github-pat]" in added["added"]
    assert pat in boards.find_board("Work")["cards"][0]["title"]
    made = asyncio.run(box.call("board_create", {"name": f"Board {pat}", "columns": [f"Col {pat}"]}, {"project_id": None}))
    assert pat not in made["created"] and pat not in "".join(made["columns"])
    assert "[github-pat]" in made["created"] and "[github-pat]" in made["columns"][0]
    stored = next(b for b in boards.list() if pat in b["name"])
    assert pat in stored["name"]


def test_concurrent_claims_one_winner(env):
    _, b, _, card = env
    out: list = []
    ts = [threading.Thread(target=lambda h=h: out.append(b.claim(card["id"], h, 60, T))) for h in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(x is not None for x in out) == 1 and sum(x is None for x in out) == 1


def test_expiry_allows_reclaim_and_stale_token_moves_nothing(env):
    _, b, board, card = env
    t1 = b.claim(card["id"], "a", 60, T)
    assert b.claim(card["id"], "b", 60, T + 30) is None
    t2 = b.claim(card["id"], "b", 60, T + 61)
    assert t2 and t2 != t1
    col = board["columns"][2]["id"]
    assert b.agent_move(card["id"], t1, col, T + 62) is False
    assert b.event(card["id"], t1, "comment", {"text": "x"}, T + 62) is False
    assert b.get(board["id"])["cards"][0]["column_id"] != col
    assert b.agent_move(card["id"], t2, col, T + 62) is True
    assert ("released", "expired") in kinds(b, card["id"])


def test_user_move_preempts_in_one_transaction(env):
    _, b, board, card = env
    tok = b.claim(card["id"], "a", 600)
    b.move_card(card["id"], board["columns"][3]["id"])
    row = b.get(board["id"])["cards"][0]
    assert row["claim_token"] is None and row["lease_expires_at"] is None and row["claimed_by"] is None
    k = kinds(b, card["id"])
    assert ("moved", None) in k and ("released", "preempted") in k
    assert b.event(card["id"], tok, "comment") is False


def test_completed_only_through_complete(env):
    _, b, _, card = env
    tok = b.claim(card["id"], "a", 600)
    with pytest.raises(ValueError):
        b.event(card["id"], tok, "completed", {"check": "tests_passed"})
    b.sweep(T + 10**12)  # expiry never completes
    assert all(e["kind"] != "completed" for e in b.events(card["id"]))
    assert b.complete(card["id"], "path_exists", "/tmp/out.txt")
    done = [e for e in b.events(card["id"]) if e["kind"] == "completed"]
    assert done[0]["payload"] == {"check": "path_exists", "pointer": "/tmp/out.txt"}
    with pytest.raises(ValueError):
        b.complete(card["id"], "vibes", "x")


def test_log_is_ordered_and_complete(env):
    _, b, _, card = env
    tok = b.claim(card["id"], "a", 600)
    b.event(card["id"], tok, "comment", {"text": "hi"})
    assert b.release(card["id"], tok, "finished")
    ev = b.events(card["id"])
    assert [e["seq"] for e in ev] == sorted(e["seq"] for e in ev)
    assert [e["kind"] for e in ev] == ["created", "claimed", "comment", "released"]


def test_proposal_idempotency(env):
    db, _, _, _ = env
    p = Proposals(db)
    a = p.create(run_id=None, tool="send_email", args={"to": "x"}, scope="card1")
    b2 = p.create(run_id=None, tool="send_email", args={"to": "x"}, scope="card1")
    assert a["id"] == b2["id"]
    assert p.create(run_id=None, tool="send_email", args={"to": "y"}, scope="card1")["id"] != a["id"]
    assert p.claim(a["id"]) is not None and p.claim(a["id"]) is None  # accepting twice runs the tool once
    # without a scope every call stays its own proposal
    assert p.create(run_id=None, tool="t", args={})["id"] != p.create(run_id=None, tool="t", args={})["id"]


def test_http_surface():
    from fastapi.testclient import TestClient

    from personal_os import app as appmod
    c = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    bd = c.post("/boards", json={"name": "H"}).json()
    card = c.post(f"/boards/{bd['id']}/cards", json={"title": "t"}).json()
    tok = c.post(f"/boards/cards/{card['id']}/claim", json={"holder": "agent"}).json()["token"]
    assert c.post(f"/boards/cards/{card['id']}/claim", json={"holder": "x"}).status_code == 409
    c.post(f"/boards/cards/{card['id']}/move", json={"column_id": bd["columns"][1]["id"]})
    evs = c.get(f"/boards/{bd['id']}/cards/{card['id']}/events").json()
    assert ("released", "preempted") in [(e["kind"], e["payload"].get("reason")) for e in evs]
    assert c.post(f"/boards/cards/{card['id']}/release", json={"token": tok}).json() == {"ok": False}
