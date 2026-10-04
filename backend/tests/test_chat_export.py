"""Per-chat Markdown export (GET /conversations/{id}/export) and the whole-app export it shares a renderer with.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_chat_export.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir)

from personal_os import app as app_mod  # noqa: E402
from personal_os import backups  # noqa: E402
from personal_os.db import new_id  # noqa: E402

client, j, check = T.client, T.j, T.check

try:
    import pytest
except ImportError:
    pytest = None
else:
    @pytest.fixture(scope="module", autouse=True)
    def _portal():  # type: ignore[no-untyped-def]
        with client:
            client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
            yield


def _row(c, cid: str, role: str, content: str, t: float, **kw) -> str:  # type: ignore[no-untyped-def]
    mid = new_id()
    c.execute("INSERT INTO messages(id,conversation_id,role,content,model,tool_events,trace,superseded_at,variant_of,created_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (mid, cid, role, content, kw.get("model"), json.dumps(kw["tools"]) if "tools" in kw else None,
               json.dumps(kw["trace"]) if "trace" in kw else None, kw.get("superseded_at"), kw.get("variant_of"), t))
    return mid


def _chat(title: str) -> str:
    cid = T.new_conv()
    j("PATCH", f"/conversations/{cid}", {"title": title})
    t = time.time()
    with app_mod.db.tx() as c:
        _row(c, cid, "user", "first question", t)
        old = _row(c, cid, "assistant", "OLD-ANSWER", t + 1, model="m1", superseded_at=t + 3)
        _row(c, cid, "assistant", "new answer", t + 2, model="m1", variant_of=old,
             tools=[{"id": "1", "name": "web_search", "arguments": {"q": "SECRET-ARG"}, "result_preview": "RESULT-BODY", "error": None},
                    {"id": "2", "name": "gmail_send", "arguments": {}, "result_preview": "", "error": "Declined by the user", "approval": "deny"},
                    {"id": "3", "name": "fetch_url", "arguments": {}, "result_preview": "", "error": "timeout\nstack"}])
        _row(c, cid, "user", "second question", t + 4)
        _row(c, cid, "assistant", "second answer", t + 5, model="m2", trace=[{"kind": "compact", "meta": {"kind": "history"}}])
    return cid


def test_export_reads_active_rows_in_order() -> None:
    cid = _chat("Trip plans")
    out = j("GET", f"/conversations/{cid}/export")
    md = out["text"]
    check(out["title"] == "Trip plans", "the title comes back for the renderer to name the file")
    check(md.startswith("---\nid: " + cid) and "exported: " in md and "# Trip plans" in md, "front matter then the title")
    order = [md.index(s) for s in ("first question", "new answer", "second question", "second answer")]
    check(order == sorted(order), "rows in order")
    check("OLD-ANSWER" not in md, "superseded variants are left out")
    check(md.count("## You") == 2 and md.count("## Grain") == 2, "one heading per turn")
    check("· m2" in md, "the model rides the assistant heading")
    check("summarized to fit the context window" in md, "a compaction is marked")


def test_tool_calls_are_summaries() -> None:
    md = j("GET", f"/conversations/{_chat('Tools')}/export")["text"]
    check("- Tool `web_search` (ok)" in md, "a successful call")
    check("- Tool `gmail_send` (declined)" in md, "a declined call")
    check("- Tool `fetch_url` (failed: timeout)" in md and "stack" not in md, "a failure keeps only its first line")
    check("RESULT-BODY" not in md and "SECRET-ARG" not in md, "no result bodies or arguments")


def test_unknown_and_trashed_are_404() -> None:
    j("GET", "/conversations/nope/export", expect=404)
    cid = _chat("Binned")
    with app_mod.db.tx() as c:
        c.execute("UPDATE conversations SET deleted_at=? WHERE id=?", (time.time(), cid))
    j("GET", f"/conversations/{cid}/export", expect=404)


def test_whole_app_export_skips_trash_and_shares_the_renderer() -> None:
    keep, gone = _chat("Kept chat"), _chat("Trashed chat")
    with app_mod.db.tx() as c:
        c.execute("UPDATE conversations SET deleted_at=? WHERE id=?", (time.time(), gone))
    dest = Path(tempfile.mkdtemp(prefix="chat-export-")) / "snap.db"
    with app_mod.db.tx() as c:
        c.execute("VACUUM INTO ?", (str(dest),))
    md, rows = backups.human_export(dest)["conversations"]
    check("# Kept chat" in md and "Trashed chat" not in md, "trashed chats are not exported")
    check("- Tool `web_search` (ok)" in md and "RESULT-BODY" not in md, "same tool summaries as the per-chat export")
    check(all("tool_events" not in m for r in rows for m in r["messages"]), "the JSON carries no tool payloads")
    check(keep in {r["id"] for r in rows} and gone not in {r["id"] for r in rows}, "the JSON skips trash too")
