# Settings simplification and magic numbers

Audit date 2026-10-06, branch `worktree-settings-simplify` (base `ec906933`). Read-only research: no code was changed. This document is the one place other products are named (`docs/research/` exception in CLAUDE.md).

Three goals: (1) few plain Settings sections plus ONE collapsed Advanced area, (2) no user-facing magic numbers: derive them or centralize them in a new `backend/personal_os/limits.py`, (3) one `permissionMode` (`auto` | `manual` | `allow_all`) replacing `skipPermissions` / `autoReview` / `unattendedApprovals` in the UI.

## Removed budgets (supersedes every budget, cap and spend-alert entry below)

Branch `worktree-remove-budgets`. Grain has no budgets any more: nothing caps a reply's rounds, tokens, cost or wall-clock time. The only things that stop work are **stuck detection** (`REPEAT_LIMIT`, `TOOL_ERROR_LIMIT`, `stuck.StuckDetector`, `permrules.DenialStreak`), **hang detection** and **per-request timeouts**. The tables below are the audit as it stood before this change; where they describe a budget, this section wins.

What replaced the numbers:

| New | Value | Where | Meaning |
|---|---|---|---|
| `JOB_IDLE_SECONDS` | 1800 | `limits.py`, `app._run_job` | An unattended run that published no model or tool event for this long (and is not waiting on an approval) is cancelled and failed with "stopped after N minutes with no model or tool activity". Hang detection, not a length cap: a long healthy job is never cut. `Run.last_active` is stamped on every publish and status change. |
| `CONTEXT_SHARES` | memories .012, graph .006, chunks .016, activity .006, meetings .006, pinned .023, profile .008, skills .012 | `limits.py` | Share of the model's context window each injected block may take. They match the old fixed token counts (1500, 800, 2000, 800, 800, 3000, 1000, ~1500 for skills) at the 128k fallback to within 4%. |
| `context_shares(window)` | `int(window * share)` per block | `limits.py` | The single hook memory and context code use (`context.build_context` reads it once per turn). A 8k window gets 96 memory tokens, a 1M window gets 12000. Pinned files take `shares["pinned"] * 4` characters in all, a third of that per file. Approved skills are inlined while their text fits `shares["skills"]`, else the manifest is sent. |

What was removed (old value, where it lived):

| Removed | Old value | Lived in |
|---|---|---|
| `maxToolRounds` / `MAX_ROUNDS_HARD` / `limits.max_rounds()` | 25 default (1-60), hard backstop 100 | `limits.py`, `llm.DEFAULT_SETTINGS`, `app.Budget` |
| `maxRunTokens` / `RUN_TOKENS` | 200 000 per reply | same |
| `maxRunSeconds` / `RUN_SECONDS` | 300 s per reply, approvals excluded | same |
| `JOB_MAX_ROUNDS`, `JOB_RUN_TOKENS`, `JOB_RUN_SECONDS`, `app.JOB_BUDGET`, `_job_caps`, `_check_job_budget` | 8 rounds, 60 000 tokens, 240 s | `limits.py`, `app.py` |
| `JOB_HARD_SECONDS` | 1800 s wall clock (`asyncio.wait_for`) | replaced by the idle watchdog (`JOB_IDLE_SECONDS`) |
| Per-job `budget` and `desk_budget` fields | tighten-only overrides of the job caps | still accepted on the API and ignored; the columns stay in the schema and are never written |
| `subagentMaxRounds` / `SUBAGENT_MAX_ROUNDS` | 12 per child (1-60) | `limits.py`, `subagents.py` |
| Per-agent `steps` (agent definition frontmatter and column) | 1-60 round cap per definition | ignored when parsing; stored values are never read |
| `PARENT_RESERVE`, the `cost_cap` and `max_steps` child exits | children stopped at 60% of the parent's tokens or time; "charged to the parent" accounting | `subagents.py`; usage still rolls up for display |
| `deskMaxTurns` / `DESK_MAX_TURNS`, desk `budget {maxTurns}` | 12 turns, tightenable per desk | `limits.py`, `app._desk_caps`, `_chain_kind` |
| `BUDGET_STOPS` ("rounds", "tokens", "time" partials), `BUDGET_STOP`, `TIME_STOP`, the 60% `SOFT_NUDGE` | stop texts and a nudge when 60% of a budget was used | `app.py` |
| `Budget.arm_deadline` for the main stream | bounded the provider stream by what was left of `maxRunSeconds` | `llm.stream_deadline` is now set only around the closing answer (`FINAL_ROUND_SECONDS`) |
| `codingSessionTimeoutMinutes` / `CODING_SESSION_TIMEOUT_MINUTES` | 30 min (1-1440) | `limits.py`, `codingagents.py`; OpenCode now launches with no timeout. App shutdown already SIGTERM/SIGKILLs every shell job group, Stop ends a session, and a session a crash orphaned is recorded `orphaned` with a kill; the timeout's watcher lived in the backend process, so it never reaped crash orphans anyway. |
| `contextBudget` | memories 1500, graph 800, chunks 2000, activity 800, meetings 800, pinned 3000, profile 1000 tokens | `llm.DEFAULT_SETTINGS`; now `CONTEXT_SHARES` |
| `skillsInlineBudget` / `SKILLS_INLINE_BUDGET` | 6000 characters | now `CONTEXT_SHARES["skills"]` |
| `memory_limits.PROFILE_WINDOW_SHARE` | 0.02 | now `CONTEXT_SHARES["profile"]` (.008) |
| `usageAlerts`, `usage.alert_state`, `GET /usage` `alerts`, the `usage_alert` event | $/day and $/month notices | usage and cost reporting stay |

Migration 20 `drop_budget_settings` deletes the stored `maxToolRounds`, `maxRunTokens`, `maxRunSeconds`, `subagentMaxRounds`, `deskMaxTurns`, `codingSessionTimeoutMinutes`, `contextBudget`, `skillsInlineBudget` and `usageAlerts` rows. `PUT /settings` already ignores keys that are not in `DEFAULT_SETTINGS`, so an old client sending them gets 200 and nothing is stored. Old run rows can still carry `partial` values `rounds`, `tokens`, `time`, `cost` and a `budget` snapshot with `max_*` keys; readers (`resume.py`, `job_history._timed_out`) tolerate them.

Kept on purpose (not reply budgets):

- `VOICE_LOOP_MAX_TURNS` (20): the hands-free voice loop is a safety stop, because a microphone left on would otherwise keep a chat looping with nobody there.
- `FILE_SNAPSHOT_BUDGET_MB` and the `RETAIN_*` days: disk retention, not run limits.
- `LLM_IDLE_SECONDS`, `FINAL_ROUND_SECONDS`, `SUBAGENT_STALE_SECONDS`, `SUBAGENT_TOOL_SECONDS`, `SHELL_TIMEOUT_SECONDS`, `REVIEW_TIMEOUT_SECONDS`: hang detection and per-request timeouts.
- `MAX_UPLOAD_MB`, the zip-bomb guard, concurrency limits (`deskMaxLive`, `subagentMaxConcurrent`, `SUBAGENT_MAX_DEPTH`, `CODING_SESSION_MAX_CONCURRENT`), retrieval and browser limits.
- The one `max_tokens` the app sends itself is the connection probe in `setup.py` (`max_tokens: 1`, falling back to `max_completion_tokens: 16`): a key and connection check, not a reply. Without it a reasoning model thinks at length and the probe's timeout reports a false failure.

`max_tokens` on replies: never a number Grain picks. `llm.stream_chat` and `llm.complete` send it only when the provider requires it (`llm.requires_max_tokens`: the base URL host is the Anthropic API) and the model's own maximum output is known. `usage.Pricing` captures it as `max_output_tokens` from the proxy's `/model/info` (`model_info.max_output_tokens`) or, for the Anthropic API, from `GET {base}/models` (`max_tokens`). Every other provider gets no field.

