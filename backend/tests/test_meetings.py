"""Meetings: the repo, the FTS index, the enhance proposal, retention and the 45s tick's predicates.

The capture threads need a real recording session, so they are not exercised here; they have
test_meeting_recorder.py, which drives real ffmpeg from a sine generator. Everything between
"a segment arrived" and "what the chat sees" is. Nothing below starts a process, opens a socket
or asks for a microphone: the LLM is a stub, `audiocap`'s device cache is pinned, and the one
test that needs a live recording hands the pool a fake session.

Runs under pytest, or directly: python backend/tests/test_meetings.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import activity, audiocap, meeting_notes, meetings  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Projects  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}

# What the enhance pass asks for: one JSON object, nothing else (meeting_notes.ENHANCE_PROMPT).
GOOD_REPLY = json.dumps({
    "enhanced_markdown": "# Pricing call\n\n## Decisions\n- Ship the tiers on the fourteenth\n",
    "decisions": ["Ship the tiers on the fourteenth"],
    "action_items": [{"text": "send the deck", "owner": "ada@example.com", "due": "2026-10-02"}],
    "topics": ["pricing"],
    "headline": "Agreed to ship the tiers on the fourteenth",
})

NOTES = "## Agenda\n- pricing tiers\n- launch date"


class devices_are:
    """Pin what ffmpeg reports as audio inputs.

    `audiocap.audio_devices()` is a 15s-timeout subprocess on a cold cache (audiocap.py:43-66),
    and both `capabilities()` and `status()` read it, so a test that let it through would be
    spawning ffmpeg to assert on whatever hardware happens to be plugged in. The cache is module
    state behind a 20s TTL, so filling it is enough.
    """

    def __init__(self, *names: str):
        self.devices = [{"index": str(i), "name": n} for i, n in enumerate(names)]

    def __enter__(self) -> devices_are:
        self.real = audiocap._dev_cache
        audiocap._dev_cache = (time.time(), self.devices)
        return self

    def __exit__(self, *exc: object) -> None:
        audiocap._dev_cache = self.real


class _Todos:
    """Enough of `Todos` for promote_action_item: one create, recorded.

    `promote_action_item(item_id, todos, ...)` takes the singleton as an argument rather than
    holding one, so the repo never reaches Google Tasks itself - `todos.on_change` is already
    wired to `tasks_sync.poke` at app.py:218-220 and pushes within ~2s.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kw: object) -> dict:
        self.calls.append(kw)
        return {"id": f"todo{len(self.calls)}"}


class _FakeSession:
    """What `RecorderPool._live()` looks for: a meeting id, a clock, not stopping, alive.

    Stands in for a RecordingSession so the loop's predicates can be exercised against a
    meeting that claims to be recording without any ffmpeg.
    """

    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        self.started_at = time.time()
        self.stopping = False
        self.alive = True


def _svc(tmp: Path, reply: str = "") -> tuple[meetings.Meetings, meetings.MeetingService]:
    """A repo and a service over a throwaway db, with a stub LLM and nothing recording."""
    calls: list[dict] = []

    async def fake_complete(settings, model, messages, kind="learn"):
        calls.append({"model": model, "messages": messages, "kind": kind})
        if reply == "__raise__":
            raise RuntimeError("proxy down")
        return reply

    db = Database(tmp)
    repo = meetings.Meetings(db)
    svc = meetings.MeetingService(db, lambda: dict(SETTINGS), fake_complete, repo)
    svc.llm_calls = calls  # type: ignore[attr-defined]
    return repo, svc


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


# ---------------------------------------------------------------- schema and the row


def test_schema_applies_twice_without_error() -> None:
    db = Database(_tmp())
    first = meetings.Meetings(db)
    m = first.create(title="Standup")
    # The second construction is what every app restart does, and what a second Meetings()
    # in one process would do: CREATE TABLE IF NOT EXISTS plus the PRAGMA-guarded ALTERs.
    second = meetings.Meetings(db)
    assert second.get(m["id"])["title"] == "Standup"
    with db.tx() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(meetings)").fetchall()}
    assert set(meetings.ADDED_COLUMNS) <= have             # the ALTER seam ran, and only once
    # Nothing here expires, which is the point: POST /activity/purge is a bare DELETE.
    assert "expires_at" not in have


