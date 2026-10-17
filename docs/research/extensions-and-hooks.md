# Extensions and hooks: a small core and everything else as extensions

Status: design and research proposal, 2026-10-14. Nothing here is built. File and line references are against `main` at `e8263a46` unless marked otherwise; the lean-default work is on the unmerged branch `feat/default-off-views` (commit `0cb17f37`).

This file is under `docs/research/`, where comparative write-ups may name their sources. Sections 3 to 7 describe the proposed API in terms of mechanisms only. External systems are named in the "equivalents" columns, section 7's comparison tables and section 12.

## 1. Summary

**Thesis.** Grain is two things fused together: a small agent harness (turn loop, tools, permissions, context assembly, runs, persistence) and about forty features that grew on top of it (mail, calendar, lists, health, Telegram, notes, spaces, dashboards, coding sessions). The features reach into the loop by name. `app.py` is 10,311 lines and imports about 120 modules; `_chat_stream` is a single 2,200-line generator that hard-codes retrieval sources, prompt hints, four post-turn learners and the gate chain. Making the harness smaller is not mainly a deletion exercise. It means giving every feature the same small set of doors: registrations (tools, routes, views, slots) and lifecycle hooks (named points where a handler may observe, add context, or narrow what happens).

**What changes.**
1. A fixed **core** keeps the loop, `Toolbox`, the permission stack, context assembly, runs/SSE, persistence and the MCP client. It stays in-process and never loads user code.
2. Every other feature becomes an **extension**: a folder with a manifest, a Python half and an optional TypeScript half. Built-ins ship in the app bundle, and each has an `enabled()` default (the seed already exists as `llm.view_on()` / `Module.enabled()` on `feat/default-off-views`).
3. A **hook bus** replaces the ad hoc seams that exist today (`RunBus.after_hooks`, `llm.on_usage`, `Docs.on_chunks`, `Workspace.on_save`, about 25 `@app.on_event` handlers).
4. The frontend gets **open registries and fixed slots** (sidebar sections, composer toolbar, side-panel tabs, settings panels, message-footer chips, palette commands) instead of the closed `View` and `WidgetKind` unions and hard-coded `switch` statements.
5. Extensions can **customize the app**, not only add features: design tokens, layout, keybindings, renderers, default overrides, and "profile" extensions that contain no code. Authoring has tooling (`grain ext new/dev/check/test`), and Grain can write its own extensions through a skill, landing them disabled in Review.

**Two findings that shape the plan.**
- There are **three separate tool loops** (chat `_chat_stream`, `Subagents._loop/_exec`, `Engine._tool_step`) that share only `Toolbox.call`. A tool hook implemented in `_chat_stream` will not fire for workers, jobs-as-subagents or workflows. MCP connector calls do not go through `Toolbox.call` at all (`_mcp_call`, `_exec_mcp`). Tool hooks must live at a new shared dispatch seam, or the loops must be unified first.
- The system-prompt stable prefix must stay byte-identical turn to turn for provider caching. Context hooks therefore declare `stable` or `volatile`, and stable contributions must be deterministic.

**Recommendation.** Built-in extensions first, third-party loading later. Port order: Health, Todos, Telegram, recap/context hooks plus the open frontend registry, split `google.py` into a service extension, Calendar, Mail, Spaces. About 14 to 16 sequential PRs, sequential because they all edit `app.py`. A green test baseline comes first. Third-party extensions reuse the MCP connector trust model (the strongest in the codebase): namespaced names, schema-hash approval, drift quarantine, taint, default ask. Gating hooks fail toward asking, never open.

## 2. What the core is, exactly

### 2.1 Core (cannot be removed without Grain ceasing to be an agent harness)

| Piece | Owns | Where |
|---|---|---|
| Turn loop | `_chat_stream` (steering, stop, compaction trigger, stuck/loop breakers, plan re-injection, tool rounds, finish), `_run_chat`, `_run_desk`, `_run_job` wrappers | `app.py:1982`, `4151`, `4209`, `4190` |
| Model layer | `llm.stream_chat/complete`, provider routing, tier models, heuristic router, retries and auth breaker | `llm.py:1196`, `provider_route.py`, `providers.py:84`, `router.py` |
| Toolbox | `ToolSpec` (danger, group, `force_ask`, `taint_ok`, `available_fn`), mode resolution, `available()`, `call/_call`, write-verification gate `checked` | `tools.py:152`, `1087`, `1096`, `343` |
| Permission stack | tool modes at four scopes, rules, taint, plans, approvals, review gate, floors | `permissions.py`, `permrules.py:1280`, `autoreview.py:43`, `plans.py`, `approval_log.py` |
| Context assembly | `build_context` (stable and volatile parts), `layout_messages`, `context_map`, compaction, `tool_results` handles | `context.py:247`, `231`, `compaction.py`, `working.py:253` |
| Runs and transport | `RunBus/Run/RunStore`, journal tables `agent_runs`, `run_events`, `approvals`, `executed_calls`, per-run SSE and app-wide `Topic` | `runs.py:732`, `938`, `979`; `app.py:4850`, `4868` |
| Persistence | SQLite, migration ledger, `Conversations/Projects/Memories/Documents` repos | `db.py`, `migrations.py:759`, `repos.py` |
| Subagents and workers | the second loop; `delegate`, wake turns, root locks | `subagents.py:1029`, `workers.py`, `app.py:4609` |
| MCP client | connectors as the runtime-added tool source, drift and eval | `mcp_client.py`, `mcp_servers.py`, `mcp_drift.py` |
| Commands | slash-command expansion and registry (`/compact`, `/research` and user commands) | `commands.py` |
| Projects (scope) | scope object used by memory, permissions and context | `repos.py` projects tables |
| Onboarding | first-run provider setup | `setup.py`, `setup_cards.py` |
| Frontend host | Electron main, preload, IPC, chat view, composer, message renderer, sidebar host, settings host, command palette, shortcut table | `src/main/*`, `src/renderer/src/components/ChatView.tsx`, `store.ts`, `src/shared/shortcuts.ts` |

### 2.2 Core services (always present, no UI of their own, extensions call them)

| Service | What it offers | Where |
|---|---|---|
| Storage, secrets, logs, backups, blobs, retention | Per-extension data dir, Keychain secrets, backup include list | `db.py`, `secrets.py`, `backups.py`, `blobs.py` |
| Redaction | `scrub_command_output` on every rendered result | `redact.py` |
| Sandbox: shell, Python, file tools | the only exec and filesystem path; Seatbelt profile and network egress allowlist | `shell.py`, `sandbox.py`, `fsx.py`, `egress.py` |
| Mac platform layer | `mac`, `macos`, screenshot (imported by about 20 modules) | `mac.py`, `macos.py` |
| Retrieval | chunk, embed, hybrid search, rerank, citations; becomes a registry of search sources | `retrieval.py`, `embed.py`, `chunker.py` |
| Usage and trace | `llm.on_usage`, spans, OTLP export | `usage.py`, `trace.py`, `otel_export.py` |
| Scheduler | cron/once/watch dispatch, catch-up, power wake; trigger kinds become a registry | `jobs.py:1058`, `jobs_policy.py` |
| Uploads and text extraction | attachments, `extract_text` | `chat_files.py`, `extract_text.py` |
| Inbox and attention | the shared "needs you" surface for approvals and proposals | `runs.py`, `attention.py` |
| Trash | soft delete; per-type restore handlers (today it imports docs, todos, mcp_client directly) | `trash.py` |
| Web read and search | basic tool group other extensions depend on | `websearch.py`, `webread.py` |
| Terminal hub | Electron-main PTYs (`src/main/terminals.ts`, `src/shared/terminal.ts`), reached by the Terminal card and, from PR 17, by a token-authenticated backend bridge | `terminals.ts`, `terminal.ts` |
| Service registry (new) | named services an extension can provide or require (`google`, `mac`, `usage`) | new |

### 2.3 What the core does not do

- It does not know any feature's tables, tools, routes, views or prompt text. `migrations.py` currently imports 11 feature schemas; that goes away.
- It does not call Google, Gmail, Calendar, Tasks, Telegram, a health provider or a coding CLI.
- It does not own a feature's Today card, recap section, canvas widget or settings panel.
- It does not load third-party code in this phase, and never loads code into the permission stack.
- It does not let any hook widen a decision. Hooks may observe, add context, narrow (ask or deny), or transform data. Allowing something is done only by the user through the existing grant and rule paths.

### 2.4 One message through the loop, with hook points

Hook ids match section 5. Dashed boxes are the `U` hooks: they depend on the tool-path seam (section 5.0).

```mermaid
flowchart TD
  A["POST /conversations/{id}/chat<br/>app.py:4546"] --> H1{{"turn.accept"}}
  H1 --> B["bus.start -> _run_chat<br/>runs.py:938"]
  B --> C["load conv, pick model<br/>app.py:2001-2018"]
  C --> H2{{"model.select"}}
  H2 --> D["persist user row<br/>app.py:2069"]
  D --> H3{{"turn.start"}}
  H3 --> E["retrieval: docs, memory, graph<br/>app.py:2136"]
  E --> H4{{"context.retrieve"}}
  H4 --> F["build_context: stable + volatile<br/>context.py:247"]
  F --> H5{{"context.contribute"}}
  H5 --> G["compaction check<br/>app.py:2158"]
  G --> H6{{"compaction.before / after"}}
  H6 --> I["tool schemas offered<br/>app.py:2450"]
  I --> H7{{"tools.offer"}}
  H7 --> J["round loop: llm.stream_chat<br/>app.py:3026"]
  J --> H8{{"model.request / model.response"}}
  H8 --> K{"tool calls?"}
  K -- no --> R["finish message, yield done<br/>app.py:4062"]
  K -- yes --> L["gate chain: modes, rules, taint, review<br/>app.py:3340-3563"]
  L --> H9{{"tool.permission (narrow only)"}}
  H9 --> M["approval wait if asked<br/>app.py:3593"]
  M --> H10{{"approval.requested / resolved"}}
  H10 --> N["dispatch: Toolbox.call / _mcp_call"]
  N --> H11[/"tool.pre (U)"/]
  H11 --> O["tool body, checked()<br/>tools.py:1096"]
  O --> H12[/"tool.post, tool.result (U)"/]
  H12 --> P["render for model, fence, taint<br/>app.py:3951"]
  P --> J
  R --> H13{{"reply.complete"}}
  H13 --> S["after-run: learners, followups, title<br/>app.py:4087-4148"]
  S --> H14{{"turn.settled"}}
  H14 --> T["bus.after_hooks, run.end<br/>runs.py:979"]
  T --> H15{{"run.after"}}
```

The two sibling loops (subagents, workflows) skip most of the left column but must still hit `tool.pre/post/result` because the dispatch seam is shared. That is the reason the seam exists.

## 3. Extension model

### 3.1 Manifest

One file, `extension.json`, read by both halves. JSON because Python and TypeScript both parse it with no dependency. Unknown top-level keys are rejected, not stripped, so typos fail loudly.

| Field | Type | Meaning |
|---|---|---|
| `id` | string, `[a-z0-9-]+` | Stable identity. Namespaces tools, settings, routes, tables, events. |
| `name`, `description` | string | Display. |
| `version` | semver | Extension version. |
| `apiVersion` | integer | Host API major it targets. Host refuses a higher value and warns on a lower one past the deprecation window. |
| `kind` | `"extension"` or `"profile"` | A profile has no code files; only `contributes.theme/defaults/layout`. |
| `tier` | derived, not declared | `builtin`, `user`, `thirdparty`. Set by where the folder lives and how it arrived. |
| `enabled` | `"always"`, `true`, `false` | Default for a fresh install. Existing installs keep their current state (the rule on `feat/default-off-views`). |
| `activation` | `["startup"]` or any of `onView:<id>`, `onCommand:<id>`, `onChannel:<id>`, `onSchedule` | When the code half loads. Declared contributions exist before the code does. |
| `requires` | `{ services: [...], extensions: [...] }` | e.g. Calendar requires service `google`. Start order is a topological sort. |
| `provides` | `{ services: [...] }` | e.g. the Google extension provides `google`. |
| `contributes` | object | Declarative registrations (section 4): tools with schemas, routes prefix, views, slots, settings panel, commands, keybindings, theme, defaults. |
| `hooks` | `{ "<hook.name>": { priority?, timeoutMs?, onFailure? } }` | Every hook the code may subscribe to. Subscribing to an undeclared hook is an error. |
| `permissions` | string list | Capabilities, section 7. |
| `settings` | JSON Schema subset | Typed settings. Values live under `settings.ext.<id>.*`; secret-typed fields go to the Keychain, never to the settings table. |
| `data` | `{ tables: [...], migrations: "migrations/" }` | Tables the extension owns (backup include list) and ordered migrations. |

Example (Health, abridged):

```json
{
  "id": "health",
  "name": "Health",
  "version": "1.0.0",
  "apiVersion": 1,
  "kind": "extension",
  "enabled": false,
  "activation": ["startup"],
  "requires": { "services": ["mcp"] },
  "contributes": {
    "tools": [{ "name": "health_log", "danger": "writes", "group": "health", "schema": "tools/health_log.json" }],
    "routes": { "prefix": "/health" },
    "views": [{ "id": "health", "label": "Health", "icon": "heart" }],
    "slots": { "sidebar.row": [{ "view": "health", "order": 20 }],
               "recap.section": [{ "id": "health" }] },
    "settings": { "panel": "settings/HealthPanel" }
  },
  "hooks": { "app.start": {}, "app.stop": {}, "context.contribute": { "timeoutMs": 300 } },
  "permissions": ["db:own", "service:mcp", "context:volatile"],
  "data": { "tables": ["health_days", "health_goals"], "migrations": "migrations/" }
}
```

### 3.2 Folder layout

```
extensions/<id>/
  extension.json
  extension.py          # backend half: def activate(api: Extension) -> None
  extension.tsx         # frontend half: export function activate(ui: UiApi): void
  migrations/0001_init.sql
  tools/*.json          # static tool schemas
  settings/*.tsx
  tests/test_*.py
```

Built-ins live at `backend/personal_os/extensions/<id>/` and `src/renderer/src/extensions/<id>/` (both halves in one logical extension, joined by `id`). User and third-party extensions live at `<data>/extensions/<id>/` with a prebuilt `dist/extension.js` for the renderer half. Per-extension persistent data goes to `<data>/ext/<id>/`, kept separate from the code folder so an update never overwrites state.

### 3.3 Lifecycle

