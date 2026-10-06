# Streamline audit (report only)

Date: 2026-10-05. Base: `origin/main` at 607a6bc7. Branch: `worktree-streamline-audit`. Nothing in this branch changes code; this file is the only addition.

Method: knip, ts-prune and depcheck over `src/`, vulture (60% confidence) over `backend/personal_os`, then every candidate re-checked by word-boundary grep across `src/`, `backend/`, `tests/` and `scripts/`. Three analysts each took one area (TS and CSS, Python and routes, product duplication); the orchestrator spot-checked every item that appears below. No tests, builds or app launches were run. Line estimates are approximate (counted from the def or rule to its closing line).

Size baseline: backend 66.4K lines of Python (excluding tests), renderer and main 56.3K lines of TS/TSX (excluding tests), 6.3K lines of CSS, 55K lines of backend tests, 11.6K lines of e2e specs.

## Executive summary

The codebase is already lean at the symbol level: both dead-code tools came back nearly clean once route handlers, registries and pydantic fields were filtered out. The real weight is structural (a 9.8K-line `app.py` with one 1,741-line function, a 4.5K-line store, a 3.3K-line types file) and in repo hygiene (437 worktrees holding 62 GB, 755 merged local branches).

| Section | Items | Estimated lines removable | Other |
|---|---|---|---|
| Safe to delete | 9 | about 280 lines of code and CSS | 62 GB of worktrees, 755 local + 151 remote merged branches |
| Needs a decision | 18 | about 1,700 lines of product code, plus about 5,000 lines of docs, scripts and manual test files | 78 MB out of the bundled backend if matplotlib leaves the core deps |
| Refactor candidates | 11 | 0 (reorganisation) | `app.py` 9,792 to about 1,000; `store.ts` 4,533 to about 300 |

Top 5 needs-a-decision items:

1. **Sticky notes on spaces** (`notes.py`, `canvas/widgets/note.tsx`, 5 routes): a second document store that only the space canvas can reach, about 380 lines. The `doc` widget does the same job with history.
2. **Two plan-approval cards** (`PlanApproval` + `planSteps` vs `ActionPlanCard` + `planDigest`): about 250 lines, and the two canonicalisers sort differently, which matters because the digest binds an approval to its arguments.
3. **matplotlib as a core backend dependency**: no backend code imports it; only model-written `run_python` code does, and the shared work environment installs its own copy. About 78 MB of the 355 MB site-packages.
4. **22 unmerged branches with real unique commits** (`sota/*` ×11, `worktree-cowork-planning` 11.6K lines, `worktree-artifacts-canvas` 8.4K, `worktree-ui-ux-qa-fixes` 7.2K, `feat/kanban-drive-cleanup` 7.1K, `worktree-skills-connectors-library` 6.1K, and others): keep, salvage or drop.
5. **Two Google mirrors of the same todo list** (`gtasks.py` two-way Tasks sync, `todocal.py` one-way calendar mirror, plus a Home card for Tasks when sync is off): about 675 lines across two features that overlap in purpose.

Suggested order: section 1 items 1 to 6 in one small PR, then the hygiene sweep (item 1.9), then decisions, then the `types.ts` and `api.ts` barrels before anything touches `store.ts` or `app.py`.

---

## 1. Safe to delete (high confidence, no user-visible change)

### 1.1 Dead TypeScript symbols and one dead file (about 60 lines, risk low)

| Where | What | Lines | Evidence |
|---|---|---|---|
| `src/renderer/src/lib/id.ts` | whole file, `uid()` | 4 | Only `.ts` under `src/` unreachable from the three entry points. `grep -rn "lib/id'" src tests` = 0. `Composer.tsx:308` calls `crypto.randomUUID()` directly. |
| `src/shared/types.ts:318` and `:1839` | duplicate `export interface PlanEdit` | 4 | Declared twice; TypeScript merges them silently into `{idx; arguments?; drop?}`. Fold into one. A bug in waiting rather than a saving. |
| `src/renderer/src/store.ts:4485-4488` | `selectActive`, `useSession` | 4 | One occurrence each repo-wide. Comment says "what `active` used to be", leftover of the multi-session refactor. |
| `src/renderer/src/store.ts:72` | `KnowledgeTab` type | 1 | 0 importers. |
| `src/main/pagefetch.ts:300` | `pageBridgeUrl` | 3 | One occurrence (the declaration). `bridgeUrl` itself stays. |
| `src/main/popouts.ts:275` | `popoutOpacity` | 4 | One occurrence. |
| `src/renderer/src/canvas/snapping.ts:74` | `snapRectToGrid` | 1 | One occurrence; check `snapValue` still has a caller afterwards. |
| `src/renderer/src/lib/calendarOverlay.ts:47,444` | `CALENDAR_WRITE_TOOLS`, `AgendaItem` | 2 | One occurrence each. |
| `src/renderer/src/lib/toolResult.ts:160` | `BrowserView` type | 1 | One occurrence. |
| `src/renderer/src/components/toolcards/parts.tsx:194` | `Section` helper | 3 | 0 callers. The `tc-section` class is still used as a literal by `DeskCards.tsx` and `ViewImageCard.tsx`, so keep the CSS. |
| `src/renderer/src/features/docrec/index.ts:4-12` | re-exports `TranscriptView`, `useDocRecSync`, `dictationText`, `RecordingChip`, `REC_SCHEME`, `recordingIdFromHref` | 6 | The barrel's only importer is `DocsView.tsx:18`, which imports none of these. Other callers import the files directly. |
| `src/renderer/src/modules.ts:49`, `toolcards/index.ts:18-19` | re-exports `viewHidden`, `registerToolCard`, `ToolCardProps` | 0 | All consumers import from `./moduleToggles` and `./registry`. Drop the names. |
| 13 `canvas/widgets/*.tsx`, 11 `toolcards/*.tsx`, `StatusRing.tsx:53`, `useRingStatus.ts:36` | `export default` lines | about 27 | `canvas/registry.ts` imports the named `def`; cards register via `registerToolCard`. Nothing consumes the defaults, except `rerender.test.ts` and `frameMemo.test.ts`, which import `StatusRing` as default: keep that one or edit the two tests. |

How to verify: `npm run typecheck && npm test`; for each symbol `grep -rnw <symbol> src tests scripts` returns only the declaration before deletion.

