# Grain

<img src="build/grain.svg" alt="Grain logo" width="72" align="right" />

Grain is a personal AI operating system for your desktop. Chat with any model through
[LiteLLM](https://docs.litellm.ai/) (Fireworks AI out of the box), give the
assistant tools, and let it build memory and a knowledge graph about you as you
go. Organise work into **projects**: groups of chats with
their own instructions, knowledge files, memories and graph.

```
┌──────────────┬──────────────────────────────────────┬──────────────┐
│ + New chat   │  Today · Monday, September 29        │  Context     │
│ Today        │  ┌ Calendar ─────┐ ┌ Todos ────────┐ │  ☑ Memory    │
│ Boards       │  │ 10:00 Standup │ │ ○ Ship v0.1   │ │  ☑ Graph     │
│ Dashboards   │  │ 14:00 1:1     │ │ ○ USB-C hub   │ │  ☑ Documents │
│ Files        │  └───────────────┘ └───────────────┘ │  ☑ Auto-learn│
│ PROJECTS   + │  ┌ Unread mail ──┐ ┌ Projects ─────┐ │  ☑ Tools  ▾  │
│ ■ Grain      │  │ Alice: Q4 …   │ │ ■ Grain       │ │   web search │
│ RECENTS      │  └───────────────┘ └───────────────┘ │   run python │
│ · …          │                                      │   gmail send │
│ · …          │  [Brief me]                          │  Last reply… │
└──────────────┴──────────────────────────────────────┴──────────────┘
```

## Features

- **Provider-agnostic chat.** Streaming replies from any model LiteLLM routes to,
  one key. Per-chat model picker. Markdown, code copy, regenerate, stop.
- **Tools with permissions.** The assistant can search your documents, read and
  revise your docs, search
  and save memory, traverse and extend the knowledge graph, read your writing
  style before drafting as you, search the web and
  read pages, run Python in a sandbox, manage todos and kanban boards, and (once
  connected) read your Google Calendar, triage Gmail, draft or send email, and
  manage Google Tasks. Each tool has a mode: **on** (runs automatically),
  **ask** (pauses the reply with an inline approve/deny card) or **off**.
  In-app tools default to on; anything that acts outside the app (email,
  calendar events, Google Tasks) defaults to ask. Override globally, per
  project, or per chat. Tool calls render inline with arguments, results and
  timing, and every reply carries an execution trace.
- **Projects.** Groups of chats with instructions, knowledge files, project
  memories and a project graph, layered on top of your personal ones.
- **Memory.** One panel holding what the app remembers about you, over a shared
  scope filter and search box — the first two halves side by side, either alone,
  or the voice profile on its own:
  - *Memories* — facts, preferences and goals, auto-extracted after each reply
    or added by hand or by the assistant. Edit, pin, move between personal and
    project scope, forget, see a memory's past versions, and export or import
    a scope as a JSON file. It lives in Settings → Memory & learning, next to
    the auto-learn switches.
  - *Knowledge graph* — entities and relations, auto-extracted and
    hand-editable in a force-directed view. Relevant subgraphs are injected
    into chats.
  - *Voice* — how you write, learned from your own writing: long messages you
    send and docs you save are banked as samples (short instructions, code and
    quoted text are skipped), and turned into a summary, guidelines, traits and
    characteristic phrasings. Injected when the assistant drafts something you
    will send as your own — email, messages, docs — and explicitly *not* used
    for its replies to you. Every guideline is editable and every sample
    deletable; editing one stops auto-relearn overwriting it. Projects can have
    their own voice. See [docs/writing-style.md](docs/writing-style.md).
- **Documents.** Upload any file up to 20 MB. Text, PDF, and Word are read; other files are kept by name. Chunked,
  full-text indexed, best excerpts pulled into replies.
- **Files.** Docs of your own (the sidebar's **Files** view), in an editor rather than an upload box:
  markdown and LaTeX, a line-numbered editor beside a live preview, and full
  revision history. The assistant can read and revise a doc — but its edits are
  *proposed*, never written straight in. Each one arrives as a diff you accept
  or reject, so you can point a model at prose you care about. Notes features:
  a `/` menu, `[[wikilinks]]` and backlinks, an outline, templates and a daily
  note. On macOS any doc can be recorded or dictated into, with the transcript
  kept apart from the text and a summary proposed for you to accept. See
  [docs/docs-editor.md](docs/docs-editor.md).
- **Ask about this page** (⌘I). A side panel that sees the view you are on (a
  chat, a doc, a project, a Space) and answers or acts on it.
- **Health.** A page in the title-bar app strip that tracks a few daily metrics,
  with a Today card and `health_*` tools; it can sync from a connected fitness
  account. See [docs/health.md](docs/health.md).
- **Activity monitor** (macOS, opt-in, off by default; the view starts hidden,
  turn it on in Settings → Modules). Watches what you actually
  do — frontmost app and window, browser URLs, typing and click rhythm, the text
  you type, microphone and system audio — summarizes it every few minutes, and
  writes the result to `context/activity.md`, which is fed back into chats so the
  assistant knows what you were working on. Every signal is a separate switch;
  password managers and sign-in windows are never recorded; macOS secure input
  stops keystroke capture dead; credentials and PII are redacted before anything
  is stored; raw samples expire after 48h. The raw log is browsable row by row
  and deletable. See [docs/activity-monitor.md](docs/activity-monitor.md).
- **Habits and automation suggestions.** On top of that data, a local miner keeps
  one counts-only row per day — which outlives the 48h sample retention — and
  detects what recurs: the apps that own your mornings, the site you open eleven
  times a day, the two apps you ping-pong between, where your long uninterrupted
  stretches actually land, how much of the day lands after seven. Those patterns
  are the panel's evidence, computed with no model and no network. A slower pass
  then turns them into **habits**, each owning one row in your Memory panel so
  chats already know how you work, and **suggestions** for what the app could do
  instead — a digest widget to replace the tab reflex, a project for the topic that
  keeps coming back, a calendar block around your real focus window. Suggestions
  are proposals: the common action opens a chat pre-loaded with the request rather
  than acting, "not now" hides one for a week, and dismissing one is permanent.
- **Meetings** (macOS, opt-in, off by default; the view starts hidden, turn it
  on in Settings → Modules). A notepad that listens: type
  during a call while the recorder captures it natively (AVAudioEngine and, on
  macOS 14.2+, a Core Audio tap for the far side of the call), segments
  transcribe in the background, and afterwards the enhance pass proposes your
  outline with the transcript filled in around it — as a diff you accept or
  reject. Your typed notes live in their own column and no model ever writes
  them. Calendar events happening now offer a Record button; action items become
  todos on a click. Nothing is recorded until you acknowledge a modal naming the
  exact directory the audio lands in. Transcription prefers on-device Speech,
  then whisper.cpp, then your LLM proxy. Meetings never expire, are unreachable
  from the activity monitor's purge, and never reach auto-learn. See
  [docs/meetings.md](docs/meetings.md).
- **Cowork desks** (the view starts hidden; turn it on in Settings → Modules).
  A desk is a task you hand over: its own conversation, its own
  folder, and one plan you approve before it acts. Several run at once. Long
  autonomy is bought by chaining bounded replies, never by a longer leash — each
  turn is an ordinary reply with an ordinary budget, and the desk chains another
  only while the approved plan still has steps left and the last turn actually
  consumed one. Three modes: plan first (nothing consequential runs until you
  approve a plan, and those tools are withheld rather than offered and refused),
  ask as it goes (one card per change), and propose only (it may plan an external
  action and never perform one). Nothing it writes reaches the app until you accept
  it: it works in `cowork/<desk>/` and nominates files for review, and every
  promotion is read back before it counts. A card nobody is watching parks after a
  few minutes — the run lets go, the card stays pending and decidable, and answering
  it wakes the desk. See [docs/cowork-design.md](docs/cowork-design.md).
- **Library.** One place for what the assistant may follow and reach: **Skills**,
  the procedures it can be asked to repeat; **Connectors**, the MCP servers whose
  tools join the toolbox; and **Made**, every doc, dashboard and board built here.
  A skill is the one place prose a model wrote could land inside a later system
  prompt, so authoring is lint-gated: warnings are quality, but any sentence that
  claims authority over the assistant's permissions is an error that blocks
  approval, and the same check runs on what `skill_draft` writes and on what you
  approve by hand. `skill_draft` and `skill_revise` can only ever produce a
  candidate — there is no tool that approves one, and a revision of an approved
  procedure is forked beside it rather than overwriting the text in use. The
  preview shows the real injected block, assembled by the function the chat uses.
- **Context management.** Per-chat toggles for memory, graph, documents, activity,
  meetings, auto-learn and tools; an inspector showing exactly what was injected into
  each reply; a live preview for a draft message.
- **Charts and diagrams.** Replies can include a ```` ```chart ```` block (a small
  JSON spec rendered as a bar / line / area / pie / scatter chart, each with
  chart, data-table and source views) or a ```` ```mermaid ```` block.
  The Python sandbox has numpy and matplotlib, and any figure a script saves is
  shown inline on the tool card.
- **Interactive charts.** An ```` ```interactive ```` block adds sliders, number
  fields, dropdowns and toggles, and plots formulas over them — so you can drag
  an assumption and watch the curve move. It recomputes locally, with no new
  request to the model.
- **Traces.** Every reply records what it did: context assembly, each model
  round with time-to-first-token and token counts, each tool call, and the
  auto-learn pass. Spans stream live into a waterfall in the Context panel.
- **Usage and cost.** Each model call is logged locally with tokens, latency and
  cost. Settings shows spend, tokens, calls and frequency charts over 7/30/90
  days, broken down by model, kind and project. Prices come from your LiteLLM
  proxy and can be overridden per model.
- **Today, todos, calendar, boards.** A Today screen with a generated daily
  recap, calendar, unread inbox, todos, projects and recently learned memories,
  plus a one-click brief. A native todo list, a week calendar (Google events
  plus due todos, double-click to add), and kanban boards with drag and drop.
  The assistant can drive all of them through tools.
- **Scheduled tasks and the agent inbox.** Give the assistant work to do later:
  once at a time you pick ("tomorrow at 3pm, check whether they replied") or
  repeatedly on a cron expression ("every Friday at 17:00, write my weekly
  review"). Schedule it from the Agent inbox on Today, or just ask in a chat —
  the assistant has a `schedule_task` tool, which asks before it books anything.
  A scheduled run happens with nobody watching, so it is deliberately boxed in:
  it runs in a fresh chat on a tighter budget, it can read and write inside
  Grain, and anything that would leave the app — mail, calendar events, Docs —
  comes back to the Agent inbox as a **proposal** you accept, edit or reject.
  Accepting is what actually sends it, exactly once. A run cannot schedule
  further runs either; that proposal is yours to accept too. If the machine was
  asleep over a slot the task still runs, once, and is told it is late so it says
  so in its report. A one-off retires itself after it fires.
- **Dashboards you describe.** Register data sources (an HTTP API with an API
  key, an RSS feed, or your own todos/calendar/mail), then describe a widget in
  plain English. The model writes a self-contained HTML widget that runs in a
  sandboxed iframe and fetches data through the backend (keys never reach the
  widget). "AI summary" widgets turn any source into a short briefing. Revise a
  widget by telling it what to change.
- **Google Workspace.** Sign in once with your own OAuth client; calendar,
  Gmail and Tasks become dashboard widgets and assistant tools.
- **Sign in with ChatGPT.** *(Planned — not shipped yet.)* Connect an OpenAI
  account the way Google connects today: an OAuth flow that opens in the browser
  and returns to the app on a loopback URL, the token stored in the local
  database, and model calls signed with it so usage draws on the signed-in
  user's own OpenAI credits instead of the app's shared key. That account's
  models join the picker, usage rows are attributed to it, and the LiteLLM key
  in Settings stays the fallback for anyone who does not connect one. One
  constraint to design around: OpenAI's ChatGPT sign-in bills API usage to the
  connected OpenAI account — a Plus or Pro subscription is not itself a pool of
  API credits — so the flow has to name the account being charged before the
  first call.
- **Private inference.** *(Planned — not shipped yet.)* Chat can stay on the
  cloud model in the picker. Jobs that read the sensitive store go to a local
  model you run on this machine (Ollama, llama.cpp, or MLX, wired through
  LiteLLM's `ollama/` route): activity summaries, meeting enhance, auto-learn
  over mail and keystrokes, voice extraction from your docs. If that model is
  down, those jobs skip or fail closed instead of forwarding the payload to
  Fireworks. That is the switch an air-gapped deployment would use:
  private data stays on the box; only ordinary chat hits a remote API.
  `extractionModel` today is just a cheaper LiteLLM name, not an on-device
  guarantee. Audio already has a local path (whisper.cpp); this is the same
  idea for text.
- **Cloud worker.** *(Planned — not shipped yet.)* A scheduled task only fires
  while this Mac is awake, and a missed slot is caught up once, late. The cloud
  piece is a second process on a machine that stays up — a VPS you control, to
  start — and it owns only the jobs that are useless with the lid closed:
  scheduled tasks, the morning brief, and watches whose result is a proposal in
  the Agent inbox. Chat stays in the desktop app. Activity, meetings, the Python
  sandbox, cowork folders and the macOS tools stay here; they need this machine,
  and raw keystrokes and call audio are never uploaded. A task has one home,
  local or cloud, so a late catch-up and a cloud run cannot both fire. The
  worker keeps its own database for the stores those jobs read, synced per
  domain the way [docs/sources-of-truth.md](docs/sources-of-truth.md)
  proposes, rather than a network copy of the desktop SQLite file. Signing the
  desktop into the worker replaces the loopback sidecar token, and Google
  sign-in on the worker is a web OAuth client with a fixed redirect, not the
  Desktop client the app ships now. Its tool box is the scheduled-run box, drawn
  tighter: read Grain, propose anything that would leave the app, and no view of
  your filesystem, the activity log, or meeting audio. Private inference still
  wins for a job that reads the sensitive store — with that switch on, the
  worker does not receive it. A browser that replaces the desktop app, and a
  multi-tenant host, are a later product.
- **RLHF on company data.** *(Planned — enterprise, later.)* Once Grain is
  running on an organisation's own mail, docs, tickets and accepted/rejected
  drafts, those preference signals (approve vs deny on tool cards, accept vs
  reject on doc diffs, edited vs sent mail) become labelled pairs. A later
  enterprise build trains or DPO-adapts a tenant-local policy on that data so
  the assistant writes and acts in the company's voice, against the company's
  rules, without the traces leaving the tenant. Personal Grain stays
  single-user and does not train on your machine.

## Architecture

```
Electron (TypeScript)               Python (FastAPI)                      LiteLLM proxy
┌──────────────────────┐  HTTP/SSE  ┌───────────────────────────────┐  OpenAI API  ┌──────────────┐
│ renderer: React      │ ─────────▶ │ personal_os.app  (routes)     │ ───────────▶ │ Fireworks AI │
│ main: sidecar spawn  │            │  ├ repos     SQLite + FTS5    │              │ (or any)     │
└──────────────────────┘            │  ├ context   prompt assembly  │              └──────────────┘
                                    │  ├ tools     agent tool loop  │
                                    │  ├ learn     memory/graph ext.│
                                    │  ├ google    OAuth + APIs     │
                                    │  ├ todos     native tasks     │
                                    │  └ sandbox   isolated python  │
                                    └───────────────────────────────┘
```

Electron spawns the backend on a free port with a data directory under the
app's user-data folder. State is one SQLite file plus folders beside it:
`uploads/` and `doc_assets/` (your files and pasted images), `recordings/` (meeting
audio you keep), `cowork/` (desk workspaces), `context/` (`activity.md`),
`backups/` and `logs/`. API keys and Google tokens are kept in the macOS
Keychain, or a 0600 file in the data folder when the Keychain is unavailable.
In development, `scripts/dev.sh` runs LiteLLM, the backend (autoreload) and
Electron (HMR) together.

## Install

The packaged app (`npm run package`, see [docs/releasing.md](docs/releasing.md))
is self-contained: no Python, uv or LiteLLM needed. On first launch a setup
wizard asks a few things about you and has you pick a model provider (Fireworks
AI, OpenAI, Anthropic, OpenRouter, a local Ollama, a LiteLLM proxy or any
OpenAI-compatible endpoint). Google is connected from Settings → Integrations.

## Development

Requirements: Node 20+, Python 3.10+, [uv](https://docs.astral.sh/uv/), and a
Fireworks AI key (or any provider LiteLLM supports).

```bash
npm install
cd backend && uv venv && uv pip install -e . && cd ..
cp .env.example .env          # then paste your FIREWORKS_API_KEY
./scripts/dev.sh              # starts LiteLLM + backend + Electron
```

`.env` also seeds the app's first-launch settings (LiteLLM URL, key, default
model `kimi-k3`, extraction model `deepseek-v4-flash`). Change them any time in
Settings (⌘,). `litellm.yaml` lists the Fireworks models exposed to the app;
add any other provider there and it appears in the model picker.

To skip LiteLLM entirely, point Settings at
`https://api.fireworks.ai/inference` with your Fireworks key and model id
`accounts/fireworks/models/kimi-k3`.

### Google Workspace

Users only ever click **Sign in with Google** (Settings → Integrations). Sign-in
opens in the browser and returns to the app on a loopback URL. The OAuth client
the app signs in with is set up once per install:

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   create a project and enable the **Calendar**, **Gmail**, **Tasks**,
   **Drive**, **Docs** and **Sheets** APIs (the Integrations panel links each one).
2. Configure the OAuth consent screen (External; add yourself as a test user).
   Publishing it, or making it Internal on a Workspace account, avoids the
   7-day refresh-token expiry that "Testing" apps have.
3. Create an OAuth client of type **Desktop app** and put its id and secret in
   `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (see `.env.example`).
   Google treats a Desktop-app client secret as non-confidential, which is why
   it can ship with the app. The type matters: the backend listens on a fresh
   loopback port each launch, and only Desktop-app clients may vary the port. A
   "Web application" client rejects the callback with `redirect_uri_mismatch`.

Restart the app after editing `.env` — the client is read when the backend starts.

If `.env` has no client, the Settings panel falls back to asking for one, and a
client pasted there always overrides the one from `.env`.

Scopes: `openid`, `email`, `calendar`, `gmail.modify`, `tasks`,
`drive.readonly`, `drive.file`, `documents`, `spreadsheets`. Tokens and the
client secret are kept in the macOS Keychain (a 0600 file in the data folder if
the Keychain is unavailable). `gmail_send` is a separate tool you can keep off; `gmail_draft` never
sends.

When a token is revoked, expires (a "Testing" consent screen kills refresh
tokens after 7 days) or is missing a permission that was unticked on the consent
screen, Integrations shows why and offers **Reconnect** instead of failing the
next calendar or mail call with an opaque error.

### Verified writes

A 200 from an API is not proof that anything was written, and a model's report
that it wrote something is worth even less: on real tasks, agents claim
completion they did not achieve about 45% of the time, and an independent
read-back of the remote state cuts that to about 3%. So every external write in
`google.py` reads itself back and compares the fields it wrote
(`backend/personal_os/verify.py`):

| Write | How it is proved |
|---|---|
| `calendar_create` / `calendar_update` | the event is fetched by id and the written fields compared (times as instants, since Google re-renders the offset) |
| `calendar_delete` | the event must 404 or come back as a `cancelled` tombstone |
| `calendar_respond` | the event is refetched and your own `responseStatus` compared |
| Gmail send | the message is fetched by id, must carry the `SENT` label, and its thread, subject and recipients are compared |
| `gmail_draft` | the draft is fetched by id and its subject compared |
| `gmail_modify` | the message's labels are refetched: every added label present, every removed one gone |
| Google Tasks insert / patch / delete | the task is fetched by id (title, notes, due, status), or must be gone |
| Docs create / append | the document is refetched and the written text found in its body |
| Sheets create / write | the title, or the written range's row and filled-cell counts, read back |

The verdict is one of **verified**, **unverified** (the read-back could not find
it — eventual consistency, or it never happened) or **mismatch** (it is there
but stored differently, or still there after a delete), and it is never collapsed
into "ok". Eventual consistency gets two quick retries, ~2 s in total (one more
rung for mail, which files into `SENT` a beat later) and then reports
`unverified` rather than waiting.

Anything but `verified` is surfaced as a failure, not a success:

- The tool result the model sees comes back with an `error` that begins
  `UNVERIFIED` and tells it not to claim success — so it cannot say "I sent
  that" on the strength of its own request. It also says not to retry, because
  the write may well have landed.
- The tool-call row in the chat shows a **verified** / **unverified** /
  **mismatch** badge naming what was compared, and an unverified write renders as
  an error. The verdict is stored with the row, so an old reply still shows how
  its writes were proved.
- The calendar and mail views reject an unverified write instead of toasting
  success, and say to check Google.

### Undo on outgoing mail

Agency people will actually use is reversible, so by default nothing sends
mail immediately (Settings → Integrations can turn the hold off, which makes
every send immediate and final). A send — from the compose window or from the assistant — is written
to `pending_sends` and held for 90 s (configurable, 60–120, Settings →
Integrations) while a countdown with an **Undo** button sits above the toasts.
"Send now" is in that card and deliberately not a tool: the assistant can cancel
a send it queued (`gmail_outbox`), but only you can shorten the window.

The queue is in SQLite, so a restart cannot lose a send or fire one twice.
Firing and cancelling race on one atomic `UPDATE … WHERE status='holding'`, so a
send is cancellable right up to the instant it is claimed and never after. On
startup the remaining hold is simply resumed; a send that came due while the
backend was down goes out if that was less than 15 minutes ago, and otherwise is
marked `expired` and **not** sent — a mail queued before a laptop slept for a day
should not go out by itself once the user has had no chance to stop it, and it
stays in the list with a Send now button so it cannot be mistaken for something
that went out. A send interrupted mid-API-call is marked `failed` and never
retried, because an exception can be raised after Gmail accepted the message.
When the hold expires and the mail goes out, the verification above runs on it
and its verdict is kept on the row.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| ⌘N | New chat |
| ⌘0 … ⌘6, ⌘8 | Today / Chats / Todos / Calendar / Boards / Dashboards / Memory / Documents |
| ⌘7 | Memory, opened on the knowledge graph |
| ⌘9 | Activity |
| ⌘⇧M | Meetings |
| ⌘⇧K | Cowork |
| ⌘⇧N / ⌘⇧D | New note / Today's note |
| ⌘⇧F | Search chats |
| ⌘⇧[ / ⌘⇧] | Previous / next chat |
| ⌘⇧C | Toggle Spaces |
| ⌘B | Toggle sidebar |
| ⌘I | Ask about this page |
| ⌃⌘I | Toggle context panel |
| ⌘U | Upload document |
| ⌘, | Settings |
| Enter / Shift+Enter | Send / newline |

## How a reply is built

1. System prompt: your global prompt, then the project's description and
   instructions.
2. **Memories** in scope (personal + project): pinned first, then recent, plus
   full-text matches for the message.
3. **Graph** entities whose labels appear in the message with their 1-hop
   neighbours as `A —[relation]→ B` triples.
4. **Document excerpts**: top BM25 matches over chunks in scope.
5. **Tools**: the effective tool set after global, project and chat overrides.
   The model may call tools for up to `maxToolRounds` rounds; each call and
   result streams to the UI and is stored on the message. A tool in **ask**
   mode pauses the stream until you approve it (once, for the chat, or always)
   or deny it, in which case the model is told to continue without it.
6. After the reply, if auto-learn is on, a second (cheaper) model call extracts
   new memories and graph relations. Facts must come from what you said, and
   the user is never a graph entity. It runs in a background worker, one job at
   a time, *after* the run has ended — the chat is free for your next message
   while it works — and reports what it learned on `GET /events`, the app-wide
   event stream, since the reply's own stream is long closed by then.

Everything used is stored on the assistant message (`context_used`,
`tool_events`) and shown in the Context panel.

## Charts, diagrams and images

Rich output is a fenced code block the UI knows how to render, so it works with
any model and streams naturally. The system prompt describes three block types:

````markdown
```chart
{"type": "bar", "title": "Revenue vs costs", "x": "month", "series": ["revenue", "costs"],
 "unit": "$", "stacked": false,
 "data": [{"month": "Jan", "revenue": 12000, "costs": 8000},
          {"month": "Feb", "revenue": 15000, "costs": 8500}]}
```
````

`type` is `bar | line | area | pie | scatter`. `series` entries may also be
objects (`{"key", "label", "type"}`) to mix bars and lines in one chart.
Chart.js-style `labels`/`datasets` and plain `{"A": 1, "B": 2}` maps are
accepted too, and a malformed block degrades to its source rather than an error.

An ```` ```interactive ```` block is a chart you can steer. It names some
controls and plots formulas over them, and dragging a slider redraws it locally —
no round trip to the model:

````markdown
```interactive
{"title": "Compound growth", "unit": "$",
 "controls": [{"id": "start", "label": "Starting amount", "type": "number", "value": 5000},
              {"id": "rate", "label": "Annual return", "type": "slider",
               "min": 0, "max": 15, "step": 0.25, "value": 7, "unit": "%"}],
 "x": {"id": "year", "label": "Year", "from": 0, "to": 30, "steps": 120},
 "series": [{"key": "balance", "label": "Balance", "expr": "start * pow(1 + rate/100, year)"}],
 "readouts": [{"label": "Final balance", "expr": "balance_last", "unit": "$"}]}
```
````

A control is a `slider` (the default), `number`, `select` (with `options`) or
`toggle`. The x axis is a swept range (`from`/`to`/`steps`, any of which may
itself be a formula, so one slider can set another's range) or a fixed
`values` list; pass `data` rows instead and formulas can transform real columns.
`readouts` are scalar tiles under the chart and can use `<series>_last`,
`_first`, `_min`, `_max`, `_sum` and `_mean`.

Formulas are **not** evaluated with `eval`. `src/renderer/src/lib/expr.ts` is a
small parser that compiles them to closures, with a fixed whitelist of maths
functions and no property access, indexing or assignment — a spec is written by
the model, which may have read an untrusted page, so a formula must not be able
to become code in the renderer. The worst a hostile one can do is return NaN.
`npm run test:expr` covers the grammar and that boundary.

Diagrams use ```` ```mermaid ```` (loaded lazily, so it costs nothing until
used). For anything those cannot express, `run_python` has numpy and matplotlib;
figures saved with `plt.savefig()` come back as images attached to the tool
call and are stored with the message.

## MicroVM sandboxes

Beyond one-shot `run_python`, each chat can get a persistent Linux sandbox the
assistant drives with the `sandbox_*` tools: `sandbox_exec` (shell),
`sandbox_write_file` / `sandbox_read_file` / `sandbox_list_files` (state in
`/workspace` persists between calls), `sandbox_put_document` (copy an uploaded
document's text in for editing or analysis) and `sandbox_reset`. Sandboxes are
containers run through the `docker` CLI — on this machine's colima setup they
execute inside a Virtualization.framework Linux VM, so the host filesystem is
unreachable by construction. Containers are created with `--network none`
(settings `sandboxNetwork: true` attaches the network, and networked results
then taint the run exactly like `fetch_url`), capabilities dropped, and
memory/cpu/pids caps; at most 5 exist at once (LRU-reaped). They are stopped on
app shutdown and kept for `sandboxKeepDays` (default 14), so `/workspace` and
installed packages survive a relaunch. `sandbox_checkpoint` / `sandbox_restore`
snapshot the filesystem to a local image (3 per chat) and roll back to it;
`sandbox_reset` also drops the checkpoints. `sandboxImage` (default `python:3.12-slim`) and
`sandboxRuntime` (default `docker`) are configurable in settings. The tools
only appear when the runtime is actually reachable. A container left idle for five
minutes is stopped by a one-minute reaper (its files and installs survive and it
restarts on next use) and every container is stopped at exit.

## Host shell

`shell_run(command, cwd?, timeout_s?, background?)` runs a command in `/bin/zsh` on
this Mac, inside the desk workspace or a folder listed under `workspaceRoots`.
It is confined by the OS, not by parsing the command: macOS Seatbelt lets it read
the disk except secrets (ssh, gpg, aws and gcloud config, keychains, any `.env`,
the app's own data), write only in the folder it runs in and a private temp dir
(never a repo's `.git/hooks` or `.git/config`), and reach no network unless
`shellNetwork` is on, in which case the reply is marked as having read untrusted
content. The environment is an allowlist (`PATH`, `HOME`, `LANG`, `TERM=dumb`,
`TMPDIR`), never the app's own. Output (stdout and stderr together) passes through
credential redaction, is cut to the last 2000 lines or 50 KB, and the full text
sits behind a result handle that `read_tool_result` pages. A foreground command ends
at `shellTimeoutSec` (default 120, at most 600): SIGTERM to its whole process
group, SIGKILL three seconds later.

`background: true` returns a job id; `shell_poll` returns what is new and the exit
status, `shell_kill` stops it, and the finished job's exit code and last output are
handed to the model at its next round (or wake an idle desk) unless
`notify_on_complete` is false. At most `shellMaxBackground` jobs run at once and 64
are tracked; finished ones are forgotten after 30 minutes. Jobs end with the chat
reply that started them and with the app. After a restart a survivor is listed
as `orphaned` (kill only) and is never adopted.

`shell_run` asks by default. If `sandbox-exec` is missing or refuses the profile
nothing runs; the only way on is `unsandboxed: true`, which is a forced approval on
every call that no "always allow" can remove. Scheduled (unattended) runs cannot use
it at all.

### Scripts that call tools

`run_python(code, tools=[...])` lets a script call app tools as
`grain_tools.call("fs_grep", pattern="TODO", root=".")` over a Unix socket that is
the one thing the sandbox profile lets it reach. Only `fs_glob`, `fs_grep`,
`read_local_file`, `fs_edit`, `search_documents`, `web_search` and `fetch_url` can
be named, and only if they are on or ask for the chat. A call gets the same gate as
the model's own: a tool in `ask` parks the script on an approval card (the script's
clock stops while the card is open), and a run with nobody to ask refuses it. At
most 50 calls and 300 seconds; stdout is kept as 40% head and 60% tail up to 50 KB,
the whole text behind a handle, and stderr to 10 KB.

## Traces

Each assistant message carries a `trace`: spans of kind `context`, `llm`, `tool`
and `learn`, each with start and end times and metadata such as token usage,
time to first token, finish reason, tool arguments and result sizes. Spans are
streamed as `span` SSE events while the reply is generated, so the Trace tab in
the Context panel (⌃⌘I) fills in live. The chip under a finished reply
(`4 steps · 6.1 s · 3.7k tok`) opens that reply's trace.

## Usage and cost

Every model call, including auto-learn extraction, appends a row to `usage_log`
with its model, kind, conversation, project, token counts, latency and cost.
Prices are read from the LiteLLM proxy's `/model/info` and cached; anything the
proxy does not price can be set by hand in Settings, which re-prices the whole
history. When a provider does not return a usage block, tokens are estimated
from character counts and the row is flagged `estimated`.

## Activity monitor

Off by default. Turn it on in the **Activity** panel (⌘9), where each signal is a
separate switch with a plain description of what it records — or flip **Record
everything** for one switch that records everything, with the redaction and
“never record” filters down. Turning that mode off restores the settings it
replaced rather than resetting to defaults.

One script installs what can be installed and prints what is left to grant:

```bash
./scripts/activity-setup.sh
```

The panel's access checklist probes all six macOS permissions — Accessibility,
Input Monitoring, Screen Recording, browser Automation, Microphone, Full Disk
Access — says which signals each one gates, and offers a **Grant** button that
asks macOS directly plus a deep link to the right Settings pane. Restart the app
after granting: a keystroke tap created before the grant stays dead. In
development the grants go to **Electron**, not Personal OS.

Full design, privacy model, API and limits:
[docs/activity-monitor.md](docs/activity-monitor.md).

## Meetings

Also off by default, and a separate switch from the activity monitor. Open the
**Meetings** view (⌘⇧M), pick a microphone, and press **Test** before you rely on
it. On-device Speech is the default when macOS has granted it; otherwise a
default `litellm.yaml` has nothing behind `/v1/audio/transcriptions`, so the
self-test is what tells you transcription works, and a failing one blocks
Record rather than warning.

ffmpeg, whisper.cpp and BlackHole are optional fallbacks, not a setup tax:

```bash
cd backend && uv pip install -e '.[activity]'   # AVAudioEngine, process tap, Speech
# only if you want the fallbacks:
brew install ffmpeg                             # truncated-wav repair
brew install whisper-cpp                        # instead of Apple Speech
brew install blackhole-2ch                      # system audio on macOS older than 14.2
```

Attribution is channel-level — you versus them — not per person.

Full design, pipeline, privacy model, the Audio MIDI Setup recipe, API and limits:
[docs/meetings.md](docs/meetings.md).

## Sandbox

`run_python` executes scripts with `python -I` in a throwaway directory, with
CPU, memory and wall-clock limits, wrapped in macOS `sandbox-exec` with a
profile that denies network, writes outside the work directory, and reads of
local files. A script may read the interpreter, its libraries, and that
throwaway directory.

## Layout

```
src/main/           Electron main: window, menu, backend sidecar, .env loader
src/preload/        contextBridge (backend URL, menu events)
src/renderer/       React UI (store.ts holds all state; lib/api.ts is the client)
src/shared/         Types shared between processes
backend/personal_os app.py routes · repos.py storage · context.py · learn.py
                    tools.py · sandbox.py · google.py · todos.py · boards.py
                    docs.py · dashboards.py · usage.py · trace.py · llm.py
                    activity.py collectors, privacy gate, rollup, activity.md
                    meetings.py repo + service · meeting_notes.py templates/enhance
                    meeting_recorder.py capture threads · stt.py · audiocap.py · native_audio.py · redact.py
scripts/dev.sh      LiteLLM + backend + Electron
scripts/litellm.sh  LiteLLM proxy alone
litellm.yaml        Model routing (Fireworks by default)
docs/research/roadmap.md    Feature research and roadmap
docs/docs-editor.md The Docs editor: revisions, diffs and the doc_* tools
docs/activity-monitor.md  Activity monitor: signals, privacy model, API
docs/meetings.md    Meetings: the capture pipeline, consent, STT setup, API
```

## Roadmap

See [docs/research/roadmap.md](docs/research/roadmap.md) for the researched roadmap across
memory, retrieval, app features, life-OS features and agentic capabilities,
with the per-track source reports under [docs/research/](docs/research/).

Track 5's four security defects (G1–G4: sandbox profile, SSRF guard, sidecar
auth, CSP images) were fixed on 2026-09-29. Much of what the roadmap then listed
as later work has shipped since: durable runs, budgets, taint tracking and the
undo journal, skills, MCP connectors, scripts that call tools, local file tools
and the agent browser (see [CHANGELOG.md](CHANGELOG.md)). What is still open is
the **Planned** items under Features, plus whatever in the roadmap is not in
the changelog yet.