## 0. Findings that change the plan

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| 1 | `PUT /settings` only accepts keys present in `llm.DEFAULT_SETTINGS`, and numeric type checking uses `DEFAULT_SETTINGS[key]`'s type. | `app.py:865-879` | Deleting a key from `DEFAULT_SETTINGS` also stops a user override being saved. Keep a separate `limits.OVERRIDABLE` set and make `put_settings` + `_check_numeric_setting` consult it (`app.py:817-830`). |
| 2 | `public_settings()` merges defaults over stored values, so a reader cannot tell "user set 128000" from "default 128000". | `llm.py:75`, `app.py:770-775` | An override is only honourable if the key is absent from `DEFAULT_SETTINGS` and the default lives in `limits.py`. Readers become `limits.get(settings, "key")`. |
| 3 | The Settings modal saves **only changed fields** (`changedFields(next)`), so existing installs have a stored value only if the user edited it. | `SettingsModal.tsx:195-199, 295-303` | Honouring stored values as overrides is safe: nobody has a stored value they did not choose. Exception: the legacy "cleared field saves default" path (`SettingsModal.tsx:288-291`) is removed with the UI. |
| 4 | `compaction.window_for` already takes `min(contextWindow setting, proxy max_input_tokens, learned-from-overflow)`. Because `contextWindow` defaults to 128000, a 1M-token model reported by the proxy is still capped at 128k. | `compaction.py:218-224`, `app.py:338, 1983, 2451`, `usage.py:29-55` | Dropping the default makes the proxy value effective for the first time. Direct providers (no `/model/info`) fall back to 128k and are corrected downward by `note_overflow`. |
| 5 | `app.py:368` (`_message_too_long`) and `src/renderer/src/lib/messageLimit.ts` use `contextWindow` directly, not `window_for`. | `app.py:363-368` | Switch both to the derived window, or the per-message cap stays pinned to 128k. |
| 6 | The review gate is skipped when `skip_permissions` is on, and only reviews calls that would already run (`mode == "on"`). It fails closed (reviewer error -> `ask`). | `app.py:3019-3026`, `autoreview.py:44-63` | `auto` mode cannot be expressed by setting `autoReview` + `skipPermissions` together; the `not skip_permissions` condition must change (see E5). |
| 7 | Nothing in the backend uses `os.cpu_count`, `psutil` or memory probes. | grep of `backend/personal_os` | Resource-derived limits are new code, stdlib only (`os.cpu_count`, `os.sysconf`). |
| 8 | `McpSettings` is not mounted in the Settings modal (it lives in `LibraryView` and `features/health/Sources.tsx`). `insights.DEFAULTS` is edited from `ActivityView`, not Settings. `mode` is a legacy migration key read once by `store.ts:2029`. | grep | Integrations in Settings should link to the Library for connectors; insights keys are out of Settings scope. |
| 9 | `maxToolRounds` and `maxRunTokens` cannot be raised by a scheduled job; `JOB_BUDGET` (8 rounds, 60k tokens, 240 s) and `JOB_HARD_SECONDS` (1800) are separate hardcoded caps. | `app.py:1527-1534` | Those stay as named internal constants. |
| 10 | Run token budget sums prompt + completion tokens of every model call (`Budget.add`), so the default 200k is only about 6-8 rounds of a 30k context. | `app.py:1618-1620` | `maxRunTokens` is a real cost lever, so it stays user-editable in Advanced. |

## A. Settings audit

Current tabs (`SettingsModal.tsx:43-72`): Provider & cost, Permissions, Workspace folders, Autonomy, Memory | Behavior, Modules, Integrations, Meetings | System access, Data. Target: **Model, Permissions, Workspace folders, Integrations, Texting, Appearance, System access, Advanced** (collapsed, last).

Reader notation: `be N / fe N` is the count of non-test references in `backend/personal_os` / `src/{main,renderer,shared}`; a `file:line` is the decisive reader. "UI only" means the backend never reads it (renderer-applied).

Decision vocabulary: **Visible(section)**, **Adv(group)**, **Hide** (removed from UI, default in `limits.py`, stored value still honoured), **Derive**, **Dead**.

Advanced groups: **Assistant behaviour**, **Approvals**, **Files and web**, **Memory and search**, **Spending**, **Desks and background**, **Mail, calendar and plans**, **Voice and shortcuts**, **Layout**, **Data and support**, **Developer**.

### A1. `llm.DEFAULT_SETTINGS`

