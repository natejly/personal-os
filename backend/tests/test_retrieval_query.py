"""A follow-up message is retrieved with the turn before it in view; the model still sees what was typed.
Run: PYTHONPATH=backend python -m pytest backend/tests/test_retrieval_query.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import app as app_mod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.context import build_context, retrieval_query  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents, Graph, Memories  # noqa: E402

import pytest  # noqa: E402

HISTORY = [
    {"role": "user", "content": "Summarise the Q3 budget memo and list its open questions"},
    {"role": "assistant", "content": "The Q3 budget memo raises three questions: hiring, travel and the vendor renewal."},
]


def test_standalone_question_is_unchanged() -> None:
    q = "Summarise every section of the quarterly hiring plan for engineering and design teams please"
    assert retrieval_query(HISTORY, q) == q


def test_follow_up_carries_the_previous_turn() -> None:
    rq = retrieval_query(HISTORY, "what about the second one?")
    assert "budget memo" in rq and rq.endswith("what about the second one?")
    assert "vendor renewal" in rq  # head of the last reply


def test_long_reply_is_clipped() -> None:
    rq = retrieval_query([HISTORY[0], {"role": "assistant", "content": "x" * 1000}], "and then?")
    assert rq.count("x") == 300


def test_first_message_has_no_history() -> None:
    assert retrieval_query([], "what about it?") == "what about it?"


def test_bm25_fallback_finds_the_memo_for_a_follow_up() -> None:
    db = Database(tempfile.mkdtemp(prefix="rq-"))
    documents = Documents(db)
    memo = documents.create(None, "q3-budget.txt", "text/plain", 10, "", "Q3 budget memo: hiring freeze, travel cap, vendor renewal.")
    documents.create(None, "garden.txt", "text/plain", 10, "", "Tomatoes need sun and water every second day.")
    follow = "what about the second one?"
    kw: dict[str, Any] = dict(memories=Memories(db), graph=Graph(db), documents=documents, project=None, project_id=None,
                              query=follow, settings={}, conv_settings={"useMemory": False, "useGraph": False},
                              global_system_prompt="")
    _, used = build_context(**kw, retrieval_text=retrieval_query(HISTORY, follow))
    assert memo["id"] in [c["document_id"] for c in used["chunks"]]
    _, raw = build_context(**kw)  # without it, "second" finds the garden note, not the memo
    assert memo["id"] not in [c["document_id"] for c in raw["chunks"]]


# ---- through the chat run: new message and regenerate --------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    with T.client:
        T.client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield


def test_stream_retrieves_with_history_and_regenerate_counts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def spy(project_id: Any, query: str, cfg: Any, conv_settings: Any) -> Any:
        seen.append(query)
        return None

    monkeypatch.setattr(app_mod, "_doc_hits", spy)
    llm.stream_chat = T._scripted_stream
    T.SCRIPT["chunks"] = ["The Q3 budget memo raises hiring and travel."]
    T.SCRIPT["delay"] = 0.0
    cid = T.new_conv()
    T.j("POST", f"/conversations/{cid}/chat", {"content": "Summarise the Q3 budget memo"})
    T.drain(cid)
    assert seen[-1] == "Summarise the Q3 budget memo"  # first message: nothing to add
    T.j("POST", f"/conversations/{cid}/chat", {"content": "what about the second one?"})
    T.drain(cid)
    assert seen[-1].startswith("Summarise the Q3 budget memo\nThe Q3 budget memo raises")
    assert seen[-1].endswith("what about the second one?")
    first = seen[-1]
    T.j("POST", f"/conversations/{cid}/chat", {})  # regenerate: the trailing user message is not history
    T.drain(cid)
    assert seen[-1] == first
    assert seen[-1].count("what about the second one?") == 1
    msgs = T.j("GET", f"/conversations/{cid}")["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"][-1] == "what about the second one?"
