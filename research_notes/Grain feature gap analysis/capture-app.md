# Grain capture features (meetings, activity, habits, dictation)

Inventory of the working tree on 2026-10-02. Code was read after `graphify query` / `graphify explain` oriented the search. Graph nodes for `docs/research/sota-meetings.md` and `docs/research/sota-activity.md` still describe an older “where we are”; those claims were checked against the files below and lose where they conflict. `backend/personal_os/meetings.py` and `backend/personal_os/app.py` are modified in the working tree; this note is that tree, not a clean HEAD.

Scope is capture: meetings, the activity monitor, habits and automation suggestions, dictation and recording into docs. Generic chat and calendar CRUD are out of scope except where a meeting or activity row is created or injected from them.

## How do meetings record, transcribe, diarize, and propose notes? What never happens without an explicit user act?

### Takeaway

A meeting stays off until the user turns the recorder on and acknowledges consent. Capture is a long-lived mic and optional system-audio channel that closes wav clips, transcribes them in the background, and on stop rolls a transcript and (by default) proposes enhanced notes. The notes column is only what the user typed. Per-person speaker labels exist only when diarization is turned on and audio was kept; the default transcript is `[you]` / `[them]`.

### Cited Findings

- Feature is off by default (`enabled: False`). Recording is blocked until `consentedAt` is set by `MeetingService.consent`, and `MeetingConfigIn` cannot stamp that field. `MeetingService.start` calls `preflight`, which also blocks when the master switch is off or the transcription self-test fails. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`DEFAULT_CONFIG`, `MeetingService.consent`, `preflight`, `start`); [backend/personal_os/app.py](backend/personal_os/app.py) (`MeetingConfigIn`)
- Two live channels: `mic` and `output`. Default sources are mic only. System audio prefers a Core Audio process tap when `native_audio.system_available()` is true (macOS 14.2+); otherwise a named loopback device via ffmpeg. A missing far-side channel is a visible degradation, not a failed start. Native mic is AVAudioEngine; ffmpeg avfoundation is the fallback. **macOS-only** for live capture: the `platform` capability is `audiocap.IS_MAC`, and start is blocked off macOS. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`SOURCES`, `capabilities`, `start`); [backend/personal_os/native_audio.py](backend/personal_os/native_audio.py) (module docstring, `IS_MAC`)
- `ChannelCapture` writes 16 kHz mono PCM wavs. Standalone meetings use a fixed `segmentSeconds` (default 20) on the recording clock (`t_start` from seq, not from when STT returns). A recording with `doc_id` sets `cut_on_silence` and closes a clip at a pause (`meeting_vad.find_cut`), capped by `docSegmentSeconds` (10) or `dictationSegmentSeconds` (8). Dictation forces mic only. One meeting records once: status must be `scheduled` or `notes_only`. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`DEFAULT_CONFIG`, `start`); [backend/personal_os/meeting_recorder.py](backend/personal_os/meeting_recorder.py) (`_read_until_pause`, `TranscribeWorker._transcribe_one`)
- Before STT, `vadGate` (default true) runs `meeting_vad.analyze`. Speech ratio under `vadMinSpeechRatio` (0.03) stores the segment as `empty`, deletes the wav, and does not call STT. An unreadable wav is transcribed anyway. After a successful reply, `hallucinationFilter` (default true) calls `stt.filter_hallucinations`: drop a verbose segment when `no_speech_prob > 0.6` and `avg_logprob < -1.0`, or `compression_ratio > 2.4`; drop a known silence phrase only when speech ratio is under 0.15; collapse repetition loops. — [backend/personal_os/meeting_recorder.py](backend/personal_os/meeting_recorder.py) (`_transcribe_one`); [backend/personal_os/stt.py](backend/personal_os/stt.py) (`filter_hallucinations`)
- `stt.resolve_backend`: `auto` uses Apple on-device Speech when authorized, else whisper.cpp when the binary and a ggml model exist, else the proxy `POST /v1/audio/transcriptions`. `speech` returns plain text and an empty `detail` (`shouldReportPartialResults` is false). `proxy` keeps `verbose_json` segments. `local` runs `whisper-cli` with `-oj` so `detail.segments` has start/end, and adds `--vad` when a Silero ggml file is configured or present under `data_dir/models`. `off` keeps notes and produces no transcript. Failures keep the wav; the 45s loop retries up to 3 failed segments per tick, 8 attempts max. — [backend/personal_os/stt.py](backend/personal_os/stt.py) (`resolve_backend`, `_speech`, `_proxy`, `_local`); [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`retranscribe`, `TICK_SECONDS`, `RETRANSCRIBE_PER_TICK`)
- `Meetings.build_transcript` runs at finalize, not per clip. Without utterance speakers it merges done segments by channel into `[you]` / `[them]` / import-as-`[them]` lines with `mm:ss`. Mic is never diarized. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`CHANNEL_LABELS`, `build_transcript`)
- `finish_segment` runs `redact.scrub_secrets` on text and utterance detail when `redactSecrets` is true (default). That is the credential subset. Email and phone rules used by the activity gate are not applied. Meetings have no app or title exclusion list. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`finish_segment`); [docs/meetings.md](docs/meetings.md) (Privacy)
- Enhance is one LLM call in `meeting_notes.enhance` over six templates (`general`, `standup`, `one_on_one`, `user_interview`, `sales_call`, `lecture`). User notes are the outline. Output is a `meeting_revisions` row via `Meetings.propose`. `enhanced` changes only in `accept`. On stop, `enhanceOnStop` (default true) queues that pass. A successful pass is auto-accepted only when `enhanced` is empty or still equal to the last applied revision. A degraded mechanical fallback (notes plus raw transcript) is never auto-accepted. Action items are stored as `proposed`; `promote_action_item` is a separate call into todos. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`propose`, `accept`, `stop`, `enhance`); [backend/personal_os/meeting_notes.py](backend/personal_os/meeting_notes.py) (`ENHANCE_PROMPT`, templates)
- Speaker naming in the enhance prompt stays channel-level until `speaker_names` has at least one display name; then `ATTRIBUTION_RULE` allows a name only when the transcript line already carries it. `[S1]` stays unnamed. — [backend/personal_os/meeting_notes.py](backend/personal_os/meeting_notes.py) (`_system_prompt`)
- Diarization is default off (`diarize: False`). `MeetingService.stop` runs it only when the drain finished and both `diarize` and `keepAudio` are true. `diarize.py` is a seam: `NullBackend` unless sherpa-onnx imports and both model files exist (`diarizeBackend` auto). Whole retained channel (import, else output) is concatenated once; `assign_speakers` is max overlap; ids become S1, S2. `POST /meetings/{id}/diarize` and `PUT /meetings/{id}/speakers` exist. The Meetings UI renames speakers when ids are already present; no renderer call site invokes `api.meetings.diarize`, and Meeting settings expose `keepAudio` and `autoRecord` but no diarize toggle. — [backend/personal_os/diarize.py](backend/personal_os/diarize.py); [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`stop`, `diarize_segments`); [backend/personal_os/app.py](backend/personal_os/app.py) (`MeetingConfigIn`, routes); [src/renderer/src/components/MeetingsView.tsx](src/renderer/src/components/MeetingsView.tsx); [src/renderer/src/components/MeetingSettings.tsx](src/renderer/src/components/MeetingSettings.tsx)
- The 45s loop, while the recorder is enabled, upserts a `scheduled` row for a current calendar event with enough attendees (`suggest` / `adopt`), auto-starts only if `autoRecord` is true (default false) and preflight passes, auto-stops on scheduled end plus `autoStopGraceSeconds` (90) or `maxMeetingSeconds` (14400) or when every capture channel has died, retries failed STT, and deletes wavs of ready meetings after 7 days unless keep-audio or a still-retriable failure remains. The loop comment says auto-stop is time-based. That part matches the code. The same comment says there is no voice-activity detection anywhere; `meeting_vad.py` exists and is used for the silence gate and for doc clip cuts. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`loop`, `_nudge`, `_auto_stop`, `_sweep_audio`, `AUDIO_RETENTION_SECONDS`)
- Meeting rows have no `expires_at`. Chat injection (`MeetingService.context_block`) is titles and accepted notes, never the raw transcript, and is empty when the recorder is off or `injectContext` is false. Doc-linked recordings are omitted from that block and from `GET /meetings` list defaults. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (module docstring, `context_block`); [backend/personal_os/context.py](backend/personal_os/context.py)

