# Notes recording: codemap of the Meetings pipeline and the seams for doc-attached recordings

Goal: open any doc in Docs/Files, press Record, see a live transcript beside the note, and on stop get a
stored transcript linked to the doc plus an AI summary proposed into the doc as a pending revision.
This map is read-only research over the worktree `docs-notes-dictation`. Everything is verified against the
code unless marked "(inferred)". Line numbers are for this worktree (HEAD 11b1a54).

Paths are relative to the repo root. `BE` = `backend/personal_os`, `FE` = `src/renderer/src`.

---------------------------------------------------------------------------------------------------

## 1. Data model

All meeting tables are created by `Meetings.__init__` (`BE/meetings.py:389`) via `SCHEMA` (`meetings.py:45-162`),
NOT by `db.py`. Post-release columns go through `ADDED_COLUMNS` (`meetings.py:225`, applied by a
`PRAGMA table_info` guard at `meetings.py:392-397`). `PRAGMA foreign_keys=ON` is set on every connection
(`BE/db.py:26`, `db.py:574`), so FKs and cascades are live.

Construction order in `BE/app.py`: `docs = Docs(db)` (line 101) ... `meeting_store = Meetings(db)` (445),
`meeting_svc = MeetingService(db, settings, llm.complete, meeting_store, google=google, todos=todos)` (446).
`docs` already exists when the meeting service is built, so injecting it is trivial.

### `meetings` (one row per recording session)
| column | type / default | notes |
|---|---|---|
| id | TEXT PK | `new_id()` |
| project_id | TEXT FK projects ON DELETE SET NULL | NULL = personal |
| title | TEXT '' | trimmed to 200, indexed in FTS |
| status | TEXT 'scheduled' | scheduled, recording, stopped, transcribing, enhancing, ready, failed, notes_only. `Meetings.create` default is `notes_only`; `POST /meetings` (MeetingIn) defaults to `scheduled` |
| template | TEXT 'general' | key into `meeting_notes.TEMPLATES` |
| notes | TEXT '' | what the USER typed; only `patch` writes it |
| enhanced | TEXT '' | only `accept(rev_id)` writes it |
| summary | TEXT '' | one-line headline for the rail (set by enhance) |
| transcript | TEXT '' | channel-interleaved rollup; written ONLY by `finalize` (and `_settle_transcript` via finalize) |
| scheduled_start, scheduled_end, started_at, ended_at | REAL | epoch seconds |
| duration_ms | INTEGER 0 | |
| calendar_event_id (partial UNIQUE idx), calendar_id, calendar_link, conference_link | TEXT | |
| attendees | TEXT '[]' | JSON `[{email,name,response,organizer,self}]` |
| sources | TEXT '[]' | JSON, what actually captured, e.g. `["mic"]`, `["import"]` |
| audio_dir | TEXT '' | `<data_dir>/recordings/<id>`, '' after delete |
| audio_bytes | INTEGER 0 | |
| keep_audio | INTEGER 0 | |
| consent_ack | INTEGER 0 | present, unused by the service (inferred: legacy) |
| conversation_id | TEXT FK conversations ON DELETE SET NULL | |
| error | TEXT '' | banner; clauses joined by "; " |
| created_at, updated_at | REAL NOT NULL | |
| speaker_names | TEXT '{}' | added by ADDED_COLUMNS; JSON `{"S1":"Dana"}` |

Indexes: `idx_meetings_start`, `idx_meetings_status`, `idx_meetings_event` (partial unique on `calendar_event_id`).
There is NO `doc_id` column today and no `expires_at` (deliberate; `test_schema_applies_twice_without_error` asserts it).

### `meeting_segments` (one row per closed ~20 s wav per channel)
`id PK`, `meeting_id FK meetings ON DELETE CASCADE`, `channel` (mic | output | import), `seq` INTEGER,
`t_start`/`t_end` REAL (seconds from meeting start, recording clock = `seq * segment_seconds`),
`started_at` REAL (absolute epoch of first sample), `duration_ms`, `text` '', `detail` '{}' (JSON: verbose_json
`{"segments":[...]}`, plus `filtered`, `vad`, later `utterances`), `speaker` '' , `state`
(recorded | transcribing | done | failed | empty | discarded), `attempts`, `backend`, `error`, `wav_path`,
`wav_bytes`, `created_at`. `UNIQUE(meeting_id, channel, seq)`. Indexes `idx_mseg_timeline`, `idx_mseg_pending`,
`idx_mseg_failed`. Written in two halves: `add_segment` (INSERT ... ON CONFLICT upsert, `meetings.py:672`) when
the wav closes, then `finish_segment` (UPDATE, `meetings.py:695`) when STT returns. The rowid does not change
between the two (matters for the `?since=` cursor, see section 3).

### `meeting_revisions` (enhance proposals; mirrors `doc_revisions`)
`id`, `meeting_id FK CASCADE`, `before`, `after`, `title_before`, `title_after`, `summary`, `author` 'assistant',
`tool` (e.g. 'meeting_enhance'), `status` (pending | applied | rejected | superseded), `template`, `model`,
`degraded` INT, `decisions` JSON, `topics` JSON, `created_at`, `resolved_at`. Index `idx_mrev_pending`.
`Meetings._rev_view` (`meetings.py:872`) emits DocRevision field names with `doc_id` = meeting id plus
`meeting_id, template, model, degraded, decisions, topics, stale, stat, stat_vs_current`.

### `meeting_action_items`
`id`, `meeting_id FK CASCADE`, `revision_id FK meeting_revisions ON DELETE SET NULL` (nullable), `text`,
`owner`, `due` (YYYY-MM-DD or ''), `status` (proposed | added | dismissed), `todo_id` (no FK on purpose),
`created_at`. Dedup on re-propose: `add_action_items` (`meetings.py:984`). Promote: `promote_action_item`
(`meetings.py:1025`) calls `todos.create(..., source="meeting")`, never sets `external_id`.

### FTS
`meetings_fts` fts5(title, notes, enhanced, transcript, meeting_id UNINDEXED, porter unicode61)
(`meetings.py:159`). No triggers: `Meetings._reindex(c, ...)` (`meetings.py:401`) is called from `create`,
`patch` (only when title/notes/enhanced changed, `INDEXED_FIELDS`), `finalize`, `accept`. `delete` removes the FTS
row by hand (`meetings.py:667`). The transcript is indexed only after `finalize`, never per segment.

### Config (not a table)
`settings["meetings"]`, deep-merged over `DEFAULT_CONFIG` (`meetings.py:168-206`) by `config_for(db)`
(`meetings.py:275`). Written by `MeetingService.set_config` (`meetings.py:1124`) through
`PUT /meetings/config` (`MeetingConfigIn`, app.py:5398). The `meetings` key is read-only through `PUT /settings`
(`SETTINGS_READ_ONLY`, app.py:627). Keys that matter here: `enabled` (False), `consentedAt` (0.0), `sources`
(["mic"]), `segmentSeconds` (20), `maxMeetingSeconds` (14400), `drainSeconds` (90), `sttBackend` ("auto"),
`enhanceOnStop` (True), `keepAudio` (False), `redactSecrets` (True), `injectContext` (True), `vadGate` (True),
`vadMinSpeechRatio` (0.03), `hallucinationFilter` (True), `maxTranscriptChars` (48000).

