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
| **Microphone** | Short recordings, transcribed, audio deleted. Text only | AVAudioEngine + Microphone permission |
| **System audio** | Same, for whatever your speakers played | Core Audio process tap (macOS 14.2+), or a loopback device |

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
`CGEventTap`. `AudioCollector` records a chunk (native capture, ffmpeg fallback),
transcribes it, and unlinks the wav.

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
- **Redaction v2** (`redact.analyze` / `scrub_v2`) — each regex hit becomes a scored
  span. Cards must pass Luhn, phones and SSNs must be plausible, long runs need
  Shannon entropy >= 3.5 and a hex hash after "commit"/"sha" is left alone. A failed
  validator is still kept (score 0.5) when a context word ("card", "ssn"...) sits
  within 40 characters before it; spans under `redactThreshold` are dropped.
  `redactAllow` / `redactDeny` take strings or `/regex/`. Page URLs go through
  `sanitize_url`: no userinfo or fragment, sensitive query values become `~`.
  Per-entity counts show in `/activity/status` (`redactions`, never the text) and
  `POST /activity/redact/test` previews a string without storing it. Palantir mode
  turns all of this off with `redact`.
- **Redaction (v1 rules)** — emails, phone numbers, card numbers, SSNs, API keys, AWS keys,
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

## Insights: habits and automations

Recording and summarizing answers *what happened*. Insights answers the two
questions after it: what does this person do over and over, and what could the
app do for them instead. It lives on its own tab, and it is built in four layers.

**Day aggregates.** Raw samples die after 48 hours, so nothing could ever notice
"every weekday morning" from them. On every mining pass the day's events are
folded into one row per day in `activity_day_stats` — seconds per app, visits per
host, keystrokes per app, hour-of-day histogram, switch count, first and last
activity. Counts only: no titles, no URLs, no text. The merge takes the **max** of
what is stored and what the current events add up to, so re-mining a day is
idempotent and a retention sweep can never shrink history. These rows age out with
the summaries, on the 90-day clock.

**Mining.** Deterministic, local, no model, no network. It reads the day
aggregates plus whatever raw events are still held and emits patterns, each with a
support count, the number of days it recurred on and a confidence that is mostly
recurrence and only a little volume — a pattern on five of seven days outranks one
with a thousand samples on a single Tuesday. What it looks for:

| Pattern | What it means |
| --- | --- |
| `app_routine` | An app that holds real time on several days, and the hours it clusters in |
| `site_habit` | A host opened three or more times a day across days |
| `thrash` | Round trips between two apps with a short dwell — waiting, or copying by hand |
| `deep_work` | Stretches of 15 minutes or more without switching, and when they start |
| `day_shape` | Median first and last activity, and how much of it is actually at the keyboard |
| `after_hours` | Share of screen time outside 08:00–19:00 |
| `input_load` | Where the typing happens, as opposed to where the time goes |
| `recurring_window` | The same window title returning on separate days |
| `topic` | Topics the period summaries keep naming — these outlive the raw samples |
| `switch_rate` | Switches per focused hour, when it is high enough to be the story |

Patterns are what the panel shows first, with bars for time-by-app and
sites-by-visits and a 24-hour strip. They are the evidence; everything below rests
on them.

**The pass.** One LLM call, on a slower clock than the rollup (every 12h by
default). It gets the mined patterns, the durable profile, and a list of what this
app can actually do — including the tool names registered on *this* install, so an
unconnected integration cannot turn into a suggestion that cannot be carried out.
It returns habits and suggestions, each citing the pattern ids it rests on. If the
model is unreachable or answers with prose instead of JSON, a deterministic
fallback maps pattern kinds to obvious suggestions, so the feature degrades to
"obvious" rather than to nothing.

**Habits** are durable third-person statements — "User does their longest
uninterrupted work between 09:00 and 11:00". Each habit owns at most **one** row in
the ordinary Memory panel, written with `source: activity`. A later pass rewrites
that row rather than adding a near-duplicate beside it, and a habit that claims to
supersede an older one retires that habit *and* deletes its memory — which is the
only way a stale "works late every night" ever leaves the memory panel on its own.
Forgetting a habit in the panel deletes its memory too. A memory the module did not
write is never touched, whatever happens.

Habits go into memory and into `activity.md` under `## Habits noticed`, so chats
know them. **Suggestions deliberately do not** — a proposal is not context.

**Suggestions** are proposals with a status, and they never apply themselves:

- `prompt` (the common case) acts on nothing. Pressing the button opens a fresh
  chat pre-loaded with the message, because setting the thing up is a conversation
  with the usual tool approvals in it, not a side effect of a click in a panel.
