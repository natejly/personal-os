# Paperclip: what it is and what Grain can take from it

Date: 2026-10-11. Repo: https://github.com/paperclipai/paperclip (MIT), shallow clone at commit `bfe7c6357d1e0c945c8cb84259153cb3b6542d28` (2026-10-11). Read as data only; nothing was installed or run. Grain was read in code at the worktree `paperclip-research`.

Paths below are relative to the Paperclip repo unless they start with `backend/`, `docs/` or `src/` (Grain).

## 1. What Paperclip is

A Node/TypeScript server plus React UI (`server/`, `ui/`, `cli/`) that runs a "company" of AI agents. The pitch in `README.md`: "If OpenClaw is an employee, Paperclip is the company." It does not run models itself. It stores the org, hands work to external agent runtimes (Claude Code, Codex, Cursor, Gemini CLI, OpenCode, Pi, Hermes, Grok, Kimi, OpenClaw, plain processes or HTTP) and records what they did and spent. The README's four pillars: agentic task manager, org chart for agents, agent training (skills, evals), and an "agentic OS" (runtimes, sandboxes, SSO, cost controls).

It is a multi-tenant control plane: everything carries `companyId`, with board users (humans) and agents as two kinds of actor. Postgres via Drizzle (`packages/db/src/schema/*.ts`, an embedded Postgres for local installs, `packages/db/src/embedded-postgres-*.ts`). Scale is large: `server/src/services/heartbeat.ts` alone is 5.3k lines, with a `services/heartbeat/` folder of 14 sub-modules.

### Core concepts and where they live