### API shapes
- Preview (`GET /meetings`, `Meetings.list` -> `_list_view`, `meetings.py:413/445`): every `meetings` column
  EXCEPT `notes`, `enhanced`, `transcript`, plus `notes_preview` (first 240 chars of notes), `words`
  (of enhanced or notes), `segment_count`, `has_pending` bool, `keep_audio` bool, `attendee_count`. JSON columns
  decoded (`JSON_FIELDS`, `meetings.py:219`).
- Full (`GET /meetings/{id}`, `Meetings.get`, `meetings.py:462`): all columns including `notes`, `enhanced`,
  `transcript`, plus `words`, `attendee_count`, `keep_audio` bool, `segment_count`, `has_pending`, `pending`
  (newest pending `meeting_revisions` row through `_rev_view` or null), `actions` (all action rows).
- Segments: `GET /meetings/{id}/segments?since=<rowid>&limit=` -> `Meetings.since` (`meetings.py:742`), full
  segment rows (`detail` decoded) with an extra `cursor` = rowid; without `since` it pages by
  `t_start` via `Meetings.segments` (`meetings.py:731`). TS types: `src/shared/types.ts:2186` (`Meeting`),
  `:2214` (`FullMeeting`), `:2244` (`MeetingSegment`), `:2274` (`MeetingRevision extends DocRevision`),
  `:2374` (`MeetingStatusInfo`).
- `GET /meetings/status` (`MeetingService.status`, `meetings.py:1219`): `{enabled, consented, config, active,
  upcoming, devices, stt, counts}`; `active` = `{meeting_id, status, started_at, elapsed_ms, segments_done,
  segments_pending, queued, paused, channels:[{channel,alive,error}], error}` or null.

---------------------------------------------------------------------------------------------------

## 2. Lifecycle

Module graph: `MeetingService` (`meetings.py:1097`) owns `self.pool = meeting_recorder.RecorderPool(...)`
(`meetings.py:1114`); `Meetings` (repo) only touches SQLite. The recorder knows nothing about the DB; rows leave
through two injected callbacks `_on_segment` (INSERT) and `_on_result` (UPDATE) (`meetings.py:1949`, `1958`).

1. **create**: `POST /meetings` -> `Meetings.create(title, project_id, template, calendar_event_id, ..., status)`
   (`meetings.py:573`; route `app.py:5560`, body `MeetingIn` app.py:5361). Returns `get(mid)`. No doc linkage.
2. **start**: `POST /meetings/{id}/start` (`app.py:5603`) -> 400 if not macOS (`activity.IS_MAC`), else
   `await asyncio.to_thread(meeting_svc.start, id)`. `MeetingService.start` (`meetings.py:1299`):
   - 404 -> None; refuses unless status in (scheduled, notes_only) else `MeetingBlocked(already_recorded)`
     (`meetings.py:1310`). A meeting records ONCE (a second run would renumber audio).
   - `preflight()` (`meetings.py:1258`, 10 min cache `PREFLIGHT_TTL`) -> blockers, in order: `enabled`
     (master switch, `meetings.py:1276`), `consent` (`consentedAt <= 0`, `meetings.py:1270`), then failing rows of
     `BLOCKING_CAPABILITIES = ("platform","ffmpeg","mic","stt")`, then `selftest` (real STT round trip on a silent
     wav, `stt.selftest`, up to 120 s on a cold cache). Any blocker -> `MeetingBlocked(blockers)` -> route 409
     `{"blockers": [...]}`.
   - Channel resolution (`meetings.py:1321-1362`): `sources` from config, native mic via
     `audiocap.native_mic_input(uid)` or ffmpeg device, native output tap via `audiocap.native_output_input()`.
     A mic channel that cannot resolve raises `MeetingBlocked(mic_device)`; a dropped `output` is only a banner.
   - `self.pool.start(meeting_id, channels, on_segment=..., on_result=..., segment_seconds=cfg["segmentSeconds"],
     max_seconds, keep_audio, max_audio_bytes, drain_seconds, on_disk_check)` (`meetings.py:1364`). Raises
     `RecorderBusy` -> route 409 `{"meeting_id", "blockers":[{"id":"busy",...}]}` (`app.py:5614`).
   - `meetings.mark_started(...)` sets `status='recording'`, `started_at`, `audio_dir`, `sources`
     (`meetings.py:622`); degradation note goes to `error`.
3. **segments (hot path)**: `RecordingSession.start` (`meeting_recorder.py:610`) starts one `TranscribeWorker`
   thread plus one `ChannelCapture` thread per channel. Native capture loop `_native_loop`
   (`meeting_recorder.py:224`): `cap.read_seconds(segment_seconds)` blocks until a full segment of 16 kHz mono PCM
   exists, writes `<channel>-%05d.wav` under `recordings/<id>/`, calls `_emit_direct` -> `RecordingSession._segment`
   (`meeting_recorder.py:701`) which computes `t_start = seq * segment_seconds`, calls `on_segment` (row INSERT,
   state `recorded`, or `discarded` if paused) and `q.put((channel, seq, path, paused))`.
   `TranscribeWorker._transcribe_one` (`meeting_recorder.py:445`): paused -> `discarded`; `audiocap.validate_wav`;
   VAD gate (`meeting_vad.analyze`, `vadGate`) -> `empty` without an STT call; `stt.transcribe(path, settings=,
   cfg=, data_dir=, prompt=<last 180 chars of this channel's previous text>)` with up to `MAX_ATTEMPTS=3` and
   backoff; `stt.filter_hallucinations`; `_report` deletes the wav unless `keepAudio` or error, then
   `on_result` -> `MeetingService._on_result` -> `Meetings.finish_segment` (scrubs secrets, UPDATE). If an
   error: also `patch(meeting_id, {"error": ...})`.
   ffmpeg fallback (`_run_once`/`_emit_ready`, `meeting_recorder.py:273-336`): a file counts as finished only
   once its successor exists.
4. **pause/resume**: `MeetingService.pause/resume` (`meetings.py:1382/1390`) just set `session.paused`; capture
   keeps running, new segments are stored as `discarded` and never transcribed.
5. **stop**: `POST /meetings/{id}/stop` (`app.py:5623`) -> `MeetingService.stop` (`meetings.py:1397`): patch
   `status='transcribing'`; `await asyncio.to_thread(self.pool.stop, id)` (`RecorderPool.stop`,
   `meeting_recorder.py:802` -> `RecordingSession.stop`, `:629`: captures down, drain up to `drainSeconds`, worker
   join, returns `{"drained","pending","stats"}`); `build_transcript` (`meetings.py:782`); `finalize(..., status="ready",
   error=...)` (`meetings.py:644`, the only writer of `transcript`, one reindex); optional diarize; then
   enhance is QUEUED not awaited: `asyncio.ensure_future(self._enhance_quietly(id))` (`meetings.py:1449`), or if the
   drain gave up `_settle_after_drain` (`meetings.py:1452`) re-rolls the transcript when segments settle and then
   enhances. The route returns the finalized meeting row (not the revision).
