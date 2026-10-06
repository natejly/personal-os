---
name: grain
description: Using, launching, developing or testing the Grain desktop app: views, shortcuts, tools, data dir, dev vs packaged, e2e
---

# Grain

Grain is a personal AI assistant for the Mac desktop. Electron (React + TypeScript in `src/`) spawns a
FastAPI backend (`backend/personal_os/`) on a free port with one SQLite file as its state. The backend
talks to a model through a LiteLLM proxy or a direct provider. Depth lives in [README.md](../../../README.md)
and `docs/`; this file is the map. Long tables (all tools, shortcuts, slash commands) are in
[reference.md](reference.md).

Mental model:

- **Chats** run a tool loop. Tools read and write the app, Google, the Mac and a sandbox.
- **Memory and graph**: auto-learn saves memories and graph links after replies. **Projects** group chats with their own instructions, files and memories.
- **Files** (⌘4) holds notes you write and uploads. Settings → Memory (⌘6) reviews memory and the writing Voice profile.
- **Lists** (todos), **Calendar**, **Mail** and **Health** are apps at the top right of the title bar. Meetings and Activity ship hidden. Settings → Modules moves any view to the sidebar, the title bar or out of sight (`navPlacement`, `src/renderer/src/shell/nav.tsx`).
- **Spaces** (⌘⇧C): a desktop of live windows (chat, lists, calendar, note, memory, graph, uploads, recap, project, usage, activity, doc, face, crew). See [docs/spaces.md](../../../docs/spaces.md).
- **Library**: Agents, Automations (workflows and saved commands), Skills, Connectors (MCP), and what Grain made.
- **Autonomy**: a reply can hand work to subagents (`agent_spawn`). "Work autonomously" in any chat (beside plan mode; Plan first / Ask as it goes / Work and propose, with a turn limit) turns it into a longer session with its own plan and workspace: a strip above the composer (state, turns, Needs you, Start/Pause/Resume/Stop) opens a side panel with Files / Changes / Review; settings are under Settings → Autonomy. Workflows are approved once and run in waves ([docs/workflows.md](../../../docs/workflows.md), [docs/cowork-design.md](../../../docs/cowork-design.md), which is the original desk spec, partly out of date; the standalone Cowork view was folded into chats on 2026-10-05). Scheduled runs only propose; results land in the Agent inbox on Today.
- **Settings** (⌘,) tabs: Provider & cost, Tools, Memory, Behavior, Modules, Integrations, Meetings, Data.

## Using it

First run, as a user:

1. Launch. The setup wizard picks a model provider and key, then asks a few lines about you; those become a pinned memory.
2. Connect Google in Settings → Integrations. Calendar, Mail, Google Tasks sync and the first-prompt suggestions wait for it.
3. ⌘N for a chat. Model and effort pickers are under the composer.
4. Drop or paste a file to attach it; it rides on the message as a chip and its text is inlined for the model up to a size cap.
5. Set a **Working folder** on the chat if the assistant should touch files. With none set, file and shell tools work in `~/Grain` (`mac.py`).
6. ⌘4 Files: ⌘⇧N new note, ⌘⇧D today's note, ⌘U upload. ⌘I opens the Page agent, a chat bound to the open note.
7. Sidebar **+** next to Projects makes a project. Lists, Calendar and Mail are the title-bar icons (⌘2, ⌘3, ⌘5).
8. Spaces: ⌘⇧C, then Add widget or right-click the plane. Library for agents and automations.

Shortcuts that matter (full list in reference.md): ⌘N new chat, ⌘K command palette, ⌘I page agent, ⌘⇧C
Spaces, ⌘0 to ⌘7 views (Today, Chats, Lists, Calendar, Files, Mail, Memory, Activity), ⌘, settings,
⌘⇧P cycle plan mode, ⌘⇧F search chats.

Composer features:

- **Slash commands**: `/skill`, `/schedule`, `/loop`, `/compact`, `/skills`, `/commands`, `/plan` (table in reference.md).
- **Plan mode** cycles off, auto, always. The assistant then proposes a plan (`propose_plan`); you approve it once and each step runs bound to its approved arguments.
- **Approval card**: a tool in `ask` mode stops the reply until you approve or deny. The card offers allow once, always for this chat or globally, or a pattern rule.
- **Always-ask list** (`alwaysAsk` in `llm.py` `DEFAULT_SETTINGS`): `gmail_send`, `calendar_delete`, `trash_local_file`, `move_local_file`, `run_shortcut`, `python_install`, `schedule_task`. No setting or card turns these fully on.
- **Allow-host card**: when a reply that read the web wants to fetch another page, approve it or pick **Allow <host> from now on** (Settings → Tools → Allowed hosts, `fetchAllowlist`).
- **Side panel**: the `show` tool opens HTML, SVG, charts and files beside the chat ([docs/side-panel.md](../../../docs/side-panel.md)).
- Related docs: [docs/docs-editor.md](../../../docs/docs-editor.md), [docs/meetings.md](../../../docs/meetings.md), [docs/health.md](../../../docs/health.md), [docs/activity-monitor.md](../../../docs/activity-monitor.md), [docs/writing-style.md](../../../docs/writing-style.md).