| Key | Where shown now | Readers | Decision | Label / help (plain language) |
|---|---|---|---|---|
| baseUrl | Provider tab | be 26 / fe 25; `llm.py:_url` | Visible(Model) | "Provider address" / "Where Grain sends model requests." |
| apiKey | Provider tab | be 16 / fe 19; `db.py:20` secret | Visible(Model) | "API key" / "Stored on this Mac." |
| defaultModel | Provider tab | be 46 / fe 15; `subagents.py:692` | Visible(Model) | "Chat model" / "Used for new chats." |
| provider | onboarding/Provider | be 92 / fe 94 | Visible(Model), implied by address | (picked in setup) |
| onboardedAt | none | be 4 / fe 1 | keep, internal | none |
| systemPrompt | Behavior tab | be 4 / fe 3 | Adv(Assistant behaviour) | "Standing instructions" / "Added to every chat." |
| extractionModel | Memory tab | be 27 / fe 4; `autoreview.py:49` | Adv(Assistant behaviour) | "Helper model" / "Writes titles, memories and suggestions. Empty uses the chat model." |
| fastModel | Provider tab | be 2 / fe 6; `router.py` | Visible(Model) | "Fast model" / "Used for short, simple messages. Leave empty to always use the chat model." |
| autoRoute | Provider tab | be 1 / fe 3 | Visible(Model), same row as Fast model | "Use the fast model for short messages" / "New chats start this way." |
| consolidateEvery | Memory > Advanced | be 1 / fe 2; `learn.py:842` | Hide | const `MEMORY_TIDY_EVERY = 25` |
| autoLearn | Memory tab | be 8 / fe 8 | Adv(Memory and search) | "Learn from chats" / "Save useful facts after replies." |
| followUps | Memory tab | be 1 / fe 4 | Adv(Assistant behaviour) | "Suggest next questions" / "Show a few follow-up chips under replies." |
| autoTitle | Memory tab | be 1 / fe 3 | Adv(Assistant behaviour) | "Name new chats" / "Write a short title after the first reply." |
| fileSnapshots | none | be 1 `filesnap.py:62` | Hide | on; "Keep a copy before the assistant overwrites a file" is covered by the Undo behaviour; no toggle |
| fileSnapshotMaxBytes | none | `filesnap.py:65` | Hide | const `FILE_SNAPSHOT_MAX_BYTES` |
| fileSnapshotRetainDays | none | `filesnap.py:257` | Hide | const `FILE_SNAPSHOT_RETAIN_DAYS` |
| fileSnapshotBudgetMB | none | `filesnap.py:258` | Hide | const `FILE_SNAPSHOT_BUDGET_MB` |
| toolReadRetries | none | `tools.py:926` | Hide | const `TOOL_READ_RETRIES` |
| parallelReads | none | `app.py:2403` | Derive | `worker_slots()` |
| stuckDetection | none | `app.py:2355` | Hide | on, const; the new round limit depends on it |
| learnStyle | Memory tab | be 3 / fe 9 | Adv(Memory and search) | "Learn how I write" / "Keep a profile of your writing so drafts sound like you." |
| theme | Behavior | UI only (fe 22) | Visible(Appearance) | "Theme" / "System follows your Mac." |
| accent | Behavior | UI only (fe 18) | Visible(Appearance) | "Accent colour" |
| mode | none | `store.ts:2029` legacy | Dead after one release | none |
| gatherShortcut | Behavior > Advanced | UI/main (fe 11) | Adv(Voice and shortcuts) | "Bring widgets to front" |
| quickCaptureShortcut | Behavior > Advanced | fe 8 | Adv(Voice and shortcuts) | "Quick note shortcut" |
| quickAskShortcut | Behavior > Advanced | fe 8 | Adv(Voice and shortcuts) | "Quick ask shortcut" |
| dictationChord | Behavior | fe 8 | Adv(Voice and shortcuts) | "Dictation key" / "Hold in a file to dictate; tap to keep it on." |
| ttsVoice | Behavior (VoiceSettings) | fe 4 | Adv(Voice and shortcuts) | "Read-aloud voice" |
| ttsRate | Behavior | fe 5 | Adv(Voice and shortcuts) | "Read-aloud speed" |
| voiceLoopMaxTurns | Behavior | fe only: `useVoiceLoop.ts:27` | Hide | const `VOICE_LOOP_MAX_TURNS = 20` (renderer copy in `src/shared`) |
| homeWidgets | Modules | be 2 / fe 10 | Adv(Layout) | "Today screen cards" |
| hiddenViews | Modules | be 3 / fe 6 | Adv(Layout) | "Where each view lives" |
| navPlacement | Modules | fe 3 | Adv(Layout) | same row |
| modulesDefault | none | `app.py:280,293` | keep, migration marker | none |
| maxToolRounds | Autonomy | `app.py:1610` | Hide + replace (E3) | none; no-progress detection + internal cap |
| snapshotsEnabled | Permissions > Run safety | be 1 `snapshots.py:157` / fe 3 | Adv(Files and web) | "Snapshot workspace folders before changes" / "Lets Undo reverse shell effects. Needs git." |
| cacheLayout | Autonomy > Advanced | `app.py:2305` | Hide | on, const |
| devTools | Behavior > Advanced | fe 11 | Adv(Developer) | "Developer tools" / "Show traces, the context preview and the system prompt." |
| contextWindow | Memory > Context | `compaction.py:220,338,485`, `app.py:368` | Derive (E1) | none; override honoured |
| autoCompact | Memory > Context | `compaction.py:339` | Adv(Assistant behaviour) | "Summarize old messages automatically" / "When a chat gets long. Off: only when you ask." |
| compactAt | Memory > Context | `compaction.py:338,490` | Hide | const fraction in `limits.py` |
| compactKeepRecent | Memory > Context | `compaction.py:285,339`, `app.py:2457` | Hide | const |
| microKeep | none | `app.py:2635`, `subagents.py:862` | Hide | const |
| microAt | none | `app.py:2636`, `subagents.py:862` | Hide | const fraction |
| otelExport | Data > Advanced | be 2 / fe 2 | Adv(Developer) | "Send traces to a collector" |
| mcpDeferAbove | Autonomy > Advanced | `app.py:2085` | Hide | const |
| toolDeferAbove | Autonomy > Advanced | `app.py:2253` | Hide | const |
| mcpServerNotes | none | `app.py:2295` | Hide | on, const |
| skillsInlineBudget | Autonomy > Advanced | `context.py:396` | Derive (E1) | share of window |
| contextBudget | none | `context.py:56-60` | Derive (E1) | per-block share of window |
| maxRunTokens | none (backend only; JOB_BUDGET separate) | `app.py:1611` | Adv(Spending) | "Stop a reply after" / "A size limit per reply, in tokens. 0 means no limit." |
| maxRunSeconds | none | `app.py:1612` | Adv(Spending) | "Stop a reply after (minutes)" / "A time limit per reply. 0 means no limit." Show minutes, store seconds. |
| llmRetries | Data > Advanced | `llm.py:422` | Hide | const `LLM_RETRIES = 3` |
| llmIdleSeconds | Data > Advanced | `llm.py:427` | Hide | const |
| retainUsageDays | Data > Advanced | `retention.py:36` | Hide | const |
| usageAlerts | Provider > Usage & cost | be 2 / fe 3; `app.py:355-359` | Adv(Spending), kept (user intent) | "Warn me when spend passes" / "Dollars per day or per month. 0 turns a warning off." |
| retainTraceDays | Data > Advanced | `retention.py:37` | Hide | const |
| retainToolResultDays | Data > Advanced | `retention.py:38` | Hide | const |
| retainApprovalDays | Data > Advanced | `retention.py:39` | Hide | const |
| parkAfterSeconds | Autonomy > Advanced | `app.py:3095` | Hide | const |
| deskMaxTurns | Autonomy | `app.py:1557,3691` | Hide | const `DESK_MAX_TURNS = 12` |
| deskMaxLive | Autonomy > Advanced | `app.py:906,1569,3730` | Derive | `worker_slots()` |
| deskAutoResume | Autonomy | be 6 / fe 2 | Adv(Desks and background) | "Resume desks after a restart" |
| subagentMaxConcurrent | none | `subagents.py:675`, `research.py:99`, `workflows.py:1014` | Derive | `worker_slots()` |
| subagentMaxDepth | none | `subagents.py:595,653` | Hide | const 2 |
| subagentMaxRounds | none | `subagents.py:693` | Hide | const 12 |
| subagentStaleSeconds | none | `subagents.py:1215` | Hide | const 450 |
| subagentToolSeconds | none | `subagents.py:1215` | Hide | const 1200 |
| workflowMaxFanOut | none | `workflows.py:1011` | Hide | const 50 |
| jobRetryBackoffS | none | `jobs_policy.py:68` | Hide | const 120 |
| jobFailureStreakLimit | none | `jobs_policy.py:72` | Hide | const 3 |
| proposalExpireDays | none | `jobs.py:876` | Hide | const 7 |
| jobExpireDays | none | `jobs.py:774` | Hide | const 0 (never) |
| notifyJobs | Behavior | be 1 / fe 3 | Adv(Desks and background) | "Notify me about scheduled jobs" |
| deskNotify | Autonomy | fe 7 | Adv(Desks and background) | "Notify me when a desk needs me" |
| chatNotify | Behavior | fe 3 | Adv(Desks and background) | "Notify me about chats" |
| compactChats | Behavior | fe 4 | Adv(Layout) | "Start chat windows as blobs" |
| selectionToolbar | Behavior | fe 3 | Adv(Assistant behaviour) | "Selection toolbar" / "Explain, summarize, verify or ask about selected text." |
| uiZoom | Behavior | `app.py:785`, fe 9 | Visible(Appearance) | "Zoom" |
| docTypography | Behavior | be 2 / fe 5 | Adv(Layout) | "Default file font" |
| responseStyle | Behavior | be 4 / fe 5 | Adv(Assistant behaviour) | "Reply style" |
| responseStyleText | Behavior | be 3 / fe 5 | Adv(Assistant behaviour) | shown when style is Custom |
| gmailSendHold | Integrations | be 2 / fe 4 (`outbox.py`) | Adv(Mail, calendar and plans) | "Hold outgoing email so I can undo" ; the seconds field is Hide (const 90 s) |
| firecrawlApiKey | Integrations | be 2 / fe 2 | Adv(Files and web) | "Firecrawl key" / "Optional. Used first for web search and page reads." |
| braveApiKey | Integrations | be 3 / fe 3 | Adv(Files and web) | "Brave Search key" |
| tavilyApiKey | Integrations | be 3 / fe 3 | Adv(Files and web) | "Tavily key" |
| exaApiKey | Integrations | be 2 / fe 2 | Adv(Files and web) | "Exa key" / "Optional; lifts the rate limit." |
| searxngUrl | Integrations | be 3 / fe 2 | Adv(Files and web) | "Your own search server" |
| readerFallback | Integrations > Advanced | be 2 / fe 2 | Adv(Files and web) | "Retry blocked pages through a reader service" |
| fetchCacheSeconds | Memory > Advanced retrieval | `tools.py:1252,1276,1304` | Hide | const 3600 |
| githubToken | Integrations | be 3 / fe 2 | Adv(Files and web) | "GitHub token" / "Optional. Empty uses your `gh` login." |
| sandboxKeepDays | none | `microvm.py:380` | Hide | const 14 |
| sandboxMountDesk | Autonomy > Advanced | `app.py:2276`, `microvm.py:256` | Adv(Desks and background) | "Share a desk's folder with its sandbox" |
| requireReadBeforeWrite | none | `fsx.py:734,771` | Hide | on, const |
| shellTimeoutSec | none | `shell.py:671`, `opencode.py:141` | Hide | const 120 |
| shellMaxBackground | none | `shell.py:712,872` | Hide | const 4 |
| codingSessionTimeoutMinutes | Advanced | `codingagents.py` `_launch_opencode` | Adv(Desks and background) | const 30 |
| codingSessionMaxConcurrent | Advanced | `codingagents.py` `_check_capacity` | Adv(Desks and background) | const 3 |
| visionModel | Autonomy | be 4 / fe 3 | Adv(Assistant behaviour) | "Model that reads pictures" |
| imageModel | Memory tab | be 4 / fe 2 | Adv(Assistant behaviour) | "Image generation model" |
| browserMaxTabs | Autonomy > Advanced | `browser.py:190` | Hide | const 4 |
| browserIdleSeconds | none | `browser.py:190` | Hide | const 300 |
| workEnvPackages | Autonomy | be 2 / fe 3 | Adv(Desks and background) | "Extra Python packages" |
| modelPrices | none | be 5 / fe 4 | keep, not UI | none |
| googleClientId / googleClientSecret | Integrations | be 3-5 / fe 8 | Visible(Integrations), inside Google's own "use your own OAuth client" fold | "OAuth client" |
| googleToken | none | be 12 | keep, internal | none |
| microsoftClientId / microsoftTenant | Integrations | be 2 / fe 7 | Visible(Integrations), inside Microsoft's fold | "App registration" |
| microsoftToken | none | be 4 | keep, internal | none |
| pimProvider | Integrations | be 5 / fe 17 | Visible(Integrations) | "Mail and calendar account" |
| activity | Activity view | be 217 | out of scope (own view) | none |
| meetings | Meetings tab | be 291 | Visible(Integrations) as "Meetings" (recorder, mic/system audio, transcription, notes, privacy) | existing labels; nothing numeric is shown there |
| digest | Meetings tab | be 106 | Adv(Assistant behaviour) | "Daily digest" with "Written at" hour |
| googleTasksSync | Google card | `gtasks.py` | Visible(Integrations), inside Google | "Sync todos with Google Tasks" |
| retrievalMode | Memory > Advanced retrieval | be 9 / fe 2 | Adv(Memory and search) | "Search by" / "Keywords and meaning, or keywords only." |
| embeddingModel | Memory | be 4 / fe 2 | Adv(Memory and search) | "Search model" / "After changing it, rebuild the search index." |
| retrievalMinSimilarity | Memory > Advanced retrieval | `retrieval.py:320` | Hide | const 0.25 |
| hybridRetrieval | Memory | be 2 / fe 2 | merge into retrievalMode | none |
| meetingEmbeddings | Memory > Advanced retrieval | be 2 / fe 2 | Adv(Memory and search) | "Find meetings by meaning" / "Sends meeting text to the search model's provider." |
| retrievalPerDocCap | Memory > Advanced retrieval | `retrieval.py:321` | Hide | const 3 |
| retrievalCandidates | Memory > Advanced retrieval | `retrieval.py:284`, `retrieval_rerank.py:51` | Hide | const 20 |
| contextualChunks | Memory > Advanced | be 5 / fe 4 | Adv(Memory and search) | "Describe each file passage when indexing" / "One extra model call per passage." |
| retrievalRerank | Memory > Advanced retrieval | be 1 / fe 2 | Adv(Memory and search) | "Re-rank search results" |
| retrievalRerankModel | Memory > Advanced retrieval | be 2 / fe 3 | Adv(Memory and search), under Re-rank | "Re-rank model" |
| useDocsInContext | Memory > Advanced retrieval | `app.py:5865`, `context.py:339` | Adv(Memory and search) | "Search my Docs for context" |
| mailWatch | Integrations | be 2 / fe 10 | Adv(Mail, calendar and plans) | "Reply tracker": thresholds in days/hours are the user's own meaning, keep |
| imessageEnabled | Integrations | be 2 / fe 5 | Visible(Texting) | "Control Grain by text" |
| imessageHandles | Integrations | be 5 / fe 6 | Visible(Texting) | "Allowed numbers and emails" |
| imessageConversationId | Integrations | be 4 / fe 3 | Visible(Texting) | "Chat for texts" |
| imessageNotifyLongRuns | Integrations | be 2 / fe 3 | Visible(Texting) | "Text me when long runs finish or need approval" |
| imessageLongRunMinutes | Integrations | `imessage.py:873` | Hide | const 3 |
| planner | Integrations | be 22 / fe 20 | Adv(Mail, calendar and plans) | "Work hours and days" for the day plan (user preference) |