#### What does not happen without an explicit user act

- Live capture does not start by itself while `autoRecord` is false. The calendar tick may still create a `scheduled` meeting row once the recorder is enabled.
- Consent and the master switch are user acts. A settings PUT cannot set `consentedAt`.
- The `notes` column is written only through user patch.
- A degraded enhance is not applied. Action items are not turned into todos until promote.
- A doc-recording summary is proposed as a pending doc append and is never auto-applied (`summarize_into_doc`). Dictation mode does not run that pass.
- Import, speaker rename, and a manual diarize re-run are request-driven. With defaults, diarize does not run on stop because `diarize` is false.
- Deleting a meeting or its audio is a user route.

#### What does happen after the user has started (or opted into auto-record)

- Clips transcribe, silence is dropped, credentials are scrubbed, and stop finalizes the transcript without another click.
- With `enhanceOnStop` still at its default, stop queues enhance, and a clean pass is auto-accepted when the user has not edited `enhanced`.
- Failed segments retry on the 45s tick. Finished wavs are deleted unless keep-audio or a replay is still possible; the 7-day sweep is automatic.
- Auto-stop fires from the clock or from dead captures, without a Stop click.

### Inferences

- Default install behavior a gap comparison should treat as shipped is channel-level live transcription plus a notes proposal, not per-person diarization. Diarization is implemented and off, and the default Speech backend returns no timed segments, so even a later diarize pass assigns a whole clip when `detail.segments` is missing.
- “Nothing is recorded without a click” is true only with stock settings. `autoRecord` is the one opt-in that starts capture from the calendar tick, and it still has to pass consent and preflight.

