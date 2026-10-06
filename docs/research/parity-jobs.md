# Parity: scheduled jobs, one-off tasks, Agent Inbox, proactive delivery

Date: 2026-10-04. Worktree: `harness-parity`. Grain was read in code (no edits); peers from public docs (URLs below).

## Peers surveyed

| Short name | Product | Sources |
|---|---|---|
| ChatGPT | ChatGPT Tasks (Scheduled page, rebuilt June 2026) | https://help.openai.com/en/articles/10291617 , https://futurefive.co.nz/story/openai-expands-chatgpt-scheduled-tasks-with-new-hub , https://gigazine.net/gsc_news/en/20260619-chatgpt-scheduled-tasks/ |
| Pulse | ChatGPT Pulse (retired June 2026) | https://help.openai.com/en/articles/12293630-chatgpt-pulse , https://www.digit.in/news/general/openai-is-retiring-chatgpt-pulse-and-replacing-it-with-scheduled-tasks-here-is-why.html/amp/ |
| Routines | Claude Code routines (cloud) | https://code.claude.com/docs/en/routines |
| Desktop | Claude Code Desktop local scheduled tasks / Cowork scheduled tasks | https://code.claude.com/docs/en/desktop-scheduled-tasks , https://support.claude.com/en/articles/13854387 |
| Gemini | Gemini scheduled actions / Spark schedules | https://support.google.com/gemini/answer/17094710 , https://blog.google/products/gemini/scheduled-actions-gemini-app/ |
| Manus | Manus Scheduled Tasks / Automations | https://www.manus.im/docs/features/scheduled-tasks , https://manus.im/docs/automations |
| Lindy | Lindy routines | https://docs.lindy.ai/teammate/routines.md , https://docs.lindy.ai/account-billing/usage.md |
| Zapier | Zapier Agents / AI by Zapier | https://help.zapier.com/hc/en-us/articles/45394909914381 , https://help.zapier.com/hc/en-us/articles/47402591569805 |

## Feature-by-feature

Legend: Y = has it, P = partial, N = no, ? = not documented.