Dead in `DEFAULT_SETTINGS`: **none strictly dead**. `mode` is legacy (one reader, a migration) and `modulesDefault` is a migration marker; `hybridRetrieval` duplicates `retrievalMode` (two readers each, both live) and can be folded later but is not removable now.

### A2. `permissions.DEFAULTS`

| Key | Where shown now | Readers | Decision | Label / help |
|---|---|---|---|---|
| tools | Permissions > Tool access (`ToolGlobalToggles`) | be 470 / fe 166 | Adv(Approvals) | "Per-tool access" / "On runs, Ask pauses for your approval, Off hides the tool." |
| alwaysAsk | Permissions > Always ask (`AlwaysAsk`) | be 13 / fe 4; `tools.py:831` | Adv(Approvals) | "Always ask first" / "Cards that appear every time, in every mode." |
| permissionRules | `PermissionRules` | be 16 / fe 5; `permrules.py` | Adv(Approvals) | "Rules" / "Allow, ask or deny a specific call. Deny always wins." |
| skipPermissions | Permissions > Skip permissions; `SkipPermissionsToggle` in composer | be 7 / fe 13; `permrules.py:1068-1073`, `app.py:2046` | Replace by `permissionMode` (E5); composer toggle becomes a per-chat override | none |
| unattendedApprovals | Permissions > Run safety | `app.py:2115,3004`, `subagents.py:1046` | Hide, const `deny` for unattended runs in every mode | none |
| autoReview | Permissions > Run safety | `app.py:3026`, `autoreview.py:31` | Replace by `permissionMode` (E5) | none |
| autoReviewModel | Permissions > Run safety | `autoreview.py:49` | Visible(Permissions), shown only under Auto | "Reviewer model" / "Checks each action before it runs. Empty uses the helper model." |
| fetchAllowlist | Run safety | be 8 / fe 2 | Adv(Approvals) | "Sites to trust after reading outside content" |
| docEditMode | Permissions > File edit mode | be 6 / fe 4 | Adv(Approvals) | "File edits" / "Ask: review each diff. Accept all: write it and still show the diff." |
| workspaceRoots | Workspace folders | be 23 / fe 3 | Visible(Workspace folders) | "Folders the assistant may edit" |
| planMode | Permissions > Plan mode default | be 9 / fe 14 | Adv(Approvals) | "Plan first" / "Off, only before changes, or always. A chat can override." |
| sandboxNetwork | Permissions > Sandbox | be 8 / fe 5 | Adv(Files and web) | "Network in the Linux sandbox" |
| sandboxImage | Permissions > Sandbox | be 4 / fe 2 | Adv(Developer) | "Sandbox image" |
| sandboxRuntime | Permissions > Sandbox | be 6 / fe 2 | Adv(Developer) | "Container program" |
| shellNetwork | Permissions > Shell network | be 9 / fe 8 | Adv(Files and web), via the existing Off/Registries/Open selector | "Network for commands" |
| shellRegistryAccess | same | be 5 / fe 6 | part of that selector | none |
| shellAllowedDomains | same | be 10 / fe 9 | Adv(Files and web) | "Allowed hosts" |
| deskShellAuto | Permissions > Desks | be 3 / fe 3 | Adv(Desks and background) | "Run sandboxed commands in a desk without asking" |
| browserEnabled | Permissions > Browser | be 2 / fe 2 | Adv(Desks and background) | "Let desks use a browser" |
| browserAllowlist | same | be 5 / fe 2 | Adv(Desks and background) | "Allowed sites" |
| deskDoneGate | Permissions > Desks | be 2 / fe 3 | Adv(Desks and background) | "Check before a desk finishes" |
| deskSelfReview | same | be 2 / fe 2 | Adv(Desks and background) | "Review a desk's result against its brief" |

### A3. Other stores and components

| Item | Where | Decision |
|---|---|---|
| `insights.DEFAULTS` (enabled, everyHours 12, lookbackDays 21, minDays 2, maxSuggestions 8, autoMemory, memoryConfidence 0.6) | Activity view only (`ActivityView.tsx:925-943`), read `insights.py:927-1010` | Out of Settings scope. Treat `lookbackDays`, `minDays`, `maxSuggestions`, `memoryConfidence` as named constants in a later pass; do not surface in Settings. |
| `meetings.DEFAULT_CONFIG` numerics (nudgeSeconds 120, segmentSeconds 20, docSegmentSeconds 10, dictationSegmentSeconds 8, maxMeetingSeconds 14400, drainSeconds 90) | `meetings.py:168-193`; only toggles/devices/templates are shown in `MeetingSettings.tsx` | Already hidden; leave. Candidates for `limits.py` later. |
| `RunSafetySettings` | Permissions | Split: allow-hosts list -> Adv(Approvals); unattended/review controls removed (E5); snapshot toggle -> Adv(Files and web). |
| `SandboxSettings`, `ShellNetwork`, `BrowserAccess`, `DeskGates`, `WorkEnv`, `SignIns` | Permissions / Autonomy | Move under Advanced as written (see A2). The sandbox container list + Reset stays inside Adv(Files and web). |
| `CoworkSettings` / `CoworkAdvanced` NumFields | Autonomy | Remove the numeric fields (`deskMaxTurns`, `deskMaxLive`, `parkAfterSeconds`, `browserMaxTabs`); keep resume, notify, vision, work env. The Autonomy tab disappears. |
| `DataSettings`, `SupportSettings`, `PresetFiles`, `TrashPanel` | Data tab | Move to Adv(Data and support) as-is (all are actions, not numbers). |
| `ReliabilitySettings` | Data > Advanced | Delete the component (all six `Num` fields are Hide). |
| `TraceExportSettings` | Data > Advanced | Adv(Developer); `timeoutSeconds` (5) stays a constant. |
| `McpSettings` | Library, not Settings | Integrations shows a one-line "Connectors" row that opens the Library. |
| `PermissionsPanel` ("System access") | System access tab | Visible(System access), unchanged. |
| `WorkspaceRoots` | Workspace folders tab | Visible(Workspace folders), unchanged. |
| `GrantsPanel` ("Standing grants") | Permissions | Adv(Approvals). Its "Chats skipping permissions" list becomes "Chats in Allow all mode" (reads `conversations.settings.skipPermissions`, `app.py:4623`). |
| `SkipPermissionsToggle` | Composer | Becomes a three-way per-chat selector bound to a conversation-level `permissionMode` (E5). |
| `MemoryPanel` (embedded in Memory tab) | Memory tab | Not a setting. Remove the Memory tab from Settings; the panel stays reachable via `store.openMemory` (confirm a nav entry exists; I did not verify one). |
| Settings search index | none found in `SettingsModal.tsx`; `SETTINGS_TABS` (line 841) is exported for the command palette | Update `SETTINGS_TABS` to the new tab ids and make the palette open Advanced for anything inside it. |

## B. Magic numbers

Verdict key: **(a)** derive adaptively, **(b)** named constant in `limits.py`, **(c)** keep as internal safety cap in its own module (not user-facing, not moved).

### B1. User-facing knobs (every key in `NUMERIC_SETTING_RANGES` plus other numeric defaults)

