# Grain

<img src="build/grain.svg" alt="Grain logo" width="72" align="right" />

Grain is a personal AI assistant that lives on your Mac. You chat with it, and it can do things for you: read and write your notes, manage your to-dos, look at your calendar and mail, search the web, run code, and work on longer jobs in the background. It remembers what matters about you, so you do not have to explain yourself every time.

It is for people who want one assistant that knows their work, runs on their own machine, and asks before it does anything that matters. You bring your own model provider and key. Your chats, notes, files and memories stay on your Mac.

## What it can do for you

- Answer questions and draft things, with a memory of your preferences, projects and people.
- Read, write and organise notes and files, and search everything you have uploaded.
- Keep your to-do lists, read your Google Calendar, and triage, draft and send Gmail.
- Do research with sources, browse the web for you, and run Python or shell commands in a sandbox.
- Take on longer jobs by itself, hand work to helper agents, and run tasks on a schedule.
- Hand coding tasks to Claude Code, OpenCode or pi and follow their progress.
- Take messages from your phone through your own Telegram bot.
- Track daily health numbers such as sleep, steps and water.
- Take dictation by voice.

## A quick look around

```
┌──────────────┬──────────────────────────────────────┬──────────────┐
│ + New chat   │            Good afternoon.           │  Context     │
│ Files        │  ┌ Daily recap ───────────────────┐  │  ☑ Memory    │
│ Lists        │  │ Two meetings, one deadline …   │  │  ☑ Graph     │
│ Calendar     │  └────────────────────────────────┘  │  ☑ Files     │
│ Mail         │                                      │  ☑ Auto-learn│
│ Health       │  [ Message …                      ]  │  ☑ Tools     │
│ Library      │                                      │              │
│ CANVASES   + │                                      │  Last reply… │
│ PROJECTS   + │                                      │              │
│ CHATS        │                                      │              │
└──────────────┴──────────────────────────────────────┴──────────────┘
```

The sidebar holds your views (Files, Lists, Calendar, Mail, Health, Library, Jobs), your canvases, your projects and your chats. The panel on the right shows what the assistant used for its last reply. Settings → Sidebar turns any row off.

---

## Feature tour

### Chat and agents

- **Chat with any model.** Replies stream in. Pick the model and the reasoning effort under the composer. **Auto** sends short, plain messages to a fast model and harder ones to your default. Every message can be edited, regenerated or stopped. `⌘F` finds text in a chat, `⌘⇧F` searches all chats.
- **Attachments.** Drop or paste a file into the composer. It rides on the message, and its text goes to the model up to a size limit.
- **Working folder.** Set one on a chat if the assistant should read or write files there. With none set, file and shell tools work in `~/Grain`.
- **Slash commands.** Type `/` in the composer:
  - `/skill <name>` uses one of your approved skills.
  - `/schedule` and `/loop` run something later or on an interval.
  - `/research <question>` plans sub-questions, searches in parallel and answers with `[n]` citations and a visible trail.
  - `/compact` summarises the chat so far to free room. `/clear` starts over.
  - `/skills` and `/commands` list what you have. Your own saved commands appear in the same menu.
- **Quick ask.** `⌥Space` from any app opens a small ask bar.
- **Selection verbs.** Select text in a chat, a note, an email or a page and choose Explain, Summarize, Verify or Ask. Verify checks claims against pages it opens and cites them.
- **Autonomous chats.** With **Start new chats working autonomously** on (Settings → Agents, on by default), a new chat can keep working on its task in its own workspace until it is done or needs you. A strip above the composer shows state and has Pause, Resume and Stop. A side panel (`⌘I` inside a chat) shows Files, Changes, Review and a checklist. Nothing a chat writes reaches your notes or files until you accept it in Review.
- **Helper agents.** A reply can hand work to background workers. They appear as indented rows under the reply with live status. Click one to open it and message it. A crew window on a canvas shows the lead agent with its helpers around it.
- **Charts, diagrams and previews.** Replies can include charts, Mermaid diagrams, and interactive charts with sliders that recompute on your Mac. The assistant can open an HTML page, SVG, PDF or image in a side panel. The Python sandbox draws figures inline.
- **Image generation.** Pick an image model in Settings → Models and ask for a picture. Results are saved to your uploads.
- **Browser.** The assistant has its own browser for pages that need JavaScript or a login. When it hits a sign-in, CAPTCHA or payment step, it hands the window to you and carries on when you press Hand back.
- **Traces.** Each reply records what it did: context, every model round with timing, every tool call. Open it from the chip under the reply, or the Trace tab in the context panel (`⌃⌘I`).
- **Context panel.** Per-chat switches for memory, graph, files, auto-learn and tools, plus an inspector showing exactly what went into the last reply.

