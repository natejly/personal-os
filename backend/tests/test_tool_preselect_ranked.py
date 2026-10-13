"""mcp_search.preselect_ranked: the rerank model decides what loads before round 1; bm25 is the fallback.

Runs under pytest, or directly: python backend/tests/test_tool_preselect_ranked.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import mcp_search, retrieval_rerank  # noqa: E402

SETTINGS = {"provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1"}
DOCS = mcp_search.build_docs([
    {"slug": "calendar_update", "description": "Edit one calendar event", "parameters": {}},
    {"slug": "gmail_read", "description": "Read an email by id", "parameters": {}},
    {"slug": "todo_add", "description": "Add a todo", "parameters": {}},
])


def run(text: str, settings: dict[str, Any] = SETTINGS) -> list[str]:
    return asyncio.run(mcp_search.preselect_ranked(DOCS, text, settings))


def fake(monkeypatch: Any, result: Any, calls: list[int] | None = None) -> None:
    async def rerank_scores(settings, model, query, docs, **kw):  # noqa: ANN001
        if calls is not None:
            calls.append(len(docs))
        if isinstance(result, BaseException):
            raise result
        await asyncio.sleep(result if isinstance(result, (int, float)) else 0)
        return [] if isinstance(result, (int, float)) else result

    monkeypatch.setattr(retrieval_rerank, "rerank_scores", rerank_scores)
    monkeypatch.setattr(mcp_search, "_rank_down_until", 0.0)


def test_picks_above_threshold_load(monkeypatch: Any) -> None:
    fake(monkeypatch, [(0, 0.9), (2, 0.5), (1, 0.1)])  # 0.1 is below the share of the top score
    assert run("move my 3pm") == ["calendar_update", "todo_add"]


def test_below_threshold_loads_nothing(monkeypatch: Any) -> None:
    fake(monkeypatch, [(1, mcp_search.PRESELECT_RANK_MIN / 2)])
    assert run("tell me a joke") == []


def test_error_falls_back_to_bm25_and_backs_off(monkeypatch: Any) -> None:
    calls: list[int] = []
    fake(monkeypatch, RuntimeError("503"), calls)
    text = "calendar event calendar event calendar event"
    assert run(text) == mcp_search.preselect(DOCS, text)
    assert run(text) == mcp_search.preselect(DOCS, text)
    assert calls == [3]  # the second turn did not call: backed off


def test_timeout_falls_back(monkeypatch: Any) -> None:
    fake(monkeypatch, 5)
    monkeypatch.setattr(mcp_search, "PRESELECT_TIMEOUT", 0.05)
    assert run("todo") == mcp_search.preselect(DOCS, "todo")
    assert mcp_search._rank_down_until > 0


def test_no_rerank_route_or_model_falls_back(monkeypatch: Any) -> None:
    calls: list[int] = []
    fake(monkeypatch, None, calls)  # rerank_scores returns None: no such route
    assert run("todo") == mcp_search.preselect(DOCS, "todo")
    fake(monkeypatch, [(0, 1.0)], calls)
    assert run("todo", {"provider": "anthropic", "baseUrl": "https://api.anthropic.com/v1"}) == mcp_search.preselect(DOCS, "todo")  # no rerank model resolves
    assert calls == [3]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
