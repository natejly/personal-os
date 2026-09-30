# Track A — Agent architecture, orchestration and long-horizon execution

Research date: **2026-09-29**. Target stack: Python FastAPI sidecar + SQLite + LiteLLM (Fireworks, `kimi-k3` chat / `deepseek-v4-flash` cheap) + Electron/React, single local user, tools-on-by-default with ask-gates on external actions.

Reading note on evidence quality: I flag `[strong]` where there are controlled numbers from a primary source, `[medium]` where a vendor reports internal-eval numbers, and `[contested]` where the field actively disagrees. Most agent-architecture "best practice" is `[medium]` at best — the single biggest honest finding in this whole track is that **nobody has a clean ablation of harness design**, and the two papers that try ([MAST](https://arxiv.org/abs/2503.13657), [Model-or-Harness](https://arxiv.org/pdf/2607.28802)) both conclude a large share of agent failure is harness-attributable, not model-attributable.

---

## 0. Executive orientation: what actually separates a good loop from `while tool_calls: execute()`

Current `_chat_stream()` is a ReAct loop with an 8-round cap, serial tool execution, all 25 schemas injected every turn, no plan artifact, no compaction, no persistence, and the whole thing living inside one SSE response. That is the *1-star* version of every harness surveyed below. The gap to the 4-star version is six structural additions, in rough order of value-per-effort for this app:

1. **A durable run record** (rows in SQLite) so the loop is not the HTTP connection.
2. **A budget model instead of a round cap** (tokens/cost/wall-clock + a graceful "continue?" handoff instead of a silent truncation at 8).
3. **A plan/todo artifact** the model re-reads and re-writes each turn (attention recitation).
4. **Compaction + tool-result eviction** so long runs don't rot.
5. **Context-isolated subagents** for the two or three jobs where they clearly pay (research fan-out, verification).
6. **An idempotent tool ledger** so retry/resume never double-sends an email.

Everything below is the evidence for those six and the design detail.

---

## 1. Single-agent loop design done well

### 1.1 What the loop actually is, in the best harnesses

The canonical structure is identical everywhere — **model → tool calls → results → model**, until the model emits a message with no tool calls. [Claude Agent SDK's agent-loop doc](https://code.claude.com/docs/en/agent-sdk/agent-loop) and [OpenAI's "Unrolling the Codex agent loop"](https://openai.com/index/unrolling-the-codex-agent-loop/) describe the same five steps. Cline's docs describe the same Reason–Act–Observe cycle. So the loop shape is not where the differentiation is.

The differentiation is in **six structural choices layered on top**:

**(a) A stated task frame: gather context → take action → verify work.**
Anthropic frames the loop explicitly as three phases in [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk): gather context, take action, verify work, repeat. The third phase is the one almost everyone omits — and it's exactly the phase that [MAST](https://arxiv.org/abs/2503.13657) attributes **21.3% of all multi-agent failures** to. In that post, verification is ranked: rules-based feedback (best, e.g. lint/schema/test), visual feedback (screenshots), LLM-as-judge (explicitly called "generally not a very robust method").

**(b) Where reasoning goes: interleaved thinking, not front-loaded thinking.**
The distinction matters more than it sounds. Without interleaved thinking the model reasons once, then fires tool calls in sequence without re-reasoning on results. [Anthropic's extended-thinking docs](https://docs.claude.com/en/docs/build-with-claude/extended-thinking) describe interleaved thinking (`interleaved-thinking-2025-05-14`) as letting Claude "think between tool calls and run more sophisticated reasoning after receiving tool results." In [Anthropic's multi-agent research post](https://www.anthropic.com/engineering/multi-agent-research-system), extended thinking is used deliberately in two different roles: the lead agent uses it as a *planning* scratchpad, subagents use it *after tool results* to evaluate quality and identify gaps.

> **For this app:** `kimi-k3` on Fireworks is an OpenAI-compatible endpoint; you will not get Anthropic's `thinking` blocks. The portable equivalent is a **cheap structured "reflection" slot in the message stream**: after each batch of tool results, before the next model call, append a short synthetic user/system turn like `<observation_check>Briefly: what did you learn, what's still missing, what's the next action?</observation_check>` — or simply prompt the system prompt to always emit a one-line assessment before tool calls. Cheaper and model-agnostic. Measure it; don't assume it helps on a non-frontier model.

**(c) Stop conditions that are not just a counter.**
This is the current design's sharpest edge. `maxToolRounds = 8` is what the literature calls a *syntactic kill-switch*: a fixed iteration cap that is blind to whether the answer is still improving. The recent survey work ([Semantic Early-Stopping for Iterative LLM Agent Loops](https://arxiv.org/abs/2606.27009)) puts it directly: a fixed cap "over-spends tokens on easy inputs and truncates hard ones."

What good harnesses do instead:
- **Multiple independent budgets.** Claude Agent SDK exposes `max_turns` *and* `max_budget_usd`, and returns distinct terminal states `error_max_turns` / `error_max_budget_usd` so the caller can distinguish "failed" from "ran out of allowance" ([agent-loop docs](https://code.claude.com/docs/en/agent-sdk/agent-loop)).
- **Marking partial output as partial.** When a subagent stops at `maxTurns`, Claude Code "returns its output marked as partial" and it can be resumed ([subagents docs](https://code.claude.com/docs/en/agent-sdk/subagents)). It does not silently present a truncated run as an answer.
- **Effort scaling rules in the prompt.** Anthropic's research lead agent has explicit rules embedded: simple queries → 1 agent, 3–10 tool calls; comparisons → 2–4 subagents, 10–15 calls each; complex research → 10+ subagents ([multi-agent post](https://www.anthropic.com/engineering/multi-agent-research-system)). Budget is a *prompt-level* concept, not only a harness-level one.

**(d) The "one more turn" problem, in both directions.**
Evidence that this is real and bidirectional:
- **Stopping too early.** MAST's FM-3.1 "premature termination" is **7.82%** of failures, and FM-1.5 "unaware of termination conditions" another **9.82%** ([MAST](https://arxiv.org/html/2503.13657v2)). Anthropic lists "agents continuing research after obtaining sufficient results" as a fixed failure mode on the other side.
- **The strongest intervention published.** [When May an Agent Stop? Evidence-Carrying Termination](https://arxiv.org/html/2608.23623) `[strong]` requires the agent to emit a typed *certificate* binding every claim in its answer to a specific tool-call result in an immutable evidence ledger; a **deterministic** (non-LLM) verifier replays the claimed values and returns `complete` or `continue` with reason codes. Results: **0/288 unsafe completions vs 252/288 for an LLM critic baseline** (−87.5pp); in closed loop, premature unsupported terminations **0/66 vs 40/66** (−60.6pp), with supported completions essentially unchanged (+3.8pp). Cost: ~0.9 extra decision turns and ~4,653 extra tokens per trajectory.
- **Never-stopping.** [When Agents Do Not Stop: Uncovering Infinite Agentic Loops](https://arxiv.org/pdf/2607.01641) documents the opposite failure. Keep a hard outer cap; just don't make it the *only* control.

> **For this app:** the 8-round cap is doing three jobs badly (cost control, runaway protection, latency control). Split them. Replace with: (i) hard outer cap ~40 rounds, (ii) a token/cost budget from `usage.py`, (iii) a wall-clock budget, (iv) a *soft* threshold at which the loop injects a system nudge — "you have used 60% of your budget; wrap up or state what remains" — and (v) an explicit `partial` terminal state surfaced in the UI with a **Continue** button that resumes the same run. The step-3 "evidence certificate" idea is over-engineering here, but a cheap version is very attractive: for tool-heavy answers, require the final message to cite the `tool_event` ids it relied on, and have the harness (not a model) check those ids exist and succeeded. That's ~50 lines and catches the "confidently summarised a failed tool call" failure.

**(e) Tool surface shape.** Every schema on every turn is the default that everyone is moving away from. Claude Code now defers MCP tool schemas by default behind a `ToolSearch` tool, loading schemas on demand ([agent-loop docs](https://code.claude.com/docs/en/agent-sdk/agent-loop)). [Agent Skills](https://claude.com/blog/building-agents-with-skills-equipping-agents-for-specialized-work) generalise this into three-tier progressive disclosure: name+description (~50–80 tokens each) always loaded, full SKILL.md (~500 tokens) on activation, reference files (2,000+) only during execution. Anthropic's [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) also reports that a `ResponseFormat` enum letting the agent pick concise vs detailed responses cuts tokens to roughly **one third**.

Counter-evidence worth respecting: [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) argues *against* dynamically removing tool definitions mid-run, because it invalidates the KV-cache prefix — they mask logits instead. With Claude Sonnet pricing they cite, cached input is **$0.30/MTok vs $3/MTok uncached, a 10x difference**, and Manus runs roughly a **100:1 input:output token ratio**. `[contested]` — progressive disclosure and prefix caching pull in opposite directions.

> **For this app:** 25 tools is under the pain threshold (Anthropic's own multi-agent guidance flags "20+ tools" as a specialization signal). The higher-value move is not tool search but **tool gating by context**: the permission resolution already computes which tools are on for this chat/project — only inject schemas for enabled tools, and keep that set *stable within a conversation* so the prefix caches. Gmail's 5 tools + Calendar's 2 + Tasks' 3 = 10 schemas that a note-taking chat never needs.

**(f) Code-as-action as an alternative to many tools.**
[smolagents](https://huggingface.co/blog/smolagents) builds on [Executable Code Actions Elicit Better LLM Agents (CodeAct)](https://arxiv.org/abs/2402.01030), which reports **up to 20% higher success rate** than JSON/text action formats `[medium — single paper, benchmark-dependent]`. The mechanism: code gives you composition for free (loops, conditionals, nesting) so one action can do what five tool calls would.

> **For this app:** you already have a sandboxed `run_python` under `sandbox-exec`. The unlock is to **expose the app's own read-only tools as a Python API inside that sandbox** (`os.search_documents(...)`, `os.graph_traverse(...)`) via a local socket or a pre-seeded results file — then "find every doc mentioning X, cross-reference against my todos, count by week" is one code action instead of fifteen rounds. This is the single highest-leverage way to beat the 8-round problem for analytical questions. Keep the sandbox's no-network rule; proxy tool calls through the parent process with the same permission gate.

### 1.2 Harness-by-harness cribsheet

| Harness | Loop distinctive |
|---|---|
| **Claude Code / Agent SDK** | gather/act/verify framing; auto-compaction with a `compact_boundary` event; `PreCompact`/`PreToolUse`/`Stop` hooks that run *outside* the context window; read-only tools parallel, mutating tools serial; subagents default to background; per-agent `model`/`effort`/`tools`/`maxTurns`; caps on subagent depth (3), concurrency (20), spend ([docs](https://code.claude.com/docs/en/agent-sdk/subagents)) |
| **Codex CLI** | compaction at a threshold with a **hard 90% ceiling enforced in code**; two compaction paths (local-LLM for non-OpenAI models, remote `POST /responses/compact` for OpenAI); compaction fires *before* sending a new user message, so it is invisible mid-turn ([Codex config reference](https://developers.openai.com/codex/config-reference), [loop post](https://openai.com/index/unrolling-the-codex-agent-loop/)). Notably the loop is **quadratic in JSON sent to the API** over a conversation — an argument for server-side state or aggressive eviction |
| **OpenAI Agents SDK** | loop + handoffs + approvals; `RunState` is a serializable snapshot (`to_json()`) that is the durable pause/resume boundary; sticky `always_approve`/`always_reject` decisions survive serialization ([run_state ref](https://openai.github.io/openai-agents-python/ref/run_state/), [HITL guide](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/)) |
| **Cline / Roo** | Plan mode is **read-only by design**, Act mode executes after approval; optional *different models* for plan vs act ([Cline docs](https://docs.cline.bot/core-workflows/plan-and-act)). Roo is role-based modes (Architect/Code/Ask/Debug) rather than workflow-phase-based |
| **Aider** | no autonomous loop at all in the classic mode — a tree-sitter **repo map** with PageRank-style ranking substitutes for agentic exploration ([repo map post](https://aider.chat/2023/10/22/repomap.html)); separate **architect/editor** split, where a reasoning model writes the change and a cheaper model applies the edit format ([architect post](https://aider.chat/2024/09/26/architect.html)) |
| **smolagents** | CodeAct: actions are Python snippets, sandboxed (E2B/Docker/Modal) |
| **Pydantic AI** | the loop is designed to be hosted *inside* a durable engine — Temporal, DBOS, Prefect, Restate, AWS Lambda, Kitaru, Airflow ([durable execution docs](https://ai.pydantic.dev/durable_execution/temporal/)) |
| **Devin/Cognition** | single-threaded linear agent + a dedicated compression model; 2026 update adds narrow multi-agent (below) |

**Aider's architect pattern is directly applicable and cheap here.** You already have two models configured (`kimi-k3`, `deepseek-v4-flash`). Splitting "decide" from "execute the mechanical part" — e.g. `kimi-k3` decides which documents to read, `deepseek-v4-flash` does the extraction/summarisation of each — is the same trick and fits the existing auto-learn pipeline.

---

## 2. Planning and task decomposition

### 2.1 The evidence is genuinely mixed, and the mixture is informative

**Pro-plan, with numbers:**
- [How Do Agent Harnesses Create Value? Planning Information and Release Control in Stateful LLM Agents](https://arxiv.org/abs/2609.20474) (Sept 2026) `[strong]` is the best-controlled study I found. On τ²-bench across 265 matched cells, prewritten task-specific plans beat **word-count-matched shuffled policy text** ("Sham") by **+7.17pp oracle-verified success (90% CI 1.15–13.36)**, with gains concentrated in higher-complexity tasks. The Sham control is what makes this credible — it isolates plan *content* from "more tokens in the prompt."
- Same paper on verification: a **read-only terminal verifier rejected 61% of invalid episodes while incorrectly withholding 17% of correct ones, at <$0.01/episode**, and "a standalone verifier captures nearly all the false-pass benefit of the full planning-plus-verification stack at a fraction of its cost." **This is the most actionable single number in Track A**: if you can only build one of {plan artifact, verifier}, build the verifier.
- [MAST](https://arxiv.org/html/2503.13657v2) case study: enhanced multi-level verification gave **+15.6pp absolute** on ProgramDev; role-specification improvements **+9.4%**.

**Pro-plan, on efficiency rather than accuracy:**
- The [plan-once-ground-locally](https://github.com/TheDivyanshShukla/plan-once-ground-locally) browser benchmark `[medium — one author, 200 tasks, 7 sites, 2,400 runs, DeepSeek v4.1 flash at temp 0]`: plan-then-execute scored **88.8% vs 92.0%** for a budget-matched ReAct baseline (−3.2pp, not significant) while using **84% fewer input tokens (4,060 vs 24,697), 67% fewer LLM calls (2.03 vs 6.22), 80% less money, and 4.1x less wall-clock (10.2s vs 42.2s)**. On the 505 paired runs both solved: 3,905 vs 13,298 tokens.
- **But its failure mode is the whole story**: on TodoMVC, plan-first scored **0/30 and 2/30 vs 26/30** for ReAct, because "plans written before page loads cannot reference dynamically appearing elements." A plan written before you know the state is a liability.

**Attention-mechanism argument for a plan artifact (different from the accuracy argument):**
[Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) rewrites `todo.md` step by step specifically to **push the objective back into recent context**, countering lost-in-the-middle over ~50 tool calls per task. Anthropic's [context engineering post](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) makes the same point about structured note-taking. This says the plan's value is *recitation*, not foresight — which reconciles the contradictory evidence above: **plans help when re-read, hurt when written once against unknown state.**

**Skeptical baseline:** Kambhampati's PlanBench line of work ([PlanBench](https://dl.acm.org/doi/10.5555/3666122.3667815), and the o1 follow-up) argues LLMs do not *plan* in the classical sense at all, and gains come from approximate retrieval of plan-shaped text. Take "the model will decompose your task correctly" as unproven; take "a visible checklist keeps it on task" as well-supported.

### 2.2 Plan approval / plan mode UX

Two distinct designs:
- **Cline's Plan/Act**: Plan mode is read-only-enforced (not just prompted); explicit user approval flips to Act; optionally a *stronger model* for Plan and a cheaper one for Act ([Cline](https://cline.bot/blog/plan-smarter-code-faster-clines-plan-act-is-the-paradigm-for-agentic-coding)).
- **Claude Code's plan mode**: `permission_mode: "plan"` — Claude explores and plans, file edits are never auto-approved; `ExitPlanMode` is a gatekeeper tool that presents the plan for approval and unlocks write tools. The tool description explicitly says *not* to use it for research tasks, only when the task requires planning implementation steps ([tool description](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-exitplanmode.md)).

The second detail is the one people miss: **plan mode is not for every task.** Gating a "what's on my calendar tomorrow" behind a plan is pure friction.

> **For this app, concretely:**
> - Add a **`todo_write` tool** writing to a `run_plan` table (or a JSON column on the run), mirrored into the context assembly on every turn as a compact checklist. Don't make it a separate "planning phase" — make it a tool the agent is prompted to use "when a task needs 3+ distinct steps or any external write."
> - Render it live in the Electron UI as a checklist beside the chat. This is the highest-visible-value/lowest-effort item in the whole track: it converts an opaque 8-round loop into something the user can watch and interrupt.
> - **Gate plan mode on danger, not on complexity.** The natural trigger in this app is already there: if the agent's plan contains any `external`-danger tool, show the plan for approval *once*, up front, instead of blocking mid-stream on three separate approval cards. This turns the existing 600-second-timeout approval into a batched pre-approval — strictly better UX and it fixes the "user walked away, everything auto-denied" failure.
> - The permission mode idea generalises: `plan` (read-only tools only, produce a plan), `normal` (current behaviour), `auto` (pre-approved plan executing). Reuse the existing danger levels for enforcement rather than trusting the prompt.

---

## 3. Subagents and multi-agent patterns

### 3.1 The headline numbers, and the equally important counter-numbers

**For:** [Anthropic's multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) `[medium — internal eval]`: orchestrator (Opus 4) + parallel subagents (Sonnet 4) beat single-agent Opus 4 by **90.2%** on their internal research eval. **Token usage alone explains 80% of performance variance** on BrowseComp. Parallel tool calling cut research time **up to 90%** on complex queries.

**Against:** the same post: multi-agent uses **~15x the tokens of a chat** (agents generally ~4x). [Anthropic's own 2026 decision guide](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them) softens this to **3–10x** and opens with "start with single-agent." And it explicitly lists where multi-agent loses: domains where all agents need the same context, tasks with many inter-agent dependencies, and **most coding tasks**.

**Against, harder:** [Cognition's "Don't Build Multi-Agents"](https://cognition.com/blog/dont-build-multi-agents) — the Flappy Bird example (one subagent renders a Super Mario background, another an incompatible bird, the coordinator inherits conflicting outputs). Principles: *share full agent traces, not just messages*; *actions carry implicit decisions, and conflicting decisions carry bad results*. Their recommendation was a single linear agent plus a dedicated compression model.

**The 2026 reconciliation (important, and newer):** [Cognition, "Multi-Agents: What's Actually Working"](https://cognition.com/blog/multi-agents-working) (Apr 2026) reverses partially. What works is a narrow class: **multiple agents contribute intelligence, but writes stay single-threaded** ("single-writer principle"), with the agents sharing maximal context (same sources, same todo/plan file, same priors). Two patterns they ship:
1. **Code-review loop** — reviewer runs on *clean, separate* context (the one deliberate exception to context sharing). Catches ~2 bugs/PR at 58% severity rate.
2. **"Smart friend"** — a primary model consults a stronger model on hard sub-problems; a capability *router*, not a difficulty escalator. Failed when the primary was too weak to recognise it should escalate.
Their framing for the general case: **map-reduce-and-manage**, not swarm. They also note cross-agent messaging "doesn't happen by default, because models haven't been trained in environments where it needed to" — i.e. don't build peer-to-peer agent chat.

**The mechanism everyone agrees on:** the benefit is *context isolation*, not "more brains." Anthropic: each subagent "returns only a condensed, distilled summary of its work (often **1,000–2,000 tokens**)". Claude Agent SDK subagent docs: "intermediate tool calls and results stay inside the subagent; only its final message returns to the parent." Anthropic's decision guide gives a usable threshold: **most effective when the subtask generates >1,000 tokens of information irrelevant to the main task.**

**And the multi-agent-specific tax:** [MAST](https://arxiv.org/html/2503.13657v2) on 1,600+ annotated traces across 7 frameworks (κ=0.88): **36.94% of failures are inter-agent misalignment** — fail-to-ask-for-clarification 11.65%, reasoning-action mismatch 13.98%, task derailment 7.15%, information withholding 1.66%. You are buying a new, large class of bugs.

### 3.2 Mechanics worth copying

**Subagent definition surface** ([Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/subagents)): `description` (how the parent decides to call it), `prompt` (system prompt), `tools` (allowlist — omitted tools are *absent from the session*, no prompt, no error), `disallowedTools`, `model`, `effort`, `maxTurns`, `background`, `permissionMode`, `skills`. **What it inherits:** its own system prompt + the Agent tool's prompt string + tool definitions + project CLAUDE.md. **What it does not:** parent conversation history, parent tool results, parent system prompt. "The only content you pass from parent to subagent is the Agent tool's prompt string."

**Result return:** the subagent's final message becomes the Agent tool result in the parent. Claude Code v2.1.210+ **scans that final message for instruction-shaped patterns** before the parent reads it — neutralising `<system-reminder>`-style control tags and `Human:`/`Assistant:` turn markers, prefixing a `[harness: ...]` marker. That is a prompt-injection defence at the subagent boundary, and it matters here because your subagents would be summarising **web pages and email bodies**.

**Caps:** depth default 3, concurrency default 20, plus a spend cap that stops background subagents and fails new spawns with `Budget limit reached`.

**OpenAI Agents SDK's two patterns** ([orchestration docs](https://openai.github.io/openai-agents-python/multi_agent/)):
- **Agents-as-tools** (`Agent.as_tool()`): manager keeps the conversation and the final answer; specialist gets structured input, no conversation history. This is the orchestrator-worker shape.
- **Handoffs**: implemented as a tool literally named `transfer_to_<agent>`; the specialist takes over the conversation **with the full history**, and owns the reply. Customisable via `on_handoff` callbacks, `input_type` (structured handoff args e.g. escalation reason), `is_enabled`, and **input filters** that strip tool calls or redact before the receiver sees history.

> **For this app:** handoffs are the wrong pattern (single user, single conversation thread, no "billing vs refunds" routing). **Agents-as-tools is right**, and there are exactly three subagents worth building, in priority order:
> 1. **`research` subagent** — own context, tools limited to `web_search` + `fetch_url` + `search_documents` + `read_document`, cheap model, returns ≤1,500 tokens. Today a "research X and add it to my notes" request burns the parent's context on 8 full web pages and then hits the 8-round cap. This is the textbook >1,000-irrelevant-tokens case.
> 2. **`verifier` subagent** — clean context, read-only tools, given *only* the task statement and the list of writes performed, asked "did this actually happen and does it satisfy the request?" Backed by the τ²-bench finding that the standalone verifier captures most of the benefit for <$0.01/episode, and Cognition's review-agent-on-clean-context result.
> 3. **`extractor`** — you already have this in spirit (auto-learn). Formalise it as a subagent definition so it shares the model/tool/prompt config shape.
>
> **Do not** build a general "spawn N agents" primitive. Single-writer principle: only the parent loop may call `writes`/`external` tools. Subagents get read-only tool allowlists — which also sidesteps the entire approval-across-subagents problem.
>
> **Subagent definition storage:** a `subagents` table (`name, description, system_prompt, tool_allowlist JSON, model, max_rounds, scope: global|project`) so the user can add their own from the Electron UI later. This is also the natural home for the roadmap's "skills/procedures."

---

## 4. Context management over long runs

### 4.1 The degradation evidence `[strong]`

[Chroma's Context Rot report](https://www.trychroma.com/research/context-rot) (Jul 2025), 18 models (Claude Opus 4/Sonnet 4/3.7/3.5/Haiku 3.5, o3, GPT-4.1 family, GPT-4o, Gemini 2.5 Pro/Flash, Qwen3 235B/32B/8B), holding task complexity constant and varying only input length:
- **Every one of the 18 degraded** with input length, including on trivial copy-and-retrieve.
- **Needle-question similarity matters**: at low semantic similarity (0.445) performance falls off far faster with length than at high similarity (0.829). Implication: vague questions rot faster.
- **Distractors compound**: 1 distractor hurts; 4 hurt much more; specific distractors show up in hallucinations. Claude models had the lowest hallucination rate, GPT the highest — they tend to *abstain* instead.
- **Counter-intuitive and load-bearing**: models do **worse when the haystack has logical flow**; shuffled haystacks beat coherent ones across all 18 models. A neatly narrated context is not automatically a better context.
- **LongMemEval**: focused prompts (~300 tokens) near-perfect; full prompts (~113k tokens) significantly degraded. Thinking modes narrow but do not close the gap.

Anthropic's framing ([context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)): attention is a finite budget depleted by every token, transformer n² pairwise relations stretch attention thin; it's a **gradient, not a cliff**.

### 4.2 The four moves, with numbers

**(1) Compaction.** Claude Code summarises history preserving architectural decisions, unresolved bugs, implementation details, and continues with **the summary plus the five most recently accessed files**. Guidance: *maximize recall first, then iterate to improve precision*. Codex: threshold-triggered, **hard 90% ceiling in code**, fires before a new user message so it's invisible mid-turn. Cognition's version: a dedicated model whose only job is compressing history into "key details, events, and decisions."

**(2) Tool-result clearing / microcompaction** — Anthropic calls this "one of the safest lightest-touch forms of compaction." The [context editing API](https://platform.claude.com/docs/en/build-with-claude/context-editing) has exactly the knobs worth copying:

| Param | Default | Meaning |
|---|---|---|
| `trigger` | 100,000 input tokens | when clearing activates (or `{type:"tool_uses", value:N}`) |
| `keep` | 3 tool uses | most recent tool use/result pairs preserved |
| `clear_at_least` | — | min tokens to clear per activation, else skip (avoids thrashing the cache) |
| `exclude_tools` | — | never clear these (e.g. `["web_search"]`) |
| `clear_tool_inputs` | false | also clear the call params, not just results |

Numbers `[medium — vendor]`: **+29% with context editing alone, +39% with context editing + memory tool** over baseline; in a 100-turn web-search eval, context editing let agents finish workflows that otherwise failed on context exhaustion while cutting token consumption **84%**.
Caveat stated in the docs and confirmed by a Claude Code bug report ([#42542](https://github.com/anthropics/claude-code/issues/42542)): clearing **invalidates the cached prefix**, and silent clearing can surprise users. Emit a visible boundary event.

**(3) Structured note-taking / external memory.** Anthropic: the agent "regularly writes notes persisted to memory outside of the context window" — todo lists in Claude Code, exact tallies over thousands of steps in the Pokémon agent. Manus: **"the file system as the ultimate context" — unlimited, persistent, directly operable by the agent**. Combined with just-in-time retrieval: keep *lightweight identifiers* (file paths, stored queries, URLs) and load on demand, with `head`/`tail` style partial reads instead of inlining whole objects.

**(4) Sub-agent isolation** — covered in §3; the quantified version is "explore extensively, return 1,000–2,000 tokens."

### 4.3 What this means concretely here

Your current design has **no compaction at all** and a **24,000-char truncation of tool results**. Those interact badly: truncation destroys information permanently at the moment of capture, while the un-truncated bulk still accumulates across rounds.

> **Design for this app:**
> - **Store tool results in full in SQLite; put a handle in the message stream.** `tool_events` already exists for observability — make it the canonical store. The message the model sees becomes a preview (first ~2k chars) + `result_id` + shape metadata (`rows: 137`, `bytes: 84021`), plus a `read_tool_result(result_id, offset, limit)` or `grep_tool_result(result_id, pattern)` tool. This is Anthropic's just-in-time retrieval applied to your own tool outputs, and it's strictly better than a 24k char guillotine: nothing is lost, and the agent pays only for what it looks at.
> - **Microcompaction, not full compaction, first.** Implement the `clear_tool_uses` shape above over your own message list: when assembled input exceeds a threshold (start at ~60% of the model's window — `kimi-k3` won't have Claude's headroom), replace all but the last 3 tool results with `[tool result cleared — retrievable as result_id=…]`. Because results are in SQLite, this is genuinely lossless: the agent can pull any of them back. Exclude nothing initially; measure.
> - **Full compaction second**, and use `deepseek-v4-flash` for the summariser (Claude Agent SDK explicitly supports a cheaper compaction model). Summary template should be explicit about what to preserve: objective + acceptance criteria, decisions made and why, writes already performed (critical for idempotency), unresolved errors, file/document ids touched. Emit a visible `compact_boundary` event into the SSE stream and render it in the transcript — never silently.
> - **The "session tape".** Persist every turn (role, content, tool_calls, tool results, token counts, timestamps) as append-only rows. Compaction then becomes *a view over the tape*, not a destructive edit. This gives you, for free: resumption, conversation branching (already on the roadmap), replay/debugging, and the ability to change compaction strategy retroactively. **This is the single most structurally important schema decision in Track A.**
> - **Notes as external memory.** You have no filesystem tool. The nearest safe equivalent given the sandbox: a `scratchpad` table scoped to the run, with `scratchpad_write(key, content)` / `scratchpad_read(key)` / `scratchpad_list()`. Surviving compaction is the point. The run's plan artifact (§2) is the first citizen of this store.
> - **Order in the assembled prompt matters.** Chroma's needle-position and Manus's recitation findings both say: put the **objective and the current plan last**, immediately before the model generates — not at the top with the system prompt. Your `context.py` currently front-loads everything.
> - **Stability for cache.** Keep system prompt + tool schemas byte-identical within a conversation; put memories/graph/doc excerpts *after* them; append-only. With LiteLLM→Fireworks, prompt caching behaviour varies by provider — verify empirically rather than assuming, but don't design in a way that precludes it.

---

## 5. Durable and resumable execution

### 5.1 The core argument

An agent run is a long-lived, multi-step, side-effect-producing process. An HTTP response is a short-lived, single-process, best-effort channel. Binding the two means: a laptop lid close kills the run; a backend reload kills the run; the 600s approval timeout is an artefact of the connection's patience, not the task's; and the in-memory `_approvals` dict means an approval decision cannot outlive the process that created it.

[LangGraph's checkpointer](https://docs.langchain.com/oss/python/langgraph/checkpointers) is the most-copied design: a `StateSnapshot` per super-step, keyed by `thread_id`; re-invoking with the same `thread_id` loads the latest checkpoint and continues from the next node. Three [durability modes](https://reference.langchain.com/python/langgraph/types/Durability): `sync` (write before next step; safest), `async` (write while the next step runs; default; "small risk that checkpoints are not written if the process crashes"), `exit` (write only at the end; fastest, no crash recovery).

**But checkpoints ≠ durable execution**, and the critique is worth internalising ([Diagrid](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)): a LangGraph run still lives in one process, so if the process dies the *run* dies — the checkpoint preserves **data**, not **execution**. There's no automatic failure detection, no automatic resumption, and no lease preventing two workers resuming the same thread. There's even a filed LangGraph issue about a run dying before its first checkpoint leaving no durable record it ever existed ([#8764](https://github.com/langchain-ai/langgraph/issues/8764)).

The heavyweight answers — [Temporal](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution), [DBOS](https://docs.dbos.dev/integrations/pydantic-ai), Restate, Inngest — put the agent loop in a workflow and every model call / tool call / MCP call in an *activity* that is checkpointed and replayed. [Pydantic AI supports seven such engines](https://ai.pydantic.dev/durable_execution/temporal/). **DBOS is the interesting one for you**: it "runs fully in-process as a library" and checkpoints to **Postgres or SQLite**. But even Temporal's own framing is honest: Activities are **at-least-once**; Temporal "does not make external side effects exactly once" — idempotency is your job.

**Do not adopt Temporal.** A single-user local Electron app with a FastAPI sidecar and SQLite does not need a workflow cluster. Adopt the *pattern*, hand-rolled, in ~400 lines. ([You Don't Need Temporal Yet: Durable Execution for AI Agents in 150 Lines](https://hackernoon.com/you-dont-need-temporal-yet-durable-execution-for-ai-agents-in-150-lines) argues exactly this.)

### 5.2 Pause-for-approval as serializable state

The [OpenAI Agents SDK `RunState`](https://openai.github.io/openai-agents-python/ref/run_state/) is the reference design for the thing this app most needs. It is a serializable snapshot (`to_json()` / `to_string()`) of context, usage, model responses, generated items, approval state and conversation ids. Tools declare they need approval; the run surfaces **interruptions**; `RunState` is "the durable pause/resume boundary." Two details worth stealing verbatim:
- The approval surface is **run-wide**, not scoped to the current agent — it applies to tools belonging to the current agent, to an agent reached by handoff, or to a nested agent.
- **Sticky decisions** (`always_approve=True` / `always_reject=True`) are stored in the run state and survive serialization/deserialization.

Your `allow / deny / always_chat / always_global` vocabulary already matches. What's missing is that the *pending* state is an in-memory Future rather than a row.

### 5.3 Idempotency and the tool ledger

Retry-safety is the part everyone skips and then regrets when an email sends twice. The pattern set that recurs across sources ([Temporal guidance](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution), [Formation](https://formation.dev/blog/agent-tool-retry-idempotency), [outbox-for-agents writeups](https://dev.to/redis/building-reliable-agents-with-the-transactional-outbox-pattern-and-redis-streams-45e6)):

1. **Deterministic idempotency key at the tool boundary**, computed from things that don't change on retry: `hash(run_id, step_index, tool_name, canonical_json(args))`. Not a random uuid.
2. **An intent ledger**: every dispatch is recorded *before* execution, with status transitions. A replayed step is a no-op insert, not a second row (`UNIQUE(idempotency_key)`; a partial unique index `WHERE status IN ('pending','running')` prevents two active runs on the same key).
3. **Outbox for external effects**: write the intent row and the outbox row in one SQLite transaction; a worker drains the outbox. This is what makes "send this email" survive a crash *between* deciding and sending, in either direction.
4. **Reconciliation**: on startup, any row left `running` is either retried (if the tool is idempotent/read-only) or marked `unknown` and surfaced to the user (if `external`).

Pair this with the provider-side key where one exists — Gmail `send` supports no idempotency key, but you can dedupe on a hash of (to, subject, body, ±2min) before sending; Google Calendar's `events.insert` accepts a client-supplied `id`, which *is* an idempotency key; Google Tasks similar.

### 5.4 Concrete schema for this app

```sql
-- the run: one per agent turn (or per scheduled/background task)
CREATE TABLE agent_run (
  id                TEXT PRIMARY KEY,           -- uuid
  chat_id           TEXT NOT NULL,
  project_id        TEXT,
  kind              TEXT NOT NULL,              -- 'chat' | 'scheduled' | 'subagent' | 'recap'
  parent_run_id     TEXT REFERENCES agent_run(id),
  status            TEXT NOT NULL,              -- queued|running|awaiting_approval|paused|
                                                -- done|failed|cancelled|partial
  status_reason     TEXT,                       -- 'max_rounds'|'budget'|'user_cancel'|error msg
  goal              TEXT,                       -- the user's request, verbatim
  plan_json         TEXT,                       -- the todo artifact (see §2)
  model             TEXT,
  budget_tokens     INTEGER, budget_usd REAL, budget_seconds INTEGER,
  used_tokens       INTEGER DEFAULT 0, used_usd REAL DEFAULT 0,
  round_index       INTEGER DEFAULT 0,
  lease_owner       TEXT,                       -- pid/uuid of the worker holding it
  lease_expires_at  INTEGER,                    -- epoch; prevents double-resume
  created_at, updated_at INTEGER
);
CREATE INDEX ix_run_resumable ON agent_run(status) WHERE status IN ('queued','running','awaiting_approval');

-- the session tape: append-only, one row per message
CREATE TABLE agent_step (
  run_id        TEXT NOT NULL REFERENCES agent_run(id),
  seq           INTEGER NOT NULL,
  role          TEXT NOT NULL,                  -- system|user|assistant|tool|compaction
  content       TEXT,
  tool_calls    TEXT,                           -- JSON
  tool_call_id  TEXT,
  tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
  created_at    INTEGER,
  PRIMARY KEY (run_id, seq)
);

-- every tool invocation, with idempotency + full (untruncated) result
CREATE TABLE tool_invocation (
  id               TEXT PRIMARY KEY,
  run_id           TEXT NOT NULL REFERENCES agent_run(id),
  step_seq         INTEGER NOT NULL,
  tool_name        TEXT NOT NULL,
  args_json        TEXT NOT NULL,
  idempotency_key  TEXT NOT NULL,               -- hash(run_id, step_seq, tool, canon(args))
  danger           TEXT NOT NULL,               -- safe|writes|network|executes|external
  status           TEXT NOT NULL,               -- pending|awaiting_approval|running|
                                                -- succeeded|failed|denied|skipped_duplicate
  approval_decision TEXT,                       -- allow|deny|always_chat|always_global
  approval_by      TEXT,  approval_at INTEGER,
  started_at INTEGER, finished_at INTEGER,
  result_blob      TEXT,                        -- FULL result, never truncated
  result_bytes     INTEGER,
  error            TEXT,
  undo_token       TEXT                         -- for the roadmap's undo journal
);
CREATE UNIQUE INDEX ux_tool_idem ON tool_invocation(idempotency_key);

-- outbox for external side effects
CREATE TABLE effect_outbox (
  id              TEXT PRIMARY KEY,
  invocation_id   TEXT NOT NULL REFERENCES tool_invocation(id),
  sink            TEXT NOT NULL,                -- 'gmail.send' | 'gcal.insert' | ...
  payload_json    TEXT NOT NULL,
  provider_idem   TEXT,                         -- e.g. the calendar event id we chose
  status          TEXT NOT NULL,                -- ready|inflight|delivered|failed|abandoned
  attempts        INTEGER DEFAULT 0,
  last_error      TEXT,
  delivered_at    INTEGER
);
```

> **Wiring it into FastAPI:**
> - `POST /chat` creates an `agent_run` row (status `queued`) and returns `{run_id}` immediately. A background worker (an `asyncio` task in the same process is fine for a single local user; a `multiprocessing` worker if you want the API responsive during `run_python`) picks it up under a lease.
> - `GET /runs/{run_id}/events?since=<seq>` is the SSE endpoint. It **replays from the tape** then tails live. The client reconnects with `Last-Event-ID` / `?since=`. This is exactly the Redis-buffer pattern the [Vercel AI SDK resumable-stream](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams) solves for — except SQLite *is* your buffer, which is strictly simpler and (unlike Vercel's, which [covers page reloads only, single-device](https://ably.com/topic/ai-stack/vercel-ai-sdk-resumable-stream-what-it-covers-and-what-it-doesnt)) also covers backend restarts.
> - Approvals become `POST /runs/{run_id}/approvals/{invocation_id}` writing a row and setting the run back to `queued`. **Kill the 600s auto-deny**: a run may sit in `awaiting_approval` for days and still be resumable. Show pending approvals in a persistent queue in the Electron UI (this is also the roadmap's "approval queue" item — it falls out of this design for free).
> - On sidecar startup, sweep: `status='running' AND lease_expires_at < now` → re-queue (safe, because of the idempotency ledger); `effect_outbox WHERE status='inflight'` → reconcile against the provider or surface to the user.
> - **SQLite specifics**: `PRAGMA journal_mode=WAL`, `busy_timeout=5000`. Single writer, so use one write connection (or a write queue) and many read connections. WAL gives you readers-during-writes, which is what the SSE tail needs.

---

## 6. Parallelism

### 6.1 The evidence

- Anthropic's research system: two kinds of parallelism — lead agent spawns **3–5 subagents in parallel**, and each subagent issues **3+ tool calls in parallel** — together cutting research time **up to 90%** on complex queries. Their stated remaining bottleneck: "lead agents execute subagents synchronously," and async execution is the next win.
- Claude Agent SDK's rule is the one to copy: **read-only tools run concurrently; state-mutating tools run sequentially.** Custom tools default to sequential; opt in to parallel by setting MCP's `readOnlyHint` annotation ([agent-loop docs](https://code.claude.com/docs/en/agent-sdk/agent-loop)).
- Anthropic's own honest counterweight: total *execution time* often increases despite parallelisation, because of duplicated context and synthesis overhead ([decision guide](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them)).

### 6.2 Pitfalls with `parallel_tool_calls` on OpenAI-compatible APIs — this one bites you directly

- **Strict schemas silently degrade under parallel calls.** [OpenAI's function-calling docs](https://developers.openai.com/api/docs/guides/function-calling): "when the model outputs multiple function calls via parallel function calling, model outputs may not match strict schemas." Fine-tuned models disable strict mode entirely for multi-call turns.
- **Some models duplicate calls.** OpenAI documents `gpt-4.1-nano-2025-04-14` "can sometimes include multiple tool calls for the same tool if parallel tool calls are enabled," with a recommendation to disable.
- **The flag is frequently dropped by proxies.** Multiple gateway bug reports ([llmgateway#4148](https://github.com/theopenco/llmgateway/issues/4148), [codex-proxy#824](https://github.com/icebear0828/codex-proxy/issues/824)) show `parallel_tool_calls` and `strict` being stripped at ingress so no provider ever sees them. **You are behind LiteLLM.** Verify empirically that the flag reaches Fireworks; do not assume.
- Non-frontier models are also more prone to emitting the same call twice in one turn, and to emitting a mutating call alongside a read.

> **For this app:**
> - Execute tool calls from one assistant turn **concurrently only when every call in the batch has `danger == 'safe'`** (your existing taxonomy is already the exact discriminator you need). Any `writes`/`network`/`executes`/`external` in the batch → fall back to serial in the order the model emitted them. This is Claude Code's rule expressed in your vocabulary, and it's ~15 lines.
> - **Dedupe within a batch** on the idempotency key before executing — free protection against the duplicate-call bug above, and a prerequisite anyway for §5.
> - **Approval ordering:** when a batch contains multiple `ask` tools, collect all of them and present **one** approval card listing all pending actions rather than serialising N modal blocks. This is both better UX and how the plan-approval gate (§2) should behave.
> - Bound concurrency (4–6) — you're on a laptop and `fetch_url` can fan out hard.
> - Emit `tool_events` with start/end timestamps so the trace panel can render a gantt; you already have `trace.py` spans, so this is nearly free and makes the parallelism visible/debuggable.

---

## 7. Long-horizon reliability

### 7.1 The reliability wall is steeper than headline scores suggest `[strong]`

- **pass^k.** [τ-bench](https://arxiv.org/abs/2406.12045) introduced pass^k = probability *all* k independent trials succeed. A GPT-4o function-calling agent with **>60% pass^1 drops to <25% at pass^8** on τ-retail. Reliability decays roughly as p^k. A 90% pass@1 agent is ~57% at k=8.
- **Time horizon.** [METR](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/) measures the human-task-length an agent completes with 50% reliability, doubling ~every 7 months. But 50% reliability is the headline; the **80%-reliability horizon is dramatically shorter** — commonly cited as roughly 18 months behind the 50% curve. For a personal OS acting on your email and calendar, 50% is not a product.
- **Failure attribution is harness-heavy.** [Model or Harness? An Interaction-Centric Taxonomy](https://arxiv.org/pdf/2607.28802) splits failures into model / harness / interaction and finds a substantial share is harness-attributable — i.e. your loop design is a first-class lever, not an afterthought.

**Practical read for this app:** with a non-frontier model on Fireworks, assume per-step reliability is *materially* below Claude's. Under p^k, the only two levers that scale are **(a) fewer steps per task** (code-as-action, plan-once where state is known, better tools) and **(b) external verification after the fact**. Adding more autonomous rounds makes things worse, not better.

### 7.2 Self-correction: the negative result is the important one

[Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) (Huang et al., DeepMind/UIUC, ICLR 2024) `[strong]`: **intrinsic** self-correction — revising with no external feedback — *degrades* performance. GPT-3.5 on GSM8K **77.4% → 75.9%** (−1.5pp); on CommonSenseQA **66.8% → 55.4%** (−11.4pp). It corrected 7.6% of wrong answers while breaking 8.8% of right ones. Root cause: models cannot reliably judge the correctness of their own reasoning.

[Reflexion](https://arxiv.org/abs/2303.11366) is the apparent counterexample — 91% pass@1 on HumanEval vs GPT-4's 80% — but read the architecture: the Evaluator is **exact match, task heuristics, or unit tests**. Its own ablation on hard Rust problems shows neither test generation nor reflection alone improved over a 0.60 baseline; **only the combination reached 0.68.** The lesson is not "reflection works," it is **"reflection works when attached to an external signal."**

This generalises. Recent verification work all routes through external grounding: [ReVeal](https://arxiv.org/pdf/2506.11442) generates tests and invokes a Python interpreter; [LLM-as-a-Verifier](https://www.alphaxiv.org/abs/2607.05391) reports exposing a discovered verifier set as an external tool improves downstream accuracy **by up to 17.0 points**; the [agent-harness paper](https://arxiv.org/abs/2609.20474)'s verifier is a **read-only terminal**, not a model opining.

And the failure mode when you *do* build a verifier agent: [Anthropic's decision guide](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them) names the **"early victory problem"** — verification agents mark things passing after minimal testing. Mitigation: concrete criteria and explicit "you MUST run the complete test suite before marking as passed."

### 7.3 Error recovery

Anthropic's operational advice ([multi-agent post](https://www.anthropic.com/engineering/multi-agent-research-system)): don't restart from the beginning — "resume from where the agent was when errors occurred"; telling the agent a tool failed and letting it adapt "works surprisingly well"; but combine that adaptability with "deterministic safeguards like retry logic and regular checkpoints."

Manus adds a counterintuitive one: **keep failures and stack traces in context** rather than hiding them — models use the evidence to avoid repeating the mistake. Your current design truncates and may swallow errors; don't. (Balance against Chroma's distractor finding: keep the error, drop the 40KB of HTML that caused it.)

Also from Manus: **avoid few-shot ruts** — repeated identical serialization makes the model mimic the pattern; introduce controlled variation. Relevant because your tool results are machine-formatted and uniform.

> **For this app, the verification design:**
> 1. **Rules-based first, and you have the rules.** After every `writes`/`external` tool, run a deterministic post-condition check in Python: "created a todo" → the row exists with the expected title; "created a calendar event" → `events.get` returns it; "sent an email" → the message id resolves. Feed failures back as tool results. **Zero model cost, and it's the category Anthropic ranks highest.**
> 2. **Cheap claim-grounding.** Before emitting the final answer on a tool-heavy run, have the harness check that every `tool_invocation` the answer relies on has `status='succeeded'`. If the agent says "I've added that to your calendar" and the invocation failed or was denied, block the answer and force one more round. This is the poor-man's evidence-carrying termination and catches the most damaging class of error in a personal-OS product: confidently reporting an action that didn't happen.
> 3. **Verifier subagent only for `external` writes**, on clean context, read-only tools, cheap model, with explicit criteria. The τ²-bench number (<$0.01/episode, 61% of invalid episodes caught, 17% false-withhold) suggests the cost is negligible and the false-withhold rate is tolerable *if* a withheld result means "ask the user" rather than "silently redo."
> 4. **Do not build free-floating self-critique.** The Huang et al. result says an un-grounded "are you sure?" pass will, on average, make a non-frontier model worse.

---

## 8. Where the current design breaks — consolidated

| Current | Breaks because | Fix (section) |
|---|---|---|
| `maxToolRounds = 8`, silent stop | syntactic kill-switch; truncates hard tasks, no partial marking, no resume; Manus's baseline is ~50 tool calls/task | budgets + `partial` state + Continue (§1.1c, §5) |
| Loop lives in one SSE response | run dies with the connection, the reload, the lid; no background/scheduled agents possible at all | runs-as-rows + replayable event stream (§5.4) |
| `_approvals` in-memory dict, 600s auto-deny | approval cannot survive restart; the timeout is the connection's patience, not the task's; user walks away → auto-deny | `tool_invocation.status='awaiting_approval'` rows + approval queue UI (§5.2) |
| No idempotency | any retry/resume path can double-send an email or double-create an event | deterministic idempotency key + outbox (§5.3) |
| Tool results truncated to 24k chars, inlined | destroys information at capture; still accumulates; no way to look deeper | full result in SQLite + handle + `read_tool_result` (§4.3) |
| No compaction | long runs rot; Chroma: all 18 models degrade with length, LongMemEval 300 tok → 113k tok is a cliff in practice | microcompaction then full compaction, visible boundary (§4.2–4.3) |
| No plan artifact | nothing recites the objective into recent context; nothing for the user to watch or interrupt; no batched approval | `todo_write` + plan table + UI checklist (§2.2) |
| All 25 schemas every turn | wasted context; ambiguous decision points; Anthropic flags 20+ tools as a specialization signal | inject only enabled tools, stable within a conversation (§1.1e) |
| Serial tool execution always | read-only fan-out (3 web fetches) is 3x slower than it needs to be | parallel iff whole batch is `danger='safe'` (§6.2) |
| No subagents | a research request burns the parent's whole context on raw pages, then hits the cap | 3 subagents: research, verifier, extractor; read-only allowlists (§3.2) |
| No verification | the highest-ROI intervention in the literature is simply absent; MAST attributes 21.3% of failures here | deterministic post-conditions + claim grounding + verifier subagent (§7.3) |
| Context assembled front-loaded | objective sits far from the generation point; lost-in-the-middle | plan/objective last in the assembled prompt (§4.3) |
| `parallel_tool_calls` behaviour unverified through LiteLLM | proxies are documented to strip it; strict schemas degrade under parallel calls | verify empirically; dedupe by idempotency key (§6.2) |

---

## 9. Proposed features

Ordered by (value × evidence) / effort. **S** ≈ hours, **M** ≈ 1–3 days, **L** ≈ a week+.

| # | Feature | Effort | Why it matters here |
|---|---|---|---|
| A1 | **Runs as rows**: `agent_run` + `agent_step` tape + lease; loop runs in a background worker | **L** | Everything else in this table is easier or impossible without it; unblocks scheduled agents, background tasks, recap, heartbeat |
| A2 | **Replayable SSE** `GET /runs/{id}/events?since=` — replay tape, then tail | **M** | A reload or sidecar restart no longer kills the turn; SQLite is your Redis buffer |
| A3 | **Durable approvals**: `tool_invocation` rows, no 600s auto-deny, persistent approval queue in the UI | **M** | Today an approval can't survive a restart and silently auto-denies; this also delivers the roadmap's approval-queue item |
| A4 | **Idempotency key + tool ledger + outbox for `external` tools** | **M** | Without it, every retry/resume path you add becomes a way to send the same email twice |
| A5 | **Budgets replace the 8-round cap**: tokens/cost/wall-clock, soft nudge at 60%, `partial` state + Continue button | **S** | The 8-round cap is currently the #1 cause of "the agent just stopped"; `usage.py` already has the numbers |
| A6 | **`todo_write` plan artifact** + plan re-injected last in the prompt + live checklist in Electron | **S/M** | Recitation counters lost-in-the-middle (Manus); τ²-bench shows +7.17pp from plan content over word-matched sham; makes the loop watchable |
| A7 | **Deterministic post-condition checks** after every `writes`/`external` tool | **S** | Highest-ranked verification class (rules-based) at zero model cost; catches "said it created the event, didn't" |
| A8 | **Claim grounding before final answer**: block answers that rely on failed/denied invocations | **S** | Cheap ECT-lite; kills the most damaging error class in a personal-OS product |
| A9 | **Tool results as handles**: full blob in SQLite, preview + `result_id` in context, `read_tool_result(id, offset, limit)` | **M** | Replaces the lossy 24k truncation with just-in-time retrieval; prerequisite for lossless microcompaction |
| A10 | **Microcompaction**: clear all but last 3 tool results above a token threshold, visible boundary event | **S/M** | "Safest lightest-touch compaction" (Anthropic); vendor numbers: 84% token reduction on a 100-turn eval |
| A11 | **Full compaction** with `deepseek-v4-flash` + explicit preserve-list (objective, decisions, writes performed, open errors) | **M** | Needed past ~30 rounds; the preserve-list is what makes resumption safe |
| A12 | **Parallel read-only tool execution** (batch is `danger='safe'`), bounded concurrency, batch-level dedupe | **S** | Up to 90% latency reduction on fan-out reads (Anthropic); 15 lines using the danger taxonomy you already have |
| A13 | **Batched plan approval** for runs whose plan contains `external` writes — one card, not N modals | **S** | Fixes the worst current UX (three sequential blocking modals mid-stream) |
| A14 | **Subagent primitive** + `subagents` table + `research` subagent (read-only tools, own context, ≤1,500-token return) | **L** | The >1,000-irrelevant-tokens rule fits research exactly; it's the case where context isolation clearly pays |
| A15 | **`verifier` subagent** for `external` writes, clean context, read-only, explicit criteria | **M** | τ²-bench: a standalone verifier captures nearly all the false-pass benefit of the full plan+verify stack for <$0.01/episode |
| A16 | **Tool schema gating**: inject only tools enabled for this chat/project; keep the set stable within a conversation | **S** | Fewer ambiguous decision points for a non-frontier model; better prefix-cache behaviour |
| A17 | **Code-as-action**: expose read-only app APIs inside the `run_python` sandbox via a permission-gated bridge | **L** | CodeAct reports up to +20% success; collapses multi-round analytical queries into one action — the real answer to the round-cap problem |
| A18 | **Plan/Act permission mode** enforced by danger level (read-only tools only in Plan) | **M** | Cline/Claude Code both enforce rather than prompt; gate on danger, not complexity, so trivial queries stay frictionless |
| A19 | **Scratchpad table** as external memory (`scratchpad_write/read/list`), run-scoped, survives compaction | **S** | The file-system-as-context pattern, without giving the agent a filesystem tool |
| A20 | **Subagent output sanitisation** (neutralise control-tag/turn-marker patterns before the parent reads) | **S** | Your subagents would summarise web pages and email bodies — prompt-injection surface; Claude Code does this at the boundary |
| A21 | **Startup reconciliation sweep**: expired leases re-queued, `inflight` outbox rows reconciled or surfaced | **S** | Without it, a crash mid-send leaves a permanently ambiguous state |
| A22 | **Empirically verify `parallel_tool_calls` + strict schemas survive LiteLLM → Fireworks** | **S** | Documented that gateways strip these; a 30-minute experiment prevents a class of silent bugs |

**If you build only five:** A1, A5, A6, A7, A3. That's durable runs, real budgets, a visible plan, deterministic verification, and approvals that survive a restart — the five places where the current architecture is not merely missing a feature but actively losing user work.

---

## Sources

Primary / vendor engineering:
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic — Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Claude — Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
- [Claude — When to use multi-agent systems (and when not to)](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them)
- [Claude — Building agents with Skills](https://claude.com/blog/building-agents-with-skills-equipping-agents-for-specialized-work)
- [Claude Agent SDK — How the agent loop works](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Claude Agent SDK — Subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- [Claude Platform — Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Claude — Building with extended thinking (interleaved thinking)](https://docs.claude.com/en/docs/build-with-claude/extended-thinking)
- [Claude Code issue #42542 — silent tool-result clearing](https://github.com/anthropics/claude-code/issues/42542)
- [ExitPlanMode tool description (extracted system prompts)](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/tool-description-exitplanmode.md)
- [OpenAI — Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [OpenAI Codex — configuration reference (auto-compact limits)](https://developers.openai.com/codex/config-reference)
- [OpenAI — Function calling guide (parallel + strict schemas)](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Agents SDK — RunState](https://openai.github.io/openai-agents-python/ref/run_state/)
- [OpenAI Agents SDK — Human in the loop](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/)
- [OpenAI Agents SDK — Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [OpenAI Agents SDK — Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- [Cognition — Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents)
- [Cognition — Multi-Agents: What's Actually Working (Apr 2026)](https://cognition.com/blog/multi-agents-working)
- [Manus — Context Engineering for AI Agents](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Cline — Plan & Act docs](https://docs.cline.bot/core-workflows/plan-and-act) · [Cline blog](https://cline.bot/blog/plan-smarter-code-faster-clines-plan-act-is-the-paradigm-for-agentic-coding)
- [Aider — repo map with tree-sitter](https://aider.chat/2023/10/22/repomap.html) · [architect/editor split](https://aider.chat/2024/09/26/architect.html)
- [smolagents — introduction](https://huggingface.co/blog/smolagents)
- [Pydantic AI — Durable execution with Temporal](https://ai.pydantic.dev/durable_execution/temporal/) · [with DBOS](https://ai.pydantic.dev/durable_execution/dbos/)
- [LangGraph — Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) · [Durability modes](https://reference.langchain.com/python/langgraph/types/Durability) · [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph issue #8764 — crash before first checkpoint](https://github.com/langchain-ai/langgraph/issues/8764)
- [Temporal — LangGraph plugin / durable execution](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)
- [Vercel AI SDK — resume streams](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams)

Research:
- [Chroma — Context Rot (18 models)](https://www.trychroma.com/research/context-rot)
- [Huang et al. — LLMs Cannot Self-Correct Reasoning Yet (ICLR 2024)](https://arxiv.org/abs/2310.01798)
- [Shinn et al. — Reflexion](https://arxiv.org/abs/2303.11366)
- [Wang et al. — Executable Code Actions Elicit Better LLM Agents (CodeAct)](https://arxiv.org/abs/2402.01030)
- [Yao et al. — τ-bench (pass^k)](https://arxiv.org/abs/2406.12045)
- [Cemri et al. — Why Do Multi-Agent LLM Systems Fail? (MAST)](https://arxiv.org/abs/2503.13657) · [full taxonomy](https://arxiv.org/html/2503.13657v2)
- [How Do Agent Harnesses Create Value? (Sept 2026)](https://arxiv.org/abs/2609.20474)
- [When May an Agent Stop? Evidence-Carrying Termination](https://arxiv.org/html/2608.23623)
- [Model or Harness? An Interaction-Centric Taxonomy](https://arxiv.org/pdf/2607.28802)
- [Semantic Early-Stopping for Iterative LLM Agent Loops](https://arxiv.org/abs/2606.27009)
- [When Agents Do Not Stop: Infinite Agentic Loops](https://arxiv.org/pdf/2607.01641)
- [ReVeal — self-evolving code agents via reliable self-verification](https://arxiv.org/pdf/2506.11442)
- [LLM-as-a-Verifier](https://www.alphaxiv.org/abs/2607.05391)
- [METR — Measuring AI Ability to Complete Long Tasks](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)
- [PlanBench (Kambhampati et al.)](https://dl.acm.org/doi/10.5555/3666122.3667815)
- [plan-once-ground-locally — 200-task plan-vs-ReAct benchmark](https://github.com/TheDivyanshShukla/plan-once-ground-locally)

Practitioner / secondary:
- [Diagrid — Checkpoints are not durable execution](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)
- [You Don't Need Temporal Yet — durable execution in 150 lines](https://hackernoon.com/you-dont-need-temporal-yet-durable-execution-for-ai-agents-in-150-lines)
- [Formation — Agent tool call idempotency for safe retries](https://formation.dev/blog/agent-tool-retry-idempotency)
- [Transactional outbox for agents](https://dev.to/redis/building-reliable-agents-with-the-transactional-outbox-pattern-and-redis-streams-45e6)
- [Ably — Vercel resumable-stream: what it covers and what it doesn't](https://ably.com/topic/ai-stack/vercel-ai-sdk-resumable-stream-what-it-covers-and-what-it-doesnt)
- [Codex CLI context compaction deep dive](https://codex.danielvaughan.com/2026/03/31/codex-cli-context-compaction-architecture/)
- [Simon Willison on Anthropic's multi-agent post](https://simonwillison.net/2025/Jun/14/multi-agent-research-system/)