| State | What happens | Notes |
|---|---|---|
| Discovered | Scan built-in dir and user dir. Read manifests only. | No code runs. |
| Validated | Schema check, `apiVersion`, id collision, tool-name collision against the live registry, `requires` resolvable, hook names exist, permissions known. | Fail = listed as "invalid" with the reason; never partially loaded. |
| Disabled / Enabled | From the extension's setting `ext.<id>.enabled`, else manifest default. Third-party default is disabled until the user approves its contribution and permission summary. | `enabled()` is what `Toolbox.available()`, loops and `/dashboard` consult. |
| Registered (declarative) | Manifest contributions go into registries: tool names and schemas, views, slots, palette commands. | The UI and the model-visible tool list are complete before any code runs. |
| Activated | The code half is imported on its activation event; `activate(api)` runs. | Must not start tasks or I/O at import time. Loops start in `api.on_start`. |
| Deactivated | `deactivate` hook, then every disposable registered through `api` is disposed in reverse order; spawned tasks cancelled and awaited. | A reload is deactivate plus activate with a fresh module namespace. |

Auto-disposal is the central rule: every `api.register_*`, `api.on(...)`, `api.spawn(...)` and `ui.register_*` returns a `Disposable` and is also appended to the extension's scope, so an extension author who forgets to unregister cannot leak. Tools are the one exception to "remove on disable": a tool already offered in a running turn stays callable until the turn ends, then disappears from the next schema list (the append-only tool order noted at `app.py:2497-2503` is preserved by hiding rather than reordering).

### 3.4 Backend API object (Python sketch)

Extensions import only from `grain_sdk`, never from `personal_os.app`. The `check` command (section 6.2) enforces this by AST scan.

```python
class Extension(Protocol):
    id: str
    log: Logger
    data_dir: Path                      # <data>/ext/<id>/
    db: ScopedDb                        # only tables listed in manifest data.tables
    settings: Settings                  # typed by the manifest schema; .get(), .on_change(fn)
    secrets: Secrets                    # Keychain, scoped to this id
    services: Services                  # .get("google") -> proxy; raises if not in requires

    # registrations (all return Disposable and are auto-disposed)
    def tool(self, spec: ToolDecl, fn: Callable[[ToolCtx, ...], Awaitable[Any]]) -> Disposable: ...
    def router(self, router: APIRouter) -> Disposable: ...          # mounted under /ext/<id>/ (built-ins may keep legacy prefixes)
    def command(self, name: str, fn: CommandFn) -> Disposable: ...  # slash command
    def context(self, fn: ContextFn, *, slot: Literal["stable","volatile","tail"], priority: int = 50,
                share: float | None = None) -> Disposable: ...
    def retrieval_source(self, name: str, fn: SearchFn) -> Disposable: ...
    def channel(self, adapter: ChannelAdapter) -> Disposable: ...
    def trigger(self, kind: str, adapter: TriggerAdapter) -> Disposable: ...
    def job(self, name: str, every: timedelta, fn: JobFn) -> Disposable: ...
    def provide(self, service: str, impl: object) -> Disposable: ...
    def precheck(self, fn: Callable[[str, dict, dict], str | None]) -> Disposable: ...   # PR 17: refusal text before any card; narrow only
    def directive(self, fn: Callable[[dict], str], *, version: int) -> Disposable: ...  # PR 17: built-in only; unwrapped stable prompt text

    # hooks
    def on(self, hook: str, fn: HookFn, *, priority: int | None = None, timeout_ms: int | None = None) -> Disposable: ...

    # runtime
    def spawn(self, coro: Awaitable[Any]) -> Task: ...             # tracked; cancelled on deactivate
    def emit_ui(self, name: str, data: Any) -> None: ...           # app-topic event "ext:<id>:<name>"
    def notify(self, title: str, body: str, *, target: Target | None = None) -> None: ...
    def inbox(self, item: InboxItem) -> None: ...                   # "needs you" card
```

A hook handler receives a read-only, redacted snapshot and returns a typed result:

```python
@api.on("tool.pre")
async def guard(ev: ToolPre, ctx: HookCtx) -> ToolPreResult | None:
    if ev.name == "shell_run" and "curl" in ev.args.get("command", ""):
        return Deny("network fetches through shell are off in this profile")   # narrowing only
    return None                                                              # no opinion
```

`HookCtx` carries `run_id`, `conversation_id`, `project_id`, `agent_id`, `tainted`, the stop event, and `via_hook` depth. It is the typed face of today's `tool_ctx` dict (`app.py:2221-2244`), exposing a stable subset instead of the dict.

### 3.5 Frontend API object (TypeScript sketch)

```ts
export interface UiApi {
  id: string
  // registrations; each returns a Disposable and is auto-disposed
  view(def: { id: string; label: string; icon: IconName; Component: FC }): Disposable
  slot<K extends SlotName>(slot: K, item: SlotItem<K>): Disposable
  command(def: { id: string; label: string; hint?: string; run: () => void }): Disposable
  keybinding(def: { id: string; keys: string; when?: string; run: () => void }): Disposable
  settingsPanel(def: { id: string; label: string; Component: FC }): Disposable
  toolCard(toolName: string, Component: FC<ToolCardProps>): Disposable
  fenceRenderer(lang: string, Component: FC<{ code: string }>): Disposable
  selectionAction(def: { id: string; label: string; run: (text: string) => void }): Disposable
  widget(def: WidgetDef): Disposable
  theme(tokens: Partial<DesignTokens>, opts?: { scheme?: 'light' | 'dark' }): Disposable

  // state and I/O
  store: ExtStore                         // per-extension zustand slice, namespaced, persisted by key
  on(event: `ext:${string}` | HostEvent, fn: (data: unknown) => void): Disposable
  call<T>(path: string, init?: RequestInit): Promise<T>   // fetch to /ext/<id>/..., token attached
  notify(title: string, body: string): void
  settings: { get<T>(key: string): T; onChange(fn: () => void): Disposable }
}

export function activate(ui: UiApi): void { /* ... */ }
```

Two frontend openings are prerequisites, because today's design blocks them: `View` becomes `BuiltinView | \`ext:${string}\`` (the closed union at `store.ts:42`), and the per-run reducer and app-topic handler (`store.ts:905-964`, `1305-1360`) gain a pass-through for `ext:<id>:<name>` events that writes into the extension's store slice. Unknown events are silently dropped today, so this is additive.

## 4. Contribution types

"Exists today" is judged against `main`. Legend: B backend, F frontend.

