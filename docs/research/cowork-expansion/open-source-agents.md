# R3: Open-source agent tool and capability inventory vs Grain

Source root for all citations: `<clone>/` (shallow clones, fetched 2026-10-02). Paths below are relative to it. Where a claim comes from memory instead of the cloned tree it is marked "(unverified, not in cloned tree)".

Repos note: OpenHands' main repo is now frontend only. The agent tools live in `software-agent-sdk/` (OpenHands/software-agent-sdk), which I cloned instead. The old CodeAct IPython tool no longer exists there (`grep -ri ipython|jupyter` over `openhands-tools` and `openhands-sdk` returned nothing). Cline and Roo Code current trees no longer contain `browser_action` (grep returned nothing in `cline/docs`, `cline/sdk`, `Roo-Code/src`).

Grain "built" baseline comes from `backend/personal_os/` (tools.py, shell.py, subagents.py, workflows.py, stuck.py, mcp_search.py, reach.py, extract_text.py, fs_* tools) plus the task brief.

---

## 1. Per-agent inventory

### 1.1 OpenClaw (`openclaw/`)

Overview doc: `openclaw/docs/tools/index.md` ("Built-in tool categories" table). Tool policy is enforced before the model call, so a denied tool's schema is never sent.

| Tool | Purpose / notable params / limits | Source |
|---|---|---|
| `exec` | Shell. `command, workdir, env, yieldMs (10000 default auto-background), background, timeoutSeconds (0 disables), pty, host (auto/sandbox/gateway/node), ask (off/on-miss/always, can only harden), elevated (escape sandbox)` | `docs/tools/exec.md` |
| `process` | Poll/kill backgrounded exec sessions, scoped per agent | `docs/tools/exec.md` |
| `terminal` | Shared operator terminal (UI) | `docs/tools/index.md` |
| `read/write/edit/apply_patch` | `edit` also does whitespace/Unicode-quote normalizing edits; `apply_patch` patch format | `docs/tools/index.md`, `docs/tools/apply-patch.md` |
| `browser` | ONE tool, actions: `doctor/status/start/stop/tabs/open/focus/close/snapshot/screenshot/navigate/act/requests/errors/text/emulate`. Snapshot has `query` filter (keeps lines with all tokens, retains refs) + `maxChars`; `text` max 40,000 chars; `requests` network log (`filter`,`limit` 50); `errors`; `emulate` (device, colorScheme, timezoneId, locale); `navigate` returns snapshot inline; downloads returned as managed `{url,suggestedFilename,path}`; `profile` (managed `openclaw`, `user` = attach to real signed-in Chrome via DevTools MCP, remote CDP); `target` sandbox/host/node; `dashboard` selector | `docs/tools/browser/agent-tools.md`, `docs/tools/browser.md` |
| `computer` | Desktop GUI. One action per call: screenshot, left/right/middle/double/triple click, mouse_move, left_click_drag, mouse down/up, scroll(dir, amount), type, key, hold_key, wait. Coordinates must echo screenshot `frameId` (stale-frame fence). Optional v2: list_apps/list_windows/get_accessibility_tree/get_window_state(includeScreenshot)/launch_app/kill_app/set_value/zoom/invoke, plus browser_* family, start/stop_recording, replay_trajectory. Providers: Peekaboo (macOS default) or CUA driver. Identical-frame dedupe ("screen unchanged"). Screenshots are model-only, on-screen text is untrusted. Dangerous sub-actions (force kill, browser downloads, file inputs, recording, replay) are separate high-risk classes | `docs/nodes/computer-use.md` |
| `screen`, `theme`, `dashboard` | Agent arranges the UI panes/panels | `docs/tools/screen.md` |
| `ask_user` | 1 to 3 structured questions, options, multi-select, "Other", skip; main session only (subagents don't get it) | `docs/tools/ask-user.md` |
| `secrets` | `request/list/delete`. Agent asks the human for a credential; value goes straight to store, never into chat/transcript/tool result; `allowedHosts` for egress; timeout 15 min default, clamp 30-3600 s | `docs/tools/secrets.md` |
| `web_search`, `x_search`, `web_fetch` | ~12 provider plugins (brave, exa, tavily, searxng, ddg, firecrawl...) | `docs/tools/*-search.md`, `docs/tools/web-fetch.md` |
| `pdf` | `pdf`/`pdfs` (<=10), `prompt`, `pages` ("1-5,7"), `password`, `model`. Native PDF input on Anthropic/Google, else text/image extraction fallback; model resolution chain pdfModel -> imageModel -> provider vision model -> session model | `docs/tools/pdf.md` |
| `view_image`, `image_generate`, `music_generate`, `video_generate`, `tts` | Media | `docs/tools/media-overview.md` |
| `code_execution` | Remote sandboxed Python on xAI Responses API, 30 s default, billed per call | `docs/tools/code-execution.md` |
| `goal` tools: `get_goal/create_goal/update_goal` | One durable objective per session, survives restarts, token budget, pause/resume/block/complete; not a task queue | `docs/tools/goal.md` |
| `progress_card` | Durable parent-session progress card; not for subagents | `docs/tools/index.md` |
| `sessions_list/history/search/send/spawn/yield`, `subagents`, `agents_wait`, `session_status`, `conversations_*` | Cross-session recall, messaging, spawn, yield-until-children-done; `user` param binds authority | `docs/concepts/session-tool.md` |
| `/steer` (`/tell`) | Inject guidance into the active run at next runtime boundary | `docs/tools/steer.md` |
| `/btw` (`/side`) | Ephemeral side question on a snapshot of the session, never written to history | `docs/tools/btw.md` |
| `cron`, `heartbeat_respond` | Scheduling. Heartbeat = periodic main-session turn (default 30m) with monitor scratch checklist, active hours, isolated session option, 30 s min between event wakes, flood guard 5 starts/60 s | `docs/gateway/heartbeat.md`, `docs/automation/cron-jobs.md` |
| Standing orders | Programs with scope/triggers/approval gates/escalation, in AGENTS.md | `docs/automation/standing-orders.md` |
| `nodes`, `gateway`, `plugins` | Paired devices (camera, location, voice, system.run), self-management | `docs/nodes/*.md` |
| `tool_search`, `tool_describe`, `tool_call` | Deferred catalog; model sees bounded name+description directory scaled to context window, searches, describes one tool for exact schema, then calls. Untrusted MCP names/descriptions are NOT copied into system prompt | `docs/tools/tool-search.md` |
| Code Mode `exec`/`wait` | Model writes JS that searches/calls hidden catalog; `{title, code}`; Node or QuickJS executor. Auto tier engages only for models flagged as good at it | `docs/tools/code-mode.md` |
| Swarm | JS-driven fan-out: `agents.run`, collector children (`collect:true`, no notify, not steerable), `outputSchema`, `groupId`, `maxConcurrent` default 32 | `docs/tools/swarm.md` |
| Lobster | Typed workflow runtime, resumable approval/input gates, resume tokens, "one call instead of many" | `docs/tools/lobster.md` |
| `llm-task` | JSON-only LLM step with JSON-Schema validation | `docs/tools/llm-task.md` |
| Tokenjuice plugin | Compacts noisy `exec/bash` results after the fact (output-side only) | `docs/tools/tokenjuice.md` |
| Loop detection | Rolling `(tool,args,result)` history, ignores volatile fields (PIDs, durations, nonces, fresh element refs); post-compaction guard aborts with `compaction_loop_persisted`; recommended ON for small models | `docs/tools/loop-detection.md` |

Memory and learning: dreaming (light/deep/REM consolidation with Dream Diary, `docs/concepts/dreaming.md`), active memory (blocking recall sub-agent only when message asks about the past and deterministic recall missed, `docs/concepts/active-memory.md`), Skill Workshop (agent-authored skills as proposals with scanner state, hashes, rollback, `docs/tools/skill-workshop.md`). Bundled skills dir includes `coding-agent`, `tmux`, `peekaboo`, `nano-pdf`, `apple-notes`, `apple-reminders`, `things-mac`, `1password`, `summarize`, `openai-whisper`, `python-debugpy`, `node-inspect-debugger`, `healthcheck` (`openclaw/skills/`).

### 1.2 OpenHands software-agent-sdk (`software-agent-sdk/`)

Default preset: `openhands-tools/openhands/tools/preset/default.py`: Terminal, FileEditor, TaskTracker, Browser toolset (optional), Task toolset (subagents), plus built-ins Finish/Think.

| Tool | Notes | Source |
|---|---|---|
| `terminal` | `command, is_input (send stdin to running proc), timeout (soft: asks continue/stop), reset (new session)`. Persistent tmux-style session; no-change timeout then "still running" return. Full output saved to a dir | `openhands-tools/openhands/tools/terminal/definition.py:89-113` |
| `file_editor` | `command (view/create/str_replace/insert/undo_edit), path, old_str, new_str, insert_line, view_range`. Requires unique exact match | `.../file_editor/definition.py:32-60,181` |
| `task_tracker` | `command: view|plan`, `task_list` full replace, each item with `notes` and `status` | `.../task_tracker/definition.py:28-51` |
| Browser toolset (browser-use) | navigate(`url,new_tab`), click(`index`), type(`index,text`), get_state(`include_screenshot`), get_content(`extract_links,start_from_char`), scroll, go_back, list_tabs, switch_tab, close_tab, get_storage, set_storage (cookie/localStorage state in/out), start_recording, stop_recording; screenshots saved with content hash | `.../browser_use/definition.py:141-780` |
| `task` (TaskToolSet) | Launch subagent: short description, prompt, `subagent_type`, `resume` task id. Presets: code-explorer, bash-runner, web-researcher, general-purpose (markdown definitions) | `.../task/definition.py`, `.../preset/subagents/*.md` |
| `delegate` | `spawn` + `delegate` commands | `.../delegate/definition.py` |
| `workflow` | Run Python orchestration script against `wf.run_agent/map_agents/reduce_agent/pipeline`; scripts must not do work themselves; no barrier between pipeline stages | `.../workflow/definition.py:56-134` |
| `ask_oracle` | Consult a second saved LLM profile named "oracle" when stuck (`question`, `context`) | `.../ask_oracle/definition.py` |
| `consult_tom` / sleeptime compute | User-modelling agent consulted for guidance | `.../tom_consult/definition.py` |
| `inspect_image_with_vision` | `image_index, question, profile_name`: routes an attached image to a separate vision-capable LLM profile, so a text-only main model can still "see" | `openhands-sdk/openhands/sdk/tool/builtins/vision_inspect.py:30-50` |
| `switch_llm`, `classify_and_switch_llm` | Agent changes its own model mid-run | `openhands-sdk/.../tool/builtins/` |
| `invoke_skill`, `think`, `finish` | Built-ins | same dir |
| `glob`, `grep`, `apply_patch`, `gemini/*` | Alternate file tool sets per model family (`preset/gemini.py`, `gpt5.py`, `planning.py`) | `.../preset/` |

Also: security risk analysis on actions (`openhands-sdk/.../security/risk.py` LOW/MEDIUM/HIGH with shell-AST parser, LLM analyzer, ensemble, defense-in-depth), critic with iterative refinement (`.../critic/base.py` re-runs task if score under threshold), condensers (`.../context/condenser/`: LLM summarizing, pipeline, no-op), hooks (`.../hooks/`), marketplace for plugins/skills, agent-server + workspace packages (docker/remote runtimes).

### 1.3 Goose (`goose/`)

Extensions are "platform extensions" registered in `goose/crates/goose/src/agents/platform_extensions/mod.rs` (default_enabled flag per extension):

| Extension | Default | Tools | Source |
|---|---|---|---|
| developer | on | `shell` (`command, timeout_secs`; returns stdout/stderr/exit_code/timed_out/output_truncated, long output spilled to temp file), `write`, `edit` (exact before/after, unique), `tree` (line counts, gitignore-aware), `read_image` (local path or http URL, png/jpeg/gif/webp, 20 MB cap) | `developer/mod.rs:105-175`, `developer/image.rs:14` |
| analyze | on | Tree-sitter directory overview, file details, symbol call graph | `analyze/*.rs`, mod.rs:40-45 |
| summon | on | Load knowledge sources and delegate: `source`, `task`, `recipe/agent` name, extension subset (omit = inherit, `[]` = none), provider/model/temperature overrides, `max_turns`, `context` injected into the delegate system prompt, `cwd` (must be inside parent cwd), `background`; plus peek/cancel on running background tasks returning durable turn count, idle time, recent tool activity | `summon.rs:691-774` |
| todo | off | `todo_write` | `todo.rs:17-19` |
| apps | on | `list_apps/create_app/iterate_app/delete_app` (agent-authored HTML apps in own window) | `apps.rs:593-608` |
| chatrecall | off | Search past chat sessions | `chatrecall.rs` |
| extensionmanager | on | `search_available_extensions`, `manage_extensions` (agent turns extensions on/off mid-session), `list_resources` | `ext_manager.rs:69-71` |
| scheduler | on | Create/manage scheduled recipe runs | `scheduler.rs` |
| summarize | off | Load files/dirs and get an LLM summary in one call | mod.rs:143-147 |
| code_execution ("Code Mode") | off | Extension calls through code to save tokens | mod.rs:157-162 |
| orchestrator | off | `list_sessions, view_session, start_agent, send_message, interrupt_agent` | `orchestrator.rs:657-687` |
| tom ("Top Of Mind") | on | MOIM: inject text/file into every turn via env vars | mod.rs:206-211 |
| skills | on | Discover skill instructions | mod.rs:220 |

goose-mcp built-ins (`goose/crates/goose-mcp/src/`): `computercontroller` = `computer_control` (macOS via Peekaboo CLI, subcommand string: see/image/click/type/press/hotkey/paste/scroll/drag/menu/dock/dialog/clipboard/space/open/permissions; "see --annotate" returns annotated screenshot with element IDs; `capture_screenshot` flag to verify), `xlsx_tool` (list_worksheets, get_columns, get_range, find_text, update_cell, get_cell, save), `docx_tool` (extract_text, update_doc modes append/replace/structured/add_image), `pdf_tool` (extract_text, extract_images to PNG) (`computercontroller/mod.rs:564-870`). `memory` = `remember_memory`, `retrieve_memories`, `remove_memory_category`, `remove_specific_memory` (`memory/mod.rs:390-484`). Peekaboo is auto-installed via brew (`peekaboo/mod.rs`). Also recipes + subrecipes, large-response handler (`agents/large_response_handler.rs`), retry (`agents/retry.rs`), tool schema normalizer (`agents/tool_schema_normalize.rs`).

### 1.4 Codex CLI (`codex/codex-rs/`)

Tool specs in `codex-rs/core/src/tools/handlers/*_spec.rs`, plan in `core/src/tools/spec_plan.rs`.

| Tool | Notes | Source |
|---|---|---|
| `exec_command` | `cmd, workdir, tty (PTY), yield_time_ms (default 10000, range 250-30000), max_output_tokens (default 10000), shell, login`, plus approval args; returns `session_id` if still running | `handlers/shell_spec.rs:21-110` |
| `write_stdin` | `session_id, chars (empty = poll), yield_time_ms, max_output_tokens` | `shell_spec.rs:112-155` |
| `shell` (legacy), `apply_patch` | Freeform patch grammar | `handlers/apply_patch*.rs`, `codex-rs/apply-patch/` |
| `update_plan` | Plan with step status | `handlers/plan_spec.rs` |
| `view_image` | `path`, optional `detail` (`high` default resized, `original`), returns data URL; output budget aware | `handlers/view_image_spec.rs:1-50` |
| `web_search` | Hosted provider-side tool with mode config (cached/live), location | `protocol/src/config_types.rs` |
| `request_user_input` | Structured questions: `id, header, question, options[{label,description}]` | `handlers/request_user_input_spec.rs:16-90` |
| `request_permissions` | Agent asks user mid-run for extra filesystem/network permissions; user grants a subset | `shell_spec.rs:161-196` |
| `send_message_to_user_async` | Non-blocking message to user without ending the turn; reply arrives later as new user message | `handlers/send_message_to_user_async.rs` |
| `spawn_agent`, `send_message`, `send_input`, `followup_task`, `wait_agent`, `list_agents`, `interrupt_agent`, `close_agent`, `resume_agent` | Multi-agent v1/v2: `task_name`, `message`, `target`, `interrupt`; `wait_agent` is a mailbox wait that also ends when new user input is steered in; closed agents counted against concurrency until closed | `handlers/multi_agents_spec.rs:80-370` |
| `tool_search` | BM25 over deferred tool metadata, `query, limit`; source directory budgeted to bytes | `handlers/tool_search_spec.rs` |
| `list_mcp_resources`, `read_mcp_resource` | | `handlers/mcp_resource_spec.rs` |
| `get_context_remaining`, `new_context` | Agent can read its remaining token budget and start a fresh context window itself | `handlers/get_context_remaining_spec.rs`, `new_context_window_spec.rs` |
| `sleep` (clock namespace) | `duration_ms` 1..12 h | `handlers/sleep.rs:20-45` |
| `current_time`, `wait_for_environment`, `request_plugin_install`, `list_available_plugins_to_install` | | `handlers/*.rs` |
| code mode `exec` | Freeform JS in embedded V8 with `// @exec:` pragma, nested tool calls, `wait` | `core/src/tools/code_mode/execute_spec.rs`, `codex-rs/code-mode*` |

Infrastructure: guardian (isolated reviewer model that auto-approves/denies approval requests, own input/request budgets, `core/src/guardian/`), network proxy (HTTP 3128 + SOCKS5 8081, allow/deny, "limited" read-only mode with MITM, per-permission-profile, `network-proxy/README.md`), OS sandboxes (`linux-sandbox`, `bwrap`, `windows-sandbox-rs`, `mxc-sandbox`), `execpolicy` crate, two-phase background memory pipeline (extract per rollout, then consolidate; skipped for ephemeral and sub-agent sessions, `memories/README.md`), hooks, git worktree crate, agent identity/message board crates.

### 1.5 Gemini CLI (`gemini-cli/`)

Tool names in `packages/core/src/tools/tool-names.ts`; docs `docs/tools/*.md`.

| Tool | Notes | Source |
|---|---|---|
| `run_shell_command` | `command, description, dir_path, is_background`; returns Command/Directory/Stdout/Stderr/Exit Code/Background PIDs. Policy rules by `commandPrefix`/`commandRegex` | `docs/tools/shell.md` |
| background shell tools | Read output log of a background process: tail lines + `delay_ms` | `tools/shellBackgroundTools.ts:112-280` |
| `read_file`, `read_many_files`, `write_file`, `replace` (edit), `glob`, `grep_search`/ripgrep, `list_directory` | | `tools/*.ts` |
| `web_fetch`, `google_web_search` | | `tools/web-fetch.ts`, `web-search.ts` |
| `write_todos` (legacy) and `tracker_*` | `tracker_create_task` (title, description, type epic/task/bug), `tracker_update_task` (status open/in_progress/blocked/closed, deps), `tracker_get_task` (6-hex id), `tracker_list_tasks` (filter status/type/parent), `tracker_add_dependency` (DAG), `tracker_visualize`; stored at `.gemini/tmp/tracker/<session-id>` | `docs/tools/tracker.md`, `tools/trackerTools.ts` |
| `ask_user` | Structured questions with options | `tools/ask-user.ts` |
| `enter_plan_mode`, `exit_plan_mode` | Plan file name param | `tools/enter-plan-mode.ts` |
| `activate_skill` | Load a skill's body on demand | `tools/activate-skill.ts` |
| `update_topic` | `title, summary, strategic_intent`: agent narrates its phase | `tools/topicTool.ts` |
| `complete_task` | Explicit completion tool for subagents | `tools/complete-task.ts` |
| `invoke_agent` | Run a subagent (local, or remote over A2A protocol) | `tools/tool-names.ts:191`, `agents/*` |
| `get_internal_docs` | Agent reads its own docs | `tools/get-internal-docs.ts` |
| memory | No dedicated tool: agent edits GEMINI.md (project), per-project private memory dir, or `~/.gemini/GEMINI.md` with `write_file`/`replace` | `docs/tools/memory.md` |
| Auto Memory | Background miner over idle sessions (>=10 user messages), drafts unified-diff `.patch` memory updates and `SKILL.md` drafts into an inbox you approve | `docs/cli/auto-memory.md` |
| Checkpointing | Shadow git repo at `~/.gemini/history/<hash>` snapshot before each file-modifying tool + conversation + the pending tool call; `/restore` re-proposes the call. `/rewind` (Esc Esc) reverts conversation and optionally files | `docs/cli/checkpointing.md`, `docs/cli/rewind.md` |
| Browser agent subagent | Disabled by default. Uses bundled `chrome-devtools-mcp` (Chrome 144+), accessibility-tree driven, `analyze_screenshot` sends screenshots to a visual-analysis model call that only returns coordinates/descriptions, `click_at(x,y)` fallback; profile modes persistent (`~/.gemini/cli-browser-profile/`) / isolated / existing (attach to running Chrome); consent dialog; input-blocker overlay while automating; snapshot superseder replaces stale `take_snapshot` outputs with a placeholder before each model call; explicitly excludes lighthouse, performance, screencast, extensions tools | `docs/core/subagents.md:108-181`, `packages/core/src/agents/browser/{browser-tools-manifest.json,browserAgentDefinition.ts,analyzeScreenshot.ts,snapshotSuperseder.ts,inputBlocker.ts}` |
| Policy engine | TOML rules per tool name with `argsPattern` | `docs/reference/policy-engine.md` |

### 1.6 Cline (`cline/`)

Current SDK built-ins (`cline/docs/tools-reference/all-cline-tools.mdx:9-24`): `bash`, `editor`, `read_files` (batch), `apply_patch`, `search` (ripgrep), `fetch_web` (HTML to markdown), `ask_question`. MCP via `.cline/mcp.json`; custom tools registered via SDK plugins. Subagents: `use_subagents` launches parallel read-only research agents (each own context and token budget, cannot edit, use browser, MCP or nest; per-subagent cost tracked) (`docs/features/subagents.mdx`). Other: checkpoints (`docs/core-workflows/checkpoints.mdx`), plan/act modes, auto-approve and auto-compact (`docs/features/`), Jupyter notebook cell tools in VS Code (`docs/features/jupyter-notebooks.mdx`), cron service in core (`sdk/packages/core/src/cron/`), Kanban multi-agent board (`docs/kanban/`). Legacy `browser_action` (launch/click/type/scroll_down/scroll_up/close, Puppeteer, screenshot + console logs per step) is not in the current tree (unverified, not in cloned tree).

### 1.7 Roo Code (`Roo-Code/`)

Native tools in `Roo-Code/src/core/prompts/tools/native-tools/`; groups in `Roo-Code/src/shared/tools.ts`:
- Tool groups: `read` (read_file, search_files, list_files, codebase_search), `edit` (apply_diff, write_to_file, generate_image; custom edit, search_replace, edit_file, apply_patch), `command` (execute_command with `command, cwd, timeout`; read_command_output with artifact id, `search`, `offset`, `limit`), `mcp` (use_mcp_tool, access_mcp_resource), `modes` (switch_mode, new_task) (`shared/tools.ts` TOOL_GROUPS).
- Always available: `ask_followup_question`, `attempt_completion`, `switch_mode`, `new_task` (`mode, message, todos`), `update_todo_list`, `run_slash_command`, `skill` (`shared/tools.ts:317-325`).
- Modes (`packages/types/src/mode.ts:9-60`): architect (`groups: read, edit limited by fileRegex "\.md$", mcp`), code, ask (read+mcp), debug, orchestrator. Mode = tool-group allowlist + role prompt + optional `fileRegex` on edit; `filter-tools-for-mode.ts` removes disallowed schemas.
- `ToolRepetitionDetector`: 3 identical consecutive calls then asks user (`core/tools/ToolRepetitionDetector.ts`).
- `generate_image` (prompt, path, optional input image for edits), `codebase_search` (semantic index with embeddings).
- Roo's browser tool is not in the current tree.

### 1.8 opencode (only items beyond prior research) (`opencode/packages/opencode/src/tool/`)

- `lsp` tool: `operation` in goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol (`query`), goToImplementation, prepareCallHierarchy, incomingCalls, outgoingCalls; `filePath, line, character` 1-based; servers must be configured per file type (`lsp.txt`, `lsp.ts:23-32`).
- `webfetch`: `url, format (text|markdown|html), timeout<=120 s`, 5 MB cap, http auto-upgraded to https (`webfetch.ts:9-21`).
- `websearch` (Exa/Parallel gated by flag, `registry.ts:58-63`), `question` tool with `multiple`, auto "type your own answer", "(Recommended)" first option (`question.txt`).
- `invalid` tool: bad-arguments sink that returns the validation error to the model instead of crashing (`invalid.ts`).
- Output truncation: 2000 lines / 50 KB, full output spilled to a dir kept 7 days (`truncate.ts:12-17`).
- Experimental Code Mode `execute` tool: confined script over connected MCP tools (`code-mode.ts`).
- Per-agent plan tools `plan_enter/plan_exit` (`plan-enter.txt`).

---

## 2. Gap matrix

Legend: Y = ships it; P = partial/narrower; - = not present; "Grain" column uses what exists in `backend/personal_os`.

| Capability | OpenClaw | OpenHands | Goose | Codex | Gemini CLI | Cline/Roo | opencode | **Grain (built)** |
|---|---|---|---|---|---|---|---|---|
| Interactive browser (click/type/snapshot) | Y (`browser`, own profile + attach to real Chrome) | Y (browser-use toolset) | - (Peekaboo only) | - | Y (subagent, chrome-devtools-mcp) | - now (legacy only) | - | **-** (`open_page` is an offscreen read-only loader; Web widget is a user-facing webview) |
| Browser state: network log, console errors, emulate, storage export | Y | P (storage get/set) | - | - | Y (devtools) | - | - | - |
| Desktop GUI control (screenshot + click/type) | Y (`computer`, Peekaboo/CUA) | - | Y (`computer_control`, Peekaboo) | - | - | - | - | **-** (Shortcuts only) |
| Persistent shell / PTY / stdin | Y (`exec`+`process`, pty) | Y (terminal, is_input) | P (one-shot shell) | Y (`exec_command`+`write_stdin`, tty) | P (background + log tail) | P (Roo `read_command_output`) | P | P (`shell_run/poll/kill`; verify PTY + stdin support) |
| Stateful code kernel (Jupyter/Python session) | - | - (IPython removed) | - | P (JS V8 code mode) | - | P (notebook cell edit) | - | **-** (`run_python` one-shot; `sandbox_exec` files persist) |
| View image / screenshot into model | Y (`view_image`) | Y (`inspect_image_with_vision` side model) | Y (`read_image`) | Y (`view_image`, detail) | via read_file multimodal (unverified) | P | P | **-** |
| Vision via a separate vision model for text-only main model | Y (imageModel/pdfModel fallbacks) | Y | - | - | Y (`analyze_screenshot`) | - | - | **-** |
| PDF read (native/extract with pages, password) | Y | - | Y (`pdf_tool` text+images) | - | via read_file | - | - | P (`extract_text.py` pypdf for uploads; no agent PDF tool with pages/OCR) |
| Office docs read/write (docx/xlsx/pptx) | via skills (nano-pdf, etc.) | - | Y (docx, xlsx tools) | - | - | - | - | P (read docx text; Google Docs/Sheets API tools; no local xlsx/docx write) |
| Image generation | Y | - | - | - | - | Y (Roo `generate_image`) | - | - |
| Structured clarifying question tool | Y (`ask_user` 1-3 q) | - | - | Y (`request_user_input`) | Y | Y | Y | P (`desk_ask` in desks; verify in chat) |
| Non-blocking message to user mid-run | P (`progress_card`) | - | - | Y (`send_message_to_user_async`) | Y (`update_topic`) | - | - | P (`desk_deliver`) |
| Agent requests extra permissions mid-run | P (`elevated`) | P (risk) | - | Y (`request_permissions`) | - | - | - | P (ask rules; no agent-initiated grant request) |
| Agent obtains a credential without seeing it | Y (`secrets`) | - | - | - | - | - | - | - |
| Todo / task graph | P (goal) | Y (task_tracker) | Y (todo) | Y (update_plan) | Y (tracker_* DAG) | Y | Y | Y (`todo_write`, plans) |
| Durable per-session goal with token budget | Y | - | - | - | - | - | - | P (approved plan) |
| Sub-agents | Y | Y (task, delegate) | Y (summon) | Y (multi_agents) | Y (invoke_agent, A2A) | Y | Y | Y (`agent_spawn/wait/stop`, `desk_start`) |
| Messaging between running agents / mailbox | Y (`sessions_send`) | P | Y (orchestrator) | Y (`send_message`, `followup_task`, `wait_agent`) | P | - | - | - |
| Steer a running agent mid-run | Y (`/steer`) | - | Y (interrupt) | Y (steer ends wait) | P | - | - | verify |
| Side question not stored in history | Y (`/btw`) | - | - | P (fork) | - | - | - | - |
| Script-driven orchestration | Y (swarm, Lobster) | Y (workflow python) | P | Y (code mode) | - | - | Y (execute) | Y (declarative workflows) |
| Second-opinion model tool | - | Y (`ask_oracle`) | - | Y (guardian reviewer) | - | - | - | - |
| Agent switches own model | - | Y (`switch_llm`) | - | - | - | Y (switch_mode) | - | - |
| LSP / code intelligence | - | - | Y (tree-sitter `analyze`) | - | - | P (codebase_search) | Y (`lsp`) | - |
| Semantic codebase search | - | - | - | - | - | Y (Roo) | - | P (document/memory embeddings, not workspace code) |
| Git/GitHub helpers | Y (skills: github, gh-issues) | P | - | Y (git-utils, worktree) | P (worktrees doc) | P | - | P (`github_search/read`; shadow-git undo; no commit/PR tools) |
| Persistent memory tool | Y (dreaming, active memory) | Y (tom) | Y (4 memory tools) | Y (2-phase pipeline) | Y (file-based + auto memory inbox) | - | - | Y (`save_memory`, learn.py) |
| Self-authored skills | Y (Skill Workshop) | P | P | P (skills) | Y (Auto Memory drafts) | P (`skill`) | P | Y (`skill_draft/revise/from_run`) |
| Heartbeat / proactive wake | Y (heartbeat, standing orders) | - | Y (scheduler) | - | - | P (cron in Cline core) | - | Y (cron jobs, `schedule_task`; no event-driven wake of a desk) |
| Checkpoint / rewind conversation + files | - | - | - | Y (rollback) | Y (checkpoint, `/rewind`) | Y (checkpoints) | Y (snapshot) | Y (shadow-git undo) |
| Loop / stuck detection | Y (rolling + post-compaction guard) | P (critic) | - | - | - | Y (3 identical) | - | Y (`stuck.py`: ping-pong, error storm; post-compaction guard unverified) |
| Tool catalog deferral / search | Y (`tool_search`/`describe`/`call`) | - | Y (extension manager) | Y (`tool_search` BM25) | - | Y (modes filter) | - | P (`mcp_tool_search`, MCP tools only) |
| Mode = tool-group allowlist per task | Y (profiles) | P (presets) | P | P | P | Y (modes with fileRegex) | Y (agents) | P (plan mode, presets) |
| Output compaction of tool results | Y (tokenjuice) | Y (condenser) | Y (large response handler) | Y | P | P | Y (truncate+spill) | Y (paged handles `read_tool_result`) |
| Agent can read its own context budget / reset context | - | - | - | Y (`get_context_remaining`, `new_context`) | - | - | - | - |
| Wait/sleep tool | P | - | - | Y (`sleep` up to 12 h) | P (`delay_ms`) | - | - | - |
| Network egress policy for shell (proxy allow/deny) | P (`allowedHosts`) | - | - | Y (network-proxy) | P | - | - | P (Seatbelt; verify egress control) |
| Agent-authored UI apps | Y (show_widget, canvas) | - | Y (apps) | - | - | - | - | Y (HTML artifacts) |
| Screen/audio activity recall | - | - | - | - | - | - | - | **Y** (activity monitor, meetings; unique) |

---

## 3. Mechanisms worth copying (concrete)

1. **Single `browser` tool with ref-based snapshots (OpenClaw).** One tool, `action` enum; `snapshot` returns a stable tree with element refs; `query` filter keeps only lines containing all tokens and retains refs, plus `maxChars`; `text` (selector, <=40,000 chars) answers questions without a full snapshot; `navigate` returns the compact snapshot inline to save a round trip; refs go stale on DOM change and fail loudly (`docs/tools/browser/agent-tools.md`). `requests` (network log, `filter`, `limit` 50) and `errors` give debugging for free.
2. **Snapshot superseder (Gemini).** Before each model call, replace all but the newest `take_snapshot` result with a short placeholder (`agents/browser/snapshotSuperseder.ts`). Essential for weak/open models and small context.
3. **Visual side-call (Gemini `analyze_screenshot`, OpenHands `inspect_image_with_vision`).** The main model never receives pixels; a separate vision profile is asked a question and returns coordinates/descriptions. Fits Grain's "non-frontier text model" reality: add `vision_model` setting, tool `view_image(path, question?)`.
4. **Browser profile modes (Gemini, OpenClaw).** persistent / isolated / attach-to-existing, first-use consent dialog, input-blocker overlay while the agent drives (`inputBlocker.ts`), explicit exclusion list of heavy devtools tools in a manifest.
5. **`computer` safety design (OpenClaw).** Every coordinate action must echo the last screenshot `frameId`; pixel-identical frame returns "screen unchanged" metadata only; screenshots model-only and marked untrusted; force-kill, downloads, file input, recording and replay classified as separate high-risk actions (`docs/nodes/computer-use.md`). Goose's annotated `see` returns element IDs so the model clicks by ID, not pixels (`computercontroller/mod.rs:851`).
6. **PTY/stdin pair (Codex).** `exec_command{cmd, workdir, tty, yield_time_ms 250-30000 default 10000, max_output_tokens default 10000}` returns a `session_id` when still running; `write_stdin{session_id, chars (empty=poll), yield_time_ms}` (`codex/codex-rs/core/src/tools/handlers/shell_spec.rs`). OpenHands equivalent: `is_input` flag, soft `timeout` that asks continue/stop, `reset`.
7. **Tool search with a bounded directory (OpenClaw/Codex).** Put a context-scaled directory of tool names (descriptions shortened before names are dropped) in the prompt, `tool_search(query, limit)` BM25, `tool_describe` for exact schema, call. Do not copy untrusted MCP descriptions into the system prompt (`docs/tools/tool-search.md`; `codex-rs/core/src/tools/handlers/tool_search_spec.rs`). Grain already has this for MCP; extend to all optional native groups (google, sandbox, health, meetings, activity).
8. **Goal object (OpenClaw).** One durable objective per session with pause/resume/block/complete and token budget; visible in UI footer (`docs/tools/goal.md`). Maps neatly to a desk's plan.
9. **`secrets` request (OpenClaw).** Agent names a credential and host(s); user types it into a trusted prompt; value written to store; agent receives only a SecretRef; timeout 15 min (30-3600 clamp); cancelled if the run closes (`docs/tools/secrets.md`). Pair with Grain's Keychain.
10. **`send_message_to_user_async` + `request_permissions` (Codex).** Non-blocking "attention" message with reply arriving later as a new user message; and agent-initiated permission requests that the user can grant partially (`send_message_to_user_async.rs`, `shell_spec.rs:161`).
11. **Subagent mailbox semantics (Codex v2).** `spawn_agent{task_name, message}`, `send_message` (no new turn), `followup_task` (triggers a turn, queued at message boundary), `wait_agent` waits on a mailbox and also wakes on user steering, `interrupt_agent`, `close_agent` (descendants too; open agents count against concurrency) (`multi_agents_spec.rs:140-370`).
12. **Summon delegation options (Goose).** Per-delegate extension subset (omit = inherit, `[]` = none), model override, `max_turns`, injected `context`, `cwd` constrained inside parent cwd, background with peek returning turn count/idle time/recent tool activity (`summon.rs:691-774`).
13. **Mode as tool-group allowlist with file regex (Roo).** `groups: ["read", ["edit", {fileRegex: "\\.md$"}], "mcp"]`; always-available set (ask, completion, todo, skill) (`packages/types/src/mode.ts:18`, `shared/tools.ts:317`). Cheap per-desk "role".
14. **Loop detector hygiene (OpenClaw).** Strip volatile fields (PIDs, durations, session ids, nonces, fresh element refs) when hashing; keep per-run scope; post-compaction guard aborts when same `(tool,args,result)` recurs right after a compaction retry (`docs/tools/loop-detection.md`). Grain's `stuck.py` already strips VOLATILE; adding the post-compaction guard is small.
15. **Output spill (opencode, Goose).** 2000 lines/50 KB cap, spill full output to a file retained 7 days, tell the model the path (`opencode/.../truncate.ts:12-17`; Goose shell `output_truncated` + temp file). Grain already has paged handles.
16. **Auto memory inbox (Gemini).** Mine idle sessions (>=10 user messages, skip active/sub-agent), draft memory as `.patch` and skills as `SKILL.md`, human approves (`docs/cli/auto-memory.md`). Matches Grain's propose-only stance.
17. **PDF tool params (OpenClaw).** `pdf|pdfs (<=10), prompt, pages "1-5,7-9", password, model`; native when provider supports, extraction fallback otherwise (`docs/tools/pdf.md`).
18. **Office tools (Goose).** `xlsx_tool` ops list_worksheets/get_columns/get_range/find_text/update_cell/get_cell/save; `docx_tool` extract_text/update_doc(append, replace, structured, add_image); `pdf_tool` extract_text/extract_images (`computercontroller/mod.rs:564-870`).
19. **LSP tool (opencode).** 9 operations over `filePath,line,character` (1-based); needs a configured server per language (`lsp.txt`).
20. **Self-checks (OpenHands).** Critic with iterative refinement: score below threshold triggers an automatic follow-up prompt (`openhands-sdk/.../critic/base.py`); `ask_oracle` second-model consult.

## 4. Not worth copying / risks

- **Remote `code_execution` (OpenClaw/xAI):** sends data to a third party, billed per call; conflicts with local-first.
- **OpenClaw Code Mode / Codex code mode / opencode `execute`:** JS sandbox that hides tool schemas; only helps strong models (OpenClaw gates it to a "preferred Code Mode performers" tier, `docs/tools/code-mode.md`). Poor fit for weak open models; Grain's workflows plus tool bridge already cover orchestration.
- **Peekaboo/CUA pixel clicking as a default:** Grain's roadmap explicitly says "do NOT click pixels" (memory: agency roadmap anti-goals). If computer use is added, make it opt-in per desk, ask-first, accessibility-tree first, never default on. Peekaboo needs Accessibility + Screen Recording + Event posting and brew install.
- **Heartbeat polling (OpenClaw):** also an anti-goal in Grain's roadmap ("no heartbeat"). Event-driven wake of a desk (e.g. background shell finished) with flood guard is the safer alternative.
- **Agent `switch_llm` / self model choice:** cost and safety surprises; skip.
- **Cline/Roo XML-style legacy tool names and mode-switch tool:** Roo's `switch_mode` lets the model change its own permissions (only with user approval); only copy the group allowlist, not model-initiated switching.
- **Remote A2A agents (Gemini `a2a-*`):** extra attack surface for little local value.
- **Tom / MOIM per-turn injection (Goose):** unconditional injection costs tokens every turn.
- **Auto-attach to the user's real signed-in Chrome (OpenClaw `user` profile):** powerful but exposes cookies/sessions to prompt injection; require ask-first, per-site allowlist, taint rules like other external content.
- **Browser content is untrusted input:** all of OpenClaw's and Gemini's browser docs treat page text as data; Grain's taint rules must cover browser snapshots.

## 5. Ranked recommendations for Grain

1. **Vision: `view_image` + vision side-model setting (S-M).** Tool `view_image(path, question?, detail?)` returning image content when the active model is multimodal, else routing to a configured vision profile and returning text (OpenHands `vision_inspect.py`, Gemini `analyzeScreenshot.ts`, Codex `view_image_spec.rs`). Cap size (Goose 20 MB), supports png/jpeg/gif/webp, home-scoped path rules from `reach.py`. Unblocks screenshots from sandbox, browser, and activity monitor.
2. **Interactive `browser` tool in a desk (L).** One tool with `action` enum, ref-based snapshot with `query`/`maxChars`, `text`, `screenshot` (feeds #1), `act` (click/type/select/press), `tabs`, `navigate` returning compact snapshot, `requests`/`errors`. Run on an Electron-owned hidden `BrowserView`/CDP session via the existing main-process bridge (`pagefetch.ts`), isolated persistent profile per desk, optional attach later. Add Gemini's snapshot superseder and an input blocker overlay. Treat all page text as tainted. Highest-value missing capability.
3. **PTY + stdin for `shell_*` and an event wake (S-M).** Confirm `shell_run` supports `tty`, `yield_time_ms`, `max_output_tokens`; add `shell_write{session_id, chars}` (Codex `write_stdin`). Add an event-driven desk wake when a backgrounded job exits (rate-limited: 30 s min, 5 per 60 s, copying OpenClaw's guard) instead of polling.
4. **`pdf` and office tools for desks (M).** `pdf_read(path, pages, password)` with image-page fallback through #1 when text extraction is empty; `xlsx_read/update` and `docx_read/append` (Goose ops list) writing only inside the desk workspace with read-before-write and shadow-git undo. Builds on `extract_text.py`.
5. **Tool search for all optional groups + per-desk "mode" allowlists (M).** Generalise `mcp_search.py` to native groups (google, health, meetings, activity, sandbox) with a bounded directory; add Roo-style `groups` allowlist with `fileRegex` for edit so a desk can be "research" (read+web), "writer" (edit `*.md` only), "builder" (shell+edit). Reduces schema count for open models.
6. **Agent-initiated `request_permissions` and `secrets` request (S-M).** Desk can ask for an extra path or host, user grants a subset for this desk only (Codex); `secrets_request(name, reason, allowed_hosts)` stores into Keychain and returns only a ref (OpenClaw). Fits ask-first design and approval store.
7. **Async user message + clarifying question parity (S).** `message_user_async(message)` that does not end the turn (Codex) and structured `ask_user(questions[1-3]{header,question,options[{label,description}], multiple})` available in chat as well as desks, with answers returned as labels (Codex/opencode/OpenClaw). Skip for subagents.
8. **Sub-agent mailbox + delegate options (M).** Add `agent_send(target, message, interrupt)` and mailbox-style `agent_wait` that also wakes on user steering (Codex), plus delegate options from Goose summon: extension/tool-group subset (`[]` = none), `max_turns`, injected `context`, `cwd` inside parent workspace, peek status (turns, idle, recent tool activity).
9. **Goal + context-budget tools (S).** A per-desk goal object with status (active/paused/blocked/complete) and token budget (OpenClaw `goal`), plus `get_context_remaining` and `sleep(duration_ms)` (Codex) so weak models can pace themselves and self-compact.
10. **Opt-in `computer` (screenshot + accessibility-first click/type) and LSP/git helpers (L, optional).** Computer use only behind a desk-level toggle with frame-id fencing, "screen unchanged" dedupe, high-risk action classes (OpenClaw), after #1 and #2 prove out. Separately, a read-only `lsp` tool (opencode 9-op schema) and `git_status/diff/log/commit` helpers inside the desk workspace sit on top of existing shell and shadow-git; lower priority than the above.

Quick wins that need no new surface: add the post-compaction repeat guard to `stuck.py` (S); add Gemini-style Auto Memory "inbox" for skills drafted from finished desks (Grain already has `skill_from_run`, so this is mostly UI).