6. **enhance**: `MeetingService.enhance` (`meetings.py:1527`) -> `meeting_notes.enhance` (`BE/meeting_notes.py:200`) ->
   `Meetings.propose` (`meetings.py:920`, INSERT pending `meeting_revisions`, `before` = current `enhanced`) ->
   `add_action_items` -> patches `status` back, `summary` headline, `error`. AUTO-APPLY RULE
   (`meetings.py:1591-1594`): accepted for the user only when the pass succeeded (not `degraded`) and `enhanced`
   is empty or byte-identical to the last applied revision's `after`. Cache-or-generate: an existing pending
   revision is returned unless `force`. Manual: `POST /meetings/{id}/enhance?force=&template=` (`app.py:5746`).
7. **accept/reject**: `POST /meetings/revisions/{rev_id}/accept|reject` (`app.py:5535/5544`; these literals are
   registered above `/meetings/{id}`). `Meetings.accept` (`meetings.py:938`): sets revision `applied`, rewrites its
   `before` to what was replaced, supersedes other pendings, writes `meetings.enhanced` ONLY (never `notes`), reindexes.
8. **crash/quit**: `MeetingService.recover` (`meetings.py:1825`, called from `_meetings_startup`, app.py:5823)
   finalizes rows stuck in recording/transcribing/enhancing using the recording clock
   (`_closing_clock`, `meetings.py:1502`). `shutdown` -> `pool.stop_all()` with a 5 s drain (app.py shutdown hook,
   `app.py:5841`). `MeetingService.loop` (`meetings.py:1869`) ticks every 45 s: calendar nudge, auto-stop on
   `maxMeetingSeconds`/scheduled end, `retranscribe` of failed segments (3 per tick, max 8 attempts), audio sweep.

### Global state and the single-live-session constraint
- `RecorderPool.sessions: dict[meeting_id, RecordingSession]` (`meeting_recorder.py:750`). `RecorderPool.start`
  (`meeting_recorder.py:753`) takes the pool lock, calls `_live()` and raises `RecorderBusy(live.meeting_id)` if ANY
  session is live (`meeting_recorder.py:764-766`). So at most one recording in the whole app, across meetings AND any
  future doc recordings. Same-id double start also raises `RecorderBusy`.
- The live session is tracked by `MeetingService.pool.live()` / `.get(id)`. `status()["active"]` is a single object,
  not a list. The UI mirror is `meetingStatus.active` (a single slot) in `FE/store.ts`.
- `MeetingService._enhancing: set[str]` guards concurrent enhance of one meeting.
- `MeetingService._preflight` (10 min cache; reset by `set_config`), `_suggest` (60 s).
- Window/process model: one `TranscribeWorker` thread and one capture thread per channel; callbacks run on those
  threads and open their own SQLite connections through `Database.tx()` (no shared connection).

---------------------------------------------------------------------------------------------------

## 3. Latency: why spoken words take ~10-30 s to appear, and the cheapest fixes

### Today, step by step
1. **Segment fill (dominant)**: `Capture.read_seconds(secs, halt)` (`BE/native_audio.py:178`) blocks until
   `segment_seconds` (default 20) of PCM are in the sink. Nothing is emitted before then. A word spoken at the
   start of a segment waits ~20 s; the last word of a segment waits ~0.
2. **Queue**: `TranscribeWorker` is single-threaded per session and processes (mic, output) segments in FIFO order;
   a slow STT call delays the next. `queued` is shown in the bar.
3. **STT**: `stt.transcribe` per wav. `speech` backend (`stt._speech`, `BE/stt.py:251`): a
   `SFSpeechURLRecognitionRequest` over the finished wav with `setShouldReportPartialResults_(False)` and a run-loop
   spin of up to 60 s, so it returns once, final. `proxy`: HTTP POST of the wav, `verbose_json`, 120 s timeout.
   `local`: `whisper-cli` subprocess, 300 s timeout. Typical per-clip cost is seconds (inferred; not measured here).
4. **Persist**: `finish_segment` UPDATE. Nothing is pushed anywhere.
5. **Transport to the UI**: `MeetingRecorderBar` polls every 2 s (`pollMeetingLive`, `FE/components/MeetingRecorderBar.tsx:46`
   -> `api.meetings.segments(id, meetingCursor)`), and the store's `liveTick` every 5 s (`FE/store.ts:962`).
   **Trap**: the row is INSERTed (state `recorded`, empty text) when the wav closes. A poll that lands before STT
   finishes advances the `rowid` cursor past it, and the later UPDATE keeps its rowid, so `?since=` NEVER redelivers
   the text (the comment at `FE/store.ts:926-931` says exactly this). The words reach the pane only via
   `liveTick` -> `needsSegmentReload(held, segment_count)` (`FE/lib/transcript.ts:171`, true while any held row is not
   final) -> `loadSegments` full reload. `liveTick` only runs while `meetingStatus.active` or the active meeting is
   settling, on a 5 s `setInterval` started by `refreshMeetingStatus` (`FE/store.ts:2221-2224`). Net transport
   delay is up to ~5 s on top of STT.
6. **Bar text**: `transcript ~{config.segmentSeconds}s behind` (`MeetingRecorderBar.tsx:92`) is a static statement.

Estimate (inferred): first word of a segment appears ~25-30 s after it was spoken, last word ~5-8 s; mean ~15-18 s.

### What `native_audio.py` and `stt.py` expose
- `native_audio.Capture` (`native_audio.py:117`): `from_spec`, `start`, `stop`, `read_seconds(seconds, halt)`,
  `drain()`, and a `sink` (`_PcmSink`, `push/take/available`, `native_audio.py:364`, capped at 30 s). Kinds: `mic`
  (AVAudioEngine input tap, `_start_engine`, `native_audio.py:266`), `output` (Core Audio process tap, macOS 14.2+),
  `sine` (test tone). The tap callback (`native_audio.py:285`) converts each hw buffer to 16 kHz mono s16 with
  `_buffer_to_s16_mono` and pushes it to the sink. It is a PULL model: no per-buffer callback hook, `take()`
  consumes, and there is no peek.
- `stt.transcribe(path, *, settings, cfg, data_dir, prompt="") -> {text, detail, backend, error, ms}` takes a FILE path
  only; there is no bytes/stream entry point. `detail` is `{"segments":[...]}` for proxy (verbose_json) and local
  (whisper `-oj`), `{}` for speech.
- `stt.speech_ready()`, `resolve_backend(cfg, data_dir)`, `capabilities`, `selftest`, `filter_hallucinations`,
  `vad_model_path`. No streaming API.

