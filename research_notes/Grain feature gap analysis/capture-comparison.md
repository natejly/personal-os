# Capture comparison (meetings, activity, habits, dictation)

Date: 2026-10-02. Built only from the working-tree inventory in [capture-app.md](capture-app.md) and the market inventory in [capture-market.md](capture-market.md). No new web research. The two notes do not make conflicting claims about the same fact. Where they describe different Apple speech APIs, or where one note is internally unresolved, that is called out below and was not “settled” by looking anything up.

Effort means: **S** one existing module and an offline test, **M** a behavior change across the UI and backend paths the app note already names, **L** a new capture architecture or an OS API the tree does not call.

## Conflicts and reconciliations

- The app inventory’s Speech path is `SFSpeechURLRecognitionRequest` with partial results off and an empty `detail`. The market inventory’s on-device long-form API is `SpeechTranscriber`, including time-indexed and progressive presets. Those are different APIs. Grain has the first; the market note does not say Grain calls the second.
- `docs/research/sota-meetings.md` and `docs/research/sota-activity.md` disagree with the working tree. This comparison follows the tree, as the app note does.
- `docs/meetings.md` Limits still says the speaker column is empty. The same file’s “Speaker turns” section, and the code the app note read, describe the diarization seam. Limits is the stale side.
- A `context.py` comment says activity context is off unless the monitor is running. `Monitor.context_block` still returns a profile or a recent summary when the monitor is stopped, if `injectContext` is true.
- The market note leaves Fireflies’ “saved for at least 12 months” blog line in conflict with user-controlled deletion in the privacy policy and knowledge base. Nothing below uses the 12-month figure.

## 1. What Grain ships now

From the app inventory only.

### Meetings

Live capture is off until the user turns the recorder on (`enabled: False`) and calls `MeetingService.consent`. A settings write cannot set `consentedAt`. `start` also runs `preflight`, which blocks when the master switch is off or the transcription self-test fails. Live capture is macOS-only.

Two channels exist, `mic` and `output`. Stock sources are mic only. System audio uses a Core Audio process tap on macOS 14.2+ when that tap is available, otherwise a named loopback device. A missing far side is a visible degradation; start still proceeds. Mic capture prefers AVAudioEngine. Clips are 16 kHz mono wavs. A standalone meeting cuts every `segmentSeconds` (default 20) on the recording clock. Silence cutting is for doc-linked recordings, not for those fixed meeting clips.

On stop, the transcript is built once: `[you]` / `[them]`, or import as `[them]`, with `mm:ss`. The mic channel is never diarized. `vadGate` and `hallucinationFilter` default on. `redactSecrets` defaults on and scrubs the credential subset only. Email and phone rules from the activity gate are not applied. Meetings have no app or title exclusion list.

`stt` `auto` uses Apple on-device Speech when it is authorized, otherwise whisper.cpp when the binary and a ggml model exist, otherwise `POST /v1/audio/transcriptions`. The Speech path returns plain text and an empty `detail`, and it does not report partials. whisper.cpp local returns timed segments. `off` keeps notes and writes no transcript. Failed wavs retry on a 45s loop.

The `notes` column changes only through a user patch. Stop can queue one enhancement pass (`enhanceOnStop` defaults true) over six templates: `general`, `standup`, `one_on_one`, `user_interview`, `sales_call`, `lecture`. The user’s notes are the outline. Output is a `meeting_revisions` row. `enhanced` changes in `accept`. A clean pass is auto-accepted when `enhanced` is empty or still equal to the last applied revision. A degraded fallback (notes plus raw transcript) is never auto-accepted. Action items stay `proposed` until a separate promote into todos.

The calendar loop, while the recorder is enabled, may insert a `scheduled` row for the current event. It starts the microphones only if `autoRecord` is true (default false) and preflight passes. Auto-stop is the scheduled end plus 90 seconds, or 4 hours, or every capture channel dying. The loop comment that says there is no voice-activity detection is wrong: `meeting_vad.py` is used for the silence gate and for doc clip cuts, not for ending a meeting. Finished wavs are deleted after 7 days unless keep-audio is on or a segment can still be retried. Meeting rows have no `expires_at`. Chat injection is titles and accepted notes, never the raw transcript, and it is empty when the recorder is off or `injectContext` is false.

Diarization code is in the tree and default off (`diarize: False`). Stop runs it only when draining finished and both `diarize` and `keepAudio` are true. Without sherpa-onnx and both model files the backend is a no-op. The UI can rename speaker ids that already exist. Meeting settings expose `keepAudio` and `autoRecord`. They do not expose diarize, and the renderer does not call `POST /meetings/{id}/diarize`.

### Activity monitor

