"""About-you summary: evidence-only draft, review-gated accept that supersedes, dismiss."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import about_you, memory_limits as ml  # noqa: E402
from personal_os.consolidate import Consolidator  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Graph, Memories  # noqa: E402


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(tmp)
        m = Memories(db)
        yield m, Consolidator(db, m, Graph(db))


def _seed(m: Memories, n: int = 6) -> list[dict[str, Any]]:
    return [m.create(None, f"The user fact number {i}", source="auto") for i in range(n)]


def _stub(monkeypatch: Any, reply: Any, calls: list | None = None) -> None:
    async def fake(settings: Any, model: str, messages: Any, kind: str = "learn") -> str:
        if calls is not None:
            calls.append(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    monkeypatch.setattr(about_you.llm, "complete", fake)


def _run(c: Consolidator):
    return asyncio.run(about_you.draft(c, {}))


def test_draft_uses_live_memories_only_and_drops_unsourced_lines(env, monkeypatch):
    m, c = env
    rows = _seed(m)
    gone = m.create(None, "Retracted secret fact", source="auto")
    m.invalidate(gone["id"])
    calls: list = []
    _stub(monkeypatch, {"lines": [{"text": "They have fact 1.", "ids": ["M1"]}, {"text": "Made up.", "ids": []},
                                  {"text": "Bad id.", "ids": ["M99"]}]}, calls)
    made, reason = _run(c)
    assert reason == "ok" and made["kind"] == "about_you" and made["status"] == "pending"
    assert made["payload"]["text"] == "They have fact 1."
    assert "Retracted secret" not in calls[0][1]["content"] and all(r["content"] in calls[0][1]["content"] for r in rows)
    assert "Evidence only" in calls[0][0]["content"]


def test_cap_enforced_by_whole_lines(env, monkeypatch):
    m, c = env
    _seed(m)
    long = "x" * (ml.ABOUT_YOU_CHARS - 50)
    _stub(monkeypatch, {"lines": [{"text": long, "ids": ["M1"]}, {"text": "y" * 100, "ids": ["M2"]}]})
    made, _ = _run(c)
    assert made["payload"]["text"] == long and len(made["payload"]["text"]) <= ml.ABOUT_YOU_CHARS


def test_under_threshold_failure_and_unchanged_make_nothing(env, monkeypatch):
    m, c = env
    _stub(monkeypatch, {"lines": [{"text": "T.", "ids": ["M1"]}]})
    _seed(m, 3)
    assert _run(c) == (None, "too_few")
    _seed(m, 6)
    _stub(monkeypatch, RuntimeError("model down"))
    assert _run(c) == (None, "failed") and c.list("pending") == []
    _stub(monkeypatch, {"lines": [{"text": "T.", "ids": ["M1"]}]})
    assert _run(c)[1] == "ok"
    assert _run(c) == (None, "unchanged")  # same memories as the last draft


def test_new_draft_replaces_pending_one(env, monkeypatch):
    m, c = env
    _seed(m)
    _stub(monkeypatch, {"lines": [{"text": "First.", "ids": ["M1"]}]})
    first, _ = _run(c)
    m.create(None, "A brand new fact", source="auto")
    _stub(monkeypatch, {"lines": [{"text": "Second.", "ids": ["M1"]}]})
    second, _ = _run(c)
    assert [p["id"] for p in c.list("pending")] == [second["id"]] and c.get(first["id"])["status"] == "dismissed"


def test_accept_pins_then_supersedes_previous_keeping_history(env, monkeypatch):
    m, c = env
    _seed(m)
    _stub(monkeypatch, {"lines": [{"text": "They like tea.", "ids": ["M1"]}]})
    p1, _ = _run(c)
    assert not [r for r in m.list(None) if r["source"] == "about_you"]  # a draft is not a memory
    assert c.apply(p1["id"])["status"] == "applied"
    first = about_you.current(c)
    assert first["pinned"] == 1 and first["content"] == "They like tea." and first["kind"] == "note"
    m.create(None, "Another fact", source="auto")
    _stub(monkeypatch, {"lines": [{"text": "They like tea and chess.", "ids": ["M1"]}]})
    p2, _ = _run(c)
    assert about_you.accept(c, p2["id"], "  Edited   text. ")["status"] == "applied"
    now_ = about_you.current(c)
    assert now_["content"] == "Edited text." and now_["pinned"] == 1
    old = m.list(None, include_invalid=True)
    old = next(r for r in old if r["id"] == first["id"])
    assert old["invalid_at"] is not None and old["superseded_by"] == now_["id"]  # history kept
    assert len([r for r in m.list(None) if r["source"] == "about_you"]) == 1
    assert about_you.current(c)["id"] in {r["id"] for r in m.profile(None)}  # rides in the profile block


def test_dismiss_changes_nothing(env, monkeypatch):
    m, c = env
    _seed(m)
    _stub(monkeypatch, {"lines": [{"text": "T.", "ids": ["M1"]}]})
    p, _ = _run(c)
    assert c.dismiss(p["id"])["status"] == "dismissed" and about_you.current(c) is None
    assert about_you.accept(c, p["id"])["status"] == "dismissed"  # cannot be accepted afterwards


def test_maybe_draft_waits_for_enough_new_auto_memories(env, monkeypatch):
    m, c = env
    _stub(monkeypatch, {"lines": [{"text": "T.", "ids": ["M1"]}]})
    _seed(m, 6)
    assert asyncio.run(about_you.maybe_draft(c, {"aboutYouEvery": 7})) is False
    m.create(None, "one more", source="auto")
    assert asyncio.run(about_you.maybe_draft(c, {"aboutYouEvery": 7})) is True
    assert asyncio.run(about_you.maybe_draft(c, {"aboutYouEvery": 7})) is False