### Can we get streaming/partial results with the existing code?
No. Apple Speech partials are available in principle (`setShouldReportPartialResults_(True)` on the URL request would
deliver partials while it chews a finished 20 s wav, which helps nothing because the wav is already closed). Real
live partials need `SFSpeechAudioBufferRecognitionRequest` fed with `appendAudioPCMBuffer_` from the AVAudioEngine tap
(before the 16 kHz conversion), a results handler delivering `isFinal=False` text, and request rotation (Apple limits one
request to about a minute, inferred). Neither `Capture` nor `stt.py` has this; it is new code in `native_audio.py`
(an optional per-buffer listener on the tap) and `stt.py` (a streaming recognizer class), needs
`pyobjc-framework-Speech` and the Speech grant. whisper.cpp's `whisper-stream` is not used. whisper `proxy` cannot stream.

### Cheapest changes, in order
1. **Shorter segments for doc recordings (config only, big win)**. `segmentSeconds` is global config; `MeetingService.start`
   reads `cfg["segmentSeconds"]` at `meetings.py:1368`. Add an optional `segment_seconds` argument (default config) so a
   doc recording runs at ~5-8 s without touching the user's meeting setting. Everything downstream keys off it:
   `t_start = seq * segment_seconds` (`meeting_recorder.py:704`), `mergeSegments` merges consecutive same-channel
   segments into lines (GAP 30 s), `build_transcript` merges runs. Costs: more STT calls (proxy is billed per call), words cut
   at clip edges (mitigated by the 180-char `prompt` tail for proxy/local; the `speech` backend has no cross-clip
   context), more VAD-gated empty clips. Latency drops to roughly segment + STT + transport.
2. **Fix the delivery bug and push, not poll**. Either (a) make the pane reload on `state` change (reuse
   `needsSegmentReload` inside the 2 s poll, i.e. call `loadSegments` when any held row is non-final), or (b) publish
   finished segments. For (b): `GET /meetings/{id}/stream` (`app.py:5723`) reads `meeting_bus.get(id)` (`meeting_bus =
   RunBus()`, app.py:252). It "ships idle": `grep meeting_bus` shows only app.py:252, 3884 (shutdown) and 5727 (the
   route). It is a `RunBus` of chat `Run`s keyed by conversation id: `meeting_bus.get(id)` returns a `Run` only if
   something called `meeting_bus.start(id, runner, ...)` (`runs.py:686`) with an async runner that lives for the
   recording. The FE contract already exists: `meetingStream` (`FE/lib/api.ts:790`), `watchMeeting` and
   `applyMeetingEvent` (`FE/store.ts:721`) accept frames `segment` (data = `MeetingSegment` with `meeting_id`, optional
   `cursor`), `status`, `revision` (`MeetingRevision`), `error`, `end`. The alternative is the app-wide
   `events = Topic()` (app.py:259, `GET /events`, `backgroundStream` in the FE) which has no run machinery.
   **Traps**: `Run.publish`/`Topic.publish` call `asyncio.Queue.put_nowait` and are not thread-safe, and `_on_result` runs on
   the worker thread, so publish through `loop.call_soon_threadsafe` (the loop must be captured at start). Publish the row that
   `finish_segment` RETURNS (already secret-scrubbed), never the raw `res["text"]` from `_on_result`.
3. **Optimistic "listening" UI**: show the in-flight segment as a pulsing placeholder (the bar already knows `queued`,
   `segments_pending`).
4. **Streaming partials** (section above): largest change, best result, macOS-only, Speech backend only.

Recommended: 1 + 2(a or b) first; keep 4 as a follow-up. Doc recordings are low-volume (mic only), so shorter segments
are cheap.

---------------------------------------------------------------------------------------------------

## 4. Reuse seams for doc-attached recordings

### Decision: option (a), `doc_id` on `meetings` (one meeting row per recording, many per doc)
Why: the entire pipeline (pool, worker, segments, finalize, FTS, transcript tools, delete, consent, preflight, UI poll)
is keyed by `meeting_id`. A separate table (b) would duplicate ~600 lines (`add_segment`, `finish_segment`, `since`,
`build_transcript`, `_on_result`, `retranscribe`, `recover`, `_sweep_audio`, `diarize`) or force an abstraction layer.
With (a) a "recording of doc D" is just a meeting with `doc_id=D`; a doc can have many (each Record press creates a new
meeting, which also satisfies the "records once" rule at `meetings.py:1310`).
Use `doc_revisions` (NOT `meeting_revisions`) for the summary, via the existing `Docs.propose`. Reason: the doc UI
already renders `doc.pending` with `DiffView` (`FE/components/DocsView.tsx:245-258`, history `:286`), and the pending badge
(`docsPending`) already counts it.

### Touch points for option (a)

**Schema / migration**
- `meetings.py` `ADDED_COLUMNS` (`:225`): add `"doc_id": "TEXT"` (ALTER cannot add a REFERENCES column unless default NULL; with
  `foreign_keys=ON` SQLite allows `ALTER TABLE ... ADD COLUMN doc_id TEXT REFERENCES docs(id) ON DELETE CASCADE`
  only if default is NULL, which it is; if that proves fragile keep it a plain TEXT and cascade in code). Add
  `CREATE INDEX IF NOT EXISTS idx_meetings_doc ON meetings(doc_id)` to the `SCHEMA` string (idempotent) or run it after the
  ALTER loop (the index must come after the column exists on an upgraded DB; putting it in `SCHEMA` before the ALTER fails on
  an existing DB, so create it in `__init__` after the column loop). (inferred: the ordering trap)
- No new table. Optionally `ADDED_COLUMNS` `"kind": "TEXT NOT NULL DEFAULT 'meeting'"` instead of or beside `doc_id` to mark
  recordings (not required).
- TS: `Meeting`/`FullMeeting` (`src/shared/types.ts:2186,2214`) gain `doc_id: string | null`.

**Store methods (`Meetings`)**
- `create(...)` (`meetings.py:573`): add `doc_id: str | None = None`; INSERT column list is explicit, update both the
  column list and the `VALUES(?,...)` count. Title default = the doc title; `project_id` = the doc's `project_id` (so
  scope filters work; moving the doc later does not update it, accept the drift or derive via JOIN).
- `PATCH_FIELDS` (`:211`): do NOT add `doc_id` (immutable after create, service-only like `started_at`).
- `list(...)` (`:413`): add `doc_id` filter param; decide whether the Meetings rail shows doc recordings (recommend default
  EXCLUDE `doc_id IS NOT NULL` from the rail, `GET /meetings?doc_id=<id>` for the doc UI).
- New: `for_doc(doc_id) -> list[preview]`, `Meetings.get` already returns every column once `doc_id` exists.
- `search` (`:520`): hits include doc recordings; return `doc_id` in the row so the UI can link. The `SELECT` at `:549`
  lists columns explicitly, add `doc_id`.
- `delete` (`:660`): unchanged logic, but see cascade below.
- `counts`/`pending_count` (`:514/916`): count only `meeting_revisions`; doc summaries show up in `Docs.pending_count`.

**Routes (`BE/app.py`)**
- `MeetingIn` (`:5361`) + `create_meeting` (`:5560`): `doc_id: str | None = None`; validate with `docs.get(doc_id)`
  (404/400 if missing or trashed). All literal sub-paths stay registered ABOVE `/meetings/{id}` (FastAPI order trap noted
  at app.py:5457).
