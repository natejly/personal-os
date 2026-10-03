"""Absolute dates on new memories; fact + message time on edges; ended edges as history."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import learn  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents, Graph, Memories  # noqa: E402

TS = datetime(2026, 10, 2, 9, 0).timestamp()  # a Friday


def _learn(reply: dict[str, Any], monkeypatch: Any, tmp: str) -> tuple[Database, dict[str, Any]]:
    seen: list[Any] = []

    async def fake(settings: Any, model: str, messages: Any, kind: str = "learn") -> str:
        seen.extend(messages)
        return json.dumps(reply)

    monkeypatch.setattr(learn.llm, "complete", fake)
    db = Database(tmp)
    out = asyncio.run(learn.learn_from_exchange(settings={}, memories=Memories(db), graph=Graph(db), project_id=None,
                                                user_text="hi", assistant_text="ok", model="m", message_ts=TS))
    assert "Today is Friday, 2026-10-02" in seen[0]["content"]
    return db, out


def test_relative_dates_become_absolute_or_drop(monkeypatch: Any) -> None:
    reply = {"memories": [{"content": "User is traveling next Tuesday"},
                          {"content": "User went to Singapore in July 2026"},
                          {"content": "User has a launch next month"}]}
    with tempfile.TemporaryDirectory() as tmp:
        _, out = _learn(reply, monkeypatch, tmp)
    assert [x["content"] for x in out["memories"]] == ["User is traveling 2026-10-06", "User went to Singapore in July 2026"]


def test_edge_fact_valid_at_and_context(monkeypatch: Any) -> None:
    reply = {"entities": [{"label": "Acme"}, {"label": "Bea"}],
             "relations": [{"source": "Acme", "target": "Bea", "relation": "employs", "fact": "Bea joined Acme as designer"}]}
    with tempfile.TemporaryDirectory() as tmp:
        db, out = _learn(reply, monkeypatch, tmp)
        e = out["edges"][0]
        assert e["fact"] == "Bea joined Acme as designer" and e["valid_at"] == TS
        system, _ = build_context(memories=Memories(db), graph=Graph(db), documents=Documents(db), project=None, project_id=None,
                                  query="Acme", settings={}, conv_settings={"useGraph": True}, global_system_prompt="")
        assert "Bea joined Acme as designer" in system and "2026-10-02" in system


def test_ended_edge_hidden_by_default() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        g = Graph(Database(tmp))
        a, b = g.upsert_node(None, "Acme"), g.upsert_node(None, "Bea")
        e = g.upsert_edge(None, a["id"], b["id"], "employs")
        g.invalidate_edge(e["id"])
        assert g.get(None)["edges"] == []
        assert [x["id"] for x in g.get(None, True, True)["edges"]] == [e["id"]]
