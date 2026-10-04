"""Regenerate keeps the answer it replaces: superseded rows, restore on failure, the variant switcher's routes.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_regenerate_variants.py
Built on the test_runs harness (TestClient + a scripted llm.stream_chat).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runs as T  # noqa: E402  (sets the data dir and scripts llm.stream_chat)

from personal_os import app as app_mod  # noqa: E402
from personal_os import backups, db as db_mod  # noqa: E402
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

OK_STREAM = T._scripted_stream


async def _raises(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    raise RuntimeError("provider exploded")
    yield  # pragma: no cover


async def _delta_then_raises(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
    yield {"type": "delta", "text": "partial words"}
    raise RuntimeError("cut off")


async def _blocks(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, cancel: asyncio.Event | None = None, **k: Any) -> Any:
    for _ in range(1500):
        if cancel is not None and cancel.is_set():
            return
        await asyncio.sleep(0.01)
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def use(fn: Any) -> None:
    llm.stream_chat = fn


def answered(text: str = "first answer") -> str:
    """A conversation with one finished exchange; returns its id."""
    T.SCRIPT["chunks"] = [text]
    T.SCRIPT["delay"] = 0.0
    use(OK_STREAM)
    cid = T.new_conv()
    j("POST", f"/conversations/{cid}/chat", {"content": "hello"})
    drain(cid)
    return cid


def msgs(cid: str) -> list[dict[str, Any]]:
    return j("GET", f"/conversations/{cid}")["messages"]


def regen(cid: str) -> list[tuple[str, Any]]:
    j("POST", f"/conversations/{cid}/chat", {})
    drain(cid)
    return events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])


def names(evs: list[tuple[str, Any]]) -> list[str]:
    return [e for e, _ in evs if e in ("removed_message", "assistant_message", "restored_message", "done")]


def test_provider_error_restores_the_original() -> None:
    cid = answered()
    before = msgs(cid)[-1]
    use(_raises)
    evs = regen(cid)
    check(names(evs) == ["removed_message", "assistant_message", "done", "removed_message", "restored_message"], f"tape order, got {names(evs)}")
    restored = dict(evs)["restored_message"]
    check(restored["message"]["id"] == before["id"] and restored["reason"], "restored_message carries the old row and the reason")
    after = msgs(cid)
    check(len(after) == 2 and after[-1] == before, "GET returns the original row byte for byte")
    use(OK_STREAM)


def test_stop_before_first_token_restores() -> None:
    cid = answered()
    before = msgs(cid)[-1]
    use(_blocks)
    j("POST", f"/conversations/{cid}/chat", {})
    T.wait_until(lambda: bool(T.run_info(cid).get("message_id")), "the replacement row")
    check(j("POST", f"/conversations/{cid}/stop")["ok"] is True, "stop reaches the run")
    drain(cid)
    after = msgs(cid)
    check(len(after) == 2 and after[-1] == before, "the original is back and there is no blank row")
    evs = events(read_streams([f"/conversations/{cid}/stream?since=0"])[0])
    check(names(evs)[-2:] == ["removed_message", "restored_message"], "the tape ends with removed then restored")
    use(OK_STREAM)


def test_setup_failure_restores() -> None:
    cid = answered()
    before = msgs(cid)[-1]
    real = app_mod.compaction.prepare_history

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("history assembly failed")

    app_mod.compaction.prepare_history = boom
    try:
        evs = regen(cid)
    finally:
        app_mod.compaction.prepare_history = real
    check(msgs(cid)[-1] == before, "the original is visible after a setup crash")
    check("restored_message" in names(evs), "restored_message was published")


def test_success_makes_variants_and_activate_swaps() -> None:
    cid = answered("one")
    old = msgs(cid)[-1]
    T.SCRIPT["chunks"] = ["two"]
    regen(cid)
    now = msgs(cid)
    check(len(now) == 2 and now[-1]["content"] == "two", "one answer per turn")
    check(now[-1]["variants"] == [old["id"], now[-1]["id"]], "variants lists both ids in creation order")
    hist = [m for m in app_mod.convos.history(cid) if m["role"] == "assistant"]
    check(len(hist) == 1 and hist[0]["content"] == "two", "history() has one assistant entry")
    swapped = j("POST", f"/conversations/{cid}/messages/{old['id']}/activate")
    check(swapped["messages"][-1]["id"] == old["id"] and swapped["messages"][-1]["variants"][0] == old["id"], "activate swaps the answer")
    seen: list[list[dict[str, Any]]] = []

    async def spy(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
        seen.append(messages)
        yield {"type": "delta", "text": "three"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    use(spy)
    j("POST", f"/conversations/{cid}/chat", {"content": "next"})
    drain(cid)
    use(OK_STREAM)
    check(any(m.get("content") == "one" for m in seen[0]) and not any(m.get("content") == "two" for m in seen[0]), "the next send replays the activated text")


def test_regenerate_tells_the_model_what_the_old_answer_changed() -> None:
    cid = answered("made it")
    old = msgs(cid)[-1]
    made = [{"id": "c1", "name": "doc_create", "arguments": {"title": "Weekly Meal Plan"}, "result_preview": "created doc d1"},
            {"id": "c2", "name": "doc_create", "arguments": {"title": "Failed"}, "error": "boom"}]
    with app_mod.db.tx() as c:
        c.execute("UPDATE messages SET tool_events=? WHERE id=?", (json.dumps(made), old["id"]))
    seen: list[list[dict[str, Any]]] = []

    async def spy(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], *a: Any, **k: Any) -> Any:
        seen.append(messages)
        yield {"type": "delta", "text": "again"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    use(spy)
    regen(cid)
    use(OK_STREAM)
    notes = [m["content"] for m in seen[0] if m["role"] == "system" and "Regenerating a reply" in str(m.get("content"))]
    check(len(notes) == 1 and "Weekly Meal Plan" in notes[0] and "Failed" not in notes[0], f"one note naming the finished write, got {notes}")


def test_activate_guards() -> None:
    cid = answered("one")
    old = msgs(cid)[-1]
    T.SCRIPT["chunks"] = ["two"]
    regen(cid)
    j("POST", "/conversations/nope/messages/x/activate", expect=404)
    j("POST", f"/conversations/{cid}/messages/{msgs(cid)[-1]['id']}/activate", expect=409)  # the active member is not superseded
    use(_blocks)
    j("POST", f"/conversations/{cid}/chat", {"content": "more"})
    T.wait_until(lambda: bool(T.run_info(cid).get("message_id")), "a live run")
    j("POST", f"/conversations/{cid}/messages/{old['id']}/activate", expect=409)  # answering
    j("POST", f"/conversations/{cid}/stop")
    drain(cid)
    use(OK_STREAM)
    j("POST", f"/conversations/{cid}/messages/{old['id']}/activate", expect=409)  # no longer the trailing group


def test_delta_then_error_keeps_the_new_row() -> None:
    cid = answered("one")
    use(_delta_then_raises)
    regen(cid)
    use(OK_STREAM)
    last = msgs(cid)[-1]
    check(last["content"] == "partial words" and last["error"], "the partial reply is kept with its error")
    check(len(last["variants"]) == 2, "and the old answer is one switch away")


def test_six_regenerates_keep_five() -> None:
    cid = answered("v0")
    for i in range(6):
        T.SCRIPT["chunks"] = [f"v{i + 1}"]
        regen(cid)
    last = msgs(cid)[-1]
    check(last["content"] == "v6", "the newest is active")
    check(len(last["variants"]) == 5 + 1, f"five superseded plus the active one, got {len(last['variants'])}")
    with app_mod.db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)).fetchone()["n"]
    check(n == 6, "older rows were pruned")


def test_regenerate_over_an_empty_row_carries_the_group() -> None:
    cid = answered("one")
    old = msgs(cid)[-1]
    T.SCRIPT["chunks"] = ["two"]
    regen(cid)
    blank = msgs(cid)[-1]
    # A replacement that was kept empty (restore could not run): the next regenerate drops it, not the group.
    with app_mod.db.tx() as c:
        c.execute("UPDATE messages SET content='', error='boom' WHERE id=?", (blank["id"],))
    T.SCRIPT["chunks"] = ["three"]
    evs = regen(cid)
    now = msgs(cid)
    check(now[-1]["content"] == "three" and now[-1]["variants"] == [old["id"], now[-1]["id"]], "no empty variant, the first answer still reachable")
    check(dict(evs)["assistant_message"].get("variants") == now[-1]["variants"], "the live event already carries the siblings")
    with app_mod.db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM messages WHERE id=?", (blank["id"],)).fetchone()["n"]
    check(n == 0, "the empty row is gone")


def test_prune_keeps_the_answer_just_superseded() -> None:
    cid = answered("v0")
    first = msgs(cid)[-1]["id"]
    for i in range(5):
        T.SCRIPT["chunks"] = [f"v{i + 1}"]
        regen(cid)
    j("POST", f"/conversations/{cid}/messages/{first}/activate")  # back to the oldest answer, then replace it
    use(_raises)
    regen(cid)
    use(OK_STREAM)
    check(msgs(cid)[-1]["id"] == first, "the failed replacement restored the answer that was on screen, not an older prune victim")


def test_human_export_one_answer_per_turn() -> None:
    cid = answered("exported-one")
    T.SCRIPT["chunks"] = ["exported-two"]
    regen(cid)
    dest = Path(tempfile.mkdtemp(prefix="rv-export-")) / "snap.db"
    with app_mod.db.tx() as c:
        c.execute("VACUUM INTO ?", (str(dest),))
    md = backups.human_export(dest)
    text = "\n".join(v[0] for v in md.values())
    check("exported-two" in text and "exported-one" not in text, "the export reads the active answer only")


def test_delete_message_removes_the_group() -> None:
    cid = answered("one")
    T.SCRIPT["chunks"] = ["two"]
    regen(cid)
    mid = msgs(cid)[-1]["id"]
    check(app_mod.convos.delete_message(mid, "someone-else") is False, "a foreign conversation is refused")
    check(app_mod.convos.delete_message(mid, cid) is True, "the scoped delete succeeds")
    with app_mod.db.tx() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)).fetchone()["n"]
    check(n == 0, "no invisible variants are left behind")


def test_pre_migration_db_gains_the_columns() -> None:
    d = Path(tempfile.mkdtemp(prefix="rv-migrate-"))
    old = db_mod.Database(d)
    with old.tx() as c:
        c.execute("ALTER TABLE messages DROP COLUMN superseded_at")
        c.execute("ALTER TABLE messages DROP COLUMN variant_of")
    fresh = db_mod.Database(d)
    with fresh.tx() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)")}
    check({"superseded_at", "variant_of"} <= cols, "the migration adds both columns")
    sqlite3.connect(":memory:").close()
