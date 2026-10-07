# Grain

<img src="build/grain.svg" alt="Grain logo" width="72" align="right" />

Grain is a personal AI operating system for your desktop. Chat with any model through
[LiteLLM](https://docs.litellm.ai/) (Fireworks AI out of the box), give the
assistant tools, and let it build memory and a knowledge graph about you as you
go. Organise work into **projects**: groups of chats with their own
instructions, files, memories and graph.

```
┌──────────────┬──────────────────────────────────────┬──────────────┐
│ + New chat   │  Today · Monday, September 29        │  Context     │
│ Today      2 │  ┌ Calendar ─────┐ ┌ Lists ────────┐ │  ☑ Memory    │
│ Files        │  │ 10:00 Standup │ │ ○ Ship v0.1   │ │  ☑ Graph     │
│              │  │ 14:00 1:1     │ │ ○ USB-C hub   │ │  ☑ Files     │
│ Library      │  └───────────────┘ └───────────────┘ │  ☑ Auto-learn│
│ SPACES     + │  ┌ Unread mail ──┐ ┌ Projects ─────┐ │  ☑ Tools  ▾  │
│ ▦ Space 1    │  │ Alice: Q4 …   │ │ ■ Grain       │ │   web search │
│ PROJECTS   + │  └───────────────┘ └───────────────┘ │   run python │
│ ■ Grain      │                                      │   gmail send │
│ RECENTS      │  [Brief me]                          │  Last reply… │
│ · …          │                                      │              │
│              │                                      │              │
└──────────────┴──────────────────────────────────────┴──────────────┘
```

Lists, Calendar, Mail and Health are sidebar rows under Today and Files.
Settings → Appearance → Sidebar turns any of them off (it takes effect at once). A **Quick chat** button at the top right of
every view opens the ⌘I panel. Bars are 40 px and
sidebar rows 26 px, so more fits on screen.

## How to use

A first-run walkthrough. Each step stands alone, so skip to the one you need.

1. **Install and launch.** Open the packaged app (or `./scripts/dev.sh`, see
   Development). The setup wizard asks which model provider to use and for its
   key, then a few lines about you, which become a pinned memory so the first
   reply already knows who you are. Connect Google from Settings (⌘,) →
   Integrations; Calendar, Mail and the first-prompt suggestions wait for it.
2. **Chat.** ⌘N opens a new chat. The model and effort pickers sit under the
   composer. Set a fast model in Settings → Model and pick **Auto** in the model
   menu: short plain messages go to the fast model, long, analytical or
   tool-heavy ones (and High or Max reasoning) to the default, with the reason
   shown while the reply starts. Type `/` for slash commands: `/skill`, `/schedule`, `/loop`,
   `/compact`, `/skills`, `/commands` and `/plan` (⌘⇧P also cycles plan mode).
   From any app, press ⌥Space (or the menubar item) for a small ask bar: type a
   line, optionally attach your clipboard text, and the reply streams in place.
   Open in chat continues it in the main window.
   The speaker button on a reply reads it aloud (voice and speed in Settings →
   Chat). `/voice`, or the waveform button by the mic, starts hands-free voice
   chat: speak, pause, and the message is sent and the reply read back, then it
   listens again until Esc, the button, or the turn cap; an open approval card
   pauses it.
   Drop or paste a file to attach it: it rides on the message as a chip, and its
   text is given to the model up to a size cap. Set a **Working folder** for the
   chat if the assistant should read or write files there; the **Style** picker
   under the box shapes replies (concise, formal, tutor, thorough or your own
   wording), and Settings → Behavior sets it for new chats; with none set, it
   uses `~/Grain`. Tools run on their own, and a tool in *ask* mode stops the
   reply with an approve/deny card. Mail sends, calendar deletes, moving or
   trashing files, shortcuts, Python installs and scheduling always ask.
3. **Files.** ⌘4 opens Files, with three sections: *Notes*, *Uploads* (⌘U) and *Artifacts*.
   ⌘⇧N makes a note, ⌘⇧D opens today's. Paste or drop an image into
   a note and it shows inline, then gets a description and its text read so search finds it. ⌘I opens the Page agent
   panel; a note has its own chat there, and opening another note switches to
   that one's chat. The assistant can edit a note (you accept each diff) and can
   delete one after asking; Settings → Trash restores it.
4. **Lists, Calendar, Mail.** These are rows in the sidebar
   (⌘2, ⌘3, ⌘5). Lists holds your todos in a rail of lists, with a one-line add
   row. With Google connected, todos sync both ways with Google Tasks. Double-click the
   calendar to add an event; Mail drafts and sends with a 90 s undo.
5. **Projects and memory.** In the sidebar, **+** next to Projects makes a
   project: instructions, knowledge files, and memories that apply only inside
   it. Auto-learn saves memories and graph links after replies, and when it sees
   you repeating yourself it suggests a skill. Review it all in Settings →
   Memory (⌘6), which also holds **Voice**, the profile of how you write.
   Under each reply, "Used N memories" and "Learned M" chips show what it read
   and saved (with Undo), and a chat's menu has **Don't learn from this chat**.
6. **Spaces.** ⌘⇧C opens Spaces, a desktop of live windows. Use **Add widget**
   or right-click the plane to add a chat, lists, calendar, doc, memory, graph,
   uploads, recap, project, usage, face or crew window; drag a chat, a
   Files note or a sidebar row onto it. ⌘⌃O pops a window out of the Space into
   its own OS window. Save a layout as a preset from the Spaces bar, and lock a
   Space with ⌃⌘L so it cannot be rearranged. See [docs/spaces.md](docs/spaces.md).
7. **Agents and crews.** Library → Agents lists roles. Describe one and the
   model drafts its label, hue, skills, boundaries and prompt; edit it, then
   approve it. Click an agent to open its page: its chats, its routines (jobs
   that run as it), the skills it carries, notes it always remembers and its
   recent activity. Start a chat as an agent, type `@name` in any chat to reach
   it, or let a reply hand work to a background worker with `delegate` (naming a Library agent runs the worker as it).
   Subagents appear as indented rows under the reply that started them, with
   live status; click one to open and message it. A crew window shows the
   delegating agent as a big face with its subagents around it. Longer jobs: turn on **Work
   autonomously** under the composer, pick Plan first, Ask as it goes or Work and
   propose, and watch the strip above the composer; its side panel holds the
   workspace files, changes and review. Library
   → Automations holds workflows, which you approve once. `/schedule` or the
   `schedule_task` tool books a run for later; its results arrive in the Agent
   inbox on Today as proposals. A good reply climbs a ladder: **Save as skill…**
   turns it into a candidate procedure (steps, decision rules, failure handling,
   output, boundaries) for you to approve, and **Schedule as routine…** opens the
   task editor switched off; **Test run** it, check the result labelled test, then
   Enable. Each task keeps its last 20 runs.
8. **Settings you will touch.** Tools: each tool's mode (on, ask, off), Allowed
   hosts, and Workspace folders. When a reply that read the web wants to fetch a
   page, approve the card or click **Allow <host> from now on**. Modules: which
   views get a sidebar row.
   Data: daily backups (the newest 7 plus one a week), **Back up now**, restore
   on next start, and **Export all data** as a zip. Behavior → Appearance: theme, accent and Zoom (80-160%, ⌘= / ⌘−, ⌥⌘0 to reset).

## Features

- **Quick ask bar.** A global hotkey (⌥Space, editable in Settings) opens a floating
  one-line composer that starts a new chat with the normal tools and approvals,
  can quote your clipboard, and hands the chat to the main window.
- **Selection verbs.** Select text in a chat, a file, an email or the page agent
  (or right-click it) for Explain, Summarize, Verify and Ask. Each opens the ⌘I
  panel with the quote fenced as data; Verify checks claims against opened pages
  and cites sources. The floating bubble can be turned off in Settings → Chat.
- **Provider-agnostic chat.** Streaming replies from any model LiteLLM routes to,
  one key. Per-chat model picker. Markdown, code copy, regenerate, stop.
  Type `/` in the composer for `/skill`, `/schedule`, `/loop`, `/research`, `/compact`,
  `/skills`, `/commands` and `/plan`. Attachments ride on the message as chips,
  and their text is given to the model under a size cap. Chat rows show a face
  that blinks while the chat works.
- **Image generation.** `generate_image` makes pictures with the model chosen under
  Settings → Model(s) → Image model (any provider that exposes an OpenAI-compatible
  `/images/generations` endpoint). Results are saved in Uploads and shown in the chat;
  the tool asks first by default (change it under Settings → Permissions).
  After a reply, up to three follow-up questions appear as chips under it
  (Settings > Memory, Follow-up suggestions): click one to fill the composer,
  Shift-click to send.
- **Research mode.** `/research <question>` plans sub-questions, runs read-only
  researchers in parallel, drops claims with no source, and answers with `[n]`
  citations and a collapsed trail of the plan and sources considered. See
  `docs/research-mode.md`.
- **Tools with permissions.** The assistant can search your uploaded files, read and
  revise your files, search
  and save memory, traverse and extend the knowledge graph, read your writing
  style before drafting as you, search the web and
  read pages, run Python in a sandbox, manage your todo lists, and (once
  connected) read your Google Calendar, triage Gmail, draft or send email, and
  manage Google Tasks. Each tool has a mode: **on** (runs automatically),
  **ask** (pauses the reply with an inline approve/deny card) or **off**.
  Tools default to on, and a few always ask, whatever their mode: Gmail send,
  calendar delete, trash or move a file, run a shortcut, install a Python
  package and schedule a task. Override modes globally, per project, or per
  chat.
  The optional **Review gate** (Settings → Permissions) has a second model read a
  risky call that would run unasked and turn it into an approval card, showing
  its reason. Its "ask" always wins, over allow rules and grants alike; an
  unreadable answer or an error asks too, safe tools are never reviewed, and a
  background run only records the verdict. Off by default. Web search retries a failed key, then falls back to keyless engines
  rather than failing. A Firecrawl key (Settings, or `FIRECRAWL_API_KEY` in `.env`) makes Firecrawl the first engine for web search and page reads, with the other engines as the fallback. Fetching a URL after the reply read untrusted content
  asks once; the card can also add the host to Settings → Permissions → Allowed hosts.
  Hovering a tool row or a sidebar row shows what it does. Tool calls
  render inline with arguments, results and timing, and every reply carries an
  execution trace.
- **Projects.** Groups of chats with instructions, knowledge files, project
  memories and a project graph, layered on top of your personal ones.
- **Memory.** One panel (Settings → Memory, ⌘6) holding what the app remembers about you, over a shared
  scope filter and search box — the first two halves side by side, either alone,
  or the voice profile on its own (Split is the default):
  - *Memories* — facts, preferences and goals, auto-extracted after each reply
    or added by hand or by the assistant. Edit, pin, move between personal and
    project scope, forget, see a memory's past versions, and export or import
    a scope as a JSON file. It lives in Settings → Memory, next to
    the auto-learn switches.
    **Don't learn from this chat** (chat menu or Context panel) keeps a chat in
    history but out of auto-learn, skill drafts and the graph; **Forget what was
    learned here** trashes the memories, draft skills and relations it produced.
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
- **Side chat.** ⌘I opens a chat beside any view, with the same model and effort pickers as the main chat. The pin button keeps it on the page it was opened on while you switch views; Back to this page unpins.
- **Files.** One view with two sections:
  - *Notes* — writing of your own, in an editor rather than an upload box:
    markdown and LaTeX, a line-numbered editor beside a live preview, folders
    per project, and full revision history. The assistant can read and revise
    a note, or delete one after asking (undo from Settings → Trash). Each note
    has its own chat in the Page agent panel (⌘I). By default its edits are *proposed*: each one arrives as a diff you
    accept or reject, so you can point a model at prose you care about.
    Settings → Permissions → **File edits** → *Accept all* writes them straight
    in instead, still showing the diff and still undoable from the history.
    A `/` menu, `[[wikilinks]]` and backlinks, an outline, templates and a
    daily note. Export menu → *Export as PDF…* typesets the note (maths,
    code, tables) for A4 or Letter, or files the PDF straight into Uploads. See [docs/docs-editor.md](docs/docs-editor.md).
  - *Uploads* — any file up to 50 MB (⌘U). Text, PDF and Word are read; other
    files are kept by name. Chunked, indexed, and the best excerpts pulled
    into replies.
  - *Artifacts* — everything the assistant made in any chat (documents, data,
    images, code, browser downloads), from plain chats and autonomous sessions
    alike, grouped by chat, newest first, with a link back to the chat. Nothing is
    copied: the list is an index of the files where they were saved. A chat in the
    trash keeps its artifacts here until it is erased for good.
- **Spaces.** A desktop of live windows beside the ordinary views (⌘⇧C, or a
  space in the sidebar; ⌃1–⌃9 jump between spaces). Chats, lists, calendar,
  docs, memory, graph, uploads, recap, project, usage, face and crew
  windows sit side by side. Drag anything from the sidebar, or a Files note
  (it becomes its own editable `doc` window), or right-click to add. A window can
  pop out into its own OS window, pinned on top and see-through, and one
  global shortcut gathers them all. Save a space as a preset, lock it so
  neither you nor the assistant can rearrange it, and let the assistant add
  windows with the `space_*` and `widget_*` tools. See
  [docs/spaces.md](docs/spaces.md).
- **Health.** Daily metrics (water, steps, sleep, weight, mood, or your own)
  with goals, a Today card, and `health_*` tools for the assistant. COROS and
  Garmin sync through MCP. See [docs/health.md](docs/health.md).
- **Work autonomously** (a control beside plan mode in any chat). A chat hands its task to a desk that keeps
  working in the same conversation, in its own folder, and has one plan you approve before it acts. Several run at
  once. Each turn is an ordinary reply that runs until the work is done, the desk asks you something,
  or stuck detection stops a loop; a turn that simply trails off gets one nudge to finish or ask. Three modes: Plan first (nothing consequential runs until you
  approve a plan, and those tools are withheld rather than offered and refused),
  Ask as it goes (no plan up front; risky actions follow the permission mode), and Work and propose (it may plan an external
  action and never perform one). A strip above the composer shows the state, turns used and
  how many things need you, with Start, Pause, Resume and Stop, and opens a side panel
  with Files, Changes and Review tabs. While a lone agent is working the strip stays out of the way
  (the composer's Stop covers it); it appears once a background worker is live or the desk needs you. Questions, parked cards, the plan and interruption notices appear at the
  end of the transcript. Autonomous chats are listed with the other chats with a status dot, and the Chats header
  carries the Needs-you count. Nothing it writes reaches the app until you accept
  it: it works in `cowork/<desk>/` and nominates files for review, and every
  promotion is read back before it counts. An accepted output goes where you
  send it: a new note or an append to one, an upload, a download, todos (one per
  checklist line), or a Gmail draft (a file with
  To and Subject headers; drafted, never sent). A card nobody is watching parks after a
  few minutes: the run lets go, the card stays pending and decidable, and answering
  it wakes the desk. Files attached to an autonomous chat land in its `inputs/` folder.
  Turning autonomy off detaches the desk and the chat answers as a plain chat again; the workspace is kept.
  Another chat can also hand work to a desk (`desk_start`) along with the docs it needs; when the desk
  finishes, fails or waits for review, its report is posted back into that chat.
  A desk that needs you also shows up in the Agent inbox on Today. See [docs/cowork-design.md](docs/cowork-design.md).
- **Agent inbox.** Everything agents left for you, in one list on Today, with the
  total on the sidebar's Today row: approval cards from any chat, desks waiting on
  you, scheduled-job proposals and paused jobs, and a count with a link for every
  other review queue (proposed doc edits, skills and workflow runs
  to approve, memory tidy-ups). A plan or a desk's question
  opens where it is decided rather than offering a bare Allow.
- **Library.** One place for what the assistant may follow and reach: **Skills**,
  the procedures it can be asked to repeat; **Agents**, roles with their own
  face, instructions, tools and skills (describe one and the model drafts it; you
  edit and approve); **Automations**, which holds workflows (multi-step jobs you
  approve once) and commands (prompt templates you write); and **Connectors**, the MCP servers whose tools join the toolbox. Install them
  from a curated catalog, search the official MCP Registry, or import the servers you already set up in
  Claude Desktop, Claude Code or Cursor. Every connector tool asks by default, grants are bound to the
  tool's schema, and keys live in the Keychain. See [docs/connectors.md](docs/connectors.md).
  A skill is the one place prose a model wrote could land inside a later system
  prompt, so authoring is lint-gated: warnings are quality, but any sentence that
  claims authority over the assistant's permissions is an error that blocks
  approval, and the same check runs on what `skill_draft` writes and on what you
  approve by hand. `skill_draft` and `skill_revise` can only ever produce a
  candidate — there is no tool that approves one, and a revision of an approved
  procedure is forked beside it rather than overwriting the text in use. The
  preview shows the real injected block, assembled by the function the chat uses.
  Writing one is tooled: say what it is for and the model drafts the steps into the
  form for you to edit (nothing is saved until you add it), and the lint reads the
  draft as you type.
  Skills can be imported by URL, and a Popular skills catalog offers presets.
  The memory extractor turns repeated friction in a chat into one suggested skill.
- **Agents and subagents.** A reply can hand work to a background worker (`delegate`); desks, workflows and crews start subagents (`agent_spawn`).
  They show as indented rows under that reply with live status; click one to open
  and message it. A crew window shows the delegating agent as a big face with its
  subagents around it. Subagents get the chat's tools minus asking, planning and
  scheduling, there is no per-subagent cost cap, and workflow steps see the
  chat's working folder. A chat can also speak as an approved agent.
- **Taking over the browser.** When the assistant's own browser reaches a sign-in,
  two-factor code, CAPTCHA or payment step, it hands the window to you: finish the
  step, press Hand back, and it carries on from the page you left. A Take over
  button on a live browser card does the same unasked. See `docs/cowork-design.md`.
- **Scoped agents as teammates.** An agent carries a one-line label, boundaries
  (what it must ask first and what it never does, fenced into its prompt), its
  own notes, an optional working folder and its own tool settings (above the
  project's, below the chat's; anything that leaves the app still asks). Its
  routines are scheduled jobs that run as it, still proposal-only; a task
  scheduled from its chat joins them. Typing `@name` opens an agent menu: a
  message that starts with `@name` goes to that agent's latest chat, and an
  `@name` elsewhere hints the reply to hand the work to it. See
  [docs/agents.md](docs/agents.md).
- **Context management.** Per-chat toggles for memory, graph, files,
  auto-learn and tools; an inspector showing exactly what was injected into
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
- **Side panel.** The assistant can open a panel beside the chat on something
  you should look at: a full-page HTML mock-up, an SVG, a diagram, a chart,
  markdown, or a file on this Mac (a PDF in the built-in viewer, an image, a
  text file). Any ```` ```html ````, ```` ```svg ````, ```` ```mermaid ````,
  ```` ```chart ```` or ```` ```interactive ```` block in a reply has an
  "Open in side panel" button too. Split puts two things side by side (two
  PDFs, or a PDF and a note); a file refreshes from disk, and a PDF sits in a
  quiet frame with its toolbar tucked away. See `docs/side-panel.md`.
- **Traces.** Every reply records what it did: context assembly, each model
  round with time-to-first-token and token counts, each tool call, and the
  auto-learn pass. Spans stream live into a waterfall in the Context panel.
- **Backups.** Settings → Data takes a daily backup (the newest 7, plus one per
  week for four weeks) and manual ones with **Back up now**. A restore is staged
  and applied on the next start. **Export all data** writes a zip.
- **Usage and cost.** Each model call is logged locally with tokens, latency and
  cost. Settings shows spend, tokens, calls and frequency charts over 7/30/90
  days, broken down by model, kind and project. Prices come from your LiteLLM
  proxy and can be overridden per model.
- **Today, lists, calendar.** A Today screen with a generated daily
  recap, calendar, unread inbox, todos, projects and recently learned memories,
  plus a one-click brief. Lists is a native todo view with a rail of lists and a
  one-line add row, a week calendar (Google events
  plus due todos, double-click to add), and a board view of the same todos (columns by status or by list, drag and
  drop to move). Each older board is now a list, each card a todo with the column as its status.
  The assistant can drive all of them through tools.
- **Scheduled tasks and the agent inbox.** Give the assistant work to do later:
  once at a time you pick ("tomorrow at 3pm, check whether they replied") or
  repeatedly on a cron expression ("every Friday at 17:00, write my weekly
  review"). Schedule it from the Scheduled tab of the Agent inbox on Today, or just ask in a chat —
  the assistant has a `schedule_task` tool, which asks before it books anything.
  A scheduled run happens with nobody watching, so it is deliberately boxed in:
  it runs in a fresh chat, it can read and write inside
  Grain, and anything that would leave the app — mail, calendar events, Docs —
  comes back to the Agent inbox as a **proposal** you accept, edit or reject.
  Accepting is what actually sends it, exactly once. A run cannot schedule
  further runs either; that proposal is yours to accept too. If the machine was
  asleep over a slot the task still runs, once, and is told it is late so it says
  so in its report. A one-off retires itself after it fires. Besides a clock, a
  task can start from a folder, from matching mail, or **a set number of minutes
  before a calendar event** whose title or guests match your words (it reads the
  cached calendar, fires once per event, and is handed the event's details).
  Tick **Notify only when the result changes** on a task that keeps checking the
  same thing: a run whose result matches the last one is recorded as unchanged
  and never reaches the inbox or a notification, and the task shows when it last
  changed. Unattended reports use fixed headings (Verified, Assumptions, Done,
  Awaiting approval, Open questions), each claim with its evidence, and the inbox
  shows them as sections.
- **Google Workspace.** Sign in once with your own OAuth client; Calendar,
  Gmail, Tasks, Drive, Docs and Sheets become assistant tools. Connecting turns on two-way Tasks sync with your todos. The planner puts its
  Focus blocks on a calendar named "Grain Todos".

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
`uploads/` and `doc_assets/` (your files and pasted images), `cowork/` (desk
workspaces), `backups/` and `logs/`. API keys and Google tokens are kept in the macOS
Keychain, or a 0600 file in the data folder when the Keychain is unavailable.
In development, `scripts/dev.sh` runs LiteLLM, the backend (autoreload) and
Electron (HMR) together.

## Install

The packaged app (`npm run package`, see [docs/releasing.md](docs/releasing.md))
is self-contained: no Python, uv or LiteLLM needed. On first launch a setup
wizard asks a few things about you and has you pick a model provider (Fireworks
AI, OpenAI, Anthropic, OpenRouter, a local Ollama, a LiteLLM proxy or any
OpenAI-compatible endpoint). Google is connected from Settings → Integrations. With no workspace folder set, file and shell tools work in `~/Grain`.

On your own Mac, `npm run install-app` puts the build at `/Applications/Grain.app` (keep only that
copy). If a self-signed "Grain Local Signing" identity is in your keychain, `npm run package` signs
with it, so macOS keeps Full Disk Access and other grants across rebuilds; otherwise builds are
ad-hoc signed. See [docs/releasing.md](docs/releasing.md#signing).

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

Scopes: `calendar`, `gmail.modify`, `tasks`, `drive.readonly`, `drive.file`,
`documents` and `spreadsheets`, plus `openid` and `email` to know which account
signed in. Tokens and the client secret are kept in the macOS Keychain (a 0600
file in the data folder if the Keychain is unavailable). `gmail_send` is a
separate tool you can keep off; `gmail_draft` never sends.

Connecting turns on two-way sync between your todos and Google Tasks, which can
be switched off under Integrations. Todos are not copied onto your calendar; the
planner puts its Focus blocks on a calendar named "Grain Todos".

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
| ⌘K | Command palette (link while typing in a doc) |
| ⌘N | New chat |
| ⌘⇧N / ⌘⇧D | New note / today's note |
| ⌘U | Upload file (Files → Uploads) |
| ⌘0 … ⌘6 | Today / Chats / Lists / Calendar / Files / Mail / Settings → Memory |
| ⌘⇧M | Maths (while typing in a doc) |
| ⌘⇧F | Search chats |
| ⌘⇧[ / ⌘⇧] | Previous / next chat |
| ⌘F | Find in this chat |
| ⌘⇧P | Cycle plan mode in the composer |
| ⌘B | Toggle sidebar (bold while typing in a doc) |
| ⌘I | Page agent panel |
| ⌃⌘I | Toggle context panel |
| ⌘⇧C | Toggle Spaces (Spaces menu) |
| ⌃1 … ⌃9 | Go to space 1–9 |
| ⌘, | Settings: Model, Usage, Permissions, Integrations, Texting, Memory, Appearance, System access, Advanced |
| ⌘/ or ? | Help: every shortcut, searchable, plus the Using Grain guide (also Help menu) |
| Enter / Shift+Enter | Send / newline |

A view turned off in Settings → Appearance keeps its shortcut, which then offers
to turn the view back on instead of opening it.

## How a reply is built

1. System prompt: your global prompt, then the project's description and
   instructions.
2. **Memories** in scope (personal + project): pinned first, then recent, plus
   full-text matches for the message.
3. **Graph** entities whose labels appear in the message with their 1-hop
   neighbours as `A —[relation]→ B` triples.
4. **Document excerpts**: top BM25 matches over chunks in scope.
5. **Tools**: the effective tool set after global, project and chat overrides.
   The model calls tools until it is done (stuck detection stops a loop); each call and
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

## Coding sessions

`coding_session_start(agent, repo_path, prompt, new_worktree?, branch?, model?, permission_mode?, name?)` hands a
coding task to Claude Code (`agent: "claude"`) or OpenCode (`agent: "opencode"`) on a git repo inside a workspace
folder and returns at once; the agent works in the background and the app follows it. With `new_worktree` the agent
gets its own branch and `git worktree` under `<repo>/.claude/worktrees/` (default branch `grain/<task>-<hex>`, never
`main` or `master`), so the repo's checkout stays as it was. Each session is a row in `coding_sessions` (migration 11)
and every change is published as a `coding_session` event, which the "Coding sessions" section of the chat's context drawer and the tool cards read.

| Driver | Where it runs | How it is followed |
| --- | --- | --- |
| Claude Code | `claude --bg` in the repo or worktree, **outside** Grain's sandbox, with your own account and tools | the CLI's own files under `~/.claude/jobs/<id>/` (`state.json`, `timeline.jsonl`), read as untrusted text |
| OpenCode | `opencode run` under the OS sandbox as a background job in the shell registry; writes stay in the workspace folder that holds the repo | the job's event stream; a follow-up continues the same OpenCode session |

**Permissions.** Claude Code keeps its normal prompting: Grain passes no permission flag. A permission prompt shows as
`needs_you` (attention: needs you) and you answer it in a terminal with `claude attach <id>`; the card says so.
`permission_mode` (`acceptEdits` or `bypassPermissions`, Claude Code only) relaxes the prompting for that one session
only; a call that sets it is always a card, whatever the tool's mode or any standing grant, and the card spells out
what the mode allows. OpenCode has nobody at its prompt, so it runs with every permission allowed inside the sandbox
and takes no `permission_mode`. Starting and following up are `external` tools: they ask first in a chat and are only
proposals in an unattended run, and they ask again once the reply has read untrusted content. Subagents are gated
the same way as `opencode_run`.

| Tool | Danger | Default |
| --- | --- | --- |
| `coding_session_start` | external | ask |
| `coding_session_send(id, message)` | external | ask |
| `coding_session_stop(id)` | executes | on |
| `coding_session_list`, `coding_session_status(id, lines?)`, `coding_session_diff(id, full?)` | safe | on |

`send` only reaches a session that is `done` or `stopped`: continuing one that is still running would start a copy
of it, so it is refused until the session finishes or is stopped. `diff` runs `git status`, `git diff --stat` and the
commits ahead of `origin/main` in the session's worktree (the full patch with `full: true`, cut at 60 KB). Nothing here
removes a session (`claude rm` is never run), force-pushes or pushes at all; shipping the branch is `ship_checklist`.
OpenCode jobs end at the shell registry's 600 second cap; a follow-up carries on.

Routes: `GET /coding-sessions`, `GET /coding-sessions/{id}`, `GET /coding-sessions/{id}/logs?limit=`,
`GET /coding-sessions/{id}/diff?full=0|1`, `POST /coding-sessions/{id}/stop`, `POST /coding-sessions/{id}/send`
(`{message}`, 409 when the session cannot take one).

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

## Voice input

The mic button in the chat composer dictates into the message box. On-device Speech is the default
when macOS has granted it; otherwise Whistle, whisper.cpp or your LLM proxy (a default `litellm.yaml`
has nothing behind `/v1/audio/transcriptions`, so pick a backend in Settings → Advanced → Voice and shortcuts).

```bash
cd backend && uv pip install -e '.[mac]'        # Speech and the macOS permission probes
# only if you want the alternatives:
cd backend && uv pip install -e '.[whistle]'    # a 17 MB on-device model
brew install whisper-cpp                        # instead of Apple Speech
```

Setup, settings and permissions: [docs/voice-input.md](docs/voice-input.md).

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
                    tools.py · sandbox.py · google.py · todos.py
                    docs.py · recap.py · usage.py · trace.py · llm.py
                    stt.py voice input · macos.py permission probes · audiocap.py · redact.py
scripts/dev.sh      LiteLLM + backend + Electron
scripts/litellm.sh  LiteLLM proxy alone
litellm.yaml        Model routing (Fireworks by default)
docs/research/      Feature research, the roadmap, per-track source reports
docs/docs-editor.md The Files editor: revisions, diffs and the doc_* tools
docs/spaces.md      Spaces: windows, pop-outs, presets, lock, agent tools
docs/agents.md      Agents: scope, boundaries, routines, agent page, @mentions
docs/health.md      Health: metrics and connected services
docs/voice-input.md Voice input: backends, settings, permissions
docs/connectors.md  Connectors: MCP catalog, import, security model, writing your own
examples/mcp/       Two small example MCP servers (Python, TypeScript)
docs/permissions.md Permissions: the one store, its migration, the one Settings tab
docs/help.md        In-app help and the shortcut registry
```

## Roadmap

See [docs/research/roadmap.md](docs/research/roadmap.md) for the researched roadmap across
memory, retrieval, app features, life-OS features and agentic capabilities,
with the per-track source reports under [docs/research/](docs/research/).

Track 5's four security defects (G1–G4: sandbox profile, SSRF guard, sidecar
auth, CSP images) were fixed on 2026-09-29. Much of what the roadmap then listed
as later work has shipped since: durable runs, taint tracking and the
undo journal, skills, MCP connectors, scripts that call tools, local file tools
and the agent browser (see [CHANGELOG.md](CHANGELOG.md)). What is still open is
the planned items below, plus whatever in the roadmap is not in
the changelog yet.

Planned, not shipped:

**Sign in with ChatGPT.** Connect an OpenAI account through a browser OAuth flow
with a loopback return, so model calls bill the user's own OpenAI account and
its models join the picker. The LiteLLM key stays the fallback. The flow must
name the account being charged before the first call, since a Plus or Pro
subscription is not a pool of API credits.

**Private inference.** Jobs that read the sensitive store (auto-learn, voice extraction) go to a local model through
LiteLLM's `ollama/` route and fail closed if it is down, while chat can stay on
a cloud model. `extractionModel` today is only a cheaper LiteLLM name, not an
on-device guarantee.

**Cloud worker.** A second process on an always-on machine owns the jobs that
are useless with the lid closed: scheduled tasks, the morning brief, and
watches that propose into the Agent inbox. Each task has one home, local or
cloud. The worker keeps its own per-domain database (see
[docs/sources-of-truth.md](docs/sources-of-truth.md)), proposes rather than
acts, and never sees the filesystem.

**RLHF on company data (enterprise, later).** Approve/deny, accept/reject and
edited-vs-sent signals become labelled pairs for a tenant-local policy trained
inside the organisation. Personal Grain stays single-user and does not train
on your machine.