The monitor is off until the user enables it, and it refuses to start off macOS. Six signals, stock defaults in parentheses: apps (on), browser URLs (off), input counts and WPM (on), typed text (off), mic audio (off), output audio (off). Collectors check a denylist, secure input, and redaction before a write. A denylist match still records time as `(private)` and drops typed text and audio transcripts for that window. Secure-input keystrokes are not stored. The explicit widen-everything mode cannot turn that secure-input skip off.

Redaction defaults on (`scrub_v2`, URL sanitizing, allow and deny lists). Raw events expire in 48 hours. Summaries and day stats last 90 days. The profile is rewritten every 6 hours. Collectors do not take screenshots, OCR, or accessibility-tree body text. When an audio signal is on, the wav is deleted after transcription. Chat sees the profile, up to six summaries from the last day, and a “right now” line when `useActivity` is on. That block can still appear after the monitor stops if a profile or a recent summary exists.

### Habits

`mine()` is deterministic (routines, sites, thrash, deep work, day shape, after hours, input load, recurring windows, topics, switch rate, category share, distraction drift). A model pass, or a deterministic fallback, writes habit statements and suggestion rows. Suggestions never apply themselves; `apply` is button-only. `autoMemory` defaults on: a habit at confidence 0.6 or higher is written into Memory while the monitor is running, and a later pass rewrites that memory. The Insights tab shows the overview. `context/activity.md` can list habits. The chat block does not include habit lines or suggestions.

### Dictation and doc recording

Docs reuse the meeting recorder and the same consent and master switch. Record mode keeps a transcript and proposes a summary section; that append is never auto-applied, and dictate mode does not summarize. Dictate is mic only, inserts settled clips at the caret with no second confirm, and maps a whole utterance of “new line” or “new paragraph” to a break. Clips end on a pause, capped at 10 seconds (record) or 8 seconds (dictate). Import of an existing audio file is a separate explicit action, needs consent and ffmpeg, and is not macOS-only.

### Already in the tree

Audio import and category rules ship, including UI. The diarization seam ships as code and routes. Treat “add import” and “add categories” as already done. Treat diarization as present and unreachable, which is the gap in section 4.

## 2. What leading apps ship that Grain does not

Names and links are only those in the market note.

### Meeting notes

**Far-side audio is part of starting a note.** Granola captures microphone plus system audio once the user opens that meeting’s note, clicks a notification, clicks New Note, or is already in an upcoming meeting note. It does not transcribe every calendar event on its own. It cannot isolate one app’s audio, so music and other system audio land in the transcript. Grain’s stock meeting sources are mic only. Sources: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription).

**Live channel transcript.** Granola’s live transcript splits system audio and the microphone into bubbles while the meeting is going. Grain builds the transcript at finalize, and the Speech path does not report partials. Source: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription).

**Named people, three different ways.**

