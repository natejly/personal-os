# Meetings

A notepad that listens. You type during a call, the app records the audio,
transcribes it in the background, and afterwards proposes a cleaned-up version of
your notes with the transcript filled in around them — as a diff you accept or
reject.

It is off until you turn it on, nothing is recorded without a one-time
acknowledgement, and the notes you typed are never overwritten by a model.

---

## What it records

A meeting is the fourth text-bearing thing in the app, beside docs (markdown you
write), documents (files you upload) and notes (canvas stickies). Five things land
in the database, and they are kept in separate columns on purpose.

| Part | What it is | Who writes it |
| --- | --- | --- |
| **Notes** | What you typed during the call | **Only you.** No model ever writes this column |
| **Transcript** | What was said, labelled `[you]` / `[them]` by audio channel | The transcription backend, rolled up once on finalize |
| **Enhanced notes** | Your outline with the transcript filled in around it | Set *only* by accepting a revision |
| **Action items** | Tasks the enhance pass proposed, promotable into todos | The enhance pass; promotion is a click |
| **Audio** | The wav segments under `<data_dir>/recordings/<id>/` | Deleted as each segment transcribes, unless **Keep audio** is on |

Two capture channels, each a separate switch:

| Channel | What it captures | Needs |
| --- | --- | --- |
| **mic** | You and whoever is in the room | AVAudioEngine + Microphone permission |
| **output** | Whatever your speakers played — i.e. everyone else on the call | Core Audio process tap (macOS 14.2+), or a loopback device on older Macs |

Default sources: **mic** only. Without a process tap or loopback driver the `output` channel is
reported as unavailable in the checklist and the meeting records one-sided, which
is a degradation and not a failure.

## How it works

```
ChannelCapture ×N ──▶ segment files ──▶ queue ──▶ TranscribeWorker ──▶ meeting_segments
  native AVAudioEngine    wav, closed        in-        stt.py: speech |        one row per
  or ffmpeg fallback      every 20s        memory        local | proxy | off    closed segment
  ┌────────────────────────────────────────────────────────────────────────────┘
  ▼
finalize ──▶ transcript ──▶ enhance ──▶ pending revision ──▶ accept
(once, on    (interleaved      (LLM)        (a diff, rendered    (the only writer
 stop)        rollup)                        by DiffView)         of `enhanced`)
```

**ChannelCapture** (`meeting_recorder.py`) is one long-lived capture per channel.
Native mic uses AVAudioEngine; system audio uses a Core Audio process tap on
macOS 14.2+ so a BlackHole loopback is not required. ffmpeg avfoundation is the
fallback when the native path cannot start. It closes a finished wav every
`segmentSeconds` and keeps recording while the previous one is in flight. That is
the difference from the activity monitor's `AudioCollector`, which records for *n*
seconds and then blocks on transcription — everything said during that block is
simply never captured. Here, falling behind costs latency, not audio. Native
teardown stops the engine and flushes the ring; the ffmpeg fallback writes `q\n`
to stdin rather than sending a signal. Every closed segment is checked as a real
wav (the stdlib `wave` module, then ffprobe if the header is truncated).

**TranscribeWorker** drains a queue on a second thread and calls `stt.py`, which
has four backends resolved by `sttBackend`:

- `speech` — Apple's on-device Speech framework. Audio never leaves the machine.
  Needs Speech Recognition granted to the app (macOS asks on the first transcribe).