### Memory

- **Memories.** Facts, preferences and goals, saved automatically after replies (auto-learn) or added by hand. Edit, pin, move between personal and project scope, forget, or see a memory's past versions. Under a reply, "Used N memories" and "Learned M" chips show what it read and saved, with Undo.
- **Knowledge graph.** People, places and things, with how they connect. Relevant parts are added to chats. You can edit it in a graph view.
- **Voice.** A profile of how you write, learned from your own messages and notes. It is used only when the assistant drafts something as you, such as an email.
- **Control.** **Don't learn from this chat** keeps a chat out of auto-learn. **Forget what was learned here** removes what it produced. Memories can be exported and imported as JSON.
- Open it with `⌘6` (Settings → Memory). See [docs/writing-style.md](docs/writing-style.md).

### Files and notes

- **Files** (`⌘4`) is a file tree of your own writing, with **Uploads** (any file up to 100 MB, `⌘U`) and **Artifacts** (what the assistant made in any chat, grouped by chat) as rows at its foot.
- **Notes** support Markdown and LaTeX, a `/` menu, `[[wikilinks]]` and backlinks, an outline, templates, folders, comments, pasted images and full revision history. `⌘⇧N` makes a note, `⌘⇧D` opens today's. Export as PDF from the Export menu.
- **Edits by the assistant** arrive as diffs you accept or reject. Settings → Permissions can switch that to apply directly (still undoable).
- **Uploads** are read (text, PDF, Word), indexed and searched. The best excerpts are pulled into replies with citations.
- Deleted notes and files go to Settings → Advanced → Data → Trash, where you can restore them. See [docs/docs-editor.md](docs/docs-editor.md).

### Lists, Calendar and Mail

- **Lists** (`⌘2`): your to-dos in a rail of lists, a one-line add row and a board view. With Google connected, to-dos sync both ways with Google Tasks.
- **Calendar** (`⌘3`): your Google events and due to-dos. Double-click to add an event. A planner suggests time blocks for your open to-dos and adds the ones you accept to a calendar named "Grain Todos".
- **Mail** (`⌘5`): read, reply and draft in Gmail. Sends are held for 90 seconds so you can undo them. A reply tracker finds threads waiting on someone. The assistant can attach files, add Cc and Bcc, and prepare an editable send card.
- **Verified writes.** After the assistant writes to Google (an event, a send, a task, a Doc or Sheet), it reads the result back. The tool row shows **verified**, **unverified** or **mismatch**, so you can see whether it really happened.
- Google also gives the assistant Drive, Docs and Sheets tools. Connect it in Settings → Integrations.

### Projects

A project is a group of chats with its own instructions, files and memories, layered on top of your personal ones. Make one with **+** next to Projects in the sidebar. A project can be isolated so its chats see none of your personal memory. Its page lists chats and files side by side, with its context, instructions and memory below.

### Canvases

A canvas is a desktop of live windows: chats, lists, calendar, notes, memory, graph, uploads, recap, projects, usage, faces and crews. Open it with `⌘⇧C`, add a window with **Add widget** or a right-click, and drag a chat, note or sidebar row onto it. Windows can pop out into their own always-on-top, see-through window. Lock a canvas (`⌃⌘L`) so nothing moves. The assistant can add and arrange windows too. See [docs/spaces.md](docs/spaces.md).

### Library

The Library (sidebar) holds what the assistant may follow and reach.

- **Skills.** Step-by-step procedures. The assistant can draft one from a chat you liked (**Save as skill**), and auto-learn suggests one when you repeat yourself. Nothing is used until you approve it. Import by URL or pick from Popular skills.
- **Agents.** Roles with a face, instructions, boundaries, tools and skills. Describe one and Grain drafts it. Type `@name` in a chat to reach one, or start a chat as an agent. Each agent has a page with its chats, routines and notes. See [docs/agents.md](docs/agents.md).
- **Automations.** **Workflows** are multi-step jobs you approve once and that run in waves. **Commands** are prompt templates you write. See [docs/workflows.md](docs/workflows.md).
- **Connectors.** MCP servers whose tools join the assistant's toolbox. Install from a catalog, search the official registry, or import servers you already use in Claude Desktop, Claude Code or Cursor. Connector tools ask by default, and keys live in the Keychain. See [docs/connectors.md](docs/connectors.md).