- `todo` creates one todo. `memory` writes one memory. `setting` just explains what
  to click.
- **Not now** hides it for a week, then it comes back on its own.
- **Dismiss is permanent.** A later pass may re-score or re-word a dismissed
  suggestion, but it can never move it back to new, and dismissed keys are listed in
  the next prompt as ground not to cover again, so it cannot come back wearing a new
  title either.

Three kinds: `automation` (the app takes something over), `hygiene` (a change to how
the day is shaped — breaks, after-hours, fragmentation), and `platform` (friction
visible in the data that *Grain itself* should remove — kept as a note, not acted on).

Switches, on the same tab: the schedule (`everyHours`, 0 to only look when asked),
whether confident habits are written to memory at all, how much history to mine, the
recurrence floor, and how many suggestions to hold at once. Turning
`autoMemory` off deletes the habit memories already written, on the next pass.

## Categories

`activity_categories.py` is a nested rule tree: each rule has a name path
(`["Work", "Coding"]`), an optional regex over app and/or title, optional `hosts` (suffix
match against the page's host) and a productivity `score` from -2 to 2 that children inherit.
The deepest matching rule wins (ties go to list order), `type: "none"` rules are folders that
only group, and an invalid regex is skipped rather than raised. Rules live in the activity
config under `categories` (`null` = the shipped default tree).

Classification is local and model-free. `activity_day_stats` gains a `cats` map (path string
and every ancestor prefix, to seconds, max-merged like the other maps), so a changed rule
affects future time while counted days keep their totals. Only paths and seconds are stored,
never titles or URLs. `insights.mine()` adds `category_share` and `distraction_drift`
patterns, and the rollup digest gains a "Time by category" line.

## Retention

Raw samples carry an `expires_at` and are swept on every loop tick; the default
is 48 hours. Summaries default to 90 days, and `activity.md` keeps 3 days of
detail. Day aggregates ride the summary clock. All of it is configurable, and the
Privacy tab has one-click deletes for raw samples, summaries, or everything —
where "everything" reaches the derived rows too: habits, their memories, the
suggestions and the day aggregates. A purge that left habits behind would be a
purge that lied.

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

Installs the pyobjc bridge (including Speech), then prints the checklist and
what is left for you to grant. ffmpeg and BlackHole are optional fallbacks:

```bash
cd backend && uv pip install -e '.[activity]'   # window titles, the keystroke tap, native audio, Speech
# optional:
brew install ffmpeg                             # truncated-wav repair and the ffmpeg capture path
brew install --cask blackhole-2ch               # system audio on macOS older than 14.2
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
- `activity_report(days)` — time by category, productivity score and uncategorized apps.
  Read-only, computed locally.
- `activity_insights(limit)` — the habits noticed, the patterns behind them, and the suggestions
  still on offer. For "how could I save time?", and for grounding any workflow advice in real
  patterns instead of guesses. Read-only on purpose: the assistant may raise a suggestion in
  conversation, but it cannot accept one on the user's behalf.

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
| `GET /activity/categories` · `PUT /activity/categories` | Effective rules (and whether default); replace them (`rules: null` resets). 400 names the bad index |
| `GET /activity/categories/report?days=7` | Category seconds per day, totals, productivity (-2..2) and top uncategorized apps |
| `POST /activity/redact/test` | Run a string through the current redaction config; nothing is stored |
| `GET /activity/insights` | Patterns, habits, suggestions and counts |
| `POST /activity/insights/mine` | Re-mine the patterns. No model call, works offline |
| `POST /activity/insights/refresh` | Mine, then run the pass that proposes habits and automations |
| `POST /activity/insights/{id}/status` | `new` \| `accepted` \| `done` \| `dismissed` \| `snoozed` |
| `POST /activity/insights/{id}/apply` | Do the one thing that suggestion's action says |
| `DELETE /activity/insights/{id}` | Drop a suggestion row outright |
| `DELETE /activity/habits/{id}` | Forget a habit and the memory it wrote |

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

`backend/tests/test_insights.py` covers the insights half: host-only URL handling,
the day-aggregate fold and its monotonic merge, every pattern detector, the rule
that recurrence beats volume, habit upsert and supersede (including the memory row
each habit owns), that a dismissed suggestion never comes back, snooze waking up on
its own, what `apply` does for each action type, the deterministic fallback when the
proxy is dead or the reply is garbled, and that a full purge leaves nothing derived
behind while never touching a memory the user wrote themselves.

```bash
cd backend && .venv/bin/python tests/test_insights.py
```