- `proxy` — POSTs the wav to `/v1/audio/transcriptions` on your configured base
  URL. **A default `litellm.yaml` has no model behind that path**, so this fails
  until you add one (see `litellm.yaml`'s commented block).
- `local` — `whisper-cli` from `whisper-cpp` against a `ggml-*.bin` model. Audio
  never leaves the machine.
- `off` — keep the notes, produce no transcript. A legitimate choice, not a
  failure state.
- `auto` — `speech` if the recognizer is authorized, else `local` if the binary
  and a model are both present, else `proxy`.

A transcription failure is a row of data, not an exception: the segment gets an
`error` string, keeps its wav, and is retried by the 45-second tick (three per
tick, so a dead route is retried rather than hammered) or on demand with
**Retranscribe**.

**finalize** runs once, on stop. It interleaves every `done` segment by its
recording-clock `t_start` into the `transcript` column and reindexes FTS. Nothing
rebuilds that column per batch, because nobody searches a running meeting.

**enhance** (`meeting_notes.py`) is one LLM call: your notes are the outline and
the transcript is only allowed to fill them in, against a template
(`general`, `standup`, `one_on_one`, `user_interview`, `sales_call`, `lecture`). It returns
markdown, a headline, decisions, topics and proposed action items, and the caller
stores it as a `meeting_revisions` row — the same field shape as `doc_revisions`,
so the Docs editor's `DiffView` renders it with no adapter. If the LLM is down you
still get a revision, flagged `degraded`, built mechanically from your notes plus
the transcript.

**accept** is the only thing that ever writes `enhanced`. The revision is
auto-accepted for you when `enhanced` is empty or still byte-identical to the last
accepted version — i.e. you have not hand-edited it. Once you have, the proposal
waits. That keeps the "the notes just appear" feel without a model ever
overwriting a human edit.

**The recorder bar** polls `/meetings/{id}/segments?since=` every two seconds and
says plainly how far behind it is: `transcript ~20s behind · 3 queued`. A
per-meeting SSE route and client ship (`GET /meetings/{id}/stream`,
`meetingStream`) but nothing publishes to the meeting bus yet, so the stream opens
and finishes immediately and the poll is what carries the pane. Pushed updates do
exist, but on a different channel: the service publishes `recording` events
(segment settled, status change, summary landed) on the app-wide `GET /events`
for every recording. Only the Docs view listens, for recordings made in a doc; the
Meetings view still polls.

**Silence gate and hallucination filter.** Before a segment is sent to STT,
`meeting_vad.analyze` (stdlib adaptive energy VAD) measures its speech ratio; below
`vadMinSpeechRatio` (0.03) the segment is stored as `empty`, its wav deleted, and no
billed call is made. After a reply, `stt.filter_hallucinations` applies faster-whisper's
rules per verbose_json segment (`no_speech_prob > 0.6` with `avg_logprob < -1.0`, or
`compression_ratio > 2.4`), drops known silence phrases ("Thank you.") only when the
segment's speech ratio is under 0.15, and collapses repetition loops; dropped phrases
land in `detail.filtered`. Local whisper now runs with `-oj`, so `detail.segments`
carries start/end, and passes `--vad -vm` when `whisperVadModelPath` (or a
`ggml-silero*.bin` in `<data_dir>/models`) exists. Switches: `vadGate`,
`hallucinationFilter` (both default on); turning both off restores the old behaviour.

**Recordings made inside a doc** are the same machinery with a `doc_id` on the
row, a shorter clip ceiling that closes at a pause (`docSegmentSeconds`, default
10; `dictationSegmentSeconds`, default 8; `segmentSeconds` is unchanged for
meetings), and a summary that is proposed into the doc instead of a revision of
your notes. See [docs-editor.md](docs-editor.md#recording-into-a-doc). They are not
meetings of their own: `GET /meetings` and the "Recent meetings" block leave them
out, and `meeting_list`, `meeting_search` and `meeting_read` do include them.

**The calendar nudge** is a 45-second tick with no LLM in it. It lists events
happening now across `calendarIds` with at least `minAttendees` people, upserts a
`scheduled` row per event, and offers a Record button on Today and in the rail. A
partial unique index on `calendar_event_id` is what stops it creating a duplicate
meeting every tick.

## Privacy

Recording a call captures people who never opened this app, so the posture here is
different from the activity monitor's in four deliberate ways.

**A one-time acknowledgement, with the real paths in it.** Before the first
recording this install ever makes, a modal names the exact directory the wavs are
written to and the exact base URL each clip is uploaded to — not "securely
stored", not "your data stays private" — and will not enable anything until you
tick a box saying you will tell the others on the call. It is stored as
`consentedAt` and asked once. Until it is set, `POST /meetings/{id}/start` is a
409 with a `consent` blocker.

**No window-title exclusion.** The activity gate drops a whole stretch while an
excluded app is in front, which is right for a timeline and wrong for a
conversation: dropping twenty seconds of a call because 1Password came to the
front would silently hole the meeting with no indication in the transcript that
anything is missing. Meetings have no exclusion list.

**Credential-only redaction.** `redact.scrub_secrets` keeps the rules that catch
private keys, cards, SSNs, tokens, AWS keys, JWTs and high-entropy runs, and drops
the two the gate applies that destroy a conversation: `email` replaces every
address with `[email]` and `phone` every phone-shaped run of
digits with `[phone]` (both in `redact.py`). A leaked API key is a breach; a
colleague's email address inside their own meeting is the point. Switch it off
entirely with `redactSecrets: false`.

**Nothing starts itself by default.** There is no background capture: a recording
begins with a click or an HTTP call. The one exception is opt-in and off by
default — `autoRecord` will start a meeting the nudge found once it has begun, and
even then only if consent is acknowledged and the preflight passes; a blocked
auto-start writes the reason onto the row instead of failing silently.

**Meetings never reach auto-learn.** Nothing feeds a meeting into
`learn_from_exchange`, which writes durable memories re-injected into unrelated
chats. docs/research/roadmap.md:216 already flags auto-learn as an injection-persistence
channel, and anything said on a call becoming a permanent memory is worse.

**Transcripts are untrusted third-party content.** `meeting_search` and
`meeting_read` carry `taints=True`: for the rest of that turn any tool that acts
outside the app asks before it runs. Instructions inside a transcript are quotes
to report, never requests to follow.

Three independent ways to stop a meeting reaching a chat:

1. `injectContext` off — keeps recording, injects nothing.
2. The **Meetings** toggle in a chat's Context drawer (`useMeetings`) — per chat.
3. Delete the meeting.

What chats actually receive is a short "## Recent meetings" block of titles and
*accepted* notes (recordings made in a doc are not in it). Raw transcript is never injected; it is reachable only through
`meeting_read`, by explicit tool call.

## Retention

**Durable by design.** There is no `expires_at` column anywhere in
`meetings.py`, and that absence is the feature. An `activity_events` row carries
one and is swept on every `Monitor.loop` tick, and `POST /activity/purge` runs a
bare `DELETE FROM activity_events` behind the Privacy tab (`Store.purge` in activity.py, scopes `events` and `all`).
Nothing under `/activity/*` can reach a meeting. A meeting goes away when you
delete it, and only then.

Audio is the exception: each wav is unlinked the moment its segment transcribes
successfully, so `<data_dir>/recordings/<id>/` is normally empty while the meeting
is still there. Two things keep one:

- **Keep audio** (`keepAudio`, per install or per meeting) retains every segment.
- A **failed** segment always keeps its wav, so **Retranscribe** can replay it
  once your STT route works. Today that is every segment on a default config.

`maxAudioBytes` (2 GiB) is the ceiling, evicting oldest-failed-first, because
"keep failed wavs forever" on a machine where everything fails is a disk bomb.
`DELETE /meetings/{id}/audio` drops the wavs now and leaves the rows, so the UI can
say why retranscribe is no longer possible.

A recording made in a doc is the one case where something other than you deletes
a meeting row: purging the doc (not trashing it) removes its recordings, segments,
FTS rows and audio directory. Trashing only hides them until the doc is restored.

Deleting a meeting cascades its segments, revisions and action items, removes its
FTS row by hand (the virtual table is not covered by `ON DELETE CASCADE`) and
rmtree's its recordings directory. Action items already promoted into todos stay:
`todo_id` has no foreign key on purpose, so deleting the task does not erase the
record that this meeting produced the item.

## Permissions

Microphone permission is required. In System Settings → Privacy & Security →
**Microphone**, enable the app. macOS attributes the grant to the bundle, so it
is **Grain** in a packaged build and **Electron** in development, and a grant
made after launch does not take until the app restarts.

On-device transcription uses **Speech Recognition** in the same pane. The first
time you press Test or Record with the Speech backend, macOS asks. Packaged
builds include the usage string in Info.plist; in development the grant still
lands on Electron.

Capture itself is native (AVAudioEngine / Core Audio tap). ffmpeg is optional:
it remuxes a truncated wav and is the fallback on a Mac where pyobjc is missing.

```bash
# Optional fallbacks, not required on macOS 14.2+ with the activity extras installed:
brew install ffmpeg          # truncated-wav repair and the ffmpeg capture path
brew install whisper-cpp     # if you prefer whisper.cpp over Apple Speech
```

### System audio

macOS 14.2+ can record its own output with a Core Audio process tap. Grain uses
that when `CATapDescription` is present, so you do not need BlackHole or a
Multi-Output Device. On older macOS the `output` channel still needs a loopback
driver:

```bash
brew install blackhole-2ch
```

1. Open **Audio MIDI Setup** (`/Applications/Utilities`).
2. Click **+** at the bottom left → **Create Multi-Output Device**.
3. Tick both **your real output** (MacBook Pro Speakers, or your headphones) and
   **BlackHole 2ch**. Put your real output **first** in the list.
4. Tick **Drift Correction** on BlackHole, but *not* on your real output — the
   first device in the list is the clock master and correcting it causes
   artefacts.
5. Right-click the new Multi-Output Device → **Use This Device For Sound Output**.
6. In Grain: Settings → Meetings → **System audio device** → **BlackHole 2ch**,
   and add `output` to the sources.

Two things to know afterwards: the system volume keys do not control a
Multi-Output Device (set the level on the real output inside Audio MIDI Setup, or
in the app you are listening to), and video-call apps sometimes need to be told
the new device explicitly.

### On-device transcription (no cloud)

```bash
brew install whisper-cpp

# The model goes in <data_dir>/models/. On a fresh install data_dir is
# ~/Library/Application Support/Grain/data; installs that predate the rename keep
# .../personal-os/data, so copy the exact command from the panel instead of pasting this one.
DATA="$HOME/Library/Application Support/Grain/data"
mkdir -p "$DATA/models"
curl -L -o "$DATA/models/ggml-base.en.bin" \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

`sttBackend: auto` picks Speech when it is granted, then this whisper.cpp path as
soon as both the binary and a model exist, and audio then never leaves the
machine. The Meetings panel's `stt_speech` and `stt_local` rows print the exact
next step with *your* data directory already substituted. `whisperModelPath`
overrides the location if you keep models elsewhere.

The panel's capability checklist probes `platform`, `ffmpeg` (now “Audio capture”:
native or ffmpeg), `mic`, `loopback`, `stt`, `stt_speech` and `stt_local`, prints
the exact fix for anything missing, and never triggers a permission prompt. On
top of that, **Test** (`POST /meetings/selftest`) writes a short wav and does a
*real* round trip, because a capability probe cannot tell you whether
transcription actually works. A failing self-test **blocks** Start rather than
warning, and the blocker is cached for ten minutes so a panel poll cannot fire a
120-second POST. A 409 on Start lists the same blockers.

## Tools

Three tools, all read-only:

- `meeting_list(limit, offset, since_days, project_id)` — previews, newest first.
  No notes body and no transcript. Finds the id when you say "the pricing call".
  Rows for recordings made in a doc carry `doc_id` and `doc_title`; other rows have
  `doc_id: null`.
- `meeting_search(query, project_id, limit)` — FTS across titles, your notes,
  enhanced notes and transcripts, one row per meeting with a snippet and a
  `found_in` saying which of those matched. **Taints the run.**
- `meeting_read(meeting, part, from_line, to_line)` — one part as numbered lines:
  `enhanced`, `notes`, `transcript` or `actions`. Paged, because a tool result is
  truncated before the model sees it. **Taints the run.**

**Nothing that starts, stops, pauses, enhances, summarizes, appends to or deletes a
meeting is registered as a tool, at any tier.** That is a decision, not an omission. The
precedent is `activity_pause`, registered at danger `writes`, which `DEFAULT_MODE`
resolves to mode **on** with no approval card (tools.py:32, 978-980) — so a polite
or prompt-injected model can switch capture off today. Add `meeting_record` beside
it and the same injection can switch capture *on*. Lifecycle is a click or an HTTP
route, full stop.

## API

| Route | Purpose |
| --- | --- |
| `GET /meetings/status` | Enabled, consented, config, live session (with `doc_id`, `doc_mode` and its `segment_seconds`), upcoming, devices, STT, counts |
| `GET`·`POST /meetings/preflight` | Blockers, the capability checklist and the last self-test. `?force=` re-probes |
| `PUT /meetings/config` | Deep-merged config patch; never touches a live recording. Returns the status |
| `GET /meetings/config` | The merged config alone |
| `POST /meetings/consent` | Stamp `consentedAt`. The only writer of that key |
| `POST /meetings/selftest` | Synthesize a wav and do a real transcription round trip |
| `GET /meetings/devices` | avfoundation audio inputs, each flagged `loopback`. `?refresh=` re-probes |
| `GET /meetings/suggest` | Calendar events worth taking notes on now. `[]` when Google is not connected |
| `GET /meetings/search` | FTS over titles, notes, enhanced notes and transcripts |
| `GET /meetings/pending` | `{"pending": n}` — enhance proposals awaiting review (the sidebar badge) |
| `POST /meetings/revisions/{rev_id}/accept` | Write the proposal into `enhanced` |
| `POST /meetings/revisions/{rev_id}/reject` | Discard it; `enhanced` is untouched |
| `GET /meetings` | The rail: previews, no bodies. `?project_id=&q=&status=&since_days=&limit=`. Recordings made in a doc are left out unless `include_docs=true`; `doc_id=` selects one doc's |
| `POST /meetings` | Create one. A repeat `calendar_event_id` returns the existing row. `doc_id` and `doc_mode` (`record` or `dictate`) link it to a doc (404 for a missing or trashed doc, 400 for another mode); the row takes the doc's project |
| `GET /meetings/{id}` | One meeting with its pending revision and action items |
| `PUT /meetings/{id}` | Patch title, notes, enhanced, summary, template, keep_audio, project |
| `DELETE /meetings/{id}` | Idempotent. Cascades segments, revisions, items, FTS and the wavs |
| `POST /meetings/{id}/start` | Begin capture. A doc recording starts through `POST /docs/{id}/recordings`, which calls the same service. 409 with `{"blockers": [...]}` when preflight fails |
| `POST /meetings/{id}/stop` | Drain the backlog (up to `drainSeconds`), finalize, queue enhance |
| `POST /meetings/{id}/pause` · `/meetings/{id}/resume` | Stop writing segments without tearing ffmpeg down |
| `GET /meetings/{id}/segments` | The transcript rows. `?since=` is a rowid cursor; `?offset=&limit=&channel=` pages |
| `GET /meetings/{id}/stream` | Per-meeting SSE. Ships idle: nothing publishes to the bus yet. Live pushes are `recording` events on `GET /events` |
| `GET /meetings/{id}/transcript` | The rolled-up transcript, paged by line |
| `POST /meetings/{id}/enhance` | Propose enhanced notes. Returns the revision. `?force=&template=`. 400 for a doc recording |
| `POST /meetings/{id}/summarize` | Doc recordings only: propose a summary as a pending append revision of the doc. `{template?, focus?, force?}`. Never applied here. 400 for an ordinary meeting; a model failure is a 200 with `error` and no revision |
| `GET /meetings/{id}/revisions` | Every proposal, newest first |
| `GET /meetings/{id}/actions` | Proposed action items and whether each became a todo |
| `POST /meetings/{id}/actions/add-todos` | Promote items into todos. Empty `ids` means all still proposed |
| `POST /meetings/{id}/actions/{action_id}/dismiss` | Drop one item |
| `POST /meetings/{id}/retranscribe` | Replay failed segments and rebuild the transcript |
| `DELETE /meetings/{id}/audio` | Delete the wavs, keep the rows |

Every one of these is behind the app's auth token, like the rest of the API. The
literal sub-paths are all registered *above* `/meetings/{id}`, or
`/meetings/status` would resolve as a meeting id and 404 — so a new route appended
to the bottom of the group is a silent break, not a compile error.

One thing deliberately absent: there is no `POST /meetings/adopt` (a repeat
`POST /meetings` with the same `calendar_event_id` is find-or-create, which is the
same thing).

`POST /meetings/{id}/import-audio` (multipart `file`, 202) transcribes an existing
recording into a meeting that has no segments yet, on any platform. The upload is
streamed to `<data_dir>/tmp` (1 GiB cap), probed (`maxImportSeconds`, default 14400,
else 400), cut by ffmpeg into the live 20 s wav scheme and drained by the same
`TranscribeWorker` as `import`-channel segments on the file's own clock, then the
transcript is rolled up, the meeting goes `ready` and enhance is queued when
`enhanceOnStop` is on. It needs the same `consentedAt` as recording (409 with the
consent blocker), and refuses a meeting that is recording or already has segments.
Failures behave like live ones: the wav is kept and Retranscribe replays it.
Logic lives in `meeting_import.py`; progress is the existing segments poll.

In the UI: the **Meetings** view (⌘⇧M, or the sidebar row), an **Upcoming
meetings** card on Today, and a live-recording indicator in the sidebar visible
from every view. The sidebar badge is the number of enhance proposals awaiting
review, not a meeting count. The sidebar row and the Today card both start
hidden; turn them on in Settings → Modules. *Recording* is a separate switch
(`meetings.enabled` plus the consent acknowledgement) and is off.

## Limits

- **macOS only.** Capture is AVAudioEngine / a Core Audio tap, with ffmpeg as
  fallback. `POST /meetings/{id}/start` returns 400 elsewhere with the platform
  row's own fix string, the way `POST /activity/start` does. Everything else —
  notes, templates, enhance, search, the tools — works on any platform.
- **System audio on older macOS still needs a loopback driver.** macOS 14.2+ has
  a process tap and Grain uses it. Before that, `brew install blackhole-2ch` needs
  an admin password and a Multi-Output Device built by hand or your speakers go
  silent. Without either path a meeting records mic-only, which is honest in the
  checklist and still useful — you get your half plus whoever is in the room.
- **Speaker attribution is channel-level, not per person.** mic is you, output is
  everyone else. docs/activity-monitor.md:180 already says transcription is not
  speaker-aware; here a four-person call reads as `[them]` throughout. The seam for
  a later diarization pass is open with no migration needed —
  `meeting_segments.speaker` exists and is empty, and `detail` holds the
  `verbose_json` utterances such a pass would read.
- **The Microphone and Speech Recognition grants land on the bundle, not on Grain.**
  In development that is **Electron**, not Grain, and a grant made after launch
  does not take until the app restarts. `capabilities()` never prompts.
- **A crash loses the tail of the segment being written.** ffmpeg flushes WAV
  output in 256 KiB blocks: measured on this machine, a realtime 16 kHz mono
  16-bit capture sat at 0 bytes for seven seconds, jumped to exactly 262144 at
  eight, and to 524288 at sixteen — 8.19 seconds of audio per block. A hard kill
  therefore costs up to that much from the open file. A clean stop costs nothing:
  `q` on stdin exits 0 and flushes a valid final segment, where a kill during a
  segment rotation leaves a 0-byte file instead, which is why every closed segment
  is `ffprobe`'d rather than size-checked. Shorter `segmentSeconds` means less
  worst-case loss and more HTTP requests.
- **Retrieval is literal FTS5, not embeddings.** "pricing" finds a meeting that
  said "pricing"; it will not find one that only said "what we charge". That is
  deliberately in character — memories, documents and docs all search the same
  way — but it means `meeting_search` wants keywords, not paraphrases.
- **Cost scales with meeting length.** The enhance pass sends up to
  `maxTranscriptChars` (48k, head *and* tail, because decisions land at the end)
  and is logged in `usage_log` under `kind="meeting"`. Transcription is logged
  under `kind="meeting-stt"` with `duration_ms` carrying the audio length rather
  than wall time — and a per-minute price for that model has to be set by hand in
  Settings → `modelPrices`, or the number is *wrong* rather than missing, since the
  proxy prices chat models per token.
- **Transcription is unproven on a default install.** Nothing answers
  `/v1/audio/transcriptions` in the shipped `litellm.yaml` and `whisper-cli` is not
  installed, so until you do one of those two things every segment fails, keeps its
  wav and waits for Retranscribe. Press **Test** before you rely on a call.
- **Recording the other side of a conversation may require their consent where you
  live.** Several US states and much of Europe require all parties to agree. The app
  requires an acknowledgement before it will record anything; it cannot verify you
  actually told anyone. That is on you.

## Tests

```bash
cd backend && uv run --with pytest pytest tests/test_audiocap.py tests/test_redact.py \
  tests/test_stt.py tests/test_meeting_recorder.py tests/test_meeting_notes.py \
  tests/test_meetings.py tests/test_meeting_vad.py tests/test_doc_recordings.py \
  tests/test_mcp_servers.py -q
npm test            # includes src/renderer/src/lib/transcript.test.ts
```

- `test_audiocap.py` — device parsing, loopback detection, the TTL cache, argv
  construction, `ffprobe` validation and the remux repair.
- `test_redact.py` — the named rule sets, and that `scrub()` with the defaults is
  byte-identical to what `activity.Gate.scrub` did before the patterns moved.
- `test_stt.py` — backend resolution, the capability rows and their fix strings,
  and the proxy and local paths against a stubbed client. No real provider.
- `test_meeting_recorder.py` — capture, segmenting, pause, the queue, the retry
  ladder and teardown, all against `audiocap.synthetic_input()`
  (`-f lavfi -i sine`), so it needs no microphone, no loopback driver and no
  permission grant. This is the single most important testability decision in the
  subsystem.
- `test_meeting_notes.py` — the templates, the head-and-tail transcript cap, the
  parse of a model's reply, and the mechanical fallback when the LLM is dead.
- `test_meetings.py` — the schema, FTS, the propose/accept/reject cycle, the
  auto-apply rule, action-item promotion, and the service lifecycle.
- `test_meeting_vad.py` — the silence gate and `find_cut`, which decides where a
  doc recording closes a clip.
- `test_doc_recordings.py` — the doc link, the proposed append summary, stop-time
  dispatch (summarize for `record`, nothing for `dictate`), trash and purge, and the
  `/docs/{id}/recordings` routes.
- `test_mcp_servers.py` — that `RESERVED_TOOL_NAMES` really is every built-in tool
  name, which is what stops an MCP server claiming `meeting_read`.
- `transcript.test.ts` — the renderer's channel interleaving and cursor folding.

### The manual pass

The capture threads have no automated coverage on a real device, and no automated
test may assert that real audio becomes real words. Run this by hand after
touching anything in `meeting_recorder.py` or `stt.py`:

1. Settings → Meetings. Confirm the checklist: `platform`, `ffmpeg` and `mic`
   should be ok. `loopback` and `stt_local` will not be unless you installed them.
2. Pick your microphone in **Input device**.
3. Press **Test**. It must come back with text. If it comes back
   `transcription 500` or `404`, stop here — nothing downstream can work, and Start
   will refuse with that as a blocker.
4. Press **Record** and accept the consent modal (first time only). Speak for a
   minute, typing a few notes as you go.
5. Watch the recorder bar: `transcript ~20s behind` with segments appearing in the
   pane every `segmentSeconds`.
6. Press **Stop**. It blocks while the backlog drains, then the enhanced notes
   arrive as a reviewable diff. Accept it and confirm `enhanced` is written while
   the **Notes** tab still shows exactly what you typed, character for character.
7. Confirm `<data_dir>/recordings/<id>/` is **empty** — unless **Keep audio** is on,
   or some segment failed, in which cases the ones that need replaying are still
   there.
8. With a loopback device configured, repeat with `output` in the sources and
   confirm your speakers still make sound *and* the transcript carries `[them]`
   lines. This is the one step that cannot be verified without hardware setup.

## Speaker turns (optional)

Off by default (`diarize: false`) and dependency-free: `diarize.py` is a seam with a
`none` backend that returns no turns, so a transcript keeps its `[you]`/`[them]` labels.
With `pip install sherpa-onnx` (ONNX only, no torch) plus a pyannote segmentation model
and an embedding model (paths in `diarizeSegmentationModel` / `diarizeEmbeddingModel`,
relative paths resolve under `<data_dir>/models`; the `diarize` capability row prints the
download hints), `diarizeBackend: auto` picks `sherpa`.

It only runs on **retained** audio: an import, or a live meeting with `keepAudio` on. The
channel's wavs are concatenated and clustered once (never per 20 s clip), turns are renamed
`S1, S2...` in order of first appearance, and `assign_speakers` gives each whisper utterance
(from `detail.segments`, or the whole clip) the speaker with the greatest overlap. Results
live in `detail.utterances` and `meeting_segments.speaker` (dominant); the `mic` channel is
never diarized and stays `[you]`. `build_transcript` then writes one line per speaker run,
`[S1]`, or `[Dana]` once named; with no utterance speakers it is byte-identical to before.
Imports keep their wavs until diarization has run, then drop them unless `keepAudio`.

- `POST /meetings/{id}/diarize` re-runs it (`ok: false` plus a note when there is no
  backend or no audio, never an error).
- `PUT /meetings/{id}/speakers` `{names: {S1: "Dana"}}` renames (ids must exist, 60 chars
  max, blank clears), rebuilds the transcript and reindexes FTS. Names live in
  `meetings.speaker_names`.
- The enhance prompt keeps its channel-level rule until at least one name exists; then it
  may attribute to a speaker only when the transcript line carries that name.
