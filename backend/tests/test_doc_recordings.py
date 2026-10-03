"""Recordings made inside a doc: the link, the proposed summary, and what happens when the doc goes.

A doc recording is an ordinary `meetings` row with a `doc_id`, so everything below drives the repo
and the service directly (the way test_meetings.py does) with a stub LLM and no microphone: segments
are written by hand through the same `_on_segment`/`_on_result` callbacks the recorder uses.

Runs under pytest, or directly: python backend/tests/test_doc_recordings.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docrectest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, meetings  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.trash import Trash  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}

GOOD_DOC_REPLY = json.dumps({
    "summary_markdown": "- Ship the tiers on the fourteenth\n- Dana owns the deck",
    "headline": "Agreed to ship the tiers on the fourteenth",
    "action_items": [{"text": "send the deck", "owner": "Dana", "due": "2026-10-02"}],
})

SAID = "we should ship the pricing tiers on the fourteenth"


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


class World:
    """db + docs + repo + service, with a stub model and a list of the events the service published."""

    def __init__(self, reply: str = GOOD_DOC_REPLY, tmp: Path | None = None):
        self.reply = reply
        self.llm: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

        async def fake_complete(settings: Any, model: str, messages: list[dict[str, Any]], kind: str = "learn") -> str:
            self.llm.append({"model": model, "messages": messages, "kind": kind})
            if self.reply == "__raise__":
                raise RuntimeError("proxy down")
            return self.reply

        self.tmp = tmp or _tmp()
        self.db = Database(self.tmp)
        self.docs = Docs(self.db)
        self.repo = meetings.Meetings(self.db)
        self.svc = meetings.MeetingService(self.db, lambda: dict(SETTINGS), fake_complete, self.repo,
                                           docs=self.docs, publish=self.events.append)
        self.docs.on_delete = self.repo.purge_doc

    def recording(self, doc: dict[str, Any], mode: str = "record", said: str = SAID,
                  status: str = "ready") -> dict[str, Any]:
        """A finished recording of `doc` with one transcribed segment, as `stop` would leave it."""
        m = self.repo.create(title=doc["title"], doc_id=doc["id"], doc_mode=mode, status="scheduled")
        mid = m["id"]
        self.repo.mark_started(mid, str(self.tmp / "rec" / mid), ["mic"], started_at=time.time() - 120)
        if said:
            self.segment(mid, said)
        self.repo.finalize(mid, self.repo.build_transcript(mid), status=status)
        return self.repo.get(mid)  # type: ignore[return-value]

    def segment(self, mid: str, text: str, seq: int = 0) -> None:
        info = {"t_start": seq * 6.0, "t_end": seq * 6.0 + 6.0, "started_at": time.time(), "wav_path": "",
                "wav_bytes": 0, "duration_ms": 6000, "state": "recorded"}
        self.svc._on_segment(mid, "mic", seq, Path("x.wav"), info)
        self.svc._on_result(mid, "mic", seq, Path("x.wav"), {
            "text": text, "detail": {}, "backend": "proxy", "error": "", "state": "done",
            "wav_path": "", "wav_bytes": 0})

    def summarize(self, mid: str, **kw: Any) -> dict[str, Any]:
        return asyncio.run(self.svc.summarize_into_doc(mid, **kw))


def _pending(w: World, doc_id: str) -> list[dict[str, Any]]:
    return (w.docs.get(doc_id) or {})["pending"]


# ---------------------------------------------------------------- schema


def test_an_existing_database_gains_the_link_columns_and_the_append_column() -> None:
    tmp = _tmp()
    db = Database(tmp)
    # An old install: meetings without doc_id and doc_revisions without append.
    with db.tx() as c:
        c.executescript(meetings.SCHEMA)
        c.executescript("""
            CREATE TABLE docs (id TEXT PRIMARY KEY, project_id TEXT, title TEXT NOT NULL DEFAULT 'Untitled',
              content TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '', starred INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE doc_revisions (id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, before TEXT NOT NULL DEFAULT '',
              after TEXT NOT NULL DEFAULT '', title_before TEXT, title_after TEXT, summary TEXT NOT NULL DEFAULT '',
              author TEXT NOT NULL DEFAULT 'user', tool TEXT, status TEXT NOT NULL DEFAULT 'applied',
              created_at REAL NOT NULL, resolved_at REAL);
        """)
        c.execute("INSERT INTO meetings(id,title,created_at,updated_at) VALUES('old1','Old standup',1,1)")
    repo = meetings.Meetings(db)
    docs = Docs(db)
    with db.tx() as c:
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(meetings)").fetchall()}
        rcols = {r["name"] for r in c.execute("PRAGMA table_info(doc_revisions)").fetchall()}
        idx = {r["name"] for r in c.execute("PRAGMA index_list(meetings)").fetchall()}
    assert {"doc_id", "doc_mode", "summary_revision_id"} <= mcols
    assert "append" in rcols
    assert "idx_meetings_doc" in idx
    old = repo.get("old1")
    assert old and old["doc_id"] is None and old["doc_mode"] is None
    assert [m["id"] for m in repo.list()] == ["old1"]       # an ordinary meeting is still in the rail
    d = docs.create("Plan", "# Plan")
    assert docs.propose_append(d["id"], "## More")["status"] == "pending"
    # constructing both a second time (an app restart) is a no-op
    meetings.Meetings(db)
    Docs(db)


# ---------------------------------------------------------------- the link and the rail


def test_create_list_for_doc_and_the_rail_excludes_doc_recordings() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    other = w.docs.create("Other", "x")
    plain = w.repo.create(title="Standup", status="scheduled")
    rec = w.recording(doc)
    w.recording(other)
    assert rec["doc_id"] == doc["id"] and rec["doc_mode"] == "record" and rec["summary_revision_id"] is None
    assert [m["id"] for m in w.repo.list()] == [plain["id"]]                     # the rail: ordinary only
    assert {m["id"] for m in w.repo.list(include_docs=True)} >= {plain["id"], rec["id"]}
    assert [m["id"] for m in w.repo.list(doc_id=doc["id"])] == [rec["id"]]
    rows = w.repo.for_doc(doc["id"])
    assert [r["id"] for r in rows] == [rec["id"]] and rows[0]["summary_state"] == "none"
    # doc_id is set once, at create: a PATCH cannot move a recording to another doc
    assert w.repo.patch(rec["id"], {"doc_id": other["id"]})["doc_id"] == doc["id"]
    assert w.repo.search("pricing tiers")[0]["doc_id"] in (doc["id"], other["id"])


def test_summary_state_follows_the_doc_revision() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    rev = w.summarize(m["id"])["revision"]
    assert w.repo.for_doc(doc["id"])[0]["summary_state"] == "pending"
    w.docs.accept(rev["id"])
    assert w.repo.for_doc(doc["id"])[0]["summary_state"] == "applied"
    again = w.summarize(m["id"], force=True)["revision"]
    w.docs.reject(again["id"])
    assert w.repo.for_doc(doc["id"])[0]["summary_state"] == "rejected"


# ---------------------------------------------------------------- the summary


def test_summarize_proposes_an_append_and_never_touches_the_doc() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan\n- item")
    m = w.recording(doc)
    out = w.summarize(m["id"])
    rev = out["revision"]
    assert out["error"] is None and rev["status"] == "pending" and rev["tool"] == "recording_summary"
    assert rev["after"].startswith("# Plan\n- item\n\n:::ai\n\n## Recording summary (") and rev["after"].rstrip().endswith("\n\n:::")
    assert "Ship the tiers on the fourteenth" in rev["after"]
    got = w.docs.get(doc["id"])
    assert got["content"] == "# Plan\n- item"                      # not applied, whatever the edit mode
    assert [r["id"] for r in got["pending"]] == [rev["id"]]
    row = w.repo.get(m["id"])
    assert row["summary_revision_id"] == rev["id"]
    assert row["summary"] == "Agreed to ship the tiers on the fourteenth"
    assert "Dana owns the deck" in row["enhanced"] and row["notes"] == ""
    assert row["pending"] is None and w.repo.revisions(m["id"]) == []   # no meeting_revisions row
    assert [a["text"] for a in row["actions"]] == ["send the deck"]
    assert row["status"] == "ready"
    # the transcript is quoted data in the prompt, and the doc is context
    user = json.loads(w.llm[0]["messages"][1]["content"])
    assert SAID in user["transcript"] and user["note_title"] == "Plan"
    assert w.llm[0]["kind"] == "doc_recording"


def test_text_typed_after_the_proposal_survives_accept() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    rev = w.summarize(w.recording(doc)["id"])["revision"]
    w.docs.save(doc["id"], content="# Plan\nTyped while the summary was thinking.")
    view = w.docs.get(doc["id"])["pending"][0]
    # the diff shows only the addition and is not stale
    assert view["before"] == "# Plan\nTyped while the summary was thinking."
    assert view["after"].startswith(view["before"] + "\n\n:::ai\n\n## Recording summary") and view["stale"] is False
    got = w.docs.accept(rev["id"])
    assert got["content"].startswith("# Plan\nTyped while the summary was thinking.\n\n:::ai\n\n## Recording summary")
    applied = w.docs.revision(rev["id"])
    assert applied["status"] == "applied" and applied["after"] == got["content"]
    assert applied["before"] == "# Plan\nTyped while the summary was thinking."


def test_an_ordinary_revision_still_replaces_the_body() -> None:
    w = World()
    doc = w.docs.create("Plan", "old")
    rev = w.docs.propose(doc["id"], "new body", summary="edit")
    assert rev["append"] is None
    w.docs.save(doc["id"], content="old plus typing")
    assert w.docs.get(doc["id"])["pending"][0]["stale"] is True
    assert w.docs.accept(rev["id"])["content"] == "new body"


def test_resummarizing_supersedes_the_earlier_pending_revision() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    first = w.summarize(m["id"])["revision"]
    # without force the pending one is the answer, and no second model call is paid for
    assert w.summarize(m["id"])["revision"]["id"] == first["id"] and len(w.llm) == 1
    second = w.summarize(m["id"], force=True, focus="decisions only")["revision"]
    assert second["id"] != first["id"] and len(w.llm) == 2
    assert json.loads(w.llm[1]["messages"][1]["content"])["focus"] == "decisions only"
    assert w.docs.revision(first["id"])["status"] == "rejected"
    assert [r["id"] for r in _pending(w, doc["id"])] == [second["id"]]
    assert w.repo.get(m["id"])["summary_revision_id"] == second["id"]


def test_a_model_failure_leaves_no_revision_and_no_transcript_in_the_doc() -> None:
    w = World(reply="__raise__")
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    out = w.summarize(m["id"])
    assert out["revision"] is None and "Summary failed" in out["error"]
    assert _pending(w, doc["id"]) == [] and w.docs.revisions(doc["id"])[0]["author"] == "user"
    assert SAID not in w.docs.get(doc["id"])["content"]
    row = w.repo.get(m["id"])
    assert "Summary failed" in row["error"] and row["summary_revision_id"] is None
    assert SAID in row["transcript"] and row["status"] == "ready"        # the transcript is intact
    # a reply with no section is a failure too, not an empty proposal
    w.reply = json.dumps({"headline": "x"})
    assert w.summarize(m["id"], force=True)["revision"] is None
    # and a later success clears the banner
    w.reply = GOOD_DOC_REPLY
    assert w.summarize(m["id"], force=True)["revision"] is not None
    assert "Summary failed" not in w.repo.get(m["id"])["error"]


def test_nothing_said_is_explained_not_summarized() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc, said="")
    out = w.summarize(m["id"])
    assert out["revision"] is None and "Nothing was said" in out["error"] and w.llm == []
    assert _pending(w, doc["id"]) == []


def test_the_summary_is_scrubbed_of_credentials() -> None:
    key = "sk-aaaaaaaaaaaaaaaaaaaaaa"
    w = World(reply=json.dumps({"summary_markdown": f"- the key is {key}", "headline": f"key {key}",
                                "action_items": [{"text": f"rotate {key}"}]}))
    doc = w.docs.create("Plan", "# Plan")
    out = w.summarize(w.recording(doc)["id"])
    assert key not in out["revision"]["after"] and "[secret]" in out["revision"]["after"]
    row = out["meeting"]
    assert key not in row["enhanced"] and key not in row["summary"]
    assert all(key not in a["text"] for a in row["actions"])


def test_a_trashed_doc_gets_no_summary() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    Trash(w.db, None, w.docs).trash("doc", doc["id"])
    out = w.summarize(m["id"])
    assert out["revision"] is None and "trash" in out["error"] and w.llm == []


def test_stop_time_dispatch_summarizes_a_record_row_and_leaves_dictation_alone() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    dictated = w.recording(doc, mode="dictate")
    asyncio.run(w.svc._enhance_quietly(dictated["id"]))
    assert _pending(w, doc["id"]) == [] and w.llm == []             # dictation: the words were already typed
    recorded = w.recording(doc, mode="record")
    asyncio.run(w.svc._enhance_quietly(recorded["id"]))
    assert len(_pending(w, doc["id"])) == 1
    # the meeting enhance pass refuses a doc-linked row
    assert asyncio.run(w.svc.enhance(recorded["id"], force=True)) is None
    assert len(w.llm) == 1


# ---------------------------------------------------------------- lifecycle of the link


def test_a_trashed_doc_hides_its_recordings_and_restore_brings_them_back() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    trash = Trash(w.db, None, w.docs)
    assert w.repo.search("pricing")
    trash.trash("doc", doc["id"])
    assert w.repo.list(doc_id=doc["id"]) == [] and w.repo.list(include_docs=True) == []
    assert w.repo.for_doc(doc["id"]) == []
    assert w.repo.search("pricing") == []
    assert w.repo.find(m["id"]) is None and w.repo.find(doc["title"]) is None
    trash.restore("doc", doc["id"])
    assert [r["id"] for r in w.repo.for_doc(doc["id"])] == [m["id"]]
    assert w.repo.search("pricing")[0]["doc_id"] == doc["id"]
    assert w.repo.find(m["id"])["id"] == m["id"]


def test_purging_a_doc_deletes_its_recordings_row_fts_and_audio() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    audio = w.tmp / "rec" / m["id"]
    audio.mkdir(parents=True)
    (audio / "mic-00000.wav").write_bytes(b"RIFF")
    keep = w.recording(w.docs.create("Other", "x"))
    trash = Trash(w.db, None, w.docs)
    trash.trash("doc", doc["id"])
    assert trash.purge("doc", doc["id"]) is True
    assert w.repo.get(m["id"]) is None and not audio.exists()
    with w.db.tx() as c:
        assert c.execute("SELECT 1 FROM meetings_fts WHERE meeting_id=?", (m["id"],)).fetchone() is None
        assert c.execute("SELECT 1 FROM meeting_segments WHERE meeting_id=?", (m["id"],)).fetchone() is None
    assert w.repo.get(keep["id"]) is not None                       # another doc's recording is untouched


def test_chat_context_never_carries_a_doc_recording() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    w.summarize(m["id"])
    plain = w.repo.create(title="Standup", status="notes_only")
    w.repo.patch(plain["id"], {"summary": "Weekly standup headline"})
    w.svc.set_config({"enabled": True, "injectContext": True})
    block = w.svc.context_block()
    assert "Weekly standup headline" in block
    assert "Agreed to ship the tiers" not in block and "Plan" not in block
    assert w.repo.records_doc(doc["id"]) is True and w.repo.records_doc("nope") is False


# ---------------------------------------------------------------- the recorder seam


class _Pool:
    """Stands in for RecorderPool.start so `start` can be checked without a microphone."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.kw: dict[str, Any] = {}
        self.channels: dict[str, Any] = {}
        self.session: Any = None

    def start(self, meeting_id: str, channels: dict[str, Any], **kw: Any) -> Any:
        self.kw, self.channels = kw, channels
        self.session = SimpleNamespace(
            meeting_id=meeting_id, out_dir=self.tmp / "rec" / meeting_id, started_at=time.time(),
            segment_seconds=kw["segment_seconds"],
            stats=lambda: {"elapsed_ms": 1, "segments_done": 0, "segments_pending": 0, "queued": 0,
                           "paused": False, "channels": []},
            errors=lambda: {})
        return self.session

    def live(self) -> Any:
        return self.session

    def get(self, meeting_id: str) -> Any:
        return self.session


def test_start_overrides_segment_length_and_sources_for_doc_recordings() -> None:
    w = World()
    w.svc.set_config({"enabled": True, "sources": ["mic", "output"], "segmentSeconds": 20})
    w.svc.preflight = lambda force=False: {"ok": True, "blockers": []}      # type: ignore[assignment]
    real = (meetings.native_audio.mic_available, meetings.native_audio.system_available,
            audiocap.native_mic_input, audiocap.native_output_input)
    meetings.native_audio.mic_available = lambda: True                       # type: ignore[assignment]
    meetings.native_audio.system_available = lambda: True                    # type: ignore[assignment]
    audiocap.native_mic_input = lambda uid="": ["native", "mic", uid]        # type: ignore[assignment]
    audiocap.native_output_input = lambda: ["native", "output"]              # type: ignore[assignment]
    try:
        doc = w.docs.create("Plan", "# Plan")
        for mode, want_seconds, want_channels in (("record", 10, {"mic", "output"}), ("dictate", 8, {"mic"})):
            w.svc.pool = _Pool(w.tmp)                                        # type: ignore[assignment]
            m = w.repo.create(title="t", doc_id=doc["id"], doc_mode=mode, status="scheduled")
            w.svc.start(m["id"])
            assert w.svc.pool.kw["segment_seconds"] == want_seconds, mode    # type: ignore[attr-defined]
            assert set(w.svc.pool.channels) == want_channels, mode           # type: ignore[attr-defined]
            assert w.svc.pool.kw["cut_on_silence"] is True, mode             # type: ignore[attr-defined]
            active = w.svc.status()["active"]
            assert active["doc_id"] == doc["id"] and active["doc_mode"] == mode
            assert active["segment_seconds"] == want_seconds
        # an ordinary meeting keeps the user's own setting, and the explicit override wins
        w.svc.pool = _Pool(w.tmp)                                            # type: ignore[assignment]
        plain = w.repo.create(title="Standup", status="scheduled")
        w.svc.start(plain["id"])
        assert w.svc.pool.kw["segment_seconds"] == 20                        # type: ignore[attr-defined]
        assert w.svc.pool.kw["cut_on_silence"] is False                      # type: ignore[attr-defined]
        assert w.svc.status()["active"]["doc_id"] is None
        w.svc.pool = _Pool(w.tmp)                                            # type: ignore[assignment]
        again = w.repo.create(title="Standup 2", status="scheduled")
        w.svc.start(again["id"], segment_seconds=9, sources=["mic"])
        assert w.svc.pool.kw["segment_seconds"] == 9 and set(w.svc.pool.channels) == {"mic"}  # type: ignore[attr-defined]
    finally:
        (meetings.native_audio.mic_available, meetings.native_audio.system_available,
         audiocap.native_mic_input, audiocap.native_output_input) = real  # type: ignore[assignment]
    assert [e["status"] for e in w.events if e["kind"] == "status"][:1] == ["recording"]


# ---------------------------------------------------------------- events


def test_events_carry_the_scrubbed_row_and_the_doc_link() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.repo.create(title="t", doc_id=doc["id"], doc_mode="record", status="scheduled")
    key = "sk-aaaaaaaaaaaaaaaaaaaaaa"
    w.segment(m["id"], f"the key is {key} ok")
    segs = [e for e in w.events if e["kind"] == "segment"]
    assert len(segs) == 1
    ev = segs[0]
    assert ev["meeting_id"] == m["id"] and ev["doc_id"] == doc["id"] and ev["doc_mode"] == "record"
    assert key not in ev["segment"]["text"] and "[secret]" in ev["segment"]["text"]
    assert isinstance(ev["segment"]["cursor"], int) and ev["segment"]["state"] == "done"
    json.dumps(ev)                                                           # the topic serialises it
    # an ordinary meeting publishes too, with a null doc
    plain = w.repo.create(title="Standup", status="scheduled")
    w.segment(plain["id"], "hello", seq=0)
    last = [e for e in w.events if e["kind"] == "segment"][-1]
    assert last["doc_id"] is None and last["doc_mode"] is None
    # the summary lands as an event, and so does a failure
    w.repo.mark_started(m["id"], str(w.tmp / "x"), ["mic"])
    w.repo.finalize(m["id"], w.repo.build_transcript(m["id"]))
    rev = w.summarize(m["id"])["revision"]
    ok = [e for e in w.events if e["kind"] == "summary"][-1]
    assert ok["revision_id"] == rev["id"] and ok["error"] is None
    w.reply = "__raise__"
    w.summarize(m["id"], force=True)
    bad = [e for e in w.events if e["kind"] == "summary"][-1]
    assert bad["revision_id"] is None and "Summary failed" in bad["error"]


def test_a_dead_listener_cannot_cost_a_segment() -> None:
    w = World()
    w.svc.publish = lambda ev: (_ for _ in ()).throw(RuntimeError("no listener"))     # type: ignore[assignment]
    doc = w.docs.create("Plan", "# Plan")
    m = w.repo.create(title="t", doc_id=doc["id"], doc_mode="record", status="scheduled")
    w.segment(m["id"], "still stored")
    assert w.repo.segments(m["id"])[0]["text"] == "still stored"


# ---------------------------------------------------------------- the routes


def test_routes_a_refused_start_leaves_no_row_and_the_doc_routes_work() -> None:
    from fastapi.testclient import TestClient

    from personal_os import app as app_mod
    from personal_os.app import AUTH_TOKEN, app, docs, meeting_store, meeting_svc

    client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    real = meetings.stt.selftest
    real_dev = audiocap._dev_cache
    meetings.stt.selftest = lambda **k: {"ok": True, "backend": "proxy", "error": ""}   # type: ignore[assignment]
    audiocap._dev_cache = (time.time(), [{"index": "0", "name": "Test mic"}])
    real_mac = app_mod.activity.IS_MAC
    app_mod.activity.IS_MAC = True
    d = docs.create("Route plan", "# Route plan")
    made: list[str] = []
    try:
        before = {m["id"] for m in meeting_store.list(include_docs=True)}
        # the recorder switch is off, so the start is blocked: 409 with the same body as /meetings/{id}/start
        r = client.post(f"/docs/{d['id']}/recordings", json={"mode": "record"})
        assert r.status_code == 409, r.text
        assert any(b["id"] == "enabled" for b in r.json()["detail"]["blockers"])
        assert {m["id"] for m in meeting_store.list(include_docs=True)} == before       # nothing left behind
        assert client.post(f"/docs/{d['id']}/recordings", json={"mode": "shout"}).status_code == 400
        assert client.post("/docs/nope/recordings", json={}).status_code == 404
        assert client.get("/docs/nope/recordings").status_code == 404

        # POST /meetings with a doc_id links the row (audio import into a doc), and validates the doc
        assert client.post("/meetings", json={"title": "x", "doc_id": "nope"}).status_code == 404
        r = client.post("/meetings", json={"title": "Imported", "doc_id": d["id"]})
        assert r.status_code == 200 and r.json()["doc_id"] == d["id"] and r.json()["doc_mode"] == "record"
        mid = r.json()["id"]
        made.append(mid)
        assert mid not in {m["id"] for m in client.get("/meetings").json()}               # not in the rail
        assert mid in {m["id"] for m in client.get("/meetings?include_docs=true").json()}
        assert [m["id"] for m in client.get(f"/meetings?doc_id={d['id']}").json()] == [mid]
        recs = client.get(f"/docs/{d['id']}/recordings").json()
        assert [x["id"] for x in recs] == [mid] and recs[0]["summary_state"] == "none"

        # summarize: doc-linked rows only; the model is the real llm.complete, so stub it on the service
        plain = client.post("/meetings", json={"title": "Standup"}).json()
        made.append(plain["id"])
        assert client.post(f"/meetings/{plain['id']}/summarize", json={}).status_code == 400
        assert client.post("/meetings/nope/summarize", json={}).status_code == 404
        assert client.post(f"/meetings/{plain['id']}/enhance").status_code in (200, 502)
        assert client.post(f"/meetings/{mid}/enhance").status_code == 400

        async def fake(settings: Any, model: str, messages: list[Any], kind: str = "learn") -> str:
            return GOOD_DOC_REPLY
        real_complete = meeting_svc._complete
        meeting_svc._complete = fake
        try:
            meeting_store.finalize(mid, "[you] we should ship on the fourteenth")
            r = client.post(f"/meetings/{mid}/summarize", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["error"] is None and body["revision"]["tool"] == "recording_summary"
            assert body["meeting"]["summary_revision_id"] == body["revision"]["id"]
            assert client.get(f"/docs/{d['id']}/recordings").json()[0]["summary_state"] == "pending"
            assert client.get(f"/docs/{d['id']}").json()["content"] == "# Route plan"
        finally:
            meeting_svc._complete = real_complete

        # the doc now has a record-mode recording, so saving it is not banked as the user's voice
        from personal_os.app import style
        prose = "I walked to the shop on the corner and bought bread, then came home and wrote this down. " * 6
        client.put(f"/docs/{d['id']}", json={"content": prose})
        assert not [s for s in style.samples(None) if s["ref"] == f"doc:{d['id']}"]
        free = docs.create("Free doc", "")
        made_doc = free["id"]
        client.put(f"/docs/{made_doc}", json={"content": prose})
        assert [s for s in style.samples(None) if s["ref"] == f"doc:{made_doc}"]
        style.delete_sample([s for s in style.samples(None) if s["ref"] == f"doc:{made_doc}"][0]["id"])
        docs.delete(made_doc)
    finally:
        meetings.stt.selftest = real                              # type: ignore[assignment]
        audiocap._dev_cache = real_dev
        app_mod.activity.IS_MAC = real_mac
        for mid in made:
            meeting_store.delete(mid)
        docs.delete(d["id"])                                      # also deletes anything still linked to it
    # the test data dir is shared with other files' imports: leave no docs behind
    assert not [x for x in docs.list() if x["title"] in ("Route plan", "Free doc")]
    # the config accepts the two new keys
    assert client.put("/meetings/config", json={"docSegmentSeconds": 7, "dictationSegmentSeconds": 3}).status_code == 200
    cfg = client.get("/meetings/config").json()
    assert cfg["docSegmentSeconds"] == 7 and cfg["dictationSegmentSeconds"] == 3
    client.put("/meetings/config", json={"docSegmentSeconds": 10, "dictationSegmentSeconds": 8})


def test_a_trashed_docs_recording_is_missing_to_external_reads_but_not_to_the_lifecycle() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    trash = Trash(w.db, None, w.docs)
    trash.trash("doc", doc["id"])
    assert w.repo.get(m["id"], include_hidden=False) is None and w.repo.is_hidden(m["id"])
    # lifecycle code (stop, the transcribe worker, recover) still reaches the row
    assert w.repo.get(m["id"])["id"] == m["id"]
    w.repo.finalize(m["id"], "late", status="ready")
    trash.restore("doc", doc["id"])
    assert w.repo.get(m["id"], include_hidden=False)["id"] == m["id"] and not w.repo.is_hidden(m["id"])


def test_routes_404_for_a_trashed_docs_recording() -> None:
    from fastapi.testclient import TestClient

    from personal_os.app import AUTH_TOKEN, app, docs, meeting_store

    client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    d = docs.create("Hidden plan", "# Hidden plan")
    m = meeting_store.create(title="Hidden plan", doc_id=d["id"], doc_mode="record", status="ready")
    try:
        assert client.get(f"/meetings/{m['id']}").status_code == 200
        Trash(meeting_store.db, None, docs).trash("doc", d["id"])
        for path in ("", "/segments", "/transcript", "/actions", "/revisions"):
            assert client.get(f"/meetings/{m['id']}{path}").status_code == 404, path
        assert client.post(f"/meetings/{m['id']}/summarize", json={}).status_code == 404
    finally:
        docs.delete(d["id"])


def test_an_orphaned_recording_stays_hidden() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    w.docs.on_delete = None                       # a purge whose hook failed: the doc row goes, the recording stays
    w.docs.delete(doc["id"])
    assert w.repo.get(m["id"]) is not None
    assert w.repo.get(m["id"], include_hidden=False) is None
    assert w.repo.list(include_docs=True) == [] and w.repo.search("pricing") == [] and w.repo.find(m["id"]) is None


def test_a_failing_purge_hook_is_logged_not_swallowed() -> None:
    import logging

    w = World()
    doc = w.docs.create("Plan", "# Plan")

    def boom(_id: str) -> None:
        raise RuntimeError("hook down")

    w.docs.on_delete = boom
    records: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    h = Grab()
    lg = logging.getLogger("personal_os.docs")
    lg.addHandler(h)
    try:
        w.docs.delete(doc["id"])
    finally:
        lg.removeHandler(h)
    assert w.docs.get(doc["id"]) is None and any("hook down" in r.getMessage() for r in records)


def test_daily_is_atomic_under_concurrent_calls() -> None:
    import threading

    w = World()
    out: list[Any] = []
    ts = [threading.Thread(target=lambda: out.append(w.docs.daily("2031-01-02"))) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len({d["id"] for d, _ in out}) == 1 and sum(1 for _, created in out if created) == 1
    assert len([d for d in w.docs.list() if d["title"] == "2031-01-02"]) == 1


def test_a_recording_follows_its_doc_into_a_new_project() -> None:
    w = World()
    w.docs.on_move = w.repo.move_doc
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    with w.db.tx() as c:
        c.execute("INSERT INTO projects(id,name,created_at) VALUES('p1','P One',0)")
    w.docs.update_meta(doc["id"], {"project_id": "p1"})
    assert w.repo.get(m["id"])["project_id"] == "p1"
    assert [r["id"] for r in w.repo.list(project_id="p1", include_docs=True)] == [m["id"]]
    w.docs.move(doc["id"], None, "")
    assert w.repo.get(m["id"])["project_id"] is None


def test_an_ordinary_revision_is_stale_after_an_append_is_accepted() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan\n\nbody")
    old = w.docs.propose(doc["id"], "# Plan\n\nrewritten", "Rewrite")
    assert old["stale"] is False
    app_rev = w.docs.propose_append(doc["id"], "## Summary\n\n- a point", "Summary", tool="recording_summary")
    w.docs.accept(app_rev["id"])
    cur = w.docs.get(doc["id"])["content"]
    full = w.docs._rev_view(w.docs.revision(old["id"]), cur)
    assert full["status"] == "pending" and full["stale"] is True and full["stat_vs_current"] is not None


def test_summary_evidence_reaches_the_meeting_row_and_the_stored_text_is_tag_free() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan")
    m = w.recording(doc)
    seg_id = w.repo.segments(m["id"], limit=10)[0]["id"]
    w.reply = json.dumps({"summary_markdown": "- Ship the tiers {s1}\n- Dana owns the deck {s7}", "headline": "h"})
    out = w.summarize(m["id"], force=True)
    row = w.repo.get(m["id"])
    assert row["summary_evidence"] == {"0": [seg_id]}
    assert "{" not in row["enhanced"] and "{" not in out["revision"]["after"]


def test_note_marks_reach_the_summary_payload_only_when_present() -> None:
    w = World()
    doc = w.docs.create("Plan", "# Plan\n- pricing tiers: ask about the fourteenth\n- gone line")
    m = w.recording(doc)
    w.summarize(m["id"])
    assert "note_timeline" not in json.loads(w.llm[0]["messages"][1]["content"])
    marks = w.repo.set_note_marks(m["id"], [{"line": "- pricing tiers: ask about the fourteenth", "t": 271},
                                            {"line": "- gone line", "t": 300}, {"line": "- deleted", "t": 5}])
    assert len(marks) == 3 and w.repo.get(m["id"])["note_marks"][0]["t"] == 271
    w.docs.save(doc["id"], content="# Plan\n- pricing tiers: ask about the fourteenth")
    w.summarize(m["id"], force=True)
    user = json.loads(w.llm[-1]["messages"][1]["content"])
    assert user["note_timeline"] == [{"at": "04:31", "line": "- pricing tiers: ask about the fourteenth"}]


def test_note_marks_merge_cap_and_ignore_dictation() -> None:
    w = World()
    doc = w.docs.create("Plan", "x")
    m = w.recording(doc)
    w.repo.set_note_marks(m["id"], [{"line": "a", "t": 1}])
    out = w.repo.set_note_marks(m["id"], [{"line": "a", "t": 9}, {"line": "bad", "t": "x"}])
    assert out == [{"line": "a", "t": 9.0}]
    out = w.repo.set_note_marks(m["id"], [{"line": f"l{i}", "t": i} for i in range(600)])
    assert len(out) == meetings.MAX_NOTE_MARKS and out[-1]["line"] == "l599"
    d = w.recording(doc, mode="dictate")
    assert w.repo.set_note_marks(d["id"], [{"line": "a", "t": 1}]) == []
    assert w.repo.set_note_marks("nope", []) is None
# ---------------------------------------------------------------- calendar 'Take notes'
def _event_world(start: Any) -> tuple[World, Any]:
    from personal_os import app as appmod
    appmod.docs, appmod.meeting_store = w.docs, w.repo
    appmod.activity.IS_MAC = True
    appmod.meeting_svc = SimpleNamespace(start=start, capabilities=lambda: [])
    w.docs.on_delete = w.repo.on_doc_deleted if hasattr(w.repo, "on_doc_deleted") else w.docs.on_delete
    return w, appmod
def test_from_event_creates_one_doc_with_attendees_and_is_idempotent() -> None:
    w, appmod = _event_world(lambda mid: w.repo.get(mid))
    body = appmod.DocFromEventIn(event_id="ev1", title="Pricing sync", start=1000.0,
                                 attendees=[{"email": "a@x.io", "name": "Ann"}, "b@x.io"])
    out = asyncio.run(appmod.doc_from_event(body))
    assert out["existing"] is False and out["doc"]["title"] == "Pricing sync"
    assert "Attendees: Ann, b@x.io" in out["doc"]["content"]
    assert out["started"]["doc_id"] == out["doc"]["id"] and out["started"]["calendar_event_id"] == "ev1"
    again = asyncio.run(appmod.doc_from_event(body))
    assert again["existing"] is True and again["doc"]["id"] == out["doc"]["id"]
    assert len(w.docs.list("__all__", "")) == 1
def test_from_event_refused_start_leaves_no_doc() -> None:
    from personal_os.meetings import MeetingBlocked
    def refuse(mid: str) -> Any:
        raise MeetingBlocked([{"id": "consent", "label": "Consent", "ok": False}])
    w, appmod = _event_world(refuse)
    try:
        asyncio.run(appmod.doc_from_event(appmod.DocFromEventIn(event_id="ev2", title="T")))
        raise AssertionError("expected 409")
    except appmod.HTTPException as e:
        assert e.status_code == 409
    assert w.docs.list("__all__", "") == [] and w.repo.by_event("ev2") is None


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