| Capability | Grain | ChatGPT | Routines | Desktop | Gemini | Manus | Lindy | Zapier |
|---|---|---|---|---|---|---|---|---|
| Create from chat in natural language | P: `schedule_task` takes ISO-8601 only (`jobs.parse_when`); the model must call `current_time` first | Y | Y (`/schedule tomorrow at 9am`, confirms the absolute time) | Y | Y (confirms schedule) | Y | Y | P |
| Form with presets (hourly/daily/weekdays/weekly) | N: raw cron text box in `NewTask` (AgentInbox.tsx:301) | Y | Y | Y | Y | Y | Y (default daily 9:00) | Y |
| Preview of next fire times | N (only "next" on the row) | Y (next run) | ? | ? | Y | Y (calendar view) | ? | ? |
| One-off retires after firing, stays visible | Y (`mark_fired(disable=True)`, row shows "ran") | Y | Y ("Ran") | Y | Y (Completed section) | Y | ? | ? |
| Edit name / prompt / schedule after creation | N in UI; `PATCH /jobs/{id}` supports every field (app.py:4151) | Y | Y | Y (a task can edit itself) | Y | Y | Y | Y |
| Pause / resume, delete, Run now | Y (toggle, Play, Delete) | P (no Run now) | Y | Y | Y | Y (run a test) | Y | Y |
| Dry run / read-only preview | Y (`/jobs/{id}/dry_run`, Eye button) | N | N | N | N | Y ("run a test") | Y | N |
| Per-job tool / connector scoping | Y (`allowed_tools`, job_tools.py) | P (admin app restriction) | Y (connectors default to all) | Y (permission mode) | N | Y (project) | P | Y |
| Per-job model | N (global `defaultModel` in `_launch_job`) | N | Y | Y | N | ? | ? | Y (model tier) |
| Per-job budget / runaway guard | P: one constant `JOB_BUDGET` (app.py:1333) plus `JOB_HARD_SECONDS` | N | P (account usage) | N | P (compute limits) | ? | Y (pauses and asks on a heavy task) | Y (per-run task cap 75-500, pauses) |
| External writes gated in unattended runs | Y: always a proposal (two gates, `PROPOSAL_ONLY_DANGER`) | N (connected apps act) | N (fully autonomous) | P (stalls on approval) | ? | P (confirm by default, can skip) | Y ("confirm before sending") | P (per-tool toggle) |
| Trigger payload treated as untrusted | Y by construction (no payload; the prompt is the user's own) | ? | Y (`<routine-fire-payload>`) | ? | ? | ? | ? | ? |
| Missed-run policy | Y: one catch-up for the latest slot plus a late notice in the prompt; no lookback cutoff | ? | Y (auth-gated skips) | Y: one catch-up, 7-day lookback | ? | ? | ? | ? |
| Skipped runs recorded with reason | P: only the last skip (`last_skip_reason`) | N | P | Y (asleep / still running / other tasks) | N | Y (errors in history) | ? | Y |
| Overlap guard | Y (`JobPolicy.admit`) | ? | ? | Y | Y (15 concurrent) | ? | ? | ? |
| Retry with backoff, auto-pause on failure streak | Y (jobs_policy.py) | N | P (off after 72 h of bad auth) | N | N | N | N | N |
| Wake from sleep / run while app closed | P: `pmset schedule wake` needs root (jobs.py:239) and fails silently; no `powerMonitor` resume hook | Y (cloud) | Y (cloud) | P (app must be open; Cowork now remote) | Y (cloud) | Y (cloud) | Y (cloud) | Y (cloud) |
| Event triggers | P: folder watch only, backend-only (renderer `Job.kind` is `'cron' \| 'once'`, types.ts:1930) | P (monitoring tasks) | Y (GitHub, API) | N | Y (Gmail monitor, topic monitor) | N | Y (email, Slack, calendar N min before, webhook) | Y (app events) |
| Each run is its own inspectable thread | Y (hidden conversation, "open" link) | Y | Y | Y | Y | Y | Y | Y (Zap history) |
| Dedicated management page / sidebar entry | P: lives on Today only (`HomeView` "agent" module); sidebar badge counts desks only (Sidebar.tsx:91) | Y | Y | Y | Y | Y | Y | Y |
| Per-job run history, stats, export | Y (`/jobs/{id}/runs`, stats, CSV) | N | Y | Y | P | Y | ? | Y (2 days by default) |
| Push / OS notification | P: raw `new Notification` while hidden (App.tsx:113); no click action, no Settings toggle, nothing for a plain success | Y (push + email) | N | Y | ? | P (output target) | Y (SMS / Slack) | Y (email / Slack) |
| Quiet hours / notification limit | N | P (OS settings) | N | N | N | N | N | N |
| Notify only when there is something to report | N (every run is a card) | Y (monitoring tasks) | N | N | Y (monitors) | N | P | N |
| Proposals inbox: accept / edit / reject | Y (`/proposals/*`, single-use claim, edit on accept) | N | N | P (sidebar approvals) | N | N | Y (approve via Slack / web) | Y (HITL approve / decline / edit) |
| Undo window on sent mail | Y (outbox.py, default 90 s) | N | N | N | N | N | N | N |
| Daily brief | Y twice and unlinked: `/recap` (LLM, cached per day) and the seeded "Morning brief" job; plus a "Brief me" chat button | Y (a task; Pulse retired) | N | N | P | P | Y | N |
| Active-task caps | N (by design for a local app) | Y (3-15) | Y | N | Y (10 / 50) | ? | Y (credits) | Y |

## Where Grain is ahead

- Unattended runs cannot touch the outside world. Every mail, calendar, Tasks or MCP call from a job becomes a `proposals` row, checked in `_call_tool`/`_propose` and again in `Toolbox.call`. Cloud routines let every included connector tool run without asking. Grain's proposals inbox, with edit-on-accept and a single-use atomic claim, is stricter than any peer surveyed.
- Run policy: overlap guard, retry with exponential backoff, failure-streak auto-pause with the reason shown, and a boot retry for an interrupted run. No peer documents retries or auto-pause; Routines only turn themselves off after 72 h of failed auth.
- Late runs say they are late. `LATE_NOTICE` tells the model when the slot was due and how many were skipped, so it re-checks time-sensitive facts. Desktop does one catch-up and a notification but does not tell the model.
- A read-only preview (dry run) that switches off everything not `safe`. Only Lindy and Manus offer a comparable test run.
- Per-job history with stats and a CSV export whose cells are escaped against formula injection.
- Gmail sends, including accepted proposals, sit in a 60-120 s undo outbox.

## Where Grain differs on purpose

- Local, not cloud. Jobs run only while the backend runs. That is the cost of keeping data on the Mac; peers that run while closed rely on their cloud. The aim is to make local limits visible (skip reasons, a wake hook), not to add a server.
- No caps on active tasks. A single-user local app with a budget on every run does not need one.
- A fresh conversation every fire, with autoLearn off. Manus offers "continue in the same context"; Grain picks repeatable, cheap runs.
- No opaque proactive feed. Pulse was retired in favour of explicit, user-owned schedules, which is how Grain already works. Insights suggestions are proposals only.
- Proposals instead of stalls. Desktop's local tasks stall on an approval; Grain parks external writes as proposals, lets the run finish, and refuses in-app "ask" tools.
- Schedule tools ask by default, and a job can never schedule a job (`schedules` danger tier).

## Gaps (ranked)

| ID | Priority | Size | Gap |
|---|---|---|---|
| J1 | P1 | M | No edit form for a job (name, prompt, schedule, timezone, project, retries); a spent one-off cannot be re-armed from the UI |
| J2 | P1 | S | Raw cron only: no presets, no "next runs" preview |
| J3 | P1 | M | Job notifications: no click-through, no Settings toggle, no quiet hours, no "tell me every run" option |
| J4 | P1 | S | Wake: `pmset` needs root and fails silently; no `powerMonitor` resume nudge, so catch-up after wake can take up to 60 s |
| J5 | P1 | M | Folder-watch jobs cannot be created or seen properly in the renderer, and the run is not told which files changed |
| J6 | P1 | M | Inbox reachable only from Today: no sidebar badge, no mark-as-read on run cards |
| J7 | P1 | M | No per-job model or tighten-only budget |
| J8 | P2 | S | Only the last skip is kept; no skipped-slot history with reasons |
| J9 | P2 | S | Proposal hygiene: never expire, orphaned after job delete, no bulk reject, misleading "actually ran" toast for queued mail |
| J10 | P2 | M | No mail-arrival trigger (Gmail query monitor) |

The structured gap list returned with this doc has the proposal, files and tests for each. Not proposed: caps on active tasks, cloud execution, phone push (needs a service or an account), an opaque daily feed, auto-executing anything a job proposes.

## Inventory spot-checks (verified in code)

- `src/shared/types.ts:1930,1987` and `src/renderer/src/lib/api.ts:177`: `kind: 'cron' | 'once'`; no `watch_dir` on the create type.
- `backend/personal_os/app.py:4075-4165`: `JobPatch` accepts name, prompt, kind, cron, run_at, timezone, enabled, project_id, max_retries, allowed_tools and watch_dir. `JobRow` in `AgentInbox.tsx` (L210-296) only patches `allowed_tools`; the store only patches `enabled`.
- `app.py:1332-1333`: `JOB_HARD_SECONDS = 1800.0` and the `JOB_BUDGET` constant. `_launch_job` creates the conversation with `cfg.get("defaultModel")`.
- `app.py:4010`: `_job_prompt` uses only `late` and `missed_slots`; the `trigger` and `collapsed` set in `jobs.py:658,672` are ignored, and no file names are carried.
- `jobs.py:239-253`: `subprocess.run(["pmset", "schedule", ...], check=False)`, a failure logged at debug only. No `powerMonitor` anywhere in `src/main`.
- `App.tsx:100-113`: `JobNotifier` calls `new Notification(e.title, { body })` with no click handler; `src/main/deskNotify.ts` already has a main-process notifier with click-to-navigate.
- `job_history.py:96-132`: `notify_events` emits only failed, done-with-proposals, paused and proposal_pending events.
- `store.ts:3703`: the toast "Done — that one actually ran." is shown on every accept.
- `Sidebar.tsx:91`: the sidebar badge counts `deskInbox` only.
