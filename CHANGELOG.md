# Changelog

All notable changes to Grain (formerly Personal OS). Dates are the days the work landed on `main`. There are no version tags yet, so everything sits under Unreleased.

## Unreleased — 2026-10-02

### Added

- **Recording into a doc.** Any doc can be recorded from a toolbar Record control
  (*Record and summarize*, *Dictate into note*, *Import audio file*), the `/`
  menu, or the Recordings tab of a new side panel, which shows a live transcript
  and a Summary tab per recording. A doc recording is a `meetings` row with
  `doc_id` and `doc_mode`, started through the same `MeetingService.start`, so
  consent, preflight and the one-recording-app-wide rule are unchanged. On stop a
  summary is proposed into the doc as an *append* revision
  (`Docs.propose_append`, `tool="recording_summary"`): it stores only the section
  and is resolved against the doc as it stands when you accept, so text typed
  meanwhile survives. It is never auto-applied, and a model failure proposes
  nothing rather than pasting the transcript. Transcripts stay in the meetings
  tables, never in the doc body or its index, so `meeting_search` and
  `meeting_read` remain the only way in and stay tainted. Dictation types each
  finished mic clip at the caret and proposes no summary. Doc recordings close
  clips at a pause (`docSegmentSeconds` 10, `dictationSegmentSeconds` 8 are
  ceilings, `meeting_vad.find_cut`), and `recording` events on `GET /events` push
  updates, with a two-second poll as the fallback. Routes: `POST`/`GET
  /docs/{id}/recordings` and `POST /meetings/{id}/summarize`. They are left out of
  the Meetings rail and the "Recent meetings" context block, trashing a doc hides
  them and purging it deletes them, and a doc with a `record` recording is not
  banked as a writing-style sample. See
  [docs/docs-editor.md](docs/docs-editor.md#recording-into-a-doc).
- **Note-taking in Docs.** A `/` command menu, `[[wikilinks]]` with a picker and a
  Links (backlinks) tab, clickable task checkboxes in the preview, an Outline tab,
  reading time and selection counts, templates and a New menu, a daily note
  (`POST /docs/daily`), export (download, copy, print or save as PDF), and smart
  paste of a URL over a selection. Programmatic inserts go through one editor
  handle that uses `insertText` so they stay on the undo stack. The History
  view is now one tab of the side panel. Limits: transcript latency is a clip
  length (a pause, or the ceiling), not word by word, and dictation shares it;
  capture is macOS only; undo of inserted text depends on Chromium's `insertText`
  and has not been run in a live build; print renders maths as source.

## Unreleased — 2026-10-01

### Added

- **Meetings** (macOS, opt-in, off by default). A notepad that listens: native
  capture (AVAudioEngine, and a Core Audio process tap for the far side of the
  call on macOS 14.2+) records a call in short wavs, those transcribe on a worker
  thread, and afterwards the enhance pass proposes your typed outline with the
  transcript filled in around it as a diff you accept or reject. ffmpeg,
  whisper.cpp and BlackHole are fallbacks, not a setup tax. Your notes and the
  enhanced notes are separate columns, so no model ever writes what you typed.
  Includes six note templates, channel-level attribution (you versus them), FTS5
  search over titles/notes/enhanced/transcripts, action items promotable into
  todos, a 45-second calendar nudge that offers a Record button on live events, a
  **Meetings** view on ⌘⇧M with a live recorder bar, an **Upcoming meetings** card
  on Today, and the three read-only tools `meeting_list`, `meeting_search` and
  `meeting_read` (the last two taint the run, because a transcript is other
  people's speech). Nothing that starts, stops, pauses, enhances or deletes a
  meeting is a tool at any tier. Recording requires a one-time acknowledgement of
  a modal naming the exact directory the audio is written to; meetings carry no
  `expires_at`, are unreachable from `POST /activity/purge`, and never reach
  auto-learn. See [docs/meetings.md](docs/meetings.md).
- `stt.py`: swappable speech to text — on-device Speech, the LLM proxy, a local
  whisper.cpp, or off, with `auto` preferring Speech when it is granted — plus
  `POST /meetings/selftest`, which writes a real wav (no ffmpeg) and does a real
  round trip. A failing self-test *blocks* Record rather than warning. A default
  `litellm.yaml` still has nothing behind `/v1/audio/transcriptions`; it now ships
  a commented block showing how to add one.
- `audiocap.py`, `native_audio.py` and `redact.py`: capture prefers AVAudioEngine
  and a Core Audio process tap; ffmpeg avfoundation and BlackHole are fallbacks.
  The named redaction rules were lifted out of `activity.py` so both features
  share one copy. Meetings use the credential rules only — the gate's `email` and
  `phone` rules would replace every attendee with `[email]` and erase identity
  from inside a conversation.
- `backend/tests/test_mcp_servers.py`: the guard `mcp_servers.py`'s comment has
  always promised now exists, asserting `RESERVED_TOOL_NAMES` really is every
  built-in tool name. It was unguarded until now, and the previous commit was
  literally the fix-up for forgetting the calendar tools.
- **Cowork: desks that pick up where they stopped.** Every round of a desk turn now
  carries the approved plan with each step's status, and the system prompt carries
  the desk hint (`outputs/`, `desk_deliver`, `desk_ask`, `desk_done`). Before this,
  a turn that was woken or chained was told to "continue the plan below" with no
  plan in front of it. Cards a desk parked and you answered later are reported to
  its next turn: plan approved or rejected, your answer to its question, call
  approved or declined. An approved parked call becomes a single-use grant for
  exactly those arguments, so repeating it runs without a second card. The desk
  pane now answers waiting cards in place, opens a pending plan on the Plan tab,
  and lets you change autonomy and the desk's own turn and spend limits. The rail,
  badge and Today card are live for every desk (`desk_status` on `GET /events`),
  and archived desks are one toggle away.
- **Plan mode in ordinary chats.** The composer's off / auto / always toggle now
  drives the backend. *always* drafts a plan before anything consequential.
  *auto* switches to drafting the first time a reply reaches for a write, send or
  run. Both use the same planning gate as desks.

### Fixed

- Plan-mode refusals reached the model as "turned off for this chat, do not
  retry", because the `off` branch ran before the planning message. The model now
  hears "put it in a plan step". This affected desks too.
- A desk's Plan tab said "No plan yet" while its plan was waiting on you, and its
  Approve button posted the plan id to `/approvals/{call_id}` and got a 404.
- An answer typed into a `desk_ask` card was discarded and the desk asked again.
  The answer is now the tool result, and the banner's box answers the same card.
- A parked desk turn never persisted its reply or sent `done`, so the card
  vanished on reload and a watching window kept spinning.
- `desk_import_sandbox` always failed (`MAX_FILE_CHARS` was never imported, and
  `ALTERNATIVE` had no `desk_*` entries), and the rail showed "waiting on your
  plan" on todo-list updates because it watched `plan` instead of `plan_card`.
- Pause and Stop accepted desks in review or done. The live-desk cap applied only
  to create and start, not to resume, messages or wakes. Desk outputs were never
  tied to the run that wrote them. A message sent during a run's closing tail got
  a 409 instead of waiting it out.
- `planMode` and `deskNotify` were missing from `DEFAULT_SETTINGS`, so
  `PUT /settings` silently dropped them.
- `tests/test_cowork.py`'s scripted stream had fallen behind `llm.stream_chat`'s
  signature, so the whole module refused to run.

- `AudioCollector` stamped each audio event with the time transcription *finished*
  rather than when the clip was recorded, because it called `store.add` with no
  `ts=` and `Store.add` defaults to `now()`. With a chunk that takes up to two
  minutes to transcribe, `ts + duration_ms` pointed into the future and the
  timeline put speech after events that followed it.
- `DEFAULT_CONV_SETTINGS` was missing `useActivity`, so the Activity toggle never
  appeared in a stored conversation's settings even though `context.py` read the
  key. `useMeetings` is listed beside it for the same reason.


## Unreleased — 2026-09-29 to 2026-09-30

### Added

- The original app: LiteLLM chat, tool-calling with ask/on/off permissions, projects, memory, knowledge graph, documents, todos, boards, dashboards, Google Calendar/Gmail/Tasks.
- **Canvas.** A spatial desk of windows (chat, todos, calendar, board, note, dashboard widget, memory, graph, documents, recap, project, usage). Drag and drop between widgets, status rings on chats, Spaces in the sidebar, savable presets. New windows open in place; two-finger scroll hits the widget under the cursor; plain drag pans, shift-drag marquees.
- **Pop-outs.** Any canvas window can detach; menubar tray and a Gather shortcut bring them back. Pinned pop-outs ignore the window shortcuts until unpinned.
- **Run bus.** A reply keeps going after you close the window that started it. Canvas chats can switch conversations and survive a deleted chat. You can steer a live reply from the composer.
- **Mail.** Gmail tab with filters, message viewer, compose/reply/send, AI draft review, and Tab-complete ghost text.
- **Calendar editor.** Create, edit, delete; recurrence, guests, Meet links, reminders, colors. Todos drop onto a day or hour and can create a Google event. Local-day due dates (not UTC).
- **Google.** Docs and Sheets tools; Tasks and Drive on Today and in chat; two-way todo sync with Google Tasks. Desktop OAuth sign-in.
- **Docs.** Markdown/LaTeX editor with reviewable assistant revisions, folder picker, and Memory + Documents grouped as Knowledge Base.
- **Activity monitor.** Records what you do (with a privacy gate), rolls it up, and can feed it back into chat.
- **MicroVM sandboxes.** Persistent per-chat Linux containers as agent tools, alongside the local Python sandbox.
- **Web widget.** A browser window on the canvas.
- Interactive chart blocks (sliders and other controls in chat).
- Reasoning effort on chat. Modular shell: hide sidebar views and Today cards.
- Rebrand to Grain (rice-grain logo).
- Research notes under `docs/research/` (memory, retrieval, desktop assistants, life OS, agent loop, Context.ai gap analysis).

### Security

- Sidecar auth on every route except `/health`; CORS limited to the Vite dev ports.
- SSRF guard on `fetch_url` (no loopback/private after DNS; redirects re-checked).
- Hardened macOS `sandbox-exec` profile; each rlimit applied on its own so one unsupported limit does not disable the rest.
- Taint tracking: once a run has read untrusted mail or web, external tools that were `on` are forced to `ask`, and standing grants cannot be bought from a tainted reply.
- Approvals keyed by message id plus call id, so two concurrent chats cannot resolve each other's cards.

### Fixed

- Approvals resolving the wrong conversation's tool call.
- Calendar `toISOString()` `…Z` timestamps rejected by Python 3.10 `fromisoformat`.
- Gmail list: batched metadata fetch and RFC 2822 dates converted to ISO; inbox query defaults to unread in the last 14 days.
- Canvas click on a window that is not on top crashing the renderer.
- Transparent windows, flicker mid-drag, see-through dashboard rectangles, geometry stored as null.
- Menu click killing the app; renderer death with no explanation.
- Keyboard shortcuts wired twice (or not at all until the backend was up).
- Stale project id 500s; data-source fetches unguarded.
- Sidebar footer falling off the bottom of the window.
- Widget error boundary so one throw does not take down the whole canvas.

### Changed

- Chat widget reworked; every canvas window mounts up front instead of on demand.
- Google sign-in button always starts OAuth rather than looking like a no-op.
- UI copy stripped of filler. Accessibility pass on labels and contrast.
- `mcp_servers.py` reserved-name list kept in sync as new built-in tools land (activity, calendar). MCP connectors themselves are not a user-facing feature yet.