Not dead despite tool hits (do not touch): `lib/followQueue.ts`, `lib/expr.ts` (its `RESERVED` is read by `scripts/test-expr.mjs`), `lib/chartRepair.ts`, all of `src/main/popouts.ts` except `popoutOpacity`, `viewHidden` (8 production consumers). About 120 further exports flagged by knip are used inside their own file; dropping `export` from them is cosmetic (0 lines) and better enforced with a knip config than by hand.

### 1.2 Dead settings-tab and module-shell ids (about 10 lines, risk low)

- `src/renderer/src/store.ts:71`: `SettingsTab` declares 15 ids; `SettingsModal.tsx` renders 9 (`provider, permissions, cowork, memory, behavior, modules, integrations, meetings, data`). Dead: `knowledge`, `usage`, `spaces`, `appearance`, `advanced`, `trash`. `SettingsModal.tsx:163` already falls back to `provider` for them; `grep -rn "openSettings('" src` shows no caller using a dead id.
- `src/renderer/src/shell/types.ts:24`: `ModuleNav.section` union members `'main' | 'knowledge'`; every module uses `'apps'`.
- `src/renderer/src/moduleToggles.ts:9`: `DEFAULT_HIDDEN_VIEWS` is an always-empty readonly array spread in three places (`moduleToggles.ts:14`, `SettingsModal.tsx:300,307`). Inline `[]`.
- `store.ts:67` comment says the modules tab is "labelled Views" and describes a `knowledge` tab; both stale.

How to verify: `npm run typecheck`.

### 1.3 Dead CSS (about 145 lines, risk low)

Method: every class selector across the 17 CSS files matched against all `.ts`, `.tsx` and `.html` under `src/`, with prefix matches counted as live for template-built names (`p${priority}`, `desk-ring-${status}`). 1,757 distinct classes, 53 unreferenced.

- `src/renderer/src/styles/cowork.css:8` to about `:190`: the old Cowork desk page. `.cowork-page*`, `.desk-rail`, `.desk-row*`, `.desk-new`, `.desk-field`, `.desk-head*`, `.desk-title-input`, `.desk-pill`, `.desk-actions`, `.desk-meter`, `.desk-brief`, `.desk-banners`, `.desk-banner-action`, `.desk-steer`, `.desk-card-head`, `.desk-settings`, `.desk-inputs`, `.desk-input-chip`, `.desk-timeline*`, `.desk-ev-*`. About 105 rule lines. Evidence: `grep -rlE 'desk-row|cowork-page|desk-new|desk-timeline|desk-rail' src --include='*.tsx'` = 0 files. Keep the live neighbours: `.desk-strip*`, `.desk-badge`, `.desk-inline`, `.desk-banner`, `.desk-cards` (line 145), `.desk-panel`, `.desk-plan*` (line 192 on), `.desk-ring-<status>` (lines 64-68, set by `DeskStrip.tsx:31`), `.autonomy*`, `.cowork-env-row`, `.cowork-error`, `.cowork-net`, `.cowork-num`, `.aplan-*`. The `.desk-ring` base rule at 58-63 is labelled "shared by the rail ring" and goes with the rail.
- `src/renderer/src/styles.css`, about 34 lines: `558-568` `.chat-empty`, `.chat-starters` (old new-chat screen, with its stale comment at 557); `1327-1328` `.add-inline`; `1654` `.add-row .list-input`; `1794` `.approval-args` (and `cowork.css:150` `.desk-card .approval-args`); `1967,1970` `.inbox-item-head`; `2070` `.row3`; `2795-2796` `.crew-face`; `2850-2864` `.skill-title`, `.skill-badge`, `.skill-edit*`, `.made-grid`, `.made-card`, `.made-head`, `.made-name` (leftover Skills review and "what has been made" UI); `2921` `.kcol .count.over-limit`; `2607` `.dw-width`.
- `src/renderer/src/styles/docs.css:174-180`: `.doc-folder-pick` (4 rules).

False positives to leave alone: `.recharts-*`, `.katex-display`, `.hljs-*`, `.lucide`, `.todo.p3`, `.kcard.p3`. No class prefix for the removed web widget, kanban page, dashboards or pages survives (`grep -n "\.web-\|\.dash-\|\.pages-\|\.page-editor"` over CSS = 0).

How to verify: after deleting, `grep -rnoE "desk-[a-z-]+" src/renderer/src --include='*.tsx' | sort -u` is a subset of what remains in `cowork.css`; eyeball the Cowork strip, Skills panel and a new chat in both themes.

### 1.4 Dead Python constants, functions and imports (about 50 lines, risk low)

All verified with `grep -rnw <name> backend src tests` = 1 hit (the definition).

| Where | What | Lines |
|---|---|---|
| `backend/personal_os/app.py:8876` | `class PlanDecisionIn` | 5 |
| `backend/personal_os/app.py:318` | `todo_calendar` name in the tuple unpack | 0 |
| `backend/personal_os/consolidate.py:76` | `jaccard()` (code uses `_jaccard_sets`) | 3 |
| `backend/personal_os/docs.py:769` | `Docs.forget_scope` (superseded by `trash.py:76`) | 7 |
| `backend/personal_os/plans.py:384` | `Plans.for_conversation` (app uses `for_desk`, `for_run`) | 8 |
| `backend/personal_os/plans.py:65` | `STEP_REJECTED` | 1 |
| `backend/personal_os/llm.py:302` | `STREAM_IDLE_S` (code uses `DEFAULT_IDLE_S` and `llmIdleSeconds`) | 3 |
| `backend/personal_os/subagents.py:83` | `WRITE_TOOLS` | 2 |
| `backend/personal_os/shell.py:72` | `SHELL_REDACT` | 2 |
| `backend/personal_os/skillbuild.py:44` | `MIN_DRAFT_STEPS` | 1 |
| `backend/personal_os/style_presets.py:4` | `STYLES` | 1 |
| `backend/personal_os/cowork.py:154` | `EVENT_KINDS` | 2 |
| `backend/personal_os/db.py:26` | `GOOGLE_TOKEN_SECRET_FIELDS` | 1 |
| `backend/personal_os/extundo.py:42` | `TOOLS = CALENDAR | TASKS` | 1 |
| `backend/personal_os/todos.py:100` | `DEFAULT_STATUSES` (frontend `TodoBoard.tsx` has its own list) | 1 |
| `activity.py:29`, `microvm.py:40`, `modules/planner.py:24` | unused imports `httpx`, `timezone`, `tool_error` | 3 |