### Scheduled jobs and the inbox

- **Jobs.** Ask in a chat ("every Friday at 5, write my weekly review"), use `/schedule`, or use the Jobs view. A job can run at a time, on a cron schedule, when a folder changes, when matching mail arrives, or a few minutes before a calendar event.
- **Safe by design.** A job runs on its own in a fresh chat. Anything that would leave the app (mail, calendar, Docs) comes back as a **proposal** you accept, edit or reject. A job that finds nothing new can stay quiet.
- **Agent inbox.** One list for everything agents left for you: approval cards, chats waiting on you, job proposals and review queues. Open it from the title bar.

### Coding sessions

Grain does not edit code in a git repo by itself. It hands the change to **Claude Code**, **OpenCode** or **pi**, working in its own git worktree and branch, and follows the session. You see status, logs and the diff, and can send follow-ups or stop it. Starting a session asks first. See [docs/coding-agents.md](docs/coding-agents.md).

### Telegram

Message Grain from your phone through your own private Telegram bot. It runs the same chat turns, with the same tools and approvals (including Approve and Deny buttons), and can send back screenshots and files. Set it up in Settings → Integrations. See [docs/telegram.md](docs/telegram.md).

### Health

A page for daily numbers: water, steps, sleep, weight, mood or your own metrics, with goals. COROS and Garmin can sync through connectors. The assistant can read and log health data. See [docs/health.md](docs/health.md).

### Voice and dictation

The mic button in the composer dictates into your message. Hold the dictation shortcut to talk, or tap it to latch. Transcription runs on-device with macOS Speech, or with Whistle, whisper.cpp or your model proxy. See [docs/voice-input.md](docs/voice-input.md).

### Permissions and safety

- Each tool is **on**, **ask** or **off**, set globally, per project or per chat. A few always ask: sending mail, deleting calendar events, trashing or moving files, running Shortcuts, installing Python packages and scheduling.
- Fetching a page after the assistant has read untrusted content asks once. You can allow a host from then on.
- Shell commands and Python run in a macOS sandbox. Shell commands cannot read your secrets or reach the network unless you allow it.
- An optional **review gate** has a second model check risky calls first.
- A chat can run in Auto, Manual or Allow everything mode.
- Settings → System access shows what macOS has granted Grain and what the file tools may not touch.
- See [docs/permissions.md](docs/permissions.md).

### Settings

`⌘,` opens Settings. Tabs: General, Models, Agents, Memory, Integrations, Permissions, System access, Sidebar, Usage and Advanced.

- **Model and Models.** Provider, key, default and fast models, image model, and per-model price overrides.
- **Usage.** Spend, tokens and calls over 7, 30 or 90 days, by model and project.
- **Advanced → Data.** Daily backups, **Back up now**, restore on next start, **Export all data** as a zip, and Trash.
- **Help.** `⌘/` lists every shortcut and the built-in "Using Grain" guide. Ask the assistant "how do I..." and it answers from the same guide.

---

## Getting started

Grain runs on macOS (Apple Silicon or Intel). The simplest path is to ask an AI coding agent to set it up: point it at [AGENTS.md](AGENTS.md), which walks through cloning, installing, configuring and running Grain with a check at every step.