- Platform display names on Google Meet, Zoom, and Microsoft Teams (Granola, desktop). Without that, Granola is “Me” and “Them.” The phone app can recognize speakers in a face-to-face meeting on one mic. Sources: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription), [iOS transcription](https://docs.granola.ai/help-center/ios/transcription).
- Fireflies: speaker names on Meet and Zoom; other platforms and uploads are Speaker 1, 2, 3. The owner can rename a turn or a speaker and is told to regenerate notes so summaries pick up the names. Sources: [Fireflies.ai API](https://fireflies.ai/api), [How to Edit Speaker Labels in Uploaded Files](https://guide.fireflies.ai/articles/1234873612-how-to-edit-speaker-labels-in-uploaded-files).
- Otter: speaker turns, plus a voice-enrollment store that labels the same person across conversations. Enrollment can be disabled; manual labels are not overwritten. Sources: [Disable Speaker Learning](https://help.otter.ai/hc/en-us/articles/40643231903639-Disable-Speaker-Learning), [Speaker Identification Overview](https://help.otter.ai/hc/en-us/articles/21665587209367-Speaker-Identification-Overview).

Grain’s default transcript is `[you]` / `[them]`. The acoustic seam that would produce S1, S2 is off, has no settings control, and the default Speech backend returns no timed segments. Nothing in the app inventory reads Meet, Zoom, or Teams participant names. Do not close this by copying Otter’s enrollment store (section 4).

**Notes the user can trace and restyle.** When a Granola call ends, enhanced notes are generated from the transcript, the notes the user typed, and the calendar event. Text the user wrote stays visually distinct; a control on a point shows the transcript or raw-note span it came from. The user edits in place, edits the raw notes and re-enhances, swaps templates, or writes their own prompt and structure. They can resume transcription on a finished note, delete individual transcript chunks, and regenerate. Sources: [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes), [How to get the best from Granola](https://www.granola.ai/blog/get-the-best-from-granola), [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription).

Grain has six fixed templates, a proposal row, and auto-accept of a clean pass into `enhanced` when that field is empty. The inventory describes no citation back to a transcript timestamp, no user-written template, no resume of a finished meeting (`start` requires `scheduled` or `notes_only`), and no per-chunk delete.

**Personal jargon.** Granola’s documented accuracy lever is personal and workspace jargon, plus mic level and a headset. The app inventory found no vocabulary feature, and attendee names are not passed into the whisper prompt (the prompt is the previous clip’s tail). Source: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription).

**Cloud transcription, retained or deleted by policy.** Granola streams audio to an unnamed transcription provider, caches it for the meeting, and deletes it from Granola and that provider after transcription. Notes and transcripts sit on AWS in the United States with no other residency option; retention is indefinite unless a policy is set. Enterprise can auto-delete transcripts. Enhanced notes are primarily OpenAI and Anthropic. Free and Business accounts may use anonymised data to improve Granola’s own models unless the user opts out. Sources: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription), [Security, Privacy & Data FAQs](https://docs.granola.ai/help-center/consent-security-privacy/security-privacy-data-faqs), [Transcript auto-deletion](https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion), [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes).

Otter processes audio in the cloud, can keep recordings the user later downloads, and may take automatic screenshots on virtual meetings. Its privacy policy includes training proprietary models on de-identified audio and on transcripts. Enterprise admins can set separate retention for audio and for the transcript. Sources: [Otter.ai Terms of Service](https://otter.ai/terms-of-service), [Otter.ai Privacy Policy](https://otter.ai/privacy-policy), [Set a custom Data Retention policy](https://help.otter.ai/hc/en-us/articles/19500988656279-Set-a-custom-Data-Retention-policy).

Fireflies joins the call as a bot. A single participant opt-out stops the bot from joining. Vendors are under a zero-retention rule; Fireflies keeps the user’s transcript, summary, and recordings, and orgs can auto-delete them. The policy says Fireflies does not train models on meeting content. Sources: [Ensuring Meeting Compliance with Fireflies AI Notetaker](https://fireflies.ai/blog/meeting-compliance-with-fireflies/), [Privacy Policy](https://fireflies.ai/privacy-policy), [Responsible and secure meeting notetaking](https://guide.fireflies.ai/articles/7434774675-responsible-and-secure-meeting-notetaking-with-fireflies).

None of those three document an on-device transcription mode. Grain’s `auto` order is on-device Speech, then local whisper.cpp, then the proxy. That proxy, and the enhancement call, are the leaves the inventory does not claim stay on device. The inventory does not say whether `meeting_notes.enhance` is local.

**Stop when the room goes quiet.** Granola stops on a manual stop, when it decides the call ended, after 15 minutes with no new audio, or when the computer sleeps. Grain’s auto-stop is the calendar end plus 90 seconds, the 4-hour cap, or dead capture channels. Source: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription).

**Participant-visible consent aids.** Granola’s consent help describes a video watermark while transcribing and an automatic chat notification. Consent itself is still the user’s legal duty. Grain stores `consentedAt` before capture and does not, in the inventory, post into the meeting or watermark the call. Source: [Getting Consent](https://docs.granola.ai/help-center/consent-security-privacy/getting-consent). A chat notice that requires joining the meeting is a bot behavior. Do not add one.

**Upload.** Granola cannot transcribe an uploaded file. Otter and Fireflies can. Grain already can. This is not a Grain gap. Sources: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription), [Otter.ai Terms of Service](https://otter.ai/terms-of-service), [How to Edit Speaker Labels in Uploaded Files](https://guide.fireflies.ai/articles/1234873612-how-to-edit-speaker-labels-in-uploaded-files).

### Activity and computer-use memory

The market note’s still-shipping products store different payloads. Grain’s monitor stores none of the pixel payloads.

- ActivityWatch logs applications and websites into a local SQLite database and does not upload that usage. Its security doc says there is no API authentication and describes encryption of old data as future work. Sources: [FAQ](https://docs.activitywatch.net/en/latest/faq.html), [Privacy Policy](https://docs.activitywatch.net/en/latest/privacy.html), [Security](https://docs.activitywatch.net/en/latest/security.html).
- Screenpipe stores screen, audio, input, browser metadata, meeting transcripts, and connected-app context on the machine unless the user turns on a cloud path. Localhost API calls require a bearer token. Secrets in its local database are AES-256-GCM with the key in the OS keychain. A privacy filter can redact text or images before selected AI workflows. Sources: [Privacy data flow](https://docs.screenpipe.com/privacy-data-flow), [Security Architecture](https://screenpipe.com/security/architecture), [Privacy filter](https://docs.screenpipe.com/privacy-filter).
- Windows Recall, on Copilot+ PCs, saves periodic screenshots only after opt-in, derives text with local OCR, and does not record audio. Sensitive-content filtering defaults on. It is not a Mac feature. Sources: [Privacy and control over your Recall experience](https://support.microsoft.com/en-us/windows/privacy/privacy-and-control-over-your-recall-experience), [Recall overview](https://learn.microsoft.com/en-us/windows/ai/recall/).
- OtterPilot may attach automatic screenshots to a meeting transcript. Source: [Otter.ai Privacy Policy](https://otter.ai/privacy-policy).
- Limitless (formerly Rewind) had Mac screen and audio capture turned off on 19 December 2025 after the Meta acquisition, according to a news report of the company’s email. The 5 December 2025 policy still describes Pendant audio retention chosen by the user, including indefinite retention. Sources: [Rewind Mac app shutting down following Meta acquisition](https://9to5mac.com/2025/12/05/rewind-limitless-meta-acquisition/), [Limitless Privacy Policy](https://www.limitless.ai/privacy-policy).

The market note does not describe a habit miner or a propose-only suggestion ledger. Grain’s insights pipeline has no named counterpart in that note. The gap is internal: `autoMemory` writes Memory by itself (section 4).

The app inventory never mentions encryption at rest or authentication on Grain’s local API. Screenpipe documents both. That is not proof Grain’s store is unencrypted. Do not schedule an encryption project from these two notes alone.

### Dictation

The current Mac User Guide (the page lists “What’s new in macOS 27”) is the OS floor: dictation in any text field, a shortcut, a Keyboard-settings line that says whether ordinary dictation stays on device, unlimited length, typing while speaking on Apple silicon, auto-punctuation, spoken “new line” and “new paragraph,” punctuation by name, and alternatives for ambiguous words. Sharing dictation audio with Apple is a separate choice. Source: [Dictate messages and documents on Mac](https://support.apple.com/guide/mac-help/use-dictation-mh40584/mac).

Wispr Flow dictates into every application. Its privacy page says transcription always happens in the cloud. A separate control decides whether dictation audio, transcripts, and edits may be used to train models. Turning training off does not stop cloud processing. Source: [Wispr Flow privacy](https://wisprflow.ai/privacy), [Data Controls](https://wisprflow.ai/data-controls).

Superwhisper pastes polished text into the focused app, keeps a custom vocabulary, and offers modes (Voice, Message, Email) plus custom prompts. Offline models are part of the product and “only run really well” on Apple silicon; the page tells Intel Macs to use cloud models. The homepage does not say whether polished text always calls a cloud model. Source: [Superwhisper](https://superwhisper.com).

Yap, one project’s README, uses on-device `SpeechAnalyzer` on macOS 26, a global shortcut, and a preview before paste, and says it makes no network calls. Source: [FrigadeHQ/yap](https://github.com/frigadehq/yap).

Grain dictates into its own docs. The formatter capitalizes, escapes `$` before a digit, inserts a joining space, and maps “new line” / “new paragraph.” The inventory does not describe spoken punctuation, a vocabulary list, ambiguous-word alternatives, a preview, system-wide paste, or partial results. Insert happens as clips settle, so latency is the pause cut (ceiling 8 seconds) plus transcription time. Whether the Speech backend already returns punctuated words is not stated; do not “fix” that by adding a period to every clip.

### What can already run on device

whisper.cpp runs Whisper locally, including Metal on Apple silicon, and can transcribe 16 kHz mono wavs. Apple’s `SpeechTranscriber` is the on-device long-form model Notes was said to use at WWDC25, with presets for progressive results and time ranges. The market note does not show speaker IDs on either recognizer. Note enhancement in Granola, Otter, Fireflies, and Wispr’s Notetaker is a second, cloud model. Superwhisper and Screenpipe document a local or user-chosen model for that second step. Sources: [whisper.cpp README](https://github.com/ggml-org/whisper.cpp/blob/master/README.md), [SpeechTranscriber](https://developer.apple.com/documentation/speech/speechtranscriber), [SpeechTranscriber.Preset](https://developer.apple.com/documentation/speech/speechtranscriber/preset), [WWDC25 session 277](https://developer.apple.com/videos/play/wwdc2025/277/).

## 3. Where Grain is already ahead, or different on purpose

These are defaults to keep while closing section 4.

**Capture starts only after an explicit act.** Meetings stay off until the master switch and consent. `autoRecord` defaults false, so the calendar tick does not open the mic. Activity stays off until enabled, then only the apps and input-count signals are on. Browser URLs, typed text, mic audio, and output audio are off. Dictation and doc recording use that same meeting consent. ActivityWatch’s fetched docs describe a background usage tracker. Screenpipe’s docs describe screen and audio as the stored payload unless the user stays on a local-only setup. Recall’s snapshots are opt-in, and they are screenshots on Windows.

**On-device transcription is the first `auto` choice.** Speech when authorized, then whisper.cpp, then the proxy. Granola, Otter, Fireflies, and Wispr document cloud transcription and do not document an on-device recognizer. Superwhisper is the market product that also sells an offline path on Apple silicon. Grain’s proxy is still there when both local backends are missing; a machine in that state sends audio to `POST /v1/audio/transcriptions`.

**Audio files are not the retained object.** Meeting wavs for a finished meeting are deleted after 7 days unless the user kept audio or a segment can still be retried. Activity audio deletes the wav in `finally` and keeps transcript text. Granola also deletes audio after transcription and then keeps the transcript. Otter can keep downloadable audio. Grain does not have a meeting-transcript expiry (section 4); the ahead claim is the file sweep, not the text.

**The user’s notes are a separate column.** Meeting `notes` change only by user patch. Doc record proposes an append and never auto-applies it. Dictate does not run that summary. Action items become todos only on promote. Suggestion `apply` is button-only. Granola’s acceptance model is editing the enhanced note in place, with the user’s words visually distinct. Grain is stricter on `notes` and on doc appends. Grain is looser on the `enhanced` field, which a clean pass can fill without a click (section 4).

**Chat does not receive raw meeting transcripts.** Injection is titles and accepted notes, and only while the recorder is enabled and `injectContext` is on. Doc-linked recordings are left out of that block.

**Pixels are out of the collectors.** No screenshot, OCR, or accessibility-tree body text. Screen Recording permission is documented as titles only when the accessibility title is empty. Recall, Screenpipe, and OtterPilot document screenshot or screen capture. Rewind’s always-on Mac capture is not a live product as of the 19 December 2025 shutdown. Do not add those payloads to catch up.

**Activity redaction is on, and one override cannot punch a hole in secure input.** Denylist before write, `scrub_v2` on text, sanitized URLs, 48-hour raw events. Password managers and Tor Browser are on the default app list; password, sign-in, and similar strings are on the default title list. ActivityWatch’s security page says whoever can read the SQLite file can read the data, and that encryption is future work. Palantir in Grain turns redaction off, clears both exclusion lists, and enables all six signals, and it restores the previous settings afterward. It still cannot store secure-input keystrokes.

**Import and categories already exist.** Granola cannot transcribe an upload. Grain can, with consent, on or off macOS. Category rules classify locally with no model call, and the Activity panel can edit them. The market note does not describe that classifier. Do not rebuild it.

**Habits are propose-first, with one default that is not.** Suggestions do not change todos, memory, or settings until the user applies them. High-confidence habits do write Memory when `autoMemory` is left on. That second path is the one to change. The market note has no habit product to copy.

**A meeting bot, cloud training, and company-wide sharing are out of scope on purpose.** Grain is a local macOS app. Fireflies’ bot, Otter’s training purpose, Wispr’s always-cloud transcription, and Granola’s AWS note store with link sharing are product choices this codebase should not mirror. The app inventory does not describe a “train on my audio” switch.

## 4. Gap table

Rows are ordered for a builder. The first three are the builds in section 5. “Do not” rows are market features that would break the privacy defaults in section 3.

| Gap | User impact | Evidence | How to close it in this codebase | Effort |
| --- | --- | --- | --- | --- |
| Stock meeting capture is mic only, and the speaker pass is unreachable | A recorded call stores the user’s side. Other people appear only if an output source was added. Even then, S1/S2 never run unless something outside the UI sets `diarize` and `keepAudio`, and the default Speech result has no timestamps to align. | App: default sources mic only; diarize defaults false; no control in `MeetingSettings.tsx`; no renderer call to `POST /meetings/{id}/diarize`; Speech `detail` is `{}`; mic is never diarized; diarize reads import, else output. Market: Granola captures mic plus system audio and is Me/Them without platform names; Meet/Zoom/Teams names are a further step; face-to-face labeling exists on Granola’s phone mic. [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription). | In `meetings.py` `start`, when the user has already passed consent and preflight, include `output` if the process tap or loopback is available. Keep dictate mic-only. Leave activity `outputAudio` off. If the far side is missing, keep today’s successful start and visible degradation. In `MeetingSettings.tsx`, add `diarize` defaulting false, and state that stop-time diarization already requires `keepAudio`. Wire the existing client in `api.ts` to `POST /meetings/{id}/diarize`. In `stt.py`, when diarize is on and Speech would return empty `detail`, use the local whisper `-oj` segments if that backend is installed; otherwise keep `[you]` / `[them]`. In `diarize.py`, run the existing assignment on the mic wav only when mic is the sole channel; when output exists, leave mic as `[you]` and diarize output or import. No enrollment store, no cross-meeting voice match, no Meet/Zoom/Teams integration (none is in the tree). | M |
| A clean enhance pass writes `enhanced` with no accept, and nothing cites the transcript | The notes column stays the user’s. The enhanced note can still appear on its own, and the user cannot see which timestamp a sentence came from. Doc summaries already stay pending. Granola shows user text and model text differently and points at the source span. | App: `enhanceOnStop` defaults true; auto-accept when `enhanced` is empty or equal to the last applied revision; degraded fallback never auto-accepted; `notes` only via user patch; `summarize_into_doc` is `propose_append` and is not auto-applied. Market: [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes), [How to get the best from Granola](https://www.granola.ai/blog/get-the-best-from-granola). Fireflies tells the owner to regenerate after a speaker rename: [speaker labels](https://guide.fireflies.ai/articles/1234873612-how-to-edit-speaker-labels-in-uploaded-files). | In `meetings.py` `stop`, stop auto-accepting a successful `meeting_notes.enhance` result. Leave the revision pending until `accept`, including when `enhanced` is empty. Keep refusing the degraded fallback. Never copy model text into `notes`. In `meeting_notes.py`, the transcript lines already carry `mm:ss`; keep those timestamps on the revision when a point depends on them, and show that pending revision in `MeetingsView.tsx`. After `PUT /meetings/{id}/speakers`, queue the same propose path and do not auto-accept it. | M |
| No personal vocabulary, and dictation has no spoken punctuation | Names and jargon are the accuracy lever Granola and Superwhisper document. Grain’s prompt is only the previous clip’s tail. The dictation formatter does not implement punctuation-by-name, which the Mac dictation guide does. | App: `TranscribeWorker._transcribe_one` prompt is `TAIL_CHARS`; no vocabulary feature found; `dictationText` maps “new line” and “new paragraph” only. Market: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription), [Superwhisper](https://superwhisper.com), [Dictate messages and documents on Mac](https://support.apple.com/guide/mac-help/use-dictation-mh40584/mac). | Add a term list on the existing meeting config (`MeetingConfigIn` / `DEFAULT_CONFIG`) and a field in `MeetingSettings.tsx`. Prepend that list, plus attendee names from the calendar event `_nudge` / `suggest` already uses, to the current tail prompt in `meeting_recorder.py`. In `dictation.ts`, treat spoken “period,” “comma,” and “question mark” as punctuation the way whole-utterance “new line” already becomes a break, including when the word sits inside a clip. Do not append a period to every 8-second clip. A polish pass, if added, goes through `docs.propose_append` like `recording_summary`, on text already transcribed, and stays unapplied until the user accepts. Do not send that audio to a new cloud transcriber. | M |
| Live transcript partials are off | The user sees text only after a clip finishes and STT returns. Dictate waits out a pause, up to 8 seconds, plus STT. Granola shows a live split transcript. Apple’s newer transcriber has a progressive preset. | App: `setShouldReportPartialResults_(False)`; transcript built in `build_transcript` at finalize; dictation inserts settled clips only. Market: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription), [SpeechTranscriber.Preset](https://developer.apple.com/documentation/speech/speechtranscriber/preset). | This is the `SpeechTranscriber` path, which the tree does not call. Add it beside `stt.py` `_speech` for dictate and for an optional live meeting view. Persist the finalized transcript only. `readyForInsert` must ignore volatile partials so the doc is not filled twice. Keep capture opt-in. | L |
| `autoMemory` writes Memory without a per-habit confirmation | After the monitor has been on long enough for a refresh, a habit at 0.6 confidence becomes a memory and a later pass rewrites it. Suggestions in the same file do not apply themselves. | App: `insights.py` `autoMemory` default true, `memoryConfidence` 0.6, `_persist_habits`. Market note has no habit ledger to copy. | Default `autoMemory` to false in `DEFAULTS`. Keep the current “flag off drops that habit’s memory on the next persist” behavior. Leave `apply` button-only. Habits stay on the Insights tab and in `context/activity.md`. Do not add them to `Monitor.context_block` in the same change. | S |
| Meeting transcript redaction is credentials only | An email or phone that the activity gate would scrub can sit in a meeting transcript, which is kept after the wav is gone. | App: `finish_segment` calls `redact.scrub_secrets` when `redactSecrets` is true; email and phone rules used by the activity gate are not applied. | In `finish_segment`, run the activity gate’s email and phone rules from `redact.py` on segment text and utterance detail. Leave entropy and title redaction on the activity path so ordinary words in a meeting are not stripped by the activity threshold. Keep `redactSecrets` default true. Do not touch the `notes` column. | S |
| Meeting text has no retention control | Wavs age out in 7 days. Transcript rows never do. Activity raw events expire in 48 hours. Granola enterprise and Otter enterprise can auto-delete transcripts. | App: no `expires_at` on meeting rows; `AUDIO_RETENTION_SECONDS` is 7 days; activity `retentionHours` defaults to 48. Market: [Transcript auto-deletion](https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion), [Set a custom Data Retention policy](https://help.otter.ai/hc/en-us/articles/19500988656279-Set-a-custom-Data-Retention-policy). | Add an optional transcript-and-audio lifetime on the meeting config. Default it to “keep text,” which is today’s behavior, so existing notes are not wiped by the upgrade. When the user sets a window, delete transcript text and remaining wavs. Do not delete the `notes` column or an `enhanced` value `accept` already applied. | M |
| A finished meeting cannot take more audio | The user cannot append to a note they already stopped. Granola can resume a finished note and regenerate. | App: `start` requires status `scheduled` or `notes_only`. Market: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription). | A user-triggered resume on a finished row that appends segments, still behind consent, the master switch, and preflight. Do not clear `notes` or an accepted `enhanced` value. | M |
| Meeting auto-stop ignores silence | With no calendar end, a recording runs until a channel dies or 4 hours. Granola stops after 15 minutes with no new audio. The VAD gate already exists and is not consulted by `_auto_stop`. | App: `_auto_stop`, `autoStopGraceSeconds` 90, `maxMeetingSeconds` 14400; loop comment is stale relative to `meeting_vad.py`. Market: [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription). | In `_auto_stop`, if `meeting_vad` reports no speech for 15 minutes, stop. Keep the calendar grace and the 4-hour cap. Use 15 minutes because that is the figure Granola documents, and do not reuse the 90-second calendar grace for it. Silence may stop a recording the user started. It must not start one. | M |
| Templates are the six built-ins | Structure is whatever those templates produce. Granola lets the user supply a prompt and outline. | App: `ENHANCE_PROMPT` templates listed in section 1. Market: [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes). | A user template string on the meeting config, passed into the existing `meeting_notes.enhance` prompt. Built-ins stay. | S |
| Dictation is inside Grain docs, as whole clips | It does not ride along in other apps, and it does not show a partial. That is the 2026 third-party category (Wispr, Superwhisper, Yap) and the OS floor (“anywhere you can type”). | App: `DOC_MODES`, `dictation.ts`, 8-second ceiling. Market: [Dictate messages and documents on Mac](https://support.apple.com/guide/mac-help/use-dictation-mh40584/mac), [Wispr Flow privacy](https://wisprflow.ai/privacy), [Superwhisper](https://superwhisper.com), [Yap](https://github.com/frigadehq/yap). | Do this only after the in-doc formatter and vocabulary work. A global shortcut that pastes final text into the focused app, same consent and master switch, mic only, no retained audio. Out of scope for the first pass. | L |
| Activity exclusions are case-insensitive substrings | A private window is all-or-nothing on an app name or a title substring. There is no “this app only when the title matches.” | App: `Gate.excluded`. Category rules are a separate classifier and already ship. Market note does not document regex exclusions. | Extend `Gate.excluded` with an optional per-app title condition. Keep the default password-manager list and the secure-input skip. | M |

### Do not build

- Screenshot capture, OCR, accessibility-tree body text, or always-on screen recording. Market products that do this are Recall, Screenpipe, OtterPilot, and the discontinued Rewind Mac capture. They are not a backlog.
- Training on user audio, or an Otter-style voice-enrollment store that re-identifies people across meetings. Otter’s policy includes training on de-identified audio; Wispr’s improvement control uses dictation audio and edits. Grain has no such switch. Speaker labels in this table are per meeting, from the existing seam, with manual rename.
- A meeting bot, including a bot whose job is to post a consent line into the call.
- Default-on recording. `autoRecord` stays false. The activity monitor stays off until enabled. Turning on the meeting output source applies only after consent and `start`.
- Writing enhancement or a dictation polish into the user’s `notes`, or auto-applying a doc append.
- A second diarizer, a second importer, or a second category engine. Import and categories are shipped. Diarization is shipped and hidden.

### Not established, so not scheduled

- Whether Grain’s database is encrypted, or whether the local API requires a token. The app inventory does not say. Screenpipe’s architecture page does, for Screenpipe.
- A click-to-seek player. The app note did not find one in the modules it read and did not prove the rest of the renderer has none. The market note does not document a player control by name.
- Whether Apple Speech text is already punctuated when it reaches `dictationText`.
- The editor undo stack for dictation insert. The hook calls `insert` and bails when it returns false. The textarea path was not traced.
- Fathom, tl;dv, Fellow, Zoom AI Companion, Google Meet notes, and Notion meeting notes. The market note did not fetch them.

## 5. Top three builds

Order is user-visible capture quality first, then note trust, then dictation accuracy. Each one stays opt-in, leaves `notes` under the user’s patch, and keeps training-on-audio and screenshots out. The two S privacy fixes (`autoMemory` off, email and phone scrub on meeting segments) are small enough to land in the same week; they are not ahead of these three because they do not change what a recording contains.

### 1. Far-side audio on a meeting the user started, and a speaker pass that can run

Ship the output source as part of an already consented `start`, and make `diarize` a real setting that stays off until the user turns it on. Feed it timed segments. Label the mic as `[you]` when the other side exists. Label the mic with the existing diarizer only when it is the only channel. Keep activity system-audio off. Dictate stays mic only.

Offline acceptance (no microphone, no network, no sherpa import; stub the diarizer the way `NullBackend` already short-circuits):

- Default config has `diarize` false and `autoRecord` false. `MeetingConfigIn` still cannot set `consentedAt`.
- With system audio reported available, the source list for a meeting `start` includes `output`. With system audio unavailable, `start` still succeeds and the far side is reported missing. A dictate `start` has mic only in both cases.
- Fixture output segments whose `detail.segments` have start and end: `diarize` true and `keepAudio` true yields S1 and S2 in `build_transcript`. `diarize` false yields `[you]` / `[them]` only.
- A Speech-shaped segment with `detail` `{}` does not gain speaker ids. A whisper-shaped segment with start and end does, when diarize is on and the stub backend returns turns.
- Mic plus output: mic lines stay `[you]`. Mic only and `diarize` true: speaker ids come from the mic wav. Mic only and `diarize` false: `[you]`.
- `notes` is unchanged by the diarize pass.

### 2. Enhanced notes stay a proposal until accept, and point at a timestamp

Match the doc recorder: a successful enhance queues a revision and does not fill `enhanced` by itself. Citations use the `mm:ss` the transcript already has. Speaker rename queues another proposal and does not accept it.

Offline acceptance (stub the model; do not call a hosted LLM):

- A user `notes` string is identical before enhance, after the queued pass, and after `accept`.
- After stop, with a stub that returns a normal enhancement, `enhanced` is still empty (or still the previous accepted text) until `accept`.
- The degraded fallback, notes plus raw transcript, is still not accepted.
- The stub returns one point tied to a timestamp that exists on a fixture transcript line. The pending revision still contains that timestamp after `accept`.
- A second pass does not replace an `enhanced` value that differs from the last applied revision.
- `summarize_into_doc` is still unapplied, as a regression check on the doc path.

### 3. Vocabulary in the prompt, and spoken punctuation in the doc

One term list for meetings and dictation. Attendee names from the calendar event already used to suggest a meeting go on the whisper prompt in front of the existing tail. Spoken punctuation is a formatter change. Any prose polish is a pending doc append, not an insert.

Offline acceptance (no audio device and no network; assert on the prompt string and the formatter, not on word error):

- Given attendees and a term list, the string passed into the whisper prompt contains those strings and still contains the previous-clip tail.
- `dictationText` of a whole-utterance “new line” or “new paragraph” is still a break. `$` before a digit is still escaped. Sentence-start capitalization still happens.
- “thanks period” becomes text ending in a period. “hello comma there” contains a comma. “question mark” as the punctuation word becomes `?`. A clip with no spoken punctuation mark is inserted without a period added by the formatter.
- A polish request creates a pending append and does not change the doc body until the existing accept path runs. Dictate with no polish request still inserts the formatted clip and does not create that append.

## 6. Left off the backlog on purpose

The app note marks these as already in the working tree. They are not builds:

- **Audio import.** `meeting_import.py`, the meetings file picker, and the doc Record menu. Consent and ffmpeg required. Not macOS-only. Granola’s lack of upload is a Grain advantage.
- **Category rules.** `activity_categories.py`, day-stat `cats`, `category_share` and `distraction_drift` in `mine()`, the Activity bar and rule editor. Classification does not call a model. They are a classifier, not an exclusion list, and they are not hidden.
- **The diarization seam itself.** `diarize.py`, `diarize_segments`, speaker rename, `POST /diarize`, `PUT /speakers`. The gap is that the switch is absent from settings, the renderer never calls it, the default recognizer supplies no times, and the default meeting source is the channel the seam refuses to label. Section 5 build 1 is that gap. It is not a new diarizer.
- **VAD, the hallucination filter, native mic and process-tap audio, and `scrub_v2`.** The older sota “where we are” pages deny these. The tree has them. Use them in build 1 and in the silence auto-stop. Do not re-implement them from those pages.