Backups: Settings → Data keeps daily backups (newest 7 plus one a week), has **Back up now**, a restore
that applies on next start (`backups.apply_pending_restore`), and **Export all data** as a zip. Deleted
notes and todos go to the trash for 30 days (Settings → Trash).

## The assistant's tools

Each tool has a mode: `on` (runs), `ask` (card first), `off` (not offered). The effective mode resolves
chat override, then project override, then global `settings.tools`, then the tool's default
(`Toolbox.effective` in `tools.py`). The default comes from the danger tier: `safe`, `writes`, `network`,
`executes`, `external`, `schedules` default to `on`; `plan` defaults to `ask`. A few tools override that
(`shell_run`, `opencode_run`, `doc_delete` default to `ask`).

- **Locked tools**: an `external` or `schedules` tool listed in `alwaysAsk` tops out at `ask` whatever any map says. Other external tools (calendar create/update, Docs/Sheets writes, `gmail_draft`) run on a plain yes, but are still read back and verified.
- **Tainted replies**: once a reply has read untrusted content (web page, email, MCP result), network tools, locked tools and lasting-text writes (`PROMPT_WRITES`: memory, graph, notes, todos, skills, health) are forced to `ask`.
- **Deny rules win over allow rules** (`permissionRules` allow/ask/deny with argument patterns). `skipPermissions` skips ordinary `ask` cards but not plan cards, deny rules, desk questions or scheduled jobs.
- **Unattended runs** (scheduled jobs) cannot complete `external` or `schedules` calls; they are recorded as proposals for you to accept. An unattended approval is denied by default (`unattendedApprovals`).

Groups, by what they do (every tool, danger and default in reference.md):

| group | covers |
|---|---|
| knowledge, docs, memory, graph | search/read uploads and notes, edit notes (you accept a diff), long-term memory, graph |
| web, browser, vision | search, fetch, feeds, video and repo reads, a driven browser, image reading |
| google | Calendar (events, find time, propose), Gmail (search, draft, send with undo hold), Tasks, Drive, Docs, Sheets |
| todos, health, meetings, activity, style | lists, health data, recorded meetings, the activity monitor, writing voice |
| files, mac, shell, opencode, code | local files (home-scoped), shortcuts, shell in a working folder, coding agent, Python |
| sandbox | isolated Linux container tools (`sandbox_*`), needs a container runtime |
| agents, workflows, desk, schedule, skills, spaces | subagents, saved workflows, desks, scheduling, skill authoring, arranging Spaces |
| plan, utility, context | plans and questions, `show`, `current_time`, paged tool results |

## Operating it as a developer or agent

Read the repo `CLAUDE.md` first; its rules override this file.

- **Data dirs**: the packaged app uses `~/Library/Application Support/personal-os/data`; `./scripts/dev.sh` defaults to the same folder. Two backends on one SQLite file means two schedulers and dev's startup recovery marks the real app's live runs interrupted. Always give dev its own:

```bash
mkdir -p "$HOME/Library/Application Support/Grain-dev/data"
PERSONAL_OS_DATA_DIR="$HOME/Library/Application Support/Grain-dev/data" ./scripts/dev.sh > "$CLAUDE_JOB_DIR/tmp/dev.log" 2>&1 &
```

  Sign in to Google again in the dev window. Mail, calendar and task writes from dev still hit the real Google account. Use the real data dir only with the packaged app quit.