### Gaps

- Whether the Meetings panel offers a control that sets `diarize` was checked in `MeetingSettings.tsx` (no match) and `MeetingsView.tsx` (rename only). A hidden control elsewhere in the renderer was not exhaustively searched beyond those files and `api.ts`.
- No click-to-seek player or live partial transcript was found in the modules read. `SFSpeechURLRecognitionRequest.setShouldReportPartialResults_(False)` is explicit. Absence of a player outside those files was not proven.
- Attendee names are not passed as the whisper prompt in `TranscribeWorker._transcribe_one`; the prompt is the prior clip’s tail (`TAIL_CHARS`). A separate vocabulary feature was not found.

## How does the activity monitor work (signals, redaction, retention, what is excluded)?

### Takeaway

The monitor is off until the user enables it, and it refuses to start off macOS. Six signals are separate switches. Every collector consults a gate (denylist, secure input, redaction) before a write. Raw events expire (default 48 hours). Summaries and a profile last longer and are what chats see.

### Cited Findings

- `Monitor.start` returns immediately when `not IS_MAC`. **macOS-only** collectors: Accessibility frontmost app and window title, optional browser URL via Apple events, a listen-only `CGEventTap` for keys/clicks/scrolls, and audio via AVAudioEngine or a process tap, with ffmpeg avfoundation as fallback. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`start`, `FocusCollector`, `InputCollector`, `AudioCollector`); [backend/personal_os/native_audio.py](backend/personal_os/native_audio.py)
- Signals in `SIGNALS`, defaults in parentheses: `apps` (on), `browserUrls` (off), `input` counts and WPM (on), `text` typed characters (off), `micAudio` (off), `outputAudio` (off). Sample period 5s. Idle threshold 120s writes an `idle` event and closes the focus stretch. Focus rows are one row per stretch, written in `_close` with real duration. Input flushes every 30s. Audio records a chunk (default 30s), transcribes with `stt` backend `auto`, deletes the wav in `finally`, and drops text shorter than `minChars` (12). — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`DEFAULT_CONFIG`, collectors)
- Exclusions are case-insensitive substrings. `excludeApps` matches the app name (password managers and Tor Browser ship in the default list). `excludeTitlePatterns` match title plus URL (password, sign-in, 2fa, seed phrase, bank, incognito, and similar). A match still records time: app is stored as `(private)`, title and URL cleared, `private` set on `last_focus`. Typed text and audio transcripts are dropped for that window (`content_withheld` / `window_withheld`), including when focus is a sample behind the live app. Keystrokes during macOS secure input are not stored; `secure_skipped` is counted. Palantir cannot disable secure input. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`Gate.excluded`, `FocusCollector.work`, `InputCollector`, `content_withheld`)
- Redaction (`redact: True` by default) uses `redact.scrub_v2`: validators (Luhn, phone digit rules, SSN area checks, Shannon entropy), context-word score boost, threshold default 0.4, user `redactAllow` / `redactDeny`. `Gate.scrub_url` calls `sanitize_url` (strip userinfo and fragment; secret-like query values become `~`). Titles are scrubbed and URLs sanitized in `FocusCollector._close`. Typed text and audio transcripts go through `Gate.scrub`. Counts of redactions today are on `Monitor.status()["redactions"]` (counts only). `POST /activity/redact/test` previews in memory via `redact_preview` and does not store the string. Palantir sets `redact` false, empties both exclusion lists, turns all six signals on, and snapshots the previous settings to restore. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`Gate`, `set_palantir`, `redact_preview`); [backend/personal_os/redact.py](backend/personal_os/redact.py) (`scrub_v2`, `analyze`, `sanitize_url`); [backend/personal_os/app.py](backend/personal_os/app.py) (`POST /activity/redact/test`); [src/renderer/src/components/ActivityView.tsx](src/renderer/src/components/ActivityView.tsx) (redaction panel)
- Retention: each event gets `expires_at = ts + retentionHours` (default 48). `Monitor.loop` deletes expired events and summaries older than `summaryRetentionDays` (90), and purges day stats on that same 90-day clock. Pattern snapshots that hold window titles are purged on the raw-event clock. Profile rewrite interval is 6 hours. Rollup interval is 15 minutes while running. `contextDays` (3) bounds the markdown file’s summary section. User purge scopes: expired, events, summaries, all. `all` also deletes habits, their memories, suggestions, and day stats. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`Store.add`, `Store.purge`, `Monitor.loop`, `Monitor.purge`); [backend/personal_os/insights.py](backend/personal_os/insights.py) (`Insights.purge`)
- Not collected: no screenshot, OCR, or accessibility-tree body text appears in the collectors. Screen Recording permission is documented as titles only when `AXTitle` is empty. Browser URL is a separate signal, off by default. Audio is transcript text after the wav is deleted, not retained audio. — [backend/personal_os/activity.py](backend/personal_os/activity.py); [docs/activity-monitor.md](docs/activity-monitor.md) (Permissions)
- Chat injection is `Monitor.context_block`: profile plus up to six summaries from the last day, plus a “right now” line. It is skipped when `injectContext` is false, and also when there is no profile, no recent summary, and the monitor is not running. It does not require `running` if a profile or recent summary already exists. `build_context` appends that block when the chat’s `useActivity` is true. `GET /activity/context` returns the markdown file and this trimmed block as separate fields. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`context_block`, `write_markdown`); [backend/personal_os/context.py](backend/personal_os/context.py); [backend/personal_os/app.py](backend/personal_os/app.py) (`activity_context`)