Note: `pyproject.toml` ignores F401 and F841 in ruff, so unused imports and locals are never linted; an AST scan found only these three.

How to verify: `cd backend && uv run --with vulture vulture personal_os --min-confidence 60` shows them gone, then `uv run pytest -q`.

### 1.5 One route nothing calls (5 lines, risk low)

`GET /cowork/desks/{id}/events`, handler `desk_event_list` at `backend/personal_os/app.py:9373-9377`. Zero references in `src/`, `backend/tests`, `tests/e2e`. Desk detail already embeds `desks.events(id)` (`app.py:9230`). Verify: `grep -rn "desks/\${[^}]*}/events\|desk_event_list" src backend tests` = handler only.

### 1.6 Stale comments (about 8 lines, risk none)

`src/main/pagefetch.ts:5` ("or the web widget's"), `src/renderer/src/canvas/Canvas.tsx:54` and `components/ResizeHandle.tsx:62` (`<webview>`), `components/ActionPlanCard.tsx` header (claims it mounts in `ToolEvents`; it does not), `components/DocumentsView.tsx` header (says hosted by Settings; it is not, and its `embedded` prop is the only mode), `backend/personal_os/app.py:171-172` (IntegrityError docstring mentions cards, columns and dashboards), `store.ts:67` (see 1.2).

### 1.7 Dependency manifest fixes (0 lines, risk low)

depcheck and knip found no unused npm package; every Python dependency has an importer or is an implicit runtime dependency (`python-multipart` for `UploadFile`/`Form` at `app.py:6249,6635,7322`; `google-auth-httplib2` for `googleapiclient.build`; `cactus-needle` imports as `needle`). Fixes are declarations only:

- Add `highlight.js` (CSS import at `main.tsx:14`, transitive via rehype-highlight), `unified` (type-only at `MarkdownPreview.tsx:4`), `remark-parse` (test only) and `esbuild` (the `npm test` script's binary, transitive via vite) so a transitive version bump cannot break them.
- Move `@types/d3-force` from `dependencies` to `devDependencies`.
- `@playwright/test` is undeclared on purpose (`tests/e2e/README.md:8` symlinks an external install).
- `pyobjc-framework-CoreAudio` has no `import CoreAudio`; `native_audio.py` loads the framework through ctypes and `objc.lookUpClass("CATapDescription")`. Leave it unless the tap path is tested without it.

### 1.8 Test harness hygiene (risk none)

`src/renderer/src/lib/trashLabels.test.ts` exists but is missing from the hand-maintained `npm test` list in `package.json`, so it never runs. Every other listed file exists. Add it, or replace the 134-entry list with a glob so new tests cannot be silently skipped.

### 1.9 Git worktrees and branches (62 GB, risk low; report only, nothing was deleted)

Inventory from `git worktree list`, `git branch --merged origin/main`, `git cherry origin/main <branch>`:

- **437 worktrees.** 391 under `Desktop/Personal OS/.claude/worktrees`, 23 under `~/.claude/jobs/a16ce26e/tmp/wt`, 20 under `~/.claude/jobs/e835abad/tmp/wt`, 2 under `~/.claude/jobs/0e923301/tmp`, 1 under `~/.claude/jobs/6095334e/tmp/wt/qa`. 411 sit on a branch already merged into `origin/main`, 22 on unmerged branches, 4 detached.
- **Disk:** 62 GB in `.claude/worktrees` (315 `node_modules` copies, 273 `backend/.venv` copies), 2.7 GB in `e835abad`, 1.4 GB in `a16ce26e`. Largest: `integrate-2026-10-04b` 3.1 GB, `harness-parity` 2.8 GB, `worktree-merge-all-2026-10-04` 2.8 GB, `integrate-2026-10-05` 2.0 GB, `integrate-2026-10-03` 1.9 GB.
- **Locked:** `agent-a34b58233731b1ce7`, `agent-a5533207d87e98529`, `agent-a57477faf06146a75` (need `git worktree unlock` first).
- **Local branches:** 782; 755 merged (`worktree-*` 593, `parity/*` 45, `pkg/*` 41, `cowork/*` 9, `oh/*` 8, `cx/*` 8, `verify/*` 6, `wip/*` 4, `ui/*` 4, `agentic/*` 4, `integrate/*` 3, plus singles).
- **Remote branches:** 187; 151 merged (`pkg/*` 22, `integrate/*` 3, the rest `worktree-*`); 8 `backup/2026-10-05/*` snapshots; `copilot/fix-backend-github-actions-job` backs open draft PR #7.
- **Unmerged by ancestry but superseded by patch id** (every commit shows `-` in `git cherry`): `worktree-qa-merge-2026-10-05`, `worktree-wf_a59ac431-db6-{32,36,38,46}`, and `preview-page-agent-docs-tree` (0 own commits). Safe to delete along with their worktrees.
- The remaining unmerged branches carry real commits; see section 2.14.

How to verify before any cleanup: `git worktree list --porcelain | grep -c '^worktree'`, then for a branch `git cherry origin/main <branch>` shows only `-` lines. Suggested commands, for Nate to run after review: `git worktree remove <path>` per merged worktree (or `git worktree prune` after moving the directories), `git branch --merged origin/main | grep -v main | xargs git branch -d`, `git push origin --delete` for the merged remote `pkg/*` and `worktree-*` branches.

---

## 2. Needs a decision (features or settings Nate may want to cut)

### 2.1 Sticky notes on spaces (about 380 lines, risk medium)

- Where: `backend/personal_os/notes.py` (66), `src/renderer/src/canvas/widgets/note.tsx` (199), 5 routes at `app.py:7006-7047` (about 42), `presets.py` note snapshot/instantiate (about 25), `canvas/AddWidgetMenu.tsx` note rows (about 25), api/types/`WidgetKind` glue (about 20).
- Evidence: `api.notes` is referenced only by `note.tsx` and `AddWidgetMenu.tsx`. The `notes` table is not in Trash, search, retrieval or any agent tool. `docs.py` itself describes notes as "a body, a colour, no history". The `doc` widget already puts a file in a space window, with revisions.
- User would lose: coloured sticky notes on a space and any existing note rows (a one-time migration into docs would preserve the text). Presets that embed note bodies (`test_presets.py`, 272 lines) would need updating; `canvas.py:97` already drops windows of unknown kind at startup.
- Verify: `grep -rn "api.notes\|/notes" src backend/personal_os/app.py`; run `backend/tests/test_presets.py` and `tests/e2e/spaces-widgets.spec.mjs` after.

### 2.2 Two plan-approval cards with two canonicalisers (about 250 lines, risk medium)

- Where: `components/PlanApproval.tsx` (103) + `lib/planSteps.ts` (92) + test (77), mounted from `ToolEvents.tsx:343` for a pending `propose_plan` event; `components/ActionPlanCard.tsx` (269) + `lib/planDigest.ts` (167) + test (136), mounted from `DeskPlan.tsx:45` on a `PlanRecord`.
- Evidence: both libraries define `canon`, `edited`, `editPayload`, `argRows`, `invalid`. `planDigest.canon` sorts keys by code point and drops `undefined`, matching Python `sorted()` in `plans.py` `args_digest`; `planSteps.canon` uses plain `.sort()` (UTF-16 order). Because the digest binds an approval to the exact arguments, two canonicalisers are a latent mismatch, not just duplication.
- User would lose: nothing if `planDigest` and one card are kept; the chat-inline card and the desk card can share one component with two layouts.
- Verify: `npm test` (both plan tests), `tests/e2e/approvals-plan-mode.spec.mjs`, `backend/tests/test_propose_plan*.py`.

### 2.3 matplotlib out of the core backend dependencies (0 lines, 78 MB, risk medium)

- Where: `backend/pyproject.toml` core deps; `scripts/bundle-backend.sh:59-60` trims its sample data.
- Evidence: no backend module imports it (`grep -rn "import matplotlib" backend/personal_os` hits only an example string in `tools.py:1392`, a font-cache warm in `sandbox.py:50` and `guides/charts.md`). Model-written `run_python` code imports it, and `envs.BASE_PACKAGES` (`envs.py:29-30`) already installs matplotlib, pillow and numpy into the shared work environment that `run_python` prefers once ready (`tools.py:1352-1353`). In the main checkout's venv, matplotlib + numpy + PIL + fontTools + kiwisolver + contourpy = 78 MB of 355 MB site-packages.
- User would lose: a chart drawn by `run_python` before the work environment has finished its first install would fail until `python_install` runs. `backend/tests/test_shell.py:874-878` and `e2e_cowork_tools.py` assume it is present.
- Options: move to an extra (`charts = ["matplotlib"]`) and let the bundle script decide, or drop it and rely on the work env.
- Verify: `du -sh backend/.venv/lib/python3*/site-packages/{matplotlib,numpy,PIL}`; run `test_shell.py` after a trial removal.

### 2.4 `planner.py` reimplements `scheduling.py` (about 100 lines, risk medium)

- Where: `backend/personal_os/planner.py` (230, imported by `modules/planner.py`) vs `scheduling.py` (432, imported by `tools.py`).
- Evidence: both define `busy_from_events`, free-window subtraction (`planner._subtract`/`free_windows` vs `scheduling.subtract`/`working_windows`), working-hours parsing (`planner._hm`/`validate_config` vs `scheduling.parse_working_hours`) and slot scoring (`planner.score_slot` vs `scheduling._score`). `grep -c scheduling backend/personal_os/planner.py` = 0.
- Catch: `planner.py` is deliberately tz-naive and deterministic with an injected `now`; `scheduling.py` is tz-aware. Rebuilding the planner on `scheduling.Interval` needs that contract confirmed. Tests: `test_planner.py` (257), `test_planner_taint.py` (109).

### 2.5 Production code kept alive only by its tests (about 130 lines plus tests, risk low)

TypeScript, each symbol appears in exactly two files (its module and its test): `notes/tags.ts:6-27` `extractTags` (22), `main/navPolicy.ts:32-46` `targetsLoopbackService` (15, security policy: confirm the guard was not meant to call it), `lib/drafts.ts:303-316` `resetDrafts` (14, a legitimate test helper), `lib/calendarOverlay.ts:406-417` `hourBand` (12), `notes/slash.ts:110-119` `composerSlash` (10), `lib/compact.ts:3-10` `compactCommand` (8), `lib/toolResult.ts:68-73` `tailLines` (6), `notes/stats.ts:3-8` `readingTime` (6), `lib/cron.ts:4-8` `buildCron` (5), `lib/htmlFence.ts:69-73` `isSafeSandbox` (5), `docrec/recordingBlock.ts:26-29` `parseRecordingBlocks` (4), `lib/planDigest.ts:73-76` `sameArgs` (4), `notes/tasks.ts:36-38` `mapTaskLine` (3), `lib/defText.ts:3` `AGENT_SKELETON` (1).

Python: `app.py:835` `HOST_LIST_SETTINGS` (only `test_settings_lists.py`), `canvas.py:263` `Canvases.reset_popped` (only `test_canvas.py`; docstring says "called at startup" but `app.py:6813` deliberately does not), `tools.py:3607` `REACH_TOOLS`, `commands.py:89` `BUILTIN`, `autoreview.py:16` `LEVELS`, `deliver.py:37` `GUIDE_MAX_CHARS`, `redact.py:62` `REDACTIONS` (re-exported through `activity.py:38` with a noqa for tests).

Decision: delete symbol and test together, or keep as intended API. Verify: `grep -rlw <symbol> src backend tests` = 2 files.

### 2.6 Routes referenced only by tests (about 45 lines, risk medium)

| Route | Handler | Lines | Referenced from |
|---|---|---|---|
| `GET /runs/{run_id}/children` | `app.py:3994-4004` `run_children` | 11 | `test_subagents.py:1128`, `e2e_subagents.py`, `tests/e2e/library-agents.spec.mjs:115`. The UI reads subagent trees via `/runs/{id}/events` and `crew_view`. |
| `POST /messages/{mid}/stop` | `app.py:5739-5748` `stop_message` | 10 | `test_runs.py:264` ("the per-message stop route still responds"); UI uses `/conversations/{id}/stop`. Looks like deliberate compatibility. |
| `GET /cowork/desks/{id}/outputs` | `app.py:9561-9565` | 5 | `test_cowork.py` ×5. |
| `GET /file-snapshots` | `filesnap.py:287-290` | 4 | `test_filesnap.py:255`; UI only calls `/file-snapshots/{id}/restore`. |
| `POST /maintenance/sweep` | `reliability.py:113-117` | 5 | `test_reliability.py`; `retention.run_now()` would then be test-only. |
| `PUT /health/entries/{id}` | `modules/health.py:150-156` | 7 | `test_health.py:149`; `api.ts:300-302` has list, log, delete only. |

Also `GET /canvas-presets/{pid}` (`app.py:8321`) has no frontend caller while PUT, DELETE, export and instantiate on the same path do. Verify with `grep -rn "<path>" src tests backend/tests`.

### 2.7 `open_page` vs the `browser_*` tools (about 100-150 lines, risk medium)

`open_page` (`tools.py:2973`, `mac.py:547`, loader in `src/main/pagefetch.ts`) is a read-only, JS-rendered offscreen load in the agent session; `browser_*` (`browser.py` 450, `src/main/agentBrowser.ts` 1072) can do the same and more. `tools.py:202` already falls back from `open_page` to `fetch_url`. `pagefetch.ts` also hosts the guarded session that `agentBrowser.ts:85` consumes, so only the loader and the tool could go. Catch: `browserEnabled` is user-togglable and `open_page` is not gated by it, so a user with the browser off would lose rendered-page reads. User would lose: one model-visible tool.

### 2.8 ffmpeg capture fallback for meetings (about 150-250 lines across 5 files, risk high)

`native_audio.py` (AVAudioEngine mic and Core Audio process tap, macOS 14.2+) replaced the ffmpeg avfoundation plus loopback-driver capture, which `native_audio.py` says is "still the fallback when this module cannot start". ffmpeg references: `meeting_recorder.py` 30, `audiocap.py` 22, `meetings.py` 20; loopback mentions: `meetings.py` 15, `audiocap.py` 5, `MeetingSettings.tsx` 4. ffmpeg itself must stay (decoding imports in `meeting_import.py`, screen frames in `teach.py`); only the capture-device fallback is a candidate. Decision hinges on the minimum supported macOS and on what happens when the tap permission is denied.

### 2.9 Two Google mirrors of the todo list (about 675 lines if one goes, risk medium)

`gtasks.py` (337, two-way Google Tasks sync, Settings toggle `googleTasksSync.enabled`, default on) and `todocal.py` (337, one-way calendar mirror, toggle `mirrorEnabled`), plus a Home card "Google Tasks (when sync is off)" and three `google_tasks_*` agent tools (`tools.py:2112-2127`, about 15 lines) whose `available()` is False whenever Tasks sync is on, so with defaults the model never sees them. User would lose: whichever mirror is cut (tasks in the Google Tasks app, or todos as calendar blocks). Not dead code; a product overlap.

### 2.10 Standalone Meetings view vs doc-attached recordings (about 150-250 lines, risk high)

`MeetingsView.tsx` (544) + `MeetingRecorderBar.tsx` (113) inline their own transcript and summary; `features/docrec/*` (2,159) has `TranscriptView`, `SummaryView`, `RecordingsPanel`, `DocRecorderBar` for the same `meeting_*` rows and share `lib/transcript.ts`. Two reviewable-revision systems exist on the backend (`meeting_revisions` and `doc_revisions`). Reusing docrec's components inside `MeetingsView` saves the estimate; removing the Meetings rail entirely is a product decision (`docs/meetings.md`).

### 2.11 One-shot migrations that can retire (about 50 lines, risk low to medium)

- Legacy `Settings.mode` (`store.ts:38-44` `withoutLegacyMode`, `store.ts:2014-2020`, `src/shared/types.ts:1340-1341`, `llm.py:111` `"mode": "classic"`): reads `mode==='canvas'` once, opens the canvas, resets to `classic`. About 20 lines. A user who last quit in the old canvas mode lands on Today once.
- Boards-into-todos migration (`todos.py:127-156` `import_boards`, `migrations.py:39-42` step `(3, "boards_into_todos")`, `gtasks.py:245,266` `source=='board'`): keep the step number, reduce it to a no-op once no install is below `PRAGMA user_version` 3. About 25 lines.
- `canvas.py` `_RENAMED_DEFAULTS` ("Desk 1" to "Space 1") runs an UPDATE on every start. About 6 lines.
- `will-attach-webview` guards in `navigation.ts:33-34`, `agentBrowser.ts:231`, `pagefetch.ts:151` while `webviewTag: false` is set. About 6 lines; harmless defence either way.

### 2.12 Two uploads lists (about 40 lines, risk low)

`components/DocumentsView.tsx` (74) and `canvas/widgets/documents.tsx` (84, own `Row`) both render `useStore.documents` with upload and delete. Both are reachable (Files view and Project "Uploads" tab; space window). One list component with a `compact` flag covers both.

### 2.13 Docs, scripts and manual test files (about 5,000 lines of non-product text, risk low)

- `docs/ux-audit-2026-10-04.md` (93): orphan, nothing links to it, point-in-time synthesis. Delete or move under `docs/research/`.
- `docs/research/notes-docs-codemap.md` (305) and `notes-recording-codemap.md` (562): build-time codemaps pinned to a worktree HEAD, stale by construction. `notes-ai-notepad-ux.md` (141) and `notes-editor-dictation.md` (196) duplicate `docs/docs-editor.md`. The `track-*.md` roadmap inputs (largest: `track-c-environment.md` 1,698, `track-b-tools.md` 957, `track-d-safety.md` 916) are partly superseded by `roadmap.md` and `sota-*`. `docs/research/` is 41 files, 10,053 lines. Archive or keep as history.
- `docs/workflows.md` and `docs/microsoft.md` are live but unlisted in the README file tree (lines 800-804 list 5 docs). `docs/cowork-design.md` is 1,675 lines and reads as implementation history.
- `scripts/verify-agent-browser.mjs` (588): manual live check, zero references anywhere, not in docs. Document or delete.
- `scripts/test-expr.mjs` (182, `npm run test:expr`): its comment "there is no frontend test runner" is no longer true. Fold into `lib/expr.test.ts` and the `npm test` list.
- `backend/tests/e2e_*.py` (7 files, 1,339 lines): not `test_`-prefixed, so pytest never collects them; referenced only by each other. Keep with a README pointer, or delete.
- `tests/e2e/backend_fake_google.py` (605) and `backend/tests/fake_google_api.py` (284): two fake-Google implementations (transport-level for e2e, client-level for pytest) with overlapping seed data. Unifying saves about 150-250 lines; medium risk since both feed many tests.
- `backend/personal_os/tests/` (12 files, 2,799 lines) ships inside the package next to `backend/tests/` (251 files). `bundle-backend.sh:55` prunes any `tests` dir under site-packages, so it does not ship, but two of its files collide by name with the outer dir (`test_mcp_oauth.py` 5 vs 1 tests, `test_mcp_servers.py` 44 vs 37) and likely overlap. Move inner to outer and dedupe.

### 2.14 Unmerged branches with unique commits (0 lines in the repo, risk: lost work if dropped)

From `git cherry origin/main <branch>` (`+` = patch not in main) and `git diff --shortstat origin/main...<branch>`:

| Branch | Unique commits | Branch diff | Last commit | Worktree |
|---|---|---|---|---|
| `worktree-cowork-planning` | 1 | 50 files, +11,602 | 2026-10-01 | none |
| `worktree-artifacts-canvas` | 2 | 35 files, +8,394 | 2026-09-29 | none |
| `worktree-ui-ux-qa-fixes` | 2 | 41 files, +7,167 | 2026-09-29 | none |
| `feat/kanban-drive-cleanup` | 1 ("Remove Dashboards, rename Boards to Kanban, fix Google errors, add Drive suite") | 36 files, +7,073 | 2026-09-30 | `.claude/worktrees/remove-dashboards-kanban-drive` |
| `worktree-skills-connectors-library` | 1 | 25 files, +6,148 | 2026-09-30 | `.claude/worktrees/skills-connectors-library` |
| `sota/pim` | 6 | 38 files, +3,605 | 2026-10-01 | `.claude/worktrees/wf_3ee5731e-2bf-9` |
| `worktree-microsoft-cut1` | 2 | 20 files, +2,606 | 2026-10-05 | `.claude/worktrees/microsoft-cut1` |
| `worktree-reasoning-traces` | 4 | 31 files, +2,139 | 2026-09-29 | `.claude/worktrees/reasoning-traces` |
| `sota/{activity,agentloop,canvas,context,jobs,mcp,meetings,memory,reach,retrieval}` | 2-4 each | 1.0K to 2.1K each | 2026-10-01 | `.claude/worktrees/wf_3ee5731e-2bf-{1..11}` |
| `qa/loop-fixes` | 2 small fixes (data-source fetch guards; per-limit sandbox rlimits) | | 2026-09-29 | none |
| `worktree-compact-chat-faces` | 1 ("a setting that folds chat windows to a face and a message box", 148 lines) | | 2026-10-04 | `.claude/worktrees/compact-chat-faces` |

Memory notes say the sota swarm and several of these were integrated by content through other branches, so most are probably superseded but not provably so by patch id. Decision per branch: cherry-pick what is missing, or delete branch and worktree. The eight `origin/backup/2026-10-05/*` refs can go once this list is settled.

### 2.15 Backend-only tuning knobs without UI or TS type (0 lines, informational)

Read by the backend, absent from `Settings` in `types.ts` and from `src/`: `fileSnapshots*` (4 keys), `toolReadRetries`, `parallelReads`, `stuckDetection`, `subagentMax*`/`subagentStale*`/`subagentToolSeconds` (5), `workflowMaxFanOut`, `jobRetryBackoffS`, `jobFailureStreakLimit`, `jobExpireDays`, `proposalExpireDays`, `modulesDefault`, `onboardedAt`, `provider`, `mcpServerNotes`, `microKeep`, `microAt`, `contextBudget`, `maxRunTokens`, `sandboxKeepDays`, `shellTimeoutSec`, `shellMaxBackground`, `browserIdleSeconds`, `requireReadBeforeWrite`. Each read in 1 to 4 places. No settings key in `llm.DEFAULT_SETTINGS` (148 keys) is dead, and no renderer key is missing a default. Hardcoding these saves almost nothing; a short list in `docs/permissions.md` or a "power settings" doc would be the cheaper fix.

### 2.16 Settings toggles

Every checkbox in `SettingsModal.tsx` and its sub-panels has a backend or `src/main` reader (verified key by key: `autoRoute`, `autoLearn`, `autoTitle`, `followUps`, `learnStyle`, `hybridRetrieval`, `autoCompact`, `contextualChunks`, `useDocsInContext`, `meetingEmbeddings`, `retrievalRerank`, `gmailSendHold`, `readerFallback`, `skipPermissions`, `docEditMode`, `unattendedApprovals`, `autoReview`, `snapshotsEnabled`, `browserEnabled`, `deskShellAuto`, `deskDoneGate`, `deskSelfReview`, `deskAutoResume`, `sandboxMountDesk`, `cacheLayout`, `otelExport`, `chatNotify`, `selectionToolbar`, `compactChats`, `devTools`, `notifyJobs`, all meeting `cfg.*` flags). Nothing to cut here.

### 2.17 Backend modules vs the frontend shell registry (0 lines, informational)

Backend registers 4 modules (`health`, `mailwatch`, `planner`, `todos`); the frontend `MODULES` has 2 (`todos`, `health`). `mailwatch` and `planner` have a Today card hard-wired in `HomeView.tsx:402-417` and `PlannerPanel` instead of a `ModuleDef`. `docs/module-manifest.md` still reads partly as a build plan (lines 49-56). Either port the two or update the doc.

### 2.18 Vocabulary drift (0 lines, informational)

"Canvas" in code (`canvases`, `canvas_windows`, `canvas_presets`, `ClassicView`, `lastClassicView`, 16 comments) vs "Space" in the UI; "workspace" means both the cowork desk workspace (`workspace.py`) and the granted `workspaceRoots`. Rename only when touching those files.

---

## 3. Refactor candidates (no lines removed; reorganisation)

### 3.1 `backend/personal_os/app.py` (9,792 lines, 349 routes, 23 `on_event` hooks)

Section map by line range: 1-742 imports, singletons, auth, middleware; 743-887 health/settings/models (4 routes); 888-930 projects (2); 931-1199 MCP connectors (20); 1200-3823 conversations, with `_chat_stream` at 1789-3529 as **one 1,741-line function** and the desk runner at 3576-3816 (11); 3824-4126 running views, shell jobs, sandboxes, run streams, steer, crew (13); 4127-4788 workflows, commands, agent defs, resume/stop, approvals and permissions (37); 4789-5331 scheduled jobs, proposals, agent inbox (10); 5332-5708 ship checklist (15); 5709-5802 usage (5); 5803-5937 memories (12); 5938-6031 writing style (7); 6032-6107 knowledge graph (7); 6108-6302 documents (10); 6303-6352 Google (4); 6353-6585 Microsoft (20); 6586-6698 assist (5); 6699-6807 dashboard and recap (2); 6808-7048 canvas (16); 7049-7472 docs (28); 7473-7752 activity (27); 7753-8301 meetings (32); 8302-8379 space presets (8); 8380-8417 outbox and backups hooks; 8418-8635 working memory and skills (14); 8636-8815 teach (13); 8816-9792 cowork desks, plans, workspaces (27).

Proposal, following the router-factory pattern already used by `trash.router(trash)`, `outbox_router(outbox)`, `otel_export.router(db, settings_fn)` and `snapshots_router(...)`, in order of safety:

1. `app.py` keeps lines 1-930 plus the singletons, auth, middleware, lifespan ordering and `include_router` calls (about 1,000). About 138 test files import `personal_os.app` and some reassign its globals (`_mcp_call` ×7, `extract_text` ×4, `_mcp_tooling` ×3, `learn_style_from_exchange`, `_digest_gaps`, `_digest_checked`, `usage`, `meeting_svc`, `meeting_store`), so singletons stay here and moved handlers must look patched names up late.
2. `routes_sensing.py` (about 830): activity, insights, meetings, 7473-8301. Self-contained, 59 routes; its pydantic config models are the bulk of the vulture noise.
3. `routes_spaces.py` (about 740): canvas 6808-7048, docs 7049-7472, presets 8302-8379.
4. `routes_integrations.py` (about 560): Google, Microsoft, assist, dashboard, recap, outbox and backup hooks.
5. `routes_knowledge.py` (about 590): usage, memories, style, graph, documents, 5709-6302.
6. `routes_skills.py` (about 400): working memory, skills, teach, 8418-8815.
7. `routes_jobs.py` (about 920): jobs, proposals, inbox, ship, 4789-5708.
8. `routes_agents.py` (about 590): MCP 931-1199 plus workflows, commands, agent defs 4127-4440.
9. `cowork_api.py` (about 980): 8816-9792.
10. `routes_chat.py` (about 830): conversation CRUD, running views, run streams, crew, resume/stop/approvals.
11. `chat_engine.py` (about 2,400): chat helpers 1404-1786, `_chat_stream`, `_run_chat` and the desk runner. Highest risk: `_chat_stream` closes over dozens of globals and tests call `app._chat_stream` directly. Do it last; breaking the function itself up is separate work.

Guardrails: preserve `on_event` order explicitly; do not name a router `docs` or `activity` (modules exist); route parity check `python3 -c "from personal_os.app import app; print(len(app.routes))"` before and after; run the per-file pytest runner. Risk: medium for 2-10, high for 11.

### 3.2 `backend/personal_os/tools.py` (3,792 lines)

`_register_*` are already free functions attached to `Toolbox`, and `fsx.register`, `space_tools.register`, `browser.register` show the target pattern. Map: 1-332 `ToolSpec`, danger and mode tables, helpers; 333-667 SSRF and URL guard; 668-990 `Toolbox` core (init, permission model, `gate`, `call`); 990-1463 core tools; 1464-1599 schedule; 1600-1718 result summarising, approval validators; 1719-2193 Google; 2194-2340 sandbox; 2341-2457 activity and style; 2458-2621 docs; 2622-2807 meetings; 2808-2990 mac and files; 2991-3206 skills; 3207-3606 cowork and desk; 3607-3792 reach, mcp_search, `tool_search`, core-set gating.

Proposal: `tools.py` core (about 1,100), `url_guard.py` (340; re-export `UrlBlocked`), `tools_google.py` (480), `tools_workspace.py` (sandbox, desk, files, mac; 730), `tools_knowledge.py` (activity, style, docs, meetings, skills; 680), `tools_web.py` (reach, mcp_search; 190). Watch the existing late import in `space_tools.register`; tests use `tools.REACH_TOOLS`, `tools.is_core` and monkeypatch `toolbox`. Risk: low to medium.

### 3.3 `backend/personal_os/meetings.py` (2,566) and `activity.py` (2,054)

`meetings.py` is already two halves: `Meetings` repo (460-1352) and `MeetingService` (1355-2566). Split into `meetings_store.py` (schema 46-163, helpers, `Meetings`; about 1,000) and `meetings.py` (service), optionally `meeting_enhance.py` (about 1953-2130) and `meeting_capture.py` (about 1443-1870); keep `Meetings` and `MeetingBlocked` re-exported. `activity.py` has section banners that map directly to `activity_gate.py` (152-280), `activity_mac.py` (281-905), `activity_store.py` (906-1040), `activity_collectors.py` (1041-1435) and the rollup/`Monitor` remainder (1436-2054); keep `REDACTIONS` re-exported for tests. Risk: low.

### 3.4 `src/renderer/src/store.ts` (4,533 lines, 156 importers)

One zustand store; keep it as the public entry point and re-export. Map: 1-163 types; 164-688 the `State` interface; 689-1035 module-level helpers and timers (`applyEvent` 925-1022); 1036-1851 closure helpers inside `create()` (`watchBackgroundEvents` 1305-1385, meeting live loop 1386-1448, `watchRun` 1514-1736, `runStream` 1737-1787); 1852-1976 initial state; 1977-2246 init, shell and UI actions; 2247-2781 chat actions (`send` 2456-2598, `sendToPageAgent` 2599-2679); 2782-3095 docs, folders, revisions, plans; 3096-3330 library and desks; 3331-3380 skills and agent defs; 3381-3807 meetings; 3808-3899 memories, graph, documents, style; 3900-4074 activity; 4075-4261 uploads, dashboard, recap, inbox, jobs, proposals; 4262-4427 Google, Microsoft, todo sync, todos; 4428-4533 post-create exports and selector hooks.

Proposal: slice creators (`StateCreator<State, [], [], XSlice>`) composed in `store.ts`, slices talking through `get()` as the code already does. `store/types.ts` (about 700), `store/chatSlice.ts` (about 1,050; `store.test.ts` imports `applyEvent`, `stopOutcome`, `settleInterrupted`, `learnedText`, so re-export), `store/docsSlice.ts` (420), `store/meetingsSlice.ts` (560), `store/desksSlice.ts` (270), `store/activitySlice.ts` (175), `store/integrationsSlice.ts` (300), `store/shellSlice.ts` + `store/events.ts` + `store/hooks.ts` (500). Residue about 300 lines. Module-level timers move with their slice. Precedent: `canvas/store.ts`, `features/docrec/store.ts`. Risk: medium; do `types.ts` and `api.ts` first.

### 3.5 `src/shared/types.ts` (3,328 lines, 278 exports, 219 importers)

Type-only except `askLocked`, `DEFAULT_EFFORT`, `NEEDS_YOU`, `DESK_LIVE`, so a barrel keeps every importer unchanged. Ranges: chat core 1-770 (tools, permissions and MCP inside 196-545); memory, style, graph 769-868; documents, uploads, todos 869-955; Google, Microsoft, calendar, mail, Drive 955-1188 and 1266-1275; health 1189-1265; Today 1276-1296; settings 1297-1510; usage and models 1511-1567; events, backend, `GrainApi` 1568-1817; desks 1827-2005; canvas, windows, trash, docs, presets 2006-2188; runs, jobs, inbox, bus 2189-2511; activity 2512-2796; meetings and docrec 2797-3122; planner, mail-watch, workflows, crew, agents 3123-3328. Proposal: `src/shared/types/{chat,tools,memory,docs,google,health,settings,events,desks,canvas,jobs,activity,meetings,agents}.ts` with `types.ts` as `export * from`. `GrainApi` (1712-1796) imports nearly everything, so keep it in `events.ts` or a `bridge.ts`. Fix the duplicate `PlanEdit` first (1.1). Both `tsconfig.node.json` and `tsconfig.web.json` include `src/shared`. Risk: low to medium.

### 3.6 `src/renderer/src/styles.css` (2,958 lines)

The file's own `/* ---------- X ---------- */` headings give contiguous ranges; cascade order must be preserved, so split into files imported in order: `base.css` 1-242 (tokens, reset, shell grid), `sidebar.css` 243-381, `chat.css` 382-836, `settings.css` 837-1062, `shell-nav.css` 1063-1232, `memory-docs.css` 1233-1439, `tools.css` 1440-1514 + 1682-1917 (not contiguous; `home-lists.css` 1515-1681 sits between), `inbox-kanban-cal.css` 1918-2233, `mail.css` 2234-2370, `activity.css` 2371-2595, `controls.css` 2596-2684 (global; keep late), `library.css` 2685-2958. 50 lines exceed 200 characters, so split first and format separately. Verify the top three views in light and dark. Risk: low.

### 3.7 `src/renderer/src/lib/api.ts` (1,051 lines, 99 importers)

1-173 base URL, token, `req`, `fetchRaw` to `lib/api/core.ts`; 175-885 the `api` object's 40 namespaces to `lib/api/<domain>.ts` (largest: `meetings` 803-862, `cowork` 705-752, `activity` 672-704, `docs` 773-802, `google` 368-415, `mcp` 338-362, `teach` 553-598); 886-1051 SSE (`chatStream`, `backgroundStream`, `readWithIdle`) to `lib/api/stream.ts`. Keep `api.ts` as `export const api = {...a, ...b}` plus re-exports (`api.test.ts` and `chatStream.test.ts` import `CONTROL_TIMEOUT_MS`, `readWithIdle`, `STREAM_*`). Risk: low.

### 3.8 `src/renderer/src/components/ActivityView.tsx` (1,145 lines)

`ActivityView` at 648-1133 holds four tab bodies inline (overview 759, insights 812, signals 958, privacy 1041); 21-647 are 13 helper components. Split into `activity/ActivityInsights.tsx` (812-957 + `HabitRow`, `SuggestionCard`, `PatternRow`, `Bars`, `HoursStrip`; about 330), `ActivitySignals.tsx` (958-1040 + `EventRow`; about 100), `ActivityPrivacy.tsx` (1041-1133 + `RedactionPanel`, `CategoriesCard`, `RuleEditor`, `ListEditor`, `NumberField`; about 400), leaving overview and shell (about 350). Move `AudioDevicePicker` out so `MeetingSettings` stops importing an 1,100-line view. Tabs share local hook state, so props or a small context are needed. Risk: low to medium.

### 3.9 `src/main/agentBrowser.ts` (1,072 lines)

Sections: constants and types 22-80; session and CDP plumbing 81-203; window and tab creation 204-338; snapshots and replies 339-433; element targeting and input 434-596; downloads 597-650; request handlers 651-987 (`act` 764-848, `manage` 871-951); live frame streaming and IPC 988-1072. Proposal: `agentBrowser/cdp.ts` (130), `input.ts` (165), `downloads.ts` (55), `frames.ts` (85), shared `sessions` map and `Sess`/`Tab` types in `state.ts`, remainder about 600. Electron-main code with little test cover and heavy shared mutable state; lowest priority of the splits. Risk: medium.

### 3.10 Test harness

- Replace the hand-maintained 134-file list in `package.json` `test` with a glob (see 1.8).
- Consolidate `backend/personal_os/tests/` into `backend/tests/` and dedupe the two name collisions (2.13).
- Unify the two fake-Google implementations (2.13).

### 3.11 Settings plumbing

`Settings.mode` and `withoutLegacyMode` (2.11) are the only migration code left in the renderer store; once retired, `init()` loses a special case. `modulesDefault` and `hiddenViews` are read in `app.py:274,287` to build the module list and may still carry ids of removed views in saved settings (harmless `includes` on unknown ids); a one-line prune in `public_settings()` would keep saved data honest.

---

## Appendix: what was checked and found clean

- No `backend/personal_os/*.py` module has zero production importers (`health_sync.py` and `todocal.py` are imported through `modules/`, which simple greps miss).
- No agent tool is permanently unreachable; 152 `ToolSpec` registrations, all gated only by services or settings. No frontend display map (`toolDisplay.ts` `VERBS`, 124 keys; `toolcards/*`; `permTester.ts`) names a tool the backend lacks. 29 backend tools have no `VERBS` entry and fall back to the generic card.
- No `View` id, `HOME_MODULES` key or `WidgetKind` is orphaned; `canvas.py:97` drops windows of unknown kind at startup.
- No e2e spec or backend test targets a removed feature or imports a missing module.
- No npm or Python dependency is unused.
- No settings default is unread; no settings toggle gates nothing.
- The only localStorage nit is `calendar.mode` (`CalendarView.tsx:67-68`) lacking the `grain.` prefix.