def test_create_get_patch_round_trip_and_the_patch_whitelist() -> None:
    repo, _ = _svc(_tmp())
    m = repo.create(title="  Pricing call  ", template="sales_call")
    assert m["title"] == "Pricing call"              # trimmed on the way in
    assert m["template"] == "sales_call"
    assert m["status"] == "notes_only"
    assert m["notes"] == "" and m["enhanced"] == "" and m["transcript"] == ""
    assert m["words"] == 0 and m["has_pending"] is False and m["actions"] == []

    out = repo.patch(m["id"], {
        "notes": NOTES,
        "transcript": "injected by a PATCH body",      # not in PATCH_FIELDS
        "started_at": 1.0,                             # the recorder owns this
        "nonsense": True,
    })
    assert out["notes"] == NOTES
    assert out["transcript"] == ""                     # mark_started/finalize own the capture columns
    assert out["started_at"] is None
    assert "nonsense" not in out
    assert out["updated_at"] > m["updated_at"]         # every patch stamps it
    assert out["created_at"] == m["created_at"]
    assert repo.list()[0]["notes_preview"] == NOTES


def test_project_id_conventions_match_docs_and_todos() -> None:
    repo, _ = _svc(_tmp())
    pid = Projects(repo.db).create("Acme")["id"]
    mine = repo.create(title="Personal one-on-one")
    theirs = repo.create(title="Acme kickoff", project_id=pid)

    assert {m["id"] for m in repo.list("__all__")} == {mine["id"], theirs["id"]}
    assert [m["id"] for m in repo.list(None)] == [mine["id"]]          # None means "personal"
    assert [m["id"] for m in repo.list(pid)] == [theirs["id"]]


# ---------------------------------------------------------------- segments


def test_segments_interleave_by_offset_not_by_insert_order() -> None:
    repo, _ = _svc(_tmp())
    m = repo.create(title="Pricing call")
    mid = m["id"]
    # Deliberately out of order: the transcribe worker settles whichever segment returns first,
    # and the two channels race each other.
    for channel, seq, t_start, text in (("mic", 2, 40.0, "bye"),
                                        ("mic", 0, 0.0, "hello"),
                                        ("output", 1, 20.0, "hi there")):
        seg = repo.add_segment(mid, channel, seq, t_start, t_start + 20.0, 1_700_000_000.0 + t_start,
                               f"/nowhere/{channel}-{seq}.wav", 4096)
        repo.finish_segment(seg["id"], text=text, backend="proxy")

    assert repo.build_transcript(mid) == "00:00 [you] hello\n00:20 [them] hi there\n00:40 [you] bye"
    assert [s["seq"] for s in repo.segments(mid)] == [0, 1, 2]


def test_segment_started_at_is_the_recording_clock_not_the_insert_time() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    recorded_at = 1_700_000_000.0            # long before this test ran
    seg = repo.add_segment(mid, "mic", 0, 20.0, 40.0, recorded_at, "/nowhere/0.wav", 4096)
    # activity.py:866-870 calls store.add with no ts=, so Store.add defaults to now() and
    # ts + duration_ms points into the future. The whole segment table exists to not do that.
    assert seg["started_at"] == recorded_at
    assert seg["t_start"] == 20.0 and seg["t_end"] == 40.0
    assert seg["created_at"] > recorded_at
    assert repo.segment(mid, "mic", 0)["started_at"] == recorded_at


