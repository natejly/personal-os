# Artifacts, dashboards, canvas, charts, web reach, and the Python sandbox

Inventory of the working tree at `/Users/natejly/Desktop/Personal OS` on 2026-10-02. Code on disk wins over docs and over the knowledge graph. Status words used below: **shipped** (callable from the app), **partial** (present but narrower than the surrounding docs or UI imply), **absent** (no implementation found). No usage metrics were measured.

## What can dashboards do?

### Takeaway
Dashboards are shipped: HTTP, RSS, and internal sources with server-held secrets; AI-written HTML widgets in a CSP iframe; AI summary widgets; static markdown; and declarative chart, stat, and table widgets that bind a JSON spec to one source and refresh without another model call. There is no agent tool that creates a dashboard or a widget.

### Cited Findings
- Tables `data_sources`, `dashboards`, `widgets`, and `recaps` are created in `Dashboards.__init__`. Source kinds are `http`, `rss`, and `internal`. Widget kinds documented on the table are `html`, `summary`, `markdown`, plus `chart`, `stat`, and `table`. Width is stored as 1–3 columns. Extra columns `spec`, `data`, and `data_error` are added with `ALTER` if missing. — [dashboards.py](backend/personal_os/dashboards.py)
- Internal source keys are `todos`, `calendar`, `gmail`, `memories`, `projects`, `boards`. — [dashboards.py `INTERNAL_SOURCES`](backend/personal_os/dashboards.py)
- `sources()` strips `secret` unless `with_secret=True` and returns `has_secret`. `fetch_source` injects the secret into a header (`Authorization` / configurable name and prefix, default `Bearer `) or a query parameter (`auth_in=query`). HTTP and RSS fetches go through `guarded_request` with redirects off. HTTP JSON is parsed; non-JSON becomes `{"text": ...}` capped at 200,000 characters. RSS items are capped at 40, with text fields stripped of tags and cut to 600 characters. Failures are stored on `last_status`. — [dashboards.py `fetch_source`, `_parse_rss`](backend/personal_os/dashboards.py)
- HTML widgets: `generate_widget_code` asks the model for one HTML document. Each source is described as `fetch("{base}/sources/{id}/fetch")` plus a sample truncated at 1,800 characters. The system prompt says the iframe is `allow-scripts` only, no external scripts or CSS, and credentials stay server-side. — [dashboards.py `WIDGET_SYSTEM`, `generate_widget_code`](backend/personal_os/dashboards.py)
- The iframe cannot send the app token. `render_widget` rewrites `/sources/{id}/fetch` URLs to add `wt`, `w`, and `we`. The token is HMAC-SHA256 of `widget:{wid}:{exp}` with the app secret, truncated to 32 hex chars, TTL 12 hours. Middleware accepts that query token only when the source id is in that widget's `source_ids`. CSP is `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; font-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'`. — [app.py `WIDGET_CSP`, `_widget_fetch_token`, `_require_token`, `render_widget`](backend/personal_os/app.py)
- Summary widgets call `generate_summary` on every `_run_widget` pass (including refresh). The data blob is capped at 24,000 characters. The model is `extractionModel` or else `defaultModel`. Recaps are one row per calendar day in `recaps`, skipped when `apiKey` is empty, otherwise built from recent chats, memories, todos, projects, the next calendar slice, and up to 10 unread mail subjects, then cached. — [dashboards.py `SUMMARY_SYSTEM`, `generate_summary`, `generate_recap`](backend/personal_os/dashboards.py); [app.py `_run_widget`, `recap`](backend/personal_os/app.py)
- Declarative kinds are `chart`, `stat`, `table` (`widget_spec.KINDS`). The model writes a JSON spec once (`path`, `select`, transforms `sort`/`limit`/`filter`/`group`, and a `chart`/`stat`/`table` block). `generate_spec` validates, runs `autofix`, and makes at most one repair call (`llm.complete` with `kind="widget"`). A plain refresh re-binds and does not call the model. `GET /widgets/{wid}/data` returns cached rows while `now - refreshed_at < refresh_minutes * 60` (minimum window 60 seconds). Rows are capped at 500; series at 8. Numeric strings such as `$1,234` and `12%` are coerced. — [widget_spec.py](backend/personal_os/widget_spec.py); [app.py `_run_widget`, `get_widget`, `widget_data`, `refresh_widget`, `revise_widget`](backend/personal_os/app.py)
- `revise_widget` appends the instruction to the prompt and regenerates. For declarative kinds that regenerates the spec; for `html` it regenerates code. — [app.py `revise_widget`](backend/personal_os/app.py)
- The Dashboards screen offers source types HTTP API (JSON), RSS/Atom, and Grain data, and widget types Interactive (AI-coded), AI summary, Chart, Stat, and Table. Chart/stat/table render through `DeclarativeWidget` (no iframe). HTML renders in an iframe of the render URL. Summary and markdown render as markdown. Deleting a dashboard or widget also deletes canvas windows of kind `dashboard-widget`. — [DashboardsView.tsx](src/renderer/src/components/DashboardsView.tsx); [DeclarativeWidget.tsx](src/renderer/src/components/DeclarativeWidget.tsx); [app.py `delete_dashboard`, `delete_widget`](backend/personal_os/app.py)
- `tools.py` has no registration whose name or body mentions dashboard, widget create, or data source. — [tools.py](backend/personal_os/tools.py)

### Inferences
- HTML and summary widgets are still model-authored on each regenerate or (for summary) each refresh. Chart, stat, and table are the path that separates a stored spec from a TTL re-fetch.
- A declarative widget binds only the first id in `source_ids` (`run_widget` takes `(source_ids or [""])[0]`). HTML widgets can be told about several sources.
- Key sandboxing applies to the HTML iframe's fetch capability. Declarative widgets never see the secret because the backend fetches.

### Gaps
- Whether the HTML iframe's `sandbox` attribute is `allow-scripts` only was stated in the model prompt and in `sota-canvas.md`, and the canvas widget comment says the iframe is the expensive part, but this note did not re-read the iframe markup in `dashboardWidget.tsx` past the declarative branch.
- No measurement of how often generated HTML fails at runtime. There is no lint or repair loop for HTML widgets in `generate_widget_code`.

## What is canvas or spaces, and are artifacts wired?

### Takeaway
A space is a persisted canvas of floating windows. Fifteen widget kinds are registered on both sides, including `artifact`. Artifacts are wired: storage, signed render route, patch and rewrite tools, and a canvas window. The agent has no tool that opens or arranges a space. The six-window "heavy" cap exists in code and is not applied, because every non-minimized window on the space is rendered up front.

### Cited Findings
- `Canvases` owns `canvases` (name, optional `project_id`, snap mode, grid, zoom, pan, wallpaper, `locked`) and `canvas_windows` (kind, `ref_id` with no foreign key, geometry, state `normal|minimized|maximized|popped`, restore and popout bounds, pinned, opacity, `config` JSON). Tables are created in `Canvases.__init__`, not in `db.py` migrate. `WIDGET_KINDS` is the 15-kind tuple ending in `artifact`, and the comment says `src/shared/types.ts` `WidgetKind` is the source of truth. The TypeScript union matches. — [canvas.py](backend/personal_os/canvas.py); [types.ts `WidgetKind`](src/shared/types.ts)
- Frontend catalog `WIDGETS` is a `Record<WidgetKind, WidgetDef>` so a missing kind is a compile error. Kinds and labels: chat, todos (from the shell module registry), calendar, board, note, dashboard-widget ("Widget"), memory, graph, documents, recap, project, usage, activity, web, artifact. `needsRef` is set on artifact and dashboard-widget (the type comment also lists chat, board, note, project). — [registry.ts](src/renderer/src/canvas/registry.ts)
- Presets keep windows whose referent still exists. `REF_TABLES` maps `chat`, `board`, `note`, `dashboard-widget`, `project`, and `artifact` to tables. Other kinds have no referent check. — [presets.py](backend/personal_os/presets.py)
- `tools.py` does not call `canvases` or `add_window`. Windows are placed from the UI (and from presets). — [tools.py](backend/personal_os/tools.py)
- `Canvas.tsx` sets `EAGER = true`. `liveWindows` then marks every window that is not minimized or popped as live, and does not apply `HEAVY_CAP` (6) or the 60% zoom gate. Those gates remain in the `EAGER === false` branch. A test states the cap is held in reserve. Kinds flagged `heavy`: artifact, calendar, dashboard-widget, graph, usage, web. — [Canvas.tsx `EAGER`, `liveWindows`](src/renderer/src/canvas/Canvas.tsx); [canvasStore.test.ts](src/renderer/src/canvas/canvasStore.test.ts)
- Artifacts module is instantiated: `artifacts = Artifacts(db)` and `app.include_router(artifact_router(...))`. Render URLs are HMAC capabilities, TTL 24 hours, and the auth middleware exempts the render path. Delete sweeps canvas windows of kind `artifact`. — [app.py](backend/personal_os/app.py)
- Routes include list, create, get, update, delete, versions, restore, edit (patch), revise (full rewrite), set code, and render. — [artifact_routes.py](backend/personal_os/artifact_routes.py)
- Tools, registered only when `artifacts` is passed into the toolbox: `artifact_create`, `artifact_update` (full HTML), `artifact_edit` (exact search/replace via `artifact_patch.py`), plus `artifact_read` and `artifact_list` (named in the module docstring). Group `artifacts`, danger `writes`. The model is told to use them for a kept page and not to paste large HTML into chat. The chat stream attaches the created artifact onto the tool event. — [artifact_tools.py](backend/personal_os/artifact_tools.py); [app.py system prompt around the html fence](backend/personal_os/app.py)
- Stored kind is `html` only. Versions are append-only, `MAX_VERSIONS` 50, numbers only increase. Render CSP is `default-src 'none'`, `connect-src 'none'`, `sandbox allow-scripts`, `img-src data: blob:`, `frame-ancestors` limited to the app. `blocked_capabilities` flags `fetch(`, XHR, WebSocket, remote script/link/iframe src, storage, forms, and `window.open`. `inject_shim` inserts a resize/title `postMessage` script. — [artifacts.py](backend/personal_os/artifacts.py)
- `ArtifactWidget` loads `api.artifacts.get`, iframes the signed render URL only while `live`, accepts `resize` and `setTitle` after `readFrameMessage` checks `event.source`, and offers history, revise, download, and copy. It is `needsRef` and `heavy`. — [artifact.tsx](src/renderer/src/canvas/widgets/artifact.tsx)
- A dashboard window loads `GET /widgets/{id}` and, for declarative kinds, `GET /widgets/{id}/data`. HTML still uses the render iframe. Refresh calls the refresh route. — [dashboardWidget.tsx](src/renderer/src/canvas/widgets/dashboardWidget.tsx)

### Inferences
- "Spaces" in the product are canvases. The fallback name is `"Space"`; an old default `"Desk 1"` is renamed once to `"Space 1"`. That rename is about canvas labels, not cowork desks. — [canvas.py `DEFAULT_NAME`, `_RENAMED_DEFAULTS`](backend/personal_os/canvas.py)
- Artifacts are a backend feature with a canvas window and chat tools. They are not "unwired."
- Presets still store refs and geometry, not a copy of artifact HTML or widget specs. Instantiating a preset skips a window whose row is gone.

### Gaps
- This note did not walk every widget's data source (calendar, graph, web, and so on) past the registry label. The web canvas widget is an in-app browser surface (`web.tsx`); its computer-use behavior was left alone.
- Whether chat's "Open in space" card is wired for every artifact tool result was not re-read in `ToolEvents.tsx`. The backend does emit an `artifact` event from the chat stream.

## How do chart, mermaid, and interactive blocks work?

### Takeaway
Chat and doc markdown share one renderer. Fences `chart`, `interactive`, `mermaid`, and HTML/SVG become live blocks. Charts are Recharts from a JSON spec (500 rows). Interactive blocks add local controls and a non-eval formula parser. Mermaid is lazy-loaded. The same chart components also draw declarative dashboard widgets. There is no model repair loop when a fence fails to parse.

### Cited Findings
- `MarkdownPreview` is the shared pipeline (GFM, highlighting, KaTeX). `Pre` dispatches `chart` to `ChartBlock`, `interactive` to `InteractiveBlock`, `mermaid` to `MermaidBlock`, and HTML/SVG via `fenceKind` to `HtmlBlock`. — [MarkdownPreview.tsx](src/renderer/src/components/MarkdownPreview.tsx)
- `parseSpec` accepts a documented shape plus Chart.js `labels`/`datasets`, `[[x,y]]` pairs, and key-value maps. Types: `bar`, `line`, `area`, `pie`, `scatter`. `MAX_ROWS` is 500. Mixed series, stacked, units, and axis labels are fields on `Spec`. Colors are `var(--chart-1)` through `--chart-8`. Views are chart, table, and source. Parse errors render as "Couldn't render chart" with no further model call in this component. `xType: 'number'` is used by interactive sweeps; plain chart fences leave it unset. Y domain comments say bars start at zero and lines/areas fit the data. No brush component is imported. — [ChartBlock.tsx](src/renderer/src/components/ChartBlock.tsx)
- Interactive fences: controls `slider`, `number`, `select`, `toggle`, caps 12 controls, 8 series, 6 readouts, 400 sweep points, 500 data rows. Formulas go through `lib/expr` `tryCompile`, not `eval`. X can be a sweep (`from`/`to`/`steps`), a `values` list, or `data` rows. — [InteractiveBlock.tsx](src/renderer/src/components/InteractiveBlock.tsx)
- Mermaid is `import('mermaid')` on first use, `m.render`, diagram vs source, and on a failed edit it keeps the previous SVG and shows the error. Streaming shows "Drawing diagram…". — [MermaidBlock.tsx](src/renderer/src/components/MermaidBlock.tsx)
- `DeclarativeWidget` builds a `ChartBlock` `Spec` from bound rows (`boundChartSource`) and uses `Chart` and `DataTable`. Stat is a number plus optional delta. Table is an HTML table from bound columns. The file states nothing there is model-written code. — [DeclarativeWidget.tsx](src/renderer/src/components/DeclarativeWidget.tsx); [boundWidget.ts](src/renderer/src/lib/boundWidget.ts)
- The README documents the interactive fence, the control kinds, readout aggregates (`_last`, `_first`, `_min`, `_max`, `_sum`, `_mean`), the expr whitelist, and lazy mermaid, and points simple charts at the fence and harder figures at `run_python` plus `plt.savefig`. — [README.md charts section](README.md)

### Inferences
- A ```chart fence is self-contained JSON in the message. It is not a dashboard source binding. Pinning live data is the declarative widget path, which then can sit in a `dashboard-widget` window.
- Transforms (sort, limit, filter, group) exist on dashboard specs, not inside `parseSpec`.

### Gaps
- `expr.ts` itself was not re-read; the no-eval claim is the component comment plus the README. Tests are named `npm run test:expr` in the README and were not run.
- No check of whether a failed chart fence is sent back to the model anywhere outside `ChartBlock`.

## How do web search, fetch, and the Python sandbox work?

### Takeaway
`web_search` and `fetch_url` are shipped with the pipeline `sota-reach.md` still describes as future work: content-type routing, numbered links, BM25 focus, offset paging, a one-hour cache, SearXNG, and reciprocal-rank merge of keyless engines. `run_python` is a throwaway macOS seatbelt sandbox with a shared venv and inline figures. The per-chat Linux container is a separate, persistent sandbox with stop-on-quit, a 14-day keep, and filesystem checkpoints.

### Cited Findings
- `web_search` calls `websearch.search` with `time_range` (`day|week|month|year`) and `site`, pages the rows, and `_allow_url`s every result URL so a tainted run can fetch them. — [tools.py `web_search`](backend/personal_os/tools.py)
- Provider order: if `braveApiKey`, Brave alone; else if `tavilyApiKey`, Tavily alone; else concurrent SearXNG (only when `searxngUrl` is http(s)) and keyless Exa, merged with `rrf_merge` when both return rows; if both fail or are empty, DuckDuckGo. Exa-only with a time range sets `time_range_ignored`. SearXNG week is rewritten and noted as month. `apply_site` appends `site:<domain>`. — [websearch.py `search`](backend/personal_os/websearch.py)
- `fetch_url` checks the URL, follows up to 5 redirects, and only then reads `WebCache` (TTL `fetchCacheSeconds`, default 3600; `fresh` bypasses). Bodies over 2 MB are not cached; rows older than 24 hours and beyond 200 are deleted. `classify` routes pdf, json, html, text, or binary. `render` uses trafilatura for HTML and can number links (`LINK_CAP` 40). PDF goes through `extract_text`. Binary raises `Unreadable`. Optional `focus` runs `bm25_focus` before `page_window`. `max_chars` is clamped to 1,000–40,000 (default 12,000). Link URLs are not added to the allowlist. HTML that is 401/403/429/503 or under 300 characters can fall back to `reach.jina_read` when `readerFallback` is true, and that fallback replaces the text if it is longer. — [tools.py `fetch_url`](backend/personal_os/tools.py); [webread.py](backend/personal_os/webread.py)
- Also registered in the same reach area: `youtube_video`, `youtube_search`, `github_search`, `github_read`. — [tools.py](backend/personal_os/tools.py)
- `run_python`: temp dir `pos-sandbox-`, `python -I`, deleted in a `finally`. On macOS, if `sandbox-exec` exists, the profile denies network, allowlists interpreter, site-packages, the work dir, and a short list of system paths, denies the app data dir, `.env`, ssh/aws/gnupg/kube, keychains, and browser profiles, and denies Apple events. Writes stay in the temp dir unless a cowork desk `workspace` is passed, in which case that folder is also readable and writable. A tool-bridge Unix socket is the only extra network path, and only when `tools` is set. rlimits: CPU 20s, address space and data 1.5 GB, nproc 64, file size 512 MB, core 0, each applied separately. The file states macOS refuses `RLIMIT_AS` and `RLIMIT_DATA`, so the wall clock is the memory bound there. Default timeout 30s. Stdout kept to the last 20,000 characters; output above 4 MB kills the run. — [sandbox.py](backend/personal_os/sandbox.py); [tools.py `run_python`](backend/personal_os/tools.py)
- Figures: if the source mentions matplotlib, pyplot, or seaborn, the parent warms a font cache outside the sandbox and copies `fontlist-*` into the run. `MPLBACKEND=Agg`. Images written as png, jpg, jpeg, gif, webp, or svg are returned as data URIs, at most 6 images and 3 MB each. — [sandbox.py `_warm_mpl`, `_collect_images`, `run_python`](backend/personal_os/sandbox.py)
- Packages: one venv at `<data_dir>/envs/work`. Base set is pandas, openpyxl, xlsxwriter, python-docx, python-pptx, pypdf, pdfplumber, reportlab, matplotlib, pillow. `python_install` validates at most 10 plain PyPI requirements (no URLs, paths, or options) and installs wheels only (`--only-binary=:all:`). Danger is `external` (asks). `run_python` uses that interpreter once the env is ready, otherwise the app interpreter. The tool text says numpy and matplotlib are installed; numpy is not in `BASE_PACKAGES`. — [envs.py](backend/personal_os/envs.py); [tools.py `run_python` spec](backend/personal_os/tools.py)
- MicroVM: one container per conversation, name prefix `pos-sbx-`, image default `python:3.12-slim`, runtime default docker. Create flags include `--network none` unless `sandboxNetwork`, `--cap-drop` path described in the module docstring, `--memory 1g`, `--cpus 2`, `--pids-limit 256`. At most 5 sandboxes (LRU). Idle stop is described in the README as five minutes with a one-minute reaper; this note confirmed `shutdown` and checkpoint code, not the idle-timer function body. `shutdown` runs `docker stop -t 3` on running labeled containers, not `rm`. `sandboxKeepDays` defaults to 14 for stale stopped containers. `sandbox_checkpoint` is `docker commit --pause=true`, at most 3 images per conversation. `sandbox_restore` deletes the container and recreates it from the image using the current network setting. `sandbox_reset` also removes checkpoint images. Tools: `sandbox_exec`, `sandbox_write_file`, `sandbox_read_file`, `sandbox_list_files`, `sandbox_put_document`, `sandbox_reset`, `sandbox_checkpoint`, `sandbox_restore`. They appear only when the runtime is reachable (README; tool registration checks `sb.available()` in the design comments and tests). — [microvm.py](backend/personal_os/microvm.py); [tools.py sandbox tools](backend/personal_os/tools.py); [README.md MicroVM section](README.md)

### Inferences
- Two sandboxes, different jobs. `run_python` does not keep files or a kernel between calls. The container does, including pip/apt when `sandboxNetwork` is on.
- Jina is a fallback reader, not the default fetch path. SearXNG is off until `searxngUrl` is set. With a Brave or Tavily key, keyless engines are not merged.

### Gaps
- `reach.py` line counts and YouTube/GitHub internals were not re-read. Only the tool registrations and the Jina/Exa call sites above were confirmed.
- Whether numpy is installed because it is a dependency of matplotlib, or missing until `python_install`, was not checked against a lockfile or the venv marker.
- Host `shell_run` seatbelt (`shell_profile` in `sandbox.py`) was not inventoried beyond noting it exists and is a different profile from `run_python`.
- `open_page` / hidden browser text extraction was not re-read. `sota-reach.md` describes it; this note does not confirm the current function.

## What desktop reach exists?

### Takeaway
Shipped desktop reach in this scope is a first-run setup wizard, a menu-bar tray for gathering canvas pop-outs, one global shortcut for that gather, in-app New Chat, and OS notifications for unattended jobs (and, separately, for cowork desks). A global quick-capture window is absent.

### Cited Findings
- Onboarding opens when `GET /setup/status` says `needsOnboarding`. That is false once `onboardedAt` is set, or for an existing install that already has an API key, or that has no `provider` and is inferred as the LiteLLM proxy. Steps are `welcome`, `provider`, `key`, `test`, `google`, `done`. Completing setup writes `provider`, `baseUrl`, `apiKey`, `defaultModel`, clears `extractionModel`, and sets `onboardedAt`. Settings can rerun the wizard. After setup, the empty chat offers three fixed first prompts. — [setup.py `status_of`, `complete`](backend/personal_os/setup.py); [steps.ts](src/renderer/src/components/onboarding/steps.ts); [onboardingStore.ts](src/renderer/src/components/onboarding/onboardingStore.ts); [App.tsx](src/renderer/src/App.tsx)
- Provider presets, all described as OpenAI-compatible: Fireworks, OpenAI (`https://api.openai.com/v1`, API key), Anthropic, OpenRouter, Ollama (`http://localhost:11434/v1`, no key, default model `llama3.2`), LiteLLM proxy, and custom. — [providers.py](backend/personal_os/providers.py)
- The only `globalShortcut` registration found under `src/main` is the gather shortcut, default `Control+Alt+Command+Space`, rebindable. Failure (taken or invalid) is reported to the renderer. — [shortcuts.ts](src/main/shortcuts.ts)
- The tray menu is Gather Widgets, Scatter, Bring Pop-outs to Front, Transparency, Pin all on top, Open Grain, and Quit. It does not create a note or a task. — [tray.ts](src/main/tray.ts)
- The File menu has New Chat (`CmdOrCtrl+N`) and Upload Document. Those accelerators are app-menu shortcuts, not global hotkeys. — [index.ts menu](src/main/index.ts)
- Job notifications: the scheduler publishes `job_finished`; `JobNotifier` calls `GET /inbox/notify?since=` only while the window is hidden and `notifyJobs` is not false. The route returns at most the events `notify_events` builds: failed run (when it will not retry), finished with proposals, paused after consecutive failures, and pending proposal. Bodies are names and counts, not reply text. The renderer uses the browser `Notification` API for these. — [app.py `inbox_notify`](backend/personal_os/app.py); [job_history.py `notify_events`](backend/personal_os/job_history.py); [App.tsx `JobNotifier`](src/renderer/src/App.tsx)
- Desk notifications are a second path: statuses `awaiting_plan`, `needs_approval`, `blocked`, `interrupted`, `review`, `done`, `failed`, sent to the main process, shown only when the main window is not focused, click focuses and opens `desk:<id>`. Setting `deskNotify` defaults on. This is cowork UI, noted only because it is the other OS notification. — [deskNotify.ts renderer](src/renderer/src/lib/deskNotify.ts); [deskNotify.ts main](src/main/deskNotify.ts)
- Roadmap item L5 is "Quick capture: global hotkey window, natural-language parse into task/note/event, local voice." No `quickEntry` or quick-capture window was found in `src/`. — [docs/research.md](docs/research.md)

### Inferences
- "Quick entry" as a global capture popup is absent. The nearest shipped entry points are New Chat inside the app and the gather shortcut for canvas pop-outs.
- Ollama in onboarding is a chat endpoint preset. It is not a second, fail-closed model used only for sensitive jobs.

### Gaps
- Notification permission prompts and click behavior for job notifications (they construct `new Notification` in the renderer, unlike desk notifications which go through Electron `Notification` in the main process) were not exercised.
- Menu items beyond File and the start of View were not listed.

## Are ChatGPT sign-in, private on-device inference, and the cloud worker absent?

### Takeaway
All three README items marked planned are absent as specified. What exists instead is an OpenAI API-key provider, an optional Ollama base URL for the whole app, and a local job scheduler that runs only while this process is up.

### Cited Findings
- README marks three items *(Planned — not shipped yet.)*: Sign in with ChatGPT (browser OAuth, token in the local database, calls billed to that OpenAI account, LiteLLM key remains fallback); Private inference (sensitive jobs go to a local Ollama, llama.cpp, or MLX model and fail closed if it is down; `extractionModel` is "just a cheaper LiteLLM name"); Cloud worker (a second process on a VPS for scheduled tasks, morning brief, and watches, with its own database, while chat and the sandbox stay on the Mac). — [README.md](README.md)
- Backend Python has no `chatgpt.com`, `auth.openai`, ChatGPT sign-in, `cloud_worker`, `llama.cpp`, or `mlx` matches. The OpenAI preset is `https://api.openai.com/v1` plus `needsKey: True` and a platform API-key URL. — [providers.py](backend/personal_os/providers.py); search of `backend/**/*.py`
- `extractionModel` defaults to `""` in settings. Setup completion sets it to `""` so background calls use `defaultModel`. Call sites (`_run_widget` summaries, recap, learn, meetings enhance, activity summaries, compaction) use `extractionModel or defaultModel` through the same `llm.complete` path. Nothing in those call sites checks that the model is local or refuses a remote URL. — [llm.py](backend/personal_os/llm.py); [setup.py `complete`](backend/personal_os/setup.py); [app.py](backend/personal_os/app.py)
- Jobs start in-process: `@app.on_event("startup")` seeds jobs and `asyncio.create_task(scheduler.loop())`. There is no second worker process in that startup path. — [app.py `_jobs_startup`](backend/personal_os/app.py)
- Audio on-device transcription is a different feature (meetings / activity). The README itself separates it from the planned text air-gap. It was not re-audited here.

### Inferences
- Choosing Ollama in the wizard makes chat and background jobs share that local endpoint, because `extractionModel` is cleared. That is not the planned split (cloud chat, local sensitive jobs, fail closed).
- A user can point Custom or LiteLLM at a local server. That is still one OpenAI-compatible base URL, not an on-device guarantee and not a cloud worker.

### Gaps
- `litellm.yaml` was not opened, so this note does not list which remote models the default proxy config names.
- No search of Electron packaging for a bundled MLX or llama.cpp binary.

## Where do the README and the two research notes disagree with the code?

### Takeaway
`docs/research/sota-canvas.md` and `docs/research/sota-reach.md` describe an older tree. Artifacts, declarative dashboard widgets, `webread`/`websearch`, and checkpointed MicroVMs are in the code those notes say is missing. The README's dashboard blurb and its short Sandbox section lag the code; its MicroVM section and its three "planned" items match.

### Cited Findings
- `sota-canvas.md` "Where we are" says artifacts have no `Artifacts(` instance, no routes, no tool, no widget kind, and no panel, and that the README feature is therefore not shipped. The working tree has `Artifacts(db)`, `artifact_routes`, `artifact_tools`, `WidgetKind` `artifact`, `widgets/artifact.tsx`, and preset `REF_TABLES['artifact']`. — [sota-canvas.md](docs/research/sota-canvas.md); citations in the canvas section above
- The same note's build spec names tools `create_artifact`, `edit_artifact`, and `rewrite_artifact`. Shipped names are `artifact_create`, `artifact_edit`, and `artifact_update`. — [sota-canvas.md artifacts-1](docs/research/sota-canvas.md); [artifact_tools.py](backend/personal_os/artifact_tools.py)
- The same note says every dashboard visual is bespoke model JS, `GET /widgets/{id}` does not exist, and `refresh_minutes` cannot refresh a chart without code that fetches itself. The working tree has kinds `chart|stat|table`, `GET /widgets/{wid}` and `GET /widgets/{wid}/data`, and TTL re-bind with no model call. The 1,800-character HTML sample, the lack of an HTML lint/repair loop, the 3-column width, and the lack of an agent canvas tool are still true. — [sota-canvas.md](docs/research/sota-canvas.md); [widget_spec.py](backend/personal_os/widget_spec.py); [app.py](backend/personal_os/app.py)
- The same note says canvas kinds are 14 and omits `artifact`. Code has 15, including `artifact`. Its description of canvas tables, snap modes, and presets-as-refs is still accurate. — [sota-canvas.md](docs/research/sota-canvas.md); [canvas.py](backend/personal_os/canvas.py)
- The same note says chart specs live only in message text, with no transforms, no time axis, and no brush. Message fences are still that limited. Transforms exist on dashboard specs. `ChartBlock` still has no brush import and no time-axis type. — [sota-canvas.md](docs/research/sota-canvas.md); [ChartBlock.tsx](src/renderer/src/components/ChartBlock.tsx); [widget_spec.py](backend/personal_os/widget_spec.py)
- `sota-reach.md` says `fetch_url` always reads `r.text`, drops links, has no offset, no focus, and no cache, and that search is exactly one of Brave, Tavily, Exa, or DuckDuckGo with no merge and no SearXNG. The working tree has `webread.py` and `websearch.py` wired from `fetch_url` and `web_search` as described in the web section. — [sota-reach.md](docs/research/sota-reach.md); [webread.py](backend/personal_os/webread.py); [websearch.py](backend/personal_os/websearch.py)
- The same note says `Sandboxes.shutdown()` does `rm -f` and that there is no checkpoint. `shutdown` stops containers; `checkpoint` and `restore` exist; reset removes checkpoint images. — [sota-reach.md](docs/research/sota-reach.md); [microvm.py `shutdown`, `checkpoint`, `restore`](backend/personal_os/microvm.py)
- The same note's claim that `run_python` is stateless (no kernel, no pip inside that tool) still matches `sandbox.py`. Package install is `python_install` into the shared venv, not persistence of the temp dir. — [sandbox.py](backend/personal_os/sandbox.py)
- README "Dashboards you describe" mentions HTTP, RSS, internal sources, sandboxed HTML, AI summaries, and revise. It does not mention chart, stat, table, specs, or TTL refresh. — [README.md](README.md)
- README MicroVM section matches the code that was read: stop on shutdown, `sandboxKeepDays` default 14, checkpoint/restore, 3 checkpoints, reset drops them, 1g/2 cpu/256 pids, max 5, network off by default. — [README.md](README.md); [microvm.py](backend/personal_os/microvm.py)
- README "Sandbox" (the short section after meetings) says `run_python` has CPU, memory, and wall-clock limits and denies reads of local files. The implementation comments say macOS will not apply the address-space rlimit, and the profile allowlists the interpreter, site-packages, and selected system directories rather than denying all local reads. The richer `run_python` description earlier in the README (charts section and MicroVM intro) is closer. — [README.md Sandbox](README.md); [sandbox.py module docstring and `_limits`](backend/personal_os/sandbox.py)
- README interactive-block and mermaid paragraphs match `InteractiveBlock.tsx` and `MermaidBlock.tsx` on controls, caps described in prose, expr-not-eval, and lazy mermaid. The README does not state the numeric caps (12/8/6/400/500); those are in the component. — [README.md](README.md); [InteractiveBlock.tsx](src/renderer/src/components/InteractiveBlock.tsx)
- README planned ChatGPT sign-in, private inference, and cloud worker match the absence check above. The sentence that `extractionModel` is not an on-device guarantee still matches the code.

### Inferences
- A gap comparison that treats `sota-canvas.md` and `sota-reach.md` "Where we are" as current will double-count work that is already in the tree (artifacts-1/2, dashboards-1, web-1, web-2, sbx-1).
- Remaining gaps those notes name that this pass still sees in code: no agent tool to open or arrange a space; presets carry refs not content; HTML widgets have no validate-and-repair; chat chart fences have no brush, time axis, or repair loop; `run_python` has no persistent kernel; no site map/crawl tool was found next to `web_search`.

### Gaps
- `sota-reach.md` claims about `open_page` (flat innerText, no selector wait) were not re-checked in `mac.py` or `pagefetch.ts`.
- README layout blurb omits many modules. That is a doc index gap, not a behavior gap, and it was not line-audited.
