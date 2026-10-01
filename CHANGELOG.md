# Changelog

Notable changes to Grain. Starts here — earlier history is in `git log`.

## Unreleased

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