To do it yourself, you need Node 20, [uv](https://docs.astral.sh/uv/) and a model provider key (Fireworks AI by default; OpenAI, Anthropic, OpenRouter, local Ollama or any OpenAI-compatible endpoint also work):

```bash
npm install
cd backend && uv venv && uv pip install -e . && cd ..
cp .env.example .env          # optional: Google client, Fireworks key for the LiteLLM proxy
./scripts/dev.sh              # LiteLLM + backend + Electron
```

To build an app you can keep in `/Applications`, run `npm run package` then `npm run install-app`. See [docs/releasing.md](docs/releasing.md).

### First run

1. A setup wizard asks which model provider to use and for its key, then a few lines about you. Those become a pinned memory, so the first reply already knows who you are.
2. Optional: connect Google in Settings → Integrations for Calendar, Mail, Tasks, Drive, Docs and Sheets. A source build needs a one-time OAuth client, described below.
3. Press `⌘N` and say hello. Try `/research` on something you are curious about, or drop in a file and ask about it.
4. Make a project with **+** next to Projects, and add a note with `⌘⇧N`.
5. Optional: connect Telegram, add a connector in Library, or set up dictation.

### Google Workspace setup (source builds)

People using a packaged app only click **Sign in with Google**. A build from source needs an OAuth client first:

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials), create a project and enable the Calendar, Gmail, Tasks, Drive, Docs and Sheets APIs.
2. Configure the OAuth consent screen (External, with yourself as a test user). Publishing it, or making it Internal on a Workspace account, avoids the 7-day refresh-token expiry of "Testing" apps.
3. Create an OAuth client of type **Desktop app** and put its id and secret in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. A "Web application" client fails with `redirect_uri_mismatch`, because the backend uses a new loopback port on each launch.
4. Restart the app. Settings → Integrations shows a **Reconnect** button if a token later expires or is revoked.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `⌘N` | New chat |
| `⌘K` | Command palette |
| `⌘/` or `?` | Every shortcut, searchable, and the Using Grain guide |
| `⌘,` | Settings |
| `⌘1` to `⌘6` | Chats, Lists, Calendar, Files, Mail, Memory |
| `⌘⇧N` / `⌘⇧D` | New note / today's note |
| `⌘U` | Upload a file |
| `⌘⇧F` / `⌘F` | Search chats / find in this chat |
| `⌘B` | Toggle sidebar |
| `⌘I` | Inside a chat, toggles the side panel on Context |
| `⌃⌘I` | Toggle context panel |
| `⌘⇧C` | Toggle canvases (`⌃1` to `⌃9` jump between them) |
| `⌃⌘O` | Pop a window out of its canvas |
| `⌃⌘L` | Lock or unlock a canvas |
| `⌥Space` | Quick ask, from any app |
| `⌘⇧Space` | Quick capture, from any app |
| `Enter` / `⇧Enter` / `Esc` | Send / new line / stop the reply |

The full list, including note-editor and window shortcuts, is in `⌘/` and in [.claude/skills/grain/reference.md](.claude/skills/grain/reference.md).

## Privacy and your data

- Chats, notes, uploads, memories, the graph and settings live on your Mac in `~/Library/Application Support/personal-os/data`: one SQLite file plus folders for uploads, images, workspaces, backups and logs.
- API keys and Google tokens are kept in the macOS Keychain.
- What leaves your Mac: your messages and the context for each reply go to the model provider you chose. Web searches and page reads go to the sites involved. Google, Telegram and connector calls go to those services when you use them. Nothing else is sent anywhere.
- Usage and cost are logged locally.
- Back up or export everything from Settings → Advanced → Data.

## For developers

Grain is an Electron app (React and TypeScript) that starts a Python FastAPI backend on a free local port. State is one SQLite file. Models are reached through a LiteLLM proxy or a direct provider.

```
src/main/            Electron main: window, menu, backend sidecar
src/preload/         contextBridge
src/renderer/        React UI (store.ts holds state; lib/api.ts is the client)
src/shared/          Types and the shortcut table shared by both processes
backend/personal_os/ FastAPI app: app.py routes, repos.py storage, tools.py, context.py,
                     learn.py, jobs.py, telegram.py, and more
scripts/             dev.sh, litellm.sh, bundle-backend.sh, install-app.sh
litellm.yaml         Model routing (Fireworks by default)
tests/e2e/           Playwright end-to-end tests
```

```bash
npm run typecheck                          # TypeScript, both projects
npm test                                   # renderer and main unit tests (Node 20)
cd backend && .venv/bin/python -m pytest   # backend tests (install pytest first)
npm run package                            # build dist/mac-arm64/Grain.app
```

Run dev against its own data directory so it does not touch the packaged app's database. The commands are in [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md).

Where to read more:

- [AGENTS.md](AGENTS.md): get Grain running from nothing, including Google setup and traps.
- [CLAUDE.md](CLAUDE.md): repo rules for contributors and agents.
- [.claude/skills/grain/SKILL.md](.claude/skills/grain/SKILL.md): a map of the app, its HTTP API and test suites. [reference.md](.claude/skills/grain/reference.md) lists every tool and shortcut.
- [docs/releasing.md](docs/releasing.md): signing, packaging and publishing.
- [docs/](docs/): one file per feature (agents, connectors, spaces, permissions, workflows, health, telegram, voice input, docs editor, side panel, research mode, coding agents, and more). [docs/sources-of-truth.md](docs/sources-of-truth.md) and [docs/module-manifest.md](docs/module-manifest.md) cover the architecture.
- [docs/research/](docs/research/): research write-ups and the roadmap.
- [CHANGELOG.md](CHANGELOG.md): what shipped, and when.