### Inferences

- The privacy boundary that is actually enforced in code is: denylist and secure input before write, validated redaction on text, sanitized URLs, short raw retention, and propose-only suggestions. Palantir is an explicit override of the denylist and of redaction, not of secure input.
- Substring denylist is still the exclusion mechanism. There is no regex or “this app only when the title matches” rule on the gate. Category rules are a separate classifier, not an exclusion.

### Gaps

- The exact TCC prompt strings and whether a grant survives restart were not re-tested on a machine; the code and docs say a keystroke tap created before the grant stays dead and that dev grants land on Electron. — [README.md](README.md); [backend/personal_os/activity.py](backend/personal_os/activity.py) (`InputCollector.work`)
- No measurement of how often `scrub_v2` false-positives was taken from tests or production. Validators exist; rates were not computed.

## How are habits and automation suggestions mined and surfaced?

### Takeaway

`insights.py` mines counts from activity into deterministic patterns, then one model pass (or a deterministic fallback) writes habit statements and suggestion rows. Suggestions never apply themselves. High-confidence habits can be written into Memory automatically while the monitor is running, because `autoMemory` defaults on.

### Cited Findings

- Day stats (`activity_day_stats`) store per-day apps, hosts, hours, typing, switches, keys, clicks, scrolls, focus and idle seconds, and `cats` (category path to seconds). Merge is max, so a retention sweep of raw events does not shrink a day already counted. Mining reads `lookbackDays` (21) and requires `minDays` (2). — [backend/personal_os/insights.py](backend/personal_os/insights.py) (`SCHEMA`, `DEFAULTS`, `DayStats`, `mine_now`)
- `mine()` emits these kinds: `app_routine`, `site_habit`, `thrash` (at least 4 round trips, median dwell in the second app at most 180s), `deep_work` (stretches of at least 15 minutes), `day_shape`, `after_hours` (at least 20% of screen time outside 08:00–19:00, and more than 3600s total), `input_load` (at least 500 keystrokes), `recurring_window` (same title on enough days), `topic` (from summary “Topics:” lines), `switch_rate` (at least 30 switches per focused hour), `category_share` (top-level category at least 20% of focus on enough days), `distraction_drift` (negative-score categories over 30% of a day’s focus). No LLM in `mine`. — [backend/personal_os/insights.py](backend/personal_os/insights.py) (`mine`)
- `Insights.refresh` asks the model with `kind="activity"`. If the reply has no habits or suggestions, `fallback()` maps patterns to obvious ones. Habits upsert by key. With `autoMemory` true (default) and confidence at least `memoryConfidence` (0.6), the habit owns one memory with `source: "activity"`. A later pass rewrites that memory. `supersedes` retires the old habit and deletes its memory. `forget_habit` deletes the memory. Memories from other sources are not touched. Turning `autoMemory` off drops the habit’s memory on the next persist. — [backend/personal_os/insights.py](backend/personal_os/insights.py) (`DEFAULTS`, `refresh`, `_persist_habits`, `MEMORY_SOURCE`)
- Suggestions upsert by key. Statuses: `new`, `accepted`, `done`, `dismissed`, `snoozed`. Dismissed, accepted, and done are sticky: a refresh may reword them and must not move them back to `new`. Snooze defaults to 7 days and `wake_snoozed` returns them to `new`. Kinds: `automation`, `platform`, `hygiene`. Action types: `prompt` (returns text, does not run tools), `todo`, `memory`, `setting`/`none` (explains only). `apply` is the only executor and is documented as button-only. — [backend/personal_os/insights.py](backend/personal_os/insights.py) (`apply`, `_persist_suggestions`, `set_status`)
- Cadence: `Monitor.loop` calls `insights.maybe_refresh` only while the monitor is running. Default `everyHours` is 12. Insights `enabled` defaults true inside the activity config, so turning the monitor on is enough to schedule the pass. `everyHours` 0 disables the timer. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`loop`); [backend/personal_os/insights.py](backend/personal_os/insights.py) (`maybe_refresh`)
- Surfacing: the Insights tab reads `overview`. `write_markdown` adds up to 12 habits under `## Habits noticed` in `context/activity.md` and does not write suggestions. `Monitor.context_block` (what `build_context` injects) includes the profile and recent period headlines, not habit lines and not suggestions. `Insights.brief` is the tool form and tells the model suggestions are unaccepted. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`write_markdown`, `context_block`); [backend/personal_os/insights.py](backend/personal_os/insights.py) (`brief`); [docs/activity-monitor.md](docs/activity-monitor.md) (Insights)

