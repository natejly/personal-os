# Personal OS

A personal AI operating system for your desktop. Chat with any model through
[LiteLLM](https://docs.litellm.ai/) (Fireworks AI out of the box), give the
assistant tools, and let it build memory and a knowledge graph about you as you
go. Organise work into **projects** the way Claude does: groups of chats with
their own instructions, knowledge files, memories and graph.

```
┌──────────────┬──────────────────────────────────────┬──────────────┐
│ + New chat   │  Today · Monday, September 29        │  Context     │
│ Today        │  ┌ Calendar ─────┐ ┌ Todos ────────┐ │  ☑ Memory    │
│ Todos      3 │  │ 10:00 Standup │ │ ○ Ship v0.1   │ │  ☑ Graph     │
│ Memory    19 │  │ 14:00 1:1     │ │ ○ USB-C hub   │ │  ☑ Documents │
│ Documents  1 │  └───────────────┘ └───────────────┘ │  ☑ Auto-learn│
│ PROJECTS   + │  ┌ Inbox ────────┐ ┌ Projects ─────┐ │  ☑ Tools  ▾  │
│ ■ Personal OS│  │ Alice: Q4 …   │ │ ■ Personal OS │ │   web search │
│ RECENTS      │  └───────────────┘ └───────────────┘ │   run python │
│ · …          │                                      │   gmail send │
│ · …          │  [Brief me]                          │  Last reply… │
└──────────────┴──────────────────────────────────────┴──────────────┘
```

## Features

- **Provider-agnostic chat.** Streaming replies from any model LiteLLM routes to,
  one key. Per-chat model picker. Markdown, code copy, regenerate, stop.
- **Tools with permissions.** The assistant can search your documents, search
  and save memory, traverse and extend the knowledge graph, search the web and
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
- **Memory.** One panel holding both halves of what the app remembers, over a
  shared scope filter and search box, as a split view or either half alone:
  - *Memories* — facts, preferences and goals, auto-extracted after each reply
    or added by hand or by the assistant. Edit, pin, move between personal and
    project scope, forget.
  - *Knowledge graph* — entities and relations, auto-extracted and
    hand-editable in a force-directed view. Relevant subgraphs are injected
    into chats.
- **Documents.** Upload `.txt/.md/.pdf/.docx` and code files. Chunked,
  full-text indexed, best excerpts pulled into replies.
- **Context management.** Per-chat toggles for memory, graph, documents,
  auto-learn and tools; an inspector showing exactly what was injected into
  each reply; a live preview for a draft message.
- **Charts and diagrams.** Replies can include a ```` ```chart ```` block (a small
  JSON spec rendered as an interactive bar / line / area / pie / scatter chart,
  each with chart, data-table and source views) or a ```` ```mermaid ```` block.
  The Python sandbox has numpy and matplotlib, and any figure a script saves is
  shown inline on the tool card.
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
- **Dashboards you describe.** Register data sources (an HTTP API with an API
  key, an RSS feed, or your own todos/calendar/mail), then describe a widget in
  plain English. The model writes a self-contained HTML widget that runs in a
  sandboxed iframe and fetches data through the backend (keys never reach the
  widget). "AI summary" widgets turn any source into a short briefing. Revise a
  widget by telling it what to change.
- **Google Workspace.** Sign in once with your own OAuth client; calendar,
  Gmail and Tasks become dashboard widgets and assistant tools.

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
app's user-data folder. All state is one SQLite file plus an `uploads/` folder.
In development, `scripts/dev.sh` runs LiteLLM, the backend (autoreload) and
Electron (HMR) together.

## Setup

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
   create a project and enable the **Calendar**, **Gmail** and **Tasks** APIs.
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

Scopes: `calendar`, `gmail.modify`, `tasks`, `email`. Tokens live in the local
database. `gmail_send` is a separate tool you can keep off; `gmail_draft` never
sends.

When a token is revoked, expires (a "Testing" consent screen kills refresh
tokens after 7 days) or is missing a permission that was unticked on the consent
screen, Integrations shows why and offers **Reconnect** instead of failing the
next calendar or mail call with an opaque error.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| ⌘N | New chat |
| ⌘0 … ⌘6, ⌘8 | Today / Chats / Todos / Calendar / Boards / Dashboards / Memory / Documents |
| ⌘7 | Memory, opened on the knowledge graph |
| ⌘B | Toggle sidebar |
| ⌘I | Toggle context panel |
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
   the user is never a graph entity.

Everything used is stored on the assistant message (`context_used`,
`tool_events`) and shown in the Context panel.

## Charts, diagrams and images

Rich output is a fenced code block the UI knows how to render, so it works with
any model and streams naturally. The system prompt describes two block types:

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
Diagrams use ```` ```mermaid ```` (loaded lazily, so it costs nothing until
used). For anything those cannot express, `run_python` has numpy and matplotlib;
figures saved with `plt.savefig()` come back as images attached to the tool
call and are stored with the message.

## Traces

Each assistant message carries a `trace`: spans of kind `context`, `llm`, `tool`
and `learn`, each with start and end times and metadata such as token usage,
time to first token, finish reason, tool arguments and result sizes. Spans are
streamed as `span` SSE events while the reply is generated, so the Trace tab in
the Context panel (⌘I) fills in live. The chip under a finished reply
(`4 steps · 6.1 s · 3.7k tok`) opens that reply's trace.

## Usage and cost

Every model call, including auto-learn extraction, appends a row to `usage_log`
with its model, kind, conversation, project, token counts, latency and cost.
Prices are read from the LiteLLM proxy's `/model/info` and cached; anything the
proxy does not price can be set by hand in Settings, which re-prices the whole
history. When a provider does not return a usage block, tokens are estimated
from character counts and the row is flagged `estimated`.

## Sandbox

`run_python` executes scripts with `python -I` in a throwaway directory, with
CPU, memory and wall-clock limits, wrapped in macOS `sandbox-exec` with a
profile that denies network and writes outside the work directory. Reads are
allowed so scripts can analyse your local files.

## Layout

```
src/main/           Electron main: window, menu, backend sidecar, .env loader
src/preload/        contextBridge (backend URL, menu events)
src/renderer/       React UI (store.ts holds all state; lib/api.ts is the client)
src/shared/         Types shared between processes
backend/personal_os app.py routes · repos.py storage · context.py · learn.py
                    tools.py · sandbox.py · google.py · todos.py · boards.py
                    dashboards.py · usage.py · trace.py · llm.py
scripts/dev.sh      LiteLLM + backend + Electron
scripts/litellm.sh  LiteLLM proxy alone
litellm.yaml        Model routing (Fireworks by default)
docs/research.md    Feature research and roadmap
```

## Roadmap

See [docs/research.md](docs/research.md) for the researched roadmap across
memory, retrieval, app features, life-OS features and agentic capabilities,
with the per-track source reports under [docs/research/](docs/research/).

**Read Track 5 first.** It documents four live security defects in the current
tool defaults — the stock permission modes let an injected web page or email
read the Fireworks key and the whole database and exfiltrate them without ever
showing an approval card — and it establishes that durable runs (an agent run
that survives its HTTP connection) are the shared prerequisite for the
approval queue, the scheduled brief, scheduled tasks and the session tape.

Near-term after those: budgets replacing the fixed tool-round cap, taint
tracking and an undo journal, tool-use examples and result pagination, skills,
an agent inbox on Today, and scheduled jobs. Later: MCP client, code mode and
file/browser reach — after an eval harness exists.