| Concept | Shape | Where |
|---|---|---|
| Agent ("employee") | Row with `role`, `title`, `reportsTo` (self-reference, so the org chart is a tree), `capabilities`, `adapterType`, `adapterConfig`, `runtimeConfig`, `permissions`, `budgetMonthlyCents`, `status`, `pauseReason`, `lastHeartbeatAt`. | `packages/db/src/schema/agents.ts` |
| Agent lifecycle | Explicit state machine (`pending_approval`, `preparing`, `ready`, paused, terminated, rejected) with holds and a version counter; commands own their transactions and a worker finishes external steps. | `doc/AGENT-LIFECYCLE.md`, `server/src/modules/agent-lifecycle/`, `server/src/services/agent-lifecycle.ts` |
| Hiring | An agent can propose a hire; it becomes a `hire_agent` approval and the agent sits in `pending_approval` until the board accepts. | `server/src/services/agent-hiring.ts`, `approvals.ts` |
| Goals | Tree (`level`, `status`, `parentId`, `ownerAgentId`). Tasks and projects link to a goal and the agent receives the goal chain as context. | `schema/goals.ts`, `server/src/services/goals.ts`, `issue-goal-fallback.ts`, `doc/GOAL.md` |
| Tasks ("issues") | Single assignee (agent or user, never both), parent/sub-issue structure, separate blocker relation, statuses with execution meaning (`backlog`, `todo`, `in_progress`, `blocked`, ...). | `doc/execution-semantics.md`, `server/src/services/issues.ts` |
| Checkout | Moving an issue to agent-owned `in_progress` requires a checkout; `checkoutRunId` is a lock tying the issue to one run. | `doc/execution-semantics.md` section 5, `server/src/routes/issues.ts` |
| Heartbeat | The unit of agent execution. An agent "wakes" for assigned work, a comment, a dependency resolving, a schedule or a manual poke; each wake becomes a request, then a run. | `server/src/services/heartbeat.ts`, `heartbeat/queue.ts`, `heartbeat/run-lifecycle.ts`, `schema/agent_wakeup_requests.ts` |
| Routines | Recurring or triggered work that creates an issue. Policies: `concurrencyPolicy` (`coalesce_if_active` or `skip_if_active`), `catchUpPolicy` (default `skip_missed`), `activityGatePolicy` (run only if something changed, scoped to company or project). Every edit snapshots a revision with restore. | `schema/routines.ts`, `server/src/services/routines.ts` |
| Budgets | Policies by scope (company, agent, project), metric, window; `warnPercent` (80), `hardStopEnabled`, `unpricedUsagePolicy` (default `block`). Crossing creates a `budget_incident`, which can open a `budget_override_required` approval. | `schema/budget_policies.ts`, `budget_incidents.ts`, `server/src/services/budgets.ts` |
| Budget reservations | A run reserves an estimate before provider dispatch, under the same lock as charges; reservations survive restarts until accounting proves closure. | `server/src/services/budget-reservations.ts`, `accounting-transaction.ts`, `accounting-integrity.ts` |
| Cost ledger | One `cost_events` row per provider call with agent, issue, project, goal, run, provider, biller, billing type, `costStatus`, `pricingProvenance`, `idempotencyKey`, `receiptHash`. | `schema/cost_events.ts`, `server/src/services/costs.ts`, `billing-reconciliation.ts` |
| Approvals | Typed requests (`hire_agent`, `approve_ceo_strategy`, `budget_override_required`, ...) with payload, comments, decision note, decider. Also issue-thread interactions and a decision-queue system (`decisions*.ts`) with training data from past decisions. | `schema/approvals.ts`, `server/src/services/approvals.ts`, `issue-approvals.ts`, `decision-queues.ts` |
| Completion contracts | Per-issue versioned acceptance criteria with a risk level, a completion authority and a policy for incomplete criteria. | `schema/completion_contracts.ts` |
| Adapters | A package per runtime exposing `execute`, environment test, model list, session codec, quota and skills. | `packages/adapters/*`, `packages/adapters/claude-local/src/server/execute.ts`, `packages/adapter-utils`, `adapter-plugin.md` |
| Execution workspaces | Each run gets a local worktree; the cwd is the only cross-run persistence (no remote git), enforced by a static check that forbids `git push` in runtime code. Ssh and sandbox adapters round-trip the cwd. | `packages/adapters/AUTHORING.md`, `scripts/check-no-git-push.mjs`, `schema/execution_workspaces.ts` |
| Sessions | Per-agent and per-task session handles so a wake resumes the runtime's conversation. | `schema/agent_runtime_state.ts`, `schema/agent_task_sessions.ts` |
| Recovery | Liveness classification of the issue graph (an in-progress issue with no live path to move is a finding), detached-process detection, bounded transient retries with a schedule, workspace-busy deferral with jitter. | `server/src/services/issue-liveness.ts`, `heartbeat/recovery.ts`, `heartbeat/retries.ts` |
| Audit | Activity log on every mutation, agent commentary, per-run event log, instruction-file history. | `schema/activity_log.ts`, `server/src/services/activity-log.ts`, `agent-action-audit.ts`, `heartbeat/run-log.ts` |
| Skills and evals | Company skill catalog with sources and policies; promptfoo evals and an eval kernel. | `packages/skills-catalog`, `schema/company_skills.ts`, `evals/`, `packages/paperclip-eval-kernel`, `doc/evals.md` |
| Plugins and MCP | Plugin SDK, an MCP server exposing Paperclip to agents, MCP access governance. | `packages/plugins/sdk`, `packages/mcp-server`, `doc/MCP-ACCESS-GOVERNANCE.md` |
| Memory | A provider contract above several memory systems, company default with per-agent override, provenance back to runs and issues; a design note, with a bundled wiki plugin. | `doc/memory-landscape.md`, `packages/plugins/plugin-llm-wiki` |
| Chat channels | Slack, Discord, Teams, Telegram, GitHub, voice bridges for talking to agents. | `doc/CHANNELS.md`, `server/src/services/chat-*.ts` |

### How a run flows

1. Something requests a wake: issue assignment (`issue-assignment-wakeup.ts`), a comment (`issue-comment-wakeup.ts`), a dependency finishing (`issue-dependency-wakeups.ts`), a routine, a timer, or a person.
2. The request is written to `agent_wakeup_requests` (source, reason, payload, `idempotencyKey`, `coalescedCount`, status `queued`, claimed, finished). Duplicate wakes for the same agent coalesce into the live request instead of stacking (`heartbeat/queue.ts`, `shouldDeferFollowupWakeForSameIssue`).
3. Admission checks: agent not paused, budget not blocked (`getInvocationBlock`), no workspace lock holder, per-agent start lock (`agent-start-lock.ts`).
4. A budget reservation is taken, a workspace is prepared, the adapter's `execute` runs the external runtime, output streams to the run log.
5. Completion writes cost events, settles the reservation, updates the session handle, records an issue change receipt (`issue-change-receipt.ts`) and wakes whoever is waiting.
6. A reconciler repairs anything that stopped halfway (startup recovery, stale lease, detached process).