def test_a_rewritten_segment_upserts_and_drops_the_stale_transcription() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    first = repo.add_segment(mid, "mic", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(first["id"], text="the first take", backend="proxy")
    again = repo.add_segment(mid, "mic", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 8192)
    assert again["id"] == first["id"]
    assert again["text"] == "" and again["state"] == "recorded"      # the wav was rewritten
    assert len(repo.segments(mid)) == 1


# ---------------------------------------------------------------- FTS


def test_fts_finds_notes_then_the_transcript_and_survives_a_malformed_query() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    hits = repo.search("pricing")
    assert [h["meeting_id"] for h in hits] == [mid]
    assert hits[0]["field"] == "notes" and hits[0]["snippet"]

    assert repo.search("fourteenth") == []                  # not said anywhere yet
    seg = repo.add_segment(mid, "output", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(seg["id"], text="agreed, the fourteenth works", backend="proxy")
    repo.finalize(mid, repo.build_transcript(mid))          # the only path that reindexes a transcript
    hit = repo.search("fourteenth")[0]
    assert hit["meeting_id"] == mid and hit["field"] == "transcript"
    assert "fourteenth" in hit["snippet"]

    # A query fts5 cannot parse must come back as a LIKE scan, not a 500. `(((` yields no terms
    # at all, so repos.fts_query falls back to quoting it, which fts5 accepts as a zero-token
    # phrase; a lone double quote is the input that actually breaks the MATCH parse.
    assert repo.search("(((") == []
    repo.patch(mid, {"notes": NOTES + '\n- the "launch" date'})
    assert [h["meeting_id"] for h in repo.search('"')] == [mid]


def test_deleting_a_meeting_leaves_no_ghost_fts_row() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Quarterly offsite")["id"]
    repo.patch(mid, {"notes": "we discussed bioluminescence"})
    assert repo.search("bioluminescence")

    repo.delete(mid)
    assert repo.get(mid) is None
    assert repo.search("bioluminescence") == []
    # fts5 virtual tables are not reachable by ON DELETE CASCADE (docs.py:236-239), so the row
    # only goes away because delete() removes it by hand.
    with repo.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM meetings_fts WHERE meeting_id=?", (mid,)).fetchone()[0] == 0
    repo.delete(mid)                                        # idempotent


# ---------------------------------------------------------------- the enhance proposal


def test_enhance_proposes_and_never_writes_the_notes_column() -> None:
    repo, svc = _svc(_tmp(), reply=GOOD_REPLY)
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    before = repo.get(mid)["notes"]

    rev = asyncio.run(svc.enhance(mid))
    assert rev["status"] in ("pending", "applied")
    assert rev["degraded"] is False
    assert "Ship the tiers" in rev["after"]
    assert rev["decisions"] == ["Ship the tiers on the fourteenth"]
    # The whole two-column split exists for this assertion.
    assert repo.get(mid)["notes"] == before
    assert svc.llm_calls[0]["kind"] == "meeting"
    assert len(repo.action_items(mid)) == 1


def test_auto_apply_stops_once_the_user_has_hand_edited_the_enhanced_notes() -> None:
    repo, svc = _svc(_tmp(), reply=GOOD_REPLY)
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})

    first = asyncio.run(svc.enhance(mid))
    assert first["status"] == "applied"                     # enhanced was empty, so it just appears
    assert "Ship the tiers" in repo.get(mid)["enhanced"]

    repo.patch(mid, {"enhanced": "mine"})
    second = asyncio.run(svc.enhance(mid, force=True))
    assert second["status"] == "pending"                    # now it waits to be accepted
    assert repo.get(mid)["enhanced"] == "mine"
    assert repo.get(mid)["has_pending"] is True