- `GET /meetings` (`:5552`): `doc_id` query param.
- Optional one-shot convenience route `POST /docs/{id}/recordings` that creates the linked meeting and calls
  `meeting_svc.start` (register above `/docs/{id}`; `/docs/pending` shows the precedent at app.py:4918). Not required, the
  FE can call create + start.
- `start_meeting` (`:5603`), `stop_meeting` (`:5623`), pause/resume, `/segments`, `/transcript`, `/stream`: unchanged.

**Service (`MeetingService`)**
- `__init__` (`:1104`): add `docs: Any = None`; app.py:446 passes `docs=docs`.
- `start` (`:1299`): optional `segment_seconds` override (section 3). Nothing else changes: preflight, consent, `RecorderBusy`.
- `stop` (`:1397`): at `:1444-1449`, branch on `m["doc_id"]`: instead of `_enhance_quietly` call a new
  `_summarize_into_doc_quietly(meeting_id)`. Same branch in `_settle_after_drain` (`:1472`) and in
  `meeting_import.run` (`BE/meeting_import.py`, the `if cfg.get("enhanceOnStop")` after `finalize`).
- New `summarize_into_doc(meeting_id, force=False)`: read doc (`self.docs.get(m["doc_id"])`, returns None if trashed), build
  the section, call the new summarizer (below), scrub the markdown (`redact.scrub_secrets`, see section 6), then
  `self.docs.propose(doc_id, after=doc["content"] + section, summary="Recording summary", tool="recording_summary")`.
  Action items: `self.meetings.add_action_items(meeting_id, None, items)` (revision_id is nullable, so they still show
  and promote through `/meetings/{id}/actions/add-todos`). Patch `summary` headline and `status` back to `ready`.
  Do NOT auto-accept (see section 6, `docEditMode` trap).
- `enhance` (`:1527`): the meeting-style path stays for non-doc meetings; refuse or redirect for doc-linked rows.
- `context_block` (`:1790`): filter out `doc_id IS NOT NULL` rows from "## Recent meetings" (the doc is the content; the
  title would just be a copy of the doc title).
- `recover` (`:1825`): the "enhancing" note says "Enhance pass"; fine. A doc-linked meeting left `ready` without a
  pending doc revision after a crash needs a "Summarize again" button, not auto-retry.

**`meeting_notes.enhance` (what is meeting-specific, and the variant)**
Meeting-specific parts (`BE/meeting_notes.py`): `TEMPLATES` (`:20`, six meeting kinds with section lists/hints),
`ENHANCE_PROMPT` (`:53`: "rough meeting notes", "typed during the meeting", `[you]`/`[them]` channel attribution,
"never reorder their points", output key `enhanced_markdown`), `_template_block` (`:161`), payload keys `title/when/
duration/attendees/notes/transcript` (`:218-225`), `kind="meeting"` for the usage log (`:234`), `_mechanical` fallback
(`:188`, appends the raw transcript), `pick_model` (`:184`, reusable). Generic and reusable as-is: `cap_transcript`
(40/60 head/tail cap), `_parse_json`, `_action_items`, `_speaker_names`, the never-raises/degraded contract.
Variant to add (new function, same file, same `complete_fn` injection): `summarize_recording(*, complete_fn, settings, model,
doc_title, doc_content, transcript, duration_ms, max_transcript_chars)` with its own `DOC_SUMMARY_PROMPT`:
- return JSON `{"summary_markdown": "...", "action_items": [...], "headline": "..."}`; produce ONLY a new section; never
  restate or edit the doc text; the doc is given as read-only context so the section can reference its terms;
- treat transcript text as quoted third-party speech, never as instructions (this prompt rule is a defence, not a guarantee);
- keep the channel rule (`[you]`, `[them]`) and the diarization rule (`_system_prompt(names)` pattern);
- degraded fallback: return NO section and an error (do not paste the raw transcript into the doc; the existing meeting path
  already refuses to auto-apply its degraded fallback for the same reason, `meetings.py:1537-1541`). The UI then offers
  "Summarize again" and a link to the stored transcript.
Section shape (recommended): `\n\n## Recording summary (YYYY-MM-DD HH:MM, 12m)\n{summary_markdown}\n`. Appending means the user's text
is preserved; the proposal's `after` is `content_at_stop + section`.

**Delete cascade: what happens to the transcript when the doc goes**
- Doc delete is a soft trash: `DELETE /docs/{id}` -> `trash.trash("doc", id)` (app.py:5008-5011) sets `docs.deleted_at`;
  restore clears it; hard delete happens only on purge (`Trash.purge` -> `docs.delete`, `BE/trash.py:125`, `Docs.delete`
  `docs.py:402`, which also removes `docs_fts` and `doc_chunks_fts` by hand).
- While the doc is in the trash the linked meeting is untouched: `Meetings.get/list/search/find`, `meeting_list/
  search/read` tools and the FTS all still return its transcript (nothing joins on `docs.deleted_at`). Recommend: join/filter
  so a doc-linked meeting whose doc is trashed is hidden from list/search/tools (a trashed doc must not leak its recording).
- On purge, with `foreign_keys=ON` an FK `doc_id REFERENCES docs(id) ON DELETE CASCADE` would delete the meeting row and
  its segments/revisions/action items, but NOT `meetings_fts` (virtual table, no cascade) and NOT the `recordings/<id>/`
  directory. So `Docs.delete`/`Trash.purge` must call `meeting_store.delete(mid)` for each linked meeting BEFORE the doc row goes
  (`Meetings.delete` does the FTS row + `shutil.rmtree`). Hook shape: like `Docs.on_chunks` (`docs.py:165`), add
  `Docs.on_delete` set in app.py, or call `meeting_store` from `Trash.purge`. Without an FK, the explicit hook is the only
  mechanism; with `ON DELETE SET NULL` the transcript would become an orphan meeting visible in the Meetings rail (the
  alternative policy, "keep the transcript"; decide explicitly).
- Project delete: docs are demoted to personal (`Trash` `CHILD_TABLES = conversations, memories, documents`; docs not
  trashed), so linked meetings stay valid; `meetings.project_id` is `ON DELETE SET NULL` like docs.

**FTS**
- Keep `meetings_fts` for transcripts (via `finalize`/`_reindex`), as today. Do NOT also index the transcript into `docs_fts`
  or `doc_chunks`: `doc_search` and chunk retrieval are not tainted (section 6) and would hand third-party speech to the
  model untainted. Only the ACCEPTED summary becomes doc content and is indexed by `Docs.accept` -> `_reindex` as any edit.
- Add `doc_id` to `search()` output so the doc UI can show "mentioned in a recording of this doc".

**Tools (`BE/tools.py`)**
- `_register_meetings` (`:1906`): the three read tools keep working; add `doc_id` and the linked doc's title to
  `meeting_list` rows (`:1957-1966`). `meeting_read(part="transcript")` is how a model reads a doc recording; it stays
  `taints=True` (`:2064`).