### Reading it critically

- Most of the weight is multi-tenant, multi-agent, multi-runtime bookkeeping that exists because a company has many agents that talk to each other. Grain has one user, one machine, one SQLite file.
- The tasks-as-wakeups model (an agent only acts when a task or comment wakes it) is the one idea that shapes everything else. It gives a clean audit trail, at the price of pushing every interaction through a ticket.
- Accounting is treated as a transaction-grade system (locks, reservations, receipt hashes, integrity checks). That is justified for a hosted product billing real money; it is more than a local app needs.

## 2. What Grain does today (for the comparisons)

- Runs: `backend/personal_os/runs.py` (`RunStore`, `RunBus`, per-tool-call idempotency keys, `unknown_outcome`), chat jobs in `app.py`. `RunMeter` (app.py:1555) records rounds, tokens, cost and seconds, and its docstring says it is display only.
- Scheduling: `jobs.py` (triggers: cron, one-off, mail query, calendar, watch dir, `only_on_change`), `jobs_policy.py` (overlap skip, retries with backoff, failure streak auto-pause with `paused_reason`, auth-breaker aware), `scheduling.py`, `job_history.py` (history derived from rows).
- Attention: `attention.py` maps every run, desk and job to one of idle, working, needs_you, blocked, mirrored in `src/renderer/src/lib/attention.ts`.
- Agents: saved roles in `docs/agents.md`; delegation through `workers.py` and `subagents.py`; editing a prompt withdraws approval.
- Safety: approvals (`permissions.py`, `permrules.py`, `approval_log.py`), proposal-only external writes in unattended runs, read-back checks (`verify.py`), stuck detection (`stuck.py`).
- Cost: `usage.py` writes `usage_log(model, kind, conversation_id, project_id, tokens, cost, estimated, tag, round)`. `backend/tests/test_no_budgets.py` pins a deliberate decision: nothing caps a reply, a job, a subagent or a desk turn; only stuck detection, idle detection and timeouts stop work.
- Memory: `memory_index.py`, `memory_limits.py`, `consolidate.py`, `learn.py`, graph recall; see `docs/research/sota-memory.md`.

## 3. Ideas worth adopting, ranked by value against effort

Ranking is value to a single-user local app divided by effort. S is under a day, M is a few days.

### 1. Record every skipped, coalesced and deferred fire as a row (S)

Paperclip: `routine_runs` has statuses `skipped` and `coalesced`, with `coalescedIntoRunId` pointing at the live run (`schema/routines.ts`, `services/routines.ts` around lines 1890 and 1957). A user can see "9:00 skipped because the 8:00 run was still live".

Grain: `jobs.py` keeps only the latest skip on the job row (`last_skip_at`, `last_skip_reason`, around line 631). `docs/research/parity-jobs.md` already lists this as a gap.

Change: add a small `job_fires` table (job_id, slot, outcome: ran / skipped / coalesced / retried / late, reason, run_id) written by `jobs_policy.py` and the scheduler. `job_history.py` already derives history from rows, so it can include these. Gives the user an answer to "why did my digest not run".

### 2. Attribute cost to the thing that caused it, and show it where the user looks (S to M)

Paperclip: every `cost_events` row carries agent, issue, project, goal, run, and a `costStatus` and `pricingProvenance` so unpriced or estimated spend is visible rather than hidden (`schema/cost_events.ts`).

Grain: `usage_log` knows conversation and project and a free-text `tag`. A job run's cost is in `agent_runs.budget` JSON (a meter snapshot), a second place. There is no per-job or per-agent total.

Change: add `run_id`, `job_id`, `agent_id` columns to `usage_log` (migration in `migrations.py` next to the existing usage_log upgrade near line 505), fill them where `Usage.record` is called, and add "spent this month" to the job row in the Agent Inbox and the agent page. Keep it read-only.

### 3. Soft spend thresholds that notify, never stop (S)

Paperclip: `warnPercent` (default 80) raises an incident and a notification before `hardStopEnabled` pauses work (`schema/budget_policies.ts`, `budgets.ts`).

