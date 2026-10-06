"""Edit and resend: a user message replaced in place; it and everything after it is soft-superseded inside the run.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_edit_resend.py
Built on the test_runs harness (TestClient + a scripted llm.stream_chat).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import app as app_mod  # noqa: E402
from personal_os import llm  # noqa: E402

client, j, check, drain, events, read_streams = T.client, T.j, T.check, T.drain, T.events, T.read_streams

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

SEEN: list[list[dict[str, Any]]] = []


async def _recording(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    SEEN.append(messages)
    async for ev in T._scripted_stream(settings, model, messages, *a, **k):
        yield ev


def three_turns() -> str:
    llm.stream_chat = T._scripted_stream
    T.SCRIPT["chunks"] = ["ok"]
    T.SCRIPT["delay"] = 0.0
    cid = T.new_conv()
    for t in ("one", "two", "three"):
        j("POST", f"/conversations/{cid}/chat", {"content": t})
        drain(cid)
    return cid


def msgs(cid: str) -> list[dict[str, Any]]:
    return j("GET", f"/conversations/{cid}")["messages"]


def tape(cid: str) -> list[tuple[str, Any]]:
    return events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])


def edit(cid: str, mid: str, text: str = "edited") -> None:
    j("POST", f"/conversations/{cid}/chat", {"content": text, "replace_from": mid})
    drain(cid)


def test_edit_the_first_turn_supersedes_the_rest() -> None:
    cid = three_turns()
    first = msgs(cid)[0]
    SEEN.clear()
    llm.stream_chat = _recording
    edit(cid, first["id"])
    now = msgs(cid)
    check([m["role"] for m in now] == ["user", "assistant"] and now[0]["content"] == "edited", f"only the edited turn is live, got {now}")
    evs = tape(cid)
    names = [e for e, _ in evs if e in ("removed_message", "user_message", "assistant_message")]
    check(names == ["removed_message"] * 6 + ["user_message", "assistant_message"], f"tape order, got {names}")
    check(dict(evs)["user_message"]["edited_from"] == first["id"], "user_message carries edited_from")
    sent = [m["content"] for m in SEEN[-1] if m["role"] in ("user", "assistant")]
    check(sent == ["edited"], f"the model saw only the edited turn, got {sent}")
    with app_mod.db.tx() as c:
        rows = c.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (cid,)).fetchone()["n"]
    check(rows == 8, "nothing was deleted")
    llm.stream_chat = T._scripted_stream


def test_title_resets_when_the_first_message_is_cut() -> None:
    cid = three_turns()
    check(j("GET", f"/conversations/{cid}")["title"] == "one", "the title was derived from the first message")
    edit(cid, msgs(cid)[0]["id"], "a brand new topic")
    check(j("GET", f"/conversations/{cid}")["title"] == "a brand new topic", "the title is re-derived from the new first message")
    check(("title", {"id": cid, "title": "a brand new topic"}) in tape(cid), "a title event announces it")
    cid2 = three_turns()
    app_mod.convos.update(cid2, {"title": "Keep me"})
    edit(cid2, msgs(cid2)[2]["id"], "second turn edited")
    check(j("GET", f"/conversations/{cid2}")["title"] == "Keep me", "a later cut leaves the title alone")
    cid3 = three_turns()
    app_mod.convos.update(cid3, {"title": "Renamed by hand"})
    edit(cid3, msgs(cid3)[0]["id"], "a brand new topic")
    check(j("GET", f"/conversations/{cid3}")["title"] == "Renamed by hand", "a user-renamed title survives a first-message cut")


def test_had_writes_names_hidden_mutating_tool_runs() -> None:
    from personal_os.plans import MUTATING
    cid = three_turns()
    ms = msgs(cid)
    tool = next(n for n, s in app_mod.toolbox.specs.items() if s.danger in MUTATING)
    ran = {"id": "t1", "name": tool, "arguments": {}, "result_preview": "", "duration_ms": 1, "error": None, "pending": False}
    app_mod.convos.finish_message(ms[3]["id"], "did it", None, None, tool_events=[ran])
    edit(cid, ms[4]["id"])
    check(dict(tape(cid))["user_message"]["had_writes"] is False, "a cut below the tool run reports no writes")
    edit(cid, msgs(cid)[2]["id"])
    um = dict(tape(cid))["user_message"]
    check(um["had_writes"] is True and um["edited_from"] == ms[2]["id"], f"a cut above it reports had_writes, got {um}")


def _summary(cid: str, upto: dict[str, Any]) -> None:
    with app_mod.db.tx() as c:
        c.execute("INSERT INTO conv_summaries(conversation_id,upto_message_id,upto_created,summary,summarized_messages,tokens_before,tokens_after,created_at,updated_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?)", (cid, upto["id"], upto["created_at"], "s", 3, 10, 5, 1.0, 1.0))


def test_summary_and_plan_are_invalidated() -> None:
    cid = three_turns()
    ms = msgs(cid)
    _summary(cid, ms[2])
    app_mod.work_plans.set(cid, [{"content": "do it", "status": "pending"}])
    edit(cid, ms[2]["id"])
    check(app_mod.compactor.get(cid) is None, "a summary covering the cut is cleared")
    check(app_mod.work_plans.get(cid) is None, "the working plan is cleared")
    check(("plan", {"conversation_id": cid, "steps": []}) in tape(cid), "a plan event announces it")


def test_early_summary_survives() -> None:
    cid = three_turns()
    ms = msgs(cid)
    _summary(cid, ms[1])
    edit(cid, ms[4]["id"])
    check(app_mod.compactor.get(cid) is not None, "a summary that ends before the cut is kept")


def test_pending_approval_on_a_hidden_row_is_denied() -> None:
    cid = three_turns()
    ms = msgs(cid)
    app_mod.run_store.open_approval("edit-call-1", None, "gmail_send", {"to": "x"}, conversation_id=cid, message_id=ms[3]["id"])
    edit(cid, ms[2]["id"])
    a = app_mod.run_store.approval("edit-call-1")
    check(a["status"] == "denied" and a["decided_by"] == "superseded", f"denied by superseded, got {a}")
    r = client.post("/approvals/edit-call-1", json={"decision": "allow"})
    check(r.status_code in (404, 409) or r.json().get("status") != "approved", f"it cannot be approved now, got {r.status_code} {r.text[:100]}")
    check(app_mod.run_store.approval("edit-call-1")["status"] == "denied", "still denied")


def test_refusals() -> None:
    cid = three_turns()
    ms = msgs(cid)
    j("POST", f"/conversations/{cid}/chat", {"content": "x", "replace_from": ms[1]["id"]}, expect=404)  # an assistant row
    j("POST", f"/conversations/{cid}/chat", {"content": "  ", "replace_from": ms[0]["id"]}, expect=400)
    j("POST", f"/conversations/{cid}/chat", {"content": "x", "replace_from": "nope"}, expect=404)
    check(len(msgs(cid)) == 6, "refusals cut nothing")
    desk = T.new_conv()
    app_mod.convos.update(desk, {"settings": {"deskId": "d1"}})
    du = app_mod.convos.add_message(desk, "user", "hi")
    j("POST", f"/conversations/{desk}/chat", {"content": "x", "replace_from": du["id"]}, expect=400)
    job = T.new_conv()
    app_mod.convos.update(job, {"settings": {"job_id": "j1"}})
    ju = app_mod.convos.add_message(job, "user", "hi")
    j("POST", f"/conversations/{job}/chat", {"content": "x", "replace_from": ju["id"]}, expect=400)


def test_conflict_while_answering_cuts_nothing() -> None:
    cid = three_turns()
    ms = msgs(cid)
    T.SCRIPT["chunks"] = [f"w{i} " for i in range(40)]
    T.SCRIPT["delay"] = 0.05
    j("POST", f"/conversations/{cid}/chat", {"content": "slow"})
    j("POST", f"/conversations/{cid}/chat", {"content": "x", "replace_from": ms[0]["id"]}, expect=409)
    j("POST", f"/conversations/{cid}/stop")
    drain(cid)
    check(ms[0]["id"] in [m["id"] for m in msgs(cid)], "the cut row is still live")
    T.SCRIPT["delay"] = 0.0


def test_delete_message_is_scoped_to_its_conversation() -> None:
    a, b = three_turns(), three_turns()
    foreign = msgs(b)[0]["id"]
    j("DELETE", f"/conversations/{a}/messages/{foreign}", expect=404)
    check(foreign in [m["id"] for m in msgs(b)], "the foreign row survives")
    j("DELETE", f"/conversations/{a}/messages/{msgs(a)[-1]['id']}")