- **Never run Electron in the foreground** (`dev.sh`, `npm run dev`, the packaged app): it blocks until quit. Start it in the background with output to a log, poll `/health` (the port is `PERSONAL_OS_PORT`, default 8765 in dev.sh) and the log, and kill what you started. Packaged build: `open -g dist/mac-arm64/Grain.app` (`-g` keeps focus).
- **LiteLLM** on :4000 is shared: dev.sh starts it only when `FIREWORKS_API_KEY` is set and nothing answers on :4000, otherwise it reuses it. `.env` seeds first-launch settings; `litellm.yaml` lists models.
- **Logs**: `~/Library/Logs/Grain/` when packaged (`backend.log` inside), `<userData>/logs` in dev (`src/main/index.ts`). `PERSONAL_OS_LOG_DIR` overrides it.
- **Scripts** (`package.json`): `npm run typecheck`, `npm test` (esbuild + node:test on `*.test.ts`), `npm run build`, `npm run package` (see [docs/releasing.md](../../../docs/releasing.md)).
- **Backend tests** are script-style, one process per file. From `backend/`:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q -p no:cacheprovider tests/<file>.py
```

  Read the `inner totals:` line (printed by `backend/conftest.py`). A worktree without its own venv can use the main checkout's `backend/.venv/bin/python`.
- **End-to-end** (`tests/e2e/`, see its README): Playwright drives the built app against an isolated backend (own data dir and port, file-backed secrets) and a mock provider, so it cannot touch real data or the Keychain.

```bash
npm run build        # the harness loads out/main/index.js
node tests/e2e/node_modules/.bin/playwright test -c tests/e2e/playwright.config.mjs <spec-or-grep>
```

  `tests/e2e/setup-worktree.sh` links node_modules and the venv into a fresh worktree. The harness runs the Electron window in the background by default (`GRAIN_E2E_BACKGROUND`); set `E2E_FOREGROUND=1` to watch or debug. Other knobs: `E2E_LLM=real` (use the LiteLLM proxy, slow and paid), `E2E_KEEP=1`, `E2E_WORKERS=n`, `E2E_RETRIES=n`.
- **Mock provider directives** (`tests/e2e/mockllm.mjs`), put them in the prompt: `!!reply <text>` answers that text; `!!tool <name> <json args>` makes one tool call then answers "MOCK: tool done"; `!!slow <ms>` delays; `!!fail <status>` fails once. Anything else echoes `MOCK: <message>`.

## Driving the app programmatically

The backend takes any HTTP client. Every route except `/health` and the OAuth callbacks needs the token,
as `X-Personal-OS-Token: <token>` or `Authorization: Bearer <token>`. The token is `PERSONAL_OS_AUTH_TOKEN` if set,
otherwise the contents of `<data dir>/.auth_token` (0600), so an agent can read it from the data dir. Dev
uses port `PERSONAL_OS_PORT` (default 8765); the packaged app picks a free port. Interactive API docs are at `/api-docs` (not `/docs`, which is the user's notes).

Routes (verified in `backend/personal_os/app.py` and `modules/todos.py`):

| route | use |
|---|---|
| `GET /health` | `{"ok": true}`, no token; poll it for readiness |
| `GET/PUT /settings` | read or patch settings (PUT a partial object) |
| `GET /tools` | every tool with group, danger, `default_mode`, `ask_locked`, `available` |
| `GET/POST /conversations` | list, or create `{title, model, project_id, private}` |
| `POST /conversations/{id}/chat` | body `{content, model?, attachments?}`; starts the reply as a background run |
| `GET /conversations/{id}/stream?since=<seq>` | SSE tail of the run; reattach any time with the last seq |
| `POST /conversations/{id}/stop`, `/steer` | stop a run, or add a message mid-run |
| `GET /approvals`, `POST /approvals/{call_id}` | pending cards; decide with `{decision: allow, deny, always_chat, always_global, always_session, always_rule, allow_host}` |
| `GET/POST /docs`, `GET/PUT/PATCH/DELETE /docs/{id}` | Files notes |
| `GET/POST /todos`, `PUT/DELETE /todos/{id}` | todos |
| `GET /memories`, `GET/POST /projects`, `GET /runs`, `GET /events` | memory, projects, run list, app-wide SSE |
| `GET/POST /workflows`, `POST /workflows/{id}/runs` | workflows and run proposals |

Example, from the e2e harness `api()` (`tests/e2e/harness.mjs`): `fetch(url + path, {headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}, ...})`, parse JSON, throw on status >= 400. A minimal exchange: `POST /conversations` for an id, `POST /conversations/{id}/chat`, then read `GET /conversations/{id}/stream` until it ends.

## Rules to keep

- **No other product names** in code, comments, UI copy, docs, tests, commit messages or PR text. Describe the mechanism ("deny rules win over allow rules"). Only `docs/research/` is exempt, and real integrations (Google Calendar, Gmail, LiteLLM, MCP, Docker) are fine.
- **External writes are verified and undoable where an undo exists**: every write is read back, an unverified write is an error and never reported as success, outgoing mail is held about 90 s for Undo, and an agent cannot Send now.
- **Never point dev at the real data dir while the packaged app runs.** A backend reload drops in-flight runs.
- Scheduled runs only propose: do not build a path that lets one complete an external write or book more runs.
- Commits: one-line subject in the repo's style; do not push or merge unless asked.
