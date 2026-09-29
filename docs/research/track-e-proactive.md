# Track E — Proactive, background and ambient agency

*Research for Personal OS (Electron + FastAPI sidecar + SQLite, single local user, Mac that sleeps). Compiled 2026-09-29.*

---

## 0. The honest constraint, stated up front

Personal OS's agent lives inside an HTTP SSE response, in a process that only exists while the Electron app is open, on a laptop that sleeps. Every reliability promise in this track has to be written against that.

The reference implementation of exactly your situation is **Claude Code Desktop scheduled tasks**, and Anthropic's docs say the quiet part out loud: *"Tasks only run while the desktop app is running and your computer is awake. If your computer sleeps through a scheduled time, the run is skipped."* ([Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks))

And it's worse than "asleep" means colloquially. [claude-code#60144](https://github.com/anthropics/claude-code/issues/60144) is a laptop-sleep post-mortem worth reading in full:

| Time (BST) | Event |
|---|---|
| 07:00:14 | Scheduler sees `morning-briefing` due, applies 463s jitter → dispatch planned ~07:07:43 |
| 07:01:31 | macOS enters **maintenance sleep** (Apple Silicon, lid open, on AC) |
| 07:07:43 | Dispatch time — machine asleep |
| 07:17:07 | Dark wake; scheduler logs `Cleared stale pending dispatch for: morning-briefing` |
| 07:43 | User notices "skipped", clicks Run now manually |

Two root causes, both directly applicable to us:
1. The app held a `PreventUserIdleSystemSleep` power assertion, which does **not** block macOS maintenance sleep. Only `PreventSystemSleep` (what `caffeinate -s` holds) does.
2. The scheduler **discarded** the stale dispatch rather than replaying it. Real schedulers (cron, systemd timers, anacron) replay within a grace period.

**What this means for Personal OS.** Do not ship a UI that implies "this will run at 9am." Ship one that says *"runs at 9am when the app is open and the Mac is awake; otherwise it runs at the next wake, once, and tells you it was late."* That is both achievable and honest. Everything below assumes best-effort-local scheduling with visible skip accounting, not guaranteed delivery.

---

## 1. Scheduled and triggered agents

### 1.1 Landscape: what shipped products actually do