def test_a_dead_model_still_leaves_the_user_their_notes() -> None:
    repo, svc = _svc(_tmp(), reply="__raise__")
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    seg = repo.add_segment(mid, "mic", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(seg["id"], text="agreed, the fourteenth works", backend="proxy")
    repo.finalize(mid, repo.build_transcript(mid))

    rev = asyncio.run(svc.enhance(mid))
    assert rev["degraded"] is True
    assert NOTES in rev["after"]                            # verbatim, not reconstructed
    assert "agreed, the fourteenth works" in rev["after"]
    assert repo.get(mid)["notes"] == NOTES
    assert "RuntimeError: proxy down" in repo.get(mid)["error"]
    # Unlike rollup_once, which marks its events rolled_up even when the LLM dies
    # (activity.py:1201-1206), a degraded pass consumes nothing and stays re-runnable.
    assert [(s["state"], s["text"]) for s in repo.segments(mid)] == [("done", "agreed, the fourteenth works")]


def test_enhance_is_cached_and_force_pays_again() -> None:
    repo, svc = _svc(_tmp(), reply=GOOD_REPLY)
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})

    first = asyncio.run(svc.enhance(mid))
    again = asyncio.run(svc.enhance(mid))
    assert len(svc.llm_calls) == 1                          # cache-or-generate, like /recap
    assert again["id"] == first["id"]
    assert asyncio.run(svc.enhance(mid, force=True))["id"] != first["id"]
    assert len(svc.llm_calls) == 2


def test_accept_applies_to_enhanced_only_and_supersedes_the_rest() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES})
    old = repo.propose(mid, "# an earlier pass")
    new = repo.propose(mid, "# Pricing call\n\n- tiers ship on the fourteenth")

    out = repo.accept(new["id"])
    assert out["enhanced"] == "# Pricing call\n\n- tiers ship on the fourteenth"
    assert out["notes"] == NOTES                            # untouched, as ever
    assert out["has_pending"] is False
    applied = repo.revision(new["id"])
    assert applied["status"] == "applied" and applied["resolved_at"]
    assert repo.revision(old["id"])["status"] == "superseded"
    assert repo.last_applied(mid)["id"] == new["id"]
    # accept() reindexes inside its own transaction, so the accepted text is searchable at once.
    assert repo.search("fourteenth")[0]["field"] == "enhanced"
    assert repo.accept(new["id"]) is None                   # pending rows only

    spare = repo.propose(mid, "# a pass nobody wanted")
    after = repo.reject(spare["id"])
    assert after["notes"] == NOTES
    assert after["enhanced"] == "# Pricing call\n\n- tiers ship on the fourteenth"
    assert repo.revision(spare["id"])["status"] == "rejected"


def test_a_revision_satisfies_the_doc_revision_interface() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    rev = repo.propose(mid, "# enhanced", summary="Agreed to ship")
    # <DiffView> is typed `revision: DocRevision` (DiffView.tsx:101), so the row has to carry
    # DocRevision's own field names or the reuse is a lie.
    assert {"id", "doc_id", "before", "after", "title_before", "title_after", "summary",
            "author", "tool", "status", "created_at", "resolved_at", "stat"} <= set(rev)
    assert rev["doc_id"] == mid
    assert rev["author"] == "assistant" and rev["tool"] == "meeting_enhance"
    assert rev["title_before"] == "Pricing call" and rev["title_after"] is None
    assert set(rev["stat"]) == {"added", "removed"}
    assert repo.revisions(mid)[0]["id"] == rev["id"]


# ---------------------------------------------------------------- redaction