- Do NOT add any record/start/stop tool and do not extend `doc_edit`/`doc_create`. `doc_read`/`doc_search` could surface
  "this doc has N recordings" (inferred, optional), pointing at `meeting_read`.
- `_meetings_ok` (`:572`) gates the whole `meetings` tool group on `enabled` + ffmpeg + STT backend != off. Unchanged.

**Context injection**
- `BE/context.py:180-186` (`useMeetings` toggle) -> `meetings.context_block()`; filter doc-linked rows (above).
- `app.py:1422` `ctx_taints` taints a turn if `used["meetings"]`; unchanged.
- The page agent (FE `usePageContext`, `DocsView.tsx:101`): add the recording state (live? meeting id) to `detail`.

---------------------------------------------------------------------------------------------------

## 5. Frontend reuse

Everything lives in the shared zustand store (`FE/store.ts`) with ONE active-meeting slot.

| Piece | Reusable as-is in Docs? | Props / coupling |
|---|---|---|
| `lib/transcript.ts` | Yes, all pure | `mergeSegments`, `speakerLabel`, `formatOffset`, `applyCursor`, `fetchSegmentPages`, `needsSegmentReload`, `recorderState`, `offerableCandidates`, constants `SEGMENT_PAGE`. Tested by `lib/transcript.test.ts`. |
| `components/MeetingRecorderBar.tsx` | Yes, with one caveat | No props. Reads `meetingStatus.active` (global, whichever meeting is live), `meetingBusy`, and actions `pollMeetingLive`, `refreshMeetings`, `stopRecording`, `pauseMeeting`, `resumeMeeting`. Mounts the 2 s tail + 5 s rail intervals when `active` exists. Caveat: it renders the live session even when the user is looking at a different doc, so the Docs host must render it only when `active.meeting_id === <this doc's live meeting id>`, or show a different "recording elsewhere" chip. Latency line uses `config.segmentSeconds` (global); a per-recording override needs the bar to read `active.segment_seconds` (new field in `status()["active"]`). |
| `components/MeetingConsentModal.tsx` | Yes, but mount it | No props; driven by store `meetingConsentOpen`, `meetingPreflight`, `loadMeetingPreflight`, `acceptMeetingConsent`, `setMeetingConsentOpen`; uses `useModal`. It is rendered ONLY inside `MeetingsView` (`MeetingsView.tsx:456`), so DocsView (or App) must render `{meetingConsentOpen && <MeetingConsentModal/>}` too. `acceptMeetingConsent` (`store.ts:2599`) stamps consent then calls `startRecording(consentIntent)`, which is meeting-specific (below). |
| Transcript pane | No, it is inline JSX | Lines `MeetingsView.tsx:327-366` (`<aside className="mtg-transcript">` with head, speaker chips, `lines.map`). Extract a `TranscriptPane({segments, meeting, recording})` component; it needs `m.sources`, `m.attendees`, `m.speaker_names`, `m.segment_count`, `m.status`. |
| Polling logic | Reuse the store pieces, not the view | `pollMeetingLive` (`store.ts:2392`) uses `get().activeMeeting?.id` and `meetingCursor`, so it assumes the meeting is `activeMeeting`. `loadSegments` and `liveTick` are closures inside `create()` (not exported). A doc recording should either set `activeMeeting` to the linked meeting without switching the view, or add a parallel `docRecording` slice and parameterize `pollMeetingLive`/`loadSegments` by id. |
| `startRecording(meetingId?)` (`store.ts:2266`) | No | Opens the consent modal if unconsented (`consentIntent`, `store.ts:756`), flushes the NOTES draft, creates via `api.meetings.create({title: newMeetingTitle()})`, starts, then `set({view:'meetings', activeMeeting, meetingSegments:[], ...})` and `watchMeeting`. A doc variant `startDocRecording(docId)` must create with `doc_id`, flush `docDraft` (`flushDoc`) not `meetingNotesDraft`, and NOT change `view`. |
| `stopRecording` (`:2300`) | Mostly | Stops the live id, toasts, sets `meetingSettleUntil` so the 5 s tick keeps going 90 s after stop (enhance/late segments), `loadSegments(id)`. Doc variant additionally refreshes the doc (`api.docs.get`) and `refreshDocsPending` so the new pending summary revision appears; `liveTick` does not know about docs. |
| `watchMeeting`/`meetingStream`/`applyMeetingEvent` | Yes | Idle today; the frame contract is ready for server pushes (section 3). |
| `MeetingsView` notes editor | No | Binds `meetingNotesDraft`/`editMeetingNotes`/`flushMeetingNotes`; the doc's own `MarkdownEditor` + `editDoc/flushDoc` replaces it. |
| `DiffView` + `acceptRevision` | Yes, already in DocsView | `DocsView.tsx:245-258` shows `activeDoc.pending`; the recording summary lands there if created through `Docs.propose`. `acceptRevision` (`store.ts:1880`) does `flushDoc()` then `api.docs.accept`. |
| `MeetingSettings` | Settings tab only | `variant='modal'` in `SettingsModal.tsx:268`, and the Meetings empty pane (`MeetingsView.tsx:257`). The Docs Record button must deep-link blockers (`enabled`, `consent`, `stt`) to Settings -> Meetings. |
| `MeetingIndicator` (sidebar) | Keep | `MeetingsView.tsx:462`, mounted in `Sidebar.tsx:296`; clicking does `setView('meetings')`. For a doc recording it should navigate to the doc instead (needs the live meeting's `doc_id`, i.e. `status().active.doc_id`). |
| CSS | Import it | `styles/meetings.css` is global (`.mtg-*`) and imported by `MeetingsView` and only there; import it from the Docs component. Layout classes `.mtg-panes`, `.mtg-transcript`, `.mtg-main` assume the Meetings grid. |

What is coupled to MeetingsView: the transcript pane markup, the consent modal mount, the `view:'meetings'` side effects in
`openMeeting`/`startRecording`, the notes autosave buffer, the single `activeMeeting` slot, and `refreshMeetings`
(which lists meetings for the rail and would show doc recordings unless filtered).

Doc-side hook points: the toolbar at `DocsView.tsx:200-243` (add a Record button beside the history button), the body grid
`div.doc-panes` at `:261-279` (add the transcript aside; mind `ResizeHandle`s and the `docs-history` aside), the page-context
`usePageContext` at `:101`.

Gotcha for the summary revision UX: `Docs.accept` (`docs.py:554`) replaces `content` with `r.after` wholesale (the stale flag
`stale = current != r.before` only labels the diff; `acceptRevision` flushes the draft first but then `after` overwrites it).
Anything typed after the summary was proposed is lost on accept. Mitigate by building `after` from the doc content AT PROPOSE
TIME after flushing the draft (backend `docs.get` is the truth), or by adding a `Docs.propose_append(doc_id, section)` that
resolves the base at accept time. (inferred fix; existing behaviour verified.)

---------------------------------------------------------------------------------------------------

## 6. Privacy and safety invariants to preserve

1. **Consent gate (backend-enforced).** `MeetingService.preflight` (`meetings.py:1270-1275`):
   ```
   if float(cfg.get("consentedAt") or 0) <= 0:
       blockers.insert(0, {"id": "consent", "label": "Recording consent", ...})
   ```
   `start` refuses when `pf["ok"]` is false (`meetings.py:1317-1319`). `consentedAt` has one writer: `POST /meetings/consent`
   -> `meeting_svc.consent()` (`app.py:5489`, `meetings.py:1142`). `MeetingConfigIn` deliberately omits it (docstring
   `app.py:5402`) and `meetings` is in `SETTINGS_READ_ONLY` (`app.py:627`). Same consent for import
   (`meeting_import.check`). The master switch (`enabled`) also blocks the start path (`meetings.py:1276-1285`). A doc Record
   button MUST go through `MeetingService.start`, never a new capture path, so these hold. The FE modal is UX; the backend
   blocker is the guarantee. Preserve the modal's "I will tell the other people" acknowledgement text.
2. **No model-triggered start/stop.** Quote `tools.py:1906-1915` (`_register_meetings` docstring): "Nothing here starts, stops
   or pauses a recording, enhances notes, appends to them, or deletes a meeting or its audio - at any danger tier ... Meeting
   lifecycle is a click or an HTTP route, full stop." `docs/meetings.md:297`. Do not register `doc_record`/`meeting_start`;
   `test_mcp_servers.py` asserts `RESERVED_TOOL_NAMES` covers built-in names.
3. **Taint on transcript reads.** `meeting_search` (`tools.py:2010`) and `meeting_read` (`tools.py:2064`) are `taints=True`;
   `_missing` (`tools.py:1934-1941`) arms the gate even on an error-shaped result because it leaks titles. Context injection
   taints: `app.py:1422` `ctx_taints = [k for k in ("meetings", "activity", "chunks") if used.get(k)]`. **Doc tools are NOT
   tainted** (no `taints=True` on `doc_read`/`doc_search`, tools.py:1801-1830), so the accepted summary becomes ordinary doc text:
   it is third-party speech laundered through an LLM. Defences: (a) it is a PENDING `doc_revisions` row the user reviews, (b)
   never apply it automatically: `doc_edit` honours `docEditMode == "apply"` (`tools.py:1875`), the summary path must not,
   (c) prompt rule that the transcript is quoted data, (d) if you add a doc-side transcript view, route it through
   `meeting_read`, not `doc_read`.
4. **Meetings never reach auto-learn.** There is no `meeting` code in `learn.py`; the guard is the taint gate. `app.py:2276`:
   `if (not error and text and not proposal_only(run) and not tool_ctx["tainted"] and cfg.get("autoLearn", True) ...)`
   `learner.submit(LearnJob(...))`; a turn that read a meeting (tool taint) or had meetings injected (`ctx_taints`) is tainted
   and never learned from (`app.py:2268-2275` comment). `docs/meetings.md:159-162`. Do not add any
   `learn_from_exchange` call on the recording summary or transcript. Related trap (inferred): `save_doc` banks the doc body
   as a voice sample (`style.add_sample(..., source="doc", ref=f"doc:{id}")`, app.py:4987-4990). `Docs.accept` does not call it,
   but the next autosave of that doc will, so an accepted summary of other people's speech can be banked as the user's own
   writing. Consider skipping sample banking for docs that have linked recordings, or marking the section.
5. **Secret scrubbing.** `Meetings.finish_segment` (`meetings.py:695-716`): when `config()["redactSecrets"]` (default True) it
   runs `redact.scrub_secrets(text)` on `text` AND `_scrub_detail(detail)` on the verbose payload (credential rules only; email
   and phone stay). `redact.scrub_secrets` at `BE/redact.py:84`. This is the ONLY scrub point, so anything that reads a segment
   straight from `_on_result`'s `res["text"]` (e.g. a live push) would bypass it; publish the row returned by `finish_segment`.
   The LLM summary is not scrubbed today (the meetings enhance path doesn't either); recommend `redact.scrub_secrets` on the
   summary before `Docs.propose`. (inferred recommendation.)
6. Other invariants: durability (no `expires_at`, `/activity/purge` cannot reach meetings); audio deleted per segment unless
   `keepAudio` or failed (`meeting_recorder.py:509-526`), under `data_dir/recordings/<id>/` (never `tmp/`, which is swept on
   shutdown, `app.py:3885`); `notes`/`enhanced` single-writer split (a doc-linked meeting should leave `meetings.notes` empty
   and use the doc as the notes); degraded enhance never auto-applies; no window-title exclusion list for meetings; one live
   recording at a time (`RecorderBusy`); pause discards audio, never queues it.

---------------------------------------------------------------------------------------------------

## 7. Testing

### How tests run
- `backend/conftest.py` makes `pytest` collect ONE item per `test_*.py` file and run each in its own subprocess with a fresh
  `PERSONAL_OS_DATA_DIR` (a `tempfile.mkdtemp`), `PYTHONPATH=backend`, `PERSONAL_OS_TEST_CHILD=1` (`conftest.py:FileItem.runtest`).
  pytest-style files (any top-level `test*` function/class) run under an inner `python -m pytest -q file`; script-style files (no
  tests, or a bare `setup()`, or a module-level skip with `__main__`) run as `python file`. The summary prints
  `inner totals: ...`, the real count.
- Run one file: `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_meetings.py -q` (use the worktree's
  backend on `PYTHONPATH`, otherwise a shared venv may import the MAIN checkout's `personal_os`; this is the known gotcha).
  Or as a script: `python backend/tests/test_meetings.py` (each meeting test file has a `if __name__ == "__main__"` runner
  loop, e.g. `test_meetings.py:919`). The docs file `test_docs.py` is script-style (module-level `check()` against a `TestClient`).
- Doc says: `cd backend && uv run --with pytest pytest tests/test_audiocap.py tests/test_redact.py tests/test_stt.py
  tests/test_meeting_recorder.py tests/test_meeting_notes.py tests/test_meetings.py tests/test_mcp_servers.py -q`
  (`docs/meetings.md:419`). Also relevant: `test_meeting_import.py`, `test_meeting_vad.py`, `test_native_audio.py`,
  `test_diarize.py`, `test_docs.py`, FE `npm test` (`src/renderer/src/lib/transcript.test.ts`).

### Running capture with no microphone
- Native sine: `audiocap.native_sine_input()` (`audiocap.py:250`) -> `["native","sine","440"]`; `ChannelCapture` dispatches on
  `audiocap.is_native_input(spec)` and `native_audio.Capture.from_spec` builds a `kind="sine"` capture that generates a
  16 kHz tone paced to wall clock (`native_audio.py:223`). Needs no ffmpeg, no pyobjc, no permission. Used in
  `test_meeting_recorder.py:126,152,274,582-588`.
- ffmpeg sine: `audiocap.synthetic_input()` (`audiocap.py:229`) -> `-re -f lavfi -i sine=frequency=440:sample_rate=16000`; real
  ffmpeg, so those tests skip with a note if no binary (`test_meeting_recorder.py:170-251`).
- Silence also works: `{"mic": ["-f","lavfi","-i","anullsrc"]}` (`test_meeting_recorder.py:652`).
- Disable the gates because the tone is not speech: `cfg = {"vadGate": False, "hallucinationFilter": False, "sttBackend": "proxy",
  "sttModel": "whisper-1"}` (`test_meeting_recorder.py:25-27`, `test_meeting_import.py:_svc`).

### Stubbing the LLM and STT
- LLM: `MeetingService(db, settings_fn, complete_fn, meetings)`; `complete_fn(settings, model, messages, kind="learn")` is an
  async stub returning a JSON string, optionally raising for the degraded path (`test_meetings.py:_svc` lines 93-107;
  `test_meeting_notes.py:_stub`). Reply shape for enhance: `{"enhanced_markdown", "decisions", "action_items", "topics",
  "headline"}` (`GOOD_REPLY`, `test_meetings.py:33`). The new doc summarizer needs its own `GOOD_DOC_REPLY` in the new shape.
- STT: swap `stt.transcribe` per module (`transcribes_as`, `test_meeting_recorder.py:30-60`, patches
  `meeting_recorder.stt.transcribe`; signature `(path, *, settings, cfg, data_dir, prompt="")` returning
  `{"text","detail","backend","error","ms"}`) or the service-level `stt_is` (`test_meetings.py:564`) that pins
  `resolve_backend`, `transcribe` and `validate_wav`. `selftest_ok` (`:585`) pins `stt.selftest`; `devices_are(...)` (`:42`)
  pins the ffmpeg device cache so `capabilities()` spawns nothing.

### Recipe: doc-linked recording test, no mic, no live LLM
Pytest-style file `backend/tests/test_doc_recording.py` (add a `__main__` runner like the others):
1. `tmp = Path(tempfile.mkdtemp()); db = Database(tmp); docs = Docs(db); repo = Meetings(db); svc = MeetingService(db, lambda: dict(SETTINGS), fake_complete, repo, docs=docs)`.
2. `doc = docs.create("Plan", "# Plan\n- item")`; `m = repo.create(title=doc["title"], doc_id=doc["id"], status="scheduled")`.
3. `svc.set_config({"enabled": True, "consentedAt": time.time(), "sttBackend": "proxy", "vadGate": False,
   "hallucinationFilter": False, "enhanceOnStop": True, "segmentSeconds": 1})`.
4. Force the capture source: monkeypatch `meetings.native_audio.mic_available = lambda: True` and
   `meetings.audiocap.native_mic_input = lambda uid="": audiocap.native_sine_input()` (the service builds the channel spec at
   `meetings.py:1328-1340`), wrap in `devices_are("Test mic")`, `selftest_ok()` and `stt_is("hello world")`. The `platform`
   capability row is `audiocap.IS_MAC` (`meetings.py:1192`), so on Linux CI also patch `meetings.audiocap.IS_MAC = True`
   (inferred) and `native_audio.mic_available`. `capabilities()` also calls `native_audio.system_available()`; harmless.
5. `svc.start(m["id"])`, `time.sleep(3.5)`, `asyncio.run(svc.stop(m["id"]))`. Assert segments `state == "done"`, `transcript`
   non-empty and `[you]`-prefixed, `meetings.status == "ready"`, `meetings.doc_id == doc["id"]`.
6. Await the queued summary: either call `asyncio.run(svc.summarize_into_doc(m["id"]))` directly (deterministic) or
   `asyncio.run(asyncio.sleep(0.2))` after stop. Assert `docs.get(doc["id"])["pending"]` has one revision with
   `tool == "recording_summary"`, `after.startswith(doc["content"])`, `docs.get(...)["content"]` UNCHANGED (never
   auto-applied), `repo.get(m["id"])["pending"] is None` (no `meeting_revisions` row), action items present.
7. Degraded path: `fake_complete` raising -> no pending doc revision, `error` set on the meeting, transcript intact.
8. Single live session: start a second doc recording while the first is live -> `meeting_recorder.RecorderBusy`.
9. Delete: purge the doc via `Trash`/`docs.delete` hook -> `repo.get(mid)` is None, `meetings_fts` has no row, `recordings/<id>`
   gone (mirrors `test_deleting_a_meeting_leaves_no_ghost_fts_row`, `test_meetings.py:238`).
10. Privacy: with `consentedAt=0` start raises `MeetingBlocked` whose first blocker id is `consent`
    (`test_consent_is_the_only_thing_that_unblocks_the_consent_blocker`, `test_meetings.py:468`); and
    `redactSecrets` scrubs a segment containing a fake key (`test_a_transcript_keeps_the_people_and_loses_the_credentials`, `:366`).
For a faster, even more hermetic variant skip the capture entirely: insert segments by hand with `repo.add_segment(...)` +
`repo.finish_segment(...)` and call `svc.stop`-equivalents (`repo.finalize`, `svc.summarize_into_doc`) as
`test_stop_says_so_when_the_drain_gave_up...` does (`test_meetings.py:813-858`). Or reuse the file-import path
(`meeting_import.run(svc, id, wav_path)` with a synthetic 50 s sine from ffmpeg, `test_meeting_import.py:_sine`) which
exercises the full transcribe->finalize->enhance chain with no live capture; for doc import this is the cheapest end-to-end test.
Route tests (`TestClient`) follow `test_docs.py`; set `PERSONAL_OS_DATA_DIR` before importing `personal_os.app`. Avoid app.py
import side effects unless you need routes (the import tests keep to the service layer on purpose).
FE tests: add pure functions to `src/renderer/src/lib/transcript.ts` and cover them in `transcript.test.ts` (no React).

---------------------------------------------------------------------------------------------------

## Appendix: traps checklist
- One live recording app-wide (`RecorderPool.start` raises `RecorderBusy`); the status/UI model has a single `active`.
- `since=` rowid cursor never redelivers a segment whose text arrived after the row was first polled; liveTick's full reload
  (5 s) is what actually delivers text.
- `meeting_bus` is a `RunBus` of chat runs, not a generic pub/sub; publishing needs a Run and thread-safe hand-off, and must
  publish the scrubbed row.
- `segmentSeconds` is global config; add a per-start override rather than mutating settings (set_config never touches a
  live recording by design).
- `meeting` restart is not allowed (`already_recorded`); one meeting row per recording.
- Literal routes must be registered above `/meetings/{id}` and `/docs/{id}`.
- `ALTER TABLE ADD COLUMN` + index ordering on upgraded databases; `meetings_fts` and the audio dir are not covered by FK cascade.
- Doc trash is soft: linked transcript stays queryable unless filtered by the doc's `deleted_at`.
- `Docs.accept` overwrites content with the proposal's `after`; typing between propose and accept is lost.
- Doc tools are untainted: only user-accepted summaries may enter doc text, never auto-applied, never the raw transcript.
- Meetings view and nav are default-off (`_DEFAULT_OFF_VIEWS`, app.py:288) but `meetings.enabled` is a separate switch;
  the doc Record button must handle `enabled=false`, no consent, STT not ready, non-macOS (start returns 400).
- `docs/meetings.md` states the per-meeting SSE ships idle; keep that doc in sync if you change it.
