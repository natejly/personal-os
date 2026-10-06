# Notes that listen: UX research for recording inside Docs

Scope: how the best "notepad that listens" products behave, so Docs can gain in-note recording, live transcript, summaries and stored transcripts. This extends `docs/research/sota-meetings.md` (capture, VAD, STT backends, diarization) and `docs/meetings.md` (the Meetings recorder, enhance-as-diff). It does not repeat them. Research date 2026-10-02. Facts come from product help centers where possible; review-site claims are labelled as such. Competitor names are fine here (docs/research is exempt from the no-competitor-names rule); they must not leak into code, UI or docs outside this folder.

---

## 1. Granola, in detail

Sources: [Granola help index](https://docs.granola.ai/llms.txt), [How transcription works](https://docs.granola.ai/help-center/taking-notes/transcription.md), [AI-enhanced notes](https://docs.granola.ai/help-center/taking-notes/ai-enhanced-notes.md), [Writing your own notes](https://docs.granola.ai/help-center/taking-notes/taking-notes-in-granola.md), [Templates](https://docs.granola.ai/help-center/taking-notes/customise-notes-with-templates.md), [Chat](https://docs.granola.ai/help-center/getting-more-from-your-notes/chatting-with-your-meetings.md), [Recipes](https://docs.granola.ai/help-center/getting-more-from-your-notes/recipes.md), [Data FAQs](https://docs.granola.ai/help-center/consent-security-privacy/security-privacy-data-faqs.md), [Multi-language](https://docs.granola.ai/help-center/customising-granola/multi-language.md), [Dictation vs transcription](https://docs.granola.ai/help-center/getting-more-from-your-notes/granola-chat-dictation-vs-transcription.md), [Granola 101](https://docs.granola.ai/help-center/getting-started/granola-101.md).

**Starting a note.** Capture begins only when the user engages with a note: clicking a notification, opening a scheduled meeting after it starts, pressing New Note (top right), or sitting in a calendar meeting when its start time arrives. The help center says it "does not automatically transcribe meetings without you having clicked into a note." The calendar is a list of upcoming meetings; a note can be created before the call (prep) or during it, and the calendar event supplies title, attendees and context to the enhance step.

**While recording.** The notepad is the whole screen. Recording state is three green dancing bars at the bottom of the window (the audio level indicator); a floating indicator shows when the user switches to another app. A waveform button opens a live transcript panel; it is hidden by default. Bubbles are coloured by channel: grey for system audio (others), green for the mic (you). The editor takes light markdown (`#`, `-`, `[]`, bold/italic), images by paste or drop, and `/` opens a menu including templates. The docs tell users not to worry about typos or abbreviations. Typing notes is optional; with no notes it works from the transcript alone.

**Stop and enhance.** Notes are generated automatically when the meeting ends (or recording is stopped by hand), from three inputs: transcript, the user's raw notes, calendar event info. Distinguishing the two authors is done by colour: the user's own text stays black, AI-added text is rendered grey. Edits are allowed either way, and per the help docs edits apply only to that note and do not train later notes. There is an Enhanced / My notes pair of views per note.

**Regenerate.** A sparkle button picks a different template; a refresh button regenerates with the current one; or ask in chat ("make next steps more detailed"). Templates are chosen per note, via `/` during the call or from the Enhanced tab afterwards.

**Zoom into source.** A magnifying-glass icon beside each AI line shows "where in the transcript or raw notes the note came from." This is the evidence feature and the single best trust mechanism in the product.

**Templates.** Pre-built ones per role (sales call, 1:1, stand-up, and so on), custom templates written as prose instructions: purpose and context, length/style/detail level ("include quotes and data"), and section structure. Private by default, shareable org-wide.

**Chat.** A floating bar at the bottom (Cmd+J). Scope is one note (before, during, after the meeting), a folder, hand-ticked notes, or everything. Rewriting a section of the note is possible by phrasing. Chats are never shared when a note is shared. Dictation on the chat bar is only for asking questions; it does not capture a meeting.

**Recipes.** Reusable saved prompts, invoked by typing `/` in the chat bar or from a Recipes tab; shareable (private, workspace, link). Static: no variables.

**Organisation and sharing.** "My notes" plus team space; folders can be shared and also act as chat scopes. Notes are shared by link/workspace. Integrations (Notion, Slack, CRMs, Zapier, MCP, API) push notes out. Follow-up emails and pre-meeting briefs are generated from the same data.

**Audio.** Not stored: audio is cached during the meeting only for transcription, then deleted. Transcript and notes persist indefinitely by default, on a cloud backend, with a local cache for offline editing. Transcript panel allows copying the whole transcript or a chunk, and deleting individual segments. Speaker tags (real names) exist for Meet/Zoom/Teams; otherwise it is just "Me" / "Them".

**Languages.** 31 on desktop, 17 on mobile. Desktop has English or multi-language mode, switchable mid-meeting at the cost of a short pause while a model loads. Summary language: English or auto-match.

**Praise** (reviews: [Zack Proser](https://zackproser.com/blog/granola-ai-review), [TechCrunch launch](https://techcrunch.com/2024/05/22/granola-debuts-an-ai-notepad-for-meetings)): no bot in the call, you stay in your own notes, the output reads like your notes improved. **Complaints** ([Happyscribe](https://www.happyscribe.com/blog/granola-ai-review), [tl;dv](https://tldv.io/blog/granola-review/), [Anarlog](https://anarlog.so/blog/granola-ai-complaints/); competitor-written, so discount the framing): no audio so nothing to verify a wrong name or number against, weak speaker attribution beyond two people, language limits, and cannot regenerate notes if the transcript was deleted.

## 2. Comparable notepads

**Apple Notes** ([Mac help](https://support.apple.com/guide/notes/record-and-transcribe-audio-apdb5106e334/mac), [MacRumors](https://www.macrumors.com/how-to/ios-record-audio-transcribe-notes-app/)). Model: an audio recording is an attachment object embedded in the note, beside text, checklists and files. Flow: attach button, Record Audio, record button; pause/resume; a transcript button during recording shows live text. After recording, double-clicking the recording opens an "audio details" view with the transcript; More menu has Find in Transcript, copy, "add to note", and Apple Intelligence summary (copy/share). Deleting the recording deletes its transcript. M1+ Macs, about ten languages. Complaints ([MacRumors forum](https://forums.macrumors.com/threads/notes-audio-transcription.2436709/), [Granola blog comparison](https://www.granola.ai/blog/how-to-record-meeting-notes-on-iphone-native-apple-options-vs-ai-note-takers-2026), the latter vendor-written): no speaker separation, one continuous block; summary needs newest hardware; playback controls vanish; tapping for playback fights text selection; hard to see where you are in the transcript. Lesson: the embedded-object model is the right mental model for "a doc holds recordings", but the object needs a real transcript viewer with position tracking.

**Notion AI Meeting Notes** ([Notion guide](https://www.notion.com/help/guides/preserve-perfect-meeting-memory-with-ai-meeting-notes)). A block added by `/meet` or a "Meet" pill at the bottom of a page, or a mic icon on an existing page, or from the menu bar during a call. The block has Summary, Notes and Transcript tabs. Summary formats: Auto, Sales, Stand-up, Team meeting. The summary draws on both the notes you typed and the transcript; people tagged in notes are tagged in the summary; instructions written in the Notes tab ("summarise in 5 bullets for execs") steer the summary. Reviewers ([eesel](https://www.eesel.ai/blog/notion-ai-meeting-notes), [tl;dv](https://tldv.io/blog/notion-ai-meeting-notes-review/), vendor-written): no speaker identification, no audio stored, manual start/stop so people forget, no import of recordings. Lesson: a block inside a normal page, with tabs, is easy to understand and keeps the page as the unit; notes-as-prompt is cheap and loved.

**Hyprnote / Anarlog** (open source, [repo](https://github.com/fastrepl/hyprnote)). Local audio capture, on-device or chosen STT, any LLM (local via Ollama), data in SQLite with Markdown export. Memo-plus-transcript summary; the memos are optional; templates (bullets, agenda, paragraph, custom). The enhance prompt is not documented in the README, so I could not quote it.

**Meetily** ([repo](https://github.com/Zackriya-Solutions/meeting-minutes)). Live transcript (Whisper/Parakeet), summary through Ollama or hosted models, custom summary templates are a paid tier. Its `transcript_processor.py` is the clearest public example of long-transcript handling: 5,000-character chunks with 1,000 overlap (larger for big-context models), each chunk summarised independently into a fixed JSON schema (People, SessionSummary, CriticalDeadlines, KeyItemsDecisions, ImmediateActionItems, NextSteps, MeetingNotes), "if a section has no relevant information in this chunk, return an empty list", correct transcription errors, JSON only. Notably it does not merge the chunk summaries in that file; the merge is left downstream. That is the weak point to avoid.

**Voicenotes** ([site](https://voicenotes.com/)). Recording yields title, transcript, summary, speaker labels, decisions, follow-ups; everything is in one note library with Ask-AI over it, plus imports and an MCP server. Dictation, voice memos and meetings share one note type.

**Obsidian plugins** ([Whisper](https://community.obsidian.md/plugins/whisper), [NeuroVox](https://www.obsidianstats.com/plugins/neurovox)). Record or upload, transcribe, optionally post-process with an LLM; the audio file is embedded in the note (`![[audio]]`) and the transcript is inserted as a callout. Custom prompts per post-process. Lesson: transcript-as-inserted-text is simple but bloats the note and loses timestamps.

**Superwhisper / Wispr Flow** ([comparison](https://superwhisper.com/vs/wispr-flow), [ClickUp](https://clickup.com/blog/wispr-flow-vs-superwhisper/)). These are dictation-into-text, a different job: hold-to-talk or toggle a hotkey, speak, formatted text lands at the cursor. Wispr cleans filler words and formats automatically; Superwhisper is faithful and offers user-defined "modes" (prompts) that reshape the dictation, local models available. Lesson for Grain: "dictation" in a doc means two separate things, and the user said "dictation similar to Granola", which is meeting-style capture. Dictate-at-cursor is a cheap add-on (P2) that reuses the same STT.

## 3. Summary quality practices

Sources: [Gladia pipeline guide](https://www.gladia.io/blog/transcript-to-actionable-notes-llm), [AssemblyAI](https://www.assemblyai.com/blog/summarize-meetings-llms-python), [Summaries, Highlights, and Action Items (arXiv 2307.15793)](https://arxiv.org/html/2307.15793v3), [Re-FRAME (arXiv 2509.15901)](https://arxiv.org/pdf/2509.15901), the Meetily code above, and Granola's documented behaviour.

- **User notes are the outline.** Every product that does this well treats typed notes as the skeleton and the transcript as evidence to fill in. Granola: raw notes steer, transcript fills gaps. Notion: summary refers to the Notes tab, and instructions typed there are obeyed. Grain's existing enhance pass already does this; the same prompt applies per recording.
- **Structure.** Headline, then headed sections with bullets; a separate decisions list; a separate action items list with owner and due date when stated; open questions. Fixed schema (JSON or tagged markdown) so the UI can render and promote items. Templates change section lists, not rules.
- **Two-stage, not one prompt.** Extract first (explicit commitments, decisions, unresolved questions), then summarise, "excluding inferred commitments" (Gladia). Reduces invented action items.
- **Evidence.** Keep per-utterance timestamps and speaker/channel in the prompt; have the model return, per bullet, the segment ids it relied on. The UI shows these as a source link (Granola's magnifier). Extractive anchors (a few exact quoted sentences) reduce hallucination.
- **Long transcripts.** Split at speaker/segment boundaries, 10-20% overlap, summarise chunks in parallel into the same schema, then a reduce call merges and dedupes, preferring later statements when a decision was revised. For recordings that fit the model context, skip chunking: one pass beats map-reduce on coherence. Meetily's 5k-char chunks are a floor, not a target.
- **Preserve the user's words.** Typed notes pass through verbatim; AI may only add lines or expand an item below it. Never reword a user line without it being a visible suggestion. (Granola's black/grey and Grain's diff both implement this; they are different mechanics, see recommendation 7.)
- **Template per meeting type** and a free-text "focus" instruction at generate time ("for my manager", "only decisions"). Granola's template guidance: state purpose, length, style, "include quotes and data".
- **Language.** Summarise in the transcript's majority language or a chosen one; offer both.
- **Cheap guards.** Don't summarise transcripts under a minimum word count (a silent recording must not yield a plausible summary); label degraded fallbacks; attach the model name and template used to each summary so regenerate is reproducible.

## 4. Transcript UX

- **Granola**: hidden behind a waveform button; channel-coloured bubbles (you vs others); copy whole, copy chunk, delete segment; speaker names only with platform integration.
- **Apple Notes**: separate audio-details view, Find in Transcript, copy, add to note; complaints about losing your place.
- **Notion**: Transcript is a tab of the same block; no speaker labels.
- **Common gaps** ([arXiv recap-system study](https://arxiv.org/html/2307.15793v3) and the reviews above): no click-to-seek because no audio is kept; no indicator of how far transcription lags the speaker.
- **What Grain can do better, given audio can be kept optionally**: timestamps per segment (start of each 20-second segment is already stored); click a line to seek the stored audio when it exists, otherwise a disabled affordance with a tooltip; in-transcript find with highlights and next/prev; copy as text with `[mm:ss] you:` prefixes; auto-scroll that follows the live edge and pauses when the user scrolls up, with a "Jump to live" pill; the lag indicator Grain already has (`transcript ~20s behind · 3 queued`) shown in the recorder bar; speaker labels from channel (you / them) now, named speakers later (see the diarization spec in `sota-meetings.md`); a "not transcribed" marker for failed or empty segments so holes are visible rather than silent.

---

## Recommended feature set for Grain's Docs

Existing assets reused: segment capture, STT backends, VAD gate, `finalize` rollup, enhance-as-diff with `DiffView`, templates, consent modal, recorder bar with lag display, FTS, action-item promotion. Build the Docs feature on those rather than a second pipeline.

### P0: first version

1. **A doc can hold any number of recordings.** Each recording is its own row linked to the doc (not a column on the doc), with state (recording / processing / done / failed), started/ended times, optional audio kept flag, and per-segment transcript rows with start/end offsets, channel, text. Deleting a doc deletes its recordings and transcripts; deleting a recording leaves the typed note intact.
2. **Record button in the doc toolbar and `/record` in the editor.** Starts a recording attached to the open doc (consent modal on first use). The doc editor stays the primary surface; nothing about the layout changes until the user asks.
3. **Compact recorder bar pinned above the editor while recording.** Shows elapsed time, a live level meter, pause/resume, stop, and the existing "transcript ~Ns behind · N queued" text. If the mic is silent for 10+ seconds the meter says so, in the bar, so nobody records a dead mic for an hour.
4. **Inline recording block in the note.** On start, insert a block at the cursor ("Recording, 14:32, 18 min") that links to the recording and shows its state. It is the anchor for the transcript and summary, survives copy/paste of the doc, and is how a doc holds several recordings in order.
5. **Side panel with Transcript and Summary tabs.** Hidden while recording by default (notepad stays centre), opened from the recorder bar or the block. Transcript tab: live segments with timestamps, channel labels (you / them), auto-scroll with a "Jump to live" pill. Summary tab: the generated notes for the selected recording.
6. **Typed text is never overwritten.** Summary lives in its own field or tab and is inserted into the doc only on an explicit "Insert summary" or "Accept", through the existing diff/revision path. Typed notes are the outline passed to the model.
7. **Authorship visible in the doc.** When summary content is inserted, mark AI-authored runs (grey text or a subtle left bar) so human and model text stay distinguishable after the fact, and keep that mark in markdown (e.g. a comment fence or span class) so it survives save and reload.
8. **Generate summary on stop, per recording.** One LLM call using the doc's typed notes as outline plus the transcript, with a template choice (reuse the Meetings set: general, stand-up, 1:1, interview, sales, lecture) and a headline, decisions, action items. Runs automatically on stop, retryable, with a degraded fallback that is labelled.
9. **Stored transcripts, searchable.** Transcript rows persist with the recording, are included in FTS so doc search finds spoken words, and are downloadable as `.md` / `.txt`. The existing Meetings durability rules apply (no expiry, delete = gone).
10. **Crash-safe capture.** Segment files are flushed every 20 seconds as they are now; on app restart, any recording left in "recording" state is recovered: remaining wav segments are transcribed, the recording is finalized, and the doc shows "recovered after unexpected quit".
11. **Copy and export from the transcript tab.** Copy all, copy a selection, each with optional `[mm:ss] you:` prefixes.

### P1: strong follow-ups

12. **Evidence links on summary lines.** Return segment ids per bullet; render a small source icon that opens the Transcript tab scrolled to and highlighting those segments. This is the single most important trust feature.
13. **Click-to-seek when audio is kept.** "Keep audio" per recording; clicking a timestamp plays from there, with speed control. Without kept audio the timestamps still navigate the transcript only.
14. **Find in transcript.** Search box with highlighted matches, next/prev, and a "jump to this time in the note" cross reference when the match falls inside a typed line's time window.
15. **Note-time stamps.** While a recording is active, quietly record the time each typed line was written (a hidden attribute). The summary prompt then knows what was being said when each note was typed, and the Transcript tab can highlight the segment next to the focused line.
16. **Action items to todos.** Show proposed action items under the summary with Add to todos on each (existing promotion path), and a "promoted" state so they are not offered twice.
17. **Ask about this doc.** Chat scope selector: this doc (typed notes + transcripts), this recording, or the whole project. The page-agent panel (Cmd+I) already exists; give it the recording as context and let it answer during recording from the transcript so far ("what did they say about pricing?").
18. **Regenerate with a focus line.** A prompt field next to the template picker ("for my manager, decisions only") and a regenerate button that produces a new proposal without discarding the previous one; previous summaries kept as versions.
19. **Calendar start.** Record button on a doc created from a calendar event pre-fills title and attendees; the existing calendar nudge offers "Take notes" which creates the doc and starts recording in one step.
20. **Pause on long silence, resume prompt.** After a configurable silence stretch, pause capture and show "Still recording? Resume / Stop" in the bar, instead of recording an empty hour.
21. **Auto-title and tags.** Offer a title when the doc is untitled, derived from the summary headline; never rename a doc the user titled.

### P2: later

22. **Named speakers.** Diarization and per-meeting name mapping from `sota-meetings.md` (meetings-3), shown as coloured labels in the transcript.
23. **Import audio into a doc** (meetings-2) as a recording row, for voice memos and call recordings.
24. **Dictate at the cursor.** Hold-to-talk key inserts transcribed, lightly cleaned text at the caret, as ordinary typed text, reusing the same STT, with an optional cleanup prompt. Separate from recordings and never creates a recording row.
25. **Reusable prompts ("recipes") for a recording**: saved instructions run from a slash menu over the summary or the transcript.
26. **Custom templates** authored in Settings as prose (purpose, length, style, sections).
27. **Summary language choice**: transcript language or a fixed one.

### Placement summary

| Element | Where |
| --- | --- |
| Record / stop | Doc toolbar right side, plus `/record` |
| Recorder bar | Slim strip above the editor, only while recording (level meter, timer, lag, pause, stop) |
| Recording block | Inline in the doc at the cursor, links to the recording |
| Transcript / Summary | Right-hand panel with two tabs, collapsed by default, remembers last tab |
| Action items | Under the Summary tab, each with Add to todos |
| Settings | Keep audio default, STT backend, default template, consent text (existing Meetings settings) |

---

## Pitfalls

- **Hallucinated summaries.** Silent or near-silent recordings, or whisper hallucinations ("Thank you."), produce plausible notes. Keep the VAD gate and filter; refuse to summarise under a minimum word count; show "based on N minutes of speech".
- **Invented action items and wrong attributions.** The model assigns tasks nobody accepted, especially with only you/them labels. Extract-then-summarise, say "owner not stated" rather than guessing, and put source links on every item.
- **Overwriting or blurring the user's notes.** Users distrust anything that rewrites what they typed. Enhancement goes into a separate field or a visible diff; AI text is always visibly marked.
- **No way to verify.** Products that delete audio leave users unable to check a name or figure. Keep-audio per recording, off by default, with the trade-off stated in the setting.
- **Losing the recording on a crash or sleep.** Segment files and per-segment rows survive; recover on launch; re-enter after system sleep without losing the clock.
- **Latency without feedback.** A transcript that silently lags looks broken. Keep the "~Ns behind" line, mark failed segments, offer retranscribe.
- **Consent.** A bot-free recorder still records people who do not know. Keep the acknowledgement modal; add an optional one-click "copy a heads-up message" for the call, and show a persistent recording indicator in the bar and the doc tab.
- **Transcript pollutes the note.** Pasting the whole transcript into the doc (Obsidian-style) bloats it and loses timestamps. Keep it in its own store, reachable from the block and the panel.
- **Tapping fights text selection** (Apple Notes). Keep seek controls separate from the text, on timestamps only.
- **Manual start and forgetting to stop** (Notion). Calendar nudge, silence pause, and an always-visible bar.
- **Prompt injection from transcripts.** Existing rule applies: transcripts are untrusted; Docs recordings must reuse `taints=True` and stay out of auto-learn and default chat context, otherwise a doc becomes a bypass for the Meetings privacy rules.
- **Language mismatch.** STT language and summary language drift apart in multilingual calls; make both explicit settings and show the detected one.