| Key | Value | Where | Purpose / protects | Verdict |
|---|---|---|---|---|
| maxToolRounds (1-60) | 25 | `llm.py:122`, `app.py:1610` | Caps model/tool rounds of one reply; protects against runaway loops and cost. | **(a)** replace by no-progress detection (`stuck.py`, `app.py:2355`, `REPEAT_LIMIT` 5, `TOOL_ERROR_LIMIT` 3) plus internal cap `MAX_ROUNDS_HARD = 100` (b). Job cap stays `JOB_BUDGET` 8. |
| maxRunTokens (0-10M) | 200 000 | `llm.py:157`, `app.py:1611` | Per-reply spend ceiling, cumulative prompt+completion. | stays user-visible in Adv(Spending); default const. |
| maxRunSeconds (0-86400) | 300 | `llm.py:158`, `app.py:1612` | Per-reply wall clock, approval waits excluded. | stays user-visible in Adv(Spending). |
| contextWindow (1000-4M) | 128 000 | `llm.py:136`, `compaction.py:220` | Size all fractions are taken from. | **(a)** model metadata (E1). |
| compactAt (0.1-0.95) | 0.7 | `compaction.py:338` | Summarize history at this share of the window. | **(b)** fraction of derived window. |
| compactKeepRecent (2-200) | 8 | `compaction.py:285` | Messages never summarized. | **(b)** |
| microAt (0.05-0.95) | 0.25 | `app.py:2636` | Stub old tool results past this share; comment says time-to-first-token dominates past ~30k tokens. | **(a)** `min(0.25 x window, 32 000)` (E1). |
| microKeep (0-50) | 3 | `app.py:2635` | Recent tool results kept intact. | **(b)** |
| autoCompact | true | `compaction.py:339` | On/off. | stays Adv toggle. |
| subagentMaxConcurrent (1-20) | 4 | `subagents.py:675` | Parallel children; protects CPU, memory, rate limits. | **(a)** `worker_slots()`. |
| subagentMaxDepth (0-3) | 2 | `subagents.py:595` | Nesting. | **(b)** |
| subagentMaxRounds (1-60) | 12 | `subagents.py:693` | Child round cap; its cost also bills the parent. | **(c)** |
| subagentStaleSeconds (0-86400) | 450 | `subagents.py:1215` | A child with no model/tool activity this long is stopped. That is already a no-progress timer. | **(b)** |
| subagentToolSeconds (0-86400) | 1200 | `subagents.py:1215` | Child stuck inside one tool. | **(b)** |
| fileSnapshotMaxBytes (0-100M) | 5 000 000 | `filesnap.py:65` | Largest file pre-image kept. | **(b)** |
| fileSnapshotRetainDays (1-365) | 14 | `filesnap.py:257` | Undo history age. | **(b)** |
| fileSnapshotBudgetMB (1-20000) | 200 | `filesnap.py:258` | Disk budget for pre-images. | **(b)** |
| llmRetries (0-10) | 3 | `llm.py:422` | Provider retry before first token. | **(b)** |
| llmIdleSeconds (10-3600) | 300 | `llm.py:427,356` | Abandon a silent stream. | **(b)** (one constant replaces the duplicated `DEFAULT_IDLE_S`) |
| retainUsageDays (7-3650) | 365 | `retention.py:36` | Bookkeeping age. | **(b)** |
| retainTraceDays (1-3650) | 60 | `retention.py:37` | same | **(b)** |
| retainToolResultDays (1-3650) | 30 | `retention.py:38` | same | **(b)** |
| retainApprovalDays (1-3650) | 90 | `retention.py:39` | same | **(b)** |
| toolReadRetries (0-5) | 2 | `tools.py:926` | Retry read-only tools on transient network error. | **(b)** |
| parallelReads (1-8) | 4 | `app.py:2403` | Concurrent read-only calls in a round. | **(a)** `worker_slots()` |
| browserMaxTabs (1-12) | 4 | `browser.py:190` | Tabs per desk. | **(b)** |
| browserIdleSeconds (30-86400) | 300 | `browser.py:190` | Close idle browser. | **(b)** |
| sandboxKeepDays (0-3650) | 14 | `microvm.py:380` | Delete stopped sandboxes. | **(b)** |
| retrievalMinSimilarity (0-1) | 0.25 | `retrieval.py:320` | Drop weak embedding-only hits. | **(b)** |
| retrievalPerDocCap (1-10) | 3 | `retrieval.py:321` | Passages per document. | **(b)** |
| retrievalCandidates (5-50) | 20 | `retrieval.py:284` | Candidates per ranker. | **(b)** |
| fetchCacheSeconds (0-86400) | 3600 | `tools.py:1252` | Reuse fetched pages. | **(b)** |
| imessageLongRunMinutes (1-1440) | 3 | `imessage.py:873` | When a text run counts as long. | **(b)** |
| uiZoom (80-160) | 100 | `app.py:785` | A preference. | user-visible (Appearance). |
| voiceLoopMaxTurns | 20 | `useVoiceLoop.ts:27` | Cap on hands-free loop. | **(b)** |
| consolidateEvery | 25 | `learn.py:842` | Memory tidy cadence. | **(b)** |
| mcpDeferAbove | 12 | `app.py:2085` | Defer connector tool schemas past N tools. | **(a)** share of window (E1) |
| toolDeferAbove | 40 | `app.py:2253` | Same, built-ins. | **(a)** share of window (E1) |
| skillsInlineBudget | 6000 chars | `context.py:396` | Inline skill text. | **(a)** share of window (E1) |
| contextBudget | memories 1500, graph 800, chunks 2000, activity 800, meetings 800, pinned 3000 tokens | `context.py:56-60` | Per-block retrieval token caps. | **(a)** same fractions of window (E1) |
| deskMaxTurns | 12 | `app.py:1557,3691` | Desk turns before it stops and asks. | **(b)** |
| deskMaxLive | 4 | `app.py:906,3730` | Desks running at once. | **(a)** `worker_slots()` |
| parkAfterSeconds | 180 | `app.py:3095` | Desk waits on an unwatched card. | **(b)** |
| workflowMaxFanOut | 50 | `workflows.py:1011` | Items per fan-out. | **(b)** |
| jobRetryBackoffS | 120 | `jobs_policy.py:68` | Retry backoff base (cap `BACKOFF_CAP_S` 1800). | **(b)** |
| jobFailureStreakLimit | 3 | `jobs_policy.py:72` | Failures before a job is paused. | **(b)** |
| proposalExpireDays / jobExpireDays | 7 / 0 | `jobs.py:774-876` | Expiry. | **(b)** |
| shellTimeoutSec | 120 (max 600) | `shell.py:671`, `shell.py:38-40` | Foreground shell default. | **(b)** |
| shellMaxBackground | 4 | `shell.py:712` | Live background jobs. | **(b)** |
| codingSessionTimeoutMinutes (1-1440) | 30 | `codingagents.py` | An OpenCode coding session is stopped after this. Shown in Advanced. | **(b)** |
| codingSessionMaxConcurrent (1-20) | 3 | `codingagents.py`, `shell.py` pool `coding` | Live coding sessions (claude and opencode), apart from `shellMaxBackground`. Shown in Advanced. | **(b)** |
| gmailSendHold.seconds | 90 (clamped 60-120) | `outbox.py` | Undo window. | **(b)** |
| usageAlerts.dailyCost / monthlyCost | 0 / 0 | `app.py:355-359` | Spend warnings. | **User intent. Keep visible in Adv(Spending). Do not remove.** |
| mailWatch awaitingAfterDays 3 / needsReplyAfterHours 24 | | `mailwatch.py` | Reply tracker meaning. | User intent; keep in Adv. |
| planner work hours, bufferMin 10, minBlockMin 15, maxBlockMin 120, slotStepMin 15, lookaheadDays 7 | | `planner.py` | Day plan. | Hours/days are user intent (keep); the rest **(b)**. |
| digest.hour | 8 | `digest.py` | User preference. | keep. |

### B2. Hardcoded tuning constants (grouped; only behaviour-affecting ones)