| Contribution | B/F | Signature sketch | Exists today? | Notes |
|---|---|---|---|---|
| Tool | B | `api.tool(ToolDecl, fn)`; schema in manifest | Yes: `Module.register_tools(box)`, `ToolSpec` (`tools.py:152`) | Names derived into the reserved set; replaces hand-maintained `RESERVED_TOOL_NAMES` (`mcp_servers.py:44`). |
| Tool card | F | `ui.toolCard(name, Component)` | Yes: `registerToolCard` (`toolcards/registry.ts`) | Registration is a static import list; make it dynamic. Icons and verbs maps (`ToolEvents.tsx`, `lib/toolDisplay.ts`) need a field on the registration. |
| HTTP routes | B | `api.router(APIRouter)` | Yes: `Module.router()` | Mounted via one dynamic dispatcher so a disabled extension 404s and a reload swaps handlers (FastAPI cannot unmount a router). |
| Background loop / interval job | B | `on("app.start")` plus `api.spawn`; `api.job(name, every, fn)` | Yes: `Module.start/stop` | `job` is a declared interval, visible in the dev panel. |
| View | F | `ui.view({id,label,icon,Component})` | Partial: `ModuleDef.view` | Needs open `View` type. |
| Nav / sidebar row, badge | F | slot `sidebar.row` | Partial: `ModuleNav {order,badge}` (`shell/types.ts`) | Sections (Spaces, Projects, chats) are hard-coded in `Sidebar.tsx`. |
| Canvas widget | F | `ui.widget(WidgetDef)` | Partial: `ModuleDef.widget` | `WidgetKind` is a closed union (`shared/types.ts:2288`). |
| Settings panel | F | `ui.settingsPanel(...)` | No | `SettingsModal.tsx:28-39` `TABS` hard-coded. Panel stores to `settings.ext.<id>`. |
| Settings keys | B | manifest `settings` schema | No | Whitelist is `llm.DEFAULT_SETTINGS` (`app.py:995`); unknown keys are dropped. Add a namespaced prefix. |
| Slash command | B/F | `api.command(name, fn)` | Partial: built-ins in `slashCommands.ts` plus `commands.py` | Built-ins (`/research`, `/schedule`, `/compact`) become registrations. |
| Palette command | F | `ui.command(...)` | No (inline array in `CommandPalette.tsx:11-52`) | `Entry {key,label,hint,run}` is already registry-shaped. |
| Keybinding / menu item | F | `ui.keybinding(...)` | Partial: `SHORTCUTS` table (`src/shared/shortcuts.ts`), menu built in `src/main/index.ts` | Menu layout and the `onMenu` if/else chain (`store.ts:1017`) must become table-driven. |
| Context contributor | B | `api.context(fn, slot, priority, share)` | No: hard-wired in `build_context` and the `hints` tuple (`app.py:2560-2574`) | The strongest new door. Section 5.3. |
| Retrieval source | B | `api.retrieval_source(name, fn)` | No: docs, memory, graph hard-wired at `app.py:2136-2139` | Yields `{id, text, ref}`; citations flow through `cite_ref`. |
| Recap section / new-chat card | B/F | slot `recap.section` | Partial: `Module.today()` feeds `/dashboard`; recap facts built at `app.py:8178` | The Today view was removed (git `d23e9f8a`), so `docs/module-manifest.md` `home` is stale; recap is the surviving surface. |
| Message footer chip, header chip | F | slot `message.footer` | No (reply footer chips hard-coded in `Message.tsx`) | |
| Composer toolbar item | F | slot `composer.toolbar` | No | Mic, duel controls, mode buttons today. |
| Side-panel tab | F | slot `sidepanel.tab` | No (`DeskPanel.tsx:15-16` fixed `Tab` union) | |
| Fence / markdown renderer | F | `ui.fenceRenderer(lang, C)` | No (`if (lang === ...)` chain, `MarkdownPreview.tsx:133-140`) | The model must be told supported fences: renderers also contribute a prompt line (stable context). |
| Selection action | F | `ui.selectionAction(...)` | No (`selectionActions.ts`) | |
| Theme / design tokens | F | `ui.theme(tokens)` | Partial: 78 CSS custom properties in `styles.css`, no theme API | Section 6.1. |
| Channel adapter | B | `api.channel(ChannelAdapter)` | No (Telegram is hard-wired: `_telegram_turn` `app.py:10172`) | Inbound to `ChatIn`, outbound from `done`, approvals, file send. |
| Trigger kind (scheduler) | B | `api.trigger(kind, adapter)` | No (closed kind set in `jobs.py`) | Mail and calendar triggers become provided by those extensions. |
| Service | B | `api.provide("google", impl)` | No | Replaces module-level imports of `google`. |
| Model provider | B | `api.provider(...)` | No (`provider_route.py` fixed) | Later; alternative routers use `model.select` first. |
| Inbox item | B | `api.inbox(item)` | Partial: proposals and approvals only | |
| Migration / owned tables | B | manifest `data` | No (tables in each store's `SCHEMA`; `migrations.py` imports 11 features) | Ledger id `ext:<id>:<n>` in `schema_migrations`. |
| Trash restore handler | B | `api.trash_handler(kind, fn)` | No (trash imports docs, todos, mcp_client) | |
| Precheck refusal | B | `api.precheck(fn)` | Partial: `Toolbox.precheck` with `coding_route` hard-wired (`tools.py:1088`) | Returns a refusal string only; cannot approve. Pairs with a `tool.pre` gate (section 10.5). |
| Prompt directive | B | `api.directive(fn, version=)` | Partial: `coding_route.hint` in the `hints` tuple (`app.py:2609`) | Built-in tier only; unwrapped text at a fixed position so the stable prefix stays byte-identical. |
| Defaults override | B/F | manifest `contributes.defaults` | Partial: first-boot `hiddenViews` seed on `feat/default-off-views` | Section 6.1. |

## 5. Hooks by phase

### 5.0 Conventions

**Hook kinds.** Four call shapes, chosen per hook:

| Kind | Shape | Handler may |
|---|---|---|
| `emit` | concurrent, fire-and-forget with timeout | observe |
| `collect` | concurrent, results merged in priority order | add context or items |
| `pipe` | sequential, each sees the previous result | transform a value |
| `gate` | sequential, first non-allow wins | narrow: `Deny(reason)` or `Ask(reason)`; cannot return `Allow` |

**Ordering.** Priority integer (lower first, default 50); ties break by extension order (built-ins first, then user, then third-party; manifest order within). Deterministic across runs.

**Failure semantics.**

| Kind | Timeout default | On exception or timeout |
|---|---|---|
| `emit` | 2 s | Log, record in dev panel, continue. After 5 consecutive failures the handler is muted for the session and an inbox note is raised. |
| `collect` | 1 s volatile, 100 ms stable (stable must read cached state, never do I/O in the turn path) | Contribution omitted; `context_used.skipped` records it. |
| `pipe` | 1 s | Previous value passes through unchanged. |
| `gate` | 2 s | **Fail toward asking:** the call becomes a forced approval card with "hook X did not answer". Never fail open. The manifest may set `onFailure: "deny"` but not `"allow"`. |

The fail-open default on gating hooks elsewhere is a documented pitfall (section 7.3).

**Payloads** are frozen snapshots with `redact` applied. Raw conversation content needs the `conversation:read` permission. Payload schemas are defined once as JSON Schema and generated into Python `TypedDict`s and TypeScript types.

**Loop guards.** A tool call made by a hook handler carries `via_hook` depth; depth above 2 is refused, and a handler cannot re-trigger its own hook. Any "request another turn" return is capped at one per turn.

**Synchronous call sites.** Repositories and the model layer are not coroutines, and some of their hooks must have run before the call returns (`model.response` writes the usage row, `memory.before_write` can refuse a write). They fire through `emit_sync` / `pipe_sync`: a sync handler runs inline on the calling thread, in order, with no timeout, so it must be quick; an async handler is started on the event loop and not waited for (`pipe_sync` skips it). Failure counting and muting are the same as `emit` (and, like `emit`, never apply to a built-in handler, which has no default timeout either). Used by `model.request/response`, `memory.*`, `approval.*` and `subagent.*`.

**`U` marker.** A hook marked `U` depends on the tool-path seam. The seam is a new function `dispatch_tool(ctx, name, args)` that wraps `Toolbox.call` and `_mcp_call`/`_exec_mcp` and is called by all three loops. It is not the full unification of the three loops (shared gate chain and approval wait), which is a larger refactor listed in section 10.

### 5.1 App and session

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `app.start` | replaces the `@app.on_event("startup")` handlers (`app.py:614`..`10234`, `_modules_startup` `8084`); run after DB and core services exist | `{api}` | none (spawn tasks) | `emit`, topological by `requires` | session start/gateway start in all four agent systems; VS Code `onStartupFinished` |
| `app.stop` | `_modules_shutdown` (`app.py:8090`) and `_shutdown` | none | none | reverse order; 5 s budget then cancel | session shutdown (pi), SessionEnd (Claude Code), gateway_stop (OpenClaw) |
| `settings.changed` | `put_settings` (`app.py:993`) after validation | `{keys, old, new}` | none | `emit`; today each side effect is hand-coded by key | ConfigChange (Claude Code) |
| `chat.created` / `chat.deleted` | `POST /conversations` (`app.py:1457`), delete (`1581`) | `{id, project_id}` | none | `emit` | session.created/deleted (OpenCode) |
| `project.created` / `project.deleted` | `app.py:1403`, `1419` | `{id}` | none | `emit` | n/a |
| `ext.changed` | host, when an extension enables or disables | `{id, enabled}` | none | `emit` | installation.updated (OpenCode) |

### 5.2 Input

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `turn.accept` | `chat()` before `bus.start` (`app.py:4546-4577`) | `{conversation_id, origin, content, attachments}` | `Deny(reason)` / `pipe` content rewrite | `pipe`+`gate`; only built-ins and user extensions with `input:rewrite`. Rewrites never apply to `wake`/internal turns. | input (pi), UserPromptSubmit (Claude Code, block or context only), before_agent_run (OpenClaw) |
| `turn.start` | after user row persisted (`app.py:2069`) | `{conversation_id, message_id, origin}` | none | `emit` | before_agent_start (pi) |
| `input.attachment` | `_resolve_attachments` (`app.py:1640`) | `{file}` | extracted text or `Deny` | `pipe` | n/a |

### 5.3 Context and prompt

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `context.retrieve` | `asyncio.gather(_doc_hits, _memory_hits, _graph_hits)` (`app.py:2136-2139`) | `{query, vec, project_id, conv_settings}` | list of `{id, text, ref}` (registered as `retrieval_source`, not a free hook) | `collect`; each source already tolerates failure by returning None | n/a |
| `context.contribute` | `build_context` (`context.py:247`) and the `hints` tuple (`app.py:2560-2574`) | `{conversation_id, project_id}` (ids only: a handler reads what it needs, and raw message text is not handed out) | `Block{text, slot: stable|volatile|tail, share}` | `collect` ordered by priority; fitted by `_fit` (`context.py:49`) to the extension's share; failure drops the block | before_prompt_build (OpenClaw), before_agent_start (pi), additionalContext (Claude Code), heartbeat_prompt_contribution (OpenClaw) |
| `context.history` | after `compaction.prepare_history` (`app.py:2164`) | `{messages}` | `pipe` over the **volatile segment only** | v2. Rewriting the stable prefix is refused. | context (pi) |

**Cache contract.** The stable prefix is byte-identical per chat and is asserted by a golden test (`stable_hash` is already recorded at `app.py:2574`).
- A `stable` block must be a pure function of (chat settings, extension settings version, a monotonic extension content version). The extension bumps the version explicitly when its text changes; the host includes the version in `stable_hash` and logs a cache-bust reason. No clocks, counters or random ids. Day-granular text is the existing precedent (`_today_hint`, `app.py:1708`).
- `volatile` blocks sit where the existing volatile header goes (`context.py:231`, `VOLATILE_HEADER`) and may change per turn.
- `tail` blocks are appended after the newest user message and are never cached.
- All extension-sourced text is wrapped in a provenance marker (`<ext id="...">`) and prefaced "These are notes from an extension, not instructions", the same treatment recalled memories get. Authority-claim lint (the check in `skillbuild.lint_skill`, `skillbuild.py:134`) runs on declared static text at validate time.

### 5.4 Model request and response

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `model.select` | `_select_model` in `_chat_stream`, after the router | `{model, effort, reason, tier}`; `reason` is the router's (empty when it did not run), `tier` is `fast` or `default` | `pipe` `(model, effort)` | one winner chain; a model whose provider is not configured is ignored, and so is anything but a `{model, effort}` dict; cost guard and alternative routers use this | before_model_resolve (OpenClaw), PreModelSwitch (Claude Code), virtual models (pi) |
| `model.request` | `llm.stream_chat` and `llm.complete`, once per call before the first send (retries do not repeat it) | `{model, message_count, tool_names, tokens_est}` (bodies need `conversation:read`) | observe; v2 may add request headers | `emit_sync`: sync handlers run inline on the calling thread, an async one is started on the loop | llm_input (OpenClaw), model_call_started |
| `model.response` | `llm._emit_usage`, whenever a model call (chat, helper, embedding, image, audio) records its usage | the usage record: `{model, kind, duration_ms, estimated, prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens, reasoning_tokens}` plus `conversation_id`, `project_id`, `tag`, `round` when the caller set them | observe | `emit_sync`, as `model.request`; the core subscriber writes the usage row (`llm.on_usage` is gone). Deliberately not the section 5.4 draft `{usage, finish_reason, latency_ms, cost}` at end of round: the usage record is the one place every model call passes (helpers, embeddings, images, audio too), `duration_ms` is the latency, and the cost is worked out by the subscriber that stores the row | llm_output (OpenClaw), provider_stream_event (pi) |
| `text.delta` | `buf.append` / `yield "delta"` (`app.py:3165-3171`) | `{text}` | `pipe` (v2) | strict 5 ms budget; off by default | message_sending (OpenClaw), MessageDisplay (Claude Code, display only) |

### 5.5 Tool call (the unification-sensitive phase)

All `U` hooks attach at `dispatch_tool`, which wraps `Toolbox.call` (`tools.py:1087`/`1096`) and the MCP call paths (`_mcp_call` `app.py:818`, `Subagents._exec_mcp` `subagents.py:1344`), so chat, workers, jobs and workflows all pass through them. Three things to preserve:
- `_call_tool` (`app.py:1866`) wraps writes and externals in the `executed_calls` journal (`call_once`). The seam sits inside the journal for post hooks so a replayed result is the post-hook result; a `tool.pre` deny inside the journal is recorded as the call's result, which is correct for replay.
- Side-effecting calls triggered from a hook must use the same journaled path.
- `tool_ctx` is shared by reference with parallel read-only warm calls (copies for reads at `app.py:2721`); hook payloads are snapshots for that reason.

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `tools.offer` | end of `_schemas_all` (`app.py:2494`/`2506`) | `{names}` | `pipe`: hide names (built-ins/user), or add own | hide-only for third-party | tool exposure modes (pi), hidden tools |
| `tool.args` **U** | before the gate chain (`app.py:~3430`, after `precheck`) | `{name, args, ctx}` | `pipe` args | **Not in the first cut.** Mutating args after permission resolution would bypass the permission check; this hook is only valid before the gate in each loop, so it needs full loop unification. | tool_call mutate (pi), updatedInput (Claude Code), tool.execute.before (OpenCode) |
| `tool.permission` **U** | `permrules.resolve` (`app.py:3402`, `permrules.py:1280`) and `autoreview.route` (`app.py:3496-3516`) | `{name, args, danger, mode, tainted}` | `gate`: `Deny`/`Ask` only | built-ins and user extensions with `hooks:gate`; fail toward ask; consistent with "rules can only narrow" (`app.py:3365-3367`) | PermissionRequest/PermissionDenied (Claude Code), permission.asked (OpenCode), before_tool_call (OpenClaw) |
| `tool.pre` **U** | top of `dispatch_tool`, after the gate chain, before the body | `{name, args, call_id, ctx}` | `gate`: `Deny`/`Ask` (cannot mutate args) | fail toward ask | tool_call (pi), PreToolUse (Claude Code), tool.execute.before (OpenCode), before_tool_call (OpenClaw) |
| `tool.post` **U** | after `checked()` (`tools.py:1140`) / after MCP return | `{name, args, result_preview, error, duration_ms}` | observe | `emit` | tool_result (pi), PostToolUse (Claude Code), tool.execute.after (OpenCode) |
| `tool.result` **U** | before `tool_results.render` (`app.py:3951`, `working.py:253`) | `{name, result, tainted}` | `pipe` result for the model (redaction, trimming) | must cover every channel: model-bound text, the UI-only side-channel keys (`images`, `show`, `research`, `dashboard`, `app.py:3881-3885`) and the stored preview. A redactor that misses one channel leaks. | tool_result modify (pi, must also redact structured content), updatedToolOutput (Claude Code) |
| `tool.taint` | `app.py:3897-3902` | `{name, source}` | add a taint source | `emit`; monotonic, cleared only by the user | n/a |
| `tool.error` | `_call` exception path (`tools.py:1096-1147`) | `{name, error}` | observe | `emit` | PostToolUseFailure (Claude Code) |

### 5.6 Turn and run end

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `reply.complete` | after `done` is yielded, before the run's background jobs end | `{message_id, text, tool_events, usage: {tokens, cost, rounds}, trace, taint: {tainted, sources}, outcome}`, a deep copy with the text credential-scrubbed | observe | `emit`; carries the full reply; not fired for a silent wake turn | agent_end (pi, OpenClaw), Stop (Claude Code), session.idle (OpenCode) |
| `turn.settled` | after `reply.complete`, on the same task: the four former blocks (style learn, auto-learn, follow-ups, title) are now its built-in subscribers (`settled.py`) | a `Settled` record (an extension gets a copy without the live trace and `publish`, with the secret settings blanked and the reply scrubbed): ids, the conversation as loaded, settings, model, user text, reply, error/stopped/gone/internal/in-side/proposal-only flags, taint, tool events, the trace and the run's `publish` | none (jobs submit themselves) | `emit`; each subscriber keeps its own gate conditions; slow work goes to a worker, so the chat is busy only as long as the handlers take. Style learning is subscribed first because auto-learn copies the trace it extends | agent_settled (pi), SessionEnd |
| `turn.continue` | where desk chaining decides (`_should_chain`, `app.py:4288`) | `{reply, desk}` | `RequestTurn(note)` at most once | v2; loop guard mandatory | agent_before_settle `continue:true` (pi), Stop block (Claude Code) |
| `run.after` | `RunBus._drive`'s `finally`, before the run is closed (the after-snapshot and the child clean-up are its core subscribers) | `Run` | observe | `emit`; the snapshot subscriber has a 120 s budget (`limits.SNAPSHOT_AFTER_MS`), so a closing run still waits for its after-snapshot | n/a |
| `subagent.start` / `subagent.stop` | `Subagents.begin` (a queued worker starts when it begins running), `Subagents._finish` | `{child_id, role, parent}` | observe | `emit_sync` | SubagentStart/Stop (Claude Code) |
| `worker.report` | `workers_mod.build_wake` (feeds `_wake_conversation`, `app.py:4609`) | `{worker, summary}` | add note | `collect` | n/a |

### 5.7 Memory and compaction

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `memory.before_write` | `Memories.create/supersede/update`, before the write | `{content, kind, source, project_id}` | `Deny` / `pipe` content: a handler returns the payload with `content` changed, or `Deny(reason)` | `pipe_sync` (sync handlers only); a denial raises `MemoryRefused` (HTTP 403 on the memory routes); built-ins and `memory:gate` only. PII or sensitivity filters. Tainted chats can only save user-backed content already (`tools.py:998`). | n/a |
| `memory.written` / `memory.deleted` | `Memories.create/supersede/update` after commit; `delete` and `invalidate` fire `memory.deleted` | `{memory, source}` (`memory` is a copy of the row) | observe | `emit_sync` | n/a |
| `compaction.before` | `Compactor.compact`, before the summarizer is called | `{messages, window, summary}` | `pipe` over the payload: a handler sets `summary` to replace the built-in summarizer's text | one winner (the last to set `summary`); a failing or empty one changes nothing, so the built-in summarizer runs | PreCompact (Claude Code, can block), compaction entries (pi) |
| `compaction.after` | `Compactor.compact`, after the summary is saved | `{summary}` | observe | `emit` | PostCompact, session.compacted |

### 5.8 Approvals

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `approval.requested` | `RunStore.open_approval`, when the card is newly opened | `{call_id, tool, args_digest, run_id}` | observe | `emit_sync`. Used by Telegram and notifiers. Not mutable: first-decision-wins and `args_digest` binding are security invariants. | permission.asked (OpenCode) |
| `approval.resolved` | `RunStore.decide`, when this call won the decision | `{call_id, decision, by}` | observe | `emit_sync` | permission.replied |

No hook may decide an approval. A channel extension relays the user's decision through the same `POST /approvals/{call_id}` route (`app.py:5542`), exactly as Telegram does now.

### 5.9 Background and schedule

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `job.fired` | `_launch_job` and `_launch_desk_job`, once the run (or desk) has started | `{job_id, kind, trigger}`; `trigger` is the fire's own (`dir`, `mail`, `calendar`, `clock+dir`), else `manual` or `clock` | observe | `emit` | heartbeat (OpenClaw) |
| `job.done` | the run task's done callback (`_job_finished`), after the row is final and `job_finished` is on the app topic; desk jobs have no such callback | `{job_id, run_id, outcome}`; `outcome` is the run row's status | observe | `emit` | n/a |
| `schedule.tick` | manifest `job(every=...)` | `{name}` | none | minimum interval 60 s, visible in dev panel | Raycast interval commands, monitors (Claude Code) |
| trigger kinds | registration, not a hook | `TriggerAdapter.poll()` | fires `job` | replaces the closed kind set in `jobs.py` | n/a |

### 5.10 Channels and I/O

| Hook | Fires at | Payload | Returns | Order and failure | Equivalents |
|---|---|---|---|---|---|
| `channel.inbound` | adapter calls `chat()`/`steer_run()` (`_telegram_turn`, `app.py:10172`) | `{channel, sender, text, files}` | adapter-owned | owner check stays in the adapter's `_call` | inbound_claim, message_received (OpenClaw), channels (Claude Code) |
| `channel.outbound` | after `done` (`_push_wake_reply`, `app.py:4071`; `telegram_bridge.push`, `4744`) | `{channel, text, files}` | `pipe` text, or `Cancel` | last-wins is rejected: chained | message_sending, reply_payload_sending (OpenClaw) |
| `file.written` | `chat_files.record_*`, `Workspace.on_save` (`app.py:646-647`) | `{path, owner, kind}` | observe | `emit`; generalizes the one-slot `on_save` / `on_chunks` callables | file.edited, FileChanged |
| `review.accepted` | `Docs.accept` (`docs.py:864`), `accept_outputs` (`app.py:10026`) | `{kind, id}` | observe | `emit` | n/a |
| `notify` | `api.notify` | `{title, body, target}` | none | published as app-topic event; renderer calls `lib/notify.ts` | Notification (Claude Code), tui.toast |

### 5.11 UI hooks (frontend)

| Hook | Fires at | Payload | Returns | Notes | Equivalents |
|---|---|---|---|---|---|
| `ui.view.opened` | store view change | `{view}` | none | lets lazy extensions activate | VS Code `onView` |
| `ui.message.display` | `Message.tsx` render | `{message}` | transform of displayed text only | display-only; never edits stored text | MessageDisplay (Claude Code) |
| `ui.event` | `ext:<id>:<name>` from backend | `{data}` | none | the pass-through in section 3.5 | custom events |
| `ui.selection` | selection toolbar | `{text, source}` | actions via slot | | n/a |

### 5.12 Coding bridge (PR 17)

Reported from Claude Code's own tool calls in Grain-started sessions through the session's `--settings` hooks and a per-session loopback route (section 10.5.5). Distinct from `tool.pre/post`, which see Grain's own tool calls.

| Hook | Fires at | Payload | Returns | Order and failure | Who may subscribe |
|---|---|---|---|---|---|
| `coding.tool.pre` | `claude_hook` PreToolUse, via `POST /coding-hooks/{session}/pre` | `{session_id, agent, tool, input, cwd, worktree, permission_mode, lifted, call_id?, conversation_id, run_id}`; strings scrubbed, 2 KB each, 16 KB total | `gate`: `Deny`/`Ask` only; strictest of this and the local floor wins; never `allow` or `updatedInput` | 2 s bus budget, 3 s socket timeout; failure, timeout or a bad return becomes `Ask`; with the app unreachable and a gate subscriber at session start the hook asks; `lifted` sessions honor `Deny` only | built-in and user with `hooks:gate` and `coding:read`; third-party only if decision 5 is taken, narrow-only |
| `coding.tool.post` | PostToolUse, via `POST /coding-hooks/{session}/post` | the pre payload plus `{result_preview, error?, duration_ms?}` | observe | `emit`, 1 s hook timeout, never affects the session | any enabled extension with `coding:read` |

## 6. Customization and authoring tooling

### 6.1 Layer 1: the customization surface

Extensions fill **fixed named slots** and set **design tokens**; they do not reach into component internals.

| Surface | Mechanism | Notes |
|---|---|---|
| Theme | `contributes.theme`: partial map over the existing CSS custom properties (78 today in `styles.css`), light and dark | The token list becomes the public contract; renames follow the deprecation rule in section 11. |
| Layout slots | `sidebar.section`, `sidebar.row`, `titlebar.left/right`, `composer.toolbar`, `message.footer`, `message.header`, `sidepanel.tab`, `recap.section`, `settings.panel`, `statusbar.item` | Each slot has a typed item shape, an `order`, and a `when` predicate on store state. |
| Keybindings and menus | `ui.keybinding`, menu items by group | Defaults must not collide with the core `SHORTCUTS` table; collisions are validation errors. User rebinding wins. |
| Renderers | tool cards, fence languages, message-part renderers | A fence renderer also contributes the line that teaches the model its syntax (a stable context block). |
| Commands | slash commands, palette commands, selection actions | |
| Settings panels | `settings.panel` slot, typed schema; secrets to Keychain | |
| Default overrides | `contributes.defaults`: views on/off, default model and effort, theme, permission preset | Applied once on enable, recorded as `source: ext:<id>`, so a disable reverts untouched values. The user's later edits always win. |
| Raw CSS / DOM access | opt-in `permissions: ["ui:unsupported-css"]` | Unsupported: breaks across releases, shown with a warning in the extension card. Off by default; not available to third-party tier. |

**Permission floor for defaults.** A profile or extension may set a permission preset only if it is equal to or stricter than the current one. Anything looser (more `on` tools, `allow_all`, wider workspace folders) is never applied silently: the host shows the diff and requires an explicit click. This follows the existing rule that standing grants are only ever added by the user.

**Profile extensions.** `kind: "profile"` has no code files, only `theme`, `defaults` and slot layout. The lean default becomes a bundled profile (`lean`): it replaces the first-boot `hiddenViews` seed now on `feat/default-off-views`, and "focus mode" (composer-only chrome, side panel closed, Lists and Mail hidden, effort low) becomes another profile the user toggles. Profiles are the safest extension type to share, because the validator can prove they run nothing.

### 6.2 Layer 2: authoring tooling

| Piece | What it does |
|---|---|
| `grain ext new <id> [--profile]` | Scaffolds `extension.json`, `extension.py`, `extension.tsx`, a test, and a migrations folder. Registers the folder with a running dev backend. |
| Typed SDK | `grain_sdk` (Python, `py.typed`) and `@grain/ext-sdk` (TypeScript), both generated from the one set of JSON Schemas for hook payloads, slot items and manifest. Extensions import nothing from `personal_os.*`. |
| Hot reload (`grain ext dev`) | Backend: file watcher, deactivate, import into a fresh module namespace, activate; per-extension state survives via `api.data_dir` and the UI store slice. Frontend: Vite HMR with registry disposal so a hot-swapped component replaces, not duplicates, its slot item. |
| Dev panel (Settings > Developer) | Loaded extensions with state; contributions registered; hooks subscribed with count, p50 and p95 latency; last N errors; muted-handler list; recent payloads (redacted); enable, disable, reload buttons. |
| Test harness | `from grain_sdk.testing import Harness`: starts a scratch backend (temp data dir, scripted fake model), loads the extension, then `h.fire("tool.pre", ...)`, `h.chat("...", model_script=[...])`, `h.assert_context_contains(...)`, `h.assert_prefix_stable()`. The project already runs a scratch backend with a Playwright-driven Electron for e2e (`tests/e2e/`); the harness reuses that fixture. |
| `grain ext check` | Validates manifest schema, `apiVersion`, id and tool-name collisions, `requires` closure, undeclared hook usage (AST scan for `api.on("...")` against `hooks`), forbidden imports, tool-schema lint, permission diff against the previous version, static authority-claim lint on prompt text. Exit non-zero on error. |
| `grain ext pack` | Builds `dist/extension.js` for the renderer half, writes a content hash into a lock file used for pinning (section 7). |

### 6.3 Layer 3: Grain authoring its own extensions

A skill (`author-extension`, a normal approved skill with the SDK reference as `references`) lets the agent do the work, with no new privileges:

1. The agent writes a draft into a staging folder `<data>/ext-drafts/<id>/` using the existing sandboxed file tools. It cannot write into the live extensions folder.
2. A new tool `ext_propose(id)` runs `grain ext check`. The tool is `ALWAYS_CARD` (the same class as `agent_create`, `tools.py:111`), so it always shows a card, even in `allow_all`.
3. On success the draft lands in **Review as a diff** (reusing the docs revision and desk-output review paths, `Docs.accept` `docs.py:864`, `accept_outputs` `app.py:10026`), not installed.
4. Accepting the diff copies the folder into `<data>/extensions/` with `enabled: false`. The agent has no tool that enables an extension.
5. Before the user's Enable click the card shows: contributions (tools with danger levels, slots, views), hooks, permissions, settings, and any permission that would change a gate. The card also shows the **provenance of the drafting session**: if that session was tainted (it read web pages, mail, or a connector result), the card says so and `prompt:inject` and `hooks:gate` are refused.
6. An agent-authored extension is tier `user`: the same restrictions as third-party for gating hooks and prompt injection, and its content hash is pinned at approval. A later agent edit produces a new diff and drops the extension back to disabled.

## 7. Trust and security model

### 7.1 Cost-ranked options

Ranked cheapest first (ranking synthesized from the external survey, section 12).

| # | Option | Cost | Seen in | Grain adopts |
|---|---|---|---|---|
| 1 | Off by default, explicit enable | very low | Obsidian restricted mode, Claude Code enabled plugins, Goose per-entry flag | Yes, for every non-built-in tier |
| 2 | Scope trust (project-level extensions only after folder trust) | low | pi, Claude Code | Not needed: Grain has no project-local code folders |
| 3 | Declarative manifest with contracts declared up front, optional tools allowlisted | low | OpenClaw, VS Code | Yes: tools, hooks, permissions, tables all declared and checked before code loads |
| 4 | Hooks as a policy layer with deny-wins and a fail-closed option | low | Claude Code (`onFailure: block`) | Yes, with fail-toward-ask as the default |
| 5 | Pinned versions or hashes, validate command, scan and scorecard | medium | Claude Code, Obsidian, ClawHub scanning | Yes: content-hash pinning plus `grain ext check`; scorecard later |
| 6 | Registry review | high (human) | Raycast, Obsidian popular plugins | No registry in this phase |
| 7 | Out-of-process host | medium to high | VS Code extension host, MCP stdio, Goose | Yes for third-party backend halves (section 11) |
| 8 | Real capability sandbox | very high | none of the surveyed systems | Spike only: launch the out-of-process half under the existing Seatbelt profile and network allowlist (`sandbox.py`, `egress.py`) |

### 7.2 Tiers

| Tier | Source | Default | May register gating hooks | May inject prompt text | Process |
|---|---|---|---|---|---|
| Core | app | always on | n/a | n/a | in-process |
| Built-in | app bundle | per manifest | yes (declared) | yes | in-process |
| User | local folder, agent-authored | disabled | only with explicit per-extension grant | yes, marked, linted | in-process in phase 1; out-of-process later |
| Third-party | git URL, archive | disabled | narrow-only, with explicit grant | yes, marked, linted | out-of-process |

The brief says a third-party extension "can never register hooks that change permissions or approvals". This design implements that as: no tier may register any hook that can **allow, auto-approve, answer an approval, or widen a mode**. Whether third-party may register **narrowing** hooks (deny or ask only) is listed as a decision in section 11, with a recommendation to allow it behind an explicit grant, because a spend guard or audit gate is a safety feature.

### 7.3 Reuse the MCP connector model

MCP connectors already have the strongest trust model in the codebase. Third-party extensions use it as the template:

| MCP mechanism today | Extension equivalent |
|---|---|
| Name prefix `mcp__<server>__<tool>`, `RESERVED_PREFIX` and `RESERVED_TOOL_NAMES` (`mcp_servers.py:38`, `44`) | Third-party tools are `x__<id>__<tool>`; the reserved set is derived from the live registry, so a collision is a validate error, not a test failure |
| Danger `external`, default mode `ask` (`MCP_DANGER`, `mcp_client.py`) | Third-party tools default to danger `external`, mode `ask`; the manifest can only declare a danger equal or higher |
| Schema-hash grants: a changed schema decays `on` to `ask` (`effective_mode`, `mcp_servers.py:583`) | A content hash over manifest and code; any change returns the extension to "needs approval" and shows the diff |
| Drift quarantine (`mcp_drift.offerable`) | A changed contribution is withheld until the user accepts the change |
| Taint: connector results always taint the run (`app.py:3895-3902`) | Third-party tool results always taint; extension-injected prompt text is data, not instructions |
| Provenance in the description (`[server - third-party MCP connector]`, `app.py:752`) | Every extension tool description is prefixed with the extension name |
| Static and LLM review of tool text (`mcp_eval.py`) | The same review runs on manifest descriptions and static prompt text before first enable |
| Secrets by name in `db.secrets`, rows hold only names | Secret-typed settings go to Keychain; manifest holds only names |

### 7.4 Pitfalls from the survey and how each is avoided

| Pitfall (source) | Avoidance |
|---|---|
| In-process extensions have full credential access (pi, OpenCode, Obsidian all say so) | Built-ins are first-party code. Third-party backend runs out-of-process, optionally under Seatbelt. In-process user tier is documented as "installed software", not sandboxed. |
| Gate hooks that time out fail open (Claude Code) | Gate hooks fail toward a forced approval card; no return value can say allow. |
| Hooks are best-effort, hard rules belong to the permission system (Claude Code) | The permission stack stays in core; hooks only narrow. |
| Markdown skills and "setup" commands are an attack channel (341 of 2,857 ClawHub skills found malicious, per the security reports in section 12) | Extension prompt text is marked and linted, never run as instructions with authority; setup shell commands in extension READMEs are shown but never run by Grain; `ext_propose` and install are card-gated. |
| Loop hazards from continue/stop hooks (pi, Claude Code) | `RequestTurn` capped at one per turn; `via_hook` depth limit. |
| Redaction that misses a result channel (pi structured content) | `tool.result` must apply to every channel; core redacts after the pipe, so an extension cannot unredact. |
| Override-by-name and replace-vs-merge surprises (OpenCode, Claude Code) | Names are namespaced and reserved; an extension can never replace a core tool; overrides are not supported. |
| Writes into the code folder lost on update (Claude Code) | Separate `<data>/ext/<id>/` data directory. |
| Side effects at load time (pi, VS Code) | `activate` must not start I/O; declarations are manifest-driven; loops start in `app.start`. |
| Tools that cannot be unregistered (pi) | Every registration is a `Disposable` auto-disposed on deactivate; tools are hidden from the next schema list. |
| Experimental API churn (OpenClaw, pi) | Integer `apiVersion`, additive-only payloads, two-release deprecation window. |
| Handler timeout does not cancel the plugin's own work (OpenClaw) | Handlers run as tracked tasks; the host cancels the task on timeout. |
| Hook output size (Claude Code caps fields at 10k chars) | Hard caps on returned text per hook, enforced by the host. |
| Secrets leaking through shared env (OpenCode `shell.env`) | No env injection hook; secrets only via `api.secrets`. |

## 8. Feature map

Class: **Core**, **Svc** (core service), **BI-on** (built-in extension, default on), **BI-off** (built-in, default off on a fresh install; existing installs unchanged), **Opt** (optional extension, needs setup). "Phase" refers to the PR order in section 10. Row numbers match the feature inventory used for this study.

| # | Feature | Class | Hooks and contributions consumed |
|---|---|---|---|
| 1 | Chat turn loop, streaming, models | Core | provides all hooks; `model.select` |
| 2 | Tool system (Toolbox, specs, danger) | Core | provides `tools.offer`, `tool.*` |
| 3 | Persistence, migrations, backups, secrets | Svc | extension migrations, backup include list |
| 4 | Context assembly, compaction | Core | `context.contribute`, `compaction.*` |
| 5 | Permissions, approvals, safety | Core/Svc | `tool.permission` (narrow only), `approval.*` |
| 6 | Runs, attention, inbox | Svc | `api.inbox`, `run.after` |
| 7 | Sandbox: shell, Python, file tools | Svc | `tool.pre/post`, sandbox profile |
| 8 | Memory (facts, auto-learn) | Svc first, learners BI-on later | `context.contribute`, `turn.settled`, `memory.*`, settings panel, footer chip |
| 9 | Knowledge graph | BI-on | `context.contribute`, `turn.settled`, retrieval source, view, widget |
| 10 | Writing style | BI-on | `context.contribute` (draft turns), `turn.settled`, tool |
| 11 | Notes / docs editor | BI-on | view, tools, routes, retrieval source, `review.accepted`, widget |
| 12 | Uploads, chat files, extract_text | Svc | `input.attachment`, `file.written` |
| 13 | Artifacts view | BI-on | view |
| 14 | RAG, retrieval, citations | Svc | retrieval source registry, citation renderer |
| 15 | Projects | Core | scope provider |
| 16 | Skills | BI-on | slash command, `context.contribute`, library tab, tools |
| 17 | Agents / personas | BI-on | library tab, view, `@mention`, widget |
| 18 | Slash commands | Svc | command registry (built-ins register) |
| 19 | Workflows | Opt | library tab, tools, routes, job |
| 20 | MCP connectors | Core | tool source, settings panel |
| 21 | Workers, delegate, subagents | Core | `subagent.*`, `worker.report`, widget |
| 22 | Autonomous chats / desks | BI-on | side-panel tab, `tool.pre` (stage writes), inbox item, settings panel |
| 23 | Side panel, Review, show panel | Svc | `sidepanel.tab` host |
| 24 | Traces | Svc | span emitter, context-panel tab |
| 25 | Jobs, scheduled tasks | BI-on (scheduler is Svc) | trigger kinds, view, tools, `job.*` |
| 26 | Research mode | BI-on | slash command, tools, message renderer |
| 27 | Web search / read | Svc | tools |
| 28 | Agent browser | BI-on | tools, tool card, side-panel tab, Electron IPC |
| 29 | Coding sessions | BI-on (phase 17; was Opt) | tools, routes, tool card, job, `tool.pre`, `precheck`, `directive`, terminal control, recipes, `coding.tool.*` |
| 30 | Image generation | BI-on | tool, tool card, settings |
| 31 | Charts, Mermaid, interactive, previews | BI-on (renderer only) | fence renderer |
| 32 | Saved dashboards | Opt | view, tools |
| 33 | Document generation guides | Opt (ship as skills) | skills |
| 34 | Dictation / voice | Opt | composer toolbar item, routes, settings, global shortcut |
| 35 | Quick ask, quick capture | BI-on | global shortcut, window, route |
| 36 | Selection verbs | BI-on | selection action |
| 37 | Today / recap | Svc (shell) | `recap.section` |
| 38 | Lists / todos / Google Tasks | BI-off | view, tools, routes, loop, `recap.section`, widget, nav (phase 5) |
| 39 | Calendar | BI-off | view, tools, widget, `recap.section`, trigger kind (phase 9) |
| 40 | Planner | BI-off | routes, tools, calendar overlay; requires todos and calendar |
| 41 | Mail, outbox, mailwatch | BI-off | view, tools, routes, loop, tool card, trigger kind (phase 10) |
| 42 | Google service | BI-on service ext | `provide("google")`, settings panel, tools (phase 8) |
| 43 | Health | BI-off | all module surfaces (phase 4) |
| 44 | Spaces / canvases | BI-off | widget registry, drag kinds, tools (phase 11) |
| 45 | Telegram | Opt | channel adapter, `approval.requested`, settings panel, loop (phase 6) |
| 46 | Mac reach | Svc + tools BI-on | tools, platform service |
| 47 | Activity monitor | BI-on (unverified: no dedicated backend file found) | view |
| 48 | Cost / usage | Svc | `model.response`, settings tab |
| 49 | Model duels | Opt | tools, composer control, side-panel tab |
| 50 | Onboarding, setup cards | Core | setup step contributions |
| 51 | Trash | Svc | per-type restore handlers |
| 52 | Command palette, help, shortcuts, shell | Core | command, keybinding, nav registries |
| 53 | Settings framework | Svc | `settings.panel` slot |
| 54 | Backups, export | Svc | backup include list |

## 9. New extensions this enables

"Tier A" = needs only the hooks that exist in the first hook release: lifecycle (`app.*`, `chat.*`), `context.contribute`, `tool.pre/post/result`, `reply.complete`, `turn.settled`, `model.*`, `job.*`, and the contribution types of section 4. "Tier B" lists what is missing.

| Idea | What it does | Needs |
|---|---|---|
| Reading list | Saves links with summaries; surfaces unread ones in recap | A |
| Finance / budget | Imports CSV, answers spending questions; Python tools plus a view | A (no money movement) |
| CRM / people follow-ups | Tracks last contact per person, nudges in recap | A, plus mail service for auto-detection |
| Habit tracker | Daily check-ins, streaks, recap section | A |
| Journaling prompts | Daily prompt as a new-chat card, entries to notes | A |
| RSS / news digest | Scheduled fetch, summary job, inbox item | A |
| Meeting notes | Calendar event to note template, post-meeting prompt | B: calendar extension as a service |
| Travel planner | Itinerary doc, packing list, calendar blocks | A (needs calendar for blocks) |
| Focus profile | Quiet chrome, low effort, only core tools | A (profile, no code) |
| Slack / Discord / iMessage channels | Same shape as the Telegram port | A after phase 6 (channel adapter) |
| Linear / GitHub issues | Tools, review queue, trigger kind for new assignments | A (tools); trigger kind needs `api.trigger` |
| Home automation | Tools with danger `external`, ask by default | A |
| Alternative model router | Picks model by task, time or budget | A (`model.select`) |
| Cost guard | Narrow-only gate: ask or deny after a spend threshold | B: `tool.permission` / `model.select` gate (built-in or user-granted) |
| PII redaction | Redacts outgoing request text and tool results | B: `tool.result` (A) plus `turn.accept` rewrite (needs `input:rewrite`) |
| Audit log | Append-only log of tool calls, approvals, models | A (`tool.post`, `approval.*`) |
| Per-project prompt packs | Context blocks keyed by project | A |
| Daily standup generator | Job plus recap plus todos, Telegram delivery | A |
| OpenClaw compatibility | Lets an OpenClaw user move over without losing their setup: (1) a channel bridge that connects to an existing OpenClaw gateway so its messaging channels reach Grain, and (2) a skill importer that reads ClawHub/OpenClaw skill folders, converts them to Grain skills, and lands each one disabled in Review with its declared tools and any shell/setup commands shown. Imported executable code runs only out-of-process under the third-party tier, never in-process; markdown instructions are treated as untrusted prompt text (the ClawHub malicious-skill campaign used fake setup steps). | B: channel adapter (phase 6) plus the third-party out-of-process tier; the importer alone is A |
| Test-fix loop | A recipe (prompt, repo, verify command) run from the palette; done only when verify exits 0, one bounded retry | A, on PR 17 |
| Nightly dependency bump | Recipe job pinned to a version, result in the inbox | A, on PR 17 and `api.trigger` |
| Structured code review or extraction | `coding_run_structured` with a schema; invalid output is an error, not a guess | A, on PR 17 |
| Coding audit and spend guard | Logs `coding.tool.post`; asks or denies on `coding.tool.pre` (narrow only) | B: `coding.tool.*` hooks and `coding:read` |
| Dev-server babysitter | Agent opens a terminal, starts a server, waits for a ready line, reads logs; the user can take over the same terminal | A, on PR 17 terminal tools |

Compatibility is the cheapest way to win users from an established tool: matching its extension model is table stakes, so the import path lets the differences (review-before-write, safer defaults, lower running cost) be tried without a migration cost.

## 10. Migration plan

### 10.1 Prerequisites

1. **Green test baseline.** Project notes list known problems on `main`: a hanging backend test (`test_working_memory`, `test_spotlight`), a sticky-notes migration test broken by a later migration, stale settings e2e specs, a few flaky e2e tests (all recorded in earlier PR notes; I have not re-run them for this document). Each port PR is judged by "no new red", which needs a recorded baseline. Fix or quarantine with a tracking list.
2. **Merge `feat/default-off-views`.** It carries `view_on`, `TOOL_VIEWS`, `Module.enabled()` and the gating in `Toolbox.available()`. It is the seed of per-extension `enabled()`.
3. **Prefix-stability test.** A golden test that builds the stable prefix for a fixed chat before and after each refactor and compares bytes.
4. **Tool-path seam** (PR 3 below). Not full loop unification, but required before any tool hook.

### 10.2 Steps

All steps edit `app.py` or `tools.py`, so they are sequential, not parallel. Each is one PR.

| # | PR | Content | Done when |
|---|---|---|---|
| 0 | Baseline | Fix or quarantine red tests; merge `feat/default-off-views` | Recorded pass counts; prefix golden test added |
| 1 | Host skeleton | `ExtensionHost`, manifest loader, `HookBus` (emit/collect/pipe/gate), disposables, `Module` as an adapter, lifespan-based start/stop replacing `@app.on_event` | No behavior change; `test_modules` unchanged green; hook overhead benchmark (empty bus) < 0.1 ms per emit |
| 2 | Lifecycle events and data | `app.start/stop`, `settings.changed`, `chat.*`; settings namespace `ext.<id>`; migration ledger ids; backup include list | Existing four modules loaded through the host |
| 3 | Tool-path seam | `dispatch_tool` wrapping `Toolbox.call` and MCP paths; used by `_call_tool`, `Subagents._exec`, `_tool_step`; `tool.pre/post/result/error` | A test fires each hook from a chat call, a worker call, a workflow step and an MCP call |
| 4 | Context contributors | Convert `hints` tuple and `build_context` extra blocks to contributor list with stable/volatile | Prefix golden test byte-identical; `context_used` unchanged |
| 5 | Post-turn subscribers | The four blocks (`app.py:4087-4148`) become `turn.settled` subscribers, still built-in | Learner behavior tests unchanged |
| 6 | Port Health | Manifest, backend, frontend; first extension through the new host; open `View` union with `ext:` ids; settings-panel slot | Same routes, tools, order; e2e health spec; default off on fresh install |
| 7 | Port Todos (with gtasks sync, planner, mailwatch dependencies) | Same pattern; per-extension store slice for todos | e2e todos specs; Google Tasks sync loop test |
| 8 | Port Telegram | Channel adapter contribution; `channel.inbound/outbound`, `approval.requested`; `telegram_*` out of `app.py` | Mocked-transport tests; one manual pairing check |
| 9 | Recap/context hooks and open frontend registry | `recap.section`; slots (sidebar section, composer toolbar, message footer, side-panel tab, palette, keybinding); `ext:` event pass-through; dynamic tool-card registry | Slots render; core views still use them |
| 10 | Split `google.py` (2,075 lines) | A `google` service extension: OAuth, Drive/Docs/Sheets, verified writes; `provide("google")` | Calendar, mail and tasks still pass |
| 11 | Port Calendar | View, tools, widget, recap section, trigger kind | Calendar e2e |
| 12 | Port Mail | View, tools, outbox, mailwatch, tool card, trigger kind | Mail e2e; outbox undo tests |
| 13 | Port Spaces | Open `WidgetKind`; widget registry | Canvas e2e |
| 14 | Profiles, defaults, theme tokens | `kind: "profile"`; `lean` profile replaces the seed; defaults with permission floor | Profile apply/revert tests |
| 15 | Authoring tooling | `grain ext new/check/dev/test/pack`, SDK generation, dev panel | A sample extension built end to end by CLI |
| 16 | Authoring skill and Review flow | `author-extension` skill, `ext_propose`, diff in Review, disabled install | A drafted extension enabled by hand |
| 17 | Coding extension | Port coding sessions to a built-in extension (default on); terminal control tools over the existing PTY hub; recipes with verify; headless structured runs; hook bridge (`coding.tool.pre/post`). Specified in section 10.5 | Section 10.5.7 "Done when" |
| 18 | Third-party loading | Out-of-process host, content-hash pinning, approval card | Separate design review first |

PRs 0 through 9 are the core of the effort. PRs 10 to 13 can slip without blocking anything else. PR 17 needs PRs 3 and 9 (and 11 for recipe jobs) and slips independently.

### 10.3 Risks

| Risk | Mitigation |
|---|---|
| Prompt-cache regression from reordered context | Golden byte test per PR; `stable_hash` churn metric |
| Three loops diverge again | The seam test fires each hook from all three loops; later full unification |
| Partial ports leave two code paths | Each port deletes the old path in the same PR; no dual-run |
| Hook latency on the first-token path | Budgets: empty bus < 0.1 ms; typical 5 hooks < 20 ms p95; `collect` timeouts; measured in PR 1 and 4 |
| Frontend `store.ts` (3,924 lines) is monolithic | Per-extension slices first; do not break up the store in this effort |
| Behavior drift in ported tool order | Existing rule: tool names, schemas and position unchanged; asserted by tests |
| FastAPI router add/remove | One dynamic dispatcher for `ext` routes; built-ins keep static routers |
| Dev while the app is in use | Use the `Grain-dev` data dir described in `CLAUDE.md`; never run the host against the real data dir |

### 10.4 What to measure

- Cold start time and RSS before and after PR 1, 6 and 10.
- Time to first token with N hooks (0, 5, 20) on a fixed prompt.
- Prompt-cache hit rate (provider-reported cached tokens) and `stable_hash` change rate per chat.
- Tool-call overhead per dispatch.
- Lines in `app.py`, `tools.py`, `store.ts` per phase (a size trend, not a goal by itself).
- Test pass counts per PR against the baseline.
- Hook failure and mute counts from the dev panel during a week of daily use.

### 10.5 PR 17: Coding extension

Coding sessions are the one large feature that already behaves like an extension (own tables, tools, routes, tool card, loop) and the one that most needs the hook bus (its gate is a repo-write refusal wired into three call sites). PR 17 ports it and, in the same step, adds four capabilities that only make sense on top of the host: agent-driven terminals, recipes, headless structured runs and a hook bridge. It needs PR 3 (tool seam), PR 9 (dynamic tool-card registry, `sidepanel.tab`, palette commands) and, for recipe jobs, PR 11 (`api.trigger`). It does not need third-party loading and stays built-in only.

#### 10.5.0 Facts from the code and the CLI that shape the spec

| Fact (checked in the `feat/extensions` worktree; CLI is `claude` 2.1.302) | Consequence |
|---|---|
| The only PTY code is in the Electron main process: `src/main/terminals.ts` (`node-pty`) around `createTermHub` in `src/shared/terminal.ts`. The hub is keyed `<coding session id>:<mode>`, owned per renderer window (`senderId`), capped at `MAX_PTYS = 8`, `WRITE_CAP = 64 KB`, and keeps one 200 KB raw `Ring` per PTY with no offsets and no screen model. The Python backend has no PTY. | Agent terminal tools cannot be a Python-side feature alone and must not add a second PTY stack: the hub gains control entries and a backend-to-main bridge (10.5.2). |
| `shell_run` is OS-sandboxed (`shell.py`, `sandbox.shell_profile`); a terminal login shell (`pickArgv` mode `shell`: `$SHELL -l`) is not. `run_fixed(..., sandboxed=True)` already builds the sandboxed argv for a fixed command. | Agent-opened terminals spawn the shell under the same sandbox profile as `shell_run`, argv built by the backend; the user's own "Shell in worktree" is unchanged and unsandboxed. |
| `coding_route.check` runs in four places: `Toolbox.precheck` (before any approval card), `Toolbox._call`, `Subagents` (`subagents.py:1237`) and `toolbridge`/`workflows._precheck` through `precheck`; `guard_start`/`guard_finish` wrap the `shell_run` body. A `tool.pre` gate sits below the approval wait, so alone it would show a card for a call it then refuses. | Needs a second narrow-only door, `api.precheck`, for the pre-card refusal; `tool.pre` is the authoritative gate and `tool.result` carries the after-the-fact revert (10.5.1). |
| `coding_route.hint(cfg)` is a plain, unwrapped paragraph in the stable prefix (`backend/tests/golden/chat_prefix.txt` contains it). `context.contribute` wraps extension text in `<ext>` provenance and a "not instructions" line. | A built-in-only `api.directive` places the text unwrapped at the same position, so the golden stays byte-identical. |
| Background `claude --bg` sessions outlive the app, and every start and resume bakes `python -I -B -c "from personal_os.claude_hook import main..."` plus `PERSONAL_OS_DATA_DIR` into `--settings`. Sessions started before the upgrade keep that command. | `claude_hook.py`, `permrules.claude_code_floor` and the hook command shape stay where they are, unchanged. They are the floor, not the feature. |
| `claude --help` shows `--bg`, `--resume`, `--settings`, `--permission-mode` (choices `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`), `--allowedTools`/`--allowed-tools`, `--disallowedTools`/`--disallowed-tools`, `--tools`, `-p/--print`, `--output-format` (`text`, `json`, `stream-json`), `--json-schema`, `--max-budget-usd`, `--no-session-persistence`, `--strict-mcp-config`, `--disable-slash-commands`, `--model`, `-n`, `--agents`, `--add-dir`. **It has no `--max-turns`.** | A "turn cap" cannot be a CLI flag. It is enforced by Grain (tool-call counting hook plus a `num_turns` check, 10.5.3 and 10.5.4) and described as a step cap in the UI. |
| One observed run of `claude -p --output-format json --json-schema <schema> --no-session-persistence` (prompt on stdin) returned a single JSON object with `type: "result"`, `subtype: "success"`, `is_error`, `num_turns`, `total_cost_usd`, `terminal_reason`, `permission_denials`, `result` (a string) and `structured_output` (the object). `--tools` is variadic: with a positional prompt after it the CLI answered "Input must be provided either through stdin or as a prompt argument". | Headless runs send the prompt on stdin, read only `structured_output`, and never parse `result` as a fallback. Whether `stream-json` also carries `structured_output` was not tested. |
| `jsonschema` is installed (4.26.0) but only transitively (through `mcp`); it is not in `backend/pyproject.toml`. | PR 17 declares `jsonschema>=4.20` before relying on it. |
| Tool positions: `opencode_run` is registered mid-list by `Toolbox._register` (after `shell.register`); the seven `coding_session_*` tools are appended last by `app.py` (`codingagents.register`, after `ship_mod.register`). An extension's tools land in one slot. | The manifest gets a per-tool `after` anchor so each tool keeps its place (10.5.6). |
| The coding tables came from core ledger steps (`_coding_sessions` migration 11, `_coding_session_base`, `_coding_session_events` migration 37). `ALTER ... ADD COLUMN` in a `.sql` extension step is not idempotent. | Those core steps stay as frozen history; `ext:coding:1` is a baseline `CREATE TABLE IF NOT EXISTS` of the final shape, later changes are numbered `ext:coding:n` steps. |

#### 10.5.1 Part 1: port coding sessions to a built-in extension

**Package.** `backend/personal_os/extensions/coding/` (`extension.json`, `extension.py`, `__init__.py`) holding the moved modules: `codingagents.py` (as `sessions.py`), `coding_events.py` (`events.py`), `coding_route.py` (`route.py`), `opencode.py`, `opencode_mcp.py`, `opencode_usage.py`, `pi.py`. Frontend: `src/renderer/src/extensions/coding/` (`extension.tsx`, the `codingSessions` store slice, `CodingSessions.tsx`, `CodingSessionCard.tsx`, `lib/codingSessions.ts`, the Terminal card as a `sidepanel.tab`). Stays in core: `claude_hook.py`, `permrules.py` (including `claude_code_floor`), `shell.py` and `sandbox.py`, `src/main/terminals.ts` and `src/shared/terminal.ts` (the terminal hub becomes a core service, section 2.2).

**Manifest.** `enabled: true` (it exists today, so fresh and existing installs both have it on; an existing install that already turned it off through `hiddenViews`/settings keeps that), `requires.services: [mcp]` only if the OpenCode MCP path needs it (implementer confirms), `provides.services: [coding]` (for `system_access` CLI detection and the setup cards), `data.tables: [coding_sessions, coding_session_events, coding_recipes, coding_recipe_versions, coding_recipe_runs, coding_terminals]`, `hooks`: `tool.pre`, `tool.result`, `tool.post`, `app.start`, `app.stop`, `run.after`, `subagent.stop`. `permissions`: `db:own`, `hooks:gate`, `service:mcp`, `terminal:control`.

**Moves, one for one.**
- Tools: the seven `coding_session_*` and `opencode_run`, with the same names, descriptions, schemas, danger, `default = "ask"`, `force_card`/`force_ask`/`taint_ok`/`available_fn`. The host's existing manifest check (names, danger, group) is the guard. A golden test of the full ordered tool-name list is captured on the parent commit and must pass unchanged.
- Routes: `/coding-sessions`, `/{id}`, `/logs`, `/events`, `/diff`, `/stop`, `/send` keep their legacy prefix (built-ins may). The app-topic events `coding_session` and `coding_session_event` keep their names so `store.ts` consumers do not change; the frontend slice moves, not the wire.
- Loop and jobs: `CodingSessions.watch` starts from the extension's `app.start` through `api.spawn`; `_recover` stays at construction. `stop_owned` is called from `subagent.stop` and `run.after` (the stopped-versus-finished distinction must be readable from those payloads; if `run.after` does not expose it, add the field rather than a direct import). The "session settled, wake the chat" plumbing (`_coding_owed`, `_wake_coding`) becomes a generic `owe_wake(conversation_id, text, wake)` in core; `build_coding_wake` moves into the extension.
- `app.py`, `tools.py`, `subagents.py`, `system_access.py`, `workers.py`, `shell.py`, `toolbridge.py`: every import of the moved modules is removed. `system_access` reads CLI presence through the `coding` service. No module outside the package imports `coding_route`.

**The repo-write refusal as a gate.** The extension registers three things, all narrow-only:
1. `api.precheck(fn)` (new, 10.5.6): `fn(name, args, ctx) -> str | None`, consulted by `Toolbox.precheck` (so chat, workers, workflows and the bridge refuse before any card) and by the subagent loop in place of its direct call. A non-None string is the refusal text; it cannot return approval. An exception in `fn` is logged and counted as no refusal here **only** because `tool.pre` repeats the same check below; neither can allow.
2. `tool.pre` gate: the same function, returning `Deny(text)`. On `shell_run` it also takes the `guard_start` snapshot, keyed by `call_id` in extension memory. A failure or timeout of this gate is an `Ask` by the bus rule (never allow); because `Ask` stops the call at the seam (PR 3), a wedged gate fails closed.
3. `tool.result` pipe: if a snapshot exists for the call, run `guard_finish`; a refusal replaces the whole result dict with the `tool_error` it returned today, with `alternative="coding_session_start"`. `tool.post`/`tool.error` drop an unused snapshot.

Behavior unchanged: the setting `codingRoute` (default on) and `codingAgent`, the refusal text, the verifying-commands allowance, the dotfiles-at-`~` exception, `git push`/`gh` allowed, `opencode_run`, `coding_session_*` and `desk_*` not judged, `proposal_only` runs. Known and accepted ordering difference: the `tool.pre` check runs before `coerce_args`/`proposal_only` in `_call`; the pre-card `precheck` path keeps today's order (coerce first), so only a call that is both malformed and routed changes its message. While the extension is disabled the precheck, gate and directive are inert, so nothing tells the model to call tools that are hidden, and repo edits are no longer routed (the same as turning `codingRoute` off).

**The prompt rule.** `api.directive(fn, *, version)` (built-in tier only, section 3.4): `fn(cfg) -> str`, appended unwrapped at the position `coding_route.hint(cfg)` has now (`app.py:2609`), also used for workers (`subagents.py:871`). Its `version` is part of `stable_hash`; the text stays a pure function of `codingRoute`/`codingAgent`.

**Floor hook.** Untouched: `claude_hook.decide/main/command/settings_hooks`, `floor_settings(lifted=...)`, `claude_code_floor`. PR 17 only adds optional flags to the hook command (10.5.5).

**Frontend.** `ui.toolCard` for the eight tools (PR 9 registry), a `sidepanel.tab` for Terminal, the sessions list and Workspace entry points as before. `registerToolCard('coding_session_*')` static imports are deleted. The Terminal card keeps listing shell jobs and coding sessions, and now agent terminals (10.5.2).

#### 10.5.2 Part 2: terminal control tools

All five tools are in group `shell`, available only when `term:available` is true in the running app (the Electron main process, with `node-pty`, is connected to the bridge) and a sandbox exists; otherwise they are not offered and the error says terminals need the desktop app.

**Ids and ownership.** The backend mints `terminal_id` (`tm_` plus 16 hex) and stores `coding_terminals(id, owner_key, owner_run_id, session_id NULL, conversation_id, cwd, argv_digest, created_at, last_used_at, state, exit_code, user_attached, user_dirty)`. `owner_key` is `(conversation_or_desk_or_job id, agent_id)`: the chat's agent and later replies in the same chat are one owner; a worker, subagent, workflow step, another chat or another job is a different owner and gets "No such terminal" (the same text as a missing id, so ids cannot be probed). Every tool checks ownership before anything else. `session_id` is set when `cwd` is a coding session's worktree or inside it, which is what makes the terminal appear under that session in the Terminal card. The renderer never supplies a command, argv or cwd: the bridge accepts them from the authenticated backend only.

**Bridge (backend to Electron main).** Main long-polls `POST /terminal-bridge/next` (token header as `fetchSession` uses today, 25 s hold) and answers each command with `POST /terminal-bridge/reply`. Commands: `open {terminal_id, argv, env, cwd, cols, rows}`, `write {terminal_id, data}`, `read {terminal_id, cursor, max_bytes, screen}`, `status {terminal_id}`, `close {terminal_id}`, `attach {terminal_id}`. Backend-side `TerminalBridge.call(cmd, timeout)` returns a clear error if no poller has been seen in 3 s. The hub (`createTermHub`) gains control entries keyed by `terminal_id` with no renderer owner (`senderId = 0`: output goes only to the ring and the headless screen buffer, `send` is a no-op); main validates `argv[0]` absolute, `cwd` an existing directory, sizes clamped, and runs `scrubEnv` again. The backend builds the argv: the user's `$SHELL -l -c` wrapped with the same `osplat.current().sandbox.wrap(argv, sandbox.shell_profile(writable=[cwd, tmp], network=..., proxy_port=...))` helper that `shell_run` and `run_fixed(sandboxed=True)` use (factor that out of `shell_run` into one function used by both; no behavior change for `shell_run`).

**Hub additions.** `Ring` gets an absolute offset (`total` characters ever pushed, `base` = offset of the oldest kept character) so a cursor is an integer and "output was lost" is detectable; a screen buffer from `@xterm/headless` (pinned to the same release family as `@xterm/xterm` 6.0.0; a new dependency, not present in `package.json` today) is fed the same data so `screen` mode returns the visible rows as plain text. If the dependency is rejected in review, `screen` degrades to the last N ANSI-stripped lines of the ring and the schema says so. Agent terminals count against `MAX_PTYS = 8`; agent terminals are capped at 6 in total (2 are always left for the user's own).

**Tools.**

| Tool | Arguments | Result | Danger and gate |
|---|---|---|---|
| `terminal_open` | `cwd?` (as `shell_run`: any folder, relative to the default folder), `command?` (typed as the first line, judged exactly like a `terminal_send`), `cols?`, `rows?`, `name?` | `{terminal_id, cwd, cursor: 0, session_id?, sandboxed: true}` | `executes`, `default = "ask"`; a standing grant buys the open, not later sends. |
| `terminal_send` | exactly one of `text` (one line, `submit` default true) or `key` (enum below) | `{ok, cursor}` | `executes`, judged as `shell_run` (below). |
| `terminal_read` | `terminal_id`, `mode`: `"new"` (default, from `cursor`) or `"screen"`, `cursor?`, `max_bytes?` (<= 16 KB) | `{text, cursor, dropped, truncated, exited, exit_code, user_attached}` | `safe`; result taints (below). |
| `terminal_wait_for` | `terminal_id`, `pattern?` (regex) or `literal?`, `idle_ms?` (200 to 60000), `cursor?`, `timeout_s` (**required**, 1 to 600) | `{reason, text, cursor, match?, exit_code?}` | `safe`; taints. |
| `terminal_close` | `terminal_id` | `{closed, exit_code?}` | `writes`; kills the PTY, drops the row's live state. |

`key` enum: `ctrl-c`, `ctrl-d`, `escape`, `up`, `down`, `left`, `right`, `tab`. There is deliberately no bare `enter`, no `backspace`, no `ctrl-u/r/x`, no `shift`/paste: the only way to run a line is `text` with `submit`, so what ran is what was judged. (History recall with `up` cannot be submitted by the agent; driving full-screen programs that need Enter is the user's job after taking over.)

**`wait_for` reasons** (the `reason` field is always one of these five, and `text` always carries the new scrubbed output since `cursor`, <= 16 KB, tail-biased):
- `matched`: the regex or literal hit in new output (`match` holds the matched text, scrubbed, <= 200 chars).
- `exited`: the PTY process exited; `exit_code` set.
- `idle`: no output for `idle_ms`; only when `idle_ms` was given.
- `stdin_read`: the process is blocked waiting for input. Best effort and labelled so (`"confidence": "heuristic"`): output quiet for >= 400 ms and the last screen line ends like a prompt (`?`, `:`, `>`, `$`, `#`, `%`, `]`, `(y/n)`) while the PTY is alive. `idle` is the exact signal; `stdin_read` only saves a round trip.
- `timeout`: `timeout_s` elapsed with none of the above.
When several hold in one poll the order is `exited`, `matched`, `stdin_read`, `idle`, `timeout`. Waiting is implemented in the backend as a loop of cheap `read` bridge calls (250 ms), so no matching code runs in the Electron main process. Regex safety (Python `re` cannot be interrupted): pattern <= 200 characters, matched only against the new scrubbed text capped at 64 KB per poll, and rejected at parse time if it uses backreferences, lookarounds or a repeat nested inside an unbounded repeat (one function, walking `re._parser`, with its own test and a fallback to refusing the pattern if the private API is missing); `literal` is the encouraged form.

**Judging a `terminal_send` (a command typed into a terminal is judged like `shell_run`).** In this order, first refusal wins:
1. Owner and state: terminal live, not `user_attached`, not `user_dirty`. While the user has the terminal open in the Workspace window the agent cannot send (it can still read). Once the user has typed, `user_dirty` stays set until the agent sends `key=ctrl-c`, because the shell's line editor may hold text the agent never saw.
2. Shape: `text` is a single line <= 4 KB with no control characters (below 0x20 except tab), no ESC, no DEL, no newline; it must not contain a registered secret value (`register_secret`/`secret_values`).
3. `permrules.hardline(text)`: refused, every mode, no card (the same list `claude_hook` uses).
4. `permrules.resolve("shell_run", {"command": text, "cwd": terminal.cwd}, ...)`: `terminal_send` maps to the same `Subject("Bash", text)` in `subject_for`, so the user's allow, ask and deny rules and session grants for shell commands apply here with the same meaning (deny wins). It is a mapping in `permrules`, not a copy of the lists.
5. Floor: `ToolSpec.floor` is `shell_run`'s `_destructive` (kind `"shell"`): a card in Auto and Allow everything, `destructiveDefaults` in Manual, via `Toolbox.floor_action`; `touches_protected` (Grain's own data folder and app) is a refusal.
6. `route.check` with `name == "terminal_send"` treated as `shell_run`: while `codingRoute` is on, a line that would change a git work tree is refused with the usual text. There is no `guard_finish` diff for a terminal (the command outlives the call), so in a work tree the extension also refuses lines that start an opaque interpreter, shell, editor or pager (`python*`, `node`, `deno`, `bun`, `perl`, `ruby`, `sh`, `bash`, `zsh`, `fish`, `vim`, `vi`, `nano`, `emacs`, `less`, `man`) unless the line only verifies per `_only_verifies`. This closes the script/heredoc gap by refusing what cannot be judged, not by guessing.
7. Taint: `force_ask` when the run is tainted and the shell can reach out (`reaches_out(cfg)`) unless `trustExternalContent`, the same predicate as `shell_run`; `proposal_only` runs refuse (`unattended()` text).
8. The line is written as `text + "\r"` in one `write` bridge command; the `tool.pre`/`tool.post` hooks fire as for any tool, with `terminal_send` as the name and the text in `args`.

`terminal_open` with `command` runs steps 2 to 7 on that command before spawning. Because the shell is sandboxed, the sandbox, not this judging, is the boundary for what a judged line can do; judging stops the cases the sandbox allows but the user's floor forbids. Keys are never judged beyond the enum (`ctrl-c`, `ctrl-d`, `escape` and arrows write fixed bytes).

**Output scrubbing and redaction.** Every `read`/`wait_for` text passes, in order: control-sequence stripping (CSI, OSC, other ESC sequences, carriage-return progress collapsed to the last frame, NUL and C0 except `\n` and `\t`), `redact.scrub_command_output`, replacement of registered secret values, then the 16 KB tail-biased cut with `dropped` counting what was cut (and `from_cursor_lost: true` if `cursor < base`). `args` in `tool.post` carry the scrubbed text. Raw bytes are never stored by the backend; only the hub ring holds them, in main-process memory, and a killed PTY drops its ring.

**Taint.** Reading terminal output is reading untrusted program text: `terminal_read` and `terminal_wait_for` call `shell.taint(ctx, "terminal:<id>")` on every non-empty result, and `terminal_open` taints `"terminal:network"` when the shell profile allows network, as `shell_run` does. Taint makes later `terminal_send` calls ask (step 7) and blocks saving recipes (10.5.3).

**Limits.** At most 3 live terminals per owner and 6 for agents overall; `send` <= 4 KB and <= 30 sends per minute per terminal; read <= 16 KB; `wait_for` timeout 1 to 600 s and <= 4 concurrent waits per owner; ring 200 KB (`RING_CAP`); a terminal idle for 30 minutes with no owner activity is closed. Terminals close on owner conversation delete, `subagent.stop` for a child-owned one, owner `run.after` with a stop reason only for desk/job owners (a chat's terminal survives its replies), and app quit (`hub.killAll`).

**User takeover.** The Terminal card lists agent terminals under their coding session (or under "Agent terminals" when `session_id` is null), labelled with the owner and `sandboxed`. Clicking one attaches it in the existing `TerminalWindow` through a new main handler `term:attach(termId, cols, rows)`: the hub re-homes the entry from `senderId 0` to the main window's id, replays the ring, and the bridge flips `user_attached`. Detaching (closing the window) sets it back and the PTY keeps running, exactly as for coding sessions today. Anything the user types sets `user_dirty`. The user's own unsandboxed "Shell in worktree" stays a separate, user-only terminal.

#### 10.5.3 Part 3: recipes

A recipe is a saved, versioned coding-task setup.

**Fields (JSON in `coding_recipe_versions.body`).**

| Field | Rule |
|---|---|
| `name`, `description` | Unique name per recipe id. |
| `prompt` | Template with `{{input}}` placeholders. Plain substitution only, no expressions; an unknown placeholder fails at save. |
| `inputs` | `[{name, type: string|number|boolean|enum, required, default, max_len <= 4000, values?}]`; values are validated and length-capped before substitution; they are data in the prompt, never part of the verify command. |
| `repo_path`, `worktree` | `repo_path` must pass `check_repo`; `worktree`: `"new"` (default: its own branch/worktree as `start` does today, `branch_prefix`) or `"existing"` with a `worktree_path` that is a live worktree of the repo. |
| `driver` | `claude`, `opencode` or `pi`; omitted = the user's default harness at run time (recorded in the run). |
| `model` | Optional, validated by `MODEL_RE`. |
| `permission_mode` | `null`, `acceptEdits`, `auto` or `dontAsk`. `bypassPermissions` is refused in a recipe: it is chosen per start in a card, not saved. |
| `allowed_tools`, `denied_tools` | Lists of CLI tool patterns, passed as `--allowedTools` / `--disallowedTools` on `claude` starts and resumes. A recipe that sets either on `opencode` or `pi` is refused at save (those drivers have no such control); it is never silently ignored. |
| `max_steps` | 1 to 500, default 60. For `claude` it is a **tool-call cap** enforced by the PreToolUse counter in 10.5.5 (the CLI has no turn flag); the UI says "steps". Refused at save for drivers that cannot enforce it, unless `timeout_s` is set. |
| `timeout_s` | Wall clock for the whole run, <= 7200; at the deadline the session is stopped (`_stop`) and the run ends `timeout`. |
| `verify` | `{command, timeout_s <= 600, cwd?}`; required (a recipe without one is a "task", not a recipe). |
| `max_retries` | `0` or `1`. |

**Storage.** `coding_recipes(id, name, current_version, archived, created_at)`, `coding_recipe_versions(recipe_id, version, body, created_by user|agent, approved_at, created_at)` (immutable rows; a save inserts version n+1), `coding_recipe_runs(id, recipe_id, recipe_version, origin chat|palette|job, conversation_id, job_id, inputs, resolved, session_id, status, attempt, verify_exit, verify_tail, created_at, started_at, ended_at)`. `resolved` is the exact settings the run used: substituted prompt, repo, worktree path and branch, driver and model as resolved, permission mode, allowed/denied lists, step cap, timeouts, verify command and cwd, retry allowance, `claude` flags that differ from default (no secrets). It is written once, before the agent starts, and never edited; a retry appends `attempts[]` with the verify output tail it was fed.

**Tools** (group `shell`, all `available_fn` like `coding_session_start`): `coding_recipe_list` (`safe`), `coding_recipe_show` (`safe`), `coding_recipe_save` (`writes`; in `PROMPT_WRITES`, so a tainted run asks, and a version saved by the agent is `created_by: agent` with `approved_at = NULL`: it cannot run until the user approves that version in the UI, because a recipe carries permission settings), `coding_recipe_run` (`external`, `default = "ask"`, `force_ask` when tainted, same card and `proposal_only` refusal as `coding_session_start`; the card shows the `resolved` settings). Running reuses `CodingSessions.start`, so capacity, one-live-session-per-worktree and the floor settings apply unchanged. `coding_session_wait` still waits on the session; `coding_recipe_run` returns `{run_id, session_id}` and `coding_recipe_status(run_id)` (`safe`, taints, returns the run row) is the recipe-level view.

**Run lifecycle.** `working` (agent) then, when the session settles `done`, `verifying`, then `passed` or, after one optional retry, `failed`. Other ends: `agent_failed` (session `failed`/`stopped`/`needs_you` with no answer), `timeout`, `stopped`, `verify_blocked`. **Done means verify exited 0**, never the agent's claim: a session that reports done but fails verify is `failed`.
- Verify runs through the normal tool path: `dispatch_tool(toolbox, ctx, "shell_run", {"command": verify.command, "cwd": worktree, "timeout_s": ...})`, so the hardline list, the destructive floor (`Toolbox.floor_action`), user rules, `route.check` (tests/builds are allowed), the OS sandbox and the tool hooks all apply. The exit code is the `exit_code` field of that result. In an unattended run a verify that needs a card ends `verify_blocked` with the reason; it never runs unjudged.
- Retry (`max_retries = 1`): on a non-zero exit, the last 4 KB of the scrubbed verify output (marked as program output, tainted data) is sent with `CodingSessions.send` as the next message ("verify failed with exit N: ..."), the run goes back to `working` once, and verify runs once more. There is no second retry and no retry after `timeout`.
- The session's `on_finish` hook drives the state machine, so it survives an app restart: a run in `working`/`verifying` is reconciled by `_recover` and the run table on `app.start`.

**Entry points.**
- Chat: the four tools above; the directive for `codingRoute` names `coding_recipe_run` when recipes exist.
- Palette: command "Run coding recipe..." (`ui.command`, PR 9) opens a picker and an inputs form that shows the resolved settings; the confirm button is the user's approval and calls `POST /coding/recipes/{id}/run` with `origin: "palette"`.
- Job: if PR 11's `api.trigger` exists, the extension registers trigger kind `coding_recipe` (`{recipe_id, version, inputs, cron}`). Creating such a job is the approval and its card shows the `resolved` preview; the job pins the version (an edit makes a new version and the job keeps the old one until the user re-pins it); `permission_mode` for a job run is capped at the recipe's and `bypassPermissions` is impossible; a run that would exceed `CODING_SESSION_MAX_CONCURRENT` waits, then fails `queued_timeout` after 10 minutes. A fire emits `job.fired`/`job.done` with the recipe run's outcome. If `api.trigger` is not in the host, jobs are not offered in this PR (an unattended chat run cannot start a coding session today, `unattended()`), and the done-when item is marked not applicable.

#### 10.5.4 Part 4: headless structured runs

Tool `coding_run_structured` (group `shell`, danger `external`, `default = "ask"`, `force_ask` when tainted, `proposal_only` refused): a one-shot, non-interactive `claude` run whose result is validated against a JSON Schema. Only the Claude driver supports it; `opencode`/`pi` are refused with that text (their flags were not checked).

| Argument | Rule |
|---|---|
| `prompt` | Required. Sent on **stdin**, not as an argument (observed: `--tools` is variadic). |
| `schema` | Required JSON Schema object, <= 16 KB, checked with `jsonschema.Draft202012Validator.check_schema` before anything runs; a schema that references remote `$ref`s is refused. |
| `cwd` | Folder to run in (a repo, a worktree or a temp folder); default a fresh temp folder. |
| `tools` | Allowlist of CLI tool names, default `["Read", "Grep", "Glob"]`. Any entry that can write or execute (`Edit`, `Write`, `Bash...`, `NotebookEdit`, `WebFetch`) requires an explicit mention and, with `codingRoute` on, a `cwd` that is not a git work tree unless it is a worktree Grain made for a session. |
| `max_steps` | 1 to 100, default 20. |
| `timeout_s` | 5 to 900, default 300. |
| `max_budget_usd` | Optional, passed as `--max-budget-usd`; default from a setting. |
| `model` | Optional (`--model`). |

**Command.** `claude -p --output-format json --json-schema <schema> --no-session-persistence --permission-mode dontAsk --tools <list> --allowedTools <list> --strict-mcp-config --disable-slash-commands --settings <floor> [--model m] [--max-budget-usd n]`, prompt on stdin, `cwd` as given, the `claude_env()` environment, run through `run_fixed(sandboxed=False)` like a session start (it runs on the user's account, outside the OS sandbox, which is why the danger is `external`). `--permission-mode dontAsk` makes any permission that would prompt a denial, since nothing can answer in `-p`; `--settings` is `floor_settings(...)` (the same deny/ask rules and the Bash hook as sessions) plus the step counter of 10.5.5, never `lifted`. Nothing else is added: no `--dangerously-skip-permissions`, no `--allow-dangerously-skip-permissions`, no `--permission-prompts`, no `--bare` (it skips hooks).

**Result handling, exactly.** Parse stdout as one JSON object. Then, in order, the first failure returns a `tool_error` and no value:
1. Process error, timeout (process group killed), or output that is not one JSON object: `{"error": "run_failed"}` with the scrubbed stderr tail.
2. `is_error` true or `subtype != "success"`: `{"error": "agent_error", "subtype": ...}`.
3. `num_turns > max_steps + 1`, or the step counter tripped: `{"error": "step_cap"}`; the output is discarded even if it validates.
4. No `structured_output` key, or it is `null`: `{"error": "no_structured_output"}`. `result` (the text form) is never parsed as a stand-in.
5. `Draft202012Validator(schema).iter_errors(structured_output)` yields anything: `{"error": "schema_mismatch", "errors": [first 5, each "path: message" with the offending value omitted]}`.
Only then `{"ok": true, "value": <structured_output>, "num_turns", "cost_usd", "permission_denials": [...]}`. A run is retried by nothing: the caller sees the error.

**Taint.** The value is produced by an agent that may have read untrusted files, so the call taints the run (`"coding_run_structured:<sha8 of schema>"`); a schema proves shape, not safety, and the tool description says so. The call is journaled like other external tools.

#### 10.5.5 Part 5: hook bridge

Claude Code's own tool calls inside Grain-started sessions are reported into the hook bus, through the existing `claude_hook.py` path.

**Hook ids** (also in section 5.12): `coding.tool.pre` (gate) and `coding.tool.post` (emit).

**Wiring.** `floor_settings(worktree, lifted, bridge=None)` takes an optional `bridge = {port, session_id, key, gate: bool, post: bool, cap: int | None, counter: path}`. Without it the `--settings` JSON is byte-identical to today's (the PreToolUse `matcher: "Bash"` entry only). With it:
- `PreToolUse` matcher widens from `"Bash"` to all tools **only if** `gate` or `cap` is set (extension subscribers exist or the run has a step cap); otherwise it stays `"Bash"`, so the default install pays nothing for Edit/Read calls. The match-all matcher syntax and the exact event fields (`tool_use_id`, `session_id`, `tool_response`) are not covered by `--help`; the implementer records a real PreToolUse/PostToolUse event from the installed CLI as a test fixture before relying on them. The fields `claude_hook.decide` reads today (`tool_name`, `tool_input.command`, `cwd`) are known.
- A `PostToolUse` entry is added only if `post` is set (a `coding.tool.post` subscriber exists).
- The hook command takes new optional arguments: `--bridge http://127.0.0.1:<port>`, `--session <id>`, `--gate`, `--cap N --counter <file>`; the per-session key goes in the command's environment prefix (`GRAIN_HOOK_KEY=...`, next to `PERSONAL_OS_DATA_DIR`). The key is random per session, stored in `coding_sessions.hook_key`, authorizes only `POST /coding-hooks/{session_id}/pre|post` for that session while it is live (constant-time compare, refused after the session ends, exempt from the app-token check for that path only, and loopback only). It cannot read or change anything.

**Pre path (`main()`).** In order: (1) the existing local `decide()` (hardline, protected paths, destructive floor), unchanged; (2) if `--cap`, increment the counter file and add `deny` ("Grain's step limit for this run was reached") above the cap; (3) if `--gate` or the session has any subscriber, POST the scrubbed event to `/coding-hooks/<id>/pre` with a **3 s** socket timeout (the hook's own `timeout` stays 10 s); the backend runs `bus.gate("coding.tool.pre", payload)` (2 s bus budget) and returns `{verdict: "deny"|"ask"|null, reason}`; (4) the printed decision is the strictest of the local and bridge verdicts, `deny` over `ask` over nothing. **It never prints `allow`**, never `updatedInput`, never anything that skips Claude Code's own prompt: no verdict means no output, as now. If `--gate` is set and the backend cannot be reached or does not answer in time, the hook prints `ask` ("Grain's extension check did not answer"), so a gated session blocks toward the user (`needs_you`) instead of passing; if `--gate` is not set the bridge is best effort and silent. Background sessions outlive the app: with the app closed and `--gate` set, tool calls ask until the app returns. A subscriber added after a session started applies from its next start or resume (`floor_settings` is recomputed there); the row records the `gate` flag it was started with.

**Lifted sessions** (`bypassPermissions`, Allow everything): the floor hook is dropped today and stays dropped. The bridge then reports events for observers and honors a `deny` from a gate, but an `ask` is not shown (Allow everything has no asks) and a bridge failure does nothing. Documented, tested.

**Post path.** A fire-and-forget POST to `/coding-hooks/<id>/post` with a 1 s timeout; exit 0 and no stdout in every case; the backend `bus.emit("coding.tool.post", payload)`. The hook cannot affect the session.

**Payloads** (snapshots, strings credential-scrubbed and cut to 2 KB each, total <= 16 KB; `tool_input` values that are file contents are cut):
`coding.tool.pre`: `{session_id, agent: "claude", tool, input, cwd, worktree, permission_mode, lifted, call_id?, conversation_id, run_id}`.
`coding.tool.post`: the same plus `{result_preview, error?, duration_ms?}`.

**Rules that keep it narrow.** Gate verdicts are `Deny`/`Ask` only (the bus rejects anything else as a failure, which becomes `Ask`); a handler cannot see another handler's verdict; the route has no field for approving, answering a permission, changing the mode or editing input; subscribing to `coding.tool.*` needs `permissions: ["coding:read"]` (payloads include file paths and command text); gate subscribers also need `hooks:gate` (built-in and user tier; third-party only if decision 5 is taken). Events are also appended to `coding_session_events` only when the existing timeline parser did not already record the call (no duplicates).

**Latency.** Local path unchanged (<= the current interpreter start). With a gate subscriber: p95 added < 150 ms on loopback, hard ceiling 3 s then `ask`. Measured in the dev panel by the existing hook latency columns.

#### 10.5.6 Host additions this step needs

| Addition | Where | Rule |
|---|---|---|
| `ExtensionScope.precheck(fn)` | `host.py`, `Toolbox.precheck`, subagent loop | Returns a refusal string or None; cannot approve; disposable; inert while the extension is disabled. |
| `ExtensionScope.directive(fn, *, version)` | `host.py`, `app.py` hints, `subagents.py` | Built-in tier only; unwrapped text at the old hint position; version in `stable_hash`. |
| Per-tool `after` anchor in `contributes.tools[]` | `manifest.py`, `Toolbox` registration | Places the tool right after the named existing tool; unknown anchor is a validate error. |
| `terminal:control` permission, terminal hub service | section 7, section 2.2 | Built-in only in this step; it is the only way to reach the bridge. |
| `permrules.subject_for` and `ToolSpec.floor` mapping for `terminal_send` | `permrules.py`, `tools.py` | Same subject and floor as `shell_run`; no copied lists. |
| Generic `owe_wake(conversation_id, text, wake)` | `app.py` | Replaces `_coding_owed`/`_wake_coding`; core stops naming coding. |
| `jsonschema>=4.20` | `backend/pyproject.toml`, `uv.lock` | Declared dependency. |
| `@xterm/headless` | `package.json` | Pinned; only for `screen` reads. |
| Ledger steps `ext:coding:1` to `ext:coding:4` | `extensions/coding/migrations/` | 1 baseline `IF NOT EXISTS` of `coding_sessions` (final shape) and `coding_session_events`; 2 recipe tables; 3 `coding_terminals`; 4 `ALTER TABLE coding_sessions ADD COLUMN hook_key TEXT` and `recipe_run_id TEXT`. |

#### 10.5.7 Done when

Port (Part 1)
1. The full ordered tool-name list, each tool's description, schema, danger, group and default mode are byte-identical to the parent commit (golden test), including `opencode_run` mid-list and the seven `coding_session_*` at the end.
2. `modules`/imports: `grep -rn "coding_route\|codingagents\|coding_events" backend/personal_os --include=*.py` finds nothing outside `extensions/coding/` and `claude_hook.py` comments; `opencode.py`, `opencode_mcp.py`, `opencode_usage.py`, `pi.py` live under the package; `app.py` has no `/coding-sessions` handlers, no `_coding_*` plumbing other than the generic `owe_wake`.
3. `backend/tests/golden/chat_prefix.txt` is byte-identical with `codingRoute` on and with it off; `context_used` unchanged.
4. Every existing coding test passes with only moved import paths: `test_coding_route`, `test_coding_events`, `test_codingagents*`, `test_opencode`, `test_pi`, `test_coding_summary`, plus a new test per call site that the refusal still holds for chat, desk, worker, subagent, workflow step and `run_python` bridge, and that it is refused **before** an approval card (precheck) and again at `tool.pre` if the precheck were bypassed.
5. A `shell_run` that writes through a script into a repo is still reverted and refused (`guard_finish` via `tool.result`); a snapshot is dropped on error.
6. A throwing, hanging or `Allow`-returning `tool.pre` handler from the extension never lets a routed call through (bus rule tests).
7. Disabling the extension hides the eight tools and the Terminal tab, 404s the routes, removes the directive and routing; enabling restores them; an install whose settings show the coding tools on keeps them on after upgrade; the three core ledger steps and `ext:coding:1` are idempotent on a database that already has the tables (test with an existing-install fixture) and the export includes the tables.
8. `claude_hook.py` is unmodified except for the optional arguments of 10.5.5; `floor_settings(...)` without `bridge` is byte-identical to the parent's output (test), including `lifted`.
9. Frontend: tool card registered through `ui.toolCard`, no static card import left, `store.ts` has no `codingSessions` state; `terminal.spec.mjs`, the coding specs and the sidebar/settings specs pass.

Terminal control (Part 2)
10. Tool schemas as in the table; `terminal_wait_for` rejects a missing or out-of-range `timeout_s`; the `reason` is always one of the five (property test over scripted PTY output: match, exit, idle, prompt, silence, and the ordering rule).
11. A second owner (worker, subagent, another chat) gets the not-found error for read, send, wait_for and close; ids are not guessable and not probeable (same error text); a chat's later reply keeps access.
12. `terminal_send` judging: tests for each step in order, including hardline refusal, a deny rule, the destructive floor card in Auto and Allow everything, `codingRoute` refusal and the interpreter refusal in a work tree, secret-value refusal, control characters and multi-line refusal, tainted-and-networked ask, `proposal_only`, and that `terminal_open(command=...)` is judged identically.
13. A `user_attached`/`user_dirty` terminal refuses sends until `ctrl-c`; reads still work.
14. Output goes through stripping, `scrub_command_output` and secret replacement; limits (bytes, per-owner and global terminals, send rate, idle close) enforced; taint recorded as `terminal:<id>`.
15. The shell is spawned from the backend-built sandboxed argv; with no sandbox the tools are not offered; the main process rejects a relative argv, a missing cwd or a request without the token. `term:attach` re-homes a control PTY into the Workspace Terminal window and replays the ring; e2e (`terminal.spec.mjs` with `GRAIN_TERM_FAKE_CMD=/bin/cat`) covers agent open, send, read, user takeover and close.
16. No second PTY implementation: `node-pty` is imported only in `src/main/terminals.ts`.

Recipes (Part 3)
17. Version immutability (saving creates n+1), agent-saved versions cannot run until approved, tainted save asks, `bypassPermissions` and allowed/denied tools on `opencode`/`pi` refused at save, unknown placeholder refused.
18. A run's `resolved` JSON equals what was actually passed to `CodingSessions.start`/the argv (test compares), is written before the agent starts and never changes.
19. A fake session reporting `done` with a failing verify ends `failed`; with exit 0 ends `passed`; one retry feeds back the scrubbed tail and verifies once more; a second failure ends `failed`; a blocked verify ends `verify_blocked`; a stopped or timed-out run never reports `passed`; verify goes through `dispatch_tool("shell_run")` (hook fires, destructive floor applies).
20. Start from chat (tool), palette (e2e) and, if `api.trigger` is present, as a job pinned to a version; a restart mid-run reconciles the state.

Headless (Part 4)
21. The argv contains exactly the flags in 10.5.4, every one listed in `claude --help` above, prompt on stdin; a test with a fake `claude` covers each of the five error branches and the success value; `result` text is never used as a fallback; an invalid schema or remote `$ref` is refused before spawning; the step cap and timeout kill the process group.
22. An opt-in smoke test against the real CLI (skipped without `claude`) with a tiny schema checks the observed result fields still exist.

Hook bridge (Part 5)
23. With no subscriber the generated `--settings` hook block is unchanged (matcher `"Bash"`, no PostToolUse). With a gate subscriber the matcher widens; with a post subscriber PostToolUse appears. A recorded real hook event fixture is checked in.
24. The hook never prints `allow` or `updatedInput` for any input (property test); strictest-wins merge of local and bridge verdicts; a gate exception, timeout or non-`Deny`/`Ask` return becomes `ask`; `--gate` plus unreachable backend prints `ask`; without `--gate` it is silent; lifted sessions follow 10.5.5.
25. No hook field can approve, answer a permission, change the mode or rewrite input; the route is limited to its own session and key; payloads are scrubbed and size capped; latency target measured.
26. Section 5.12 and the tables in sections 3.4, 4, 8 and 9 agree with the implementation (hook ids, payloads, permissions).

Repo gates: backend suite, `npm run typecheck`, `npm test`, the e2e suite for terminal, coding, health, todos and settings/sidebar specs, with no new red against the recorded baseline.

#### 10.5.8 Not in this step

Third-party loading (PR 18); structured runs on `opencode`/`pi`; terminal access for non-coding extensions; any hook that can allow or approve; `stream-json` turn-kill for headless runs (a follow-up once it is confirmed to carry `structured_output`); merging or pushing a recipe's branch (the main agent still does that with `git push`/`gh`).

## 11. Open questions and decisions for the user

| # | Question | Options | Recommendation |
|---|---|---|---|
| 1 | Language for third-party backend extensions | Python (matches the backend; SDK is natural) / TypeScript (matches the frontend half; one language to author) / language-neutral JSON-RPC over stdio | Define the wire protocol first (JSON-RPC over stdio, the same shape MCP uses), ship the Python SDK first. Built-ins stay Python in-process. |
| 2 | Built-in only first? | Built-in only now / load third-party in the same release | Built-in only. Third-party loading is PR 18 after a separate design review. |
| 3 | Fixed slots vs raw CSS/React access | Slots plus tokens / raw access to component internals | Slots plus design tokens. Slots survive refactors; raw access breaks on every one. Raw CSS only as the opt-in "unsupported" escape hatch, never for the third-party tier. |
| 4 | In-process vs out-of-process | All in-process / third-party out-of-process / all out-of-process | Built-ins in-process; user tier in-process in phase 1 with a clear "installed software" label; third-party out-of-process. |
| 5 | Narrowing gate hooks for third-party | None / narrow-only with explicit grant | Narrow-only with an explicit grant, fail toward ask. A spend guard and an audit gate need it; nothing can ever return Allow. |
| 6 | Versioning | Integer `apiVersion` major + additive payload changes / semver ranges | Integer major, additive-only payloads, deprecation over two releases, `minHost` optional. |
| 7 | Hook handlers on the event loop | async only / sync allowed in a thread | Both; sync handlers run in a worker thread with the same timeouts. |
| 8 | What survives uninstall | Delete data / keep tables | Keep by default; explicit "Delete data" action; backups still include the tables. |
| 9 | Extension distribution | Folder drop / git URL with hash pin / registry | Folder and git URL with pinned hash; no registry until the review process exists. |
| 10 | `hooks:conversation` / raw content access | Always granted to enabled extensions / per-extension grant | Per-extension grant, off by default, shown on the card. |
| 11 | Telegram default | Built-in off until paired / on | Optional extension, enabled when a bot token exists; existing installs unchanged. |
| 12 | Full loop unification | Do it before hooks / do the seam only | Seam only now; full unification as a separate effort after PR 9. |

## 12. Sources

Grain code and docs: `backend/personal_os/app.py`, `tools.py`, `subagents.py`, `workflows.py`, `runs.py`, `context.py`, `modules/__init__.py`, `mcp_servers.py`, `docs/module-manifest.md`, `src/renderer/src/shell/types.ts`, `src/renderer/src/store.ts`. Branch `feat/default-off-views` (`llm.py` `view_on`, `TOOL_VIEWS`).

External (fetched 2026-10-14 for the survey; items marked unverified were not confirmed from a primary source):
- pi coding agent extensions: https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/extensions.md and `packages.md` in the same directory. The full per-event order for session and provider events, and the compaction event names, are unverified.
- Claude Code hooks: https://code.claude.com/docs/en/hooks ; plugins reference: https://code.claude.com/docs/en/plugins-reference ; marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- OpenCode plugins: https://opencode.ai/docs/plugins/ . Other named hooks and the experimental compaction hook are unverified.
- OpenClaw: https://docs.openclaw.ai/ ; https://docs.openclaw.ai/plugins/building-plugins ; https://docs.openclaw.ai/plugins/hooks . The full hook catalog page was not read (unverified).
- OpenClaw registry incidents: https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html ; https://thehackernews.com/2026/02/openclaw-integrates-virustotal-scanning.html ; https://esecurityplanet.com/threats/hundreds-of-malicious-skills-found-in-openclaws-clawhub . The figure of 341 malicious of 2,857 skills is from the first report. A larger audit count (386) and a figure of 1,103 of 14,706 appear only in secondary sources (unverified). A CVE number cited by one secondary guide is unverified and not used here.
- Goose extensions: https://goose-docs.ai/docs/getting-started/using-extensions . Goose has no documented hook system.
- VS Code: https://code.visualstudio.com/api/references/activation-events ; https://code.visualstudio.com/api/advanced-topics/extension-host
- Obsidian: https://obsidian.md/help/plugin-security ; https://docs.obsidian.md/Plugins/Releasing/Plugin+guidelines . Several `Plugin` method names are unverified from the fetch.
- Raycast: https://developers.raycast.com/information/manifest ; https://developers.raycast.com/basics/prepare-an-extension-for-store
