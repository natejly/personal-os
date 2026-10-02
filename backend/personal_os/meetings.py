"""Meetings: a recorded conversation plus the notes taken during it.

The fourth text-bearing type, and distinct from the three that already exist:
  * `docs` — markdown the user writes, with a revision history.
  * `documents` — files the user uploaded, chunked for retrieval. Read-only knowledge.
  * `notes` — canvas mode's sticky notes: a body, a colour, no history.

A meeting is the only one whose body is partly machine-made, so it keeps the two halves in
separate columns: `notes` is what the user typed and has exactly one writer, and `enhanced` is
only ever set by accepting a `meeting_revisions` row. No model writes either column directly.

Nothing here expires, and no table below carries an expiry column. An activity event has one
and is swept on every `Monitor.loop` tick, and `POST /activity/purge` runs a bare
`DELETE FROM activity_events` (`Store.purge` in activity.py, scopes `events` and `all`) behind the Privacy tab. That absence is the
feature: nothing under /activity/* can reach a meeting.

Two classes, split the way activity.py splits Store from Monitor: `Meetings` is the repo and
touches nothing but SQLite, `MeetingService` owns the recorder pool, the capability probes, the
enhance pass and the 45s tick.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import audiocap, diarize, meeting_notes, meeting_recorder, native_audio, redact, stt, stt_stream
from .db import Database, new_id, now, row_to_dict
from .docs import diff_stat, word_count
from .repos import fts_query

log = logging.getLogger("personal_os.meetings")

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__,
# before Meetings(db) exists, so a _migrate entry for them would see nothing. Post-release
# columns need an additive ALTER in __init__ below. The rule is spelled out at canvas.py:21-23.
SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal, like docs/notes/todos
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'scheduled',
      -- scheduled | recording | stopped | transcribing | enhancing | ready | failed | notes_only
  template TEXT NOT NULL DEFAULT 'general',   -- key into meeting_notes.TEMPLATES
  notes TEXT NOT NULL DEFAULT '',             -- what the USER typed. No model ever writes this column.
  enhanced TEXT NOT NULL DEFAULT '',          -- set only by accepting a meeting_revisions row
  summary TEXT NOT NULL DEFAULT '',           -- one-line headline for the list rail
  transcript TEXT NOT NULL DEFAULT '',        -- channel-interleaved rollup, built ONLY on finalize
  scheduled_start REAL,
  scheduled_end REAL,
  started_at REAL,
  ended_at REAL,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  calendar_event_id TEXT,
  calendar_id TEXT,
  calendar_link TEXT,
  conference_link TEXT,                       -- Meet/Zoom/Teams URL; _event_out.meet is hangoutLink only
  attendees TEXT NOT NULL DEFAULT '[]',       -- JSON [{email,name,response,organizer,self}]
  sources TEXT NOT NULL DEFAULT '[]',         -- JSON e.g. ["mic","output"]: what was actually captured
  audio_dir TEXT NOT NULL DEFAULT '',         -- <data_dir>/recordings/<id>; '' once audio is deleted
  audio_bytes INTEGER NOT NULL DEFAULT 0,     -- retained wav bytes, for the disk ceiling
  keep_audio INTEGER NOT NULL DEFAULT 0,
  consent_ack INTEGER NOT NULL DEFAULT 0,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,  -- "chat with this meeting"
  error TEXT NOT NULL DEFAULT '',             -- last recorder/stt/enhance failure, shown as a banner
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meetings_start  ON meetings(COALESCE(started_at, scheduled_start) DESC);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
-- Stops the calendar nudge creating a duplicate meeting for one event on every 45s tick.
CREATE UNIQUE INDEX IF NOT EXISTS idx_meetings_event
  ON meetings(calendar_event_id) WHERE calendar_event_id IS NOT NULL;

-- One row per closed ffmpeg segment. t_start comes from the RECORDING clock
-- (session_start + seq * segment_seconds), not from when transcription returned.
-- That is the bug at activity.py:866-870, which calls store.add without ts=, so Store.add
-- defaults to now() and ts + duration_ms points into the future.
CREATE TABLE IF NOT EXISTS meeting_segments (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,                      -- mic | output | import
  seq INTEGER NOT NULL,                       -- ffmpeg segment number: order survives a restart
  t_start REAL NOT NULL,                      -- seconds from meeting start
  t_end REAL NOT NULL,
  started_at REAL NOT NULL,                   -- absolute epoch seconds of the segment's first sample
  duration_ms INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '{}',          -- verbose_json utterances, for a later diarization pass
  speaker TEXT NOT NULL DEFAULT '',           -- '' until diarized; 'me' by convention for mic
  state TEXT NOT NULL DEFAULT 'recorded',     -- recorded | transcribing | done | failed | empty | discarded
  attempts INTEGER NOT NULL DEFAULT 0,
  backend TEXT NOT NULL DEFAULT '',           -- 'proxy' | 'local', for the usage/debug line
  error TEXT NOT NULL DEFAULT '',
  wav_path TEXT NOT NULL DEFAULT '',          -- under data_dir/recordings/<id>/, never tmp/
  wav_bytes INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  UNIQUE (meeting_id, channel, seq)
);
CREATE INDEX IF NOT EXISTS idx_mseg_timeline ON meeting_segments(meeting_id, t_start);
CREATE INDEX IF NOT EXISTS idx_mseg_pending  ON meeting_segments(meeting_id, state, seq);
CREATE INDEX IF NOT EXISTS idx_mseg_failed   ON meeting_segments(state, created_at);

-- The enhance pass is a PROPOSAL (docs.py:266-314). Column names deliberately mirror
-- doc_revisions so Meetings._rev_view can emit a row that satisfies the DocRevision
-- interface in src/shared/types.ts:581-600 and <DiffView> renders it with no adapter.
CREATE TABLE IF NOT EXISTS meeting_revisions (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  before TEXT NOT NULL DEFAULT '',            -- `enhanced` as it stood when the pass ran
  after TEXT NOT NULL DEFAULT '',             -- the enhanced markdown
  title_before TEXT,
  title_after TEXT,
  summary TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT 'assistant',   -- 'user' | 'assistant'
  tool TEXT,                                  -- e.g. 'meeting_enhance'
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | applied | rejected | superseded
  template TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  degraded INTEGER NOT NULL DEFAULT 0,        -- 1 = the LLM failed; this is the mechanical fallback
  decisions TEXT NOT NULL DEFAULT '[]',       -- JSON
  topics TEXT NOT NULL DEFAULT '[]',          -- JSON
  created_at REAL NOT NULL,
  resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mrev_pending ON meeting_revisions(meeting_id, status, created_at DESC);

-- Action items are recorded here first and promoted into the existing todos table on demand,
-- so the review screen can show which ones already became tasks. todo_id has no FK on purpose:
-- deleting the todo must not erase the record that this meeting produced the item. And
-- Todos.update's whitelist (todos.py:96) cannot re-point an existing todo at a meeting later.
CREATE TABLE IF NOT EXISTS meeting_action_items (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  revision_id TEXT REFERENCES meeting_revisions(id) ON DELETE SET NULL,
  text TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT '',             -- attendee email or display name, '' if unassigned
  due TEXT NOT NULL DEFAULT '',               -- YYYY-MM-DD, '' if none
  status TEXT NOT NULL DEFAULT 'proposed',    -- proposed | added | dismissed
  todo_id TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mai_meeting ON meeting_action_items(meeting_id, status);

-- Same shape as docs_fts (docs.py:49-51) and chunks_fts/memories_fts (db.py:127-132):
-- owner id UNINDEXED, porter unicode61. There are no FTS triggers anywhere in this codebase,
-- so Meetings._reindex(c, ...) takes the live cursor and is called from every write path that
-- touches title/notes/enhanced/transcript. Meetings.delete must ALSO run
-- DELETE FROM meetings_fts WHERE meeting_id=? by hand: the virtual table is not covered
-- by ON DELETE CASCADE (docs.py:236-239).
CREATE VIRTUAL TABLE IF NOT EXISTS meetings_fts USING fts5(
  title, notes, enhanced, transcript, meeting_id UNINDEXED, tokenize='porter unicode61'
);
"""

# Channels the user may ask for. `output` needs a loopback driver (BlackHole/Loopback); without
# one a meeting records mic-only, which is a degradation and not a failure.
SOURCES = ("mic", "output")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "consentedAt": 0.0,       # unix seconds the consent modal was acknowledged; 0 blocks recording
    "autoRecord": False,
    "nudgeSeconds": 120,
    "micDevice": "",
    "micDeviceName": "",      # the name that index had when it was picked, so a reshuffle is refused
    "outputDevice": "",
    "outputDeviceName": "",
    "sources": ["mic"],
    "segmentSeconds": 20,     # how far behind live the transcript runs
    # A recording made inside a doc ends each clip at a pause in speech, so these two are ceilings:
    # the longest a clip runs when nobody stops talking. Most clips close sooner.
    "docSegmentSeconds": 10,
    "livePreview": False,     # dictation only: show in-flight words at the caret (on-device Speech; never typed in)
    "dictationSegmentSeconds": 8,  # dictation, where the words are waiting to be typed
    "maxMeetingSeconds": 14400,
    "drainSeconds": 90,
    "sttBackend": "auto",     # auto | speech | proxy | local | off
    "sttModel": "whisper-1",
    "whisperModelPath": "",
    "template": "general",
    "enhanceOnStop": True,
    "enhanceModel": "",
    "maxTranscriptChars": 48000,
    "keepAudio": False,
    "maxAudioBytes": 2147483648,
    "redactSecrets": True,
    "injectContext": True,
    "autoStopGraceSeconds": 90,
    "calendarIds": ["primary"],
    "minAttendees": 2,
    "vadGate": True,          # skip STT for segments with no speech (meeting_vad)
    "vadMinSpeechRatio": 0.03,
    "hallucinationFilter": True,
    "whisperVadModelPath": "",
    "maxImportSeconds": 14400,  # longest audio file an import will accept
    "diarize": False,           # separate remote speakers on retained audio (diarize.py)
    "diarizeBackend": "auto",   # auto | none | sherpa
    "diarizeSegmentationModel": "",
    "diarizeEmbeddingModel": "",
    "diarizeThreshold": 0.5,
    "diarizeSpeakers": 0,       # 0 = decide from the audio
}

# `patch` is the user's door into a meeting. Everything the recorder owns - started_at,
# audio_dir, sources, transcript, duration_ms - is deliberately absent: those go through
# mark_started/finalize so a stray PATCH cannot claim a meeting recorded something it didn't.
PATCH_FIELDS = {"title", "notes", "enhanced", "summary", "template", "project_id",
                "keep_audio", "status", "error", "conversation_id"}

# Only these three columns are in meetings_fts alongside the transcript, so a patch that changes
# none of them skips the reindex: FTS5 has no UPDATE, so a reindex is a DELETE plus an INSERT of
# the whole row, and the error banner is patched once per failed segment.
INDEXED_FIELDS = {"title", "notes", "enhanced"}

JSON_FIELDS = ("attendees", "sources", "decisions", "topics", "detail", "speaker_names")

# Columns added to `meetings` after the first release, as {name: ddl}. Empty today and applied in
# __init__ anyway: CREATE TABLE IF NOT EXISTS will not add a column, and db.py's _migrate runs
# inside Database.__init__, before this class exists (canvas.py:21-23). todos.py:49-53 is the shape.
# speaker_names maps a diarized id to a display name, e.g. {'S1': 'Dana'} (see diarize.py).
# doc_id/doc_mode link a recording to a doc (plain TEXT, the cascade is code: see Docs.on_delete);
# summary_revision_id is the doc_revisions row of the latest proposed summary.
ADDED_COLUMNS: dict[str, str] = {"speaker_names": "TEXT NOT NULL DEFAULT '{}'",
                                 "doc_id": "TEXT", "doc_mode": "TEXT", "summary_revision_id": "TEXT"}

DOC_MODES = ("record", "dictate")

# Channel-level attribution only: mic is the user, anything else is the room. There is no
# diarization in this slice, so every remote participant is one speaker.
CHANNEL_LABELS = {"mic": "[you]", "output": "[them]", "import": "[them]"}

NOTES_PREVIEW_CHARS = 240

# Sentinel wrapped around an FTS hit so `search` can tell which column matched; see search().
MARK = "\x02"

# Conference URLs a calendar event carries in prose rather than in a field: _event_out's `meet`
# is `hangoutLink` only (google.py:686), so a Zoom or Teams meeting has no structured link at all.
CONFERENCE_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:zoom\.us|zoomgov\.com|teams\.microsoft\.com|teams\.live\.com|"
    r"webex\.com|meet\.google\.com|whereby\.com|chime\.aws|around\.co|gather\.town)"
    r"/[^\s<>\"')\]]+",
    re.I,
)

TICK_SECONDS = 45.0           # the calendar nudge / auto-stop / retranscribe tick
# A drain that timed out leaves the worker still transcribing with the session already popped,
# so `stop` watches the segment rows instead and re-rolls the transcript once they settle.
DRAIN_WATCH_SECONDS = 900.0
DRAIN_POLL_SECONDS = 2.0
PREFLIGHT_TTL = 600.0         # a self-test records a wav and does a real round trip; cache it
SUGGEST_TTL = 60.0
RETRANSCRIBE_PER_TICK = 3     # slow on purpose: a failed segment is retried, not hammered
RETRANSCRIBE_MAX_ATTEMPTS = 8
# Audio outlives the meeting only while a failed segment could still be replayed. The backstop
# exists because an STT route that is never fixed would otherwise keep every wav forever.
AUDIO_RETENTION_SECONDS = 7 * 86400

# Which capability rows stop a recording from starting. `loopback` and `stt_local` are optional:
# no system-audio tap means mic-only, and whisper.cpp is only needed when Speech is unavailable
# and the proxy has no transcription route. The ffmpeg row is ok when native capture works.
BLOCKING_CAPABILITIES = ("platform", "ffmpeg", "mic", "stt")