Grain: deliberately no caps (`test_no_budgets.py`). A scheduled job that quietly gets expensive is the one case where a number helps, and it fits the existing failure path: `jobs_policy.py` already pauses a job with a `paused_reason` and shows it under "Needs you".

Change: an optional per-job or global "tell me when a job's month-to-date cost passes X" setting that raises an attention item and a notification. No admission check, no stop. This respects the no-caps decision; a hard stop would reverse it and should be a separate conversation.

### 4. Revision history with restore for job and agent definitions (S to M)

Paperclip: every routine edit stores a full snapshot (`routine_revisions`: snapshot, `changeSummary`, `restoredFromRevisionId`, who and which run made it), so an agent that edits its own routine can be rolled back (`schema/routines.ts` from line 70).

Grain: `PATCH /jobs/{id}` rewrites in place (`jobs.py` update path); agents lose approval on prompt edit but keep no previous prompt (`docs/agents.md`). A model can edit a job through chat tools.

Change: `job_revisions` and `agent_revisions` tables written on every update with an `actor` field (user or run id), a Restore button, and a diff view. Restoring an agent prompt still withdraws approval. Cheap insurance for self-editing automation. Note Paperclip itself moved away from revision tables for agent instruction files (`doc/agent-instruction-revisions.md`) in favor of managed files; the routine-level snapshot is the part worth copying.

### 5. A "blocked needs a named owner and action" rule (S)

Paperclip: an issue may enter `blocked` only with a blocker issue, a pending approval naming a responder, or a structured `{owner, action}` descriptor; prose-only blocked is rejected and the named owner is notified at once (`doc/execution-semantics.md`, section 3).

Grain: `attention.py` has `blocked` and `needs_you`, but a run that ends waiting does not have to say what it is waiting for or who can unblock it.

Change: when a job or desk run ends in a waiting state, require a one-line `waiting_on` and `action` in the run record and show it on the Inbox row instead of a generic status. Where the model supplied none, derive it from the pending proposal or approval. Improves "Needs you" rows with no schema churn.

### 6. Idempotent wake requests as a single ingress (M)

Paperclip: every cause of a run funnels through `agent_wakeup_requests` with source, reason, `idempotencyKey` and `coalescedCount`, so a burst of comments produces one run that sees all of them (`heartbeat/queue.ts`).

Grain: each trigger launches its own way (mail watch in `mailwatch.py`, calendar and cron in `jobs.py`, worker reports in `workers.build_wake`, Telegram in `telegram.py`). Overlap is handled per job by `JobPolicy.admit`, but a burst of mail matches or a flurry of worker reports is handled case by case.

Change: introduce one internal `wake(target, source, reason, key)` that coalesces by target and key while a run is live and queues exactly one follow-up carrying all coalesced reasons. Migrate mail watch first, since that is where bursts occur. Worth doing only if mail-triggered jobs show duplicate runs in practice; do not build it speculatively.

### 7. Stale-state reconciler for "in progress with no live run" (M)

Paperclip: `issue-liveness.ts` and `heartbeat/recovery.ts` classify tasks that claim to be in progress but have no live execution path, and create a recovery action rather than leaving a silent dead state.

Grain: startup recovery marks runs `interrupted` and `boot_retry` re-launches one (`jobs_policy.py`). Desks and todos that point at a vanished run are not swept.

Change: a periodic sweep (run at boot and every few minutes) that finds desks, worker children and todos marked active whose run row is terminal or missing and moves them to `blocked` with a reason. Read `docs/research/track-f-codebase-gaps.md` first to avoid duplicating an open item.

### 8. Goal context passed to runs (S, uncertain value)

Paperclip: tasks inherit their goal chain, and the prompt includes why the work matters (`issue-goal-fallback.ts`).

Grain: projects and spaces provide context; jobs have a prompt and an optional `origin_conversation_id`. An optional one-line "purpose" on a job, shown in its prompt and in notifications, captures most of the benefit. Skip a goal tree.

### 9. Completion check for background work (M)

Paperclip: `completion_contracts` gives each issue acceptance criteria and says who may declare it done.

