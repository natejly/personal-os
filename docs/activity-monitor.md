# Activity monitor

A mode that watches what you do on this Mac, summarizes it every few minutes,
and writes the result to a markdown file that gets fed back into your chats — so
the assistant knows what you were actually working on without being told.

It is off until you turn it on, and every signal is a separate switch.

---

## What it can record

Six independent signals. The Activity panel states, in plain words, what each one
captures before you can enable it.

| Signal | What lands in the database | Needs |
| --- | --- | --- |
| **Apps and windows** | Frontmost app + focused window title, as stretches of attention with durations | pyobjc for titles; app names work without it |
| **Browser URLs** | Active tab URL in Safari, Chrome, Arc, Brave, Edge, Vivaldi | Automation permission per browser |
| **Typing and clicks** | Counts and rhythm: keystrokes, clicks, scrolls, words per minute. No characters | Accessibility |
| **The text you type** | Every character typed, redacted. This is a keylogger | Accessibility |
| **Microphone** | Short recordings, transcribed, audio deleted. Text only | ffmpeg + Microphone permission |
| **System audio** | Same, for whatever your speakers played | ffmpeg + a loopback device |

Defaults when you first switch the monitor on: **apps** and **typing counts**.
Everything else stays off until you choose it.

## How it works

```
collectors ──▶ the gate ──▶ activity_events ──▶ rollup ──▶ activity_summaries
  threads      exclusions,     (short TTL)       (LLM)      │
  polling      secure input,                                ├─▶ context/activity.md
  the OS       redaction                                    └─▶ each chat's system prompt
```

**Collectors** are daemon threads in the Python backend.
`FocusCollector` samples the frontmost window every `sampleSeconds` and writes
one row per stretch of attention, so a rollup can say "35 minutes in Xcode"
instead of listing 400 samples. `InputCollector` installs a listen-only
`CGEventTap`. `AudioCollector` shells out to ffmpeg, transcribes the chunk, and
unlinks the wav.

**The gate** (`activity.Gate`) runs before every single write:

- **Excluded apps** — while one is in front, nothing is recorded. The timeline
  shows `(private)` for that stretch, so the time accounting stays honest without
  storing anything about the window. Password managers ship in the default list.
- **Excluded titles and URLs** — case-insensitive substring match. Defaults cover
  sign-in pages, 2FA, banking, private browsing, and anything mentioning keys or
  seed phrases.
- **Secure input** — macOS sets a system-wide flag whenever a password field is
  focused. Keystroke capture stops entirely while it is set, and the count of
  skipped keys is reported rather than hidden.
- **Redaction** — emails, phone numbers, card numbers, SSNs, API keys, AWS keys,
  JWTs, private key blocks and long high-entropy strings become placeholders. On
  top of that, a word that announces a secret (`password`, `passphrase`,
  `api key`, `cvv`, `seed phrase`…) takes whatever follows it with it, since that
  is where the value lives. The replacement is scoped to the value rather than the
  whole line on purpose: typed text arrives as one long single-line buffer, so
  dropping the line would throw away the entire buffer over one word.

**Rollup** runs on a background asyncio task. Every `rollupMinutes` it folds the
pending events into a compact digest, asks the LLM for a headline, a few
sentences and any durable patterns, and stores the result. A dead proxy does not
lose the observations: the summary falls back to the raw digest and the events
are still consumed, so it never retries forever.

**The profile** (`activity_profile`) is rewritten every `profileEveryHours` from
the last fortnight of summaries: at most twelve bullets on which tools you live
in, when you do deep work, and how you context-switch.

## The context file

`<data_dir>/context/activity.md` is the real output. Plain markdown — read it,
back it up, delete it.

```markdown
# Activity context

_Written by the Personal OS activity monitor. Stays on this machine._
_Updated 2026-09-29 14:07 · signals on: apps, input · raw samples kept 48h, summaries 90d._

## Right now
In Cursor - activity.py for 12m.

## How this person works
### Tools
- Lives in Cursor; Safari is reference, not browsing

## Today - 2026-09-29
### 13:45-14:00 · Debugging the embedding config
They worked through the LiteLLM embedding config in Cursor, checking pyobjc docs...

_Apps: Cursor, Safari, Terminal_
```