### Inferences

- A habit memory can appear without a per-habit click once the monitor has been on long enough for a refresh and a pattern clears 0.6 confidence. A suggestion cannot change todos, memory, or settings until `apply`.
- The markdown file and the chat block are different surfaces. Docs that say habits reach chats via `activity.md` overstate what `context_block` actually appends.

### Gaps

- The exact system prompt text in `SUGGEST_PROMPT` / `SURFACE` was not quoted here. The persist and apply behavior above is what the pass is allowed to do.
- Whether the Insights tab is the only UI for dismiss / apply was not click-tested. Routes exist on the activity API; the panel is described in `docs/activity-monitor.md`.

## How does dictation into docs work?

### Takeaway

Docs reuse the meeting recorder. Record mode keeps a transcript and proposes a summary section onto the doc. Dictate mode listens to the mic, types settled clips at the caret, and does not summarize. Both need the same consent and recorder switch as a meeting. Import of an existing audio file into a doc is a third explicit action and is not macOS-only.

### Cited Findings

- `DOC_MODES` are `record` and `dictate`. Rows carry `doc_id`, `doc_mode`, and `summary_revision_id` (`ADDED_COLUMNS`). They are hidden from the meetings list and from the recent-meetings chat block. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py)
- Toolbar: Record starts `record`; the menu offers Dictate and Import audio. `useDocRec.start` flushes the doc, creates a linked meeting, and on a consent refusal opens `MeetingConsentModal` and retries. Stop drains transcription, then `beginSettling`. — [src/renderer/src/features/docrec/DocRecordButton.tsx](src/renderer/src/features/docrec/DocRecordButton.tsx); [src/renderer/src/features/docrec/store.ts](src/renderer/src/features/docrec/store.ts)
- After stop, `_enhance_quietly` calls `summarize_into_doc` for `record` and returns without a summary for `dictate`. The summary is `docs.propose_append` with tool `recording_summary`. It is not auto-applied. A model failure stores no revision and does not paste the raw transcript into the doc. Credential scrub runs on the proposed markdown. A second summary rejects the previous pending revision for that recording. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`_enhance_quietly`, `summarize_into_doc`)
- Dictation insert is client-side. `planDictation` / `readyForInsert` take mic clips in seq order and will not skip an unfinished clip. `dictationText` maps a whole utterance of “new line” or “new paragraph” to a break, capitalizes at a sentence start, escapes `$` before a digit, and inserts a joining space. `useDictation` types into the doc that was dictated into, holds clips while the editor is unavailable (including preview-only), and retries for 120s after stop. Insert happens as clips settle, without a second confirm. — [src/renderer/src/features/docrec/dictation.ts](src/renderer/src/features/docrec/dictation.ts); [src/renderer/src/features/docrec/hooks.ts](src/renderer/src/features/docrec/hooks.ts)
- Doc clips end on a pause, with ceilings 10s (record) and 8s (dictate). Meeting clips stay fixed-length. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`start`); [backend/personal_os/meeting_recorder.py](backend/personal_os/meeting_recorder.py) (`_read_until_pause`)
- Import: user picks a file (`audio/*`, common extensions, `video/mp4`). `meeting_import.run` requires `consentedAt`, refuses a live or already-segmented meeting, probes duration against `maxImportSeconds` (14400), needs ffmpeg, cuts 20s wavs on the `import` channel, transcribes with the same worker (VAD and hallucination filter), finalizes, and queues enhance. If `diarize` is on and sherpa resolves, wavs are held for `diarize_segments` and then dropped unless `keepAudio`. The module docstring says this path is not macOS-only. Doc import creates a linked meeting in `record` mode, so a successful import can propose a doc summary. — [backend/personal_os/meeting_import.py](backend/personal_os/meeting_import.py); [src/renderer/src/features/docrec/store.ts](src/renderer/src/features/docrec/store.ts) (`importAudio`); [backend/personal_os/app.py](backend/personal_os/app.py) (`POST /meetings/{id}/import-audio`, 202)