| Module | Constants (value, file:line) | Protects | Verdict |
|---|---|---|---|
| `app.py` run loop | `REPEAT_LIMIT 5`, `TOOL_ERROR_LIMIT 3` (1518-1519); `FINAL_ROUND_SECONDS 90`, `JOB_HARD_SECONDS 1800`, `JOB_BUDGET {8, 60000, 240}` (1528-1531); `STOP_GRACE_SECONDS 3` (1735); `MESSAGE_WINDOW_FRACTION 0.5` (363); `_caps` | Runaway loops, unattended runs, stop latency | **(c)**, but import names from `limits.py` so there is one file to read. `MESSAGE_WINDOW_FRACTION` is **(b)**. |
| `stuck.py` | `SAME_RESULT 4`, `ERROR_CYCLE 3`, `ALTERNATIONS 6`, `ERROR_STORM 4`, `WINDOW 24` (16-23) | Loop shapes the identical-call breaker misses | **(c)**. They are the replacement for `maxToolRounds`, not knobs. |
| `permrules.py` | `DENIAL_LIMIT 3`, `DOOM_LIMIT 3`, `MAX_SUGGESTIONS 5` (28-32) | Repeated refusals/calls | **(c)** |
| `llm.py` | `RETRY_BASE_S 1`, `RETRY_CAP_S 30`, `RETRY_AFTER_MAX_S 60`, `CONNECT_TIMEOUT_S 10`, `DEFAULT_IDLE_S 300`, `CAPS_TTL_S 30d` (349-356, 635); `httpx` 15 s at 809 | Provider resilience | **(b)** retry/idle numbers; others **(c)** |
| `compaction.py` | `MICRO_MIN_CHARS 400`, `MAX_ROW_CHARS 6000`, `TOOL_RECORD_CAP 1200`, `TOOL_ARG_CHARS 120`, `TOOL_PREVIEW_CHARS 300` (60-130) | Summary size | **(c)** |
| `context.py` | `PAGE_DETAIL_LIMIT 6000`, `PAGE_SELECTION_LIMIT 2000`, `PINNED_LIMIT 4000` chars (22-48) | Prompt size | **(a)** scale with window as in E1; else **(c)** |
| `autoreview.py` | `ARGS_CAP 2000`, reviewer deadline `monotonic()+30` (57) | Reviewer prompt size, latency | **(c)**; the 30 s becomes `REVIEW_TIMEOUT_S` (b) |
| `tools.py` | `RETRY_DANGER`, `NO_RETRY_GROUPS` (299-300), `SHOW_MAX_CHARS 200000`, `SHOW_MAX_FILE_BYTES 50MB` (303-304); fetch timeouts 10-30 s (568, 1290, 3599, 3758) | Tool output size, network hangs | **(c)** |
| `reach.py` | `MAX_BODY 5MB`, `BODY_DEADLINE_S 45` (49-50) | Web read hangs | **(c)** |
| `webread.py` | `MAX_CACHE_BODY 2MB`, `MAX_CACHE_ROWS 200`, `CACHE_MAX_AGE 24h`, `LINK_CAP 40` | Page cache | **(c)** |
| `shell.py` | `DEFAULT_TIMEOUT 120`, `MAX_TIMEOUT 600`, `BACKGROUND_TIMEOUT 600`, `FG_CAPTURE 5MB`, `MAX_TRACKED 64`, `FINISHED_KEEP_S 1800` (38-47) | Hung commands, memory | `DEFAULT_TIMEOUT` **(b)**; rest **(c)** |
| `microvm.py` | `MAX_SANDBOXES 5`, `MAX_CKPTS 3`, `MAX_EXEC_S 600`, `IDLE_STOP_S 300`, `STDOUT_CAP 20000`, `EXEC_HARD_CAP 4MB`, `EXPORT_MAX_BYTES 10MB`; docker call timeouts 4-300 s | Disk, VM sprawl, hangs | **(c)** |
| `sandbox.py` (Seatbelt) | `HARD_CAP 4MB`, `MAX_IMAGES 6`, `MAX_IMAGE_BYTES 3MB` | Output blow-up | **(c)** |
| `egress.py` | `MAX_TUNNELS_PER_TOKEN 16`, `IDLE_S 60`, `HEAD_TIMEOUT_S 15`, `CONNECT_TIMEOUT_S 15`, `MAX_HEAD 64KB` | Proxy abuse | **(c)** |
| `subagents.py` | `MAX_TASK_CHARS 20000`, `SCOPE_LIMITS`, wait cap 600 s (1304) | Child prompt size | **(c)** |
| `mcp_client.py` | `CONNECT_TIMEOUT 20`, `CALL_TIMEOUT 45`, `READY_TIMEOUT 8`, `PING_INTERVAL 25`, `PING_TIMEOUT 5`, `BACKOFF_MAX 60`, `MAX_RESULT_CHARS 200000` (55-74) | Connector hangs | **(c)** |
| `mcp_eval.py`, `mcp_servers.py`, `mcp_search.py` | description/schema caps (2000-4000 chars, depth 8), `MAX_VERSIONS 10`, search `MAX_LIMIT 10` | Prompt bloat from connectors | **(c)** |
| `fsx.py` | `GLOB_CAP 500`, `GREP_CAP 200`, `EDIT_MAX_BYTES 2MB`, `COPY_MAX_FILES 2000`, `COPY_MAX_BYTES 100MB`, `DIFF_MAX_CHARS 8000` | File tool blast radius | **(c)** |
| `mac.py` | `MAX_READ_BYTES 20MB`, `MAX_WRITE_CHARS 400k`, shortcut in/out caps, `PAGE_MAX_CHARS 60000` | Mac tools | **(c)** |
| `extract_text.py` | `MAX_UPLOAD_BYTES 20MB`, `MAX_INDEX_CHARS 400k`, `MAX_PDF_PAGES 80`, `MAX_UNZIPPED_BYTES 50MB` | Upload bombs | **(c)** |
| `workspace.py`, `snapshots.py` | `MAX_FILES 500`, `MAX_TOTAL_BYTES 200MB`; snapshot `MAX_FILE_BYTES 2MB`, `MAX_FILES 50000`, `MAX_STORE_BYTES 500MB` | Disk | **(c)** |
| `browser.py` | `SNAPSHOT_CAP 12000` | Page text handed to the model | **(c)** |
| `vision.py`, `imagegen.py`, `deliver.py` | `MAX_EDGE 1568`, `MAX_ENCODED 1MB`, `MAX_CALLS_PER_REPLY 12`, `OCR_TIMEOUT_S 30`; imagegen `TIMEOUT_S 180`, `MAX_N 4` | Image cost | **(c)** |
| `jobs.py`, `jobs_policy.py` | `MAX_SLEEP_S 60`, `LATE_GRACE_S 90`, `MAIL_POLL_S 300`, `CAL_POLL_S 300`, `WATCH_MAX_S 7200`, `BACKOFF_CAP_S 1800` | Scheduler cadence | **(c)** |
| `runs.py` | `QUEUE_MAX 1000`, `KEEPALIVE_S 15`, `RETAIN_S 300`, `EVENTS_RETAIN_S 14d` | SSE memory | **(c)** |
| `meeting_recorder.py`, `meetings.py` | segment 20 s, `DEFAULT_MAX_SECONDS 4h`, `DRAIN 120`, `MAX_RESTARTS 3`, `TICK_SECONDS 45`, `AUDIO_RETENTION_SECONDS 7d` | Recorder health | **(c)** |
| `imessage.py` | `POLL_SECONDS 2.5`, `STALE_SECONDS 600`, `ECHO_SECONDS 180`, `MAX_BACKOFF 60` | Polling load | **(c)** |
| `memory_index.py`, `meeting_index.py` | `VECTOR_CAP 5000/20000`, `RANK_DEPTH 50`, `QUERY_TIMEOUT 2` | Brute-force search cost | **(c)** |
| `db.py` | `sqlite3.connect(timeout=30)` (644) | Writer bursts | **(c)** |
| small model calls | titles `TIMEOUT_S 30`, followups 30, embed 10, rerank 10 + `BACKOFF_SECONDS 300` | Helper latency | **(c)** |
| `src/main` | `printDoc.ts:37` 30 s timeout; `backend.ts:275` 1 s wait; restart policy in `restartPolicy.ts` | Print hang, supervisor | **(c)**. Nothing in `src/main` is a user-tunable limit. |

Not found anywhere: any resource-aware limit (`os.cpu_count`, free memory). Every concurrency number is a literal.

## C. Stored-value policy

Rule: **a stored user value is honoured as an override; the default comes from `limits.py`. Validation ranges stay.** Mechanism (finding 2): remove the key from `DEFAULT_SETTINGS`, list it in `limits.OVERRIDABLE`, readers call `limits.get(settings, key)` which returns the stored value only if it is a valid number inside its range, else the default or derived value. Because the modal only ever saved changed fields (finding 3), every stored value is a deliberate choice.

| Key group | Policy |
|---|---|
| `contextWindow` | Stored value is a hard override (replaces the derived window). Cap: still `min(stored, known)` is wrong for a user who sets a bigger value on purpose; use the stored value as-is and let `note_overflow` lower it. |
| `compactAt`, `compactKeepRecent`, `microAt`, `microKeep` | Honour if stored. |
| `maxToolRounds` | **Ignored** in chats (replaced by detection + `MAX_ROUNDS_HARD`); a stored value lower than the hard cap is still honoured as a cap, because someone set it on purpose. Jobs keep `JOB_BUDGET`. |
| `maxRunTokens`, `maxRunSeconds` | Stay real settings (Adv(Spending)). |
| `subagent*`, `workflowMaxFanOut`, `job*`, `proposalExpireDays`, `desk*`, `parkAfterSeconds`, `browser*`, `shell*`, `parallelReads`, `toolReadRetries`, `llm*`, `retain*`, `fileSnapshot*`, `sandboxKeepDays`, `retrieval*`, `fetchCacheSeconds`, `imessageLongRunMinutes`, `consolidateEvery`, `voiceLoopMaxTurns`, `mcpDeferAbove`, `toolDeferAbove`, `skillsInlineBudget`, `contextBudget` | Honour if stored (same `limits.get`). Concurrency keys (`subagentMaxConcurrent`, `deskMaxLive`, `parallelReads`) override `worker_slots()`. |
| `skipPermissions`, `autoReview`, `unattendedApprovals` | One-time migration to `permissionMode` (E5), then ignored. Keep the keys readable for the migration only. `unattendedApprovals` is not migrated into a mode: unattended runs refuse in every mode. |
| `hybridRetrieval` | Fold into `retrievalMode` (`hybrid` only when true); keep reading until folded. |
| `gmailSendHold.seconds` | Honour if stored (backend already clamps 60-120). |

**Dead keys:** none can be deleted from `DEFAULT_SETTINGS` today (every key has a reader; verified by grep above). Deletable after the migration ships: `mode` (legacy, `types.ts:1329`), and `skipPermissions` / `autoReview` / `unattendedApprovals` from `permissions.DEFAULTS` once `permissionMode` has been released for a version. Everything moved to `limits.py` is removed from `DEFAULT_SETTINGS` (not dead, relocated).

## D. How other agents handle the same limits

| Topic | Claude Code | Codex CLI | OpenCode |
|---|---|---|---|
| Turn/round limit | No default round cap for the main loop; `maxTurns` is set per subagent in frontmatter, and a subagent that hits it returns partial output that can be resumed. A `--max-turns` print-mode flag exists (unverified here). | No documented cap on rounds found (unverified). | `steps` per agent: "maximum number of agentic iterations"; when hit, the agent gets a prompt to summarize its work and remaining tasks. Unset means iterate until the model stops or the user interrupts. |
| Loop detection | Not documented in the pages read (unverified). | Unverified. | `doom_loop` is a permission key defaulting to `ask`, i.e. repeated identical calls raise a prompt. |
| Auto-compaction | Compacts when the conversation reaches the model's own context limit; models with a native 1M window compact at about 967k tokens; `CLAUDE_CODE_AUTO_COMPACT_WINDOW` or `/autocompact` set a window (100k-1M). So the threshold is derived from the real model window, near full. | `model_auto_compact_token_limit`: "unset uses model defaults"; `model_context_window` is the active model's window. | `compaction.auto` default true, `compaction.prune` default false, `compaction.reserved` default 10 000 tokens kept free so compaction cannot overflow. |
| Timeouts | Per-tool (Bash) timeouts rather than a whole-run timeout; not verified in docs read. | `stream_idle_timeout_ms` default 300 000 (5 min); `request_max_retries` 4; `stream_max_retries` 5. | Provider `timeout` 300 000 ms, `headerTimeout` 300 000, `chunkTimeout` 300 000 (between stream chunks). |
| Subagent concurrency/depth | Default 20 concurrent (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`); nesting depth default 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`); at the limit the spawn tool is withheld. | `agents.max_concurrent_threads_per_session` (alias `agents.max_threads`): "when unset, Codex chooses the default". | Not documented in the pages read (unverified). |
| Approval modes | `default` (reads only), `acceptEdits`, `plan`, `auto`, `bypassPermissions`. In `auto`, a separate classifier model reviews actions that would prompt; it blocks escalation beyond the request, unrecognized infrastructure and actions steered by read content. Deny rules block in every mode including bypass; some actions are never auto-approved in any mode. Auto is the built-in starting mode for interactive terminal/VS Code sessions from v2.1.283. | Approval policy `on-request` (default), `never`, `granular` (route some categories to prompts, auto-reject others), `untrusted` retired. Separate sandbox axis: `read-only`, `workspace-write` (default Auto preset with `on-request`), `danger-full-access`. Prompts for edits outside the workspace, network, destructive tool calls, sandbox escalation. | `allow` / `ask` / `deny` per tool (`read`, `edit`, `bash`, `task`, `webfetch`, `external_directory`, `doom_loop`, ...), wildcard patterns with last-match-wins; defaults mostly `allow`, with `doom_loop` and `external_directory` at `ask` and `.env` reads denied. Per-agent overrides. |