Grain already has the stronger half: `verify.py` reads external writes back instead of trusting the model. The missing half is an optional "done when" line on a job that the final summary is checked against, surfacing "criteria unmet" in the Inbox. Low priority; only if jobs produce confident-but-wrong summaries.

## 4. Things to skip, and why

- Org chart and `reportsTo` hierarchy, CEO/CTO personas, delegation up and down the tree. Grain has one human; a flat list of saved agents plus `delegate` (`workers.py`) is the right shape. A hierarchy adds ceremony with no user to staff it.
- Hiring and agent lifecycle as an approval workflow (`agent-lifecycle`, `hire_agent`). Grain's "inert until you approve it" (`docs/agents.md`) already covers the single-user case.
- Multi-company tenancy, board roles, memberships, SSO, RBAC, API keys per agent (`schema/company_memberships.ts`, `agent_api_keys.ts`, `auth/`). Not applicable to a local desktop app.
- Hard budget enforcement with reservations, ledgers locks and receipt hashes (`budget-reservations.ts`, `accounting-*.ts`, `billing-reconciliation.ts`). Reverses the decision in `test_no_budgets.py` and solves a hosted-billing problem. Take only the attribution (idea 2) and the soft alert (idea 3).
- Postgres, Drizzle, embedded Postgres supervision. SQLite with `migrations.py` is the correct choice here.
- External-runtime adapters as the core abstraction (`packages/adapters/*`). Grain runs its own loop and wraps coding agents through `codingagents.py`, `opencode.py` and `pi.py`. An adapter registry would be a rewrite with no user-visible gain. The one transferable rule is "persistence across runs lives in the working folder, never in a git remote", which matches how coding sessions already treat worktrees.
- Execution workspace leases, ssh and sandbox sync-back (`schema/environment_leases.ts`, `adapter-utils/src/ssh.ts`). Grain's sandbox work is tracked in `docs/research/parity-sandboxes.md`.
- Tasks as the only way to talk to an agent (everything is an issue or comment). Grain's chat-first interaction is the product; do not push chat through tickets.
- Decision queues, decision models and training examples (`decisions*.ts`, `decision-training.ts`). Interesting (learning approvals from past decisions) but Grain's `permrules.py` and `approval_log.py` already learn "always allow" rules, and a trained model for one user is premature.
- Chat channel zoo (Slack, Discord, Teams, voice). Grain has Telegram (`docs/telegram.md`); more channels are demand-driven, not a Paperclip lesson.
- Skill catalog, team catalog, ClipHub marketplace (`packages/skills-catalog`, `teams-catalog`, `doc/CLIPHUB.md`). Grain's skill linting (`skillbuild.py`) and approval flow is more careful about untrusted skill text; a marketplace is a trust problem we do not need.
- Memory provider contract (`doc/memory-landscape.md`). It is a design note abstracting over external memory services; Grain owns its memory store and should not add an indirection layer.
- The scale of the codebase itself. A 5k-line `heartbeat.ts` with 14 sub-modules, transactional accounting and migration-safety tooling reflects hosted-service risk. The lesson is the vocabulary (wake, coalesce, skip, reservation, liveness), not the structure.

## 5. Suggested order

1. Fire log (idea 1) and cost attribution (idea 2) together: both touch `jobs_policy.py`, `usage.py` and `job_history.py`, and make the Inbox answer "what ran, what skipped, what it cost".
2. Soft cost alert (3) and `waiting_on` (5): small, user-visible, no new tables beyond a column.
3. Job and agent revisions (4): independent, can run in parallel.
4. Hold ideas 6 to 9 until a concrete failure (duplicate mail-triggered runs, dead desks, wrong summaries) shows up in logs.

## Sources

- `README.md`, `doc/execution-semantics.md`, `doc/AGENT-LIFECYCLE.md`, `doc/memory-landscape.md`, `doc/agent-instruction-revisions.md`, `packages/adapters/AUTHORING.md`
- Schema: `packages/db/src/schema/{agents,goals,approvals,budget_policies,budget_incidents,cost_events,agent_wakeup_requests,agent_runtime_state,agent_task_sessions,completion_contracts,routines}.ts`
- Services: `server/src/services/{heartbeat,budgets,budget-reservations,approvals,routines,issue-liveness}.ts`, `server/src/services/heartbeat/{queue,recovery,retries}.ts`
