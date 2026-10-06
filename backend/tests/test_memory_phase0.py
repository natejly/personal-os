"""Memory phase 0: a tainted chat learns from the user's words only, the tidy-up counter survives a relaunch,
memories_fts holds live rows only, and old auto memories get a source only when it is unambiguous.

Run: pytest backend/tests/test_memory_phase0.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import learn, memory_limits, migrations  # noqa: E402
from personal_os.consolidate import Consolidator  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.repos import Conversations, Graph, Memories, Projects  # noqa: E402
from personal_os.todos import Todos  # noqa: E402
from personal_os.trash import Trash  # noqa: E402


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(tmp)


REPLY = {"memories": [{"content": "User prefers short emails", "kind": "preference"}],
         "entities": [{"label": "Priya", "type": "person"}, {"label": "Acme", "type": "organization"}],
         "relations": [{"source": "Priya", "target": "Acme", "relation": "works at"}]}


def _extract(db: Database, monkeypatch: Any, reply: dict[str, Any] = REPLY, **kw: Any) -> tuple[dict[str, Any], list[Any]]:
    seen: list[Any] = []

    async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **k: Any) -> str:
        seen.append(messages)
        return json.dumps(reply)

    monkeypatch.setattr(learn.llm, "complete", fake_complete)
    out = asyncio.run(learn.learn_from_exchange(
        settings={}, memories=Memories(db), graph=Graph(db), project_id=None, user_text="please keep emails short",
        assistant_text="SECRET-ASSISTANT-TEXT", model="m", conversation_id="c1", message_id="a1",
        tool_events=[{"name": "fetch_url", "arguments": {"url": "https://x.test"}, "result": "TOOL-RESULT"}],
        skills_in_use=[{"id": "s", "name": "PROCEDURE-NAME", "description": "d"}], **kw))
    return out, seen


# ---- 1. user-only extraction ----
def test_user_only_withholds_the_reply_and_cites_the_user_message(db, monkeypatch) -> None:
    out, seen = _extract(db, monkeypatch, user_only=True, user_message_id="u1")
    prompt = seen[0][1]["content"]
    assert "please keep emails short" in prompt
    assert "Only the user's message is available; the assistant's reply is withheld." in prompt
    for gone in ("Assistant replied", "SECRET-ASSISTANT-TEXT", "Tools the assistant called", "TOOL-RESULT", "Procedures in use", "PROCEDURE-NAME"):
        assert gone not in prompt
    assert out["memories"][0]["source_message_id"] == "u1"
    assert out["memories"][0]["source_conversation_id"] == "c1"
    assert [e["source_message_id"] for e in out["edges"]] == ["u1"]


# ---- 2. the ordinary path is unchanged ----
def test_normal_exchange_still_shows_the_reply(db, monkeypatch) -> None:
    out, seen = _extract(db, monkeypatch, user_message_id="u1")
    prompt = seen[0][1]["content"]
    assert "Assistant replied" in prompt and "SECRET-ASSISTANT-TEXT" in prompt and "withheld" not in prompt
    assert out["memories"][0]["source_message_id"] == "u1"
    assert [e["source_message_id"] for e in out["edges"]] == ["u1"]


def test_without_a_user_message_id_the_reply_is_cited(db, monkeypatch) -> None:
    out, _ = _extract(db, monkeypatch)
    assert out["memories"][0]["source_message_id"] == "a1"
    assert [e["source_message_id"] for e in out["edges"]] == ["a1"]


# ---- 3. a user-only job drafts no skill ----
def test_user_only_job_never_drafts_a_skill(db, monkeypatch) -> None:
    reply = {**REPLY, "friction": {"what": "had to re-explain the weekly report", "fix": "procedure", "task": "weekly report"}}

    async def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("needs the assistant half")

    async def fake_complete(settings: Any, model: str, messages: Any, kind: str = "learn", **k: Any) -> str:
        return json.dumps(reply)

    monkeypatch.setattr(learn.llm, "complete", fake_complete)
    published: list[tuple[str, Any]] = []
    worker = learn.LearnWorker(memories=Memories(db), graph=Graph(db), set_trace=lambda *_: None,
                               publish=lambda e, d: published.append((e, d)))
    monkeypatch.setattr(worker, "_suggest_skill", boom)
    monkeypatch.setattr(worker, "_revise_skills", boom)
    job = learn.LearnJob(conversation_id="c1", message_id="a1", project_id=None, user_text="keep emails short",
                         assistant_text="", model="m", settings={}, spans=[], user_only=True, user_message_id="u1")
    asyncio.run(worker._run(job))
    assert [e for e, _ in published] == ["learned"]
    payload = published[0][1]
    assert payload["user_message_id"] == "u1" and payload["message_id"] == "a1"
    assert payload["skill_candidates"] == [] and payload["skill_revisions"] == []
    assert payload["friction"]["fix"] == "procedure"  # still reported, just never drafted into a skill


# ---- 4. the tidy-up counter is derived, so a relaunch keeps it ----
class _Tidy:
    def __init__(self) -> None:
        self.calls = 0

    async def propose(self, settings: Any, project_id: Any, model: str) -> list[Any]:
        self.calls += 1
        return []


def _tidy_job() -> learn.LearnJob:
    return learn.LearnJob(conversation_id="c1", message_id="a1", project_id=None, user_text="", assistant_text="",
                          model="m", settings={"consolidateEvery": 3}, spans=[])


def test_tidy_counter_survives_a_relaunch(db) -> None:
    tidy, mem = _Tidy(), Memories(db)

    def consolidate_on_new_worker() -> None:  # a fresh worker each time: the app was relaunched
        w = learn.LearnWorker(memories=mem, graph=Graph(db), set_trace=lambda *_: None, publish=lambda *_: None, consolidator=tidy)
        asyncio.run(w._maybe_consolidate(_tidy_job(), 1))

    for i in range(2):
        mem.create(None, f"User fact number {i}", source="auto")
        consolidate_on_new_worker()
    assert tidy.calls == 0
    mem.create(None, "User fact number 2", source="auto")
    consolidate_on_new_worker()
    assert tidy.calls == 1
    with db.tx() as c:
        assert c.execute("SELECT value FROM settings WHERE key=?", (memory_limits.TIDY_AT_KEY,)).fetchone() is not None
    time.sleep(0.01)  # the next memories must be strictly newer than the stamp
    for i in range(3, 5):
        mem.create(None, f"User fact number {i}", source="auto")
        consolidate_on_new_worker()
    assert tidy.calls == 1
    mem.create(None, "User fact number 5", source="auto")
    consolidate_on_new_worker()
    assert tidy.calls == 2


# ---- 5. memories_fts holds live rows only ----
def _fts(db: Database) -> list[str]:
    with db.tx() as c:
        return sorted(r["memory_id"] for r in c.execute("SELECT memory_id FROM memories_fts"))


def test_sync_memories_fts_and_its_migration(db) -> None:
    mem = Memories(db)
    live, missing, trashed, invalid = (mem.create(None, f"User {w} fact") for w in ("live", "missing", "trashed", "invalid"))
    with db.tx() as c:  # the stale shapes old code left behind
        c.execute("UPDATE memories SET deleted_at=1 WHERE id=?", (trashed["id"],))
        c.execute("UPDATE memories SET invalid_at=1 WHERE id=?", (invalid["id"],))
        c.execute("DELETE FROM memories_fts WHERE memory_id=?", (missing["id"],))
        c.execute("INSERT INTO memories_fts(content, memory_id) VALUES('ghost', 'nope')")
    assert "nope" in _fts(db) and trashed["id"] in _fts(db) and invalid["id"] in _fts(db)
    with db.tx() as c:
        migrations._memories_fts_live(c)
    assert _fts(db) == sorted([live["id"], missing["id"]])
    with db.tx() as c:
        migrations.sync_memories_fts(c)
        migrations.sync_memories_fts(c, [live["id"], trashed["id"]])
        migrations.sync_memories_fts(c, [])
    assert _fts(db) == sorted([live["id"], missing["id"]])  # idempotent
    assert [m["id"] for m in mem.list(None, q="missing")] == [missing["id"]]


# ---- 6. trash and restore keep the index in step ----
def test_trash_and_restore_a_memory(db) -> None:
    mem = Memories(db)
    trash = Trash(db, Todos(db), Docs(db))
    m = mem.create(None, "User drinks oolong")
    assert trash.trash("memory", m["id"])
    assert _fts(db) == []
    assert trash.restore("memory", m["id"])
    assert _fts(db) == [m["id"]]


def test_trash_and_restore_a_project_with_memories(db) -> None:
    mem, projects = Memories(db), Projects(db)
    trash = Trash(db, Todos(db), Docs(db))
    p = projects.create("P")
    m = mem.create(p["id"], "Project uses oolong")
    other = mem.create(None, "Personal fact stays")
    assert trash.trash("project", p["id"])
    assert _fts(db) == [other["id"]]
    assert trash.restore("project", p["id"])
    assert _fts(db) == sorted([m["id"], other["id"]])


def test_editing_a_trashed_memory_does_not_make_it_searchable(db) -> None:
    mem = Memories(db)
    trash = Trash(db, Todos(db), Docs(db))
    m = mem.create(None, "User drinks oolong")
    trash.trash("memory", m["id"])
    mem.update(m["id"], {"content": "User drinks sencha"})
    assert _fts(db) == []
    trash.restore("memory", m["id"])
    assert _fts(db) == [m["id"]]


# ---- 4b. consolidation keeps the newest source ----
def test_merge_carries_the_newest_provenance(db) -> None:
    mem = Memories(db)
    old = mem.create(None, "User lives in Austin", source="auto", provenance={"conversation_id": "c-old", "message_id": "u-old"})
    new = mem.create(None, "User lives in Austin Texas", source="auto", provenance={"conversation_id": "c-new", "message_id": "u-new"})
    bare = mem.create(None, "User is based in Austin", source="auto")
    with db.tx() as c:
        c.execute("UPDATE memories SET created_at=100 WHERE id=?", (old["id"],))
        c.execute("UPDATE memories SET created_at=200 WHERE id=?", (new["id"],))
        c.execute("UPDATE memories SET created_at=300 WHERE id=?", (bare["id"],))  # newest, but no source: skipped
    ids = [old["id"], new["id"], bare["id"]]
    pl = {"ids": ids, "text": "User lives in Austin, Texas", "snapshot": {i: mem.get(i)["content"] for i in ids}}
    assert Consolidator(db, mem, Graph(db))._apply_memories("merge", pl)
    merged = [m for m in mem.list(None) if m["content"] == "User lives in Austin, Texas"][0]
    assert (merged["source_conversation_id"], merged["source_message_id"]) == ("c-new", "u-new")
    assert merged["id"] in _fts(db) and not set(ids) & set(_fts(db))


def test_merge_without_any_source_stays_unsourced(db) -> None:
    mem = Memories(db)
    a, b = mem.create(None, "User likes tea", source="auto"), mem.create(None, "User likes green tea", source="auto")
    pl = {"ids": [a["id"], b["id"]], "text": "User likes green tea", "snapshot": {a["id"]: a["content"], b["id"]: b["content"]}}
    assert Consolidator(db, mem, Graph(db))._apply_memories("merge", pl)
    merged = mem.list(None)[0]
    assert merged["source_conversation_id"] is None and merged["source_message_id"] is None


# ---- 7. conservative provenance backfill (migration) ----
T = 1_000_000.0


class _Chat:
    """One conversation whose rows are pinned to exact times; span ends are epoch milliseconds."""

    def __init__(self, db: Database) -> None:
        self.db, self.convos = db, Conversations(db)

    def conv(self) -> str:
        return self.convos.create(None, "t", "m")["id"]

    def say(self, conv: str, role: str, created: float, ends_at: float | None = None, trace: list[dict[str, Any]] | None = None) -> str:
        mid = self.convos.add_message(conv, role, "x")["id"]
        spans = trace if trace is not None else ([{"kind": "llm", "start": 0, "end": ends_at * 1000}] if ends_at else None)
        with self.db.tx() as c:
            c.execute("UPDATE messages SET created_at=?, trace=? WHERE id=?", (created, json.dumps(spans) if spans else None, mid))
        return mid


def _memory(db: Database, source: str = "auto", prov: dict[str, Any] | None = None, text: str = "User prefers short emails") -> str:
    m = Memories(db).create(None, text, source=source, provenance=prov)
    with db.tx() as c:
        c.execute("UPDATE memories SET created_at=? WHERE id=?", (T, m["id"]))
    return m["id"]


def _backfill(db: Database, mid: str) -> tuple[Any, Any]:
    with db.tx() as c:
        migrations._memory_provenance_backfill(c)
        r = c.execute("SELECT source_conversation_id, source_message_id FROM memories WHERE id=?", (mid,)).fetchone()
    return r[0], r[1]


def test_backfill_links_the_one_reply_that_finished_just_before(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    chat.say(conv, "user", T - 500)
    u2 = chat.say(conv, "user", T - 100)
    # The reply row was created at T-90 but finished at T-30; its own auto-learn span ends after the memory.
    chat.say(conv, "assistant", T - 90, trace=[{"kind": "llm", "end": (T - 30) * 1000}, {"kind": "learn", "end": (T + 5) * 1000}])
    chat.say(conv, "assistant", T - 5000, ends_at=T - 4990)  # an old reply, far outside the window
    mid = _memory(db)
    assert _backfill(db, mid) == (conv, u2)


def test_backfill_falls_back_to_the_row_time_without_a_trace(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    u = chat.say(conv, "user", T - 60)
    chat.say(conv, "assistant", T - 40)
    mid = _memory(db)
    assert _backfill(db, mid) == (conv, u)


def test_backfill_leaves_two_candidates_alone(db) -> None:
    chat = _Chat(db)
    for _ in range(2):
        conv = chat.conv()
        chat.say(conv, "user", T - 100)
        chat.say(conv, "assistant", T - 90, ends_at=T - 30)
    mid = _memory(db)
    assert _backfill(db, mid) == (None, None)


def test_backfill_leaves_a_memory_with_no_candidate_alone(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    chat.say(conv, "user", T - 1000)
    chat.say(conv, "assistant", T - 990, ends_at=T - 900)  # finished more than the window before
    chat.say(conv, "assistant", T - 10, ends_at=T + 20)  # finished after the memory was written
    mid = _memory(db)
    assert _backfill(db, mid) == (None, None)


def test_backfill_needs_a_user_message_to_cite(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    chat.say(conv, "assistant", T - 40, ends_at=T - 30)
    mid = _memory(db)
    assert _backfill(db, mid) == (None, None)


def test_backfill_skips_sourced_and_user_memories(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    chat.say(conv, "user", T - 100)
    chat.say(conv, "assistant", T - 90, ends_at=T - 30)
    sourced = _memory(db, prov={"conversation_id": "other", "message_id": "m9"})
    mine = _memory(db, source="user", text="User prefers long emails")
    assert _backfill(db, sourced) == ("other", "m9")
    assert _backfill(db, mine) == (None, None)


def test_backfill_skips_a_row_that_replaced_another(db) -> None:
    chat = _Chat(db)
    conv = chat.conv()
    chat.say(conv, "user", T - 100)
    chat.say(conv, "assistant", T - 90, ends_at=T - 30)
    old = _memory(db, text="User likes tea")
    merged = _memory(db, text="User likes green tea")
    with db.tx() as c:
        c.execute("UPDATE memories SET superseded_by=? WHERE id=?", (merged, old))
    assert _backfill(db, merged) == (None, None)


def test_memory_migrations_are_registered() -> None:
    assert [n for _, n, _ in migrations.MIGRATIONS[-2:]] == ["memories_fts_live", "memory_provenance_backfill"]