### Inferences

- Dictation is “type what was said” with no review diff. Record-into-doc is “propose a section” with the same accept/reject shape as other doc revisions.
- Latency is still clip-based. Dictate’s ceiling is 8 seconds plus STT time, not a streaming partial.

### Gaps

- The editor `insert` implementation (undo stack, whether the caret insert marks the doc dirty and autosaves) lives outside `dictation.ts`. This note confirms the hook calls `insert` and bails if it returns false. It does not trace the textarea undo path.
- Whether import into a doc runs diarization in the UI was not exercised. The server does when `diarize` is on and a backend resolves; the default is off.

## Speaker diarization, audio import, category rules: shipped, partial, or absent?

### Takeaway

All three are in the working tree. Diarization is partial: code and routes exist, default off, no settings checkbox, and the default Speech backend has no timed segments. Audio import and category rules are shipped, including UI.

### Cited Findings

- **Diarization — partial.** Backend seam, assignment, naming column, transcript rebuild, import-time and keep-audio stop-time hooks, capability row, and speaker chips are present. Default `diarize` is false. No Meetings settings control sets it. No renderer call to `POST /diarize` was found. Mic stays `[you]`. Without sherpa-onnx and both model files, `resolve_backend` is `none` and the transcript is unchanged. Apple Speech returns `{}` for detail, so utterance times fall back to the whole segment. — [backend/personal_os/diarize.py](backend/personal_os/diarize.py); [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`diarize_segments`); [backend/personal_os/stt.py](backend/personal_os/stt.py) (`_speech`); [src/renderer/src/lib/api.ts](src/renderer/src/lib/api.ts)
- **Audio import — shipped.** `meeting_import.py`, `POST /meetings/{id}/import-audio`, Meetings view file input, and the doc Record menu. Consent required. ffmpeg required to cut the file. Not limited to macOS. — [backend/personal_os/meeting_import.py](backend/personal_os/meeting_import.py); [src/renderer/src/components/MeetingsView.tsx](src/renderer/src/components/MeetingsView.tsx); [src/renderer/src/features/docrec/DocRecordButton.tsx](src/renderer/src/features/docrec/DocRecordButton.tsx)
- **Category rules — shipped.** `activity_categories.CategoryEngine`: deepest regex/host match, folder rules do not match, invalid regex skipped, score −2..2 with ancestor inheritance, default tree (Work/Coding, Work/Writing, Work/Meetings, Comms, Reference, Social/Media, Uncategorized). `categories: null` means that tree. Day stats store `cats`. `mine()` emits `category_share` and `distraction_drift`. Rollup digest adds a category line. `GET /activity/categories/report` and an Activity panel stacked bar plus rule editor exist. Classification does not call a model. — [backend/personal_os/activity_categories.py](backend/personal_os/activity_categories.py); [backend/personal_os/insights.py](backend/personal_os/insights.py) (`mine`); [backend/personal_os/app.py](backend/personal_os/app.py); [src/renderer/src/components/ActivityView.tsx](src/renderer/src/components/ActivityView.tsx)

### Inferences