def test_a_transcript_keeps_the_people_and_loses_the_credentials() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    seg = repo.add_segment(mid, "output", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    stored = repo.finish_segment(
        seg["id"], text="ada@example.com will call +1 415 555 0134 about sk-aaaaaaaaaaaaaaaaaaaaaa",
        backend="proxy")["text"]
    # Credential rules only. activity.Gate.scrub's identity rules replace every address with
    # [email] (activity.py:126) and every phone-shaped digit run with [phone] (activity.py:132),
    # which would erase who was on the call from inside the record of the call.
    assert "ada@example.com" in stored
    assert "+1 415 555 0134" in stored
    assert "sk-aaaaaaaaaaaaaaaaaaaaaa" not in stored
    assert "[secret]" in stored


# ---------------------------------------------------------------- action items


def test_an_action_item_becomes_a_todo_exactly_once() -> None:
    repo, _ = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    rev = repo.propose(mid, "# enhanced")
    items = repo.add_action_items(mid, rev["id"], [
        {"text": "send the deck", "owner": "ada@example.com", "due": "2026-10-02"},
        {"text": "   "},                                    # blank text is not an action item
    ])
    assert [i["text"] for i in items] == ["send the deck"]
    assert items[0]["status"] == "proposed" and items[0]["todo_id"] is None

    todos = _Todos()
    added = repo.promote_action_item(items[0]["id"], todos)
    assert added["status"] == "added" and added["todo_id"] == "todo1"
    assert todos.calls[0]["source"] == "meeting"
    assert todos.calls[0]["title"] == "send the deck"
    assert todos.calls[0]["external_id"] == mid
    assert todos.calls[0]["due"] == "2026-10-02"
    repo.promote_action_item(items[0]["id"], todos)
    assert len(todos.calls) == 1                            # idempotent: one item, one todo

    second = repo.add_action_items(mid, rev["id"], [{"text": "book the room"}])[-1]
    assert repo.dismiss_action_item(second["id"])["status"] == "dismissed"


# ---------------------------------------------------------------- what chat sees


def test_context_block_is_opt_out_and_bounded() -> None:
    repo, svc = _svc(_tmp())
    mid = repo.create(title="Pricing call")["id"]
    repo.patch(mid, {"notes": NOTES, "summary": "Agreed to ship the tiers on the fourteenth"})

    svc.set_config({"injectContext": False})
    assert svc.context_block() == ""

    svc.set_config({"injectContext": True})
    block = svc.context_block()
    assert "## Recent meetings" in block
    assert "Agreed to ship the tiers on the fourteenth" in block
    assert len(svc.context_block(max_chars=40)) == 40
    # Accepted notes and headlines only: a transcript is other people's speech.
    repo.finalize(mid, "00:00 [them] my salary is confidential")
    assert "confidential" not in svc.context_block()


# ---------------------------------------------------------------- capabilities and config


def test_capabilities_explain_themselves_and_ask_for_no_grants() -> None:
    _, svc = _svc(_tmp())
    with devices_are("MacBook Pro Microphone"):
        caps = svc.capabilities()
    ids = [c["id"] for c in caps]
    assert {"platform", "ffmpeg", "mic", "loopback", "stt", "stt_local"} <= set(ids)
    # A recorder needs neither grant: ffmpeg talks to avfoundation directly and nothing here
    # reads a window title, so activity's two macOS-permission rows are not copied over.
    assert "pyobjc" not in ids and "accessibility" not in ids
    for c in caps:
        assert isinstance(c["ok"], bool)
        assert c["fix"] or c["ok"]                          # anything not ok says how to fix it


def test_set_config_deep_merges_and_drops_an_unknown_source() -> None:
    _, svc = _svc(_tmp())
    svc.set_config({"nudgeSeconds": 30})
    cfg = svc.set_config({"sources": ["mic", "output", "telepathy"], "sttBackend": "nonsense"})
    assert cfg["sources"] == ["mic", "output"]
    assert cfg["sttBackend"] == "auto"                      # not in stt.BACKENDS
    assert cfg["nudgeSeconds"] == 30                        # the earlier patch survived
    assert cfg["enabled"] is False and cfg["segmentSeconds"] == 20
    assert set(cfg) == set(meetings.DEFAULT_CONFIG)
    assert svc.config()["nudgeSeconds"] == 30               # and it persisted

    assert svc.set_config({"sources": []})["sources"] == ["mic"]
    assert svc.set_config({"template": "nonsense"})["template"] == "general"
    assert svc.set_config({"template": "standup"})["template"] in meeting_notes.TEMPLATES


def test_consent_is_the_only_thing_that_unblocks_the_consent_blocker() -> None:
    _, svc = _svc(_tmp())
    assert svc.config()["consentedAt"] == 0.0               # off by default; recording is blocked
    assert svc.consent()["consentedAt"] > 0
    with devices_are("MacBook Pro Microphone"):
        st = svc.status()                                  # status() reads the device cache too
    assert st["consented"] is True
    assert st["active"] is None and st["enabled"] is False
    assert st["devices"] == [{"index": "0", "name": "MacBook Pro Microphone", "loopback": False}]
    assert st["counts"] == {"total": 0, "pending": 0}


# ---------------------------------------------------------------- retention


def test_purging_activity_cannot_reach_a_meeting() -> None:
    repo, svc = _svc(_tmp())
    mid = repo.create(title="Board review")["id"]
    repo.patch(mid, {"notes": NOTES})
    seg = repo.add_segment(mid, "mic", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(seg["id"], text="agreed, the fourteenth works", backend="proxy")

    monitor = activity.Monitor(repo.db, lambda: dict(SETTINGS), svc._complete)
    monitor.store.add("focus", app="zoom.us", title="Board review")
    assert monitor.store.recent()

    # Store.purge("all") is a bare unfiltered DELETE (activity.py:422-436) behind the Privacy
    # tab. Meetings keep their own non-expiring tables precisely so it cannot see them; if
    # anyone ever moves meeting storage into activity_events, this is the test that breaks.
    monitor.store.purge("all")
    assert monitor.store.recent() == []
    survived = repo.get(mid)
    assert survived is not None and survived["notes"] == NOTES
    assert len(repo.segments(mid)) == 1
    assert repo.search("pricing")[0]["meeting_id"] == mid       # the FTS row survived too
    with repo.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM meeting_segments").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 1


# ---------------------------------------------------------------- the 45s tick


def test_auto_stop_is_time_based_and_respects_the_grace_window() -> None:
    repo, svc = _svc(_tmp())
    stopped: list[str] = []

    async def fake_stop(meeting_id: str) -> None:
        stopped.append(meeting_id)

    svc.stop = fake_stop  # type: ignore[method-assign]
    cfg = svc.config()
    grace = float(cfg["autoStopGraceSeconds"])
    assert grace == 90

    inside = repo.create(title="Running long", scheduled_end=time.time() - grace / 2)["id"]
    svc.pool.sessions = {inside: _FakeSession(inside)}
    asyncio.run(svc._auto_stop(cfg))
    assert stopped == []                                    # still within the grace window

    late = repo.create(title="Finished a while ago", scheduled_end=time.time() - grace - 60)["id"]
    svc.pool.sessions = {late: _FakeSession(late)}
    asyncio.run(svc._auto_stop(cfg))
    assert stopped == [late]

    # Nothing in this design measures amplitude - there is no voice-activity detection anywhere
    # in the codebase - so the only other rule is total length.
    forever = repo.create(title="No scheduled end")["id"]
    session = _FakeSession(forever)
    session.started_at = time.time() - float(cfg["maxMeetingSeconds"]) - 1
    svc.pool.sessions = {forever: session}
    asyncio.run(svc._auto_stop(cfg))
    assert stopped == [late, forever]


def test_recover_finalizes_what_a_quit_left_mid_flight() -> None:
    repo, svc = _svc(_tmp())
    live = repo.create(title="Pricing call")["id"]
    repo.mark_started(live, "/nowhere/recordings/x", ["mic"])
    seg = repo.add_segment(live, "mic", 0, 0.0, 20.0, 1_700_000_000.0, "/nowhere/0.wav", 4096)
    repo.finish_segment(seg["id"], text="agreed, the fourteenth works", backend="proxy")
    assert repo.get(live)["status"] == "recording"
    assert repo.unfinished()[0]["id"] == live

    assert svc.recover() == [live]
    out = repo.get(live)
    assert out["status"] == "ready"
    assert "interrupted" in out["error"]
    assert out["transcript"] == "00:00 [you] agreed, the fourteenth works"
    assert repo.unfinished() == []
    assert svc.recover() == []                              # nothing left to recover


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