# The banners `stop` writes ABOUT THE TRANSCRIPT ITSELF, and the only ones a later settle is
# allowed to clear. An enhance failure is still true after a segment replays, so it stays.
TRANSCRIPT_BANNERS = ("could not be transcribed", "still transcribing")


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config_for(db: Database) -> dict[str, Any]:
    """The merged meetings config. A free function so the repo can read it without the service."""
    stored = db.get_settings().get("meetings")
    return _deep_merge(DEFAULT_CONFIG, stored if isinstance(stored, dict) else {})


MAX_SPEAKER_NAME = 60


def _names(raw: Any) -> dict[str, str]:
    """speaker_names as a dict, whether it arrives as the stored JSON text or already decoded."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            return {}
    return {str(k): str(v) for k, v in raw.items() if str(v).strip()} if isinstance(raw, dict) else {}


def _speaker_utterances(detail: Any) -> list[dict[str, Any]]:
    """The diarized utterances in a segment's detail, or [] if it has none with a speaker."""
    if isinstance(detail, str):
        try:
            detail = json.loads(detail or "{}")
        except ValueError:
            return []
    utts = detail.get("utterances") if isinstance(detail, dict) else None
    if not isinstance(utts, list):
        return []
    utts = [u for u in utts if isinstance(u, dict)]
    return utts if any(u.get("speaker") for u in utts) else []


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _due_or_none(due: Any) -> str | None:
    """An action item's due date as the todo list accepts it; a date the model made up is dropped."""
    from .todos import clean_due

    try:
        return clean_due(due)
    except ValueError:
        return None


def _epoch(iso: Any) -> float:
    """Google's ISO timestamp as unix seconds. 0.0 when it is absent or a bare date."""
    s = str(iso or "").strip()
    if not s or "T" not in s:
        return 0.0
    with contextlib.suppress(ValueError):
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    return 0.0


def _conference_link(event: dict[str, Any]) -> str:
    """A joinable URL for an event, from hangoutLink first and then out of the prose."""
    direct = str(event.get("meet") or "").strip()
    if direct:
        return direct
    for field in ("location", "description"):
        m = CONFERENCE_RE.search(str(event.get(field) or ""))
        if m:
            return m.group(0)
    return ""


def _scrub_detail(detail: Any, depth: int = 0) -> Any:
    """Credential-scrub every string inside an STT detail payload.

    `detail` is not metadata: the proxy backend returns verbose_json, so `detail["segments"][i]`
    carries its own verbatim `text` - the SAME words as the `text` column (stt.py:243). Scrubbing
    only the column would store a redacted and an unredacted copy of one utterance side by side,
    and `GET /meetings/{id}/segments` hands `detail` straight back out (JSON_FIELDS includes it).
    Keys are left alone; only values are rewritten, so a later diarization pass still finds its
    timings where it left them.
    """
    if isinstance(detail, str):
        return redact.scrub_secrets(detail)
    if depth > 8:  # a pathological payload must not recurse the request thread to death
        return detail
    if isinstance(detail, list):
        return [_scrub_detail(v, depth + 1) for v in detail]
    if isinstance(detail, dict):
        return {k: _scrub_detail(v, depth + 1) for k, v in detail.items()}
    return detail


def _attendee_rows(raw: Any) -> list[dict[str, Any]]:
    """Normalise whatever the caller had into the attendees column's shape."""
    out: list[dict[str, Any]] = []
    for a in raw or []:
        if isinstance(a, str):
            out.append({"email": a, "name": "", "response": "", "organizer": False, "self": False})
        elif isinstance(a, dict):
            out.append({
                "email": str(a.get("email") or ""), "name": str(a.get("name") or ""),
                "response": str(a.get("response") or a.get("responseStatus") or ""),
                "organizer": bool(a.get("organizer")), "self": bool(a.get("self")),
            })
    return out[:60]


class MeetingBlocked(RuntimeError):
    """Preflight said no. Carries the capability rows so the UI can show what to fix.

    Start is blocked rather than warned about on purpose: `capabilities()` can tell you a model
    name is set, only the self-test can tell you anything answers, and recording an hour of audio
    that nothing will ever transcribe is worse than refusing to start.
    """

    def __init__(self, blockers: list[dict[str, Any]]):
        self.blockers = blockers
        first = (blockers[0]["label"] if blockers else "preflight failed")
        super().__init__(f"{first} - {len(blockers)} blocker(s)")