| System | Where it runs | Min interval | Permission model | Missed runs | Run history |
|---|---|---|---|---|---|
| [Claude Code `/loop`](https://code.claude.com/docs/en/scheduled-tasks) | In an open session | 1 min | Inherits session | **No catch-up**; fires once when idle | In-session; 7-day auto-expiry; 50 tasks max |
| [Claude Code Desktop tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks) | Your machine, app open | 1 min | **Per-task mode** (Manual/Auto/Skip) + per-task always-allow set | **Exactly one** catch-up for most recent missed time within 7 days | Full history incl. skipped runs *with reasons* |
| [Claude Code Routines (cloud)](https://code.claude.com/docs/en/routines) | Anthropic cloud | 1 hour | None — fully autonomous | N/A (machine-independent) | Full session per run; daily run cap |
| [ChatGPT tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt) | OpenAI cloud | 1 hour | N/A | N/A | Hub UI; 3/5/10/15 active tasks by plan |
| [Khoj automations](https://docs.khoj.dev/features/automations/) | Self-hostable server | cron | N/A | N/A | Dedicated conversation per automation; email delivery |
| [AnythingLLM scheduled jobs](https://docs.anythingllm.com/scheduled-jobs/overview) | Local server | cron | **Per-job tool allowlist** | New firing **dropped** if previous still running | 50 most recent runs per job, full trace |
| [OpenClaw heartbeat](https://docs.openclaw.ai/gateway/heartbeat) | Gateway daemon | 30m default (1h on OAuth) | Agent's own | Defers while queue busy | Separate cron jobs carry histories |
| [n8n](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger) | Server | seconds | N/A | Workflow must be published | Execution list |

### 1.2 Per-job scoping is the consensus design

Two independent products converged on the same answer, and it's the answer for us:

- **AnythingLLM**: each job carries *"the subset of agent skills, plugins, flows, and MCP servers the job is allowed to use when it runs."* ([overview](https://docs.anythingllm.com/scheduled-jobs/overview)) Jobs run *"in a dedicated background worker as an autonomous agent with auto-approved tool calls"* — auto-approval is safe precisely *because* the allowlist is narrow.
- **Claude Code Desktop**: each task has its own permission mode and its own persisted "Always allowed" set, reviewable and revocable from the task's detail page. The recommended flow is explicit: *"click **Run now** after creating a task, watch for permission prompts, and select 'always allow' for each one."*

**For Personal OS.** Your existing permission resolution chain is `chat override → project override → global setting → tool default`. Add a **job scope** that sits at the top: `job override → chat → project → global → default`. A job row gets `allowed_tools JSON`, `model`, `max_tool_rounds`, `token_budget`, `wall_timeout_s`. Anything outside the job's allowlist is *denied*, not asked — because there is nobody sitting there to ask. This converts your `external → ask` default (which would deadlock a 3am run for 600s and then auto-deny) into something coherent: at authoring time the user grants the job a specific external capability, and at run time it either has it or the run reports "wanted to send email, not granted."

This also fixes a latent bug in your current design: `_approvals` is an **in-memory dict** and approval auto-denies after 600s. A scheduled run that hits an `ask` tool at 03:00 will burn 10 minutes of wall clock and then silently fail. Scheduled runs must never enter the interactive approval path.

### 1.3 The cron implementation: APScheduler vs a croniter ticker

**Recommendation: a croniter ticker in the FastAPI lifespan, not APScheduler.** Reasoning:

- APScheduler's default `misfire_grace_time` is **1 second**. A job whose scheduled time plus grace has passed is discarded with outcome `missed_start_deadline` rather than running late. A laptop that wakes at 07:00:02 skips the 07:00 job. You can set a generous grace and `coalesce=True` (collapsing multiple missed occurrences into one run rather than a burst) — see the [APScheduler job docs](https://apscheduler.readthedocs.io/en/3.x/modules/job.html) and the long-running [missed-jobs issue #146](https://github.com/agronholm/apscheduler/issues/146) — but at that point you have configured away most of what APScheduler gives you and you still don't control the catch-up policy or get skip reasons.
- The behaviour you actually want is Claude Code Desktop's, and it is ~60 lines: a ticker that wakes every 30–60s, and on each tick, for each enabled job, computes `croniter(expr, last_fire_or_created_at).get_next()` and compares to now.

Concrete shape, against your SQLite:

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  name TEXT, description TEXT,
  prompt TEXT NOT NULL,
  cron TEXT,                       -- NULL = manual / event-only
  timezone TEXT DEFAULT 'local',
  enabled INTEGER DEFAULT 1,
  project_id TEXT,                 -- inherits project instructions/memory
  model TEXT, allowed_tools JSON,
  max_tool_rounds INTEGER DEFAULT 8,
  token_budget INTEGER, wall_timeout_s INTEGER DEFAULT 600,
  catchup_grace_s INTEGER DEFAULT 5400,   -- 90 min
  active_hours JSON,               -- {"start":"07:00","end":"22:00"}
  jitter_s INTEGER,                -- deterministic from hash(id)
  consecutive_failures INTEGER DEFAULT 0,
  quarantined_at TEXT,
  last_fire_at TEXT, next_fire_at TEXT,
  created_at TEXT
);

CREATE TABLE job_runs (
  id TEXT PRIMARY KEY, job_id TEXT,
  scheduled_for TEXT, started_at TEXT, finished_at TEXT,
  status TEXT,        -- queued|running|ok|failed|timeout|skipped|cancelled|awaiting_input
  skip_reason TEXT,   -- asleep|app_closed|overlap|quiet_hours|budget|quarantined|disabled
  late_by_s INTEGER,
  trace_id TEXT, tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
  summary TEXT, output JSON, error TEXT
);
```

**Catch-up semantics — copy Claude Code Desktop exactly.** On app start and on `powerMonitor` `resume`: for each job, walk cron occurrences backwards; if one or more were missed within the last 7 days, start **exactly one** run for the most recently missed time and discard everything older. A daily task that missed six days runs once. Record the older ones as `skipped` rows with `skip_reason` so the history shows the gap. Anthropic explicitly warns about the consequence, and you should surface the same warning in your job editor: *"A task scheduled for 9am might run at 11pm if your computer was asleep all day. If timing matters, add guardrails to the prompt itself."*

Two refinements over their design:
- **Grace period, not unlimited.** #60144 proposes: within a grace window (default 30 min there; 90 min is reasonable for a personal brief), run immediately and mark as delayed dispatch; beyond it, log the skip with its reason. Pass `late_by_s` into the prompt as context so the agent can self-censor ("this brief is 9 hours late; summarise what was missed rather than pretending it's morning").
- **Active hours.** OpenClaw has `activeHours` with timezone support on heartbeats. A job with `active_hours` that comes due outside them is skipped with `skip_reason=quiet_hours`, not deferred into your evening.

**Jitter.** Claude Code derives a deterministic offset from the task ID: recurring tasks fire up to 30 minutes after schedule, one-shots up to 90s early, *"the same task always gets the same offset."* You have one user and one API key, so you don't need jitter for API-fleet reasons — **skip it**. It is the direct cause of the 40-minute loss in #60144 (463s of jitter pushed dispatch past the sleep boundary). If you want staggering so three 9am jobs don't hammer LiteLLM at once, stagger by a few seconds, deterministically, and never more than 60s.

**Overlap.** AnythingLLM: *"A single job can only have one run in flight at any moment. If a scheduled time fires while the previous run continues, the new firing is dropped."* Their global cap is `SCHEDULED_JOB_MAX_CONCURRENT`, **default 1**, with `SCHEDULED_JOB_TIMEOUT_MS` **default 300000** (5 min). Take all three: single-flight per job, global concurrency 1 (you have one user and one LLM budget), hard wall timeout. Claude Code Desktop records overlap as an explicit skip reason too (*"the previous run was still in progress, or other scheduled tasks were already running"*).

### 1.4 Re-arming after sleep: Electron `powerMonitor`

[`powerMonitor`](https://www.electronjs.org/docs/latest/api/power-monitor) (main process only) gives you, on macOS: `suspend`, `resume`, `on-ac`/`on-battery`, `lock-screen`/`unlock-screen`, `user-did-become-active`/`user-did-resign-active`, `thermal-state-change`, plus `getSystemIdleTime()` and `getSystemIdleState(threshold)` returning `active | idle | locked | unknown`.

Wiring for Personal OS:
- On `resume` → POST `/scheduler/wake` to the sidecar → run the catch-up pass immediately rather than waiting up to 60s for the next tick. Do the same on `unlock-screen` and `user-did-become-active`.
- Known macOS flakiness: `suspend` has been reported as not firing, or firing on *wake* instead ([electron#24244](https://github.com/electron/electron/issues/24244), [#16297](https://github.com/electron/electron/issues/16297), [#44971](https://github.com/electron/electron/issues/44971)). **Never rely on `suspend` for correctness.** Derive "we were asleep" from a wall-clock gap: the ticker records `last_tick_at` every tick; if `now - last_tick_at > 3 × tick_interval`, you slept, regardless of what events fired. Clear any "suspended" flag on `resume` *and* on the next user prompt, as a belt-and-braces fallback.
- `getSystemIdleTime()` is your cheapest and best **interruptibility signal** (see §3): don't pop a proactive card at a moment the user is mid-keystroke; don't pop one when they've been idle 4 hours either — queue it for the next `active` transition.
- **Do not** silently hold a `PreventSystemSleep` assertion to make schedules reliable. Claude Code exposes it as an explicit opt-in ("Keep computer awake" in Settings) and that's the right call — it's a battery-life decision the user must make knowingly. Offer it per-app, off by default, with a line about battery.

### 1.5 Event triggers, not just time

Time is the easy half. Everything below is a trigger you can implement locally.

| Trigger | Mechanism on this stack | Notes |
|---|---|---|
| **New email matching a filter** | Poll Gmail `users.history.list` from your existing gmail tools, every 2–5 min while app open | Push via Pub/Sub needs a public HTTPS endpoint — [not viable for a local-only app](https://developers.google.com/workspace/gmail/api/guides/push). Note the cost of polling: *"Polling a Gmail inbox every 5 minutes generates 288 API calls per day per inbox"* — fine for one user, and you should use `historyId` incremental sync rather than re-listing. Google's own guidance is to keep a polling fallback anyway because push is unreliable. |
| **Calendar event approaching** | Local timer derived from the next N events, re-computed on each calendar sync | The highest-value trigger you have (§5) |
| **File changing** | `watchdog` (FSEvents on macOS) in the sidecar, debounced | Claude Code's Monitor tool does the analogous thing: *"watch a directory for file changes"*, streaming each event rather than polling ([tools reference](https://code.claude.com/docs/en/tools-reference)) |
| **Webhook** | A localhost FastAPI route; Electron can expose a `personalos://` URL scheme for external triggers | Claude Code Routines' API trigger is the model: POST with a bearer token, optional `text` payload |
| **Git push / repo event** | `watchdog` on `.git/refs` or a post-commit hook writing to the localhost endpoint | Cheap and local |
| **App/window focus, screen unlock** | `powerMonitor` + Electron `app` events | Good for "user just came back" batching |

**Critical security lesson from Routines on payload handling.** Claude Code wraps fire-time text in a `<routine-fire-payload>` block that *"labels it as untrusted data and tells Claude not to follow instructions inside it unless the routine's own prompt says to."* The job's saved prompt must opt in explicitly ("Investigate the alert described in the routine-fire-payload block"). Meanwhile the *saved prompt itself* is treated as an authorized assigned task, because it *"was stored ahead of time by an authorized session."*

This distinction is exactly right and you must replicate it. An email-triggered job must receive the email body inside an untrusted-data envelope. Otherwise "new email matching filter → agent with gmail_send in its allowlist" is a one-step prompt-injection-to-exfiltration pipeline. Anthropic notes that before v2.1.213 they framed the stored prompt *as* untrusted, and Claude would refuse to act on it — the fix was to separate the two trust levels, not to collapse them.

### 1.6 Heartbeat vs cron: the OpenClaw lesson, and it's about your model

OpenClaw runs both: a **heartbeat** (periodic agent turn in the main session, default 30 min, 1 hour under OAuth) and **cron jobs** (independent scheduled work items with their own payloads and histories). The heartbeat's contract is *"If nothing needs attention, reply NO_REPLY."* The docs are emphatic that recurring work belongs in cron jobs, not in the heartbeat prompt, and that the prompt forbids inferring tasks from chat history: *"Do not infer or repeat old tasks from prior chats."*

The cost math they publish is stark: each heartbeat is a full agent turn; `isolatedSession: true` *"cuts context from ~100K to ~2–5K tokens"*, and `lightContext: true` skips workspace bootstrap files.

**Then read [openclaw#159329](https://github.com/openclaw/openclaw/issues/159329).** During scheduled heartbeat runs, the model expressed silence by calling `exec("return \"NO_REPLY\";")` instead of replying in plain text. The framework didn't recognise the tool result as the sentinel, fed it back, and the model repeated the identical call:

- **~898 identical tool calls**
- **≥6 independent heartbeat runs**
- **~6.5 hours** (00:36–06:56 GMT+8, 2026-09-27)
- **~80k input tokens per iteration**, ~150 RMB burned, ending in HTTP 429 billing errors

The model was **moonshot/kimi-k3** — your default chat model. This is not an abstract risk for Personal OS; it is a documented failure of your exact model on your exact pattern, unattended, overnight.

Three guardrails, all cheap, all mandatory before you ship any unattended loop:
1. **Repetition circuit breaker.** Abort a run that issues the same `(tool_name, args)` ≥5 consecutive times. Their own recommended fix.
2. **Sentinel recognition in tool results**, not just message text — or explicitly reject a sentinel that arrives via a tool.
3. **Hard per-run budget** in tokens *and* dollars *and* wall clock, enforced in `_chat_stream`'s loop, not just `maxToolRounds`. 898 calls fit inside no sane round cap, but they also fit inside "8 rounds" repeated across 6 runs — the budget must be per-run *and* per-job-per-day.

**Do you want a heartbeat at all?** Given kimi-k3, the answer for v1 is **no**. Build explicit cron jobs and event triggers; skip the "wake up every 30 minutes and think about whether anything needs attention" loop. It is the highest-cost, lowest-precision, hardest-to-debug form of proactivity, and OpenClaw's own docs push users away from it toward cron jobs. If you later add one, make it `isolatedSession`-equivalent (fresh context, cheap model — `deepseek-v4-flash`, not kimi-k3), `activeHours`-gated, and NO_REPLY-by-default.

---

## 2. The agent inbox pattern

### 2.1 The canonical schema

LangChain's [Agent Inbox](https://github.com/langchain-ai/agent-inbox) is a Gmail-shaped UI over paused agent runs. Its data model is three small objects and is worth copying wholesale:

- **`ActionRequest`**: `{ action, args }` — `action` renders as the header, `args` are the proposed tool arguments.
- **`HumanInterruptConfig`**: `{ allow_ignore, allow_respond, allow_edit, allow_accept }` — per-interrupt booleans controlling which buttons appear.
- **`HumanInterrupt`**: `{ action_request, config, description }` — `description` is markdown giving context and telling the user how to respond.

Four response types: **accept** (run as proposed), **edit** (modify args, then run), **response** (freeform text back to the agent), **ignore** (reject, args null).

[LangChain's HITL middleware](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) adds the per-tool policy layer:

```python
interrupt_on={
    "write_file": True,                                      # all decisions allowed
    "execute_sql": {"allowed_decisions": ["approve", "reject"]},
    "read_data": False,                                      # auto-approve
}
```

...plus a `when` predicate for conditional interrupts based on the actual arguments — "interrupt only if the recipient is outside my domain", "interrupt only if the amount > $50". The four decisions are `approve | edit | reject | respond`, with a useful semantic distinction: *"Use `reject` when the human is denying the requested action. Use `respond` only when the human is acting as the tool."*

### 2.2 Durable pause is the whole trick

The mechanic that makes this work: the run pauses on `interrupt()`, graph state is written to a **checkpointer** keyed by `thread_id`, and resumption is `Command(resume={"decisions": [...]})` against the same `thread_id`. Crucially: *"If the entire system restarts between pause and resume, the persistent checkpointer retains the paused graph state... The agent continues from exactly where it paused, avoiding re-execution of prior steps."* Without a checkpointer, resuming is impossible.

**For Personal OS this is the single most important architectural change in this whole track.** Your approvals live in an in-memory dict, tied to an open SSE connection, auto-denying after 600s. That design cannot support background work at all: a job that runs at 03:00 and wants to send an email has nobody to ask and nowhere to wait.

Replace it with a durable inbox. Minimum viable version, and it's genuinely small:

```sql
CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  run_id TEXT, job_id TEXT, chat_id TEXT,
  kind TEXT,            -- tool_approval | draft | suggestion | question | automation_proposal
  action TEXT,          -- e.g. "gmail_send"
  args JSON,            -- editable
  title TEXT, description TEXT,  -- markdown, rendered on the card
  allow JSON,           -- {accept:true, edit:true, reject:true, respond:false}
  status TEXT,          -- pending | accepted | edited | rejected | expired | superseded
  resolution JSON,      -- what the user actually chose/edited
  created_at TEXT, expires_at TEXT, resolved_at TEXT
);
```

And a **durable run log** so an accepted proposal can resume the run rather than restart it. The cheapest credible implementation on SQLite is the step-log/replay pattern: [Durable LLM workflows on SQLite](https://www.pedroalonso.net/blog/durable-llm-workflows-sqlite/) shows a ~190-line engine with one table —

```sql
CREATE TABLE steps (run_id TEXT, name TEXT, status TEXT, output TEXT,
                    attempts INTEGER, PRIMARY KEY (run_id, name));
```

— where each step's result is committed before advancing, and on re-run finished steps replay from the log instead of re-executing. Measured: ~1,000 durable commits/sec with `synchronous=FULL` (real fsync per commit), ~29,000/sec with `synchronous=NORMAL` (loses the last transaction on power loss); WAL mode, P50 ~0.95ms, P95 3.8ms at 32 workers. Their crash test (`os._exit(137)` mid-run) recovered with **~50% token savings** from replay. Their own "graduate to Temporal/Inngest" triggers are multi-node workers, day-scale durable timers, and large fan-out — **none of which apply to a single-user local app**. SQLite is the right answer here and you can say so with numbers.

Note the article's HITL pattern maps perfectly onto your approval card: *"a step raises `Paused` if its signal isn't in the table; the engine stops cleanly and resumes when the signal row is added."* An approval is a row. A restart is survivable. A 600-second timeout becomes unnecessary.

### 2.3 Drafts as the default output of background work

Superhuman's [Auto Drafts 2.0](https://blog.superhuman.com/auto-drafts-2-0/) is the strongest real-world evidence that *proposals* beat *actions*. The system writes a full reply for every message that needs one, in your voice, pulling from inbox, calendar and web, and puts it in the inbox and Drafts folder. Published numbers:

- **60% of Auto Drafts are sent unedited**
- **40% are sent within 1 day**
- **~9 minutes saved per draft**

Note what the product does *not* do: it never sends. The entire value is captured by removing the cold start while leaving the send button to the human. [TechCrunch's review](https://techcrunch.com/2026/07/14/superhumans-new-auto-draft-feature-almost-makes-me-like-ai-replies/) is titled "almost makes me like AI replies" — the "almost" is doing real work, and the reason it's positive at all is that a bad draft costs one keystroke to delete.

**For Personal OS.** Your permission model already says external actions must ask. Extend the principle: **background runs may not perform `external`-class actions at all — they may only produce proposals.** A 3am email-triage job produces N draft replies in the inbox; the user accepts them at 9am with one click each. This is strictly better than an approval that blocks a dead SSE stream, and it's the same shape as Gmail drafts, which you already have a tool for (`gmail_draft`).

### 2.4 What this looks like on the Today dashboard

You already have a Today dashboard with a daily recap and a manual "Brief me". The inbox slots in above them:

```
┌─ Today ─────────────────────────────────────────┐
│ ▸ Needs you (3)            ← proposals, pending  │
│   ✉ Reply to Sam re: contract      [Send][Edit][✕]│
│   ⚠ Job "inbox triage" wants gmail_send  [Grant] │
│   ? "Which invoice did you mean?"     [Answer]   │
│ ▸ While you were away (2)  ← completed runs      │
│   ✓ Morning brief (ran 11:04, 4h late)    [Open] │
│   ✗ Repo watch — failed, 2nd time     [Why?][⏸]  │
│ ▸ Recap / Brief me  ← what you have today        │
└──────────────────────────────────────────────────┘
```

Design rules drawn from the sources:
- **Group by status, not by time.** Agent Inbox groups threads by interrupted/idle/busy/error. "Needs you" must never mix with "FYI".
- **Skipped runs are first-class.** Claude Code Desktop shows skipped runs in history and *"Hover a skipped entry to see why: your computer was asleep, the previous run was still in progress, or other scheduled tasks were already running."* This is the single feature that converts "unreliable" into "trustworthy": the user forgives a miss they can see and understand.
- **Status ≠ success.** Anthropic's own warning on Routines: *"A green status in the run list means the session started and exited without an infrastructure error. It does not mean the task in your prompt succeeded."* Your run card should show the agent's own one-line self-assessment, not just an exit code.
- **Cap the inbox.** ChatGPT Pulse deliberately produces **5–10 cards once a day** and stops. An unbounded queue is a second inbox, and people already have one.

---

## 3. Proactivity people actually tolerate

### 3.1 The numbers you should design against

**Codellaborator (CHI '25, [arXiv 2502.18658](https://arxiv.org/html/2502.18658v4) / [ACM](https://dl.acm.org/doi/10.1145/3706598.3713357))** — N=18, within-subject, 1,004 interaction episodes, 398 proactivity instances:

| Outcome | Share |
|---|---|
| Effective engagement | **53.3%** (212) |
| Ignored | **34.7%** (138) |
| Actively disruptive | **12.1%** (48) |

Perceived disruption (1–7): PromptOnly (reactive) **1.56**, Codellaborator **3.78**, CodeGhost (most proactive) **4.61**. Proactivity did buy real speed — interpretation time dropped from **34.5s** to **~19s** (−44%) — but **6 of 18** participants reported decreased code ownership.

Trigger quality varied enormously, and the ranking is transferable:

| Trigger heuristic | Engagement |
|---|---|
| Multi-line change | **73.1%** |
| User-written comment | **69.2%** |
| Program execution | **66.7%** |
| Code block completion | ~50% ignored |

Read that as: **triggers tied to a completed, intentional unit of user work do ~1.5× better than triggers tied to mechanical state changes.**

**ProMemAssist ([arXiv 2507.21378](https://arxiv.org/html/2507.21378))** — N=12, within-subject, working-memory-based timing vs prompt-engineered LLM baseline. The system scores each candidate message on assistance value minus interruption cost and delivers only above a **0.75 utility threshold**:

| | ProMemAssist | Baseline |
|---|---|---|
| Candidates generated | 218 | 332 |
| Delivered | 130 | 332 (all) |
| Deferred / discarded | 31 / 57 | 0 / 0 |
| **Positive response rate** | **24.6%** | **9.34%** |
| Frustration (1–7) | **2.32** | 3.14 (p<0.05) |
| Perceived interruption | 4.45 | 5.18 (n.s.) |

The headline: **delivering 39% of what you could, at the right moments, produced 2.6× the positive response rate.** Their design implication is stated flatly — *"Fewer, better-timed messages yield higher engagement than high-volume assistance"*, and *"prioritize avoiding false positives (unnecessary interruptions) over false negatives."*

**Agentic coding proactivity survey ([arXiv 2605.06717](https://arxiv.org/html/2605.06717v1))** reports two more transferable figures: state-aware notifications reached **90% preference vs 47%** for a persistent variant, and feedback-informed timing lifted acceptance from **4.9% to 18.6%**. It also distinguishes autonomy ("can act without supervision") from proactivity ("decides *whether and when* to act without an explicit prompt"), and offers a three-level ladder — L1 reactive, L2 scheduled/triggered, **L3 situation-aware: continuously monitors, weighs expected benefit against interruption cost, and treats silence as an explicit action.** Their four insight actions are a good taxonomy for your card types: **Notify, Question, Draft, Stay Silent.** Their line for you: *"showing insights matters less than deciding when not to show them."*

### 3.2 Task boundaries and the deferral machinery

The classical HCI result: interruptions at **coarse breakpoints** (between large meaningful units of work) cost roughly as much as no interruption at all, while interruptions at **fine breakpoints** produce longer resumption lag, higher self-reported mental workload, and more frustration ([Iqbal & Horvitz, CHI 2007](http://erichorvitz.com/CHI_2007_Iqbal_Horvitz.pdf); [Iqbal & Bailey CHI 2005](https://www.interruptions.net/literature/Iqbal-CHI05-p1489-iqbal.pdf); [Iqbal & Bailey CHI 2008](https://interruptions.net/literature/Iqbal-CHI08.pdf)). Notifications delivered at predicted "best" moments consistently caused less resumption lag and annoyance and *"fostered more social attribution"* — i.e. the assistant was perceived as more considerate, not just less annoying.

The decision-theoretic frame ([Horvitz, "Learning and Reasoning about Interruption"](http://erichorvitz.com/iw.pdf); [Attention-Sensitive Alerting](https://arxiv.org/pdf/1301.6707)) is three quantities: expected benefit of acting, expected cost of interrupting, and the benefit of leaving the user in control. **Bounded deferral** ([Horvitz et al.](http://erichorvitz.com/Bounded_Deferral.pdf)) is the operational version and it is cheap to implement: hold a notification until the user reaches a low-cost state *or until a maximum deferral time elapses*, whichever comes first. That bound is what makes deferral safe — nothing is lost forever, it's just late.

**Ceiling on how clever you can be.** Sensor-based interruptibility models top out around **71.8–78%** accuracy ([Fogarty et al., CHI 2003/2005](https://homes.cs.washington.edu/~jfogarty/publications/chi2003.pdf)), against a **58.5%** base rate for naive systems. Computer-interaction data beat biometrics (**74.8% vs 68.3%**, combined **75.7%**). For calibration: **human observers** only manage **76.9%**. Fogarty's own caution is the design principle: *"it will be important for applications to negotiate entry into interruptions, rather than treating an interruptibility estimate as if it provides absolute guidance."*

So: don't build an ML interruptibility model. Use `getSystemIdleTime()`, app focus, and calendar state as a three-rule heuristic, defer with a bound, and always *negotiate* — a badge, not a modal.

And don't use an LLM for the wake decision either. [arXiv 2605.30152](https://arxiv.org/abs/2605.30152) (Liu et al., May 2026) argues that proactive agents wastefully *"read user activity as text and call an LLM on every event"*, when the signal is natively a structured event stream. Using a small temporal-graph model as the trigger encoder and reserving the LLM for the user-facing sentence: **F1 +16.7 average across 14 backbones (up to +46.0), 12–83× faster on a consumer laptop, ~220 MiB on-device.** You don't need their model — you need their conclusion: **triggering is a cheap deterministic decision; generation is the expensive LLM step; do not merge them.** Concretely, never call kimi-k3 to decide whether to speak.

### 3.3 Notification budget

Baselines: ~**63.5–90 notifications/day** on average ([APA 2023 via multiple summaries](https://www.psypost.org/new-psychology-research-reveals-the-cognitive-cost-of-smartphone-notifications/); [Pielot et al., MobileHCI 2014](https://pielot.org/pubs/Pielot2014-MobileHCI-Notifications.pdf)), with some samples >100 and student samples >400. Frequency of checking and notification volume predict distraction better than total screen time.

**Budget for Personal OS: ≤3 OS-level notifications per day, hard cap.** Everything else is a silent badge on the Today dashboard. A local-first personal app has an enormous advantage here — the dashboard is a *pull* surface the user visits anyway, so almost nothing needs to *push*.

### 3.4 The negative cases

**Dot / New Computer.** A proactive, memory-rich AI companion from a former Apple designer; [shut down ~4 months after launch](https://techcrunch.com/). The founders cited diverged visions, but Appfigures counted roughly **24,500 lifetime iOS downloads** against the founders' claim of "hundreds of thousands" ([futurism](https://futurism.com/ai-dot-companion-controversy), [aicerts summary](https://www.aicerts.ai/news/dot-app-shutdown-startup-failure-signals-ai-companion-risks/)). The lesson isn't "proactive apps die" — it's that *proactive-and-charming* without a job to do has no retention floor. Personal OS's advantage is that its proactivity attaches to concrete artefacts the user already maintains: todos, calendar, boards, documents. Keep it there.

**Motion.** The canonical "scheduler people stop trusting". Reviews converge on the same complaint: the AI *"can end up reshuffling tasks in ways that don't feel discerning, even with priorities and time windows configured"*, and *"can 'shuffle' tasks in ways that feel unclear unless you invest in rules and guardrails"* ([efficient.app](https://efficient.app/apps/motion), [saner.ai](https://www.saner.ai/blogs/motion-reviews), [Morgen](https://www.morgen.so/blog-posts/akiflow-vs-motion)). Users also report days packed too tightly to absorb the unplanned.

Two distinct failures there, both avoidable: **silent mutation of state the user owns**, and **inscrutability**. If Personal OS ever auto-schedules a todo or moves a kanban card, it must (a) propose rather than apply, or (b) apply with a one-line reason and a one-click undo backed by a write journal. Never silently.

**Windows Recall.** The highest-profile "ambient capture" backlash: announced always-on, shipped **opt-in and off by default** after security researchers demonstrated extraction tooling ("Total Recall"), with Windows Hello + proof-of-presence gating added, and *further* exploits demonstrated after the redesign ([Computerworld](https://www.computerworld.com/article/2140187/microsoft-makes-windows-recall-opt-in-after-privacy-security-backlash.html), [The Record](https://therecord.media/microsoft-reverses-course-recall-opt-in), [SecurityWeek](https://www.securityweek.com/microsoft-bows-to-public-pressure-disables-controversial-windows-recall-by-default/)). Apple's Watch "Live Rewind" drew the same reaction in 2026 ([TechCrunch](https://techcrunch.com/2026/09/09/apple-watchs-new-ai-features-are-normalizing-the-idea-that-technology-is-always-listening/)). Even with strong local-only claims, continuous capture reads as surveillance to a large fraction of users.

---

## 4. Long-running and asynchronous agent work

### 4.1 Progress to a user who closed the window

**OpenAI background mode** ([docs](https://developers.openai.com/api/docs/guides/background)) is the cleanest primitive design: `background: true` starts the response asynchronously; the client polls `GET` while status is `queued`/`in_progress`; `background: true, stream: true` streams immediately and each event carries a **`sequence_number`** so a dropped connection resumes with `starting_after=<last seq>`. Background response data is *"temporarily stored to disk for roughly 10 minutes to enable asynchronous execution and polling."*

**For Personal OS, adopt the sequence-number pattern directly.** Today, `_chat_stream()` couples the agent loop to one SSE connection; when the renderer navigates away or the window closes, the run dies. Decouple:

1. `POST /runs` → creates a `run` row, returns `run_id`; the agent loop runs in an asyncio task owned by the app, not the request.
2. Every event the loop produces (token delta, tool_event, trace span, approval request) is appended to a `run_events` table with a monotonic `seq`.
3. `GET /runs/{id}/stream?after=<seq>` is a thin SSE tail over that table. Reconnect is free, replay is free, and the Context panel / trace viewer become queries over `run_events` instead of ephemeral state.

This one change buys you: survival across window close, survival across app restart (given step-log replay from §2.2), the agent inbox, and honest run history — all from the same table.

**Codex's cautionary tale.** [openai/codex#45264](https://github.com/openai/codex/issues/45264) catalogues what users hate about opaque long runs: no visibility into *"shell commands, command output, files being inspected, tests being run, tool calls, process status, errors, retries"*; a backgrounded long-running script where *"the usable process state was effectively lost"* with no PID or stdout to reconnect to; no live diff; no visibility into context utilisation or whether compaction occurred; and no per-task usage attribution — *"plan-wide allowance decrease without seeing whether the individual task followed an efficient path."* [#42880](https://github.com/openai/codex/issues/42880) reports long-running tasks losing state and overstating background progress. There is even an open [feature request for a native OS notification when a long run finishes](https://github.com/openai/codex/issues/4998).

You are *ahead* here and should press the advantage: you already emit `tool_events` and a span `trace` with token/cost accounting. Persist them per run and render them as a live timeline. **Per-run cost is a first-class UI element, not a debug panel** — it's the thing Codex users are asking for and not getting.

**Streaming vs polling vs notification** — the rule that falls out: **stream while the window is open and focused; poll cheaply when it's open but hidden; notify only on terminal state, and only if the run took longer than a threshold** (Codex's own feature request suggests >5 min) **or needs a decision.** Never notify for a successful short run.

### 4.2 Cost control on long runs

Devin publishes the most concrete guardrails: in-product warnings that long sessions degrade appear around **~2.5 hours or 10 ACUs**; an idle session *"will typically sleep after about 0.1 ACUs of inactivity and stop charging"*; and a per-automation **Consumption tab** showing ACUs used by sessions that automation started ([Devin docs](https://docs.devin.ai/release-notes/overview), [Session Insights](https://docs.devin.ai/product-guides/session-insights)).

Claude Code Routines gate on a **daily run cap per account** on top of normal subscription limits, and reject additional runs when it's hit unless usage credits are on.

**For Personal OS**, using `usage.py`:
- Per-run: `token_budget`, `cost_ceiling_usd`, `wall_timeout_s`, `max_tool_rounds`. Exceeding any → terminate, mark `status=budget`, and *report what was achieved* rather than erroring blankly.
- Per-job-per-day and per-app-per-day ceilings. A runaway (see §1.6) should hit the daily ceiling within one run, not six.
- Show cumulative cost per job on its detail page. The OpenClaw incident cost ~150 RMB precisely because nobody was watching the meter overnight.
- Long runs should use `deepseek-v4-flash` for scan/filter steps and escalate to kimi-k3 only for the small synthesis step. This mirrors both the TGL-trigger paper's split and OpenClaw's `isolatedSession` advice, and it's where most of the savings are.

### 4.3 Resumption after the app quits

Three tiers, in increasing order of effort:
1. **Mark and report.** On startup, any run left in `running` is marked `interrupted` and shown on Today as "interrupted when the app closed." *Table stakes; do this first.*
2. **Replay from the step log.** With `steps(run_id, name, status, output)` and named steps, a re-run replays completed steps for free — measured ~50% token savings on a 5-step agent after a hard kill.
3. **Parked-run resume.** A run that produced a proposal sits at `awaiting_input` indefinitely; accepting the proposal writes the signal row and the engine resumes from that step. This is the same mechanism as (2) and costs almost nothing extra once (2) exists.

---

## 5. Ambient context and triggers

### 5.1 The ranking: useful → creepy

Ordered by (value × tolerance) for a local personal app:

1. **Calendar proximity — the best trigger you have.** [Granola](https://docs.granola.ai/help-center/taking-notes/pre-meeting-briefs) generates pre-meeting briefs *"overnight"* and surfaces them when you open the note, as **2–3 bullets at the top of the chat panel**, expandable. Critically: *"Briefs are only generated for meetings with people outside your own organization"* — a deliberate precision filter that suppresses the majority of low-value cases. They also fire a notification **one minute before** a scheduled meeting. Everything about this shape is correct: precomputed cheaply while idle, terse by default, scoped to cases that reliably matter, surfaced where the user is already looking.
2. **Email arrival matching a filter.** High value, but only with a *narrow* filter and **draft-only** output (§2.3). "New email from anyone" is a notification firehose; "new email from someone on my people-graph that contains a question mark and no reply yet" is a proposal generator.
3. **File changes.** Genuinely useful for a documents-and-knowledge app: a changed file in a watched folder → re-index → optionally extract memories/graph triples with `deepseek-v4-flash`. Silent by default; no card unless something noteworthy is found.
4. **Focus / idle state.** `powerMonitor.getSystemIdleState()` and `getSystemIdleTime()` are your gate, not your trigger. macOS Focus modes are [not cleanly exposed to third-party apps](https://developer.apple.com/documentation/foundation/nsbackgroundactivityscheduler) — the hacks people use (checking whether the Focus icon is in the menu bar) are unreliable. Use idle time + a user-set quiet-hours window instead; it's more predictable and the user configured it themselves.
5. **Meeting start/end.** Cheap from calendar state; "meeting just ended" is an excellent coarse breakpoint for delivering anything deferred (§3.2).
6. **Screen / selection context.** A global hotkey that captures the current selection is great (it's *pull*). Continuous screen capture is not. [screenpipe](https://github.com/screenpipe/screenpipe) does the local-first version properly — Rust, all data on device, works with local models, MIT-ish source-available — and is a fine *optional integration* if someone already runs it. Building your own is the Recall trap.
7. **Location.** Skip. No plausible value for a Mac desktop app; all cost.

### 5.2 The creepiness line, evidenced

The pattern across Recall, Live Rewind and Rewind AI is consistent: **continuous, undirected capture triggers backlash even when the vendor's local-only/encryption claims are strong**, and the backlash is led by security researchers demonstrating extraction. Microsoft's climbdown to opt-in-by-default plus biometric gating is the template for what "acceptable" eventually looked like ([Computerworld](https://www.computerworld.com/article/2140187/microsoft-makes-windows-recall-opt-in-after-privacy-security-backlash.html)).

By contrast, Granola's calendar-triggered briefs and Superhuman's inbox-triggered drafts generate no comparable reaction, despite reading your calendar and your entire mailbox. The difference isn't the volume of data — it's that **capture is bounded by an event the user already knows about**. "You have a meeting with Sam at 2; here's what you last discussed" is legible. "Here's what was on your screen at 11:04" is not.

**Design rule for Personal OS:** every ambient trigger must be nameable in one sentence the user would recognise as a thing they already do. If you can't write that sentence, don't build the trigger.

---

## 6. Agent-authored automation

### 6.1 "Every Monday, do X" → a durable rule

The natural-language→rule compile is well-trodden: the IF-trigger-THEN-action paradigm from IFTTT/Zapier/Home Assistant, now with an LLM doing the parsing ([End-User Customization of Trigger-Action Rules Through Fine-Tuned LLMs](https://link.springer.com/chapter/10.1007/978-3-031-95452-8_2); [FARM](https://arxiv.org/pdf/2601.15687); [authoring context-aware reminders in everyday language](https://arxiv.org/pdf/2605.23085)). The key structural insight from that literature, and from the in-car policy-compilation work ([arXiv 2608.23282](https://arxiv.org/pdf/2608.23282)), is that **the LLM compiles at authoring time only**; at run time a deterministic engine evaluates the rule. Safety requirements include at-least-once delivery and durable dedup so that *"signal delivery and intent execution survive runtime restart without duplicate task creation."*

Three products already do the conversational-authoring UX and you can copy the flow verbatim:
- **Claude Code**: *"set up a daily code review that runs every morning at 9am"* creates a recurring task; *"remind me at 3pm tomorrow to check the deploy"* creates a one-time task that disables itself after firing. `/schedule` walks through schedule, scope and prompt conversationally, then **confirms the absolute resolved timestamp** before saving.
- **ChatGPT tasks**: created from a conversation, managed in a hub.
- **AnythingLLM**: a cron builder with dropdowns *plus* a raw-cron field with validation — both modes, because the dropdowns cover 90% and the raw field covers the rest.

**For Personal OS**, a single new tool `automation_propose(name, prompt, cron|trigger, allowed_tools, model)` that does **not** create the job but creates a **proposal card** in the inbox. The user sees the resolved human-readable schedule ("every Monday at 09:00 local — next run Mon 5 Oct 09:00"), the tool allowlist, the model, and the budget, and clicks Create. Never let the agent create a live automation in one step.

### 6.2 Proposing an automation after noticing repetition

This is a genuinely differentiated feature and it's cheap: you already log every chat and every tool call. A nightly `deepseek-v4-flash` pass over the last 30 days looking for near-duplicate user requests (same intent, ≥3 occurrences, roughly periodic) can emit one `automation_proposal` card. Gate it hard: at most one such proposal per week; dismissal suppresses that cluster for 90 days. Per §3.1, a wrong proactive suggestion is expensive and this is exactly the class of suggestion that feels presumptuous when wrong.

### 6.3 Governance — the OpenClaw lessons

OpenClaw's 2026 security crisis is the most instructive failure in this whole track ([Trend Micro](https://www.trendmicro.com/en_us/research/26/b/what-openclaw-reveals-about-agentic-assistants.html), [Adversa](https://adversa.ai/blog/openclaw-security-101-vulnerabilities-hardening-2026/), [Backslash](https://www.backslash.security/blog/openclaw-security-risks-explained), [ClawTrust](https://clawtrust.ai/blog/openclaw-security-341-malicious-skills-and-what-we-do-about-it)):

- **CVE-2026-25253** (RCE) alongside a supply-chain campaign distributing **335–341 malicious skills** via the public ClawHub marketplace, using professional docs and innocuous names like `solana-wallet-tracker`, which then installed keyloggers on Windows and Atomic Stealer on macOS.
- Because *"skills execute inside the operational context of the agent, malicious extensions may inherit access to credentials, filesystem operations, browser sessions, and connected APIs already trusted by the agent."*
- **Config mutation as escalation**: with a stolen gateway token an attacker could modify *"configuration (including sandbox settings and tool policies), invoking privileged actions"*. Mis-scoped tools let the bot expand its own reach.

Direct rules for Personal OS:
1. **The agent must never be able to edit its own permissions, tool allowlists, budgets, or a job's `allowed_tools`.** Those fields are writable only from the UI, by the human. If you add an `automation_*` tool family, its schema must not contain `allowed_tools` as an agent-settable field — the *user* picks it on the proposal card.
2. **Quarantine after N failures.** `consecutive_failures >= 3` → set `quarantined_at`, disable the job, put one card on Today: "'Repo watch' failed 3 times in a row and has been paused. [Why] [Resume] [Delete]". Claude Code Routines does the time-based version of this: a routine with a broken GitHub connection skips runs for up to 72 hours, then **turns itself off**.
3. **Budget caps at every level** (§4.2), enforced in the loop.
4. **Review before first run.** Claude Code's advice — Run now, watch the prompts, grant always-allow — is the human-supervised first execution. Make it a required step in the job creation flow: a newly created job's first run is always manual and always in Manual permission mode.
5. **Loop guard** (§1.6): abort on ≥5 identical consecutive tool calls.
6. **No skills marketplace.** If you ever add importable procedures/skills, they are code-equivalent and must be treated as such. OpenClaw's marketplace is the entire attack surface.

One more nuance worth stealing: Claude Code Desktop lets a running task *modify its own schedule or prompt* via an `update_scheduled_task` tool — e.g. rescheduling a review earlier when it sees a release branch. That's a reasonable, narrowly-scoped self-modification because it touches **cadence, not capability**. If you allow any self-modification, draw the line exactly there: schedule yes, permissions never.

---

## 7. Multi-device and continuity

Short answer for this app: **mostly don't.**

The case against building it:
- You are a local-first single-user Mac app with no server. Pushing to a phone requires a hosted relay (APNs/ntfy/Pushover), which introduces an account, a network dependency, and a place where your data lives that isn't the user's Mac. That is a direct contradiction of the product's premise.
- The work can't run when the laptop is asleep anyway (§0), so a phone notification would mostly say "your Mac was asleep."
- Claude Code solves this by having a *cloud* tier (Routines) and being explicit that the local tier requires the machine on. You don't have a cloud tier, and adding one is a different product.

The case for a narrow version:
- The only genuinely useful cross-device act is **acting on a decision from elsewhere** — approving a draft while away. But the run is parked durably (§2.2), so it will still be there when the user opens the lid. The cost of waiting is near zero for a personal life-OS.

**Recommendation.** Ship two cheap things and stop: (a) native macOS notifications from Electron with a hard ≤3/day budget, clicking through to the relevant Today card; (b) an **optional, user-supplied** outbound webhook per job (ntfy topic, Pushover key, or a shell command) so a user who *wants* phone alerts can wire their own, with no account and no hosted component from you. This is the same escape hatch Khoj uses (email delivery via user-configured Resend) and it keeps the privacy story intact.

---

## 8. Proposed features

Ordered roughly by dependency. "Effort" assumes the existing FastAPI/SQLite/Electron stack.

| # | Feature | Effort | Why it matters here |
|---|---|---|---|
| 1 | **Durable runs**: `runs` + `run_events` tables, agent loop decoupled from the SSE request, `GET /runs/{id}/stream?after=seq` | **M** | Prerequisite for literally everything else in this track; also fixes "close the window, lose the turn" today |
| 2 | **Step log + replay** (`steps` table, named steps, commit-before-advance) | **S** | ~190 lines on SQLite; crash-safe runs and ~50% token savings on resume; kills the 600s in-memory approval timeout |
| 3 | **Durable approval → proposals table**, replacing the in-memory `_approvals` dict | **M** | The current design cannot survive a restart or serve a background run; this is the single biggest blocker to any unattended work |
| 4 | **Agent Inbox on Today**: "Needs you" / "While you were away", with accept / edit / reject / respond on each card | **M** | The reviewable-proposal pattern is what makes background work tolerable (Superhuman: 60% sent unedited) |
| 5 | **Jobs table + croniter ticker in the FastAPI lifespan**, with per-job model / tool allowlist / budget / timeout | **M** | Per-job scoping is the consensus design (AnythingLLM, Claude Code Desktop) and it's what makes auto-approval safe at 3am |
| 6 | **Run history with skip reasons** (`asleep` / `app_closed` / `overlap` / `quiet_hours` / `budget` / `quarantined`) | **S** | The feature that converts "unreliable scheduler" into "honest scheduler"; hover-to-see-why is directly from Claude Code Desktop |
| 7 | **Catch-up on wake**: one run for the most recent missed fire within 7 days, grace window, `late_by_s` injected into the prompt | **S** | Directly answers the laptop-sleep problem; avoids the six-runs-at-once burst |
| 8 | **`powerMonitor` wake hook** + wall-clock-gap sleep detection (never trust `suspend`) | **S** | Re-arms the scheduler in seconds instead of up to a minute; macOS `suspend` is documented-flaky |
| 9 | **Per-run budget enforcement** (tokens, USD, wall clock) + repetition circuit breaker (≥5 identical tool calls) | **S** | kimi-k3 burned ~898 calls / ~150 RMB unattended in a documented incident on exactly this pattern |
| 10 | **Background runs are proposal-only**: `external`-class tools produce drafts/cards, never act | **S** | Makes "tools on by default" and "external must ask" coherent when nobody is there to ask |
| 11 | **Morning brief as a real job** (replacing the manual "Brief me" button), calendar + inbox + todos, ≤10 cards | **S** | ChatGPT Pulse's 5–10-cards-once-a-day shape; you already have the recap, this makes it arrive on its own |
| 12 | **Pre-meeting brief trigger**: calendar proximity, external attendees only, 2–3 bullets, precomputed on idle | **M** | Granola's exact shape; highest value-per-interruption trigger available on this stack |
| 13 | **Quiet hours + notification budget** (≤3 OS notifications/day, bounded deferral to the next active moment) | **S** | Baseline is 65–90 notifications/day already; ProMemAssist got 2.6× engagement by delivering 39% of candidates |
| 14 | **Conversational automation authoring** → `automation_propose` emits a proposal card showing resolved schedule, tools, model, budget; first run is manual | **M** | "Every Monday do X" without letting the agent grant itself capabilities |
| 15 | **Quarantine after 3 consecutive failures** + one explanatory card | **S** | Claude Code turns routines off after 72h of failure; failing silently forever is worse than stopping |
| 16 | **Email-arrival trigger** (narrow filter, `historyId` incremental poll, draft-only output, payload wrapped as untrusted) | **M** | The untrusted-payload envelope is mandatory — otherwise this is a prompt-injection-to-exfiltration path |
| 17 | **File-watch trigger** (`watchdog`/FSEvents, debounced, silent re-index with the cheap model) | **S** | Natural fit for the documents + graph half of the product; zero interruption cost |
| 18 | **Repeated-request detector** → at most one automation proposal per week | **M** | Differentiated and cheap given your existing logs; must be rate-limited because wrong suggestions are expensive |
| 19 | **Per-run cost surfaced in the UI** (job detail page shows cumulative spend) | **S** | The thing Codex users are loudly asking for and not getting; you already have `usage.py` |
| 20 | **Optional user-supplied webhook per job** (ntfy/Pushover/shell) instead of building push | **S** | Gives phone continuity to people who want it without a hosted component or an account |
| 21 | Optional **"Keep Mac awake while jobs are pending"** toggle, off by default, with a battery warning | **S** | `PreventUserIdleSystemSleep` is not enough on Apple Silicon; make the tradeoff the user's explicit choice |
| 22 | ~~Heartbeat loop~~ (periodic "does anything need attention?" turn) | **L** | **Recommend against for v1.** Highest cost, lowest precision; the documented runaway was kimi-k3 on this exact pattern |

---

## 9. Where the evidence is thin

- **Proactive-LLM HCI numbers are all small-N lab studies** (N=18 and N=12 above), in programming and AR/working-memory contexts. The direction of the effects is consistent and matches 20 years of interruption research, but the specific percentages should not be treated as targets.
- **The 90%-vs-47% preference and 4.9%→18.6% acceptance figures** come from a survey paper citing other work; I did not verify the primary sources.
- **Superhuman's 60%/40%/9-minutes** are vendor-published marketing metrics with no methodology disclosed.
- **Dot's shutdown** has a stated cause (founder divergence) and a circumstantial one (download numbers, safety scrutiny). Treat the download figure as third-party estimation, not a company disclosure.
- **`misfire_grace_time` default of 1 second**: reported consistently across issue threads and secondary sources; I did not confirm it against APScheduler source. It does not change the recommendation (write your own ticker).
- **macOS Focus-mode detection** for third-party apps appears to have no supported API; the workarounds in circulation are unreliable. Use idle time and user-configured quiet hours instead.

---

## Sources

**Scheduling & triggers**: [Claude Code scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks) · [Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks) · [Routines](https://code.claude.com/docs/en/routines) · [claude-code#60144 (maintenance sleep)](https://github.com/anthropics/claude-code/issues/60144) · [ChatGPT scheduled tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt) · [Khoj automations](https://docs.khoj.dev/features/automations/) · [AnythingLLM scheduled jobs](https://docs.anythingllm.com/scheduled-jobs/overview) · [AnythingLLM configuration](https://docs.anythingllm.com/scheduled-jobs/configuration) · [AnythingLLM cron builder](https://docs.anythingllm.com/scheduled-jobs/scheduling) · [OpenClaw heartbeat](https://docs.openclaw.ai/gateway/heartbeat) · [openclaw#159329 (898-call runaway)](https://github.com/openclaw/openclaw/issues/159329) · [n8n Schedule Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger) · [n8n Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook) · [APScheduler job docs](https://apscheduler.readthedocs.io/en/3.x/modules/job.html) · [APScheduler #146](https://github.com/agronholm/apscheduler/issues/146) · [Electron powerMonitor](https://www.electronjs.org/docs/latest/api/power-monitor) · [electron#24244](https://github.com/electron/electron/issues/24244) · [NSBackgroundActivityScheduler](https://developer.apple.com/documentation/foundation/nsbackgroundactivityscheduler) · [Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push)

**Agent inbox & durability**: [langchain-ai/agent-inbox](https://github.com/langchain-ai/agent-inbox) · [LangChain human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) · [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) · [Durable LLM workflows on SQLite](https://www.pedroalonso.net/blog/durable-llm-workflows-sqlite/) · [Superhuman Auto Drafts 2.0](https://blog.superhuman.com/auto-drafts-2-0/) · [TechCrunch on Auto Drafts](https://techcrunch.com/2026/07/14/superhumans-new-auto-draft-feature-almost-makes-me-like-ai-replies/)

**HCI**: [Codellaborator, CHI '25](https://arxiv.org/html/2502.18658v4) · [ProMemAssist](https://arxiv.org/html/2507.21378) · [Agentic coding needs proactivity](https://arxiv.org/html/2605.06717v1) · [Do proactive agents need an LLM to decide when to wake?](https://arxiv.org/abs/2605.30152) · [Iqbal & Horvitz, disruption and recovery (CHI 2007)](http://erichorvitz.com/CHI_2007_Iqbal_Horvitz.pdf) · [Iqbal & Bailey, mental workload (CHI 2005)](https://www.interruptions.net/literature/Iqbal-CHI05-p1489-iqbal.pdf) · [Iqbal & Bailey, intelligent notification management (CHI 2008)](https://interruptions.net/literature/Iqbal-CHI08.pdf) · [Horvitz, learning and reasoning about interruption](http://erichorvitz.com/iw.pdf) · [Horvitz, bounded deferral](http://erichorvitz.com/Bounded_Deferral.pdf) · [Attention-sensitive alerting](https://arxiv.org/pdf/1301.6707) · [Fogarty et al., predicting interruptibility](https://homes.cs.washington.edu/~jfogarty/publications/chi2003.pdf) · [Fogarty et al., programmers' interruptibility](https://faculty.washington.edu/ajko/papers/Fogarty2005ProgrammersInterruptibility.pdf) · [Pielot et al., in-situ notifications](https://pielot.org/pubs/Pielot2014-MobileHCI-Notifications.pdf) · [Intelligent notification systems survey](https://arxiv.org/pdf/1711.10171)

**Long-running work**: [OpenAI background mode](https://developers.openai.com/api/docs/guides/background) · [codex#45264](https://github.com/openai/codex/issues/45264) · [codex#42880](https://github.com/openai/codex/issues/42880) · [codex#4998](https://github.com/openai/codex/issues/4998) · [Devin release notes](https://docs.devin.ai/release-notes/overview) · [Devin session insights](https://docs.devin.ai/product-guides/session-insights) · [Claude Code tools reference (Monitor)](https://code.claude.com/docs/en/tools-reference)

**Ambient & negative cases**: [Granola pre-meeting briefs](https://docs.granola.ai/help-center/taking-notes/pre-meeting-briefs) · [screenpipe](https://github.com/screenpipe/screenpipe) · [Windows Recall opt-in reversal](https://www.computerworld.com/article/2140187/microsoft-makes-windows-recall-opt-in-after-privacy-security-backlash.html) · [The Record on Recall](https://therecord.media/microsoft-reverses-course-recall-opt-in) · [SecurityWeek on Recall](https://www.securityweek.com/microsoft-bows-to-public-pressure-disables-controversial-windows-recall-by-default/) · [TechCrunch on always-listening normalization](https://techcrunch.com/2026/09/09/apple-watchs-new-ai-features-are-normalizing-the-idea-that-technology-is-always-listening/) · [Futurism on Dot](https://futurism.com/ai-dot-companion-controversy) · [AICerts on Dot shutdown](https://www.aicerts.ai/news/dot-app-shutdown-startup-failure-signals-ai-companion-risks/) · [efficient.app Motion review](https://efficient.app/apps/motion) · [saner.ai Motion reviews](https://www.saner.ai/blogs/motion-reviews) · [Morgen: Akiflow vs Motion](https://www.morgen.so/blog-posts/akiflow-vs-motion)

**Automation authoring & governance**: [Trigger-action rules via fine-tuned LLMs](https://link.springer.com/chapter/10.1007/978-3-031-95452-8_2) · [FARM](https://arxiv.org/pdf/2601.15687) · [Context-aware reminders in everyday language](https://arxiv.org/pdf/2605.23085) · [NL policies to executable obligations](https://arxiv.org/pdf/2608.23282) · [Trend Micro on OpenClaw](https://www.trendmicro.com/en_us/research/26/b/what-openclaw-reveals-about-agentic-assistants.html) · [Adversa OpenClaw hardening](https://adversa.ai/blog/openclaw-security-101-vulnerabilities-hardening-2026/) · [Backslash on OpenClaw risks](https://www.backslash.security/blog/openclaw-security-risks-explained) · [ClawTrust: 341 malicious skills](https://clawtrust.ai/blog/openclaw-security-341-malicious-skills-and-what-we-do-about-it) · [Cisco on personal AI agents](https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare)
