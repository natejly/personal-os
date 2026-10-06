# Track G: Context.ai — feature research and gap analysis

Researched 2026-09-30. Subject: [context.ai](https://www.context.ai/), which launched in July 2025 as
"the world's first AI-native office suite" and has since repositioned as an enterprise agent platform.
The office-suite heritage (AI Documents / Slides / Spreadsheets, a "Context Engine" claimed to hold
50M+ tokens without degradation) still shows in the product surface, but the pitch today is the
*operational* layer: the procedures, exceptions and expert judgment a firm runs on.

This track exists because Context's product decomposition maps almost one-to-one onto the structure
that Tracks 1–5 arrived at independently. That correspondence is the useful part — it is a second,
commercially-pressured group reaching the same architecture, which makes the one place they diverge
worth taking seriously.

## What Context.ai ships

Four products, sold as one closed loop.

| Product | What it is |
|---|---|
| **Workspace** | Chat, files, tasks, skills, applets and evals on one surface. Agents are **named members** (e.g. "Atlas") that work *under the permissions of the person who asked*. Native docs, spreadsheets, slides, kanban. Runbooks written in plain English. Every action becomes a trace the team owns; finished work lands in a channel. |
| **Engine** | The agent runtime. Identity inherited from the IdP **at every action**, not once at login. 800+ connectors. Isolated compute. Credentials held outside the prompt. Append-only audit trail. Permission tiers split by action type (read / write / external communication). **Step-level model routing** across Claude, GPT, Gemini, Kimi and open-weight models, trading cost against capability per step. Deploys hosted, in-VPC, on-prem or air-gapped. |
| **Unify** | Institutional knowledge as a **structured filesystem** — hierarchy by team, project, client and function — explicitly positioned against flat similarity retrieval, so agents reason about how knowledge *connects* rather than pulling similar snippets. Permissions follow each user's grants. Agents capture learnings across runs. |
| **Evals** | Rubrics gate quality **at every run**, not in CI. Domain experts set the bar; accepted outputs become reusable standards; human corrections become standards for later work. A failed criterion goes to an **Improver** agent that writes a new version of the skill from that feedback. Routing improves as the library grows. Marketing claims 40× faster turnaround and 28× lower cost per case. |

**Context Desktop** (private preview, macOS + Windows) extends this to local files, browser tabs and
authorized applications, with scheduled tasks, browser actions, parallel agents and shared
spreadsheets/documents.

## Mapping onto Personal OS

Their four products line up with this repo's own tracks:

- **Engine** ≈ the durable-run spine, G5–G11 plus the safety layer G12–G19.
- **Unify** ≈ Tracks 1–2 (memory, graph, retrieval).
- **Workspace** ≈ Track 3 (app surface) plus G43 skills.
- **Evals** ≈ G81–G83.

Three of those four are specified here in more depth than Context describes publicly. The fourth is
the gap, and it is load-bearing.

### Already shipped here, and a real match

- **Applets.** Context's applets — an agent turning work into a small interactive thing that opens
  beside the channel — is the same idea as Dashboards: describe a widget, the model writes a
  self-contained HTML widget, it runs in a sandboxed iframe and fetches through the backend so API
  keys never reach the widget. This one is done, and the key-isolation detail is better than what
  Context documents.
- **Traces.** They say every action becomes a trace. Here spans stream live as SSE with
  time-to-first-token, token counts and finish reasons, rendered as a waterfall. Shipped, not planned.
- **Permission tiers by action type.** `danger: safe | writes | network | executes | external` with
  three modes and chat → project → global resolution already exists, and Track 5 found and fixed the
  ways the tiers leak into each other (`executes` silently subsuming `external`).
- **Cost accounting.** Per-call token, latency and cost logging with proxy-sourced prices, manual
  overrides and full-history re-pricing. Context does not describe an equivalent.
- **The security work.** Sticky cross-turn taint, an egress allowlist that survives path- and
  subdomain-encoded exfil, a sandbox profile denying `.env` / the database / `~/.ssh` / `~/.aws` /
  `osascript`, and sidecar auth. Context markets "permissions and audit"; Track 5 actually enumerated
  the egress inventory and found two defects the research pass had missed.

### Gap 1 — Evals are a runtime flywheel there, a test suite here

**The gap.** G82 is a pytest harness with YAML cases and fake connectors reporting `pass^3`. That is
a developer test suite: it runs offline, it tells *you* whether a change regressed, and nothing about
it reaches a live run. Context's Evals is a different object — a rubric attached to the work itself,
scored on every run, where the human's correction is captured as an accepted example and a failed
criterion triggers a rewrite of the skill that produced it.

**Why it matters more than its position on the list.** G82 is rated L and sits in step 6, yet
G52 (MCP), G56 (code mode), G57–G63 (environment reach) and G47 (subagents) are all explicitly gated
behind it. The largest unstarted item is the gate for most of the remaining expansion surface. That
is an ordering deadlock, and it is the single most consequential structural finding in this
comparison.

The evidence already collected in Track 5 argues for the runtime version specifically: the τ²-bench
verifier caught 61% of invalid episodes at under a cent each and "captures nearly all the false-pass
benefit of the full planning-plus-verification stack"; independent verification cut false success
from 45% to 3%. G39 (post-condition checks) and G40 (claim grounding) are the per-action form of
this. G48 (verifier subagent) is the per-write form. Nothing covers the per-run form, which is
exactly what a rubric is.

**How to close it.** Split G82 rather than moving it. Ship a thin runtime eval first — call it G85:

1. A rubric is 3–5 plain-English criteria attached to a skill, a scheduled job, or a chat.
2. After a run finishes, `deepseek-v4-flash` scores the run's output against each criterion, reading
   from the journal and the `report_result` envelope (G40) rather than assistant prose.
3. Scores land on the run record (G81 already stores the replayable request bodies and context
   selection needed to make a failure reproducible).
4. A "correct this" affordance on the reply saves the corrected output as an **accepted example**
   against that rubric.
5. Accepted examples inject into the next run of the same skill, few-shot.

That is M, not L, and it reuses G40, G81 and the existing cheap extraction model. The pytest harness
stays valuable for the enforcement-layer red-team suite (G83) — assertions about the *enforcement*
layer genuinely belong in CI, since they must not depend on a model — but it stops being the gate on
MCP and environment reach.

### Gap 2 — Skills that improve themselves

**The gap.** G43–G46 cover skills, induction from successful chats, and hygiene counters. The arc
stops at "review queue, never auto-enable," which is the right safety call — model-authored text
entering future system prompts is a self-injection channel. But there is no mechanism that *revises*
an existing skill when it fails. Context's Improver closes exactly that loop: failed criterion →
rewritten skill → measured on the next run.

**How to close it.** G86: when a run scores below its rubric (Gap 1), generate a **diff** against the
skill that produced it and queue that diff in the same review UI G45 already builds. Human approves
the diff; the skill version increments; `use_count`/`success_count` from G46 now measure whether the
revision actually helped. The safety property is preserved — nothing auto-enables — while the
improvement arc closes. Track 5's own citations support the payoff: Agent Workflow Memory at
+24.6%/+51.1% relative, ReUseIt at 24.2% → 70.1%, SkillOps holding 80.5% across a 200 → 2,000 skill
library *but only with explicit maintenance*. The maintenance is the part currently missing.

### Gap 3 — Knowledge is searchable here, navigable there

**The gap.** Memory is a flat list with two scopes (personal, project), retrieved by pinned-first
plus recency plus FTS5 match. Documents are BM25 over chunks. The graph is entity-level with 1-hop
neighbours injected on label match. Unify's claim is hierarchy — knowledge filed by team, project,
client and function, so the agent can *navigate* rather than only query.

The honest read is that this is a partial gap. The knowledge graph arguably encodes *connection*
better than a filesystem does; a triple is strictly more expressive than a directory. What is missing
is **navigability and scoping**: there is no way for the agent to ask "what do I know about Alice" or
"everything under finance/taxes" without hoping FTS5 surfaces it, and no way for the user to scope a
chat to a subtree.

**How to close it.** G87, and it is cheap: add an `area` path to memories and documents
(`people/alice`, `finance/taxes/2026`, `health/sleep`), let retrieval filter by prefix, and add a
`browse_knowledge(path)` tool returning children and counts so the agent can descend. This
complements R1 hybrid retrieval rather than competing with it — prefix scoping shrinks the candidate
set before ranking, which makes reranking (R2) cheaper too. The auto-learn pass can propose an area
alongside each extracted memory, with the same review posture as everything else it writes.

### Gap 4 — Step-level model routing

**The gap.** There are exactly two model knobs: `defaultModel` per chat and `extractionModel` for
auto-learn. Context routes per step, explicitly trading cost against capability. This appears nowhere
in the 84-item list.

The asymmetry is notable: `usage_log` already slices by `kind` (`chat`, `learn`, `other`), by model
and by project, with latency and cost per call. The measurement apparatus exists; the control does
not.

**How to close it.** G88, S effort. Give the tool loop a per-phase model: planning rounds, tool-argument
formatting rounds, the closing summarization round, and the learn pass. Route the cheap model to
argument formatting and summarization and keep the strong model for planning. Extend `usage_log.kind`
to name the phase, and the existing usage report measures the win with no new UI. Track 5's finding
that tool-use examples move parameter handling 72% → 90% on `kimi-k3` suggests argument formatting is
exactly the phase where a cheaper model plus good examples is likely to hold up.

### Gap 5 — Agents as named, persistent members

**The gap.** G47's subagents are ephemeral: spawned inside a run, read-only tools, own context, return
≤1,500 tokens. Context's agents are named members with standing identity and permissions that produce
work landing in a channel.

**How to close it.** This is not a new subsystem — it is a naming and UI layer over two things already
planned. A named agent is (name + system prompt + tool allowlist + budget + schedule + destination),
which is G64's jobs table with a name and a sidebar entry, delivering into G69's Agent Inbox as the
"channel". The note worth recording: **build G47 and G64 with a shared definition of an agent**, or
they become two unrelated features that each hold half of this.

### Gap 6 — Connector breadth

**The gap.** 800+ connectors against Google Calendar/Gmail/Tasks plus the HTTP and RSS data sources
registered for dashboards. G52's MCP client is the right answer and correctly scoped to stdio-only,
but it sits behind the G82 gate — the same deadlock as Gap 1.

**How to close it.** Closing Gap 1 unblocks it. There is also a cheaper partial win available first:
the dashboard data-source registry (an HTTP endpoint plus a stored key, with the key never reaching
the consumer) is already a miniature connector framework. Generalizing it to emit *tools* rather than
only widget data would add real reach without waiting on MCP's lifespan and cancel-scope complexity.

## What not to copy

- **The 50M-token Context Engine claim.** Chroma's context-rot work, already cited in Track 2, found
  all 18 models tested degrade with length even on pure copy tasks. Compaction (G36/G37) is the
  better-evidenced answer than a longer window, and the claim is unfalsifiable from outside.
- **Deployment tiers.** VPC, on-prem and air-gapped are enterprise procurement surface. A local-first
  single-user app is already past the problem they solve.
- **IdP-inherited identity.** The same observation: there is one user, and their grants are the
  OAuth tokens in the local database. The transferable half — *credentials never enter the prompt*,
  and permission checks happen at every action rather than once per session — is already the design here.

## Revised ordering suggestion

The existing order in `research.md` holds through step 4. The change this track argues for is at the
boundary of steps 5 and 6:

- Insert **G85 (runtime rubrics + accepted examples)** into step 4, alongside G39/G40, which it reuses.
- Insert **G86 (Improver diffs into the G45 review queue)** immediately after G43–G46.
- **G87 (knowledge areas)** joins step 4 next to R1/R2, which it makes cheaper.
- **G88 (step-level routing)** is an S-effort item for step 1, next to the other cheap wins.
- Re-gate step 6 on **G85 + G83** rather than on G82. The red-team suite must stay in CI because it
  asserts on the enforcement layer; the general capability harness no longer needs to block MCP.

## Sources

- [Context — build, run, and improve AI agents on your infrastructure](https://www.context.ai/)
- [Context — Workspace](https://www.context.ai/product/workspace)
- [Context launches the world's first AI-native office suite](https://www.businesswire.com/news/home/20250708658619/en/Context-Launches-the-Worlds-First-AI-Native-Office-Suite-to-Automate-2.5-Trillion-Hours-of-Annual-Knowledge-Work)
- [Context AI: enterprise AI agents, features and latest updates](https://www.analyticsinsight.net/artificial-intelligence/what-is-context-ai)
- [Contextual AI (contextual.ai)](https://contextual.ai/) — a separate company with a similar name; its Agent Composer launched 2026-01-27. Not the subject of this track.
