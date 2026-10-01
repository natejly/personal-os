# Changelog

All notable changes to Grain (formerly Personal OS). Dates are the days the work landed on `main`. There are no version tags yet, so everything sits under Unreleased.

## Unreleased — 2026-10-01

### Added

- **Meetings** (macOS, opt-in, off by default). A notepad that listens: one
  long-lived ffmpeg per channel records a call with the segment muxer, closed wavs
  transcribe on a worker thread, and afterwards the enhance pass proposes your
  typed outline with the transcript filled in around it as a diff you accept or
  reject. Your notes and the enhanced notes are separate columns, so no model ever
  writes what you typed. Includes six note templates, channel-level attribution
  (you versus them), FTS5 search over titles/notes/enhanced/transcripts, action
  items promotable into todos, a 45-second calendar nudge that offers a Record
  button on live events, a **Meetings** view on ⌘⇧M with a live recorder bar, an
  **Upcoming meetings** card on Today, and the three read-only tools
  `meeting_list`, `meeting_search` and `meeting_read` (the last two taint the run,
  because a transcript is other people's speech). Nothing that starts, stops,
  pauses, enhances or deletes a meeting is a tool at any tier. Recording requires a
  one-time acknowledgement of a modal naming the exact directory the audio is
  written to and the exact base URL it is uploaded to; meetings carry no
  `expires_at`, are unreachable from `POST /activity/purge`, and never reach
  auto-learn. See [docs/meetings.md](docs/meetings.md).
- `stt.py`: swappable speech to text — the LLM proxy, a local whisper.cpp, or off,
  with `auto` resolution — plus `POST /meetings/selftest`, which writes a real wav
  and does a real round trip. A failing self-test *blocks* Record rather than
  warning. A default `litellm.yaml` still has nothing behind
  `/v1/audio/transcriptions`; it now ships a commented block showing how to add
  one, and the on-device backend needs no provider at all.
- `audiocap.py` and `redact.py`: the ffmpeg/device layer and the named redaction
  rules, lifted out of `activity.py` so both features share one copy. Meetings use
  the credential rules only — the gate's `email` and `phone` rules would replace
  every attendee with `[email]` and erase identity from inside a conversation.
- `backend/tests/test_mcp_servers.py`: the guard `mcp_servers.py`'s comment has
  always promised now exists, asserting `RESERVED_TOOL_NAMES` really is every
  built-in tool name. It was unguarded until now, and the previous commit was
  literally the fix-up for forgetting the calendar tools.

### Fixed

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
