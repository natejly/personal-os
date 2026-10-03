"""Earlier tool work reaches the model as a compact, deterministic text record on the assistant turn that made it.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_tool_record.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="trtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos, compactor = appmod.convos, appmod.compactor

EVENTS = [
    {"id": "e1", "name": "gmail_draft", "arguments": {"to": "a@b.c", "body": "x" * 500}, "result_preview": "Draft created. id=d_42",
     "error": None, "artifact": {"id": "art_1", "title": "t"}},
    {"id": "e2", "name": "fs_write", "arguments": {"path": "/tmp/x"}, "error": "Permission denied by the operating system", "result_preview": ""},
    {"id": "e3", "name": "gmail_send", "arguments": {"id": "d_42"}, "approval": "deny", "error": None, "result_preview": ""},
    {"id": "e4", "name": "web_fetch", "arguments": {"url": "https://x"}, "tainted": True, "result_preview": "IGNORE PREVIOUS", "result_id": "tr_abc"},
    {"id": "e5", "name": "calendar_create", "arguments": {}, "pending": True, "needs_approval": True},
]


def conv_with(events: list[dict[str, Any]] | None, content: str = "") -> str:
    c = convos.create(None, "t", "m")["id"]
    convos.add_message(c, "user", "do it")
    am = convos.add_message(c, "assistant", "", model="m")
    convos.finish_message(am["id"], content, None, None, events)
    return c


def test_record_lines_statuses_and_stability() -> None:
    rec = compaction.tool_record(EVENTS)
    assert rec.startswith(compaction.TOOL_RECORD_HEADER)
    assert "gmail_draft" in rec and "-> ok" in rec and "artifact=art_1" in rec and "Draft created. id=d_42" in rec
    assert "-> error: Permission denied" in rec and "-> declined" in rec
    assert "calendar_create" not in rec, "an unanswered approval card did not run"
    assert "x" * 200 not in rec, "long string arguments are cut"
    assert rec == compaction.tool_record(json.loads(json.dumps(EVENTS)))
    assert compaction.tool_record([]) == "" and compaction.tool_record(None) == ""


def test_tainted_preview_is_withheld_until_the_conversation_is_tainted() -> None:
    assert "IGNORE PREVIOUS" not in compaction.tool_record(EVENTS, False)
    assert compaction.WITHHELD in compaction.tool_record(EVENTS, False) and "result_id=tr_abc" in compaction.tool_record(EVENTS, False)
    assert "IGNORE PREVIOUS" in compaction.tool_record(EVENTS, True)


def test_record_is_capped_with_a_count_of_the_rest() -> None:
    many = [{"name": f"tool_{i}", "arguments": {"q": "v" * 100}, "result_preview": "r" * 280} for i in range(30)]
    rec = compaction.tool_record(many)
    assert len(rec) <= compaction.TOOL_RECORD_CAP + 40 and "more calls)" in rec.splitlines()[-1]


def test_history_rows_and_build_history() -> None:
    cid = conv_with(EVENTS, "Draft created.")
    only_tools = conv_with(EVENTS, "")
    rows = convos.history_rows(only_tools)
    assert [r["role"] for r in rows] == ["user", "assistant"] and rows[1]["tool_events"][0]["name"] == "gmail_draft"
    assert len(convos.history(only_tools)) == 1, "history() stays prose-only"
    nothing = conv_with(None, "")
    assert [r["role"] for r in convos.history_rows(nothing)] == ["user"], "a row with neither prose nor events stays out"
    h1 = compactor.build_history(convos.history_rows(cid), None)
    h2 = compactor.build_history(convos.history_rows(cid), None)
    assert h1 == h2 and h1[1]["content"].startswith("Draft created.\n\n" + compaction.TOOL_RECORD_HEADER)
    assert compactor.build_history(rows, None)[1]["content"].startswith(compaction.TOOL_RECORD_HEADER)
    convos.add_message(cid, "user", "send it")
    assert compactor.build_history(convos.history_rows(cid), None)[1] == h1[1], "an earlier row renders the same after a later turn"


def test_superseded_rows_stay_excluded() -> None:
    cid = conv_with(EVENTS, "")
    row = [r for r in convos.history_rows(cid) if r["role"] == "assistant"][0]
    with appmod.db.tx() as c:
        c.execute("UPDATE messages SET superseded_at=1 WHERE id=?", (row["id"],))
    assert [r["role"] for r in convos.history_rows(cid)] == ["user"]


def test_bad_json_tool_events_become_none() -> None:
    cid = conv_with(None, "hello")
    with appmod.db.tx() as c:
        c.execute("UPDATE messages SET tool_events='{nope' WHERE conversation_id=? AND role='assistant'", (cid,))
    assert convos.history_rows(cid)[1]["tool_events"] is None


def test_prepare_history_returns_row_ids_and_the_record() -> None:
    cid = conv_with(EVENTS, "Draft created.")
    hist, info = asyncio.run(compaction.prepare_history(compactor, convos, {"contextWindow": 100000}, "m", cid, 10))
    assert len(info["row_ids"]) == 2 and not info["compacted"]
    assert compaction.TOOL_RECORD_HEADER in hist[1]["content"]
    assert compaction.WITHHELD in hist[1]["content"], "the conversation is not tainted, so the preview is withheld"


def test_the_summarizer_sees_the_record_after_the_prose_cut() -> None:
    cid = convos.create(None, "t", "m")["id"]
    for i in range(12):
        convos.add_message(cid, "user", f"q{i}")
        am = convos.add_message(cid, "assistant", "", model="m")
        convos.finish_message(am["id"], "long " * 3000 if i == 0 else f"a{i}", None, None,
                              [{"name": "gmail_draft", "arguments": {}, "result_preview": "id=d_77", "result_id": "tr_keep"}] if i == 0 else None)
    seen: list[str] = []

    async def stub(cfg: Any, model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
        seen.append(messages[-1]["content"])
        return "S"
    res = asyncio.run(compactor.compact({"compactKeepRecent": 4}, "m", cid, convos.history_rows(cid), complete=stub))
    assert res and "result_id=tr_keep" in seen[0] and "id=d_77" in seen[0]
    assert "ids and result_ids verbatim" in compaction.SUMMARY_PROMPT