What this means for Grain: all three tie compaction to the real model window and keep the user-facing surface to a mode, not numbers; Claude Code is the closest analogue to `permissionMode` (manual/`default`, auto/classifier, allow_all/`bypassPermissions`, with hard floors that no mode lifts). Grain's 300 s idle timeout matches the 300 s used by the other two.

Sources:
- https://code.claude.com/docs/en/permission-modes
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/model-config (auto-compact window)
- https://code.claude.com/docs/en/costs
- https://learn.chatgpt.com/docs/agent-approvals-security (redirect from developers.openai.com/codex/agent-approvals-security)
- https://learn.chatgpt.com/docs/config-file/config-reference (redirect from developers.openai.com/codex/config-reference; only the first 100 000 of 159 323 characters were read)
- https://opencode.ai/docs/permissions/
- https://opencode.ai/docs/agents/
- https://opencode.ai/docs/config/

## E. Recommendations (implementers: follow literally)

### E1. `backend/personal_os/limits.py`

Pure stdlib, no imports from other `personal_os` modules (so `llm.py`, `compaction.py`, `app.py` can all import it). Contents:

```python
"""Every tuning number the app uses, with its reason. A user may override any key in OVERRIDABLE by storing it
in settings; `get` returns the stored value when valid, else the derived or default one."""
import os

# ---- Context (fractions of the model's window) ----
CONTEXT_WINDOW_FALLBACK = 128_000  # used only when neither the proxy nor an overflow told us the real window
COMPACT_AT = 0.70                  # summarize history past this share of the window (token counts are len//4 estimates)
COMPACT_KEEP_RECENT = 8            # newest messages never summarized
MICRO_AT = 0.25                    # stub old tool results past this share of the window ...
MICRO_AT_TOKEN_CEILING = 32_000    # ... but never later than this many tokens (time to first token dominates past ~30k)
MICRO_KEEP = 3                     # newest tool results left intact
MESSAGE_WINDOW_FRACTION = 0.5      # one message may fill at most this share of the window
SKILLS_INLINE_FRACTION = 0.012     # skill text inlined in the prompt: 6000 chars ~ 1500 tokens ~ 1.2% of 128k
TOOL_DEFER_TOKEN_SHARE = 0.03      # defer tool schemas once they would exceed this share of the window (see below)
CONTEXT_BLOCK_SHARE = {"memories": 0.012, "graph": 0.006, "chunks": 0.016, "activity": 0.006, "meetings": 0.006, "pinned": 0.023}
                                   # = 1500/800/2000/800/800/3000 tokens at 128k

# ---- Run budget ----
RUN_TOKENS = 200_000               # per reply, prompt+completion summed over every model call (0 = none)
RUN_SECONDS = 300                  # per reply wall clock, approvals excluded (0 = none)
MAX_ROUNDS_HARD = 100              # internal backstop; stuck.py + REPEAT_LIMIT end loops long before this
JOB_MAX_ROUNDS, JOB_RUN_TOKENS, JOB_RUN_SECONDS, JOB_HARD_SECONDS = 8, 60_000, 240, 1800
REPEAT_LIMIT, TOOL_ERROR_LIMIT = 5, 3
FINAL_ROUND_SECONDS = 90

# ---- Provider ----
LLM_RETRIES = 3                    # before the first token only
LLM_IDLE_SECONDS = 300             # silent stream is abandoned (same as other agents' 5 min)
TOOL_READ_RETRIES = 2

# ---- Agents ----
SUBAGENT_MAX_DEPTH = 2
SUBAGENT_MAX_ROUNDS = 12
SUBAGENT_STALE_SECONDS = 450
SUBAGENT_TOOL_SECONDS = 1200
WORKFLOW_MAX_FAN_OUT = 50
DESK_MAX_TURNS = 12
DESK_PARK_AFTER_SECONDS = 180
BROWSER_MAX_TABS, BROWSER_IDLE_SECONDS = 4, 300
SHELL_TIMEOUT_SECONDS, SHELL_MAX_BACKGROUND = 120, 4
REVIEW_TIMEOUT_SECONDS = 30

# ---- Jobs ----
JOB_RETRY_BACKOFF_S, JOB_FAILURE_STREAK_LIMIT = 120, 3
PROPOSAL_EXPIRE_DAYS, JOB_EXPIRE_DAYS = 7, 0
GMAIL_SEND_HOLD_SECONDS = 90

# ---- Storage ----
FILE_SNAPSHOT_MAX_BYTES, FILE_SNAPSHOT_RETAIN_DAYS, FILE_SNAPSHOT_BUDGET_MB = 5_000_000, 14, 200
RETAIN_USAGE_DAYS, RETAIN_TRACE_DAYS, RETAIN_TOOL_RESULT_DAYS, RETAIN_APPROVAL_DAYS = 365, 60, 30, 90
SANDBOX_KEEP_DAYS = 14
FETCH_CACHE_SECONDS = 3600

# ---- Retrieval / misc ----
RETRIEVAL_MIN_SIMILARITY, RETRIEVAL_PER_DOC_CAP, RETRIEVAL_CANDIDATES = 0.25, 3, 20
MEMORY_TIDY_EVERY = 25
VOICE_LOOP_MAX_TURNS = 20
IMESSAGE_LONG_RUN_MINUTES = 3
```

Derivation helpers (same file):