Chats receive a trimmed version — the live line, the profile, and the last day of
period headlines — capped at roughly a thousand tokens. It arrives as a context
block like memory and documents do, and shows up in the Context drawer's "last
reply" inspector so you can always see exactly what was injected.

Three independent ways to stop it reaching a chat:

1. **Feed summaries into chats** off in Privacy — keeps recording, injects nothing.
2. The **Activity** toggle in a chat's Context drawer — per chat.
3. Turn the monitor off — records nothing at all.

## Retention

Raw samples carry an `expires_at` and are swept on every loop tick; the default
is 48 hours. Summaries default to 90 days, and `activity.md` keeps 3 days of
detail. All three are configurable, and the Privacy tab has one-click deletes for
raw samples, summaries, or everything including the profile.

The raw log is browsable in the panel with per-row delete, because the only way
this feature is reasonable to run is if you can see exactly what it knows.

## Permissions

macOS grants input and screen access per binary, and the backend is a child of
the Electron app, so the grant lands on the app bundle — **Personal OS** in a
packaged build, **Electron** in development. After granting, restart the app: a
`CGEventTap` created before the grant stays dead.

The Activity panel's capability checklist probes what is actually available and
prints the exact fix for anything missing. Probing never triggers a permission
prompt.

```bash
# Window titles and the keystroke tap
cd backend && uv pip install -e '.[activity]'

# Both audio signals
brew install ffmpeg

# System audio only: macOS will not record its own output without a loopback device
brew install blackhole-2ch
```

Then: System Settings → Privacy & Security → Accessibility, and enable the app.

## Tools

The assistant gets two tools when the monitor exists:

- `activity_recent(hours)` — the live line, the profile, and summarized periods.
  Answers "what was I doing this morning?" and grounds advice in your real
  workflow.
- `activity_pause(minutes)` — stops recording. Ask the assistant to stop watching
  and it can.

## API

| Route | Purpose |
| --- | --- |
| `GET /activity/status` | Running state, config, capability checklist, counts, live line |
| `PUT /activity/config` | Deep-merged config patch; restarts collectors if signals changed |
| `POST /activity/start` · `/stop` | Turn the collectors on and off |
| `POST /activity/pause` · `/resume` | Stop recording without tearing collectors down |
| `GET /activity/events` | The raw log, newest first |
| `DELETE /activity/events/{id}` | Redact one row |
| `GET /activity/summaries` | Rolled-up periods |
| `DELETE /activity/summaries/{id}` | Drop one period and rewrite activity.md |
| `POST /activity/rollup` | Summarize what is pending now |
| `POST /activity/profile` | Rebuild the durable profile |
| `GET /activity/context` | activity.md plus the block chats receive |
| `GET /activity/devices` | avfoundation audio inputs |
| `POST /activity/purge` | `expired` \| `events` \| `summaries` \| `all` |

## Limits

- **macOS only.** The collectors need Quartz and the accessibility API. The rest
  of the app is unaffected on other platforms; `/activity/start` returns 400.
- **Window titles need pyobjc and Accessibility.** Without them app tracking
  falls back to `lsappinfo`, which gives the app name and nothing else.
- **System audio needs a loopback driver.** There is no native way to capture
  macOS output.
- **Transcription is not speaker-aware.** With the microphone on, people around
  you get transcribed too.
- **Typed text arrives out of order.** The buffer is global, not per-field, so
  fragments interleave. The rollup prompt treats it as a hint about the subject,
  not as content to quote.

## Tests

`backend/tests/test_activity.py` covers the gate, config merging, retention and
purge, the digest, rollup (including a dead LLM), the context block, the
lifecycle and the markdown writer. The collectors themselves need a real session
with granted permissions, so they are exercised by hand rather than in tests.

```bash
cd backend && .venv/bin/python tests/test_activity.py
```