class Meetings:
    """Reads and writes the four meeting tables plus the FTS index. No threads, no processes."""

    def __init__(self, db: Database, config_fn: Callable[[], dict[str, Any]] | None = None):
        self.db = db
        self.config = config_fn or (lambda: config_for(db))
        # Set by MeetingService: the routes call this repo directly, so the recorder pool has to
        # be reachable from here or a delete would pull the wav directory out from under a live capture.
        self.before_destroy: Callable[[str, bool], None] | None = None
        with db.tx() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(meetings)").fetchall()}
            for col, ddl in ADDED_COLUMNS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE meetings ADD COLUMN {col} {ddl}")
            # After the ALTER loop: on an upgraded DB the column does not exist until it has run.
            c.execute("CREATE INDEX IF NOT EXISTS idx_meetings_doc ON meetings(doc_id)")

    # ---- indexing ----
    @staticmethod
    def _reindex(c: Any, meeting_id: str, title: str, notes: str, enhanced: str, transcript: str) -> None:
        """DELETE then INSERT, on the LIVE cursor so the index commits with the write that caused it."""
        c.execute("DELETE FROM meetings_fts WHERE meeting_id=?", (meeting_id,))
        c.execute("INSERT INTO meetings_fts(title, notes, enhanced, transcript, meeting_id) VALUES(?,?,?,?,?)",
                  (title, notes, enhanced, transcript, meeting_id))

    def _reindex_row(self, c: Any, meeting_id: str) -> None:
        r = c.execute("SELECT title, notes, enhanced, transcript FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if r:
            self._reindex(c, meeting_id, r["title"], r["notes"], r["enhanced"], r["transcript"])

    # ---- reads ----
    @staticmethod
    def _visible(c: Any, alias: str = "m") -> str:
        """SQL that hides a recording whose doc is in the trash (a trashed doc must not leak its audio text).

        `docs` belongs to Docs, so a bare Meetings(db) has no such table to join against.
        """
        have = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='docs'").fetchone()
        if not have:
            return "1=1"
        # A doc_id with no doc row is an orphan (a purge whose on_delete hook failed), hidden like a trashed one.
        return (f"({alias}.doc_id IS NULL OR EXISTS "
                f"(SELECT 1 FROM docs dd WHERE dd.id={alias}.doc_id AND dd.deleted_at IS NULL))")

    def list(self, project_id: str | None = "__all__", q: str = "", status: str = "",
             since_days: int = 0, limit: int = 100, doc_id: str | None = None,
             include_docs: bool = False) -> list[dict[str, Any]]:
        """Meetings without their bodies: notes, enhanced notes and transcripts all get long.

        Recordings made inside a doc are left out of the rail unless asked for: they are the doc's,
        not a meeting of their own. `doc_id` selects one doc's recordings.
        """
        where, args = [], []
        if doc_id:
            where.append("m.doc_id = ?")
            args.append(doc_id)
        elif not include_docs:
            where.append("m.doc_id IS NULL")
        if project_id != "__all__":
            if project_id is None:
                where.append("m.project_id IS NULL")
            else:
                where.append("m.project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("(m.title LIKE ? OR m.notes LIKE ? OR m.enhanced LIKE ?)")
            args += [f"%{q}%", f"%{q}%", f"%{q}%"]
        if status.strip():
            where.append("m.status = ?")
            args.append(status.strip())
        if since_days > 0:
            where.append("COALESCE(m.started_at, m.scheduled_start, m.created_at) >= ?")
            args.append(now() - int(since_days) * 86400)
        args.append(max(1, int(limit)))
        with self.db.tx() as c:
            where.append(self._visible(c))
            sql = (
                "SELECT m.*, "
                "  (SELECT COUNT(*) FROM meeting_segments s WHERE s.meeting_id=m.id) AS segment_count, "
                "  (SELECT COUNT(*) FROM meeting_revisions r WHERE r.meeting_id=m.id AND r.status='pending') AS pending "
                "FROM meetings m WHERE " + " AND ".join(where) +
                " ORDER BY COALESCE(m.started_at, m.scheduled_start, m.created_at) DESC LIMIT ?"
            )
            rows = c.execute(sql, args).fetchall()
        return [self._list_view(row_to_dict(r, JSON_FIELDS)) for r in rows]  # type: ignore[arg-type]

    def for_doc(self, doc_id: str) -> list[dict[str, Any]]:
        """One doc's recordings, newest first, each with where its proposed summary stands."""
        rows = self.list(doc_id=doc_id, limit=500)
        if not rows:
            return []
        with self.db.tx() as c:
            status_of = {r["id"]: r["status"] for r in c.execute(
                "SELECT id, status FROM doc_revisions WHERE doc_id=?", (doc_id,)).fetchall()} \
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='doc_revisions'").fetchone() else {}
        for m in rows:
            rid = m.get("summary_revision_id")
            m["summary_state"] = "none" if not rid else {
                "pending": "pending", "applied": "applied"}.get(status_of.get(rid, ""), "rejected")
        return rows

    @staticmethod
    def _list_view(d: dict[str, Any]) -> dict[str, Any]:
        notes = d.pop("notes", "") or ""
        enhanced = d.pop("enhanced", "") or ""
        d.pop("transcript", None)
        pending = d.pop("pending", 0)
        return {
            **d,
            "notes_preview": notes[:NOTES_PREVIEW_CHARS],
            # The body the list is counting is the one the UI shows: the enhanced notes once they
            # have been accepted, the user's own notes until then.
            "words": word_count(enhanced or notes),
            "segment_count": int(d.get("segment_count") or 0),
            "has_pending": bool(pending),
            "keep_audio": bool(d.get("keep_audio")),
            "attendee_count": len(d.get("attendees") or []),
        }

    def get(self, id: str, include_hidden: bool = True) -> dict[str, Any] | None:
        """One meeting. Internal lifecycle code (stop, the transcribe worker, recover, purge) keeps the
        default so it still reaches a recording whose doc was trashed mid-recording; every external read
        (routes, tools) passes include_hidden=False so such a row reads as missing, like `list` and `find`."""
        with self.db.tx() as c:
            if not include_hidden and c.execute(
                    f"SELECT 1 FROM meetings WHERE id=? AND NOT {self._visible(c, 'meetings')}", (id,)).fetchone():
                return None
            d = row_to_dict(c.execute(
                "SELECT m.*, "
                "  (SELECT COUNT(*) FROM meeting_segments s WHERE s.meeting_id=m.id) AS segment_count "
                "FROM meetings m WHERE m.id=?", (id,)).fetchone(), JSON_FIELDS)
            if not d:
                return None
            pending = row_to_dict(c.execute(
                "SELECT * FROM meeting_revisions WHERE meeting_id=? AND status='pending' "
                "ORDER BY created_at DESC LIMIT 1", (id,)).fetchone(), JSON_FIELDS)
            actions = [row_to_dict(r) for r in c.execute(
                "SELECT * FROM meeting_action_items WHERE meeting_id=? ORDER BY created_at", (id,)).fetchall()]
        d["words"] = word_count(d["enhanced"] or d["notes"])
        d["attendee_count"] = len(d["attendees"] or [])
        d["keep_audio"] = bool(d["keep_audio"])
        d["has_pending"] = pending is not None
        d["pending"] = self._rev_view(pending, d["enhanced"]) if pending else None
        d["actions"] = actions
        return d

    def is_hidden(self, id: str) -> bool:
        """True when the row exists but its doc is trashed or gone (see `_visible`)."""
        with self.db.tx() as c:
            return c.execute(f"SELECT 1 FROM meetings WHERE id=? AND NOT {self._visible(c, 'meetings')}",
                             (id,)).fetchone() is not None

    def find(self, name_or_id: str) -> dict[str, Any] | None:
        """Resolve what a model passed: an id, or a title (exact, then unique substring)."""
        key = (name_or_id or "").strip()
        if not key:
            return None
        with self.db.tx() as c:
            vis = self._visible(c, "meetings")
            r = c.execute(f"SELECT id FROM meetings WHERE (id=? OR lower(title)=lower(?)) AND {vis}",
                          (key, key)).fetchone()
            if not r:
                hits = c.execute(f"SELECT id FROM meetings WHERE title LIKE ? COLLATE NOCASE AND {vis} LIMIT 2",
                                 (f"%{key}%",)).fetchall()
                if len(hits) != 1:
                    return None
                r = hits[0]
        return self.get(r["id"])

    def by_event(self, calendar_event_id: str) -> dict[str, Any] | None:
        """The meeting already created for a calendar event, so the nudge is not offered twice."""
        if not calendar_event_id:
            return None
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM meetings WHERE calendar_event_id=?", (calendar_event_id,)).fetchone()
        return self.get(r["id"]) if r else None

    def unfinished(self) -> list[dict[str, Any]]:
        """Rows claiming to be mid-flight. A crash or a quit leaves these behind; recover() finalizes them."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id, status, started_at, ended_at, duration_ms FROM meetings "
                "WHERE status IN ('recording','transcribing','enhancing') ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        with self.db.tx() as c:
            total = int(c.execute("SELECT COUNT(*) FROM meetings").fetchone()[0])
            pend = int(c.execute("SELECT COUNT(*) FROM meeting_revisions WHERE status='pending'").fetchone()[0])
        return {"total": total, "pending": pend}

    def search(self, query: str, project_id: str | None = "__all__", limit: int = 10) -> list[dict[str, Any]]:
        """FTS over titles, notes, enhanced notes and transcripts, with a snippet around the hit."""
        q = (query or "").strip()
        if not q:
            return []
        match = fts_query(q) or f'"{q}"'
        want = max(1, int(limit))
        cols = (("title", 0), ("notes", 1), ("enhanced", 2), ("transcript", 3))
        # snippet() returns the HEAD of a column that contained no match rather than an empty
        # string, so empty open/close tags make "which field matched" undetectable - every hit
        # looks like a notes hit. The markers are sentinels, stripped before the snippet is
        # returned; they are control characters so they cannot occur in a transcript.
        snippets = ", ".join(
            f"snippet(meetings_fts, {i}, '{MARK}', '{MARK}', ' … ', 24) AS snip_{name}" for name, i in cols)
        with self.db.tx() as c:
            try:
                rows = c.execute(
                    f"SELECT f.meeting_id, {snippets}, bm25(meetings_fts) AS score "
                    "FROM meetings_fts f WHERE meetings_fts MATCH ? ORDER BY score LIMIT ?",
                    (match, want * 3)).fetchall()
            except Exception:  # noqa: BLE001 - malformed FTS expression; fall back to LIKE
                rows = c.execute(
                    "SELECT id AS meeting_id, title AS snip_title, substr(notes,1,200) AS snip_notes, "
                    "  substr(enhanced,1,200) AS snip_enhanced, substr(transcript,1,200) AS snip_transcript, "
                    "  0 AS score FROM meetings "
                    "WHERE notes LIKE ? OR enhanced LIKE ? OR transcript LIKE ? OR title LIKE ? LIMIT ?",
                    (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", want * 3)).fetchall()
            out = []
            for r in rows:
                m = c.execute("SELECT id, title, status, project_id, started_at, doc_id FROM meetings "
                              f"WHERE id=? AND {self._visible(c, 'meetings')}", (r["meeting_id"],)).fetchone()
                if not m:
                    continue
                if project_id != "__all__" and m["project_id"] != project_id:
                    continue
                # Which column actually matched matters to the reader: a hit in the notes is
                # something the user wrote, a hit in the transcript is something someone said.
                field, snippet = "notes", ""
                for name, _ in (("notes", 1), ("enhanced", 2), ("transcript", 3), ("title", 0)):
                    text = r[f"snip_{name}"] or ""
                    if MARK in text:
                        field, snippet = name, text.replace(MARK, "").strip()
                        break
                else:
                    snippet = (r["snip_notes"] or "").replace(MARK, "").strip()
                out.append({"meeting_id": m["id"], "title": m["title"], "status": m["status"],
                            "started_at": m["started_at"], "doc_id": m["doc_id"],
                            "snippet": snippet, "field": field,
                            "score": float(r["score"] or 0.0)})
                if len(out) >= want:
                    break
        return out

    # ---- writes ----
    def create(self, title: str = "", project_id: str | None = None, template: str = "general",
               calendar_event_id: str | None = None, calendar_id: str | None = None,
               calendar_link: str = "", conference_link: str = "", attendees: Any = None,
               scheduled_start: float | None = None, scheduled_end: float | None = None,
               status: str = "notes_only", doc_id: str | None = None,
               doc_mode: str | None = None) -> dict[str, Any]:
        mid = new_id()
        t = now()
        people = _attendee_rows(attendees)
        tpl = template if template in meeting_notes.TEMPLATES else "general"
        try:
            with self.db.tx() as c:
                c.execute(
                    "INSERT INTO meetings(id,project_id,title,status,template,scheduled_start,scheduled_end,"
                    " calendar_event_id,calendar_id,calendar_link,conference_link,attendees,created_at,updated_at,"
                    " doc_id,doc_mode)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, project_id, (title or "").strip()[:200], status, tpl, scheduled_start, scheduled_end,
                     calendar_event_id or None, calendar_id, calendar_link, conference_link,
                     json.dumps(people), t, t, doc_id or None,
                     (doc_mode if doc_mode in DOC_MODES else "record") if doc_id else None))
                self._reindex(c, mid, title, "", "", "")
        except sqlite3.IntegrityError:
            # The partial unique index on calendar_event_id is the point: the 45s nudge tries to
            # create the same meeting on every tick, and losing that race must hand back the row
            # that already exists rather than raise into the loop.
            existing = self.by_event(calendar_event_id or "")
            if existing:
                return existing
            raise
        return self.get(mid)  # type: ignore[return-value]

    def patch(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in (patch or {}).items() if k in PATCH_FIELDS}
        if not fields:
            return self.get(id)
        if "title" in fields:
            fields["title"] = str(fields["title"] or "").strip()[:200]
        if "template" in fields and fields["template"] not in meeting_notes.TEMPLATES:
            fields["template"] = "general"
        if "keep_audio" in fields:
            fields["keep_audio"] = int(bool(fields["keep_audio"]))
        if "error" in fields:
            fields["error"] = str(fields["error"] or "")[:1000]
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE meetings SET {sets} WHERE id=?", (*fields.values(), id))
            if INDEXED_FIELDS & set(fields):
                self._reindex_row(c, id)
        return self.get(id)

    def doc_link(self, id: str) -> tuple[str | None, str | None]:
        """(doc_id, doc_mode) of a meeting, without loading its bodies. (None, None) for an ordinary one."""
        with self.db.tx() as c:
            r = c.execute("SELECT doc_id, doc_mode FROM meetings WHERE id=?", (id,)).fetchone()
        return (r["doc_id"], r["doc_mode"]) if r else (None, None)

    def records_doc(self, doc_id: str) -> bool:
        """Whether any recording of this doc captured other people (`record` mode, not dictation)."""
        with self.db.tx() as c:
            return c.execute("SELECT 1 FROM meetings WHERE doc_id=? AND doc_mode='record' LIMIT 1",
                             (doc_id,)).fetchone() is not None

    def move_doc(self, doc_id: str, project_id: str | None) -> int:
        """The hook behind `Docs.on_move`: a doc's recordings follow it into its new project."""
        with self.db.tx() as c:
            return c.execute("UPDATE meetings SET project_id=? WHERE doc_id=?", (project_id, doc_id)).rowcount

    def purge_doc(self, doc_id: str) -> int:
        """Delete every recording linked to a doc: row, segments, FTS entry and audio directory.

        The hook behind `Docs.on_delete`. `doc_id` is plain TEXT with no FK, and the FTS row and the
        wavs would not be covered by a cascade anyway. Trashed docs are not purged, only hard deletes.
        """
        with self.db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM meetings WHERE doc_id=?", (doc_id,)).fetchall()]
        for mid in ids:
            self.delete(mid)
        return len(ids)

    def set_summary_revision(self, id: str, revision_id: str | None) -> None:
        """Which doc revision holds this recording's latest proposed summary. Service-only, not a PATCH field."""
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET summary_revision_id=?, updated_at=? WHERE id=?",
                      (revision_id, now(), id))

    def mark_started(self, id: str, audio_dir: str, sources: list[str],
                     started_at: float | None = None) -> dict[str, Any] | None:
        """What the recorder owns at start: the clock, the directory, and what is actually captured.

        Separate from `patch` so no PATCH body can claim a meeting recorded a channel it never
        opened - `sources` is the honest record of which inputs resolved, not which were asked for.
        """
        t = started_at if started_at is not None else now()
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET status='recording', started_at=?, audio_dir=?, sources=?, "
                      " ended_at=NULL, updated_at=? WHERE id=?",
                      (t, audio_dir, json.dumps([s for s in sources if s in SOURCES]), now(), id))
        return self.get(id)

    def mark_import(self, id: str, audio_dir: str, started_at: float) -> dict[str, Any] | None:
        """The import twin of mark_started: the file's clock, source 'import', transcribing until done."""
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET status='transcribing', started_at=?, audio_dir=?, sources=?, "
                      " ended_at=NULL, updated_at=? WHERE id=?",
                      (started_at, audio_dir, json.dumps(["import"]), now(), id))
        return self.get(id)

    def finalize(self, id: str, transcript: str, *, ended_at: float | None = None,
                 status: str = "ready", error: str = "") -> dict[str, Any] | None:
        """Close a meeting out: the rolled-up transcript, the duration, and one reindex."""
        cur = self.get(id)
        if not cur:
            return None
        end = ended_at if ended_at is not None else now()
        start = cur["started_at"]
        duration = int(max(0.0, end - start) * 1000) if start else int(cur["duration_ms"] or 0)
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET transcript=?, ended_at=?, duration_ms=?, status=?, error=?, "
                      " updated_at=? WHERE id=?",
                      (transcript, end, duration, status, str(error or "")[:1000], now(), id))
            self._reindex_row(c, id)
        return self.get(id)

    def delete(self, id: str) -> None:
        """Idempotent. Segments, revisions and action items CASCADE; the FTS row and the wavs don't."""
        if self.before_destroy is not None:
            with contextlib.suppress(Exception):
                self.before_destroy(id, True)
        with self.db.tx() as c:
            r = c.execute("SELECT audio_dir FROM meetings WHERE id=?", (id,)).fetchone()
            audio_dir = (r["audio_dir"] if r else "") or ""
            c.execute("DELETE FROM meetings WHERE id=?", (id,))
            # fts5 virtual tables are not reachable by ON DELETE CASCADE (docs.py:236-239).
            c.execute("DELETE FROM meetings_fts WHERE meeting_id=?", (id,))
        if audio_dir:
            shutil.rmtree(audio_dir, ignore_errors=True)

    # ---- segments ----
    def add_segment(self, meeting_id: str, channel: str, seq: int, t_start: float, t_end: float,
                    started_at: float, wav_path: str, wav_bytes: int, *, duration_ms: int = 0,
                    state: str = "recorded") -> dict[str, Any] | None:
        """The INSERT half of a segment: where the wav sits in the meeting, before any text exists.

        Upserted on (meeting_id, channel, seq) so a capture restart that reopens a seq cannot
        collide, and tiny on purpose: this runs on a capture thread while ffmpeg keeps writing.
        """
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO meeting_segments(id,meeting_id,channel,seq,t_start,t_end,started_at,"
                " duration_ms,state,wav_path,wav_bytes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(meeting_id, channel, seq) DO UPDATE SET"
                "  t_start=excluded.t_start, t_end=excluded.t_end, started_at=excluded.started_at,"
                "  duration_ms=excluded.duration_ms, state=excluded.state,"
                "  wav_path=excluded.wav_path, wav_bytes=excluded.wav_bytes,"
                # the wav was rewritten, so the old take's transcription is stale
                "  text='', detail='{}', error='', attempts=0",
                (new_id(), meeting_id, channel, int(seq), float(t_start), float(t_end), float(started_at),
                 int(duration_ms), state, wav_path, int(wav_bytes), t))
        return self.segment(meeting_id, channel, int(seq))

    def finish_segment(self, seg_id: str, text: str = "", detail: Any = None, backend: str = "",
                       error: str = "", state: str = "done", wav_path: str = "", wav_bytes: int = 0,
                       *, attempts: int = 0) -> dict[str, Any] | None:
        """The UPDATE half: the transcription, scrubbed, plus where the wav ended up."""
        body = str(text or "")
        payload = detail if detail is not None else {}
        if self.config().get("redactSecrets", True):
            # Credential rules ONLY, never activity.Gate.scrub: its identity rules replace every
            # address with [email] and every phone-shaped digit run with [phone]
            # (redact.py), which would erase attendee identity from inside the conversation.
            if body:
                body = redact.scrub_secrets(body)
            # The same words arrive twice - once as `text`, once per utterance inside `detail` -
            # so both copies go through the redactor or neither is redacted.
            payload = _scrub_detail(payload)
        with self.db.tx() as c:
            c.execute("UPDATE meeting_segments SET text=?, detail=?, backend=?, error=?, state=?, "
                      " attempts=?, wav_path=?, wav_bytes=? WHERE id=?",
                      (body, json.dumps(payload), backend,
                       str(error or "")[:1000], state, int(attempts), wav_path, int(wav_bytes), seg_id))
            # `cursor` is the rowid, as in `since`, so a pushed row can advance the same cursor a poll does.
            return row_to_dict(c.execute("SELECT rowid AS cursor, * FROM meeting_segments WHERE id=?",
                                         (seg_id,)).fetchone(), JSON_FIELDS)

    def segment(self, meeting_id: str, channel: str, seq: int) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute(
                "SELECT * FROM meeting_segments WHERE meeting_id=? AND channel=? AND seq=?",
                (meeting_id, channel, int(seq))).fetchone(), JSON_FIELDS)

    def segment_id(self, meeting_id: str, channel: str, seq: int) -> str:
        """The row id for a (channel, seq), since the recorder's callbacks only know those two."""
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM meeting_segments WHERE meeting_id=? AND channel=? AND seq=?",
                          (meeting_id, channel, int(seq))).fetchone()
        return r["id"] if r else ""

    def segments(self, meeting_id: str, offset: int = 0, limit: int = 200, channel: str = "") -> list[dict[str, Any]]:
        where, args = ["meeting_id = ?"], [meeting_id]
        if channel:
            where.append("channel = ?")
            args.append(channel)
        args += [max(1, int(limit)), max(0, int(offset))]
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM meeting_segments WHERE " + " AND ".join(where) +
                             " ORDER BY t_start, channel, seq LIMIT ? OFFSET ?", args).fetchall()
        return [row_to_dict(r, JSON_FIELDS) for r in rows]  # type: ignore[misc]

    def since(self, meeting_id: str, cursor: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """New segments only. rowid is the cursor: it is monotonic and needs no extra column."""
        with self.db.tx() as c:
            rows = c.execute("SELECT rowid AS cursor, * FROM meeting_segments WHERE meeting_id=? AND rowid > ? "
                             "ORDER BY rowid LIMIT ?", (meeting_id, int(cursor), max(1, int(limit)))).fetchall()
        return [row_to_dict(r, JSON_FIELDS) for r in rows]  # type: ignore[misc]

    def interrupt_pending(self, meeting_id: str) -> int:
        """Flip segments a crash left in recorded/transcribing to `failed`, when their wav survived.

        `retranscribe` only replays `failed` rows and the audio sweep only keeps wavs a failed row
        points at, so without this the untranscribed tail of a crashed meeting is deleted unheard.
        """
        with self.db.tx() as c:
            rows = c.execute("SELECT id, wav_path FROM meeting_segments WHERE meeting_id=? "
                             "AND state IN ('recorded','transcribing')", (meeting_id,)).fetchall()
            ids = [r["id"] for r in rows if r["wav_path"] and Path(r["wav_path"]).exists()]
            for sid in ids:
                c.execute("UPDATE meeting_segments SET state='failed', error=? WHERE id=?",
                          ("interrupted: the app quit before this segment was transcribed", sid))
        return len(ids)

    def pending_segments(self, meeting_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM meeting_segments WHERE state IN ('recorded','transcribing')"
        args: list[Any] = []
        if meeting_id:
            sql += " AND meeting_id=?"
            args.append(meeting_id)
        with self.db.tx() as c:
            return [row_to_dict(r, JSON_FIELDS) for r in c.execute(sql + " ORDER BY created_at", args).fetchall()]  # type: ignore[misc]

    def failed_segments(self, meeting_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM meeting_segments WHERE state='failed'"
        args: list[Any] = []
        if meeting_id:
            sql += " AND meeting_id=?"
            args.append(meeting_id)
        args.append(max(1, int(limit)))
        with self.db.tx() as c:
            return [row_to_dict(r, JSON_FIELDS) for r in c.execute(sql + " ORDER BY created_at LIMIT ?", args).fetchall()]  # type: ignore[misc]

    def segments_end(self, meeting_id: str) -> float:
        """Absolute epoch seconds of the last sample any segment of this meeting holds.

        The RECORDING clock, not the wall clock: it is what `recover()` has to close a crashed
        meeting out on, since the app may reopen a day after the audio stopped and `now()` would
        record the downtime as the length of the call. 0.0 when nothing was captured.
        """
        with self.db.tx() as c:
            r = c.execute(
                "SELECT MAX(started_at + CASE WHEN duration_ms > 0 THEN duration_ms / 1000.0 "
                "  WHEN t_end > t_start THEN t_end - t_start ELSE 0 END) AS e "
                "FROM meeting_segments WHERE meeting_id=?", (meeting_id,)).fetchone()
        return float(r["e"] or 0.0) if r and r["e"] is not None else 0.0

    def build_transcript(self, meeting_id: str) -> str:
        """Channels merged by offset, one line per run of consecutive segments on one channel.

        CALLED ONLY FROM finalize-time paths, never per segment. Rewriting a growing TEXT column
        and reindexing four FTS columns every 20 seconds for an hour - on the one WAL file the
        capture thread and the transcribe worker are already writing - is exactly the contention
        this design exists to avoid. Nobody searches a meeting that is still running.
        """
        with self.db.tx() as c:
            rows = c.execute("SELECT channel, t_start, text, detail FROM meeting_segments "
                             "WHERE meeting_id=? AND state='done' AND text <> '' "
                             "ORDER BY t_start, channel, seq", (meeting_id,)).fetchall()
            nm = c.execute("SELECT speaker_names FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        names = _names(nm["speaker_names"] if nm else "")
        # key is the channel for an ordinary segment and the speaker id for a diarized utterance, so
        # a transcript with no utterance speakers is built exactly as before.
        runs: list[tuple[str, float, list[str], str]] = []
        for r in rows:
            text = (r["text"] or "").strip()
            if not text:
                continue
            utts = _speaker_utterances(r["detail"]) if r["channel"] != "mic" else []
            if utts:
                for u in utts:
                    spk = str(u.get("speaker") or "")
                    key = f"spk:{spk}" if spk else r["channel"]
                    label = f"[{names.get(spk, spk)}]" if spk else CHANNEL_LABELS.get(r["channel"], "[them]")
                    utext = str(u.get("text") or "").strip()
                    if not utext:
                        continue
                    if runs and runs[-1][0] == key:
                        runs[-1][2].append(utext)
                    else:
                        runs.append((key, float(u.get("start") or r["t_start"]), [utext], label))
                continue
            if runs and runs[-1][0] == r["channel"]:
                runs[-1][2].append(text)
            else:
                runs.append((r["channel"], float(r["t_start"]), [text], CHANNEL_LABELS.get(r["channel"], "[them]")))
        return "\n".join(f"{_mmss(start)} {label} {' '.join(parts)}" for _k, start, parts, label in runs)

    # ---- speakers (diarization) ----
    def set_segment_speakers(self, seg_id: str, speaker: str, utterances: list[dict[str, Any]]) -> None:
        """Persist per-utterance speakers into detail and the dominant one into the speaker column."""
        with self.db.tx() as c:
            r = c.execute("SELECT detail FROM meeting_segments WHERE id=?", (seg_id,)).fetchone()
            if not r:
                return
            try:
                detail = json.loads(r["detail"] or "{}")
            except ValueError:
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            detail["utterances"] = utterances
            c.execute("UPDATE meeting_segments SET detail=?, speaker=? WHERE id=?",
                      (json.dumps(detail), speaker, seg_id))

    def speaker_ids(self, meeting_id: str) -> list[str]:
        """Diarized speaker ids present in this meeting, in order of first appearance."""
        seen: list[str] = []
        for s in self.segments(meeting_id, limit=100000):
            for u in _speaker_utterances(s.get("detail")):
                spk = str(u.get("speaker") or "")
                if spk and spk not in seen:
                    seen.append(spk)
        return seen

    def set_speaker_names(self, meeting_id: str, names: dict[str, Any]) -> dict[str, str]:
        """Merge display names for known speaker ids. Blank removes a name. ValueError on bad input."""
        known = set(self.speaker_ids(meeting_id))
        cur = self.get(meeting_id)
        if cur is None:
            raise LookupError(meeting_id)
        out = _names(cur.get("speaker_names"))
        for sid, raw in (names or {}).items():
            if sid not in known:
                raise ValueError(f"unknown speaker {sid!r}")
            name = str(raw or "").strip()
            if len(name) > MAX_SPEAKER_NAME:
                raise ValueError(f"speaker names are limited to {MAX_SPEAKER_NAME} characters")
            if name:
                out[sid] = name
            else:
                out.pop(sid, None)
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET speaker_names=?, updated_at=? WHERE id=?",
                      (json.dumps(out), now(), meeting_id))
        return out

    def _rev_view(self, r: dict[str, Any], current: str | None = None) -> dict[str, Any]:
        """A revision in DocRevision's field shape, so <DiffView> renders it with no adapter.

        DiffView is typed `revision: DocRevision` (DiffView.tsx:101), so `doc_id` carries the
        meeting id and `meeting_id` is the extra that marks which kind of row this is.
        """
        before, after = r["before"] or "", r["after"] or ""
        base = before if current is None else current
        return {
            "id": r["id"], "doc_id": r["meeting_id"], "meeting_id": r["meeting_id"],
            "before": before, "after": after,
            "title_before": r["title_before"], "title_after": r["title_after"],
            "summary": r["summary"], "author": r["author"], "tool": r["tool"],
            "status": r["status"], "created_at": r["created_at"], "resolved_at": r["resolved_at"],
            "stat": diff_stat(before, after),
            # A pending proposal is reviewed against the enhanced notes as they stand now, not as
            # they stood when it was generated - otherwise the diff lies after a hand-edit.
            "stale": current is not None and r["status"] == "pending" and current != before,
            "stat_vs_current": diff_stat(base, after) if current is not None else None,
            "template": r["template"], "model": r["model"], "degraded": bool(r["degraded"]),
            "decisions": r["decisions"] or [], "topics": r["topics"] or [],
        }

    def revisions(self, meeting_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = [row_to_dict(r, JSON_FIELDS) for r in c.execute(
                "SELECT * FROM meeting_revisions WHERE meeting_id=? ORDER BY created_at DESC LIMIT ?",
                (meeting_id, max(1, int(limit)))).fetchall()]
        return [self._rev_view(r) for r in rows]  # type: ignore[arg-type]

    def revision(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = row_to_dict(c.execute("SELECT * FROM meeting_revisions WHERE id=?", (rev_id,)).fetchone(),
                            JSON_FIELDS)
        return self._rev_view(r) if r else None  # type: ignore[arg-type]

    def last_applied(self, meeting_id: str) -> dict[str, Any] | None:
        """The newest revision that was actually applied - what `enhanced` should still equal."""
        with self.db.tx() as c:
            r = row_to_dict(c.execute(
                "SELECT * FROM meeting_revisions WHERE meeting_id=? AND status='applied' "
                "ORDER BY resolved_at DESC, created_at DESC LIMIT 1", (meeting_id,)).fetchone(), JSON_FIELDS)
        return self._rev_view(r) if r else None  # type: ignore[arg-type]

    def pending_count(self) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM meeting_revisions WHERE status='pending'").fetchone()[0])

    def propose(self, meeting_id: str, after: str, summary: str = "", tool: str | None = "meeting_enhance",
                template: str = "", model: str = "", degraded: bool = False,
                decisions: Any = None, topics: Any = None) -> dict[str, Any] | None:
        """Record the enhance pass's output. `enhanced` is untouched until `accept`."""
        cur = self.get(meeting_id)
        if cur is None:
            return None
        rid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO meeting_revisions(id,meeting_id,before,after,title_before,title_after,summary,"
                " author,tool,status,template,model,degraded,decisions,topics,created_at)"
                " VALUES(?,?,?,?,?,?,?,'assistant',?,'pending',?,?,?,?,?,?)",
                (rid, meeting_id, cur["enhanced"], after, cur["title"], None,
                 (summary or "Enhanced notes")[:300], tool, template or cur["template"], model,
                 int(bool(degraded)), json.dumps(list(decisions or [])), json.dumps(list(topics or [])), now()))
        return self.revision(rid)

    def accept(self, rev_id: str) -> dict[str, Any] | None:
        """Apply a proposal to `enhanced` - and to nothing else.

        `notes` has exactly one writer, the user, through `patch`. A model's output landing there
        is the failure mode this whole two-column split exists to prevent.
        """
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM meeting_revisions WHERE id=? AND status='pending'", (rev_id,)).fetchone()
            if not r:
                return None
            m = c.execute("SELECT * FROM meetings WHERE id=?", (r["meeting_id"],)).fetchone()
            if not m:
                return None
            t = now()
            # `before` is rewritten to what was actually replaced, so an undo after a stale accept
            # restores what the user had rather than what the model saw (docs.py:299-301).
            c.execute("UPDATE meeting_revisions SET status='applied', before=?, resolved_at=? WHERE id=?",
                      (m["enhanced"], t, rev_id))
            c.execute("UPDATE meeting_revisions SET status='superseded', resolved_at=? "
                      "WHERE meeting_id=? AND status='pending' AND id<>?", (t, r["meeting_id"], rev_id))
            c.execute("UPDATE meetings SET enhanced=?, updated_at=? WHERE id=?", (r["after"], t, r["meeting_id"]))
            self._reindex_row(c, r["meeting_id"])
            meeting_id = r["meeting_id"]
        return self.get(meeting_id)

    def reject(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT meeting_id FROM meeting_revisions WHERE id=? AND status='pending'",
                          (rev_id,)).fetchone()
            if not r:
                return None
            c.execute("UPDATE meeting_revisions SET status='rejected', resolved_at=? WHERE id=?", (now(), rev_id))
            meeting_id = r["meeting_id"]
        return self.get(meeting_id)

    # ---- action items ----
    def action_items(self, meeting_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM meeting_action_items WHERE meeting_id=? ORDER BY created_at", (meeting_id,)).fetchall()]

    @staticmethod
    def _item_key(text: str) -> str:
        """How two proposals of the same action item are recognised as one: words, case-folded."""
        return " ".join(str(text or "").split()).casefold()

    def add_action_items(self, meeting_id: str, revision_id: str | None,
                         items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Record this pass's action items, WITHOUT re-proposing ones the user already acted on.

        A second enhance pass of the same meeting returns much the same list, so an unconditional
        INSERT would re-propose an item the user dismissed - undoing the dismissal - and give an
        already-promoted item a duplicate row with a NULL todo_id, which `promote_action_item`'s
        per-row `todo_id` guard cannot see, so "Add to todos" would create the todo twice. An item
        that is already on the meeting therefore keeps its row, its status and its todo_id, and
        only has its `revision_id` re-pointed at the pass that proposed it again.
        """
        t = now()
        with self.db.tx() as c:
            seen = {self._item_key(r["text"]): r["id"] for r in c.execute(
                "SELECT id, text FROM meeting_action_items WHERE meeting_id=? ORDER BY created_at",
                (meeting_id,)).fetchall()}
            for it in items or []:
                # truncated FIRST, so the key matches the one the stored row will produce
                text = str((it or {}).get("text") or "").strip()[:500]
                if not text:
                    continue
                key = self._item_key(text)
                existing = seen.get(key)
                if existing:
                    c.execute("UPDATE meeting_action_items SET revision_id=? WHERE id=?",
                              (revision_id, existing))
                    continue
                rid = new_id()
                c.execute(
                    "INSERT INTO meeting_action_items(id,meeting_id,revision_id,text,owner,due,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (rid, meeting_id, revision_id, text, str(it.get("owner") or "")[:200],
                     str(it.get("due") or "")[:10], t))
                seen[key] = rid
        return self.action_items(meeting_id)

    def dismiss_action_item(self, item_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            c.execute("UPDATE meeting_action_items SET status='dismissed' WHERE id=?", (item_id,))
            return row_to_dict(c.execute("SELECT * FROM meeting_action_items WHERE id=?", (item_id,)).fetchone())

    def promote_action_item(self, item_id: str, todos: Any, project_id: str | None = None) -> dict[str, Any] | None:
        """Turn a proposed item into a real todo, once.

        Only `todos.create` is called. `todos.on_change = tasks_sync.poke` is already wired at
        app.py:218-220 and pushes the new task to Google within ~2s, so writing the remote side
        here too would create the task twice.

        The todo is created UNLINKED. `todos.external_id` is the Google Task id and nothing else:
        `TasksSync._merge` reads a non-empty `external_id` as "this todo mirrors a remote task",
        and deletes the todo - no tombstone, no notification - when that id is not in the remote
        list (gtasks.py:151-158). Putting the meeting id there made every promoted action item a
        todo whose remote task had apparently vanished, so the sync destroyed it on its next pass
        (and skipped it at gtasks.py:182-184, so it was never pushed either). With external_id
        left alone the sync treats it as a new local todo and creates the Google task. The link
        back to the meeting is `meeting_action_items.todo_id`, which is where it belongs.
        """
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM meeting_action_items WHERE id=?", (item_id,)).fetchone()
            if not r:
                return None
            item = dict(r)
            m = c.execute("SELECT title, project_id FROM meetings WHERE id=?", (item["meeting_id"],)).fetchone()
        if item["todo_id"]:
            return item
        title = (m["title"] if m else "") or "Untitled meeting"
        todo = todos.create(
            title=item["text"], project_id=project_id if project_id is not None else (m["project_id"] if m else None),
            notes=f"From meeting: {title}", due=_due_or_none(item["due"]), priority=2,
            source="meeting")
        with self.db.tx() as c:
            c.execute("UPDATE meeting_action_items SET status='added', todo_id=? WHERE id=?", (todo["id"], item_id))
            return row_to_dict(c.execute("SELECT * FROM meeting_action_items WHERE id=?", (item_id,)).fetchone())

    # ---- audio ----
    def audio_bytes(self, meeting_id: str) -> int:
        """Retained wav bytes, measured from disk and stored on the row.

        Measured rather than accumulated because the worker deletes a wav the instant its segment
        transcribes; this is also the recorder's `on_disk_check`, so it runs on the worker thread.
        """
        with self.db.tx() as c:
            r = c.execute("SELECT audio_dir FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if not r:
                return 0
            total = audiocap.dir_bytes(Path(r["audio_dir"])) if r["audio_dir"] else 0
            # Bookkeeping, so it does not bump updated_at (todos.set_sync_state does the same).
            c.execute("UPDATE meetings SET audio_bytes=? WHERE id=?", (total, meeting_id))
        return total

    def drop_done_audio(self, meeting_id: str) -> None:
        """Delete the wavs of segments that settled, keeping failed ones for Retranscribe."""
        for s in self.segments(meeting_id, limit=100000):
            if s["state"] in ("done", "empty") and s["wav_path"]:
                with contextlib.suppress(OSError):
                    Path(s["wav_path"]).unlink(missing_ok=True)
                with self.db.tx() as c:
                    c.execute("UPDATE meeting_segments SET wav_path='', wav_bytes=0 WHERE id=?", (s["id"],))

    def delete_audio(self, meeting_id: str) -> dict[str, Any] | None:
        m = self.get(meeting_id)
        if not m:
            return None
        if self.before_destroy is not None:
            with contextlib.suppress(Exception):
                self.before_destroy(meeting_id, False)
            m = self.get(meeting_id) or m
        if m["audio_dir"]:
            shutil.rmtree(m["audio_dir"], ignore_errors=True)
        with self.db.tx() as c:
            c.execute("UPDATE meetings SET audio_dir='', audio_bytes=0, updated_at=? WHERE id=?",
                      (now(), meeting_id))
            # The rows stay; only the audio is gone, so the UI can say why retranscribe is over.
            c.execute("UPDATE meeting_segments SET wav_path='', wav_bytes=0 WHERE meeting_id=?", (meeting_id,))
        return self.get(meeting_id)


class MeetingService:
    """Owns the recorder pool, the capability probes, the enhance pass and the 45s tick.

    Constructor shape mirrors `activity.Monitor(db, settings, llm.complete)` (app.py:240) so a
    test can inject a fake `complete`. One instance per app.
    """

    def __init__(self, db: Database, settings_fn: Callable[[], dict[str, Any]],
                 complete_fn: Callable[..., Any], meetings: Meetings,
                 google: Any = None, todos: Any = None, docs: Any = None,
                 publish: Callable[[dict[str, Any]], None] | None = None):
        self.db = db
        self.data_dir = db.data_dir
        self.settings = settings_fn
        self._complete = complete_fn
        self.meetings = meetings
        meetings.before_destroy = self._release_recorder
        self.google = google
        self.todos = todos
        # The doc store, for recordings made inside a doc (their summary is proposed into it).
        self.docs = docs
        # Called with a `recording` event dict from any thread; app.py hands it to the event loop.
        self.publish = publish
        # Called with a `preview` event dict from any thread; app.py hands it to the event loop.
        self.preview_publish: Callable[[dict[str, Any]], None] | None = None
        self.pool = meeting_recorder.RecorderPool(db.data_dir, settings_fn, self.config)
        self.last_error = ""
        self._preflight: tuple[float, dict[str, Any]] | None = None
        self._suggest: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._enhancing: set[str] = set()
        self._summarizing: set[str] = set()

    def _emit(self, kind: str, meeting_id: str, **extra: Any) -> None:
        """Tell the app a recording moved. Never raises: a dead listener must not cost a transcript."""
        if self.publish is None:
            return
        try:
            doc_id, doc_mode = self.meetings.doc_link(meeting_id)
            self.publish({"kind": kind, "meeting_id": meeting_id, "doc_id": doc_id, "doc_mode": doc_mode, **extra})
        except Exception as e:  # noqa: BLE001
            log.warning("meetings: event %s for %s not published: %s", kind, meeting_id, e)

    # ---- config ----
    def config(self) -> dict[str, Any]:
        return config_for(self.db)

    def set_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Persist a config change. It NEVER touches a live recording.

        The deliberate divergence from `Monitor.set_config`, which calls `restart()` whenever the
        monitor is running (activity.py:1004-1006): picking a different microphone or flipping
        `keepAudio` halfway through someone's call must not tear the capture down and lose the
        conversation. The new values are read fresh for the next segment and the next meeting.
        """
        cfg = _deep_merge(self.config(), patch or {})
        cfg["sources"] = [s for s in (cfg.get("sources") or []) if s in SOURCES] or ["mic"]
        if cfg.get("template") not in meeting_notes.TEMPLATES:
            cfg["template"] = "general"
        if str(cfg.get("sttBackend") or "") not in stt.BACKENDS:
            cfg["sttBackend"] = "auto"
        self.db.set_settings({"meetings": cfg})
        self._preflight = None  # the next Start re-probes rather than trusting a stale self-test
        return cfg

    def consent(self) -> dict[str, Any]:
        """Record the one-time acknowledgement. Recording stays blocked until this is set."""
        return self.set_config({"consentedAt": now()})

    # ---- capabilities ----
    def devices(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Audio inputs this machine can record, with loopback devices marked."""
        return [{"index": d["index"], "name": d["name"], "loopback": audiocap.looks_like_loopback(d["name"])}
                for d in audiocap.audio_devices(0.0 if refresh else 20.0)]

    def capabilities(self) -> list[dict[str, Any]]:
        """What this machine can record right now, and how to fix what it can't.

        Native capture does not need ffmpeg or a loopback driver. Those rows stay in the
        checklist as fallbacks: ffmpeg still remuxes a truncated wav, and BlackHole still
        covers a Mac too old for the process tap. `pyobjc` and `accessibility` are dropped
        because a recorder needs neither grant.
        """
        cfg = self.config()
        devices = audiocap.audio_devices()
        loopbacks = [d for d in devices if audiocap.looks_like_loopback(d["name"])]
        native_mic = native_audio.mic_available()
        native_out = native_audio.system_available()
        ff = audiocap.ffmpeg_path()
        capture_ok = native_mic or bool(ff)
        if native_mic:
            capture_detail = "AVAudioEngine records the microphone; ffmpeg is not required."
            if ff:
                capture_detail = f"AVAudioEngine (ffmpeg at {ff} is the fallback)."
        elif ff:
            capture_detail = f"ffmpeg at {ff}."
        else:
            capture_detail = "No native capture and ffmpeg is not on PATH."
        if native_out:
            loop_ok, loop_detail = True, "Core Audio process tap can record system audio without a loopback driver."
        elif loopbacks:
            loop_ok, loop_detail = True, f"Loopback device available: {loopbacks[0]['name']}."
        else:
            loop_ok, loop_detail = False, (
                "This macOS has no process tap and no loopback device, so a meeting records "
                "your side only.")
        mic_ok = bool(devices) or native_mic
        if devices:
            mic_detail = f"{len(devices)} audio input(s) visible."
        elif native_mic:
            mic_detail = "Default microphone (AVAudioEngine); pick a specific input if you want one."
        else:
            mic_detail = "No audio inputs found."
        return [
            {
                "id": "platform", "label": "Supported platform", "ok": audiocap.IS_MAC,
                "detail": f"Running on {sys.platform}.",
                "fix": "" if audiocap.IS_MAC else "Recording is macOS-only; notes and the rest of the app still work.",
            },
            {
                "id": "ffmpeg", "label": "Audio capture", "ok": capture_ok,
                "detail": capture_detail,
                "fix": "" if capture_ok else
                "Install pyobjc-framework-AVFoundation (`cd backend && uv pip install -e '.[activity]'`) "
                "or brew install ffmpeg.",
            },
            {
                "id": "mic", "label": "Microphone input", "ok": mic_ok,
                "detail": mic_detail,
                "fix": "" if mic_ok else "Grant Microphone permission to the app, then reopen this panel.",
            },
            {
                "id": "loopback", "label": "System audio capture", "ok": loop_ok,
                "detail": loop_detail,
                "fix": "" if loop_ok else
                "macOS 14.2+ has a process tap; on older systems install BlackHole "
                "(brew install blackhole-2ch) and pick it as the output device below.",
            },
            *stt.capabilities(cfg, self.data_dir),
            diarize.capabilities(cfg, self.data_dir),
        ]

    def status(self) -> dict[str, Any]:
        """Cheap enough to poll every second or two.

        Unlike /activity/status this never probes twice per tick: the device list comes from
        audiocap's 20s TTL cache, and `upcoming` is served from the last `suggest()` result rather
        than calling Google, so a status poll makes no network request at all.
        """
        cfg = self.config()
        session = self.pool.live()
        active = None
        if session is not None:
            m = self.meetings.get(session.meeting_id) or {}
            s = session.stats()
            errors = session.errors()
            active = {
                "meeting_id": session.meeting_id,
                "status": m.get("status") or "recording",
                "started_at": session.started_at,
                "elapsed_ms": s["elapsed_ms"],
                "segments_done": s["segments_done"],
                "segments_pending": s["segments_pending"],
                "queued": s["queued"],
                "paused": s["paused"],
                "channels": s["channels"],
                "doc_id": m.get("doc_id"),
                "doc_mode": m.get("doc_mode"),
                "segment_seconds": int(getattr(session, "segment_seconds", 0) or cfg["segmentSeconds"]),
                "error": m.get("error") or "; ".join(f"{k}: {v}" for k, v in errors.items()),
            }
        row = next((r for r in stt.capabilities(cfg, self.data_dir) if r["id"] == "stt"), None) or {}
        return {
            "enabled": bool(cfg["enabled"]),
            "consented": float(cfg.get("consentedAt") or 0) > 0,
            "config": cfg,
            "active": active,
            "upcoming": self._suggest[1],
            "devices": self.devices(),
            "stt": {"backend": stt.resolve_backend(cfg, self.data_dir),
                    "ok": bool(row.get("ok")), "detail": str(row.get("detail") or "")},
            "counts": self.meetings.counts(),
        }

    def preflight(self, force: bool = False) -> dict[str, Any]:
        """Capabilities plus a real round trip, cached for ten minutes.

        The self-test writes a silent wav and POSTs it, which costs an ffmpeg run and an HTTP
        request with a 120s timeout, so it is not something a panel can poll. `ok` false blocks
        Start rather than warning.
        """
        if not force and self._preflight and 0 <= now() - self._preflight[0] < PREFLIGHT_TTL:
            return self._preflight[1]
        cfg = self.config()
        caps = self.capabilities()
        blockers = [dict(r) for r in caps if not r["ok"] and r["id"] in BLOCKING_CAPABILITIES]
        if float(cfg.get("consentedAt") or 0) <= 0:
            blockers.insert(0, {
                "id": "consent", "label": "Recording consent", "ok": False,
                "detail": "Nobody has acknowledged that the people on the call will be told.",
                "fix": "Open Meetings settings and accept the recording notice once.",
            })
        if not cfg["enabled"]:
            # The master switch has to block the START PATH, not just the 45s tick: its help text
            # reads "Off means no capture at all", and a user who records once (stamping
            # consentedAt) and then turns the recorder off has asked for exactly that. It goes
            # first so the 409's message names the switch rather than a capability below it.
            blockers.insert(0, {
                "id": "enabled", "label": "Meeting recorder", "ok": False,
                "detail": "The meeting recorder is switched off, so nothing is captured.",
                "fix": "Turn Meeting recorder on in Meetings settings.",
            })
        test = stt.selftest(settings=self.settings(), cfg=cfg, data_dir=self.data_dir)
        if not test["ok"]:
            blockers.append({
                "id": "selftest", "label": "Transcription self-test", "ok": False,
                "detail": f"A silent test wav went to the {test['backend']} backend and came back: {test['error']}",
                "fix": "Fix the transcription route above, then run the self-test again. Recording an hour "
                       "of audio nothing can transcribe is worse than not starting.",
            })
        out = {"ok": not blockers, "blockers": blockers, "capabilities": caps, "selftest": test}
        self._preflight = (now(), out)
        return out

    # ---- lifecycle ----
    def start(self, meeting_id: str, segment_seconds: int | None = None,
              sources: list[str] | None = None) -> dict[str, Any] | None:
        """Open the configured channels and start capturing. Raises MeetingBlocked if preflight fails.

        A recording made inside a doc runs on its own segment length (its transcript is on screen,
        so a 20 s lag is too long) without touching the user's meeting setting, and dictation only
        ever listens to the microphone: it types what the user says, never what the room says.
        """
        m = self.meetings.get(meeting_id)
        if m is None:
            return None
        # A meeting records once. Restarting a finished one would continue the segment numbering
        # from the first run (meeting_recorder seeds `_next` off the wavs already on disk, so a new
        # ffmpeg cannot reopen a stored row's file) while `mark_started` resets `started_at` - so
        # `t_start = seq * segment_seconds` would sit a whole first run in the future and the rolled
        # up duration would overstate by that much. The UI already only offers Record on a
        # 'scheduled' meeting; this closes the raw route behind it.
        if m["status"] not in ("scheduled", "notes_only"):
            raise MeetingBlocked([{
                "id": "already_recorded", "label": "Already recorded", "ok": False,
                "detail": f"This meeting is {m['status']}, so it has recorded once already.",
                "fix": "Start a new meeting instead - a second run would renumber this one's audio.",
            }])
        cfg = self.config()
        pf = self.preflight()
        if not pf["ok"]:
            raise MeetingBlocked(pf["blockers"])

        want = [s for s in ((sources if sources is not None else cfg["sources"]) or []) if s in SOURCES] or ["mic"]
        seg_seconds = segment_seconds
        # Only a doc recording cuts at pauses: its transcript is on screen while you talk, and a clip
        # that ends mid-word reads as a typo there. A meeting keeps fixed clips and their fixed clock.
        cut_on_silence = bool(m.get("doc_id"))
        if m.get("doc_id"):
            if m.get("doc_mode") == "dictate":
                want = ["mic"]
                seg_seconds = seg_seconds or int(cfg["dictationSegmentSeconds"])
            else:
                seg_seconds = seg_seconds or int(cfg["docSegmentSeconds"])
        seg_seconds = int(seg_seconds or cfg["segmentSeconds"])
        channels: dict[str, list[str]] = {}
        dropped: dict[str, str] = {}
        for source in want:
            if source == "output" and native_audio.system_available():
                channels["output"] = audiocap.native_output_input()
                continue
            if source == "mic" and native_audio.mic_available():
                uid, note = audiocap.resolve_device(
                    str(cfg.get("micDevice") or ""), str(cfg.get("micDeviceName") or ""))
                if note.startswith("no input named"):
                    dropped["mic"] = note
                    continue
                # Empty uid is the default input: resolve_device refuses an unset index, which
                # is right for ffmpeg (a bare `:0` after a reshuffle records the wrong room)
                # and wrong for AVAudioEngine, which already has a default.
                if not uid and (cfg.get("micDevice") or cfg.get("micDeviceName")):
                    dropped["mic"] = note or "no device chosen yet"
                    continue
                channels["mic"] = audiocap.native_mic_input(uid)
                continue
            key = "micDevice" if source == "mic" else "outputDevice"
            index, note = audiocap.resolve_device(str(cfg.get(key) or ""), str(cfg.get(key + "Name") or ""))
            # resolve_device falls back to the stored index when the NAME no longer matches, which
            # is how a reshuffled device list ends up recording the wrong input. Refuse that here:
            # a meeting that did not record is recoverable, one that recorded the wrong room is not.
            if not index or note.startswith("no input named"):
                dropped[source] = note or "no device chosen yet"
                continue
            channels[source] = audiocap.device_input(index)
        if "mic" in want and "mic" in dropped:
            raise MeetingBlocked([{
                "id": "mic_device", "label": "Microphone", "ok": False,
                "detail": dropped["mic"],
                "fix": "Pick your microphone again in Meetings settings; the input list changed.",
            }])
        if not channels:
            raise MeetingBlocked([{
                "id": "mic_device", "label": "Audio input", "ok": False,
                "detail": "; ".join(f"{k}: {v}" for k, v in dropped.items()) or "no channels configured",
                "fix": "Pick an audio input in Meetings settings.",
            }])

        preview = (stt_stream.make_engine(meeting_id, cfg, self.preview_publish)
                   if m.get("doc_mode") == "dictate" and "mic" in channels else None)
        session = self.pool.start(
            meeting_id, channels, preview=preview,
            on_segment=lambda ch, seq, path, info: self._on_segment(meeting_id, ch, seq, path, info),
            on_result=lambda ch, seq, path, res: self._on_result(meeting_id, ch, seq, path, res),
            segment_seconds=seg_seconds,
            cut_on_silence=cut_on_silence,
            max_seconds=int(cfg["maxMeetingSeconds"]),
            keep_audio=bool(cfg["keepAudio"]),
            max_audio_bytes=int(cfg["maxAudioBytes"]),
            drain_seconds=float(cfg["drainSeconds"]),
            on_disk_check=lambda: self.meetings.audio_bytes(meeting_id),
        )
        self.meetings.mark_started(meeting_id, str(session.out_dir), list(channels), session.started_at)
        # A missing loopback device is a degradation the user has to be able to see, not an error.
        self.meetings.patch(meeting_id, {"error": "; ".join(
            f"{k} not captured: {v}" for k, v in dropped.items())[:1000]})
        log.info("meetings: recording %s (%s)", meeting_id, ", ".join(channels) or "no channels")
        self._emit("status", meeting_id, status="recording")
        return self.meetings.get(meeting_id)

    def _release_recorder(self, meeting_id: str, deleting: bool) -> None:
        """Stop a live capture (no drain) before its meeting or its audio is removed.

        Runs on whatever thread the delete came in on. A meeting whose audio alone is going is
        closed out as `ready` so it does not sit in `recording` with no recorder behind it.
        """
        if self.pool.get(meeting_id) is None:
            return
        self.pool.stop(meeting_id, 0)
        if not deleting:
            self.meetings.finalize(meeting_id, self.meetings.build_transcript(meeting_id), status="ready",
                                   error="Recording stopped because its audio was deleted.")

    def pause(self, meeting_id: str) -> dict[str, Any] | None:
        """Keep ffmpeg running and throw the audio away, so segment numbering stays monotonic."""
        session = self.pool.get(meeting_id)
        if session is None:
            return None
        session.pause(True)
        return self.status()

    def resume(self, meeting_id: str) -> dict[str, Any] | None:
        session = self.pool.get(meeting_id)
        if session is None:
            return None
        session.pause(False)
        return self.status()

    async def stop(self, meeting_id: str) -> dict[str, Any] | None:
        """Captures down, queue drained, transcript rolled up - then enhance is QUEUED, not awaited.

        The drain blocks for as long as the transcription backlog takes (`drainSeconds`), so it
        runs in a thread; the enhance pass is a separate LLM round trip and must not hold the stop
        request open for it.

        The drain can also GIVE UP: `RecorderPool.stop` reports `{"drained": bool, "pending": int,
        "stats": {...}}`, and `drained` is false when the deadline passed or the worker thread was
        still inside an STT call when it was abandoned. `transcript` has exactly one writer, so
        rolling it up here would freeze a partial segment set - the last minutes of the meeting,
        where the decisions are - out of the column, the FTS index and the enhance pass. When the
        drain did not finish, the banner says so and a watcher re-rolls the transcript once the
        abandoned worker settles, with enhance waiting for it rather than reading a hole.
        """
        m = self.meetings.get(meeting_id)
        if m is None:
            # Deleted while recording: the captures and the microphone still need releasing.
            if self.pool.get(meeting_id) is not None:
                await asyncio.to_thread(self.pool.stop, meeting_id, 0)
            return None
        if self.pool.get(meeting_id) is None and m["status"] not in ("recording", "transcribing"):
            # Already finished (double click, or auto-stop racing the user): finalize would
            # restamp ended_at to now and inflate the duration, or turn a scheduled row into ready.
            return m
        cfg = self.config()
        drained, pending = True, 0
        if self.pool.get(meeting_id) is not None:
            self.meetings.patch(meeting_id, {"status": "transcribing"})
            res = await asyncio.to_thread(self.pool.stop, meeting_id) or {}
            # Absent keys mean a pool that predates the contract; the old behaviour was to assume
            # the drain finished, so that is what a missing `drained` still means.
            drained = bool(res.get("drained", True))
            pending = max(0, int(res.get("pending") or 0))
        # Re-read: the drain ran for up to `drainSeconds`, during which _on_result wrote segment
        # text and possibly an error banner. `m` is a snapshot from before all of that.
        m = self.meetings.get(meeting_id) or m
        transcript = self.meetings.build_transcript(meeting_id)
        failed = len(self.meetings.failed_segments(meeting_id))
        # Whatever the recorder reported stays: the banner about the transcript is ADDED to it,
        # not substituted for it, so a channel that died mid-call is still on the row.
        notes = [n for n in [(m.get("error") or "").strip()] if n]
        if failed and not transcript:
            notes.append(f"{failed} segment(s) could not be transcribed, so there is no transcript. "
                         "The notes are untouched - fix the transcription route and retry.")
        elif not drained:
            notes.append(f"{pending or 'Some'} segment(s) were still transcribing when this meeting "
                         "was closed, so the transcript is incomplete. It fills in as they finish.")
        out = self.meetings.finalize(meeting_id, transcript, status="ready", error="; ".join(notes))
        self._emit("status", meeting_id, status="ready")
        if drained and cfg.get("diarize") and cfg.get("keepAudio"):
            # Only retained audio can be diarized; a missing backend is a quiet no-op.
            with contextlib.suppress(Exception):
                await self.diarize(meeting_id)
                out = self.meetings.get(meeting_id) or out
        if not drained:
            # Not fire-and-forget on purpose: enhance runs INSIDE the watcher, after the rebuild,
            # so the pass is not fed the hole the abandoned worker left behind.
            asyncio.ensure_future(self._settle_after_drain(meeting_id, bool(cfg["enhanceOnStop"])))
        elif cfg["enhanceOnStop"]:
            asyncio.ensure_future(self._enhance_quietly(meeting_id))
        return out

    async def _settle_after_drain(self, meeting_id: str, enhance: bool,
                                  deadline_seconds: float = DRAIN_WATCH_SECONDS) -> None:
        """Wait out the worker the drain abandoned, re-roll the transcript, then enhance.

        The session is already popped from the pool by the time `stop` returns, so there is no
        thread left to join: the segment rows are the only observable. A segment the worker
        settles after the meeting closed lands `state='done'` with real text, which `retranscribe`
        never revisits (it reads `state='failed'`), so without this the text exists in
        meeting_segments and nowhere else.
        """
        try:
            end = now() + max(0.0, float(deadline_seconds))
            while now() < end and self.meetings.pending_segments(meeting_id):
                await asyncio.sleep(DRAIN_POLL_SECONDS)
            await asyncio.to_thread(self._settle_transcript, meeting_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a background watcher has nobody to raise to
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("meetings: late drain of %s: %s", meeting_id, e)
        if enhance:
            await self._enhance_quietly(meeting_id)

    def _settle_transcript(self, meeting_id: str) -> dict[str, Any] | None:
        """Re-roll a CLOSED meeting's transcript from whatever its segments now say.

        `transcript` has exactly one writer, finalize(), so text that arrives after a meeting was
        closed - a replayed failed segment, or one the drain gave up waiting for - only reaches the
        rolled-up column and the FTS index by closing the meeting out again on its own clock.
        `ended_at` and `status` are carried over so a rebuild cannot restate the length of the
        meeting, and only the transcript's OWN banners are cleared: an enhance failure is still
        true after a segment replays.
        """
        m = self.meetings.get(meeting_id)
        if m is None or m["status"] in ("recording", "transcribing"):
            return None
        text = self.meetings.build_transcript(meeting_id)
        unsettled = bool(self.meetings.failed_segments(meeting_id)) or \
            bool(self.meetings.pending_segments(meeting_id))
        error = m.get("error") or ""
        if text and not unsettled:
            # Only the transcript's own banners go, one "; "-joined clause at a time: a mic that
            # died or an enhance pass that failed is still true now that a segment has settled.
            error = "; ".join(p for p in error.split("; ")
                              if not any(marker in p for marker in TRANSCRIPT_BANNERS))
        if text == (m.get("transcript") or "") and error == (m.get("error") or ""):
            return m  # nothing changed, so no rewrite and no reindex
        return self.meetings.finalize(meeting_id, text, ended_at=self._closing_clock(m),
                                      status=m["status"] or "ready", error=error)

    def _closing_clock(self, m: dict[str, Any]) -> float | None:
        """When a meeting ENDED, by the recording clock rather than the clock at this moment.

        `finalize` defaults `ended_at` to `now()` and recomputes `duration_ms` from it, which is
        right when a user presses Stop and wrong everywhere else: a rebuild hours later, or a
        `recover()` the morning after a crash, would otherwise record the downtime as the length
        of the call. An `ended_at` the row already has wins; failing that, the last sample any
        segment holds; failing that, the start, i.e. a meeting that captured nothing.
        """
        if m.get("ended_at"):
            return float(m["ended_at"])
        start = float(m["started_at"] or 0.0)
        end = self.meetings.segments_end(m["id"])
        if end > 0:
            return max(end, start)
        return start or None

    async def _enhance_quietly(self, meeting_id: str) -> None:
        """What happens after a recording closes: the enhance pass, or for a doc recording its own pass.

        One dispatch point, because stop, the late-drain watcher and an audio import all end here.
        A doc recording in `record` mode gets its summary PROPOSED into the doc; dictation gets
        nothing, the words were already typed.
        """
        try:
            doc_id, doc_mode = self.meetings.doc_link(meeting_id)
            if doc_id:
                if doc_mode != "dictate":
                    await self.summarize_into_doc(meeting_id)
                return
            await self.enhance(meeting_id)
        except Exception as e:  # noqa: BLE001 - a queued pass has nobody to raise to
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("meetings: enhance %s: %s", meeting_id, e)

    # ---- the enhance pass ----
    async def enhance(self, meeting_id: str, force: bool = False,
                      template: str | None = None) -> dict[str, Any] | None:
        """Cache-or-generate, like /recap (app.py:1881-1907): an existing proposal is the answer.

        AUTO-APPLY RULE: the revision is accepted for the user only when the pass SUCCEEDED and
        `enhanced` is empty, or still byte-identical to the last applied revision's `after` - i.e.
        they have not hand-edited it. That is what keeps Granola's "the notes just appear" feel
        without ever overwriting a human edit; once they have touched it, the proposal waits for
        accept or reject.

        A DEGRADED pass is never auto-applied. Its markdown is `meeting_notes._mechanical`'s
        fallback - the user's notes followed by the raw transcript - and `enhanced` is read by
        `context_block`, so auto-accepting it would paste other people's verbatim speech into the
        system prompt of every unrelated chat. A failed pass is something to show the user and
        offer again, not something to apply on their behalf; the route already returns
        `degraded: true` and the review screen already renders the warning (MeetingsView.tsx:288).
        """
        m = self.meetings.get(meeting_id)
        if m is None or m.get("doc_id"):
            # A doc recording has no notes of its own (the doc is the notes); see summarize_into_doc.
            return None
        if not force:
            if m["pending"]:
                return m["pending"]
            # Auto-apply leaves nothing pending, so without this a second enhance of an unchanged
            # meeting would pay for the same answer again. Same cache-or-generate as /recap.
            last = self.meetings.last_applied(meeting_id)
            if last is not None and m["enhanced"] and m["enhanced"] == last["after"]:
                return last
        if meeting_id in self._enhancing:
            # A pass is already in flight; hand back whatever exists rather than paying twice.
            return m["pending"] or self.meetings.last_applied(meeting_id)
        cfg = self.config()
        tpl = template or m["template"] or cfg["template"]
        if tpl not in meeting_notes.TEMPLATES:
            tpl = "general"
        settings = self.settings()
        model = meeting_notes.pick_model(cfg, settings)
        self._enhancing.add(meeting_id)
        prior_status = m["status"]
        try:
            self.meetings.patch(meeting_id, {"status": "enhancing"})
            res = await meeting_notes.enhance(
                complete_fn=self._complete, settings=settings, model=model, meeting=m,
                notes=m["notes"], transcript=m["transcript"], template=tpl,
                max_transcript_chars=int(cfg["maxTranscriptChars"]))
        finally:
            self._enhancing.discard(meeting_id)
        rev = self.meetings.propose(
            meeting_id, res["markdown"], summary=res["headline"], tool="meeting_enhance",
            template=tpl, model=res["model"], degraded=res["degraded"],
            decisions=res["decisions"], topics=res["topics"])
        if rev is None:
            return None
        if res["action_items"]:
            self.meetings.add_action_items(meeting_id, rev["id"], res["action_items"])
        patch: dict[str, Any] = {"status": "ready" if prior_status == "enhancing" else prior_status}
        if res["error"]:
            # Only ever SET the banner here. A successful pass must not clear the "N segments could
            # not be transcribed" message `stop` left behind - that failure is still true.
            patch["error"] = res["error"]
        if res["headline"]:
            patch["summary"] = res["headline"]
        self.meetings.patch(meeting_id, patch)
        last = self.meetings.last_applied(meeting_id)
        untouched = not m["enhanced"].strip() or (last is not None and m["enhanced"] == last["after"])
        if untouched and not res["degraded"]:
            self.meetings.accept(rev["id"])
            return self.meetings.revision(rev["id"])
        return rev

    # ---- the doc summary pass ----
    def _note(self, meeting_id: str, clause: str = "", clear: tuple[str, ...] = ()) -> None:
        """Add one "; "-joined clause to the banner, dropping earlier clauses that start with a `clear` marker."""
        m = self.meetings.get(meeting_id)
        if m is None:
            return
        parts = [p for p in (m.get("error") or "").split("; ")
                 if p and not any(p.startswith(c) for c in clear) and p != clause]
        if clause:
            parts.append(clause)
        self.meetings.patch(meeting_id, {"error": "; ".join(parts)})

    async def summarize_into_doc(self, meeting_id: str, *, template: str | None = None,
                                 focus: str = "", force: bool = False) -> dict[str, Any]:
        """Turn a doc recording's transcript into a section PROPOSED for the doc.

        Returns {"meeting", "revision", "error"}; never raises for a model or doc failure.

        NEVER auto-applied, whatever `docEditMode` says: the section is other people's speech put
        through a model, and doc tools are not tainted, so once it is doc text it reaches every
        chat as the user's own. It lands as a pending append revision the user accepts or rejects.
        On a model failure there is no revision at all, and the raw transcript is not pasted
        anywhere (the meeting enhance pass's degraded fallback does exactly that, which is why it
        is never auto-applied either). The transcript stays in `meetings`, one click away.
        """
        m = self.meetings.get(meeting_id)
        out: dict[str, Any] = {"meeting": m, "revision": None, "error": None}
        if m is None or not m.get("doc_id"):
            out["error"] = "That is not a recording of a doc."
            return out
        doc_id = m["doc_id"]
        doc = self.docs.get(doc_id) if self.docs is not None else None
        if doc is None:
            out["error"] = "The doc this was recorded in is gone or in the trash."
            self._emit("summary", meeting_id, revision_id=None, error=out["error"])
            return out
        if not force and not template and not focus and m.get("summary_revision_id"):
            prev = self.docs.revision(m["summary_revision_id"])
            if prev is not None and prev["status"] == "pending":
                out["revision"] = prev
                return out
        if not (m.get("transcript") or "").strip():
            out["error"] = "Nothing was said in this recording, so there is nothing to summarize."
            self._note(meeting_id, "Nothing was said", clear=("Nothing was said", "Summary failed"))
            self._emit("summary", meeting_id, revision_id=None, error=out["error"])
            out["meeting"] = self.meetings.get(meeting_id)
            return out
        if meeting_id in self._summarizing:
            out["error"] = "A summary is already being written for this recording."
            return out
        cfg = self.config()
        tpl = template or m["template"] or cfg["template"]
        if tpl not in meeting_notes.TEMPLATES:
            tpl = "general"
        settings = self.settings()
        model = meeting_notes.pick_model(cfg, settings)
        self._summarizing.add(meeting_id)
        prior_status = m["status"]
        try:
            self.meetings.patch(meeting_id, {"status": "enhancing"})
            res = await meeting_notes.summarize_recording(
                complete_fn=self._complete, settings=settings, model=model, meeting=m,
                doc_title=doc["title"], doc_content=doc["content"], transcript=m["transcript"],
                template=tpl, focus=focus, max_transcript_chars=int(cfg["maxTranscriptChars"]))
        finally:
            self._summarizing.discard(meeting_id)
            self.meetings.patch(meeting_id, {"status": "ready" if prior_status == "enhancing" else prior_status})
        if res["error"] or not res["markdown"]:
            out["error"] = f"Summary failed: {res['error'] or 'the model returned nothing'}"[:300]
            self._note(meeting_id, out["error"], clear=("Summary failed", "Nothing was said"))
            self._emit("summary", meeting_id, revision_id=None, error=out["error"])
            out["meeting"] = self.meetings.get(meeting_id)
            return out
        body, headline = res["markdown"], res["headline"]
        items = res["action_items"]
        if cfg.get("redactSecrets", True):
            # The model can echo a credential the transcript scrubber missed in a paraphrase; the
            # same credential-only rules as finish_segment, never the identity ones.
            body = redact.scrub_secrets(body)
            headline = redact.scrub_secrets(headline)
            items = [{**it, "text": redact.scrub_secrets(it["text"])} for it in items]
        # Stored escaped too, so the Summary tab and the doc render the same thing.
        body = meeting_notes.escape_currency(body)
        section = meeting_notes.wrap_ai(f"{meeting_notes.section_heading(m)}\n\n{body.strip()}")
        # The doc may have been trashed while the model was thinking; propose_append says so with None.
        rev = self.docs.propose_append(doc_id, section, summary="Recording summary", tool="recording_summary")
        if rev is None:
            out["error"] = "The doc this was recorded in is gone or in the trash."
            self._emit("summary", meeting_id, revision_id=None, error=out["error"])
            return out
        prev_id = m.get("summary_revision_id")
        if prev_id and prev_id != rev["id"]:
            prev = self.docs.revision(prev_id)
            if prev is not None and prev["status"] == "pending":
                self.docs.reject(prev_id)  # superseded: the doc never stacks two summaries of one recording
        self.meetings.set_summary_revision(meeting_id, rev["id"])
        if items:
            self.meetings.add_action_items(meeting_id, None, items)
        patch: dict[str, Any] = {"enhanced": body}
        if headline:
            patch["summary"] = headline
        self.meetings.patch(meeting_id, patch)
        self._note(meeting_id, clear=("Summary failed", "Nothing was said"))
        out["revision"] = self.docs.revision(rev["id"])
        out["meeting"] = self.meetings.get(meeting_id)
        self._emit("summary", meeting_id, revision_id=rev["id"], error=None)
        return out

    # ---- retranscription ----
    def retranscribe(self, meeting_id: str = "", limit: int = RETRANSCRIBE_PER_TICK) -> int:
        """Replay failed segments whose wav is still on disk. Returns how many were settled.

        Blocking (one HTTP request per segment), so callers run it in a thread. A segment that has
        already burned RETRANSCRIBE_MAX_ATTEMPTS is left alone: with no transcription route at all
        every segment fails forever, and retrying them on every tick would be a busy loop against
        the proxy.

        Every meeting it touched is then closed out again through `_settle_transcript`: replayed
        text reaches `meetings.transcript` and the FTS index only through finalize(), and the 45s
        tick is a caller with nobody to do that for it. Doing it here rather than in the HTTP route
        also closes the hole where the tick repaired the segments, the manual retranscribe then
        found nothing left to settle, and no code path was left that would ever rebuild the column.
        """
        cfg = self.config()
        if stt.resolve_backend(cfg, self.data_dir) == "off":
            return 0
        live = self.pool.live()
        touched: set[str] = set()
        done = 0
        for seg in self.meetings.failed_segments(meeting_id, limit=limit * 10):
            if done >= max(1, int(limit)):
                break
            # The worker owns the segments of a meeting that is still recording.
            if live is not None and seg["meeting_id"] == live.meeting_id:
                continue
            if int(seg["attempts"] or 0) >= RETRANSCRIBE_MAX_ATTEMPTS or not seg["wav_path"]:
                continue
            path = Path(seg["wav_path"])
            ok, note = audiocap.validate_wav(path)
            if not ok:
                self.meetings.finish_segment(
                    seg["id"], state="empty" if note == "empty" else "failed",
                    error=f"unusable segment: {note}", wav_path="" if note == "empty" else seg["wav_path"],
                    wav_bytes=0 if note == "empty" else int(seg["wav_bytes"] or 0),
                    attempts=int(seg["attempts"] or 0) + 1)
                touched.add(seg["meeting_id"])
                continue
            res = stt.transcribe(path, settings=self.settings(), cfg=cfg, data_dir=self.data_dir)
            text = str(res["text"] or "").strip()
            keep = bool(res["error"]) or bool(cfg["keepAudio"])
            if not keep:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
            self.meetings.finish_segment(
                seg["id"], text=text, detail=res["detail"], backend=res["backend"], error=res["error"],
                state="done" if text and not res["error"] else ("failed" if res["error"] else "empty"),
                wav_path=seg["wav_path"] if keep else "", wav_bytes=int(seg["wav_bytes"] or 0) if keep else 0,
                attempts=int(seg["attempts"] or 0) + 1)
            touched.add(seg["meeting_id"])
            done += 1
        for mid in touched:
            with contextlib.suppress(Exception):
                self._settle_transcript(mid)
        return done

    # ---- speakers ----
    def diarize_segments(self, meeting_id: str, backend: Any = None,
                         concat: Callable[[list[Path], Path], bool] = diarize.concat_wavs) -> dict[str, Any]:
        """Whole-file diarization of one channel's retained wavs; writes utterances, not the transcript.

        Blocking (an ffmpeg concat and a model run), so callers use a thread. A missing backend,
        missing audio or an empty result is a note in the return value, never an exception and
        never an error banner: with no diarizer the transcript keeps its channel labels.
        """
        cfg = self.config()
        backend = backend if backend is not None else diarize.get_backend(cfg, self.data_dir)
        name = type(backend).__name__
        if isinstance(backend, diarize.NullBackend):
            return {"ok": False, "backend": "none", "speakers": 0,
                    "note": "no speaker-separation backend is installed; see the diarize row in Capabilities"}
        segs = [s for s in self.meetings.segments(meeting_id, limit=100000)
                if s["state"] == "done" and s["wav_path"] and Path(s["wav_path"]).is_file()]
        channel = "import" if any(s["channel"] == "import" for s in segs) else "output"
        segs = sorted((s for s in segs if s["channel"] == channel), key=lambda s: (s["t_start"], s["seq"]))
        if not segs:
            return {"ok": False, "backend": name, "speakers": 0, "note": "no retained audio to separate speakers in"}
        tmp = self.data_dir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        whole = tmp / f"diarize-{meeting_id}.wav"
        try:
            if not concat([Path(s["wav_path"]) for s in segs], whole):
                return {"ok": False, "backend": name, "speakers": 0, "note": "could not join the audio for diarization"}
            turns = diarize.rename_turns(backend.diarize(whole, cfg))
        finally:
            with contextlib.suppress(Exception):
                whole.unlink(missing_ok=True)
        if not turns:
            return {"ok": False, "backend": name, "speakers": 0, "note": "the diarizer found no speech turns"}
        offset = 0.0
        for s in segs:
            length = float(s["duration_ms"] or 0) / 1000.0 or max(0.0, float(s["t_end"]) - float(s["t_start"]))
            parts = [{"start": float(x.get("start") or 0.0), "end": float(x.get("end") or 0.0),
                      "text": str(x.get("text") or "").strip()}
                     for x in ((s.get("detail") or {}).get("segments") or []) if isinstance(x, dict)]
            parts = [u for u in parts if u["text"]] or [{"start": 0.0, "end": length, "text": (s["text"] or "").strip()}]
            shifted = [{**u, "start": u["start"] + offset, "end": u["end"] + offset} for u in parts]
            merged = diarize.merge_adjacent(diarize.assign_speakers(shifted, turns))
            utts = [{"start": round(u["start"] - offset + float(s["t_start"]), 3),
                     "end": round(u["end"] - offset + float(s["t_start"]), 3),
                     "text": u["text"], "speaker": u.get("speaker", "")} for u in merged]
            weight: dict[str, float] = {}
            for u in utts:
                if u["speaker"]:
                    weight[u["speaker"]] = weight.get(u["speaker"], 0.0) + max(0.0, u["end"] - u["start"])
            dominant = max(weight.items(), key=lambda kv: kv[1])[0] if weight else ""
            self.meetings.set_segment_speakers(s["id"], dominant, utts)
            offset += length
        return {"ok": True, "backend": name, "speakers": len({t[2] for t in turns}), "note": ""}

    async def diarize(self, meeting_id: str, backend: Any = None) -> dict[str, Any]:
        """Run diarization, then re-roll the transcript (and FTS) so [S1]/[S2] lines appear."""
        if self.meetings.get(meeting_id, include_hidden=False) is None:
            return {"ok": False, "backend": "none", "speakers": 0, "note": "no such meeting"}
        res = await asyncio.to_thread(self.diarize_segments, meeting_id, backend)
        if res["ok"]:
            await asyncio.to_thread(self._settle_transcript, meeting_id)
        return res

    def set_speakers(self, meeting_id: str, names: dict[str, Any]) -> dict[str, Any] | None:
        """Rename diarized speakers and rebuild the transcript. ValueError on an unknown id or long name."""
        if self.meetings.get(meeting_id, include_hidden=False) is None:
            return None
        self.meetings.set_speaker_names(meeting_id, names)
        return self._settle_transcript(meeting_id) or self.meetings.get(meeting_id)

    # ---- calendar ----
    async def suggest(self) -> list[dict[str, Any]]:
        """Calendar events happening now that are worth taking notes on. No LLM, 60s cached.

        Returns [] rather than raising whenever Google is not connected or the call fails: a
        missing suggestion is a missing row in a panel, not a broken panel.
        """
        cfg = self.config()
        if 0 <= now() - self._suggest[0] < SUGGEST_TTL:
            return self._suggest[1]
        if self.google is None:
            self._suggest = (now(), [])
            return []
        try:
            events = await asyncio.to_thread(
                self.google.calendar_events, 1, "primary", 30, None, list(cfg["calendarIds"] or ["primary"]))
        except Exception as e:  # noqa: BLE001 - not connected, offline, scope revoked: all the same here
            log.debug("meetings: calendar suggest skipped: %s", e)
            self._suggest = (now(), [])
            return []
        t = now()
        nudge = float(cfg["nudgeSeconds"] or 0)
        want = max(1, int(cfg["minAttendees"] or 1))
        out: list[dict[str, Any]] = []
        for e in events:
            if e.get("all_day"):
                continue
            start = _epoch(e.get("start"))
            end = _epoch(e.get("end")) or (start + 3600 if start else 0.0)
            if not start or not (start - nudge <= t <= end):
                continue
            details = e.get("attendee_details") or []
            emails = e.get("attendees") or []
            count = len(details or emails)
            if count < want:
                continue
            # `self` is only present on the full event; from a list the honest proxy is "more
            # than one invitee", since the user is always one of them.
            has_external = any(not a.get("self") for a in details) if details else count > 1
            existing = self.meetings.by_event(str(e.get("id") or ""))
            out.append({
                "event_id": str(e.get("id") or ""), "calendar_id": str(e.get("calendar_id") or ""),
                "title": str(e.get("summary") or "(no title)"), "start": str(e.get("start") or ""),
                "end": str(e.get("end") or ""), "attendee_count": count, "has_external": has_external,
                "conference_link": _conference_link(e), "meeting_id": existing["id"] if existing else None,
            })
        self._suggest = (now(), out)
        return out

    def adopt(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Create (or find) the `scheduled` meeting row for one calendar candidate."""
        event_id = str(candidate.get("event_id") or "")
        if not event_id:
            return None
        existing = self.meetings.by_event(event_id)
        if existing:
            return existing
        cfg = self.config()
        return self.meetings.create(
            title=str(candidate.get("title") or ""), template=cfg["template"], status="scheduled",
            calendar_event_id=event_id, calendar_id=str(candidate.get("calendar_id") or "") or None,
            conference_link=str(candidate.get("conference_link") or ""),
            scheduled_start=_epoch(candidate.get("start")) or None,
            scheduled_end=_epoch(candidate.get("end")) or None)

    # ---- what chat actually sees ----
    def context_block(self, max_chars: int = 3000) -> str:
        """The compact version injected into a chat's system prompt. Empty when off or opted out.

        Accepted notes and headlines only - never the raw transcript. A transcript is other
        people's speech, and feeding it into every unrelated chat is how it ends up quoted back.
        """
        cfg = self.config()
        # The master switch means the whole feature, not just capture: with the recorder off,
        # meetings stop reaching the system prompt of every chat as well.
        if not cfg["enabled"] or not cfg.get("injectContext", True):
            return ""
        recent = [m for m in self.meetings.list(limit=6) if m["status"] in ("ready", "stopped", "notes_only")]
        if not recent:
            return ""
        parts = [
            "## Recent meetings",
            "Notes from the user's own recorded meetings. Use them when relevant; do not recite "
            "them back or quote attendees unless asked.",
        ]
        for m in recent:
            full = self.meetings.get(m["id"]) or {}
            body = (full.get("enhanced") or full.get("notes") or "").strip()
            # A DEGRADED pass's fallback markdown is the user's notes followed by the raw
            # transcript (meeting_notes._mechanical), so if one of those was ever applied -
            # by hand, since `enhance` no longer auto-accepts them - `enhanced` is partly other
            # people's verbatim speech. Fall back to the user's own notes for the preview line.
            last = self.meetings.last_applied(m["id"])
            if last is not None and last["degraded"] and body == (last["after"] or "").strip():
                body = (full.get("notes") or "").strip()
            first = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), "")
            head = m["summary"] or m["title"] or "(untitled)"
            parts.append(f"- {head}" + (f": {first[:200]}" if first else ""))
        return "\n".join(parts)[:max_chars]

    # ---- startup / shutdown ----
    def recover(self) -> list[str]:
        """Finalize meetings a crash or a quit left mid-flight.

        A row that still says `recording` would otherwise be polled forever by a UI waiting for
        segments no process is producing. It keeps whatever segments landed - and its LENGTH: the
        end comes from `_closing_clock`, never from the clock at boot. The app may reopen a day
        after the crash, and `finalize`'s default would record that downtime as the duration of a
        ten-minute call (and overwrite the correct duration of a meeting that merely had its
        enhance pass interrupted, which already carries its own `ended_at`).
        """
        out: list[str] = []
        for m in self.meetings.unfinished():
            with contextlib.suppress(Exception):
                self.meetings.interrupt_pending(m["id"])
            note = ("The enhance pass was interrupted when the app quit; your notes and the "
                    "transcript are untouched. Run it again when you like."
                    if m["status"] == "enhancing" else
                    "Recording was interrupted when the app quit. The notes and the segments that "
                    "finished are kept.")
            with contextlib.suppress(Exception):
                self.meetings.finalize(m["id"], self.meetings.build_transcript(m["id"]),
                                       ended_at=self._closing_clock(m), status="ready", error=note)
                out.append(m["id"])
        # A meeting whose drain was abandoned carries the "still transcribing" banner and a
        # watcher task that died with the process. Its segments may well have settled before the
        # quit, so roll the transcript up now: `unfinished()` cannot see it (it is already
        # `ready`) and `retranscribe` will not either (those segments are not `failed`).
        for row in self.meetings.list(status="ready", limit=200, include_docs=True):
            if "still transcribing" not in (row.get("error") or ""):
                continue
            with contextlib.suppress(Exception):
                before = self.meetings.get(row["id"]) or {}
                after = self._settle_transcript(row["id"]) or {}
                if (after.get("transcript"), after.get("error")) != \
                        (before.get("transcript"), before.get("error")):
                    out.append(row["id"])
        if out:
            log.info("meetings: recovered %d interrupted meeting(s)", len(out))
        return out

    def shutdown(self) -> None:
        """Every live ffmpeg gets its graceful `q`, so the final segment is flushed rather than orphaned."""
        with contextlib.suppress(Exception):
            self.pool.stop_all()

    # ---- background loop ----
    async def loop(self) -> None:
        """One tick every 45 seconds. No LLM in here: a nudge must not cost a model call.

        Auto-stop is purely TIME-based. Nothing in this design measures amplitude - there is no
        voice-activity detection anywhere in the codebase - so a "stopped after 90s of silence"
        rule would be a lie about what is being observed.
        """
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                cfg = self.config()
                if not cfg["enabled"]:
                    continue
                await self._nudge(cfg)
                await self._auto_stop(cfg)
                await asyncio.to_thread(self.retranscribe, "", RETRANSCRIBE_PER_TICK)
                await asyncio.to_thread(self._sweep_audio, cfg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - the loop must outlive any single failure
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("meetings loop: %s", e)
                await asyncio.sleep(30)

    async def _nudge(self, cfg: dict[str, Any]) -> None:
        """Upsert a `scheduled` row per live calendar candidate, and auto-start if asked to."""
        for cand in await self.suggest():
            m = self.meetings.by_event(cand["event_id"]) or self.adopt(cand)
            if m is None or not cfg["autoRecord"]:
                continue
            if m["status"] != "scheduled" or self.pool.live() is not None:
                continue
            if _epoch(cand["start"]) > now():
                continue
            try:
                await asyncio.to_thread(self.start, m["id"])
            except MeetingBlocked as e:
                # autoRecord must not silently fail: the row carries why nothing was recorded.
                self.meetings.patch(m["id"], {"error": "; ".join(b["detail"] for b in e.blockers)})
            except Exception as e:  # noqa: BLE001
                log.warning("meetings: auto-record %s: %s", m["id"], e)

    async def _auto_stop(self, cfg: dict[str, Any]) -> None:
        session = self.pool.live()
        if session is None:
            return
        m = self.meetings.get(session.meeting_id)
        if m is None:
            return
        if getattr(session, "captures_dead", lambda: False)():
            # Every channel gave up, so only the transcribe worker is keeping the session "alive":
            # close it with the capture error rather than sit empty until the length cap.
            why = "; ".join(f"{k}: {v}" for k, v in session.errors().items() if k != "transcribe")
            self.meetings.patch(m["id"], {"error": why or "Every capture channel stopped."})
            log.info("meetings: closing %s, all captures died (%s)", m["id"], why)
            await self.stop(m["id"])
            return
        grace = float(cfg["autoStopGraceSeconds"] or 0)
        elapsed = now() - (session.started_at or now())
        past_end = bool(m["scheduled_end"]) and now() > float(m["scheduled_end"]) + grace
        if past_end or elapsed > float(cfg["maxMeetingSeconds"]):
            log.info("meetings: auto-stopping %s (%s)", m["id"], "scheduled end" if past_end else "max length")
            await self.stop(m["id"])

    def _sweep_audio(self, cfg: dict[str, Any]) -> int:
        """Delete the wavs of finished meetings once nothing can be replayed from them.

        A failed segment keeps its wav so `retranscribe` can replay it, so audio outlives the
        meeting until either every failure is settled or AUDIO_RETENTION_SECONDS has passed -
        otherwise a transcription route the user never fixes keeps every wav forever.
        """
        swept = 0
        live = self.pool.live()
        for m in self.meetings.list(status="ready", limit=200, include_docs=True):
            if not m["audio_dir"] or m["keep_audio"] or cfg["keepAudio"]:
                continue
            if live is not None and live.meeting_id == m["id"]:
                continue
            replayable = [s for s in self.meetings.failed_segments(m["id"])
                          if s["wav_path"] and int(s["attempts"] or 0) < RETRANSCRIBE_MAX_ATTEMPTS]
            stale = now() - float(m["ended_at"] or m["updated_at"] or 0) > AUDIO_RETENTION_SECONDS
            if replayable and not stale:
                continue
            self.meetings.delete_audio(m["id"])
            swept += 1
        return swept

    # ---- recorder callbacks (worker and capture threads, never the request thread) ----
    def _on_segment(self, meeting_id: str, channel: str, seq: int, path: Path, info: dict[str, Any]) -> None:
        """The INSERT. Runs on a capture thread, so it gets its own connection via Database.tx()."""
        try:
            self.meetings.add_segment(
                meeting_id, channel, seq, info["t_start"], info["t_end"], info["started_at"],
                info["wav_path"], info["wav_bytes"], duration_ms=info["duration_ms"], state=info["state"])
        except Exception as e:  # noqa: BLE001 - the recorder swallows this; log it or it is invisible
            log.warning("meetings: segment %s/%s of %s not recorded: %s", channel, seq, meeting_id, e)

    def _on_result(self, meeting_id: str, channel: str, seq: int, path: Path, res: dict[str, Any]) -> None:
        """The UPDATE. Also fires a SECOND time for an evicted segment, whose audio is now gone."""
        try:
            seg_id = self.meetings.segment_id(meeting_id, channel, seq)
            if not seg_id:
                return
            row = self.meetings.finish_segment(
                seg_id, text=res["text"], detail=res["detail"], backend=res["backend"],
                error=res["error"], state=res["state"], wav_path=res["wav_path"],
                wav_bytes=int(res["wav_bytes"] or 0), attempts=int(res.get("attempts") or 0))
            if res["error"] and not res.get("evicted"):
                self.meetings.patch(meeting_id, {"error": res["error"]})
                self._emit("status", meeting_id, status="error", error=str(res["error"])[:300])
            # The row finish_segment RETURNS, never `res["text"]`: the stored row is the one that
            # went through the credential scrubber.
            if row is not None and row.get("state") in ("done", "empty", "failed"):
                self._emit("segment", meeting_id, segment=row)
        except Exception as e:  # noqa: BLE001
            log.warning("meetings: result %s/%s of %s not stored: %s", channel, seq, meeting_id, e)
