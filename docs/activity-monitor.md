# Activity monitor

A mode that watches what you do on this Mac, summarizes it every few minutes,
and writes the result to a markdown file that gets fed back into your chats — so
the assistant knows what you were actually working on without being told.

It is off until you turn it on, and every signal is a separate switch - or one switch, if you want
it recording everything: see [Palantir mode](#palantir-mode).

---

## What it can record

Six independent signals. The Activity panel states, in plain words, what each one
captures before you can enable it.

| Signal | What lands in the database | Needs |
| --- | --- | --- |
| **Apps and windows** | Frontmost app + focused window title, as stretches of attention with durations | pyobjc for titles; app names work without it. Screen Recording covers apps that hide their title from the accessibility API |
| **Browser URLs** | Active tab URL in Safari, Chrome, Arc, Brave, Edge, Vivaldi | Automation permission, one grant per browser |
| **Typing and clicks** | Counts and rhythm: keystrokes, clicks, scrolls, words per minute. No characters | Accessibility; Input Monitoring where it is explicitly denied |
| **The text you type** | Every character typed, redacted. This is a keylogger | Accessibility; Input Monitoring where it is explicitly denied |
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

macOS gates each signal behind a different switch, and every grant lands on the **app bundle** that
spawned the backend, because the Python process is a child of Electron: **Personal OS** in a
packaged build, **Electron** in development.

| Permission | What it unlocks | Can it be requested? |
| --- | --- | --- |
| **Accessibility** | Window titles, and the keystroke/click tap | Yes |
| **Input Monitoring** | The tap, on machines where this one is explicitly denied | Yes |
| **Screen Recording** | Window titles for apps that leave `AXTitle` empty. Titles only - no screenshot is ever taken | Yes, once per app ever |
| **Automation** | The active tab URL, one grant per browser | Yes, per browser, while it is running |
| **Microphone** | The microphone signal | Yes |
| **Full Disk Access** | Nothing here needs it. Listed because it is what "full access" means on macOS | No - no program can ask |

The Activity panel's **Access on this machine** checklist probes all six and prints each one's state
(`granted`, `denied`, `not asked yet`), which signals it gates, and what to do about it. Probing is
read-only: opening the panel can never make a dialog appear.

Each row has two buttons. **Grant** asks macOS directly - `AXIsProcessTrustedWithOptions`,
`IOHIDRequestAccess`, `CGRequestScreenCaptureAccess`, `AVCaptureDevice.requestAccess`, or for
Automation the very AppleScript the collector runs. **Open System Settings** deep-links the exact
pane, which is the fallback that matters: macOS shows most of these at most once per app, so a
second ask is silent and the pane is the only way back. **Ask for everything missing** walks the
requestable rows in one go.

After granting, **restart the app**: a `CGEventTap` created before the grant stays dead, and the
rows that behave this way say so.

```bash
./scripts/activity-setup.sh
```

Installs the pyobjc bridge and ffmpeg, offers the loopback driver, then prints the checklist and
what is left for you to grant. The loopback driver is a `.pkg`, so that step asks for your password
and cannot run unattended:

```bash
cd backend && uv pip install -e '.[activity]'   # window titles, the keystroke tap, mic status
brew install ffmpeg                             # both audio signals
brew install --cask blackhole-2ch               # system audio only; asks for your password
```

## Palantir mode

One switch, on the Overview tab, for *record everything*:

- all six signals on, including the keylogger, the microphone and system audio
- redaction off
- both "never record" lists emptied, so password managers and sign-in pages are recorded like any
  other window

It is the only control in the app that turns protections off rather than on, so it sits behind a
confirmation that says exactly that, the panel wears a **Palantir mode** pill while it is on, and
the Signals and Privacy tabs say which of their switches the mode is currently sitting on.

**What it cannot turn off:** secure input. While macOS reports a focused password field it withholds
keystrokes from every tap in the system, so those keys were never ours to record. The count of
dropped keys is still reported rather than hidden.

**Turning it off restores what you had.** The signals, the redaction flag and both exclusion lists
are snapshotted on the way in (`palantirRestore`) and put back on the way out, so a carefully built
exclusion list survives a stint in the mode - and a second enable does not overwrite that snapshot
with the mode's own flattened values.

It needs the grants like anything else: with it on, the panel names any permission macOS is still
withholding, instead of quietly recording less than it claims.

## Tools

The assistant gets two tools when the monitor exists:

- `activity_recent(hours)` — the live line, the profile, and summarized periods.
  Answers "what was I doing this morning?" and grounds advice in your real
  workflow.
- `activity_pause(minutes)` — stops recording. Ask the assistant to stop watching
  and it can.
- `activity_access()` — which permissions exist, which signals each one gates, and what is missing,
  so "why isn't it recording my typing?" gets a real answer. Read-only: it cannot grant anything.

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
| `GET /activity/permissions` | The six macOS permission rows on their own. Never prompts |
| `POST /activity/permissions/request` | Ask macOS for one - the only route that can show a dialog |
| `POST /activity/permissions/open` | Open that permission's Privacy & Security pane |
| `POST /activity/palantir` | Record everything, or restore what the mode replaced |

## Limits

- **macOS only.** The collectors need Quartz and the accessibility API. The rest
  of the app is unaffected on other platforms; `/activity/start` returns 400.
- **Window titles need pyobjc and Accessibility.** Without them app tracking
  falls back to `lsappinfo`, which gives the app name and nothing else. With Screen Recording
  granted too, a title the accessibility API leaves empty is read from the window server instead -
  the name field only, never an image.
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
lifecycle, the markdown writer, the permission probes (which must never raise and never prompt)
and Palantir mode's snapshot-and-restore. The collectors themselves need a real session
with granted permissions, so they are exercised by hand rather than in tests.

```bash
cd backend && .venv/bin/python tests/test_activity.py
```