| Helper | Behaviour |
|---|---|
| `get(settings, key)` | Returns `settings[key]` when present, numeric (not bool), finite and inside `RANGES[key]`; otherwise the module default for that key. `RANGES` is the old `NUMERIC_SETTING_RANGES` moved here; `app.py` imports it so `_check_numeric_setting` validates against it and PUT accepts `OVERRIDABLE` keys. |
| `context_window(settings, model, known=None, learned=None)` | `override = get(settings, "contextWindow")` if stored; else `known` (proxy `max_input_tokens` from `pricing.caps(model)`) if >0; else `CONTEXT_WINDOW_FALLBACK`. Then `min(result, learned)` when an overflow taught a smaller one. Floor 4096. Replace `compaction.window_for` with this (keep `note_overflow`/`_learned`), and change `app.py:368` plus `messageLimit.ts` to use it. |
| `compact_threshold_tokens(window)` | `int(COMPACT_AT * window)` |
| `micro_threshold_tokens(window)` | `int(min(MICRO_AT * window, MICRO_AT_TOKEN_CEILING))` — judgment call: it keeps today's 32k at 128k and avoids a 250k trigger on a 1M model. Drop the ceiling if you prefer a pure fraction. |
| `context_block_budget(window, section)` | `max(200, int(CONTEXT_BLOCK_SHARE[section] * window))`; a stored `contextBudget` dict still wins per section. |
| `skills_inline_chars(window)` | `int(SKILLS_INLINE_FRACTION * window * 4)` |
| `tool_defer(n_tools, window)` | Replace the two counts (`toolDeferAbove` 40, `mcpDeferAbove` 12) by one rule: defer when estimated schema tokens (`~ n x 250` or measured `len(json)//4`) exceed `TOOL_DEFER_TOKEN_SHARE * window`. Floor: never defer fewer than 12 connector tools or 40 built-ins when the window is at the fallback (preserves today's behaviour at 128k). Keep stored counts as overrides. |
| `worker_slots()` | `cpu = os.cpu_count() or 2`; `mem_gb = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30` (wrap in try, default 8); `return max(2, min(cpu // 2, int(mem_gb // 2), 8))`. A 8-core/16 GB Mac gives 4 (today's value); a 4 GB or 2-core machine gives 2. Used for `subagentMaxConcurrent`, `deskMaxLive`, `parallelReads`. A stored value overrides. |
| `max_rounds(settings, kind)` | chat: `MAX_ROUNDS_HARD`, or a stored `maxToolRounds` if lower; job: `JOB_MAX_ROUNDS`. |

Wire-up notes: `Budget.__init__` (`app.py:1610`) reads `limits.max_rounds`; `llm.DEFAULT_SETTINGS` loses every key moved to `limits.py` (and `llm.py:356 DEFAULT_IDLE_S` imports `LLM_IDLE_SECONDS`); `app.py:1527-1534` reads `JOB_*` from `limits`. `stuck.py`'s own constants stay put (pure module with no imports).

### E1b. Implementation note (lead)

To keep the diff small, keys stay in `llm.DEFAULT_SETTINGS` (so every reader and `PUT /settings` keep working and stored values keep loading), but their values come from the named constants in `limits.py`. A derived knob defaults to `0`, meaning "derive it": `contextWindow` (proxy metadata, else the fallback), `maxToolRounds` (stuck detection plus `MAX_ROUNDS_HARD`), `subagentMaxConcurrent` / `deskMaxLive` / `parallelReads` (`worker_slots()`). A non-zero stored value is honoured as an override. The token-share tool-defer rule and the per-window context-block shares were not implemented: they would change behaviour on large windows, so `mcpDeferAbove`, `toolDeferAbove`, `contextBudget` and `skillsInlineBudget` became named constants. `MAX_ROUNDS_HARD` is 100, not 200.

### E2. Which numeric settings stay user-editable

Only three: **`uiZoom`** (Appearance), **`maxRunTokens`**, **`maxRunSeconds`** (Advanced > Spending), plus `usageAlerts` (dollars, not a limit). Everything else in the old `NUMERIC_SETTING_RANGES` stays *accepted* by `PUT /settings` and validated (as an override for power users and config files) but has no UI.

### E3. Round limit

Delete the "Max tool rounds per reply" field and the `rounds` clamp in `SettingsModal.save()` (`SettingsModal.tsx:290-291`). Chat runs end by (1) `StuckDetector` nudge then stop (already on; make it unconditional, drop the `stuckDetection` check at `app.py:2355` or hard-wire it true), (2) `REPEAT_LIMIT` identical calls, (3) `maxRunTokens` / `maxRunSeconds`, (4) `MAX_ROUNDS_HARD = 100` as a backstop that ends the reply with the existing `BUDGET_STOP` text. Scheduled jobs keep 8 rounds.

### E4. Move / hide / derive lists

- **Visible (7 sections):** Model: `baseUrl`, `apiKey`, `defaultModel`, `fastModel`+`autoRoute`, Test connection, Run setup again. Permissions: `permissionMode`, `autoReviewModel` (only under Auto). Workspace folders: `workspaceRoots`. Integrations: `pimProvider`, Google (+`googleClientId/Secret`, `googleTasksSync`), Microsoft (+`microsoftClientId/Tenant`), Meetings (`meetings`), a Connectors link to the Library. Texting: `imessageEnabled`, `imessageHandles`, `imessageConversationId`, `imessageNotifyLongRuns`. Appearance: `theme`, `accent`, `uiZoom`. System access: unchanged panel.
- **Advanced (one collapsed block, 11 groups, see A):** `systemPrompt`, `extractionModel`, `followUps`, `autoTitle`, `selectionToolbar`, `responseStyle(+Text)`, `autoCompact`, `visionModel`, `imageModel`, `digest`, `tools`, `alwaysAsk`, `permissionRules`, grants list, `fetchAllowlist`, `docEditMode`, `planMode`, `snapshotsEnabled`, `sandboxNetwork`, `shellNetwork/RegistryAccess/AllowedDomains`, sandbox list, web keys (`firecrawlApiKey`, `braveApiKey`, `tavilyApiKey`, `exaApiKey`, `searxngUrl`, `githubToken`, `readerFallback`), `autoLearn`, `learnStyle`, `retrievalMode`, `embeddingModel`, `meetingEmbeddings`, `contextualChunks`, `retrievalRerank(+Model)`, `useDocsInContext`, `usageAlerts`, `maxRunTokens`, `maxRunSeconds`, `deskAutoResume`, `deskNotify`, `chatNotify`, `notifyJobs`, `deskShellAuto`, `deskDoneGate`, `deskSelfReview`, `browserEnabled`, `browserAllowlist`, `sandboxMountDesk`, `workEnvPackages`, `gmailSendHold.enabled`, `mailWatch`, `planner`, shortcuts (`gatherShortcut`, `quickCaptureShortcut`, `quickAskShortcut`, `dictationChord`), `ttsVoice`, `ttsRate`, `homeWidgets`, `hiddenViews`, `navPlacement`, `compactChats`, `docTypography`, Data (backup, export, presets, trash), Support (diagnostics, logs, restart), `devTools`, `otelExport`, `sandboxImage`, `sandboxRuntime`.
- **Removed from UI, default in `limits.py`, stored value honoured:** `maxToolRounds`, `contextWindow`*, `compactAt`, `compactKeepRecent`, `microAt`, `microKeep`, `toolReadRetries`, `llmRetries`, `llmIdleSeconds`, `retainUsageDays`, `retainTraceDays`, `retainToolResultDays`, `retainApprovalDays`, `fileSnapshotMaxBytes`, `fileSnapshotRetainDays`, `fileSnapshotBudgetMB`, `fileSnapshots`, `fetchCacheSeconds`, `retrievalMinSimilarity`, `retrievalPerDocCap`, `retrievalCandidates`, `consolidateEvery`, `voiceLoopMaxTurns`, `imessageLongRunMinutes`, `gmailSendHold.seconds`, `deskMaxTurns`, `parkAfterSeconds`, `browserMaxTabs`, `browserIdleSeconds`, `subagentMaxDepth/Rounds/StaleSeconds/ToolSeconds`, `workflowMaxFanOut`, `jobRetryBackoffS`, `jobFailureStreakLimit`, `proposalExpireDays`, `jobExpireDays`, `shellTimeoutSec`, `shellMaxBackground`, `sandboxKeepDays`, `stuckDetection`, `cacheLayout`, `mcpServerNotes`, `requireReadBeforeWrite`, `unattendedApprovals`. (*`contextWindow` is derived; the stored value is an override.)
- **Derived:** `contextWindow`, `microAt` ceiling, `contextBudget`, `skillsInlineBudget`, `mcpDeferAbove` + `toolDeferAbove` (one rule), `subagentMaxConcurrent`, `deskMaxLive`, `parallelReads`, `maxToolRounds` (detection + cap).
- **Dead:** none deletable now (A1 note). Deletable after migration: `mode`, `skipPermissions`, `autoReview`, `unattendedApprovals`.

Delete these UI pieces: the Autonomy and Memory-settings tabs' numeric rows, `ReliabilitySettings`, `AdvancedRetrieval`'s four numeric inputs, the Context section (`SettingsModal.tsx:466-501`), the "Hold for" seconds field, the "Runs longer than" field, the voice-loop turns field, the `CONTEXT_DEFAULTS` constant, and the `rounds` clamp.

### E5. Permission mode (decided by the lead; supersedes the first draft)

New permission key `permissionMode`: `"auto" | "manual" | "allow_all"`, default **`auto`** for new installs. A versioned one-time migration sets `permissionMode = "auto"` for every existing install, whatever `skipPermissions` / `autoReview` say (they are read only by that migration), and touches no other key (never `workspaceRoots`).

| Mode | Behaviour |
|---|---|
| `auto` | Every call that is not known safe (danger `safe`, an explicit user `on` for that tool, an allow rule, a session grant, an approved plan step) goes to the reviewer model first. `allow` runs, `deny` refuses with the reason as the tool result, `ask` (or any reviewer error, unreadable answer or timeout) is the normal card. `alwaysAsk` and `force_ask` items are lifted only by `allow` with `confidence: "high"` in a reply that has not read untrusted content. A tool the user set to `ask`, an ask rule, an outside-folder write, taint/plan/desk/doom-loop forced cards stay cards. Unattended runs: `ask` becomes a proposal. |
| `manual` | Today's behaviour exactly: per-tool modes, grants and rules; no reviewer; `unattendedApprovals` as before. |
| `allow_all` | No reviewer and no cards (`alwaysAsk`/`force_ask` included). Deny rules still refuse; writes outside workspace folders still need the folder (card); question and plan cards still show; every non-safe call is logged as allowed in allow-all mode. |

The old `autoReview` levels and the global/per-chat `skipPermissions` switch are folded into this one key; `tools` on/ask/off and `permissionRules` stay as Advanced overrides with their current precedence.

### E6. Order of work

1. Add `limits.py` and move constants (pure refactor, no behaviour change at defaults). 2. `context_window` + the `app.py:368` / `messageLimit.ts` fix. 3. `worker_slots`. 4. Round limit change (E3). 5. `permissionMode` backend + migration + `app.py` gate change. 6. Settings UI restructure and deletion list. 7. Update `SETTINGS_TABS` for the palette.

### E7. Unsure / needs a decision

- `MICRO_AT_TOKEN_CEILING` and the token-share tool-defer rule are my proposals; both change behaviour only for non-128k windows.
- `MAX_ROUNDS_HARD = 100` is a judgment call; the current default is 25 and the hard cap is 60, so some users will see longer runs. Spend is still bounded by `maxRunTokens`/`maxRunSeconds`.
- `fileSnapshots` and `requireReadBeforeWrite` are safety toggles with no UI today; I left them hidden-on. Say so if they should become Advanced toggles.
- Confirm a navigation entry for the Memory browser exists before removing the Memory tab (not verified).
- Claude Code `--max-turns`, per-tool timeouts and loop detection, and Codex/OpenCode concurrency and loop handling were not confirmable from the pages read; marked unverified in D.