- A gap list that marks “no diarization”, “no import”, or “no categories” is describing the research notes, not this tree.
- Diarization should stay marked partial until a user can turn it on in settings and the default STT path emits timed segments. Import and categories should be marked shipped.

### Gaps

- sherpa-onnx was not imported in this session, so the live clustering path was not executed. The seam’s failure mode (return no turns, keep channel labels) is what the source specifies.
- Category report numbers for a real user were not queried. The engine and routes are present; no productivity score from a live database is stated here.

## Where do README and docs/research/sota-meetings.md and sota-activity.md disagree with the code?

### Takeaway

`docs/research/sota-meetings.md` and `docs/research/sota-activity.md` “Where we are” sections describe an older tree: no native audio module, no VAD, no import, no diarizer, no categories, blunt redaction, raw URLs. Those features are in the working tree. The README’s short Meetings and Activity sections match the defaults more closely and omit the optional paths. `docs/meetings.md` is split: the pipeline and “Speaker turns” sections match the code, and the Limits section still says the speaker column is empty.

### Cited Findings

#### README vs code

- README: activity is off by default, per-signal switches, Palantir restores previous settings, six macOS permissions, dev grants land on Electron. That matches `DEFAULT_CONFIG`, `set_palantir`, and `capabilities`. — [README.md](README.md) (Activity monitor); [backend/personal_os/activity.py](backend/personal_os/activity.py)
- README: meetings off by default, separate from activity; Speech is the default when granted; ffmpeg, whisper.cpp, and BlackHole are optional fallbacks; attribution is “you versus them”, not per person. The default path matches (`sttBackend: auto`, `diarize: False`, `CHANNEL_LABELS`). The sentence does not mention that `diarize.py`, speaker rename, and import exist. It is accurate as a description of stock settings and incomplete as a description of the tree. — [README.md](README.md) (Meetings); [backend/personal_os/meetings.py](backend/personal_os/meetings.py); [backend/personal_os/diarize.py](backend/personal_os/diarize.py)
- README does not mention category rules, redaction v2, habits, doc recording, or dictation. Those are in code and in `docs/activity-monitor.md` and `docs/meetings.md`. Omission, not a false claim. — [README.md](README.md); [docs/activity-monitor.md](docs/activity-monitor.md); [docs/meetings.md](docs/meetings.md)

#### `docs/research/sota-meetings.md` vs code

The “Where we are” section states it was verified in code and then asserts all of the following. Each is false or outdated in this tree:

- “`native_audio.py` does not exist, capture is ffmpeg/avfoundation only.” The module exists and is the preferred mic and system-audio path. — [docs/research/sota-meetings.md](docs/research/sota-meetings.md); [backend/personal_os/native_audio.py](backend/personal_os/native_audio.py)
- “No VAD”, “no silence gate”, “no hallucination filter”, “local whisper returns empty detail”. `meeting_vad.py`, `vadGate`, `filter_hallucinations`, and `_local` with `-oj` are in the tree and default on for the gate and filter. — [docs/research/sota-meetings.md](docs/research/sota-meetings.md); [backend/personal_os/stt.py](backend/personal_os/stt.py); [backend/personal_os/meeting_recorder.py](backend/personal_os/meeting_recorder.py)
- “`speaker` column exists and is always empty”, “no diarization, no speaker naming”, “enhance prompt forbids naming speakers”. The column is written by `set_segment_speakers`. Naming is `speaker_names` plus `PUT /speakers`. The prompt forbids names only when no display names are stored. — [docs/research/sota-meetings.md](docs/research/sota-meetings.md); [backend/personal_os/meetings.py](backend/personal_os/meetings.py); [backend/personal_os/meeting_notes.py](backend/personal_os/meeting_notes.py)
- “No audio-file import” and “the `import` channel is unused”. `meeting_import.run` writes `channel='import'`. — [docs/research/sota-meetings.md](docs/research/sota-meetings.md); [backend/personal_os/meeting_import.py](backend/personal_os/meeting_import.py)
- “Loopback is a manual BlackHole setup” as the only system-audio path. BlackHole remains the pre-14.2 fallback; the process tap is preferred when available. — [docs/research/sota-meetings.md](docs/research/sota-meetings.md); [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`capabilities`)
- “Fixed-length cuts … no VAD” for every recording. True for a meeting (`segmentSeconds` 20, `cut_on_silence` only when `doc_id` is set). False for doc record and dictation. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`start`)

Claims in that same “Where we are” / Gaps list that still match the code:

- Literal FTS only (`meetings_fts`), no embedding column in the meetings schema. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py) (`SCHEMA`)
- No streaming partials on the Speech path. — [backend/personal_os/stt.py](backend/personal_os/stt.py) (`_speech`)
- Backends are speech, proxy, local, and off. No other ASR engine is registered in `resolve_backend`. — [backend/personal_os/stt.py](backend/personal_os/stt.py)
- Per-meeting SSE is described in `docs/meetings.md` as a route that finishes immediately because nothing publishes on that bus; the app-wide `GET /events` stream is what Docs listens to. The sota line “the per-meeting SSE ships idle” is consistent with that doc. This note did not re-read the route handler. — [docs/meetings.md](docs/meetings.md) (How it works)

The sota “Build next” specs (meetings-1 VAD, meetings-2 import, meetings-3 diarize seam) match modules that now exist. Treating that file’s gap table as the current backlog would double-count shipped work.

#### `docs/research/sota-activity.md` vs code

“Where we are” weak spots that the code now contradicts:

- “No categorization anywhere.” `activity_categories.py` exists, day stats have `cats`, the panel has a category bar and editor. — [docs/research/sota-activity.md](docs/research/sota-activity.md); [backend/personal_os/activity_categories.py](backend/personal_os/activity_categories.py)
- Card/phone/entropy rules have no validation, no allow/deny lists, no telemetry, URLs stored raw. `scrub_v2`, `sanitize_url`, `Gate.scrub_url`, `Gate.counts`, and `POST /activity/redact/test` are present. `Gate.scrub` calls `scrub_v2`, not the old `scrub()` ordering. — [docs/research/sota-activity.md](docs/research/sota-activity.md); [backend/personal_os/redact.py](backend/personal_os/redact.py); [backend/personal_os/activity.py](backend/personal_os/activity.py)
- “Day aggregates have no category.” The `cats` column and `category_share` / `distraction_drift` patterns exist. — [backend/personal_os/insights.py](backend/personal_os/insights.py)

Claims there that still match:

- Exclusions are substring-only. No regex exclusion and no per-bucket “exclude this app only when the title matches” on `Gate.excluded`. — [backend/personal_os/activity.py](backend/personal_os/activity.py) (`Gate.excluded`)
- No accessibility-tree or OCR screen-text signal in the collectors. — [backend/personal_os/activity.py](backend/personal_os/activity.py)
- No composable AFK query language was found in `activity.py`.
- `mine()` is still deterministic and the suggestion ledger is still propose-only. The sota description of that pipeline is right; its “ten pattern kinds” list is short two (`category_share`, `distraction_drift`). — [backend/personal_os/insights.py](backend/personal_os/insights.py)

`docs/activity-monitor.md` Categories, Insights, and Retention sections agree with this code more closely than the sota note does. One doc/code mismatch: `context.py` comments that activity context is off unless the monitor is on, while `Monitor.context_block` still returns text when a profile or a recent summary exists and the monitor is stopped, as long as `injectContext` is true. — [backend/personal_os/context.py](backend/personal_os/context.py); [backend/personal_os/activity.py](backend/personal_os/activity.py) (`context_block`)

#### Extra doc that will mislead a gap pass

`docs/meetings.md` Limits still says speaker attribution is channel-level only, `meeting_segments.speaker` is empty, and diarization is a later pass. The same file’s “Speaker turns (optional)” section describes the implemented seam. The Limits bullets are stale; the later section matches `diarize.py`. README points at `docs/meetings.md` as the full design. — [docs/meetings.md](docs/meetings.md) (Limits, Speaker turns); [README.md](README.md)

A comment in `Meetings` still says “There is no diarization in this slice” next to `CHANNEL_LABELS`, in the same file that implements `diarize_segments`. The labels are the fallback, not the absence of the feature. — [backend/personal_os/meetings.py](backend/personal_os/meetings.py)

### Inferences

- For a gap comparison, prefer `meetings.py`, `meeting_recorder.py`, `meeting_import.py`, `diarize.py`, `stt.py`, `native_audio.py`, `activity.py`, `activity_categories.py`, `redact.py`, `insights.py`, and `src/renderer/src/features/docrec/` over `docs/research/sota-meetings.md` and `docs/research/sota-activity.md`.
- Prefer `docs/activity-monitor.md` and the non-Limits parts of `docs/meetings.md` when a prose spec is needed. Re-read Limits before citing it.

### Gaps

- `docs/research/notes-recording-codemap.md` and `docs/research/notes-editor-dictation.md` were located and not fully reconciled line by line. The codemap’s “native_audio and stt expose” section may also be stale; it was not the assigned disagreement set.
- This note did not re-run the meeting or activity test suites. Behavior above is from source, not from a green pytest log.
