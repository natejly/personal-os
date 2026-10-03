# Delegation comparison (2026-10-02)

Builder notes for cowork desks, scheduled tasks, skills, and MCP connectors. Claims come from `delegation-app.md` (working tree on 2026-10-02) and `delegation-market.md` (pages fetched the same day). Where a close names a function, that name is in the app note or in `jobs.py`, `jobs_policy.py`, `skillmd.py`, or `app.py` as they stood while this was written. No new market research. The older status sections in `docs/research/sota-jobs.md`, `sota-mcp.md`, and `sota-cowork.md` are stale against this tree; they are not used as the product.

Hook check for the closes below: `Jobs.earliest_due` and `Scheduler.loop` live in `backend/personal_os/jobs.py`. The in-process retry watcher is `JobPolicy` in `backend/personal_os/jobs_policy.py`. Skill import is `skillmd.import_text`. No power-wake call showed up in the application code.

## Where the notes disagree

Use these as written. They are not resolved by extra sources.

- **Claude, remote schedule versus local files.** The help article updated the week of 2026-10-02 says scheduled tasks run remotely with the computer asleep or the desktop app closed, and that they cannot be tied to a folder on the computer. The same article says a scheduled task that needs local files or apps runs only locally. An Academy lesson says connector-only tasks run remotely, and tasks that need files or apps on the computer run locally, wait until the person is back, and say they were delayed. The help article is the one marked current. Source: [Schedule recurring tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork), [Academy: scheduled tasks](https://academy.claude.com/courses/introduction-to-claude-cowork/scheduled-tasks).
- **ChatGPT, unattended permission.** The automations page says scheduled tasks use `approval_policy = "never"` when the org allows it, and fall back to the selected mode when that value is disallowed. A separate inbox-zero page says the product proposes cleanup and drafts, then waits for approval before acting. Both are OpenAI pages. The market note leaves them unreconciled. Source: [Scheduled tasks](https://learn.chatgpt.com/docs/automations), [Get your email to inbox zero](https://learn.chatgpt.com/use-cases/manage-your-inbox).
- **ChatGPT numeric caps.** Search snippets of help article `10291617` disagree, and the article timed out on fetch. Caps are not used here.
- **AnythingLLM’s “up to 80%” savings**, and its claim that other products put every tool into every prompt, are the vendor’s own docs with no method. Source: [AnythingLLM agent setup](https://docs.anythingllm.com/agent/setup).
- **Claude plugins.** The older plugins blog (local save, research preview) disagrees with the current help center (plugins saved to the account). Use the help center. Source: [Use plugins in Claude](https://support.claude.com/en/articles/13837440-use-plugins-in-claude).
- **Inside Grain.** The README’s desk chain rule is narrower than `_chain_kind`. The code continues after any successful tool and issues one nudge with no plan-step check. `desk_start` allows only `plan` and `propose`, and its success text still says the desk is waiting for plan approval when mode is `propose`.

On 2026-10-02, Claude’s cloud Cowork sessions are already in beta. The further change dated 6 October 2026 (new Pro and Max tasks run in the cloud, and “Only on your computer” is removed) is announced and not yet in effect. Source: [Get started with Claude Cowork](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork).

## 1. What Grain ships now

From the app note only.

A desk is one conversation, one relative folder `cowork/<id>`, and one status row, chained by a supervisor. Plan mode withholds consequential tools until the user approves a plan. Ask mode cards every mutation and a standing grant cannot turn the card off. Propose mode refuses external calls, including MCP. The agent nominates files under `outputs/`; `POST /cowork/desks/{id}/accept` is the only promotion path, and promotion is checked by read-back. A desk card with no viewer parks after 180 seconds by default and can wake the desk when answered. A restart moves live desks to `interrupted`. Nothing auto-resumes. Defaults that a desk may only tighten: 12 turns, $2, 4 live desks. `done` is written only by `desk_done`.

A job is five-field cron or a single ISO instant. The scheduler sleeps until the next slot, capped at 60 seconds, because the asyncio clock does not move during suspend. A gap collapses to one run for the latest missed slot, with a late notice when lateness is over 90 seconds or more than one slot was collapsed. The run is a new conversation, `kind=job`. External calls and scheduling calls become inbox proposals and do not execute. Accepting a proposal is what runs the tool, once. A job that still needs an ordinary approval is denied because nobody is watching; it does not park. Overlap skip, in-process retry (default 1, max 5), failure-streak pause (default 3), per-job allowlists, dry run, and `GET /jobs/{id}/runs` are in this tree. The allowlist can narrow a run and cannot turn a globally off tool on. Tools that book more unattended work are rejected even if listed. The Agent Inbox (`GET /inbox`, default last 72 hours) is a separate queue from desk needs-you.

Approved skills are prose (name, description, procedure). They inline in full until the fenced block passes 6,000 characters (or the chat asks for a manifest); past that, the prompt gets an index of up to 50 and `skill_view` loads one approved body. A model can only create candidates. Approval is a human status change. Authority language blocks it. `allowed-tools` is ignored. `scripts/`, `references/`, and `assets/` are not imported. The procedure cap is 4,000 characters. In full-inline mode, at most 12 bodies are injected and the rest are omitted.

Each ready, granted, non-quarantined MCP tool is a schema `mcp__<server>__<tool>` with danger external. Past 12 such tools, the model gets `mcp_tool_search` (BM25) and only loaded slugs are callable. At or under 12, every schema is sent every round. The loaded set is new on each reply, including each chained desk turn. Stdio works. Streamable HTTP works, with OAuth refresh. Any other transport fails. A shape change that adds a fail-level finding is quarantined until the user accepts it, and a hash mismatch decays an `on` grant to `ask`. On a job, an MCP call is a proposal; accept is what calls it.

Subagents (`agent_spawn` and roles), workflows (`workflow_run` only as a proposal from a job), and commands are registered. Children cannot schedule, start desks, draft skills, or call `mcp_tool_search`.

There is no cloud runner. Kinds are only `cron` and `once`.

## 2. What leading apps ship that Grain does not

Named from the market note. Progressive skill loading, deferred MCP search, per-job allowlists, run history, dry run, in-process retry, and pause are already in Grain and are not listed here.

**Claude Cowork** ([Get started](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork), [Schedule recurring tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork), [Use safely](https://support.claude.com/en/articles/13364135-use-claude-cowork-safely))

- Scheduled tasks that keep running when the computer is asleep or the desktop app is closed, for work that uses connectors and files on the Claude account. Grain’s scheduler runs in the app process and needs the machine awake.
- A Scheduled page with upcoming runs, past runs, edit, pause, resume, delete, and run on demand. Grain has per-job history and an inbox; it does not have this remote runner.
- Clock presets in the manual form: hourly, daily, weekly, weekdays, or manual. Grain takes a five-field cron or an ISO instant, and does not parse natural language.
- Permission postures Manual, Auto, and Skip. Auto lets the model review each action, block what it judges unsafe, and fall back to asking. Skip pauses for nothing and does not run that check. Deletion still waits for an explicit Allow in every mode. The scheduled-task form has a field named “approval mode”; the fetched article does not define the values or what a cloud run does when it needs a person who is offline.
- Skills as ZIP packages that require code execution, with built-ins for Excel, Word, PowerPoint, and PDF, invoked when relevant or with `/skill-name`. Custom skills can include code and can install packages inside the vendor container. Grain stores three text fields and drops bundled files. Source: [Use skills in Claude](https://support.claude.com/en/articles/12512180-use-skills-in-claude).
- Plugins that bundle skills, connectors, and sub-agents, saved to the account, with a Discover tab. Local MCP servers bundled in plugins run on the computer with the user’s privileges. Grain has the pieces separately and has no bundle install. Source: [Use plugins](https://support.claude.com/en/articles/13837440-use-plugins-in-claude), [Get started](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork).

**ChatGPT** ([Scheduled tasks](https://learn.chatgpt.com/docs/automations), [Projects](https://help.openai.com/en/articles/10169521))

- Web scheduled tasks that do not need the desktop app and cannot touch a folder on the computer. Desktop tasks that need local files need the computer on and the app running, which is the class Grain is already in.
- Standalone tasks edit an RFC 5545 RRULE (the documented example is monthly on day 1 at 09:00). In-chat tasks can use minute intervals. One standalone task can cover more than one project.
- Event triggers on web and mobile, not on desktop: Gmail, Slack, and GitHub, with filters. One task can have several event triggers and cannot mix events with a clock schedule. Nearby events may be combined into one run. Grain’s kinds are `cron` and `once`.
- Desktop scheduled runs in the project directory or on a git worktree so agent edits stay off unfinished local work. The app note describes desk isolation as `cowork/<id>` (outputs, work, baseline, trash). It does not describe a worktree for a job.
- Scheduled view as an inbox with an unread indicator. Grain’s away feed is the last 72 hours of job runs; per-job history is separate and already shipped.
- Sandbox modes (read-only, workspace-write, full access) and `approval_policy = "never"` when org policy allows it. Full access is the setting the page warns about. Skills can create or update scheduled tasks. Grain turns `schedule_task` from a job into a proposal.

**MCP guidance, not a second product** ([Client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices), [Registry about](https://modelcontextprotocol.io/registry/about), [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog))

- Official client guidance: keep a server catalog, connect on demand, cache tool definitions, and reload on list-changed. A skill file can name the servers it needs. Grain connects the servers the user has already configured, and the loaded tool set does not stick across replies.
- The public MCP Registry is a preview metadata catalog. Hosts are told not to call it directly. Grain has no registry browse. That part of the guidance says to use a downstream marketplace, not the preview API.
- The changelog removes HTTP+SSE as a current transport (deprecated since 2025-03-26). Grain failing closed on transports other than stdio and streamable HTTP matches that, for SSE specifically.

**Open WebUI and AnythingLLM** ([Open WebUI tools](https://github.com/open-webui/docs/blob/main/docs/features/extensibility/plugin/tools/index.mdx), [AnythingLLM agent setup](https://docs.anythingllm.com/agent/setup))

- Open WebUI automations skip the experimental approval prompt and run with full access, because nobody is watching. The docs do not describe a tool allowlist stored on the automation. Grain already denies that pattern for jobs.
- AnythingLLM’s Intelligent Tool Selection adds the tools it judges useful. The market note treats relevance as retrieval, not permission. Grain’s job allowlist is the permission control, and it is already shipped.

The app note does not say which MCP protocol revision Grain speaks, and it did not re-audit resources, prompts, sampling, elicitation, or `list_changed`. Those are not scored as missing.

## 3. Where Grain is already ahead, or different on purpose

**Outward work stays a proposal.** On a job, danger `external` and `schedules` are recorded and not executed. A second accept does not run the tool again. A job cannot create another job by itself. Claude Skip, ChatGPT `approval_policy = "never"`, and Open WebUI’s automation bypass are the opposite posture. Do not add a blanket skip for unattended runs. The inbox-zero page’s “wait for approval” is the behavior Grain already has for outward calls.

**The per-job allowlist is the control those docs do not show.** NULL inherits the user’s tools. A JSON list narrows. It cannot enable a tool the user turned off. Seed jobs ship with lists. Dry run, overlap skip, in-process retry, failure-streak pause, and per-job history are in the tree. The market note calls a real per-job allowlist rare.

**Desk autonomy is enforced in the tool loop.** Plan mode omits write, shell, and external schemas before approval. Ask mode forces the card. Propose mode denies external calls. File promotion is a separate human claim plus a hash read-back. Parked approvals cannot be edited. These stay as they are.

**Skills cannot grant authority.** Nothing inserts `approved` except the human status change. Authority sentences block that change. Fence closers are stripped. `allowed-tools` is ignored on purpose. That is stricter than a ZIP skill that can install packages and run code.

**MCP annotations are not a grant.** The 2026-07-28 tools page says clients must treat annotations as untrusted unless the server is trusted ([Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Grain marks every MCP tool external. Drift quarantine and hash decay to `ask` are extra gates the fetched product docs do not describe. Deferred search above 12 tools is shipped; the client guidance’s 1–5% threshold is guidance, not a conformance rule.

**Delegation does not nest.** Workflow steps cannot start workflows, desks, or schedules. Children cannot schedule, deliver, or load MCP schemas. A desk cannot start a desk. `desk_start` from a normal chat is the door, and it is itself a card. `workflow_run` from a job only records `awaiting_approval`.

**Catch-up is one late run, with a notice.** That matches the Academy lesson’s “say it was delayed” for local Claude tasks, and ChatGPT’s “nearby events may be combined,” for the clock. Grain does not replay every missed slot.

**Local files stay on this machine.** Claude’s safety article says files opened through the desktop app are processed on Anthropic’s servers, and that a cloud session cannot reach the computer when the desktop app is offline. Grain’s desk folder never stores an absolute path and resolves containment itself. ChatGPT’s desktop worktree is a different isolation for scheduled edits in a git repo. The app note does not describe a worktree, and this pass does not add one: desk writes already land in `cowork/<id>`. A later cloud worker is out of the builds below.

**Do not build from leftovers the market note does not support.** No fetched leader page describes deterministic jitter or automatic expiry of recurring jobs. The app note did not find them in `jobs.py`. Leave them. Do not add HTTP+SSE. Do not auto-resume a desk after restart: a restarted desk is `interrupted` until the user resumes it, and a desk can write inside its folder without a proposal.

## 4. Gap table

Restart-retry below is the case the shipped watcher does not cover. The watcher retries `error` and `interrupted` only while the process lives. A backend restart kills the watcher, and nothing re-attaches.

| Gap | User impact | Evidence | How to close it here | Effort |
| --- | --- | --- | --- | --- |
| A due job does not run while the Mac is asleep. After the process is back, one late run covers the latest missed slot. | A morning job waits until the laptop is open. Claude’s connector tasks and ChatGPT web tasks do not. Local-folder tasks on both of those products also wait, which is the fair comparison for desk files. | App note: `Scheduler.loop` caps sleep at 60s because suspend freezes the asyncio clock; no cloud runner. Market: [Claude schedule](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork), [ChatGPT automations](https://learn.chatgpt.com/docs/automations). | From `Jobs.earliest_due()`, record an OS wake for that timestamp. Inject the wake call the same way `Scheduler` already injects `clock` and `sleep`, so tests never call the OS. `Scheduler.tick` stays the only launcher. External and schedule tools stay on the proposal path in `_launch_job`. If the process is quit, the existing catch-up on next launch is the fallback. A wake with no process running does not tick. A helper that runs jobs while the user is logged out is out of this build. | M |
| A job run left `interrupted` by process death is not retried, and `mark_fired` has already consumed the slot. | Quit or crash after the slot is marked loses that occurrence. The user gets neither the run nor a catch-up of the same slot. | App note, jobs section; `jobs_policy.py` states that a restart is not re-attached. In-process retry, user-stop, manual run, and dry run stay as they are. | On process start, find job runs still `interrupted` whose stored `attempt` is under `max_retries`, and launch once through the existing retry arguments (`retry_of`, same slot). Do not retry user-stopped runs. The new run is still `kind=job` and proposal-only. | S |
| No event trigger. Kinds are `cron` and `once`. | “When this file appears” cannot start work. ChatGPT documents Gmail, Slack, and GitHub triggers on web and mobile, not mixed with a clock. | App note gaps: webhook, file change, and calendar were not found. Market: [ChatGPT automations](https://learn.chatgpt.com/docs/automations). | Add a trigger next to `kind` in `jobs.py`, admitted from `Scheduler.tick` with the same one-launch-per-wake rule as `plan()`. First slice: a directory watch, no network. A burst collapses to one fire record. `schedule_task` and external tools, including MCP, still become proposals. Clock and file on the same job: one admit per tick, so they cannot double-launch. Gmail, Slack, and GitHub are a later slice on this same hook. | M for the directory. L for those three connectors. |
| Skill bundles are not imported. | A SKILL.md that points at `references/` or `scripts/` becomes a short procedure plus a warning. Claude’s skills are ZIP packages, including code, and load when relevant. | App note: `skillmd` warns that `allowed-tools` is ignored and that `scripts/`, `references/`, and `assets/` are not imported. Procedure cap 4,000. Market: [Use skills](https://support.claude.com/en/articles/12512180-use-skills-in-claude). | Teach `import_text` to store `references/` as text beside the procedure, returned by `skill_view` after approval. Keep the row a candidate. `skillbuild.lint_skill` / `approval_blockers` still block authority language. Leave `allowed-tools` ignored. Do not append references into the 4,000-character procedure. Store `scripts/` as inert text or refuse them. The job runner must not execute them. | M |
| No bundle install for a skill plus a connector. | Claude installs a plugin (skills, MCP servers, sub-agents) from a directory. Grain’s registry browse and bundle install were not found. | App note MCP gaps. Market: [Use plugins](https://support.claude.com/en/articles/13837440-use-plugins-in-claude). The registry about page says hosts should not call the preview registry directly. | Import a local folder: `skillmd.import_text` for each skill (candidate), an MCP server entry that stays ungranted until the existing grant path, and a command if the folder has one. Do not HTTP-call the public registry. Do not mark imported MCP tools `on`. | M |
| MCP schemas loaded with `mcp_tool_search` die at the end of the reply. | A chained desk turn, and the next reply in any chat, searches again. The official client note says to cache definitions and append new ones so the prompt cache survives. | App note: `tool_ctx["mcp_loaded"]` is a new set at each `_chat_stream`. Market: [Client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices). | Persist the loaded slugs on the run, and pass them into the next turn of that conversation. Drop a slug the drift quarantine hides. Leave `CHILD_BLOCK` as it is: children still do not get `mcp_tool_search`, and danger `external` stays off their toolbox. | S |
| Creating a schedule means writing cron. | Claude’s form is hourly, daily, weekly, weekdays, or manual. ChatGPT standalone tasks edit an RRULE. Grain already accepts any five-field cron, so this is the form, not the clock. | App note: five fields, sixth refused, `parse_when` is ISO only. Market: Claude schedule article; ChatGPT automations page. | Map those four repeating presets onto `valid_cron` in `schedule_task` and the job create route. Keep the cron field for everything else. Leave natural language unparsed. | S |
| Connector-only work does not run while this machine is off. | Leaders run account and connector tasks with the laptop shut. Grain cannot, without a different product. | App note: cloud worker described as planned in the README and absent in this tree. Market: Claude schedule article; ChatGPT web tasks. | **Later, separate product.** Not one of the builds below. Scope it to connector calls the user has already granted. Keep raw activity and meeting audio off it. Desk folders and meeting audio stay on this machine. Local-folder jobs stay on the wake-and-catch-up path above. Outward actions stay proposals there too. | L, separate product |

## 5. The top three builds

Order is the order to build. Each one is testable with the injected clock and local fixtures. None of them add a skip flag, a cloud worker, or an auto-resume of desks.

### 1. Wake the machine for the next slot, and retry a job the last process killed

`Scheduler.loop` already wakes itself within 60 seconds of the Mac waking, if the process is still alive. The hole is that nothing asks the Mac to wake, and a run interrupted by quit is abandoned after `mark_fired`.

Close it in `Jobs.earliest_due` / `Scheduler.loop` (record a wake) and in the boot path next to `JobPolicy` (one retry). `_launch_job` stays proposal-only. `unattended` skip is not part of this. A logged-out helper is not part of this.

**Acceptance (offline):**

- Build a scheduler with a fake clock and a fake wake sink. Arm one enabled `once` job two minutes ahead. The sink contains one wake at `next_due_at`. Disabling the job clears that wake. A second arm does not add another wake for the same instant.
- Advance the fake clock past the slot and call `tick` once. Exactly one launch. A second `tick` does not launch again. The fire record’s `missed_slots` follows the existing `plan()` rule.
- In that launched run, an external tool and `schedule_task` each insert one `proposals` row. The tool body does not run. `POST /proposals/{id}/accept` runs it once; a second accept does not.
- A stored job run with status `interrupted`, `attempt` 0, and `max_retries` 1 is launched once by the boot pass. The same row is not launched when `attempt` is already 1. A user-stopped run is not launched. The retry run is proposal-only on the same check as above.

### 2. A directory trigger that collapses to one proposal-only run

ChatGPT’s event triggers are the market feature Grain’s kinds do not have. The slice that can be tested without an account is a folder.

Close it as a new trigger beside `cron` and `once`, admitted inside `Scheduler.tick`, using the same one-launch rule as `plan()`.

**Acceptance (offline):**

- A job whose only trigger is a directory, with no cron, stays idle until a file appears. One created file produces one `kind=job` launch on the next tick.
- Five files written before that tick produce one launch, and the fire record shows the collapse (a count greater than one, the same idea as `missed_slots`).
- The run’s external call and `schedule_task` are proposals. Accept is what creates the next job. The directory job’s allowlist still cannot enable a tool that is off for the user.
- A job that has both a cron slot and a directory trigger launches at most once on a tick where both are due.

### 3. Import skill references without running scripts or granting tools

Progressive loading is shipped. The remaining skills hole is the bundle `skillmd` warns about and then drops.

Close it in `import_text` / `to_skill_fields`, with a side field `skill_view` can read. Approval stays human. `scripts/` stay inert.

**Acceptance (offline):**

- Import a SKILL.md whose body is under 4,000 characters plus `references/note.md`. The row is `candidate`. `approved_block` does not include it. After a human approve, `skill_view` returns the procedure and the reference text. Before approval, `skill_view` returns the same error it returns today for a non-approved id.
- A procedure that claims to skip confirmation still fails `approval_blockers`. Frontmatter `allowed-tools` still produces the existing warning and adds no tool to a job’s mode map.
- A body over 4,000 characters is still a 422. A long reference does not get around that cap by being copied into `procedure`.
- `scripts/run.py` in the same folder is not executed by a `kind=job` run that has the skill in context. The run has no new tool name taken from that file.

After these three, the small MCP follow-on is the sticky loaded-slug set in the table (effort S). The local plugin folder (effort M) should wait until reference import exists, so a bundle does not grow a second skill parser. The cloud runner stays a separate product.
