# Shared-state coordination for multi-agent work

Research date: 2026-10-02. Audience: a local single-user personal AI app. Names below are the systems being compared. Benchmark papers are marked as such; production claims are marked as such.

## What shared-state designs are actually used, and what does the shared object contain?

### Takeaway

Shipping systems cluster into six mechanisms. The ones that hold up in 2025–2026 production writing are a single writer plus a read-only or clean-context contributor, or an orchestrator that alone updates a ledger. Multi-writer boards exist as real code, mostly as a human-facing control plane or an experimental teammate task list, and the vendors who run coding agents still tell you not to let two agents write the same artifact.

### Cited Findings

**Hierarchical orchestrator plus workers, with a single-writer ledger.** Magentic-One (Microsoft Research, arXiv:2411.04468, November 2024; code in AutoGen’s `MagenticOneOrchestrator`) is an orchestrator plus WebSurfer, FileSurfer, Coder, and ComputerTerminal. This is a benchmark system evaluated under AutoGenBench, later shipped as library code, not a consumer product. The orchestrator is the only writer of two short-term memories for one task. The task ledger holds given or verified facts, facts to look up, facts to derive, educated guesses, and a natural-language plan (not a structured task graph). The progress ledger is five answers: is the request fully satisfied; is the team looping; is forward progress being made; which agent should speak next; what instruction to give that agent. A stall counter (threshold ≤ 2 in their experiments) sends the orchestrator back to revise the ledger. Revising the plan clears every agent’s context. Workers talk through the conversation transcript; they do not co-edit the ledger. — [Magentic-One paper (PDF)](https://www.microsoft.com/en-us/research/wp-content/uploads/2024/11/Magentic-One.pdf); [arXiv HTML](https://arxiv.org/html/2411.04468)

**Orchestrator-worker for research, shared plan in memory, findings returned as messages.** Anthropic’s Research feature (engineering post, 13 June 2025) is a production system. A lead agent writes a plan into Memory so the plan survives truncation past 200,000 tokens, spawns subagents that search in their own windows, and waits synchronously for each set to finish. Subagents return findings; a CitationAgent then places citations. The appendix says subagents can write artifacts to an external store and hand back references, to avoid copying large outputs through the lead. Early failure: vague delegation made subagents run the same searches. Effort rules they embedded: simple fact-finding is 1 agent and 3–10 tool calls; direct comparisons are 2–4 subagents with 10–15 calls each; complex research is more than 10 subagents. — [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

**Shared typed scratchpad with reducers (LangGraph).** `StateGraph` nodes read one user-defined state schema and return partial updates. A reducer on a key aggregates concurrent writes; without one, parallel updates to the same key are the conflict. A checkpointer stores a `StateSnapshot` at each super-step, keyed by `thread_id`. That snapshot is the shared object: whatever fields the app put in state (often a message list), plus checkpoint metadata. Nodes do not see a kanban. Resume restarts the interrupted node from the start of its function. — [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) (docs fetched 2026-10-02)

**Handoff with a single actor, plus an optional local context object (OpenAI Agents SDK).** `handoff()` transfers control to one named agent. The next agent receives the prior conversation by default; `input_filter` or an opt-in `nest_handoff_history` can replace or compact that history into summary segments. Separately, `RunContextWrapper.context` is an app-defined mutable object shared by tools, handoff callbacks, and lifecycle hooks inside one run. The SDK docs distinguish that object from context the model sees: it is not shown to the model unless the app puts it in the prompt. Nested `Agent.as_tool()` runs do not get an isolated copy of that app state by default. — [Handoffs](https://openai.github.io/openai-agents-python/handoffs/); [Context management](https://openai.github.io/openai-agents-python/context/) (docs fetched 2026-10-02)

**Prior-task output plus an optional fact memory (CrewAI).** A task’s `context` is a list of earlier tasks. Those tasks’ outputs are injected into the next task, and an async predecessor is waited on. With `memory=True`, the crew builds one `Memory`, extracts discrete facts from each task output after it finishes, and recalls relevant facts into the next task prompt. Agents can be given a scope so they do not see the whole tree. That is a shared fact store across a crew, not a column board, and the task output string is the handoff payload. — [Tasks](https://docs.crewai.com/edge/en/concepts/tasks); [Memory](https://docs.crewai.com/edge/en/concepts/memory) (docs fetched 2026-10-02)

**Workflow state bag, not an agent board (Mastra).** Workflow `state` is a Zod schema every step can read and `setState`. It is separate from step input/output, persists across suspend/resume, and a parent’s updates are visible inside a nested workflow. `Agent.network()` is deprecated; the docs say to use a supervisor calling `agent.stream()` or `agent.generate()`, and network memory is what tracks task history and completion. Sharing one memory thread between supervisor and sub-agent is an open request (issue 19291): the current cross-agent lock deadlocks if a child uses the parent’s `(resourceId, threadId)`. Default delegation isolates memory. — [Workflow state](https://mastra.ai/docs/workflows/workflow-state); [Agent networks](https://mastra.ai/docs/agents/networks); [issue 19291](https://github.com/mastra-ai/mastra/issues/19291)

**Shared task list plus a per-agent mailbox (Claude Code agent teams).** Documented as of v2.1.178, still experimental, off unless `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. One lead session, teammates as separate sessions, one team per session, teammates cannot spawn teammates. The task list lives at `~/.claude/tasks/{team-name}/`. Tasks have three states: pending, in progress, completed, plus dependencies; a pending task with unfinished dependencies cannot be claimed. Completing a task unblocks dependents. The mailbox is `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`. Teammates load project files and the spawn prompt; the lead’s conversation history does not carry over. The docs say two teammates editing the same file leads to overwrites, and teams do not isolate worktrees. — [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)

**Ticket board with an event log (agent-kanban and Mission Control).** Covered in the board section below. They are the multi-writer ticket design. Anthropic’s June 2025 Research system and Magentic-One are not.

### Inferences

- For one person and one chat, the designs that match the product are LangGraph-style state or an OpenAI-style handoff (one actor, a transcript, an optional side object) and Anthropic’s lead-plus-searchers pattern when the job is breadth-first reading. A column board is a second product: a place the person looks to see several jobs.
- Magentic-One’s “clear all agent contexts when the plan changes” is the opposite of Cognition’s “share full traces.” It is a benchmark control loop, not a pattern to copy into a personal writer.
- CrewAI memory and Mastra workflow state solve different problems. Memory is recalled facts across tasks. Workflow state is a bag the steps mutate inside one run. Neither is a claimable task with a single owner.
- Message-bus designs (Claude teammate mailboxes, and the Magentic-One conversation the orchestrator reads) still need a single place that decides the next write. The bus does not resolve conflicting edits.

### Gaps

- MetaGPT’s shared message pool is the usual citation for a publish/subscribe blackboard (MAST cites arXiv:2308.00352). This pass did not open that paper or the repo’s schema, so the field list is not recorded here.
- Current AutoGen GroupChat message fields, beyond Magentic-One’s ledgers, were not opened.
- Google’s A2A protocol is listed in the MAST bibliography (Google Developers Blog, April 2025) and was not opened. It is an inter-agent wire protocol, which matters for many organizations talking to each other more than for one local app.
- CrewAI’s older “hierarchical” manager process was not on the pages opened here. Only `context` and `memory=True` were verified.
- Mastra supervisor delegation’s exact persisted payload (prompt plus response versus full tool trace) is specified as an open question in issue 19291, not as current behavior.

## What is Cognition’s 2025–2026 position on multi-agent writers versus a single writer?

### Takeaway

On 12 June 2025 Cognition said collaborative multi-agent setups were fragile and the reliable default was one linear agent. On 22 April 2026 the same author said that claim still holds for parallel writers, and the setups they actually shipped keep a single writer while other agents only add intelligence.

### Cited Findings

Walden Yan, “Don’t Build Multi-Agents,” Cognition, dated 06.12.25:

> “it is evident that in 2025, running multiple agents in collaboration only results in fragile systems. The decision-making ends up being too dispersed and context isn’t able to be shared thoroughly enough between the agents.”

The two principles in that post: “Share context, and share full agent traces, not just individual messages” and “Actions carry implicit decisions, and conflicting decisions carry bad results.” He describes the default that satisfies both as “a single-threaded linear agent.” On Claude Code as of that date: it “never does work in parallel with the subtask agent, and the subtask agent is usually only tasked with answering a question, not writing any code,” because the subagent lacks the main agent’s context. — [Don’t Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents)

Walden Yan, “Multi-Agents: What’s Actually Working,” Cognition, dated 04.22.26. He says the June 2025 observations “still hold today for parallel-writer swarms” and that those ideas “still don’t see meaningful adoption.” What they shipped is narrower:

> “setups where multiple agents contribute intelligence to a task while writes stay single-threaded.”

The closing claim:

> “multi-agent systems work best today when writes stay single-threaded and the additional agents contribute intelligence rather than actions.”

Three production patterns, all on Devin or Windsurf, not on a public benchmark:

1. Devin and Devin Review iterate on a PR. Even on Devin-written PRs, review “catches an average of 2 bugs per PR, of which roughly 58% are severe (logic errors, missing edge cases, security vulnerabilities).” He says this worked best when the reviewer did **not** share the coder’s context: a clean window, reading the diff, avoids context rot. The coder then filters review comments against the user’s instructions. That is a deliberate break with “share full traces,” limited to a verifier that does not write the code.
2. “Smart friend”: the writer calls a stronger model. Forking the full context was their “80/20.” SWE-1.5 as the weak primary was not good enough; cross-frontier Claude and GPT in this setup “produced real gains in the trickiest scenarios.” He calls the weak-primary version an open training problem.
3. A manager Devin, “live in Devin today,” splits a larger task, spawns child Devins, and coordinates them through an internal MCP. Getting it coherent took more work than expected. “Agents assume they share state with their children when they don’t.” Child-to-sibling messages do not happen unless you build the bridge. He calls unstructured swarms “mostly a distraction” and names the practical shape “map-reduce-and-manage.”

He also restates principle 2 in the April post: when one agent edits, it makes implicit choices about style, patterns, and edge cases that conflict with other parallel writers, so “most multi-agent setups in the world are limited to ‘readonly’ subagents.” — [Multi-Agents: What’s Actually Working](https://cognition.com/blog/multi-agents-working)

A 16 September 2025 Cognition post about rebuilding Devin for Claude Sonnet 4.5 says subagent delegation “might” get more practical because the model is more aware of what to delegate, and that “you have to be very careful about when to use subagents because the context and state management gets complex quickly.” That is a hedge, not a reversal of the single-writer rule. — [Rebuilding Devin for Claude Sonnet 4.5](https://cognition.com/blog/devin-sonnet-4-5-lessons-and-challenges)

### Inferences

- The 2026 position is not “never use a second model.” It is “one writer.” Reviewers, advisors, and a manager that assigns work are allowed. Two agents editing the same result are still the failure case.
- The clean-context reviewer is the exception they measured. Copying it means the checker sees the artifact, not the writer’s trace, and the writer still decides what to change. That fits a personal app: one chat edits the doc or the code, a second pass reads the result and comments.
- Manager-plus-children is what they run for week-long, multi-PR Devin work. They say it only feels like one agent after extra context engineering, and children do not see each other’s state. That is a poor default for a single-user app doing one task in one sitting.
- “Share the same page (todo list, plan files)” in the April post is the shared object they do want: a plan both sides can read, not a board both sides can write.

### Gaps

- The 2 bugs per PR and 58% severe figures are Cognition’s production numbers. The post does not give the sample size, the time window, or a comparison against a single agent with no reviewer.
- No Cognition post opened here gives a token multiplier for these patterns.

## What token multipliers has Anthropic published, and which tasks justify a subagent?

### Takeaway

The first-party multiplier is still the 13 June 2025 Research post: agents about 4× a chat, multi-agent systems about 15× a chat. No Anthropic page opened for this note revises that to 3–10×. Later Anthropic text tightens *when* a subagent is worth it (parallel, isolated, no shared state) and, in August 2026, shows a research swarm that spent far more tokens for a gain that shrinks once you compare the same scope.

### Cited Findings

**13 June 2025, production Research system, internal evals.** “In our data, agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats.” On BrowseComp, three factors explained 95% of performance variance; “token usage by itself explains 80% of the variance,” with tool-call count and model choice the other two. A Claude Opus 4 lead with Claude Sonnet 4 subagents beat single-agent Opus 4 by 90.2% on an internal research eval. Upgrading Sonnet 3.7 to Sonnet 4 beat doubling the token budget on Sonnet 3.7. Spinning 3–5 subagents in parallel, each using 3 or more tools in parallel, “cut research time by up to 90% for complex queries.” They say multi-agent systems excel at “valuable tasks that involve heavy parallelization, information that exceeds single context windows, and interfacing with numerous complex tools.” They say domains that need one shared context, or many dependencies between agents, are a poor fit, and “most coding tasks involve fewer truly parallelizable tasks than research.” Economic bar: the task has to be valuable enough to pay for the extra tokens. The “3–10” in this post is tool calls for simple fact-finding, not a token multiplier. — [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

**29 September 2025, context engineering (production guidance, not a new multiplier).** Subagents “might explore extensively, using tens of thousands of tokens or more, but return only a condensed, distilled summary (often 1,000–2,000 tokens).” They place multi-agent architectures on “complex research and analysis where parallel exploration pays dividends,” compaction on long back-and-forth, and note-taking on iterative work with milestones. — [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**13 August 2026, research essay, not the Research product and not a revision of the 15× line.** “Patterns and problems in emerging multiagent systems.” A 45-agent swarm with a shared forum and an arbiter, versus independent agents pointed at slices of 15 codebases. Mythos Preview: independent run found 21 vulnerabilities in 6.5 million tokens; the swarm found 266 in 27 million tokens. About half the swarm’s finds were outside the core directories the independent agents were told to search. Restricted to those core directories, “the two methods seem comparable in terms of tokens per vulnerability found.” Only 12 vulnerabilities were in common. A separate 12-hour “build a game” swarm: Sonnet 4.6 and Opus 4.6 opened PRs that conflicted and were abandoned; Opus 4.8 and Mythos Preview kept a high merge rate by barely sharing files; Sonnet 5 both shared code and merged. In one early run, 18 of 30 agents created a branch named `mvp-game-loop`. Prescriptive roles and a “CEO hierarchy” prompt “did not make much difference.” — [Patterns and problems in emerging multiagent systems](https://www.anthropic.com/research/multiagent-systems)

**Current prompting docs, fetched 2026-10-02 (the page mentions Claude Opus 4.6 and Opus 5; it does not restate 15× or 4×).** Recommended damping text: “Use subagents when tasks can run in parallel, require isolated context, or involve independent workstreams that don’t need to share state. For simple tasks, sequential operations, single-file edits, or tasks where you need to maintain context across steps, work directly rather than delegating.” The page says Opus 4.6 may spawn a subagent for code exploration “when a direct grep call is faster and sufficient.” — [Prompting best practices](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting)

**Claude Code agent teams docs, fetched 2026-10-02.** No numeric multiplier. “Agent teams use significantly more tokens than a single session” because each teammate has its own window, and “token usage scales with the number of active teammates.” Worth it, in their words, for research, review, and new feature work; a single session is more cost-effective for routine tasks. Start with 3–5 teammates. Strongest uses they name: research and review, new modules with separate owners, competing debug hypotheses, and cross-layer work where each teammate owns different files. Same-file edits and highly dependent work should stay in one session or in subagents. — [Agent teams](https://code.claude.com/docs/en/agent-teams)

### Inferences

- The 15× figure is “multi-agent research system versus chat,” not “multi-agent versus a single agent” and not a universal constant. The single-agent figure in the same sentence is about 4× chat, so the step from one agent to their multi-agent research stack is roughly 15/4 ≈ 4× that agent, if you divide the two published ratios. They do not publish that quotient themselves.
- For this app, a subagent is the expensive tool for a breadth-first read: many independent lookups, a summary of 1,000–2,000 tokens back to the main thread. It is the wrong tool for a single-file edit, a sequential plan, or anything where the next step depends on unstated decisions from the previous step.
- The August 2026 swarm is a lab on vulnerability search and a toy game, with VMs and a forum. It is evidence that coordination can spend ~4× the tokens of an already-parallel baseline (27M / 6.5M) and still tie on the comparable slice. It is not a design to install locally.

### Gaps

- No first-party Anthropic page found that replaces ~15× with a 2026 figure of 3–10×. A secondary item, [The Crypto Post, 23 January 2026](https://thecryptopost.io/anthropic-shares-multi-agent-ai-framework-for-developers/), says Anthropic “found that multi-agent implementations typically consume 3-10x more tokens than single-agent approaches.” That page was not fetched, it does not match the June 2025 primary wording (15× versus chat, not versus a single agent), and no Anthropic URL in the searches stated 3–10× tokens. Treat 3–10× as unverified. The nearest first-party “3–10” is “3–10 tool calls.”
- BrowseComp’s 80% variance result is an analysis of their browsing agents, not a law for every multi-agent stack.
- Agent-teams docs point at a further “agent team token costs” section that was not opened, so a numeric Claude Code multiplier may exist there and is not in these notes.

## Which 2025–2026 agent boards are real code, and what columns and events do they store?

### Takeaway

Three codebases were opened far enough to name columns. Claude Code’s task list is the one that is a local, single-user, single-session board. agent-kanban and Mission Control are real apps with event logs, and both are built as a control plane for a fleet (many agents, GitHub or multi-tenant workspaces), which is more machine than a personal app needs. A fourth local board, Kangentic, was only seen from its README card; its on-disk schema was not opened.

### Cited Findings

**Claude Code agent teams (product docs, experimental, local files).** Not a kanban UI. States: pending, in progress, completed. Dependencies gate claiming. Events described in prose, not as an enum in the page: lead creates tasks, teammates claim, completion unblocks dependents, idle and failure notifications to the lead, `SendMessage` into a per-agent JSON mailbox. Malformed mailbox entries are dropped on read. Team config (`~/.claude/teams/{name}/config.json`) holds members, session ids, and pane ids, and is deleted when the session ends. The task directory persists and is not uploaded. Limitations that are coordination bugs: “teammates sometimes fail to mark tasks as completed, which blocks dependent tasks”; the lead “may decide the team is finished before all tasks are actually complete”; `/resume` does not restore in-process teammates. One team per session. No nested teams. — [Agent teams](https://code.claude.com/docs/en/agent-teams)

**saltbo/agent-kanban (repo created 2026-03-20).** README: agent lifecycle idle → working → offline; task flow Todo → In Progress → In Review → Done. CLI transitions: claim, review, complete, reject (back to in progress), cancel, release (back to todo). A task spec can carry title, description, priority, labels, repo URL, `assignTo`, `dependsOn`, and `createdFrom` (parent task). Commit `711a1bf` replaces `task_notes` with `task_actions`: `actor_type` in `user | machine | agent:worker | agent:leader`, and `action` in `created, claimed, moved, commented, completed, assigned, released, timed_out, cancelled, rejected, review_requested`, plus a free-text `detail`. Changelog: Cancelled is reachable from any non-done stage; In Review was added as a column. Claiming is an atomic D1 batch so two workers cannot take the same task. The README also describes Ed25519 agent identities, a machine daemon that spawns workers, GitHub PR open and merge detection, multi-repo boards, and stale agents marked offline after 2 hours. — [README](https://github.com/saltbo/agent-kanban/blob/main/README.md); [commit 711a1bf](https://github.com/saltbo/agent-kanban/commit/711a1bf57cccbcb82b2cb8f9d3bbbb8b0f7388bf)

**builderz-labs/mission-control (repo created 2026-02-13; README at commit `c94010d` fetched in full).** Self-hosted Next.js app, SQLite, MIT. The README calls itself alpha and says schemas can change. Task board: six columns, “inbox → assigned → in progress → review → quality review → done,” plus drag-and-drop, priority, assignment, threaded comments, and inline sub-agent spawning. “Built-in Aegis review system that blocks task completion without sign-off.” Create-task example fields: `title`, `assigned_to`, `priority`. Agents poll `GET /api/tasks/queue?agent=`. An activity feed filters by event type, agent, or time. A read-only bridge scans `~/.claude/tasks/` and `~/.claude/teams/`. Adapters named for OpenClaw, CrewAI, LangGraph, AutoGen, and the Claude SDK. Also in that README: viewer/operator/admin roles, Google sign-in, multi-tenant `/api/super/*` workspaces, webhooks, cron templates. Local install exists (`install.sh --local`, no Postgres required), so one person can run it, but the object model is a fleet console. — [README at c94010d](https://github.com/builderz-labs/mission-control/blob/c94010d5ebe49af1f41cd3080f037567cd743745/README.md)

A search extract of `docs/orchestration.md` at commit `b407710` describes a **different** flow: inbox → assigned → in_progress → review → done, with side exits to cancelled, failed, and rejected, a `quality_reviews` table, and an Aegis verdict of `APPROVED` or `REJECTED` (reject requeues to assigned, cap of 3 reviews then failed). Re-fetching that blob returned an empty body, so this second schema is not confirmed by a full read. DeepWiki and a mintlify mirror listed still other column sets (`backlog`/`todo`, or `quality_review` as a status). Those are not used here. The conflict itself is the finding: the public README and a docs page in the same repo do not agree, and the README warns the schema moves.

**Kangentic (Kangentic/kangentic, created 2026-02-22).** Search snippet of the README: a local desktop board that spawns, suspends, and resumes sessions across a list of coding-agent CLIs, with per-column permission mode, entry prompt, and exit script. The example pipeline named in the README is Plan, Execute, Review, described as customizable, not as a fixed enum. Persistence tables were not opened. — [repo](https://github.com/Kangentic/kangentic)

### Inferences

- The column set that keeps showing up is some variant of inbox/todo, in progress, review, done, plus cancel or fail. The event that matters for correctness is the claim (agent-kanban’s atomic claim) and the review gate (Mission Control’s Aegis, agent-kanban’s reject back to in progress). A board without a single-owner claim will double-assign. A board without a reviewer will accept “done” from the same agent that did the work.
- Claude’s three-state list is enough for one user session and stores less than a fleet board. It already documents the failure where work is finished but the card stays in progress, which blocks everything downstream. A personal app that copies this needs a way for the person to flip the card.
- agent-kanban’s GitHub identity, machine daemon, and multi-repo model, and Mission Control’s tenants, RBAC, and gateway adapters, are for many agents operated as a workforce. Running either locally is possible. Designing the personal app around cryptographic worker identities or a quality-review agent is not required to get a shared todo.

### Gaps

- agent-kanban’s full `tasks` row (every column in the table, not just the lifecycle and the action enum) was not opened. The action enum is from commit `711a1bf`, which may have grown since.
- Mission Control’s live `tasks` table and activity-event enum were not read from `migrations.ts`. The six column names are from the README only. The orchestration.md flow is unverified on re-fetch.
- Kangentic’s stored card and event schema was not opened.
- KanbAgent (IvyNotFound/KanbAgent) appeared in search as another local board with worktrees and cost tracking. Its schema was not opened, so it is not described here.

## What failure modes are documented for duplicate work, conflicting writes, lost handoffs, and false completion?

### Takeaway

These four failures are measured, not hypothetical. Duplicate work and lost handoff context show up in Anthropic’s production research write-up and in a NeurIPS 2025 taxonomy of framework traces. Conflicting writes are the Cognition single-writer argument, Claude Code’s same-file warning, and an August 2026 Anthropic swarm where early models abandoned conflicting pull requests. False “done” is both premature stop and the opposite bug, a card left open so dependents never start.

### Cited Findings

**Duplicate work.** Anthropic, 13 June 2025: short subagent briefs were vague enough that “one subagent explored the 2021 automotive chip crisis while 2 others duplicated work investigating current 2025 supply chains.” They also saw agents “spawn 50 subagents for simple queries” and “continuing when they already had sufficient results.” — [Research system](https://www.anthropic.com/engineering/multi-agent-research-system)

MAST (Cemri et al., NeurIPS 2025; arXiv:2503.13657), a trace study of seven frameworks, not a product. Inter-annotator κ = 0.88 on 150 traces; the released set is 1,600+ annotated traces. FM-1.3 Step repetition is 15.7% of failures in the write-up: unnecessary repetition of steps. The appendix shows a HyperAgent planner emitting the same “let’s examine the code” thought twice, and an inner navigator repeating a `code_search` plan. OpenManus is called out as tilted toward this mode. — [arXiv HTML](https://arxiv.org/html/2503.13657); [NeurIPS PDF](https://proceedings.neurips.cc/paper_files/paper/2025/file/b1041e52d3be19f0a9bc491657488e4a-Paper-Datasets_and_Benchmarks_Track.pdf)

Anthropic, 13 August 2026, lab, not production: 18 of 30 agents independently created a git branch with the same name, `mvp-game-loop`. Agents given the same prompt also converged on the same fiction title and the same project ideas (ray tracers, compilers). That is duplicate *choice*, even when a shared forum existed. — [Patterns and problems](https://www.anthropic.com/research/multiagent-systems)

**Conflicting writes.** Cognition, 12 June 2025: “Actions carry implicit decisions, and conflicting decisions carry bad results.” The Flappy Bird example is two subagents building incompatible pieces from the same task. Cognition, 22 April 2026: parallel writers still fragment decisions about style, patterns, and edge cases; writes should stay single-threaded. — [Don’t Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents); [What’s Actually Working](https://cognition.com/blog/multi-agents-working)

Claude Code agent-teams docs: “Two teammates editing the same file leads to overwrites.” The mitigation they document is partitioning files, not a merge. — [Agent teams](https://code.claude.com/docs/en/agent-teams)

Anthropic, 13 August 2026: in the game-building swarm, Sonnet 4.6 and Opus 4.6 “committed code to the same sets of files, but a very low fraction of these PRs were merged,” and “the PRs often conflicted with one-another, at which point they were then abandoned.” Newer models in that experiment avoided the conflict by not sharing files. Only Sonnet 5 kept both high code sharing and a high merge rate. Role prompts and a CEO prompt did not fix the earlier models. — [Patterns and problems](https://www.anthropic.com/research/multiagent-systems)

LangGraph’s graph API is the library-level admission that parallel nodes write one state: you attach a reducer or accept whatever the runtime does with two updates to one key. Checkpoints are taken at super-step boundaries, so a crash mid-node reruns that node’s side effects. — [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)

Magentic-One’s outer loop, on a replan, “force[s] all agents to clear their contexts and reset their states,” which drops in-progress local decisions rather than merging them. — [Magentic-One PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2024/11/Magentic-One.pdf)

**Context loss at the handoff.** Cognition, 12 June 2025: share full traces, not individual messages; copying only the original task is not enough once the decomposition itself contains tool calls and implicit decisions. Cognition, 22 April 2026: “Agents assume they share state with their children when they don’t,” and “How do you transfer context between agents without drowning the receiver?” is listed as an open problem. The smart-friend section says quality gaps from context loss remain even after prompt tuning; their practical patch was to fork the full primary context, and to have the stronger model answer “what should I do?” rather than a narrow question. — [Don’t Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents); [What’s Actually Working](https://cognition.com/blog/multi-agents-working)

Anthropic, 13 June 2025, appendix: “Subagent output to a filesystem to minimize the ‘game of telephone.’” Passing everything through the lead loses information; they store the artifact and return a reference. The same post says that when the lead’s window nears the limit, fresh subagents are spawned “while maintaining continuity through careful handoffs,” and the plan is reloaded from memory. The 29 September 2025 post quantifies the compression: tens of thousands of tokens in, often 1,000–2,000 tokens out. — [Research system](https://www.anthropic.com/engineering/multi-agent-research-system); [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

Claude Code agent teams: “The lead’s conversation history does not carry over.” The spawn prompt has to repeat task-specific facts. — [Agent teams](https://code.claude.com/docs/en/agent-teams)

OpenAI Agents SDK: the default handoff passes prior items, and the opt-in history nester compacts them into summary segments wrapped for the next agent. Anything dropped by `input_filter` is gone for that agent. The local `context` object survives the handoff inside the run but is not model-visible by itself. — [Handoffs](https://openai.github.io/openai-agents-python/handoffs/); [Context](https://openai.github.io/openai-agents-python/context/)

MAST: FM-1.4 Loss of conversation history is 2.80% of failures, defined in the paper’s text as unexpected context truncation, “disregarding recent interaction history and reverting to an antecedent conversational state.” FM-2.1 Conversation reset appears in an appendix example (HyperAgent) where recent interaction is ignored and an earlier plan repeats. FM-2.2 is “Fail to ask for clarification.” FM-2.3 is “Task derailment” (the agents solve the problem, then answer a different one). These are trace labels across frameworks including AutoGen/AG2, ChatDev, MetaGPT, and HyperAgent, on coding, math, and general-agent tasks, with models of that study (GPT-4, Claude 3, and others). They are not 2026 production rates. — [arXiv HTML](https://arxiv.org/html/2503.13657)

**Agents marking work done.** Two different bugs:

- Stopping too early. MAST FM-3.1 Premature termination, 6.20%: ending the task “before all necessary information has been exchanged or objectives have been met.” AppWorld is cited as suffering this, attributed to a star topology and no predefined workflow, so the stop condition is unclear. FM-1.5, 12.4% in the body (“not recognizing task completion”); the appendix heading calls the same id “Unaware of stopping conditions” and shows an AG2 math chat that does not know it should stop. FM-3.2 No or incomplete verification, 8.20%, and FM-3.3 Incorrect verification, 9.10%. The ChatDev example in the paper: a chess program passes a compile-style check and still has runtime bugs. Systems with an explicit verifier (they name MetaGPT and ChatDev) had fewer total failures in their Figure 4, and verification failures stayed high anyway. Their own interventions (better prompts, a verifier role, a cycle that ends only when a CTO accepts) moved accuracy some, not a lot: ChatDev on a 32-task ProgramDev-v0 set went from 25.0% to 34.4% (prompt) and 40.6% (cyclic topology); HumanEval went from 89.6 to 90.3 to 91.5. Those are benchmark points, small sets, not production. — [arXiv HTML](https://arxiv.org/html/2503.13657)
- Never marking the card done, or the lead declaring victory early. Claude Code docs: teammates fail to mark tasks completed and that blocks dependents; the lead may shut the team down before tasks are done; as of v2.1.198 a turn that ends on an API error notifies the lead of failure instead of looking like a normal finish (the previous behavior was the false completion). — [Agent teams](https://code.claude.com/docs/en/agent-teams)
- Anthropic Research, June 2025: agents kept going after they already had enough, which is the inverse false completion, and they added effort caps to stop it. Mission Control’s README says Aegis blocks the done column without sign-off, which is a product response to self-certified completion. The exact gate belongs to the README; the alternate orchestration.md verdict flow was not re-read in full. — [Research system](https://www.anthropic.com/engineering/multi-agent-research-system); [Mission Control README](https://github.com/builderz-labs/mission-control/blob/c94010d5ebe49af1f41cd3080f037567cd743745/README.md)

Magentic-One’s progress ledger asks “is the request fully satisfied?” and “is the team looping?” on every inner step, and only the orchestrator may answer. That is a single-writer completion bit, with a stall budget, in a benchmark harness. — [Magentic-One PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2024/11/Magentic-One.pdf)

### Inferences

- For a personal app, the cheap coordination object is a plan file or a three-state list with one owner per card, plus a second pass that reads the artifact and does not edit it. That matches Cognition’s April 2026 production claim and Anthropic’s own split: parallelize independent reads, do not parallelize writers that share a file.
- Duplicate work is mostly a delegation-quality bug (vague briefs, identical prompts). A board does not fix it unless the cards name disjoint outputs. Anthropic’s fix was better briefs and an effort cap, not a richer schema.
- Conflicting writes are not solved by a message bus. Claude’s documented fix is file partitioning. Cognition’s is one writer. The August 2026 swarm mostly “solved” merge conflicts by stopping the agents from touching the same files, until a newer model. A local app should assume current models will collide if two of them edit one note.
- Handoffs throw away the trace unless you choose otherwise. The patterns that kept enough context were: full-trace fork (Cognition smart friend), a persisted plan plus a short summary (Anthropic Research), or an artifact on disk and a pointer (Anthropic appendix). A kanban card with a title and no trace will reproduce FM-1.4.
- Do not let the worker set `done`. MAST’s verification rates, Claude’s stuck-card bug, and Mission Control’s review gate all point the same way: completion is a separate decision, and the person is the right verifier in a single-user app. An automatic reviewer is what Cognition measured (2 bugs per PR on their own PRs); it still needs the writer to accept or reject the comments.
- Fleet features (multi-tenant workspaces, per-agent crypto identity, a gateway, heartbeats, a quality-review agent that is itself a model) show up because those projects operate many agents for many tasks. They do not change the coordination result above. One user does not need them to keep a single writer honest.

### Gaps

- MAST percentages are shares of annotated failures, not failure rates per task. The paper’s success-rate charts were not copied out. FM-2.4, FM-2.5, and FM-2.6 names were not read off the taxonomy figure in this pass; only FM-2.1, FM-2.2, and FM-2.3 were confirmed from appendix headings. Category labels also differ slightly between the arXiv abstract (“specification issues”) and the NeurIPS abstract (“system design issues”).
- FM-1.5’s name is “not recognizing task completion” in the body percentages and “Unaware of stopping conditions” in appendix N.2. Same id, wording not stable in the HTML.
- No 2026 field study was found that reports duplicate-work or conflict rates inside a personal, single-user agent. The August 2026 Anthropic numbers are a lab swarm. The April 2026 Cognition numbers are one vendor’s PR review loop without a published denominator.
